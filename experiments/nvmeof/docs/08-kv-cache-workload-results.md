# KV-cache Workload 两节点结果

## 状态

截至 2026-08-23，两节点硬件批次
`20260823T140512Z-phase4-kv-final` 已完成。批次使用 source commit
`255736477145236f39702f44e7a523a36914828c`，五个 KV-cache case 全部通过，
阶段四透明层 strict gate 的八项输入也全部通过：

```text
transparent-acceptance.json: status=pass
required_evidence=8
failures=[]
```

原始结果保存在被 Git 忽略的目录：

```text
experiments/nvmeof/results/20260823T140512Z-phase4-kv-final/
```

其中 `RESULTS.md` 是完整的批次报告，`kv-workload/` 保存 trace、manifest、
五个 raw replay、CSV 和 conclusion，`audit/` 保存两次补充 remote overhead
复测。

## KV-cache workload

五个 case 使用同一 trace，digest 为
`3a49f1f79b9fdd5df28ad41190dfb5839524ef397052bae178b7f4e9b9d47051`。
每个 Store case 都完成 148 次操作，descriptor target 全部正确；没有 miss、
错误读取或残留对象。

| Case | Operation p50 / p95 / p99 (us) | Request p50 / p95 (us) | Operations/s | Descriptor target |
| --- | ---: | ---: | ---: | --- |
| `direct-local` | 675.301 / 1290.221 / 10783.154 | 3622.773 / 5351.482 | 1291.048 | 148 local |
| `transparent-local` | 519.070 / 1108.280 / 1580.541 | 3348.381 / 4856.214 | 1519.443 | 148 local |
| `direct-remote` | 156.730 / 243.091 / 311.680 | 908.741 / 955.361 | 2572.232 | 148 remote |
| `transparent-remote` | 157.860 / 285.810 / 381.390 | 1057.360 / 1181.170 | 2554.292 | 148 remote |
| `no_store` | 1000.000 / 1000.000 / 1000.000 | 4000.000 / 4000.000 | 1541.667 | n/a |

`no_store` latency 是固定 recompute proxy，不是模型执行时间，因此不能用来
声称 Store 比模型重计算更快。该批次只证明 workload replay、真实 Store I/O
和 target descriptor 闭环。

## 阶段四透明层验收

同一批次完成 local、remote、round-robin 生命周期，local/remote unavailable
行为，local/remote paired overhead 和五项 software verification。最终 gate
直接检查八份 JSON，结果为 `8/8 pass`。

正式 remote NoF overhead 使用 100 个 128 KiB 对象：

| 操作 | Direct p50 / p95 / p99 (ms) | Transparent p50 / p95 / p99 (ms) | Delta p50 / p95 / p99 |
| --- | ---: | ---: | ---: |
| put | 0.2432 / 0.2693 / 0.6330 | 0.2814 / 0.3058 / 0.4316 | +15.72% / +13.55% / -31.82% |
| get | 0.3338 / 0.3457 / 0.3729 | 0.3339 / 0.3456 / 0.3665 | +0.04% / -0.03% / -1.71% |
| remove | 0.0561 / 0.0618 / 0.0720 | 0.0564 / 0.0625 / 0.0658 | +0.46% / +1.10% / -8.67% |

两次同参数补充复测均为 `status=pass`。三个 run 的 remote put p50 delta 为
`+15.72% / +17.27% / +18.53%`，p95 delta 为
`+13.55% / +15.08% / +23.56%`，说明透明路径的中心延迟成本可复现。三个
direct run 的首个 put 分别达到 `32.07 / 41.10 / 37.96 ms`，使 direct p99
和全样本吞吐失真；transparent 首个 put 为 `0.83 / 0.85 / 0.73 ms`。
各 run 只剔除最大样本后，put mean 的三次平均为 direct `0.248 ms`、
transparent `0.291 ms`，与 p50/p95 的约 15--18% 成本一致。

remote get 的全样本带宽 delta 稳定在 `-4.75%` 到 `-5.54%`，但三个
transparent run 各有一个 `2.12--2.30 ms` 首样本。每 run 只剔除最大样本后，
三次 mean 平均为 direct `0.334 ms`、transparent `0.336 ms`，差约 `0.33%`；
因此不能把全样本带宽差解释为 steady-state 回归。CPU utilization 的 paired
delta 在 `-0.082` 到 `+0.027` 之间变号，短批次下没有一致的 CPU 退化方向。
NoF service window 只有连接时重复出现的既有 RDMA 配置 warning，没有记录
service restart、I/O error 或 transport failure。

