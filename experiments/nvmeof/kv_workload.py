"""Deterministic KV-cache workload trace generation and validation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
REPLAY_SCHEMA_VERSION = 1
OPERATIONS = frozenset(("produce", "reuse", "evict", "miss"))
POLICIES = frozenset(("local_only", "remote_only", "round_robin"))
REPLAY_MODES = frozenset(("no_store", "direct", "transparent"))
DESCRIPTOR_TARGETS = frozenset(("local_nvme", "remote_nof"))
COMPACT_EVIDENCE_SCHEMA_VERSION = 1
COMPACT_FAILURE_RECORD_LIMIT = 16
SUMMARY_METRIC_FIELDS = (
    "operations",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "operation_rate",
    "request_count",
    "request_hit_rate",
    "block_hit_rate",
    "miss_rate",
    "request_p50_latency_us",
    "request_p95_latency_us",
    "request_arrival_lag_p50_us",
    "request_arrival_lag_p95_us",
    "request_arrival_lag_max_us",
    "storage_wait_us",
    "replay_scale",
    "scheduled_span_us",
    "processing_wall_us",
    "completion_lag_us",
    "produce",
    "reuse",
    "evict",
    "miss",
    "local",
    "remote",
)


@dataclass(frozen=True)
class TraceEvent:
    timestamp_us: int
    request_id: str
    prefix_id: str
    block_id: str
    block_size: int
    operation: str
    policy: str

    def __post_init__(self) -> None:
        if self.timestamp_us < 0:
            raise ValueError("timestamp_us must be non-negative")
        if not self.request_id or not self.prefix_id or not self.block_id:
            raise ValueError("request_id, prefix_id, and block_id are required")
        if self.block_size <= 0:
            raise ValueError("block_size must be positive")
        if self.operation not in OPERATIONS:
            raise ValueError(f"unsupported operation: {self.operation}")
        if self.policy not in POLICIES:
            raise ValueError(f"unsupported policy: {self.policy}")


def _event_from_dict(value: dict) -> TraceEvent:
    fields = {field.name for field in TraceEvent.__dataclass_fields__.values()}
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise ValueError(f"unknown trace fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing trace fields: {sorted(missing)}")
    return TraceEvent(**value)


def validate_trace(events: Iterable[TraceEvent]) -> list[TraceEvent]:
    """Validate event ordering and return a materialized event list."""

    materialized = list(events)
    produced: set[str] = set()
    evicted: set[str] = set()
    last_timestamp = -1
    block_sizes: dict[str, int] = {}
    for event in materialized:
        if event.timestamp_us < last_timestamp:
            raise ValueError("trace timestamps must be non-decreasing")
        last_timestamp = event.timestamp_us
        previous_size = block_sizes.setdefault(event.block_id, event.block_size)
        if previous_size != event.block_size:
            raise ValueError(f"block size changed for {event.block_id}")
        if event.operation == "produce":
            if event.block_id in produced and event.block_id not in evicted:
                raise ValueError(
                    f"block produced twice without eviction: {event.block_id}"
                )
            produced.add(event.block_id)
            evicted.discard(event.block_id)
        elif event.operation in ("reuse", "evict"):
            if event.block_id not in produced or event.block_id in evicted:
                raise ValueError(
                    f"{event.operation} requires a live produced block: {event.block_id}"
                )
            if event.operation == "evict":
                evicted.add(event.block_id)
        elif event.operation == "miss" and event.block_id in evicted:
            raise ValueError(
                f"miss cannot reference an evicted block: {event.block_id}"
            )
    return materialized


def generate_trace(
    *,
    requests: int = 12,
    blocks_per_request: int = 4,
    block_size: int = 131072,
    reuse_ratio: float = 0.5,
    concurrency: int = 1,
    policy: str = "round_robin",
    seed: int = 0,
) -> list[TraceEvent]:
    """Generate a deterministic request trace with shared-prefix reuse."""

    if requests <= 0 or blocks_per_request <= 0 or concurrency <= 0:
        raise ValueError(
            "requests, blocks_per_request, and concurrency must be positive"
        )
    if not 0 <= reuse_ratio <= 1:
        raise ValueError("reuse_ratio must be between 0 and 1")
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")

    rng = random.Random(seed)
    events: list[TraceEvent] = []
    prefixes: list[tuple[str, tuple[str, ...]]] = []
    timestamp = 0
    for request_index in range(requests):
        request_id = f"request-{request_index:04d}"
        can_reuse = bool(prefixes) and rng.random() < reuse_ratio
        if can_reuse:
            prefix_id, block_ids = prefixes[rng.randrange(len(prefixes))]
            operation = "reuse"
        else:
            prefix_id = f"prefix-{request_index:04d}"
            block_ids = tuple(
                f"{prefix_id}-block-{block_index:04d}"
                for block_index in range(blocks_per_request)
            )
            prefixes.append((prefix_id, block_ids))
            operation = "produce"

        for block_id in block_ids:
            events.append(
                TraceEvent(
                    timestamp_us=timestamp,
                    request_id=request_id,
                    prefix_id=prefix_id,
                    block_id=block_id,
                    block_size=block_size,
                    operation=operation,
                    policy=policy,
                )
            )
            timestamp += max(1, 1_000 // concurrency)

    final_request_id = f"request-{requests:04d}-cleanup"
    for prefix_id, block_ids in prefixes:
        for block_id in block_ids:
            events.append(
                TraceEvent(
                    timestamp_us=timestamp,
                    request_id=final_request_id,
                    prefix_id=prefix_id,
                    block_id=block_id,
                    block_size=block_size,
                    operation="evict",
                    policy=policy,
                )
            )
            timestamp += 1
    return validate_trace(events)


def write_trace(path: str | Path, events: Iterable[TraceEvent]) -> str:
    materialized = validate_trace(events)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(
        json.dumps(asdict(event), sort_keys=True, separators=(",", ":")) + "\n"
        for event in materialized
    )
    output.write_text(encoded, encoding="utf-8")
    return hashlib.sha256(encoded.encode()).hexdigest()


def read_trace(path: str | Path) -> list[TraceEvent]:
    events = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            raise ValueError(f"blank trace line at {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON at trace line {line_number}") from error
        if not isinstance(value, dict):
            raise ValueError(f"trace line {line_number} must be an object")
        events.append(_event_from_dict(value))
    return validate_trace(events)


def manifest_for(
    events: Iterable[TraceEvent], *, seed: int, parameters: dict[str, object]
) -> dict[str, object]:
    materialized = validate_trace(events)
    encoded = "".join(
        json.dumps(asdict(event), sort_keys=True, separators=(",", ":")) + "\n"
        for event in materialized
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "parameters": parameters,
        "event_count": len(materialized),
        "trace_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def _connect_store() -> Any:
    from correctness import connect

    return connect()


def _direct_config(target: str) -> Any:
    from mooncake.store import ReplicateConfig

    config = ReplicateConfig()
    config.replica_num = 0
    if target == "remote_nof":
        config.nof_replica_num = 1
    elif target == "local_nvme":
        if not hasattr(config, "local_replica_num"):
            raise RuntimeError(
                "installed Mooncake Python binding does not support local_replica_num"
            )
        config.local_replica_num = 1
    else:
        raise ValueError(f"unsupported replay target: {target}")
    return config


def _descriptor_for(
    store: Any, key: str, expected_target: str | None
) -> dict[str, Any]:
    from correctness import descriptor_fingerprint

    if expected_target is not None:
        return descriptor_fingerprint(store, key, expected_target)

    failures = []
    for candidate in ("local_nvme", "remote_nof"):
        try:
            return descriptor_fingerprint(store, key, candidate)
        except AssertionError as error:
            failures.append(str(error))
    raise AssertionError(f"unsupported or incomplete descriptor for {key}: {failures}")


def _block_payload(block_id: str, block_size: int) -> bytes:
    seed = hashlib.sha256(block_id.encode()).digest()
    repeats = (block_size + len(seed) - 1) // len(seed)
    return (seed * repeats)[:block_size]


def _new_compact_state(sample_limit: int) -> dict[str, Any]:
    return {
        "sample_limit": sample_limit,
        "sample_heap": [],
        "operation_latencies": [],
        "request_latencies": {},
        "request_arrival_lags": {},
        "hit_requests": set(),
        "operation_counts": {operation: 0 for operation in sorted(OPERATIONS)},
        "store_operation_counts": {
            operation: 0 for operation in ("put", "get", "remove", "recompute", "noop")
        },
        "descriptor_target_counts": {
            target: 0 for target in sorted(DESCRIPTOR_TARGETS)
        },
        "descriptor_policy_target_counts": {},
        "descriptor_hasher": hashlib.sha256(),
        "content_checks": 0,
        "content_mismatches": 0,
        "descriptor_mismatches": 0,
        "return_code_failures": 0,
        "blocks_seen": 0,
        "block_hits": 0,
        "misses": 0,
        "storage_wait_us": 0.0,
        "operation_count": 0,
        "error_count": 0,
        "failure_records": [],
    }


def _record_compact_operation(state: dict[str, Any], record: dict[str, Any]) -> None:
    state["operation_count"] += 1
    operation = record["operation"]
    store_operation = record["store_operation"] or "noop"
    latency = float(record["latency_us"])
    state["operation_counts"][operation] += 1
    state["store_operation_counts"][store_operation] += 1
    state["operation_latencies"].append(latency)
    if operation in ("produce", "reuse"):
        state["blocks_seen"] += 1
    if operation == "reuse":
        state["block_hits"] += 1
    if operation == "miss":
        state["misses"] += 1
    if store_operation in ("put", "get", "remove"):
        state["storage_wait_us"] += latency
    request_id = record["request_id"]
    if request_id != "cleanup" and not request_id.endswith("-cleanup"):
        state["request_latencies"][request_id] = (
            state["request_latencies"].get(request_id, 0.0) + latency
        )
        state["request_arrival_lags"].setdefault(
            request_id, record["arrival_lag_us"]
        )
        if operation == "reuse":
            state["hit_requests"].add(request_id)
    descriptor = record.get("descriptor") or {}
    descriptor_target = descriptor.get("target")
    if descriptor_target in DESCRIPTOR_TARGETS:
        state["descriptor_target_counts"][descriptor_target] += 1
        policy_counts = state["descriptor_policy_target_counts"].setdefault(
            record["policy"], {target: 0 for target in sorted(DESCRIPTOR_TARGETS)}
        )
        policy_counts[descriptor_target] += 1
        proof = {
            "event_index": record["event_index"],
            "operation": operation,
            "block_id": record["block_id"],
            "descriptor": descriptor,
        }
        state["descriptor_hasher"].update(
            json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
        )
        state["descriptor_hasher"].update(b"\n")
    if record["return_code"] != 0:
        state["return_code_failures"] += 1
    if (
        record["error"] is not None
        and len(state["failure_records"]) < COMPACT_FAILURE_RECORD_LIMIT
    ):
        state["failure_records"].append(record.copy())

    sample_identity = (
        f"{record['event_index']}\0{request_id}\0{record['block_id']}\0{operation}"
    )
    rank = int.from_bytes(hashlib.sha256(sample_identity.encode()).digest(), "big")
    entry = (-rank, -record["event_index"], record.copy())
    sample_heap = state["sample_heap"]
    if len(sample_heap) < state["sample_limit"]:
        heapq.heappush(sample_heap, entry)
    elif rank < -sample_heap[0][0]:
        heapq.heapreplace(sample_heap, entry)


def _finalize_compact_evidence(
    state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    request_latencies = list(state["request_latencies"].values())
    request_arrival_lags = [
        float(value)
        for value in state["request_arrival_lags"].values()
        if value is not None
    ]
    operation_latencies = state["operation_latencies"]
    latency_total = sum(operation_latencies)
    request_count = len(state["request_latencies"])
    blocks_seen = state["blocks_seen"]
    samples = [
        entry[2]
        for entry in sorted(
            state["sample_heap"], key=lambda entry: (-entry[0], -entry[1])
        )
    ]
    sample_indices = [record["event_index"] for record in samples]
    sample_digest = hashlib.sha256(
        json.dumps(sample_indices, separators=(",", ":")).encode()
    ).hexdigest()
    sample_records_digest = hashlib.sha256(
        json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pacing = result.get("pacing", {})
    summary = {
        "operations": state["operation_count"],
        "p50_latency_us": _percentile(operation_latencies, 50),
        "p95_latency_us": _percentile(operation_latencies, 95),
        "p99_latency_us": _percentile(operation_latencies, 99),
        "operation_rate": round(
            state["operation_count"] / (latency_total / 1_000_000), 6
        )
        if latency_total > 0
        else None,
        "request_count": request_count,
        "request_hit_rate": round(
            len(state["hit_requests"]) / request_count, 6
        )
        if request_count
        else None,
        "block_hit_rate": round(state["block_hits"] / blocks_seen, 6)
        if blocks_seen
        else None,
        "miss_rate": round(state["misses"] / state["operation_count"], 6)
        if state["operation_count"]
        else None,
        "request_p50_latency_us": _percentile(request_latencies, 50),
        "request_p95_latency_us": _percentile(request_latencies, 95),
        "request_arrival_lag_p50_us": _percentile(request_arrival_lags, 50),
        "request_arrival_lag_p95_us": _percentile(request_arrival_lags, 95),
        "request_arrival_lag_max_us": round(max(request_arrival_lags), 6)
        if request_arrival_lags
        else None,
        "storage_wait_us": round(state["storage_wait_us"], 6),
        "replay_scale": pacing.get("replay_scale", 0),
        "scheduled_span_us": pacing.get("scheduled_span_us"),
        "processing_wall_us": pacing.get("processing_wall_us"),
        "completion_lag_us": pacing.get("completion_lag_us"),
        "produce": state["operation_counts"]["produce"],
        "reuse": state["operation_counts"]["reuse"],
        "evict": state["operation_counts"]["evict"],
        "miss": state["operation_counts"]["miss"],
        "local": state["descriptor_target_counts"]["local_nvme"],
        "remote": state["descriptor_target_counts"]["remote_nof"],
    }
    summary_digest = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": COMPACT_EVIDENCE_SCHEMA_VERSION,
        "sample_limit": state["sample_limit"],
        "samples": samples,
        "sample_event_indices_sha256": sample_digest,
        "sample_records_sha256": sample_records_digest,
        "operation_count": state["operation_count"],
        "operation_latency_total_us": round(latency_total, 6),
        "operation_counts": state["operation_counts"],
        "store_operation_counts": state["store_operation_counts"],
        "descriptor_checks": sum(state["descriptor_target_counts"].values()),
        "descriptor_target_counts": state["descriptor_target_counts"],
        "descriptor_policy_target_counts": state[
            "descriptor_policy_target_counts"
        ],
        "descriptor_proof_sha256": state["descriptor_hasher"].hexdigest(),
        "content_checks": state["content_checks"],
        "content_mismatches": state["content_mismatches"],
        "descriptor_mismatches": state["descriptor_mismatches"],
        "return_code_failures": state["return_code_failures"],
        "request_count": request_count,
        "hit_request_count": len(state["hit_requests"]),
        "failure_record_limit": COMPACT_FAILURE_RECORD_LIMIT,
        "failure_records": state["failure_records"],
        "error_count": state["error_count"],
        "error_records_truncated": state["error_count"] > len(result.get("errors", [])),
        "summary": summary,
        "summary_sha256": summary_digest,
    }


def replay_trace(
    events: Iterable[TraceEvent],
    *,
    mode: str,
    target: str | None = None,
    key_prefix: str = "kv-workload",
    recompute_us: int = 1_000,
    store_factory: Callable[[], Any] = _connect_store,
    config_factory: Callable[[str], Any] = _direct_config,
    descriptor_reader: Callable[[Any, str, str | None], dict[str, Any]] = (
        _descriptor_for
    ),
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    sleeper: Callable[[float], None] = time.sleep,
    replay_scale: float = 0.0,
    compact_evidence: bool = False,
    compact_sample_limit: int = 128,
) -> dict[str, Any]:
    """Replay a validated trace in timestamp order and return raw case results.

    Store failures are captured in the returned result and stop the case.  The
    injectable helpers keep the Store contract testable without Mooncake hardware.
    """

    materialized = validate_trace(events)
    if mode not in REPLAY_MODES:
        raise ValueError(f"unsupported replay mode: {mode}")
    if recompute_us < 0:
        raise ValueError("recompute_us must be non-negative")
    if not math.isfinite(replay_scale) or replay_scale < 0:
        raise ValueError("replay_scale must be finite and non-negative")
    if target is not None and target not in ("local_nvme", "remote_nof"):
        raise ValueError(f"unsupported replay target: {target}")
    if mode == "direct" and target not in ("local_nvme", "remote_nof"):
        raise ValueError("direct replay requires local_nvme or remote_nof target")
    if not key_prefix:
        raise ValueError("key_prefix is required")
    if compact_sample_limit <= 0:
        raise ValueError("compact_sample_limit must be positive")

    result: dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "status": "pass",
        "mode": mode,
        "target": target,
        "event_count": len(materialized),
        "errors": [],
        "recompute_model": (
            {"kind": "fixed_proxy", "latency_us": recompute_us}
            if mode == "no_store"
            else None
        ),
        "pacing": {
            "enabled": replay_scale > 0,
            "replay_scale": replay_scale,
            "source_timestamp_unit": "microseconds",
        },
    }
    compact_state = (
        _new_compact_state(compact_sample_limit) if compact_evidence else None
    )
    if compact_evidence:
        result["evidence_mode"] = "compact"
    else:
        result["operations"] = []
    if not materialized:
        if compact_state is not None:
            compact_state["error_count"] = len(result["errors"])
            result["evidence"] = _finalize_compact_evidence(compact_state, result)
        return result

    store = None
    config = None
    live_keys: set[str] = set()
    active_keys: dict[str, str] = {}
    block_generations: dict[str, int] = {}
    descriptors: dict[str, dict[str, Any]] = {}
    pacing_started_ns: int | None = None
    first_timestamp_us = materialized[0].timestamp_us
    schedule_sleep_us = 0.0
    modeled_work_sleep_us = 0.0
    max_arrival_lag_us = 0.0
    try:
        if mode != "no_store":
            store = store_factory()
            if mode == "direct":
                config = config_factory(target)
        if replay_scale > 0:
            pacing_started_ns = clock_ns()

        for event_index, event in enumerate(materialized):
            scheduled_offset_us = None
            arrival_lag_us = None
            if pacing_started_ns is not None:
                scheduled_offset_us = (
                    event.timestamp_us - first_timestamp_us
                ) / replay_scale
                now_ns = clock_ns()
                delay_us = scheduled_offset_us - ((now_ns - pacing_started_ns) / 1_000)
                if delay_us > 0:
                    sleeper(delay_us / 1_000_000)
                    schedule_sleep_us += delay_us
                    now_ns = clock_ns()
                arrival_lag_us = max(
                    0.0,
                    (now_ns - pacing_started_ns) / 1_000 - scheduled_offset_us,
                )
                max_arrival_lag_us = max(max_arrival_lag_us, arrival_lag_us)
            if event.operation == "produce":
                generation = block_generations.get(event.block_id, 0) + 1
                block_generations[event.block_id] = generation
                key = f"{key_prefix}-{event.block_id}-generation-{generation:06d}"
                active_keys[event.block_id] = key
            elif event.operation in ("reuse", "evict"):
                key = active_keys[event.block_id]
            else:
                key = f"{key_prefix}-{event.block_id}-miss"
            record: dict[str, Any] = {
                "event_index": event_index,
                "timestamp_us": event.timestamp_us,
                "request_id": event.request_id,
                "prefix_id": event.prefix_id,
                "key": key,
                "block_id": event.block_id,
                "block_size": event.block_size,
                "operation": event.operation,
                "policy": event.policy,
                "target_policy": event.policy,
                "store_operation": None,
                "descriptor": None,
                "return_code": 0,
                "latency_us": 0.0,
                "scheduled_offset_us": scheduled_offset_us,
                "arrival_lag_us": arrival_lag_us,
                "error": None,
            }
            if not compact_evidence:
                result["operations"].append(record)
            operation_failed = False
            try:
                if event.operation == "miss" or mode == "no_store":
                    record["store_operation"] = (
                        "noop" if event.operation == "evict" else "recompute"
                    )
                    record["latency_us"] = (
                        0.0 if event.operation == "evict" else float(recompute_us)
                    )
                    if event.operation == "evict":
                        active_keys.pop(event.block_id)
                    elif pacing_started_ns is not None and recompute_us > 0:
                        sleeper(recompute_us / 1_000_000)
                        modeled_work_sleep_us += recompute_us
                else:
                    payload = _block_payload(event.block_id, event.block_size)
                    started_ns = clock_ns()
                    if event.operation == "produce":
                        record["store_operation"] = "put"
                        rc = (
                            store.put(key, payload)
                            if config is None
                            else store.put(key, payload, config)
                        )
                        record["latency_us"] = (clock_ns() - started_ns) / 1_000
                        record["return_code"] = rc
                        if rc != 0:
                            raise RuntimeError(f"put returned {rc}")
                        live_keys.add(key)
                        expected_target = target
                        if mode == "transparent" and expected_target is None:
                            expected_target = {
                                "local_only": "local_nvme",
                                "remote_only": "remote_nof",
                                "round_robin": None,
                            }[event.policy]
                        try:
                            descriptor = descriptor_reader(store, key, expected_target)
                        except Exception:
                            if compact_state is not None:
                                compact_state["descriptor_mismatches"] += 1
                            raise
                        if descriptor.get("object_size") != event.block_size:
                            if compact_state is not None:
                                compact_state["descriptor_mismatches"] += 1
                            raise AssertionError(
                                f"descriptor size mismatch for {key}: "
                                f"{descriptor.get('object_size')} != {event.block_size}"
                            )
                        descriptors[event.block_id] = descriptor
                        record["descriptor"] = descriptor
                    elif event.operation == "reuse":
                        record["store_operation"] = "get"
                        actual = store.get(key)
                        record["latency_us"] = (clock_ns() - started_ns) / 1_000
                        if compact_state is not None:
                            compact_state["content_checks"] += 1
                        if actual != payload:
                            if compact_state is not None:
                                compact_state["content_mismatches"] += 1
                            raise AssertionError(f"content mismatch for {key}")
                        expected = descriptors[event.block_id]
                        try:
                            descriptor = descriptor_reader(
                                store, key, expected["target"]
                            )
                        except Exception:
                            if compact_state is not None:
                                compact_state["descriptor_mismatches"] += 1
                            raise
                        if descriptor != expected:
                            if compact_state is not None:
                                compact_state["descriptor_mismatches"] += 1
                            raise AssertionError(f"descriptor changed for {key}")
                        record["descriptor"] = descriptor
                    elif event.operation == "evict":
                        record["store_operation"] = "remove"
                        record["descriptor"] = descriptors[event.block_id]
                        rc = store.remove(key, True)
                        record["latency_us"] = (clock_ns() - started_ns) / 1_000
                        record["return_code"] = rc
                        if rc != 0:
                            raise RuntimeError(f"remove returned {rc}")
                        live_keys.discard(key)
                        active_keys.pop(event.block_id)
                        descriptors.pop(event.block_id, None)
            except Exception as error:
                record["error"] = str(error)
                result["errors"].append(
                    {"event_index": event_index, "key": key, "error": str(error)}
                )
                result["status"] = "fail"
                operation_failed = True
            if compact_state is not None:
                _record_compact_operation(compact_state, record)
            if operation_failed:
                break
    except Exception as error:
        result["errors"].append({"event_index": None, "key": None, "error": str(error)})
        result["status"] = "fail"
    finally:
        if pacing_started_ns is not None:
            processing_wall_us = (clock_ns() - pacing_started_ns) / 1_000
            scheduled_span_us = (
                materialized[-1].timestamp_us - first_timestamp_us
            ) / replay_scale
            result["pacing"].update(
                {
                    "scheduled_span_us": round(scheduled_span_us, 6),
                    "processing_wall_us": round(processing_wall_us, 6),
                    "completion_lag_us": round(
                        max(0.0, processing_wall_us - scheduled_span_us), 6
                    ),
                    "schedule_sleep_us": round(schedule_sleep_us, 6),
                    "modeled_work_sleep_us": round(modeled_work_sleep_us, 6),
                    "max_arrival_lag_us": round(max_arrival_lag_us, 6),
                }
            )
        if store is not None:
            for key in sorted(live_keys):
                try:
                    rc = store.remove(key, True)
                    if rc != 0:
                        raise RuntimeError(f"cleanup remove returned {rc}")
                except Exception as error:
                    result["errors"].append(
                        {"event_index": None, "key": key, "error": str(error)}
                    )
                    result["status"] = "fail"
            try:
                store.close()
            except Exception as error:
                result["errors"].append(
                    {"event_index": None, "key": None, "error": str(error)}
                )
                result["status"] = "fail"
    if compact_state is not None:
        compact_state["error_count"] = len(result["errors"])
        result["errors"] = result["errors"][:COMPACT_FAILURE_RECORD_LIMIT]
        result["evidence"] = _finalize_compact_evidence(compact_state, result)
    return result


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return round(ordered[index], 6)


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _compact_operations(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise ValueError("unsupported replay result schema")
    if result.get("mode") not in REPLAY_MODES:
        raise ValueError("compact replay mode is invalid")
    if result.get("target") is not None and result.get("target") not in DESCRIPTOR_TARGETS:
        raise ValueError("compact replay target is invalid")
    if result.get("mode") == "direct" and result.get("target") is None:
        raise ValueError("compact direct replay requires a target")
    pacing = result.get("pacing")
    if not isinstance(pacing, dict):
        raise ValueError("compact pacing must be an object")
    replay_scale = pacing.get("replay_scale")
    if (
        not isinstance(pacing.get("enabled"), bool)
        or isinstance(replay_scale, bool)
        or not isinstance(replay_scale, (int, float))
        or not math.isfinite(replay_scale)
        or replay_scale < 0
        or pacing["enabled"] != (replay_scale > 0)
        or pacing.get("source_timestamp_unit") != "microseconds"
    ):
        raise ValueError("compact pacing metadata is invalid")
    if "operations" in result:
        raise ValueError("compact result must not contain full operations")
    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("compact evidence must be an object")
    if evidence.get("schema_version") != COMPACT_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported compact evidence schema")

    event_count = result.get("event_count")
    operation_count = evidence.get("operation_count")
    sample_limit = evidence.get("sample_limit")
    if not _is_non_negative_int(event_count):
        raise ValueError("compact event_count must be a non-negative integer")
    if not _is_non_negative_int(operation_count) or operation_count != event_count:
        raise ValueError("compact operation_count must equal event_count")
    if not _is_non_negative_int(sample_limit) or sample_limit == 0:
        raise ValueError("compact sample_limit must be a positive integer")

    operation_counts = evidence.get("operation_counts")
    if not isinstance(operation_counts, dict) or set(operation_counts) != OPERATIONS:
        raise ValueError("compact operation_counts have invalid keys")
    if any(not _is_non_negative_int(value) for value in operation_counts.values()):
        raise ValueError("compact operation_counts must be non-negative integers")
    if sum(operation_counts.values()) != operation_count:
        raise ValueError("compact operation_counts do not sum to operation_count")

    store_operations = ("put", "get", "remove", "recompute", "noop")
    store_counts = evidence.get("store_operation_counts")
    if not isinstance(store_counts, dict) or set(store_counts) != set(store_operations):
        raise ValueError("compact store_operation_counts have invalid keys")
    if any(not _is_non_negative_int(value) for value in store_counts.values()):
        raise ValueError("compact store_operation_counts must be non-negative integers")
    if sum(store_counts.values()) != operation_count:
        raise ValueError("compact store_operation_counts do not sum to operation_count")
    if result.get("mode") == "no_store":
        expected_store_counts = {
            "put": 0,
            "get": 0,
            "remove": 0,
            "recompute": operation_counts["produce"]
            + operation_counts["reuse"]
            + operation_counts["miss"],
            "noop": operation_counts["evict"],
        }
    else:
        expected_store_counts = {
            "put": operation_counts["produce"],
            "get": operation_counts["reuse"],
            "remove": operation_counts["evict"],
            "recompute": operation_counts["miss"],
            "noop": 0,
        }
    if store_counts != expected_store_counts:
        raise ValueError("compact store operation counts are inconsistent")

    target_counts = evidence.get("descriptor_target_counts")
    if not isinstance(target_counts, dict) or set(target_counts) != DESCRIPTOR_TARGETS:
        raise ValueError("compact descriptor_target_counts have invalid keys")
    if any(not _is_non_negative_int(value) for value in target_counts.values()):
        raise ValueError("compact descriptor counts must be non-negative integers")
    descriptor_checks = evidence.get("descriptor_checks")
    expected_descriptor_checks = store_counts["put"] + store_counts["get"] + store_counts["remove"]
    if not _is_non_negative_int(descriptor_checks):
        raise ValueError("compact descriptor_checks must be a non-negative integer")
    if descriptor_checks != sum(target_counts.values()):
        raise ValueError("compact descriptor counts do not sum to descriptor_checks")
    if descriptor_checks != expected_descriptor_checks:
        raise ValueError("compact descriptor_checks do not match store operations")

    policy_counts = evidence.get("descriptor_policy_target_counts")
    if not isinstance(policy_counts, dict) or not set(policy_counts) <= POLICIES:
        raise ValueError("compact descriptor policy counts have invalid policies")
    explicit_target = result.get("target")
    accumulated_targets = {target: 0 for target in DESCRIPTOR_TARGETS}
    for policy, counts in policy_counts.items():
        if not isinstance(counts, dict) or set(counts) != DESCRIPTOR_TARGETS:
            raise ValueError("compact descriptor policy counts have invalid targets")
        if any(not _is_non_negative_int(value) for value in counts.values()):
            raise ValueError("compact descriptor policy counts must be non-negative")
        expected_target = explicit_target or {
            "local_only": "local_nvme",
            "remote_only": "remote_nof",
            "round_robin": None,
        }[policy]
        if expected_target is not None and any(
            value for target, value in counts.items() if target != expected_target
        ):
            raise ValueError(f"compact descriptor policy mismatch for {policy}")
        for target, value in counts.items():
            accumulated_targets[target] += value
    if accumulated_targets != target_counts:
        raise ValueError("compact policy descriptor counts do not match target counts")
    if explicit_target is not None and any(
        value for target, value in target_counts.items() if target != explicit_target
    ):
        raise ValueError("compact descriptors do not match explicit target")

    proof_digest = evidence.get("descriptor_proof_sha256")
    if (
        not isinstance(proof_digest, str)
        or len(proof_digest) != 64
        or any(character not in "0123456789abcdef" for character in proof_digest)
    ):
        raise ValueError("compact descriptor proof digest is invalid")
    counters = (
        "content_checks",
        "content_mismatches",
        "descriptor_mismatches",
        "return_code_failures",
        "error_count",
    )
    if any(
        not _is_non_negative_int(evidence.get(name))
        for name in counters
    ):
        raise ValueError("compact correctness counters must be non-negative integers")
    errors = result.get("errors")
    errors_truncated = evidence.get("error_records_truncated")
    if (
        not isinstance(errors, list)
        or not isinstance(errors_truncated, bool)
        or evidence["error_count"] < len(errors)
        or errors_truncated != (evidence["error_count"] > len(errors))
    ):
        raise ValueError("compact error_count does not match result errors")
    expected_content_checks = (
        0 if result.get("mode") == "no_store" else operation_counts["reuse"]
    )
    if evidence["content_checks"] != expected_content_checks:
        raise ValueError("compact content_checks do not match reuse operations")
    if any(
        evidence[name] != 0
        for name in (
            "content_mismatches",
            "descriptor_mismatches",
            "return_code_failures",
            "error_count",
        )
    ):
        raise ValueError("compact correctness gate failed")

    failure_limit = evidence.get("failure_record_limit")
    failure_records = evidence.get("failure_records")
    if failure_limit != COMPACT_FAILURE_RECORD_LIMIT:
        raise ValueError("compact failure record limit is invalid")
    if not isinstance(failure_records, list) or len(failure_records) > failure_limit:
        raise ValueError("compact failure records are invalid")
    if failure_records:
        raise ValueError("compact result contains failure records")

    summary = evidence.get("summary")
    if not isinstance(summary, dict) or set(summary) != set(SUMMARY_METRIC_FIELDS):
        raise ValueError("compact summary has invalid fields")
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        )
        for value in summary.values()
    ):
        raise ValueError("compact summary metrics must be finite numbers or null")
    count_metrics = ("operations", "request_count", "produce", "reuse", "evict", "miss", "local", "remote")
    if any(
        isinstance(summary[name], bool)
        or not isinstance(summary[name], int)
        or summary[name] < 0
        for name in count_metrics
    ):
        raise ValueError("compact summary counts must be non-negative integers")
    non_negative_metrics = (
        "p50_latency_us",
        "p95_latency_us",
        "p99_latency_us",
        "operation_rate",
        "request_p50_latency_us",
        "request_p95_latency_us",
        "request_arrival_lag_p50_us",
        "request_arrival_lag_p95_us",
        "request_arrival_lag_max_us",
        "storage_wait_us",
        "replay_scale",
        "scheduled_span_us",
        "processing_wall_us",
        "completion_lag_us",
    )
    if any(summary[name] is not None and summary[name] < 0 for name in non_negative_metrics):
        raise ValueError("compact summary metrics must be non-negative")
    for rate_name in ("request_hit_rate", "block_hit_rate", "miss_rate"):
        rate = summary[rate_name]
        if rate is not None and not 0 <= rate <= 1:
            raise ValueError(f"compact summary {rate_name} is outside [0, 1]")
    for quantiles in (
        ("p50_latency_us", "p95_latency_us", "p99_latency_us"),
        ("request_p50_latency_us", "request_p95_latency_us"),
        (
            "request_arrival_lag_p50_us",
            "request_arrival_lag_p95_us",
            "request_arrival_lag_max_us",
        ),
    ):
        values = [summary[name] for name in quantiles]
        present = [value for value in values if value is not None]
        if present != sorted(present):
            raise ValueError("compact summary quantiles are inconsistent")
    operation_quantiles = [
        summary[name] for name in ("p50_latency_us", "p95_latency_us", "p99_latency_us")
    ]
    if (operation_count > 0 and not all(value is not None for value in operation_quantiles)) or (
        operation_count == 0 and any(value is not None for value in operation_quantiles)
    ):
        raise ValueError("compact operation quantile presence is inconsistent")
    summary_digest = hashlib.sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if evidence.get("summary_sha256") != summary_digest:
        raise ValueError("compact summary digest mismatch")
    expected_summary_counts = {
        "operations": operation_count,
        "produce": operation_counts["produce"],
        "reuse": operation_counts["reuse"],
        "evict": operation_counts["evict"],
        "miss": operation_counts["miss"],
        "local": target_counts["local_nvme"],
        "remote": target_counts["remote_nof"],
    }
    if any(summary[name] != value for name, value in expected_summary_counts.items()):
        raise ValueError("compact summary counts are inconsistent")
    request_count = evidence.get("request_count")
    hit_request_count = evidence.get("hit_request_count")
    if (
        not _is_non_negative_int(request_count)
        or not _is_non_negative_int(hit_request_count)
        or not 0 <= hit_request_count <= request_count
        or summary["request_count"] != request_count
    ):
        raise ValueError("compact request counts are inconsistent")
    expected_request_hit_rate = (
        round(hit_request_count / request_count, 6) if request_count else None
    )
    if summary["request_hit_rate"] != expected_request_hit_rate:
        raise ValueError("compact request hit rate is inconsistent")
    request_quantiles = [
        summary[name] for name in ("request_p50_latency_us", "request_p95_latency_us")
    ]
    if (request_count > 0 and not all(value is not None for value in request_quantiles)) or (
        request_count == 0 and any(value is not None for value in request_quantiles)
    ):
        raise ValueError("compact request quantile presence is inconsistent")
    blocks_seen = operation_counts["produce"] + operation_counts["reuse"]
    expected_block_hit_rate = (
        round(operation_counts["reuse"] / blocks_seen, 6) if blocks_seen else None
    )
    if summary["block_hit_rate"] != expected_block_hit_rate:
        raise ValueError("compact block hit rate is inconsistent")
    expected_miss_rate = (
        round(operation_counts["miss"] / operation_count, 6)
        if operation_count
        else None
    )
    if summary["miss_rate"] != expected_miss_rate:
        raise ValueError("compact miss rate is inconsistent")
    latency_total = evidence.get("operation_latency_total_us")
    if (
        isinstance(latency_total, bool)
        or not isinstance(latency_total, (int, float))
        or not math.isfinite(latency_total)
        or latency_total < 0
    ):
        raise ValueError("compact operation latency total is invalid")
    expected_operation_rate = (
        round(operation_count / (latency_total / 1_000_000), 6)
        if latency_total > 0
        else None
    )
    if summary["operation_rate"] != expected_operation_rate:
        raise ValueError("compact operation rate is inconsistent")
    for name, expected in {
        "replay_scale": pacing.get("replay_scale", 0),
        "scheduled_span_us": pacing.get("scheduled_span_us"),
        "processing_wall_us": pacing.get("processing_wall_us"),
        "completion_lag_us": pacing.get("completion_lag_us"),
    }.items():
        if summary[name] != expected:
            raise ValueError(f"compact summary {name} is inconsistent")
    arrival_metrics = [
        summary[name]
        for name in (
            "request_arrival_lag_p50_us",
            "request_arrival_lag_p95_us",
            "request_arrival_lag_max_us",
        )
    ]
    expected_arrival_metrics = pacing["enabled"] and request_count > 0
    if (expected_arrival_metrics and not all(value is not None for value in arrival_metrics)) or (
        not expected_arrival_metrics and any(value is not None for value in arrival_metrics)
    ):
        raise ValueError("compact arrival metric presence is inconsistent")
    pacing_metrics = [
        summary[name]
        for name in ("scheduled_span_us", "processing_wall_us", "completion_lag_us")
    ]
    if (pacing["enabled"] and not all(value is not None for value in pacing_metrics)) or (
        not pacing["enabled"] and any(value is not None for value in pacing_metrics)
    ):
        raise ValueError("compact pacing metric presence is inconsistent")

    samples = evidence.get("samples")
    expected_sample_count = min(sample_limit, operation_count)
    if not isinstance(samples, list) or len(samples) != expected_sample_count:
        raise ValueError("compact sample count does not match its declared bound")
    sample_indices = []
    sample_ranks = []
    sample_fields = {
        "event_index",
        "timestamp_us",
        "request_id",
        "prefix_id",
        "key",
        "block_id",
        "block_size",
        "operation",
        "policy",
        "target_policy",
        "store_operation",
        "descriptor",
        "return_code",
        "latency_us",
        "scheduled_offset_us",
        "arrival_lag_us",
        "error",
    }
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != sample_fields:
            raise ValueError("compact sample must be an object")
        event_index = sample.get("event_index")
        if not _is_non_negative_int(event_index) or event_index >= event_count:
            raise ValueError("compact sample event_index is out of range")
        if sample.get("operation") not in OPERATIONS:
            raise ValueError("compact sample operation is invalid")
        if (
            not _is_non_negative_int(sample.get("timestamp_us"))
            or not sample.get("request_id")
            or not sample.get("prefix_id")
            or not sample.get("key")
            or not sample.get("block_id")
        ):
            raise ValueError("compact sample identity is invalid")
        sample_store_operation = sample.get("store_operation") or "noop"
        if sample_store_operation not in store_operations:
            raise ValueError("compact sample store operation is invalid")
        expected_sample_store_operation = (
            "noop"
            if result["mode"] == "no_store" and sample["operation"] == "evict"
            else "recompute"
            if result["mode"] == "no_store" or sample["operation"] == "miss"
            else {"produce": "put", "reuse": "get", "evict": "remove"}[
                sample["operation"]
            ]
        )
        if sample_store_operation != expected_sample_store_operation:
            raise ValueError("compact sample store operation is inconsistent")
        if sample.get("policy") not in POLICIES:
            raise ValueError("compact sample policy is invalid")
        if sample.get("target_policy") != sample["policy"]:
            raise ValueError("compact sample target policy is inconsistent")
        if (
            not _is_non_negative_int(sample.get("return_code"))
            or sample["return_code"] != 0
            or sample.get("error") is not None
        ):
            raise ValueError("compact sample correctness fields are invalid")
        block_size = sample.get("block_size")
        if not _is_non_negative_int(block_size) or block_size == 0:
            raise ValueError("compact sample block_size is invalid")
        latency = sample.get("latency_us")
        if (
            isinstance(latency, bool)
            or not isinstance(latency, (int, float))
            or not math.isfinite(latency)
            or latency < 0
        ):
            raise ValueError("compact sample latency is invalid")
        for timing_name in ("scheduled_offset_us", "arrival_lag_us"):
            timing = sample.get(timing_name)
            if pacing["enabled"]:
                if (
                    isinstance(timing, bool)
                    or not isinstance(timing, (int, float))
                    or not math.isfinite(timing)
                    or timing < 0
                ):
                    raise ValueError(f"compact sample {timing_name} is invalid")
            elif timing is not None:
                raise ValueError(f"compact unpaced sample {timing_name} must be null")
        descriptor = sample.get("descriptor")
        if sample_store_operation in ("put", "get", "remove"):
            if (
                not isinstance(descriptor, dict)
                or descriptor.get("target") not in DESCRIPTOR_TARGETS
                or descriptor.get("object_size") != block_size
            ):
                raise ValueError("compact store sample descriptor is invalid")
        elif descriptor is not None:
            raise ValueError("compact non-store sample must not have a descriptor")
        sample_indices.append(event_index)
        sample_identity = (
            f"{event_index}\0{sample.get('request_id')}\0"
            f"{sample.get('block_id')}\0{sample.get('operation')}"
        )
        sample_ranks.append(
            int.from_bytes(hashlib.sha256(sample_identity.encode()).digest(), "big")
        )
    if len(sample_indices) != len(set(sample_indices)):
        raise ValueError("compact sample event indices must be unique")
    if sample_ranks != sorted(sample_ranks):
        raise ValueError("compact samples are not in deterministic rank order")
    sample_digest = hashlib.sha256(
        json.dumps(sample_indices, separators=(",", ":")).encode()
    ).hexdigest()
    if evidence.get("sample_event_indices_sha256") != sample_digest:
        raise ValueError("compact sample digest mismatch")
    sample_records_digest = hashlib.sha256(
        json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if evidence.get("sample_records_sha256") != sample_records_digest:
        raise ValueError("compact sample records digest mismatch")
    return samples


def _case_rows(result: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if result.get("status") != "pass" or result.get("errors"):
        raise ValueError("raw result contains failed operations")
    evidence_mode = result.get("evidence_mode")
    if evidence_mode == "compact":
        operations = _compact_operations(result)
    else:
        if evidence_mode is not None or "evidence" in result:
            raise ValueError("unsupported or ambiguous evidence mode")
        operations = result.get("operations")
        if not isinstance(operations, list):
            raise ValueError("raw result operations must be a list")
    rows: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("operation must be an object")
        descriptor = operation.get("descriptor") or {}
        target = descriptor.get("target")
        if target is not None and target not in DESCRIPTOR_TARGETS:
            raise ValueError(f"unsupported descriptor target: {target}")
        mode = result.get("mode")
        policy = operation.get("policy")
        expected_target = result.get("target")
        if mode == "transparent" and expected_target is None:
            expected_target = {
                "local_only": "local_nvme",
                "remote_only": "remote_nof",
            }.get(policy)
        if (
            target is not None
            and expected_target is not None
            and target != expected_target
        ):
            raise ValueError(
                f"descriptor mismatch for {operation.get('block_id')}: "
                f"{target} != {expected_target}"
            )
        if operation.get("error") is not None or operation.get("return_code", 0) != 0:
            raise ValueError("operation sample failed its correctness gate")
        latency = float(operation.get("latency_us", 0.0))
        rows.append(
            {
                "mode": result.get("mode"),
                "target": result.get("target") or "",
                "request_id": operation.get("request_id", ""),
                "block_id": operation.get("block_id", ""),
                "operation": operation.get("operation", ""),
                "store_operation": operation.get("store_operation") or "noop",
                "descriptor_target": target or "",
                "latency_us": latency,
                "scheduled_offset_us": operation.get("scheduled_offset_us"),
                "arrival_lag_us": operation.get("arrival_lag_us"),
                "return_code": operation.get("return_code", 0),
            }
        )
    return rows, result


def _summary_from_rows(rows: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    latencies = [row["latency_us"] for row in rows]
    request_rows: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["request_id"] != "cleanup" and not row["request_id"].endswith("-cleanup"):
            request_rows.setdefault(row["request_id"], []).append(row)
    request_latencies = [
        sum(row["latency_us"] for row in group) for group in request_rows.values()
    ]
    request_arrival_lags = [
        float(group[0]["arrival_lag_us"])
        for group in request_rows.values()
        if group[0]["arrival_lag_us"] is not None
    ]
    request_count = len(request_rows)
    hit_requests = sum(
        any(row["operation"] == "reuse" for row in group)
        for group in request_rows.values()
    )
    blocks_seen = sum(row["operation"] in ("produce", "reuse") for row in rows)
    block_hits = sum(row["operation"] == "reuse" for row in rows)
    misses = sum(row["operation"] == "miss" for row in rows)
    storage_wait = sum(
        row["latency_us"]
        for row in rows
        if row["store_operation"] in ("put", "get", "remove")
    )
    pacing = result.get("pacing", {})
    return {
        "operations": len(rows),
        "p50_latency_us": _percentile(latencies, 50),
        "p95_latency_us": _percentile(latencies, 95),
        "p99_latency_us": _percentile(latencies, 99),
        "operation_rate": round(len(rows) / (sum(latencies) / 1_000_000), 6)
        if sum(latencies) > 0
        else None,
        "request_count": request_count,
        "request_hit_rate": round(hit_requests / request_count, 6)
        if request_count
        else None,
        "block_hit_rate": round(block_hits / blocks_seen, 6) if blocks_seen else None,
        "miss_rate": round(misses / len(rows), 6) if rows else None,
        "request_p50_latency_us": _percentile(request_latencies, 50),
        "request_p95_latency_us": _percentile(request_latencies, 95),
        "request_arrival_lag_p50_us": _percentile(request_arrival_lags, 50),
        "request_arrival_lag_p95_us": _percentile(request_arrival_lags, 95),
        "request_arrival_lag_max_us": round(max(request_arrival_lags), 6)
        if request_arrival_lags
        else None,
        "storage_wait_us": round(storage_wait, 6),
        "replay_scale": pacing.get("replay_scale", 0),
        "scheduled_span_us": pacing.get("scheduled_span_us"),
        "processing_wall_us": pacing.get("processing_wall_us"),
        "completion_lag_us": pacing.get("completion_lag_us"),
        "produce": sum(row["operation"] == "produce" for row in rows),
        "reuse": sum(row["operation"] == "reuse" for row in rows),
        "evict": sum(row["operation"] == "evict" for row in rows),
        "miss": misses,
        "local": sum(row["descriptor_target"] == "local_nvme" for row in rows),
        "remote": sum(row["descriptor_target"] == "remote_nof" for row in rows),
    }


def summarize_results(
    result_dir: str | Path,
    *,
    required_cases: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Aggregate raw replay JSON files into CSVs and a guarded conclusion.

    The summary is deliberately offline: it never connects to Mooncake and marks
    incomplete, mixed-run, or failed inputs as ``inconclusive``.
    """

    output = Path(result_dir)
    manifest_path = output / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    raw_paths = sorted(output.glob("raw-*.json"))
    required = set(required_cases or manifest.get("required_cases", []))
    seen_cases: set[str] = set()
    run_ids: set[str] = set()
    trace_digests: set[str] = set()
    all_rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw_path in raw_paths:
        try:
            result = json.loads(raw_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("raw result must be an object")
            case_id = str(result.get("case_id") or raw_path.stem.removeprefix("raw-"))
            if case_id in seen_cases:
                errors.append(f"duplicate case: {case_id}")
            seen_cases.add(case_id)
            if result.get("run_id") is not None:
                run_ids.add(str(result["run_id"]))
            if result.get("trace_sha256") is not None:
                trace_digests.add(str(result["trace_sha256"]))
            manifest_run_id = manifest.get("run_id")
            if manifest_run_id is not None and result.get("run_id") != manifest_run_id:
                raise ValueError("raw result run ID does not match manifest")
            manifest_trace_digest = manifest.get("trace_sha256")
            if (
                manifest_trace_digest is not None
                and result.get("trace_sha256") != manifest_trace_digest
            ):
                raise ValueError("raw result trace digest does not match manifest")
            rows, result = _case_rows(result)
            all_rows.extend([{**row, "case_id": case_id} for row in rows])
            metrics = (
                dict(result["evidence"]["summary"])
                if result.get("evidence_mode") == "compact"
                else _summary_from_rows(rows, result)
            )
            case_summaries.append(
                {
                    "case_id": case_id,
                    "mode": result.get("mode"),
                    "target": result.get("target") or "",
                    **metrics,
                    "cpu_utilization_pct": result.get("cpu_utilization_pct"),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{raw_path.name}: {error}")

    missing = sorted(required - seen_cases)
    if len(run_ids) > 1:
        errors.append("mixed run IDs")
    if len(trace_digests) > 1:
        errors.append("mixed trace digests")
    if missing:
        errors.append(f"missing cases: {', '.join(missing)}")
    status = "pass" if raw_paths and not errors else "inconclusive"

    output.mkdir(parents=True, exist_ok=True)
    operation_fields = [
        "case_id",
        "mode",
        "target",
        "request_id",
        "block_id",
        "operation",
        "store_operation",
        "descriptor_target",
        "latency_us",
        "scheduled_offset_us",
        "arrival_lag_us",
        "return_code",
    ]
    with (output / "operations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=operation_fields, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(all_rows)
    summary_fields = [
        "case_id",
        "mode",
        "target",
        "operations",
        "p50_latency_us",
        "p95_latency_us",
        "p99_latency_us",
        "operation_rate",
        "produce",
        "reuse",
        "evict",
        "miss",
        "request_count",
        "request_hit_rate",
        "block_hit_rate",
        "miss_rate",
        "request_p50_latency_us",
        "request_p95_latency_us",
        "request_arrival_lag_p50_us",
        "request_arrival_lag_p95_us",
        "request_arrival_lag_max_us",
        "storage_wait_us",
        "replay_scale",
        "scheduled_span_us",
        "processing_wall_us",
        "completion_lag_us",
        "cpu_utilization_pct",
        "local",
        "remote",
    ]
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(case_summaries)
    conclusion = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "status": status,
        "run_id": next(iter(run_ids), manifest.get("run_id")),
        "trace_sha256": next(iter(trace_digests), manifest.get("trace_sha256")),
        "required_cases": sorted(required),
        "completed_cases": sorted(seen_cases),
        "missing_cases": missing,
        "errors": errors,
        "cases": case_summaries,
        "proxy_note": "no_store recompute latency is a fixed proxy, not model execution",
    }
    (output / "conclusion.json").write_text(
        json.dumps(conclusion, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return conclusion


def _write_manifest(
    path: Path,
    events: list[TraceEvent],
    parameters: dict[str, Any],
    seed: int,
    run_id: str | None,
) -> dict[str, Any]:
    manifest = manifest_for(events, seed=seed, parameters=parameters)
    if run_id is not None:
        manifest["run_id"] = run_id
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _generate_command(args: argparse.Namespace) -> int:
    output = Path(args.output)
    parameters = {
        "requests": args.requests,
        "blocks_per_request": args.blocks_per_request,
        "block_size": args.block_size,
        "reuse_ratio": args.reuse_ratio,
        "concurrency": args.concurrency,
        "policy": args.policy,
    }
    events = generate_trace(seed=args.seed, **parameters)
    digest = write_trace(output / "trace.jsonl", events)
    _write_manifest(
        output / "manifest.json", events, parameters, args.seed, args.run_id
    )
    print(
        json.dumps(
            {
                "events": len(events),
                "trace_sha256": digest,
                "manifest": str(output / "manifest.json"),
            }
        )
    )
    return 0


def _replay_command(args: argparse.Namespace) -> int:
    output = Path(args.output)
    events = read_trace(args.trace)
    manifest = (
        json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else {}
    )
    run_id = args.run_id or manifest.get("run_id")
    key_prefix = f"kv-workload-{run_id or 'unscoped'}-{args.case_id}"
    result = replay_trace(
        events,
        mode=args.mode,
        target=args.target,
        key_prefix=key_prefix,
        recompute_us=args.recompute_us,
        replay_scale=args.replay_scale,
        compact_evidence=getattr(args, "compact_evidence", False),
        compact_sample_limit=getattr(args, "compact_sample_limit", 128),
    )
    result.update(
        {
            "case_id": args.case_id,
            "run_id": run_id,
            "trace_sha256": manifest.get("trace_sha256"),
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if result["status"] == "pass" else 1


def _summarize_command(args: argparse.Namespace) -> int:
    conclusion = summarize_results(args.result_dir, required_cases=args.required_case)
    print(json.dumps(conclusion, indent=2, sort_keys=True))
    return 0 if conclusion["status"] == "pass" else 1


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    def add_generation_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("output", type=Path)
        command.add_argument("--requests", type=int, default=12)
        command.add_argument("--blocks-per-request", type=int, default=4)
        command.add_argument("--block-size", type=int, default=131072)
        command.add_argument("--reuse-ratio", type=float, default=0.5)
        command.add_argument("--concurrency", type=int, default=1)
        command.add_argument(
            "--policy", choices=sorted(POLICIES), default="round_robin"
        )
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--run-id")

    generate_parser = subparsers.add_parser("generate")
    add_generation_options(generate_parser)
    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("trace", type=Path)
    replay_parser.add_argument("output", type=Path)
    replay_parser.add_argument("--mode", choices=sorted(REPLAY_MODES), required=True)
    replay_parser.add_argument("--target", choices=sorted(DESCRIPTOR_TARGETS))
    replay_parser.add_argument("--manifest", type=Path)
    replay_parser.add_argument("--case-id", required=True)
    replay_parser.add_argument("--run-id")
    replay_parser.add_argument("--recompute-us", type=int, default=1000)
    replay_parser.add_argument("--replay-scale", type=float, default=0.0)
    replay_parser.add_argument("--compact-evidence", action="store_true")
    replay_parser.add_argument("--compact-sample-limit", type=int, default=128)
    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("result_dir", type=Path)
    summarize_parser.add_argument("--required-case", action="append", default=[])

    argv = sys.argv[1:]
    if argv and argv[0] not in {"generate", "replay", "summarize", "-h", "--help"}:
        argv.insert(0, "generate")
    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate_command(args)
    if args.command == "replay":
        return _replay_command(args)
    if args.command == "summarize":
        return _summarize_command(args)

    # Preserve the original ``kv_workload.py output.jsonl`` generator interface.
    parser.error("a command is required (generate, replay, or summarize)")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
