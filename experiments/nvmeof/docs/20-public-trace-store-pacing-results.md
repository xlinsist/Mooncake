# FAST'25 公开 Trace 的顺序 Arrival-pacing 结果

## 状态与实验边界

checkpoint `20260823T214823Z-public-trace-store-paced` 固定 500 request、64-page
LRU、128 KiB value 和 `replay_scale=10`，覆盖 conversation/toolagent、三次重复，
以及 `no_store`、direct/transparent local、direct/transparent remote 五个 case。
30/30 case、六个 cell conclusion、pacing gate 和最终 recovery smoke 全部通过；
共执行 256,800 次 put、30,324 次 get、256,800 次 remove，建模 put+get payload
为 37,633,916,928 bytes。

该实验保留 FAST'25 的毫秒 arrival timestamp，并转换为微秒 offset；scale 10 表示
把 source span 压缩为十分之一。replay 仍为单进程严格顺序执行：每个 Store 操作
完成后才处理下一个 event。到达晚于 schedule 的 event 会立即执行并记录 lag，
不会启动 worker 或产生请求重叠。因此这些数据是 **sequential arrival-paced
replay**，不是并发、饱和吞吐或 active-request 证据。

## Schedule debt

| Trace | Scheduled span | Path | Wall | Completion lag | Arrival lag p50/p95 |
| --- | ---: | --- | ---: | ---: | ---: |
| conversation | 16.5 s | direct local | 17.474 s | 0.974 s | 252.572/1,027.175 ms |
| conversation | 16.5 s | transparent local | 17.877 s | 1.377 s | 313.864/1,373.721 ms |
| conversation | 16.5 s | direct remote | 16.594 s | 0.094 s | 53.789/182.088 ms |
| conversation | 16.5 s | transparent remote | 16.610 s | 0.110 s | 61.607/205.434 ms |
| toolagent | 9.0 s | direct local | 9.964 s | 0.964 s | 636.978/956.235 ms |
| toolagent | 9.0 s | transparent local | 10.046 s | 1.046 s | 558.352/900.133 ms |
| toolagent | 9.0 s | direct remote | 9.159 s | 0.159 s | 58.419/175.508 ms |
| toolagent | 9.0 s | transparent remote | 9.182 s | 0.182 s | 65.186/196.551 ms |

local 路径在两个 trace 上都积累约 1 秒 completion debt；remote 路径的中位数
completion lag 为 0.094--0.182 秒。该差异描述当前严格顺序实现能否跟上压缩后的
arrival schedule，不能外推为并发服务能力或设备极限。

## Paired transparent overhead

| Trace | Target | Request p50 | Request p95 | Storage wait | Event rate |
| --- | --- | ---: | ---: | ---: | ---: |
| conversation | local | +5.96% | +4.49% | +6.25% | -5.88% |
| conversation | remote | +13.21% | +14.37% | +13.91% | -12.21% |
| toolagent | local | -5.00% | -5.05% | +0.90% | -0.89% |
| toolagent | remote | +9.05% | +25.09% | +13.50% | -11.90% |

conversation 的 paired overhead 方向稳定；toolagent-local 的 request quantile
再次出现负值，但 storage wait 仍为正，且既有 capacity sweep 已显示该组合存在
符号反转。因此不能选择性引用为 transparent speedup。remote 的 storage-wait
overhead 为 13.50--13.91%，request-p95 overhead 为 14.37--25.09%；pacing 没有
消除 wrapper cost，toolagent tail 还显示更大的 paired 差异。

## 恢复与证据

client Master 从 root `round_robin` 出发并恢复到相同策略，最终 transparent smoke
通过，client NVMe subsystem before/after 字节一致。target service 在 preflight 和
结束时均保持 active、`MainPID=2072748`。由于当前非 root 账号无权读取 SPDK RPC
socket，本 checkpoint 不新增独立的 target bdev/subsystem before/after 精确证明；
同一 preserved target service 的精确 topology gate 已由上一批 capacity artifact
保存。此限制被保留，不能把本批次单独写成完整 target-topology restoration 证据。

权威 artifact 位于
`experiments/nvmeof/results/20260823T214823Z-public-trace-store-paced/`，包含 raw
archive、aggregate/paired CSV、pacing acceptance verdict、脚本、client inventory、
target service observation 和 checksum。
