# PutStart → 远程 NoF → PutEnd：MVP 调用链与数据流

> 本文以当前仓库的 Mooncake Store 实现为准，抽取一个最小可用（MVP）场景：客户端向已挂载的远程 NoF（代码中的 `NOF_SSD` / `remote_nof`）写入一个对象，流程从 `PutStart` 开始，到 `PutEnd` 提交结束。
>
> 重要结论：**纯远程 NoF MVP 的数据面不经过 POSIX 文件系统**。它不是 `open/pwrite/fsync` 文件写入，而是 Master 分配远程 NoF 地址后，客户端通过 Transfer Engine/SPDK NoF 直接向远程 NoF 端点做块 I/O。文件系统只在本地磁盘（`DISK` / `LOCAL_DISK`）路径中出现；文末给出对照。

## 1. MVP 前置条件与输入

假设：

- 编译时启用 `USE_NOF`。
- Master 已通过 `MountNoFSegment` 挂载一个 NoF segment；测试构造的 segment 至少包含 `id`、`name`、`base`、`size`、`te_endpoint`，见 `mooncake-store/tests/heterogeneous_storage_test.cpp:82`。
- 客户端已经创建并初始化 `TransferSubmitter`，且底层 Transfer Engine / SPDK NoF 能访问 `te_endpoint`。
- 只请求一个远程 NoF 副本：

```cpp
std::vector<Slice> slices = {{src_ptr, object_size}};
ReplicateConfig config;
config.replica_num = 0;
config.nof_replica_num = 1;

client->Put("k", slices, config);
```

`Client::Put` 实际按所有 slice 长度求和，把 `object_size` 作为一次 placement 请求发送给 Master；请求结构是 `ManagedPlacementStartRequest{client_id, key, value_length, config, tenant_id}`，见 `mooncake-store/src/master_client.cpp:615`。

## 2. 一次成功写入的总览

```text
应用
  │ Client::Put(key, slices, config)
  ├─① MasterClient::PutStart
  │    └─RPC PutStartManaged
  │        └─MasterService::PutStart
  │            └─AllocateAndInsertMetadata
  │                └─NoF allocator 分配远程地址/句柄
  │                    └─写入 PROCESSING 元数据并返回 NoFDescriptor
  │
  ├─② Client::TransferWrite(NoFDescriptor, slices)
  │    └─TransferData(WRITE)
  │        └─连续性/4 KiB 对齐处理
  │            └─TransferSubmitter::submit
  │                └─submitSpdkNofOperation
  │                    └─OpenNofSegment(te_endpoint)
  │                    └─SPDK NoF task → 远程 NoF 块设备
  │
  └─③ MasterClient::PutEnd(ObjectMeta{key}, NOF_SSD)
       └─RPC PutEndPlacement
           └─MasterService::PutEnd
               └─PROCESSING → COMPLETE
               └─清理 processing_keys / 结算 quota / 发布可读元数据
```

这里的 `PutStart` 是“分配并登记写入中的副本”，不是数据已经持久化；只有远程传输成功并且 `PutEnd` 成功后，副本才被 Master 视为完成。

## 3. PutStart：控制面与元数据变化

### 3.1 客户端计算对象大小并发起 RPC

调用顺序：

1. `Client::Put`（`mooncake-store/src/client_service.cpp:1942`）可选计算 checksum，然后遍历 `slices` 收集每个 slice 的长度。
2. 调用 `master_client_.PutStart(key, slice_lengths, client_cfg)`（约 `client_service.cpp:1971`）。`client_cfg` 会补充客户端 host 信息；CXL 协议还会设置 preferred segment，但本例使用 NoF。
3. `MasterClient::PutStart` 将多个 slice 长度相加为 `total_slice_length`，构造 `ManagedPlacementStartRequest`，经 `PutStartManaged` RPC 发送给 Master（`master_client.cpp:615-630`）。

### 3.2 Master 校验与选择 NoF allocator

`MasterService::PutStart`（`mooncake-store/src/master_service.cpp:4200` 附近）执行：

