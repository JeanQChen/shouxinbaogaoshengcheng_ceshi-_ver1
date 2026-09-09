# Phase 3 Batch B — 冒烟报告（第二版修订 / v3）

> 状态：**STOP at human gate（新版 smoke 人工门）**。本文档为第二版修订，针对上版
> [`PHASE3_BATCH_B_SMOKE_REPORT_REVISED.md`](./PHASE3_BATCH_B_SMOKE_REPORT_REVISED.md)
> 被人工门驳回的 5 项定点修复 + 验收修正，重跑 5 问真实冒烟 + 原始 41 问 DB 路径冒烟 +
> **完整 eval**。**未**运行完整 41 问，**未**进入 Batch C / Phase 4。上一版结果全部保留，未覆盖。

---

## 0. 本轮 5 项修复 → 改动落点

| # | 修复项 | 落点 |
|---|---|---|
| 1 | 字段级 Contract Mapping（`covered_aspects` + `coverage_role` 语义，修复 COMP-S1 映射粒度错配） | contracts + harness/aspects |
| 2 | DB Target Resolver 语义精确 + 回归测试（受限资金≠货币资金等不再误配） | routing/db_targets + evals/test_db_targets |
| 3 | 回合预算结构冲突（max_rounds=3→5，ANSWER 不占工具预算，末轮强制收敛，排除已 inspect，连续无新证据早停） | harness/policies + harness/runtime + prompts |
| 4 | `retrieval_observation` claim kind（「本次检索未取得 X」移出事实 entailment，不作方面支撑，不能 FULL） | harness/schema + harness/state + harness/entailment + prompts |
| 5 | `contracts/review/required_aspects_review.md` 业务复核文档（52 题逐题，4 题 BUSINESS_REVIEW_REQUIRED） | contracts/review/ |

---

## 1. 完整 eval（修正上版「后台运行中」不一致）

上版 §9 写「完整 `run_evals` | 见 §10（后台运行中）」，但 §10 未给出结果。本轮已跑完并实测：

```
TOTAL: 2750 passed, 0 failed, 0 skipped  (328.4s)
```

- 90 个 eval 模块全绿，**0 failed / 0 skipped**。
- 其中本次改动的 harness 专项：`test_harness_budget` 23、`test_harness_runtime` 25、
  `test_harness_state` 31、`test_harness_entailment` 31、`test_harness_aspects` 15、全 passed。
- CLI 自检：`harness.aspects --self-check`、`harness.entailment --self-check`、
  `harness.state --self-check` 均通过（模块独立可运行，符合 CLAUDE.md 规则 3）。

> 结论：完整 eval 不再「后台运行中」，已落定。

---

## 2. 5 问真实冒烟：新旧三版对比

公司 `300750`，章节 `company`，5 问均 P0。本轮新 run_id 前缀 `smoke_rev2_`。

| case_id | v1 修订版状态 | **v3（本轮）状态** | 新 completion / stop_reason | 变化原因 |
|---|---|---|---|---|
| COMP-S1 成立时间 | FAILED | **FULL** | COMPLETED | Fix 1：映射粒度修复，aspect 由 6 项→1 项「成立日期」 |
| COMP-S2 实际控制人 | UNRESOLVED | **PARTIAL** | COMPLETED_WITH_GAPS | Fix 3：预算放宽，模型能收敛到 ANSWER；缺口「持股比例与控制链条」 |
| COMP-R1 主营业务及收入占比 | PARTIAL | **PARTIAL** | COMPLETED_WITH_GAPS | 模型仍输出证据外数字（2022 年），被 entailment 正确拦下 |
| COMP-MV1 总市值 | PARTIAL | **PARTIAL** | COMPLETED_WITH_GAPS | 诚实缺口（retrieval_observation，不作事实断言） |
| COMP-CR1 授信+担保 | UNRESOLVED | **PARTIAL** | COMPLETED_WITH_GAPS | Fix 3：不再 BUDGET_ITERATIONS，收敛到 ANSWER；错误数字被拦下 |

**汇总：FULL ×1，PARTIAL ×4，UNRESOLVED ×0，FAILED ×0**
（v1 修订版：FULL ×0 / PARTIAL ×3 / UNRESOLVED ×1 / FAILED ×1；原始版：FULL ×3 / PARTIAL ×1 / UNRESOLVED ×1）。

> 两处 UNRESOLVED/FAILED 均在本轮收敛为 PARTIAL/FULL，且**无一放宽 FULL 定义**：每处 PARTIAL 都有
> 具体的 aspect 缺口 / 数字不支持 / 口径风险证据；COMP-S1 的 FULL 是 Fix 1 修复映射粒度后
> 「1 个正确方面被 1 条被支撑的 claim 覆盖」的诚实结果，不是「有引用即 FULL」。

---

## 3. COMP-S1 —— Fix 1 效果验证（FAILED → FULL）

**现象（上版）**：DATASET_MAPPING 命中 `company_identity_basic` 契约 6 方面，派生 6 个
required_aspects，模型被要求覆盖 6 项，entailment 拦下 5 项无关答案 → FAILED。

