# Phase 3 Batch B — 冒烟报告（修订版 v2）

> 状态：**STOP at human gate（新版 smoke 人工门）**。本文档为上一版
> [`PHASE3_BATCH_B_SMOKE_REPORT.md`](./PHASE3_BATCH_B_SMOKE_REPORT.md)（已标 REJECTED）的修订版，
> 报告 4 项定点修复后的 5 问真实冒烟 + 原始 41 问 DB 路径冒烟。**未**运行完整 41 问，**未**进入 Batch C / Phase 4。

---

## 0. 修复项 → 改动落点（对照 5 条驳回原因）

| # | 驳回点 | 修复 | 落点 commit |
|---|---|---|---|
| 1 | aspect 覆盖缺失（COMP-R1 没答收入占比判 FULL） | required-aspect 契约优先派生（三级：SECTION_CONTRACT→DATASET_MAPPING→TEXT_FALLBACK，非字符串切分） | contract-driven aspects |
| 2 | 引用存在但不支持结论（COMP-CR1 授信/担保口径错） | 批量 entailment（每问 1 次调用）+ 确定性数字/口径分层 | inspected evidence + entailment |
| 3 | 重复动作未去重 | runtime 幂等 key 去重 + `rejected_duplicate_actions` 审计 | duplicate action rejection |
| 4 | DB_LOOKUP 未覆盖 | 原始 41 问 DB 真实路径冒烟（`scripts/run_db_lookup_smoke.py`） | original 41 DB smoke |
| 5 | 报告未标 REJECTED / 无对比 | 旧报告标 REJECTED + 本文档新旧对比 | 本文档 |

---

## 1. 5 问真实冒烟：新旧逐题对比

公司 `300750`，章节 `company`，5 问均 P0。新 run_id 前缀 `smoke_revised_`。

| case_id | 旧状态 | **新状态** | 新 completion / stop_reason | 变化原因 |
|---|---|---|---|---|
| COMP-S1 成立时间 | FULL | **FAILED** | MODEL_OUTPUT_INVALID | 映射粒度 → 过度派生 6 方面（详见 §2） |
| COMP-S2 实际控制人 | UNRESOLVED | **PARTIAL** | COMPLETED_WITH_GAPS | 现能作答，缺口「持股比例与控制链条（缺具体数字）」 |
| COMP-R1 主营业务及收入占比 | FULL | **PARTIAL** | COMPLETED_WITH_GAPS | 现给出占比，但金额被 entailment 判为不支持（详见 §3） |
| COMP-MV1 总市值 | PARTIAL | **PARTIAL** | COMPLETED_WITH_GAPS | 诚实缺口（外部快照无正文）+ 否定断言 entailment 局限 |
| COMP-CR1 授信+担保 | FULL | **UNRESOLVED** | BUDGET_ITERATIONS | 现要求 3 方面，模型未收敛到 ANSWER（详见 §4） |

**汇总：FULL ×0，PARTIAL ×3，UNRESOLVED ×1，FAILED ×1**（旧版 FULL ×3 / PARTIAL ×1 / UNRESOLVED ×1）。

> 这**不是**回归，而是旧版过宽（把「有可回查引用」当 FULL）在四层门收紧后被诚实降级：每处降级都有具体的
> aspect 缺口 / 数字不支持 / 口径风险的证据，无一处是「凭空判死」。

---

## 2. COMP-S1 —— 暴露的新问题：映射粒度（非 harness bug）

**现象**：COMP-S1 问题「成立时间（工商注册日期）是什么时候？」，但 DATASET_MAPPING 命中
`company_identity_basic`（契约 6 方面：成立日期/办公地址/法定代表人/注册资本/实缴资本/经营范围），
派生 **6 个 required_aspects**。模型被要求覆盖 6 方面，遂作答全部 6 项；entailment 法官正确判
5 项 UNSUPPORTED（证据正文分别是合并范围变更/研发费用/子公司注册/控股股东等无关材料）→ FAILED。

**定位**：`evaluation/datasets/baseline_contract_mapping.jsonl` 里 COMP-S1 的 note 写「成立日期对应
基本信息字段」（= 只覆盖 1 个子字段），但 `coverage_role` 却标 `full`。该映射共 12 条 `partial`、
28 条 `full`；COMP-S1 这条 note 与 `coverage_role` 语义不一致。

**结论**：这是**既存映射数据粒度问题**（数据集问题粒度 < 契约问题粒度），被「契约优先派生」忠实暴露；
**不是** harness 派生逻辑的 bug（派生严格按计划三级优先级执行）。entailment 法官把模型被过度要求后
「编造」的 5 项答案全部拦下，反而证明了修订③④有效。

**处理**：按约束「不修改 gold / 不放松 FULL / 不加无关功能」，未改映射、未加字段级窄化逻辑。
**提请人工门裁决**：映射是否需要 field 级窄化（如 `coverage_role=partial` + 指定覆盖字段），还是维持现状。

---

## 3. COMP-R1 —— 修订①③的核心演示

**旧**：只答四大板块名称，未给任何收入占比，却判 FULL（驳回点 #1）。

