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
   baselines, registration, characterization, failure injection, cleanup, and
   acceptance checks.
