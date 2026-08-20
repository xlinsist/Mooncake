"""Deterministic KV-cache workload trace generation and validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
REPLAY_SCHEMA_VERSION = 1
OPERATIONS = frozenset(("produce", "reuse", "evict", "miss"))
POLICIES = frozenset(("local_only", "remote_only", "round_robin"))
REPLAY_MODES = frozenset(("no_store", "direct", "transparent"))


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
                raise ValueError(f"block produced twice without eviction: {event.block_id}")
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
            raise ValueError(f"miss cannot reference an evicted block: {event.block_id}")
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
        raise ValueError("requests, blocks_per_request, and concurrency must be positive")
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
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
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


def _descriptor_for(store: Any, key: str, expected_target: str | None) -> dict[str, Any]:
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
    if mode == "direct" and target not in ("local_nvme", "remote_nof"):
        raise ValueError("direct replay requires local_nvme or remote_nof target")
    if not key_prefix:
        raise ValueError("key_prefix is required")

    result: dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "status": "pass",
        "mode": mode,
        "target": target,
        "event_count": len(materialized),
        "operations": [],
        "errors": [],
        "recompute_model": (
            {"kind": "fixed_proxy", "latency_us": recompute_us}
            if mode == "no_store"
            else None
        ),
    }
    if not materialized:
        return result

    store = None
    config = None
    live_keys: set[str] = set()
    descriptors: dict[str, dict[str, Any]] = {}
    try:
        if mode != "no_store":
            store = store_factory()
            if mode == "direct":
                config = config_factory(target)

        for event_index, event in enumerate(materialized):
            key = f"{key_prefix}-{event.block_id}"
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
                "error": None,
            }
            result["operations"].append(record)
            try:
                if event.operation == "miss" or mode == "no_store":
                    record["store_operation"] = (
                        "noop" if event.operation == "evict" else "recompute"
                    )
                    record["latency_us"] = (
                        0.0 if event.operation == "evict" else float(recompute_us)
                    )
                    continue

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
                    if mode == "transparent":
                        expected_target = {
                            "local_only": "local_nvme",
                            "remote_only": "remote_nof",
                            "round_robin": None,
                        }[event.policy]
                    descriptor = descriptor_reader(store, key, expected_target)
                    if descriptor.get("object_size") != event.block_size:
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
                    if actual != payload:
                        raise AssertionError(f"content mismatch for {key}")
                    expected = descriptors[event.block_id]
                    descriptor = descriptor_reader(store, key, expected["target"])
                    if descriptor != expected:
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
                    descriptors.pop(event.block_id, None)
            except Exception as error:
                record["error"] = str(error)
                result["errors"].append(
                    {"event_index": event_index, "key": key, "error": str(error)}
                )
                result["status"] = "fail"
                break
    except Exception as error:
        result["errors"].append({"event_index": None, "key": None, "error": str(error)})
        result["status"] = "fail"
    finally:
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
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--blocks-per-request", type=int, default=4)
    parser.add_argument("--block-size", type=int, default=131072)
    parser.add_argument("--reuse-ratio", type=float, default=0.5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--policy", choices=sorted(POLICIES), default="round_robin")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    parameters = {
        "requests": args.requests,
        "blocks_per_request": args.blocks_per_request,
        "block_size": args.block_size,
        "reuse_ratio": args.reuse_ratio,
        "concurrency": args.concurrency,
        "policy": args.policy,
    }
    events = generate_trace(seed=args.seed, **parameters)
    digest = write_trace(args.output, events)
    manifest = manifest_for(events, seed=args.seed, parameters=parameters)
    manifest_path = args.output.with_suffix(args.output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"events": len(events), "trace_sha256": digest, "manifest": str(manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
