#!/usr/bin/env python3
"""Summarize and plot local NVMe versus Mooncake NoF characterization runs."""

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path


UNITS = {"B/s": 1, "KiB/s": 1024, "MiB/s": 1024**2, "GiB/s": 1024**3}
TIME_UNITS = {"ns": 1e-6, "us": 1e-3, "ms": 1, "s": 1000}
CASE_RE = re.compile(r"(local|remote)-size(\d+)-load(\d+)-run(\d+)")


def value_with_unit(value: str, units: dict[str, float]) -> float:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z/]+)\s*", value)
    if not match or match.group(2) not in units:
        raise ValueError(f"unsupported value: {value}")
    return float(match.group(1)) * units[match.group(2)]


def percentile_ms(io: dict, percentile: str) -> float:
    for key, scale in (("clat_ns", 1e-6), ("clat_us", 1e-3), ("clat_ms", 1)):
        values = io.get(key, {}).get("percentile", {})
        if percentile in values:
            return float(values[percentile]) * scale
    return 0.0


def parse_fio(path: Path) -> dict[str, float]:
    job = json.loads(path.read_text())["jobs"][0]
    io = job["read"] if job["read"].get("io_bytes", 0) else job["write"]
    return {
        "bandwidth_Bps": float(io.get("bw_bytes", 0)),
        "iops": float(io.get("iops", 0)),
        "p50_ms": percentile_ms(io, "50.000000"),
        "p95_ms": percentile_ms(io, "95.000000"),
        "p99_ms": percentile_ms(io, "99.000000"),
        "cpu_pct": float(job.get("usr_cpu", 0)) + float(job.get("sys_cpu", 0)),
        "errors": float(job.get("error", 0)),
    }


def parse_time_cpu(path: Path) -> float:
    if not path.exists():
        return 0.0
    match = re.search(r"Percent of CPU this job got:\s*([0-9.]+)%", path.read_text())
    return float(match.group(1)) if match else 0.0


def parse_nof(path: Path, time_path: Path) -> dict[str, float]:
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.startswith("endpoint["):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    required = ("bw", "iops", "clat_p50", "clat_p95", "clat_p99", "failed_ops")
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"{path}: missing fields: {', '.join(missing)}")
    return {
        "bandwidth_Bps": value_with_unit(values["bw"], UNITS),
        "iops": float(values["iops"]),
        "p50_ms": value_with_unit(values["clat_p50"], TIME_UNITS),
        "p95_ms": value_with_unit(values["clat_p95"], TIME_UNITS),
        "p99_ms": value_with_unit(values["clat_p99"], TIME_UNITS),
        "cpu_pct": parse_time_cpu(time_path),
        "errors": float(values["failed_ops"]),
    }


def add_system_telemetry(metrics: dict[str, float], path: Path) -> None:
    defaults = {
        "system_cpu_pct": 0.0,
        "nic_rx_Bps": 0.0,
        "nic_tx_Bps": 0.0,
        "client_block_util_pct": 0.0,
    }
    if path.exists():
        data = json.loads(path.read_text())
        defaults.update({key: float(data.get(key, 0)) for key in defaults})
    metrics.update(defaults)


def add_spdk_telemetry(
    metrics: dict[str, float], before_path: Path, after_path: Path
) -> None:
    defaults = {
        "remote_device_Bps": 0.0,
        "remote_device_weighted_busy_pct": 0.0,
    }
    if not before_path.exists() or not after_path.exists():
        metrics.update(defaults)
        return
    try:
        before = json.loads(before_path.read_text())
        after = json.loads(after_path.read_text())
        before_bdevs = {bdev["name"]: bdev for bdev in before.get("bdevs", [])}
        elapsed = max(metrics.get("telemetry_elapsed_sec", 0), 1e-9)
        byte_delta = 0
        latency_ticks = 0
        for bdev in after.get("bdevs", []):
            old = before_bdevs.get(bdev["name"], {})
            for key in ("bytes_read", "bytes_written"):
                byte_delta += max(0, int(bdev.get(key, 0)) - int(old.get(key, 0)))
            for key in ("read_latency_ticks", "write_latency_ticks"):
                latency_ticks += max(0, int(bdev.get(key, 0)) - int(old.get(key, 0)))
        tick_rate = float(after.get("tick_rate", 0))
        defaults["remote_device_Bps"] = byte_delta / elapsed
        if tick_rate:
            defaults["remote_device_weighted_busy_pct"] = min(
                100, latency_ticks / tick_rate / elapsed * 100
            )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    metrics.update(defaults)


def median_metrics(items: list[dict[str, float]]) -> dict[str, float]:
    metrics = {key: statistics.median(item[key] for item in items) for key in items[0]}
    metrics["errors"] = sum(item["errors"] for item in items)
    return metrics