结论：remote `+14%` 现象在当前 128 KiB、单客户端、100-object testbed 下为
**可接受的透明封装成本**。依据是 p50/p95 在三次测量中稳定、p99 未放大、
robust dispersion 没有数量级变化、get/remove 中心延迟接近 direct，且没有
一致 CPU 或 steady-state bandwidth 退化。该结论不把 cold-start outlier
驱动的全样本吞吐当作性能收益，也不外推到其他对象大小或并发度。

## 范围与后续

阶段四在上述批次范围内已闭环，阶段五未启动。当前证据仍只覆盖两节点、
两 SSD、单 workload 参数组合，不覆盖多 SSD 扩展、HA/restart、并发 sweep、
更多 block size 或长时间稳定性。

补充批次 `20260823T151000Z-public-trace-durable` 已完成 FAST'25 conversation
公开 trace 的 bounded durable storage-path smoke：5/20 request、local/remote、
每 cell 三次。该结果证明公开 trace harness 可以在真实持久化路径上稳定执行，
但 remote 使用 target XFS file -> SPDK AIO -> NVMe-oF -> client XFS，local 使用
客户端 ext4 上的本地 NVMe；因此它是不同 substrate 的 whole-path 对比，不是
NVMe-oF transport overhead 或系统优劣结论。完整指标和 raw evidence 见
[`10-public-trace-storage-baseline.md`](10-public-trace-storage-baseline.md)。

复现实验入口和停止条件见
[`09-kv-cache-hardware-smoke-followup.md`](09-kv-cache-hardware-smoke-followup.md)；
透明层八证据契约见
[`../../../docs/transparent-layer-phase4-acceptance-plan.md`](../../../docs/transparent-layer-phase4-acceptance-plan.md)。

## 对象大小补充 Sweep

补充批次 `20260823T151851Z-kv-size-sweep` 已完成 16/64/128/256 KiB、
每个大小三次重复的两节点 matched matrix。每个 trial 包含 `no_store`、
direct/transparent local 和 direct/transparent remote；60/60 个 case 通过，
每个 case 148 次操作、0 miss，local/remote descriptor 全部与目标一致。

remote transparent-minus-direct put p50 paired delta 的三次中位数依次为
`+17.36% / +19.78% / +17.24% / +13.99%`，对应 request p50 为
`+15.32% / +17.10% / +15.27% / +12.28%`。remote get/remove p50 接近
direct。local p50 差异较小且没有一致方向，local p95/p99 受孤立 outlier
影响，不能据此声称 tail 改善或回归。

该批次还定位并修复了一个环境问题：普通用户 Master 的 NoF heartbeat SPDK
probe 因 IOVA PA 权限失败，连续失败后卸载 segment，表面报错为
`insufficient space`；root Master 保持 heartbeat 和 64 GiB capacity active，
完成剩余 matrix 及 6 local + 6 remote 的 round-robin recovery smoke。
完整结论和原始证据见
[`11-kv-cache-size-sweep-results.md`](11-kv-cache-size-sweep-results.md)。

## Reuse Ratio 补充 Sweep

补充批次 `20260823T155044Z-kv-reuse-sweep` 固定 128 KiB，完成
0%/50%/90% reuse、每档三次重复的 matched matrix。45/45 个 case 通过，
实际 block/request hit rate 为 0%/45.8333%/91.6667%，所有 case 均为
0 miss 且 descriptor 全部匹配。

从 0% 到 90% reuse，request p50 中位数下降 29.91--40.78%。remote
transparent-minus-direct put p50 overhead 在三档中稳定为
`+15.26% / +16.79% / +14.71%`，get/remove p50 接近 direct。90% reuse 时
get-dominated request p50 overhead 降到约 0%，但 request p95 仍为 `+13.93%`，
因此只说明 put 成本被多数 reuse request 摊薄，不说明 tail 成本消失。

完整结论、分布和恢复证据见
[`12-kv-cache-reuse-sweep-results.md`](12-kv-cache-reuse-sweep-results.md)。
