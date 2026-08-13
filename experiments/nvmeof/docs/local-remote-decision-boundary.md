# Local/remote NVMe decision boundary

## Scope

Experiments collected on 2026-08-13 answer two different questions. A same-SSD
matrix isolates the cost of the remote path from 4 KiB through 16 MiB. Separate
cross-device characterization runs test scheduling crossover at 4 KiB and
1 MiB. Each reported matrix cell contains three successful repetitions.

## Direct conclusion

- Against the same SSD, local is decisively better for small, latency-sensitive
  I/O. At 4 KiB/QD1, local averages 4.30 us versus 104.73 us remotely.
- As request size or QD rises, both paths saturate the SSD and converge near
  6.87 GiB/s. Remote does not make the same SSD intrinsically faster.
- Remote placement can nevertheless win when it selects a faster or less busy
  independent SSD. It then adds storage bandwidth or avoids local contention,
  and that resource advantage can exceed the network-path cost.
- The observed scheduling boundary is: use local for small I/O when remote is
  heavily loaded; otherwise keep an idle remote SSD as a candidate, especially
  for bandwidth-oriented I/O or when local storage is busy.

## Findings

### Same-SSD path overhead

The same physical SSD was measured in remote-before, target-local, and
remote-after order with 2 seconds of warmup, 15 seconds of measurement, and
three repetitions per cell. Remote-before/after latency drift stayed below 1%
for every error-free cell, the target service recovered, and SMART critical
warning and media-error counters did not increase.

The same-SSD result shows that NVMe-oF/NoF path cost is strongly dependent on
request size and queue depth. Representative valid cells are:

| Object size | QD | Remote latency overhead | Remote BW / local BW |
| ---: | ---: | ---: | ---: |
| 4 KiB | 1 | 2336% | 0.041x |
| 4 KiB | 32 | 519% | 0.162x |
| 64 KiB | 32 | -2.4% | 1.026x |
| 256 KiB | 8 | -3.4% | 1.033x |
| 1 MiB | 1 | 17.2% | 0.853x |
| 1 MiB | 8 | -0.1% | 1.004x |
| 4 MiB | 1 | 7.7% | 0.927x |
| 16 MiB | 1 | -1.7% | 1.015x |

Mooncake splits large objects into 128-KiB commands. The benchmark therefore
limits each attached NoF segment/qpair to 8 MiB, or 64 commands, in flight so
it remains below the measured qpair request-pool boundary without changing
object-level QD.

All 18 cells completed without failed operations. The 64-MiB capability probe
also succeeded at QD1 but is not part of the acceptance matrix.

These same-SSD measurements isolate path overhead. They do not replace the
cross-device scheduling results below: remote placement can still win when the
remote SSD is intrinsically faster or avoids local contention.

### Cross-device scheduling crossover

When both paths are idle, the remote path has lower p95 latency for both tested
sizes:

| Object size | Local p95 | Remote p95 | Remote speedup |
| ---: | ---: | ---: | ---: |
| 4 KiB | 0.432 ms | 0.165 ms | 2.62x |
| 1 MiB | 12.648 ms | 4.650 ms | 2.72x |

The same remote path remains faster at 50% and 90% calibrated local 4-KiB
random-read load. This establishes that remote placement belongs in the
decision space, but does not prove that local SSD contention causes the entire
gap: the local path uses fio while the remote path uses Mooncake's NoF
worker-pool benchmark.

A 4-KiB remote background stream did not expose a crossover at the tested
queue depths. A separate 1-MiB background stream, measuring about 3.36 GiB/s,
did:

| Object size | Local state | Remote background | Local p95 | Remote p95 | Winner |
| ---: | --- | --- | ---: | ---: | --- |
| 4 KiB | idle | 1 MiB, ~3.36 GiB/s | 0.432 ms | 2.400 ms | local (5.55x) |
| 1 MiB | idle | 1 MiB, ~3.36 GiB/s | 12.648 ms | 9.200 ms | remote (1.37x) |

The measured policy boundary is therefore:

- prefer remote when the remote path is idle or lightly loaded, particularly
  when local NVMe is busy;
- prefer local for small objects when the remote path carries substantial
  large-block bandwidth traffic;
- keep remote as a candidate for larger objects under the tested bandwidth
  load, while treating its higher-load crossover as unknown.

| Object size | Local state | Remote state | Recommendation | Basis |
| ---: | --- | --- | --- | --- |
| 4 KiB | idle | idle/light | remote | Measured p95: 0.165 vs 0.432 ms |
| 4 KiB | busy | idle/light | remote | Remote stayed faster under tested local load |
| 4 KiB | idle | ~3.36 GiB/s large-block load | local | Measured p95: 2.400 vs 0.432 ms |
| 1 MiB | idle/busy | idle/light | remote | Measured p95: 4.650 vs 12.648 ms at idle |
| 1 MiB | idle | ~3.36 GiB/s large-block load | remote | Measured p95: 9.200 vs 12.648 ms |
| Other sizes or heavier/mixed remote load | any | any | inconclusive | No cross-device crossover measurement |

## Evidence and limitations

The source outputs are the local, ignored directories
`../results/char-discovery-20260813/`,
`../results/char-balanced-20260813/`, and
`../results/char-remote-bandwidth-stress-20260813/`. Their `summary.csv`,
`crossover.csv`, `matrix.json`, and telemetry files provide the underlying
measurements.

The same-SSD source output is
`../results/same-ssd-full-safe-20260813T105000Z/`. Its `runs.csv`, `summary.csv`,
`same-ssd-overhead.csv`, `conclusion.json`, SMART snapshots, and raw logs are
the underlying measurements. All eighteen matrix cells are valid.

These values apply only to the measured hardware and software stack. In
particular:

- local and remote measurements use different I/O implementations and latency
  instrumentation;
- offered remote load is controlled by request size and queue depth, not an
  exact utilization percentage;
- only one remote namespace was tested, so multi-device aggregation remains
  unmeasured;
- the 64-MiB QD1 capability probe succeeded but was not part of the repeated
  acceptance matrix;
- the crossover above is a measured point, not the exact saturation threshold.
