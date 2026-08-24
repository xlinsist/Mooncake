# Full FAST'25 toolagent trace Store replay

## Scope

- Run ID: `20260824T013130Z`.
- Source: all `23,608` FAST'25 toolagent requests, converted to `710,336`
  sequential KV events per case.
- Matrix: `no_store`, direct local, transparent local, direct remote, and
  transparent remote; three trials each (`15` cases total).
- Configuration: `128 KiB` blocks, `64`-page capacity, `replay_scale=0`, no
  source-arrival pacing, and one replay process at a time.
- Compact evidence: exact full-population metrics and correctness counters with
  `128` deterministic operation samples per case.

## Acceptance

- `15/15` case return codes are zero and all three cell conclusions are `pass`.
- Every compact raw result reports exactly `710,336` operations, no full
  `operations` array, and zero content, descriptor, return-code, and error
  failures.
- The 15 raw JSON files total `1,826,488` bytes; the full converted trace is
  materialized once and referenced by three cell symlinks.
- Client NVMe subsystem inventory is byte-identical before and after the run,
  final `round_robin` recovery passed, and all three Master heartbeat checks
  are clean.
- A fresh post-run target check reports the SPDK service `active/running` with
  its original PID `2072748` and exactly one matching experiment NQN.
- `matrix-conclusion.json` is `pass` with no errors.

## Median results

| Case | Request p50 (us) | Request p95 (us) | Storage wait (s) | Operation rate (ops/s) |
| --- | ---: | ---: | ---: | ---: |
| no-store proxy | 13,000.000 | 52,000.000 | 0.000 | 1,734.151 |
| direct local | 7,262.022 | 49,046.957 | 336.776 | 2,109.226 |
| transparent local | 7,436.062 | 50,809.973 | 347.711 | 2,042.893 |
| direct remote | 2,673.741 | 16,067.216 | 113.645 | 6,250.507 |
| transparent remote | 2,774.561 | 18,003.682 | 125.608 | 5,655.175 |

Paired transparent-versus-direct medians across the three trials:

| Target | Request p50 | Request p95 | Storage wait | Operation rate |
| --- | ---: | ---: | ---: | ---: |
| local | +2.16% | +3.59% | +3.31% | -3.21% |
| remote | +3.77% | +12.03% | +10.53% | -9.52% |

The remote path is faster than the local path in this run, but the paths use
different devices, filesystems, and I/O stacks. This result is not transport
isolation and does not establish general remote superiority.

## Boundaries

- This is sequential replay, not authorized request overlap or true concurrency.
- `no_store` uses a fixed recompute proxy; it is not model execution.
- The client has no NVIDIA GPU, so this is not serving, TTFT, or TPOT evidence.
- One existing remote namespace is used; this is not multi-target scaling,
  saturation, failure-repair, or HA evidence.

## Artifacts

- `aggregate-summary.csv`: median case metrics across three trials.
- `paired-trials.csv` and `paired-aggregate.csv`: paired direct/transparent
  deltas.
- `matrix-conclusion.json`: machine-checked completeness and correctness gates.
- `20260824T013130Z-toolagent-core.tar.gz`: compact raws, summaries, manifests,
  scripts, inventories, and recovery evidence.
- `20260824T013130Z-toolagent-trace.tar.gz`: the single converted full trace.
- `20260824T013130Z-toolagent-case-logs.tar.gz`: replay logs.
- `20260824T013130Z-toolagent-service-logs.tar.gz`: Master, registration,
  driver, and recovery logs.

All archives are below `50 MiB`; verify them with `sha256sum -c SHA256SUMS`.

Rebuild the accessible summaries after extracting the core archive:

```bash
python3 aggregate_full_trace.py EXTRACTED_RUN_ROOT .
```