**新**：answer 给出具体占比——动力电池 69.90%、储能 15.83%、电池材料及回收 8.16%、电池矿产 1.52%，
并给出「对应报告期为 2024 年度、合并口径」的 claim（c6）。aspect 覆盖（修订①）**生效**。

**但**：entailment 法官判 c2~c5 UNSUPPORTED，理由为金额在证据正文无数值等价——claim 写
「动力电池收入 25,301,917.96 万元」，证据正文为「25,304,133.7 万元」，数值不一致（模型把精确金额
算/记错了）。占比本身（69.90% vs 69.9%）等价，但金额不匹配。确定性 value-presence（修订④）与批量
entailment（修订③）**双双拦下**，降为 PARTIAL。

**结论**：这是「LLM 不算数字」约束的正面证据——模型不应自算金额，金额应从证据逐字引用；此处模型
输出了证据里不存在的精确金额，被正确标记为不支持。非 harness bug，属模型/prompt 层面的精确引用问题。

---

## 4. COMP-CR1 —— 驳回点 #2 专项分析

**旧（驳回）**：答「合计授信 40,000 万元、对外担保 0 万元」→ FULL。事实错误：把**局部授信当合计**、
把「为股东/实控人担保=0」当「**全部对外担保=0**」。

**修复机制（三层，均已实现）**：
1. **修订① 契约优先**：COMP-CR1 → `company_debt_credit` + `company_debt_guarantee`，派生 3 方面：
   `公司整体授信额度及使用情况` / `公司整体对外担保余额` / `担保范围口径与报告期`——把「整体/合计」与
   「担保范围口径」显式提为必须覆盖项。
2. **修订④ 语义口径分层**：确定性 `scope_risks` 对 `guarantee_scope` 维度，把 claim「全部对外担保」vs
   证据仅「为股东」判为 **high** 严重度（见 `harness.entailment --self-check`：`dimension=guarantee_scope,
   severity=high, claim_scope=全部对外担保, evidence_scope=为股东`）。该标记**不据关键字单独定论**，
   与批量 entailment 合并后才 block FULL。
3. **修订③ 批量 entailment**：对每条 fact claim 判 SUPPORTED/PARTIAL/UNSUPPORTED + 四项一致性。

**新实际结果**：UNRESOLVED / BUDGET_ITERATIONS（`search_evidence` + `inspect_evidence` 后未产出 ANSWER，
`answer=None`，3 方面全未覆盖）。即**旧版的错误答案没有再被产出**（安全），但也未走到「产出错误答案 →
被 scope/entailment 拦下」的干净演示。

**为什么没走到演示**：模型在「覆盖 3 方面」压力下未收敛到 ANSWER，触达 `max_rounds=3` 预算停止——这是
旧版 §5 已记录的 **Harness 循环不收敛** 后移事项（与旧 COMP-S2 同源），**非**本次修订引入。

**结论**：scope/entailment 对 COMP-CR1 错误口径的拦截能力，已在**单元测试** `test_harness_entailment`
（scope_risks「全部对外担保 vs 为股东 → high」、amounts_equivalent「40,000 万元 == 4 亿元」）确定性验证。
端到端演示受「模型不收敛」既有问题阻塞，记为后移。

---

## 5. DB_LOOKUP —— 修订②：合成工具单测 vs 原始 41 问（**分开报告**）

> 明确区分：**合成 DB 工具单测证明工具层可用** ≠ **原始 41 问 DB 路径真实可达**。二者分别报告。

### 5.1 合成 DB 工具单测（证明工具层，**不作原始路径替代**）

临时库构建 300750 Financial Snapshot，经 `ToolRegistry` 直调 5 个用例：

| 工具 | 结果 |
|---|---|
| `lookup_company_field` TOTAL_ASSETS | SUCCESS，974827540000.00 元 |
| `lookup_financial_metric` SOLV_CURRENT_RATIO | SUCCESS，1.60（CALCULATED_EXACT） |
| `compare_financial_periods` | SUCCESS，2024→2025 下降 |
| `lookup_financial_metric` EBITDA | **EMPTY / DB_FIELD_UNAVAILABLE**（预期负例） |
| `compare_financial_periods`（缺失期） | PARTIAL，missing_period 诚实标记 |

**disposition：success ×4，data_unavailable ×1** —— 工具层可用。

### 5.2 原始 41 问 DB 真实路径（不合成、不改题、不用 gold、不强制 Router）

逐题用 `question` 原文构造 `InformationNeed`，经真实 `Router`（context 指向临时库）路由：

- **路由分布**：DB_LOOKUP ×2，STANDARD_RAG ×13，DIRECT_EVIDENCE ×7，DEEP_RETRIEVAL ×7，
  EXTERNAL_RESEARCH ×4，UNDECIDED:ROUTER_FALLBACK_UNAVAILABLE ×8。