def expected_cases(result_dir: Path) -> set[tuple[str, int, int]]:
    manifest_path = result_dir / "matrix.json"
    if not manifest_path.exists():
        return set()
    manifest = json.loads(manifest_path.read_text())
    sizes = [int(value) for value in manifest["sizes"]]
    cases = set()
    for backend, key in (("local", "local_loads"), ("remote", "remote_loads")):
        for size in sizes:
            for load in manifest[key]:
                cases.add((backend, size, int(load)))
    return cases


def collect(result_dir: Path) -> tuple[list[dict], list[dict]]:
    grouped: dict[tuple[str, int, int], list[dict[str, float]]] = {}
    raw_rows = []
    paths = list((result_dir / "raw" / "local").glob("*.json"))
    paths += list((result_dir / "raw" / "remote").glob("*.log"))
    for path in sorted(paths):
        match = CASE_RE.fullmatch(path.stem)
        if not match:
            continue
        backend, size, load, run = match.groups()
        exit_path = path.with_suffix(".exitcode")
        exit_code = int(exit_path.read_text().strip()) if exit_path.exists() else 0
        try:
            metrics = (
                parse_fio(path)
                if backend == "local"
                else parse_nof(path, result_dir / "telemetry" / f"{path.stem}.time")
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            metrics = {
                key: 0.0
                for key in (
                    "bandwidth_Bps",
                    "iops",
                    "p50_ms",
                    "p95_ms",
                    "p99_ms",
                    "cpu_pct",
                )
            }
            metrics["errors"] = 1.0
        if exit_code:
            metrics["errors"] = max(metrics["errors"], 1.0)
        system_path = result_dir / "telemetry" / f"{path.stem}.system.json"
        add_system_telemetry(metrics, system_path)
        if system_path.exists():
            metrics["telemetry_elapsed_sec"] = float(
                json.loads(system_path.read_text()).get("elapsed_sec", 0)
            )
        else:
            metrics["telemetry_elapsed_sec"] = 0.0
        add_spdk_telemetry(
            metrics,
            result_dir / "telemetry" / f"{path.stem}.spdk-before.json",
            result_dir / "telemetry" / f"{path.stem}.spdk-after.json",
        )
        row = {
            "backend": backend,
            "size_bytes": int(size),
            "load_pct": int(load),
            "run": int(run),
            "exit_code": exit_code,
            **metrics,
        }
        raw_rows.append(row)
        grouped.setdefault((backend, int(size), int(load)), []).append(metrics)

    summary = []
    for (backend, size, load), items in sorted(grouped.items()):
        summary.append(
            {
                "backend": backend,
                "size_bytes": size,
                "load_pct": load,
                "runs": len(items),
                **median_metrics(items),
            }
        )
    return raw_rows, summary


def crossover_rows(summary: list[dict]) -> list[dict]:
    index = {
        (row["backend"], row["size_bytes"], row["load_pct"]): row for row in summary
    }
    rows = []
    sizes = sorted({row["size_bytes"] for row in summary})
    local_loads = sorted(
        {row["load_pct"] for row in summary if row["backend"] == "local"}
    )
    remote_loads = sorted(
        {row["load_pct"] for row in summary if row["backend"] == "remote"}
    )
    for scenario, loads in (("local_load", local_loads), ("remote_load", remote_loads)):
        for size in sizes:
            for load in loads:
                local = index.get(
                    ("local", size, load if scenario == "local_load" else 0)
                )
                remote = index.get(
                    ("remote", size, load if scenario == "remote_load" else 0)
                )
                if not local or not remote:
                    continue
                valid = local["errors"] == 0 and remote["errors"] == 0
                winner = "invalid"
                if valid:
                    winner = "remote" if remote["p95_ms"] < local["p95_ms"] else "local"
                rows.append(
                    {
                        "scenario": scenario,
                        "size_bytes": size,
                        "load_pct": load,
                        "local_p95_ms": local["p95_ms"],
                        "remote_p95_ms": remote["p95_ms"],
                        "remote_over_local_p95": (
                            remote["p95_ms"] / local["p95_ms"]
                            if local["p95_ms"]
                            else math.inf
                        ),
                        "winner": winner,
                    }
                )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        if not rows:
            return
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(result_dir: Path) -> None:
    raw, summary = collect(result_dir)
    crossover = crossover_rows(summary)
    write_csv(result_dir / "runs.csv", raw)
    write_csv(result_dir / "summary.csv", summary)
    write_csv(result_dir / "crossover.csv", crossover)
    go_regions = [
        row
        for row in crossover
        if row["scenario"] == "local_load"
        and row["load_pct"] > 0
        and row["winner"] == "remote"
    ]
    expected = expected_cases(result_dir)
    observed = {(row["backend"], row["size_bytes"], row["load_pct"]) for row in summary}
    complete_matrix = (
        bool(expected)
        and expected == observed
        and all(row["runs"] >= 3 and row["errors"] == 0 for row in summary)
    )
    decision = (
        "inconclusive" if not complete_matrix else ("go" if go_regions else "no-go")
    )
    conclusion = {
        "decision": decision,
        "criterion": "remote p95 is lower than local p95 under nonzero local load",
        "go_region_count": len(go_regions),
        "complete_matrix": complete_matrix,
        "aggregation": "not-tested-single-remote-only",
    }
    (result_dir / "conclusion.json").write_text(json.dumps(conclusion, indent=2) + "\n")
    print(json.dumps(conclusion, indent=2))


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def svg_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plot(summary_path: Path, output_dir: Path) -> None:
    rows = load_csv(summary_path)
    base = [row for row in rows if int(row["load_pct"]) == 0]
    width, height, margin = 900, 520, 70
    sizes = sorted({int(row["size_bytes"]) for row in base})
    max_bw = max((float(row["bandwidth_Bps"]) for row in base), default=1)
    colors = {"local": "#2166ac", "remote": "#b2182b"}
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18">Local vs Remote bandwidth (load 0%)</text>',
    ]
    elements += [
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#333"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#333"/>',
    ]
    for backend in ("local", "remote"):
        points = []
        lookup = {
            int(row["size_bytes"]): float(row["bandwidth_Bps"])
            for row in base
            if row["backend"] == backend
        }
        for idx, size in enumerate(sizes):
            if size not in lookup:
                continue
            x = margin + idx * (width - 2 * margin) / max(1, len(sizes) - 1)
            y = height - margin - lookup[size] / max_bw * (height - 2 * margin)
            points.append(f"{x:.1f},{y:.1f}")
        elements.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[backend]}" stroke-width="3"/>'
        )
        elements.append(
            f'<text x="{width-margin-90}" y="{48 if backend == "local" else 68}" fill="{colors[backend]}">{backend}</text>'
        )
    for idx, size in enumerate(sizes):
        x = margin + idx * (width - 2 * margin) / max(1, len(sizes) - 1)
        label = f"{size // 1024}K" if size < 1024**2 else f"{size // 1024**2}M"
        elements.append(
            f'<text x="{x:.1f}" y="{height-margin+22}" text-anchor="middle" font-size="10">{svg_escape(label)}</text>'
        )
    elements.append("</svg>")
    (output_dir / "size-bandwidth.svg").write_text("\n".join(elements) + "\n")

    crossover = load_csv(output_dir / "crossover.csv")
    local_rows = [row for row in crossover if row["scenario"] == "local_load"]
    loads = sorted({int(row["load_pct"]) for row in local_rows})
    cell_w = 80
    heat_width = 150 + cell_w * len(sizes)
    heat_height = 100 + 45 * len(loads)
    heat = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{heat_width}" height="{heat_height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="20" y="25" font-size="18">Crossover: local load vs idle remote (p95)</text>',
    ]
    lookup = {
        (int(r["size_bytes"]), int(r["load_pct"])): r["winner"] for r in local_rows
    }
    for col, size in enumerate(sizes):
        x = 120 + col * cell_w
        label = f"{size // 1024}K" if size < 1024**2 else f"{size // 1024**2}M"
        heat.append(
            f'<text x="{x+cell_w/2}" y="52" text-anchor="middle" font-size="10">{label}</text>'
        )
    fills = {"remote": "#67a9cf", "local": "#ef8a62", "invalid": "#cccccc"}
    for row_index, load in enumerate(loads):
        y = 62 + row_index * 45
        heat.append(f'<text x="105" y="{y+27}" text-anchor="end">{load}%</text>')
        for col, size in enumerate(sizes):
            winner = lookup.get((size, load), "invalid")
            x = 120 + col * cell_w
            heat.append(
                f'<rect x="{x}" y="{y}" width="{cell_w-2}" height="40" fill="{fills[winner]}"/>'
            )
            heat.append(
                f'<text x="{x+(cell_w-2)/2}" y="{y+25}" text-anchor="middle" font-size="11">{winner}</text>'
            )
    heat.append("</svg>")
    (output_dir / "crossover.svg").write_text("\n".join(heat) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary_parser = subparsers.add_parser("summarize")
    summary_parser.add_argument("result_dir", type=Path)
    plot_parser = subparsers.add_parser("plot")
    plot_parser.add_argument("summary_csv", type=Path)
    plot_parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    if args.command == "summarize":
        summarize(args.result_dir)
    else:
        plot(args.summary_csv, args.output_dir)


if __name__ == "__main__":
    main()
