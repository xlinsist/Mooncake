# Local/remote NVMe decision boundary

## Scope

Three characterization runs collected on 2026-08-13 compare direct reads from
a local NVMe device with reads through one remote Mooncake NVMe-oF namespace.
Each reported cell contains three successful repetitions. The useful tested
object sizes are 4 KiB and 1 MiB.

## Findings

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

## Evidence and limitations

The source outputs are the local, ignored directories
`../results/char-discovery-20260813/`,
`../results/char-balanced-20260813/`, and
`../results/char-remote-bandwidth-stress-20260813/`. Their `summary.csv`,
`crossover.csv`, `matrix.json`, and telemetry files provide the underlying
measurements.

These values apply only to the measured hardware and software stack. In
particular:

- local and remote measurements use different I/O implementations and latency
  instrumentation;
- offered remote load is controlled by request size and queue depth, not an
  exact utilization percentage;
- only one remote namespace was tested, so multi-device aggregation remains
  unmeasured;
- the 64-MiB exploratory case produced failed submissions and is excluded;
- the crossover above is a measured point, not the exact saturation threshold.

