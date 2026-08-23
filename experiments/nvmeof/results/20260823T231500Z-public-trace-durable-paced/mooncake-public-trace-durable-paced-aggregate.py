#!/usr/bin/env python3

import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path


CASE_PATTERN = re.compile(
    r"^(?P<scenario>conversation|toolagent)-r(?P<requests>100)-t(?P<trial>[123])-(?P<path>local|remote)$"
)


def number(text: str) -> float:
    return float(text.replace(",", ""))


def integer(text: str) -> int:
    return int(text.replace(",", ""))


def section(text: str, name: str) -> str:
    match = re.search(
        rf"^\[{re.escape(name)}\]\n(?P<body>.*?)(?=^\[|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"missing section {name}")
    return match.group("body")


def field(body: str, label: str, suffix: str = "") -> float:
    match = re.search(
        rf"^\s*{re.escape(label)}:\s*([0-9,.]+)\s*{re.escape(suffix)}\s*$",
        body,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"missing field {label}")
    return number(match.group(1))


def latency(body: str, percentile: str) -> float:
    match = re.search(
        rf"Latency:\s*\n(?:\s+.*\n)*?\s+{re.escape(percentile)}:\s*([0-9,.]+)\s+ms",
        body,
    )
    if not match:
        raise ValueError(f"missing latency {percentile}")
    return number(match.group(1))


def parse_elapsed(value: str) -> float:
    parts = [float(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"invalid elapsed value {value}")


def parse_case(log_path: Path) -> dict:
    case_id = log_path.stem
    identity = CASE_PATTERN.fullmatch(case_id)
    if not identity:
        raise ValueError(f"invalid case id {case_id}")
    text = log_path.read_text(encoding="utf-8")
    time_text = log_path.with_suffix(".time").read_text(encoding="utf-8")
    rc = integer(log_path.with_suffix(".rc").read_text(encoding="utf-8").strip())
    general = section(text, "General")
    request_wall = section(text, "Request Wall Latency")
    request_io = section(text, "Request Storage I/O Latency")
    reads = section(text, "Read Operations")
    writes = section(text, "Write Operations")
    storage = section(text, "Storage Info")
    elapsed_match = re.search(
        r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", time_text
    )
    cpu_match = re.search(r"Percent of CPU this job got: ([0-9.]+)%", time_text)
    rss_match = re.search(r"Maximum resident set size \(kbytes\): ([0-9]+)", time_text)
    exit_match = re.search(r"Exit status: ([0-9]+)", time_text)
    if not all((elapsed_match, cpu_match, rss_match, exit_match)):
        raise ValueError(f"incomplete time output for {case_id}")
    row = {
        "case_id": case_id,
        **identity.groupdict(),
        "requests": int(identity.group("requests")),
        "trial": int(identity.group("trial")),
        "rc": rc,
        "tokens": int(field(general, "Tokens")),
        "total_io_time_s": field(general, "Total I/O Time", "s"),
        "qps": field(general, "QPS"),
        "hit_rate_pct": field(general, "Hit Rate", "%"),
        "replay_scale": field(general, "Fast-forward", "x"),
        "scheduled_span_s": field(general, "Scheduled Span", "s"),
        "completion_lag_s": field(general, "Completion Lag", "s"),
        "arrival_lag_p50_ms": field(general, "Arrival Lag P50", "ms"),
        "arrival_lag_p95_ms": field(general, "Arrival Lag P95", "ms"),
        "arrival_lag_max_ms": field(general, "Arrival Lag Max", "ms"),
        "request_wall_avg_ms": field(request_wall, "Avg", "ms"),
        "request_wall_p50_ms": field(request_wall, "P50", "ms"),
        "request_wall_p95_ms": field(request_wall, "P95", "ms"),
        "request_wall_p99_ms": field(request_wall, "P99", "ms"),
        "request_io_avg_ms": field(request_io, "Avg", "ms"),
        "request_io_p50_ms": field(request_io, "P50", "ms"),
        "request_io_p95_ms": field(request_io, "P95", "ms"),
        "request_io_p99_ms": field(request_io, "P99", "ms"),
        "read_count": int(field(reads, "Count")),
        "read_bandwidth_mb_s": field(reads, "Bandwidth", "MB/s")
        if "Bandwidth:" in reads
        else 0.0,
        "read_p50_ms": latency(reads, "P50") if "Latency:" in reads else 0.0,
        "read_p95_ms": latency(reads, "P95") if "Latency:" in reads else 0.0,
        "read_p99_ms": latency(reads, "P99") if "Latency:" in reads else 0.0,
        "write_count": int(field(writes, "Count")),
        "write_bandwidth_mb_s": field(writes, "Bandwidth", "MB/s"),
        "write_p50_ms": latency(writes, "P50"),
        "write_p95_ms": latency(writes, "P95"),
        "write_p99_ms": latency(writes, "P99"),
        "max_pages": int(field(storage, "Max Pages")),
        "written_pages": int(field(storage, "Written Pages")),
        "sync_count": int(field(storage, "Sync Count")),
        "elapsed_s": parse_elapsed(elapsed_match.group(1)),
        "cpu_pct": number(cpu_match.group(1)),
        "max_rss_kb": integer(rss_match.group(1)),
        "time_exit_status": integer(exit_match.group(1)),
    }
    loaded_match = re.search(r"Loaded ([0-9,]+) requests", text)
    if not loaded_match:
        raise ValueError(f"missing loaded request count for {case_id}")
    row["loaded_requests"] = integer(loaded_match.group(1))
    return row


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def median_rows(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["requests"], row["path"])].append(row)
    metrics = [
        "tokens",
        "total_io_time_s",
        "qps",
        "hit_rate_pct",
        "replay_scale",
        "scheduled_span_s",
        "completion_lag_s",
        "arrival_lag_p50_ms",
        "arrival_lag_p95_ms",
        "arrival_lag_max_ms",
        "request_wall_p50_ms",
        "request_wall_p95_ms",
        "request_wall_p99_ms",
        "request_io_p50_ms",
        "request_io_p95_ms",
        "request_io_p99_ms",
        "read_count",
        "read_bandwidth_mb_s",
        "read_p50_ms",
        "read_p95_ms",
        "read_p99_ms",
        "write_count",
        "write_bandwidth_mb_s",
        "write_p50_ms",
        "write_p95_ms",
        "write_p99_ms",
        "written_pages",
        "sync_count",
        "elapsed_s",
        "cpu_pct",
        "max_rss_kb",
    ]
    output = []
    for key in sorted(grouped):
        group = grouped[key]
        item = {
            "scenario": key[0],
            "requests": key[1],
            "path": key[2],
            "repeats": len(group),
        }
        for metric in metrics:
            item[f"median_{metric}"] = round(
                statistics.median(row[metric] for row in group), 6
            )
        output.append(item)
    return output


def paired_rows(rows: list[dict]) -> list[dict]:
    indexed = {
        (row["scenario"], row["requests"], row["trial"], row["path"]): row
        for row in rows
    }
    output = []
    for scenario in ("conversation", "toolagent"):
        for requests in (100,):
            for trial in (1, 2, 3):
                local = indexed[(scenario, requests, trial, "local")]
                remote = indexed[(scenario, requests, trial, "remote")]
                output.append(
                    {
                        "scenario": scenario,
                        "requests": requests,
                        "trial": trial,
                        "remote_qps_over_local": round(remote["qps"] / local["qps"], 6),
                        "remote_request_p50_over_local": round(
                            remote["request_wall_p50_ms"]
                            / local["request_wall_p50_ms"],
                            6,
                        ),
                        "remote_request_p95_over_local": round(
                            remote["request_wall_p95_ms"]
                            / local["request_wall_p95_ms"],
                            6,
                        ),
                        "remote_request_p99_over_local": round(
                            remote["request_wall_p99_ms"]
                            / local["request_wall_p99_ms"],
                            6,
                        ),
                        "remote_read_p50_over_local": round(
                            remote["read_p50_ms"] / local["read_p50_ms"], 6
                        ),
                        "remote_write_p50_over_local": round(
                            remote["write_p50_ms"] / local["write_p50_ms"], 6
                        ),
                        "remote_completion_lag_over_local": round(
                            remote["completion_lag_s"] / local["completion_lag_s"],
                            6,
                        ),
                        "remote_arrival_lag_p95_over_local": round(
                            remote["arrival_lag_p95_ms"] / local["arrival_lag_p95_ms"],
                            6,
                        ),
                    }
                )
    return output


def paired_aggregate_rows(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["requests"])].append(row)
    trial_pairs = paired_rows(rows)
    pair_index = defaultdict(list)
    for row in trial_pairs:
        pair_index[(row["scenario"], row["requests"])].append(row)
    output = []
    metrics = [
        "remote_qps_over_local",
        "remote_request_p50_over_local",
        "remote_request_p95_over_local",
        "remote_request_p99_over_local",
        "remote_read_p50_over_local",
        "remote_write_p50_over_local",
        "remote_completion_lag_over_local",
        "remote_arrival_lag_p95_over_local",
    ]
    for key in sorted(grouped):
        item = {"scenario": key[0], "requests": key[1], "repeats": len(pair_index[key])}
        for metric in metrics:
            values = [row[metric] for row in pair_index[key]]
            item[f"median_{metric}"] = round(statistics.median(values), 6)
            item[f"min_{metric}"] = round(min(values), 6)
            item[f"max_{metric}"] = round(max(values), 6)
        output.append(item)
    return output


