# 阶段四透明层严格验收执行计划

## 1. 目标、范围与闭环条件

本计划是阶段四的可执行入口，只验收透明层开销、生命周期和不可用目标行为。它不启动
阶段五，不开展 DRAM 分层、自适应策略、对象 migration 或新的性能优化。

阶段四只有在以下条件同时满足时才闭环：

1. 同一个 `TRANSPARENT_RUN_ID` 和同一个 `RESULT_DIR` 中生成八份严格
   gate 输入证据；
2. 八份输入证据均为 `status: "pass"`，且场景、目标、对象生命周期和
   paired metrics 与当前实现的契约一致；
3. `./run.sh transparent-acceptance` 成功，最终
   `transparent-acceptance.json` 为 `status: "pass"`、
   `required_evidence: 8`、`failures: []`；
4. remote NoF 的透明层额外开销已经按原始样本和分位数解释，并得到
   “可接受”“不可接受”或“证据不足”的明确结论。

八份 gate 输入加最终 acceptance 文件共九份交付物。当前
`correctness.py` 的 `required_evidence` 字段只统计八份输入，不包含
`transparent-acceptance.json` 自身；不得为了得到数字 9 而伪造额外输入。

## 2. 硬性约束与停止条件

- 所有命令必须复用同一个非空 `TRANSPARENT_RUN_ID` 和同一个新建的
  `RESULT_DIR`。禁止复制旧批次 JSON、修改其中的 `run_id` 或跨目录混批。
- Master policy 必须设置在实际 `mooncake_master` 服务环境中。仅在运行
  `run.sh` 的 shell 中设置 `MC_HETERO_STORAGE_POLICY` 不构成有效切换。
- local 场景只能使用已批准的、非挂载根目录的 `SSD_OFFLOAD_PATH`。在路径
  身份无法确认时，停止 local 写入，不得猜测路径。
- 任何 descriptor 不匹配、对象不可读、删除失败、失败写发布副本、JSON
  缺失或 mixed run ID 都使批次未闭环。
- 测量期间不得更改对象大小、对象数量、CPU affinity、持久化设置、绑定、
  Master policy 或后端注册状态。local 和 remote 结果分别解释，禁止平均。

## 3. 建立唯一证据批次

从 `experiments/nvmeof` 目录执行。先加载已经审阅的 testbed 配置，再创建
全新的批次目录：

```bash
cd experiments/nvmeof
source config.env
export TRANSPARENT_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-phase4-transparent"
export RESULT_DIR="${PWD}/results/${TRANSPARENT_RUN_ID}"
mkdir -p "${RESULT_DIR}/inventory" "${RESULT_DIR}/service-logs"
```

`config.env` 应提供 `BUILD_DIR`、Master/metadata/NoF 地址和绑定所需环境。
local 场景还必须显式设置已批准的路径：

```bash
export SSD_OFFLOAD_PATH=<approved-local-experiment-subdirectory>
```

在运行工作负载前，将下列信息写入 `RESULT_DIR/inventory/`。记录真实输出，
不要只记录计划值：

| 文件 | 必须记录的内容 |
| --- | --- |
| `source.txt` | `git rev-parse HEAD`、`git status --short` |
| `build.txt` | `BUILD_DIR`、CMake cache/构建类型、Master binary 路径与哈希 |
| `python.txt` | `python3 --version`、`PYTHONPATH`、`mooncake.store.__file__` |
| `services-before.txt` | Master、metadata、local owner、NoF 的 PID/start time、命令行、systemd unit/InvocationID 或 HA view |
| `cpu-affinity.txt` | client 与相关服务的 `taskset -pc`、NUMA/CPU 绑定 |
| `workload.txt` | `TRANSPARENT_BENCH_COUNT`、`TRANSPARENT_BENCH_SIZE`、持久化参数 |
| `storage.txt` | `SSD_OFFLOAD_PATH`、mount/`df` 身份、NoF NQN/NSID/endpoint、registration 状态 |
| `policies.txt` | 每个步骤实际生效的 Master policy 及切换时间 |

最低限度的 source 和 Python 绑定探针如下；输出应保存而不是只显示在终端：

```bash
git -C ../.. rev-parse HEAD >"${RESULT_DIR}/inventory/source.txt"
git -C ../.. status --short >>"${RESULT_DIR}/inventory/source.txt"
python3 - <<'PY' >"${RESULT_DIR}/inventory/python.txt"
import sys
import mooncake.store as store

print(sys.version)
print(store.__file__)
assert hasattr(store.ReplicateConfig, "local_replica_num")
assert hasattr(store.ReplicaDescriptor, "is_nof_replica")
PY
```

如果探针未解析到本批次对应的 build/release binding，或 Master binary 与
记录的 source revision 不一致，立即停止，不生成硬件验收结果。

## 4. 执行顺序

下列步骤按依赖顺序执行。每次切换 Master policy 或服务配置后，先完成监听、
metadata、backend registration 和单对象 smoke check，再运行正式命令。

