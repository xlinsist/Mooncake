# KV-cache Reuse Ratio Sweep 结果

## 状态与配置

两节点批次 `20260823T155044Z-kv-reuse-sweep` 已完成 0%/50%/90% 三档
configured reuse、每档三次重复和每次五个 matched case，共 45 个 case：

- value size 固定为 128 KiB；
- case 为 `no_store`、direct/transparent local 和 direct/transparent remote；
- 24 requests、每 request 4 blocks、seed 42；
- `configured_concurrency=1`，replay 仍为顺序执行；
- direct/transparent 顺序按 trial 交替，reuse 档顺序按 trial 轮换。

最终 gate 为 `status=pass`：9/9 个 ratio/trial cell 完整，45/45 个 case
存在。0%/50%/90% reuse 的每 case operation 数分别为 192/148/104，实际
block/request hit rate 为 0%/45.8333%/91.6667%。所有 case 均为 0 miss；
local/remote Store case 的每个 operation 均有且只有正确 target descriptor。
每个 reuse 档的三次重复共享同一 trace digest。

## Reuse 对请求延迟的影响

下表为三次重复的 request p50 中位数：

| Reuse | `no_store` | Direct local | Transparent local | Direct remote | Transparent remote |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | 4000.000 us | 3258.121 us | 3429.502 us | 922.210 us | 1078.441 us |
| 50% | 4000.000 us | 3189.152 us | 3359.491 us | 914.209 us | 1053.700 us |
| 90% | 4000.000 us | 2188.311 us | 2163.701 us | 646.372 us | 638.700 us |

从 0% 到 90% reuse，request p50 分别下降：direct local `32.84%`、
transparent local `36.91%`、direct remote `29.91%`、transparent remote
`40.78%`。这说明在当前 synthetic trace 中，get-dominated request 可以摊薄
produce/evict 的存储等待，但不能外推为模型 serving 收益。

`no_store` 在三档中都固定为 4000 us，因为每个 request 的四个 produce/reuse
事件都使用 1000 us recompute proxy；它不是实际 prefill 时间。operation 数随
reuse 上升而减少，因而跨 reuse 比较 operation rate 也没有独立系统含义。

## Transparent overhead

remote transparent-minus-direct 的三次 paired delta 中位数为：

| Reuse | Put p50 | Get p50 | Remove p50 | Request p50 | Request p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0% | +15.26% | n/a | +0.44% | +15.31% | +18.68% |
| 50% | +16.79% | +0.28% | +1.06% | +16.74% | +17.27% |
| 90% | +14.71% | -0.48% | +0.02% | +0.01% | +13.93% |

remote put p50 的透明层成本在三档中稳定为约 15--17%，get/remove p50 接近
direct。90% reuse 时，24 个 request 中有 22 个是 reuse request，因此
get-dominated request p50 的 paired delta 降到约 0%；但 request p95 仍为
`+13.93%`，对应少数 produce request 的 put 成本。不能据此声称透明层 tail
开销已经消失。

local request p50 paired 中位数为 `+5.26% / +4.19% / -1.60%`，但 90% 档
三次范围为 `-32.33%` 到 `+0.23%`，local tail 也存在 outlier，因此只保留为
分布证据，不作一致方向结论。

## 环境与恢复

local、remote 和最终恢复阶段的 Master 均以 root 运行，所有 Master log 均不含
`nof_heartbeat_failure` 或 `unmount_nof_segment_by_heartbeat`。每次 policy
切换后重新注册 NoF，remote phase 还在每个 cell 前再次注册。

矩阵结束后已恢复 root `round_robin` Master 和 NoF 注册。最终 12-object smoke
完成写入、子进程读取校验和删除，local/remote 各 6 个，
`phantom_replicas=0`。目标 SPDK service 和既有 NoF namespace 保持 active。

## 证据与边界

Git 内证据入口为
`experiments/nvmeof/results/20260823T155044Z-kv-reuse-sweep/`。摘要 CSV/JSON
支持直接复核，压缩包保存完整远端批次、执行脚本、环境 inventory 和原始日志，
`SHA256SUMS` 固定其内容。

该批次只覆盖 128 KiB、两节点、单客户端和顺序 replay。它补齐了 reuse axis，
但不验证真实 concurrency、模型 prefill、TTFT/TPOT、背景负载、多 target scaling
或 in-flight failure。已有 true-concurrency 计划仍因缺少正式 host consensus
receipt 而未获执行授权，本结果不能替代该 gate。
