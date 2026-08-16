import importlib.util
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("correctness.py")
SPEC = importlib.util.spec_from_file_location("nvmeof_correctness", MODULE_PATH)
correctness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(correctness)


class FakeStore:
    def __init__(self):
        self.setup_args = None

    def setup(self, *args):
        self.setup_args = args
        return 0


class FailedWriteStore:
    def __init__(self, replicas=None, readable=b""):
        self.replicas = replicas or []
        self.readable = readable
        self.closed = False

    def put(self, _key, _value):
        return -200

    def get_replica_desc(self, _key):
        return self.replicas

    def get(self, _key):
        return self.readable

    def remove(self, _key, _force):
        return -401

    def close(self):
        self.closed = True


class ReplicaStatus:
    def __init__(self, status):
        self.status = status


def test_connect_enables_configured_ssd_offload(monkeypatch):
    store = FakeStore()
    mooncake_store = types.ModuleType("mooncake.store")
    mooncake_store.MooncakeDistributedStore = lambda: store
    mooncake = types.ModuleType("mooncake")
    mooncake.store = mooncake_store
    monkeypatch.setitem(sys.modules, "mooncake", mooncake)
    monkeypatch.setitem(sys.modules, "mooncake.store", mooncake_store)
    monkeypatch.setenv("LOCAL_HOSTNAME", "client")
    monkeypatch.setenv("METADATA_URL", "meta")
    monkeypatch.setenv("CLIENT_RDMA_DEVICE", "rdma0")
    monkeypatch.setenv("MASTER_ADDR", "master")
    monkeypatch.setenv("ENABLE_SSD_OFFLOAD", "1")
    monkeypatch.setenv("SSD_OFFLOAD_PATH", "/mnt/local-nvme")

    assert correctness.connect() is store
    assert store.setup_args[7] is None
    assert store.setup_args[8] is True
    assert store.setup_args[9] == "/mnt/local-nvme"


def test_connect_requires_ssd_path_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SSD_OFFLOAD", "1")
    monkeypatch.delenv("SSD_OFFLOAD_PATH", raising=False)
    monkeypatch.setenv("LOCAL_HOSTNAME", "client")
    monkeypatch.setenv("METADATA_URL", "meta")
    monkeypatch.setenv("CLIENT_RDMA_DEVICE", "rdma0")
    monkeypatch.setenv("MASTER_ADDR", "master")

    try:
        correctness.connect()
    except RuntimeError as error:
        assert "SSD_OFFLOAD_PATH" in str(error)
    else:
        raise AssertionError("connect accepted SSD offload without a path")


def test_transparent_unavailable_requires_failure_without_phantom(monkeypatch):
    store = FailedWriteStore()
    monkeypatch.setattr(correctness, "connect", lambda: store)

    correctness.transparent_unavailable("local_nvme", "test")

    assert store.closed


def test_transparent_unavailable_rejects_complete_phantom(monkeypatch):
    store = FailedWriteStore([ReplicaStatus("ReplicaStatus.COMPLETE")])
    monkeypatch.setattr(correctness, "connect", lambda: store)

    with pytest.raises(AssertionError, match="published a replica"):
        correctness.transparent_unavailable("remote_nof", "test")

    assert store.closed


def test_transparent_overhead_reports_paired_deltas(monkeypatch):
    def operation(latency, bandwidth=None):
        report = {
            "samples_ms": [latency, latency],
            "p50_ms": latency,
            "p95_ms": latency * 2,
            "p99_ms": latency * 2.5,
            "operations_per_second": 1000 / latency,
        }
        if bandwidth is not None:
            report["bandwidth_mib_s"] = bandwidth
        return report

    results = {
        "direct": {
            "mode": "direct",
            "target": "local_nvme",
            "objects": 2,
            "object_size": 4096,
            "put": operation(2.0, 100.0),
            "get": operation(1.0, 200.0),
            "remove": operation(0.5),
            "cpu_utilization": 0.5,
        },
        "transparent": {
            "mode": "transparent",
            "target": "local_nvme",
            "objects": 2,
            "object_size": 4096,
            "put": operation(2.2, 90.0),
            "get": operation(1.1, 180.0),
            "remove": operation(0.55),
            "cpu_utilization": 0.55,
        },
    }

    monkeypatch.setattr(
        correctness,
        "transparent_benchmark",
        lambda _count, _size, _target, mode, _prefix: results[mode],
    )
    report = correctness.transparent_overhead(2, 4096, "local_nvme", "test")

    assert report["direct"]["mode"] == "direct"
    assert report["transparent"]["mode"] == "transparent"
    assert report["overhead"]["put"]["p50_ms"]["absolute"] == pytest.approx(0.2)
    assert report["overhead"]["put"]["p50_ms"]["percent"] == pytest.approx(10.0)
    assert report["overhead"]["get"]["bandwidth_mib_s"] == {
        "absolute": -20.0,
        "percent": -10.0,
    }
    assert report["overhead"]["remove"]["operations_per_second"] == {
        "absolute": pytest.approx(-181.81818181818198),
        "percent": pytest.approx(-9.090909090909099),
    }


