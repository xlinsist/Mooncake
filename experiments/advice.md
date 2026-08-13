下面这个版本可以直接作为你接下来 1–2 个月的研究计划骨架。核心原则是：**先把系统能力做实，再做策略；先证明“存在值得调度的空间”，再证明“自适应策略有效”。**

# 研究计划：面向 LLM KV Cache 的透明异构 NVMe 存储与自适应管理

## 1. 研究目标

面向 LLM Serving 中请求抢占后的 KV Cache 保存与恢复问题，构建一个对上层透明的异构存储层，统一管理：

* 本地 NVMe SSD；
* NVMe-oF 远端 SSD；
* 后续可扩展 DRAM 等层级。

上层 LLM Serving 系统不需要感知：

* 有多少块 SSD；
* 哪些 SSD 是本地、哪些是远端；
* SSD 的拓扑；
* KV Cache 应该保存到哪块设备；
* 是否需要迁移或跨设备并行读取。

系统内部根据：

[
KV\ Size,\ Preemption\ Duration,\ Local\ Load,\ Remote\ Load
]

动态决定：

[
Recompute / DRAM / Local\ SSD / Remote\ SSD
]

最终目标是降低请求恢复延迟，尤其是 **p95/p99 恢复延迟**，同时提高集群中 NVMe 资源的整体利用率。

---

# 2. 当前论文问题定义

研究问题可以先收敛为：

> 当 LLM 请求发生抢占时，KV Cache 应该如何在异构的本地和远端 NVMe 资源之间进行透明放置、迁移和恢复，使恢复延迟最小？

对应三个子问题：

### RQ1：远端 NVMe 是否真的值得加入决策空间？

需要回答：

* NVMe-oF 相比本地 NVMe 增加多少延迟？
* KV Cache 多大时网络固定开销可以被摊薄？
* 本地 SSD 繁忙时，远端 SSD 是否可能反而更快？
* 多个远端 NVMe 能否通过并行读取获得高于本地单盘的恢复带宽？

这是整个课题成立的第一前提。

### RQ2：怎样把 local/remote SSD 对应用隐藏？

设计一个统一存储抽象：

```text
LLM Serving
     |
     v
KV Storage Interface
     |
     v
Placement / Migration
   /            \
Local NVMe   Remote NVMe-oF
```

应用只面对：

```text
put()
get()
remove()
```

而不用：

```text
write_local()
write_remote()
read_nvme3()
```

### RQ3：什么时候应该选择哪种恢复方式？

探索：

```text
Recompute
DRAM
Local SSD
Remote SSD
```

之间的 decision boundary。

第一阶段可以先做静态规则，后续再做在线自适应。

---

# 3. 总体技术路线

整个项目建议分成五个阶段：

```text
Mooncake NVMe-oF 基线
        ↓
性能 Characterization
        ↓
透明异构存储层
        ↓
策略接口与自适应管理
        ↓
接入 LLM 抢占恢复 workload
```

不要一开始同时改 Mooncake、vLLM、HiFC 和 scheduler。

---

# 4. 阶段一：跑通 Mooncake NVMe-oF 基础能力

## 目标

先把 Mooncake 当成一个已经存在的远端 NVMe 数据通路，而不是你的研究对象。

实现：

```text
Compute Node
    |
Mooncake / NoF
    |
RDMA / NVMe-oF
    |
Remote NVMe Node
```

至少需要两台机器：

### Node A

* GPU；
* 本地 NVMe；
* 高速 NIC；
* Mooncake client / initiator。

### Node B

* NVMe SSD；
* NVMe-oF/SPDK target；
* 高速 NIC。

如果条件允许，增加 Node C，用于测试多远端 SSD。

## 这一阶段完成标准

必须稳定支持：

```text
Put KV object
Get KV object
Delete KV object
```

并能够重复跑 benchmark。

### 产物

* Mooncake 编译、部署脚本；
* NVMe-oF target 配置脚本；
* benchmark；
* 完整环境文档。

---

# 5. 阶段二：先做性能 Characterization

这一阶段非常重要，因为它决定后面策略有没有研究价值。

暂时完全不接 LLM。

---

## 实验 2.1：Local vs Remote 基础性能

对象大小：

```text
4 KB
64 KB
256 KB
1 MB
4 MB
16 MB
64 MB
256 MB
1 GB
```

比较：

```text
Local NVMe
Remote NVMe
```

测量：

* bandwidth；
* IOPS；
* p50；
* p95；
* p99；
* CPU utilization；
* NIC utilization；
* SSD utilization。

得到：

> NVMe-oF 固定开销大概是多少？

以及：

> KV Cache 大到什么程度之后，remote/local 的差距开始变小？

---

## 实验 2.2：多远端 SSD 聚合带宽

比较：

```text
1 remote SSD
2 remote SSDs
4 remote SSDs
```

观察：

[
BW(N)
]

