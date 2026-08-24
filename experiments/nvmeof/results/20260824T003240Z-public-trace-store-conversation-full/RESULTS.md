# Full FAST'25 conversation trace Store replay

## Scope

- Run ID: `20260824T003240Z`.
- Source: all `12,031` FAST'25 conversation requests, converted to `565,798`
  sequential KV events per case.
- Matrix: `no_store`, direct local, transparent local, direct remote, and
  transparent remote; three trials each (`15` cases total).
- Configuration: `128 KiB` blocks, `64`-page capacity, `replay_scale=0`, no
  source-arrival pacing, and one replay process at a time.
- Compact evidence: exact full-population metrics and correctness counters with
  `128` deterministic operation samples per case.

## Acceptance

- `15/15` case return codes are zero and all three cell conclusions are `pass`.
- Every compact raw result reports exactly `565,798` operations, no full
  `operations` array, and zero content, descriptor, return-code, and error
  failures.
- The 15 raw JSON files total `1,840,018` bytes; the full converted trace is
  materialized once and referenced by three cell symlinks.
- Client NVMe subsystem inventory is unchanged, final `round_robin` recovery
  passed, all three Master heartbeat checks are clean, and the target service
  remains active with its original PID `2072748`.
- `matrix-conclusion.json` is `pass` with no errors.

## Median results

| Case | Request p50 (us) | Request p95 (us) | Storage wait (s) | Operation rate (ops/s) |
| --- | ---: | ---: | ---: | ---: |
| no-store proxy | 14,000.000 | 78,000.000 | 0.000 | 1,961.172 |
| direct local | 12,922.868 | 72,843.779 | 268.497 | 2,107.276 |
| transparent local | 13,362.904 | 75,483.558 | 280.080 | 2,020.133 |
| direct remote | 4,284.162 | 24,629.717 | 89.931 | 6,291.491 |
| transparent remote | 4,840.061 | 27,563.910 | 100.957 | 5,604.337 |

Paired transparent-versus-direct medians across the three trials:

| Target | Request p50 | Request p95 | Storage wait | Operation rate |
| --- | ---: | ---: | ---: | ---: |
| local | +3.41% | +3.80% | +4.25% | -4.08% |
| remote | +12.98% | +12.36% | +12.26% | -10.92% |

The remote path is faster than the local path in this run, but the paths use
different devices, filesystems, and I/O stacks. This result is not transport
isolation and does not establish general remote superiority.

## Boundaries

- This is sequential replay, not authorized request overlap or true concurrency.
- `no_store` uses a fixed recompute proxy; it is not model execution.
- The client has no NVIDIA GPU, so this is not serving, TTFT, or TPOT evidence.
- One existing remote namespace is used; this is not multi-target scaling,
  saturation, failure-repair, or HA evidence.
- The inherited controller initially wrote paced wording into `design.json`.
  `results/inventory/design-metadata-correction.json` records the metadata-only
  correction to match the executed `replay_scale=0` design.

## Artifacts

- `aggregate-summary.csv`: median case metrics across three trials.
- `paired-trials.csv` and `paired-aggregate.csv`: paired direct/transparent
  deltas.
- `matrix-conclusion.json`: machine-checked completeness and correctness gates.
- `20260824T003240Z-conversation-core.tar.gz`: compact raws, summaries,
  manifests, scripts, inventories, and recovery evidence.
- `20260824T003240Z-conversation-trace.tar.gz`: the single converted full trace.
- `20260824T003240Z-conversation-case-logs.tar.gz`: replay logs.
- `20260824T003240Z-conversation-service-logs.tar.gz`: Master, registration,
  driver, and recovery logs.

All archives are below `50 MiB`; verify them with `sha256sum -c SHA256SUMS`.

Rebuild the accessible summaries after extracting the core archive:

```bash
python3 aggregate_full_trace.py EXTRACTED_RUN_ROOT .
```
