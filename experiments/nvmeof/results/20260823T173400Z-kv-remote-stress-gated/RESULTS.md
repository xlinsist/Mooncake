# Gated remote-stress KV-cache supplement

Batch `20260823T173400Z-kv-remote-stress-gated` recovers a load-valid remote
1 MiB/QD32 comparison after the primary background-load matrix exposed
nondeterministic NoF benchmark startup.

Each of four trials first runs a finite two-second sacrificial attach, then
starts a separate 75-second measured load epoch. The measured epoch must stay
within 80--120% of the 6,976.25-IOPS calibration point and report zero failed
operations. Direct/transparent order alternates D->T / T->D across trials.
Idle remote anchors bracket the phase. Foreground replay remains fixed at
128 KiB, 50% configured reuse, 24 requests, four blocks per request, seed 42,
and `configured_concurrency=1`.

The gate passed:

- four measured epochs achieved 6,974.81--6,974.92 IOPS and 6.81 GiB/s;
- all measured epochs completed 523,111--523,119 operations with zero failed;
- two of four sacrificial attaches reproduced the collapsed state at 7.5
  IOPS, while every immediately following measured attach was healthy;
- eight loaded Store cases completed 148 operations, zero misses, and exactly
  148 remote descriptors;
- four anchor cases and eight loaded cases all retained system/process,
  RDMA, SPDK, Master, and SMART telemetry;
- one trace digest is shared by all four trials.

Paired transparent-minus-direct summaries across four valid trials were:

| Metric | Direct median | Transparent median | Median paired delta | Range |
| --- | ---: | ---: | ---: | ---: |
| Request p50 | 9971.325 us | 10144.755 us | +1.74% | +0.54% to +2.09% |
| Request p95 | 10139.530 us | 10310.960 us | +1.67% | +0.31% to +2.81% |
| Put p50 | 2509.116 us | 2552.652 us | +1.77% | -0.51% to +2.16% |
| Get p50 | 2411.707 us | 2411.201 us | -0.11% | -0.48% to +0.09% |
| Remove p50 | 61.200 us | 61.460 us | +0.94% | -32.84% to +1.29% |

Put p99 remains noisy and is not used for a directional claim. The idle
anchors also expose a direct-path cold-state outlier: direct request p50 moves
from 723.920 to 924.229 us (+27.67%), while transparent request p50 moves
-0.25%. Therefore this supplement supports only within-epoch paired overhead
under the gated offered-load point; it does not support an absolute
idle-to-stress speedup or degradation claim.

The target service was active at the final check with `MainPID=2072748`. The
batch did not retain independent pre/post PID snapshots, so it does not claim
that the PID remained unchanged throughout. Root Masters completed the
remote-only phase and final round-robin recovery without heartbeat or NoF
unmount markers. The final 12-object smoke verified and removed six local and
six remote objects with zero phantom replicas.

Files in this directory:

- `matrix-conclusion.json`: load, case, telemetry, trace, and recovery gate.
- `matrix-summary.csv` and `operation-summary.csv`: loaded foreground results.
- `paired-overhead-summary.csv`: four-trial paired distributions.
- `load-summary.csv`: sacrificial and measured load outcomes.
- `anchor-summary.csv` and `anchor-drift-summary.csv`: idle drift evidence.
- `design.json`: predeclared gate and claim boundary.
- `20260823T173400Z-kv-remote-stress-gated.raw-artifacts.tar.gz`: full raw
  supplement, including controllers, diagnostics, interval logs, telemetry,
  Master logs, inventories, and recovery smoke.
- `SHA256SUMS`: checksums for all retained evidence files.

This result does not identify the root cause of the first-attach collapse. It
shows a reproducible operational guard: a sacrificial attach can absorb the
bad startup state, while the independently measured next attach must still
pass an achieved-load gate. It does not establish true concurrency, serving
behavior, isolation, exact utilization, or a general NoF scheduling policy.
