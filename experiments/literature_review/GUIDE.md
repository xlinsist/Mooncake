You are helping with a research literature survey.

Topic:
Transparent heterogeneous storage for LLM KV cache across local NVMe and remote NVMe / NVMe-oF.

Focus on:
- KV cache offloading, persistence, reuse, and disaggregation
- transparent placement across local and remote storage tiers
- unified object lifecycle, metadata commit, rollback, recovery, and deletion
- local NVMe, remote NVMe, NVMe-oF, and distributed object stores
- topology-independent interfaces and storage-aware cache management

Relevant systems include:
Mooncake, LMCache, vLLM, FlexGen, DeepSpeed-FastGen, InfiniGen, CachedAttention,
MemServe, CacheGen, KVDirect, P/D-Serve, DistServe, Splitwise, ServerlessLLM,
LlamaServe, DéjàVu, Pensieve, Symphony, G10, and GPUfs.

Tasks:

0. Verify OpenAlex access with the bundled script.
1. Search for relevant papers through August 2026, while retaining older foundational work where it directly informs transparent heterogeneous storage.
2. Prioritize peer-reviewed systems and ML systems venues, then strong arXiv or industry system reports.
3. Rank papers by their relationship to the proposed design rather than by citation count alone.
4. Separate direct prior work from adjacent KV-cache systems and foundational heterogeneous-storage mechanisms.

OpenAlex-first search policy (mandatory):

- Use OpenAlex as the primary index for recall (papers + metadata), then follow through to the best landing page (venue / arXiv / publisher) for details.
- When OpenAlex misses a system/paper name (indexing lag happens), fall back to direct web search and then re-query OpenAlex by DOI/arXiv id if available.

For each paper produce a structured table containing (mandatory):

- Title
- Year / Month
- Venue
- First Author
- Affiliation
- GPU Architecture
- Scheduling Technique
- Scheduling Level
- Abstract
- TLDR

For this storage-focused survey, interpret the last three inherited fields as follows:

- GPU Architecture: evaluated accelerator and storage/network hardware.
- Scheduling Technique: placement, offload, caching, routing, or lifecycle technique.
- Scheduling Level: inference engine, KV-cache manager, storage layer, or device/runtime layer.

Then provide (mandatory):

- A taxonomy of scheduling techniques
- A comparison between different compiler systems
- Key research trends
- Open problems

For this storage-focused survey, the taxonomy covers KV-cache/storage management techniques,
and the comparison covers inference and storage systems rather than compiler systems.
