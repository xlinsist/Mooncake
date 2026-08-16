# NVMe-oF experiment runbook

This directory turns the two-host validation plan into a reproducible test
harness. Run it from `intel-bigmem-2`; it uses SSH for read-only inventory and
target lifecycle operations on `intel-bigmem`.

The target start and baseline commands write to the first 64 GiB of the SSD.
They refuse to run unless `DESTRUCTIVE_CONFIRM` exactly matches
`ERASE-<TARGET_NVME_SERIAL>` from `config.env`.

For an existing SPDK deployment, set `TARGET_PRECONFIGURED=1` and
`SPDK_SERVICE` to its systemd unit. In that mode target start/stop controls the
existing unit, while cleanup deliberately retains its binding and service.

## 1. Configure

```bash
cd experiments/nvmeof
cp config.env.example config.env
nvme id-ctrl /dev/nvme3n1 | grep '^sn'
ssh intel-bigmem 'nvme id-ctrl /dev/nvme0n1 | grep "^sn"'
```

Fill in `CLIENT_RDMA_DEVICE`, `TARGET_NVME_BDF`, `TARGET_NVME_SERIAL`,
`CLIENT_NVME_SERIAL`, `SPDK_DIR`, and the Mooncake binary paths. The physical
target serial and the exported controller serial may differ when SPDK supplies
its own subsystem serial; both are checked independently.

### Source and build ownership

Keep one authoritative Mooncake source tree. Develop and review changes in the
primary repository, then synchronize the exact Git commit to `intel-bigmem-2`
with the normal push/pull workflow. Rebuild the client with `-DUSE_NOF=ON`
after every relevant source change. The following artifacts used by a test run
must all come from that same commit:

- `mooncake_master`;
- `nof_worker_pool_bench`;
- the Mooncake Python Store binding.

`intel-bigmem` only hosts the SPDK NVMe-oF target and normally does not need a
Mooncake source checkout. Avoid maintaining independent Mooncake edits on both
hosts or manually copying a stale Python extension between builds; either can
produce results where the master, benchmark, and client binding test different
code.

The Python binding does not have to be installed from PyPI. It may be installed
from the locally built wheel, or loaded from the `-DUSE_NOF=ON` build tree via
`PYTHONPATH`. Before testing, verify that the imported extension and binaries
resolve from the configured `MOONCAKE_ROOT` and `BUILD_DIR`, and record the Git
commit in the result inventory. If `from mooncake.store import ...` fails while
`build-nof/mooncake-integration/store*.so` exists, the build is present but the
Python package/install path is incomplete; fix the package path or install the
matching wheel instead of using an unrelated system-wide Mooncake package.

The current NoF build initializes SPDK with physical-address IOVA. On hosts
where `/proc/self/pagemap` physical addresses are restricted, the master,
Python Store workloads, registration commands, and `nof_worker_pool_bench`
must run through passwordless `sudo -n`. The harness does this explicitly;
preflight must fail rather than prompt if non-interactive sudo is unavailable.

## 2. Inventory and preflight

```bash
./run.sh inventory
./run.sh preflight
```

Review `results/<timestamp>/inventory/` and the preflight output. Stop if the
SMART data contains a critical warning, media errors, or unsafe temperature.
The automated gate rejects mounted filesystems, swap, LVM/RAID holders, open
processes, a serial mismatch, or an already VFIO-bound target.

Run RDMA microbenchmarks separately on a second management shell. Do not bring
the IPoIB interface down because it may carry SSH traffic:

```bash
# target
ib_write_bw -d <rdma-device> --report_gbits
# client
ib_write_bw 10.0.0.5 -d <rdma-device> --report_gbits
```

Save both outputs below the active result directory.

## 3. Kernel NVMe-oF baseline

Set the destructive token, then run all fio cases three times:

```bash
export DESTRUCTIVE_CONFIRM="ERASE-$TARGET_NVME_SERIAL"
./run.sh baseline
```

The harness uses direct I/O, a 10-second ramp, a 60-second measurement, a
64-GiB range, JSON+ output, and queue depths 1/8/32/64. Afterward, disconnect
the kernel initiator before SPDK takes ownership:

