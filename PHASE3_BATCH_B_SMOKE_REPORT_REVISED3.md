# Phase 3 Batch B — 冒烟报告（第三版修订 / v4 重新验收）

> 状态：**REACCEPTANCE / STOP at human gate（重新验收人工门）**。本文档针对 v3
> [`PHASE3_BATCH_B_SMOKE_REPORT_REVISED2.md`](./PHASE3_BATCH_B_SMOKE_REPORT_REVISED2.md)
> 被人工门驳回后下达的 **6 项收口（§一~§六）** 逐项落地 + 重跑代表性 smoke（7 问真实 LLM）
> + 完整 eval。**未**运行完整 41 问，**未**进入 Batch C / Phase 4。上一版结果全部保留，未覆盖。

---

## 0. 六项收口 → 改动落点

| # | 收口项 | 落点 | 验证 |
|---|---|---|---|
| 一 | 修复跨 ANSWER 状态污染 | `harness/runtime.py`（`answer_revision` 版本标记 + 每次 ANSWER 前清理 `unsupported_claims` 等 derived state）、`harness/schema.py` | `test_harness_runtime` 40 passed |
| 二 | 补齐 entailment usage 记账 | `harness/schema.py`（`UsageLedger.llm_by_category`/`llm_latency_ms`）、`harness/runtime.py`（3 类 LLM 记账）、`evaluation/run_actual_path_41.py`（报告 surface） | §5 实测 |
| 三 | 原始 41 问真正接入 Financial Snapshot | `harness/structured_needs.py`（新）、`harness/runtime.py`（`_run_structured_subneeds`）、`scripts/run_reacceptance_smoke.py`、`scripts/run_structured_smoke.py` | §3 实测 + `test_harness_structured_needs` 20 passed |
| 四 | 确认并修订 Required Aspects | `templates/contracts/standard_v2.yaml`、`contracts/review/required_aspects_review.md` | `test_harness_aspects` 15 passed |
| 五 | 模型证据外数字处理 | `llm/prompts/research_answer_v1.txt`（规则 1/8/9 强化）、`harness/runtime.py`（结构化值注入 + 有界正文）、`harness/entailment.py`（`bounded_text` 公开别名） | §4 实测 |
| 六 | 重新验收 | 本报告 + 分责提交（见 §9） | 本节 |

> 收口三修复期间发现并修复一处集成 bug：`harness/checkpoint.py` 的 `write_question_outcome`
> 用 `dataclasses.asdict` + `json.dumps` 序列化全量 state，但结构化子 need 的 DB 工具把
> `Decimal` 金额/指标值写进 `ToolResult.data`，导致 `TypeError: Decimal is not JSON serializable`。
> 已加 `_json_default`（`Decimal → str`，其余仍 fail-loud）+ 回归测试 `test_harness_checkpoint`（+2，11 passed）。

---

## 1. 完整 eval

```
TOTAL: 2787 passed, 0 failed, 0 skipped  (286.4s)
```

- 上版（v3）2750 passed → 本轮 2787 passed（+37，含 §一~§五 的 harness 专项 + §三 `structured_needs` + checkpoint Decimal 回归）。
- 本次改动相关专项全绿：`test_harness_structured_needs` 20、`test_harness_checkpoint` 11、
  `test_harness_runtime` 40、`test_harness_state` 31、`test_harness_entailment` 31、
  `test_harness_aspects` 15、`test_actual_path_41` 32。
- CLI 自检：`harness.structured_needs --self-check`、`harness.entailment --self-check`、
  `harness.aspects --self-check`、`harness.state --self-check` 均通过（模块独立可运行，符合 CLAUDE.md 规则 3）。

---

## 2. 7 问真实冒烟（新 run_id）

- run_id：`reaccept_20260909T050641Z`
- 产物：`evaluation/results/actual_path_41/reaccept_20260909T050641Z/`
- 公司 `300750`，5 问 company（P0）+ 2 问 financial（FIN-PM1 纯指标 / FIN-CF1 hybrid）。