def test_remote_direct_benchmark_does_not_require_local_replica_config(monkeypatch):
    class ReplicateConfig:
        def __init__(self):
            self.replica_num = None
            self.nof_replica_num = None

    class Store:
        def __init__(self):
            self.put_configs = []
            self.values = {}
            self.closed = False

        def put(self, key, value, config):
            self.put_configs.append(config)
            self.values[key] = value
            return 0

        def get(self, key):
            return self.values[key]

        def remove(self, key, _force):
            del self.values[key]
            return 0

        def close(self):
            self.closed = True

    store = Store()
    mooncake_store = types.ModuleType("mooncake.store")
    mooncake_store.ReplicateConfig = ReplicateConfig
    mooncake = types.ModuleType("mooncake")
    mooncake.store = mooncake_store
    monkeypatch.setitem(sys.modules, "mooncake", mooncake)
    monkeypatch.setitem(sys.modules, "mooncake.store", mooncake_store)
    monkeypatch.setattr(correctness, "connect", lambda: store)
    monkeypatch.setattr(correctness, "assert_target", lambda *_args: None)

    report = correctness.transparent_benchmark(1, 4096, "remote_nof", "direct", "test")

    assert report["remove"]["operations_per_second"] > 0
    assert store.put_configs[0].replica_num == 0
    assert store.put_configs[0].nof_replica_num == 1
    assert store.closed


def test_benchmark_removes_successful_put_when_descriptor_validation_fails(monkeypatch):
    class Store:
        def __init__(self):
            self.removed = []
            self.closed = False

        def put(self, _key, _value):
            return 0

        def remove(self, key, _force):
            self.removed.append(key)
            return 0

        def close(self):
            self.closed = True

    store = Store()
    monkeypatch.setattr(correctness, "connect", lambda: store)
    monkeypatch.setattr(
        correctness,
        "assert_target",
        lambda *_args: (_ for _ in ()).throw(AssertionError("descriptor mismatch")),
    )

    with pytest.raises(AssertionError, match="descriptor mismatch"):
        correctness.transparent_benchmark(1, 4096, "remote_nof", "transparent", "test")

    assert store.removed == ["test-bench-0"]
    assert store.closed


def test_restart_seed_and_verify_use_persisted_metadata(monkeypatch):
    class RestartStore:
        values = {}

        def put(self, key, value):
            self.values[key] = value
            return 0

        def get(self, key):
            return self.values.get(key, b"")

        def get_replica_desc(self, _key):
            replica = ReplicaStatus("ReplicaStatus.COMPLETE")
            replica.is_local_disk_replica = lambda: True
            replica.is_nof_replica = lambda: False
            replica.get_local_disk_descriptor = lambda: types.SimpleNamespace(
                object_size=131072,
                transport_endpoint="owner",
                backend_id="backend",
                locator="objects/key",
                generation=1,
                host_id="host-a",
            )
            return [replica]

        def remove(self, key, _force):
            self.values.pop(key, None)
            return 0

        def close(self):
            pass

    monkeypatch.setattr(correctness, "connect", RestartStore)
    process_ids = iter((101, 202))
    monkeypatch.setattr(correctness.os, "getpid", lambda: next(process_ids))
    manifest = correctness.transparent_restart_seed(
        2, ["local_nvme"], "client_restart", "test"
    )

    result = correctness.transparent_restart_verify(manifest)

    assert result["objects_verified"] == 2
    assert result["descriptors_verified"] == 2
    assert result["scenario"] == "client_restart"
    assert result["restart_witness_before"] == "pid:101"
    assert result["restart_witness_after"] == "pid:202"
    assert result["restart_witness_changed"] is True
    assert RestartStore.values == {}


