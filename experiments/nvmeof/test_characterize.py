import json
from pathlib import Path

import characterize


def fio_result(bw: int, p95: int) -> dict:
    return {
        "jobs": [
            {
                "error": 0,
                "usr_cpu": 2,
                "sys_cpu": 3,
                "read": {
                    "io_bytes": 4096,
                    "bw_bytes": bw,
                    "iops": bw / 4096,
                    "clat_ns": {
                        "percentile": {
                            "50.000000": p95 / 2,
                            "95.000000": p95,
                            "99.000000": p95 * 2,
                        }
                    },
                },
                "write": {"io_bytes": 0},
            }
        ]
    }


def test_parse_fio_percentiles_and_cpu(tmp_path: Path):
    path = tmp_path / "fio.json"
    path.write_text(json.dumps(fio_result(1024, 2_000_000)))
    parsed = characterize.parse_fio(path)
    assert parsed["bandwidth_Bps"] == 1024
    assert parsed["p50_ms"] == 1
    assert parsed["p95_ms"] == 2
    assert parsed["p99_ms"] == 4
    assert parsed["cpu_pct"] == 5


def test_summarize_finds_remote_crossover(tmp_path: Path):
    local_dir = tmp_path / "raw" / "local"
    remote_dir = tmp_path / "raw" / "remote"
    telemetry = tmp_path / "telemetry"
    local_dir.mkdir(parents=True)
    remote_dir.mkdir(parents=True)
    telemetry.mkdir()
    (tmp_path / "matrix.json").write_text(
        json.dumps({"sizes": [4096], "local_loads": [0, 50], "remote_loads": [0, 50]})
    )
    for run in range(1, 4):
        (local_dir / f"local-size4096-load0-run{run}.json").write_text(
            json.dumps(fio_result(4096, 1_000_000))
        )
        (local_dir / f"local-size4096-load50-run{run}.json").write_text(
            json.dumps(fio_result(2048, 4_000_000))
        )
        for load in (0, 50):
            stem = f"remote-size4096-load{load}-run{run}"
            (remote_dir / f"{stem}.log").write_text(
                "failed_ops=0\nbw=3.00 KiB/s\niops=0.75\n"
                "clat_p50=1.00 ms\nclat_p95=2.00 ms\nclat_p99=3.00 ms\n"
            )
            (remote_dir / f"{stem}.exitcode").write_text("0\n")
    characterize.summarize(tmp_path)
    conclusion = json.loads((tmp_path / "conclusion.json").read_text())
    assert conclusion["decision"] == "go"
    assert conclusion["go_region_count"] == 1
    assert conclusion["complete_matrix"] is True
    crossover = (tmp_path / "crossover.csv").read_text()
    assert "local_load,4096,50,4.0,2.0,0.5,remote" in crossover
    characterize.plot(tmp_path / "summary.csv", tmp_path)
    assert "<svg" in (tmp_path / "size-bandwidth.svg").read_text()
    assert "remote" in (tmp_path / "crossover.svg").read_text()


def test_summarize_is_inconclusive_for_missing_or_failed_cases(tmp_path: Path):
    local_dir = tmp_path / "raw" / "local"
    remote_dir = tmp_path / "raw" / "remote"
    local_dir.mkdir(parents=True)
    remote_dir.mkdir(parents=True)
    (tmp_path / "matrix.json").write_text(
        json.dumps({"sizes": [4096], "local_loads": [0, 50], "remote_loads": [0]})
    )
    for run in range(1, 4):
        for load in (0, 50):
            (local_dir / f"local-size4096-load{load}-run{run}.json").write_text(
                json.dumps(fio_result(4096, 4_000_000 if load else 1_000_000))
            )
        stem = f"remote-size4096-load0-run{run}"
        (remote_dir / f"{stem}.log").write_text(
            "failed_ops=0\nbw=3.00 KiB/s\niops=0.75\n"
            "clat_p50=1.00 ms\nclat_p95=2.00 ms\nclat_p99=3.00 ms\n"
        )
        (remote_dir / f"{stem}.exitcode").write_text("1\n" if run == 1 else "0\n")
    characterize.summarize(tmp_path)
    conclusion = json.loads((tmp_path / "conclusion.json").read_text())
    assert conclusion["decision"] == "inconclusive"
    assert conclusion["complete_matrix"] is False


def test_add_spdk_telemetry(tmp_path: Path):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(
        json.dumps(
            {
                "tick_rate": 1000,
                "bdevs": [
                    {
                        "name": "disk",
                        "bytes_read": 0,
                        "bytes_written": 0,
                        "read_latency_ticks": 0,
                        "write_latency_ticks": 0,
                    }
                ],
            }
        )
    )
    after.write_text(
        json.dumps(
            {
                "tick_rate": 1000,
                "bdevs": [
                    {
                        "name": "disk",
                        "bytes_read": 2000,
                        "bytes_written": 0,
                        "read_latency_ticks": 500,
                        "write_latency_ticks": 0,
                    }
                ],
            }
        )
    )
    metrics = {"telemetry_elapsed_sec": 2.0}
    characterize.add_spdk_telemetry(metrics, before, after)
    assert metrics["remote_device_Bps"] == 1000
    assert metrics["remote_device_weighted_busy_pct"] == 25
