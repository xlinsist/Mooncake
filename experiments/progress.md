我觉得你这份手稿里其实已经有了一条**比较清晰、而且比“做一个 NVMe-oF KV Cache 系统”更有论文味道的主线**。但目前的问题是：你同时在讲 **资源池化、透明抽象、统一生命周期、元数据/地址空间管理、placement scheduling** 五件事，它们现在还是并列出现的。

真正应该把它们串成一条**因果链**。

---

# 一、我认为你的核心逻辑主线应该是

> **NVMe-oF 使分散 SSD 可以形成共享资源池 → 资源池只有对上层透明才能真正具备可扩展性和可移植性 → 透明化要求系统内部统一管理异构后端 → 而 NVMe-oF 的 block-level 特性使这种统一抽象并不免费，需要承担地址空间、元数据和生命周期管理复杂性 → 我们设计统一的 KV Cache Store 来封装这些复杂性 → 因此系统获得全局资源视图，从而进一步支持 placement、资源调度和空间管理 → 实验验证透明抽象成本可接受，并证明 remote pool 在特定 workload 下可以优于 local。**

如果压缩成一句论文的 **storyline**：

> **Pooling creates resources; transparency makes pooling usable; unified management makes transparency possible; global visibility enables scheduling benefits.**

中文就是：

> **资源池化创造资源能力，透明抽象让资源池真正可用，统一管理实现透明抽象，而全局资源视图进一步带来调度优化空间。**

我认为这是你现在最应该抓住的论文主线。

---

# 二、你的论文不应该从“KV Cache 怎么调度”开始

你现在有一个很重要的定位需要明确。

你的核心贡献目前看起来**不是**：

> “我们发现小 KV 放 local，大 KV 放 remote。”

因为这个太容易退化成一个 heuristic paper：

```text
if small:
    local
else:
    remote
```

这不足以支撑你的系统架构。

你的真正核心应该是：

> **现有 local NVMe 和 remote NVMe-oF 是两个需要显式管理的存储后端；我们首先把它们变成一个统一、透明的存储资源池。**

然后：

> **因为统一之后系统拥有全局资源视图，所以 placement 和 scheduling 才成为可能。**

因此逻辑关系应该是：

```text
Storage Pooling
      ↓
Backend Transparency
      ↓
Unified Storage Management
      ↓
Global Resource View
      ↓
Placement / Scheduling Opportunity
```

而不是：

```text
发现 local / remote 怎么选
      ↓
为了调度
      ↓
做一个透明系统
```

这两个故事完全不同。

我认为你的应该是第一个。

---

# 三、精简后的论文骨架

我建议整个论文可以压缩成下面 **5 个逻辑部分**。

---

## 1. Motivation：为什么需要把 Local NVMe 和 Remote NVMe-oF 放进一个 Pool？

### 问题

KV Cache 的 SSD 存储资源目前天然是分散的：

```text
Node A:
    Local SSD A

Node B:
    Local SSD B

Node C:
    Local SSD C
```

这带来两个资源问题。

### ① 容量孤岛

每台机器的 SSD：

* 容量不同
* 利用率不同
* 部署拓扑不同

于是运维需要显式考虑：

> SSD 插在哪里？

> 哪台 GPU 有 SSD？

> SSD 满了怎么办？

> 新增 SSD 怎么使用？

---

### ② 带宽孤岛

单节点只能使用：

```text
Local SSD bandwidth
```

但整个集群实际上拥有：

```text
SSD A bandwidth
+ SSD B bandwidth
+ SSD C bandwidth
```

NVMe-oF 提供了把这些设备连接起来的能力，因此：

> **分散 NVMe 可以成为共享的存储资源池。**

这里是你的第一个核心 motivation。

---

# 2. Problem：为什么“有 Pool”还不够？

这是你目前手稿里**最需要加强的一跳**。

你现在有：

> NVMe-oF 可以池化
> ↓
> 所以做 transparent storage

但中间缺了一层。

应该明确：

---

## Pooling ≠ Usability

即使你有：

```text
Local NVMe
+
Remote NVMe-oF Pool
```

如果上层仍然需要写：

```text
if local:
    local_write()

if remote:
    remote_write()
```

那么实际上：

* inference framework 要知道 backend
* 要知道 topology
* 要维护不同 I/O path
* placement policy 暴露给上层
* 新增 backend 需要修改上层

于是这个 pool **没有真正成为统一资源**。

因此你的问题定义应该是：

