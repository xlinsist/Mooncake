# 透明异构存储层开发设计

## 1. 文档状态

- 目标阶段：`experiments/advice.md` 中的阶段三，并为阶段四的透明层开销实验预留接口。
- 实现范围：Mooncake Store 的 logical object / replica 层。
- 首版后端：本地 NVMe 与 Mooncake NoF。
- 首版策略：`LOCAL_ONLY`、`REMOTE_ONLY`、`ROUND_ROBIN`。
- 上层接口：继续使用现有 `put/get/remove` 及批量、零拷贝变体。

本文是开发规格，不把透明层实现成 POSIX 文件系统，也不要求推理框架理解文件路径、SSD 设备、NoF endpoint 或副本类型。

## 2. 目标与验收定义

### 2.1 核心目标

推理框架只表达对象操作：

```text
put(key, buffer, size)
get(key, buffer)
remove(key)
```

框架不负责：

- 判断 KV cache 应写入本地 NVMe 还是远端 NoF；
- 构造 `replica_num`、`nof_replica_num` 或 preferred segment；
- 保存文件路径、设备编号、offset 或 stripe 信息；
- 在读取和删除时重建后端路由。

透明层在 Mooncake Store 内部完成目标选择、数据传输、元数据提交、读取源选择和资源释放。部署者可以通过服务配置选择策略，但推理框架代码不因策略变化而变化。

### 2.2 首版完成标准

满足以下条件才认为阶段三完成：

1. 同一组 Store API 在三种策略下均可完成 put/get/remove。
2. 框架调用点不包含 local/remote 分支，也不设置 NoF 副本数。
3. `LOCAL_ONLY` 的成功对象只发布可读的本地 NVMe 副本。
4. `REMOTE_ONLY` 的成功对象只发布可读的 NoF 副本。
5. `ROUND_ROBIN` 在可用目标之间稳定轮转，失败写入不发布元数据。
6. get 根据已提交的 replica metadata 选择源，而不是依赖调用方记忆写入位置。
7. remove 通过统一对象生命周期回收元数据和对应后端资源。
8. client restart 后路由不依赖进程内 map。
9. 单测覆盖策略、状态转换、失败回滚和并发；NoF 环境完成端到端验证。

## 3. 非目标

首版不实现：

- POSIX open/read/write、目录、权限、rename 等文件系统语义；
- DRAM / recompute 决策；
- 基于负载预测的自适应策略；
- 自动迁移和多盘 striping；
- 跨集群持久化或新的 metadata database；
- 替换现有 Mooncake Master、replica allocator 或 transfer engine。

`migrate()`、设备状态和 stripe metadata 会保留扩展点，但不应阻塞首版静态策略交付。

## 4. 现状与关键缺口

### 4.1 推理框架已有合适的数据接口

Python binding 已提供普通对象接口，以及直接面向调用方 buffer 的批量接口：

- `MooncakeDistributedStore.put/get/remove`；
- `batch_put_from(keys, ptrs, sizes, config)`；
- `batch_get_into(keys, ptrs, sizes)`。

因此无需为透明层发明新的 vLLM 专用 API。外部 connector 应继续只生成 key、buffer 和 size。

当前不透明点是 `ReplicateConfig` 对调用方暴露 `replica_num`、`nof_replica_num` 和 `preferred_nof_segments`。阶段一实验中的 `correctness.py` 也会显式设置这些字段。阶段三启用后，标准推理路径不得再通过这些字段选择 local/remote。

### 4.2 现有 NoF 是直接 replica 路径

NoF 写入已经具有完整对象生命周期：

```text
Client Put/BatchPut
  -> Master PutStart/BatchPutStart
  -> allocate NOF_SSD replica
  -> TransferWrite
  -> PutEnd or PutRevoke
```

`Replica::Descriptor` 已保存 NoF endpoint、offset、size 和 status。读取时，Master 返回副本集合，Client 使用 `SelectBestReplica` 选择可读源。

### 4.3 本地 SSD 与 NoF 当前并不对称