| case_id | v3 状态 | **v4（本轮）状态** | 路由 | 结构化子 need | 变化原因 |
|---|---|---|---|---|---|
| COMP-S1 成立时间 | FULL | **FULL** | DIRECT_EVIDENCE | — | 不变 |
| COMP-S2 实际控制人 | PARTIAL | **PARTIAL** | DEEP_RETRIEVAL | — | §一 清理跨 ANSWER 残留，终态无陈旧 UNSUPPORTED 混入 |
| COMP-R1 主营及收入占比 | PARTIAL | **PARTIAL** | STANDARD_RAG | — | §四 已答收入占比（74.7% 等），但金额触发发现 #1（表头单位） |
| COMP-MV1 总市值 | PARTIAL | **PARTIAL** | EXTERNAL_RESEARCH | — | 诚实缺口，无幻觉 |
| COMP-CR1 授信+担保 | PARTIAL | **PARTIAL** | DIRECT_EVIDENCE | — | 口径风险 + 缺合计授信，仍正确拦截 |
| FIN-PM1 净利率 | （未接入） | **PARTIAL** | DEEP_RETRIEVAL | **1/1 RESOLVED** | §三 新接入，数字 18.12% 命中；发现 #2 结构化-only entailment |
| FIN-CF1 经营现金流 | （未接入） | **PARTIAL** | DEEP_RETRIEVAL | **1/1 RESOLVED** | §三 hybrid：结构化 1 + 证据 5，c1 SUPPORTED |

**汇总：FULL ×1，PARTIAL ×6，UNRESOLVED ×0，FAILED ×0，NOT_IMPLEMENTED ×0。**

> v3 的「原始 41 问 DB 路径 `NOT_REACHABLE`（0 语义正确 DB case）」在本轮被 §三 的「结构化子 need」
> 补上：**父路由仍保持 Phase 2 原始判定（DEEP_RETRIEVAL，不篡改 Track B），但数值/指标方面确定性
> 派生出子 InformationNeed，单独路由到 `DB_LOOKUP` 并命中临时 Financial Snapshot**。DB 工具层不再是空转。

---

## 3. §三：结构化子 need（原始财务问接入 Financial Snapshot）

### 3.1 统计

| 指标 | 值 |
|---|---|
| 父路由 DB_LOOKUP 题数 | **0**（父路由保持 Phase 2 原始，不篡改） |
| 结构化子 need 数 | 2 |
| 子 need RESOLVED | **2 / 2** |
| 子 need UNAVAILABLE / NOT_DB_ROUTED | 0 / 0 |
| 语义不匹配拒绝（数值方面无法精确表达） | 5 |
| 含结构化子 need 的题数 | 2 |

5 个语义不匹配拒绝全部来自 company 问的数值方面，它们**无法从财务报表精确表达**，被正确拒绝而非
冒充 DB 结果：COMP-S2「持股比例」、COMP-R1「各业务收入及收入占比」、COMP-MV1「总市值」、
COMP-CR1「合计授信金额 / 对外担保金额」（2 项）。

### 3.2 FIN-PM1（纯指标）

- 原始问题「2025年合并口径下，销售利润率（净利率）如何变化？」，父路由 `DEEP_RETRIEVAL`
  （`CROSS_DOCUMENT_OR_CONFLICT`，Phase 2 原样）。
- 派生子 need：aspect「净利率」→ `PROF_NET_MARGIN`（formula_version 1.0）→ 子路由 `DB_LOOKUP`
  （`REGISTERED_FINANCIAL_METRIC`）→ `lookup_financial_metric` → **18.12%（period 2025-12-31）**。
- answer `2025年合并口径下，销售利润率（净利率）为18.12 percent。`，citation `ref_type=structured`
  （`formula_id=PROF_NET_MARGIN`，period 2025-12-31）。数字来自 StructuredResult，LLM 未重算。
- 终态 PARTIAL：见发现 #2（纯 structured 引用无正文可回查，G4 fail-closed 判 UNSUPPORTED）。

### 3.3 FIN-CF1（hybrid：结构化数字 + Evidence 原因）

