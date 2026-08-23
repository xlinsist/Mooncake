# FAST'25 公开 Trace 的 Durable Arrival-debt 结果

## 状态与路径

批次 `20260823T231500Z-public-trace-durable-paced` 完成 12/12 个 case：

- conversation/toolagent 各 100 request；
- local NVMe ext4 与临时 file-backed NVMe-oF XFS；
- 每条路径三次 counterbalanced 重复；
- `glm5`、512-token page、64 physical pages、`fsync=always`、单线程；
- `replay_scale=1`，即按公开 trace 原始 arrival span 重放。

26,466 次 write 全部满足 `sync_count == write_count`，另有 3,432 次 read，建模
payload operation 约 1.681 TB。所有 exit、pacing field、durability count 和 target
subsystem/bdev/service exact-restoration gate 均为 `pass`。

## Arrival debt

| Trace | Path | Scheduled span | Wall | Completion lag | Arrival lag p50/p95/max | QPS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| conversation | local ext4 | 33.0 s | 148.597 s | 115.597 s | 43.520/91.292/115.510 s | 0.67 |
| conversation | file-backed NoF XFS | 33.0 s | 68.962 s | 35.962 s | 13.608/24.978/35.907 s | 1.45 |
| toolagent | local ext4 | 15.0 s | 89.065 s | 74.065 s | 42.277/66.184/73.813 s | 1.12 |
| toolagent | file-backed NoF XFS | 15.0 s | 47.797 s | 32.797 s | 18.375/28.841/32.587 s | 2.09 |

两条 durable path 都无法跟上原始 arrival schedule，且 lag 贯穿请求序列，而不是
单个尾部 outlier。remote whole path 的 completion lag 是 local 的
conversation/toolagent `0.31/0.45x`，arrival-lag p95 为 `0.27/0.44x`。

这些比值不是 NVMe-oF transport speedup。local 使用 client ext4；remote 使用
target file、SPDK AIO、NVMe-oF/RDMA 和 client XFS，两侧设备、文件系统、write
path 均不相同。因此结果只能作为 durable whole-path arrival-debt evidence，仍未
补齐 matched raw-device substrate comparison。

## 请求与 I/O 组成

remote/local request-p50 比值在 conversation/toolagent 上为 `0.460/0.842x`，
p95 为 `0.454/0.463x`。read p50 接近 `1.036/0.970x`，write p50 为
`0.464/0.464x`。这与既有 unpaced 结果一致：底层持久 write path 主导路径差异，
toolagent 的 reuse 会在 request p50 上稀释 write 差异。

本批次不能与 Mooncake Store direct/transparent 的 128 KiB value latency 直接
合并：durable benchmark 使用模型布局生成的约 56.2 MiB page operation，并要求
每次 write 同步落盘；两者回答的是不同层次的问题。

## 恢复与失败 pilot

controller 仅创建唯一命名的 8 GiB AIO file/bdev/subsystem/mount。结束后临时资源
全部卸载、断连和删除，target subsystem、bdev、service state、PID before/after
精确一致，service 保持 `MainPID=2072748`；既有 `nof-phase1` 从未重启或注销。

正式批次前的 pilot 在 workload 前停止：隔离 benchmark 目录不是 Git checkout，
旧 provenance 命令返回 128。该 pilot 未创建临时 subsystem，target/client 状态均
恢复；正式 controller 改为显式记录 source commit，raw archive 保存了 pilot。

artifact 位于
`experiments/nvmeof/results/20260823T231500Z-public-trace-durable-paced/`，包含
formal/pilot raw archive、逐 trial 与 aggregate CSV、paired path ratio、统一
conclusion、exact-restoration inventory、controller、aggregator 和 checksum。

该证据仍是 bounded 100-request、单线程、顺序、GPU-free replay，不覆盖 full
trace、matched substrate、true concurrency、模型 serving 或 TTFT/TPOT。
