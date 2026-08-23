# FAST'25 公开 Trace 持久化规模结果

## 状态与配置

两节点批次 `20260823T194510Z-public-trace-scale` 完成 36/36 个 case：

- trace：FAST'25 `conversation` 和 `toolagent`；
- 请求规模：20、50、100；
- 路径：local NVMe ext4 与 file-backed NVMe-oF XFS；
- 每个 cell 三次，路径顺序交替；
- `glm5`、512-token page、64 physical pages、`fsync=always`、单线程、unpaced。

所有 case 退出码为 0，44,394 次 write 全部满足 `sync_count == write_count`。
加上 5,544 次 read，本批次约执行 2.808 TB benchmark payload operation。

## 三次重复中位数

| Trace | Requests | Local QPS | Remote QPS | Local request p50/p95/p99 (ms) | Remote request p50/p95/p99 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| conversation | 20 | 0.66 | 1.51 | 1077.806 / 2858.672 / 7283.200 | 401.755 / 1340.449 / 3424.337 |
| conversation | 50 | 0.85 | 1.71 | 930.706 / 2476.179 / 6495.772 | 397.980 / 1251.255 / 3141.587 |
| conversation | 100 | 0.68 | 1.44 | 1049.580 / 4052.591 / 7976.262 | 485.302 / 1942.048 / 4130.668 |
| toolagent | 20 | 0.79 | 1.60 | 813.752 / 2815.371 / 6984.689 | 394.621 / 1339.917 / 3386.587 |
| toolagent | 50 | 1.03 | 1.92 | 489.707 / 2537.448 / 6122.121 | 396.065 / 1233.576 / 3089.572 |
| toolagent | 100 | 1.12 | 2.01 | 488.410 / 2505.498 / 4199.445 | 397.553 / 1232.339 / 2179.896 |

remote whole-path QPS 为 local 的 `1.79--2.30x`。六个 cell 中，remote write
p50 为 local 的 `0.45--0.50x`，而 read p50 为 `0.94--1.03x`。toolagent
hit rate 从 20 request 的 `12.65%` 增加到 100 request 的 `24.27%`；因此
request p50 的 remote/local 比值从 `0.479x` 收窄到 `0.814x`，但 p95 比值
仍约为 `0.485x`。这说明高 reuse 请求会稀释两条路径的 write 差异，同时
write-heavy tail 仍受底层持久写路径影响。

## Claim 边界

local 是客户端 `/dev/nvme1n1` ext4；remote 是 target XFS file -> SPDK AIO ->
NVMe-oF/RDMA -> client XFS。两者设备、文件系统和 I/O stack 不同，因此上述
比值是 **whole-path comparison**，不是 NVMe-oF transport speedup，也不是
Mooncake Store direct/transparent overhead。

本批次仍是 GPU-free、单线程、unpaced replay，并用 64 个 physical page 做
modulo mapping。它把公开 trace 证据从 conversation 5/20 request 扩展到
conversation/toolagent 20/50/100 request，但没有完成 12,031/23,608-request
全量 trace、模型 serving、TTFT/TPOT、matched substrate 或 true concurrency。

## 环境与证据

实验只新增唯一命名的 8 GiB AIO bdev/subsystem；没有重启 target service，也
没有修改或注销既有 `nof-phase1`。结束后 target subsystem、bdev、service
状态和 PID 的 before/after 文件逐字节一致，临时 NQN、mount、image 和本地
case 目录均已删除。

两个正式矩阵前的失败 pilot 也保存在 raw archive：一个在 RPC 参数解析时停止，
另一个在首个 workload 前因 controller 变量缺失停止；两次均通过相同恢复 gate。

Git 内证据入口为
`experiments/nvmeof/results/20260823T194510Z-public-trace-scale/`。逐 trial、
三重复聚合、paired whole-path ratio、严格 conclusion、控制器、聚合器、raw
archive 和 `SHA256SUMS` 均可直接复核。
