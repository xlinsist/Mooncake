# FAST'25 Public-trace Sequential Pacing Checkpoint

This artifact records a 500-request, 64-page, 128 KiB-value replay of the
FAST'25 conversation and toolagent traces at `replay_scale=10`. Each trace ran
three trials across `no_store`, direct local, transparent local, direct remote,
and transparent remote. All 30 cases, six cell conclusions, pacing gates, and
the final recovery smoke passed.

The replay is sequential. Source millisecond timestamps are preserved as
microsecond offsets and compressed by 10, but every Store operation completes
before the next event starts. Arrival lag therefore measures schedule debt; it
is not evidence of concurrent requests or saturation throughput.

## Aggregate evidence

| Trace | Scheduled span | Path | Wall time | Completion lag | Arrival-lag p50/p95 |
| --- | ---: | --- | ---: | ---: | ---: |
| conversation | 16.5 s | direct local | 17.474 s | 0.974 s | 252.572/1,027.175 ms |
| conversation | 16.5 s | transparent local | 17.877 s | 1.377 s | 313.864/1,373.721 ms |
| conversation | 16.5 s | direct remote | 16.594 s | 0.094 s | 53.789/182.088 ms |
| conversation | 16.5 s | transparent remote | 16.610 s | 0.110 s | 61.607/205.434 ms |
| toolagent | 9.0 s | direct local | 9.964 s | 0.964 s | 636.978/956.235 ms |
| toolagent | 9.0 s | transparent local | 10.046 s | 1.046 s | 558.352/900.133 ms |
| toolagent | 9.0 s | direct remote | 9.159 s | 0.159 s | 58.419/175.508 ms |
| toolagent | 9.0 s | transparent remote | 9.182 s | 0.182 s | 65.186/196.551 ms |

Transparent-versus-direct median request-p50/p95/storage-wait overhead was
`5.96%/4.49%/6.25%` for conversation-local,
`13.21%/14.37%/13.91%` for conversation-remote,
`-5.00%/-5.05%/0.90%` for toolagent-local, and
`9.05%/25.09%/13.50%` for toolagent-remote. The negative local toolagent
request quantiles are treated as trial variability, not speedup.

The matrix executed 256,800 puts, 30,324 gets, and 256,800 removes, modeling
37,633,916,928 put-plus-get bytes. Conversation request/block hit rates were
91.4%/3.23%; toolagent rates were 79.6%/21.20%.

## Recovery and artifact boundary

The client Master started and ended as root-owned `round_robin`, the final
transparent recovery smoke passed, and the client NVMe subsystem inventories
before and after are byte-identical. The target service remained active with
the preflight PID `2072748`; current non-root target access could not query the
SPDK RPC socket, so this checkpoint does not claim a new independent target
bdev/subsystem before-versus-after proof. The preceding capacity artifact
contains that exact topology proof for the same preserved target service.

`matrix-conclusion.json` is the acceptance verdict. CSV files contain medians
and paired trials. The compressed archive contains raw JSON, logs, manifests,
inventories, and scripts; verify it with the adjacent `.sha256` file and verify
the extracted summary files with `SHA256SUMS`.