> **The challenge is not merely how to connect remote SSDs, but how to expose heterogeneous local and remote NVMe resources as a single storage abstraction.**

中文：

> **问题不仅是如何通过 NVMe-oF 连接远端 SSD，而是如何将本地和远端异构 NVMe 资源以统一的存储抽象暴露给 KV Cache 系统。**

这是你整个论文非常关键的一句话。

---

# 3. Challenge：透明化不是免费的

这里就是你提到的：

> **要统一，就要承担复杂性。**

这个其实可以成为你论文非常好的一个 design challenge。

我建议把它组织成：

---

## Challenge 1：Backend Transparency

Local 和 Remote 的访问路径不同。

```text
Local NVMe:
Application
    ↓
OS / Kernel I/O Stack
    ↓
Local NVMe

Remote NVMe-oF:
Application
    ↓
NIC
    ↓
Network
    ↓
Remote NVMe
```

如果让上层感知：

* backend
* topology
* location

系统就不具备 portability。

因此必须：

> **将 backend selection 和 access routing 收进 Store 内部。**

---

## Challenge 2：Unified Lifecycle

这个是你现在提出的一个技术问题。

不同 backend 的对象生命周期可能不同：

```text
LOCAL:
prepare
→ write
→ commit

REMOTE:
allocate
→ RDMA/NVMe-oF write
→ PutEnd
→ revoke
```

如果生命周期不统一：

> Store 无法真正成为统一抽象。

所以你需要设计：

```text
PUT
GET
DELETE
```

或者：

```text
Prepare
Stage
Commit
Abort
```

统一的 object lifecycle。

这里需要注意一点：

### ⚠️ 我建议你不要简单写：

> “本地和远端都放在内核态，所以生命周期统一。”

这个逻辑目前有一点危险。

**生命周期统一**和**是否在内核态**其实是两个不同层面的事情。

真正的逻辑应该是：

> 本地和远端后端的原生 I/O 执行机制不同，因此系统需要在 Store 层定义统一的逻辑生命周期；底层可以分别映射到不同的 backend execution path。

也就是说：

```text
Unified Logical Lifecycle
        ↓
Backend-specific Execution
        ↓
Local / Remote
```

而不是：

```text
Both kernel mode
        ↓
Automatically unified lifecycle
```

除非你真的实现了一个统一的 kernel/SPDK execution environment，否则这一点不要说得太绝对。

---

# 4. Key Design：NVMe-oF 为什么会带来元数据和地址空间问题？

我认为这里其实可以成为你论文的**技术深度来源**。

你的理解是对的：

> NVMe-oF 给你更低层的 block/device access 能力，但相应地把一部分资源管理责任交给了系统。

可以理解为：

```text
File System:
    read(file, offset)

↓

Object Store:
    get(object_id)

↓

NVMe-oF:
    read(node, device, LBA, length)
```

越往下：

> 控制力越大，管理责任也越大。

---

## NVMe-oF 的核心复杂性

如果你做一个真正的 remote SSD pool，系统必须回答：

### 1. Object 在哪里？

```text
Object ID
    ↓
Node?
Device?
```

### 2. Object 的物理地址是什么？

```text
Node A
Device 3
LBA = 123456
Length = 128 KiB
```

### 3. 如何分配空间？

* allocation
* reuse
* reclamation
* fragmentation

### 4. 如何路由？

```text
Object ID
    ↓
Metadata Lookup
    ↓
Node / Device / Address
    ↓
NVMe-oF Access
```

所以你说的这句话非常好：

> **要统一，就要承担复杂性。**

可以进一步 formalize 成：

> **Backend transparency shifts storage-topology complexity from applications into the storage layer.**

即：

> **透明抽象并没有消除复杂性，而是将复杂性从上层应用迁移到了存储系统内部。**

这句话我觉得非常适合你的论文。

---

# 五、你的 Store 应该成为整篇论文的“中心”

目前你的描述中 Store 有点像一个 implementation detail。

但我认为它应该是：

# **整个论文的 Architectural Center**

你的系统可以抽象成：

```text
              Inference Framework
                     │
                     │ Unified KV API
                     ↓
              ┌──────────────┐
              │     Store    │
              │              │
              │ Metadata     │
              │ Placement    │
              │ Allocation   │
              │ Routing      │
              │ Lifecycle    │
              └──────┬───────┘
                     │
          ┌──────────┴──────────┐
          ↓                     ↓
      Local NVMe          Remote NVMe Pool
          │                     │
          ↓                     ↓
      Local I/O             NVMe-oF
```

