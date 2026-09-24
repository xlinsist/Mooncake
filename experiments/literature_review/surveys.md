# 近三年相关综述：面向 NVMe-oF 文件系统

检索日期：2026-09-09。窗口：2023-09-09 至 2026-09-09，区分在线发表、卷期年与预印本。依据 `../progress.md` 的 C1 后端透明、C2 统一生命周期、C3 元数据/地址空间管理；不局限于 LLM。本文是既有调研的扩展记录。

## 推荐结论

优先读 **Survey of storage systems in high performance computing** 和 **Lustre Unveiled**：前者有专门的 NVMe-oF 小节，适合建立资源池化背景；后者是成熟并行文件系统的架构与演进综述，适合校准“统一命名空间、元数据和存储目标管理”哪些已经是常规能力。两者都是期刊文章，研究内容属于综述/架构回顾。

其次读 **Empowering Cloud Computing With Network Acceleration**，其“标准接口隐藏资源、加速路径直接访问硬件”的矛盾与 C1 很接近；若考虑 target CPU/DPU 卸载，再读 SmartNIC 综述。C2 的完整跨本地/远端持久化、迁移与安全回收契约，不能仅靠这些综述证明，应回到 CETOFS、NVLog、SquirrelFS、D2FS 等原始论文。

## 期刊综述主表

日期优先采用出版社或 Crossref 的出版记录；OpenAlex 的 1 月 1 日可能是只有年份的占位日期，因此不强填月份。以下 Abstract 是摘要转述，覆盖判断及阅读建议是本调研的推论。

