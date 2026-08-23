# KV-cache 两节点 Hardware Smoke 后续执行计划

## 1. 目标与边界

本计划承接 [`07-kv-cache-workload-development-plan.md`](07-kv-cache-workload-development-plan.md)
和 [`08-kv-cache-workload-results.md`](08-kv-cache-workload-results.md)，目标是在
匹配源码、构建和服务环境通过预检后，完成最小的两节点 KV-cache workload smoke。

本阶段只形成证据支持的 local/remote 两节点结论，不开展以下工作：

- HA、Master/NoF 故障恢复或服务重启验收；
- 多 SSD、多节点或集群规模推论；
- 完整 vLLM/SGLang connector、真实模型执行或 DRAM 分层。

硬件写入前的任一构建、权限、注册或版本一致性检查失败，都必须停止写入，
只记录 blocker，不生成伪造的性能结果。

## 2. 执行前固定条件

### 2.1 源码与工作树

1. 在客户端和目标环境确认 `git rev-parse HEAD`、`git status --short` 和
   `kv_workload.py`、`run.sh` 均存在。
2. 使用同一提交创建干净 worktree；不得覆盖已有 dirty checkout、未跟踪产物
   或其备份分支。
3. 记录 worktree 路径、提交 SHA 和配置来源。路径应通过环境变量或现场配置
   提供，不写死为某台机器的目录。

### 2.2 匹配构建

在干净 worktree 使用独立的 `build-nof`：

- `USE_NOF=ON`、Release 配置；
- `mooncake_master`、`nof_worker_pool_bench` 和 Python Store binding 均从
  该 worktree/build 解析；
- 记录构件绝对路径、SHA-256、源码 commit 和 Python `module.__file__`；
- 旧 build 或无法证明来源的 binding 不得作为硬件证据。

### 2.3 只读预检

保存机器输出，至少包括：

- Master 地址和监听状态；
- NoF service 状态、目标注册和容量；
- 客户端非交互权限（例如 `sudo -n true`）；
- Master 当前 policy 和 backend 可用性；
- Python binding 导入路径和必要 API 探针。

预检失败时，结果目录只能包含 inventory 和 blocker 记录，不得执行 Store
`put/get/remove`。

## 3. 统一 trace 与五个 smoke case

只生成一次 trace/manifest，固定 `seed`、参数、`run_id` 和 trace digest。五个
case 必须使用完全相同的 manifest：

1. `no_store`：固定重算 proxy，不访问 Mooncake；
2. `direct-local`：显式选择 local backend；
3. `transparent-local`：Master local policy；
4. `direct-remote`：显式选择 remote backend；
5. `transparent-remote`：Master remote policy。

推荐顺序是先运行 `no_store`，再按 local、remote 执行 direct/transparent
成对 case。每个 Store case 完成后立即检查 raw JSON；任一退出码、生命周期、
descriptor、run ID 或 digest 校验失败，立即停止剩余硬件写入并记录真实原因。

结果目录至少包含：

```text
trace.jsonl
manifest.json
raw-no_store.json
raw-direct-local.json
raw-transparent-local.json
raw-direct-remote.json
raw-transparent-remote.json
operations.csv
summary.csv
conclusion.json
inventory/
```

只有五个 case 全部成功、summary 为 `status=pass` 且 descriptor 与目标一致时，
才允许 `conclusion.json.status=pass`。`no_store` 延迟必须标为 workload proxy，
不得与 Mooncake 存储性能混合解释。

## 4. 执行步骤

### Phase A：固化输入

- 检查干净 worktree、源码提交和 workload 入口；
- 保存客户端/目标环境、配置、Python binding 和服务身份 inventory；
- 确认旧 checkout 不会被 reset、clean 或覆盖。

### Phase B：构建与预检

- 配置并构建独立 `build-nof`；
- 保存 Master、benchmark、binding 的路径和哈希；
- 只读检查 Master、NoF、注册、policy、权限和 API 探针；
- 任一条件不满足则写入 blocker 并结束本轮硬件执行。

### Phase C：生成 trace

```bash
KV_WORKLOAD_RUN_ID=<run-id> \
KV_WORKLOAD_RESULT_DIR=results/kv-workload/<run-id> \
  ./run.sh kv-workload-generate
```

随后校验 manifest 中的参数、seed、run ID 和 digest，并在五个 case 中复用该
trace，不为每个 case 单独生成 trace。

### Phase D：执行与汇总

按第 3 节顺序运行 replay；Store case 需要匹配的 `TRANSPARENT_RUN_ID`、target
和 binding 环境。完成后运行：

```bash
KV_WORKLOAD_RESULT_DIR=results/kv-workload/<run-id> \
KV_WORKLOAD_REQUIRED_CASES=no_store,direct-local,transparent-local,\
direct-remote,transparent-remote \
  ./run.sh kv-workload-summarize
```

汇总器必须拒绝缺失 case、失败操作、重复 case ID、混合 run ID 或混合 trace
digest。保留原始 JSON、CSV、命令输出和服务日志。

### Phase E：报告与提交

更新 `08-kv-cache-workload-results.md` 时只写实际证据支持的结论，至少说明：

- 五个 case 是否全部完成；
- local/remote 的 descriptor 分布和 request-level 指标；
- no-store proxy 与真实 Store 指标的边界；
- 失败 case、环境 blocker 和未执行的 case；
- 结论仍限制在当前两节点、两 SSD 拓扑。

结果和文档必须分开形成小而可追溯的提交，不要提交构建目录、配置密钥或
`results/` 下的本地大文件。

## 5. 验收标准

完成前逐项确认：

1. worktree clean，源码、构建产物和 Python binding 来源一致；
2. Master、NoF、注册、policy 和权限均有机器输出；
3. 五个 case 使用同一 manifest、digest 和 run ID；
4. 每个 Store case 的 `put/get/remove`、descriptor、生命周期和清理均成功；
5. `operations.csv`、`summary.csv`、`conclusion.json` 和 inventory 完整；
6. 本地相关测试、`ruff`、`bash -n` 和 `git diff --check` 通过；
7. 报告未外推到 HA、故障恢复、多 SSD、多节点或模型执行；
8. 只有全部条件满足时才把硬件 smoke 标记为 `pass`，否则保持
   `inconclusive` 并保留 blocker 证据。

建议的本地验证命令：

```bash
python3 -m pytest -q \
  experiments/nvmeof/test_kv_workload.py \
  experiments/nvmeof/test_correctness.py
ruff check experiments/nvmeof/kv_workload.py experiments/nvmeof/test_kv_workload.py
bash -n experiments/nvmeof/run.sh
git diff --check
```

## 6. 恢复执行入口

当前硬件阶段如果被提交不同步、远端 dirty worktree、缺少 workload 文件、
权限或匹配构建阻断，应从 Phase A 重新开始。不得复用旧 build、旧 trace 或
跨 run ID 拼接结果；修复后必须生成新的 inventory，并按五个 case 重新执行。
