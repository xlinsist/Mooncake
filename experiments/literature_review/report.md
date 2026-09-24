# 面向本课题的相关工作调研

## 结论先行

基于 `experiments/intro.md` 的两个核心问题——**本地 NVMe 与远端 NVMe-oF 的 placement transparency**，以及跨后端的**统一、可恢复对象生命周期**——最相关的工作不是单纯的 KV-cache 压缩或 GPU 内存分页，而是以下四组：

1. **直接系统先例：Mooncake Store、LMCache、CachedAttention、MemServe。** 它们已经把 KV cache 从 GPU 内存扩展到 CPU/SSD/远端存储，并涉及对象 API、异构层次、迁移、恢复或故障处理。
2. **生命周期与恢复：HCache、Pensieve、ServerlessLLM。** 它们说明了状态持久化、恢复调度、分层 checkpoint 和重启/迁移的重要性，但通常不提供本课题所需的“同一对象接口下本地 NVMe/NVMe-oF 后端透明切换”。
3. **I/O 与计算协同：CacheGen、CacheBlend、Cake、InfiniGen。** 这些工作解决传输、预取、部分重算和 compute/I/O overlap，能直接支撑本文的性能动机，但重点不是跨后端元数据语义。
4. **容量扩展基线：FlexGen、vLLM/PagedAttention、LLM in a Flash，以及 NVMe I/O characterization。** 它们量化了分页、GPU/CPU/disk 聚合和 SSD I/O 的基础代价，是实验基线和设计边界的重要依据。

**最接近的论文排序（针对本文的 novelty，而非总体影响力）：**
`Mooncake` > `LMCache` > `CachedAttention` > `MemServe` > `HCache` > `Cake` > `ServerlessLLM` > `CacheGen` > `Pensieve` > `FlexGen` > `vLLM`。其中前四篇应在 related work 中重点正面对比；本文应明确指出：它们主要缺少跨本地/远端 NVMe 的统一 Store 内部 placement，以及带 prepare/commit/rollback、持久副本描述和重启路由恢复的对象生命周期契约。

## 检索方法与证据边界

- 采用 OpenAlex-first：先用 OpenAlex Works API 按拆分关键词召回，再通过 DOI、arXiv、USENIX 或 ACM landing page 核对论文信息和摘要。
- 检索日期截至 2026-08-25；OpenAlex 组合长查询返回 0，因此改用 `KV cache offloading`、`disaggregated KV cache`、`NVMe LLM inference`、`KV cache reuse serving` 等短查询。检索记录和 GUIDE 位于本目录。
- 年份、DOI、作者、摘要来自 OpenAlex；会议/月份优先以 Crossref 或正式会议页面为准。硬件架构字段只在论文明确给出时填写，未明确处标为“未统一披露”，不把测试平台推断成贡献本身。
- `LMCache` 的正式论文条目在当前检索日期仍主要以 2025 arXiv 版本可得；`Cake`、`Dual-Blade` 等较新的条目也应标注预印本/时间状态，避免与已同行评审工作混写。

## 结构化论文表

