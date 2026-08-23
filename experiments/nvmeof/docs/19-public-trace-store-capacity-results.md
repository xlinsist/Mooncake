# FAST'25 公开 Trace 的 Store Cache-budget 结果

## 状态与实验轴

正式 cache-budget 证据由三个 500-request checkpoint 组成：

- 16 pages：`20260823T210840Z-public-trace-store-r500-c16`；
- 64 pages：`20260823T205337Z-public-trace-store-r500`；
- 256 pages：`20260823T211747Z-public-trace-store-r500-c256`。

每个 checkpoint 均覆盖 conversation/toolagent、三次重复，以及 `no_store`、
direct/transparent local、direct/transparent remote 五个 case。三个矩阵合计
90/90 case 通过，共验证 778,056 次 put、83,316 次 get、778,056 次 remove 和
112,901,750,784 bytes put+get 建模 payload。所有 descriptor、trace digest、
cell conclusion、最终 recovery 和硬件恢复 gate 均为 `pass`。

## Capacity 对命中与操作组成的影响

| Trace | Pages | Events | Produce | Reuse | Request hit rate | Block hit rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| conversation | 16 | 28,097 | 13,935 | 227 | 45.4% | 1.60% |
| conversation | 64 | 27,867 | 13,705 | 457 | 91.4% | 3.23% |
| conversation | 256 | 27,825 | 13,663 | 499 | 99.8% | 3.52% |
| toolagent | 16 | 18,464 | 8,699 | 1,066 | 32.2% | 10.92% |
| toolagent | 64 | 17,460 | 7,695 | 2,070 | 79.6% | 21.20% |
| toolagent | 256 | 16,906 | 7,141 | 2,624 | 96.4% | 26.87% |

capacity 从 16 增至 256 pages 后，三个重复、四个 Store mode 合计的 put 从
271,608 降至 249,648，get 从 15,516 增至 37,476；访问 payload 总量保持不变。
toolagent 的 block reuse 增幅明显大于 conversation，因此 capacity 对其 request
中心分位数的影响也更强。

## Remote paired overhead

| Trace | Pages | Request p50 | Request p95 | Storage wait | Event rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| conversation | 16 | +13.21% | +13.14% | +12.85% | -11.39% |
| conversation | 64 | +11.06% | +11.79% | +11.25% | -10.11% |
| conversation | 256 | +14.72% | +13.39% | +14.41% | -12.59% |
| toolagent | 16 | +11.65% | +13.77% | +11.69% | -10.46% |
| toolagent | 64 | +3.32% | +11.17% | +10.33% | -9.36% |
| toolagent | 256 | +2.68% | +11.41% | +9.57% | -8.74% |

toolagent 的 remote request p50 overhead 随 capacity 从 16 增至 256 pages，由
`11.65%` 降至 `2.68%`，说明更多 reuse 会在中心分位数摊薄 put wrapper 成本；
但 request p95 仍为 `11.17--13.77%`，storage-wait overhead 仍为
`9.57--11.69%`。因此 capacity/reuse 可以稀释中心请求成本，不能据此声称 tail
或总存储等待开销消失。

conversation 的 block hit rate 始终只有 `1.60--3.52%`，remote overhead 没有随
capacity 单调下降。这说明仅有“更多 pages”不足以预测透明层开销，实际收益取决于
trace 的复用结构，而不是 capacity 数字本身。

## Local 结果与解释边界

conversation-local 的 storage-wait overhead 中位数为 `4.54--5.59%`，event-rate
delta 为 `-4.35-- -5.29%`，方向稳定。toolagent-local 的 storage-wait 中位数为
`0.48/2.49/1.87%`，request quantile 在部分 trial 为负；c16/c256 各有一个 trial
的 total storage wait 或 event rate 也发生符号反转。因此 local toolagent 结果应
解释为接近 run-to-run variability，不能选择性引用负 request quantile 声称
transparent speedup。

本实验固定 500 request、128 KiB value、单进程顺序 replay。它验证了真实公开
trace 下的 cache-budget sensitivity，但仍不验证 arrival pacing、真实并发、模型
prefill、TTFT/TPOT、完整 trace 或 adaptive placement policy。

## 恢复与证据

c16 与 c256 的 target service/subsystem/bdev 及 client namespace before/after
精确一致；c64 的相同 gate 已在前一批次通过。三批 target service 均保持
active/running 与 `MainPID=2072748`，client Master 最终均恢复 root
`round_robin` 并通过 transparent smoke。

权威 artifact 位于
`experiments/nvmeof/results/20260823T211747Z-public-trace-store-capacity/`。它包含
c16/c256 完整 raw archive、三个 capacity 的 aggregate、逐 trial range、统一
conclusion、硬件恢复 verifier、对照脚本和 checksum；c64 raw archive 仍由 commit
`68dbd971` 的独立 artifact 保存。