```bash
./run.sh disconnect-kernel
```

## 4. Start and register the SPDK target

```bash
./run.sh target-start
./run.sh target-status
./run.sh register
./run.sh register                 # idempotency check
```

Start Mooncake services before registration. Example commands are printed by
`./run.sh service-commands`; execute them in persistent shells and preserve
their logs in the active result directory. The registration is deliberately
limited to 64 GiB even if the namespace is larger.

Never run `nvme connect` while the SPDK/Mooncake phase is active.

## 5. Correctness and performance

```bash
./run.sh correctness
STABILITY_SECONDS=1800 ./run.sh stability
./run.sh nof-benchmark
./run.sh summarize
```

Correctness covers NoF-only and memory+NoF placement, deterministic SHA-256
verification, 4 KiB/128 KiB/1 MiB/8 MiB objects, 100 objects per size,
repeated and cross-process reads, duplicate keys, deletion, and an explicit
unaligned-payload outcome. `STABILITY_SECONDS` defaults to 60 for smoke runs;
use 1800 for acceptance.

The NoF benchmark uses the same workload matrix as fio and repeats every case
three times. It additionally sweeps workers 1/2/4 and inflight limits
8/32/128 MiB at the configured best queue depth.

### Transparent-layer paired overhead

To measure the transparent layer rather than compare unrelated storage paths,
run the paired Store API benchmark once for each selected target. Each command
first writes through an explicit `ReplicateConfig` control and then repeats the
same workload through ordinary `store.put(key, value)` with the matching Master
policy. It records `put`, `get`, and `remove` samples, latency percentiles,
operation rate, payload bandwidth where applicable, and process CPU use.

```bash
export TRANSPARENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-transparent"
# Set local_only in the Master service environment before this command.
TRANSPARENT_BENCH_TARGET=local_nvme ./run.sh transparent-overhead
# Set remote_only in the Master service environment before this command.
TRANSPARENT_BENCH_TARGET=remote_nof ./run.sh transparent-overhead
```

Do not combine the local and remote deltas: they characterize different
backends. A failed descriptor check, failed deletion, absent metric, or mixed
run ID invalidates that result instead of indicating a performance regression.

The verified Python 3.12 run is recorded under
`/sharenvme/userhome/zhouxulin/mooncake-transparent-layer-py312-20260816T000000Z`.
Its `put` p50 transparent-minus-direct overhead was `+4.0716%` for local NVMe
and `+14.0316%` for remote NoF. A separate lifecycle batch passed local-only,
remote-only, unavailable-target, client-restart, and round-robin checks. See
[`transparent-layer-deployment.md`](transparent-layer-deployment.md) for the
deployment and rollback procedure. The Master was restored to
`MC_HETERO_STORAGE_POLICY=local_only` after the run.

## 6. Failure injection

Keep a correctness or mixed-I/O workload running, and use a management shell:

```bash
./run.sh target-stop
./run.sh target-start
./run.sh register
```

Test in this order: memory+NoF, NoF-only, target recovery, master restart, and
target stop during 128-KiB mixed I/O. Record the elapsed heartbeat removal time
and verify requests terminate without incorrect data, deadlock, or a process
crash. The harness stops the listener process instead of modifying the IPoIB
link.

`nof_worker_pool_bench` now has an I/O completion deadline. Set
`BENCH_IO_TIMEOUT` in `config.env`; a stuck run emits `benchmark_timeout=1` and
`outstanding_ops=<count>`, then exits with status 124. `run.sh` also wraps the
process in `timeout --kill-after` so a target failure cannot strand an
unattended characterization run. Status 124 is a failed sample, never a
performance result.

## 7. Local / remote characterization

Configure `LOCAL_NVME_DEVICE`, its physical serial, and
`CLIENT_NET_INTERFACE` (the Linux network interface, not the verbs device).
The local device serial, swap, and holder checks are mandatory. A mounted
device is allowed because the characterization matrix issues only direct
reads, but the harness warns that unrelated filesystem traffic can add noise.
Then run:

```bash
./run.sh characterize
```

