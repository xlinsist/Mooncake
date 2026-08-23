#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def rounded(value: str) -> str:
    return f"{float(value):.6f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()

    trace_rows = []
    overhead_rows = []
    for input_dir in args.inputs:
        aggregates = read_rows(input_dir / "aggregate-summary.csv")
        request_counts = {}
        for row in aggregates:
            if row["case_id"] != "no_store":
                continue
            requests = int(float(row["median_request_count"]))
            request_counts[row["scenario"]] = requests
            trace_rows.append(
                {
                    "scenario": row["scenario"],
                    "requests": requests,
                    "events": int(float(row["median_operations"])),
                    "produce": int(float(row["median_produce"])),
                    "reuse": int(float(row["median_reuse"])),
                    "evict": int(float(row["median_evict"])),
                    "request_hit_rate": rounded(row["median_request_hit_rate"]),
                    "block_hit_rate": rounded(row["median_block_hit_rate"]),
                }
            )

        for row in read_rows(input_dir / "paired-aggregate.csv"):
            overhead_rows.append(
                {
                    "scenario": row["scenario"],
                    "requests": request_counts[row["scenario"]],
                    "target": row["target"],
                    "trials": int(row["trials"]),
                    "request_p50_overhead_pct": rounded(
                        row[
                            "median_transparent_vs_direct_request_p50_latency_us_pct"
                        ]
                    ),
                    "request_p95_overhead_pct": rounded(
                        row[
                            "median_transparent_vs_direct_request_p95_latency_us_pct"
                        ]
                    ),
                    "storage_wait_overhead_pct": rounded(
                        row["median_transparent_vs_direct_storage_wait_us_pct"]
                    ),
                    "event_rate_delta_pct": rounded(
                        row["median_transparent_vs_direct_operation_rate_pct"]
                    ),
                }
            )

    args.output.mkdir(parents=True, exist_ok=True)
    trace_rows.sort(key=lambda row: (row["scenario"], row["requests"]))
    overhead_rows.sort(
        key=lambda row: (row["scenario"], row["target"], row["requests"])
    )
    with (args.output / "trace-scale-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(trace_rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(trace_rows)
    with (args.output / "overhead-scale-summary.csv").open(
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
