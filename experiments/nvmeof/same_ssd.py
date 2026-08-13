#!/usr/bin/env python3
"""Parse and summarize same-SSD SPDK-local versus Mooncake NoF runs."""

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path

from characterize import TIME_UNITS, UNITS, value_with_unit


CASE_RE = re.compile(
    r"(remote-before|local|remote-after)-size(\d+)-qd(\d+)-run(\d+)"
)


def parse_nof(path: Path) -> dict[str, float]:
    values = _key_values(path)
    required = ("bw", "iops", "clat_mean", "failed_ops")
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"{path}: missing fields: {', '.join(missing)}")
    return {
        "bandwidth_Bps": value_with_unit(values["bw"], UNITS),
        "iops": float(values["iops"]),
        "avg_latency_ms": value_with_unit(values["clat_mean"], TIME_UNITS),
        "p50_ms": _optional_latency(values, "clat_p50"),
        "p95_ms": _optional_latency(values, "clat_p95"),
        "p99_ms": _optional_latency(values, "clat_p99"),
        "errors": float(values["failed_ops"]),
    }


def parse_bdevperf(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    total = re.findall(
        r"Total\s*:\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)",
        text,
        re.IGNORECASE,
    )
    if total:
        iops, bandwidth, fail_rate, timeout_rate, latency = total[-1]
        parsed = {
            "iops": float(iops),
            "bandwidth_MiBps": float(bandwidth),
            "avg_latency_us": float(latency),
            "errors": float(fail_rate) + float(timeout_rate),
        }
    else:
        patterns = {
            "iops": r"IOPS\s*[:=]\s*([0-9.]+)",
            "bandwidth_MiBps": r"(?:MiB/s|MB/s)\s*[:=]\s*([0-9.]+)",
            "avg_latency_us": r"(?:Average latency|Latency avg|Avg latency)\s*[:=]\s*([0-9.]+)\s*us",
        }
        parsed = {}
        for key, pattern in patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if not matches:
                raise ValueError(f"{path}: missing {key}")
            parsed[key] = float(matches[-1])
        parsed["errors"] = 0.0
    histogram = {}
    for percentile in (50, 95, 99):
        match = re.search(
            rf"p{percentile}\s*[:=]?\s*([0-9.]+)\s*(ns|us|ms|s)",
            text,
            re.IGNORECASE,
        )
        histogram[f"p{percentile}_ms"] = (
            value_with_unit(" ".join(match.groups()), TIME_UNITS) if match else 0.0
        )
    return {
        "bandwidth_Bps": parsed["bandwidth_MiBps"] * 1024**2,
        "iops": parsed["iops"],
        "avg_latency_ms": parsed["avg_latency_us"] / 1000,
        **histogram,
        "errors": parsed["errors"],
    }


def _key_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.startswith("endpoint["):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _optional_latency(values: dict[str, str], key: str) -> float:
    return value_with_unit(values[key], TIME_UNITS) if key in values else 0.0


def _failed_metrics() -> dict[str, float]:
    return {
        "bandwidth_Bps": 0.0,
        "iops": 0.0,
        "avg_latency_ms": 0.0,
        "p50_ms": 0.0,
        "p95_ms": 0.0,
        "p99_ms": 0.0,
        "errors": 1.0,
    }


def collect(result_dir: Path) -> tuple[list[dict], list[dict]]:
    raw_rows = []
    grouped: dict[tuple[str, int, int], list[dict[str, float]]] = {}
    for path in sorted((result_dir / "raw").glob("*/*")):
        if path.suffix != ".log":
            continue
        match = CASE_RE.fullmatch(path.stem)
        if not match:
            continue
        phase, size, qd, run = match.groups()
        exit_path = path.with_suffix(".exitcode")
        exit_code = int(exit_path.read_text().strip()) if exit_path.exists() else 1
        try:
            metrics = parse_bdevperf(path) if phase == "local" else parse_nof(path)
        except ValueError:
            metrics = _failed_metrics()
        if exit_code:
            metrics["errors"] = max(metrics["errors"], 1.0)
        row = {
            "phase": phase,
            "size_bytes": int(size),
            "qd": int(qd),
            "run": int(run),
            "exit_code": exit_code,
            **metrics,
        }
        raw_rows.append(row)
        grouped.setdefault((phase, int(size), int(qd)), []).append(metrics)
    summary = []
    for (phase, size, qd), items in sorted(grouped.items()):
        summary.append(
            {
                "phase": phase,
                "size_bytes": size,
                "qd": qd,
                "runs": len(items),
                **{
                    key: (
                        sum(item[key] for item in items)
                        if key == "errors"
                        else statistics.median(item[key] for item in items)
                    )
                    for key in items[0]
                },
            }
        )
    return raw_rows, summary


def overhead_rows(summary: list[dict], repetitions: int, recovered: bool) -> list[dict]:
    index = {(row["phase"], row["size_bytes"], row["qd"]): row for row in summary}
    cases = sorted({(row["size_bytes"], row["qd"]) for row in summary})
    rows = []
    for size, qd in cases:
        before = index.get(("remote-before", size, qd))
        local = index.get(("local", size, qd))
        after = index.get(("remote-after", size, qd))
        valid = all(
            row and row["runs"] == repetitions and row["errors"] == 0
            for row in (before, local, after)
        ) and recovered
        drift = math.inf
        if before and after and before["avg_latency_ms"]:
            drift = abs(after["avg_latency_ms"] / before["avg_latency_ms"] - 1)
        status = "valid" if valid and drift <= 0.10 else "inconclusive"
        remote_latency = (
            statistics.median([before["avg_latency_ms"], after["avg_latency_ms"]])
            if before and after
            else 0.0
        )
        remote_bw = (
            statistics.median([before["bandwidth_Bps"], after["bandwidth_Bps"]])
            if before and after
            else 0.0
        )
        remote_iops = (
            statistics.median([before["iops"], after["iops"]])
            if before and after
            else 0.0
        )
        rows.append(
            {
                "size_bytes": size,
                "qd": qd,
                "remote_drift": drift,
                "remote_overhead": (
                    remote_latency / local["avg_latency_ms"] - 1
                    if status == "valid" and local["avg_latency_ms"]
                    else ""
                ),
                "remote_bw_ratio": (
                    remote_bw / local["bandwidth_Bps"]
                    if status == "valid" and local["bandwidth_Bps"]
                    else ""
                ),
                "remote_iops_ratio": (
                    remote_iops / local["iops"]
                    if status == "valid" and local["iops"]
                    else ""
                ),
                "status": status,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def summarize(result_dir: Path) -> None:
    manifest = json.loads((result_dir / "matrix.json").read_text())
    recovery = result_dir / "recovery.json"
    recovered = recovery.exists() and json.loads(recovery.read_text()).get("success") is True
    smart_unchanged = _smart_unchanged(result_dir / "smart/before.json", result_dir / "smart/after.json")
    raw, summary = collect(result_dir)
    overhead = overhead_rows(
        summary, int(manifest["repetitions"]), recovered and smart_unchanged
    )
    expected = len(manifest["sizes"]) * len(manifest["depths"])
    complete = len(overhead) == expected and all(row["status"] == "valid" for row in overhead)
    write_csv(result_dir / "runs.csv", raw)
    write_csv(result_dir / "summary.csv", summary)
    write_csv(result_dir / "same-ssd-overhead.csv", overhead)
    conclusion = {
        "decision": "complete" if complete else "inconclusive",
        "complete_matrix": complete,
        "service_recovered": recovered,
        "smart_unchanged": smart_unchanged,
        "valid_cases": sum(row["status"] == "valid" for row in overhead),
        "expected_cases": expected,
        "scope": "same physical SSD NVMe-oF path overhead only; not a scheduling crossover",
    }
    (result_dir / "conclusion.json").write_text(json.dumps(conclusion, indent=2) + "\n")


def _smart_unchanged(before_path: Path, after_path: Path) -> bool:
    if not before_path.exists() and not after_path.exists():
        return True
    if not before_path.exists() or not after_path.exists():
        return False
    try:
        before = json.loads(before_path.read_text())
        after = json.loads(after_path.read_text())
        return int(after.get("critical_warning", 0)) == 0 and int(
            after.get("media_errors", 0)
        ) <= int(before.get("media_errors", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("summarize",))
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    summarize(args.result_dir)


if __name__ == "__main__":
    main()