现有本地 SSD 主要有两条路径：

1. `FileStorage` 将 MEMORY 对象异步 offload 成 `LOCAL_DISK`，适合淘汰/下沉，不是 put 时的 local-only placement。
2. `StorageBackend` 与 `PutToLocalFile` 处理 `DISK` replica，但现有 `use_disk_replica_` 是全局附加行为，并非按策略在 local 与 NoF 之间二选一。

因此不能只在 `ReplicateConfig` 外包一层，也不能把 `StorageBackendInterface` 重命名为 `LocalNvmeBackend` 就宣称完成透明异构存储。真正缺失的是：

- put 开始阶段可选择的 direct-local replica；
- local 写成功/失败与 Master metadata 的原子提交或回滚；
- local owner、路径/对象标识和读取 endpoint 的可恢复描述；
- 与 NoF 对称的 remove 生命周期。

### 4.4 可复用与不可误用的模块

应复用：

- Master 中的 `ObjectMetadata`、`Replica`、`Replica::Descriptor` 和 `ReplicaStatus`；
- NoF allocator、transfer 和 PutEnd/PutRevoke 生命周期；
- 本地 `StorageBackend` / `StorageBackendInterface` 的实际落盘能力；
- `SelectBestReplica` 的统一读取入口；
- 现有 remove、quota、checksum 和 metrics 语义。

不应直接复用为透明层抽象：

- `StorageBackendInterface`：它是 FileStorage 的 offload/load/scan 接口，不表达 NoF 的分配和提交生命周期；
- `DistributedStorageBackend` / `FileSystemAdapter`：它面向共享文件系统，不是 Mooncake NoF replica；
- HA `MetadataStore`：它是 standby/oplog 镜像，不是 placement metadata；
- `AllocationStrategy`：它解决同类 segment 内的副本分配，不负责 local-vs-remote 策略。

## 5. 总体设计

透明层位于公共 Store API 与现有 replica 机制之间：

```text
vLLM / benchmark
        |
        | key + buffer + size
        v
MooncakeDistributedStore / RealClient
        |
        | managed/manual + requester identity
        v
Master Heterogeneous Placement Policy
        |
        +--------------------+
        |                    |
        v                    v
Direct Local Replica    Existing NOF_SSD Replica
        |                    |
        +----------+---------+
                   v
       Master Object/Replica Metadata
                   |
                   v
       Unified Read / Remove Lifecycle
```

这里的 `LocalNvmeBackend` 和 `MooncakeNoFBackend` 是逻辑机制名称，不要求把两者强行塞进一个已有底层 C++ 接口。二者在 object lifecycle 层对齐：

```cpp
prepare_put(key, size) -> pending replica
write(replica, slices)
commit_put(replica) / abort_put(replica)
read(replica, slices)
remove(replica)
get_device_state()
```

首版可以先通过内部 helper 和现有 RPC 实现这些语义；只有在第二个实现也出现重复代码时，才提取正式的 `StorageTarget` 接口。

## 6. 对外透明边界

### 6.1 推理框架接口保持不变

推荐推理路径：

```python
store.batch_put_from(keys, ptrs, sizes)
store.batch_get_into(keys, ptrs, sizes)
store.remove(key)
```

框架侧不得出现：

```python
if use_remote:
    config.nof_replica_num = 1
else:
    write_local_file(...)
```

### 6.2 策略属于部署配置

首版增加 Master 服务级配置，而不是要求框架逐请求传参。Master 是 write placement 的唯一决策者，所有客户端只上报 managed/manual 模式、requester identity 和对象属性：

```text
MC_HETERO_STORAGE_POLICY=legacy|local_only|remote_only|round_robin
```

建议默认值为 `legacy`，保证功能默认关闭时完全兼容现有行为。该变量由 Master 读取；实验部署显式启用其余三种模式。`ROUND_ROBIN` 的序号由 Master 维护，因此它是集群级顺序，batch 按请求中的 key 顺序逐项决策，不受发起客户端数量影响。

