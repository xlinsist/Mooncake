《TransCache: Backend-Transparent KV Cache Storage across Local and Remote NVMe》
> 更保守的候选：TransCache: A Transparent Storage Layer for KV Cache across Local and Remote NVMe
> 实现动态调度后的候选：《Unifying Local and Remote NVMe for KV Cache Storage》

# Abstract

下面这个版本严格按照刚才的逻辑链写，每一句都承担一个明确的推进作用，而不是简单罗列背景、挑战和贡献。

**Backend-Transparent Storage for KV Cache across Local and Remote NVMe**

Large language model (LLM) inference increasingly relies on large key-value (KV) caches, making persistent storage an important component of cache eviction and recovery when GPU memory becomes insufficient. Local NVMe SSDs provide low-latency node-local storage but are constrained by the capacity and I/O resources attached to individual inference nodes, whereas NVMe over Fabrics (NVMe-oF) accesses remote NVMe resources at the cost of additional network and protocol overhead while decoupling storage resources from individual nodes. These complementary properties motivate deployments that use both local and remote NVMe, requiring the storage system to determine where KV objects are placed and how subsequent accesses are routed. However, existing designs often expose backend-specific placement and topology information to storage clients, coupling upper-layer logic to the current storage deployment and requiring caller-side configuration or logic changes when the deployment or placement policy changes. 

We therefore present **[System Name]**, a backend-transparent storage layer for KV Cache that allows deployment-specific policies to select local or remote NVMe while exposing a unified object interface to upper layers. To hide backend differences, **[System Name]** internalizes backend selection, resource allocation, location representation, and access routing through configuration-driven placement and metadata-based routing. To preserve consistent semantics across heterogeneous paths, it further defines a unified object lifecycle that provides common commit, abort, removal, and metadata-based recovery behavior across local and remote storage backends. 

Our paired experiments on the same backend show that the transparent layer adds at most **5.79%** median latency for local writes across 16–256 KiB objects, while remote writes incur **13.99%–19.78%** additional latency, corresponding to only **0.0382 ms** at 128 KiB; remote reads and removals add just **0.0001 ms** and **0.0003 ms**, respectively. We also validate consistent object lifecycle behavior across local and remote backends under failures and client restarts. Replaying representative request traces shows median request-latency increases of **2.47%–8.42%** for local storage and **11.95%–12.08%** for remote storage. These results show that local and remote NVMe can be managed behind a stable KV Cache storage interface without requiring upper-layer software to encode backend-specific placement or access logic.

---

我觉得这个摘要的主线是比较顺的：

> **KV Cache 变大**
> 
> → **需要 persistent storage**
> 
> → **local 和 remote NVMe 有互补性**
> 
> → **因此一个 deployment 可能同时使用两者**
> 
> → **需要做 placement/routing**
> 
> → **但现有系统把 backend/topology 泄漏给调用方**
> 
> → **deployment 变化导致 caller coupling**
> 
> → **因此需要 backend-transparent abstraction**
> 
> → **透明化需要解决 placement 和 lifecycle**
> 
> → **设计两个对应机制**
> 
> → **实验验证 overhead + correctness + workload behavior**

其中我尤其建议保留最后一句的边界：

> **without requiring upper-layer software to encode backend-specific placement or access logic**

因为它非常准确地定义了你的论文到底解决了什么。

---


现有系统普遍在尝试隐藏 KV cache 的物理布局和部分存储层次，但尚未普遍提供对 local NVMe 与 remote NVMe-oF 的统一、后端无关且可恢复的对象存储语义。



《TransCache: Backend-Transparent KV Cache Storage across Local and Remote NVMe》

作为KV Cache Offloading 存储后端的本地 NVMe 具有较低的访问延迟，而远程 NVMe-oF 能提供可共享、可扩展的存储资源，两者的资源属性可以互补。然而，现有的 KV Cache 存储系统如 Mooncake (FAST '25)、LMCache (arXiv '25)、HCache (EuroSys '25) 往往需要显式感知存储后端，导致系统要么无法灵活分配存储资源，要么增加维护后端的复杂度和扩展成本。

对此，我们设计了一个后端透明的 KV Cache 存储层，将本地和远程 NVMe 的后端选择、资源分配、访问路由和对象生命周期统一封装，使上层只通过统一接口访问 KV Cache 对象，而无需感知其实际存储位置和底层后端。实验表明，该透明层面向本地路径的额外开销较低，16–256 KiB 写入中位延迟相比无透明层最多高 5.79%；面向向远程路径的额外开销也在可接受范围内，远程写入增加 13.99%–19.78%，其中 128 KiB 写入仅增加 0.0382 ms，且能保持正确的存储语义。

> We make local and remote NVMe transparent to KV Cache users by moving backend selection, routing, and lifecycle management into the storage layer, enabling heterogeneous storage resources to be used through a stable object interface with low overhead.
