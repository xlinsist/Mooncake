# FAST'25 公开 Trace 的 500-request Store 规模结果

## 状态与规模

正式批次 `20260823T205337Z-public-trace-store-r500` 完成 30/30 个 case：

- trace：FAST'25 `conversation` 和 `toolagent` 的前 500 个 request；
- cache model：128 KiB fixed page、64-page deterministic LRU；
- 每个 trace 三次，每次包含 `no_store`、direct local、transparent local、
  direct remote、transparent remote；
- replay 保持单进程顺序语义，case timeout/kill grace 仍为 600/30 秒；
- 与 100-request 批次使用相同 source trace、Store binding、Master policy 和
  descriptor gate。

conversation 生成 27,867 个 event（13,705 produce、457 reuse、13,705 evict），
toolagent 生成 17,460 个 event（7,695 produce、2,070 reuse、7,695 evict）。正式
矩阵共验证 256,800 次 put、30,324 次 get 和 256,800 次 remove；put+get 的建模
payload 为 37,633,916,928 bytes。六个 cell 的 trace/source digest 一致，所有
Store descriptor 均精确匹配 case target，六个 conclusion 和最终 recovery smoke
均为 `pass`。

## 三次重复中位数

| Trace | Case | Request p50 (ms) | Request p95 (ms) | Storage wait (ms) | Event rate |
| --- | --- | ---: | ---: | ---: | ---: |
| conversation | no_store | 18.000 | 98.000 | 0 | 1,967.73 |
| conversation | direct local | 16.612 | 87.259 | 13,275.901 | 2,099.07 |
| conversation | transparent local | 17.509 | 90.526 | 14,025.866 | 1,986.83 |
| conversation | direct remote | 5.280 | 29.077 | 4,205.293 | 6,626.65 |
| conversation | transparent remote | 5.860 | 32.507 | 4,670.421 | 5,966.70 |
| toolagent | no_store | 13.000 | 54.000 | 0 | 1,788.02 |
| toolagent | direct local | 8.090 | 57.272 | 8,219.432 | 2,124.23 |
| toolagent | transparent local | 8.016 | 52.331 | 8,492.527 | 2,055.93 |
| toolagent | direct remote | 2.566 | 16.220 | 2,669.730 | 6,539.99 |
| toolagent | transparent remote | 2.648 | 18.031 | 2,930.407 | 5,958.22 |

`no_store` 仍是固定 1 ms/event proxy，不是模型 prefill 时间。绝对 local/remote
差异也包含不同 backend whole path；只有同 target 的 paired direct/transparent
delta 可用于透明层开销分析。

## Paired overhead 与分布

| Trace | Target | Request p50 median [range] | Request p95 median [range] | Storage wait median [range] | Event rate median [range] |
| --- | --- | ---: | ---: | ---: | ---: |
| conversation | local | +5.41% [+4.76,+8.01] | +4.74% [+3.18,+4.94] | +5.59% [+4.64,+6.07] | -5.29% [-5.72,-4.44] |
| conversation | remote | +11.06% [+8.64,+11.99] | +11.79% [+10.93,+12.71] | +11.25% [+10.38,+12.15] | -10.11% [-10.83,-9.41] |
| toolagent | local | -1.53% [-3.00,+6.83] | -8.63% [-10.07,+5.72] | +2.49% [+2.07,+4.19] | -2.43% [-4.02,-2.03] |
| toolagent | remote | +3.32% [+1.32,+4.06] | +11.17% [+8.11,+12.01] | +10.33% [+8.03,+10.67] | -9.36% [-9.64,-7.43] |

toolagent-local 的 request quantile 在两次 trial 中出现负 delta，但这不是透明层
speedup：三个 trial 的 total storage wait 均增加 `2.07--4.19%`，event rate 均
下降 `2.03--4.02%`。request-level quantile 受请求组成、离散排序和 mode 顺序
影响，因此结论应同时保留 trial range、storage-wait 总量与 event-rate 方向，不能
只引用一个负的 quantile 中位数。

## 100 到 500 request 的规模关系

相同 64-page budget 下，conversation block hit rate 从 `2.9664%` 增至
`3.2269%`，toolagent 从 `19.1893%` 增至 `21.1982%`。remote storage-wait
transparent overhead 在两个规模上较稳定：conversation 为 `10.72% -> 11.25%`，
toolagent 为 `9.01% -> 10.33%`；remote event-rate delta 为
`-8.27-- -10.11%`。local overhead 更依赖 trace/request distribution，尤其
toolagent request quantile 不能由 100-request 结果外推。

因此，本批次把真实 FAST'25 Store evidence 从 100 扩大到 500 request，并表明
remote wrapper 的总存储等待开销在该范围内约为 9--11%。它仍不是完整
12,031/23,608-request trace、arrival-paced replay、cache-budget sweep、true
concurrency 或 GPU serving 结果。

## 恢复与硬件连续性

target service、subsystem JSON、bdev JSON 和 client namespace 的 before/after
文件精确一致。target service 始终 active/running、`MainPID=2072748`；NQN 仍只有
discovery 与既有 `nqn.2026-08.local.mooncake:nof-phase1`，bdev 仍只有
`Nvme0n1`。client Master 从 root `round_robin` 开始，经历 local/remote phase 后
恢复为 root `round_robin`，最终 transparent smoke 通过。

实验没有创建、删除或重启 target subsystem/bdev/service，也没有新增 namespace、
mount 或 image。权威 artifact 位于
`experiments/nvmeof/results/20260823T205337Z-public-trace-store-r500/`，包含完整 raw
archive、逐 trial/aggregate CSV、100/500 scale 对照、硬件恢复 verifier、设计、
控制器和 checksum。