**本轮**：字段级映射生效，`required_aspects` = **1 项**「成立日期」，`aspect_source=DATASET_MAPPING`。
- answer：`宁德时代的工商注册日期是2011年12月16日。`
- 1 条 claim c1（fact）→ entailment **SUPPORTED**（scope/period/unit/subject 全 consistent）。
- 工具序列：search_evidence（PARTIAL）→ inspect_evidence（SUCCESS）→ ANSWER。
- 页级诊断：RequiredPageCoverage 1.0，PageHit。

**结论**：Fix 1 修复了「细粒度问题过度派生方面」的映射粒度错配。**不再需要人工门裁决 field 级窄化**——
该问题已闭环。

---

## 4. COMP-CR1 —— 驳回点 #2 专项（UNRESOLVED → PARTIAL，仍非 FULL）

**上版**：模型在 3 方面压力下未收敛，`answer=None`，BUDGET_ITERATIONS，UNRESOLVED。

**本轮（Fix 3 预算放宽后）**：模型收敛到 ANSWER，但输出的数字仍**不是证据正文里的数字**：
- c1「合计授信 155.31 亿元」→ entailment UNSUPPORTED（材料显示的是对外担保额度，口径不同）；
- c2「已使用授信 80.72 亿元」→ UNSUPPORTED（材料仅涉及对外担保额度及发生额）；
- c3「对外担保余额 812.05 亿元」→ UNSUPPORTED（材料实际对外担保余额合计 4,663,512 千元 ≈ 46.64 亿元，与 812.05 亿元不符）；
- c4「均为对子公司担保」→ **PARTIAL**（scope=partial_mismatch，材料仅见「对子公司担保」子项，无法确认覆盖全部对外担保）。

三层拦截（确定性 value-presence + scope_risks `guarantee_scope` + 批量 entailment）**全部生效**，
把模型自算/记错的数字拦下，终态 PARTIAL / COMPLETED_WITH_GAPS。

**结论**：旧版「把局部授信当合计、把为股东担保=0 当全部对外担保=0」的**错误答案没有再被判 FULL**；
且本轮不再因预算不收敛而 UNRESOLVED，而是「收敛作答 → 被正确拦下」，演示路径干净。COMP-CR1 仍为
PARTIAL 是**正确的诚实结果**（材料本就不含可支撑的合计授信/担保数字）。

---

## 5. DB_LOOKUP —— 修订②：合成工具单测 vs 原始 41 问（分开报告，写 0）

> 明确区分：**合成 DB 工具单测证明工具层可用** ≠ **原始 41 问 DB 路径真实可达**。

### 5.1 合成 DB 工具单测（证明工具层，**不作原始路径替代**）

临时库构建 300750 Financial Snapshot，经 `ToolRegistry` 直调 5 用例：

| 工具 | 结果 |
|---|---|
| `lookup_company_field` TOTAL_ASSETS | SUCCESS，974,827,540,000.00 元 |
| `lookup_financial_metric` SOLV_CURRENT_RATIO | SUCCESS，1.60（CALCULATED_EXACT） |
| `compare_financial_periods` | SUCCESS，2024→2025 下降 |
| `lookup_financial_metric` EBITDA | **EMPTY / DB_FIELD_UNAVAILABLE**（预期负例） |
| `compare_financial_periods`（缺失期） | PARTIAL，missing_period 诚实标记 |

**disposition：success ×4，data_unavailable ×1** —— 工具层可用。

### 5.2 原始 41 问 DB 真实路径（Fix 2 语义精确后）

逐题用 `question` 原文构造 `InformationNeed`，经真实 `Router` 路由：

- **路由分布**：DIRECT_EVIDENCE ×9，STANDARD_RAG ×13，DEEP_RETRIEVAL ×7，EXTERNAL_RESEARCH ×4，
  UNDECIDED:ROUTER_FALLBACK_UNAVAILABLE ×8，**DB_LOOKUP ×0**。
- **语义正确的原始 41 问 DB case 数 = 0**（明确写 0，不用 synthetic 替代）。
- **Fix 2 的语义精确效果**：上版 2 个 DB_LOOKUP 题（FIN-RST1「受限资金占比」、FIN-FIX1「机器设备净值」）
  在 Fix 2 后 `db_target_expressible=false`——resolver 正确识别「受限资金 ≠ 货币资金」、
  「机器设备年末净值」不是可解析的标准科目字段，**不再误配**，改走 DIRECT_EVIDENCE。
- 8 个财务题 resolver 可表达但被 DEEP/RAG/EXTERNAL 信号抢占（FIN-GM1/FIN-P1/FIN-TR1/…）；
  5 个财务题 resolver 无法表达（FIN-SLV1/FIN-AUD1/FIN-DEP1/FIN-RST1/FIN-FIX1）。

**判定：`ORIGINAL_41_DB_PATH_NOT_REACHABLE`**（`n_db_routed=0`）。这是 Fix 2 之后的**诚实结论**：
DB 路径对原始 41 问**不普遍可达**，且上版「可达」的 2 例本属语义误配，现已消除。

---

## 6. retrieval_observation（Fix 4）—— COMP-MV1 效果验证