是否随设备数量扩展。

尤其关注：

> 多远端 SSD 并行读取是否能够超过单个 local NVMe。

如果可以，这就是 remote pool 非常重要的存在理由。

---

## 实验 2.3：负载下的 crossover

人为增加 local SSD background I/O：

```text
0%
25%
50%
75%
90%
```

同时保持 remote SSD 空闲。

比较：

```text
T_local(size, load)

T_remote(size, load)
```

希望找到：

[
T_{remote}<T_{local}
]

出现的区域。

然后反过来也测：

* local 空闲；
* remote/network 拥塞。

最终得到类似：

```text
             Local load

low ---------------------- high

small KV     local          local
medium KV    local          remote
large KV     local          remote pool
```

这张图实际上是后面调度策略最重要的 empirical motivation。

---

# 6. 阶段三：实现透明异构存储层

这才是老师现在比较认可的系统方向。

第一版本不要真的做完整 POSIX 文件系统。

先做一个最小的 logical storage layer。

---

## 系统结构

```text
              LLM / Benchmark
                     |
                     v
            +----------------+
            | KV Storage API |
            +-------+--------+
                    |
            +-------v--------+
            | Metadata Layer |
            +-------+--------+
                    |
            +-------v--------+
            | Policy Manager |
            +---+---------+--+
                |         |
                v         v
           Local NVMe   NVMe-oF
```

---

## 6.1 Storage API

第一版只需要：

```cpp
put(key, buffer, size)
get(key, buffer)
remove(key)
```

暂时不要做复杂 filesystem semantics。

---

## 6.2 Metadata Manager

维护：

```text
KV ID
size
location
device
offset
stripe width
state
```

例如：

```text
req_1001
size = 8 GB
location = remote
devices = SSD2, SSD3
stripe = 2
```

上层不需要知道这些信息。

---

## 6.3 Backend

只保留两个：

```text
LocalNvmeBackend

MooncakeNoFBackend
```

后续再增加：

```text
DRAMBackend
```

---

## 6.4 Policy Interface

定义：

```text
select_write_target()

select_read_source()

migrate()

get_device_state()
```

第一版甚至可以只有：

```text
LOCAL_ONLY
REMOTE_ONLY
ROUND_ROBIN
```

这里重点不是策略聪明，而是把：

> **机制和策略分离。**

这对后面的系统论文设计很重要。

---

# 7. 阶段四：验证透明层本身没有明显开销

这是老师提出“文件系统封装”以后必须有的一组实验。

比较：

```text
Direct Local NVMe
vs
Storage Layer → Local NVMe
```

以及：

```text
Direct Mooncake NoF
vs
Storage Layer → Mooncake NoF
```

测：

* latency overhead；
* bandwidth loss；
* CPU overhead。

目标是能够说明：

> 使用透明抽象带来的额外软件开销很小，而换来了统一资源管理能力。

否则 reviewer 很容易问：

> 为什么不直接让 HiFC 自己选择设备？

---

# 8. 阶段五：建立 KV Cache 调度模型

做到这里以后才开始研究策略。

第一版只考虑四个变量：

[
x=(S,T,L_l,L_r)
]

其中：

### (S)

KV Cache size。

### (T)

预计抢占持续时间。

### (L_l)

local SSD 当前负载。

### (L_r)

remote SSD + network 当前负载。

输出：

[
A\in
{
Recompute,
DRAM,
Local,
Remote
}
]

---

# 9. 先不要直接写 Adaptive Algorithm

先构建 Oracle。

也就是离线穷举：

```text
KV size
×
preemption duration
×
local load
×
remote load
```

分别运行：

```text
Recompute
DRAM
Local SSD
Remote SSD
```

然后找：

[
A^*(x)=\arg\min T_{resume}
]

得到一个 Oracle decision map。

---

## 一个比较合理的实验矩阵

### KV Size

```text
512 MB
1 GB
2 GB
4 GB
8 GB
16 GB
```

### Preemption duration

```text
100 ms
500 ms
1 s
2 s
5 s
10 s
30 s
```

### Local load

```text
0%
50%
90%
```

### Remote load

```text
0%
50%
90%
```

然后分析 decision boundary。

---

# 10. 再设计真正的自适应策略

等 decision boundary 足够清晰以后，再决定算法。

第一版优先考虑 **cost model + heuristic**，不要一开始上 ML。

例如：

[
C_{local}
=========

T_{write}^{local}
+
T_{read}^{local}
]

[
C_{remote}
==========

T_{write}^{remote}
+
T_{read}^{remote}
]

[
C_{recompute}=T_{prefill}
]

再考虑预计 idle time：

如果：

[
T_{idle}<T_{write}
]

那么甚至可能不值得 offload。

最终：

```text
if short preemption:
    recompute / DRAM

elif local lightly loaded:
    local SSD

elif remote pool has spare BW:
    remote SSD

else:
    min(predicted_cost)
```