- 原始问题「合并口径下，2025年经营活动现金流净额是多少？变化原因是什么？」，父路由 `DEEP_RETRIEVAL`。
- 派生子 need：问题直接点名「经营活动现金流净额」→ `OPERATING_CASH_FLOW`（field）→ 子路由
  `DB_LOOKUP`（`REGISTERED_DB_FIELD`）→ `lookup_company_field` → **133,219,980,000.00 元（2025-12-31）**。
- answer 同时引用 **1 条 structured**（OPERATING_CASH_FLOW）+ **5 条 evidence**（合并现金流量表，
  用于「变化原因」：销售商品收到的现金较上年上升等）。
- claim c1「经营活动现金流净额 133219980000.00 yuan」→ entailment **SUPPORTED**
  （「结构化值 OPERATING_CASH_FLOW 为 133219980000.00 元，与 claim 完全一致，主体合并口径，期间 2025 年度」）。
- 终态 PARTIAL：c2~c4（变化原因明细金额）触发发现 #1（表头单位 value_presence 误判）。

**结论**：§三 要求的「一个问同时组合 StructuredResult + Evidence」在 FIN-CF1 上闭环，且数字严格来自
结构化结果（LLM 只解读）；「父路由不变、子 need 单独路由、语义不匹配拒绝」三点均在本次 smoke 实证。

---

## 4. §五：模型证据外数字处理

- **COMP-R1**：模型已答各业务收入占比（动力电池 74.7% / 储能 14.7% / 材料回收 5.2% / 矿产资源 1.4% /
  其他 4.0%），且 c1~c7 的 LLM entailment **全部 SUPPORTED**（表 5-10 主营业务收入构成表逐字一致）。
  但确定性 value_presence 对 c2~c6 的「金额（万元）」判「数字未找到」——这是发现 #1（表头单位），
  **不是模型自算**。§四 的「收入占比」已从「没答」修复为「答出且被支撑」。
- **FIN-CF1**：结构化数字（c1）逐字来自 StructuredResult，entailment SUPPORTED；「变化原因」证据
  （c2~c4）逐字来自现金流量表正文。无「凭内部知识/记忆填数字」。
- **answer prompt 规则 1/8/9** 生效：无证据数字时模型写入 `unresolved_items`（COMP-MV1「未检索到总市值」、
  COMP-CR1「缺合计授信金额」），不再「合理」填空。

---

## 5. §二：entailment usage 记账验证

读取 checkpoint `reaccept_20260909T050641Z` / FIN-PM1 的 `state.usage`：

| 分类 | calls | input_tokens | output_tokens | latency_ms |
|---|---|---|---|---|
| action | 2 | 2001 | 78 | 3424 |
| answer | 1 | 19 | 251 | 2748 |
| entailment | 1 | 744 | 125 | 2579 |
| **合计** | **4** | **2764** | **454** | **8751** |

`llm_calls=4` = 2 action + 1 answer + 1 entailment；`llm_latency_ms=8751` = 三者之和。§二 记账在
UsageLedger 中已闭环，且 `run_actual_path_41._record_case` 现 surface `llm_by_category` +
`llm_latency_ms`（后续 run 的 `case_results.jsonl` 直接可见）。

---

## 6. 新发现（提请人工门，均为 fail-safe 方向，非放宽 FULL）

1. **表头单位 value_presence 误判（确定性层 false positive）**：`harness/entailment.py` 的
   `normalize_amount`/`extract_amounts` 只识别**数字紧跟**的金额单位（`万元`/`千元`/`%` 等），
   **不识别表格表头「单位：万元，%」**。导致中文财报里最常见的表格式（表 5-10 主营业务收入构成表、
   合并现金流量表）金额被 G3a 判「数字在证据正文中未找到数值等价」：
   - COMP-R1 c2~c6（31,650,636.9万元 等）、FIN-CF1 c2~c4（13,321,998.2万元 等）——这些数字**实际
     逐字在证据正文里**（LLM entailment 全判 SUPPORTED），但确定性层因表头单位未内联而失配。
   - **方向 fail-safe**（只会把正确答案降为 PARTIAL，不会把错误答案放成 FULL），但**降低精度**。
   - 建议后续批次：`extract_amounts` 增加「表头单位上下文传播」（识别 `单位：万元，%` 表头，向下
     应用于该表所有数值）。
