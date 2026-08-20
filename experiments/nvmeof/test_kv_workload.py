import json

import pytest

from kv_workload import (
    TraceEvent,
    generate_trace,
    manifest_for,
    read_trace,
    replay_trace,
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
    assert result["operations"][1]["descriptor"] == result["operations"][0]["descriptor"]
    assert result["operations"][2]["descriptor"] == result["operations"][0]["descriptor"]
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
