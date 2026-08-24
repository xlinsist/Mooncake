import csv
import hashlib
import json
from argparse import Namespace
from copy import deepcopy

import pytest

import kv_workload
from kv_workload import (
    TraceEvent,
    generate_trace,
    manifest_for,
    read_trace,
    replay_trace,
    summarize_results,
    write_trace,
)


def test_generation_is_deterministic_and_valid(tmp_path):
    first = generate_trace(requests=8, blocks_per_request=2, reuse_ratio=0.75, seed=7)
    second = generate_trace(requests=8, blocks_per_request=2, reuse_ratio=0.75, seed=7)
    assert first == second
    assert {event.operation for event in first} == {"produce", "reuse", "evict"}

    path = tmp_path / "trace.jsonl"
    digest = write_trace(path, first)
    assert read_trace(path) == first
    assert manifest_for(first, seed=7, parameters={})["trace_sha256"] == digest


def test_invalid_reuse_and_eviction_are_rejected():
    event = TraceEvent(0, "request", "prefix", "block", 4096, "reuse", "local_only")
    with pytest.raises(ValueError, match="live produced block"):
        from kv_workload import validate_trace

        validate_trace([event])


def test_manifest_has_schema_and_sorted_json(tmp_path):
    events = generate_trace(requests=1, blocks_per_request=1, reuse_ratio=0, seed=1)
    path = tmp_path / "trace.jsonl"
    write_trace(path, events)
    line = path.read_text().splitlines()[0]
    assert list(json.loads(line)) == sorted(json.loads(line))
    manifest = manifest_for(events, seed=1, parameters={"requests": 1})
    assert manifest["schema_version"] == 1
    assert manifest["event_count"] == len(events)


class FakeStore:
    def __init__(self, *, put_rc=0):
        self.values = {}
        self.put_rc = put_rc
        self.calls = []
        self.closed = False

    def put(self, key, value, *config):
        self.calls.append(("put", key, config))
        if self.put_rc == 0:
            self.values[key] = value
        return self.put_rc

    def get(self, key):
        self.calls.append(("get", key))
        return self.values[key]

    def remove(self, key, _force):
        self.calls.append(("remove", key))
        self.values.pop(key, None)
        return 0

    def close(self):
        self.closed = True


class ManualClock:
    def __init__(self):
        self.now_ns = 0

    def clock_ns(self):
        return self.now_ns

    def sleep(self, seconds):
        self.now_ns += round(seconds * 1_000_000_000)


def descriptor(_store, _key, expected_target):
    return {"target": expected_target or "remote_nof", "object_size": 4096}


def replay_events(policy="local_only"):
    return [
        TraceEvent(0, "request-1", "prefix", "block", 4096, "produce", policy),
        TraceEvent(1, "request-2", "prefix", "block", 4096, "reuse", policy),
        TraceEvent(2, "cleanup", "prefix", "block", 4096, "evict", policy),
    ]


def test_no_store_replay_uses_fixed_proxy_without_connecting():
    result = replay_trace(
        replay_events(),
        mode="no_store",
        recompute_us=250,
        store_factory=lambda: pytest.fail("no_store must not connect"),
    )

    assert result["status"] == "pass"
    assert result["recompute_model"] == {"kind": "fixed_proxy", "latency_us": 250}
    assert [operation["store_operation"] for operation in result["operations"]] == [
        "recompute",
        "recompute",
        "noop",
    ]
    assert all(operation["descriptor"] is None for operation in result["operations"])


