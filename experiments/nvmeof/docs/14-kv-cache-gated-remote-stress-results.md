# KV-cache Gated Remote Stress 补充结果

## 为什么需要补充批次

主背景负载批次 `20260823T161023Z-kv-load-sweep` 的 remote 1 MiB/QD32
offered-load 在六个含 recovery 的 epoch 中出现 3 healthy / 3 collapsed。
collapsed epoch 只有约 7 IOPS，但进程始终 active 且 `failed_ops=0`，因此主批次
按 fail-closed 原则把 `remote_stress` 标为 `inconclusive`。

后续六次无 foreground Store client、root `round_robin` Master 下的独立短 probe
也复现 1 collapsed / 5 healthy。这排除了 direct/transparent 顺序、remote-only
policy 和 foreground replay 作为 collapse 的必要条件。healthy/collapsed attach
使用相同 endpoint、queue depth、worker、inflight、hugepage 和 target service；
现有日志没有给出足以定位根因的 error。

## Sacrificial attach gate

补充批次 `20260823T173400Z-kv-remote-stress-gated` 不覆盖或改写原始无效 epoch，
而是采用独立 gate：

1. 每个 trial 先运行 2 秒 sacrificial attach；
2. sacrificial 进程自然退出后，启动新的 75 秒 measured attach；
3. measured IOPS 必须位于 calibration `6,976.25 IOPS` 的 80--120%；
4. measured `failed_ops` 必须为 0，且 load 必须覆盖完整 foreground pair；
5. 四次 trial 交替 D->T / T->D，并保留 remote idle pre/post anchor。

四次结果为：

| Trial | Sacrificial IOPS | Measured IOPS | Completed ops | Failed ops | Mode order |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 7.50 | 6974.89 | 523117 | 0 | D -> T |
| 2 | 6976.00 | 6974.84 | 523113 | 0 | T -> D |
| 3 | 7.50 | 6974.92 | 523119 | 0 | D -> T |
| 4 | 6975.00 | 6974.81 | 523111 | 0 | T -> D |

两次 sacrificial attach 捕获 collapsed state，但四个独立 measured epoch 全部稳定
在 calibration 的 `99.979--99.981%`。这证明该 gate 能在当前 testbed 上把
startup nondeterminism 与 measured epoch 分离；它不是根因修复。

## Store paired 结果

八个 loaded Store case 均完成 148 operations、0 miss 和 148 remote
descriptors。四次 trial 共享同一 trace digest。transparent-minus-direct paired
结果为：

| Metric | Direct median | Transparent median | Paired delta 中位数 | 四次范围 |
| --- | ---: | ---: | ---: | ---: |
| Request p50 | 9971.325 us | 10144.755 us | +1.74% | +0.54% -- +2.09% |
| Request p95 | 10139.530 us | 10310.960 us | +1.67% | +0.31% -- +2.81% |
| Put p50 | 2509.116 us | 2552.652 us | +1.77% | -0.51% -- +2.16% |
| Get p50 | 2411.707 us | 2411.201 us | -0.11% | -0.48% -- +0.09% |
| Remove p50 | 61.200 us | 61.460 us | +0.94% | -32.84% -- +1.29% |

在 6.81 GiB/s remote read stress 下，request 和 put p50/p95 的透明层 paired
成本约为 2%，get p50 接近 direct。它明显低于 idle/local-load 下约 14% 的
remote put p50 overhead，因为 foreground 本身被共享 target load 推高到约
10 ms request p50；这只能解释为固定封装成本被更大的 I/O 等待摊薄，不能写成
透明层在压力下变快或具有隔离能力。

put p99 仍有 outlier，四次 paired range 为 `-26.29%` 到 `+19.61%`，不作方向
结论。remove 样本也短且 range 宽，只保留分布。

## Anchor 与 claim boundary

remote idle anchor 中，direct request p50 从 `723.920` 漂移到 `924.229 us`
（`+27.67%`），transparent request p50 为 `-0.25%`。pre direct anchor 是明显
cold-state outlier，因而本批次不能用绝对 idle/stress 差值声称 load slowdown
或 speedup。核心结论只依赖每个 measured epoch 内相邻且 counterbalanced 的
direct/transparent pair。

target service 在最终检查中保持 active，`MainPID=2072748`；该批次未保留可证明
前后 PID 相同的独立快照，因此不作 PID 全程不变的结论。root remote-only 和最终
round-robin Master log 无 heartbeat/unmount failure。最终 12-object smoke 验证并
删除 6 个 local 和 6 个 remote 对象，`phantom_replicas=0`。

Git 内证据入口为
`experiments/nvmeof/results/20260823T173400Z-kv-remote-stress-gated/`。本批次只
验证固定 128 KiB、50% reuse、单客户端顺序 replay 下的 gated offered-load
paired overhead，不验证 true concurrency、serving/TTFT、exact utilization、
isolation、transport-only overhead 或广义 stress 行为。
