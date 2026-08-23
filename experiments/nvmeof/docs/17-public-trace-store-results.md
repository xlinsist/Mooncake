# FAST'25 公开 Trace 的 Mooncake Store 重放结果

## 状态与工作负载

正式批次 `20260823T203957Z-public-trace-store` 完成 30/30 个 case：

- trace：FAST'25 `conversation` 和 `toolagent` 的前 100 个 request；
- cache model：128 KiB fixed page、64-page deterministic LRU；
- 每个 trace 三次，mode 与 trace 顺序 counterbalanced；
- 每次包含 `no_store`、direct local、transparent local、direct remote、
  transparent remote；
- 所有 replay 均为单进程顺序执行，每个 case 保留 600 秒 timeout 与 30 秒
  kill grace。

转换器直接读取公开 trace 的 `hash_ids`。resident page 记为 `reuse`，absent page
记为 `produce`，capacity overflow 记为 `evict`，结尾显式清理所有 live page。
conversation 生成 5,978 个 event（2,944 produce、90 reuse、2,944 evict），
toolagent 生成 3,524 个 event（1,575 produce、374 reuse、1,575 evict）。三个
trial 的 source digest 和转换后 trace digest 均逐 trace 唯一且一致。

正式矩阵共验证 54,228 次 put、5,568 次 get 和 54,228 次 remove；put+get 的
建模 payload 为 7,837,581,312 bytes。所有 Store operation 的 descriptor 均与
case 的 `local_nvme` 或 `remote_nof` 目标一致，六个 cell 的离线 conclusion 均为
`pass`。

## 三次重复中位数

| Trace | Case | Request p50 (ms) | Request p95 (ms) | Storage wait (ms) | Event rate |
| --- | --- | ---: | ---: | ---: | ---: |
| conversation | no_store | 21.000 | 84.000 | 0 | 1,970.34 |
| conversation | direct local | 19.392 | 73.801 | 2,846.533 | 2,100.10 |
| conversation | transparent local | 19.871 | 78.715 | 2,994.679 | 1,996.21 |
| conversation | direct remote | 6.154 | 26.556 | 928.683 | 6,437.07 |
| conversation | transparent remote | 6.835 | 29.790 | 1,024.517 | 5,834.94 |
| toolagent | no_store | 13.000 | 52.000 | 0 | 1,808.11 |
| toolagent | direct local | 8.949 | 47.546 | 1,697.071 | 2,076.52 |
| toolagent | transparent local | 9.588 | 50.832 | 1,868.599 | 1,885.90 |
| toolagent | direct remote | 2.595 | 15.631 | 567.427 | 6,210.50 |
| toolagent | transparent remote | 2.907 | 17.414 | 618.564 | 5,697.07 |

`no_store` 使用固定 1 ms/event recomputation proxy，因此只能作为可复现的模型，
不能解释为真实 prefill 或 GPU serving 时间。

## Paired transparent overhead

| Trace | Target | Request p50 | Request p95 | Storage wait | Event rate |
| --- | --- | ---: | ---: | ---: | ---: |
| conversation | local | +2.47% | +4.48% | +4.06% | -3.90% |
| conversation | remote | +11.95% | +11.78% | +10.72% | -9.68% |
| toolagent | local | +8.42% | +6.94% | +9.82% | -8.94% |
| toolagent | remote | +12.08% | +11.61% | +9.01% | -8.27% |

因此在这两个 100-request、64-page、顺序 workload 中，transparent-minus-direct
request p50 中位数为 `+2.47--+12.08%`，p95 为 `+4.48--+11.78%`。这组结果
直接补上了真实 FAST'25 `hash_ids` 经 Mooncake Store `put/get/remove` 的覆盖，
但不能外推到完整 trace、不同 cache budget、paced arrival 或并发 workload。

## 失败 pilot 与修复

第一个批次 `20260823T203650Z-public-trace-store` 在六个 `no_store` case 后，于
首个 direct-local 的 event 778 严格停止。公开 trace 会在 eviction 后再次出现
同一 page ID；旧 replay 用固定 `{run}-{case}-{page}` key，第二次 produce 被
Master 正确拒绝为 `object_already_exists`。该批次的 EXIT 路径恢复了 root-owned
`round_robin` Master，最终 transparent smoke 也通过。

修复后 replay 为每次 produce 增加单调 generation suffix；reuse/evict 始终解析
当前 live generation。回归测试覆盖 produce -> evict -> re-produce -> reuse ->
evict，Mooncake commit `0e317482` 保存该修复。失败 pilot 原样包含在正式 artifact，
没有混入正式 30-case 聚合。

## 恢复、硬件与 claim 边界

正式批次前后 client Master policy 均为 `round_robin`；最终 transparent recovery
smoke 完成 produce/read/remove 且无 error。client 最终仍只有既有 NoF namespace
`/dev/nvme2n1`。实验不创建 target subsystem、bdev、namespace、mount 或 image，
也不 restart/unregister target。结束后 target service 仍为 active/running、
`MainPID=2072748`，subsystem 仍只有 discovery 与
`nqn.2026-08.local.mooncake:nof-phase1`，bdev 仍只有 `Nvme0n1`。

本实验是 GPU-free、unpaced、顺序 Store replay。它没有使用 trace timestamp 驱动
arrival，也没有 concurrent Store call；因此不能支持 true concurrency、TTFT、
TPOT、goodput、transport speedup 或系统优越性主张。local 与 remote 的绝对延迟
还包含不同 backend 的 whole-path 差异，只有同 target 的 direct/transparent
paired delta 可用于透明层开销表述。

权威 artifact 位于
`experiments/nvmeof/results/20260823T203957Z-public-trace-store/`，包含正式 raw
archive、失败 pilot archive、逐 trial 与三重复 aggregate、严格 conclusion、
控制器、聚合器、设计和 checksum。