- 解析 placement 配置、校验 key 和长度；若没有 memory/NoF/local 副本，或长度为 0，则返回 `INVALID_PARAMS`。
- 若请求 NoF 但未编译 `USE_NOF`，返回 `INVALID_PARAMS`；若 NoF segment 未挂载，严格配置通常最终返回 `NO_AVAILABLE_HANDLE`。
- 以对象身份 `(tenant_id, key)` 加锁并查找 metadata：
  - 已有完成副本，或未完成的 PutStart 尚未过期：`OBJECT_ALREADY_EXISTS`。
  - 过期的旧 processing 元数据可能被清理；旧 processing replica 会进入 discarded 列表，等待 release timeout 回收。
- 调用 `AllocateAndInsertMetadata`（`master_service.cpp:3928`）。

在 `AllocateAndInsertMetadata` 中：

1. 按对象大小和配置计算待收取的 quota，并先占用 pending quota。
2. 因 `config.nof_replica_num == 1`，从 `nof_segment_manager_` 获取 allocator，使用 `allocation_strategy_->Allocate(..., ReplicaType::NOF_SSD)` 分配一个 NoF replica（`master_service.cpp:4042-4074`）。
3. allocator 从已挂载 NoF segment 的空闲空间中取得一块连续区域，形成 NoF replica。其 descriptor 至少携带：
   - `buffer_address_`：远端 NoF 区域的逻辑块地址/偏移对应地址；
   - `size_`：可写容量；
   - `transport_endpoint_`：NoF 的访问端点。
4. 把副本状态设为 `PROCESSING`，生成 `Replica::Descriptor`（变体类型为 `NoFDescriptor`）。
5. 在 Master metadata 中插入对象记录，关键状态为：

```text
metadata[key] = {
  client_id       = 当前 Put 发起者,
  put_start_time  = now,
  size            = object_size,
  replicas        = [NoFReplica(PROCESSING, allocated handle)],
  processing_keys += key,
  quota           = 已占用但尚未最终结算
}
```

6. 注册 group/member、soft-pin 等相关索引（如果配置启用），返回 `vector<Replica::Descriptor>` 给客户端。

此时数据仍在客户端的 `slices` 中，远端 NoF 区域只是“已分配、待写入”；`GetReplicaList` 不应把它当成可读完成对象。仓库测试也先验证 PutStart 后对象尚未 ready，再调用 PutEnd（`mooncake-store/tests/master_service_ssd_test.cpp` 中同类生命周期测试）。

## 4. 数据面：从客户端 buffer 到远程 NoF

### 4.1 客户端选择 NoF 副本

`Client::Put` 收到 PutStart 返回的副本列表后：

- 记录每个已分配副本；
- 跳过 `DISK` / `LOCAL_DISK` 分支；
- 对 `is_nof_replica()` 的 descriptor 调用 `TransferWrite(replica, slices)`，副本类型传为 `ReplicaType::NOF_SSD`（`client_service.cpp:2020` 附近）。

### 4.2 NoF 特有的 buffer 约束

`Client::TransferData`（`client_service.cpp:4318`）对 NoF 做额外处理：

1. 调用 `GetContiguousSliceRange(slices)`；如果多个 slice 不能组成连续范围，直接返回 `INVALID_PARAMS`。因此本 MVP 最好使用一个连续 slice。
2. 检查源 buffer 是否满足 4096 字节对齐。
3. 若不满足，使用 `hugepage_memory_alloc` 分配对齐 staging buffer；写操作先 `memcpy`：

```text
非对齐应用 buffer ──memcpy──> 对齐 staging buffer
连续/已对齐应用 buffer ───────────────┐
                                      └─提交 NoF transfer
```

4. 调用 `transfer_submitter_->submit(replica_descriptor, slices, WRITE, transfer_ptr, size)` 并等待 future 完成。

### 4.3 TransferSubmitter / SPDK NoF

启用 `USE_NOF` 时，NoF 分支最终进入 `TransferSubmitter::submitSpdkNofOperation`（`mooncake-store/src/transfer_task.cpp:1396`）：

