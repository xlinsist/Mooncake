# FAST'25 公开 Trace 的 Arrival-pressure 敏感性

## 实验轴与验收

本批次把 500-request、64-page、128 KiB value 的顺序 arrival-paced replay 从
单一 `replay_scale=10` 扩展到 `5/10/20`。每个 scale 覆盖 conversation/toolagent、
三次重复，以及 `no_store`、direct/transparent local、direct/transparent remote
五个 case。三个矩阵合计 90/90 case、18 个 cell conclusion、全部 pacing gate 和
最终 recovery smoke 均通过；共执行 770,400 次 put、90,972 次 get、770,400 次
remove，建模 put+get payload 112,901,750,784 bytes。

`replay_scale` 是 source arrival span 的 fast-forward 倍数，不是 configured
concurrency。所有 event 仍在单进程中逐个完成；落后 schedule 时立即继续并记录
lag，不创建 worker 或 overlap。因此该轴描述 **sequential arrival pressure**，
不能用于 QPS saturation、active requests、并发扩展或 serving goodput 主张。

## Completion debt

下表给出 completion lag 占压缩后 source span 的比例：

| Trace | Case | 5x | 10x | 20x |
| --- | --- | ---: | ---: | ---: |
| conversation | no-store proxy | 0.57% | 5.32% | 82.03% |
| conversation | direct/transparent local | 0.56/0.59% | 5.90/8.34% | 88.33/96.83% |
| conversation | direct/transparent remote | 0.29/0.33% | 0.57/0.66% | 1.45/1.94% |
| toolagent | no-store proxy | 2.10% | 15.84% | 130.00% |
| toolagent | direct/transparent local | 1.85/1.95% | 10.71/11.62% | 117.92/116.66% |
| toolagent | direct/transparent remote | 0.91/0.99% | 1.77/2.02% | 4.89/8.10% |

5x 时所有路径都基本跟上 schedule；20x 时固定 1 ms recomputation proxy 和 local
Store 路径已无法在压缩 span 内完成，而 remote 路径仍保持在 8.10% 以内。这里的
local/remote 使用不同设备与 I/O stack，且没有并发，因此只能说明当前顺序 harness
在不同 arrival pressure 下积累 debt 的边界，不能写成 remote transport 或系统
性能优越性。

20x 的 conversation direct/transparent local arrival-lag p95 达到
`7.036/7.776 s`，toolagent 为 `4.978/4.914 s`；remote 对应范围仅为
`0.200--0.289 s`。这说明 completion debt 贯穿请求序列，而不是单个尾部 outlier。

## Transparent overhead 稳定性

remote storage-wait overhead 在两个 trace 和三个 scale 上保持
`12.11--14.42%`。conversation-remote request p50/p95 overhead 为
`11.48--13.21%` / `10.88--14.37%`；toolagent-remote 为 `7.99--9.05%` /
`12.29--25.09%`。arrival pressure 改变了 schedule debt，但没有消除 transparent
wrapper 的总 storage-wait 成本。

toolagent-local request quantile 在三个 scale 间继续发生符号变化，而 storage-wait
overhead 始终为正的 `0.36--2.75%`；因此仍应解释为 run-to-run variability，不能
选择性声称 transparent speedup。

## 恢复与证据边界

三个 scale 的 client Master 都从 root `round_robin` 出发并恢复到同一策略，最终
transparent smoke 全部通过；新增 5x/20x 的 client namespace before/after 字节
一致。target service 保持 active、PID `2072748`。由于当前非 root 账号仍无权读取
SPDK RPC socket，新 scale 不新增 target bdev/subsystem 精确一致性证明；该限制与
10x checkpoint 相同并被显式保留。

统一 artifact 位于
`experiments/nvmeof/results/20260823T222345Z-public-trace-store-pacing-scale/`，包含
5x/20x raw archive、三 scale aggregate/paired 输入、统一 conclusion、schedule-
debt/overhead CSV、恢复 inventory、聚合脚本和 checksum。10x 原始证据继续由
`20260823T214823Z-public-trace-store-paced` artifact 保存。
