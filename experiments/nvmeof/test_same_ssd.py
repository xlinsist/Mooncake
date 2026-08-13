import json
from pathlib import Path

import same_ssd


def nof_log(latency_ms=2.0, failed=0):
    return (
        f"failed_ops={failed}\nbw=2.00 GiB/s\niops=1000\n"
        f"clat_mean={latency_ms} ms\nclat_p95=3 ms\n"
    )


def bdevperf_log(latency_us=1000):
    return f"Total : 2000 4096 0.00 0.00 {latency_us}\n"


def prepare(tmp_path: Path, after_latency=2.1, failed=False, recovered=True):
    (tmp_path / "matrix.json").write_text(
        json.dumps({"sizes": [4096], "depths": [1], "repetitions": 3})
    )
    (tmp_path / "recovery.json").write_text(json.dumps({"success": recovered}))
    for phase in ("remote-before", "local", "remote-after"):
        directory = tmp_path / "raw" / phase
        directory.mkdir(parents=True)
        for run in range(1, 4):
            stem = f"{phase}-size4096-qd1-run{run}"
            content = (
                bdevperf_log()
                if phase == "local"
                else nof_log(
                    after_latency if phase == "remote-after" else 2,
                    int(failed and run == 1),
                )
            )
            (directory / f"{stem}.log").write_text(content)
            (directory / f"{stem}.exitcode").write_text("0\n")


def test_parsers(tmp_path: Path):
    nof = tmp_path / "nof.log"
    local = tmp_path / "local.log"
    nof.write_text(nof_log())
    local.write_text(bdevperf_log())
    assert same_ssd.parse_nof(nof)["avg_latency_ms"] == 2
    assert same_ssd.parse_bdevperf(local)["avg_latency_ms"] == 1


def test_valid_overhead(tmp_path: Path):
    prepare(tmp_path)
    same_ssd.summarize(tmp_path)
    result = json.loads((tmp_path / "conclusion.json").read_text())
    assert result["decision"] == "complete"
    row = (tmp_path / "same-ssd-overhead.csv").read_text().splitlines()[1].split(",")
    assert float(row[3]) == 1.0499999999999998
    assert float(row[4]) == 0.5
    assert float(row[5]) == 0.5
    assert row[6] == "valid"


def test_drift_failure_and_recovery_are_inconclusive(tmp_path: Path):
    for name, arguments in (
        ("drift", {"after_latency": 2.3}),
        ("failure", {"failed": True}),
        ("recovery", {"recovered": False}),
    ):
        case = tmp_path / name
        case.mkdir()
        prepare(case, **arguments)
        same_ssd.summarize(case)
        result = json.loads((case / "conclusion.json").read_text())
        assert result["decision"] == "inconclusive"


def test_missing_sample_is_inconclusive(tmp_path: Path):
    prepare(tmp_path)
    (tmp_path / "raw/local/local-size4096-qd1-run3.log").unlink()
    same_ssd.summarize(tmp_path)
    assert json.loads((tmp_path / "conclusion.json").read_text())["decision"] == "inconclusive"