def test_local_client_restart_allows_owner_endpoint_rebind():
    before = {
        "target": "local_nvme",
        "object_size": 4096,
        "transport_endpoint": "owner-before",
        "backend_id": "backend",
        "locator": "objects/key",
        "generation": 7,
        "host_id": "host-a",
    }
    after = {**before, "transport_endpoint": "owner-after"}

    assert correctness.restart_descriptor_matches("client_restart", before, after)
    assert not correctness.restart_descriptor_matches(
        "client_restart", before, {**after, "locator": "objects/other"}
    )


def test_local_master_restart_allows_owner_endpoint_rebind():
    before = {
        "target": "local_nvme",
        "object_size": 4096,
        "transport_endpoint": "owner-before",
        "backend_id": "backend",
        "locator": "objects/key",
        "generation": 7,
        "host_id": "host-a",
    }
    after = {**before, "transport_endpoint": "owner-after"}

    assert correctness.restart_descriptor_matches("master_ha_restart", before, after)
    assert not correctness.restart_descriptor_matches(
        "master_ha_restart", before, {**after, "generation": 8}
    )


def test_restart_verify_rejects_unchanged_witness(monkeypatch):
    monkeypatch.setattr(correctness.os, "getpid", lambda: 101)
    manifest = {
        "scenario": "client_restart",
        "expected_targets": ["local_nvme"],
        "restart_witness_before": "pid:101",
        "objects": [{"key": "key"}],
    }

    with pytest.raises(AssertionError, match="witness did not change"):
        correctness.transparent_restart_verify(manifest)


def test_service_restart_requires_explicit_witness():
    with pytest.raises(AssertionError, match="before-restart witness"):
        correctness.transparent_restart_seed(
            1, ["remote_nof"], "nof_service_restart", "test"
        )


def test_transparent_acceptance_requires_every_evidence_file(tmp_path):
    report = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert report["status"] == "fail"
    assert len(report["failures"]) == report["required_evidence"]


def test_transparent_acceptance_passes_complete_evidence(tmp_path):
    reports = {
        "transparent-local.json": {"expected_targets": ["local_nvme"]},
        "transparent-remote.json": {"expected_targets": ["remote_nof"]},
        "transparent-round-robin.json": {
            "expected_targets": ["local_nvme", "remote_nof"]
        },
        "transparent-local-unavailable.json": {"expected_target": "local_nvme"},
        "transparent-remote-unavailable.json": {"expected_target": "remote_nof"},
    }
    for report in reports.values():
        if "expected_targets" in report:
            report.update(
                {
                    "objects": 2,
                    "objects_verified": 2,
                    "child_read_verified": True,
                    "objects_removed": 2,
                    "phantom_replicas": 0,
                    "target_counts": {
                        target: sum(
                            report["expected_targets"][
                                index % len(report["expected_targets"])
                            ]
                            == target
                            for index in range(2)
                        )
                        for target in report["expected_targets"]
                    },
                }
            )
        else:
            report.update(
                {
                    "write_failed": True,
                    "published_replicas": 0,
                    "readable_after_failure": False,
                }
            )
    for scenario in (
        "client_restart",
        "master_ha_restart",
        "local_owner_restart",
        "nof_service_restart",
    ):
        expected_targets = (
            ["local_nvme", "remote_nof"]
            if scenario in ("client_restart", "master_ha_restart")
            else ["local_nvme"]
            if scenario == "local_owner_restart"
            else ["remote_nof"]
        )
        reports[f"transparent-restart-{scenario}.json"] = {
            "scenario": scenario,
            "expected_targets": expected_targets,
            "objects_verified": 2,
            "descriptors_verified": 2,
            "objects_removed": 2,
            "restart_witness_before": f"{scenario}:before",
            "restart_witness_after": f"{scenario}:after",
            "restart_witness_changed": True,
        }
    for target in ("local_nvme", "remote_nof"):
        reports[f"transparent-overhead-{target}.json"] = {
            "target": target,
            "objects": 2,
            "object_size": 4096,
            "overhead": {
                operation: {
                    metric: {"absolute": 0.0, "percent": 0.0}
                    for metric in [
                        "p50_ms",
                        "p95_ms",
                        "p99_ms",
                        "operations_per_second",
                    ]
                    + ([] if operation == "remove" else ["bandwidth_mib_s"])
                }
                for operation in ("put", "get", "remove")
            },
        }
        reports[f"transparent-overhead-{target}.json"]["overhead"][
            "cpu_utilization"
        ] = {"absolute": 0.0, "percent": 0.0}
        for mode in ("direct", "transparent"):
            reports[f"transparent-overhead-{target}.json"][mode] = {
                "mode": mode,
                "target": target,
                "objects": 2,
                "object_size": 4096,
                "put": {
                    "samples_ms": [1.0, 1.0],
                    "p50_ms": 1.0,
                    "p95_ms": 2.0,
                    "p99_ms": 3.0,
                    "bandwidth_mib_s": 100.0,
                    "operations_per_second": 100.0,
                },
                "get": {
                    "samples_ms": [1.0, 1.0],
                    "p50_ms": 1.0,
                    "p95_ms": 2.0,
                    "p99_ms": 3.0,
                    "bandwidth_mib_s": 100.0,
                    "operations_per_second": 100.0,
                },
                "remove": {
                    "samples_ms": [1.0, 1.0],
                    "p50_ms": 1.0,
                    "p95_ms": 2.0,
                    "p99_ms": 3.0,
                    "operations_per_second": 100.0,
                },
                "cpu_utilization": 0.5,
            }
    reports["transparent-software-verification.json"] = {
        "commands": [
            {"command": ["verification", str(index)], "exit_code": 0, "output": "ok"}
            for index in range(5)
        ],
        "commands_passed": 5,
        "commands_required": 5,
    }
    for filename, report in reports.items():
        report["run_id"] = "run-1"
        (tmp_path / filename).write_text(
            __import__("json").dumps({"status": "pass", **report}),
            encoding="utf-8",
        )

    acceptance = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert acceptance["status"] == "pass"
    assert acceptance["failures"] == []