高级调用方现有的非默认 `ReplicateConfig` 保持兼容，并定义为 manual override。实现时必须提供明确的判定字段，不能通过“字段是否恰好等于默认值”猜测调用方意图。推荐在内部 RPC/config 中增加：

```cpp
enum class PlacementControl {
    kManaged,  // 由透明层策略决定
    kManual,   // 保留既有 ReplicateConfig 语义
};
```

Python binding 必须把省略配置与显式配置区分开。推荐把参数改为 `config=None`：`None` 转换为 `kManaged`，传入任何 `ReplicateConfig`（即使字段值恰好等于默认值）都转换为 `kManual`。`PlacementControl` 必须进入实际 Client-to-Master request/serialization，不能在 C++ 收到 `ReplicateConfig{}` 后猜测调用方是否传参。该字段用于兼容边界，不要求推理 connector 感知。

## 7. 策略接口

### 7.1 数据结构

```cpp
enum class StorageTarget {
    kLocalNvme,
    kRemoteNof,
};

enum class PlacementPolicyKind {
    kLocalOnly,
    kRemoteOnly,
    kRoundRobin,
};

struct PlacementContext {
    std::string_view key;
    uint64_t object_size;
    std::string_view requester_host_id;
    bool local_available;
    bool remote_available;
};

class PlacementPolicy {
   public:
    virtual tl::expected<StorageTarget, ErrorCode> SelectWriteTarget(
        const PlacementContext& context) = 0;

    virtual const Replica::Descriptor* SelectReadSource(
        std::span<const Replica::Descriptor> replicas,
        const ReadContext& context) = 0;

    virtual DeviceState GetDeviceState(StorageTarget target) const = 0;
};
```

首版 `SelectReadSource` 可继续复用当前固定优先级，但必须通过统一入口调用，为阶段五动态策略留下替换点。

### 7.2 三种静态策略

`LOCAL_ONLY`：

- local 不可用时返回 `NO_AVAILABLE_HANDLE`；
- 不静默降级到 remote；
- 便于获得严格可解释的本地基线。

`REMOTE_ONLY`：

- NoF 不可用时返回 `NO_AVAILABLE_HANDLE`；
- 不静默写入 memory 或 local；
- 对应当前 `replica_num=0, nof_replica_num=1` 的效果，但由 Store 内部生成。

`ROUND_ROBIN`：

- 只在健康且有容量的 target 集合中轮转；
- 使用原子递增序号，不使用随机数；
- target 写失败时本次操作失败并回滚，不在首版自动换目标，以保证实验可解释性；
- 后续可增加显式 fallback policy，不能把 fallback 暗藏在 round-robin 中。

## 8. 元数据模型

Master 的 object/replica metadata 是唯一事实来源，不新增一份进程内 key-to-backend map。

阶段三所需字段映射如下：

| 逻辑字段 | Mooncake 中的来源 |
| --- | --- |
| KV ID | object key |
| size | object metadata / descriptor size |
| location | `ReplicaType` (`LOCAL_DISK`/direct-local type 或 `NOF_SSD`) |
| device | local owner/segment 或 NoF endpoint |
| offset | backend metadata 或 NoF buffer address |
| stripe width | 首版固定为 1 |
| state | `ReplicaStatus` |

写入状态机：

```text
ABSENT
  -> PROCESSING (Master reservation succeeds)
  -> COMPLETE   (backend write and PutEnd succeed)
  -> FAILED/ABSENT (write fails; PutRevoke releases reservation)
```

约束：

- `PROCESSING` 副本不得被 get 返回；
- backend 写入成功但 metadata commit 失败时必须清理孤儿数据；
- metadata commit 成功后 remove 必须能依据 descriptor 定位资源；
- 同 key 并发 put 沿用现有 OBJECT_ALREADY_EXISTS / lease 语义，不另建锁体系；
- checksum 行为与现有 Store 保持一致。

## 9. Direct Local Replica 设计

这是阶段三的主要新增机制。

### 9.1 推荐语义

