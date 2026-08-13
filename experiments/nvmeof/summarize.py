#!/usr/bin/env python3
import argparse
import csv
import json
import re
import statistics
from pathlib import Path


UNITS = {"B/s": 1, "KiB/s": 1024, "MiB/s": 1024**2, "GiB/s": 1024**3}
TIME_UNITS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}


def value_with_unit(text: str, units: dict[str, int]) -> float:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z/]+)\s*", text)
    if not match or match.group(2) not in units:
        raise ValueError(f"unsupported value: {text}")
    return float(match.group(1)) * units[match.group(2)]


def parse_nof(path: Path) -> dict:
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.startswith("endpoint["):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return {
        "bandwidth": value_with_unit(values["bw"], UNITS),
        "iops": float(values["iops"]),
        "p99_ns": value_with_unit(values["clat_p99"], TIME_UNITS),
        "errors": int(values["failed_ops"]),
    }


def fio_percentile_ns(job: dict) -> float:
    latency = job["read"] if job["read"]["io_bytes"] else job["write"]
    percentiles = latency.get("clat_ns", {}).get("percentile", {})
    return float(percentiles.get("99.000000", 0))


def parse_fio(path: Path) -> dict:
    data = json.loads(path.read_text())
    job = data["jobs"][0]
    io = job["read"] if job["read"]["io_bytes"] else job["write"]
    return {
        "bandwidth": float(io["bw_bytes"]),
        "iops": float(io["iops"]),
        "p99_ns": fio_percentile_ns(job),
        "errors": int(job.get("error", 0)),
    }


def median_rows(paths: list[Path], parser):
    parsed = [parser(path) for path in paths]
    return {key: statistics.median(row[key] for row in parsed) for key in parsed[0]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    baseline = {}
    nof = {}
    for path in sorted((args.result_dir / "fio").glob("*.json")):
        match = re.match(r"(seq|rand)-(read|write)-bs(\d+)-qd(\d+)-run\d+", path.stem)
        if match:
            baseline.setdefault(match.groups(), []).append(path)
    for path in sorted((args.result_dir / "nof").glob("*.log")):
        match = re.match(r"(seq|rand)-(read|write)-bs(\d+)-qd(\d+)-run\d+", path.stem)
        if match:
            nof.setdefault(match.groups(), []).append(path)

    rows = []
    for key in sorted(set(baseline) & set(nof)):
        base = median_rows(baseline[key], parse_fio)
        remote = median_rows(nof[key], parse_nof)
        random_mode, operation, block_size, depth = key
        primary_ratio = (
            remote["iops"] / base["iops"]
            if random_mode == "rand"
            else remote["bandwidth"] / base["bandwidth"]
        )
        latency_ratio = (
            remote["p99_ns"] / base["p99_ns"] if base["p99_ns"] else float("inf")
        )
        passed = (
            remote["errors"] == 0
            and base["errors"] == 0
            and primary_ratio >= 0.70
            and latency_ratio <= 2.0
        )
        rows.append(
            {
                "pattern": random_mode,
                "operation": operation,
                "block_size": block_size,
                "iodepth": depth,
                "baseline_bw_Bps": base["bandwidth"],
                "nof_bw_Bps": remote["bandwidth"],
                "baseline_iops": base["iops"],
                "nof_iops": remote["iops"],
                "baseline_p99_ns": base["p99_ns"],
                "nof_p99_ns": remote["p99_ns"],
                "primary_ratio": primary_ratio,
                "p99_ratio": latency_ratio,
                "errors": remote["errors"],
                "pass": passed,
            }
        )

    csv_path = args.result_dir / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
    acceptance = {
        "performance": "pass" if rows and all(row["pass"] for row in rows) else "fail",
        "matched_cases": len(rows),
        "functional": "operator-review-required",
        "failure_recovery": "operator-review-required",
    }
    (args.result_dir / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2) + "\n"
    )
    print(json.dumps(acceptance, indent=2))


if __name__ == "__main__":
    main()