2. **结构化-only 引用 entailment fail-closed（FIN-PM1）**：纯指标题只命中结构化子 need、无任何
   Evidence 正文时，G4 法官判 `UNSUPPORTED`（「引用材料为结构化值且无任何正文证据，无法确认主体/期间
   口径一致性」）。这是 fail-closed 的**预期行为**（数字正确但无法用正文回查口径），但意味着
   「仅结构化可答」的纯指标题目前到不了 FULL。建议后续批次在 entailment 规则中明确
   「`ref_type=structured` = 代码算好的权威值，口径一致性由 StructuredResultRef 的 scope/currency/
   period 元数据判定，不要求正文 corroboration」。
3. **纯指标题「如何变化」无法单期作答（FIN-PM1）**：FIN-PM1 问「净利率如何变化」，但快照只有单一
   报告期（2025-12-31），「变化/同比」无第二期可比。结构化子 need 正确返回单期 18.12%，但「变化」
   方面无法闭合。这是数据边界（快照仅 3 张样本 Excel 的 2025 期），非 harness 缺陷。

---

## 7. 验证结果汇总

| 项 | 结果 |
|---|---|
| 完整 `run_evals` | **2787 passed / 0 failed / 0 skipped（286.4s）** |
| 重新验收 smoke | ✓ run_id `reaccept_20260909T050641Z`，7 问 FULL×1 / PARTIAL×6，0 崩溃 |
| `harness.structured_needs --self-check` | ✓（FIN-PM1/FIN-CF1/FIN-GM1 派生 + 语义不匹配拒绝） |
| `test_harness_structured_needs` | 20 passed / 0 failed |
| `test_harness_checkpoint` | 11 passed / 0 failed（含 Decimal 序列化回归） |
| `test_harness_runtime` | 40 passed / 0 failed |
| §二 entailment 记账 | ✓ FIN-PM1 `llm_by_category` action 2 / answer 1 / entailment 1 |
| §三 结构化子 need | ✓ 2/2 RESOLVED，父路由 DB 数 0（不篡改 Track B），语义不匹配拒绝 5 |

---

## 8. 人工门

按计划**停止**。**未**运行完整 41 问，**未**进入 Batch C / Phase 4。

待人工检查：
1. §6 发现 #1（表头单位 value_presence 误判，影响 COMP-R1 / FIN-CF1 精度）是否纳入后续批次修复；
2. §6 发现 #2（结构化-only 引用 entailment fail-closed）是否在后续批次把 `ref_type=structured`
   定义为权威值（口径一致性由元数据判定）；
3. §6 发现 #3（纯指标题单期快照无法答「变化」）是否需补多期快照样本；
4. §四 的 `contracts/review/required_aspects_review.md` 中 4 题 `BUSINESS_REVIEW_REQUIRED`
   （company_subject_match / company_business_main / company_debt_guarantee / industry_scale_cycle）
   是否在进入 Phase 4 前由业务确认。

---

## 9. 分责提交（§六）

按 5 项收口分责（+ 报告 + 冒烟脚本），每项一个 commit，互不混改（CLAUDE.md「One module per change」）。

- [x] commit 1（§一）：`harness/runtime.py` + `harness/schema.py` 跨 ANSWER 状态污染修复
- [x] commit 2（§二）：`harness/schema.py` + `harness/runtime.py` + `evaluation/run_actual_path_41.py` entailment 记账
- [x] commit 3（§四）：`templates/contracts/standard_v2.yaml` + `contracts/review/required_aspects_review.md`
- [x] commit 4（§三）：`harness/structured_needs.py` + `harness/runtime.py` + `harness/checkpoint.py`（Decimal 修复）
- [x] commit 5（§五）：`llm/prompts/research_answer_v1.txt` + `harness/entailment.py` + `harness/runtime.py`
- [x] commit 6（§六）：冒烟脚本 + 本报告 + 测试（`test_harness_structured_needs` / `test_harness_checkpoint` 回归）

> 提交时原计划 6 项合并为 4 个 commit：`7b6bc97`（§四）/ `eb2e5e4`（§三）/
> `41783ca`（§一 + §二 + §五）/ `d929a7a`（§六 + 本报告）。

