#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path


def audit_summary(items):
    samples = [sample for item in items for sample in item.get("samples", [])]
    epochs = [sample["epoch"] for sample in samples]
    return {
        "keys": len(items),
        "samples": len(samples),
        "observed_ms": (max(epochs) - min(epochs)) * 1000 if len(epochs) > 1 else 0,
        "residue_cleared": (
            all(item.get("residue_cleared", False) for item in items) if items else None
        ),
        "ever_readable": any(item.get("ever_readable", False) for item in items),
        "published_complete_replica": any(
            item.get("published_complete_replica", False) for item in items
        ),
        "remove_return_codes": sorted(
            {
                sample.get("remove_return_code")
                for sample in samples
                if sample.get("remove_return_code") is not None
            }
        ),
    }


def load_rows(input_dir):
    rows = []
    details = []
    for path in sorted(input_dir.glob("*/trials/*/result.json")):
        data = json.loads(path.read_text())
        initial = audit_summary(data.get("failed_put_audit", []))
        post_close = audit_summary(data.get("post_close_failed_put_audit", []))
        row = {
            "run_id": data["run_id"],
            "mode": data["mode"],
            "trial": data["trial"],
            "status": data["status"],
            "pre_fault_successes": data["pre_fault_successes"],
            "fault_window_failures": data["fault_window_failures"],
            "post_fault_successes": data["post_fault_successes"],
            "first_failure_detection_ms": data["first_failure_detection_ms"],
            "recovery_latency_ms": data["recovery_latency_ms"],
            "persistent_client_recovered": data["persistent_client_recovered"],
            "initial_audit_samples": initial["samples"],
            "initial_residue_cleared": initial["residue_cleared"],
            "post_close_audit_samples": post_close["samples"],
            "post_close_residue_cleared": post_close["residue_cleared"],
            "ever_readable": initial["ever_readable"] or post_close["ever_readable"],
            "published_complete_replica": initial["published_complete_replica"]
            or post_close["published_complete_replica"],
            "product_failures": "; ".join(data.get("product_failures", [])),
            "evidence_failures": "; ".join(data.get("evidence_failures", [])),
        }
        rows.append(row)
        details.append(
            {
                "run_id": data["run_id"],
                "initial_audit": initial,
                "post_close_audit": post_close,
                "original_client_close_epoch": data.get("original_client_close_epoch"),
                "post_close_audit_started_epoch": data.get("post_close_audit_started_epoch"),
            }
        )
    return rows, details


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    rows, details = load_rows(args.input_dir)
    if not rows:
        raise SystemExit("no trial result files found")

    fields = list(rows[0])
    with (args.output_dir / "trial-summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    current = next(row for row in rows if row["run_id"] == "20260823T183044Z")
    conclusion = {
        "status": "fail",
        "matrix_completed": False,
        "completed_trials": 1,
        "planned_trials": 6,
        "stop_reason": "failed put residue exceeded post-client-close deadline",
        "operation_recovery_observed": current["persistent_client_recovered"],
        "unsafe_publication_observed": current["ever_readable"]
        or current["published_complete_replica"],
        "true_concurrency_claimed": False,
        "claim_boundary": "Sequential single-client client-to-Master TCP fault only",
    }
    (args.output_dir / "audit-summary.json").write_text(
        json.dumps(details, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "matrix-conclusion.json").write_text(
        json.dumps(conclusion, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
