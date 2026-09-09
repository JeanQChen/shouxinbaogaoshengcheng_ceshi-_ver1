# Phase 3 Actual-Path — 41 问（300750）

- run_id: `reaccept_20260909T050641Z`
- model: `deepseek-v4-pro`
- 数据: 7 题

## 完成状态（§7.7 确定性最低规则，非 LLM 自评）

| 状态 | 数量 |
|---|---|
| FULL | 1 |
| PARTIAL | 6 |
| UNRESOLVED | 0 |
| NOT_IMPLEMENTED | 0 |
| FAILED | 0 |

## 路由分布（实际）

| route | 数量 |
|---|---|
| DEEP_RETRIEVAL | 3 |
| DIRECT_EVIDENCE | 2 |
| EXTERNAL_RESEARCH | 1 |
| STANDARD_RAG | 1 |

## 来源覆盖

- Evidence: 5 题
- Structured: 2 题
- External: 1 题

## 结构化子 need（§三 原始财务问题接入 Financial Snapshot）

- 父路由 DB_LOOKUP 题数: 0
- 结构化子 need 数: 2
- 子 need RESOLVED（结构化结果命中）: 2
- 子 need UNAVAILABLE（快照缺失）: 0
- 子 need NOT_DB_ROUTED（子表达未被路由到 DB）: 0
- 语义不匹配拒绝（数值方面无法精确表达）: 5
- 含结构化子 need 的题数: 2

## 耗时 / token

- 耗时 avg/p50/p95: 17458/16177/27479 ms
- token avg/p50/p95: 7025/7690/9246
- usage 未知调用题数: 0

## 诊断

- 工具致命错误: 0 次
- 工具空结果: 0 次
- 预算停止: 0 题
- 无效循环（连续无新证据）: 0 题
- 外部成本可获取: False

### 本地证据页级诊断（gold 仅离线比对，不并入主判据）

- 适用题数: 6
- Macro RequiredPageCoverage: 0.2962962962962963
- PageHit 题数: 4

## 逐题摘要

| case | route | 实际状态 | stop_reason | 工具数 | 证据 | 结构化 | 外部 | 子need(命中/总) |
|---|---|---|---|---|---|---|---|---|
| COMP-S1 | DIRECT_EVIDENCE | FULL | COMPLETED | 1 | 5 | 0 | 0 | 0/0 |
| COMP-S2 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 3 | 7 | 0 | 0 | 0/0 |
| COMP-R1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 | 0/0 |
| COMP-MV1 | EXTERNAL_RESEARCH | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 0 | 2 | 0/0 |
| COMP-CR1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 | 0/0 |
| FIN-PM1 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 1 | 0 | 1 | 0 | 1/1 |
| FIN-CF1 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 1 | 0 | 1/1 |
