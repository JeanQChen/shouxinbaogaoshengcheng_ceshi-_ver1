# Phase 3 frozen_final 正式评测报告（一次性，冻结后）

> **HISTORICAL PHASE 3 v1 FROZEN EVALUATION / NON-EXECUTABLE。** 本文“Phase 3 关闭”只指当时单题运行时、安全、引用、预算和 frozen 评测关闭；不证明完整 Topic 研究，也不满足现行 P3R→P4 内容接口。原结果不得回写或重跑调优。

> run_id: `frozen_final_20260909T151421Z`
> 生成时间：2026-09-09
> 结论：**无 P0 安全缺陷（错误事实进入 FULL / 无来源数字进入正式答案 / 引用无法回查 / Gold 泄漏进运行时均为 0）**。按用户门禁，Phase 3 可标记关闭；不进入 Phase 4。

---

## 0. 冻结基线（RunManifest 完整性）

| 项 | 值 |
|---|---|
| git commit | `ba2e595893edf080aac73340b94cb33a82a99b31`（workspace clean） |
| dataset sha256 | `bb6ea0de00a9984fccc15ca840ff4719c5a2a954e6e0076d098c09093f9e10b8` |
| split manifest sha256 | `08d7dfc0cfd8de809c0479780ba0e1d41e1a4bb86643744542acc5e0fc6e7ad1` |
| split | dev=8 / unseen_validation=9 / **frozen_final=24** / total=41 |
| router_fingerprint | `v2-rule-1.1` |
| harness_fingerprint | `f79631b2dbfeb6ad100846854edaf692ca9cf970ec0368c2bdae2391ca945969` |
| snapshot_id | `snap-490c67acbc3ad4b770087eb817b2c9e3`（report_as_of 2026-03-31, consolidated/CNY/credit_analysis） |
| evidence_fingerprint | `9919ddb14cc870324fcd2ee0f4d7fa5d85fa0d1eb8af09b410fcbae105568966`（3 current docs：2024 年报 / 2025 年报 / 2026 募书） |
| model | `deepseek-v4-pro` |
| freeze_sha256 | `25ef45b864c2fc9c45e258f744fc45c6e365d727c9d06ab6ecabe67128cc794d`（self-verify immutable=true） |

- 只执行 `frozen_final` 24 题（temp dataset subset，原始 dataset 未改）。
- 未修改任何代码 / 规则 / Prompt / Router / Retriever / Evidence / Financial V2 / Section Contract / dataset / gold / split manifest。
- gold 仅进入 Runner 离线 `page_diagnosis`，不进入 runtime prompt（`_build_need` 只取 question 原文）。

---

## 1. 完成状态（§7.7 确定性最低规则，非 LLM 自评）

| 状态 | 数量 |
|---|---|
| FULL | 2 |
| PARTIAL | 13 |
| UNRESOLVED | 1 |
| NOT_IMPLEMENTED | 7 |
| FAILED | 1 |

**P0 子集（6 题）**：FULL 0 / PARTIAL 2 / NOT_IMPLEMENTED 3 / FAILED 1。

## 2. 路由分布（实际）

| route | 数量 |
|---|---|
| DIRECT_EVIDENCE | 6 |
| STANDARD_RAG | 7 |
| DEEP_RETRIEVAL | 2 |
| EXTERNAL_RESEARCH | 2 |
| UNDECIDED | 7 |

- **Router 一致率**：decided 17 题中 13 题与 `expected_route_v2` 一致，4 题偏离，7 题 UNDECIDED（FALLBACK_UNAVAILABLE）。一致率（decided 口径）= 13/17 = **76.5%**。

## 3. 来源覆盖 / 工具路径统计

- 来源：Evidence 15 题、Structured 1 题、External **0** 题。
- 结构化子 need：2 题（FIN-P1 命中 1/1；FIN-AGE1 缺失 1/1），语义不匹配拒绝 3 次。
- **外部搜索/正文/快照成功**：`search_external_sources` 2/2 SUCCESS；`fetch_external_content` 2 EMPTY + 1 FATAL_ERROR(`EXTERNAL_FETCH_BLOCKED`)；`snapshot_external_source` 0。
- DB/Evidence/External 路径：无 DB_LOOKUP 父路由；Evidence 路径 15 题产出可回查证据；External 路径 0 题产出快照。

## 4. 耗时 / token / 诊断

- 耗时 avg/p50/p95：12728 / 14139 / 26056 ms；token avg/p50/p95：6770 / 9541 / 12801；usage 未知调用 0。
- 工具致命错误 1 次、工具空结果 3 次、预算停止 0、无效循环 1 题；外部成本不可得（provider 不返回 cost）。

