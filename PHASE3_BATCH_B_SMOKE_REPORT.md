# Phase 3 Batch B — 真实冒烟报告（人工门交付物）

> 状态：**STOP at human gate**。本文档只报告 5 问真实冒烟结果，不进入全量 41 问，不进入 Batch C / Phase 4。

---

## 0. 执行顺序（满足计划要求）

| 步骤 | 结果 |
|---|---|
| 1. `--validate-only` | ✅ 通过（route 分布 / manifest 一致性 fail-closed 验证） |
| 2. mock 测试 | ✅ `test_harness_runtime` 18 断言、`test_actual_path_41` 26 断言、`test_research_preview` 20 断言 全绿 |
| 3. 真实冒烟（3-5 问） | ✅ 5 问，见下文逐题 |
| 4. 完整 eval | ✅ **2674 passed / 0 failed / 0 skipped（275.4s）** |
| 5. 冒烟报告 | ✅ 本文档 |
| 6. 全量 41 问 | ⛔ **未执行**（人工门处停止） |

---

## 1. 冒烟暴露并修复的两个缺陷（已提交）

真实冒烟首次运行 5 问全部 `FAILED ACTION_SCHEMA_INVALID`（空 completion）。根因与修复：

| 缺陷 | 根因 | 修复 commit |
|---|---|---|
| 推理模型截断 | DeepSeek-V4-Pro 为推理模型，推理内容计入 `output_tokens`，开启推理时 `finish_reason=max_tokens` 且正文为空 → 动作 JSON 解析失败 | `bbf1d4c fix(llm): disable thinking for structured research/preview calls` |
| 动作参数 schema 漂移 | 模型额外输出 `{"query":...,"company":"300750"}`（多余 `company` 被 `_ALLOWED_ARGS` 拒绝）、或漏 `query`（缺必填键） | `f48d2e3 fix(prompts): explicit action argument keys` |

修复后复跑：COMP-S1 → FULL，COMP-R1 → FULL。

---

## 2. 冒烟汇总

公司 `300750`，章节 `company`，5 问均为 P0。

| case_id | 原始问题 | 路由 | 实际路径工具 | 状态 / stop_reason |
|---|---|---|---|---|
| COMP-S1 | 成立时间（工商注册日期） | DIRECT_EVIDENCE | search_evidence | **FULL** / COMPLETED |
| COMP-S2 | 实际控制人及控制链条 | DEEP_RETRIEVAL¹ | search + inspect×2 | **UNRESOLVED** / BUDGET_ITERATIONS |
| COMP-R1 | 主营业务及收入占比 | STANDARD_RAG¹ | search_evidence | **FULL** / COMPLETED |
| COMP-MV1 | 2026-08-31 总市值 | EXTERNAL_RESEARCH | search→fetch→snapshot | **PARTIAL** / COMPLETED_WITH_GAPS |
| COMP-CR1 | 合计授信金额 + 对外担保 | DIRECT_EVIDENCE | search + inspect | **FULL** / COMPLETED |

> ¹ COMP-S2、COMP-R1 的 `expected_route_v2` 为 `DIRECT_EVIDENCE`，规则路由器分别判为 `DEEP_RETRIEVAL`（`CROSS_DOCUMENT_OR_CONFLICT`）与 `STANDARD_RAG`（`SECTION_TOPIC_SYNTHESIS`）。这是路由决策与评测预期的不一致，非运行时失败；记为后移事项。

统计：**FULL ×3，PARTIAL ×1，UNRESOLVED ×1，FAILED ×0，NOT_IMPLEMENTED ×0**。
来源覆盖：Evidence ×4 问，External Snapshot ×1 问，Structured/Financial ×0（`financial_v2.db` 无数据行 → `DB_LOOKUP` 不可用，未在本批冒烟中出现）。

---

## 3. 逐题明细

### COMP-S1 — 成立时间（工商注册日期）

- **问题**：宁德时代的成立时间（工商注册日期）是什么时候？
- **路由**：`DIRECT_EVIDENCE`（`EXACT_DOCUMENT_FIELD`，规则判 DECIDED）
- **ToolCall 顺序**：`search_evidence` → PARTIAL（5110ms）
- **取得**：Evidence ×5（`50026163…4c0b` 等）
- **简短答案**：宁德时代的工商注册日期为 **2011年12月16日**。
- **逐条引用**：[fact] 工商注册日期是 2011-12-16 → `evidence_id=50026163c6edfedde21481ef6ead4c0b`
- **未解决要点**：无
- **状态**：FULL / COMPLETED（success）
- **成本**：LLM 3 次 / 工具 1 次 / 本地检索 1 / input 2165 / output 180 / 12851ms

### COMP-S2 — 实际控制人及控制链条

- **问题**：宁德时代的实际控制人是谁？其与实际控制人的关联关系及控制链条如何？
- **路由**：`DEEP_RETRIEVAL`（`CROSS_DOCUMENT_OR_CONFLICT`；评测预期 `DIRECT_EVIDENCE`）
- **ToolCall 顺序**：`search_evidence` → PARTIAL（5122ms）；`inspect_evidence` → SUCCESS ×2（3ms / 1ms）
- **取得**：Evidence ×5（`abd890e1…` 等），但模型持续 `INSPECT_EVIDENCE` 未转 ANSWER
- **简短答案**：**无**（`answer_text=null`）
- **逐条引用**：无
- **未解决要点**：无显式 unresolved（因从未产出答案）
- **状态**：UNRESOLVED / BUDGET_ITERATIONS（success=false）
- **成本**：LLM 3 次 / 工具 3 次 / 本地检索 1 / input 1808 / output 108 / 10354ms
- **诊断（离线，非运行时）**：gold 页覆盖 `required_page_coverage=0.0`（所需 2 页命中 0 页）——仅作本地证据诊断，不参与运行时判分。