def test_paced_no_store_replay_records_schedule_and_completion_lag():
    events = [
        TraceEvent(0, "request-1", "prefix", "block", 4096, "produce", "local_only"),
        TraceEvent(3000, "request-2", "prefix", "block", 4096, "reuse", "local_only"),
        TraceEvent(3000, "cleanup", "prefix", "block", 4096, "evict", "local_only"),
    ]
    clock = ManualClock()

    result = replay_trace(
        events,
        mode="no_store",
        recompute_us=1000,
        replay_scale=1,
        clock_ns=clock.clock_ns,
        sleeper=clock.sleep,
    )

    assert result["status"] == "pass"
    assert result["pacing"] == {
        "enabled": True,
        "replay_scale": 1,
        "source_timestamp_unit": "microseconds",
        "scheduled_span_us": 3000.0,
        "processing_wall_us": 4000.0,
        "completion_lag_us": 1000.0,
        "schedule_sleep_us": 2000.0,
        "modeled_work_sleep_us": 2000.0,
        "max_arrival_lag_us": 1000.0,
    }
    assert [operation["scheduled_offset_us"] for operation in result["operations"]] == [
        0.0,
        3000.0,
        3000.0,
    ]
    assert [operation["arrival_lag_us"] for operation in result["operations"]] == [
        0.0,
        0.0,
        1000.0,
    ]


@pytest.mark.parametrize("replay_scale", [-1, float("nan"), float("inf")])
def test_replay_rejects_invalid_replay_scale(replay_scale):
    with pytest.raises(ValueError, match="replay_scale"):
        replay_trace(replay_events(), mode="no_store", replay_scale=replay_scale)


def test_transparent_replay_records_and_revalidates_actual_descriptor():
    store = FakeStore()
    ticks = iter((0, 10_000, 20_000, 32_000, 40_000, 45_000))
    result = replay_trace(
        replay_events("round_robin"),
        mode="transparent",
        store_factory=lambda: store,
        descriptor_reader=descriptor,
        clock_ns=lambda: next(ticks),
    )

    assert result["status"] == "pass"
    assert result["errors"] == []
    assert [operation["store_operation"] for operation in result["operations"]] == [
        "put",
        "get",
        "remove",
    ]
    assert result["operations"][0]["descriptor"]["target"] == "remote_nof"
    assert (
        result["operations"][1]["descriptor"] == result["operations"][0]["descriptor"]
    )
    assert (
        result["operations"][2]["descriptor"] == result["operations"][0]["descriptor"]
    )
    assert all(
        operation["target_policy"] == "round_robin"
        for operation in result["operations"]
    )
    assert [operation["latency_us"] for operation in result["operations"]] == [
        10.0,
        12.0,
        5.0,
    ]
    assert store.closed


def test_replay_uses_a_new_store_key_after_eviction():
    events = [
        TraceEvent(0, "request-1", "prefix", "block", 4096, "produce", "local_only"),
        TraceEvent(1, "request-1", "prefix", "block", 4096, "evict", "local_only"),
        TraceEvent(2, "request-2", "prefix", "block", 4096, "produce", "local_only"),
        TraceEvent(3, "request-2", "prefix", "block", 4096, "reuse", "local_only"),
        TraceEvent(4, "cleanup", "prefix", "block", 4096, "evict", "local_only"),
    ]
    store = FakeStore()

    result = replay_trace(
        events,
        mode="transparent",
        target="local_nvme",
        store_factory=lambda: store,
        descriptor_reader=descriptor,
    )

    assert result["status"] == "pass"
    first_key = result["operations"][0]["key"]
    second_key = result["operations"][2]["key"]
    assert first_key.endswith("generation-000001")
    assert second_key.endswith("generation-000002")
    assert first_key != second_key
    assert result["operations"][3]["key"] == second_key
    assert result["operations"][4]["key"] == second_key


@pytest.mark.parametrize("target", ["local_nvme", "remote_nof"])
def test_transparent_replay_explicit_target_overrides_trace_policy(target):
    result = replay_trace(
        replay_events("round_robin"),
        mode="transparent",
        target=target,
        store_factory=FakeStore,
        descriptor_reader=descriptor,
    )

    assert result["status"] == "pass"
    assert result["target"] == target
    assert result["operations"][0]["descriptor"]["target"] == target


def test_direct_replay_uses_config_and_captures_failure():
    store = FakeStore(put_rc=7)
    config = object()
    result = replay_trace(
        replay_events("remote_only"),
        mode="direct",
        target="remote_nof",
        store_factory=lambda: store,
        config_factory=lambda _target: config,
        descriptor_reader=descriptor,
    )

    assert result["status"] == "fail"
    assert len(result["operations"]) == 1
    assert result["operations"][0]["return_code"] == 7
    assert "put returned 7" in result["operations"][0]["error"]
    assert store.calls[0][2] == (config,)
    assert store.closed


