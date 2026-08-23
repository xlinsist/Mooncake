# KV-cache 对象大小 Sweep 结果

## 状态与配置

两节点批次 `20260823T151851Z-kv-size-sweep` 已完成 4 个对象大小、每个大小
3 次重复和每次 5 个 matched case，共 60 个 case：

- value size：16、64、128、256 KiB；
- case：`no_store`、direct local、transparent local、direct remote、
  transparent remote；
- 24 requests、每 request 4 blocks、reuse ratio 50%、seed 42；
- `configured_concurrency=1`，replay 仍为顺序执行；
- 每个 trial 交替 direct/transparent 顺序。

最终 gate 为 `status=pass`：12/12 个 size/trial cell 完整，60/60 个 case
存在。每个 case 执行 148 次操作并保持 0 miss；24 个 local Store case 各有
148 个 local descriptor，24 个 remote Store case 各有 148 个 remote
descriptor。每个 size 的三次重复使用同一 trace digest。

## Paired overhead

下表报告每个 size 三次 transparent-minus-direct paired delta 的中位数：

| Size | Target | Put p50 | Request p50 | Get p50 | Remove p50 |
| ---: | --- | ---: | ---: | ---: | ---: |
| 16 KiB | remote NoF | +17.36% | +15.32% | -1.91% | +0.40% |
| 64 KiB | remote NoF | +19.78% | +17.10% | +0.45% | -0.20% |
| 128 KiB | remote NoF | +17.24% | +15.27% | +0.89% | +0.02% |
| 256 KiB | remote NoF | +13.99% | +12.28% | +1.81% | -0.89% |
| 16 KiB | local NVMe | +5.79% | +5.97% | -1.47% | +0.32% |
| 64 KiB | local NVMe | +4.32% | +6.48% | -3.18% | -0.12% |
| 128 KiB | local NVMe | -0.00% | -1.82% | +0.10% | +1.04% |
| 256 KiB | local NVMe | +2.02% | +1.78% | -0.72% | +0.43% |

remote put p50 是最稳定的 size-dependent 结果：三次重复的 paired delta
范围分别为 `17.24--19.91%`、`16.27--20.34%`、`15.74--17.82%` 和
`12.74--14.65%`。remote request p50 的 paired 中位数也保持在
`+12.28--17.10%`。相反，remote get/remove p50 接近 direct。

local p50 没有一致的退化趋势；部分 local p95/p99 被单个大 outlier 主导，
甚至在三次重复间变号。因此本批次不支持 local tail 改善或回归结论，也不能
把四个 size 点外推为单调 scaling law。

## 环境故障与修复

批次中途首次出现 `put returned -200` 和 `insufficient space`。该错误不是
物理容量耗尽：普通用户运行的 Master 无法完成 NoF heartbeat SPDK probe，
日志包含 `spdk_env_init_fail` 和 IOVA PA physical-address 错误；连续三次
heartbeat 失败后 Master 卸载了 NoF segment。

把 Master 改为 root 后，64 GiB NoF capacity 持续保持 active，最终 retained
log 不含 `nof_heartbeat_failure` 或 `unmount_nof_segment_by_heartbeat`，剩余
matrix 全部完成。修复后的 root `round_robin` smoke 写入、校验并删除 12 个
对象，local/remote 各 6 个，`phantom_replicas=0`。首次失败、修复日志、最终
client/target inventory 和 smoke JSON 均保存在 raw archive 中。

## 证据与边界

Git 内证据入口为
`experiments/nvmeof/results/20260823T151851Z-kv-size-sweep/`。摘要 CSV/JSON
支持直接复核，压缩包保存完整远端批次，`SHA256SUMS` 固定其内容。

该批次只覆盖两节点、单客户端、顺序 replay、50% reuse 和三次重复。它扩展了
对象大小证据，但不验证真实 concurrency、负载敏感 placement、serving SLO
或多 target scaling。已有 true-concurrency 计划仍因缺少正式 host consensus
receipt 而未获执行授权，本结果不能替代该 gate。
