# Transparent-Layer Testbed Unblock Plan and Execution Record

## Status

Complete for the paired performance and non-disruptive lifecycle scope. The
isolated deployment is
`/sharenvme/userhome/zhouxulin/mooncake-transparent-layer-py312-20260816T000000Z`.
The dirty checkout and `/usr/local` were left unchanged. The paired results
and lifecycle evidence are retained below that root.

The original plan is preserved below as the rationale and reproducibility
record. It is no longer a pending blocker.

## Objective

Produce valid, paired Store API measurements for the transparent layer on the
same local SSD and remote NoF SSD that were used by the earlier experiments.
For each backend, compare explicit placement (`direct`) with placement selected
by the Master (`transparent`) for `put`, `get`, and `remove`.

This plan addresses testbed blockers only. It does not request a new SSD or
change the device-level local/remote characterization already recorded by the
experiment suite.

## Established Facts

- The measurement client must run on `intel-bigmem-2`; the current workstation
  cannot route to `10.0.0.34:50051`.
- On that testbed, the Master and metadata endpoints are listening, and the
  NoF SPDK service is active. NoF registration alone is insufficient: direct
  4 KiB writes returned `-200` (`insufficient space`).
- The running Master is a manually launched older deployment in `legacy` mode.
  Its installed Python module lacks the local-placement and descriptor APIs
  needed by the paired benchmark.
