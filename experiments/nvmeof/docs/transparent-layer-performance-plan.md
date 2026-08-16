# Transparent-Layer Local/Remote Performance Plan

## Status

Complete on `intel-bigmem-2` with the isolated Python 3.12 deployment described
in [`transparent-layer-deployment.md`](transparent-layer-deployment.md).
The paired Store API results are in the deployment result directory:

```text
/sharenvme/userhome/zhouxulin/mooncake-transparent-layer-py312-20260816T000000Z/results/20260816T000000Z
```

The measured transparent-minus-direct `put` p50 overhead was `+4.0716%` for
local NVMe and `+14.0316%` for remote NoF. These are separate backend results;
they must not be averaged. Lifecycle evidence is retained under the same
deployment root in `results/20260816T160000Z-lifecycle`.

## Goal

Measure whether the heterogeneous-storage transparent layer preserves Store
API performance while selecting either the local NVMe or remote NoF backend.
The comparison must distinguish the policy-selection layer from the underlying
storage path.

## Experimental Matrix

For each target, run the same Store API lifecycle in both modes:

| Target | Direct control | Transparent treatment |
| --- | --- | --- |
| `local_nvme` | `store.put(..., ReplicateConfig(local_replica_num=1))` | `store.put(key, value)` with Master policy `local_only` |
| `remote_nof` | `store.put(..., ReplicateConfig(nof_replica_num=1))` | `store.put(key, value)` with Master policy `remote_only` |

Each cell measures `put`, `get`, and `remove`. The direct and transparent
variants use the same process, object count, payload size, CPU affinity,
persistence settings, warmed binding, and registered backend. The transparent
run verifies the published descriptor before timing each object so a policy
misconfiguration cannot appear as a performance result.

## Metrics And Result Contract

For every operation in every cell, record per-operation samples and report:

- operation count and total bytes where applicable;
- p50, p95, and p99 latency in milliseconds;
- throughput in MiB/s for `put` and `get`;
- operation rate for `remove`;
- process CPU utilization over the complete lifecycle.

The paired result reports absolute and percentage transparent-minus-direct
deltas for each operation metric. `remove` has no payload throughput, so its
comparison is based on latency and operation rate. Raw samples remain in the
JSON artifact to permit later distribution checks.

## Implemented Changes

1. The benchmark lifecycle in `correctness.py` now times `remove` after
   the measured `get`, asserts every removal succeeds, and retains samples
   for all three operations.
2. Paired-overhead delta generation and acceptance validation now require
   valid `remove` latency and operation-rate metrics for both modes and both
   targets.
3. Focused Python tests lock the result schema and reject missing or
   malformed `remove` evidence.
4. The runbook and experiment README document paired local and
   remote commands with the corresponding Master policy and retain one common
   `TRANSPARENT_RUN_ID`.

## Execution And Verification

1. Run `pytest -q experiments/nvmeof/test_correctness.py` before and after the
   change.
2. Run static checks available in the repository for the touched Python and
   shell files.
3. On the two-host testbed, the paired commands were run with one common
   transparent run ID. The resulting local and remote JSON artifacts each have
   `status: "pass"`, complete `put/get/remove` samples, matching descriptors,
   and successful cleanup.

   The lifecycle follow-up additionally passed local-only, remote-only,
   unavailable-local, unavailable-remote, client-restart, and round-robin
   checks. The standalone Master was restored to `local_only` after testing.

   To reproduce the hardware run, create one new result directory and run:

   ```bash
   export TRANSPARENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-transparent"
   MC_HETERO_STORAGE_POLICY=local_only TRANSPARENT_BENCH_TARGET=local_nvme ./run.sh transparent-overhead
   MC_HETERO_STORAGE_POLICY=remote_only TRANSPARENT_BENCH_TARGET=remote_nof ./run.sh transparent-overhead
   ./run.sh transparent-acceptance
   ```

   The policy must be applied to the Master service environment before each
   transparent treatment. Direct controls are executed by the paired command
   using explicit replica configuration.
4. Treat any failed descriptor check, failed removal, missing metric, mixed
   run ID, or failed acceptance artifact as invalid rather than a performance
   comparison. Review the local and remote deltas separately; do not average
   them because they characterize different storage paths.

## Acceptance Criterion

The framework demonstrated transparent encapsulation without a material
performance regression: all four paired cells succeeded, object lifecycle
checks passed, and local and remote deltas are available for `put`, `get`, and
`remove`. This plan records evidence; it does not impose a universal numeric
regression threshold because that threshold depends on the testbed's SSD, NoF
fabric, object size, and configured policy.

Master HA restart/failover and NoF service restart remain separate pre-release
gates. They were not claimed by the standalone-Master result above.
