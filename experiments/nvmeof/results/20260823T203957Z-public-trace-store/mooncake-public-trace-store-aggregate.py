#!/usr/bin/env python3
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


run_root = Path(sys.argv[1])
results = run_root / "results"
aggregate = run_root / "aggregate"
aggregate.mkdir(parents=True, exist_ok=True)

rows = []
cell_errors = []
trace_digests = defaultdict(set)
source_digests = defaultdict(set)
for cell in sorted((results / "cells").iterdir()):
    scenario, trial_text = cell.name.rsplit("-trial", 1)
    trial = int(trial_text)
    conclusion = json.loads((cell / "conclusion.json").read_text())
    if conclusion.get("status") != "pass" or conclusion.get("errors"):
        cell_errors.append({"cell": cell.name, "errors": conclusion.get("errors")})
    manifest = json.loads((cell / "manifest.json").read_text())
    trace_digests[scenario].add(manifest["trace_sha256"])
    source_digests[scenario].add(manifest["source_sha256"])
    with (cell / "summary.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            row["scenario"] = scenario
            row["trial"] = trial
            rows.append(row)

numeric_fields = [
    "operations",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "operation_rate",
    "produce",
    "reuse",
    "evict",
    "request_count",
    "request_hit_rate",
    "block_hit_rate",
    "request_p50_latency_us",
    "request_p95_latency_us",
    "storage_wait_us",
    "local",
    "remote",
]
grouped = defaultdict(list)
for row in rows:
    grouped[(row["scenario"], row["case_id"])].append(row)

aggregate_rows = []
for (scenario, case_id), group in sorted(grouped.items()):
    output = {
        "scenario": scenario,
        "case_id": case_id,
        "mode": group[0]["mode"],
        "target": group[0]["target"],
        "trials": len(group),
    }
    for field in numeric_fields:
        values = [float(row[field]) for row in group if row[field] != ""]
        output[f"median_{field}"] = statistics.median(values) if values else ""
    aggregate_rows.append(output)

aggregate_fields = list(aggregate_rows[0])
with (aggregate / "aggregate-summary.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=aggregate_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(aggregate_rows)

by_cell_case = {(row["scenario"], int(row["trial"]), row["case_id"]): row for row in rows}
paired_rows = []
for scenario in ("conversation", "toolagent"):
    for trial in (1, 2, 3):
        for target in ("local", "remote"):
            direct = by_cell_case[(scenario, trial, f"direct-{target}")]
            transparent = by_cell_case[(scenario, trial, f"transparent-{target}")]
            output = {"scenario": scenario, "trial": trial, "target": target}
            for field in (
                "request_p50_latency_us",
                "request_p95_latency_us",
                "storage_wait_us",
                "operation_rate",
            ):
                direct_value = float(direct[field])
                transparent_value = float(transparent[field])
                output[f"direct_{field}"] = direct_value
                output[f"transparent_{field}"] = transparent_value
                output[f"transparent_vs_direct_{field}_pct"] = (
                    (transparent_value - direct_value) / direct_value * 100
                )
            paired_rows.append(output)

paired_fields = list(paired_rows[0])
with (aggregate / "paired-trials.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=paired_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(paired_rows)

paired_aggregate_rows = []
for scenario in ("conversation", "toolagent"):
    for target in ("local", "remote"):
        group = [
            row
            for row in paired_rows
            if row["scenario"] == scenario and row["target"] == target
        ]
        output = {"scenario": scenario, "target": target, "trials": len(group)}
        for field in paired_fields[3:]:
            output[f"median_{field}"] = statistics.median(row[field] for row in group)
        paired_aggregate_rows.append(output)

paired_aggregate_fields = list(paired_aggregate_rows[0])
with (aggregate / "paired-aggregate.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(
        stream, fieldnames=paired_aggregate_fields, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(paired_aggregate_rows)

rc_paths = sorted((results / "cells").glob("*/*.rc"))
nonzero_rcs = [str(path.relative_to(results)) for path in rc_paths if path.read_text().strip() != "0"]
recovery = json.loads(
    (results / "smoke/final-round-robin/raw-transparent.json").read_text()
)
total_produce = sum(int(float(row["produce"])) for row in rows if row["mode"] != "no_store")
total_reuse = sum(int(float(row["reuse"])) for row in rows if row["mode"] != "no_store")
total_evict = sum(int(float(row["evict"])) for row in rows if row["mode"] != "no_store")
matrix = {
    "status": "pass",
    "planned_cases": 30,
    "observed_cases": len(rc_paths),
    "nonzero_case_rcs": nonzero_rcs,
    "cell_conclusions": len(list((results / "cells").glob("*/conclusion.json"))),
    "cell_errors": cell_errors,
    "trace_digests": {key: sorted(value) for key, value in trace_digests.items()},
    "source_digests": {key: sorted(value) for key, value in source_digests.items()},
    "store_puts": total_produce,
    "store_gets": total_reuse,
    "store_removes": total_evict,
    "store_payload_bytes_put_plus_get": (total_produce + total_reuse) * 131072,
    "final_recovery_status": recovery.get("status"),
    "final_recovery_errors": recovery.get("errors"),
    "client_master_initial_policy": (results / "inventory/initial-master-policy.txt").read_text().strip(),
    "client_master_final_policy": "round_robin",
    "errors": [],
}
if (
    matrix["observed_cases"] != matrix["planned_cases"]
    or nonzero_rcs
    or matrix["cell_conclusions"] != 6
    or cell_errors
    or any(len(value) != 1 for value in trace_digests.values())
    or any(len(value) != 1 for value in source_digests.values())
    or recovery.get("status") != "pass"
    or recovery.get("errors")
    or matrix["client_master_initial_policy"] != "round_robin"
):
    matrix["status"] = "inconclusive"
    matrix["errors"].append("one or more matrix acceptance gates failed")
(aggregate / "matrix-conclusion.json").write_text(
    json.dumps(matrix, indent=2, sort_keys=True) + "\n"
)
print(json.dumps(matrix, indent=2, sort_keys=True))