### 4.1 正常生命周期

1. 将 Master 服务切换为 `local_only`，确认 local backend 已绑定：

   ```bash
   ./run.sh transparent-local
   ```

2. 将 Master 服务切换为 `remote_only`；如果当前 NoF 注册状态已丢失，先执行
   `./run.sh register` 并确认容量可用，再运行：

   ```bash
   ./run.sh transparent-remote
   ```

3. 将 Master 服务切换为 `round_robin`，确认 local 与 NoF 同时可用：

   ```bash
   ./run.sh transparent-round-robin
   ```

逐个检查 `transparent-local.json`、`transparent-remote.json` 和
`transparent-round-robin.json`。三者必须满足：

- `status` 为 `pass`，`run_id` 等于当前批次；
- `expected_targets` 分别为 `["local_nvme"]`、`["remote_nof"]` 和
  `["local_nvme", "remote_nof"]`；
- `objects_verified == objects_removed == objects`；
- `child_read_verified` 为 `true`，`phantom_replicas` 为 `0`；
- `target_counts` 与按对象序号轮转的目标数量完全一致。

### 4.2 目标不可用

1. 让 local backend 确实不可用，再以 `local_only` 启动 Master：

   ```bash
   ./run.sh transparent-local-unavailable
   ```

2. 恢复 local backend。注销所有 NoF segment，让 remote backend 确实不可用，
   再以 `remote_only` 启动 Master：

   ```bash
   ./run.sh transparent-remote-unavailable
   ```

两个报告必须分别匹配 `local_nvme`、`remote_nof`，并同时满足
`write_failed: true`、`published_replicas: 0`、
`readable_after_failure: false`。完成后恢复 backend，验证重新绑定/注册，
不得把 unavailable 状态带入后续 overhead 测试。

### 4.3 Paired overhead

固定以下工作负载参数并写入 inventory；两个 target 使用相同值：

```bash
export TRANSPARENT_BENCH_COUNT=<approved-object-count>
export TRANSPARENT_BENCH_SIZE=<approved-object-size-bytes>
```

先把 Master 服务配置为 `local_only`，确认 local backend 和 CPU affinity，运行：

```bash
TRANSPARENT_BENCH_TARGET=local_nvme ./run.sh transparent-overhead
```

再把 Master 服务配置为 `remote_only`，恢复并确认 NoF 注册，保持对象参数、
CPU affinity 和持久化设置不变，运行：

```bash
TRANSPARENT_BENCH_TARGET=remote_nof ./run.sh transparent-overhead
```

每条命令在同一进程中先执行 explicit `ReplicateConfig` 的 `direct`，再执行
普通 `store.put(key, value)` 的 `transparent`。每份报告必须包含：

- direct/transparent 各自的 `put`、`get`、`remove` 原始 `samples_ms`；
- 每个操作的 p50/p95/p99 和 operations/s；
- `put`/`get` 的 MiB/s；
- 整体 process CPU utilization；
- 所有适用指标的 transparent-minus-direct `absolute` 和 `percent`。

任何对象数量、大小或 target 不匹配，任何 sample/metric 缺失，或 descriptor、
读取、删除失败，都使该 target 结果无效。local 与 remote 绝不合并成一个均值。

### 4.4 软件验证

硬件证据完成后运行：

```bash
./run.sh transparent-software-verification
```

当前命令固定执行五项检查：相关 CMake targets 构建、相关 CTest、descriptor
持久化回归测试、`experiments/nvmeof/test_correctness.py` 和对既定 NVMe-oF
源文件的 PR-scoped pre-commit。报告必须有五条命令、五个零退出码，且
`commands_passed == commands_required == 5`。缺失工具也按失败处理，不得手工
把 JSON 改成 pass。

如果本计划执行过程中另有源文件被修改，还必须额外对所有实际触及文件运行
`pre-commit run --files ...`；该补充检查不能替代上述软件验证 artifact。

## 5. Remote `+14%` 分布分析

已观测的 remote `put` p50 约 `+14%` 只是待解释现象，不是自动通过阈值。
从 `transparent-overhead-remote_nof.json` 的 direct/transparent 原始样本和
`overhead` 汇总分别填写下表；local 另表记录，不参与 remote 结论计算。

| 操作 | 指标 | Direct | Transparent | 绝对差 | 百分比 | 观察 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| put | p50 / p95 / p99 ms |  |  |  |  |  |
| put | MiB/s / ops/s |  |  |  |  |  |  |
| get | p50 / p95 / p99 ms |  |  |  |  |  |
| get | MiB/s / ops/s |  |  |  |  |  |  |
| remove | p50 / p95 / p99 ms |  |  |  |  |  |
| remove | ops/s |  |  |  |  |  |
| lifecycle | CPU utilization |  |  |  |  |  |

再记录以下分布诊断：

