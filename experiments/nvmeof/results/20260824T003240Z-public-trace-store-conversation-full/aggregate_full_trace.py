#!/usr/bin/env python3
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


run_root = Path(sys.argv[1])
output = Path(sys.argv[2])
results = run_root / "results"
output.mkdir(parents=True, exist_ok=True)
design = json.loads((results / "design.json").read_text())

rows = []
cell_errors = []
raw_errors = []
trace_digests = defaultdict(set)
source_digests = defaultdict(set)
raw_bytes = 0
for cell in sorted((results / "cells").iterdir()):
    scenario, trial_text = cell.name.rsplit("-trial", 1)
    trial = int(trial_text)
    conclusion = json.loads((cell / "conclusion.json").read_text())
    if conclusion.get("status") != "pass" or conclusion.get("errors"):
        cell_errors.append({"cell": cell.name, "errors": conclusion.get("errors")})
    manifest = json.loads((cell / "manifest.json").read_text())
    trace_digests[scenario].add(manifest["trace_sha256"])
    source_digests[scenario].add(manifest["source_sha256"])
    for raw_path in sorted(cell.glob("raw-*.json")):
        raw_bytes += raw_path.stat().st_size
        raw = json.loads(raw_path.read_text())
        evidence = raw.get("evidence", {})
        errors = []
        if raw.get("status") != "pass" or raw.get("errors"):
            errors.append("raw replay failed")
        if raw.get("evidence_mode") != "compact" or "operations" in raw:
            errors.append("raw replay is not compact")
        if raw.get("event_count") != evidence.get("operation_count"):
            errors.append("event and operation counts differ")
        if len(evidence.get("samples", [])) != design["compact_sample_limit"]:
            errors.append("sample count differs from compact limit")
        for field in (
            "content_mismatches",
            "descriptor_mismatches",
            "return_code_failures",
            "error_count",
        ):
            if evidence.get(field) != 0:
                errors.append(f"nonzero {field}")
        if errors:
            raw_errors.append({"raw": str(raw_path.relative_to(results)), "errors": errors})
    with (cell / "summary.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            row["scenario"] = scenario
            row["trial"] = trial
            rows.append(row)

trial_fields = ["scenario", "trial", *[field for field in rows[0] if field not in {"scenario", "trial"}]]
with (output / "trial-summary.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=trial_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

numeric_fields = [
    "operations",
    "p50_latency_us",
    "p95_latency_us",
    "p99_latency_us",
    "operation_rate",
    "produce",
    "reuse",
    "evict",
    "miss",
    "request_count",
    "request_hit_rate",
    "block_hit_rate",
    "miss_rate",
    "request_p50_latency_us",
    "request_p95_latency_us",
    "storage_wait_us",
    "replay_scale",
    "local",
    "remote",
]
grouped = defaultdict(list)
for row in rows:
    grouped[(row["scenario"], row["case_id"])].append(row)

aggregate_rows = []
for (scenario, case_id), group in sorted(grouped.items()):
    aggregate_row = {
        "scenario": scenario,
        "case_id": case_id,
        "mode": group[0]["mode"],
        "target": group[0]["target"],
        "trials": len(group),
    }
    for field in numeric_fields:
        values = [float(row[field]) for row in group if row[field] != ""]
        aggregate_row[f"median_{field}"] = statistics.median(values) if values else ""
    aggregate_rows.append(aggregate_row)

with (output / "aggregate-summary.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(
        stream, fieldnames=list(aggregate_rows[0]), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(aggregate_rows)

by_cell_case = {
    (row["scenario"], int(row["trial"]), row["case_id"]): row for row in rows
}
paired_rows = []
for scenario in design["scenarios"]:
    for trial in (1, 2, 3):
        for target in ("local", "remote"):
            direct = by_cell_case[(scenario, trial, f"direct-{target}")]
            transparent = by_cell_case[(scenario, trial, f"transparent-{target}")]
            paired_row = {"scenario": scenario, "trial": trial, "target": target}
            for field in (
                "request_p50_latency_us",
                "request_p95_latency_us",
                "storage_wait_us",
                "operation_rate",
            ):
                direct_value = float(direct[field])
                transparent_value = float(transparent[field])
                paired_row[f"direct_{field}"] = direct_value
                paired_row[f"transparent_{field}"] = transparent_value
                paired_row[f"transparent_vs_direct_{field}_pct"] = (
                    (transparent_value - direct_value) / direct_value * 100
                )
            paired_rows.append(paired_row)

with (output / "paired-trials.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(paired_rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(paired_rows)

paired_aggregate_rows = []
paired_metric_fields = [
    field for field in paired_rows[0] if field not in {"scenario", "trial", "target"}
]
for scenario in design["scenarios"]:
    for target in ("local", "remote"):
        group = [
            row
            for row in paired_rows
            if row["scenario"] == scenario and row["target"] == target
        ]
        aggregate_row = {"scenario": scenario, "target": target, "trials": len(group)}
        for field in paired_metric_fields:
            aggregate_row[f"median_{field}"] = statistics.median(
                row[field] for row in group
            )
        paired_aggregate_rows.append(aggregate_row)

with (output / "paired-aggregate.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(
        stream, fieldnames=list(paired_aggregate_rows[0]), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(paired_aggregate_rows)

rc_paths = sorted((results / "cells").glob("*/*.rc"))
nonzero_rcs = [
    str(path.relative_to(results))
    for path in rc_paths
    if path.read_text().strip() != "0"
]
recovery = json.loads(
    (results / "smoke" / "final-round-robin" / "raw-transparent.json").read_text()
)
client_inventory_equal = (
    results / "inventory" / "client-nvme-subsystems.before.txt"
).read_bytes() == (
    results / "inventory" / "client-nvme-subsystems.after.txt"
).read_bytes()
observed_event_counts = sorted({int(float(row["operations"])) for row in rows})
observed_replay_scales = sorted({float(row["replay_scale"]) for row in rows})
store_rows = [row for row in rows if row["mode"] != "no_store"]
errors = []
if len(rc_paths) != design["planned_cases"] or nonzero_rcs:
    errors.append("case count or return-code gate failed")
if len(rows) != design["planned_cases"] or len(list((results / "cells").glob("*/conclusion.json"))) != 3:
    errors.append("summary or conclusion count gate failed")
if cell_errors or raw_errors:
    errors.append("one or more compact replay artifacts failed validation")
if any(len(values) != 1 for values in trace_digests.values()):
    errors.append("converted trace digest differs across trials")
if any(len(values) != 1 for values in source_digests.values()):
    errors.append("source trace digest differs across trials")
if observed_event_counts != [565798] or observed_replay_scales != [0.0]:
    errors.append("event-count or replay-scale gate failed")
if not client_inventory_equal:
    errors.append("client NVMe subsystem inventory changed")
if recovery.get("status") != "pass" or recovery.get("errors"):
    errors.append("final round-robin recovery failed")
if (results / "inventory" / "initial-master-policy.txt").read_text().strip() != "round_robin":
    errors.append("initial master policy was not round_robin")

matrix = {
    "status": "pass" if not errors else "inconclusive",
    "run_id": design["run_id"],
    "scenario": design["scenarios"][0],
    "requests": design["requests"],
    "capacity_pages": design["capacity_pages"],
    "block_size": design["block_size"],
    "replay_scale": design["replay_scale"],
    "planned_cases": design["planned_cases"],
    "observed_cases": len(rc_paths),
    "nonzero_case_rcs": nonzero_rcs,
    "cell_conclusions": len(list((results / "cells").glob("*/conclusion.json"))),
    "cell_errors": cell_errors,
    "raw_errors": raw_errors,
    "event_count_per_case": observed_event_counts[0],
    "compact_sample_limit": design["compact_sample_limit"],
    "raw_json_bytes": raw_bytes,
    "trace_digests": {key: sorted(value) for key, value in trace_digests.items()},
    "source_digests": {key: sorted(value) for key, value in source_digests.items()},
    "store_puts": sum(int(float(row["produce"])) for row in store_rows),
    "store_gets": sum(int(float(row["reuse"])) for row in store_rows),
    "store_removes": sum(int(float(row["evict"])) for row in store_rows),
    "store_payload_bytes_put_plus_get": sum(
        (int(float(row["produce"])) + int(float(row["reuse"])))
        * design["block_size"]
        for row in store_rows
    ),
    "client_nvme_inventory_equal": client_inventory_equal,
    "final_recovery_status": recovery.get("status"),
    "final_recovery_errors": recovery.get("errors"),
    "claim_boundary": design["claim_boundary"],
    "errors": errors,
}
(output / "matrix-conclusion.json").write_text(
    json.dumps(matrix, indent=2, sort_keys=True) + "\n"
)
print(json.dumps(matrix, indent=2, sort_keys=True))
