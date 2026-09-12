# Phase 3 — unseen_validation 一次性泛化评测报告（冻结后，不修复）

> **HISTORICAL FROZEN EVALUATION / NON-EXECUTABLE。** 本结果只评价 Phase 3 v1 原子路径与安全/泛化表现；不得重跑调参、不得解释为完整 Topic 或章节覆盖。

> 本轮是泛化评测，不是开发。规则 / 代码 / Prompt 已冻结（git `53f654c`），
> 对 split manifest 的 `unseen_validation` 9 题执行一次真实 Router + 受限研究循环 + 真实 LLM，
> 只记录结果，不修改任何规则以改善结果。

- run_id: `unseen_validation_20260909T105105Z`
- 结果目录: `evaluation/results/unseen_validation_20260909T105105Z/`
- 模型: `deepseek-v4-pro`；Router: `v2-rule-1.0`；外部策略: `v1-provisional`
- 前置冻结: 工作区 clean、dataset_sha256 与 split manifest 一致、9 case_ids 与 manifest 一致

---

## 1. 冻结指纹（RunManifest 摘要）

| 字段 | 值 |
|---|---|
| git commit | `53f654c96552600ba62466b06895438fa37128f6` |
| dataset_sha256 | `bb6ea0de…f9e10b8` |
| split_manifest_sha256 | `08d7dfc0…fc6e7ad1` |
| harness_fingerprint | `0f0538e2…d21edae` |
| router_fingerprint | `v2-rule-1.0` |
| contract_version | `v1` |
| evidence_fingerprint | `9919ddb1…5568966` |
| config_sha256 | `a6dba53e…b7eb525` |
| report_as_of | `2026-03-31`（临时快照） |
| snapshot_id | `snap-1370505b2feadbde95bc8bd2d47d5f5a` |
| 外部检索提供方 | `bocha` |
| 完整指纹 | `PRE_RUN_MANIFEST.json`（immutable 校验通过，`PRE_RUN_MANIFEST.verify.json`） |

9 个 unseen case_ids（与 split manifest `unseen_validation` 完全一致）：
`COMP-BD1 · COMP-RP1 · COMP-SW1 · COMP-RD1 · FIN-INV1 · FIN-AUD1 · IND-R3 · IND-R4 · IND-R6`

---

## 2. 总体结果

| 状态 | 数量 | 占比 |
|---|---|---|
| FULL | **0** | 0% |
| PARTIAL | 7 | 77.8% |
| UNRESOLVED | 1 | 11.1% |
| NOT_IMPLEMENTED | 1 | 11.1% |
| FAILED | 0 | 0% |

- **FULL 率: 0%（0/9）**；FULL+PARTIAL 率: 77.8%（7/9）
- P0 题（COMP-BD1、IND-R3）：均 PARTIAL，0 FULL
- 无工具致命错误（tool_errors=0）、无无效循环（invalid_loops=0）

### 路由分布（实际 vs 期望）

| 实际路由 | 数量 | 期望(route_coverage) |
|---|---|---|
| DIRECT_EVIDENCE | 1 | 4 |
| STANDARD_RAG | 5 | 3 |
| EXTERNAL_RESEARCH | 1 | 1 |
| DEEP_RETRIEVAL | 1 | 1 |
| UNDECIDED | 1 | — |

**Router 与 expected_route_v2 一致率：6/9（66.7%）**。偏差：
- 2 题 DIRECT_EVIDENCE → STANDARD_RAG（COMP-RP1、IND-R4）
- 1 题 DIRECT_EVIDENCE → UNDECIDED（FIN-AUD1，`FALLBACK_UNAVAILABLE`）

### 来源覆盖

- Evidence: 7/9 题；Structured: 1/9 题；External snapshot: 0/9 题
- 结构化子 need：1 个（FIN-INV1 存货），RESOLVED 1 个，语义不匹配拒绝 0

---

## 3. 逐题结果

| case | 优先级 | 期望路由 | 实际路由 | 状态 | stop_reason | 证据 | 结构化 | unresolved |
|---|---|---|---|---|---|---|---|---|
| COMP-BD1 | P0 | DIRECT_EVIDENCE | DIRECT_EVIDENCE | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 1 |
| COMP-RP1 | P1 | DIRECT_EVIDENCE | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 3 |
| COMP-SW1 | P1 | EXTERNAL_RESEARCH | EXTERNAL_RESEARCH | UNRESOLVED | BUDGET_EXTERNAL | 0 | 0 | 0 |
| COMP-RD1 | P2 | STANDARD_RAG | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 1 |
| FIN-INV1 | P1 | DEEP_RETRIEVAL | DEEP_RETRIEVAL | PARTIAL | COMPLETED_WITH_GAPS | 5 | 2 | 3 |
| FIN-AUD1 | P1 | DIRECT_EVIDENCE | UNDECIDED | NOT_IMPLEMENTED | PATH_NOT_IMPLEMENTED | 0 | 0 | 0 |
| IND-R3 | P0 | STANDARD_RAG | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 3 |
| IND-R4 | P1 | DIRECT_EVIDENCE | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 2 |
| IND-R6 | P1 | STANDARD_RAG | STANDARD_RAG | PARTIAL | COMPLETED_WITH_GAPS | 5 | 0 | 1 |