把 direct local placement 建模为 Master 管理的 replica，而不是 facade 私有文件。现有 `LOCAL_DISK` 由 offload 成功后的 `AddReplica` 直接注册为 COMPLETE，不能原样承担以下生命周期；实现必须新增 direct-local reservation/finalize/revoke RPC，或扩展 PutStart/PutEnd/PutRevoke 使其显式接受 local replica：

1. Client 注册本地 NVMe backend 及 owner `client_id/host_id`。
2. Master 选择与请求方匹配且健康的 local segment。
3. PutStart 返回 pending local descriptor。
4. Client 将 slices 写入本地 backend。
5. Client 以 local replica type 调用 PutEnd；失败调用 PutRevoke。
6. get 根据 descriptor 判断本机直读或通过现有 peer/local-disk 传输读取。
7. remove 依据 descriptor 通知 owner backend 删除对象，再回收 metadata。

优先扩展现有 `LOCAL_DISK` descriptor、segment 注册和 peer-read 机制，避免再增加语义重复的 replica type。扩展后必须同时支持“offload 后直接注册 COMPLETE”和“direct put 从 PROCESSING finalize”两条明确区分的入口。若评估证明 `LOCAL_DISK` 的 offload 假设无法兼容 direct put，再引入 `LOCAL_NVME` 类型，并同时更新 RPC、序列化、snapshot、HA、quota、remove 和 replica selection。

### 9.2 本地 backend MVP

首版只支持 file-per-key backend，原因是它天然支持按 key 删除和故障清理。现有 `StorageBackendInterface` 缺少通用的 `Remove(key)`，bucket/offset backend 也有不同回收语义；不要为了首版一次性重构全部 offload backend。

本地 MVP 应满足：

- 初始化时验证 root directory 和 quota；
- key 到路径的映射复用现有安全编码/路径解析逻辑；
- write 完成前使用临时对象，commit 后原子发布；
- remove 幂等；
- 支持 CPU buffer，GPU buffer 沿用现有 pinned staging；
- 返回可供 Master 持久化的 owner、object size 和 backend locator。

后续再让 bucket/offset backend 实现统一 per-key remove 与 recovery contract。

## 10. 读与删除流程

### 10.1 Get

```text
get(key, destination)
  -> Master Query returns COMPLETE replicas
  -> policy.SelectReadSource(...)
  -> local descriptor: local backend/peer read
  -> NoF descriptor: existing TransferRead
  -> checksum/size validation
  -> return through the same Store API
```

所有 get 变体，包括普通 get、`batch_get_into` 和 tensor/buffer 变体，最终必须调用同一个 replica selection 函数。禁止在 Python binding 或某个批量快路径中复制 local/remote 判断。

首版对象只有一个目标副本时，读策略等价于按 metadata 路由；多副本兼容路径保留现有排序：local memory、local NoF、remote memory、remote NoF、local disk、disk。

### 10.2 Remove

remove 继续以 Master object metadata 驱动。当前单对象 remove 不能保证先删除 owner 上的本地文件再删除 metadata，因此 direct-local 上线前必须补充 owner deletion protocol：

1. Master 将对象持久化标记为 REMOVING，并保留全部 descriptor；
2. Master 向 local owner 发出删除 RPC，NoF 走现有资源回收路径；
3. owner 按稳定 backend locator 幂等删除并返回 ACK；
4. Master 收齐 ACK 后删除 object metadata；
5. owner 离线或 RPC 失败时保留 REMOVING 与重试任务，get 不再返回对象；
6. 超过重试预算时暴露告警与 orphan metric，不得先遗忘对象位置。

透明层不应维护“local 调一次、remote 调一次”的盲删逻辑。

## 11. 建议代码改动边界

下列是实施时的推荐落点，最终命名可按现有风格调整：