Store 的职责就是：

### ① Backend Selection

```text
Object → Local or Remote?
```

### ② Resource Allocation

```text
Which node?
Which SSD?
Which address?
```

### ③ Metadata Management

```text
Object ID
→ location
→ device
→ address
```

### ④ Access Routing

```text
GET(Object ID)
      ↓
metadata lookup
      ↓
local or remote path
```

### ⑤ Lifecycle Management

```text
create
write
commit
read
delete
reclaim
```

这样你的 Store 就不是：

> 一个 wrapper

而是：

> **统一异构存储资源的控制点。**

---

# 六、为什么“透明化”之后才能做调度？

这是你的第二条非常重要的逻辑线。

统一 Store 后：

```text
Inference Framework
        │
        │ doesn't know
        ↓
       Store
        │
        ├── Local SSD
        │
        └── Remote SSD Pool
```

Store 拥有：

```text
Global Resource View
```

因此才能：

* 看 local capacity
* 看 remote capacity
* 看 local queue
* 看 remote queue
* 看 device utilization
* 看 network path
* 选择 placement

于是：

> **Transparency is not only a software-engineering benefit; it creates a centralized control point for storage scheduling.**

中文：

> **透明抽象不仅降低软件复杂度，更重要的是为存储调度提供了统一的控制点。**

这是我建议你重点强调的。

---

# 七、Placement Policy 应该作为“透明架构带来的能力”，而不是前提

你现在的策略：

> 小访问 → local
> 大访问 → remote
> local busy → remote

我认为方向是对的，但论文结构上应该放在后面：

---

## 基于统一资源池的 Placement

### Latency-sensitive

```text
Small I/O
+
latency-sensitive
        ↓
Local
```

---

### Throughput-oriented

```text
Large I/O
+
parallelism opportunity
        ↓
Remote Pool
```

---

### Local Contention

```text
Local SSD Busy
        ↓
Remote SSD available
        ↓
Remote
```

这里特别重要：

> **remote 的优势不是“remote latency 比 local 更低”。**

而是：

### Remote 可以：

* 使用空闲远端 SSD
* 聚合多个 SSD 的资源
* 避开 local contention
* 获得更大的 aggregate bandwidth

所以你的论文需要寻找：

# **Remote wins not because it is remote, but because the pool provides better resources.**

这是一个非常好的实验 narrative。

---

# 八、你现在最关键的实验目标：找 Remote > Local 的区域

我非常赞同你最后这一点。

你需要找的不是：

> remote latency 有没有接近 local

而是：

> **在什么 workload 下，global resource pooling 让 remote outperform local？**

我建议至少寻找三类区域。

---

## Workload Region A：Local Contention

```text
Local SSD
├── KV Cache Restore
└── Other I/O
```

Local queue saturation：

```text
Local latency ↑
```

这时：

```text
Remote SSD idle
```

可能：

```text
Remote NVMe-oF
<
Contended Local NVMe
```

这是最容易形成 **remote > local** 的场景。

---

## Workload Region B：Aggregated Bandwidth

单个：

```text
Local SSD = 7 GB/s
```

Remote pool：

```text
SSD A = 7 GB/s
SSD B = 7 GB/s
SSD C = 7 GB/s
```

如果 KV object 可以：

* striping
* parallel read
* multi-object parallelism

理论上：

```text
Remote aggregate bandwidth
>
Single local SSD bandwidth
```

注意：

这不一定适合小对象。

更可能适合：

> 大规模 KV restore / batch restore。

---

## Workload Region C：Capacity Pressure

```text
Local SSD Full
```

传统系统：

* eviction
* reject
* recompute

Pool：

```text
Remote capacity available
```

这时 remote 的优势甚至不是 latency：

> **它使 workload 可以继续运行。**

这也是一个非常有价值的 “remote wins”。

---

# 九、我建议你的论文最终骨架

## 1. Introduction

### KV Cache creates growing SSD storage demand

↓

### Local NVMe is fast but node-bound

↓

### NVMe-oF enables storage pooling

↓

### But pooling is not usable if topology leaks into applications

↓

### We propose a backend-transparent KV Cache Store

↓

### Transparency requires unified metadata, allocation, routing, lifecycle

↓

### Unified Store creates a global control point for scheduling

↓

### Results:

* local overhead ≤ 5.79%
* remote overhead acceptable
* remote wins in XXX workload

---

# 2. Background & Motivation

### Local vs Remote NVMe

