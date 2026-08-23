# FAST'25 公开 Trace 持久化存储路径 Smoke

## 状态与配置

`20260823T151000Z-public-trace-durable` 完成 12/12 个计划 trial：conversation
trace 的 5/20 request 两种规模、local/remote 两条路径、每个 cell 三次重复。
所有 trial 固定 `glm5`、512-token page、64 个 physical page、
`fsync=always`、单线程和 unpaced replay；每次使用新数据文件并交替路径顺序。

| Requests | Path | Median I/O time (s) | Median QPS | Median write p50 / p95 / p99 (ms) |
| ---: | --- | ---: | ---: | ---: |
| 5 | local NVMe + ext4 | 4.495 | 1.11 | 73.441 / 77.393 / 78.368 |
| 5 | file-backed NVMe-oF + XFS | 1.211 | 4.13 | 18.052 / 21.275 / 21.743 |
| 20 | local NVMe + ext4 | 29.566 | 0.68 | 48.746 / 73.174 / 75.960 |
| 20 | file-backed NVMe-oF + XFS | 10.754 | 1.86 | 17.449 / 21.102 / 21.957 |

5-request trial 每次有 59 次 durable write 和 4 次 read；20-request trial 每次
有 560 次 durable write 和 19 次 read。每个 trial 的 sync count 都等于 write
count。

## 可支持与不可支持的结论

该批次证明公开 FAST'25 trace 可以在两条真实持久化路径上稳定运行，且
file-backed remote 路径在本次不同设备、不同文件系统的 whole-path 对比中更快。
它不隔离 NVMe-oF transport 开销，也不证明 NVMe-oF 比 local NVMe 更快：

- local 是客户端 `/dev/nvme1n1` 上的 ext4；
- remote 是 target XFS file -> SPDK AIO -> RDMA NVMe-oF -> client XFS。

64 个 physical page 只提供 3.35 GiB，并通过 modulo mapping 表示 20-request
cell 的 560 个 logical page。该批次不是完整 trace，不含模型执行，也不能支持
TTFT、TPOT 或 serving SLO 主张。

## 证据与恢复

Git 内结果入口为
`experiments/nvmeof/results/20260823T151000Z-public-trace-durable/`。其中
`summary.csv` 保存 12 个 trial，`raw-artifacts.tar.gz` 保存原始 benchmark
stdout、GNU `time -v` 和环境 metadata，解包后可用 `SHA256SUMS` 校验全部
raw artifact。

复制并校验 artifact 后，实验创建的临时 client mount/controller、target SPDK
subsystem、AIO bdev 和 8 GiB image 已全部删除。既有 `nof-phase1` namespace
保持连接，`mooncake-nof-spdk.service` 保持 active。

下一步应在专用可破坏 namespace 上用 LMCache raw-block/O_DIRECT control 隔离
device path，再扩大到完整 conversation/toolagent trace。真实并发仍受已记录的
host-consensus authority blocker 约束，不能用当前 `KV_WORKLOAD_CONCURRENCY`
数据替代。
