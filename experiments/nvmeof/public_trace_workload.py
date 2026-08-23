"""Convert FAST'25 public traces into deterministic Mooncake KV events."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

from kv_workload import POLICIES, TraceEvent, manifest_for, write_trace


PUBLIC_TRACE_SCHEMA_VERSION = 1


def read_public_trace(path: str | Path, *, max_requests: int) -> list[dict]:
    if max_requests <= 0:
        raise ValueError("max_requests must be positive")

    requests = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if len(requests) >= max_requests:
                break
            if not line.strip():
                raise ValueError(f"blank public trace line at {line_number}")
            try:
                request = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid public trace JSON at line {line_number}"
                ) from error
            if not isinstance(request, dict):
                raise ValueError(f"public trace line {line_number} must be an object")
            hash_ids = request.get("hash_ids")
            if not isinstance(hash_ids, list) or not hash_ids:
                raise ValueError(
                    f"public trace line {line_number} requires non-empty hash_ids"
                )
            if any(not isinstance(hash_id, int) or hash_id < 0 for hash_id in hash_ids):
                raise ValueError(
                    f"public trace line {line_number} has invalid hash_ids"
                )
            timestamp = request.get("timestamp")
            if not isinstance(timestamp, (int, float)) or timestamp < 0:
                raise ValueError(
                    f"public trace line {line_number} has invalid timestamp"
                )
            requests.append(request)

    if len(requests) != max_requests:
        raise ValueError(
            f"public trace has {len(requests)} requests, expected {max_requests}"
        )
    return requests


def convert_public_trace(
    requests: Iterable[dict],
    *,
    block_size: int,
    capacity_pages: int,
    policy: str = "round_robin",
) -> list[TraceEvent]:
    if block_size <= 0 or block_size % 512 != 0:
        raise ValueError("block_size must be a positive 512-byte multiple")
    if capacity_pages <= 0:
        raise ValueError("capacity_pages must be positive")
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")

    live_pages: OrderedDict[str, None] = OrderedDict()
    events: list[TraceEvent] = []
    timestamp_us = 0

    def append_event(request_id: str, block_id: str, operation: str) -> None:
        nonlocal timestamp_us
        events.append(
            TraceEvent(
                timestamp_us=timestamp_us,
                request_id=request_id,
                prefix_id=f"prefix-{block_id}",
                block_id=block_id,
                block_size=block_size,
                operation=operation,
                policy=policy,
            )
        )
        timestamp_us += 1

    for request_index, request in enumerate(requests):
        request_id = f"request-{request_index:05d}"
        for hash_id in request["hash_ids"]:
            block_id = f"page-{hash_id}"
            if block_id in live_pages:
                live_pages.move_to_end(block_id)
                append_event(request_id, block_id, "reuse")
                continue

            if len(live_pages) >= capacity_pages:
                evicted_block_id, _ = live_pages.popitem(last=False)
                append_event(request_id, evicted_block_id, "evict")
            append_event(request_id, block_id, "produce")
            live_pages[block_id] = None

    for block_id in list(live_pages):
        append_event("cleanup", block_id, "evict")
    return events


def source_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_command(args: argparse.Namespace) -> int:
    requests = read_public_trace(args.input, max_requests=args.requests)
    events = convert_public_trace(
        requests,
        block_size=args.block_size,
        capacity_pages=args.capacity_pages,
        policy=args.policy,
    )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    trace_sha256 = write_trace(output / "trace.jsonl", events)
    parameters = {
        "source": str(args.input),
        "source_sha256": source_sha256(args.input),
        "requests": args.requests,
        "block_size": args.block_size,
        "capacity_pages": args.capacity_pages,
        "policy": args.policy,
    }
    manifest = manifest_for(events, seed=0, parameters=parameters)
    manifest.update(
        {
            "public_trace_schema_version": PUBLIC_TRACE_SCHEMA_VERSION,
            "run_id": args.run_id,
            "source_sha256": parameters["source_sha256"],
            "trace_sha256": trace_sha256,
        }
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "events": len(events),
                "requests": args.requests,
                "source_sha256": parameters["source_sha256"],
                "trace_sha256": trace_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--requests", type=int, required=True)
    parser.add_argument("--block-size", type=int, default=131072)
    parser.add_argument("--capacity-pages", type=int, default=64)
    parser.add_argument("--policy", choices=sorted(POLICIES), default="round_robin")
    parser.add_argument("--run-id", required=True)
    return convert_command(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