|            | Local NVMe    | Remote NVMe-oF                       |
| ---------- | ------------- | ------------------------------------ |
| Latency    | Low           | Network overhead                     |
| Capacity   | Node-bound    | Poolable                             |
| Bandwidth  | Single-device | Aggregatable                         |
| Contention | Local         | Can bypass local                     |
| Management | Simple        | Requires metadata/address management |

然后提出：

> **Why a unified abstraction is necessary**

---

# 3. Problem and Design Challenges

## C1. Backend transparency

不能让 inference framework 感知：

```text
local / remote / topology
```

## C2. Unified object lifecycle

不同 backend：

```text
execution path differs
```

但必须提供：

```text
consistent semantics
```

## C3. Metadata and address-space management

NVMe-oF：

```text
Object ID
→ Node
→ Device
→ Address
```

需要系统承担。

---

# 4. System Design

## 4.1 Unified Store Interface

```text
Put / Get / Delete
```

---

## 4.2 Metadata and Placement

```text
Object ID
       ↓
Metadata
       ↓
Placement Decision
       ↓
Local / Remote
```

---

## 4.3 Unified Lifecycle

```text
Create
↓
Write
↓
Commit
↓
Read
↓
Delete
```

backend-specific execution hidden below。

---

## 4.4 Resource Management

* allocation
* reclamation
* fragmentation
* capacity balancing

---

## 4.5 Placement Opportunities

先做简单 policy：

```text
small / latency-sensitive → local

large / throughput-oriented → remote

local congested → remote
```

---

# 5. Evaluation

我建议 Evaluation 明确回答 **三个问题**。

### Q1：透明 abstraction 的成本高吗？

```text
Transparent vs Native Local
Transparent vs Native Remote
```

结果：

> Local overhead ≤ 5.79%

> Remote write +13.99%–19.78%

> 128 KiB +0.0382 ms

---

### Q2：统一 abstraction 是否保持正确语义？

验证：

* put
* get
* delete
* lifecycle consistency

---

### Q3：Pool 什么时候真正有价值？

找：

```text
Remote > Local
```

至少一个 workload：

* local contention
* aggregated bandwidth
* capacity pressure

---

# 十、我认为你现在最大的三个逻辑跳步

## 跳步 1：从“NVMe-oF Pool”直接跳到“透明抽象”

中间应该补：

> **如果 pool 的 topology 仍暴露给 inference framework，那么 pooling 的部署和扩展价值会被 backend-aware software complexity 抵消。**

这是必须补的。

---

## 跳步 2：“统一生命周期”为什么是核心挑战？

你需要解释：

> backend execution path 不同 → failure/commit/reclaim semantics 可能不同 → 如果没有统一 logical lifecycle，上层仍然需要理解 backend-specific behavior。

否则读者会问：

> 为什么不直接 Put/Get/Delete wrapper 一下？

---

## 跳步 3：从“透明 Store”跳到“智能调度”

必须明确：

> **调度不是透明 abstraction 的定义，而是统一 abstraction 带来的能力。**

即：

```text
Transparency
    ↓
Centralized Control
    ↓
Global Resource View
    ↓
Scheduling
```

---

# 最后，我会把你的整篇论文概括成这一段

> **NVMe-oF enables physically distributed NVMe SSDs to be pooled into shared storage resources, offering expandable capacity and the potential for aggregated bandwidth. However, these benefits are difficult to realize when storage topology and backend details are exposed to the inference framework. We therefore design a backend-transparent KV Cache storage layer that encapsulates heterogeneous local and remote NVMe resources behind a unified Store abstraction. The Store internalizes backend selection, metadata and address-space management, resource allocation, access routing, and object lifecycle management, allowing the inference framework to access KV Cache objects without knowing their physical location. This unified abstraction not only improves portability and extensibility, but also establishes a global control point for placement and resource scheduling. Our evaluation shows that the cost of transparency is small, while pooled remote resources can outperform local storage in workloads where they provide higher available bandwidth or avoid local resource contention.**

**一句话版本：**

> **你的论文不是“用 NVMe-oF 存 KV Cache”，而是“如何把 Local NVMe 和 NVMe-oF SSD Pool 变成一个对 LLM inference framework 透明、但内部可统一管理和调度的 KV Cache 存储系统”。**

我认为这个定位已经比较完整了。接下来最值得做的是继续收敛成**论文 Introduction 的 problem→insight→challenge→design→contribution 五段式逻辑**，因为你现在其实已经有了全部材料，只差把它们按这个故事重新排列。
