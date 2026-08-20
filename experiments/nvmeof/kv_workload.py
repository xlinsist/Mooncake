"""Deterministic KV-cache workload trace generation and validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
OPERATIONS = frozenset(("produce", "reuse", "evict", "miss"))
POLICIES = frozenset(("local_only", "remote_only", "round_robin"))


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
