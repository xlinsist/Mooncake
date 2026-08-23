# KV-cache reuse-ratio sweep

Batch `20260823T155044Z-kv-reuse-sweep` ran on the two-node NVMe-oF testbed
from source commit `255736477145236f39702f44e7a523a36914828c`.

The matrix fixes 128 KiB values and varies configured reuse ratio across 0%,
50%, and 90%. Each ratio has three repeats and five matched cases per repeat:
`no_store`, direct local, transparent local, direct remote, and transparent
remote. Every trace uses 24 requests, four blocks per request, seed 42, and
sequential replay (`configured_concurrency=1`). Direct and transparent order
alternates by trial, and ratio order rotates by trial.

The matrix gate passed all 45 cases across nine ratio/trial cells. Reuse 0%,
50%, and 90% produced 192, 148, and 104 operations per case and actual block
hit rates of 0%, 45.8333%, and 91.6667%. Every case had zero misses. All local
and remote Store rows reported exactly one correct descriptor per operation,
and each reuse ratio retained one trace digest across its three repeats.

Median request p50 values across three repeats were:

| Reuse | `no_store` | Direct local | Transparent local | Direct remote | Transparent remote |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | 4000.000 us | 3258.121 us | 3429.502 us | 922.210 us | 1078.441 us |
| 50% | 4000.000 us | 3189.152 us | 3359.491 us | 914.209 us | 1053.700 us |
| 90% | 4000.000 us | 2188.311 us | 2163.701 us | 646.372 us | 638.700 us |

From 0% to 90% reuse, request p50 fell by 32.84% for direct local, 36.91% for
transparent local, 29.91% for direct remote, and 40.78% for transparent
remote. The `no_store` value remains 4000 us because it is a fixed recompute
proxy rather than model execution.

Remote transparent-minus-direct paired median deltas were:

| Reuse | Put p50 | Get p50 | Remove p50 | Request p50 | Request p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | +15.26% | n/a | +0.44% | +15.31% | +18.68% |
| 50% | +16.79% | +0.28% | +1.06% | +16.74% | +17.27% |
| 90% | +14.71% | -0.48% | +0.02% | +0.01% | +13.93% |

Remote put p50 overhead remains stable at roughly 15--17%, while get/remove
p50 remains near direct. At 90% reuse, 22 of 24 requests use existing blocks,
so get-dominated request p50 amortizes the put-path overhead. Request p95 still
shows +13.93%, reflecting the two produce requests; the result does not show
that transparent tail overhead disappears. Local paired tails are noisy and
are not used for a directional claim.

The Master ran as root throughout policy phases. No local, remote, or final
Master log contains `nof_heartbeat_failure` or
`unmount_nof_segment_by_heartbeat`. After the matrix, the script restored root
`round_robin`, re-registered NoF, and passed a 12-object recovery smoke with
six local and six remote objects and zero phantom replicas.

Files in this directory:

- `matrix-conclusion.json`: final gate, expected event counts, and trace
  digests.
- `matrix-summary.csv`: one row per case.
- `operation-summary.csv`: per-operation distributions.
- `paired-overhead-summary.csv`: paired direct/transparent summaries.
- `20260823T155044Z-kv-reuse-sweep.raw-artifacts.tar.gz`: complete remote
  batch, including raw cases, service logs, inventories, driver, aggregator,
  and recovery smoke.
- `SHA256SUMS`: checksums for all retained evidence files.