**不提交**：`.claude/settings.json`、`.env`、API Key、临时数据库、`evaluation/results/actual_path_41/`
运行目录（含本次 `reaccept_*`）、Phase 2 冻结结果。

---

## 10. 规则冻结前收口（三项修复 + split manifest）

> 人工门结论（v4 重新验收 §8）后，§6 三个发现在本轮冻结前收口。本轮**只**做三修复 +
> 防过拟合 split manifest，定点回归 3 题（新 run_id），**未**跑完整 41 问。修复后不再
> 围绕原开发题调整规则或 Prompt。

### 10.1 三修复

| 修复（对应 §6 发现） | 落点 | 专项测试 |
|---|---|---|
| #1 表头/列级单位上下文传播 | `harness/entailment.py`（`extract_evidence_amounts` 优先级：行内 > 列头 > 表级单单位 > 表级多单位列对位；三态 `value_presence`；canonical Decimal 归一化 `4亿元==40,000万元==400,000,000元`） | `test_harness_entailment` **48** |
| #2 StructuredResult 权威化 | `harness/structured_provenance.py`（新：Store 权威复合判定 + 三 evaluator 区分 + `exclude_claim_ids`） | `test_harness_structured_provenance` **27** |
| #3 跨期/趋势结构化子 need | `harness/structured_needs.py`（`period_mode` single/compare/trend + `resolve_comparison_periods` + `compare_tool_args`） | `test_harness_structured_needs` **31** |

> 另加 `active_snapshot_id` run 级锁定（`test_harness_snapshot_lock` **7**）：run 开始由
> `context.snapshot_id` 一次性锁定，`_reset_answer_derived_state` 不清除；resume 时
> `manifest_mismatches` 逐字段校验 `snapshot_id`（fail-closed）。

### 10.2 split manifest（防过拟合）

- `evaluation/split_manifest.py`（`python -m evaluation.split_manifest`）确定性分层：**dev=8 固定**、
  **unseen_validation=9**（`(section_id, priority, expected_route_v2)` 3-way 分层 + 路由覆盖校验）、
  **frozen_final=24**。只读 `case_id/section_id/priority/expected_route_v2`，**不读 gold**。
- 产物 `evaluation/datasets/v1_baseline.split_manifest.json`（`algorithm=split_manifest_v1`、
  `seed=42`、`dataset_sha256=bb6ea0de…`、`route_coverage.covered=true`）。本轮**只生成 + 提交**，未跑。

### 10.3 定点回归（3 题，新 run_id `freeze_rerun_20260909T084110Z`）

| case_id | 路由 | 子 need | 终态 | 验证点 |
|---|---|---|---|---|
| COMP-R1 | STANDARD_RAG | — | PARTIAL（0 失败） | 表 5-10「单位：万元，%」金额+占比全部 SUPPORTED，false-positive 消除 |
| FIN-PM1 | DEEP_RETRIEVAL | compare / [2024,2025] RESOLVED | PARTIAL（0 失败） | structured 权威（c1/c2 exclude=SUPPORTED）+ 两期比较 c3「上升」SUPPORTED |
| FIN-CF1 | DEEP_RETRIEVAL | single / 2025 RESOLVED | PARTIAL（0 失败） | 结构化值 133,219,980,000 元 SUPPORTED（修 snapshot_id 转写错）；变化原因诚实「未提供」 |

- 完整 `run_evals`：**2862 passed / 0 failed / 0 skipped**。
- 无新增 300750 / 宁德时代 / case_id 专用分支（合成测试公司无关）；token/latency 完整记账
  （`usage_unknown_calls=0`，`tool_errors=0`）。
- 定点回归中发现并修复一处 `structured_provenance` 集成 bug：LLM 写引用时把 opaque `snapshot_id`
  `…eadbde…` 转写为 `…deadbe…`，`_resolve_ref` 原按 snapshot+period 精确匹配 → `unresolvable_ref`。
  改为按 `period+code` 匹配、snapshot_id 仅作偏好（ref 自身 snapshot_id 由 `_check_ref` 做 is_current
  校验）。回归 `test_harness_structured_provenance`（+1）。
