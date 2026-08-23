# NVMe-oF documentation index

This directory tracks the development path from local/remote motivation to the
verified transparent heterogeneous-storage deployment.

1. [`01-local-remote-decision-boundary.md`](01-local-remote-decision-boundary.md)
   records the measured local NVMe versus remote NoF decision boundary and
   explains why both backends belong in the scheduling space.
2. [`02-transparent-heterogeneous-storage-development.md`](02-transparent-heterogeneous-storage-development.md)
   specifies the transparent Store API layer, local/remote/round-robin policies,
   metadata lifecycle, implementation milestones, and tests.
3. [`03-transparent-layer-testbed-unblock-plan.md`](03-transparent-layer-testbed-unblock-plan.md)
   records how the blocked two-host testbed was recovered safely and how valid
   paired local/remote measurements were unblocked.
4. [`04-transparent-layer-performance-plan.md`](04-transparent-layer-performance-plan.md)
   defines the direct-versus-transparent performance experiment and records its
   completed put/get/remove evidence.
5. [`05-transparent-layer-deployment.md`](05-transparent-layer-deployment.md)
   captures the isolated Python 3.12 deployment, reversible Master switch,
   policy verification, verified results, and rollback procedure.
6. [`06-runbook.md`](06-runbook.md)
   is the current maintenance and experiment execution manual, including
   baselines, registration, characterization, cleanup, and acceptance checks.
7. [`07-kv-cache-workload-development-plan.md`](07-kv-cache-workload-development-plan.md)
   defines the KV-cache trace model, replay modes, metrics, and hardware scope.
8. [`08-kv-cache-workload-results.md`](08-kv-cache-workload-results.md)
   records the completed two-node KV-cache workload, phase-four acceptance,
   and remote transparent-overhead distribution analysis.
9. [`09-kv-cache-hardware-smoke-followup.md`](09-kv-cache-hardware-smoke-followup.md)
   is the portable next-step execution plan for the two-node hardware smoke.
10. [`10-public-trace-storage-baseline.md`](10-public-trace-storage-baseline.md)
    records the matched durable FAST'25 public-trace storage-path smoke, its
    raw evidence, and the boundary between whole-path and transport claims.
11. [`11-kv-cache-size-sweep-results.md`](11-kv-cache-size-sweep-results.md)
    records the repeated 16--256 KiB two-node size sweep, paired transparent
    overhead, descriptor invariants, and the root-Master heartbeat fix.
