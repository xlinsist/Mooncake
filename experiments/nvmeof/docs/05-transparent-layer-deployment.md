# Transparent-Layer Python 3.12 Deployment

This procedure deploys a Mooncake Store binding and Master binary from one
source revision without replacing `/usr/local` artifacts or modifying another
checkout. It is the operational follow-up to the transparent-layer paired
latency experiment.

## Scope and defaults

- Build and deploy the Python 3.12 `mooncake.store` extension together with
  `mooncake_master` from the same source revision.
- Use a timestamped deployment root on `intel-bigmem-2`; retain the source,
  build, release package, logs, and rollback record under that root.
- Start with `MC_HETERO_STORAGE_POLICY=local_only`. This is the conservative
  default because it keeps ordinary writes on the local backend. Switch to
  `remote_only` only for a NoF-specific workload and to `round_robin` only
  after both backends have passed their lifecycle checks.
- Keep `--enable_offload=true` on the Master. Local NVMe placement requires
  it, even when the selected policy is `remote_only`.

The existing verified deployment uses this layout:

```text
<deployment-root>/src/       exact source used for the build
<deployment-root>/build/     CMake build containing mooncake_master
<deployment-root>/release/   isolated Python package tree
<deployment-root>/results/   JSON evidence and service logs
<deployment-root>/rollback/  prior process record and restore command
```

## Build and package

Run the build on `intel-bigmem-2`, where the SPDK static libraries are
available. Configure it with the testbed's normal NoF options and Python 3.12,
then build the Master and Python integration target. Do not use a system-wide
installed extension as a fallback.

Before switching a service, prove the interpreter will load the staged package:

```bash
export PYTHONPATH=<deployment-root>/release/mooncake-integration
python3 - <<'PY'
import mooncake.store as store
assert hasattr(store.ReplicateConfig, "local_replica_num")
assert hasattr(store.ReplicaDescriptor, "is_nof_replica")
print(store.__file__)
PY
```

The printed path must be below `<deployment-root>/release`. Record the source
commit, Python version, CMake arguments, extension path, and Master binary
path alongside the result directory.

## Reversible Master switch

Capture the current command line, environment, PID, listeners, and a restore
command before stopping the active process. Start the staged binary with its
policy in the **Master environment**, not only in the test shell:

```bash
MC_HETERO_STORAGE_POLICY=local_only \
  <deployment-root>/build/mooncake-store/src/mooncake_master \
  --rpc_address=10.0.0.34 --enable_offload=true --logtostderr=true
```

Wait for `10.0.0.34:50051` and `:9003` to listen. Each Master restart loses
the transient NoF registration, so re-register the configured endpoint before
remote or round-robin work:

```bash
cd <deployment-root>/src/experiments/nvmeof
RESULT_DIR=<deployment-root>/results/<run-id> ./run.sh register
```

Use the preserved restore command if the staged Master fails to listen, the
staged binding probe fails, or the initial Store smoke test fails.

## Policy-specific verification

Use one `TRANSPARENT_RUN_ID` for a coherent acceptance batch. Set
`SSD_OFFLOAD_PATH` to the already approved experiment subdirectory whenever a
local backend is selected.

```bash
# Master started as local_only
SSD_OFFLOAD_PATH=<approved-local-subdirectory> \
  TRANSPARENT_RUN_ID=<run-id> RESULT_DIR=<result-dir> ./run.sh transparent-local

# Restart Master as remote_only, then run ./run.sh register.
TRANSPARENT_RUN_ID=<run-id> RESULT_DIR=<result-dir> ./run.sh transparent-remote

# Restart Master as round_robin, then run ./run.sh register.
SSD_OFFLOAD_PATH=<approved-local-subdirectory> \
  TRANSPARENT_RUN_ID=<run-id> RESULT_DIR=<result-dir> ./run.sh transparent-round-robin
```

For the paired overhead measurement, run `transparent-overhead` separately
with `local_nvme` and `remote_nof`; never combine the two deltas. The verified
experiment measured `put` p50 overhead of `+4.07%` for local NVMe and
`+14.03%` for remote NoF.

## Verified evidence

| Batch | Artifact or check | Outcome |
| --- | --- | --- |
| Paired performance (`20260816T000000Z-transparent`) | local NVMe `put` p50 | `+4.0716%` transparent-minus-direct |
| Paired performance (`20260816T000000Z-transparent`) | remote NoF `put` p50 | `+14.0316%` transparent-minus-direct |
| Lifecycle (`20260816T160000Z-lifecycle`) | local-only and remote-only | 12 objects verified and removed for each backend |
| Lifecycle (`20260816T160000Z-lifecycle`) | unavailable local and remote | write failed, zero replicas published, key unreadable |
| Lifecycle (`20260816T160000Z-lifecycle`) | client restart | 12 remote objects and descriptors verified, then removed |
| Lifecycle (`20260816T160000Z-lifecycle`) | round-robin | 6 local NVMe and 6 remote NoF objects verified and removed |

At the end of the test, the staged Master was returned to `local_only` and the
NoF endpoint was re-registered. The staged package import resolves to
`<deployment-root>/release/mooncake-integration/mooncake/store*.so`.

## Release gates

The binding and policy paths are ready for continued feature work after:

1. `python3 -m pytest -q experiments/nvmeof/test_correctness.py` passes.
2. The staged import probe resolves to the isolated release package.
3. Local, remote, unavailable, client-restart, and round-robin JSON reports
   have `status: "pass"` within their respective coherent result batches.
4. The Master is restored to `local_only` and the NoF endpoint is registered.
