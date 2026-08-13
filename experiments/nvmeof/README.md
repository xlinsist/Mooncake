# Mooncake NVMe-oF Validation

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

## 7. Cleanup

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
