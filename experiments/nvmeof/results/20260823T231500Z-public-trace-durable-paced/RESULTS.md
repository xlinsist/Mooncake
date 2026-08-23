# FAST'25 Public-trace Durable Pacing Results

## Verdict

`status=pass`: all 12 planned 100-request cases completed at
`replay_scale=1`. Every write has a matching durability operation, all pacing
fields are present, and target subsystem, bdev, service state, and service PID
are byte-identical before setup and after cleanup.

The matrix covers conversation/toolagent, local NVMe ext4 and a temporary
file-backed NVMe-oF XFS path, and three counterbalanced repetitions. It fixes
`glm5`, 512-token pages, 64 physical pages with modulo mapping,
`fsync=always`, and one benchmark thread.

## Three-repeat medians

| Trace | Path | Scheduled span | Wall | Completion lag | Arrival lag p50/p95/max | QPS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| conversation | local ext4 | 33.0 s | 148.597 s | 115.597 s | 43.520/91.292/115.510 s | 0.67 |
| conversation | file-backed NoF XFS | 33.0 s | 68.962 s | 35.962 s | 13.608/24.978/35.907 s | 1.45 |
| toolagent | local ext4 | 15.0 s | 89.065 s | 74.065 s | 42.277/66.184/73.813 s | 1.12 |
| toolagent | file-backed NoF XFS | 15.0 s | 47.797 s | 32.797 s | 18.375/28.841/32.587 s | 2.09 |

Both durable paths fall behind the source arrival schedule. The remote whole
path has `0.31/0.45x` the local completion lag for conversation/toolagent and
`0.27/0.44x` the arrival-lag p95. These are different devices, filesystems, and
I/O stacks: local uses client ext4, while remote uses a target file, SPDK AIO,
NVMe-oF/RDMA, and client XFS. The ratios are not matched-substrate,
transport-only, or system-superiority evidence.

Remote/local request-p50 ratios are `0.460x` for conversation and `0.842x` for
toolagent; p95 ratios are `0.454x` and `0.463x`. Read p50 remains near parity
(`1.036x` and `0.970x`), while write p50 is `0.464x` local. This preserves the
earlier whole-path explanation: a different persistent write path dominates
the observed difference, while reuse dilutes it in toolagent request p50.

## Durability and recovery

The 12 cases issued 26,466 writes and 3,432 reads, approximately
1,681,161,191,424 bytes of benchmark payload operations. All 26,466 writes have
`sync_count == write_count`.

The controller created one uniquely named 8 GiB AIO file, bdev, subsystem, and
client mount. Cleanup unmounted, disconnected, deleted those resources, and
proved exact target subsystem/bdev/service restoration with the target service
remaining at PID `2072748`. The existing `nof-phase1` namespace was never
restarted or unregistered.

A pre-workload pilot stopped with exit 128 because provenance capture ran
`git rev-parse` on the isolated benchmark copy. It created no temporary
subsystem and restored target/client state. The formal controller records the
explicit source commit instead; the raw archive preserves both runs.

## Boundary and files

This is GPU-free, single-threaded, sequential, bounded 100-request evidence. It
does not cover a matched raw-device substrate, full trace, Store
direct/transparent modes, true concurrency, model execution, TTFT/TPOT, or
serving goodput.

`matrix-conclusion.json` is the formal verdict. CSV files contain trial,
aggregate, and paired whole-path metrics. The raw archive includes logs, GNU
time output, environment snapshots, controller, aggregator, and the recovered
pilot.