| 文件/模块 | 计划改动 |
| --- | --- |
| `include/replica.h` | 增加 managed/manual placement 控制；必要时扩展 local descriptor |
| `include/placement_policy.h` | 新增窄策略接口、静态策略和 device state 类型 |
| `src/placement_policy.cpp` | 实现 local-only、remote-only、round-robin |
| `src/master_service.cpp` | PutStart 分配前执行 write target policy；发布统一 replica metadata |
| `src/client_service.cpp` | 执行 direct-local write、commit/revoke，并保持 NoF 原路径 |
| `include/rpc_service.h`、`src/rpc_service.cpp` | 增加 direct-local reservation/finalize/revoke 与 owner delete/ACK RPC |
| `include/master_client.h`、`src/master_client.cpp` | 增加上述 RPC 的 client stub |
| `src/file_storage.cpp` | 区分 offload 注册与 direct-local reservation 生命周期；执行 owner delete |
| `src/serialize/*`、`src/ha/*` | 持久化新增 descriptor/state 和 REMOVING 重试任务 |
| `include/replica_selection.h` | 将 read source 选择纳入统一策略入口 |
| `src/real_client.cpp` | 让所有普通/批量/into API 使用 managed placement |
| `mooncake-integration/store/store_py.cpp` | 无 config 调用默认 managed；显式 config 保留 manual |
| `src/CMakeLists.txt` | 注册新增策略源文件 |
| `tests/heterogeneous_storage_test.cpp` | 策略与对象生命周期单测 |
| `tests/CMakeLists.txt` | 注册测试目标 |

不要在第一版新增独立 metadata service、SQLite 文件或新的第三方依赖。

## 12. 分步实施计划

### M1：策略骨架，不改变默认行为

- 增加配置解析、policy interface 和三种静态策略；
- 默认 `legacy`；
- 添加纯单测，验证 target 可用性、严格失败和 round-robin 顺序；
- 增加 `placement_decision_total{target,policy,result}` 指标。

### M2：NoF managed placement

- 无 config 的 Store put 在 `REMOTE_ONLY` 下内部生成 NoF allocation intent；
- 推理/实验调用方移除 `nof_replica_num` 设置；
- 复用现有 NoF PutStart/Transfer/PutEnd/Revoke；
- 用现有 NVMe-oF 环境完成 put/get/remove 与跨进程读取。

这一里程碑先证明“后端选择已从调用方收回”，但还不宣称 local/remote 对称完成。

### M3：Direct local placement

- 实现 local backend 注册、选择、写入和 descriptor 提交；
- 支持本机读取、必要的 peer read 和幂等 remove；
- 完成 local-only 的分层重启恢复测试；
- 明确与异步 offload 的共存规则：direct placement 是初始位置，offload 是后续生命周期动作。

### M4：统一路径与 round-robin

- 同一 PutStart 根据策略产生 local 或 NoF target；
- 所有 get 变体统一 read-source selection；
- round-robin 通过两类真实 backend 验证；
- 补齐 failure/revoke、quota 和 metrics。

### M5：阶段四开销实验

比较：

```text
Direct local backend        vs Store API -> transparent layer -> local
Direct Mooncake NoF intent  vs Store API -> transparent layer -> NoF
```

测量 put/get 的 p50/p95/p99、带宽、CPU utilization，以及 policy/metadata 路径的单次额外耗时。使用相同 object size、queue depth、buffer 类型和持久化语义，避免把 fio 与对象 API 的差异误算为透明层开销。

## 13. 测试计划

### 13.1 单元测试

策略：

- local-only 只选 local；local 不可用时失败；
- remote-only 只选 NoF；NoF 不可用时失败；
- round-robin 顺序稳定，跳过 unavailable target；
- 多线程选择无 data race，计数不丢失。

生命周期：

- write 成功后 metadata 才变为 COMPLETE；
- backend write 失败触发 revoke，查询不到对象；
- PutEnd 失败清理 local orphan；
- get 忽略 PROCESSING/FAILED replica；
- remove 成功、重复 remove、backend remove 重试；
- 相同 key 并发 put 不产生双份已提交对象。

兼容性：

- `legacy` 下现有 `ReplicateConfig` 行为不变；
- 显式 manual config 不被全局 policy 改写；
- checksum、tenant quota、soft/hard pin 行为不回退。

