
《Transparent Heterogeneous Storage for KV Cache across Local and Remote NVMe》

### 第一段：Why KV Cache storage matters
> LLM 推理中 KV Cache 越来越大 → GPU HBM 不够 → offload → storage 影响恢复/淘汰性能
### 第二段：Why local + remote
> Local NVMe：低延迟，但受单节点容量/带宽限制
> NVMe-oF：增加网络开销，但提供可组合的容量/带宽
> **→ 两者具有互补性，需要协调使用**
### 第三段：What existing design is missing
> 但是现有设计仍然把 local / remote 与上层耦合
> → 上层需要感知 backend/topology
> → deployment/placement 改变时上层逻辑也要改变
> **→ 增加系统实现与运维复杂度，并限制 local / remote 资源的透明调度与扩展**
> **目标：实现存储后端透明——上层无需感知后端选择，并在不同后端上获得一致、可恢复的对象语义。**
### 第四段：Why backend transparency is technically difficult
> 要实现后端透明的统一存储抽象，需要解决两个挑战。
**Challenge 1：Placement Transparency**
> 如何隐藏本地 NVMe 与 NVMe-oF 在 placement 与访问机制上的差异？
> - allocation：节点内 backend 分配 vs. 远端 segment 分配
> - data transfer：本地 I/O vs. 网络传输
> - addressing：本地 locator vs. 远端 endpoint/offset
> - data path：本地 backend vs. NoF transfer engine
> **→ Store 必须封装后端选择、地址描述和读写路由，使上层保持统一对象接口。**
**Challenge 2：Lifecycle Consistency**
> Placement transparency 只隐藏“走哪条路径”，还需保证本地与远端路径具有一致、可恢复的对象语义：
> - prepare / write：待提交，写完前不可见
> - commit / abort：成功发布，失败撤销并释放资源
> - read / remove：依据已提交元数据访问和回收
> - recovery：依据持久化副本描述恢复路由
> **→ 将 placement 与对象状态绑定，使失败与重启不破坏后端透明性。**
### 第五段：Our design
> Mooncake Store 透明异构存储层
> - **Policy-driven placement + metadata-based routing**：封装后端选择、资源分配、地址描述与访问路径（Challenge 1）。
> - **Commit/revoke lifecycle + metadata-based recovery**：统一对象可见性、失败回滚、资源释放与重启恢复（Challenge 2）。
> **→ 对外保持统一 `put / get / remove`，对内统一 placement 与 lifecycle。**
### 第六段：Evaluation
> - **透明层开销**：16–256 KiB 对象，本地 `put` p50 增量 −0.00%–5.79% → 本地几乎无明显损失；远端 `put` 增量 13.99%–19.78% → 主要成本来自远端写入。128 KiB 时仅增加 0.0382 ms；`get`/`remove` 只增加 0.0001/0.0003 ms → 读写后的路由和回收开销很小。
> - **生命周期正确性**：覆盖 `local_only`、`remote_only`、`round_robin`、目标不可用、客户端重启 → 失败写入不发布、资源可回收、重启后可找回对象 → 证明统一生命周期语义成立；不等于高可用集群级故障恢复。
> - **请求序列开销**：FAST’25 `conversation`/`toolagent` 顺序回放，本地请求 p50 增量 2.47%–8.42%，远端 11.95%–12.08% → 缓存复用和淘汰能摊薄部分写入成本，但远端仍更慢；只反映存储路径开销，不代表完整 LLM serving 性能。
> - **实验边界**：两节点测试床、同后端 direct/transparent 配对、主要看 p50；固定 128 KiB、LRU、有限 request 前缀、单进程顺序回放 → 不外推到其他硬件/网络/对象大小/并发度，也不覆盖 p95/p99、完整 trace、并发饱和、HA 恢复、GPU、TTFT/TPOT 或 goodput。
### 第七段：Contributions
> 1. 透明异构存储抽象
> 2. 统一且可恢复的对象生命周期
> 3. 分层评估与工作负载边界分析