def test_compact_replay_omits_full_operations_and_preserves_exact_evidence():
    ticks = iter((0, 10_000, 20_000, 32_000, 40_000, 45_000))
    result = replay_trace(
        replay_events(),
        mode="transparent",
        target="local_nvme",
        store_factory=FakeStore,
        descriptor_reader=descriptor,
        clock_ns=lambda: next(ticks),
        compact_evidence=True,
        compact_sample_limit=2,
    )

    assert result["status"] == "pass"
    assert result["evidence_mode"] == "compact"
    assert "operations" not in result
    assert result["evidence"]["operation_count"] == result["event_count"] == 3
    assert len(result["evidence"]["samples"]) == 2
    assert result["evidence"]["content_checks"] == 1
    assert result["evidence"]["content_mismatches"] == 0
    assert result["evidence"]["descriptor_checks"] == 3
    assert result["evidence"]["descriptor_target_counts"] == {
        "local_nvme": 3,
        "remote_nof": 0,
    }


def test_compact_summary_matches_legacy_summary_and_writes_only_samples(tmp_path):
    def run(compact_evidence):
        ticks = iter((0, 10_000, 20_000, 32_000, 40_000, 45_000))
        result = replay_trace(
            replay_events(),
            mode="transparent",
            target="local_nvme",
            store_factory=FakeStore,
            descriptor_reader=descriptor,
            clock_ns=lambda: next(ticks),
            compact_evidence=compact_evidence,
            compact_sample_limit=2,
        )
        result.update(
            {"case_id": "case", "run_id": "run-1", "trace_sha256": "trace-1"}
        )
        return result

    legacy_dir = tmp_path / "legacy"
    compact_dir = tmp_path / "compact"
    legacy_dir.mkdir()
    compact_dir.mkdir()
    (legacy_dir / "raw-case.json").write_text(json.dumps(run(False)))
    (compact_dir / "raw-case.json").write_text(json.dumps(run(True)))

    legacy = summarize_results(legacy_dir, required_cases=["case"])
    compact = summarize_results(compact_dir, required_cases=["case"])

    assert compact["status"] == "pass"
    assert compact["cases"] == legacy["cases"]
    with (compact_dir / "operations.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 2
    assert compact["cases"][0]["operations"] == 3


def test_compact_sampling_is_deterministic_and_output_is_bounded():
    events = generate_trace(
        requests=1_000,
        blocks_per_request=1,
        reuse_ratio=0.5,
        seed=17,
    )
    first = replay_trace(
        events,
        mode="no_store",
        key_prefix="first-run",
        compact_evidence=True,
        compact_sample_limit=32,
    )
    second = replay_trace(
        events,
        mode="no_store",
        key_prefix="second-run",
        compact_evidence=True,
        compact_sample_limit=32,
    )

    first_indices = [sample["event_index"] for sample in first["evidence"]["samples"]]
    second_indices = [sample["event_index"] for sample in second["evidence"]["samples"]]
    assert first_indices == second_indices
    assert len(first_indices) == 32
    assert len(json.dumps(first)) < 100_000


def test_compact_failure_counters_preserve_runtime_correctness_gates():
    class WrongContentStore(FakeStore):
        def get(self, key):
            super().get(key)
            return b"wrong"

    content_failure = replay_trace(
        replay_events(),
        mode="transparent",
        target="local_nvme",
        store_factory=WrongContentStore,
        descriptor_reader=descriptor,
        compact_evidence=True,
    )
    descriptor_failure = replay_trace(
        replay_events(),
        mode="transparent",
        target="local_nvme",
        store_factory=FakeStore,
        descriptor_reader=lambda *_args: {"target": "local_nvme", "object_size": 1},
        compact_evidence=True,
    )
    return_code_failure = replay_trace(
        replay_events(),
        mode="direct",
        target="local_nvme",
        store_factory=lambda: FakeStore(put_rc=7),
        config_factory=lambda _target: object(),
        descriptor_reader=descriptor,
        compact_evidence=True,
    )

    assert content_failure["status"] == "fail"
    assert content_failure["evidence"]["content_mismatches"] == 1
    assert descriptor_failure["status"] == "fail"
    assert descriptor_failure["evidence"]["descriptor_mismatches"] == 1
    assert return_code_failure["status"] == "fail"
    assert return_code_failure["evidence"]["return_code_failures"] == 1
    assert all(
        len(result["evidence"]["failure_records"])
        <= result["evidence"]["failure_record_limit"]
        for result in (content_failure, descriptor_failure, return_code_failure)
    )


def test_compact_summary_fails_closed_on_tampered_evidence(tmp_path):
    base = replay_trace(
        replay_events(),
        mode="transparent",
        target="local_nvme",
        store_factory=FakeStore,
        descriptor_reader=descriptor,
        compact_evidence=True,
    )
    base.update({"case_id": "case", "run_id": "run-1", "trace_sha256": "trace-1"})
    mutations = []

    wrong_count = deepcopy(base)
    wrong_count["evidence"]["operation_count"] += 1
    mutations.append(wrong_count)

    content_mismatch = deepcopy(base)
    content_mismatch["evidence"]["content_mismatches"] = 1
    mutations.append(content_mismatch)

    wrong_target = deepcopy(base)
    wrong_target["evidence"]["descriptor_target_counts"] = {
        "local_nvme": 0,
        "remote_nof": 3,
    }
    mutations.append(wrong_target)

    wrong_summary = deepcopy(base)
    wrong_summary["evidence"]["summary"]["p50_latency_us"] = -1
    mutations.append(wrong_summary)

    wrong_sample = deepcopy(base)
    del wrong_sample["evidence"]["samples"][0]["descriptor"]
    mutations.append(wrong_sample)

    wrong_mode = deepcopy(base)
    wrong_mode["evidence_mode"] = "future"
    mutations.append(wrong_mode)

    wrong_schema = deepcopy(base)
    wrong_schema["schema_version"] = 999
    mutations.append(wrong_schema)

    for index, result in enumerate(mutations):
        result_dir = tmp_path / str(index)
        result_dir.mkdir()
        (result_dir / "raw-case.json").write_text(json.dumps(result))
        conclusion = summarize_results(result_dir, required_cases=["case"])
        assert conclusion["status"] == "inconclusive"
        assert conclusion["errors"]


def test_compact_summary_rejects_semantic_tampering_with_refreshed_digests(tmp_path):
    base = replay_trace(
        replay_events(),
        mode="no_store",
        compact_evidence=True,
        compact_sample_limit=3,
    )
    base.update({"case_id": "case", "run_id": "run-1", "trace_sha256": "trace-1"})

    def refresh_digest(result, field):
        value = result["evidence"][field]
        digest_field = "sample_records_sha256" if field == "samples" else "summary_sha256"
        result["evidence"][digest_field] = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    wrong_store_mapping = deepcopy(base)
    sample = wrong_store_mapping["evidence"]["samples"][0]
    sample["store_operation"] = "get"
    sample["descriptor"] = {"target": "local_nvme", "object_size": sample["block_size"]}
    refresh_digest(wrong_store_mapping, "samples")

    negative_latency = deepcopy(base)
    negative_latency["evidence"]["samples"][0]["latency_us"] = -99
    refresh_digest(negative_latency, "samples")

    missing_quantiles = deepcopy(base)
    for name in ("p50_latency_us", "p95_latency_us", "p99_latency_us"):
        missing_quantiles["evidence"]["summary"][name] = None
    refresh_digest(missing_quantiles, "summary")

    malformed_pacing = deepcopy(base)
    malformed_pacing["pacing"] = []

    one_event = replay_trace(
        [TraceEvent(0, "request", "prefix", "block", 4096, "miss", "local_only")],
        mode="no_store",
        compact_evidence=True,
    )
    one_event.update({"case_id": "case", "run_id": "run-1", "trace_sha256": "trace-1"})
    boolean_counts = deepcopy(one_event)
    boolean_counts["event_count"] = True
    boolean_counts["evidence"]["operation_count"] = True

    for index, result in enumerate(
        (
            wrong_store_mapping,
            negative_latency,
            missing_quantiles,
            malformed_pacing,
            boolean_counts,
        )
    ):
        result_dir = tmp_path / str(index)
        result_dir.mkdir()
        (result_dir / "raw-case.json").write_text(json.dumps(result))
        conclusion = summarize_results(result_dir, required_cases=["case"])
        assert conclusion["status"] == "inconclusive"
        assert conclusion["errors"]


def _raw_result(mode, case_id, run_id="run-1", target=None, status="pass", errors=None):
    events = replay_events("local_only")
    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "target": target,
        "case_id": case_id,
        "run_id": run_id,
        "trace_sha256": "trace-1",
        "errors": errors or [],
        "operations": [
            {
                "request_id": event.request_id,
                "block_id": event.block_id,
                "operation": event.operation,
                "store_operation": {
                    "produce": "put",
                    "reuse": "get",
                    "evict": "remove",
                }[event.operation],
                "descriptor": {"target": "local_nvme", "object_size": 4096}
                if mode != "no_store"
                else None,
                "latency_us": latency,
                "return_code": 0,
            }
            for event, latency in zip(events, (10, 20, 30))
        ],
    }


