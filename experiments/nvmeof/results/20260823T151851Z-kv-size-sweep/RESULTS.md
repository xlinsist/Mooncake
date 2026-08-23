# KV-cache object-size sweep

Batch `20260823T151851Z-kv-size-sweep` ran on the two-node NVMe-oF testbed
from source commit `255736477145236f39702f44e7a523a36914828c`.

The matrix covers 16, 64, 128, and 256 KiB values, three repeats per size,
and five matched cases per repeat: `no_store`, direct local, transparent
local, direct remote, and transparent remote. Each case used 24 requests,
four blocks per request, 50% configured reuse, seed 42, and sequential replay
(`configured_concurrency=1`). Direct and transparent order alternated by
trial.

The matrix gate passed all 60 cases across 12 size/trial cells. Every case
executed 148 operations with zero misses. All 24 local Store rows reported
148 local descriptors and all 24 remote Store rows reported 148 remote
descriptors. Each size retained one trace digest across its three repeats.

Median paired transparent-minus-direct deltas were:

| Size | Target | Put p50 | Request p50 |
| ---: | --- | ---: | ---: |
| 16 KiB | remote NoF | +17.36% | +15.32% |
| 64 KiB | remote NoF | +19.78% | +17.10% |
| 128 KiB | remote NoF | +17.24% | +15.27% |
| 256 KiB | remote NoF | +13.99% | +12.28% |
| 16 KiB | local NVMe | +5.79% | +5.97% |
| 64 KiB | local NVMe | +4.32% | +6.48% |
| 128 KiB | local NVMe | -0.00% | -1.82% |
| 256 KiB | local NVMe | +2.02% | +1.78% |

Remote get p50 deltas ranged from -1.91% to +1.81%, and remote remove p50
deltas ranged from -0.89% to +0.40%. Remote put p50 is therefore the stable
size-dependent transparent-layer cost in this batch. Several local p95 and
p99 values were dominated by isolated outliers and are not used to claim a
tail-latency improvement or regression.

During the matrix, an ordinary-user Master lost its NoF segment after three
heartbeat SPDK probes failed with `spdk_env_init_fail` because physical
addresses were unavailable for IOVA PA mode. The resulting `put returned
-200` / `insufficient space` was not capacity exhaustion. Running the Master
as root restored the heartbeat probe; the retained root Master remained clean
for the completed matrix. The archive preserves the first failure, recovery
logs, environment inventories, and a passing round-robin recovery smoke with
six local and six remote objects and no phantom replicas.

Files in this directory:

- `matrix-conclusion.json`: final gate and trace digests.
- `matrix-summary.csv`: one row per case.
- `operation-summary.csv`: per-operation distributions.
- `paired-overhead-summary.csv`: paired direct/transparent summaries.
- `20260823T151851Z-kv-size-sweep.raw-artifacts.tar.gz`: complete remote batch,
  including raw cases, logs, inventories, and failure evidence.
- `SHA256SUMS`: checksums for all retained evidence files.