- 终态 3 题均 PARTIAL（非 FAILED）原因：COMP-R1「各业务收入占比」是 segment 级无法映射 DB 指标
  （`segment_scope_qualifier`，证据通道已答出）；FIN-PM1/FIN-CF1 父路由 DEEP_RETRIEVAL + 结构化-only
  答案无 `evidence_ids` → `classify_completion` 路由级降 PARTIAL（**报告级分类，非 harness 规则缺陷**）。

### 10.4 冻结候选（核心 harness policy / prompt / version）

| 项 | 值 |
|---|---|
| harness_fingerprint | `c8589656f816f15ad7029e4036a11681185c663ff158b0a93d4db6e88a7f10b7` |
| router_fingerprint | `v2-rule-1.0` |
| contract_version | `v1` |
| prompt_versions | `research_action_v1=9afd7284…`、`research_answer_v1=4b587b94…` |
| external_policy_version | `v1-provisional` |
| snapshot_id（run 级锁定） | `snap-1370505b2feadbde95bc8bd2d47d5f5a`（300750 当前快照） |

**冻结核心规则**：表头/列级单位优先级与 canonical 归一化（Change 1）、结构化权威复合判定
（Change 2：Store 权威、ref 不自证、`exclude_claim_ids`）、跨期/趋势子 need（Change 3：compare
替代 single、缺期记 gap）。三者均 fail-closed（错答案不放过，正确答案降 PARTIAL
而非误判 FULL）。

## 11. 冻结前最后一次定点修复（四项通用安全问题）

> 上一轮 §10 收口后，冻结前最后一轮**只修通用安全/正确性问题**，**未**针对任何
> `case_id`、`300750` 或「宁德时代」写业务分支；全部改动落在 harness 通用层
> （`structured_provenance` 方向/引用/趋势判定 + repo 追踪范围），合成测试公司无关。
> 未重跑完整 41 问、未重跑 unseen_validation / frozen_final；未据结果继续调 Prompt。

| # | 修复项 | 落点 | 根因 → 修复 |
|---|---|---|---|
| 一 | 比较方向校验 | `harness/structured_provenance.py` | 原只校验 ref 有 `period_a/b` 与值，未校验 claim 的「上升/下降/持平」与 `ref.direction` 一致。新增 `_claim_direction`（词表识别 + 增长率/增速中性化）与 `_DIRECTION_WORDS`；方向矛盾 → `UNSUPPORTED/direction_mismatch`，`missing_period` → `PARTIAL/comparison_period_missing`，claim 未表达方向（仅两个正确值）→ 继续 value/period 校验。方向来自 Python 工具，LLM 不算 |
| 二 | 显式引用修复（替代静默回退） | `harness/structured_provenance.py` `_resolve_ref` + `harness/schema.py` + `harness/runtime.py` | 原 snapshot_id 转写错时静默按 period+code 回退。改为：先严格 match snapshot_id+period+item/formula；失败仅当 active_snapshot_id+period+code 唯一命中且通过 `SnapshotAuthority` 复合（company/scope/currency/purpose/status 全有效）才修复；修复后**改写最终 `CitationRef` 的 snapshot_id**、记录原值+修复值、落 `CITATION_REF_REPAIRED` trace（`reason=structured_authoritative_after_citation_repair`）。0 或 >1 候选 / 无法改写 / 无法记录 → `UNSUPPORTED/unresolvable_ref`；移除「pick active 或 first code match」回退 |
| 三 | 三期趋势不落 LLM | `harness/structured_provenance.py` `_trend_relation` | 删除「≥3 期 trend 跳过 Structured Provenance、回退 LLM」逻辑（§10.4 中「≥3 期回退 LLM 解读」即此条，本轮作废）。改由 Python Decimal 对同 item/formula/unit/scope 年报序列算方向：连续升→increased、连续降→decreased、全等→unchanged、有升有降→mixed；期数不足/缺值/单位或口径不一致→PARTIAL（绝不 FULL）。LLM 只解读 Python 已算好的方向；claim 趋势与 `_trend_relation` 相反 → `UNSUPPORTED/trend_direction_mismatch`，mixed 写成持续升/降 → UNSUPPORTED |
| 四 | repo 追踪范围修复 | `.gitignore` + `git rm --cached` | e6219c3 误提交 `.claude/settings.json` 与 `evaluation/results/actual_path_41/` 临时目录。**不重写历史、不删本地文件**：仅从追踪移除（`git rm --cached`），`.gitignore` 增加两行忽略。本地 `.claude/settings.json`（18932 B）与 `actual_path_41/` 全部临时目录均保留 |