def test_offline_summary_writes_layout_and_metrics(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"run_id": "run-1", "required_cases": ["direct"]})
    )
    (tmp_path / "raw-direct.json").write_text(
        json.dumps(_raw_result("direct", "direct", target="local_nvme"))
    )
    conclusion = summarize_results(tmp_path)
    assert conclusion["status"] == "pass"
    assert conclusion["cases"][0]["p50_latency_us"] == 20.0
    assert conclusion["cases"][0]["p95_latency_us"] == 30.0
    assert conclusion["cases"][0]["local"] == 3
    assert conclusion["cases"][0]["request_hit_rate"] == 0.5
    assert conclusion["cases"][0]["block_hit_rate"] == 0.5
    assert conclusion["cases"][0]["storage_wait_us"] == 60.0
    assert conclusion["cases"][0]["replay_scale"] == 0
    assert conclusion["cases"][0]["request_arrival_lag_p50_us"] is None
    assert (tmp_path / "operations.csv").exists()
    assert (tmp_path / "summary.csv").exists()
    assert b"\r\n" not in (tmp_path / "operations.csv").read_bytes()
    assert b"\r\n" not in (tmp_path / "summary.csv").read_bytes()


def test_offline_summary_records_pacing_metrics(tmp_path):
    events = [
        TraceEvent(0, "request-1", "prefix", "block", 4096, "produce", "local_only"),
        TraceEvent(3000, "request-2", "prefix", "block", 4096, "reuse", "local_only"),
        TraceEvent(3000, "cleanup", "prefix", "block", 4096, "evict", "local_only"),
    ]
    clock = ManualClock()
    result = replay_trace(
        events,
        mode="no_store",
        replay_scale=1,
        clock_ns=clock.clock_ns,
        sleeper=clock.sleep,
    )
    result.update({"case_id": "paced", "run_id": "run-1", "trace_sha256": "trace-1"})
    (tmp_path / "raw-paced.json").write_text(json.dumps(result))

    conclusion = summarize_results(tmp_path, required_cases=["paced"])

    case = conclusion["cases"][0]
    assert case["replay_scale"] == 1
    assert case["scheduled_span_us"] == 3000.0
    assert case["processing_wall_us"] == 4000.0
    assert case["completion_lag_us"] == 1000.0
    assert case["request_arrival_lag_p50_us"] == 0.0
    assert case["request_arrival_lag_p95_us"] == 0.0


