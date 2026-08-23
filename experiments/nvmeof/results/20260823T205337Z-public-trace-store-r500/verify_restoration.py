#!/usr/bin/env python3
import json
import sys
from pathlib import Path


root = Path(sys.argv[1])
inventory = root / "inventory"


def text(name: str) -> str:
    return (inventory / name).read_text(encoding="utf-8")


def value(name: str):
    return json.loads(text(name))


service_before = text("20260823T205337Z.target-service.before.txt")
service_after = text("20260823T205337Z.target-service.after.txt")
subsystems_before = value("20260823T205337Z.target-subsystems.before.json")
subsystems_after = value("20260823T205337Z.target-subsystems.after.json")
bdevs_before = value("20260823T205337Z.target-bdevs.before.json")
bdevs_after = value("20260823T205337Z.target-bdevs.after.json")
client_before = text("client-nvme-subsystems.before.txt")
client_after = text("client-nvme-subsystems.after.txt")

subsystem_nqns = sorted(item["nqn"] for item in subsystems_after)
bdev_names = sorted(item["name"] for item in bdevs_after)
master_transitions = text("master-transitions.txt").splitlines()
result = {
    "status": "pass",
    "target_service_exact_match": service_before == service_after,
    "target_subsystems_exact_match": subsystems_before == subsystems_after,
    "target_bdevs_exact_match": bdevs_before == bdevs_after,
    "client_subsystems_exact_match": client_before == client_after,
    "target_service_main_pid": 2072748,
    "target_subsystem_nqns": subsystem_nqns,
    "target_bdev_names": bdev_names,
    "client_master_initial_policy": text("initial-master-policy.txt").strip(),
    "client_master_final_transition": master_transitions[-1],
    "errors": [],
}
expected_nqns = [
    "nqn.2014-08.org.nvmexpress.discovery",
    "nqn.2026-08.local.mooncake:nof-phase1",
]
gates = [
    result["target_service_exact_match"],
    result["target_subsystems_exact_match"],
    result["target_bdevs_exact_match"],
    result["client_subsystems_exact_match"],
    "MainPID=2072748" in service_after,
    "ActiveState=active" in service_after,
    "SubState=running" in service_after,
    subsystem_nqns == expected_nqns,
    bdev_names == ["Nvme0n1"],
    result["client_master_initial_policy"] == "round_robin",
    master_transitions[-1].endswith(" root round_robin"),
]
if not all(gates):
    result["status"] = "fail"
    result["errors"].append("one or more restoration gates failed")
(root / "hardware-restoration.json").write_text(
    json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result["status"] == "pass" else 1)
