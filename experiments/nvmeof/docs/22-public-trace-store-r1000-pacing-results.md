# FAST'25 公开 Trace 的 1000-request 顺序 Pacing 结果

## 状态与规模

checkpoint `20260823T224104Z-public-trace-store-r1000-paced` 固定 64-page LRU、
128 KiB value 和 `replay_scale=10`，把 conversation/toolagent 的 bounded prefix
从 500 扩展到 1000 request。三次重复和五个 mode 共 30/30 case 通过，执行
503,424 次 put、60,660 次 get、503,424 次 remove，建模 put+get payload
73,935,618,048 bytes。与 500-request midpoint 合并后，统一对照为 60/60 case、
760,224 put、90,984 get、760,224 remove。

该实验仍为单进程逐 event replay。1000 request 不是完整 conversation/toolagent
trace，也不验证请求 overlap、真实 concurrency、saturation throughput、模型
prefill 或 serving goodput。

## 500/1000 Schedule debt

| Trace | Case | 500 debt | 1000 debt | 500/1000 arrival-lag p95 |
| --- | --- | ---: | ---: | ---: |
| conversation | no-store proxy | 5.32% | 0.45% | 0.869/0.780 s |
| conversation | direct/transparent local | 5.90/8.34% | 1.12/1.52% | 1.027/1.374 vs 1.211/1.306 s |
| conversation | direct/transparent remote | 0.57/0.66% | 0.22/0.25% | 0.182/0.205 vs 0.165/0.186 s |
| toolagent | no-store proxy | 15.84% | 21.00% | 1.148/3.487 s |
| toolagent | direct/transparent local | 10.71/11.62% | 15.54/17.16% | 0.956/0.900 vs 2.660/2.925 s |
| toolagent | direct/transparent remote | 1.77/2.02% | 0.90/0.95% | 0.176/0.197 vs 0.201/0.225 s |

conversation 的 operation/request 从 `55.734` 降至 `53.702`，toolagent 从
`34.920` 变为 `35.257`，且 1000-request toolagent prefix 的单位 request arrival
span 略短。两个 request count 是公开 trace 的不同长度 prefix，不是同一 workload
的机械倍增。因此 conversation debt 下降而 toolagent-local debt 上升，应解释为
prefix composition 与 arrival density 的共同结果，不能写成线性 scale-up。

## Paired overhead

| Trace | Requests | Remote request p50 | Remote request p95 | Remote storage wait |
| --- | ---: | ---: | ---: | ---: |
| conversation | 500 | +13.21% | +14.37% | +13.91% |
| conversation | 1000 | +11.92% | +11.63% | +11.97% |
| toolagent | 500 | +9.05% | +25.09% | +13.50% |
| toolagent | 1000 | +5.65% | +16.02% | +13.71% |

两个规模下 remote storage-wait overhead 均为稳定正值，支持 transparent wrapper
存在约 12--14% 总存储等待成本的窄结论。request quantile 会随 prefix composition
变化，不能把单一 500 或 1000 数字外推为 universal overhead constant。

local toolagent 的 500-request quantile 为负，而 1000-request 变为正值；两个规模的
storage-wait overhead 都为正。这进一步确认 local request quantile 的符号反转是
run-to-run/prefix variability，不是 transparent speedup。

## 恢复与 artifact

client Master 从 root `round_robin` 出发并恢复到相同策略，最终 transparent smoke
通过，client NVMe subsystem before/after 字节一致。target service 保持 active、
PID `2072748`；由于非 root SPDK RPC 权限限制，本 checkpoint 不新增 target
bdev/subsystem 精确一致性证明。

artifact 位于
`experiments/nvmeof/results/20260823T224104Z-public-trace-store-r1000-paced/`，
包含 1000-request raw archive、500/1000 aggregate 输入、统一 conclusion、逐 mode
scale CSV、恢复 inventory、比较脚本和 checksum。