## 5. 本地证据页级诊断（gold 仅离线比对，不并入主判据）

- 适用题数 22；Macro RequiredPageCoverage **0.295**；PageHit 题数 11。
- 相较 unseen（0.074 / 2/9）明显改善，但仍低于 reaccept 冒烟（≈0.30 / 4/6）。

## 6. 逐题摘要

| case | route | 状态 | stop_reason | 工具 | 证据 | 结构化 | 外部 |
|---|---|---|---|---|---|---|---|
| COMP-S3 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| COMP-D1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| COMP-MS1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 |
| COMP-E1 | EXTERNAL_RESEARCH | UNRESOLVED | CONSECUTIVE_NO_NEW_EVIDENCE | 3 | 0 | 0 | 0 |
| COMP-S1b | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| COMP-C1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 |
| COMP-FP1 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| COMP-R3 | DIRECT_EVIDENCE | **FULL** | COMPLETED | 2 | 5 | 0 | 0 |
| COMP-EQ1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 |
| COMP-ZB1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 2 | 5 | 0 | 0 |
| COMP-DZ1 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| FIN-P1 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 5 | 5 | 2 | 0 |
| FIN-TR1 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| FIN-SLV1 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| FIN-CAX1 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| FIN-DEP1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 7 | 0 | 0 |
| FIN-RST1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| FIN-AGE1 | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| FIN-FIX1 | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| IND-R1 | EXTERNAL_RESEARCH | FAILED | FATAL_TOOL_ERROR | 2 | 0 | 0 | 0 |
| IND-R2 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |
| IND-R5 | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 3 | 5 | 0 | 0 |
| IND-R7 | STANDARD_RAG | **FULL** | COMPLETED | 2 | 12 | 0 | 0 |
| IND-R8 | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 | 0 |

## 7. P0 安全核查（决定是否关闭）

对全部 36 条 evidence 引用逐一回查：**0 缺失**；答案中 **0 条 UNSUPPORTED fact claim**；state `unsupported_claims` 为 0。2 道 FULL 题逐条与 gold 离线核对：

- **COMP-R3**（前五大客户集中度）：答案「38.96% / 165,061,533 千元 / 关联方 0.00%」与 gold 完全一致，引用 `NDSD_2025_year` P28 正文精确命中。
- **IND-R7**（行业风险传导）：5 类风险 + 销量-价格-成本传导路径归纳正确，全部 claim SUPPORTED 且逐条有证据页（P17/P39/P6/P19/P50 等）。

**结论：无 P0 安全缺陷**。0 错误事实进入 FULL、0 无来源数字进入正式答案、0 引用无法回查、0 Gold 泄漏进运行时。

## 8. 问题分类（详见 `problem_classification.json`）

| 类别 | 数量 |
|---|---|
| GENERAL_P0_SAFETY_BUG | **0** |
| GENERAL_IMPLEMENTATION_BUG | 2（F-FF1 Router time_scope；F-FF2 value_presence 非业务数值误伤） |
| RETRIEVAL_OR_DATA_GAP | 2（F-FF3 外部路径 0 快照；F-FF4 残余数据缺口） |
| MODEL_VARIANCE | 0 |
| EXPECTED_PARTIAL | 0 |
| CASE_SPECIFIC | 0 |

- **F-FF1**（P1）：`_time_scope_unparseable` 拒收多期区间('2023-2025'/'2025-2026')与非常规('历史上'/'2025滚动')，runner 无 LLM fallback → 7/24 FALLBACK_UNAVAILABLE，含 3 道 P0（FIN-TR1/FIN-SLV1/COMP-S1b）。与 unseen F2 同根，放大到 29%。
- **F-FF2**（P1）：value_presence 未排除股票代码/年份区间/账龄分桶/计数等非业务数值，是多数 PARTIAL 的 unresolved 主因（unseen F1 同根，未修复）。
- **F-FF3**（P1）：外部搜索 SUCCESS 但 fetch 空/被拦 → 0 快照（unseen F4 同根）。
- **F-FF4**（P2）：子公司体量/高管履历/母公司三表口径/受限资金/账龄指标等数据可得性缺口。

## 9. 完整 eval

- `python -m evals.run_evals`：**2987 passed / 0 failed / 0 skipped**（335.6s，exit 0）。无 regression。

## 10. 门禁决策

- 无 P0 → 按用户指令，Phase 3 标记关闭（同步 `V2_TODO.md`、`V2_IMPLEMENTATION_PLAN.md`）。
- 非 P0 的四项发现（2 实现 / 2 检索数据）仅记录，不在同一次 frozen_final 后修改规则重跑。
- **不进入 Phase 4**，不修改 `PHASE4_DEVELOPMENT_TASK.md`。