def main() -> None:
    result = Path(sys.argv[1])
    rows = sorted(
        (parse_case(path) for path in (result / "raw").glob("*.log")),
        key=lambda row: row["case_id"],
    )
    errors = []
    expected = {
        f"{scenario}-r{requests}-t{trial}-{path}"
        for scenario in ("conversation", "toolagent")
        for requests in (100,)
        for trial in (1, 2, 3)
        for path in ("local", "remote")
    }
    observed = {row["case_id"] for row in rows}
    if observed != expected:
        errors.append(
            f"case mismatch missing={sorted(expected - observed)} extra={sorted(observed - expected)}"
        )
    for row in rows:
        if row["rc"] != 0 or row["time_exit_status"] != 0:
            errors.append(f"nonzero exit: {row['case_id']}")
        if row["loaded_requests"] != row["requests"]:
            errors.append(f"request mismatch: {row['case_id']}")
        if row["max_pages"] != 64:
            errors.append(f"max-pages mismatch: {row['case_id']}")
        if row["replay_scale"] != 1 or row["scheduled_span_s"] <= 0:
            errors.append(f"pacing mismatch: {row['case_id']}")
        if (
            row["write_count"] != row["written_pages"]
            or row["write_count"] != row["sync_count"]
        ):
            errors.append(f"durability count mismatch: {row['case_id']}")
    for scenario in ("conversation", "toolagent"):
        for requests in (100,):
            group = [
                row
                for row in rows
                if row["scenario"] == scenario and row["requests"] == requests
            ]
            for metric in ("tokens", "hit_rate_pct", "read_count", "write_count"):
                if len({row[metric] for row in group}) != 1:
                    errors.append(f"workload mismatch: {scenario} r{requests} {metric}")
    for name in ("target-subsystems", "target-bdevs", "target-service"):
        before = (
            result
            / "meta"
            / f"{name}.before.{ 'json' if name != 'target-service' else 'txt'}"
        ).read_bytes()
        after = (
            result
            / "meta"
            / f"{name}.after.{ 'json' if name != 'target-service' else 'txt'}"
        ).read_bytes()
        if before != after:
            errors.append(f"environment mismatch: {name}")
    aggregates = median_rows(rows)
    pairs = paired_rows(rows)
    pair_aggregates = paired_aggregate_rows(rows)
    write_csv(result / "trial-summary.csv", rows)
    write_csv(result / "aggregate-summary.csv", aggregates)
    write_csv(result / "paired-path-summary.csv", pairs)
    write_csv(result / "paired-path-aggregate.csv", pair_aggregates)
    conclusion = {
        "status": "pass" if not errors else "fail",
        "planned_cases": 12,
        "observed_cases": len(rows),
        "complete_matrix": observed == expected,
        "all_exit_zero": all(
            row["rc"] == 0 and row["time_exit_status"] == 0 for row in rows
        ),
        "all_sync_counts_match_writes": all(
            row["write_count"] == row["sync_count"] for row in rows
        ),
        "all_pacing_metrics_present": all(
            row["replay_scale"] == 1 and row["scheduled_span_s"] > 0 for row in rows
        ),
        "total_writes": sum(row["write_count"] for row in rows),
        "total_reads": sum(row["read_count"] for row in rows),
        "approximate_payload_bytes": sum(
            row["write_count"] + row["read_count"] for row in rows
        )
        * 56_229_888,
        "target_subsystems_restored": "environment mismatch: target-subsystems"
        not in errors,
        "target_bdevs_restored": "environment mismatch: target-bdevs" not in errors,
        "target_service_restored": "environment mismatch: target-service" not in errors,
        "errors": errors,
    }
    (result / "matrix-conclusion.json").write_text(
        json.dumps(conclusion, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(conclusion, indent=2))


if __name__ == "__main__":
    main()