The default matrix covers 4 KiB, 64 KiB, 256 KiB, 1 MiB, 4 MiB, 16 MiB,
64 MiB, 256 MiB, and 1 GiB objects with three repeats. Local background load is
calibrated from the device's 4-KiB random-read IOPS and rate-limited to
0/25/50/75/90 percent of that reference. Remote offered load uses a separate
random-read NoF stream at a proportional queue depth. It defaults to 4 KiB and
can be changed with `CHAR_REMOTE_LOAD_BS`; this is an offered load level, not a
claim of exact NIC utilization. Each foreground run records
host CPU, NIC byte rates, local block-device busy time, process CPU, and raw
SPDK bdev I/O-stat snapshots. The CSV's remote weighted-busy percentage is a
latency-tick estimate; use the preserved SPDK snapshots for device-specific
interpretation.

Results are written under `results/<timestamp>/characterization/`:

- `raw/`: fio JSON, NoF logs, exit codes, and background-load logs;
- `telemetry/`: host samples, process resource usage, and SPDK snapshots;
- `runs.csv` and `summary.csv`: per-run and median unified metrics;
- `crossover.csv`: local-load and remote-load comparisons by size;
- `size-bandwidth.svg` and `crossover.svg`: size curve and decision map;
- `conclusion.json`: Go/No-Go result and aggregation-test status.

`results/` is intentionally ignored because it contains machine-specific raw
outputs and telemetry. Promote only reviewed, reproducible findings to this
`docs/` directory. The current characterization summary is documented in
[`local-remote-decision-boundary.md`](local-remote-decision-boundary.md).

The automated Go criterion is deliberately narrow: at least one nonzero local
load and object-size combination must have lower remote p95 latency with no
errors. It does not claim multi-SSD aggregation; that remains marked untested
until additional namespaces or targets are configured. Rebuild summaries and
plots without rerunning hardware tests with `./run.sh characterize-summarize`.

## 8. Same-SSD path-overhead characterization

This maintenance-window workflow compares Mooncake NoF with target-local SPDK
`bdevperf` against the same physical SSD. Configure `TARGET_NVME_BDF`,
`TARGET_NVME_SERIAL`, `SPDK_DIR`, and the optional `SAME_SSD_CLIENT_*` values
when the benchmark runs on a separate client host. Then run:

```bash
./run.sh same-ssd-preflight
./run.sh same-ssd-characterize
```

The harness runs remote-before, stops the target service once, measures local
reads, recreates a transient service when necessary, validates the NQN,
listener, namespace, and physical serial, runs a 4-KiB recovery probe, and then
runs remote-after. The default required matrix is 4 KiB through 16 MiB at
QD1/8/32, with three repetitions, a 2-second warmup, and a 15-second measured
interval. Large requests use 128-KiB subcommands and an 8-MiB per-qpair inflight
window to stay below the client request-pool boundary while preserving the
object-level QD.

`same-ssd-overhead.csv` reports remote latency overhead and remote/local
bandwidth and IOPS ratios. A cell is inconclusive if a sample is missing or
fails, remote-before/after latency drifts by more than 10%, service recovery
fails, or SMART critical/media-error counters increase. The separate 64-MiB
QD1 capability probe is informative and is not an acceptance gate. Rebuild the
summary without rerunning hardware tests with:

```bash
SAME_SSD_RESULT_DIR=results/same-ssd-<timestamp> ./run.sh same-ssd-summarize
```

## 9. Cleanup

```bash
./run.sh unregister
./run.sh target-cleanup
./run.sh post-smart
```

`target-cleanup` stops `nvmf_tgt`, resets SPDK binding, and verifies the target
serial after `/dev/nvme0n1` returns. If the original kernel NVMe-oF export is
needed, restore it using the site-specific target configuration, then connect
the client by NQN. Device numbering may change; identify it by NQN, NSID,
serial number, and model rather than `/dev/nvme3n1` alone.

## Acceptance

`summarize.py` writes `summary.csv` and `acceptance.json`. A performance case
passes when all three-run medians have zero failures, sequential bandwidth or
random IOPS reaches at least 70% of fio, and P99 is no more than 2x fio. The
failure-recovery observations remain operator-verified because intentionally
stopping a remote target cannot be made safe as an unattended assertion.
