#!/usr/bin/env python3
import argparse
import atexit
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path


def payload(name: str, size: int) -> bytes:
    digest = hashlib.sha256(name.encode()).digest()
    return (digest * ((size + len(digest) - 1) // len(digest)))[:size]


def phase(control_dir: Path) -> str:
    if (control_dir / "fault-cleared").exists():
        return "after"
    if (control_dir / "fault-active").exists():
        return "during"
    return "before"


def replica_statuses(store, key: str) -> list[str]:
    return [str(replica.status) for replica in store.get_replica_desc(key)]


def timed_call(function):
    started_ns = time.perf_counter_ns()
    try:
        value = function()
        return value, (time.perf_counter_ns() - started_ns) / 1_000_000, None
    except Exception as error:
        return None, (time.perf_counter_ns() - started_ns) / 1_000_000, str(error)


def connect_store():
    from correctness import connect

    return connect()


def direct_config():
    from mooncake.store import ReplicateConfig

    config = ReplicateConfig()
    config.replica_num = 0
    config.nof_replica_num = 1
    return config


def descriptor(store, key: str):
    from correctness import descriptor_fingerprint

    return descriptor_fingerprint(store, key, "remote_nof")


def remove_best_effort(store, key: str, failures: list[str]):
    try:
        rc = store.remove(key, True)
        if rc != 0:
            failures.append(f"remove {key} returned {rc}")
    except Exception as error:
        failures.append(f"remove {key}: {error}")


def sample_failed_put(store, key: str):
    statuses_before, descriptors_ms, descriptors_error = timed_call(
        lambda: replica_statuses(store, key)
    )
    get_before, get_before_ms, get_before_error = timed_call(lambda: store.get(key))
    remove_rc, remove_ms, remove_error = timed_call(lambda: store.remove(key, True))
    statuses_after, verify_ms, verify_error = timed_call(
        lambda: replica_statuses(store, key)
    )
    get_after, get_after_ms, get_after_error = timed_call(lambda: store.get(key))
    statuses_before = statuses_before or []
    statuses_after = statuses_after or []
    terminal = (
        remove_error is None
        and verify_error is None
        and get_after_error is None
        and not statuses_after
        and get_after in (b"", None)
        and remove_rc in (0, -704)
    )
    return {
        "epoch": time.time(),
        "statuses_before": statuses_before,
        "descriptors_ms": descriptors_ms,
        "descriptors_error": descriptors_error,
        "readable_before": get_before not in (b"", None),
        "get_before_ms": get_before_ms,
        "get_before_error": get_before_error,
        "remove_return_code": remove_rc,
        "remove_ms": remove_ms,
        "remove_error": remove_error,
        "statuses_after": statuses_after,
        "verify_ms": verify_ms,
        "verify_error": verify_error,
        "readable_after": get_after not in (b"", None),
        "get_after_ms": get_after_ms,
        "get_after_error": get_after_error,
        "terminal": terminal,
    }


def audit_failed_puts(store, keys: list[str], fault_cleared_epoch: float, timeout_sec: float):
    deadline = fault_cleared_epoch + timeout_sec
    audits = {
        key: {
            "key": key,
            "samples": [],
            "published_complete_replica": False,
            "ever_readable": False,
            "residue_cleared": False,
            "residue_clearance_ms": None,
            "observation_errors": [],
        }
        for key in keys
    }
    pending = set(keys)
    while pending and time.time() < deadline:
        for key in sorted(pending):
            if time.time() >= deadline:
                break
            sample = sample_failed_put(store, key)
            audit = audits[key]
            audit["samples"].append(sample)
            audit["published_complete_replica"] = audit["published_complete_replica"] or any(
                status.endswith("COMPLETE")
                for status in sample["statuses_before"] + sample["statuses_after"]
            )
            audit["ever_readable"] = audit["ever_readable"] or sample["readable_before"] or sample["readable_after"]
            audit["observation_errors"].extend(
                sample[field]
                for field in (
                    "descriptors_error",
                    "get_before_error",
                    "remove_error",
                    "verify_error",
                    "get_after_error",
                )
                if sample[field] is not None
            )
            if sample["terminal"] and sample["epoch"] <= deadline:
                audit["residue_cleared"] = True
                audit["residue_clearance_ms"] = (sample["epoch"] - fault_cleared_epoch) * 1000
                pending.remove(key)
        if pending:
            time.sleep(min(0.25, max(0, deadline - time.time())))

    for key in sorted(pending):
        sample = sample_failed_put(store, key)
        audit = audits[key]
        sample["deadline_probe"] = True
        audit["samples"].append(sample)
        audit["published_complete_replica"] = audit["published_complete_replica"] or any(
            status.endswith("COMPLETE")
            for status in sample["statuses_before"] + sample["statuses_after"]
        )
        audit["ever_readable"] = audit["ever_readable"] or sample["readable_before"] or sample["readable_after"]
        final_probe_errors = [
            sample[field]
            for field in (
                "descriptors_error",
                "get_before_error",
                "remove_error",
                "verify_error",
                "get_after_error",
            )
            if sample[field] is not None
        ]
        audit["observation_errors"].extend(final_probe_errors)
        audit["audit_error"] = "; ".join(audit["observation_errors"]) if audit["observation_errors"] else None
        audit["cleanup_deadline_exceeded"] = not final_probe_errors and not audit["residue_cleared"]

    for key in sorted(set(keys) - pending):
        audits[key]["audit_error"] = (
            "; ".join(audits[key]["observation_errors"])
            if audits[key]["observation_errors"]
            else None
        )
        audits[key]["cleanup_deadline_exceeded"] = False
    return [audits[key] for key in sorted(keys)]


def run(args):
    control_dir = Path(args.control_dir)
    result_path = Path(args.output)
    control_dir.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, args.harness_dir)

    store = connect_store()
    config = direct_config() if args.mode == "direct" else None
    seed_key = f"{args.run_id}-{args.mode}-trial{args.trial}-seed"
    seed_value = payload(seed_key, args.block_size)
    successful_keys = set()
    failed_put_keys = set()
    operations = []
    cleanup_failures = []
    persistent_client_recovered = False
    cleanup_state = {"done": False}

    def emergency_cleanup():
        if cleanup_state["done"]:
            return
        deadline = time.time() + 10
        while (control_dir / "fault-active").exists() and not (control_dir / "fault-cleared").exists() and time.time() < deadline:
            time.sleep(0.1)
        emergency_failures = []
        for cleanup_key in sorted(successful_keys | failed_put_keys | {seed_key}):
            remove_best_effort(store, cleanup_key, emergency_failures)
        try:
            store.close()
        except Exception:
            pass

    atexit.register(emergency_cleanup)

    seed_rc = store.put(seed_key, seed_value, config) if config else store.put(seed_key, seed_value)
    if seed_rc != 0:
        raise RuntimeError(f"seed put returned {seed_rc}")
    seed_descriptor = descriptor(store, seed_key)
    if store.get(seed_key) != seed_value:
        raise AssertionError("seed content mismatch before fault")
    (control_dir / "probe-ready").write_text(f"{time.time():.9f}\n")

    started_epoch = time.time()
    index = 0
    while time.time() - started_epoch < args.max_runtime_sec:
        current_phase = phase(control_dir)
        key = f"{args.run_id}-{args.mode}-trial{args.trial}-request{index:04d}"
        value = payload(key, args.block_size)
        record = {
            "index": index,
            "key": key,
            "phase_at_start": current_phase,
            "started_epoch": time.time(),
            "store_generation": 1,
        }

        put_value, put_ms, put_error = timed_call(
            lambda: store.put(key, value, config) if config else store.put(key, value)
        )
        record.update({"put_return_code": put_value, "put_ms": put_ms, "put_error": put_error})
        if put_error is not None or put_value != 0:
            failed_put_keys.add(key)
            record["status"] = "put_failed"
        else:
            successful_keys.add(key)
            desc_value, desc_ms, desc_error = timed_call(lambda: descriptor(store, key))
            record.update({"descriptor": desc_value, "descriptor_ms": desc_ms, "descriptor_error": desc_error})
            get_value, get_ms, get_error = timed_call(lambda: store.get(key))
            record.update({"get_ms": get_ms, "get_error": get_error, "get_matches": get_value == value})
            remove_value, remove_ms, remove_error = timed_call(lambda: store.remove(key, True))
            record.update({"remove_return_code": remove_value, "remove_ms": remove_ms, "remove_error": remove_error})
            if remove_error is None and remove_value == 0:
                successful_keys.discard(key)
            if desc_error is None and get_error is None and get_value == value and remove_error is None and remove_value == 0:
                record["status"] = "pass"
            else:
                record["status"] = "post_put_failure"

        record["completed_epoch"] = time.time()
        operations.append(record)
        before_passes = sum(item["status"] == "pass" and item["phase_at_start"] == "before" for item in operations)
        during_failures = sum(item["status"] != "pass" and item["phase_at_start"] == "during" for item in operations)
        after_passes = sum(item["status"] == "pass" and item["phase_at_start"] == "after" for item in operations)

        if before_passes >= args.baseline_requests and not (control_dir / "ready-to-fault").exists():
            (control_dir / "ready-to-fault").write_text(f"{time.time():.9f}\n")

        if current_phase == "after":
            if record["status"] == "pass":
                persistent_client_recovered = True
            if after_passes >= args.recovery_requests and during_failures >= 1:
                break

        index += 1
        time.sleep(args.inter_request_ms / 1000)

    fault_started_epoch = float((control_dir / "fault-started-epoch.txt").read_text())
    fault_cleared_epoch = float((control_dir / "fault-cleared-epoch.txt").read_text())
    failed_put_audit = audit_failed_puts(
        store, sorted(failed_put_keys), fault_cleared_epoch, args.residue_timeout_sec
    )

    seed_after, seed_get_ms, seed_get_error = timed_call(lambda: store.get(seed_key))
    seed_descriptor_after, seed_descriptor_ms, seed_descriptor_error = timed_call(
        lambda: descriptor(store, seed_key)
    )
    seed_verified_after = seed_get_error is None and seed_after == seed_value
    seed_descriptor_stable = seed_descriptor_error is None and seed_descriptor_after == seed_descriptor
    remove_best_effort(store, seed_key, cleanup_failures)
    for key in sorted(successful_keys):
        remove_best_effort(store, key, cleanup_failures)
    original_client_close_epoch = None
    try:
        store.close()
        original_client_close_epoch = time.time()
    except Exception as error:
        cleanup_failures.append(f"close: {error}")
    post_close_audit_started_epoch = time.time()
    store = connect_store()
    post_close_failed_put_audit = audit_failed_puts(
        store,
        sorted(failed_put_keys),
        post_close_audit_started_epoch,
        args.post_close_residue_timeout_sec,
    )
    try:
        store.close()
    except Exception as error:
        cleanup_failures.append(f"post-close audit client close: {error}")
    cleanup_state["done"] = True

    before_passes = [item for item in operations if item["status"] == "pass" and item["completed_epoch"] < fault_started_epoch]
    fault_failures = [item for item in operations if item["status"] != "pass" and item["started_epoch"] <= fault_cleared_epoch and item["completed_epoch"] >= fault_started_epoch]
    after_passes = [item for item in operations if item["status"] == "pass" and item["started_epoch"] >= fault_cleared_epoch]
    first_failure_epoch = min((item["completed_epoch"] for item in fault_failures), default=None)
    first_recovery_epoch = min((item["completed_epoch"] for item in after_passes), default=None)
    incomplete_failed_put_audits = [
        item
        for item in failed_put_audit
        if item["audit_error"] is not None
    ]
    published_failed_puts = [
        item
        for item in failed_put_audit
        if item["published_complete_replica"] or item["ever_readable"]
    ]
    residue_deadline_failures = [
        item for item in failed_put_audit if item["cleanup_deadline_exceeded"]
    ]
    post_close_incomplete_audits = [
        item for item in post_close_failed_put_audit if item["audit_error"] is not None
    ]
    post_close_unsafe_failed_puts = [
        item
        for item in post_close_failed_put_audit
        if item["published_complete_replica"] or item["ever_readable"]
    ]
    post_close_residue_deadline_failures = [
        item
        for item in post_close_failed_put_audit
        if item["cleanup_deadline_exceeded"]
    ]
    evidence_failures = []
    product_failures = []
    if len(before_passes) < args.baseline_requests:
        evidence_failures.append("insufficient pre-fault successes")
    if not fault_failures:
        evidence_failures.append("no operation failure observed during fault")
    if len(after_passes) < args.recovery_requests:
        product_failures.append("insufficient post-fault successes")
    if not persistent_client_recovered:
        product_failures.append("persistent client did not recover")
    if not seed_verified_after or not seed_descriptor_stable:
        product_failures.append("pre-fault seed did not survive recovery")
    if published_failed_puts:
        product_failures.append("failed put became readable or published a complete replica")
    if residue_deadline_failures:
        product_failures.append("failed put residue exceeded cleanup deadline")
    if incomplete_failed_put_audits:
        evidence_failures.append("failed put audit was incomplete")
    if post_close_incomplete_audits:
        evidence_failures.append("post-client-close failed put audit was incomplete")
    if post_close_unsafe_failed_puts:
        product_failures.append("failed put became readable after original client close")
    if post_close_residue_deadline_failures:
        product_failures.append("failed put residue exceeded post-client-close deadline")
    product_failures.extend(cleanup_failures)
    failures = evidence_failures + product_failures
    status = "fail" if product_failures else "inconclusive" if evidence_failures else "pass"

    result = {
        "status": status,
        "run_id": args.run_id,
        "trial": args.trial,
        "mode": args.mode,
        "target": "remote_nof",
        "configured_concurrency": 1,
        "block_size": args.block_size,
        "rpc_timeout_ms": int(os.environ["MC_RPC_TIMEOUT_MS"]),
        "fault": {
            "kind": "client_to_master_tcp_drop",
            "destination": os.environ["MASTER_ADDR"],
            "started_epoch": fault_started_epoch,
            "cleared_epoch": fault_cleared_epoch,
            "duration_ms": (fault_cleared_epoch - fault_started_epoch) * 1000,
        },
        "pre_fault_successes": len(before_passes),
        "fault_window_failures": len(fault_failures),
        "post_fault_successes": len(after_passes),
        "first_failure_detection_ms": None if first_failure_epoch is None else (first_failure_epoch - fault_started_epoch) * 1000,
        "recovery_latency_ms": None if first_recovery_epoch is None else (first_recovery_epoch - fault_cleared_epoch) * 1000,
        "persistent_client_recovered": persistent_client_recovered,
        "workload_store_generations": 1,
        "post_close_audit_store_generation": 2,
        "seed": {
            "key": seed_key,
            "descriptor_before": seed_descriptor,
            "descriptor_after": seed_descriptor_after,
            "get_ms_after": seed_get_ms,
            "descriptor_ms_after": seed_descriptor_ms,
            "verified_after": seed_verified_after,
            "descriptor_stable": seed_descriptor_stable,
        },
        "failed_put_audit": failed_put_audit,
        "original_client_close_epoch": original_client_close_epoch,
        "post_close_audit_started_epoch": post_close_audit_started_epoch,
        "post_close_failed_put_audit": post_close_failed_put_audit,
        "incomplete_failed_put_audits": incomplete_failed_put_audits,
        "published_failed_puts": published_failed_puts,
        "residue_deadline_failures": residue_deadline_failures,
        "post_close_incomplete_audits": post_close_incomplete_audits,
        "post_close_unsafe_failed_puts": post_close_unsafe_failed_puts,
        "post_close_residue_deadline_failures": post_close_residue_deadline_failures,
        "cleanup_failures": cleanup_failures,
        "operations": operations,
        "evidence_failures": evidence_failures,
        "product_failures": product_failures,
        "failures": failures,
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--mode", choices=("direct", "transparent"), required=True)
    parser.add_argument("--control-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--harness-dir", required=True)
    parser.add_argument("--block-size", type=int, default=131072)
    parser.add_argument("--baseline-requests", type=int, default=8)
    parser.add_argument("--recovery-requests", type=int, default=8)
    parser.add_argument("--max-runtime-sec", type=float, default=35)
    parser.add_argument("--residue-timeout-sec", type=float, default=15)
    parser.add_argument("--post-close-residue-timeout-sec", type=float, default=15)
    parser.add_argument("--inter-request-ms", type=float, default=50)
    args = parser.parse_args()
    try:
        return_code = run(args)
    except Exception as error:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(
                {
                    "status": "inconclusive",
                    "run_id": args.run_id,
                    "trial": args.trial,
                    "mode": args.mode,
                    "target": "remote_nof",
                    "configured_concurrency": 1,
                    "evidence_failures": [f"uncaught probe error: {error}"],
                    "product_failures": [],
                    "traceback": traceback.format_exc(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        raise
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