### 11.1 验证结果

- 专项测试（全绿）：
  `test_harness_entailment` **48** / `test_harness_structured_provenance` **54**
  （§10.1 的 27 → 本轮 +27 覆盖方向/引用修复/趋势）/ `test_harness_structured_needs` **31** /
  `test_harness_snapshot_lock` **7** / `test_split_manifest` **13**。
- 完整 `run_evals`：**2889 passed / 0 failed / 0 skipped**（§10.3 的 2862 → +27）。
- 未重跑 FIN-PM1 等 3 题（新增测试已覆盖集成接线；不据结果调 Prompt）。

### 11.2 比较方向与三期趋势状态表

| 场景 | claim 表述 | ref.direction / Python trend | 判定 |
|---|---|---|---|
| 比较·下降一致 | 2024 1.61% → 2025 1.60%，「下降」 | `decreased` | SUPPORTED |
| 比较·方向矛盾 | 同上数值写「上升」 | `decreased` | UNSUPPORTED `direction_mismatch` |
| 比较·缺期 | 仅一个 ref `direction=missing_period` | `missing_period` | PARTIAL `comparison_period_missing` |
| 比较·无方向词 | 仅两个正确值，无升/降 | 有 direction | 继续 value/period 校验 |
| 趋势·连续升 | 2023<2024<2025「持续上升」 | `increased` | SUPPORTED |
| 趋势·连续降 | 「持续下降」 | `decreased` | SUPPORTED |
| 趋势·全等 | 「基本持平」 | `unchanged` | SUPPORTED |
| 趋势·有升有降 | mixed 写成「持续上升/下降」 | `mixed` | UNSUPPORTED `trend_direction_mismatch` |
| 趋势·期数不足/缺值/单位口径不一 | — | 无法可靠算方向 | PARTIAL（绝不 FULL） |

### 11.3 引用修复唯一性与审计证据

- 修复条件 = 严格 match 失败 **且** active+period+code 候选**恰好 1 个** **且** 候选通过
  `SnapshotAuthority` 复合（exists+is_current+validity=valid+非 report_blocked+非 quarantined）
  + company/scope/currency/purpose/status 全有效。
- 修复后最终 `CitationRef.snapshot_id` 改写为真实 id（原错误 id 不残留）；`state.citation_repairs`
  记 `{original_snapshot_id, repaired_snapshot_id, period, item_code/formula_id}`；runtime ANSWER
  分支落 `CITATION_REF_REPAIRED` trace。
- 0 候选 / >1 候选 / 候选非 current / 候选 validity 非 valid → `UNSUPPORTED/unresolvable_ref`。

### 11.4 git 追踪清理结果

- 从追踪移除：`.claude/settings.json`（1 文件）、`evaluation/results/actual_path_41/`（**28 个
  目录**：17 时间戳 + 1 reaccept + 5 smoke_rev2 + 5 smoke_revised，共 **168 文件**，含
  case_results.jsonl / inputs / metrics.json / report.md / run_manifest.json / trace_inventory.json）。
- `.gitignore` 新增：`.claude/settings.json`、`evaluation/results/actual_path_41/`。
- **本地文件全部保留**：`.claude/settings.json`（18932 B）仍存在；`actual_path_41/` 目录未删。
- 未改历史、未删本地文件、未提交 `.env` / API Key / 临时数据库 / 个人配置。

### 11.5 冻结判断

四项均为通用安全/正确性修复，无业务分支，专项 + 全量 eval 全绿，git 追踪已收敛。
**建议正式冻结，进入一次性 `unseen_validation`**（之后 `frozen_final` 最后一次全量）。
