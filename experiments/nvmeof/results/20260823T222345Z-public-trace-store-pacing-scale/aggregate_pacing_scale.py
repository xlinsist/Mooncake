#!/usr/bin/env python3
import csv
import json
import sys
from pathlib import Path


root = Path(sys.argv[1])
scales = (5, 10, 20)
summary_rows = []
overhead_rows = []
matrices = []

for scale in scales:
    scale_dir = root / "scales" / f"s{scale}"
    matrix = json.loads((scale_dir / "matrix-conclusion.json").read_text())
    matrices.append(matrix)
    with (scale_dir / "aggregate-summary.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            scheduled_span_us = float(row["median_scheduled_span_us"])
            completion_lag_us = float(row["median_completion_lag_us"])
            summary_rows.append(
                {
                    "replay_scale": scale,
                    "scenario": row["scenario"],
                    "case_id": row["case_id"],
                    "mode": row["mode"],
                    "target": row["target"],
                    "scheduled_span_s": scheduled_span_us / 1_000_000,
                    "processing_wall_s": float(row["median_processing_wall_us"])
                    / 1_000_000,
                    "completion_lag_s": completion_lag_us / 1_000_000,
                    "completion_lag_pct": completion_lag_us / scheduled_span_us * 100,
                    "arrival_lag_p50_ms": float(
                        row["median_request_arrival_lag_p50_us"]
                    )
                    / 1_000,
                    "arrival_lag_p95_ms": float(
                        row["median_request_arrival_lag_p95_us"]
                    )
                    / 1_000,
                    "arrival_lag_max_ms": float(
                        row["median_request_arrival_lag_max_us"]
                    )
                    / 1_000,
                    "request_p50_latency_us": row["median_request_p50_latency_us"],
                    "request_p95_latency_us": row["median_request_p95_latency_us"],
                    "storage_wait_us": row["median_storage_wait_us"],
                    "operation_rate": row["median_operation_rate"],
                }
            )
    with (scale_dir / "paired-aggregate.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            overhead_rows.append(
                {
                    "replay_scale": scale,
                    "scenario": row["scenario"],
                    "target": row["target"],
                    "request_p50_overhead_pct": row[
                        "median_transparent_vs_direct_request_p50_latency_us_pct"
                    ],
                    "request_p95_overhead_pct": row[
                        "median_transparent_vs_direct_request_p95_latency_us_pct"
                    ],
                    "storage_wait_overhead_pct": row[
                        "median_transparent_vs_direct_storage_wait_us_pct"
                    ],
                    "operation_rate_delta_pct": row[
                        "median_transparent_vs_direct_operation_rate_pct"
                    ],
                }
            )

with (root / "pacing-scale-summary.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=summary_rows[0])
    writer.writeheader()
    writer.writerows(summary_rows)

with (root / "pacing-scale-overhead.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=overhead_rows[0])
    writer.writeheader()
    writer.writerows(overhead_rows)

trace_digests = {
    scenario: sorted(
        {digest for matrix in matrices for digest in matrix["trace_digests"][scenario]}
    )
    for scenario in ("conversation", "toolagent")
}
source_digests = {
    scenario: sorted(
        {digest for matrix in matrices for digest in matrix["source_digests"][scenario]}
    )
    for scenario in ("conversation", "toolagent")
}
client_inventory_equal = all(
    (root / "inventory" / f"s{scale}-client-nvme-subsystems.before.txt").read_bytes()
    == (root / "inventory" / f"s{scale}-client-nvme-subsystems.after.txt").read_bytes()
    for scale in (5, 20)
)
errors = []
if any(matrix["status"] != "pass" or matrix["errors"] for matrix in matrices):
    errors.append("one or more scale matrices failed")
if any(matrix["observed_cases"] != 30 for matrix in matrices):
    errors.append("one or more scale matrices are incomplete")
if any(
    matrix["observed_replay_scales"] != [float(scale)]
    for matrix, scale in zip(matrices, scales)
):
    errors.append("replay scale mismatch")
if any(len(digests) != 1 for digests in trace_digests.values()):
    errors.append("trace digest mismatch across scales")
if any(len(digests) != 1 for digests in source_digests.values()):
    errors.append("source digest mismatch across scales")
if not client_inventory_equal:
    errors.append("client NVMe inventory changed")

conclusion = {
    "status": "pass" if not errors else "inconclusive",
    "replay_scales": list(scales),
    "planned_cases": 90,
    "observed_cases": sum(matrix["observed_cases"] for matrix in matrices),
    "store_puts": sum(matrix["store_puts"] for matrix in matrices),
    "store_gets": sum(matrix["store_gets"] for matrix in matrices),
    "store_removes": sum(matrix["store_removes"] for matrix in matrices),
    "store_payload_bytes_put_plus_get": sum(
        matrix["store_payload_bytes_put_plus_get"] for matrix in matrices
    ),
    "trace_digests": trace_digests,
    "source_digests": source_digests,
    "client_inventory_equal_for_new_scales": client_inventory_equal,
    "all_final_recovery_passed": all(
        matrix["final_recovery_status"] == "pass"
        and not matrix["final_recovery_errors"]
        for matrix in matrices
    ),
    "claim_boundary": (
        "Sequential arrival-paced replay only; no request overlap, true "
        "concurrency, saturation-throughput, or serving claim."
    ),
    "errors": errors,
}
(root / "pacing-scale-conclusion.json").write_text(
    json.dumps(conclusion, indent=2, sort_keys=True) + "\n"
)
print(json.dumps(conclusion, indent=2, sort_keys=True))
