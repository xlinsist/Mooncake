# Mooncake NVMe-oF experiments

Start here. This directory is organized into three parts:

- `docs/`: experiment instructions, AI/maintainer context, and reviewed
  conclusions that should be committed to Git;
- `results/`: raw outputs, telemetry, plots, and other intermediate results;
  this directory is intentionally ignored by Git;
- the remaining files: experiment scripts and configuration templates. Normal
  users do not need to inspect them directly.

For the numbered development trajectory, start with
[`docs/README.md`](docs/README.md).

To run or maintain the experiment, read
[`docs/06-runbook.md`](docs/06-runbook.md). For the current result, read
[`docs/01-local-remote-decision-boundary.md`](docs/01-local-remote-decision-boundary.md).
For the completed transparent-layer testbed recovery and paired-performance
execution record, read
[`docs/03-transparent-layer-testbed-unblock-plan.md`](docs/03-transparent-layer-testbed-unblock-plan.md).
For the staged Python 3.12 binding deployment and policy operations, read
[`docs/05-transparent-layer-deployment.md`](docs/05-transparent-layer-deployment.md).

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

## Transparent heterogeneous storage acceptance

Start the Master with one policy at a time, keep the local file backend and
NoF target registered, then run the matching command. The workload uses the
ordinary `store.put(key, value)` API without a `ReplicateConfig`, verifies the
published replica descriptor, reads from a second process, and removes every
object.

```bash
MC_HETERO_STORAGE_POLICY=local_only ./run.sh transparent-local
MC_HETERO_STORAGE_POLICY=remote_only ./run.sh transparent-remote
MC_HETERO_STORAGE_POLICY=round_robin ./run.sh transparent-round-robin
```

The policy variable must be present in the `mooncake_master` service
environment, not only in the shell running `run.sh`. Results are written to
`RESULT_DIR/transparent-*.json`.

Strict-policy failure acceptance must be run with the selected target absent.
Start the Master with `local_only` but do not enable an SSD backend for the
first command; start it with `remote_only` after unregistering every NoF
segment for the second. Each command requires the managed put to fail, verifies
that no COMPLETE descriptor was published, and confirms the key is unreadable:

```bash
MC_HETERO_STORAGE_POLICY=local_only ./run.sh transparent-local-unavailable
MC_HETERO_STORAGE_POLICY=remote_only ./run.sh transparent-remote-unavailable
```

Restart acceptance is deliberately split into two commands. The seed command
writes objects and records their keys, hashes, expected descriptors, and the
named restart scenario. Non-client scenarios also require an opaque incarnation
witness, such as a systemd `InvocationID`, process start time, HA view ID, or
another deployment-specific identity. Capture it before and after the restart;
the verifier rejects an unchanged identity. Client restart uses the Python PID
automatically. Perform the stated restart between the commands, then run verify
from a fresh process. Verify resolves every object from Master metadata, checks
its descriptor and contents, and removes it:

Choose one unique batch ID and keep it unchanged for every lifecycle,
unavailable-target, restart, benchmark, and acceptance command written to the
same result directory:

```bash
export TRANSPARENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-transparent"
```

```bash
TRANSPARENT_RESTART_SCENARIO=client_restart \
TRANSPARENT_RESTART_TARGETS=local_nvme,remote_nof \
  ./run.sh transparent-restart-seed
# Restart the Store API client process only.
TRANSPARENT_RESTART_SCENARIO=client_restart ./run.sh transparent-restart-verify

TRANSPARENT_RESTART_SCENARIO=master_ha_restart \
TRANSPARENT_RESTART_TARGETS=local_nvme,remote_nof \
TRANSPARENT_RESTART_WITNESS=master-before-incarnation \
  ./run.sh transparent-restart-seed
# Fail over or restart the HA Master, preserving snapshot/oplog state.
TRANSPARENT_RESTART_SCENARIO=master_ha_restart \
TRANSPARENT_RESTART_WITNESS=master-after-incarnation \
  ./run.sh transparent-restart-verify

TRANSPARENT_RESTART_SCENARIO=local_owner_restart \
TRANSPARENT_RESTART_TARGETS=local_nvme \
TRANSPARENT_RESTART_WITNESS=owner-before-incarnation \
  ./run.sh transparent-restart-seed
# Restart the local backend owner and wait for backend rebind.
TRANSPARENT_RESTART_SCENARIO=local_owner_restart \
TRANSPARENT_RESTART_WITNESS=owner-after-incarnation \
  ./run.sh transparent-restart-verify

TRANSPARENT_RESTART_SCENARIO=nof_service_restart \
TRANSPARENT_RESTART_TARGETS=remote_nof \
TRANSPARENT_RESTART_WITNESS=nof-before-incarnation \
  ./run.sh transparent-restart-seed
# Restart the NoF service and wait for segment remount.
TRANSPARENT_RESTART_SCENARIO=nof_service_restart \
TRANSPARENT_RESTART_WITNESS=nof-after-incarnation \
  ./run.sh transparent-restart-verify
```

For the phase-four transparent-layer increment, run the same Store API workload
as a paired command. It first uses an explicit manual target (`direct`), then
repeats without a config (`transparent`) in the same process and emits the
absolute and percentage deltas. Keep the Master policy, object size, CPU
affinity, and persistence settings identical:

```bash
TRANSPARENT_BENCH_TARGET=local_nvme ./run.sh transparent-overhead
TRANSPARENT_BENCH_TARGET=remote_nof ./run.sh transparent-overhead
```

After all lifecycle, unavailable-target, restart, and paired-overhead commands
have written into the same `RESULT_DIR`, generate the software-verification
artifact and run the strict evidence gate:

```bash
./run.sh transparent-software-verification
./run.sh transparent-acceptance
```

The software command runs the relevant build, CTest, Python tests, and
PR-scoped pre-commit hooks; a missing tool or failed command produces a failed
artifact. The acceptance command writes `transparent-acceptance.json` and
fails unless all twelve required
artifacts report `status=pass` with the same `TRANSPARENT_RUN_ID`, the expected
policy, restart scenario, changed restart witness, verified descriptor, and
paired direct/transparent metrics. This gate does not synthesize missing
hardware evidence or combine stale artifacts from different runs.

The gate validates recorded evidence; it is not a cryptographic hardware
attestation mechanism. Capture non-client restart witnesses from a trusted
deployment source (for example systemd `InvocationID`, process start time, or
the HA view ID), retain the corresponding service logs with the result
directory, and do not use operator-chosen placeholder strings for acceptance.

Local NVMe acceptance and benchmark commands require
`SSD_OFFLOAD_PATH=/path/to/local/nvme`; the runner enables SSD offload
automatically for `transparent-local`, round-robin, and local benchmark modes.

For unprivileged TCP loopback validation where the hugetlbfs mount is not
writable, set `MC_SPDK_ENV_CONTEXT="--no-huge --legacy-mem -m 1024"` for the
Store client. The default remains the production hugepage-backed SPDK setup.

Each JSON result contains direct and transparent `put`, `get`, and `remove`
samples plus p50, p95, p99, operation rate, and (for `put`/`get`) bandwidth.
Its `overhead` section reports absolute and percentage deltas, together with
process CPU utilization. The `direct` local run still uses the Store lifecycle
and explicit `ReplicateConfig`; it is not a fio/POSIX baseline. Existing
`nof-benchmark` and same-SSD commands remain the device/path characterization
controls. The Master's `placement_decision_latency_us` metric separately
isolates policy-decision software time.