1. 校验 `transport_endpoint_` 非空、descriptor 容量不小于请求大小。
2. `SpdkWrapper::OpenNofSegment(transport_endpoint)` 打开远程 NoF segment。
3. 从 SPDK 获取 block size，并检查：
   - descriptor 中的远端地址按 block size 对齐；
   - 请求大小按 block size 对齐；
   - 本地传输指针按 block size 对齐。
4. 构造 `SpdkNofTask(seg_handle, ptr, buffer_address / block_size, size / block_size, WRITE, state)`，提交到 `spdk_nvmf_pool_`。
5. future 完成后返回 `ErrorCode::OK`；如果之前用了 staging buffer，写成功后无需回拷，写失败则释放并走失败收尾。

数据流可以概括为：

```text
应用 slices
  →（必要时）hugepage 对齐 staging buffer
  → TransferSubmitter
  → SPDK NoF task
  → NoF transport_endpoint
  → 远程 NoF segment 的 block_offset / block_count
```

这里没有对象文件名、目录项或 POSIX inode；NoF descriptor 中的地址、长度、端点就是数据面定位信息。

## 5. PutEnd：提交和元数据变化

### 5.1 客户端收尾决策

当 NoF `TransferWrite` 成功时，`Client::Put` 将成功计入 `ReplicaTransferSummary`，随后决定对 NoF 副本执行 End；调用：

```cpp
ObjectMeta meta;
meta.key = key;
master_client_.PutEnd(meta, ReplicaType::NOF_SSD);
```

`MasterClient::PutEnd` 构造 `PlacementEndRequest{client_id, object_meta, NOF_SSD, tenant_id}`，调用 `PutEndPlacement` RPC（`master_client.cpp:662-670`）。本例没有本地文件 locator，因此 `ObjectMeta` 主要只携带 key；若启用 checksum，可携带 `object_checksum`。

若传输失败，则不应 PutEnd；正常失败路径应调用 `PutRevoke(key, NOF_SSD)`，释放 processing NoF replica 和相关 quota，避免留下“看起来已分配但不可读”的副本。

### 5.2 Master 将指定 NoF replica 标记为完成

`MasterService::PutEnd`（`master_service.cpp:4388`）依次做：

1. 查找 metadata；校验对象存在，且 `client_id` 必须等于 PutStart 的 owner。
2. 根据 `replica_type == NOF_SSD` 只选择 NoF replica，并排除 invalid NoF handle。
3. 要求 primary write 仍在 processing；遍历匹配的 `PROCESSING` replica，调用 `replica.mark_complete()`（约 `master_service.cpp:4500-4512`）。
4. 若这是对象首次产生 completed replica，提交 pending soft-pin，并更新 soft-pin 状态。
5. 更新 checksum（对 `NOF_SSD` End 会接受 `object_meta.object_checksum`）。
6. 结算 primary write quota；此时 pending quota 变为已提交对象所占 quota。
7. 如果所有 replica 都是 `COMPLETE` 或 `REMOVED`，从 `processing_keys` 删除 key。
8. 同步 cache accounting，执行 `metadata.GrantReadLease(0)`，调用 `PublishKvStored` 发布“对象已存储”。
9. 若启用 ordered oplog，序列化 metadata 并追加 `OpType::PUT_END` 日志（`master_service.cpp:4577-4585`）。

最终状态：

```text
metadata[key].replicas[NoF].status: PROCESSING → COMPLETE
processing_keys:                   key → 删除（若无其他 processing replica）
quota:                             pending → settled
read visibility:                   不可读/未 ready → 可通过 Query/Get 读取
object_checksum:                  按请求更新（若启用）
physical NoF bytes:               已在 PutEnd 之前由数据面写入；PutEnd 本身不再写数据
```

## 6. “经过文件系统”到底发生了什么？

### 6.1 纯远程 NoF MVP：没有 POSIX 文件系统步骤

本例中以下操作**不会发生**：

- 不调用 `open()` / `close()`；
- 不创建目录或文件名；
- 不调用 `pwrite()` / `pread()`；
- 不调用 `fsync()` / `fdatasync()`；
- 不通过 `StorageBackend::StoreObject` 写对象文件；
- `PutEnd` 不做文件 rename、truncate 或目录元数据提交。

