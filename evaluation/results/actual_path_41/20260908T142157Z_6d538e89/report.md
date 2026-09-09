# Phase 3 Actual-Path — 41 问（300750）

- run_id: `20260908T142157Z_6d538e89`
- model: `deepseek-v4-pro`
- 数据: 1 题

## 完成状态（§7.7 确定性最低规则，非 LLM 自评）

| 状态 | 数量 |
|---|---|
| FULL | 0 |
| PARTIAL | 0 |
| UNRESOLVED | 0 |
| NOT_IMPLEMENTED | 0 |
| FAILED | 1 |

## 路由分布（实际）

| route | 数量 |
|---|---|
| DIRECT_EVIDENCE | 1 |

## 来源覆盖

- Evidence: 1 题
- Structured: 0 题
- External: 0 题

## 耗时 / token

- 耗时 avg/p50/p95: 62960/62960/62960 ms
- token avg/p50/p95: 3862/3862/3862
- usage 未知调用题数: 0

## 诊断

- 工具致命错误: 0 次
- 工具空结果: 0 次
- 预算停止: 0 题
- 无效循环（连续无新证据）: 0 题
- 外部成本可获取: False

### 本地证据页级诊断（gold 仅离线比对，不并入主判据）

- 适用题数: 1
- Macro RequiredPageCoverage: 1.0
- PageHit 题数: 1

## 逐题摘要

| case | route | 实际状态 | stop_reason | 工具数 | 证据 | 结构化 | 外部 |
|---|---|---|---|---|---|---|---|
| COMP-S1 | DIRECT_EVIDENCE | FAILED | ACTION_SCHEMA_INVALID | 1 | 5 | 0 | 0 |
