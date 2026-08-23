# FAST'25 public-trace durable storage-path smoke

## Result

Run `20260823T151000Z-public-trace-durable` completed all 12 planned trials:
local/remote paths, 5/20 conversation requests, and three repeats per cell.
Every trial used `glm5`, 512-token pages, 64 physical pages,
`--fsync-mode always`, one thread, unpaced replay, and disabled progress output.

| Requests | Path | Median I/O time (s) | Median QPS | Median write p50 / p95 / p99 (ms) | Median write bandwidth (MB/s) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 5 | local NVMe, ext4 | 4.495 | 1.11 | 73.441 / 77.393 / 78.368 | 724.98 |
| 5 | file-backed NVMe-oF, XFS | 1.211 | 4.13 | 18.052 / 21.275 / 21.743 | 2907.36 |
| 20 | local NVMe, ext4 | 29.566 | 0.68 | 48.746 / 73.174 / 75.960 | 1037.88 |
| 20 | file-backed NVMe-oF, XFS | 10.754 | 1.86 | 17.449 / 21.102 / 21.957 | 2958.42 |

The 5-request cells performed 59 durable writes and 4 reads per trial. The
20-request cells performed 560 durable writes and 19 reads per trial. Sync
counts equaled write counts in every run.

## Interpretation boundary

This is a successful public-trace harness and whole-path smoke, not an isolated
NVMe-oF transport comparison. The paths deliberately used different backing
devices and filesystems:

- local: client `/dev/nvme1n1` -> ext4;
- remote: target XFS file on `/mnt/mxp` -> SPDK AIO -> RDMA NVMe-oF -> client
  XFS.

The remote path was 64--75% lower in median write latency for these cells, but
that difference combines target-device, filesystem, SPDK, fabric, and client
effects. It must not be reported as NVMe-oF speedup or transport overhead.

The run also uses modulo mapping: 64 physical pages (3.35 GiB) represent 560
logical pages in the 20-request cell. It does not replay the complete public
trace and cannot support serving-level TTFT, TPOT, or SLO claims.

## Evidence and cleanup

`summary.csv` contains every parsed trial. `raw-artifacts.tar.gz` preserves the
exact benchmark stdout, GNU `time -v` output, and host/mount/NVMe metadata;
after extraction, `SHA256SUMS` covers every raw file. The copied artifacts
matched the client source by SHA-256 before packaging.

The disposable `trace-bench-20260823` client mount/controller and target SPDK
subsystem/AIO bdev/8 GiB image were removed after copying the evidence. The
pre-existing `nof-phase1` namespace remained connected and
`mooncake-nof-spdk.service` remained active.