| 相关性 | 论文（年份/月，venue） | 第一作者 / 机构 | GPU / 存储硬件 | 技术与层级 | Abstract（摘要要点） | TLDR |
|---|---|---|---|---|---|---|
| **直接** | [Mooncake: A KVCache-centric Disaggregated Architecture for LLM Serving](https://doi.org/10.48550/arxiv.2407.00079)（2024/06 arXiv；2025 TOS） | Ruoyu Qin / Moonshot AI | A800/H800；CPU、DRAM、SSD、RDMA NIC | KV-cache-centric scheduler、分布式 cache pool、`put/get/change_replica`、拓扑感知 RDMA 路由；**Store/集群层** | 将 prefill/decode 解耦，并利用 GPU 集群闲置 CPU/DRAM/SSD 建立 KV cache 池；对象以 paged block、hash key 和副本元数据管理，提供对象 API 和临时网络故障下的备用路径。 | 与本文最接近的系统基线；但公开设计主要是分布式 RDMA cache pool，本文进一步统一本地 NVMe 与 NVMe-oF 后端及其可恢复生命周期。 |
| **直接** | [LMCache: An Efficient KV Cache Layer for Enterprise-Scale LLM Inference](https://doi.org/10.48550/arxiv.2510.09665)（2025/10 arXiv） | Yuhan Liu / University of Chicago 等 | GPU、CPU DRAM/本地或远端磁盘、S3、Ethernet/RDMA/NVLink | 可插拔 KV connector、chunk 化批量搬运、zero-copy、GPU/I/O pipeline、多层存储 API；**KV-cache layer** | 支持跨 vLLM/SGLang 的 GPU、CPU、磁盘、远端存储和对象存储；以 connector 解耦推理引擎，以控制 API 进行 locate/move/pin/compress。 | 在“统一 KV 层接口”和异构 tier 支持上很接近；需要重点对比本文更强的后端 placement 隐藏、提交/撤销和重启路由恢复语义。 |
| **直接** | [Cost-Efficient Large Language Model Serving for Multi-turn Conversations with CachedAttention](https://doi.org/10.48550/arxiv.2403.19708)（2024/03 arXiv） | Bin Gao / University of California 等 | GPU、host DRAM、SSD（含远端存储讨论） | scheduler-aware fetch/evict、layer-wise preload、异步保存；**推理调度 + 分层 KV 存储** | 通过 DRAM/SSD 层次保存会话 KV cache，按调度器提示把将访问的 cache 提前提升到快层，并将读写与 GPU 计算重叠。 | 证明了“异构存储 + 调度器感知”有收益；但 placement 逻辑仍是缓存策略，不是透明对象 Store 的后端无关契约。 |
| **直接** | [MemServe: Context Caching for Disaggregated LLM Serving with Elastic Memory Pool](https://doi.org/10.48550/arxiv.2406.17565)（2024/06 arXiv） | Cunchen Hu / Microsoft Research 等 | 分布式 CPU/GPU memory pool、网络 | MemPool `insert/match/delete/evict/transfer`、全局 prompt-tree locality、实例故障释放；**集群内存/缓存层** | 将跨请求 context caching 与 prefill/decode disaggregation 统一到弹性内存池；cluster manager 通过 heartbeat 发现实例故障并释放其内存块。 | 相关于统一资源池和失败清理，但没有本文针对本地 NVMe/NVMe-oF 的持久副本描述和客户端重启恢复。 |
| **直接** | [Fast State Restoration in LLM Serving with HCache](https://doi.org/10.1145/3689031.3696072)（2025/03，EuroSys） | S. Y. Gao / Tsinghua University | A100；4× PM9A3 NVMe SSD；可退化到 host DRAM | bubble-free restoration、hidden-state/KV 分层保存、64-token chunk、SSD round-robin、两阶段写入、GDS；**恢复调度 + 存储管理** | 将历史状态保存到 SSD，通过隐藏状态恢复、token recomputation 与 KV offload 填充流水线气泡；后台线程聚合小写入并用 GPUDirect Storage 读取。 | 是本文生命周期/恢复实验的强相邻工作；后端默认为 SSD/DRAM，未解决本地与远端 NVMe 统一对象语义。 |
| **直接** | [Stateful Large Language Model Serving with Pensieve](https://doi.org/10.1145/3689031.3696086)（2025/03，EuroSys） | Lingfan Yu / New York University | GPU + CPU memory（论文版本未统一披露 NVMe 后端） | token-level GPU/CPU cache swap、按重算代价选择丢弃、pipelined recovery；**请求/缓存管理层** | 将多轮会话从无状态处理改为状态化服务，按 token 粒度在 GPU 与 CPU 间换入换出，必要时重算。 | 生命周期“保存—恢复—丢弃”视角有参考价值，但存储拓扑和持久化元数据不是其重点。 |
| **相邻** | [CacheGen: KV Cache Compression and Streaming for Fast LLM Serving](https://doi.org/10.1145/3651890.3672274)（2024/08，SIGCOMM） | Yuhan Liu / University of Chicago | GPU、CPU/网络存储 | KV cache 压缩、分块流式传输、带宽感知；**传输/编码层** | 针对跨网络复用 KV 的高传输延迟，用压缩和 streaming 降低网络代价。 | 解释远端 KV 传输为何需要 chunk/stream，但不研究后端透明 placement 或对象提交。 |
| **相邻** | [CacheBlend: Fast Large Language Model Serving for RAG with Cached Knowledge Fusion](https://doi.org/10.48550/arxiv.2405.16444)（2024/05 arXiv；2026 TOCS） | Jiayi Yao / University of Chicago 等 | GPU、CPU、较慢本地/远端存储 | 选择性 token 重算 + cache retrieval/重算 pipeline；**请求/预填充层** | 允许非前缀 RAG chunk 复用 KV，并把少量重算与 KV 读取重叠，使 cache 可放在更慢但更大的存储。 | 直接支持“慢后端可被 overlap 隐藏”的论点；不提供跨后端一致生命周期。 |
| **相邻** | [Compute Or Load KV Cache? Why Not Both? (Cake)](https://doi.org/10.48550/arxiv.2410.03065)（2024/10 arXiv） | Shuowei Jin / 论文作者机构以预印本披露为准 | GPU、CPU、local disk、remote storage | bidirectional compute/I/O scheduling、chunk-level adaptive scheduling；**KV loading 调度层** | 根据计算和 I/O 吞吐动态决定 KV 重算或加载，并使二者并行；报告平均 TTFT 下降 2.6×。 | 是本文评估“透明层额外开销可被请求序列摊薄”的重要调度参照，但不改变存储对象语义。 |
| **相邻** | [InfiniGen: Efficient Generative Inference of LLMs with Dynamic KV Cache Management](https://doi.org/10.48550/arxiv.2406.19707)（2024/06，OSDI） | Wonbeom Lee / Seoul National University 等 | GPU + host memory（offloading-based system） | 重要 KV entry speculation、selective prefetch；**层/ token 级恢复调度** | 只预取后续 attention 真正需要的 KV 条目，减少 host→GPU fetch，最高报告 3× 加速。 | 关注“读哪些对象/条目”，而本文关注“对象在哪个后端以及如何可靠提交”。 |
| **相邻** | [ServerlessLLM: Low-Latency Serverless Inference for Large Language Models](https://doi.org/10.48550/arxiv.2401.14351)（2024/01，OSDI） | Yao Fu / University of California, San Diego 等 | GPU server 的 GPU/CPU/本地盘 + remote checkpoint source | multi-tier checkpoint loading、locality-aware placement、live migration；**模型状态/实例调度层** | 利用本地近 GPU 存储和多级带宽降低 checkpoint 加载，并按 checkpoint locality 放置实例。 | 可借鉴“拓扑隐藏在系统调度层”的接口思想，但对象是模型 checkpoint 而非可频繁 put/get/remove 的 KV cache。 |
| **基础** | [FlexGen: High-Throughput Generative Inference of LLMs with a Single GPU](https://doi.org/10.48550/arxiv.2303.06865)（2023/03 arXiv） | Ying Sheng / Stanford University 等 | 单张 16GB GPU、CPU、disk | LP 优化 GPU/CPU/disk placement、权重/KV 4-bit 压缩；**端到端 offload planner** | 用线性规划在 GPU、CPU 和磁盘间安排 tensor，证明 disk 可作为容量扩展。 | 本文异构容量扩展的经典 baseline；其 placement 是全局离线规划，不是透明在线对象存储。 |
| **基础** | [Efficient Memory Management for LLM Serving with PagedAttention](https://doi.org/10.1145/3600006.3613165)（2023/10，SOSP） | Woosuk Kwon / University of California, Berkeley | GPU memory；具体 GPU 随实验配置 | page/block allocation、prefix sharing、近零碎片；**GPU KV allocator / serving engine** | 用分页虚拟内存思想管理动态 KV block，减少碎片并支持请求间共享。 | 解释统一对象接口上层为何应只管理 KV 对象/生命周期，而不暴露物理地址；不涉及 NVMe。 |
| **基础** | [LLM in a flash: Efficient LLM Inference with Limited Memory](https://doi.org/10.18653/v1/2024.acl-long.678)（2024/01，ACL） | Sehoon Kim / Apple | 移动设备 DRAM + flash | flash-aware weight streaming、窗口化访问、异步加载；**设备存储/执行层** | 通过 flash 访问和权重流式化在有限内存设备上执行大模型。 | 不是 KV-cache Store，但提供“持久 flash 进入推理数据路径”时的带宽/访问粒度背景。 |
| **基础** | [An I/O Characterizing Study of Offloading LLM Models and KV Caches to NVMe SSD](https://doi.org/10.1145/3719330.3721230)（2025/03，CHEOPS/ACM workshop） | Zebin Ren / Vrije Universiteit Amsterdam；IBM Research Europe | NVMe SSD；DeepSpeed、FlexGen | block-I/O trace characterization；**设备/I/O 层** | 比较 POSIX 与 libaio，发现 KV offload 以 128 KiB 读写为主，读取带宽显著高于写入，并给出公开 traces。 | 是本文 NVMe 微基准对象大小、绝对延迟和读写不对称性的直接实验依据；不是系统设计工作。 |

## 与本文的差异矩阵

| 工作 | 本地/远端后端同时存在 | 上层不指定后端 | 持久 placement 元数据 | prepare→commit/rollback | 客户端重启路由恢复 | 主要缺口 |
|---|---:|---:|---:|---:|---:|---|
| Mooncake | 部分（分布式 CPU/DRAM/SSD/RDMA pool） | 部分 | 有 block address/replica metadata | 未以跨 NVMe 对象契约为重点 | 未充分展开 | 本文聚焦 local NVMe/NVMe-oF 的统一 Store 语义 |
| LMCache | 是（local/remote disk、DRAM、S3） | 对 engine 是；对 storage backend 抽象程度需区分实现 | 有 connector/backend metadata | 论文重点是搬运与 API，未将 commit/rollback 作为核心贡献 | 未充分展开 | 本文提供更强的对象生命周期和重启恢复证据 |
| CachedAttention | DRAM + disk（含远端讨论） | 否，层次由 scheduler-aware policy 管理 | cache placement state | 否 | 否 | 缓存策略而非统一持久对象层 |
| MemServe | 分布式 memory pool | 通过 MemPool API | 有 index/instance state | 有实例故障释放，但非持久 NVMe 对象事务 | 有限 | 内存池/集群管理，不是 local/remote NVMe Store |
| HCache | SSD + DRAM fallback | 否，storage manager 默认 SSD | chunk/format metadata | 非核心 | 未展开 | 隐藏状态恢复和单机 SSD 优化 |
| Cake | local disk + remote storage | 面向加载策略 | cache index | 否 | 否 | compute/load 调度 |
| 本文 | **是，local NVMe + NVMe-oF** | **是，统一 put/get/remove** | **是，已提交副本描述** | **是，准备—写入—提交/撤销—删除** | **是** | 代价是需要证明透明抽象不掩盖关键拓扑性能差异 |

## 技术分类（taxonomy）

1. **地址/分配抽象**：PagedAttention 的 GPU page/block、Mooncake 的 block object、LMCache 的 chunk/connector。目标是隐藏碎片、布局和引擎 page size。
2. **层次 placement 与 eviction**：CachedAttention 的 scheduler-aware DRAM/SSD fetch/evict、FlexGen 的 LP placement、Mooncake 的副本和负载均衡、ServerlessLLM 的 locality-aware instance placement。
3. **传输与计算重叠**：CachedAttention 异步保存/预取、LMCache layer-wise pipeline、HCache bubble-free restoration、Cake compute-vs-load、CacheBlend selective recompute pipeline。
4. **选择性恢复/重算**：Pensieve 的 token-level swap/recompute、InfiniGen 的重要 KV speculation、Cake 的双向计算/I/O 选择、HCache 的 hidden-state restoration。
5. **传输压缩与编码**：CacheGen 的 KV 压缩/streaming；可与本文后端透明层正交组合。
6. **生命周期与故障语义**：MemServe 的实例故障释放、Mooncake 的网络备用路径，以及本文新增的已提交副本可见性、失败撤销、资源释放和重启路由恢复。这个维度在现有 KV-cache 工作中明显欠缺系统化定义。

## 系统/编译器比较（按本课题适用的“上层调用方—存储层边界”）

| 系统 | 上层抽象 | 存储后端 | 调度粒度 | 对本文启示 |
|---|---|---|---|---|
| vLLM/PagedAttention | paged KV block | 主要 GPU memory | token/block/request | 上层应通过逻辑 block/object 操作，不暴露 NVMe 地址 |
| FlexGen/DeepSpeed | tensor/offload plan | GPU + CPU + disk | layer/tensor/batch | 说明资源优化可全局规划，但不满足在线透明 backend selection |
| CachedAttention | context cache + scheduler hints | GPU/DRAM/SSD | layer/job/cache | placement 可由 scheduler 触发，但应进一步下沉至 Store 以解除拓扑耦合 |
| Mooncake Store | object `put/get/change_replica` | distributed DRAM/SSD/RDMA | block/object/replica | 与本文接口最接近；本文把 local NVMe/NVMe-oF 选择和对象事务收敛到 Store |
| LMCache | connector + control API | GPU/CPU/disk/S3/network | chunk/layer/transfer batch | 说明稳定 connector 是跨快速演化 inference engine 的关键；本文补充 storage-backend transaction contract |
| HCache | restoration request/scheme | SSD/DRAM | layer/chunk/token | 说明持久化保存和恢复应脱离 GPU critical path |

这里的“编译器比较”不是指传统 GPU kernel compiler 的指令调度，而是本引言实际涉及的 inference runtime/storage compiler boundary：现有系统的差异主要在 KV layout、chunking、placement 和 transfer scheduling；本文贡献位于更下层的 Store backend abstraction，不声称改进 Triton/CUDA kernel compiler。

## 研究趋势

- KV cache 正从 GPU-local transient buffer 变为跨请求、跨实例、跨设备的持久/共享状态。
- 研究从“能否 offload”转向“何时计算、何时读取、读取多少，以及如何把 I/O 隐藏在 GPU 计算后面”。
- 存储层由 CPU DRAM 扩展到本地 NVMe、远端盘、对象存储和 RDMA/NVLink 组合；因此 topology-aware routing 与 backend-independent APIs 逐渐重要。
- chunk/block 粒度成为共同设计点：它同时影响 SSD 带宽、GPU DMA、碎片、缓存命中和 metadata 开销。
- 失败恢复仍多以网络重试、实例释放或重新计算处理；跨后端对象的原子可见性、持久副本描述和重启后路由恢复尚未形成共同抽象。

## 开放问题与本文定位

1. **透明性与可观测性能的矛盾**：上层不应指定后端，但应能获得 latency/bandwidth/health hints，否则 Store 无法做合理 placement。
2. **事务语义**：如何在本地写入、NVMe-oF 写入、Master 元数据提交和客户端可见性之间定义原子边界？失败/超时/重复提交如何幂等？
3. **恢复与一致性**：客户端重启、Master 重启、远端 target 暂时不可用时，哪些 metadata 是权威的，如何避免“元数据已提交但数据未完整写入”？
4. **跨后端删除与回收**：删除应先撤销逻辑副本还是先回收物理空间？并发 `get/remove` 如何避免读到待删除或半写对象？
5. **自适应多路径**：local NVMe 通常低延迟，NVMe-oF 通常提供独立容量/带宽；需要基于队列深度、网络拥塞、对象热度和 SLO 的在线策略，而非固定 round-robin。
6. **评估边界**：顺序轨迹回放能刻画 Store 路径开销，但不能替代并发 serving、GPU 执行、网络拥塞和故障注入实验；本文应把这些边界明确写出。
7. **与上层优化组合**：压缩、量化、选择性预取和部分重算都可叠加，但其 metadata/schema 变化不能破坏后端透明和重启恢复。

## 建议在论文中如何引用

- 第一段 KV cache 容量/分页动机：`PagedAttention/vLLM`、`FlexGen`。
- “已有系统使用多级/分离存储”：`Mooncake`、`LMCache`、`CachedAttention`、`MemServe`。
- “I/O 不是简单 append，需重叠/选择性恢复”：`HCache`、`CacheBlend`、`Cake`、`InfiniGen`、`CacheGen`。
- “NVMe 实际代价和 128 KiB I/O 特征”：`Ren et al.` 的 NVMe characterization。
- Novelty 边界应直接写成：已有工作管理的是**缓存策略、传输路径或分布式资源池**；本文研究的是**对上层透明的 local-NVMe/NVMe-oF 对象 Store，以及跨后端一致、可恢复的生命周期语义**。

## 面向文件系统/NVMe-oF 的近三年 A 会补充

**证据审计说明：**下方新增表按“相关性/启示/边界”组织；逐篇的 Title、Year/Month、Venue、First Author、Affiliation、GPU/存储硬件、Technique、Level、Abstract、TLDR 完整字段见紧接其后的“严格窗口内论文的结构化条目”。USENIX 官方页面已核验 CETOFS、RASK、HiDPU、Ananke、NVLog、SquirrelFS、Ethane、D2FS、GeminiFS 和 OrchFS 的作者、venue、月份与摘要；OpenAlex 仅用于召回，不作为 venue 的唯一证据。

针对 ASPLOS/MLSys 的 OpenAlex 短查询未发现比主表更直接的、已正式发表的 local-NVMe/NVMe-oF 文件系统论文；召回的 `MirrorKV`、`CaaS-LSM`、`DataStates-LLM` 等属于 hybrid-cloud KV/LLM 状态管理，作为相邻工作而非 NVMe-oF 文件系统基线。该结果是检索证据，不是“不存在”的证明。

KV-cache 论文之外，下面几项是对 C1--C3 更有解释力的系统先例。它们不一定以 LLM 为目标，但直接触及远端块设备的数据路径、地址管理、元数据边界和恢复语义。会议归属应以最终出版版本为准；OpenAlex 对部分系统论文只返回题名和预印本记录，因此这里明确标出证据强度。

### 严格窗口内论文的结构化条目

| Title | Year / Month; Venue | First author / affiliation | GPU architecture / storage hardware | Scheduling technique | Scheduling level | Abstract | TLDR |
|---|---|---|---|---|---|---|---|
| CETOFS: A High-Performance File System with Host-Server Collaboration for Remote Storage | 2026/02; FAST’26 | Jia et al.; affiliation per USENIX author list | NVMe-oF/RDMA remote NVMe SSD; accelerator not required | Host/server split, server-side permission and concurrency control, failure-atomic remote I/O | filesystem/storage runtime | A userspace data path plus kernel control path collaborates with the remote server to reduce remote I/O overhead while preserving file-system semantics. | Closest filesystem precedent for C1+C2. |
| “Range as a Key” is the Key! (RASK) | 2026/02; FAST’26 | Zhao et al.; affiliation per USENIX author list | Cloud block-store nodes; accelerator not required | Range-key mapping index with split/merge and range-aware GC | storage metadata/index layer | Compresses LBA-to-location metadata and handles overlap and fragmentation in a cloud block store. | Strong C3 baseline for compact address-space metadata. |
| HiDPU: A DPU-Oriented Hybrid Indexing Scheme for Disaggregated Storage Systems | 2025/02; FAST’25 | Zhu et al.; affiliation per USENIX author list | DPU-assisted disaggregated storage; accelerator is DPU, not GPU | Hybrid accurate/learned index and two-phase asynchronous index update | storage metadata/index layer | Reduces memory and CPU cost of logical-to-physical translation while keeping DPU and host indexes consistent. | Direct evidence that mapping metadata is a first-class bottleneck. |
| Ananke: Fast, Transparent Filesystem Microkernel Recovery | 2025/02; FAST’25 | Jing Liu; Microsoft Research / University of Wisconsin–Madison | Commodity filesystem storage; GPU not required | Process-crash log plus on-disk journal recovery | filesystem lifecycle/recovery layer | Records in-flight syscall state and restores it transparently after a filesystem-service crash, with lossless recovery in extensive fault injection. | Reference for transparent recovery, but not network failure recovery. |
| NVLog: A Transparent NVM Write-ahead Log for Disk File Systems | 2025/02; FAST’25 | Wang et al.; affiliation per USENIX author list | NVM plus disk; GPU not required | Byte-granular sync absorption, journaling, recovery and GC | filesystem persistence layer | Adds a transparent NVM WAL beneath legacy disk filesystems and optimizes sync-heavy workloads while retaining crash recovery. | Useful C2 precedent for separating logical commit from backend persistence. |
| SquirrelFS: Using the Rust Compiler to Check File-System Crash Consistency | 2024/07; OSDI’24 | LeBlanc et al.; affiliation per USENIX author list | Persistent-memory filesystem; GPU not required | Rust typestate and synchronous soft updates enforce metadata ordering | filesystem metadata/consistency layer | Encodes crash-consistency ordering in types so invalid metadata update sequences are rejected at compile time. | Shows how C2 invariants can be made explicit and checkable. |
| Ethane: An Asymmetric File System for Disaggregated Persistent Memory | 2024/07; USENIX ATC’24 | Cai et al.; affiliation per USENIX author list | Disaggregated persistent memory; GPU not required | Control/data-plane separation and locality-aware access | filesystem architecture layer | Separates metadata/control operations from data-plane operations to match asymmetric disaggregated resources. | Strong C1/C3 architectural analogy; transport differs from NVMe-oF. |

| 相关性 | 论文（年份，venue） | 对 C1--C3 的直接启示 | 与本文的边界 |
|---|---|---|---|
| **直接** | [**RIO: Order-Preserving and CPU-Efficient Remote Storage Access**](https://doi.org/10.1145/3552326.3567495)（2023/05，EuroSys；严格三年窗口外的边界基础工作） | 研究 NVMe-over-RDMA 远程存储访问的数据路径和请求重排，在保持顺序语义的同时降低 CPU 开销；说明 remote block access 需要 runtime 层承担路由、队列和完成顺序（C1/C3）。 | 关注访问机制，不定义 KV object 的持久 metadata 或跨 local/remote 的生命周期事务。 |
| **直接** | [**CETOFS: A High-Performance File System with Host-Server Collaboration for Remote Storage**](https://www.usenix.org/conference/fast26/presentation/jia)（2026，FAST） | 明确面向 NVMe-oF/RDMA 远端 SSD；用户态数据面与内核控制面协作，将权限检查、并发控制和 failure-atomic I/O 下沉远端 server，是 C1/C2 最接近的文件系统先例。 | 重点是远端存储数据路径；没有本文 local-NVMe 与 remote-NVMe 的统一 placement 和 KV object API。 |
| **直接** | **HiDPU: A DPU-Oriented Hybrid Indexing Scheme for Disaggregated Storage Systems**（2025，FAST） | 直接处理 disaggregated storage 中逻辑地址到物理地址的索引、索引内存占用和异步更新一致性；是 C3（metadata/address-space）的强基线。 | 面向 DPU/索引基础设施，不定义 KV object 的跨后端生命周期。 |
| **直接** | **Volley: Accelerating Write-Read Orders in Disaggregated Storage**（2024，EuroSys） | 研究 NVMe-oF 类 disaggregated storage 中 writeback cache 的写后读顺序与并发利用率；可作为 C1 路径透明和 C2 可见性/ordering 的参照。 | 优化 I/O ordering，不定义跨后端对象事务、删除回收或重启路由恢复。 |
| **直接** | **Disaggregating RocksDB: A Production Experience**（2023，SIGMOD） | 将计算与存储解耦后，缓存、compaction、故障恢复和一致性边界必须显式重做；适合支撑“透明化把复杂性下沉到存储层”的论点（C1--C3）。 | 面向 LSM/KV 数据库，元数据和 compaction 语义不同于短生命周期 KV cache。 |
| **直接** | **Ethane: An Asymmetric File System for Disaggregated Persistent Memory**（2024，USENIX ATC） | 将控制面文件系统与数据面文件系统分离，以适应 disaggregated persistent memory 的非对称资源；支撑 C1 的拓扑隐藏与 C3 的控制/数据元数据边界。 | 持久内存互连与 NVMe-oF 不同，不能直接外推其一致性和故障模型。 |
| **相邻** | **DDS: DPU-Optimized Disaggregated Storage**（2024，PVLDB） | 用 DPU 将远端存储数据路径和管理操作下沉，展示 topology-independent access 可以通过中间层实现，同时要处理资源隔离和元数据开销（C1/C3）。 | DPU 优化的是块存储基础设施，未定义 inference-facing object lifecycle。 |
| **边界基础** | **eZNS: An Elastic Zoned Namespace for Commodity ZNS SSDs**（2023/07，OSDI） | 以 zone arbiter 和分层调度器统一逻辑 zone 分配、拥塞控制和回收；说明设备地址空间和上层抽象之间需要显式控制面（C1/C3）。 | 早于严格的 2023-09-09 三年窗口；面向本地 ZNS SSD，不涉及 NVMe-oF 多 target 的路由与副本恢复。 |
| **相邻** | **Prism: Optimizing Key-Value Store for Modern Heterogeneous Storage Devices**（2023，OpenAlex 收录；正式 venue 需核对） | 针对异构设备的 KV layout、tier placement 和访问特征做联合优化，说明“一个 KV API”并不自动带来统一性能，需要设备-aware policy（C1/C3）。 | 主要是设备内 placement/layout 优化，没有 NVMe-oF 多节点副本提交和恢复协议。 |
| **相邻** | **Disaggregated RAID Storage in Modern Datacenters**（2023，系统论文/venue 以正式版本为准） | 远端盘池的条带化、重建和故障域管理说明地址空间不仅是 `device:LBA` 映射，还包含副本/条带元数据与恢复状态（C3）。 | RAID 可靠性目标强于 cache store；本文可借鉴 metadata state machine，但不应直接声称功能等价。 |
| **相邻** | [**“Range as a Key” is the Key! Fast and Compact Cloud Block Store Index with RASK**](https://www.usenix.org/conference/fast26/presentation/zhao)（2026，FAST） | 用 range-key 索引维护 cloud block store 的 LBA 到后端位置映射，并处理分裂、合并、GC 和碎片；是 C3 地址空间元数据的直接参照。 | 面向云 block store 索引，未涉及 KV 生命周期或 local/remote 统一 API。 |

### C1--C3 的综合结论

1. **C1（backend transparency）**：Mooncake/LMCache 给出了 inference 侧的统一 API；CETOFS、RIO、DDS 说明要把路径、队列和 CPU/DPU 处理隐藏起来，同时保留权限与性能观测边界。透明 API 本身不保证正确 placement，策略仍需明确资源视图与故障模型。
2. **C2（unified object lifecycle）**：HCache、MemServe、Ananke、NVLog 和 CETOFS 描述保存、恢复、释放及 crash-consistent/failure-atomic commit；Volley 与 Disaggregating RocksDB 补充了写后读可见性、ordering、compaction/recovery 边界。本文应把 `prepare → data write → metadata commit → visible → delete/reclaim` 写成显式状态机，并定义超时、重复提交和回滚。
3. **C3（metadata/address-space management）**：PagedAttention 隐藏 GPU page，Mooncake 隐藏 block/replica，HiDPU 处理 disaggregated mapping index，RIO/DDS 隐藏远端请求路径；文件系统方向的真正空缺是统一维护 `object_id → backend → node/target → namespace/device → extent/LBA → version/checksum`，并在 target 不可达或客户端重启后重建可路由状态。

### A 会优先阅读顺序

若时间有限，建议按 **CETOFS → RASK → HiDPU → Volley → Ethane → Ananke/NVLog → Mooncake/HCache** 阅读：前四项最能补足 NVMe-oF 文件系统的数据路径、地址映射、ordering 和 metadata 视角，Ananke/NVLog 补足恢复与提交语义，后两项连接到 KV-cache workload。FAST'25 官方页面还提供 **GeminiFS、D2FS、OrchFS、DJFS、ScaleLFS**，分别对应 GPU/NVMe 透明接口、设备/文件系统联合回收、统一 mapping、目录级事务和并发 GC，可作为 C1--C3 的扩展阅读。Volley 的正式出处是 EuroSys 2024（DOI: 10.1145/3627703.3650090），RIO 的正式出处是 EuroSys 2023（DOI: 10.1145/3552326.3567495）；两者都不是 FAST/OSDI。
