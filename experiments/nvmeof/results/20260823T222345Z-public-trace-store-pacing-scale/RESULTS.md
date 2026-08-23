# FAST'25 Public-trace Pacing-scale Sensitivity

This artifact combines sequential `replay_scale=5/10/20` checkpoints for the
first 500 conversation and toolagent requests at a fixed 64-page cache and
128 KiB value size. Each scale contains three repeats of `no_store`, direct
local, transparent local, direct remote, and transparent remote. All 90 cases,
18 cell conclusions, pacing gates, and final recovery smokes passed.

Across the three scales the replay executed 770,400 puts, 90,972 gets, and
770,400 removes, modeling 112,901,750,784 put-plus-get bytes. Source and
converted trace digests are identical across scales.

## Schedule-debt boundary

Completion lag as a percentage of the compressed source span was:

| Trace | Case | 5x | 10x | 20x |
| --- | --- | ---: | ---: | ---: |
| conversation | no-store proxy | 0.57% | 5.32% | 82.03% |
| conversation | direct/transparent local | 0.56/0.59% | 5.90/8.34% | 88.33/96.83% |
| conversation | direct/transparent remote | 0.29/0.33% | 0.57/0.66% | 1.45/1.94% |
| toolagent | no-store proxy | 2.10% | 15.84% | 130.00% |
| toolagent | direct/transparent local | 1.85/1.95% | 10.71/11.62% | 117.92/116.66% |
| toolagent | direct/transparent remote | 0.91/0.99% | 1.77/2.02% | 4.89/8.10% |

At 20x, the fixed 1 ms recomputation proxy and both local Store paths cannot
finish within the compressed source span, while the remote paths remain within
8.10%. This is a sequential schedule-debt observation on different storage
paths, not a throughput, saturation, transport-only, or system-superiority
claim. No request or Store operation overlaps another.

## Transparent overhead

Remote transparent-versus-direct storage-wait overhead remains within
`12.11--14.42%` across both traces and all scales. Conversation-remote request
p50/p95 overhead is `11.48--13.21%` / `10.88--14.37%`; toolagent-remote is
`7.99--9.05%` / `12.29--25.09%`. Local toolagent request quantiles change sign
while storage-wait overhead remains `0.36--2.75%`, so they remain variability
evidence rather than a transparent speedup claim.

## Recovery and files

The client Master returned to root-owned `round_robin` after every scale, all
final transparent smokes passed, and the 5x/20x client NVMe subsystem
inventories are byte-identical before and after. The target service remained
active with PID `2072748`; non-root SPDK RPC access remains unavailable, so the
new scales do not independently prove target bdev/subsystem equality.

`pacing-scale-conclusion.json` is the unified verdict. The two raw archives
contain the new 5x and 20x logs, JSON, manifests, inventories, and scripts. The
10x midpoint is referenced from the previously committed
`20260823T214823Z-public-trace-store-paced` artifact. Verify the new archives
with `20260823T222345Z-public-trace-store-pacing-scale.raw.sha256`.