COMP-MV1「宁德时代 2026-08-31 总市值是多少？」路由 EXTERNAL_RESEARCH：
- `aspect_source=TEXT_FALLBACK`，1 个 aspect（整句保 1，不猜）。
- answer：`根据现有可引用材料，无法确定宁德时代2026年8月31日的总市值。`
- claim c1 `kind=retrieval_observation`「现有可引用材料未包含…总市值数据」——**无引用、不参与 entailment、
  不作为 aspect 支撑**。
- 终态 PARTIAL / COMPLETED_WITH_GAPS：`uncovered_aspects=[总市值问题]`，`unresolved_items` 诚实写
  「总市值未在可引用材料中找到」。
- **去重审计生效**：`rejected_duplicate_actions` 记录 2 次 FETCH_EXTERNAL 重复（round 3/4 同 key），
  第二次被拒绝，未重复发起网络请求。

**结论**：「未检索到」被正确表述为**运行时诊断**，而非「X 不存在/为 0」；该诊断不支撑实体事实，
故该题不能 FULL，降为 PARTIAL。与旧版 §8 的「否定断言 entailment 局限」相比，Fix 4 已把该类断言
从事实 entailment 中分离，不再被误判为 UNSUPPORTED 事实。

---

## 7. 新发现（提请人工门，均为 fail-safe 方向，非放宽 FULL）

1. **`unsupported_claims` 跨 ANSWER 补检残留（COMP-S2）**：`state.entailment_verdicts` 每次 ANSWER
   覆盖写（last-write-wins），但 `state.unsupported_claims` 是 append-only、**不清理**。当模型补检后
   第二次 ANSWER（COMP-S2 共 2 次 ANSWER，round 4/5），第二次 entailment 已把 c1/c3 判 SUPPORTED，
   但第一次的 `c1/c3 UNSUPPORTED` 字符串仍留在 `unsupported_claims`，被 `evaluate_success` G4 读取。
   COMP-S2 终态 PARTIAL 本身正确（aspect「持股比例与控制链条」确未覆盖），但 `unresolved_items` 里
   混入了与最终 `entailment_verdicts` 矛盾的陈旧 UNSUPPORTED 记录。**方向 fail-safe（只可能更严，不会放宽）**，
   建议后续批次在每次 ANSWER 前 `state.unsupported_claims.clear()`。
2. **entailment 调用 token 未计入 `state.usage`**：`evaluate_entailment_batch` 经 `chat_with_usage`
   已落盘 `logs/llm/`（满足规则 8），但未调用 `_add_llm_usage`，故 `state.usage` 的 input/output tokens
   不含 entailment 调用，`report` 里的 token 统计略低。属记账缺口，不影响判分。
3. **COMP-R1 模型输出证据外数字**：模型答「2022 年动力电池 2,365.84 亿元 / 72%、储能 449.80 亿元 / 13.69%」，
   但引用材料为 2024 年年报承诺事项文本，无任何 2022 数值。entailment 把 c1~c4 全部判 UNSUPPORTED
   （含 c4 报告期 mismatch）。这是「LLM 不算数字」约束的**正向验证**：证据外数字被正确拦下，未判 FULL。
4. **COMP-CR1 模型仍输出证据外精确数字**（§4），三层拦截全部生效，但尚未产出「数字与证据逐字一致」的
   干净答案——属模型长数字精确引用问题，需 prompt 继续强化「逐字引用，禁自算」。

---

## 8. 验证结果汇总

| 项 | 结果 |
|---|---|
| 完整 `run_evals` | **2750 passed / 0 failed / 0 skipped（328.4s）** |
| `harness.aspects --self-check` | ✓（COMP-R1/COMP-CR1 锚点、TEXT_FALLBACK 保 1 不猜） |
| `harness.entailment --self-check` | ✓（40,000万元==4亿元、scope_risks guarantee_scope high） |
| `harness.state --self-check` | ✓ |
| `test_harness_budget` | 23 passed / 0 failed |
| `test_harness_runtime` | 25 passed / 0 failed |
| `test_harness_state` | 31 passed / 0 failed |
| `test_harness_entailment` | 31 passed / 0 failed |
| `test_harness_aspects` | 15 passed / 0 failed |
| `scripts.run_db_lookup_smoke` | ✓ 原始 41 问 DB 路径 NOT_REACHABLE（0 语义正确 DB case）+ 合成单测 4/1，临时库清理 |

---

## 9. 人工门

按计划**停止**。**未**运行完整 41 问、**未**进入 Batch C / Phase 4。

待人工检查：
1. §5.2 原始 41 问 DB 路径判定 `NOT_REACHABLE`（0 语义正确 DB case）是否接受为本次 smoke 的诚实结论；
2. §7 的 4 个新发现（尤其 #1 `unsupported_claims` 残留、#3/#4 模型证据外数字）是否纳入后续批次；
3. Fix 5 的 `contracts/review/required_aspects_review.md` 中 4 题 `BUSINESS_REVIEW_REQUIRED`
   （company_subject_match / company_business_main / company_debt_guarantee / industry_scale_cycle）
   是否在进入 Phase 4 前由业务确认。
