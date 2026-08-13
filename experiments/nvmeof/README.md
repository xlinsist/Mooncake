# Mooncake NVMe-oF experiments

Start here. This directory is organized into three parts:

- `docs/`: experiment instructions, AI/maintainer context, and reviewed
  conclusions that should be committed to Git;
- `results/`: raw outputs, telemetry, plots, and other intermediate results;
  this directory is intentionally ignored by Git;
- the remaining files: experiment scripts and configuration templates. Normal
  users do not need to inspect them directly.

To run or maintain the experiment, read
[`docs/runbook.md`](docs/runbook.md). For the current result, read
[`docs/local-remote-decision-boundary.md`](docs/local-remote-decision-boundary.md).

Create the machine-local configuration with:

```bash
cp config.env.example config.env
```

`config.env` is also ignored by Git.

## Same-SSD local versus remote characterization

The maintenance-window workflow compares Mooncake NoF from the client with
SPDK `bdevperf` on the target against the same SSD. It is read-only and uses
the configured PCI BDF rather than a kernel NVMe device name.

```bash
./run.sh same-ssd-preflight
./run.sh same-ssd-characterize
SAME_SSD_RESULT_DIR=results/same-ssd-YYYYMMDDTHHMMSSZ ./run.sh same-ssd-summarize
```

The characterization runs remote-before, stops `mooncake-nof-spdk.service`
once, runs target-local `bdevperf`, restores and probes the target, and then
runs remote-after. Results include raw logs, environment and SMART snapshots,
`runs.csv`, `summary.csv`, `same-ssd-overhead.csv`, and `conclusion.json`.
Set the optional `SAME_SSD_CLIENT_SSH`, `SAME_SSD_CLIENT_ROOT`, and
`SAME_SSD_CLIENT_BUILD_DIR` values when `run.sh` coordinates a benchmark binary
on a separate client host.

Remote drift above 10%, any failed/missing repeat, failed service recovery, or
new SMART media/critical errors makes the affected result inconclusive. The
64 MiB capability probe is recorded separately and is not an acceptance gate.
