# AugeFS 论文阅读报告：元数据与持久化路径中的非必要共享

> **状态：阶段报告，尚未完成目标期刊论文的全文精读。** 用户指定的 ACM 期刊版 PDF 在本次环境中返回 HTTP 403；已核实其出版元数据与完整摘要，并取得、阅读了 MSST 2024 前作的相关正文。本报告严格区分两个版本。期刊版的完整目录、逐图逐表结果、新增贡献和最终审稿评价，须取得 41 页正式全文后补齐。

| 文档信息 | 内容 |
| --- | --- |
| 目标论文 | *A High-Performance and Scalable Userspace Log-Structured File System for Modern SSDs* |
| 研究系统 | AugeFS |
| 期刊 | ACM Transactions on Storage，21(4)，1–41，2025 |
| DOI | [10.1145/3728645](https://doi.org/10.1145/3728645) |
| 作者 | Wenqing Jia、Dejun Jiang、Jin Xiong |
| 精读日期 | 2026-09-12 |
| 用户指定来源 | [ACM ePDF](https://dl.acm.org/doi/epdf/10.1145/3728645) |
| 当前证据 | 期刊版 Crossref 元数据与摘要；MSST 2024 前作的动机、方法及部分实验正文 |
| 未完成项 | 期刊版全文核读；完整图表与算法盘点；各实验的设置、结果和证据强度核验 |

**当前可支持的核心理解：** AugeFS 试图解决的不是单独一个慢函数，而是快速 SSD 暴露出的三层问题：控制操作仍绕经内核或服务进程，元数据更新被通用 KV 引擎的组提交绑在一起，数据持久化又被共享状态与串行提交限制。其设计方向是让独立请求独立推进，同时在真正需要的地方保留顺序和持久性判断。这个解释与期刊版摘要一致；下文的具体实现依据来自会议前作，不能直接认定为期刊版最终实现。

**证据约定：**

- **[J] 期刊元数据／摘要**：来自目标 DOI 的 Crossref 记录，可用于确认题名、作者、时间线及摘要明确提出的设计。
- **[C] 会议前作**：*AUGEFS: A Scalable Userspace Log-Structured File System for Modern SSDs*，MSST 2024，16 页公开 PDF。页码指该 PDF 页序。
- **[分析]**：本报告对已读材料的解释、推导或待验证问题，不代表期刊论文已经证明。
- 本报告没有复现实验；所有性能数字均标注其版本和测试语境。

## 阅读目录

- [1. 标题深度分析](#section-1)
- [2. 基本信息与时间线](#section-2)
- [3. 作者和单位背景](#section-3)
- [4. 摘要与关键词](#section-4)
  - [4.1 期刊摘要完整提取](#journal-abstract)
  - [4.2 通俗解释](#plain-summary)
- [5. 章节组织与证据边界](#section-5)
- [6. 背景的作用](#section-6)
- [7. 动机与问题严重性](#section-7)
- [8. 方法逻辑：依据会议前作](#section-8)
  - [8.1 共享且受保护的地址空间](#shared-address-space)
  - [8.2 MetaDB 并行更新](#parallel-updates)
  - [8.3 细粒度持久日志](#fine-grained-wal)
  - [8.4 Domain 与异步 fsync](#domain-fsync)
- [9. 实验解读与期刊版待核项](#section-9)
- [10. 相关工作的比较坐标](#section-10)
- [11. 对本仓库研究的启发](#section-11)
- [12. 审稿问题、缺口与来源](#section-12)
  - [12.1 当前可作出的评价](#review)
  - [12.2 完成全文精读所需材料](#remaining)
  - [12.3 可追溯来源](#sources)

<a id="section-1"></a>
## 1. 标题深度分析

**A High-Performance and Scalable Userspace Log-Structured File System for Modern SSDs**

| 关键词 | 要解决的具体问题 | 阅读时需要追问 |
| --- | --- | --- |
| High-Performance | 减少单次文件操作的软件路径和持久化等待 | 收益是平均延迟、尾延迟，还是吞吐量？ |
| Scalable | 增加并发线程时，避免软件共享状态提前成为瓶颈 | 独立目录、共享目录和共享文件是否都扩展？ |
| Userspace | 把文件服务执行放到应用用户地址空间中的受控区域 | 注册、保护、崩溃清理仍依赖什么内核功能？ |
| Log-Structured | 通过追加写组织数据或元数据 | GC、checkpoint、空间不足时的成本如何？ |
| Modern SSDs | 低延迟、高带宽让软件串行部分更突出 | 实际测了哪些设备，PMR 是实物还是模拟？ |

**一句话概述：[J＋分析]** 通过重新组织控制路径、元数据日志和文件数据分区，让用户态日志结构文件系统更好地利用快速 SSD 的并行能力。

标题里的“可扩展”首先指单机文件系统的并发性能，不能据此推断为多机容量扩展、分布式一致性或远程存储扩展。

<a id="section-2"></a>
## 2. 基本信息与时间线

以下期刊信息来自 Crossref 的出版记录与 `assertion` 字段，而非根据网页更新时间猜测。

| 事件 | 时间 | 证据及限制 |
| --- | --- | --- |
| AugeFS 会议前作 | MSST 2024 | 官方会议页面列出题名、作者、论文和报告视频；不是本次指定的 41 页论文 |
| 期刊版收到稿件 | 2024-08-20 | [J] `received` |
| 期刊版接受 | 2025-03-26 | [J] `accepted` |
| DOI 记录创建 | 2025-04-09 | [J] `created`；不能当作投稿或正式发表日期 |
| 期刊版在线发表 | 2025-11-03 | [J] `published-online`，与 `published` assertion 一致 |
| 期刊卷期出版 | 2025-11-30 | [J] `published-print` |

**载体：[J]** ACM Transactions on Storage，卷 21、期 4、页码 1–41，ISSN 1553-3077 / 1553-3093。它是期刊论文，不是 MSST 会议论文。未核验特定年度影响因子或分级名单，故不填写。

**开放获取与开源是两件事。** OpenAlex 将目标文献标为开放获取、许可证 `cc-by`，且有 PDF 内容记录；这不意味着本环境能够直接下载。ACM 在当前网络中返回 403，OpenAlex 内容下载要求 API key。

**代码：[检索结果]** 本次未找到可验证的 AugeFS 官方代码仓库、首次开源日期或发布公告。GitHub 仓库关键词检索无结果不构成“作者没有开源”的证明。也未核实期刊文末是否另有 artifact 链接。

<a id="section-3"></a>
## 3. 作者和单位背景

| 作者 | 期刊记录中的单位 |
| --- | --- |
| Wenqing Jia | 中国科学院计算技术研究所处理器芯片全国重点实验室；中国科学院大学 |
| Dejun Jiang | 中国科学院计算技术研究所处理器芯片全国重点实验室；中国科学院大学；中关村实验室 |
| Jin Xiong | 中国科学院计算技术研究所处理器芯片全国重点实验室；中国科学院大学 |

会议前作首页还列有中国科学院计算技术研究所先进计算机系统研究中心。应保留版本之间的单位差别，不直接把会议首页覆盖到期刊作者表。

**可确认的研究脉络：** 三位作者共同署名 MSST 2024 AugeFS 前作；本仓库已有同作者 CetoFS 的 FAST 2026 阅读报告。这个脉络提示其研究关注文件系统的软件开销、并发控制和持久化，但不能据此推断两套系统代码同源，或期刊版具体新增了什么。

未取得足够可靠的作者个人主页材料；通讯作者身份、个人履历和作者自述研究目标暂不填写。

<a id="section-4"></a>
## 4. 摘要与关键词

<a id="journal-abstract"></a>
### 4.1 期刊摘要完整提取

以下为 Crossref 收录的期刊英文摘要全文，仅去除 XML 标签并规范空白，未省略句子。来源：[Crossref 原始记录](https://api.crossref.org/works/10.1145/3728645)。OpenAlex 将该文标注为 CC BY；此处明确署名原作者 Wenqing Jia、Dejun Jiang、Jin Xiong。

> We present AugeFS, a scalable userspace log-structured file system for modern SSDs. AugeFS re-architects the file system stack to address three critical challenges: inefficient control plane, limited metadata scalability, and underutilized device bandwidth. First, we propose a shared and protected address space within the userspace of accessing applications to run AugeFS, which enables the high-performance data plane and efficient control plane. Second, we design a scalable LSM-tree based key-value store called MetaDB to organize small-sized metadata in AugeFS. To improve the metadata scalability, MetaDB employs parallel request processing to reduce thread synchronization overhead and fine-grained parallel write-ahead log to eliminate false sharing in metadata persistence. Finally, AugeFS distributes files into different domains. To reduce contention, we maintain space management metadata for each domain independently, which helps scale data performance and improve the device IO utilization. Moreover, AugeFS designs an asynchronous IO stack for fsync to reduce the latency of synchronous writes. The evaluation results show that AugeFS significantly improves both metadata scalability and data scalability.

**关键词状态：** 期刊 Crossref 记录没有可用的关键词列表，不能声称已提取其正式关键词。会议前作首页的原文 `Index Terms` 为 **SSD, Userspace File System, Scalability**，仅作版本明确的补充。

<a id="plain-summary"></a>
### 4.2 通俗解释

**[J＋分析]** SSD 变快后，很多时间花在“谁来处理请求”“不同线程如何排队”“为了落盘必须等待谁”。AugeFS 让应用线程直接进入受保护的共享文件服务区域；让互不冲突的元数据更新各自执行、各自持久化；再把文件的数据管理分到不同 domain，减少全局争用。异步 `fsync` 则试图让数据写和索引写重叠，从而减少等待。

这里的关键不是“不需要同步”，而是把同步从所有请求共享的大范围，收缩到真实冲突、Memtable 切换和持久化确认等边界。能否在收缩同步后保持正确性，是全文精读最应核查的内容。

<a id="section-5"></a>
## 5. 章节组织与证据边界

**目标期刊版完整目录尚未取得。** 不能把下面的会议结构标成期刊目录，也不能默认两个版本的图号一致。

作为后续阅读导航，会议前作已核查的核心组织顺序为：

```text
I. Introduction
II. Background and Motivation
    A. Modern SSDs and Userspace LFS
    B. Inefficient Control Plane in Userspace FS
    C. Poor Metadata Scalability
    D. 数据扩展性问题（此处为主题概括，不作为标题逐字转录）
III. Design Overview
IV. AugeFS Architecture
    A. Efficient Control Plane in AugeFS
    B. Protection for Shared User Address Space
    C. Put It Together
V. MetaDB
    A. Parallel Request Processing
    B. Fine-Grained Parallel WAL
VI. Data Management
    A. Domain-Based File Organization
    B. Asynchronous IO Stack for Fsync
VII. Evaluation
```

**组织逻辑：[C＋分析]** 动机先把性能问题拆成控制、元数据、数据三条路径；总览给出一一对应的组件；随后依次解释执行位置、元数据表示和持久化、数据分区和恢复。这个顺序有依赖关系：集中且可扩展的 MetaDB 承担命名空间操作后，数据 domain 才能减少跨分区的命名空间协调负担。

期刊版应重新提取全部章、节、小节、图表和算法清单，并检查从 16 页到 41 页的内容扩展。页数增加本身不能证明新机制或新实验的存在。

<a id="section-6"></a>
## 6. 背景的作用

### 6.1 快速 SSD：解释为什么原先的优化会失效

**[C，§II-A，p.2]** 前作以低至十微秒以下的设备访问延迟为背景，指出用户／内核切换和通用 IO 栈的成本变得突出；同时指出部分设备的顺序访问仍优于随机访问。因此用户态与日志结构分别针对软件开销和设备访问模式。

**[分析]** 这只能说明设计方向合理，不能推出“所有现代 SSD 都需要相同方案”。必须观察论文实测设备的延迟、带宽以及随机／顺序差距。

### 6.2 LSM 元数据：表示匹配不等于并发匹配

**[C，§II-C，p.3]** inode 等元数据通常是几十到几百字节，块接口可能带来额外写入。LSM KV 引擎能把这些小更新汇聚成顺序写，也方便把多项元数据更新放入事务。

**[分析]** 采用 KV 表示只解决“怎么存”。如果通用 KV 引擎要求不相关线程一起写 WAL、一起等待完成，它又会引入新的共享瓶颈。因此本文研究问题不是简单把 inode 放进 RocksDB。

### 6.3 PMR：后续两项机制的硬件前提

**[C，§II-A、§V-B]** NVMe PMR 是设备暴露的可持久化、可内存映射区域。前作用它保存细粒度 WAL，以及异步数据写的持久版本号。

**[分析]** PMR 不只是“小缓存”。这里需要的是细粒度、可确认的持久写入能力。它同时支撑元数据提交和数据恢复判断，因此期刊版若继续依赖 PMR，设备可得性、持久化顺序和模拟准确性都是核心条件。

<a id="section-7"></a>
## 7. 动机与问题严重性

以下图号与数字**全部来自会议前作**，不作为目标期刊版结果引用。

| 前作图 | 具体观察 | 支持什么判断 | 不能证明什么 |
| --- | --- | --- | --- |
| 图 1，p.3 | RocksDB 元数据原型随线程增加，组同步可占到 78% 延迟；SSD、RamDisk 均有扩展性限制 | 换更快设备不能消除线程协调成本 | 所有 RocksDB 版本和配置都有相同比例 |
| 图 2，p.4 | 20 个线程中仅 1 个进行同步持久化时，整体吞吐就可下降 54%；全部同步时最高下降 83% | 不相关请求通过共享 WAL 的落盘范围耦合 | 完整 AugeFS 在同场景下的期刊版最终收益 |
| 图 2，p.4 | 无同步线程时 WAL 设备写量约 0.29 GB；1 个同步线程时约 10.2 GB；20 个约 28 GB | 一个调用的持久化范围扩大，增加实际 IO | 这些数字是 SSD 内部 NAND 写放大；它们是论文测得的 WAL 设备写量 |
| 图 3，p.4 | Max 在该同步写场景设备利用率最高约 60%；24 线程下锁开销仍占 `fsync` 的 37.7% | 分拆部分结构后，提交阶段仍可能串行化 | 任何设备、工作负载或多盘系统都无法超过此利用率 |

**为什么从性能数字上升为系统问题？[分析]** 若线程 A 只要求自己的元数据持久化，却迫使其他线程的日志一同写盘，那么 A 的延迟不再主要由自己的更新量决定；其他线程也会受到 A 的 `fsync` 频率影响。这意味着并发隔离变差，增加 CPU 核和带宽可能仍无法提升服务能力。

作者这里的 “false sharing” 主要指逻辑上不相关的操作被共享提交或持久化范围绑定，不能直接等同于 CPU cache line false sharing。

<a id="section-8"></a>
## 8. 方法逻辑：依据会议前作

期刊摘要确认了这四个方向，但以下实现细节均须以期刊全文再次核验。

<a id="shared-address-space"></a>
### 8.1 共享且受保护的地址空间

**[C，图 4–5，§III–IV，pp.4–6]** 多个应用把 AugeFS 映射到自己的固定用户虚拟地址范围。共享区域包含文件系统代码、堆、栈及缓存。应用线程通过 gate 进入文件服务，临时改变 MPK 访问权限、切换栈和上下文，返回时恢复并撤销访问。

图 4 的作用是说明控制路径、MetaDB、文件 domain 与设备之间的分工；图 5 则把“像库调用一样访问文件系统”具体展开为 gate、权限切换、栈切换和操作码分派。图 5 内有示例代码，不应把它误称为单独编号算法。

**为什么有效？** 请求无需让另一个常驻服务线程代为执行；不同进程又能访问同一组文件状态和锁。因此它同时减少 IPC 和分离副本之间的协调。

**保护边界非常重要。** 前作 §IV-B 的核心防护目标是应用中的错误写入；恶意进程主动修改保护状态等攻击另列为未完成方向。不能把 MPK gate 描述为已验证的恶意多租户隔离方案。

**设计与原型要分开。** §IV-A 描述 `augefs_init` 新系统调用及内核登记；§VII 的实现实际使用管理进程处理登记、修改链接器建立固定映射，并把进一步修改内核保留地址范围列为可做事项。这不否定设计，但复现时必须说明测的是哪条路径。

<a id="parallel-updates"></a>
### 8.2 MetaDB 并行更新

**[C，图 6，§V-A，pp.6–7]** 元数据被转成 KV，例如文件元数据键为 `m:父目录inode号:文件名`；`rename` 包含多项删除／插入操作。MetaDB 使用支持并发插入的 skiplist Memtable，并拆解请求为：准备、写日志、更新 Memtable、返回。

准备阶段仍有全局互斥，负责序号分配、Memtable／WAL 切换等；后面几个阶段允许独立推进。并行更新阶段先采用 per-core WAL，但这还没有完全解决持久化范围耦合，下一节才处理它。

**两个正确性约束：**

1. **Memtable 切换不能丢掉在途插入。** 每张 Memtable 维护 `in_flight_writes`；切为可刷出的 immutable Memtable 前，等待它归零。
2. **相同 key 的更新仍需要顺序。** 前作依赖上层文件系统 inode 锁串行化真实冲突，因而不能把 MetaDB 的并发设计直接推广成无条件通用 KV 引擎结论。

**[分析]** 真正利用的是文件系统已有的语义锁：若上层已经序列化同一对象的修改，下层就不必再让所有无关对象一起等待。代价是正确性跨越了文件系统和 KV 引擎两个层次，接口契约应写清楚。

<a id="fine-grained-wal"></a>
### 8.3 细粒度持久日志

**[C，图 7，§V-B，pp.7–8]** 日志在 PMR 中存放。线程通过 CAS 原子分配日志区间，再独立写入；记录按 64 B 对齐，包含事务开始标记 TxB、内容和提交信息 TxE。恢复时检查记录是否完整；未完整提交的记录被丢弃。多个 KV 组成的 write batch 放在同一事务单元中。

**为什么 per-core WAL 还不够？** 一个目录的更新可能分散在多个 CPU 的 WAL 内。同步这个目录仍可能迫使多个 WAL 整体落盘。PMR 细粒度日志改变的是持久化单元，使当前操作不必依赖其他线程的大范围 flush。

**管理方式：** PMR 中有日志数据区及小元数据区；每张 Memtable 对应一段日志。Memtable 刷出后回收日志。容量较小时，前作提出两半轮换，将写满的一半异步迁移到 SSD 预留区。

**[分析] 需要验证的边界：** CAS tail 仍然共享；小 PMR 的迁移可能产生背压；先写日志内容再写提交标记的持久顺序必须成立。TxB／TxE 的存在本身不是完整的掉电正确性证明，还需结合写原子性、flush/fence 和回收协议。

<a id="domain-fsync"></a>
### 8.4 Domain 与异步 fsync

**[C，§VI-A，pp.8–9]** 通过 `Hash(ino) % N` 把文件放到 domain，前作默认 `N=16`。一个文件归一个 domain；每个 domain 独立管理 SIT、NAT、SSA 相关状态、日志和 checkpoint，减少跨 domain 的写入争用。SIT 项进入 MetaDB，NAT 按块分配，使一个 NAT 块不混入其他 domain 的条目。

**为什么不仅是“加分片”？** 如果只拆内存锁，磁盘上的小条目仍共用同一块，提交和 checkpoint 就可能重新发生耦合。前作同时调整了持久化布局。因此分区边界要贯穿数据结构、物理写入和恢复单位。

**仍然存在全局边界。** 全局 `sync` 时，需要对所有 domain 做 checkpoint，并有全局锁。静态哈希也不能保证工作负载均衡：一个热点文件仍集中在单个 domain。前作把动态迁移列为后续方向。

**[C，图 8，§VI-B，pp.9–10]** 常规路径等待数据块写完，再提交指向它的 node block；AugeFS 尝试重叠两类 IO。为防止恢复时索引指向未落盘数据，PMR 中为每个 CPU 保存持久版本号：node block 记录 CPU ID 与当前版本，数据完成后推进持久版本；恢复时仅接受满足版本条件的 node block。

这相当于把部分“必须先完成才能提交下一步”的顺序，转为“可以并发提交，但恢复时必须判断有效性”。per-CPU 锁保护相应过程，不能省略不讲。

**[分析] 复杂度与瓶颈：** 哈希域选择平均为常数时间；日志区间分配包含一次共享原子操作；活跃日志恢复需扫描其范围。总体性能仍受冲突锁、Memtable 切换、日志回收和设备队列影响，不能因为去掉组等待就称为无锁、无共享或线性扩展算法。

<a id="section-9"></a>
## 9. 实验解读与期刊版待核项

### 9.1 先明确研究问题

| 研究问题 | 当前已核对的前作图／正文 | 目标期刊版还需核查 |
| --- | --- | --- |
| RQ1：元数据瓶颈是否来自组同步和共享 WAL？ | 图 1–2、§II-C | 图号、引擎版本、参数及 profiling 方法 |
| RQ2：数据路径是否受提交锁与顺序限制？ | 图 3、§II-D | 设备饱和基准、锁统计口径 |
| RQ3：MetaDB 改造后，同步元数据操作能否扩展？ | 图 9 对应的 §VII-A 正文 | 每个子图、全部数据、重复次数和误差 |
| RQ4：端到端收益和资源代价如何？ | 已确认前作采用 Filebench、LevelDB | 期刊全部相关图表和 CPU／内存开销 |
| RQ5：PMR、domain、并行日志、异步 fsync 各贡献多少？ | 已读机制，尚未完成该组实验核验 | 全部消融、交互作用和敏感性实验 |
| RQ6：崩溃、热点和长期运行时是否仍有效？ | 已读前作恢复与热点讨论 | 故障注入、满盘、GC、尾延迟、偏斜负载证据 |

图 1–3 的逐图分析见第 7 节，图 4–8 的方法分析见第 8 节。**这不是目标期刊版的全图表清单。** 后续不能沿用这些编号而不核查。

### 9.2 已核实的前作实验设置

**[C，§VII，p.10]**

| 项目 | 会议前作设置 |
| --- | --- |
| CPU | 双路 Intel Xeon Platinum 8260，每路 24 核；关闭超线程 |
| 操作系统 | CentOS 7；Linux 4.19.11 |
| 内存／持久内存 | 256 GB DRAM；512 GB Optane PM |
| 存储 | 375 GB Intel P4800X SSD；2 TB Seagate HDD |
| PMR | 用 256 MB Optane PM 模拟，每次 64 B PM 访问增加 900 ns 软件延迟 |
| IO 路径 | AugeFS 原型使用 NVMeDirect |
| 元数据区域 | 默认预留 50 GB SSD 区域给 MetaDB |
| 比较对象 | Ext4、F2FS、Max、TableFS、Strata、uFS；另有 AugeFS-roc 变体 |

**重要限制：** 前作明确说当时没有可用 PMR 产品，性能来自模拟。期刊版是否换用真实设备、调整时延模型或提供更强模拟证据，当前未知。

### 9.3 前作图 9 的已读正文结论

**实验在问什么？** 同步元数据更新是否能随线程数扩展，LSM 表示是否也改善目录读取？

**设计：[C，§VII-A，p.10]** 在各线程的私有目录内测试 create、unlink、mkdir、rmdir、rename、readdir；正文描述每线程执行 100 万次操作，并以 `fsync` 提供持久化。AugeFS-roc 保留 AugeFS 框架但用 RocksDB 替换 MetaDB，帮助区分“用了 KV”与“改造 KV”的贡献。

**结果：[C]** 作者对图 9(a–e) 的同步元数据更新报告平均 34× 改善，并报告相较 AugeFS-roc 最高 35× 改善。此处只转述正文概括，尚未重算平均方式，不能把它写成相对某个指定基线、每种操作都固定加速 34×。

**反例同样重要：[C]** 图 9(f) 的 readdir 中，AugeFS、TableFS、AugeFS-roc 表现不理想：同一目录数据可能分散在不同 SST 与块中，扫描需要更多读取。论文同时说明 Strata 不支持该 readdir 测试，且多线程 rename 无法运行。

**[分析]** 这说明优化是有负载选择性的：细粒度同步修改获益，不等于目录扫描也变快。私有目录实验可以隔离非必要共享，但不能替代共享目录热点实验。

### 9.4 暂不填写的内容

目标期刊版所有图表的数值、软件版本、数据路径实验、Filebench／LevelDB 结果、完整消融与敏感性结果、错误条和统计方法均未完成核验。会议前作摘要和引言中的数值也不能填进期刊版结果表。

<a id="section-10"></a>
## 10. 相关工作的比较坐标

以下为**依据已读前作的分析框架**，不是期刊 Related Work 章节的完整提取。

| 类别 | 已出现的比较对象 | 区分 AugeFS 时应看的问题 |
| --- | --- | --- |
| 成熟内核文件系统 | Ext4、F2FS | 软件路径、日志持久化范围、语义完整性与部署成本 |
| LFS 并发优化 | Max | 分区是否覆盖分配、提交和 checkpoint 全路径 |
| KV 组织元数据 | TableFS、RocksDB 原型 | 小记录表示之外，组提交是否匹配文件系统锁和 `fsync` 语义 |
| 用户态／跨介质文件系统 | uFS、Strata | 请求由应用线程还是服务线程执行；需要多少额外 CPU 与 IPC |
| 内存保护机制 | MPK 及相关保护工作 | 防意外写、抗恶意代码、持久状态保护是否分别成立 |

**[分析]** 当前可见的贡献更像多个相互依赖边界的系统性调整，而非首次提出日志结构、用户态文件系统、分区或 LSM。需要取得期刊全文，才能判断其相对 MSST 前作的独立增量。

<a id="section-11"></a>
## 11. 对本仓库研究的启发

以下是阅读推导，**不是对 Mooncake 当前实现的代码审计结论**，也不表示本仓库已存在对应瓶颈。

1. **观察“谁被谁的持久化请求拖慢”。** 如果研究 SSD offload 或 KV 数据落盘，可测试一个同步写流对其他异步流的影响；只测总吞吐容易遗漏持久化范围耦合。
2. **分区边界应落到实际写入和恢复单元。** 仅给内存结构分片，仍可能在日志 flush、块更新、空间回收或 checkpoint 重新相遇。
3. **把服务线程与轮询成本计入资源预算。** 比较内核旁路、服务进程和应用内执行时，应同时统计应用与后台线程的 CPU 消耗。
4. **先明确哪些顺序是语义需要。** 利用对象级串行化减少下层重复同步有潜力，但必须说明跨对象事务、故障恢复和读可见性的约束。
5. **把硬件前提写进结论。** 如果优化依赖 PMR 一类细粒度持久介质，普通 NVMe SSD 上的可迁移部分和不可迁移部分应分别验证。

若未来开展实验，优先选择“同步流干扰异步流”的小型对照实验，同时记录吞吐、延迟分位数、CPU、设备实际写量以及提交／flush 次数。这个建议来自论文机制分析，未在此任务中修改代码或运行性能测试。

<a id="section-12"></a>
## 12. 审稿问题、缺口与来源

<a id="review"></a>
### 12.1 当前可作出的评价

**有证据支持的优点：** 期刊摘要的问题与设计对应关系清晰；会议前作不仅报告慢，还用线程同步占比、WAL 写量和提交锁分析定位了非必要依赖。已读方法同时讨论正常路径与恢复路径，且主动指出 readdir、热点和 PMR 可得性等边界。

**需要追问的关键问题：**

- 期刊版具体新增哪些机制、证明或实验？不能从题名与页数判断。
- PMR 模拟是否保留真实 PCIe 访问、持久化顺序、带宽与排队特性？
- CAS 日志尾、全局准备锁、Memtable 切换在更多 CPU 核下是否成为新瓶颈？
- 共享目录／单文件热点下，收益会保留多少？静态 domain 是否出现明显偏斜？
- 对所有基线，持久性保证、可用资源和 CPU 核预算是否一致？
- 异步数据／node 写入是否经过系统化崩溃注入，覆盖版本推进和日志回收边界？
- 多进程共享状态在进程异常退出时如何处理锁、栈、映射与后台任务？
- 保护目标是否仍局限于可信进程组中的错误写入？有无恶意攻击防护实证？
- 满盘、GC、compaction 和 checkpoint 重叠时的尾延迟如何？

**评价边界：** 上述问题是应查证的项目，不能写成“期刊论文没有做”。目标全文未取得，因此不对其完整实验充分性作最终判断，也不打分或给接收／拒绝建议。

<a id="remaining"></a>
### 12.2 完成全文精读所需材料

**唯一实质阻塞：取得目标 DOI 对应的 41 页期刊正式版 PDF，或可确认与之对应的作者全文。** 会议前作不足以代替。

取得后，应完成以下工作：

1. 核对题名、作者、出版信息和页数，标明稿件版本。
2. 按全文提取完整多级目录、关键词及所有图、表、算法清单。
3. 按图号逐项检查方法和实验，并先建立实验 RQ 映射。
4. 重新核对本报告引用的前作机制，标注期刊修订、扩展或删除内容。
5. 核验全部性能倍率及分母、实验软件版本、持久性条件、消融和统计方法。
6. 阅读讨论、局限、相关工作、结论及 artifact 声明，形成最终审稿人视角总结。
7. 将本报告顶部状态改为完成，删除不再成立的阻塞说明，而不是保留阶段结论冒充全文评价。

<a id="sources"></a>
### 12.3 可追溯来源

| 编号 | 来源 | 用途与本次访问结果 |
| --- | --- | --- |
| J1 | [用户指定 ACM ePDF](https://dl.acm.org/doi/epdf/10.1145/3728645) | 目标论文；HTTP 403，未取得全文 |
| J2 | [ACM PDF](https://dl.acm.org/doi/pdf/10.1145/3728645) | 目标正式版下载；HTTP 403 |
| J3 | [Crossref DOI 记录](https://api.crossref.org/works/10.1145/3728645) | 已读取；题名、完整摘要、作者单位、卷期、页码、收稿／接受／出版时间 |
| J4 | [OpenAlex W4409280366](https://api.openalex.org/works/W4409280366) | 已读取；正式版位置、开放获取与内容索引信息 |
| J5 | [OpenAlex 内容 PDF](https://content.openalex.org/works/W4409280366.pdf) | 返回 API key 要求；未下载，不属于已读全文 |
| J6 | [Semantic Scholar DOI 查询](https://api.semanticscholar.org/graph/v1/paper/DOI:10.1145/3728645?fields=title,openAccessPdf,url) | 已读取；开放 PDF 仍指向 ACM，未提供替代全文 |
| C1 | [MSST 2024 官方会议页面](https://storageconference.us/2024/) | 列出 AugeFS 前作、作者、公开论文及视频 |
| C2 | [MSST 2024 AugeFS 公开 PDF](https://storageconference.us/MSST-history/2024/Papers/msst24-7.2.pdf) | 已下载，16 页；本报告具体机制与已核查实验的来源 |

临时取证文件位于 `/tmp/3728645-crossref.json`、`/tmp/augefs-openalex.json`、`/tmp/augefs-msst24.pdf`。这些只是本次环境中的临时副本，可能被清理；可追溯依据以上述公开 URL 为准。未把返回 HTML 的失败下载冒充 PDF 保存进论文目录。
