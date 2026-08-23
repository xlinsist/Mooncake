import sys
from pathlib import Path

import pytest


BENCHMARK_ROOT = Path(__file__).parents[2] / "benchmarks" / "storage_benchmark_v1"
sys.path.insert(0, str(BENCHMARK_ROOT))

import benchmark as benchmark_module  # noqa: E402
from benchmark import KVCacheRequest, pacing_stats, wait_for_replay_time  # noqa: E402


class ManualClock:
    def __init__(self, seconds=0.0):
        self.seconds = seconds
        self.sleeps = []

    def clock(self):
        return self.seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds


def request(timestamp):
    return KVCacheRequest(timestamp, [1], 1, 1)


def test_wait_for_replay_time_sleeps_until_scaled_arrival():
    clock = ManualClock(0.1)

    lag_ms = wait_for_replay_time(
        request(3000),
        base_timestamp=0,
        start_time=0,
        replay_scale=10,
        clock=clock.clock,
        sleeper=clock.sleep,
    )

    assert clock.sleeps == [pytest.approx(0.2)]
    assert lag_ms == pytest.approx(0.0)


def test_wait_for_replay_time_records_late_arrival_without_sleeping():
    clock = ManualClock(0.4)

    lag_ms = wait_for_replay_time(
        request(3000),
        base_timestamp=0,
        start_time=0,
        replay_scale=10,
        clock=clock.clock,
        sleeper=clock.sleep,
    )

    assert clock.sleeps == []
    assert lag_ms == pytest.approx(100.0)


def test_pacing_stats_records_span_completion_and_arrival_lag():
    stats = pacing_stats(
        [request(1000), request(4000)],
        replay_scale=10,
        elapsed=0.35,
        arrival_lags_ms=[0.0, 100.0],
    )

    assert stats["scheduled_span_s"] == pytest.approx(0.3)
    assert stats["processing_wall_s"] == pytest.approx(0.35)
    assert stats["completion_lag_s"] == pytest.approx(0.05)
    assert stats["arrival_lag_ms"]["p50_ms"] == pytest.approx(50.0)
    assert stats["arrival_lag_ms"]["p95_ms"] == pytest.approx(95.0)
    assert stats["arrival_lag_max_ms"] == pytest.approx(100.0)


def test_unpaced_stats_do_not_report_schedule_metrics():
    stats = pacing_stats([request(0)], 0, 1.0, [])

    assert stats == {
        "enabled": False,
        "replay_scale": 0,
        "source_timestamp_unit": "milliseconds",
    }


def test_single_thread_result_includes_pacing_metrics(monkeypatch):
    clock = ManualClock()

    class FakeBenchmark:
        def process_request(self, _request):
            clock.sleep(0.1)

        def get_stats(self):
            return {
                "total_requests": 2,
                "read_pages": 0,
                "write_pages": 0,
                "storage": {},
            }

    monkeypatch.setattr(benchmark_module.time, "perf_counter", clock.clock)
    monkeypatch.setattr(benchmark_module.time, "sleep", clock.sleep)

    result = benchmark_module.run_single_thread(
        FakeBenchmark(),
        [request(0), request(1000)],
        replay_scale=10,
        progress_interval=0,
    )

    assert result["completed"] == 2
    assert result["elapsed"] == pytest.approx(0.2)
    assert result["pacing"]["scheduled_span_s"] == pytest.approx(0.1)
    assert result["pacing"]["completion_lag_s"] == pytest.approx(0.1)
    assert result["pacing"]["arrival_lag_ms"]["p95_ms"] == pytest.approx(0.0)
