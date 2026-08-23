#!/usr/bin/env python3
import json
import sys
from pathlib import Path


root = Path(sys.argv[1])
capacities = (16, 64, 256)
matrices = {}
hardware = {}
for capacity in capacities:
    cell = root / f"c{capacity}"
    matrices[capacity] = json.loads(
        (cell / "matrix-conclusion.json").read_text(encoding="utf-8")
    )
    hardware[capacity] = json.loads(
        (cell / "hardware-restoration.json").read_text(encoding="utf-8")
    )

result = {
    "status": "pass",
    "capacity_pages": list(capacities),
    "planned_cases": sum(item["planned_cases"] for item in matrices.values()),
    "observed_cases": sum(item["observed_cases"] for item in matrices.values()),
    "store_puts": sum(item["store_puts"] for item in matrices.values()),
    "store_gets": sum(item["store_gets"] for item in matrices.values()),
    "store_removes": sum(item["store_removes"] for item in matrices.values()),
    "store_payload_bytes_put_plus_get": sum(
        item["store_payload_bytes_put_plus_get"] for item in matrices.values()
    ),
    "matrix_status": {str(key): value["status"] for key, value in matrices.items()},
    "hardware_status": {str(key): value["status"] for key, value in hardware.items()},
    "errors": [],
}
gates = [
    result["planned_cases"] == 90,
    result["observed_cases"] == 90,
    all(item["status"] == "pass" for item in matrices.values()),
    all(item["status"] == "pass" for item in hardware.values()),
    all(not item["errors"] for item in matrices.values()),
    all(not item["errors"] for item in hardware.values()),
]
if not all(gates):
    result["status"] = "fail"
    result["errors"].append("one or more capacity acceptance gates failed")
(root / "capacity-conclusion.json").write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result["status"] == "pass" else 1)
