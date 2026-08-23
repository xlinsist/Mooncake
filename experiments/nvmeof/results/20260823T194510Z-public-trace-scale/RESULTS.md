# FAST'25 Public Trace Durable Scale Results

## Verdict

`status=pass`: all 36 planned cases completed with exit status zero. Every write
was followed by the configured durability operation (`sync_count == write_count`),
and the target subsystem list, bdev list, service state, and service PID were
byte-identical before setup and after cleanup.

The matrix covers FAST'25 `conversation` and `toolagent` traces at 20, 50, and
100 requests, local and remote storage paths, and three counterbalanced
repetitions. It fixes `glm5`, 512-token pages, 64 physical pages with modulo
mapping, `fsync=always`, one benchmark thread, and unpaced replay.

## Three-Repeat Medians

| Trace | Requests | Path | Hit rate | QPS | Request p50 / p95 / p99 (ms) | Read p50 (ms) | Write p50 (ms) |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| conversation | 20 | local ext4 | 3.28% | 0.66 | 1077.806 / 2858.672 / 7283.200 | 35.196 | 48.643 |
| conversation | 20 | remote NoF XFS | 3.28% | 1.51 | 401.755 / 1340.449 / 3424.337 | 35.476 | 22.123 |
| conversation | 50 | local ext4 | 4.07% | 0.85 | 930.706 / 2476.179 / 6495.772 | 35.466 | 46.996 |
| conversation | 50 | remote NoF XFS | 4.07% | 1.71 | 397.980 / 1251.255 / 3141.587 | 37.381 | 23.033 |
| conversation | 100 | local ext4 | 3.26% | 0.68 | 1049.580 / 4052.591 / 7976.262 | 35.300 | 46.756 |
| conversation | 100 | remote NoF XFS | 3.26% | 1.44 | 485.302 / 1942.048 / 4130.668 | 36.018 | 21.988 |
| toolagent | 20 | local ext4 | 12.65% | 0.79 | 813.752 / 2815.371 / 6984.689 | 32.003 | 47.052 |
| toolagent | 20 | remote NoF XFS | 12.65% | 1.60 | 394.621 / 1339.917 / 3386.587 | 30.972 | 22.435 |
| toolagent | 50 | local ext4 | 21.02% | 1.03 | 489.707 / 2537.448 / 6122.121 | 32.769 | 46.827 |
| toolagent | 50 | remote NoF XFS | 21.02% | 1.92 | 396.065 / 1233.576 / 3089.572 | 31.448 | 22.684 |
| toolagent | 100 | local ext4 | 24.27% | 1.12 | 488.410 / 2505.498 / 4199.445 | 33.123 | 46.885 |
| toolagent | 100 | remote NoF XFS | 24.27% | 2.01 | 397.553 / 1232.339 / 2179.896 | 31.262 | 23.357 |

Across the six cells, the remote whole path delivered `1.79--2.30x` the local
QPS. Its write p50 was `0.45--0.50x` local, while read p50 was
`0.94--1.03x` local. The toolagent hit rate rose from `12.65%` at 20 requests
to `24.27%` at 100; correspondingly, its request p50 path ratio narrowed from
`0.479x` to `0.814x`, while its p95 ratio remained about `0.485x`.

These ratios are not NVMe-oF speedups. Local uses client `/dev/nvme1n1` with
ext4; remote uses an 8 GiB target file, SPDK AIO, NVMe-oF/RDMA, and client XFS.
The result is a durable whole-path workload-scale comparison across different
devices and filesystems.

## Evidence and Boundaries

The 36 cases issued 44,394 writes and 5,544 reads, representing approximately
2.808 TB of benchmark payload operations. All 44,394 writes have matching sync
counts. The full raw archive also preserves two failed controller pilots: one
stopped before subsystem creation because an RPC model string was split, and
one stopped before the first workload because `case_id` was unset. Both pilots
restored the exact initial target state.

This batch is GPU-free, unpaced, single-threaded, and uses 64 physical pages
with modulo mapping. It does not cover the full 12,031/23,608-request traces,
true concurrency, model execution, TTFT/TPOT, a matched raw-device substrate,
or Store `direct`/`transparent` modes. It therefore strengthens trace realism
and request-scale evidence without changing the existing serving or transport
claim boundaries.

## Artifacts

- `trial-summary.csv`: all per-case metrics.
- `aggregate-summary.csv`: three-repeat medians.
- `paired-path-summary.csv` and `paired-path-aggregate.csv`: matched whole-path ratios.
- `matrix-conclusion.json`: strict completeness, durability, and restoration gate.
- `20260823T194510Z-public-trace-scale.raw-artifacts.tar.gz`: raw logs, GNU time output,
  environment snapshots, both failed pilots, controller, and aggregator.
- `SHA256SUMS`: checksums for every durable top-level artifact.
