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

Client-restart acceptance is split into two commands. The seed command writes
objects and records their keys, hashes, and expected descriptors. Restart the
Store API client process between the commands, then run verification from a
fresh process. Verification resolves every object from Master metadata, checks
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

## KV-cache workload trace replay

The independent workload path models `produce`, `reuse`, `evict`, and `miss`
events without changing the transparent overhead baseline. Generation is fully
deterministic for a fixed seed and writes the trace manifest alongside the
trace. A run directory contains `trace.jsonl`, `manifest.json`, one
`raw-<case>.json` per replay, and offline `operations.csv`, `summary.csv`, and
`conclusion.json` artifacts:

```bash
KV_WORKLOAD_RUN_ID=smoke KV_WORKLOAD_RESULT_DIR=results/kv-workload/smoke \
  ./run.sh kv-workload-generate
KV_WORKLOAD_MODE=no_store KV_WORKLOAD_RESULT_DIR=results/kv-workload/smoke \
  ./run.sh kv-workload-replay
KV_WORKLOAD_MODE=direct KV_WORKLOAD_TARGET=remote_nof \
KV_WORKLOAD_CASE_ID=direct-remote KV_WORKLOAD_RESULT_DIR=results/kv-workload/smoke \
  ./run.sh kv-workload-replay
KV_WORKLOAD_RESULT_DIR=results/kv-workload/smoke \
  KV_WORKLOAD_REQUIRED_CASES=no_store,direct-remote ./run.sh kv-workload-summarize
```

`direct` and `transparent` invoke the configured Store environment; `no_store`
uses only the fixed recomputation proxy recorded in the raw result. The
summarizer is offline and returns `status=inconclusive` for failed or missing
cases, duplicate case IDs, mixed run IDs, or mixed trace digests. Descriptor
source counts are kept separate for `local_nvme` and `remote_nof`; they are not
inferred from policy. This workflow reports synthetic/request-level proxy
metrics only and does not claim model execution, HA behavior, or cluster-scale
results.

FAST'25 public traces can be converted into the same replay schema before
running the existing Store workflow. The converter reads each request's
`hash_ids`, treats a resident page as `reuse`, an absent page as `produce`, and
uses deterministic LRU eviction at the configured page capacity. It appends a
final cleanup eviction for every resident page and records both source and
converted-trace SHA-256 digests in the manifest:

```bash
python3 public_trace_workload.py /path/to/conversation.jsonl \
  results/public-trace/conversation \
  --requests 100 --capacity-pages 64 --block-size 131072 \
  --preserve-arrivals --run-id conversation-100

KV_WORKLOAD_MODE=no_store \
KV_WORKLOAD_REPLAY_SCALE=10 \
KV_WORKLOAD_RESULT_DIR=results/public-trace/conversation \
  ./run.sh kv-workload-replay
KV_WORKLOAD_MODE=direct KV_WORKLOAD_TARGET=remote_nof \
KV_WORKLOAD_CASE_ID=direct-remote \
KV_WORKLOAD_REPLAY_SCALE=10 \
KV_WORKLOAD_RESULT_DIR=results/public-trace/conversation \
  ./run.sh kv-workload-replay
```

The adapter requires exactly the requested number of valid input rows and is
sequential. By default, it assigns synthetic one-microsecond event timestamps,
which preserves deterministic event order and the digests used by existing
experiments. `--preserve-arrivals` instead converts the FAST'25 millisecond
timestamps to microseconds while retaining same-request arrival batches.
Setting `KV_WORKLOAD_REPLAY_SCALE` to a positive fast-forward multiplier makes
replay wait for those offsets (`10` replays a 100-second source span in 10
seconds); `0`, the default, disables pacing. Raw and summarized results record
the scheduled span, processing wall time, completion lag, and request arrival
lag.

Arrival pacing does not introduce workers or overlap Store operations: every
event still completes before the next event starts, and late events run
immediately while their lag is recorded. Results therefore describe a
sequential, arrival-paced replay and must not be presented as concurrent-load
or throughput-saturation evidence. The fixed block size is a page-size model,
not a reconstruction of the trace's original byte volume. Direct and
transparent placement are selected during replay; the converted trace keeps
`policy=round_robin` so one digest can be used for matched local and remote
cases. If a page is evicted and later produced again, replay assigns a new
generation-suffixed Store key so delayed backend cleanup cannot collide with
the prior page lifetime.
