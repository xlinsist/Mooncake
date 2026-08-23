# KV-cache Master 路径故障结果

## 实验与停止条件

批次 `20260823T183044Z-kv-master-path-fault` 原计划交替运行六次 direct / transparent
128 KiB remote KV trial，`configured_concurrency=1`。每次先完成至少八次事务，再在
client OUTPUT 上对 `10.0.0.34:50051` 插入三秒 TCP DROP，要求 persistent client
恢复并再完成至少八次事务。失败 put 必须始终不可读、不得发布 COMPLETE replica，
并分别在故障解除后和原 client 关闭后接受两个独立的 15 秒清理审计。

第一轮 direct trial 命中严格停止条件，因此后五轮没有继续：失败 put 在两个审计
窗口内均持续返回 `-703` (`REPLICA_IS_NOT_READY`)。这是一个有完整单轮证据的产品
失败，不是六轮矩阵通过，也不把未执行 trial 伪装为缺失样本。

## 已验证结果

该 trial 在故障前完成 9 次事务，故障窗口捕获 1 次失败 put，恢复后同一 persistent
client 完成 8 次事务。首次失败检测为 `1023.492 ms`，故障规则移除后的首次成功为
`145.892 ms`。精确 iptables rule 计数为 35 packets / 4,999 bytes，证明故障实际
命中 client-to-Master 路径；最终 rule 已严格删除。

失败 put 的安全性与生命周期结果必须分开解释：

- 两个窗口中对象始终不可读，descriptor status 始终为空，COMPLETE replica 为 0；
- 原 client 窗口含 59 个 sample，观测跨度 `14.491 s`，deadline probe 仍为 `-703`；
- 原 client 成功关闭后，新建 client 的独立窗口也含 59 个 sample，跨度
  `14.505 s`，deadline probe 仍为 `-703`；
- 因而 operation path 已恢复且没有 unsafe publication，但 failed-put metadata /
  lifecycle residue 没有在 30 秒分段观察内清除，client close 也不是清理边界。

两个更早的失败 pilot 也原样保留。三次逐步加强 gate 的 direct pilot 中，failure
detection 为 `1012.069--1042.646 ms`，persistent-client recovery 为
`98.386--146.820 ms`；中位数分别为 `1023.492/145.892 ms`。这些重复观察可支持
恢复量级描述，但不是已完成的 counterbalanced mode distribution。

## 恢复与硬件连续性

controller EXIT 路径恢复了 root-owned `round_robin` Master。最终 12-object smoke
验证并删除 12/12 对象，位置为 local/remote 各 6，`phantom_replicas=0`；client
OUTPUT 最终仅为 `-P OUTPUT ACCEPT`。

target service 的 before 与 post-abort snapshot 字节一致，均为 active/running、
`MainPID=2072748`；subsystem JSON 也字节一致，NQN、`10.0.0.5:4420` 和 NSID 1
未变化。实验没有 restart 或 unregister target。

## 结论边界

可以写入论文的结论是：短 client-to-Master TCP fault 后，单 client 顺序 KV
操作路径可在约 0.1--0.15 秒内恢复，timed-out put 在观察期间不可读且没有发布
COMPLETE replica；但该 put 持续处于 `REPLICA_IS_NOT_READY`，原 client 关闭后仍
超过 15 秒无法 remove，暴露出未闭环的 failed-put lifecycle residue。

该实验不是 target failure、NoF data-path failure、Master HA、serving、TTFT/TPOT
或 true concurrency 证据。权威 artifact 位于
`experiments/nvmeof/results/20260823T183044Z-kv-master-path-fault/`，其中包含三份
raw archive、设计与执行脚本、逐 trial CSV、审计摘要、停止结论和 checksum。