论文里可以比较：

```text
Local-only
Remote-only
Local-first
Round-robin
Load-aware
Your Adaptive
Oracle
```

---

# 11. 最后再接入 vLLM / HiFC 式 workload

前面的系统稳定以后再做。

完整路径：

```text
Request decoding
      |
      v
Preemption
      |
      v
KV Cache eviction
      |
      v
KV Storage Layer
      |
 local / remote
      |
      v
Request resumes
      |
      v
KV restored to HBM
```

保持：

> **vLLM scheduler 的抢占时机和策略不变。**

不要把：

* 谁应该被抢占；
* 什么时候抢占；
* priority scheduling；

一起纳入当前课题。

你的研究对象是：

> 已经决定抢占之后，这些 KV 怎么保存和恢复。

---

# 12. 最终 LLM 实验

Baseline 至少：

```text
Recompute
DRAM
Local SSD
Remote SSD
Adaptive
Oracle
```

如果 HiFC/Tutti 能够合理复现，则再增加：

```text
HiFC-style local NVMe
Tutti-style local path
```

重点指标：

### 请求级

* resume latency；
* TTFT / time-to-resume；
* p50/p95/p99；
* request latency。

### 系统级

* throughput；
* GPU utilization；
* SSD bandwidth；
* network bandwidth；
* CPU utilization。

### 资源级

* local SSD utilization；
* remote SSD utilization；
* aggregate pool bandwidth；
* load imbalance。

---

# 13. 最后论文希望回答的四个核心问题

论文实验部分其实可以直接按照这四个 RQ 组织。

### RQ1：NVMe-oF 是否适合 KV Cache 抢占恢复？

回答 remote overhead 有多大，以及在哪些 KV size 下可接受。

### RQ2：为什么需要 remote pool，而不是始终使用 local SSD？

通过：

* local contention；
* capacity pressure；
* aggregated bandwidth；

说明 remote 提供了实际的额外决策空间。

### RQ3：透明存储抽象成本是否可接受？

证明：

[
Overhead_{abstraction}\ll Benefit_{management}
]

### RQ4：自适应 local/remote placement 是否有效？

与 static policies 和 Oracle 比较。

最终最好能够得到：

```text
Adaptive ≈ Oracle
         >
Local-only
Remote-only
Local-first
```

尤其看 p99。

---

# 14. 时间安排

我建议按下面顺序推进，而不是同时铺开。

| 阶段    | 工作                   | 目标                               |
| ----- | -------------------- | -------------------------------- |
| 第 1 周 | Mooncake NoF 部署      | 两节点 remote KV Put/Get 跑通         |
| 第 2 周 | Microbenchmark       | local/remote、多盘、KV size 曲线       |
| 第 3 周 | Contention benchmark | 找出 local/remote crossover        |
| 第 4 周 | Storage abstraction  | 统一 API + metadata + backend      |
| 第 5 周 | Policy interface     | local/remote placement、migration |
| 第 6 周 | Oracle experiment    | 得到 decision boundary             |
| 第 7 周 | Adaptive policy      | cost model / load-aware policy   |
| 第 8 周 | LLM integration      | 接抢占恢复 workload                   |
| 后续    | 完整评测                 | baseline、p99、ablation            |

---

# 15. 近期最具体的 TODO

你现在不要想完整论文，先把下面这些完成：

* [ ] 跑通 Mooncake NVMe-oF 最新实现；
* [ ] 建立 local NVMe benchmark；
* [ ] 建立 remote NVMe benchmark；
* [ ] 测 1/2/4 块远端 SSD aggregate bandwidth；
* [ ] 测 local SSD background load 对恢复性能影响；
* [ ] 找出至少一个 **remote 优于 local** 的 workload 区域；
* [ ] 定义 `KVStorageBackend`；
* [ ] 实现 LocalBackend；
* [ ] 封装 MooncakeNoFBackend；
* [ ] 定义 `PlacementPolicy`；
* [ ] 证明应用代码无需知道 local/remote topology；
* [ ] 再开始接 vLLM 抢占恢复。

其中有一个非常明确的 **Go/No-Go checkpoint**：

> **如果经过前面的 characterization，几乎所有合理场景下 local SSD 都显著优于 remote SSD，而且多远端盘聚合、local contention、容量压力都不能产生明显收益，那么应及时重新审视“remote pool 调度”是不是足够强的研究问题。**

反过来，如果你能在第 2–3 周明确画出 **local/remote crossover region**，这个课题的实验基础就基本立住了。

你目前最合适的研究主线，我会概括成：

> **Characterize → Abstract → Schedule → Integrate。**

也就是：**先证明异构性值得管理，再把它透明封装，再研究调度，最后落到 LLM KV Cache 抢占恢复。** 这条顺序比现在直接去复现和修改 HiFC 更稳。