> 归类为 **Harness 循环问题 → policy/动作约束修复**：模型在多步 inspect 后未收敛到 ANSWER，触达 `max_rounds=3` 预算停止。是合法的 budget 停止（未冒充成功），但需在后续 policy 优化中评估「每轮是否新增证据」的提前终止信号。

### COMP-R1 — 主营业务及收入占比

- **问题**：宁德时代的主营业务是什么？各业务的收入占比如何？
- **路由**：`STANDARD_RAG`（`SECTION_TOPIC_SYNTHESIS`；评测预期 `DIRECT_EVIDENCE`）
- **ToolCall 顺序**：`search_evidence` → PARTIAL（5119ms）
- **取得**：Evidence ×5
- **简短答案**：主营业务为**动力电池系统、储能电池系统、电池材料及回收、电池矿产资源**四大板块；动力电池系统收入占比最高，储能电池系统为第二大收入来源。
- **逐条引用**：3 条 fact 断言，分别引用 `evidence_id=53c9b372…（页3）` 与 `abd890e1…（页5）`
- **未解决要点**：无
- **状态**：FULL / COMPLETED（success）
- **成本**：LLM 3 次 / 工具 1 次 / 本地检索 1 / input 2181 / output 377 / 12831ms

### COMP-MV1 — 2026-08-31 总市值（诚实缺口）

- **问题**：宁德时代 2026年8月31日的总市值是多少？
- **路由**：`EXTERNAL_RESEARCH`（`EXPLICIT_EXTERNAL_RECENCY`，与评测预期一致）
- **ToolCall 顺序**：`search_external_sources` → SUCCESS（1025ms）；`fetch_external_content` → SUCCESS（1478ms）；`snapshot_external_source` → SUCCESS（**auto**，15ms，Rules 自动快照）
- **取得**：External Snapshot ×1（`ext-7fa76d0ed94e4ea1adc3`）；Evidence ×0
- **简短答案**：根据外部快照材料，宁德时代 2026-08-31 的总市值**未被直接给出**。
- **逐条引用**：[fact] 外部快照 `ext-7fa76d0e…` 中未直接提供该总市值 → 该快照
- **未解决要点**：`未直接获得宁德时代2026年8月31日的总市值数据`
- **状态**：PARTIAL / COMPLETED_WITH_GAPS（success=false，confidence=low）
- **成本**：LLM 4 次 / 工具 3 次 / 外部检索 1 / 正文抓取 1 / 快照 1 / input 2476 / output 272 / 9973ms

> 正面样本：模型**没有**把「未检索到」改写成「不存在」，明确列为 unresolved，符合「把缺失写成不存在」零容忍约束。快照由 Rules 自动触发（`auto=true`），验证了 SNAPSHOT_EXTERNAL 强制路径。

### COMP-CR1 — 合计授信金额 + 对外担保

- **问题**：宁德时代合计授信金额和对外担保金额是多少？
- **路由**：`DIRECT_EVIDENCE`（`EXACT_DOCUMENT_FIELD`，与评测预期一致）
- **ToolCall 顺序**：`search_evidence` → PARTIAL（5120ms）；`inspect_evidence` → SUCCESS（1ms）
- **取得**：Evidence ×5
- **简短答案**：合计授信金额为 **40,000万元**，对外担保金额为 **0万元**。
- **逐条引用**：2 条 fact 断言，分别引用 4 个 `evidence_id`（授信 → `23e72502…`+`e1609d47…`；担保 → `e1609d47…`/`dca229e1…`/`8e019e53…`/`8660f152…`）
- **未解决要点**：无
- **状态**：FULL / COMPLETED（success）
- **成本**：LLM 4 次 / 工具 2 次 / 本地检索 1 / input 2464 / output 422 / 14864ms

---

## 4. 关键结论

1. **受限研究循环真实跑通**：5 问中 3 问 FULL、1 问 PARTIAL（诚实缺口）、1 问 UNRESOLVED（预算停止），无 FAILED、无 NOT_IMPLEMENTED。
2. **成功不以 LLM 自评为准**：每问均由工具返回的 Evidence / External Snapshot 支撑，引用 ID 可回查；COMP-MV1 未拿到数字即如实列为 unresolved。
3. **两个真实缺陷已在冒烟中修复**（推理截断 + 参数 schema），并各加回归测试（`test_harness_runtime` 现 18 断言固定 `thinking=disabled`）。
4. **完整 eval 全绿**：2674 passed / 0 failed。

## 5. 已知限制 / 后移事项（不在此批返工）

- **COMP-S2** Harness 循环不收敛（inspect 未转 ANSWER）→ 后移为 policy/动作约束候选任务，引用 `COMP-S2` 失败证据。
- **COMP-S2 / COMP-R1** 路由决策与评测 `expected_route_v2` 不一致（DEEP_RETRIEVAL / STANDARD_RAG vs DIRECT_EVIDENCE）→ 后移为路由规则评估项。
- **DB_LOOKUP / Financial Snapshot 未在冒烟覆盖**：`financial_v2.db` 当前 0 数据行，`report_as_of=None`，结构化取数路径不可用 → 后移为财务取数适配项。
- **页级 gold 诊断**（`page_diagnosis`）仅在离线诊断使用，**不参与运行时判分**；本报告仅作旁证列出。

## 6. 人工门

按计划停止。**未**运行全量 41 问、**未**预实现 Batch C / Phase 4。等待人工检查引用是否支持句子、是否出现无来源数字、是否把缺失写成不存在。