def test_offline_summary_rejects_missing_failed_and_mixed_runs(tmp_path):
    (tmp_path / "raw-a.json").write_text(
        json.dumps(_raw_result("no_store", "a", run_id="one"))
    )
    (tmp_path / "raw-b.json").write_text(
        json.dumps(
            _raw_result(
                "no_store", "b", run_id="two", status="fail", errors=[{"error": "boom"}]
            )
        )
    )
    conclusion = summarize_results(tmp_path, required_cases=["a", "b", "c"])
    assert conclusion["status"] == "inconclusive"
    assert any("mixed run IDs" in error for error in conclusion["errors"])
    assert any("missing cases" in error for error in conclusion["errors"])


def test_offline_summary_rejects_manifest_provenance_mismatch(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"run_id": "expected", "trace_sha256": "expected-trace"})
    )
    result = _raw_result("no_store", "case", run_id="actual")
    result["trace_sha256"] = "actual-trace"
    (tmp_path / "raw-case.json").write_text(json.dumps(result))

    conclusion = summarize_results(tmp_path, required_cases=["case"])

    assert conclusion["status"] == "inconclusive"
    assert any("does not match manifest" in error for error in conclusion["errors"])


@pytest.mark.parametrize("raw_value", [None, [], 7])
def test_offline_summary_rejects_non_object_raw_json(tmp_path, raw_value):
    (tmp_path / "raw-case.json").write_text(json.dumps(raw_value))

    conclusion = summarize_results(tmp_path, required_cases=["case"])

    assert conclusion["status"] == "inconclusive"
    assert any("must be an object" in error for error in conclusion["errors"])


