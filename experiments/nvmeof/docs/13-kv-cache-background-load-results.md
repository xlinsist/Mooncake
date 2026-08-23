# KV-cache 背景负载 Sweep 结果

## 状态与配置

两节点批次 `20260823T161023Z-kv-load-sweep` 固定 128 KiB、50% configured
reuse、24 requests、每 request 4 blocks、seed 42 和
`configured_concurrency=1`。四次 counterbalanced trial 覆盖：

- `idle`；
- 本地 `/dev/nvme1n1` 4 KiB/QD32 randread 的 50% rate；
- 同一本地盘的 90% rate；
- remote NoF 1 MiB/QD32 offered-load stress。

每个 cell 都包含 `no_store`、direct/transparent local 和
direct/transparent remote。trial 1/3 使用 direct -> transparent，trial 2/4
使用 transparent -> direct；scenario 顺序按 Latin square 轮换。local/remote
policy phase 前后另有 idle anchor，不计入主矩阵。

80/80 个主 case 和 8/8 个 anchor case 均完成 148 次操作、0 miss，local/remote
descriptor 全部精确匹配。88 个 foreground case 都保留 system/process CPU、
NIC、block busy、RDMA、SPDK iostat、Master policy、local/target SMART 和 trace
证据。32 个主 load epoch 均有 liveness 与终止状态。

最终 batch gate 为 **`inconclusive`**，但范围只限 `remote_stress`：`idle`、
`local50` 和 `local90` 三个 scenario 为 `pass`。该结论不是 workload correctness
失败，而是 fail-closed load-drift gate 生效。

## Local load gate

fresh local calibration 为 `163,741.91 IOPS`。50%/90% 请求 rate 为
`81,870 / 147,366 IOPS`，八个对应 epoch 的 achieved 范围分别为：

```text
local50: 81,868.83 -- 81,868.90 IOPS
local90: 147,363.92 -- 147,364.04 IOPS
```

achieved/requested ratio 均约为 `99.999%`。foreground telemetry 中 local block
utilization 的中位数从 idle 的约 0% 上升到 local50 的约 94%，local90 的约
99%，说明 rate-limited fio 确实形成了目标设备背景压力；这些数字是 achieved
load，不把 requested percentage 写成精确设备 utilization。

## 三个有效 scenario 的延迟

下表为四次重复的 request p50 中位数：

| Scenario | Direct local | Transparent local | Direct remote | Transparent remote |
| --- | ---: | ---: | ---: | ---: |
| idle | 3232.071 us | 3431.102 us | 910.820 us | 1030.266 us |
| local50 | 3245.932 us | 3442.337 us | 902.805 us | 1025.800 us |
| local90 | 3251.247 us | 3437.913 us | 901.335 us | 1030.057 us |

在当前 128 KiB 顺序 replay 中，local50/local90 并未明显移动 request p50：
direct local 相对 idle 只增加约 `0.43% / 0.59%`，transparent local 增加约
`0.33% / 0.20%`。这只说明当前设备与 workload 组合的中心延迟对该 read load
不敏感，不代表更高并发、写负载或 tail 不受影响。

transparent-minus-direct 的 paired delta 中位数为：

| Scenario | Target | Put p50 | Get p50 | Remove p50 | Request p50 | Request p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| idle | local | +5.97% | +0.53% | -1.14% | +7.51% | +3.94% |
| local50 | local | +7.14% | -3.58% | +2.78% | +6.87% | +3.96% |
| local90 | local | +5.04% | -4.47% | +0.48% | +5.30% | +4.89% |
| idle | remote | +14.49% | -0.39% | +0.02% | +13.30% | +17.24% |
| local50 | remote | +14.20% | +0.10% | +0.60% | +13.68% | +9.28% |
| local90 | remote | +13.85% | -0.40% | +0.76% | +14.43% | +20.95% |

因此，在三个 load-valid scenario 中，remote put p50 的透明层开销仍保持约
`14%`，get/remove p50 接近 direct。remote local90 的 request p95 paired
中位数为 `+20.95%`，四次范围较宽，不能写成稳定 tail 回归。

## Remote stress 的 fail-closed 结果

remote calibration 为 `6,976.25 IOPS / 6.81 GiB/s`。主矩阵四个 remote-policy
stress epoch 中：

```text
trial 1: 6,975.24 IOPS, 523,143 completed, 0 failed
trial 2:     7.23 IOPS,     542 completed, 0 failed
trial 3: 6,975.28 IOPS, 523,146 completed, 0 failed
trial 4:     7.03 IOPS,     527 completed, 0 failed
```

collapsed epoch 的 unit 在所有 foreground case 前后都保持 active，benchmark
也报告 `failed_ops=0`，但 interval 只在 0 和约 14--16 MiB/s 间摆动，最终
latency percentile 出现无效 overflow 值。因此，仅检查进程存活和 error count
不足以证明背景负载有效。

为排除 scenario 间缺少 cooldown，补跑了两个同为 transparent -> direct、
显式等待 15 秒的 recovery epoch。结果一个仍 collapsed（`7.24 IOPS`），一个
恢复健康（`6,974.92 IOPS`）。六个 remote stress epoch 最终为 3 healthy / 3
collapsed；该 offered point 具有非确定性，不能把四次主矩阵混合后计算的
direct/transparent overhead 当作有效性能结论。

healthy stress epoch 中，Store request p50 约为 10 ms；collapsed epoch 中约为
1 ms。这组对照只能作为“实际 achieved load 决定 foreground latency”的失败
证据，不用于声称透明层在 remote stress 下的固定开销或隔离能力。

## Anchor、环境与恢复

local anchor 的 request p50 pre/post drift 为 `-1.18%` 到 `+1.82%`，remote
anchor 为 `-1.36%` 到 `-0.77%`。最大的 retained anchor drift 是 direct-local
operation p95 的 `+7.54%`，因此 tail 仍只作描述。

本地 read load 前验证 serial `22083552A124`、mount source
`/dev/nvme1n1`、mount point `/mnt/datassd` 和 offload path 映射。target NQN、
NSID、listener、serial 与 service PID 在前后保持一致；target service PID
始终为 `2072748`，没有 restart/unregister。local/remote/recovery Master 都以
root 运行，log 中无 `nof_heartbeat_failure` 或
`unmount_nof_segment_by_heartbeat`。

最终恢复 root `round_robin` 并重新注册 NoF。12-object smoke 完成写入、
子进程读取、descriptor 验证和删除，local/remote 各 6 个，
`phantom_replicas=0`。

## 证据与边界

Git 内证据入口为
`experiments/nvmeof/results/20260823T161023Z-kv-load-sweep/`。摘要保留主矩阵、
operation 分布、load、telemetry、anchor 和 recovery classification；压缩包保存
完整 raw case、load interval、SPDK/RDMA/SMART、service log、执行脚本和恢复证据。

本实验只覆盖固定 128 KiB、50% reuse、单客户端、顺序 replay。它不验证真实
concurrency、模型 serving/TTFT、精确 remote utilization、transport-only
overhead、隔离、adaptive policy 或广义 crossover。true-concurrency 实现仍因
缺少正式 host consensus receipt 而未获授权，本批次没有越过该 gate。
