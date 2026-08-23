# Public-trace Store matrix

Run `20260823T203957Z-public-trace-store` passed all 30 planned cases:

- FAST'25 conversation/toolagent, 100 requests, three trials;
- deterministic 64-page LRU and 128 KiB values;
- `no_store`, direct/transparent local, and direct/transparent remote;
- 54,228 puts, 5,568 gets, 54,228 removes, and exact descriptors;
- 7,837,581,312 modeled payload bytes across puts and gets;
- six passing cell conclusions and a passing final `round_robin` smoke.

Median transparent-minus-direct request p50 overhead spans 2.47--12.08%; p95
spans 4.48--11.78%. These are same-target sequential Store comparisons, not
true-concurrency, model-serving, or transport-only results.

The failed `20260823T203650Z` pilot is preserved separately. It exposed fixed
key reuse when an evicted page ID was produced again. Commit `0e317482` assigns
generation-safe keys; the formal matrix started from a new run directory.

`matrix-conclusion.json` is the acceptance result. `aggregate-summary.csv`,
`paired-trials.csv`, and `paired-aggregate.csv` are the review tables. The two
archives contain the complete formal run and failed pilot, including raw JSON,
logs, manifests, environment snapshots, controller scripts, and recovery data.
