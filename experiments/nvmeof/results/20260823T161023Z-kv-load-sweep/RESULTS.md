# KV-cache background-load sweep

Batch `20260823T161023Z-kv-load-sweep` ran on the two-node NVMe-oF testbed
from source commit `255736477145236f39702f44e7a523a36914828c`.

The fixed workload uses 128 KiB values, 24 requests, four blocks per request,
50% configured reuse, seed 42, and sequential replay
(`configured_concurrency=1`). Four counterbalanced trials cover `idle`,
`local50`, `local90`, and `remote_stress`. Each cell retains the same trace
digest and five cases: `no_store`, direct/transparent local, and
direct/transparent remote. Direct/transparent order alternates by trial.

All 80 main cases and eight idle-anchor cases completed with 148 operations,
zero misses, and exact local/remote descriptors. All 88 foreground cases have
system, process, RDMA, block, NIC, Master-policy, SPDK-iostat, and local/target
SMART snapshots. The run contains 32 main load epochs. The local 50% and 90%
rates achieved 81,868.83--81,868.90 and 147,363.92--147,364.04 IOPS against
requested rates of 81,870 and 147,366 IOPS.

The batch gate is intentionally `inconclusive`, scoped only to
`remote_stress`. Idle, local50, and local90 pass. Two of four primary remote
policy stress epochs collapsed from the 6,976.25-IOPS calibration point to
7.23 and 7.03 IOPS, despite an active load unit and zero reported failed
operations. Two matched recovery repeats with the same transparent-first
order and a 15-second cooldown produced one collapsed epoch (7.24 IOPS) and
one healthy epoch (6,974.92 IOPS). The stress point is therefore
nondeterministic and is excluded from direct/transparent overhead claims.

For the three valid scenarios, median request p50 across four trials was:

| Scenario | Direct local | Transparent local | Direct remote | Transparent remote |
| --- | ---: | ---: | ---: | ---: |
| idle | 3232.071 us | 3431.102 us | 910.820 us | 1030.266 us |
| local50 | 3245.932 us | 3442.337 us | 902.805 us | 1025.800 us |
| local90 | 3251.247 us | 3437.913 us | 901.335 us | 1030.057 us |

Transparent-minus-direct paired median deltas were:

| Scenario | Target | Put p50 | Get p50 | Remove p50 | Request p50 | Request p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| idle | local | +5.97% | +0.53% | -1.14% | +7.51% | +3.94% |
| local50 | local | +7.14% | -3.58% | +2.78% | +6.87% | +3.96% |
| local90 | local | +5.04% | -4.47% | +0.48% | +5.30% | +4.89% |
| idle | remote | +14.49% | -0.39% | +0.02% | +13.30% | +17.24% |
| local50 | remote | +14.20% | +0.10% | +0.60% | +13.68% | +9.28% |
| local90 | remote | +13.85% | -0.40% | +0.76% | +14.43% | +20.95% |

The local idle anchors show request-p50 drift between -1.18% and +1.82%; the
remote anchors show -1.36% to -0.77%. The largest retained anchor drift is
+7.54% for direct-local operation p95, so tail values remain descriptive.

The target service stayed active at PID 2072748. Root Masters completed
`local_only`, `remote_only`, and final `round_robin` phases without heartbeat
or NoF-unmount markers. The final 12-object recovery smoke verified and
removed six local and six remote objects with zero phantom replicas.

Files in this directory:

- `matrix-conclusion.json`: fail-closed main gate and per-scenario status.
- `remote-stress-recovery-conclusion.json`: cooldown recovery classification.
- `matrix-summary.csv`: one row per main case.
- `operation-summary.csv`: per-operation distributions.
- `paired-overhead-summary.csv`: paired direct/transparent summaries.
- `load-summary.csv`: requested and achieved load by phase/scenario/trial.
- `telemetry-summary.csv`: per-case system and temperature summaries.
- `anchor-summary.csv` and `anchor-drift-summary.csv`: idle-anchor evidence.
- `design.json`: predeclared schedule and claim boundary.
- `20260823T161023Z-kv-load-sweep.raw-artifacts.tar.gz`: full batch excluding
  three explicitly labeled launcher-debug probes; it includes raw cases,
  telemetry, load logs, service logs, inventories, controllers, aggregators,
  recovery repeats, and the final recovery smoke.
- `SHA256SUMS`: checksums for all retained evidence files.

This experiment is a fixed 128 KiB, 50% reuse, single-client sequential
replay. It does not establish true concurrency, serving/TTFT behavior, exact
remote utilization, transport-only overhead, isolation, adaptive policy
quality, or a generalized load crossover.
