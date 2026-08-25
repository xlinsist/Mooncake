
《Transparent Heterogeneous Storage for KV Cache across Local and Remote NVMe》

### 第一段：Why KV Cache storage matters
> LLM 推理中 KV Cache 越来越大 → GPU HBM 不够 → offload → storage 影响恢复/淘汰性能
### 第二段：Why local + remote
> Local NVMe：在相同设备和可比 I/O 路径下避免网络与协议开销，但受单节点容量/带宽限制
> NVMe-oF：增加网络与协议路径，但提供独立于推理节点的容量和 I/O 资源
> **→ 部署者需要按容量规划和既定策略选择后端，同时避免选择逻辑渗透到上层 KV Cache 管理代码**
### 第三段：What existing design is missing
> 以 Mooncake Store 为例，调用方需要通过 `ReplicateConfig` 显式指定本地/NoF 副本数或目标 segment
> → 上层需要感知 backend/topology
> → deployment/placement 配置改变时，调用方配置与访问逻辑也要改变
> **→ 增加系统实现与运维复杂度，使 local / remote 难以在不修改上层代码的情况下作为统一存储资源使用**
> **目标：实现由部署配置控制的存储后端透明——上层无需感知后端选择，并在不同后端上获得一致、可恢复的对象语义；本文不解决动态最优 placement。**
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
> - recovery：客户端进程重启后，在 Master 元数据仍可用时依据副本描述恢复访问路由
> **→ 将 placement 与对象状态绑定，使失败与重启不破坏后端透明性。**
### 第五段：Our design
> Mooncake Store 透明异构存储层
> - **Configuration-driven placement + metadata-based routing**：部署者在 Master 配置 `local_only`、`remote_only` 或 `round_robin`，Store 封装后端选择、资源分配、地址描述与访问路径（Challenge 1）。
> - **Commit/revoke lifecycle + metadata-based recovery**：统一对象可见性、失败回滚、资源释放与重启恢复（Challenge 2）。
> **→ 对外保持统一 `put / get / remove`，对内统一 placement 与 lifecycle。**
### 第六段：Evaluation
> - **透明层开销**：同一目标后端的 direct/transparent 配对实验中，16–256 KiB 对象的本地 `put` p50 增量为 −0.00%–5.79%，且没有一致退化方向；远端 `put` 增量为 13.99%–19.78%，主要成本来自远端写入。128 KiB 时增加 0.0382 ms；`get`/`remove` 仅增加 0.0001/0.0003 ms。
> - **生命周期正确性**：覆盖 `local_only`、`remote_only`、`round_robin`、目标不可用、客户端重启 → 失败写入不发布、资源可回收、重启后可找回对象 → 证明统一生命周期语义成立；不等于高可用集群级故障恢复。
> - **受控 reuse 结果**：固定 128 KiB、其他参数不变，将 reuse 从 0% 提高到 90% 后，远端请求 p50 的透明层增量从 15.31% 降至 0.01%，但请求 p95 仍为 +13.93% → 高复用降低写入成本对中位请求延迟的影响，但不消除包含写入请求的尾部开销。
> - **公开轨迹结果**：FAST’25 `conversation`/`toolagent` 的 1000-request、arrival-paced 顺序 Store 回放中，远端总存储等待增量分别为 11.97%/13.71% → 只反映透明存储路径开销，不代表完整 LLM serving 性能。
> - **实验边界**：两节点测试床、同后端 direct/transparent 配对；固定 128 KiB、LRU、有限 request 前缀、单进程逐 event 回放 → 不外推到其他硬件/网络/并发度，也不覆盖完整 trace、并发饱和、HA 恢复、GPU、TTFT/TPOT 或 goodput。
>
> （内部备注：Master 路径短时中断实验仍观察到 failed-put lifecycle residue；投稿前修复，或将“资源可回收”限定为已覆盖的目标不可用与写入失败场景。）
### 第七段：Contributions
> 1. 透明异构存储抽象
> 2. 统一且可恢复的对象生命周期
> 3. 分层评估与工作负载边界分析
