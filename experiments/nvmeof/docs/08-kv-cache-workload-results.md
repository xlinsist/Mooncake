# KV-cache Workload 当前结果

## 状态

截至 2026-08-21，离线 workload 链路已验证；两节点 Store replay 尚未完成。

离线结果目录为被 Git 忽略的：

```text
results/kv-workload-offline-20260820/
```

其中完成了 `4 block sizes × 3 reuse ratios × 3 concurrency levels = 36`
个 deterministic trace/no-store cases。每个 case 都生成了 trace、manifest、
raw replay JSON、CSV 汇总和 `conclusion.json`，36 个 case 均为
`status=pass`。

## 离线结果能说明什么

- 固定 seed 和参数可以生成可复现的 JSONL trace；
- `produce/reuse/evict` 生命周期和 block 依赖关系通过校验；
- `reuse_ratio` 会反映在 request/block hit-rate 统计中；
- no-store replay 可以生成 request latency、operation rate 和 cache 指标；
- 汇总器会拒绝缺失 case、失败操作、混合 run ID 或 trace digest。

这些 latency 使用固定的 `no_store recompute` 时间模型，只是 workload
proxy，不是模型执行时间，也不是 Mooncake 存储性能结果。

## 两节点硬件状态

本次仍未形成 local/direct/transparent 的真实 Store workload 结果。2026-08-21
只读预检证据如下：

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| 本地/客户端提交同步 | local `31735b80c39954cdeca36f4d45b9bcbc5f6634c1`；客户端 `da2f5be07f1901ba1825da0caa94d65698dd2726` | **fail** |
| 客户端远端工作树 | 11 条未提交修改/未跟踪项；未覆盖 | **blocker** |
| Master | `10.0.0.34:50051` TCP 可连接，`ss` 显示 LISTEN | pass |
| NoF service | `intel-bigmem`: `mooncake-nof-spdk.service` = `active` | pass |
| `sudo -n` | 客户端本地 `sudo -n true` 返回 `sudo: a password is required` | **blocker** |
| 远端 workload 构件 | `kv_workload.py` 在源码树存在；`build-nof`、`mooncake_master`、`nof_worker_pool_bench` 和 Python binding 均存在且 binding 可导入 | pass（但提交不匹配） |

由于提交未同步、远端工作树 dirty 且客户端非交互 sudo 不可用，硬件写入被
明确停止；没有伪造五个 case 的 raw JSON、CSV 或 conclusion。目标机 service
active 和 Master 已监听不能抵消这些客户端安全门槛。

因此不能从本文件推出 local/remote 的 request-level 优劣、transparent
收益、TTFT proxy 或两节点硬件 workload 结论。硬件阶段必须在 Master、
匹配 Python binding、NoF 注册和非交互权限都通过预检后重新执行。

## 下一次硬件执行入口

使用同一 trace manifest，分别执行 `no_store`、指定 backend 的 `direct` 和
Master policy 驱动的 `transparent`，再运行：

```bash
KV_WORKLOAD_RESULT_DIR=results/kv-workload-<run-id> \
  ./run.sh kv-workload-replay
KV_WORKLOAD_RESULT_DIR=results/kv-workload-<run-id> \
  KV_WORKLOAD_REQUIRED_CASES=no_store,direct-local,transparent-local,\
direct-remote,transparent-remote \
  ./run.sh kv-workload-summarize
```

只有真实 `put/get/remove` 全部成功、descriptor 与目标一致、每个 case 有
完整重复且 summary 为 `status=pass` 时，才能更新 request-level 结论。

当前结论范围仍限制在两节点、两 SSD 拓扑；不声称多 SSD 扩展性、HA 或
NoF 服务恢复能力。