| Title / 链接 | Year / Month、Venue | First Author、Affiliation | GPU Architecture / 涉及硬件 | Scheduling Technique / Level | Abstract | TLDR |
|---|---|---|---|---|---|---|
| [Survey of storage systems in high performance computing](https://doi.org/10.1007/s42514-025-00268-5) | 在线 2025/12/17；卷期 2026，CCF Transactions on High Performance Computing 8:254–274 | Gen Zhang；国防科技大学 | PM、SSD/ZNS/QLC、IB/RoCE、CXL、NVMe-oF；非单一 GPU 实验 | 分离/分层架构、burst buffer、网络；全存储栈 | 按架构、硬件、软件和网络梳理 HPC 存储，讨论带宽、混合负载、性能成本及未来方向。 | **最适合总览**；官方全文 §2 架构、§4 缓冲、§5.2 NVMe-oF 可直接阅读。 |
| [Lustre Unveiled: Evolution, Design, Advancements, and Current Trends](https://doi.org/10.1145/3736583) | 2025/06/18 在线，ACM Transactions on Storage；OpenAlex 标为 2025/05/22，两者有日期差异 | Anjus George；Oak Ridge National Laboratory | Lustre / Frontier 的 Orion 存储系统；非统一微基准硬件 | 并行文件系统架构、设计演进、系统比较；文件/集群层 | 回顾 Lustre 历史、架构、功能演进与未来方向，并比较其他存储技术，分析 Orion 的使用、性能趋势。 | **最适合文件系统定位**；成熟系统的背景，不是 NVMe-oF 专题综述。 |
| [Empowering Cloud Computing With Network Acceleration: A Survey](https://doi.org/10.1109/COMST.2024.3377531) | 2024；IEEE Communications Surveys & Tutorials；月份未核实 | Lorenzo Rosa；University of Bologna | XDP、DPDK、RDMA 等软硬件加速 | 接口、虚拟化、可运维性、安全；云网络/运行时层 | 云平台希望以标准接口提供加速，但硬件直达与虚拟化产生矛盾；按四个方面分类研究，并讨论 Network Acceleration as a Service。 | **C1 的强概念参考**；不把网络服务管理等同于文件持久化生命周期。 |
| [A Comprehensive Survey on SmartNICs: Architectures, Development Models, Applications, and Research Directions](https://doi.org/10.1109/ACCESS.2024.3437203) | 2024；IEEE Access；月份未核实 | Elie F. Kfoury；University of South Carolina | SmartNIC 的专用处理器与通用核；非 GPU 综述 | 网络/安全/存储/计算卸载分类；NIC/DPU 基础设施层 | 梳理 NIC 到 SmartNIC 的演进、架构、开发环境与卸载应用，以及开发部署难点和研究方向。 | **CETOFS/HiDPU 的背景阅读**；是否采用 DPU 是设计选择，不是研究前提。 |

## 逐挑战覆盖与阅读方法

“强”表示适合为该挑战建立背景与技术分类，不表示综述本身提出并验证了解决方案；“有限”表示不能用它支持具体语义保证。

| 综述 | C1 | C2 | C3 | 应带着什么问题读 |
|---|---|---|---|---|
| HPC storage survey | 强：分离、分层、NVMe-oF | 有限：不能据总览确认 commit/abort 协议 | 中：寻找并行 FS/资源管理引用 | NVMe-oF 解决设备访问后，分配、命名与恢复由哪一层负责？ |
| Lustre Unveiled | 强：成熟并行 FS 架构参考 | 中：用其参考文献追查恢复语义；本次未全文确认细节 | 强：文件系统架构入口 | 统一文件视图与元数据/数据目标的分工，哪些是已有系统能力？ |
| Network Acceleration survey | 强：统一接口与直达硬件的矛盾 | 有限：serviceability 不等于 crash consistency | 间接：隔离、虚拟化、资源管理 | 透明层怎样避免抵消内核绕过或 RDMA 的收益？ |
| SmartNIC survey | 中：卸载路径和开发模型 | 有限：摘要不能证明持久化协议覆盖 | 间接：适合追索存储卸载文献 | 地址查询、权限检查、并发控制分别值得放在哪里？ |

### C1：不要把透明 API 当成研究空白

HPC 总览与 Lustre 回顾适合建立已有架构地图，网络加速综述适合解释透明抽象的成本。你的比较对象应包含已有文件系统 over NVMe-oF；要证明的是混合 local/remote 资源池在容量偏斜、争用或迁移下的收益及抽象开销，而非仅证明应用能读写远端盘。

### C2：综述可引路，原始协议才提供语义证据

需要分别查逻辑可见性、写入顺序、掉电持久性、失败原子性、删除后安全回收。不要把 cache eviction、网络重试或服务恢复直接解释为文件系统事务。基于综述参考文献回溯原论文，再检查 `fsync`、truncate、unlink/open handle、重试幂等、迁移提交与旧 extent 回收的边界。

### C3：区分命名空间、extent 映射与设备内部地址

Lustre 提供成熟文件系统架构参照；索引细节继续读 RASK、HiDPU。拟议映射可分为 `path → inode` 与 `(inode, offset) → target/namespace/LBA/length`。LBA 是设备 namespace 的逻辑块地址，不是 NAND 物理地址。分配、版本、迁移失效缓存与地址复用，是映射查找之外需要验证的部分。

## 补充：会议综述与预印本，单独标注

| 论文 | 日期 / 类型 | 作者 / 机构 | 摘要与范围 | 对本课题价值 |
|---|---|---|---|---|
| [A Survey on Metadata Management in File Systems](https://doi.org/10.1109/ICTC62082.2024.10827668) | 2024/10，ICTC **会议综述**，不属于上表期刊 | Taehwan Ahn / Chung-Ang University | 摘要涉及 IndexFS、soft updates、journal-based versioning、multiversion B-tree、分布式元数据负载与可靠性。硬件不是重点；技术层级是 FS 元数据。 | 题目和 C3 最贴近，也触及 C2 更新顺序；适合作为引文入口，不能仅凭“survey”名称推定全面性或顶会质量。 |
| [A Survey on Large Language Model Acceleration based on KV Cache Management](https://arxiv.org/abs/2412.19442) | 2024/12 v1，2025/07 v3；本次按 **arXiv 预印本** 核验 | Haoyang Li 等；机构未从核验页面取得 | 分 token/model/system 三类，覆盖选择、量化、内存管理、调度与硬件设计，并整理数据集与 benchmark；硬件跨多类 GPU/系统。 | 仅作为 workload 层补充，优先读 system-level；不承担 NVMe-oF 文件系统语义论证。 |

## Taxonomy、系统比较、趋势与开放问题

技能模板的 scheduling taxonomy 在此解释为资源管理技术分类：**资源分离/分层 → 文件系统命名与布局 → 网络接口与卸载 → 元数据结构/更新顺序 → 应用缓存调度**。compiler systems comparison 在本主题中不适用，替换为抽象层比较：HPC 总览覆盖全栈，Lustre 深入一个 FS 家族，网络/SmartNIC 综述解释运行时与设备侧，KV-cache 综述位于应用层。它们互补，不能据此排名端到端性能。

从这些综述可归纳的趋势是硬件能力增长促使资源池化与异构化，同时接口、隔离、运维和数据管理成本越来越重要。对本课题尚待验证的问题包括：

1. local/remote extent 能否采用统一生命周期，同时明确不同后端的持久化确认条件？
2. 迁移和回收如何避免元数据已切换而旧读者仍访问被复用地址？
3. 全局 placement 的资源收益是否足以覆盖索引、协议与故障处理开销？
4. 采用 target CPU/DPU 卸载是否引入新的权威状态、恢复和升级复杂性？

这些是研究问题建议，不是通过综述已经证明的创新缺口。

## 检索记录与证据限制

- OpenAlex Works API，日期过滤 `2023-09-09..2026-09-09`；检索词：`disaggregated storage survey`、`NVMe over fabrics survey`、`distributed file systems survey`、`storage systems high performance computing survey`、`memory disaggregation survey`、`file system metadata survey`、`persistent memory file systems survey`、`KV cache survey system`。
- 对候选 DOI 定向查询 OpenAlex，并通过 `api.crossref.org/works/{doi}` 核对出版社登记的题名、期刊、作者与日期。宽查询噪声很大，已排除非综述、无关领域和仅名称相似条目。
- Springer 官方页面成功取得全文目录：§2 Storage architectures、§4 Storage software acceleration technology、§5.2 NVMe-oF；确认在线 2025/12 与卷期 2026 的区别。
- ACM 页面返回 403；IEEE 页面返回空 202。因此 Lustre 的内容判断依据出版社登记到 Crossref 的摘要；两篇 IEEE 综述依据 OpenAlex 摘要与 Crossref 书目。本次没有宣称全文精读，也不捏造章节编号。
- arXiv 官方摘要页成功核验题名、作者、v1/v3 日期及分类。
- 未核实到一篇在本窗口内专门同时覆盖 NVMe-oF 文件系统 C1–C3 的全面综述；这是本次检索范围内的结果，不是“不存在”的证明。最稳妥的阅读组合是 HPC 总览 → Lustre → 网络加速，再按 C1–C3 回到原始系统论文。
