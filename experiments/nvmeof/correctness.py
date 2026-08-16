#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
import uuid
from statistics import quantiles


def payload(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


def connect():
    enable_ssd_offload = os.environ.get("ENABLE_SSD_OFFLOAD", "0") == "1"
    ssd_offload_path = os.environ.get("SSD_OFFLOAD_PATH", "")
    if enable_ssd_offload and not ssd_offload_path:
        raise RuntimeError("SSD_OFFLOAD_PATH is required with ENABLE_SSD_OFFLOAD=1")

    from mooncake.store import MooncakeDistributedStore

    store = MooncakeDistributedStore()
    rc = store.setup(
        os.environ["LOCAL_HOSTNAME"],
        os.environ["METADATA_URL"],
        int(os.environ.get("GLOBAL_SEGMENT_SIZE", 1 << 30)),
        int(os.environ.get("LOCAL_BUFFER_SIZE", 1 << 30)),
        os.environ.get("PROTOCOL", "rdma"),
        os.environ["CLIENT_RDMA_DEVICE"],
        os.environ["MASTER_ADDR"],
        None,
        enable_ssd_offload,
        ssd_offload_path,
    )
    if rc != 0:
        raise RuntimeError(f"store setup failed: {rc}")
    return store


def replica_config(memory_replicas: int):
    from mooncake.store import ReplicateConfig

    config = ReplicateConfig()
    config.replica_num = memory_replicas
    config.nof_replica_num = 1
    return config


def verify(store, key: str, expected: bytes):
    actual = store.get(key)
    if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
        raise AssertionError(f"SHA-256 mismatch for {key}")


def descriptor_fingerprint(store, key: str, expected_target: str):
    replicas = store.get_replica_desc(key)
    complete = [
        replica for replica in replicas if str(replica.status).endswith("COMPLETE")
    ]
    if len(complete) != 1:
        raise AssertionError(
            f"expected one complete replica for {key}, got {len(complete)}"
        )
    replica = complete[0]
    if expected_target == "local_nvme":
        if not replica.is_local_disk_replica():
            raise AssertionError(f"expected local NVMe descriptor for {key}")
        local = replica.get_local_disk_descriptor()
        if not local.backend_id or not local.locator or local.generation == 0:
            raise AssertionError(f"incomplete local NVMe descriptor for {key}")
        return {
            "target": expected_target,
            "object_size": local.object_size,
            "transport_endpoint": local.transport_endpoint,
            "backend_id": local.backend_id,
            "locator": local.locator,
            "generation": local.generation,
            "host_id": local.host_id,
        }
    elif expected_target == "remote_nof":
        if not replica.is_nof_replica():
            raise AssertionError(f"expected remote NoF descriptor for {key}")
        nof = replica.get_nof_descriptor().buffer_descriptor
        if not nof.transport_endpoint or nof.size <= 0:
            raise AssertionError(f"incomplete remote NoF descriptor for {key}")
        return {
            "target": expected_target,
            "object_size": nof.size,
            "transport_endpoint": nof.transport_endpoint,
            "buffer_address": nof.buffer_address,
        }
    else:
        raise AssertionError(f"unsupported expected target: {expected_target}")


def assert_target(store, key: str, expected_target: str):
    descriptor_fingerprint(store, key, expected_target)


def restart_descriptor_matches(scenario: str, before: dict, after: dict) -> bool:
    if before.get("target") == "local_nvme":
        stable_fields = (
            "target",
            "object_size",
            "backend_id",
            "locator",
            "generation",
            "host_id",
        )
        return all(before.get(field) == after.get(field) for field in stable_fields)
    if scenario == "nof_service_restart":
        stable_fields = ("target", "object_size", "transport_endpoint")
        return all(before.get(field) == after.get(field) for field in stable_fields)
    return before == after


def assert_no_published_replica(store, key: str):
    replicas = store.get_replica_desc(key)
    published = [
        replica for replica in replicas if str(replica.status).endswith("COMPLETE")
    ]
    if published:
        raise AssertionError(f"failed write published a replica for {key}")


def transparent_lifecycle(count: int, expected_targets: list[str], prefix: str):
    store = connect()
    keys = []
    target_counts = {target: 0 for target in expected_targets}
    for object_index in range(count):
        key = f"{prefix}-transparent-{object_index}"
        value = payload(object_index, 131072)
        rc = store.put(key, value)
        if rc != 0:
            raise AssertionError(f"transparent put failed for {key}: {rc}")
        expected_target = expected_targets[object_index % len(expected_targets)]
        assert_target(store, key, expected_target)
        target_counts[expected_target] += 1
        verify(store, key, value)
        keys.append((key, object_index))

    key, seed = keys[len(keys) // 2]
    subprocess.run(
        [
            sys.executable,
            __file__,
            "child-read",
            "--key",
            key,
            "--seed",
            str(seed),
            "--size",
            "131072",
        ],
        check=True,
        env=os.environ,
    )
    for key, _ in keys:
        if store.remove(key, True) != 0:
            raise AssertionError(f"transparent remove failed for {key}")
        if store.get(key) not in (b"", None):
            raise AssertionError(f"removed transparent key remains readable: {key}")
    store.close()
    return {
        "objects_verified": len(keys),
        "child_read_verified": True,
        "objects_removed": len(keys),
        "phantom_replicas": 0,
        "target_counts": target_counts,
    }


def transparent_unavailable(expected_target: str, prefix: str):
    store = connect()
    key = f"{prefix}-unavailable-{expected_target}"
    try:
        rc = store.put(key, payload(0, 4096))
        if rc == 0:
            raise AssertionError(
                f"managed {expected_target} write unexpectedly succeeded"
            )
        assert_no_published_replica(store, key)
        if store.get(key) not in (b"", None):
            raise AssertionError(f"failed write remains readable: {key}")
    finally:
        store.remove(key, True)
        store.close()
    return {
        "write_failed": True,
        "published_replicas": 0,
        "readable_after_failure": False,
    }


def transparent_restart_seed(
    count: int,
    expected_targets: list[str],
    scenario: str,
    prefix: str,
    restart_witness: str | None = None,
):
    if scenario == "client_restart":
        restart_witness = f"pid:{os.getpid()}"
    elif not restart_witness:
        raise AssertionError(f"{scenario} requires a before-restart witness")
    store = connect()
    objects = []
    try:
        for object_index in range(count):
            key = f"{prefix}-restart-{object_index}"
            value = payload(object_index, 131072)
            rc = store.put(key, value)
            if rc != 0:
                raise AssertionError(f"restart seed put failed for {key}: {rc}")
            expected_target = expected_targets[object_index % len(expected_targets)]
            assert_target(store, key, expected_target)
            verify(store, key, value)
            objects.append(
                {
                    "key": key,
                    "seed": object_index,
                    "size": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "expected_target": expected_target,
                    "descriptor": descriptor_fingerprint(store, key, expected_target),
                }
            )
    finally:
        store.close()
    return {
        "scenario": scenario,
        "expected_targets": expected_targets,
        "restart_witness_before": restart_witness,
        "objects": objects,
    }


def transparent_restart_verify(manifest: dict, restart_witness: str | None = None):
    scenario = manifest.get("scenario")
    if scenario == "client_restart":
        restart_witness = f"pid:{os.getpid()}"
    elif not restart_witness:
        raise AssertionError(f"{scenario} requires an after-restart witness")
    witness_before = manifest.get("restart_witness_before")
    if not witness_before:
        raise AssertionError("restart manifest contains no before-restart witness")
    if witness_before == restart_witness:
        raise AssertionError(f"{scenario} restart witness did not change")

    store = connect()
    objects = manifest.get("objects", [])
    if not objects:
        raise AssertionError("restart manifest contains no objects")
    verified = 0
    try:
        for object_info in objects:
            key = object_info["key"]
            expected = payload(object_info["seed"], object_info["size"])
            if hashlib.sha256(expected).hexdigest() != object_info["sha256"]:
                raise AssertionError(f"restart manifest checksum mismatch for {key}")
            assert_target(store, key, object_info["expected_target"])
            descriptor_after = descriptor_fingerprint(
                store, key, object_info["expected_target"]
            )
            if not restart_descriptor_matches(
                scenario, object_info.get("descriptor", {}), descriptor_after
            ):
                raise AssertionError(
                    f"restart descriptor changed unexpectedly for {key}"
                )
            verify(store, key, expected)
            verified += 1
    finally:
        cleanup_failures = []
        for object_info in objects:
            if store.remove(object_info["key"], True) != 0:
                cleanup_failures.append(object_info["key"])
        store.close()
        if cleanup_failures:
            raise AssertionError(f"restart cleanup failed for {cleanup_failures!r}")
    return {
        "scenario": scenario,
        "expected_targets": manifest["expected_targets"],
        "restart_witness_before": witness_before,
        "restart_witness_after": restart_witness,
        "restart_witness_changed": True,
        "objects_verified": verified,
        "descriptors_verified": verified,
        "objects_removed": len(objects),
    }


def child_read(args):
    store = connect()
    expected = payload(args.seed, args.size)
    verify(store, args.key, expected)
    store.close()


def exercise_mode(memory_replicas: int, count: int, sizes: list[int], prefix: str):
    store = connect()
    config = replica_config(memory_replicas)
    keys = []
    for size_index, size in enumerate(sizes):
        for object_index in range(count):
            seed = size_index * 1_000_000 + object_index
            key = f"{prefix}-m{memory_replicas}-s{size}-i{object_index}"
            expected = payload(seed, size)
            rc = store.put(key, expected, config)
            if rc != 0:
                raise AssertionError(f"put failed for {key}: {rc}")
            verify(store, key, expected)
            verify(store, key, expected)
            keys.append((key, seed, size))

    key, seed, size = keys[len(keys) // 2]
    subprocess.run(
        [
            sys.executable,
            __file__,
            "child-read",
            "--key",
            key,
            "--seed",
            str(seed),
            "--size",
            str(size),
        ],
        check=True,
        env=os.environ,
    )

    duplicate_key, duplicate_seed, duplicate_size = keys[0]
    rc = store.put(duplicate_key, payload(duplicate_seed, duplicate_size), config)
    if rc != 0:
        raise AssertionError(f"idempotent duplicate put failed: {rc}")
    verify(store, duplicate_key, payload(duplicate_seed, duplicate_size))

    for key, _, _ in keys:
        rc = store.remove(key, True)
        if rc != 0:
            raise AssertionError(f"remove failed for {key}: {rc}")
        if store.get(key) not in (b"", None):
            raise AssertionError(f"removed key remains readable: {key}")
    store.close()


def unaligned_probe(prefix: str):
    store = connect()
    config = replica_config(0)
    key = f"{prefix}-unaligned"
    value = payload(42, 4097)
    rc = store.put(key, value, config)
    outcome = {"return_code": rc, "behavior": "rejected"}
    if rc == 0:
        verify(store, key, value)
        outcome["behavior"] = "accepted_and_verified"
        store.remove(key, True)
    store.close()
    return outcome


def stability(seconds: int, prefix: str):
    store = connect()
    configs = [replica_config(0), replica_config(1)]
    deadline = time.monotonic() + seconds
    operations = 0
    while time.monotonic() < deadline:
        size = (4096, 131072, 1048576)[operations % 3]
        value = payload(operations, size)
        key = f"{prefix}-stability-{operations}"
        config = configs[operations % 2]
        rc = store.put(key, value, config)
        if rc != 0:
            raise AssertionError(f"stability put failed: {rc}")
        verify(store, key, value)
        if store.remove(key, True) != 0:
            raise AssertionError("stability remove failed")
        operations += 1
    store.close()
    return operations


def percentile_ms(samples_ns: list[int], percentile: int) -> float:
    if len(samples_ns) == 1:
        return samples_ns[0] / 1_000_000
    index = {50: 49, 95: 94, 99: 98}[percentile]
    return quantiles(samples_ns, n=100, method="inclusive")[index] / 1_000_000


def transparent_benchmark(count: int, size: int, target: str, mode: str, prefix: str):
    store = connect()
    config = None
    if mode == "direct":
        from mooncake.store import ReplicateConfig

        config = ReplicateConfig()
        config.replica_num = 0
        if target == "remote_nof":
            config.nof_replica_num = 1
        elif target == "local_nvme":
            if not hasattr(config, "local_replica_num"):
                raise RuntimeError(
                    "installed Mooncake Python binding does not support "
                    "local_replica_num"
                )
            config.local_replica_num = 1
        else:
            raise ValueError(f"unsupported benchmark target: {target}")

    keys = []
    put_samples = []
    get_samples = []
    remove_samples = []
    started_cpu = time.process_time_ns()
    started_wall = time.perf_counter_ns()
    try:
        for index in range(count):
            key = f"{prefix}-bench-{index}"
            value = payload(index, size)
            started = time.perf_counter_ns()
            rc = (
                store.put(key, value)
                if config is None
                else store.put(key, value, config)
            )
            put_samples.append(time.perf_counter_ns() - started)
            if rc != 0:
                raise AssertionError(f"benchmark put failed for {key}: {rc}")
            keys.append(key)
            assert_target(store, key, target)
            started = time.perf_counter_ns()
            verify(store, key, value)
            get_samples.append(time.perf_counter_ns() - started)
        for key in keys:
            started = time.perf_counter_ns()
            rc = store.remove(key, True)
            remove_samples.append(time.perf_counter_ns() - started)
            if rc != 0:
                raise AssertionError(f"benchmark remove failed for {key}: {rc}")
        keys.clear()
    finally:
        for key in keys:
            store.remove(key, True)
        store.close()

    elapsed_ns = time.perf_counter_ns() - started_wall
    cpu_ns = time.process_time_ns() - started_cpu
    total_bytes = count * size

    def operation(samples, payload_bytes: int | None):
        total_seconds = sum(samples) / 1e9
        result = {
            "samples_ms": [sample / 1_000_000 for sample in samples],
            "p50_ms": percentile_ms(samples, 50),
            "p95_ms": percentile_ms(samples, 95),
            "p99_ms": percentile_ms(samples, 99),
            "operations_per_second": len(samples) / total_seconds
            if total_seconds
            else 0.0,
        }
        if payload_bytes is not None:
            result["bandwidth_mib_s"] = (
                payload_bytes / total_seconds / (1 << 20) if total_seconds else 0.0
            )
        return result

    return {
        "mode": mode,
        "target": target,
        "objects": count,
        "object_size": size,
        "put": operation(put_samples, total_bytes),
        "get": operation(get_samples, total_bytes),
        "remove": operation(remove_samples, None),
        "cpu_utilization": cpu_ns / elapsed_ns if elapsed_ns else 0.0,
    }


def transparent_overhead(count: int, size: int, target: str, prefix: str):
    direct = transparent_benchmark(count, size, target, "direct", f"{prefix}-direct")
    transparent = transparent_benchmark(
        count, size, target, "transparent", f"{prefix}-transparent"
    )

    def delta(transparent_value: float, direct_value: float):
        return {
            "absolute": transparent_value - direct_value,
            "percent": (
                (transparent_value - direct_value) / direct_value * 100
                if direct_value
                else None
            ),
        }

    overhead = {
        operation: {
            metric: delta(transparent[operation][metric], direct[operation][metric])
            for metric in (
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "operations_per_second",
                *(() if operation == "remove" else ("bandwidth_mib_s",)),
            )
        }
        for operation in ("put", "get", "remove")
    }
    overhead["cpu_utilization"] = delta(
        transparent["cpu_utilization"], direct["cpu_utilization"]
    )
    return {
        "target": target,
        "objects": count,
        "object_size": size,
        "direct": direct,
        "transparent": transparent,
        "overhead": overhead,
    }


def transparent_software_verification(repo_root: str, build_dir: str):
    repo_root = os.path.abspath(repo_root)
    build_dir = os.path.abspath(build_dir)
    commands = [
        [
            "cmake",
            "--build",
            build_dir,
            "--target",
            "heterogeneous_storage_test",
            "serializer_test",
            "client_storage_backend_test",
            "replica_selection_test",
            "master_service_ha_test",
            "-j2",
        ],
        [
            "ctest",
            "--test-dir",
            build_dir,
            "--output-on-failure",
            "-R",
            "heterogeneous_storage_test|serializer_test|client_storage_backend_test|replica_selection_test",
        ],
        [
            os.path.join(build_dir, "mooncake-store/tests/master_service_ha_test"),
            "--gtest_filter=MasterServiceHATest.RestoreFromStandbyPreservesNoFBufferDescriptor",
        ],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "experiments/nvmeof/test_correctness.py",
        ],
        [
            "pre-commit",
            "run",
            "--files",
            "experiments/nvmeof/correctness.py",
            "experiments/nvmeof/run.sh",
            "experiments/nvmeof/README.md",
        ],
    ]
    results = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            exit_code = completed.returncode
            output = (completed.stdout + completed.stderr)[-16000:]
        except OSError as error:
            exit_code = 127
            output = str(error)
        results.append({"command": command, "exit_code": exit_code, "output": output})
    return {
        "commands": results,
        "commands_passed": sum(result["exit_code"] == 0 for result in results),
        "commands_required": len(results),
    }


def transparent_acceptance(result_dir: str, run_id: str):
    def positive_integer(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def finite_nonnegative(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    def finite_positive(value):
        return finite_nonnegative(value) and value > 0

    def valid_delta(value):
        if not isinstance(value, dict):
            return False
        absolute = value.get("absolute")
        percent = value.get("percent")
        return (
            isinstance(absolute, (int, float))
            and not isinstance(absolute, bool)
            and math.isfinite(absolute)
            and (
                percent is None
                or (
                    isinstance(percent, (int, float))
                    and not isinstance(percent, bool)
                    and math.isfinite(percent)
                )
            )
        )

    required = {
        "transparent-local.json": {
            "expected_targets": ["local_nvme"],
        },
        "transparent-remote.json": {
            "expected_targets": ["remote_nof"],
        },
        "transparent-round-robin.json": {
            "expected_targets": ["local_nvme", "remote_nof"],
        },
        "transparent-local-unavailable.json": {
            "expected_target": "local_nvme",
        },
        "transparent-remote-unavailable.json": {
            "expected_target": "remote_nof",
        },
        "transparent-restart-client_restart.json": {
            "scenario": "client_restart",
            "expected_targets": ["local_nvme", "remote_nof"],
        },
        "transparent-restart-master_ha_restart.json": {
            "scenario": "master_ha_restart",
            "expected_targets": ["local_nvme", "remote_nof"],
        },
        "transparent-restart-local_owner_restart.json": {
            "scenario": "local_owner_restart",
            "expected_targets": ["local_nvme"],
        },
        "transparent-restart-nof_service_restart.json": {
            "scenario": "nof_service_restart",
            "expected_targets": ["remote_nof"],
        },
        "transparent-overhead-local_nvme.json": {
            "target": "local_nvme",
        },
        "transparent-overhead-remote_nof.json": {
            "target": "remote_nof",
        },
        "transparent-software-verification.json": {},
    }
    evidence = {}
    failures = []
    for filename, expected_fields in required.items():
        path = os.path.join(result_dir, filename)
        try:
            with open(path, encoding="utf-8") as source:
                report = json.load(source)
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"{filename}: {error}")
            continue
        if not isinstance(report, dict):
            failures.append(f"{filename}: report must be a JSON object")
            continue
        evidence[filename] = report.get("status")
        if report.get("status") != "pass":
            failures.append(f"{filename}: status is not pass")
        if report.get("run_id") != run_id:
            failures.append(
                f"{filename}: expected run_id={run_id!r}, "
                f"got {report.get('run_id')!r}"
            )
        for field, expected in expected_fields.items():
            if report.get(field) != expected:
                failures.append(
                    f"{filename}: expected {field}={expected!r}, "
                    f"got {report.get(field)!r}"
                )
        if filename in (
            "transparent-local.json",
            "transparent-remote.json",
            "transparent-round-robin.json",
        ):
            objects = report.get("objects")
            if not positive_integer(objects):
                failures.append(f"{filename}: objects must be a positive integer")
            for field in ("objects_verified", "objects_removed"):
                if report.get(field) != objects:
                    failures.append(f"{filename}: {field} must equal objects")
            if report.get("child_read_verified") is not True:
                failures.append(f"{filename}: child read was not verified")
            if report.get("phantom_replicas") != 0:
                failures.append(f"{filename}: phantom replicas were reported")
            target_counts = report.get("target_counts")
            if not isinstance(target_counts, dict):
                failures.append(f"{filename}: target_counts is missing")
            else:
                expected_targets = report["expected_targets"]
                expected_counts = {
                    target: sum(
                        expected_targets[index % len(expected_targets)] == target
                        for index in range(objects if positive_integer(objects) else 0)
                    )
                    for target in expected_targets
                }
                if target_counts != expected_counts:
                    failures.append(
                        f"{filename}: expected target_counts={expected_counts!r}, "
                        f"got {target_counts!r}"
                    )
        elif filename.endswith("-unavailable.json"):
            if report.get("write_failed") is not True:
                failures.append(f"{filename}: failed write was not observed")
            if report.get("published_replicas") != 0:
                failures.append(f"{filename}: failed write published replicas")
            if report.get("readable_after_failure") is not False:
                failures.append(f"{filename}: failed write remained readable")
        elif filename.startswith("transparent-restart-"):
            witness_before = report.get("restart_witness_before")
            witness_after = report.get("restart_witness_after")
            if (
                not isinstance(witness_before, str)
                or not witness_before
                or not isinstance(witness_after, str)
                or not witness_after
                or witness_before == witness_after
                or report.get("restart_witness_changed") is not True
            ):
                failures.append(f"{filename}: restart witness did not change")
            objects_verified = report.get("objects_verified")
            if not positive_integer(objects_verified):
                failures.append(
                    f"{filename}: objects_verified must be a positive integer"
                )
            if report.get("descriptors_verified") != objects_verified:
                failures.append(
                    f"{filename}: descriptors_verified must equal objects_verified"
                )
            if report.get("objects_removed") != objects_verified:
                failures.append(
                    f"{filename}: objects_removed must equal objects_verified"
                )
        elif filename.startswith("transparent-overhead-"):
            if not positive_integer(report.get("objects")):
                failures.append(f"{filename}: objects must be a positive integer")
            if not positive_integer(report.get("object_size")):
                failures.append(f"{filename}: object_size must be a positive integer")
            for mode in ("direct", "transparent"):
                mode_report = report.get(mode, {})
                if mode_report.get("mode") != mode:
                    failures.append(f"{filename}: missing paired {mode} result")
                if mode_report.get("target") != report.get("target"):
                    failures.append(f"{filename}: {mode} target does not match")
                if mode_report.get("objects") != report.get("objects"):
                    failures.append(f"{filename}: {mode} objects do not match")
                if mode_report.get("object_size") != report.get("object_size"):
                    failures.append(f"{filename}: {mode} object_size does not match")
                for operation in ("put", "get", "remove"):
                    samples = mode_report.get(operation, {}).get("samples_ms")
                    if (
                        not isinstance(samples, list)
                        or len(samples) != report.get("objects")
                        or not all(finite_nonnegative(sample) for sample in samples)
                    ):
                        failures.append(
                            f"{filename}: invalid {mode} {operation}.samples_ms"
                        )
                    for metric in ("p50_ms", "p95_ms", "p99_ms"):
                        if not finite_nonnegative(
                            mode_report.get(operation, {}).get(metric)
                        ):
                            failures.append(
                                f"{filename}: invalid {mode} {operation}.{metric}"
                            )
                    if not finite_positive(
                        mode_report.get(operation, {}).get("operations_per_second")
                    ):
                        failures.append(
                            f"{filename}: invalid {mode} {operation}.operations_per_second"
                        )
                    if operation != "remove" and not finite_positive(
                        mode_report.get(operation, {}).get("bandwidth_mib_s")
                    ):
                        failures.append(
                            f"{filename}: invalid {mode} {operation}.bandwidth_mib_s"
                        )
                if not finite_nonnegative(mode_report.get("cpu_utilization")):
                    failures.append(f"{filename}: invalid {mode} cpu_utilization")
            for operation in ("put", "get", "remove"):
                metrics = ["p50_ms", "p95_ms", "p99_ms", "operations_per_second"]
                if operation != "remove":
                    metrics.append("bandwidth_mib_s")
                for metric in metrics:
                    delta = report.get("overhead", {}).get(operation, {}).get(metric)
                    if not valid_delta(delta):
                        failures.append(
                            f"{filename}: invalid overhead {operation}.{metric}"
                        )
            cpu_delta = report.get("overhead", {}).get("cpu_utilization")
            if not valid_delta(cpu_delta):
                failures.append(f"{filename}: invalid overhead cpu_utilization")
        elif filename == "transparent-software-verification.json":
            commands = report.get("commands")
            if not isinstance(commands, list) or len(commands) != 5:
                failures.append(f"{filename}: five command results are required")
            else:
                for command in commands:
                    if (
                        not isinstance(command, dict)
                        or not isinstance(command.get("command"), list)
                        or not command["command"]
                        or command.get("exit_code") != 0
                    ):
                        failures.append(f"{filename}: command verification failed")
            if report.get("commands_passed") != 5:
                failures.append(f"{filename}: commands_passed must equal 5")
            if report.get("commands_required") != 5:
                failures.append(f"{filename}: commands_required must equal 5")
    return {
        "status": "pass" if not failures else "fail",
        "required_evidence": len(required),
        "evidence": evidence,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--count", type=int, default=100)
    run.add_argument("--output", required=True)
    steady = sub.add_parser("stability")
    steady.add_argument("--seconds", type=int, default=60)
    steady.add_argument("--output", required=True)
    transparent = sub.add_parser("transparent")
    transparent.add_argument("--count", type=int, default=12)
    transparent.add_argument(
        "--expected-targets",
        required=True,
        help="comma-separated descriptor targets: local_nvme,remote_nof",
    )
    transparent.add_argument("--output", required=True)
    unavailable = sub.add_parser("transparent-unavailable")
    unavailable.add_argument(
        "--expected-target", choices=("local_nvme", "remote_nof"), required=True
    )
    unavailable.add_argument("--output", required=True)
    restart_seed = sub.add_parser("transparent-restart-seed")
    restart_seed.add_argument("--count", type=int, default=12)
    restart_seed.add_argument(
        "--expected-targets",
        required=True,
        help="comma-separated descriptor targets: local_nvme,remote_nof",
    )
    restart_seed.add_argument(
        "--scenario",
        choices=(
            "client_restart",
            "master_ha_restart",
            "local_owner_restart",
            "nof_service_restart",
        ),
        required=True,
    )
    restart_seed.add_argument("--witness")
    restart_seed.add_argument("--output", required=True)
    restart_verify = sub.add_parser("transparent-restart-verify")
    restart_verify.add_argument("--manifest", required=True)
    restart_verify.add_argument("--witness")
    restart_verify.add_argument("--output", required=True)
    benchmark = sub.add_parser("transparent-benchmark")
    benchmark.add_argument("--count", type=int, default=100)
    benchmark.add_argument("--size", type=int, default=131072)
    benchmark.add_argument(
        "--target", choices=("local_nvme", "remote_nof"), required=True
    )
    benchmark.add_argument("--mode", choices=("direct", "transparent"), required=True)
    benchmark.add_argument("--output", required=True)
    overhead = sub.add_parser("transparent-overhead")
    overhead.add_argument("--count", type=int, default=100)
    overhead.add_argument("--size", type=int, default=131072)
    overhead.add_argument(
        "--target", choices=("local_nvme", "remote_nof"), required=True
    )
    overhead.add_argument("--output", required=True)
    software = sub.add_parser("transparent-software-verification")
    software.add_argument("--repo-root", required=True)
    software.add_argument("--build-dir", required=True)
    software.add_argument("--output", required=True)
    acceptance = sub.add_parser("transparent-acceptance")
    acceptance.add_argument("--result-dir", required=True)
    acceptance.add_argument("--run-id", required=True)
    acceptance.add_argument("--output", required=True)
    child = sub.add_parser("child-read")
    child.add_argument("--key", required=True)
    child.add_argument("--seed", type=int, required=True)
    child.add_argument("--size", type=int, required=True)
    args = parser.parse_args()

    if args.command == "child-read":
        child_read(args)
        return

    if args.command == "transparent-restart-verify":
        with open(args.manifest, encoding="utf-8") as source:
            restart_manifest = json.load(source)
    else:
        restart_manifest = None

    prefix = f"nvmeof-{uuid.uuid4().hex}"
    started = time.time()
    run_id = (
        args.run_id
        if args.command == "transparent-acceptance"
        else os.environ.get("TRANSPARENT_RUN_ID", "")
    )
    if args.command.startswith("transparent") and not run_id:
        parser.error("TRANSPARENT_RUN_ID is required for transparent acceptance runs")
    result = {"status": "pass", "started_epoch": started, "run_id": run_id}
    try:
        if args.command == "run":
            sizes = [4096, 131072, 1048576, 8388608]
            exercise_mode(0, args.count, sizes, prefix)
            exercise_mode(1, args.count, sizes, prefix)
            result["unaligned"] = unaligned_probe(prefix)
            result.update({"objects_per_size": args.count, "sizes": sizes})
        elif args.command == "stability":
            result["operations"] = stability(args.seconds, prefix)
            result["duration_seconds"] = args.seconds
        elif args.command == "transparent":
            expected_targets = args.expected_targets.split(",")
            result.update(transparent_lifecycle(args.count, expected_targets, prefix))
            result.update({"objects": args.count, "expected_targets": expected_targets})
        elif args.command == "transparent-unavailable":
            result.update(transparent_unavailable(args.expected_target, prefix))
            result["expected_target"] = args.expected_target
        elif args.command == "transparent-restart-seed":
            result.update(
                transparent_restart_seed(
                    args.count,
                    args.expected_targets.split(","),
                    args.scenario,
                    prefix,
                    args.witness,
                )
            )
        elif args.command == "transparent-restart-verify":
            result.update(transparent_restart_verify(restart_manifest, args.witness))
        elif args.command == "transparent-benchmark":
            result.update(
                transparent_benchmark(
                    args.count, args.size, args.target, args.mode, prefix
                )
            )
        elif args.command == "transparent-overhead":
            result.update(
                transparent_overhead(args.count, args.size, args.target, prefix)
            )
        elif args.command == "transparent-software-verification":
            result.update(
                transparent_software_verification(args.repo_root, args.build_dir)
            )
            if result["commands_passed"] != result["commands_required"]:
                raise AssertionError("transparent software verification failed")
        else:
            result.update(transparent_acceptance(args.result_dir, args.run_id))
            if result["status"] != "pass":
                raise AssertionError("transparent acceptance evidence is incomplete")
    except Exception as error:
        result.update({"status": "fail", "error": repr(error)})
        raise
    finally:
        result["elapsed_seconds"] = time.time() - started
        with open(args.output, "w", encoding="utf-8") as output:
            json.dump(result, output, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
