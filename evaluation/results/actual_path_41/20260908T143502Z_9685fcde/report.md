# Phase 3 Actual-Path — 41 问（300750）

- run_id: `20260908T143502Z_9685fcde`
- model: `deepseek-v4-pro`
- 数据: 1 题

## 完成状态（§7.7 确定性最低规则，非 LLM 自评）

| 状态 | 数量 |
|---|---|
| FULL | 1 |
| PARTIAL | 0 |
| UNRESOLVED | 0 |
| NOT_IMPLEMENTED | 0 |
| FAILED | 0 |

## 路由分布（实际）

| route | 数量 |
|---|---|
| DIRECT_EVIDENCE | 1 |

## 来源覆盖

- Evidence: 1 题
- Structured: 0 题
- External: 0 题

## 耗时 / token

- 耗时 avg/p50/p95: 14864/14864/14864 ms
- token avg/p50/p95: 2886/2886/2886
- usage 未知调用题数: 0

## 诊断

- 工具致命错误: 0 次
- 工具空结果: 0 次
- 预算停止: 0 题
- 无效循环（连续无新证据）: 0 题
- 外部成本可获取: False

### 本地证据页级诊断（gold 仅离线比对，不并入主判据）

- 适用题数: 1
- Macro RequiredPageCoverage: 0.16666666666666666
- PageHit 题数: 1

## 逐题摘要

| case | route | 实际状态 | stop_reason | 工具数 | 证据 | 结构化 | 外部 |
|---|---|---|---|---|---|---|---|
| COMP-CR1 | DIRECT_EVIDENCE | FULL | COMPLETED | 2 | 5 | 0 | 0 |
