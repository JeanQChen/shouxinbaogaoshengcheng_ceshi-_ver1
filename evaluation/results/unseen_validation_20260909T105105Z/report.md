# Phase 3 Actual-Path — 41 问（300750）

- run_id: `unseen_validation_20260909T105105Z`
- model: `deepseek-v4-pro`
- 数据: 9 题

## 完成状态（§7.7 确定性最低规则，非 LLM 自评）

| 状态 | 数量 |
|---|---|
| FULL | 0 |
| PARTIAL | 7 |
| UNRESOLVED | 1 |
| NOT_IMPLEMENTED | 1 |
| FAILED | 0 |

## 路由分布（实际）

| route | 数量 |
|---|---|
| DEEP_RETRIEVAL | 1 |
| DIRECT_EVIDENCE | 1 |
| EXTERNAL_RESEARCH | 1 |
| STANDARD_RAG | 5 |
| UNDECIDED | 1 |

## 来源覆盖

- Evidence: 7 题
- Structured: 1 题
- External: 0 题

## 结构化子 need（§三 原始财务问题接入 Financial Snapshot）

- 父路由 DB_LOOKUP 题数: 0
- 结构化子 need 数: 1
- 子 need RESOLVED（结构化结果命中）: 1
- 子 need UNAVAILABLE（快照缺失）: 0
- 子 need NOT_DB_ROUTED（子表达未被路由到 DB）: 0
- 语义不匹配拒绝（数值方面无法精确表达）: 0
- 含结构化子 need 的题数: 1

## 耗时 / token

- 耗时 avg/p50/p95: 14722/15812/21282 ms
- token avg/p50/p95: 7631/8844/10468
- usage 未知调用题数: 0

## 诊断

- 工具致命错误: 0 次
- 工具空结果: 0 次
- 预算停止: 1 题
- 无效循环（连续无新证据）: 0 题
- 外部成本可获取: False

### 本地证据页级诊断（gold 仅离线比对，不并入主判据）

- 适用题数: 9
- Macro RequiredPageCoverage: 0.07407407407407407
- PageHit 题数: 2

## 逐题摘要

| case | route | 实际状态 | stop_reason | 工具数 | 证据 | 结构化 | 外部 | 子need(命中/总) |
|---|---|---|---|---|---|---|---|---|
| COMP-BD1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 | 0/0 |
| COMP-RP1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 | 0/0 |
| COMP-SW1 | EXTERNAL_RESEARCH | UNRESOLVED | BUDGET_EXTERNAL | 3 | 0 | 0 | 0 | 0/0 |
| COMP-RD1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 | 0/0 |
| FIN-INV1 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 4 | 5 | 2 | 0 | 1/1 |
| FIN-AUD1 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 | 0/0 |
| IND-R3 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 | 0/0 |
| IND-R4 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 | 0/0 |
| IND-R6 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 | 0/0 |