### 13.2 本地集成测试

- 临时目录模拟 local NVMe backend；
- CPU 与 GPU/pinned staging 路径；
- client 重建后从 Master metadata 定位对象；
- local owner 离线时返回可诊断错误；
- 批量请求中 local/remote 决策与 key 一一对应。

### 13.3 NoF 端到端测试

复用 `experiments/nvmeof` 环境，至少覆盖：

- remote-only put/get/remove；
- local-only put/get/remove；
- round-robin 连续对象的实际位置分布；
- 子进程/另一 client 读取；
- NoF target 或 local backend 不可用时严格失败且无 phantom metadata。

客户端重启验证不能用笼统的“进程重启后可恢复”代替：

| 场景 | 恢复来源 | 预期语义 |
| --- | --- | --- |
| Store API client restart | Master replica metadata | 新 client 不依赖旧进程内 map，可正常查询和读取 remote；local 由 owner endpoint 路由 |

## 14. 可观测性

至少增加：

```text
placement_decision_total{policy,target,result}
placement_decision_latency_us
storage_put_total{target,result}
storage_get_total{target,result}
storage_remove_total{target,result}
storage_operation_latency_us{operation,target}
storage_bytes_total{operation,target}
storage_revoke_total{target,reason}
storage_orphan_cleanup_total{target,result}
```

日志必须包含 key 的安全标识、object size、policy、selected target、replica type 和失败阶段；不得打印 buffer 内容。阶段四需要能单独统计 policy decision 与 metadata 的软件开销。

## 15. 风险与设计约束

### 15.1 最大风险：把 offload 当成 direct placement

若 `LOCAL_ONLY` 仍先写 MEMORY、等待异步 offload，则它不能代表“透明层直接选择本地 NVMe”，阶段四数据也不可与 NoF direct put 对比。验收时必须从 replica metadata 和 I/O telemetry 证明初始写入目标确实是 local NVMe。

### 15.2 双份 metadata

在 facade 中维护进程内 `key -> backend` map 会导致重启丢路由、跨进程不可读，并与 Master metadata 冲突。首版禁止该方案。

### 15.3 配置兼容

全局策略不能静默改写现有高级用户的显式 replication config。managed/manual 边界必须可测试、可观测。

### 15.4 本地对象所有权

本地 NVMe 不是所有 client 都能直接访问。descriptor 必须持久化 owner 和可读 endpoint；owner 离线时不能把本地路径误当成共享路径。

### 15.5 严格策略与 fallback

三种首版策略用于机制验证，失败语义必须确定。自动 fallback 会污染 local/remote 实验，应作为后续独立策略设计。

## 16. Definition of Done

- [ ] 推理 connector 中不存在 local/remote 选择逻辑。
- [ ] 无 config 的 put/get/remove 可在三种策略下运行。
- [ ] Master metadata 是唯一 placement 事实来源。
- [ ] local direct put 不是 MEMORY offload 的别名。
- [ ] NoF 继续复用既有 allocator 与 transfer lifecycle。
- [ ] get 的普通、批量和 into 路径使用统一 read-source policy。
- [ ] write 失败不会产生 COMPLETE phantom replica。
- [ ] remove 可回收两类后端并可安全重试。
- [ ] legacy/manual 行为有回归测试保护。
- [ ] 单测、集成测试、NoF e2e 和 pre-commit 全部通过。
- [ ] 阶段四 benchmark 能分别测出 local 与 NoF 的透明层增量开销。

## 17. 实施后的框架视角

完成后，推理框架看到的流程始终相同：

```text
preempt request
  -> derive KV cache key
  -> store.put/batch_put_from
  -> release accelerator cache

resume request
  -> store.get/batch_get_into
  -> restore accelerator cache
  -> store.remove when lifecycle ends
```

local、remote、round-robin 以及未来的负载感知策略只改变 Mooncake 内部决策和部署配置，不改变上述框架代码。这是本阶段“透明”和“可移植”的最终边界。