- The active remote source checkout is dirty and must remain untouched.
- The runner already forwards `SSD_OFFLOAD_PATH` only when a local backend is
  requested ([`run.sh`](../run.sh#L21-L31),
  [`run.sh`](../run.sh#L569-L577)). Therefore the local test must reuse the
  prior experiment's *directory*, not allocate a new SSD or write to an
  unverified mount root.

## Decision

Reuse the existing local SSD experiment directory after identifying it from
the earlier experiment configuration or artifacts. Deploy the current build to
a new versioned directory on `intel-bigmem-2`, leave the dirty checkout and
`/usr/local` unchanged, and switch only the manually launched Master during a
maintenance window. Do not report a transparent-layer result until the
transparent descriptor proves that both variants selected the same backend.

## Execution Plan

1. **Recover the prior local-backend identity without changing it.**
   Inspect the previous `config.env`, result manifests, shell history, and
   Master launch record to obtain the exact `SSD_OFFLOAD_PATH` used for the
   local Store test. Verify it is a directory below the approved prior
   experiment location, writable by the benchmark account, and does not equal
   a mount root such as `/mnt/datassd` or `/sharenvme`. Record the resolved
   path and `df`/mount identity in the new result directory. If the old path
   cannot be evidenced, stop before writing locally and request only that
   missing path from the operator.

2. **Prepare an isolated compatible deployment.**
   Build the current source and copy the Master binary plus the matching Python
   integration artifacts to a timestamped deployment directory on
   `intel-bigmem-2`. Do not use `git pull`, reset, or overwrite the dirty
   checkout. Before switching, run import/API probes proving the staged binding
   supplies `ReplicateConfig.local_replica_num` and
   `ReplicaDescriptor.is_nof_replica`; these are required by the direct
   controls and descriptor validation in
   [`correctness.py`](../correctness.py).

3. **Perform a reversible Master switchover.**
   Capture the existing Master PID, full command line, environment, listening
   sockets, and metadata health. Quiesce the manual process only during the
   agreed window, then start the staged Master with
   `MC_HETERO_STORAGE_POLICY=local_only`. Keep the old command in the run log
   so it can be restored immediately if startup, metadata health, or the API
   probe fails. The policy must be in the Master environment, as required by
   [`README.md`](../README.md#L45-L59), not merely in the benchmark shell.

4. **Run the local paired measurement on the reused path.**
   Start the local backend with the recovered `SSD_OFFLOAD_PATH`, verify that a
   transparent smoke object publishes exactly one `local_nvme` descriptor, and
   run:

   ```bash
   export TRANSPARENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-transparent"
   TRANSPARENT_BENCH_TARGET=local_nvme ./run.sh transparent-overhead
   ```

   Preserve `transparent-overhead-local_nvme.json`. The command pairs explicit
   placement and transparent placement in one invocation
   ([`run.sh`](../run.sh#L574-L577)); all generated keys must be removed by the
   harness before the target is considered clean.

5. **Establish NoF capacity before the remote paired measurement.**
   With the staged Master still running, inspect its registered NoF segments,
   their free capacity/state, and the endpoint/NQN/NSID used by the SPDK
   service. Compare those values with the registration command and SPDK
   namespace capacity. Treat `-200` as a capacity/allocation failure, not a
   latency sample. Only reclaim a segment after proving it is an abandoned
   experiment segment and obtaining explicit deletion authority; otherwise
   repair registration/allocation configuration and retest a single small
   direct write plus removal.

6. **Run the remote paired measurement.**
   Restart the staged Master with `MC_HETERO_STORAGE_POLICY=remote_only`,
   verify a transparent smoke descriptor is exactly `remote_nof`, then run:

   ```bash
   TRANSPARENT_BENCH_TARGET=remote_nof ./run.sh transparent-overhead
   ```

   Preserve `transparent-overhead-remote_nof.json`. A descriptor mismatch,
   failed `remove`, or recurrence of `-200` invalidates the run and returns the
   process to step 5.

7. **Validate and publish only comparable evidence.**
   Run `./run.sh transparent-acceptance` with the common run ID. Its validator
   requires both modes, both targets, and valid `put/get/remove` percentiles,
   operation rates, and applicable bandwidth
   ([`correctness.py`](../correctness.py#L759-L817)). Archive raw JSON,
   Master logs, NoF capacity diagnostics, and the resolved local-path identity.
   Summarize transparent-minus-direct deltas separately for local NVMe and
   remote NoF; never average them.

8. **Restore or retain the staged service deliberately.**
   If the staged deployment is not meant to remain active, restore the captured
   original Master command and verify `50051` and metadata health. If it is
   retained, record its deployment path, commit, policy, and owner in the
   result manifest. In either case, do not modify the dirty checkout.

## Acceptance Evidence

1. The local result identifies a previously used, non-root experiment
   directory; no new SSD or mount root is selected.
2. The staged binding exposes both required APIs before Master switchover.
3. `transparent-overhead-local_nvme.json` and
   `transparent-overhead-remote_nof.json` have `status: "pass"`, a common
   `run_id`, and direct/transparent samples for `put`, `get`, and `remove`.
4. Every transparent sample's published descriptor matches its requested
   backend, and all test objects are successfully removed.
5. No remote result is accepted while any direct smoke write reports `-200`.
6. The original dirty checkout remains dirty only by its pre-existing changes;
   no reset, pull, or write occurs there.

## Risks And Stops

| Risk | Mitigation / stop condition |
| --- | --- |
| Wrong local path damages existing data | Reuse only an evidenced experiment subdirectory; stop when its identity is unknown. |
| Service switchover breaks a shared workload | Capture a restore command and health checks before stopping the manual Master. |
| NoF `-200` hides allocation exhaustion | Require a successful direct write/remove smoke test before timing remote operations. |
| Direct and transparent select different storage | Validate descriptors per object; discard mismatches. |
| Dirty checkout is overwritten | Deploy to a separate timestamped directory and never run mutating Git commands there. |

## Reproducibility Commands

The repository and hardware checks used for this execution were:

```bash
ruff check experiments/nvmeof/correctness.py experiments/nvmeof/test_correctness.py
python3 -m pytest -q experiments/nvmeof/test_correctness.py
cmake --build build-nof --target heterogeneous_storage_test client_storage_backend_test replica_selection_test -j2
ctest --test-dir build-nof -V -R '^heterogeneous_storage_test$'
```

The paired hardware evidence is retained with the isolated deployment for
reproducibility.
