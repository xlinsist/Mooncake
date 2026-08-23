# FAST'25 Public-trace 1000-request Paced Checkpoint

This artifact records a 1000-request, 64-page, 128 KiB-value sequential replay
of the FAST'25 conversation and toolagent traces at `replay_scale=10`. Each
trace ran three trials across `no_store`, direct local, transparent local,
direct remote, and transparent remote. All 30 cases, six cell conclusions,
pacing gates, and the final recovery smoke passed.

The checkpoint executed 503,424 puts, 60,660 gets, and 503,424 removes,
modeling 73,935,618,048 put-plus-get bytes. Together with the committed
500-request midpoint, the comparison covers 60 cases, 760,224 puts, 90,984
gets, and 760,224 removes.

## 500/1000 comparison

| Trace | Case | 500 debt | 1000 debt | 500/1000 arrival-lag p95 |
| --- | --- | ---: | ---: | ---: |
| conversation | no-store proxy | 5.32% | 0.45% | 0.869/0.780 s |
| conversation | direct/transparent local | 5.90/8.34% | 1.12/1.52% | 1.027/1.374 vs 1.211/1.306 s |
| conversation | direct/transparent remote | 0.57/0.66% | 0.22/0.25% | 0.182/0.205 vs 0.165/0.186 s |
| toolagent | no-store proxy | 15.84% | 21.00% | 1.148/3.487 s |
| toolagent | direct/transparent local | 10.71/11.62% | 15.54/17.16% | 0.956/0.900 vs 2.660/2.925 s |
| toolagent | direct/transparent remote | 1.77/2.02% | 0.90/0.95% | 0.176/0.197 vs 0.201/0.225 s |

The two request counts are prefixes of a nonstationary public trace rather than
repeated copies of one workload. Conversation operations per request decrease
from 55.734 to 53.702, while toolagent changes from 34.920 to 35.257 and has a
slightly denser source span per request. The opposite local-debt trends are
therefore prefix-composition evidence, not linear scale-up or throughput.

Remote transparent-versus-direct storage-wait overhead changes from
`13.91%` to `11.97%` for conversation and from `13.50%` to `13.71%` for
toolagent. Request-p50/p95 overhead changes from `13.21/14.37%` to
`11.92/11.63%` for conversation and from `9.05/25.09%` to `5.65/16.02%` for
toolagent. These bounded prefixes support a stable positive wrapper-cost
conclusion, not convergence to a universal overhead constant.

## Boundary and recovery

Replay remains single-process and strictly sequential. The result is not a
full-trace, request-concurrency, saturation-throughput, model-serving, or
system-superiority claim.

The client Master started and ended as root-owned `round_robin`, the final
transparent recovery smoke passed, and the client NVMe subsystem inventories
are byte-identical. The target service remained active with PID `2072748`;
non-root SPDK RPC access remains unavailable, so this checkpoint does not add
an independent target bdev/subsystem equality proof.

`paced-request-scale-conclusion.json` is the 500/1000 acceptance verdict. The
raw archive contains the 1000-request logs, JSON, manifests, inventories, and
scripts. The 500-request raw midpoint remains in the committed
`20260823T214823Z-public-trace-store-paced` artifact.