| 诊断 | 证据 | 结论 |
| --- | --- | --- |
| p50、p95、p99 是否同向且量级稳定 | 三个分位数及 percent delta |  |
| 是否出现尾部放大 | p95/p50、p99/p50 的 direct/transparent 对比 |  |
| 样本离散度是否改变 | 样本范围、IQR/MAD 或同等稳健统计 |  |
| 是否由少量 outlier 拉高 | 排序后的最高样本及剔除 outlier 的敏感性结果 |  |
| CPU 是否额外退化 | direct/transparent CPU utilization 与 delta |  |
| bandwidth/ops 是否同步退化 | 各操作吞吐与 operation rate |  |
| NoF 服务或网络是否抖动 | 同时间窗 service、RDMA、health 日志 |  |
| 是否存在固定路径成本 | 不同分位数的绝对延迟差与对象大小的摊销关系 |  |

结论规则：

- 只有 strict gate 通过、descriptor/读取/删除/错误行为全部正常，且额外开销
  在分位数中稳定、无异常尾部放大、无额外 CPU 或 bandwidth 退化时，才可将
  `+14%` 记录为“当前对象规模和 testbed 下可接受的 remote 透明封装成本”。
- 如果 p95/p99 明显放大、CPU 或 bandwidth 出现额外退化、NoF 日志显示抖动，
  或结果主要由少量 outlier 驱动，结论为“不可接受”，在阶段四内定位后重跑。
- 如果样本不足、日志缺失或 direct/transparent 条件不一致，结论为“证据
  不足”，补齐同批证据或废弃整批重跑。两种情况都不得进入阶段五。

## 6. Strict acceptance

先列出八份输入并检查 JSON 可解析、`run_id` 一致：

```text
transparent-local.json
transparent-remote.json
transparent-round-robin.json
transparent-local-unavailable.json
transparent-remote-unavailable.json
transparent-overhead-local_nvme.json
transparent-overhead-remote_nof.json
transparent-software-verification.json
```

然后运行唯一最终 gate：

```bash
./run.sh transparent-acceptance
```

验收者必须直接检查 `transparent-acceptance.json`：

```text
status == "pass"
run_id == TRANSPARENT_RUN_ID
required_evidence == 8
failures == []
八个 evidence 条目均为 "pass"
```

只有这一步通过，才可以填写阶段四最终结论。命令失败但 JSON 成功写出时，
保留失败文件用于诊断，不得删除失败痕迹后宣称通过。

## 7. 失败处理与重跑规则

| 失败类型 | 处理规则 |
| --- | --- |
| 单命令前置条件未满足，尚未写正式 JSON | 修复注册、policy、路径或服务健康后，在同批继续 |
| 正式 JSON 为 `fail` 或对象清理失败 | 保留失败 JSON/日志；确认无残留对象后，用同参数重跑该场景并记录重跑原因 |
| benchmark 条件发生变化 | local/remote paired 结果作废；恢复固定条件后重跑受影响 target |
| mixed `run_id`、旧文件混入或批次环境不可复原 | 废弃整个 `RESULT_DIR`，创建新的 run ID 全量重跑 |
| software verification 失败 | 修复代码/环境并重新生成该 artifact；保留原始失败输出 |
| acceptance 失败 | 按 `failures` 逐项修复或重跑；再次执行 gate，禁止手工编辑证据 |

同批重跑只允许在环境和工作负载仍可证明一致时覆盖对应正式 JSON，并必须把
旧文件、重跑原因和时间保存到审计子目录。无法证明一致时必须启用全新
`TRANSPARENT_RUN_ID` 和 `RESULT_DIR`，八项全部重跑。

## 8. 结果清单与最终结论模板

最终归档至少包含：

- 第 6 节的八份 gate 输入和 `transparent-acceptance.json`；
- `inventory/`、Master/NoF service 日志、policy 切换、backend
  rebind/re-registration、smoke check 和重跑记录；
- local/remote 指标表及 remote `+14%` 分布分析。

最终结论使用以下模板，不得在 acceptance 通过前预填“闭环”：

```text
阶段四批次
- source commit:
- TRANSPARENT_RUN_ID:
- RESULT_DIR:
- testbed / binding / build:
- transparent-acceptance.json: pass | fail
- 八项 required evidence: 8/8 pass | <实际状态>

可靠性
- lifecycle / unavailable: <结论与证据>

性能
- local NVMe: <put/get/remove latency、throughput/ops、CPU 与 delta>
- remote NoF: <put/get/remove latency、throughput/ops、CPU 与 delta>
- remote +14%: 可接受 | 不可接受 | 证据不足
- 分布解释: <分位数、尾部、离散度、outlier、CPU/bandwidth、NoF 抖动>

阶段结论
- 阶段四: 已闭环 | 未闭环
- 依据: <acceptance status 与关键证据>
- 阶段五: 仅在阶段四已闭环时另行规划；本计划未启动阶段五
```