“持久化”的边界是：SPDK NoF task 成功完成远端块写入，随后 Master 接受 `PutEnd`，把逻辑副本状态从 `PROCESSING` 提交为 `COMPLETE`。远程存储服务是否在其内部使用文件系统、如何落盘，不属于本客户端/本仓库这条 NoF 数据路径的调用链。

### 6.2 对照：如果 PutStart 同时/改为本地磁盘 `DISK`

这不是上面的纯 NoF MVP，但容易混淆，故单独列出：

1. `MasterService::AllocateAndInsertMetadata` 在 `use_disk_replica_` 时用 `ResolvePathFromKey(key, root_fs_dir_, cluster_id_)` 生成文件路径，并加入 `DiskReplica(PROCESSING)`。
2. `Client::Put` 发现 disk replica 后调用 `PutToLocalFile`；代码会把 device slice 做 D2H（如有需要），再异步调用 `StorageBackend::StoreObject`。
3. `StorageBackend::StoreObject` / 其 backend 才负责本地文件的创建、写入和 backend 元数据；这条路径才涉及 POSIX/IO 语义。
4. 本地文件成功后客户端以 `ReplicaType::DISK` 调 `PutEnd`；失败则 `PutRevoke`。

对于 `LOCAL_DISK` direct backend，`ObjectMeta` 还必须携带 `local_disk_transport_endpoint`、`local_disk_backend_id`、`local_disk_locator`；`MasterService::PutEnd` 会把这些 locator 写回 `LocalDiskReplicaData`，并更新 `ssd_used_bytes`。这些字段**不属于远程 NoF MVP**。

## 7. 失败、回收与可观测点

| 阶段 | 失败示例 | Master/客户端后果 |
|---|---|---|
| PutStart | 无 NoF segment、容量不足、未启用 `USE_NOF` | 不插入新对象，或释放 pending quota；返回 `NO_AVAILABLE_HANDLE` / `INVALID_PARAMS` |
| NoF transfer | 非连续 slices、地址/大小未按 block 对齐、OpenNofSegment 失败、SPDK task 失败 | 不应 PutEnd；调用 `PutRevoke(NOF_SSD)`，回收 processing replica |
| PutEnd | 对象不存在、client 不匹配、没有 primary write、descriptor invalid | 对象保持 processing 或进入错误收尾；需要按错误类型重试/撤销 |
| 客户端中断 | PutStart 后长期不 PutEnd | Master 的 stale cleanup / discard timeout 清理 metadata 和句柄；另有 NoF heartbeat 负责发现 segment 不健康 |

可观察指标包括客户端 `put` 到 `remote_nof` 的成功/失败与字节数/延迟，以及 Master 的 PutStart/PutEnd 计数、NoF 分配/容量/heartbeat 指标。测试中 `remote_nof` 也是单独的 storage target 标签。

## 8. 与仓库实现对应的最小证据索引

- 客户端主流程：`mooncake-store/src/client_service.cpp:1942`、`mooncake-store/src/client_service.cpp:1971`、`mooncake-store/src/client_service.cpp:2020`。
- MasterClient RPC 封装：`mooncake-store/src/master_client.cpp:615`、`mooncake-store/src/master_client.cpp:662`。
- Master NoF 分配与 metadata 插入：`mooncake-store/src/master_service.cpp:3928`、`mooncake-store/src/master_service.cpp:4042`。
- Master PutEnd 状态提交：`mooncake-store/src/master_service.cpp:4388`、`mooncake-store/src/master_service.cpp:4500`、`mooncake-store/src/master_service.cpp:4577`。
- NoF 对齐与传输：`mooncake-store/src/client_service.cpp:4318`、`mooncake-store/src/transfer_task.cpp:1396`。
- NoF segment 测试构造与 PutStart/PutEnd 生命周期：`mooncake-store/tests/heterogeneous_storage_test.cpp:72`、`mooncake-store/tests/heterogeneous_storage_test.cpp:486`。
- 本地文件对照入口：`mooncake-store/src/client_service.cpp:2040` 附近的 `PutToLocalFile`，以及 `mooncake-store/src/storage_backend.cpp` 的 `StorageBackend::StoreObject`。
