# KV-cache 上层 Workload 开发计划

## 1. 目标与边界

当前 `experiments/nvmeof` 已经完成两类底层实验：

- 本地 NVMe 与远端 Mooncake NoF 的路径/调度边界测量；
- 显式 placement（direct）与 Master 策略 placement（transparent）的
  `put/get/remove` 配对开销测量。

下一步不是增加 HA、NoF 服务重启或更多后端，而是补上**上层 KV-cache
workload**，回答：

> 当对象操作代表推理 KV-cache block 的生成、复用和淘汰时，透明层的
> local/remote 选择是否影响 cache 命中、存储延迟和请求级收益？

第一版采用独立的 trace replay，不要求启动完整 vLLM/SGLang。这样可以在
保持实验可控的同时，明确区分 Mooncake 存储开销和上层请求行为。

非目标：

- 不实现完整 vLLM/SGLang connector；
- 不模拟真实模型的矩阵计算；
- 不新增 DRAM backend、自动迁移或多 SSD 聚合；
- 不把两节点结果推广为集群规模或普适调度阈值。

## 2. 当前基线与复用边界

现有 transparent benchmark 在
[`correctness.py`](../correctness.py#L396-L482) 中固定执行一批对象的
`put -> get -> remove`，默认由
[`run.sh`](../run.sh#L569-L577) 传入 `count=100`、`size=131072`，并输出
每种操作的 p50/p95/p99、操作速率、带宽和进程 CPU。

这套基线继续保留，用于回答“透明层自身增加多少开销”。新 workload
不得替换或混入该基线，而应新增独立的 replay 命令和结果文件，用于回答
“透明层对 KV-cache 请求行为有什么影响”。

现有路径实验已显示：决策结果依赖对象大小、队列深度和 local/remote
背景负载；已测量的 crossover 只覆盖特定大小和负载，其他组合仍应标为
`inconclusive`，见
[`01-local-remote-decision-boundary.md`](01-local-remote-decision-boundary.md#L59-L100)。

## 3. Workload 模型

### 3.1 事件类型

Trace 中每一行表示一个 KV-cache block 事件：

```text
timestamp_us,request_id,prefix_id,block_id,block_size,operation,policy
```

第一版支持以下操作：

| 操作 | Store 行为 | 上层含义 |
| --- | --- | --- |
| `produce` | `put` | prefill 产生一个新的 KV block |
| `reuse` | `get` | 后续请求复用已有 prefix/block |
| `evict` | `remove` | cache pressure 或生命周期结束 |
| `miss` | 不访问 Store，可选计时 | cache miss 后重新计算 |

同一个 `block_id` 在 `reuse` 前必须有成功的 `produce`；无效顺序应使
replay 失败，而不是静默修正 trace。

### 3.2 最小请求生命周期

每个请求至少覆盖：

```text
prefix lookup
  -> hit: get existing blocks
  -> miss: produce missing blocks with put
  -> decode/reuse: repeated get on selected blocks
  -> completion: evict selected blocks with remove
```

Replay 必须记录 block 的 source descriptor（`local_nvme` 或 `remote_nof`），
不能只根据 policy 推断实际位置。

### 3.3 参数矩阵

第一版使用可解释、可复现的最小矩阵：

| 参数 | 值 |
| --- | --- |
| `block_size` | `16 KiB`, `64 KiB`, `128 KiB`, `256 KiB` |
| `reuse_ratio` | `0%`, `50%`, `90%` |
| `concurrency` | `1`, `8`, `32` |
| `local_background` | `idle`, `50%`, `90%` calibrated load |
| `remote_background` | `idle`, `large-block stressed` |
| `policy` | `local_only`, `remote_only`, `round_robin` |

先跑单变量 smoke 矩阵，再跑完整组合；每个组合至少三次，保留原始
trace、JSON 结果和环境快照。

## 4. 对照组与指标

### 4.1 对照组

每个相同 trace 运行三种模式：

1. `no_store`：不访问 Mooncake，miss 使用固定的重算时间模型；
2. `direct`：沿用显式 `ReplicateConfig`，作为指定 backend 的控制组；
3. `transparent`：普通 `store.put/get/remove`，由 Master policy 决定目标。

`direct` 与 `transparent` 用于隔离 placement 软件开销；`no_store` 只用于
估算 cache reuse 对请求级行为的贡献，不得与存储带宽结果混为一谈。

### 4.2 必须记录的指标

存储层：

- `put/get/remove` 的 p50/p95/p99 latency；
- put/get bandwidth 和 operation rate；
- 进程 CPU utilization；
- 实际 local/remote descriptor 分布。

Cache 层：

- request hit rate、block hit rate、miss rate；
- local hit、remote hit 和 miss 的比例；
- produce/reuse/evict 的数量；
- 平均每个请求重新计算的 block 数。

请求层：

- request completion latency；
- cache lookup、storage wait、recompute 三部分耗时；
- 在 workload simulator 中可计算时，报告 TTFT proxy 和 tail latency。

所有百分比必须给出分母和样本数；local 与 remote 的 delta 分开报告，
不能平均。

## 5. 实现步骤

### Step 1：定义 trace schema 与确定性生成器

新增 `experiments/nvmeof/kv_workload.py`，实现：

- 固定随机种子；
- prefix/block 生成；
- 指定 reuse ratio、并发度和 block size；
- trace schema 校验；
- JSONL 输入/输出；
- trace manifest（参数、seed、版本、生成时间）。

验收：同一 seed 和参数生成字节级相同的 trace；非法 block 生命周期被拒绝。

### Step 2：实现 Store replay runner

在同一模块中增加 replay runner，复用当前 Store 连接和 descriptor 校验
逻辑；不要复制 `transparent_benchmark()` 的统计公式，抽取或复用已有
percentile/result helper。

runner 需要：

- 按 timestamp 或并发 worker 调度事件；
- 对 `produce/reuse/evict` 调用 `put/get/remove`；
- 为每次操作保存 key、block、目标 policy、descriptor、返回码和 latency；
- 任何错误都写入结果并使该 case 失败，不得继续生成“成功”命中率。

### Step 3：增加命令入口与结果契约

在 [`run.sh`](../run.sh) 增加 `kv-workload-generate`、`kv-workload-replay`
和 `kv-workload-summarize` 三个入口，分别负责生成、执行和汇总。

建议结果布局：

```text
results/<run-id>/kv-workload/
  trace.jsonl
  manifest.json
  raw-*.json
  operations.csv
  summary.csv
  conclusion.json
```

`conclusion.json` 只能在所有必需 case 完成且无错误时标记 `status=pass`；
缺失重复、descriptor 不匹配、trace 版本不一致或混用 run ID 时必须为
`inconclusive`。

### Step 4：编写 workload 单测与离线汇总测试

新增 `test_kv_workload.py`，覆盖：

- trace deterministic generation；
- schema/lifecycle validation；
- hit/miss 统计；
- local/remote descriptor 统计；
- p50/p95/p99 与吞吐计算；
- 缺失样本、失败操作、混合 run ID 的拒绝；
- `no_store/direct/transparent` 三种模式的结果 schema。

### Step 5：执行分阶段硬件实验

执行顺序固定为：

1. 单请求、单 block、`reuse_ratio=0%/90%` smoke；
2. `concurrency=8/32`，无背景负载；
3. local 50%/90% 背景负载；
4. remote large-block 背景负载；
5. 三种 policy 的完整矩阵。

所有硬件结果需带同一版本 commit、trace manifest、Master policy、backend
descriptor 和环境 inventory。只在当前两节点拓扑范围内解释结果。

### Step 6：形成上层结论

汇总至少三张图/表：

1. reuse ratio 对 hit rate 和 request latency 的影响；
2. local/remote source 对 storage wait 与 tail latency 的影响；
3. direct/transparent 的额外开销与 cache reuse 收益对比。

结论必须分为：

- 已由 trace replay 直接测得的现象；
- 由固定重算模型推导的 proxy；
- 当前两节点、两 SSD 未覆盖的集群级问题。

## 6. 验收标准

- 同一 trace 在三种模式下可复现，参数和 seed 被写入 manifest；
- 每个成功的 `reuse` 都能追溯到已验证的 `put` 和实际 descriptor；
- 至少完成 `16/64/128/256 KiB × 0%/50%/90% reuse × 1/8/32 concurrency`
  的 smoke/主矩阵子集，并保留每次重复的 raw JSON；
- 结果同时包含 storage、cache、request 三层指标；
- local-only、remote-only、round-robin 的 source 分布与 policy/descriptor
  一致；
- 任一失败操作、缺失重复或 descriptor mismatch 都不会产生 `status=pass`；
- `no_store` 的重算时间模型在报告中显式标注为 proxy，不冒充真实模型测量；
- 报告明确声明两节点/两 SSD 的外推边界，不声称多 SSD 扩展性或 HA 能力；
- 现有 transparent overhead 基线仍能独立运行，结果 schema 不回归。

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| synthetic trace 与真实 serving 行为偏离 | 先固定 schema，再预留真实 vLLM/SGLang trace adapter；没有真实 trace 时只报告 proxy |
| workload 统计掩盖存储错误 | 每个 reuse 校验 descriptor 和内容；失败立即污染 case 状态 |
| 并发导致结果不可复现 | 固定 seed、事件顺序和 worker 调度策略；记录运行时配置 |
| local/remote 实验混入不同 backend | direct 与 transparent 使用同一 trace、对象大小、并发度和背景负载；按 target 分开汇总 |
| 现有 acceptance 脚本仍含未计划的旧场景 | 在实现前审查 `correctness.py` 的 acceptance artifact 列表，使文档计划、命令入口和验收契约一致；不因此新增 HA/NoF 恢复实验 |
| 结果被误解为模型端到端收益 | 单独标记 storage/cache/request 三层，TTFT 仅称 proxy，完整框架集成另立计划 |

## 8. 里程碑与提交规则

每个完成并验证的里程碑单独提交：

1. `trace schema + deterministic generator + unit tests`；
2. `Store replay runner + result contract`；
3. `run.sh commands + offline summarizer + tests`；
4. `two-node workload evidence + report updates`。

每个里程碑完成后，只对相关文件执行 `git add` 和聚焦的 `git commit`，
再进入下一个里程碑。任何硬件实验结果必须连同 trace manifest、commit、
环境快照和原始 JSON 一起归档。

## 9. 推荐执行路径

先执行 Step 1–4 的离线开发和测试，再执行 Step 5 的两节点硬件矩阵。
不要先扩容节点。只有当 trace replay 显示明确的 workload 级现象、且
论文问题转向“多 SSD 扩展性”时，才另行规划第三节点或更多 SSD。