def test_summary_rejects_transparent_descriptor_mismatch_with_explicit_target(
    tmp_path,
):
    result = _raw_result("transparent", "transparent-remote", target="remote_nof")
    (tmp_path / "raw-transparent-remote.json").write_text(json.dumps(result))

    conclusion = summarize_results(tmp_path, required_cases=["transparent-remote"])

    assert conclusion["status"] == "inconclusive"
    assert any("descriptor mismatch" in error for error in conclusion["errors"])


def test_replay_modes_have_common_result_schema():
    no_store = replay_trace(replay_events(), mode="no_store")
    assert {
        "schema_version",
        "status",
        "mode",
        "operations",
        "errors",
    } <= no_store.keys()
    assert no_store["mode"] == "no_store"
    assert "evidence_mode" not in no_store
    assert "evidence" not in no_store


def test_replay_command_isolates_keys_by_run_and_case(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    write_trace(trace, replay_events())
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"run_id": "run-1", "trace_sha256": "digest"}))
    prefixes = []

    def fake_replay(_events, **kwargs):
        prefixes.append(kwargs["key_prefix"])
        return {"status": "pass", "errors": [], "operations": []}

    monkeypatch.setattr(kv_workload, "replay_trace", fake_replay)
    for case_id in ("direct-local", "transparent-local"):
        args = Namespace(
            output=tmp_path / f"raw-{case_id}.json",
            trace=trace,
            manifest=manifest,
            mode="transparent",
            target="local_nvme",
            case_id=case_id,
            run_id=None,
            recompute_us=1_000,
            replay_scale=0.0,
        )
        assert kv_workload._replay_command(args) == 0

    assert prefixes == [
        "kv-workload-run-1-direct-local",
        "kv-workload-run-1-transparent-local",
    ]


def test_replay_command_forwards_compact_evidence_options(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    write_trace(trace, replay_events())
    captured = {}

    def fake_replay(_events, **kwargs):
        captured.update(kwargs)
        return {"status": "pass", "errors": [], "operations": []}

    monkeypatch.setattr(kv_workload, "replay_trace", fake_replay)
    args = Namespace(
        output=tmp_path / "raw-case.json",
        trace=trace,
        manifest=None,
        mode="no_store",
        target=None,
        case_id="case",
        run_id="run-1",
        recompute_us=1_000,
        replay_scale=0.0,
        compact_evidence=True,
        compact_sample_limit=7,
    )

    assert kv_workload._replay_command(args) == 0
    assert captured["compact_evidence"] is True
    assert captured["compact_sample_limit"] == 7