def test_transparent_acceptance_rejects_empty_pass_reports(tmp_path):
    for filename in (
        "transparent-local.json",
        "transparent-remote.json",
        "transparent-round-robin.json",
        "transparent-local-unavailable.json",
        "transparent-remote-unavailable.json",
        "transparent-restart-client_restart.json",
        "transparent-restart-master_ha_restart.json",
        "transparent-restart-local_owner_restart.json",
        "transparent-restart-nof_service_restart.json",
        "transparent-overhead-local_nvme.json",
        "transparent-overhead-remote_nof.json",
        "transparent-software-verification.json",
    ):
        (tmp_path / filename).write_text(
            '{"status": "pass", "run_id": "run-1"}', encoding="utf-8"
        )

    acceptance = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert acceptance["status"] == "fail"
    assert acceptance["failures"]


def test_software_verification_records_missing_tool_as_failure(monkeypatch, tmp_path):
    def fake_run(command, **_kwargs):
        if command[0] == "pre-commit":
            raise FileNotFoundError("pre-commit")
        return __import__("subprocess").CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(correctness.subprocess, "run", fake_run)
    result = correctness.transparent_software_verification(
        str(tmp_path), str(tmp_path / "build")
    )

    assert result["commands_required"] == 5
    assert result["commands_passed"] == 4
    assert result["commands"][-1]["exit_code"] == 127


def test_transparent_acceptance_rejects_malformed_metric_types(tmp_path):
    test_transparent_acceptance_passes_complete_evidence(tmp_path)
    benchmark_path = tmp_path / "transparent-overhead-local_nvme.json"
    report = __import__("json").loads(benchmark_path.read_text(encoding="utf-8"))
    report["overhead"]["put"]["p50_ms"]["absolute"] = "not-a-number"
    benchmark_path.write_text(__import__("json").dumps(report), encoding="utf-8")

    acceptance = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert acceptance["status"] == "fail"
    assert any("overhead put.p50_ms" in error for error in acceptance["failures"])


def test_transparent_acceptance_rejects_missing_remove_metrics(tmp_path):
    test_transparent_acceptance_passes_complete_evidence(tmp_path)
    benchmark_path = tmp_path / "transparent-overhead-local_nvme.json"
    report = __import__("json").loads(benchmark_path.read_text(encoding="utf-8"))
    del report["transparent"]["remove"]["operations_per_second"]
    benchmark_path.write_text(__import__("json").dumps(report), encoding="utf-8")

    acceptance = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert acceptance["status"] == "fail"
    assert any(
        "remove.operations_per_second" in error for error in acceptance["failures"]
    )


def test_transparent_acceptance_rejects_mixed_run_ids(tmp_path):
    test_transparent_acceptance_passes_complete_evidence(tmp_path)
    path = tmp_path / "transparent-local.json"
    report = __import__("json").loads(path.read_text(encoding="utf-8"))
    report["run_id"] = "stale-run"
    path.write_text(__import__("json").dumps(report), encoding="utf-8")

    acceptance = correctness.transparent_acceptance(str(tmp_path), "run-1")

    assert acceptance["status"] == "fail"
    assert any("expected run_id" in error for error in acceptance["failures"])
