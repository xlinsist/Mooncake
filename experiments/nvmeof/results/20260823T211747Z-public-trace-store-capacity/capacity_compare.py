#!/usr/bin/env python3
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def number(value: str) -> str:
    return f"{float(value):.6f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()

    trace_rows = []
    overhead_rows = []
    for input_dir in args.inputs:
        design = json.loads((input_dir / "design.json").read_text(encoding="utf-8"))
        capacity = int(design["capacity_pages"])
        for row in read_rows(input_dir / "aggregate-summary.csv"):
            if row["case_id"] != "no_store":
                continue
            produce = int(float(row["median_produce"]))
            reuse = int(float(row["median_reuse"]))
            trace_rows.append(
                {
                    "scenario": row["scenario"],
                    "capacity_pages": capacity,
                    "requests": int(float(row["median_request_count"])),
                    "events": int(float(row["median_operations"])),
                    "produce": produce,
                    "reuse": reuse,
                    "evict": int(float(row["median_evict"])),
                    "request_hit_rate": number(row["median_request_hit_rate"]),
                    "block_hit_rate": number(row["median_block_hit_rate"]),
                    "reuse_fraction": f"{reuse / (produce + reuse):.6f}",
                }
            )

        trials = defaultdict(list)
        for row in read_rows(input_dir / "paired-trials.csv"):
            trials[(row["scenario"], row["target"])].append(row)
        for row in read_rows(input_dir / "paired-aggregate.csv"):
            trial_rows = trials[(row["scenario"], row["target"])]
            output = {
                "scenario": row["scenario"],
                "capacity_pages": capacity,
                "target": row["target"],
                "trials": int(row["trials"]),
            }
            metrics = {
                "request_p50": "request_p50_latency_us",
                "request_p95": "request_p95_latency_us",
                "storage_wait": "storage_wait_us",
                "event_rate": "operation_rate",
            }
            for label, source in metrics.items():
                aggregate_field = f"median_transparent_vs_direct_{source}_pct"
                trial_field = f"transparent_vs_direct_{source}_pct"
                values = [float(item[trial_field]) for item in trial_rows]
                output[f"{label}_median_pct"] = number(row[aggregate_field])
                output[f"{label}_min_pct"] = f"{min(values):.6f}"
                output[f"{label}_max_pct"] = f"{max(values):.6f}"
            overhead_rows.append(output)

    args.output.mkdir(parents=True, exist_ok=True)
    trace_rows.sort(key=lambda row: (row["scenario"], row["capacity_pages"]))
    overhead_rows.sort(
        key=lambda row: (row["scenario"], row["target"], row["capacity_pages"])
    )
    with (args.output / "capacity-trace-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(trace_rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(trace_rows)
    with (args.output / "capacity-overhead-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(overhead_rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(overhead_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
