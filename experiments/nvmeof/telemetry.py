#!/usr/bin/env python3
"""Run a command while sampling host CPU, NIC, and block-device utilization."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def cpu_ticks() -> tuple[int, int]:
    fields = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields), idle


def nic_bytes(interface: str) -> tuple[int, int]:
    for line in Path("/proc/net/dev").read_text().splitlines():
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        if name.strip() == interface:
            fields = values.split()
            return int(fields[0]), int(fields[8])
    raise ValueError(f"network interface not found: {interface}")


def block_busy_ms(device: str) -> int:
    name = Path(device).name
    for line in Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        if len(fields) >= 13 and fields[2] == name:
            return int(fields[12])
    raise ValueError(f"block device not found: {device}")


def snapshot(interface: str, device: str) -> dict[str, int | float]:
    total, idle = cpu_ticks()
    rx, tx = nic_bytes(interface)
    return {
        "time": time.monotonic(),
        "cpu_total": total,
        "cpu_idle": idle,
        "nic_rx": rx,
        "nic_tx": tx,
        "block_busy_ms": block_busy_ms(device),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    before = snapshot(args.interface, args.device)
    process = subprocess.Popen(command, start_new_session=True)

    def forward(signum, _frame):
        if process.poll() is None:
            os.killpg(process.pid, signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    return_code = process.wait()
    after = snapshot(args.interface, args.device)
    elapsed = max(float(after["time"]) - float(before["time"]), 1e-9)
    cpu_delta = int(after["cpu_total"]) - int(before["cpu_total"])
    idle_delta = int(after["cpu_idle"]) - int(before["cpu_idle"])
    metrics = {
        "elapsed_sec": elapsed,
        "system_cpu_pct": 100 * (cpu_delta - idle_delta) / cpu_delta if cpu_delta else 0,
        "nic_rx_Bps": (int(after["nic_rx"]) - int(before["nic_rx"])) / elapsed,
        "nic_tx_Bps": (int(after["nic_tx"]) - int(before["nic_tx"])) / elapsed,
        "client_block_util_pct": min(100, (int(after["block_busy_ms"]) - int(before["block_busy_ms"])) / (elapsed * 10)),
        "exit_code": return_code,
    }
    args.output.write_text(json.dumps(metrics, indent=2) + "\n")
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
