# Public-trace Store capacity sweep

The 16/64/256-page FAST'25 Store sweep passed all 90 planned cases:

- conversation/toolagent, 500 requests, three trials per capacity;
- five matched no-store/direct/transparent local/remote cases per cell;
- 778,056 puts, 83,316 gets, and 778,056 removes;
- 112,901,750,784 modeled payload bytes across puts and gets;
- all matrix, descriptor, recovery, and hardware restoration gates passed.

Toolagent block hit rate rises from 10.92% to 26.87%. Its remote request-p50
overhead falls from 11.65% to 2.68%, while request-p95 remains 11.17--13.77%
and storage-wait overhead remains 9.57--11.69%. Capacity therefore amortizes
the center request cost for a reuse-rich trace without removing tail or total
storage-wait overhead.

`capacity-conclusion.json` is the unified acceptance result. The two raw
archives contain c16/c256 evidence; c64 raw evidence remains in the preceding
`20260823T205337Z-public-trace-store-r500` artifact. CSV files preserve all
three-capacity medians and three-trial ranges.