要点：
- **FIN-INV1** 是唯一真正命中结构化快照的题（存货 2025 vs 2024 数值方向正确），核心主问已答对，仅「为什么」需存货周转指标未覆盖 → PARTIAL 属预期内。
- **FIN-AUD1** 无路由（`FALLBACK_UNAVAILABLE`）→ NOT_IMPLEMENTED，未产出答案。
- **COMP-SW1** 外部检索 3 次 SUCCESS 但无可引用快照 → BUDGET_EXTERNAL → UNRESOLVED。

---

## 4. 汇总统计

| 指标 | 值 |
|---|---|
| 耗时 avg / p50 / p95 | 14.7s / 15.8s / 21.3s |
| token avg / p50 / p95 | 7631 / 8844 / 10468 |
| usage 未知调用题数 | 0 |
| 预算停止 | 1（COMP-SW1 BUDGET_EXTERNAL） |
| 工具致命错误 / 空结果 | 0 / 0 |
| 本地页离线诊断 macro coverage | **0.074**（page_hit 2/9） |
| 外部成本可获取 | false（记 counts，成本不可得） |

---

## 5. 问题分类（6 类，只记录不修复）

| 类别 | 数量 | 发现 |
|---|---|---|
| GENERAL_P0_SAFETY_BUG | **0** | 无产生错误答案/实体的安全缺陷；全部失败为保守降级 |
| GENERAL_IMPLEMENTATION_BUG | 3 | F1 value_presence 误伤年份/区间/计数/排名（7/9）；F2 Router 无规则+无 fallback（FIN-AUD1）；F3 Router 误路由 DIRECT_EVIDENCE→STANDARD_RAG（2 题） |
| RETRIEVAL_OR_DATA_GAP | 3 | F4 外部检索无可引用快照（COMP-SW1）；F5 检索页命中率 7.4%（离线）；F6 具体金额/指标未披露或未检索到 |
| MODEL_VARIANCE | 0 | 未见系统性波动 |
| EXPECTED_PARTIAL | 1 | F7 FIN-INV1 主问答对、次问受指标集限制 |
| CASE_SPECIFIC | 0 | — |

完整分类与证据见 `evaluation/results/unseen_validation_20260909T105105Z/problem_classification.json`。

**最突出发现（F1）**：`harness/entailment.py` 的 `deterministic_prechecks → value_presence` 把 claim 中的年份（2023/2024/2025）、年份区间（2023—2025）、连续年数（9 年/5 年）、计数（2 位执行董事 / 54,538 项专利）当作需与证据金额等价的数值逐一判 `value_missing`，是 unseen 集 **0 FULL** 的直接主因。Round-2 的「排除非业务数值」修复（`_extract_claim_business_amounts`）仅作用于 `structured_provenance`，未同步到该 entailment 路径。

---

## 6. 停止边界

- 本轮**不修复**上述任何发现（含 P1），仅记录。
- **未**在本次 unseen run 后修改规则 / Prompt / Router / Retriever / Evidence / Financial V2 / Section Contract / split manifest / gold 后重跑，以免污染未见集。
- 运行时**未**读取或使用 gold answer / gold evidence / gold page（仅用于 Runner 完成后的离线 `page_diagnosis`）。
- 未手工增删 / 替换 / 重排题目；未用 mock、缓存答案、手工答案或 gold 替代。
- 未提交 `.env`、API Key、`.claude/settings.json`、临时数据库、全量 Tool/LLM/Retrieval 原始日志（traces 在 `logs/`，已 gitignore）、任何代码规则或 Prompt 修改。

---

## 7. 交付物

1. `evaluation/results/unseen_validation_20260909T105105Z/PRE_RUN_MANIFEST.json` — 运行前冻结清单（immutable 校验通过）
2. `…/FINAL_MANIFEST.json` — 冻结 + Runner meta + 指标合并
3. `…/run_manifest.json` — Runner 元信息
4. `…/case_results.jsonl` — 9 题逐题完整结果（route/actions/evidence/answer/claims/citations/status/entailment/usage）
5. `…/metrics.json` — 聚合统计
6. `…/report.md` — Runner 自动报告
7. `…/trace_inventory.json` — trace 文件清单（traces 本体在 `logs/harness/`，未提交）
8. `…/problem_classification.json` — 6 类问题分类
9. 本报告 `PHASE3_UNSEEN_VALIDATION_REPORT.md`
10. CLAUDE.md 进度记忆（Phase 3 状态）

---

## 8. 结论

unseen_validation 暴露的是**泛化塌缩而非安全缺陷**：FULL 率 0%，主要归因于 value_presence 对非业务数值的过度判罚（7/9 题受 F1 影响）+ 检索页命中率降至 7.4% + Router 对 1 类新问法无规则、对 2 题误路由。这些均非"产出错误答案"的 P0 安全 bug，而是"正确答案被保守降级为 PARTIAL"的校准/覆盖缺口。按冻结纪律，本轮只冻结记录，不修不重跑；下一步是否在 frozen_final 前解冻修复 F1，留待人工门决策。
