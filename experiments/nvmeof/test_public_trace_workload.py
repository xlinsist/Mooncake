import json

import pytest

from public_trace_workload import convert_public_trace, read_public_trace, source_sha256


def test_conversion_is_deterministic_and_lru_bounded():
    requests = [
        {"timestamp": 0, "hash_ids": [1, 2]},
        {"timestamp": 1, "hash_ids": [1, 3]},
    ]

    first = convert_public_trace(requests, block_size=4096, capacity_pages=2)
    second = convert_public_trace(requests, block_size=4096, capacity_pages=2)

    assert first == second
    assert [event.operation for event in first] == [
        "produce",
        "produce",
        "reuse",
        "evict",
        "produce",
        "evict",
        "evict",
    ]
    assert [event.block_id for event in first] == [
        "page-1",
        "page-2",
        "page-1",
        "page-2",
        "page-3",
        "page-1",
        "page-3",
    ]
    assert (
        max(
            sum(
                1
                if event.operation == "produce"
                else -1
                if event.operation == "evict"
                else 0
                for event in first[: index + 1]
            )
            for index in range(len(first))
        )
        == 2
    )


def test_conversion_can_preserve_batched_arrival_timestamps():
    requests = [
        {"timestamp": 10, "hash_ids": [1, 2]},
        {"timestamp": 13, "hash_ids": [1, 3]},
    ]

    events = convert_public_trace(
        requests,
        block_size=4096,
        capacity_pages=2,
        preserve_arrivals=True,
    )

    assert [event.timestamp_us for event in events] == [
        0,
        0,
        3000,
        3000,
        3000,
        3000,
        3000,
    ]


def test_read_public_trace_requires_exact_valid_request_count(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(request)
            for request in [
                {"timestamp": 0, "hash_ids": [1]},
                {"timestamp": 1, "hash_ids": [1, 2]},
            ]
        )
        + "\n"
    )

    assert len(read_public_trace(trace, max_requests=2)) == 2
    assert len(source_sha256(trace)) == 64
    with pytest.raises(ValueError, match="expected 3"):
        read_public_trace(trace, max_requests=3)


@pytest.mark.parametrize(
    "trace_row,error",
    [
        ({"timestamp": 0, "hash_ids": []}, "non-empty hash_ids"),
        ({"timestamp": -1, "hash_ids": [1]}, "invalid timestamp"),
        ({"timestamp": float("nan"), "hash_ids": [1]}, "invalid timestamp"),
        ({"timestamp": 0, "hash_ids": ["1"]}, "invalid hash_ids"),
    ],
)
def test_read_public_trace_rejects_invalid_rows(tmp_path, trace_row, error):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps(trace_row) + "\n")

    with pytest.raises(ValueError, match=error):
        read_public_trace(trace, max_requests=1)


def test_read_public_trace_rejects_decreasing_timestamps(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(request)
            for request in [
                {"timestamp": 2, "hash_ids": [1]},
                {"timestamp": 1, "hash_ids": [2]},
            ]
        )
        + "\n"
    )

    with pytest.raises(ValueError, match="decreasing timestamp"):
        read_public_trace(trace, max_requests=2)


def test_conversion_rejects_invalid_capacity_and_alignment():
    requests = [{"timestamp": 0, "hash_ids": [1]}]
    with pytest.raises(ValueError, match="512-byte"):
        convert_public_trace(requests, block_size=513, capacity_pages=1)
    with pytest.raises(ValueError, match="capacity_pages"):
        convert_public_trace(requests, block_size=512, capacity_pages=0)
