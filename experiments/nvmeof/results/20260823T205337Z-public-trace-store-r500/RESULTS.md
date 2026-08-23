# Public-trace Store r500 matrix

Run `20260823T205337Z-public-trace-store-r500` passed all 30 planned cases:

- FAST'25 conversation/toolagent, 500 requests, three trials;
- deterministic 64-page LRU and 128 KiB values;
- `no_store`, direct/transparent local, and direct/transparent remote;
- 256,800 puts, 30,324 gets, 256,800 removes, and exact descriptors;
- 37,633,916,928 modeled payload bytes across puts and gets;
- six passing cell conclusions and a passing final `round_robin` smoke.

Median remote transparent-minus-direct storage-wait overhead is 11.25% for
conversation and 10.33% for toolagent. Local medians are 5.59% and 2.49%.
Request quantiles and their complete three-trial ranges are retained because
toolagent-local quantiles invert in two trials while storage wait increases and
event rate decreases in all three.

`matrix-conclusion.json` is the workload acceptance result and
`hardware-restoration.json` is the state-restoration result. The scale CSVs
compare this run with the 100-request artifact. The raw archive contains all
JSON, logs, manifests, environment snapshots, scripts, and recovery evidence.