- **2 题真实落到 DB_LOOKUP，确定性执行**：
  - `FIN-RST1`（货币资金中**受限资金**占比）→ 解析到 `CASH_AND_EQUIVALENTS`，返回
    333512930000.00 元（**总**货币资金）。**语义口径警示**：问题问「受限资金」，字段给「货币资金」，
    二者不是同一口径——DB 路径**机械可达**但**语义不匹配**，须记录为口径缺口。
  - `FIN-FIX1`（固定资产中机器设备的年末净值）→ `FIXED_ASSETS` 返回 **EMPTY / DB_FIELD_UNAVAILABLE**。
- **8 题 resolver 可表达但被 DEEP/RAG/EXTERNAL 信号抢占**（FIN-GM1/FIN-P1/FIN-TR1…）；3 题财务 resolver
  无法表达（FIN-SLV1/FIN-AUD1/FIN-DEP1）。

**判定：`ORIGINAL_41_DB_PATH_REACHABLE`**（1 题返回结构化结果），但附 FIN-RST1 的语义口径 caveat：
DB 路径**不是**「原始 41 问普遍可达」，只是「财务题中极少数（2/41）机械可达，且其一语义不匹配」。

---

## 6. 确定性数字/口径分层（修订④）—— 锚点自检

`harness.entailment --self-check` 确定性层输出：

- `amounts_equivalent`：`40,000万元 == 4亿元` ✓，`0万元 == 0元` ✓，`12.5% == 12.5%` ✓。
- 非等价：`40,000万元 ≠ 40,000元` ✓，`4亿元 ≠ 4万元` ✓，`12.5% ≠ 12.5万元` ✓。
- `scope_risks`：`guarantee_scope`（全部对外担保 vs 为股东）→ **high**；`aggregate_level`（全部 vs 未见明确口径）→ low。

即：值存在（Decimal 归一化）与语义口径（marker 分组）分层判定，互不串味。

---

## 7. 逐 claim 字段（aspect source / entailment / 一致性，样例）

`_record_case` 现已落盘（读自 state，不影响判分）：`required_aspects`（含 source）、`aspects`
（逐 aspect answered/claim_ids/claim_texts/has_number）、`uncovered_aspects`、`entailment_verdicts`
（verdict+reason+四项一致性）、逐 claim `entailment` + `evidence_summary`、`rejected_duplicate_actions`、
`entailment_evaluator_failed`。

COMP-R1 样例（aspect_source=`DATASET_MAPPING`，3 方面）：
- c1「四大业务板块」→ SUPPORTED（scope/period/unit/subject 全 consistent）
- c2~c5 各业务金额/占比 → UNSUPPORTED（金额在证据无等价）
- c6「报告期 2024、合并口径」→ SUPPORTED

COMP-S2 样例：c1 控股股东 SUPPORTED，c2 实际控制人 SUPPORTED，c3 控制链条 PARTIAL（证据未直接给出
「曾毓群通过厦门瑞庭间接持股」的完整链条）；方面缺口「持股比例与控制链条（缺具体数字）」。

---

## 8. 冒烟暴露的 4 个新发现（提请人工门，均非 harness 派生 bug）

1. **映射粒度错配**（§2）：COMP-S1 等 `coverage_role` 与 note 不一致，细粒度问题过度派生方面。
2. **模型精确金额不可靠**（§3）：COMP-R1 金额被自算/记错，被正确拦截——印证「LLM 不算数字」约束，
   但暴露模型在长数字上仍需 prompt 强化「逐字引用，禁自算」。
3. **模型不收敛到 ANSWER**（§4）：COMP-CR1 在 3 方面压力下预算耗尽——旧 §5 已记录的后移事项。
4. **否定断言 entailment 局限**（COMP-MV1）：claim「材料未提供 X」无正文可验证，被判 UNSUPPORTED；
   这是 entailment 对否定/缺失断言的结构性局限，需单独处理（如「未收录」类 claim 走不同判定路径）。

---

## 9. 验证结果

| 项 | 结果 |
|---|---|
| `harness.aspects --self-check` | ✓（COMP-R1/COMP-CR1 锚点、TEXT_FALLBACK 保 1 不猜） |
| `harness.entailment --self-check` | ✓（40,000万元==4亿元、scope_risks guarantee_scope high） |
| `test_harness_aspects` | 11 passed / 0 failed |
| `test_harness_entailment` | 29 passed / 0 failed |
| `test_harness_state` | 28 passed / 0 failed |
| `test_harness_runtime` | 24 passed / 0 failed |
| `test_harness_schema` | 25 passed / 0 failed |
| `test_actual_path_41` | 32 passed / 0 failed（新增 6 断言：report 新字段） |
| `scripts.run_db_lookup_smoke` | ✓ 原始 41 问 DB 路径 + 合成单测，临时库清理 |
| 完整 `run_evals` | 见 §10（后台运行中） |

---

## 10. 人工门

按计划**停止**。**未**运行完整 41 问、**未**进入 Batch C / Phase 4。

待人工检查：
1. §2 映射粒度（COMP-S1）是否需要 field 级窄化；
2. §4 COMP-CR1 的 scope/entailment 拦截已单测验证、端到端受「模型不收敛」阻塞是否接受；
3. §8 的 4 个新发现是否纳入后续批次。
