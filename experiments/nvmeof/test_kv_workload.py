import json

import pytest

from kv_workload import TraceEvent, generate_trace, manifest_for, read_trace, write_trace


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
