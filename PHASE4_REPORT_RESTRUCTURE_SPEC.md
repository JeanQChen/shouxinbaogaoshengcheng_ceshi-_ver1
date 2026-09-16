# Phase 4 发布态报告逐章写作与内容组织规格

> 版本：draft-1（待人工修改确认，未编码）
> 状态：**SUPERSEDED / 仅保留为历史诊断，不得作为当前编码或验收依据**
> 取代原因（2026-09-12）：本文假设上游 Claim 已经完整，只处理发布态压缩和编排，并提出 5,000～8,000 字符等未经业务确认的限制；真实样本证明主要缺陷位于 P3→P4 的材料归拢、aspect 覆盖和内容交付接口。现行依据改为 `DESIGN_V2.md` v0.6 与 `PHASE3_PHASE4_TOPIC_RESEARCH_REFACTOR_TASK.md` v1.1。本文中的篇幅上限、禁止补研究、case-specific 白名单及“只读投影即可完成重构”等内容全部失效。
> **全文执行授权已失效：** 下文即使使用“必须/编码/验收/唯一依据”等措辞，也只代表当时草案，不得单独执行。有效写作要求须通过新版 Contract + `SectionWritingSpec` / `ReportPresentationProfile` 进入现行主链。
> 上游依据：`PHASE4_DEVELOPMENT_TASK.md`、`templates/contracts/standard_v2.yaml`、`contracts/sc_decisions.yaml`、
> `contracts/review/required_aspects_review.md`、`FORMULA_REVIEW.md`（Financial V2 Formula Registry v1.0）、
> `sections/publishable_report.py`、真实发布态 `evaluation/results/publication_run_20260911T_jsonfix/`（report.md / publication.json / 三章 section_result.json）

---

## 〇、规格目的与适用范围

本文档定义「Phase 4 发布态报告」的**逐章写作规格与内容组织规则**，作为后续重构 `sections/publishable_report.py`
中 `render_report_markdown` / `deterministic_editor` / 章节组织逻辑的**唯一依据**。

本文档不改变以下任何既有事实：
- 上游 Contract（`standard_v2.yaml`）、`sc_decisions.yaml`、Formula Registry 的**问题定义与阻断语义**不变；
- 发布态投影仍**只读**、仍只由「合格 Claim」组装、仍不重跑 Worker/Router/Retriever/Web Search/Financial Pipeline；
- `publication_id` 内容寻址与原子 publish 机制不变。

本文档改变的是**「如何把已通过权威校验的 Claim 组织成可读、可截图、可进入 Phase 5 的正文」**这一层。

**占位符约定**：所有「示例段落骨架」一律只使用 `[公司名称]`、`[报告期]`、`[业务板块A]`、`[营业收入]`、`[引用1]`、
`[行业名称]`、`[可比公司1]` 等占位符；**严禁把任何真实公司（含宁德时代/300750）的事实、数字、实体名写入通用骨架**。
诊断章节（一）中引用真实 claim_id/topic_id/question_id 文本，是为了**如实描述当前报告**，不是通用模板的一部分。

---

## 一、诊断：当前发布态报告逐章问题清单

> 诊断对象：`evaluation/results/publication_run_20260911T_jsonfix/report.md`（301 行，约 42KB）。
> 诊断维度固定为 8 项（下称 **D1~D8**），逐章套用：

| 维度 | 含义 |
|---|---|
| **D1 章节发布状态** | `derive_publication_status` 输出与显著标注是否如实 |
| **D2 Claim 数量与分布** | adopted / excluded 计数、按 topic 分布是否失衡 |
| **D3 Unresolved 与阻断** | 未解决项数量、阻断级别（JOB/SECTION/REPORT_BLOCKED）、impact_scope |
| **D4 篇幅与压缩压力** | 字数是否超出 5,000~8,000 目标，冗余来源 |
| **D5 必需方面覆盖** | 每个 question 的 `required_aspects` 是否被正文字面/语义覆盖 |
| **D6 引用可回查性** | Evidence/Structured/External 三类引用的可读性与可回查 |
| **D7 事实/研判混杂** | fact 夹带研判措辞、inference 无 derived 链等 |
| **D8 跨章节冲突** | 同 (item_code/formula_id, period) 多章不同 snapshot 的精确冲突 |

### 1.1 标题与总览（当前缺失「封面/口径 + 核心摘要」）

- **D1**：当前报告只有单行标题 `# 授信研究报告（发布态）`，**没有**封面（报告编号、主体、报告期、口径声明、数据范围、用途/免责）、**没有**核心研究摘要。读者无法在一屏内获知「这是谁、哪期、结论是什么」。
- **D2**：n/a（无正文）。
- **D3**：三章均为 `BLOCKED_FOR_PUBLICATION`（见下），但标题层未做任何「草稿/待人工确认」的总览性提示，风险提示散落在审计附录。
- **D4**：无此层开销。
- **D5~D8**：n/a。

### 1.2 第1章　公司信用研究（当前 10 个小节，82 claim = 77 adopted + 5 excluded，19 unresolved）

- **D1**：`status=WAITING_HUMAN`，`eval.decision=BLOCKED`，`rules_passed=False`。发布态投影应显示
  `BLOCKED_FOR_PUBLICATION` 并显著标注「草稿／待人工确认」，但当前正文没有在章首做统一提示，只把阻断藏进审计附录。
- **D2**：82 claim 分布极不均衡——`company_identity` 13 条、`company_business` 13 条、`company_governance` 16 条、
  `company_profit_quality` 13 条、`company_subsidiaries` 11 条、`company_competitiveness` 7 条、`company_equity_incentive` 5 条，
  但 `company_debt` 仅 4 条、`company_legal_risks` 仅 2 条。**债务/外部融资与法律风险两处被大幅稀释**，而治理/身份类信息量冗余。
- **D3**：19 unresolved 中 **4 条阻断**：
  - `company_subject_match` → `WAITING_HUMAN`（`CONSECUTIVE_NO_NEW_EVIDENCE`）→ `JOB_BLOCKED`（impact `subject`）；
  - `company_control_chain` → `WAITING_HUMAN`（`MODEL_OUTPUT_INVALID`）→ `REPORT_BLOCKED`（impact `subject`）；
  - `company_business_main` → `NOT_FOUND_AFTER_SEARCH`（`CONSECUTIVE_NO_NEW_EVIDENCE`）→ `SECTION_BLOCKED`（impact `subject`）；
  - `company_debt_credit` → `NOT_FOUND_AFTER_SEARCH`（`COMPLETED_WITH_GAPS`）→ `REPORT_BLOCKED`（impact `solvency`）；
  - `company_debt_guarantee` → `NOT_FOUND_AFTER_SEARCH`（`CONSECUTIVE_NO_NEW_EVIDENCE`）→ `REPORT_BLOCKED`（impact `solvency`）。
  - 另有 `company_litigation`（`FATAL_TOOL_ERROR`）、`company_investment`（`no_valid_claim`）、`company_related_transactions`、
    `company_rd_capacity`、`company_pledge_sentiment`（舆情缺口）等 14 条非阻断缺口。
- **D4**：本章是当前 42KB 的主要膨胀源（10 个小节、正文 ≈ 190 行）。目标压缩后应 ≤ 约 2,500~3,000 中文字符。
- **D5**：32 条 issue 中 **26 条 `aspect_uncovered`**（成立日期、办公地址、实缴资本、重大改革、法定代表人变更、上市募资、
  集团结构、重要关联方、采购生产销售模式、成本竞争能力、客户集中度、依法未披露标注、治理内控、管理层稳定性、主要管理人员履历、
  股权质押、舆情、整体授信额度及使用、非主营损益构成、股权激励计划及进展、无计划不适用、核心信用优势、核心风险、偿债影响与未解决问题…）。
  覆盖严重不足，且多数属于「语义已覆盖但未字面出现」，需靠 LLM Evaluator 复核，而非机械判定。
- **D6**：`company_debt_credit` 中 `claim_8a0c19b1e329f1829dd7110f` 为 inference 且正文明确写「材料未完整披露公司整体授信总额度及已用授信规模」——这是**正确的诚实披露**，但当前正文没有把它与「授信研究结论缺失」的置信度影响显式关联。
- **D7**：`fact_hedged` 命中 1 条（`company_debt_credit` 中 `fact` claim 含研判措辞「预计」，应降为 inference）；
  `absence_contradicts_gap` 命中 4 条（`company_identity_history` / `company_governance` / `company_credit_summary` ×2
  断言「不存在/未发生」但问题状态为 `NOT_FOUND_AFTER_SEARCH`，即「缺证据 ≠ 事实不存在」）。
- **D8**：本章无 `structured` 直接引用（除 `company_profit_quality` 的 `claim_cb9df5d1a0bc68b9095907ed` 引 structured），
  跨章节财务冲突主要出现在第2/3章之间（见 1.4）。

### 1.3 第2章　财务分析（当前 10 个小节，15 claim，6 unresolved）

- **D1**：`status=COMPLETED_WITH_GAPS`，`eval.decision=PASS_WITH_GAPS`，`rules_passed=True`。三章中状态最完整。
- **D2**：15 claim 分布均衡：`fin_solvency` 2、`fin_profitability` 2、`fin_operating` 1、`fin_cashflow` 2、`fin_growth` 1、
  `fin_balance_structure` 2、`fin_source_scope` 2（含审计意见）、`fin_consistency` 1、`fin_asset_quality` 1、`fin_risk_summary` 1。
  全部 `structured` 引用（快照），仅审计意见 1 条 `evidence`。
- **D3**：6 unresolved，**无阻断**，全是 `MISSING_REQUIRED_ITEM` / `NOT_APPLICABLE`：
  - EBITDA（`fin_solvency`）期间 2025-12-31、2026-03-31 未取得 ×2；
  - 自由现金流（`fin_cashflow`）期间 2025-12-31、2026-03-31 未取得 ×2；
  - 净利增长率 / 营收增长率（`fin_growth`）2026-03-31 `NOT_APPLICABLE` ×2。
  - 结论：**EBITDA、FCF、季度增长率三类指标在当前快照不可计算**，正文应显式标注「未取得」，不得隐去。
- **D4**：15 claim 被切成 10 个小节，**每节平均 1.5 条 claim**，小节过度碎片化；应收敛为 5~6 个小节。
- **D5**：30 条 issue 中 26 条 `aspect_uncovered`（资产负债表齐备、利润表齐备、现金流量表齐备、审计意见、近三年趋势、三表勾稽、
  多来源冲突、资产负债结构、重大科目变化、15%以上科目强制分析、短/长期偿债、净资产、刚性债务、ROE、ROA、利润质量、现金流结构、
  经营现金流覆盖债务、异常变动及原因、杜邦分析、非主营损益与减值、受限资产、商誉与开发支出、财务风险结论、对授信方案影响），
  另有 4 条 `semantic_aspect_uncovered` 明确点名「近三年趋势覆盖缺失、杜邦分析缺失、异常变动未解释、未覆盖非主营/减值/受限资产、
  财务风险结论未关联授信方案」——**这些是真实内容缺口，不是字面覆盖误报**。
- **D6**：`structured` 引用均落到 frozen snapshot，可回查性最好；但正文未展示「报告期 / 合并范围 / 金额单位」以外的口径细节。
- **D7**：`claim_75b54b2cd5ae3a9da37c41da` 混有「报告期覆盖最新年报及最新季度」的事实与「最新完整年度三张主表齐备」的结论，属可接受。
- **D8**：**关键冲突源**——本章「最新季度」快照（2026-03-31）的 `fin_growth` 类指标与第3章 `industry_supply_demand` 的
  `claim_8b95e31f22fecd8948e239a2`「动力电池系统收入 2024 下降后 2025 回升」、`claim_6e53b40a197a78cb9266ecda`「2024 营收同比跌 9.7%」
  存在**期间口径不一致**（财务按最新季度快照、行业按 2023/2024/2025 年度数），需跨章节标注。

### 1.4 第3章　行业研究（当前仅 3 个小节，12 claim，8 unresolved）

- **D1**：`status=COMPLETED_WITH_GAPS`，`eval.decision=REWORK`，`rules_passed=False`（`final_rules_passed=True`）。行业章是三章中**内容最薄弱**的。
- **D2**：12 claim 集中在 `industry_definition`（5 条）、`industry_supply_demand`（6 条），`industry_comparables` 仅 1 条，
  且该条（`claim_59c2e14cde75ee2a12ec1d51`）内容是「存在多家合营或联营企业」——**把联营企业当可比公司，属方向性错误**（见 §七）。
- **D3**：8 unresolved 中 5 条 `MODEL_OUTPUT_INVALID` / `FATAL_TOOL_ERROR` / `CONSECUTIVE_NO_NEW_EVIDENCE` 研究失败：
  `industry_scale_cycle`（`MODEL_OUTPUT_INVALID`）、`industry_policy`（`FATAL_TOOL_ERROR`）、`industry_competition`、
  `industry_position`（`CONSECUTIVE_NO_NEW_EVIDENCE`）、`industry_monitoring`（`MODEL_OUTPUT_INVALID`）、
  `industry_comparables`（`COMPLETED_WITH_GAPS`，缺可比公司名单与相对比较数据）、
  `industry_risk_transmission`（`no_valid_claim`，impact `solvency`，缺风险传导结论）、`industry_definition`（`COMPLETED_WITH_GAPS`）。
- **D4**：12 claim 拆 3 小节，表面看紧凑，实则「供需、价格、成本、政策、竞争、地位、可比、风险传导」多块内容被挤进
  `industry_supply_demand` 一个 topic，粒度失真。
- **D5**：11 条 issue 全为 `aspect_uncovered`（行业定义与边界、公司细分领域、供需关系、价格与成本驱动、对经营的影响、可比公司选择、
  相对比较、对收入的传导、对成本与资本开支的传导、对现金流与偿债能力的传导），另有 `fact_hedged` 1 条（`industry_supply_demand`
  中 fact claim 含「预计」）。
- **D6**：行业章 12 claim 全为 `evidence` 引用，**无一条 structured/external 分级来源**，违反 `sc_decisions.yaml` SC-03
  「行业来源 A/B/C/D 分级」的意图。
- **D7**：`claim_8b95e31f22fecd8948e239a2` 是 inference，其结论「2025 回升」依赖 `industry_supply_demand` 的年度营收/成本证据，
  derived 链需在审计视图可回查。
- **D8**：见 1.3 D8，`industry_supply_demand` 的收入类数字与财务章期间口径冲突，是当前报告**唯一的跨章节精确冲突风险点**。

### 1.5 审计附录（当前 2 个小节：跨章节一致性、生成说明）

- **D1**：审计附录与正文**未分离**——claim_id、snapshot_id、错误码、返工指令等审计信息没有集中收纳，正文反而出现
  「另有 N 项审计明细」这类占位式描述，读者无法区分「正文结论」与「机器审计痕迹」。
- **D2~D8**：审计附录应承担 D3/D6/D8 的全部审计明细，当前混在正文，是重构的第一动因。

---

## 二、Phase 4 报告目标（发布态定位）

**一句话定位**：Phase 4 产出的是**「授信研究基础报告」**——它把已通过权威校验的事实与计算结论，组织成一份
**客户经理可读、可截图、可直接进入 Phase 5（授信方案）的研究底稿**。

### 2.1 必须包含

1. 主体身份、股权控制、主营业务、治理、债务/外部融资、重大事项/或有风险、公司信用小结（§五 7 个子结构）；
2. 财务三表齐备性、审计意见、资产负债结构、偿债能力、盈利/利润质量、营运、现金流、增长、资产质量、财务风险结论（§六）；
3. 行业定义、规模/周期、供需、价格成本、竞争、政策、公司地位、可比公司、结论有效期与监测、风险传导（§七）；
4. 核心风险与资料限制的诚实汇总；
5. 可回查的引用来源表。

### 2.2 绝不包含（硬边界）

- ❌ **授信额度结论**（具体建议额度、币种、品种、期限、利率）；
- ❌ **评级结论**（我方给定主体/债项评级；外部评级机构已给评级属可陈述事实，但不得据此给授信结论）；
- ❌ **担保/增信方案**（抵押物、保证人、增信结构设计）；
- ❌ **审批结论**（通过/否决/附条件、授信审批意见）。

> 依据：`shouxin_cj_zongjie.md` §6 模板中「四、综合授信建议」属 Phase 5，不在 Phase 4 范围内；`sc_decisions.yaml` SC-04 综合章节同此。

### 2.3 篇幅目标

| 项 | 目标 |
|---|---|
| 正文（第1~6 章，不含审计附录） | **5,000 ~ 8,000 中文字符**（中文字符计数，不含 Markdown 标记、表格分隔线、脚注标记） |
| 审计附录 | 不设上限，默认折叠，按需展开 |
| 当前基线 | 42KB（301 行），需压缩至约 1/4 ~ 1/3 |

### 2.4 写作语气与口径（沿用既有约定）

- 客户经理口吻；用「公司」不用「该公司」；数字保持精度，不四舍五入到失真（`shouxin_cj_zongjie.md` §7.2）。
- 表达顺序：**事实与数据 → 分析判断 → 对信用/偿债的影响 → 未解决事项**（`PHASE4_DEVELOPMENT_TASK.md` §12.1）。

---

## 三、目标报告整体结构（8 个区块）

```
1. 封面与口径声明
2. 核心研究摘要（一页内）
3. 第一章　公司信用研究（§五 7 子结构）
4. 第二章　财务分析（§六）
5. 第三章　行业研究（§七）
6. 核心风险与资料限制
7. 引用来源
8. 审计附录（默认折叠）
```

| # | 区块 | 内容要点 | 现状 |
|---|---|---|---|
| 1 | 封面与口径 | 报告编号、`[公司名称]`、`[股票代码]`、`[报告期]`、口径声明（合并/母公司、币种、金额单位、数据来源范围、发布态 schema 版本）、用途与免责 | **缺失** |
| 2 | 核心研究摘要 | 主体结论一句、三章核心结论各 2~3 句、关键阻断风险、资料限制置信度 | **缺失** |
| 3 | 第一章 公司信用研究 | §五 7 子结构 | 10 小节 → 收敛 7 子结构 |
| 4 | 第二章 财务分析 | §六 决策 | 10 小节 → 收敛 5~6 小节 |
| 5 | 第三章 行业研究 | §七 细化 | 3 小节 → 收敛 10 子节 |
| 6 | 核心风险与资料限制 | 汇总所有 `REPORT_BLOCKED`/`SECTION_BLOCKED`/`JOB_BLOCKED` 缺口 + 对结论置信度的影响 | 散落正文/附录 |
| 7 | 引用来源 | 脚注 `[1][2]…` 完整列表（Evidence 文档名+页 / Structured 快照+科目+公式 / External 来源+日期） | **缺失** |
| 8 | 审计附录 | claim_id、snapshot_id、hash、错误码、返工指令、unresolved 原文、evaluation 全量 | 混入正文 |

---

## 四、逐章写作规格（每章 12 项）

> 以下 12 项为**通用规格骨架**，第3/4/5 章各自填充（见 §五/§六/§七）。每章的 12 项是：

1. **目的** — 本章回答什么问题（不超过 2 句）。
2. **结论** — 章首必须给出一句「本章结论」句，格式固定：`本章结论：……`。
3. **必需字段** — 缺了即本章不合格的字段（对应 Contract 的 blocking / P0 key_questions）。
4. **可选字段** — 有则写、无则明确标注「未取得/不适用」的字段（P1/P2）。
5. **Evidence 类型** — 本章允许出现哪几类引用（evidence/structured/external）及各自最低要求。
6. **推荐表格** — 本章推荐使用哪几张表格（表格是压缩篇幅的主手段）。
7. **分析逻辑** — 从 Claim 到段落的组织顺序（事实→判断→影响→缺口）。
8. **阻断项** — 哪些 unresolved 会使本章/整份报告进入 `BLOCKED_FOR_PUBLICATION`。
9. **资料限制项** — 哪些缺口只降置信度、不阻断，且必须诚实写入正文。
10. **不值得检索项** — 明确「不要为此再去检索/重跑」的项（省预算、防返工）。
11. **篇幅** — 本章目标字符数上限。
12. **示例段落骨架** — 只含占位符的段落模板。

---

## 五、第一章　公司信用研究细化（7 个子结构）

> 对应 Contract `company` 21 questions / 14 topics。目标把当前 10 个小节收敛为 7 个子结构，字数 ≤ 2,800。

### 5.1 子结构清单

| 子结构 | 来源 topics | 对应 question_ids | 目标篇幅 |
|---|---|---|---|
| ① 企业概况表格 | company_identity | company_identity_basic | 表格 1 张 + 1 句 |
| ② 股权与控制关系 | company_identity / company_subsidiaries(部分) | company_identity_history、company_control_chain、company_subsidiaries | ≤ 400 |
| ③ 主营业务 BusinessSegmentFactPack | company_business | company_business_model、company_business_main、company_customer_concentration、company_supplier_concentration | 表格 1 张 + ≤ 500 |
| ④ 治理与管理 | company_governance、company_equity_incentive、company_profit_quality(部分) | company_governance、company_equity_incentive | ≤ 400 |
| ⑤ 重大事项与或有风险 | company_legal_risks、company_profit_quality、company_investment、company_related_transactions | company_litigation、company_pledge_sentiment、company_investment、company_related_transactions | ≤ 400 |
| ⑥ 债务与外部融资 | company_debt | company_debt_credit、company_debt_guarantee | ≤ 350 |
| ⑦ 公司信用小结 | company_credit_summary、company_competitiveness、company_rd_capacity | company_credit_summary、company_competitiveness、company_rd_capacity | ≤ 350 |

### 5.2 ① 企业概况表格（必需字段，对应 company_identity_basic）

- **必需字段**：`[公司名称]`、`[成立日期]`、`[办公地址]`、`[法定代表人]`、`[注册资本]`、`[实缴资本]`、`[经营范围]`、`[股票代码]`。
- **推荐表格**（唯一一张「身份表」）：

  | 项目 | 内容 |
  |---|---|
  | 公司名称 | [公司名称] |
  | 股票代码 / 上市板块 | [股票代码] |
  | 成立日期 | [成立日期][引用1] |
  | 办公地址 | [办公地址][引用1] |
  | 法定代表人 | [法定代表人][引用1] |
  | 注册资本 | [注册资本][引用1] |
  | 实缴资本 | [实缴资本][引用1] |
  | 经营范围 | [经营范围][引用1] |

- **阻断项**：`company_subject_match`（`JOB_BLOCKED`）未解决时，本表主体信息视为**待人工确认**，表头加「待确认」角标。
- **示例骨架**：
  > `[公司名称]`（`[股票代码]`）于 `[成立日期]` 设立，注册地为 `[办公地址]`，法定代表人为 `[法定代表人]`，
  > 注册资本 `[注册资本]`、实缴资本 `[实缴资本]`[引用1]。主营业务为 `[经营范围概述]`[引用1]。

### 5.3 ② 股权与控制关系

- **必需字段**：控股股东、实际控制人及控制链条（`company_control_chain` 是 `REPORT_BLOCKED`，无法确认时诚实写「控制关系待人工确认」）。
- **可选字段**：历史沿革（重大改革、股权变更、法定代表人变更、上市及募资事项，属 `company_identity_history`）。
- **推荐表格**：控制关系表（控股股东 / 实际控制人 / 持股比例 / 是否一致行动）。
- **阻断项**：`company_control_chain`（`REPORT_BLOCKED`）；`company_identity_history` 的 `absence_contradicts_gap` 命中——不得再写「不存在重大变更」。
- **资料限制项**：上市时间/募集资金金额缺失 → 写「上市及重大募资事项未检索到具体数字」。
- **示例骨架**：
  > 本章结论：`[公司名称]` 的控制关系「可确认/待人工确认」。控股股东为 `[控股股东]`，实际控制人为 `[实际控制人]`[引用1]。
  > 报告期控股股东「未发生/发生」变更[引用1]。重大股权变更与上市募资事项「已披露/未检索到具体数字」[引用2]。

### 5.4 ③ 主营业务 BusinessSegmentFactPack

- **必需字段**：主营业务构成、收入/成本/毛利构成、产业链位置（`company_business_main` 是 `SECTION_BLOCKED`）。
- **推荐表格**（核心表，直接从收入类 claim 压成一行行）：

  | 业务板块 | [报告期]营业收入 | 占比 |
  |---|---|---|
  | [业务板块A] | [营业收入][引用1] | [占比] |
  | [业务板块B] | [营业收入][引用1] | [占比] |
  | … | … | … |
  | 合计 | [营业收入合计] | 100% |

- **阻断项**：`company_business_main`（`SECTION_BLOCKED`）。
- **资料限制项**：客户/供应商名称「依法未披露」→ 写「客户名称依法未披露」，**不视为缺失**（`company_customer_concentration`）。
- **不值得检索项**：不要为「境外客户名单」「前五大供应商名单」再做深度检索——集中度数字已足够支撑结论。
- **示例骨架**：
  > 本章结论：`[公司名称]` 主营 `[业务板块A]` 与 `[业务板块B]`，报告期营业收入合计 `[营业收入合计]`[引用1]。
  > 其中 `[业务板块A]` 收入 `[营业收入]`，占比 `[占比]`[引用1]；前五大客户销售占比 `[客户集中度]`[引用2]、
  > 前五大供应商采购占比 `[供应商集中度]`[引用2]，集中度「较高/较低」。

### 5.5 ④ 治理与管理

- **必需字段**：治理结构、内控（`company_governance`）。
- **可选字段**：主要管理人员履历、管理层稳定性、股权激励计划及进展（`company_equity_incentive`，无计划时写「不适用」）。
- **阻断项**：无硬阻断；`absence_contradicts_gap` 命中时不得写「不存在内控问题」。
- **资料限制项**：主要管理人员履历覆盖不全（当前仅覆盖 2 名董事）→ 写「其余高管履历材料不足」。
- **不值得检索项**：不要为每名高管完整履历做独立检索；一表两列（姓名/职务/任期）即可。

### 5.6 ⑤ 重大事项与或有风险

- **必需字段**：重大诉讼/处罚/失信/退市风险（`company_litigation`，`FATAL_TOOL_ERROR` 时**必须**记录检索范围与截止日期）、
  股权质押与舆情（`company_pledge_sentiment`）。
- **可选字段**：关联交易合规性与占比、重大投资/收并购/资产出售/定增（`company_investment` 当前 `no_valid_claim`）。
- **阻断项**：无硬阻断（这些问题 impact 多为空）。
- **资料限制项**：舆情无证据 → 写「舆情方面未取得可回查材料」；投资/定增无有效 claim → 写「未形成有效可引用结论」。
- **不值得检索项**：`company_investment`、`company_related_transactions` 本轮**不得**再去定向重跑（预算已收敛，见 §十一批次 E）。

### 5.7 ⑥ 债务与外部融资

- **必需字段**：发债、金融机构借款、对外担保（结构化表格，`company_debt_guarantee`）、整体授信额度及已用规模（`company_debt_credit`）。
- **推荐表格**：

  | 项目 | [报告期] |
  |---|---|
  | 尚未使用的银行借款额度 | [额度][引用1] |
  | 整体授信总额度 | 未披露 |
  | 已用授信规模 | 未披露 |
  | 对外担保 | [担保情况][引用2] |

- **阻断项**：`company_debt_credit`、`company_debt_guarantee` 均为 `REPORT_BLOCKED`（impact `solvency`）——授信额度类数据缺失时，
  **不得生成偿债结论**，本章结论必须落为「外部融资与授信使用情况待人工补充」。
- **资料限制项**：授信总额度/已用规模未披露 → 显式写「无法测算授信使用率」（对应 `claim_8a0c19b1e329f1829dd7110f` 的正确做法）。
- **不值得检索项**：授信额度属内部信息，检索拿不到，不要重跑。

### 5.8 ⑦ 公司信用小结

- **必需字段**：核心信用优势、核心风险、偿债影响与未解决问题（`company_credit_summary`）。
- **组织顺序**：先优势（1~2 条）、再风险（1~2 条）、再「偿债影响与未解决事项」。
- **阻断项**：`company_credit_summary` 的 `absence_contradicts_gap` 命中——不得写「不存在有息债务逾期」除非有证据，
  当前 `claim_9a5870be8c4dbf15e340baf0` 是 inference，措辞应为「报告期内无证据显示存在有息债务逾期」而非断言不存在。
- **示例骨架**：
  > 本章结论：`[公司名称]` 核心信用优势为 `[优势A]`[引用1]；主要风险为 `[风险A]`[引用2]。
  > 对偿债的影响：`[影响]`。未解决事项：`[未解决缺口]`。

---

## 六、第二章　财务分析默认业务决定（7 项，直接写入规格）

> 依据 `FORMULA_REVIEW.md`（25 个已确认指标）+ `financial_v2.formulas.FormulaDefinition.period_requirement` +
> `sc_decisions.yaml` SC-02。以下 7 项**不再作为待议项**，直接作为重构默认值。

1. **基准口径**：以「最新完整年度三张主表（合并口径）+ 最新季度快照」为唯一财务基准；三表齐备 + 审计意见（`fin_audit_opinion`）是 `key_financial` 阻断项（SC-02），缺失即 `REPORT_BLOCKED`。当前 `fin_audit_opinion` 已取得「标准无保留意见（致同）」，满足最低边界。

2. **LLM 不算数**：所有指标由 `financial.metrics` / Financial V2 Formula Registry 计算，LLM 只写解读。正文出现的任何比率/增长率必须能在 frozen snapshot 或公式登记表回查。

3. **期间可比性按公式语义（`p4-period-comparability-v1`）**：
   - `end` → 时点指标，年度/季度**可比**；
   - `flow` + 金额单位 → 年累计流量，年度/季度**不可比**（只并排展示 + 标「口径不可直接比较」）；
   - `flow` + 比率/百分比 → 同口径流量比率，**可比**；
   - `flow/end`、`flow/avg` → 流量/存量混合，**不可比**；
   - `yoy_*` → 年度增长率，季报 `NOT_APPLICABLE`（**不展示季度增长率**）。

4. **季度 yoy 不展示**：`fin_growth` 的「营收增长率 / 净利增长率」2026-03-31 为 `NOT_APPLICABLE`，正文**不得**出现「最新季度同比」数字，只保留年度增长率。

5. **EBITDA / FCF / 有息负债 / 利息费用**：定义为 `BUSINESS_CONFIRMED`（Formula Registry），但 EBITDA、FCF 依赖的输入项在当前快照**未取得**（`MISSING_REQUIRED_ITEM`），正文必须标注「未取得」，**不得**用其他科目近似代算后冒充正式指标。

6. **异常变动强制分析**：科目变动 ≥15% 须强制解释原因（Contract `fin_balance_structure` 的「15%以上科目强制分析」方面）；当前 `fin_growth` 显示净资产增长率 35.68%、净利增长率 42.18%，须有原因句。

7. **资产质量与风险结论为必需**：杜邦分析、非主营损益/减值、受限资产、商誉/开发支出、财务风险结论及对授信方案的影响 6 个方面为必需方面，**不得整块省略**；当前语义复核已点名缺「杜邦分析」「异常变动解释」「受限资产」「授信方案影响」，重构时必须补齐或诚实标注「本快照未覆盖」。

### 6.1 财务章目标结构（10 小节 → 5 小节）

| 收敛后小节 | 合并来源 topics | 目标篇幅 |
|---|---|---|
| 数据来源、口径、审计意见 | fin_source_scope | ≤ 200 |
| 资产负债结构与重大科目变化 | fin_balance_structure、fin_consistency、fin_asset_quality | ≤ 500 |
| 偿债能力 | fin_solvency | ≤ 400 |
| 盈利、营运与增长 | fin_profitability、fin_operating、fin_growth | ≤ 500 |
| 现金流与财务风险结论 | fin_cashflow、fin_risk_summary | ≤ 400 |

### 6.2 财务章示例骨架

> 本章结论：报告期末 `[公司名称]` 资产负债率 `[资产负债率]`[引用1]，货币资金 `[货币资金]`、经营现金流净额 `[经营现金流]`[引用1]，
> 短期偿债「有/无」压力；净利润 `[净利润]`、净利率 `[净利率]`[引用1]。EBITDA 与自由现金流因输入项缺失**未取得**[引用2]。

---

## 七、第三章　行业研究细化（10 个子节 + 4 条硬规则）

### 7.1 10 个子节（对应 9 个 question + 1 小结）

| # | 子节 | 对应 question_id | 必需/可选 |
|---|---|---|---|
| 1 | 行业定义与边界 | industry_definition | 必需 |
| 2 | 公司所属细分领域 | industry_definition（细分） | 必需 |
| 3 | 行业规模、增速与周期位置 | industry_scale_cycle | 必需（缺单一数字可用代理指标） |
| 4 | 供需关系 | industry_supply_demand | 必需 |
| 5 | 价格与成本驱动 | industry_supply_demand | 必需 |
| 6 | 竞争格局与主要参与者 | industry_competition | 必需 |
| 7 | 政策、监管、技术替代与外部冲击 | industry_policy | 必需 |
| 8 | 公司行业地位与相对竞争能力 | industry_position | 必需 |
| 9 | 可比公司选择与相对比较 | industry_comparables | 目标（3~5 家是目标非门禁） |
| 10 | 行业结论有效期与监测指标 | industry_monitoring | 必需 |

> 行业章目标字数 ≤ 1,200；9/10 两个子节在可比公司缺失时须诚实降级（见 7.2）。

### 7.2 硬规则 1：联营/合营企业**不得**作为可比公司

- 当前 `claim_59c2e14cde75ee2a12ec1d51`（`industry_comparables`）把「上海快卜、阿维塔科技、洛阳钼业」等**合营/联营企业**当作可比公司，
  这是**方向性错误**——联营企业是权益法核算的投资标的，不是同行业竞争对手，可比性维度完全不同。
- 重构后：`industry_comparables` 的 claim 若内容为「存在联营/合营企业」，应**排除或降级**到第 5 章「重大事项/投资」叙述，
  **不得**进入「可比公司」子节。
- 可比公司来源应来自 `industry_competition` / `industry_position` 的竞争格局 claim，且必须落到 `sc_decisions.yaml` SC-03 的 A/B/C/D 分级外部来源。

### 7.3 硬规则 2：排除哪些 Claim（不进入行业章正文）

以下类别的 claim 在重构后**不进入**第 5 章正文（或只进入审计附录）：

1. 内容是「存在合营/联营企业」的 claim（见 7.2，归类错误）；
2. `industry_definition` 中 `fact_hedged` 命中、含「预计」的 fact claim（降 inference 或删）；
3. 与财务章期间口径冲突、且无法可靠统一 metric_key 的行业收入类 claim（见 7.4，改为「潜在冲突需人工复核」标注）；
4. `refs=['evidence']` 但 `CitationAuthority` 复验 `support_status_unavailable` 的 claim（沿用现有排除逻辑）。

### 7.4 硬规则 3：动力电池/主营业务收入跨章节冲突处理

- 现状：财务章 `fin_growth`（最新季度快照）与行业章 `industry_supply_demand` 的
  `claim_6e53b40a197a78cb9266ecda`（2024 营收同比跌 9.7%）、`claim_8b95e31f22fecd8948e239a2`（动力电池系统收入 2024 降 2025 升）、
  `claim_2c09ed815a0439ebb8bb000d`（2023-2025 营业成本）存在**期间口径不一致**。
- 处理：`detect_cross_section_conflicts` 若判定为**精确冲突**（同 item_code/formula_id + 同 period 不同 snapshot），排除相关 claim；
  若无法生成统一 metric_key（行业 evidence claim 无 formula_id），**不在正文宣称口径统一**，标「潜在冲突需人工复核」，
  并把两章数字并排展示、注明各自期间与口径。
- **禁止**：为消除冲突而把行业章的年度营收数字「翻译」成财务章季度口径——那会违反「LLM 不算数」。

### 7.5 硬规则 4：定向重跑白名单（不得重跑全部 41 问）

若人工确认需要补行业章缺口，**只允许**定向重跑以下 question（其余 41 问中的其他问题一律不得重跑）：

| 允许重跑 | 理由 | 是否阻断 |
|---|---|---|
| industry_scale_cycle | `MODEL_OUTPUT_INVALID`，且可用代理指标低成本补齐 | 否 |
| industry_competition | `CONSECUTIVE_NO_NEW_EVIDENCE`，行业章必需 | 否 |
| industry_position | `CONSECUTIVE_NO_NEW_EVIDENCE`，行业章必需 | 否 |
| industry_comparables | 缺可比公司名单，SC-03 目标项 | 否 |
| industry_risk_transmission | `no_valid_claim`，impact `solvency` | 否 |

**禁止重跑**：industry_policy（`FATAL_TOOL_ERROR` 属工具故障，先修工具）、company_subject_match、company_control_chain、
company_business_main、company_debt_credit、company_debt_guarantee（授信额度属内部信息，检索无用）、
以及财务章 EBITDA/FCF（属输入项缺失，非检索问题）。

### 7.6 行业章示例骨架

> 本章结论：`[公司名称]` 所属 `[行业名称]` 当前处于 `[周期位置]`，行业规模 `[规模/代理指标]`[引用1]。
> 主要竞争者为 `[主要参与者]`[引用2]，公司行业地位为 `[地位]`[引用2]。可比公司 `[可比公司1~3]` 相对比较见下表。
> 行业风险对收入的传导：`[传导]`。结论有效期 `[有效期]`，监测指标 `[指标清单]`。

---

## 八、正文与审计展示分离

### 8.1 正文**不显示**的内容（一律只进审计附录）

- claim_id、snapshot_id、hash、错误码（reason_code）、Prompt、返工指令、unresolved 原文、evaluation 全量 issues。
- 正文引用统一为**脚注** `[1][2]…`，指向第 7 章「引用来源」表，不内联任何机器标识。

### 8.2 正文段落组织铁律

- 每段顺序：**先结论、再事实、再对信用的影响**（不是按 claim 原始顺序罗列）。
- 每条事实句之后紧跟脚注 `[N]`；`<!-- claim:id -->` marker 只在**确定性/LLM 编辑器内部**使用，渲染成正文时**剥离**，
  marker 与 claim_id 的映射进入审计附录的「正文↔claim 映射表」。

### 8.3 审计视图

- 保留**全部**审计信息（claim_id、snapshot_id、hash、错误码、返工指令、unresolved 原文、`editor_audit` 的 call_id/model/token/prompt_version）。
- 默认折叠（HTML `<details>` 或 Markdown 折叠块），按章分组，按「阻断缺口 ≤5 条 + 其余 N 项」分两级展示。

### 8.4 阻断缺口的展示规则

- 优先级排序：`(1) WAITING_HUMAN/CONFLICT` → `(2) impact ∈ {subject, solvency, key_financial, credit_scheme}` → `(3) 核心结论` → `(4) 其他`。
- 关键缺口 ≤5 条进正文「核心风险与资料限制」章，其余+技术诊断进审计附录「另有 N 项」。

---

## 九、状态语义重审（发布态状态规则修订建议）

> 当前 `derive_publication_status` 输出 `BLOCKED_FOR_PUBLICATION`，但语义粒度不足。建议修订为**三态 + 二标注**：

| 建议状态 | 语义 | 触发条件（建议） |
|---|---|---|
| `PUBLICATION_READY` | 可正式出具 | 三章均无 REPORT_BLOCKED/SECTION_BLOCKED/JOB_BLOCKED 缺口，且无 WAITING_HUMAN |
| `PUBLICATION_DRAFT` | 草稿可读、待人工确认 | 存在 WAITING_HUMAN / 非阻断缺口 / `COMPLETED_WITH_GAPS`，但核心事实已可读 |
| `PUBLICATION_BLOCKED` | 不可出具 | 存在 REPORT_BLOCKED（subject/solvency/key_financial）且核心结论缺失 |

- **标注 1「资料限制」**：正文显式声明「哪些结论因资料缺失而不成立」。
- **标注 2「草稿/待人工确认」**：章首水印式标注，不可正式出具。

> 当前三章真实状态映射：公司章 `WAITING_HUMAN`+`BLOCKED`、财务章 `COMPLETED_WITH_GAPS`、行业章 `REWORK`。
> 按建议规则，整份报告应为 **`PUBLICATION_BLOCKED`**（因 company_subject_match / company_control_chain /
> company_business_main / company_debt_credit / company_debt_guarantee 多阻断），而非笼统的 `BLOCKED_FOR_PUBLICATION`。

**修订风险提示**：此状态规则变更会改变 `publication_id` 指纹字段（状态值入 fingerprint），需评估是否破坏既有内容寻址——建议状态语义变更与 §十一 批次 A 一同评估，不单独热改。

---

## 十、三张对照表

### 表 1：当前内容处置表（逐章逐 topic 处置）

| 当前章节/topic | 现状 | 处置 | 去向 |
|---|---|---|---|
| 标题层（无封面/摘要） | 缺失 | 新增 | §三 区块 1、2 |
| 第1章 company_identity | 13 claim 冗余 | 保留身份表，历史沿革收敛 | §五 ① ② |
| 第1章 company_subsidiaries | 11 claim 含联营/关联方 | 子公司入②，联营/关联方降级到⑤ | §五 ② ⑤ |
| 第1章 company_business | 13 claim | 收入构成压成 BusinessSegmentFactPack 表 | §五 ③ |
| 第1章 company_governance | 16 claim 过详 | 压为治理简表 | §五 ④ |
| 第1章 company_debt | 仅 4 claim，含阻断 | 保留但标注授信额度缺失 | §五 ⑥ |
| 第1章 company_legal_risks | 仅 2 claim | 并入重大事项与或有风险 | §五 ⑤ |
| 第1章 company_competitiveness | 7 claim 营销化 | 提炼 1~2 条进信用小结 | §五 ⑦ |
| 第1章 company_credit_summary | 含「不存在逾期」隐患 | 措辞降级为「无证据显示」 | §五 ⑦ |
| 第2章 10 小节 | 碎片化 | 收敛 5 小节 | §六 6.1 |
| 第2章 fin_growth 季度 yoy | NOT_APPLICABLE | 删除季度增长率展示 | §六 决定 4 |
| 第3章 industry_comparables | 联营当可比（错误） | 排除该 claim，重跑 industry_comparables | §七 7.2/7.5 |
| 第3章 industry_supply_demand | 收入口径冲突 | 跨章节标注「潜在冲突」 | §七 7.4 |
| 审计附录 | 混入正文 | 正文/附录分离 | §八 |

### 表 2：核心数据缺口表（影响发布资格的缺口）

| 缺口 | 来源 question | 状态/原因 | impact | 阻断 | 处置建议 |
|---|---|---|---|---|---|
| 主体一致性未确认 | company_subject_match | WAITING_HUMAN | subject | JOB_BLOCKED | 人工确认，不重跑 |
| 控制关系未确认 | company_control_chain | WAITING_HUMAN | subject | REPORT_BLOCKED | 人工确认，不重跑 |
| 主营业务未确认 | company_business_main | NOT_FOUND_AFTER_SEARCH | subject | SECTION_BLOCKED | 人工确认，不重跑 |
| 授信额度/已用规模未披露 | company_debt_credit | NOT_FOUND_AFTER_SEARCH | solvency | REPORT_BLOCKED | 内部信息，人工补充 |
| 发债/借款/担保未确认 | company_debt_guarantee | NOT_FOUND_AFTER_SEARCH | solvency | REPORT_BLOCKED | 内部信息，人工补充 |
| EBITDA 未取得 | fin_solvency | MISSING_REQUIRED_ITEM | solvency | 否 | 标「未取得」，不代算 |
| 自由现金流未取得 | fin_cashflow | MISSING_REQUIRED_ITEM | solvency | 否 | 标「未取得」，不代算 |
| 行业可比公司缺失 | industry_comparables | COMPLETED_WITH_GAPS | — | 否 | 定向重跑（白名单） |
| 行业风险传导缺失 | industry_risk_transmission | no_valid_claim | solvency | 否 | 定向重跑（白名单） |

### 表 3：目标报告结构表（8 区块 × 内容 × 篇幅）

| # | 区块 | 内容来源 | 目标字数 |
|---|---|---|---|
| 1 | 封面与口径 | publication.json 元数据 | ≤ 200 |
| 2 | 核心研究摘要 | 三章结论句聚合 | ≤ 500 |
| 3 | 第一章 公司信用研究 | §五 7 子结构 | ≤ 2,800 |
| 4 | 第二章 财务分析 | §六 5 小节 | ≤ 2,000 |
| 5 | 第三章 行业研究 | §七 10 子节 | ≤ 1,200 |
| 6 | 核心风险与资料限制 | 阻断缺口 ≤5 条 | ≤ 600 |
| 7 | 引用来源 | 脚注 [1]…[N] 映射表 | 不设上限 |
| 8 | 审计附录 | 全量审计信息，默认折叠 | 不设上限 |
| **合计（正文 1~6）** | | | **≈ 5,300 ~ 7,300** |

---

## 十一、实施批次建议（A~F）

> 每批都给出：修改范围 / 不修改范围 / 输入 / 输出 / 测试 / 真实验收 / 停止边界。

### 批次 A：渲染骨架重构（结构与状态语义，**纯确定性，无 LLM**）
- **修改范围**：`render_report_markdown`（8 区块结构）、`derive_publication_status`（三态+二标注，§九）。
- **不修改范围**：Contract、Prompt、`classify_claim`、`CitationAuthority`、指纹字段结构（状态值入指纹需单独评审）。
- **输入**：`publication.json` + 三章 `section_result.json`。
- **输出**：8 区块骨架 markdown（含封面/口径、摘要、风险限制、引用来源占位）。
- **测试**：`evals/test_section_publishable_report.py` 增加「8 区块存在性」「三态状态判定」「脚注剥离」回归。
- **真实验收**：对 `run_20260911T_jsonfix` 生成，检查 8 区块齐全、状态标注正确。
- **停止边界**：不启用 LLM 编辑器，不改指纹。

### 批次 B：公司章收敛（§五 7 子结构 + 表格化）
- **修改范围**：公司章 topic→子结构映射、BusinessSegmentFactPack 表、控制关系表、债务表。
- **不修改范围**：财务/行业章、状态语义。
- **输入**：公司章 `section_result.json`。
- **输出**：公司章 7 子结构 markdown。
- **测试**：表格渲染、联营企业降级、`absence_contradicts_gap` 措辞降级。
- **真实验收**：公司章 ≤ 2,800 字，阻断缺口诚实披露。
- **停止边界**：不碰 company_debt_credit/guarantee 的 REPORT_BLOCKED 语义。

### 批次 C：财务章收敛（§六 7 决定 + 5 小节）
- **修改范围**：财务章 10→5 小节、季度 yoy 删除、EBITDA/FCF「未取得」标注、15% 科目强制分析。
- **不修改范围**：指标计算公式、Formula Registry。
- **输入**：财务章 `section_result.json` + frozen snapshot。
- **输出**：财务章 5 小节 markdown。
- **测试**：期间可比性分类（`formula_period_comparability`）回归、NOT_APPLICABLE 展示。
- **真实验收**：财务章 ≤ 2,000 字，无季度增长率数字。
- **停止边界**：不改公式，不算数。

### 批次 D：行业章收敛（§七 10 子节 + 4 硬规则）
- **修改范围**：行业章 3→10 子节、联营企业排除、跨章节冲突标注、定向重跑白名单落盘。
- **不修改范围**：行业 Contract 9 问题定义、来源分级。
- **输入**：行业章 `section_result.json`。
- **输出**：行业章 10 子节 markdown + 白名单清单。
- **测试**：联营企业排除、`fact_hedged` 降级、冲突标注。
- **真实验收**：行业章 ≤ 1,200 字，可比公司子节如实降级。
- **停止边界**：本轮不实际重跑任何 question（白名单仅供人工决策后单独批次执行）。

### 批次 E：正文/审计分离 + 脚注（§八）
- **修改范围**：渲染层剥离 marker/claim_id 到审计附录、脚注 `[N]` 生成、引用来源表生成、`<details>` 折叠。
- **不修改范围**：`deterministic_editor`/`llm_editor` 内部 marker 逻辑（marker 只在编辑器内部，渲染剥离）。
- **输入**：批次 A~D 产物。
- **输出**：分离后的正文 + 审计附录 + 引用来源。
- **测试**：脚注与引用来源一一对应、marker 全部剥离、附录默认折叠。
- **真实验收**：正文无 claim_id/snapshot_id/hash/错误码/返工指令。
- **停止边界**：不改 editor 的 marker 规则。

### 批次 F：端到端回归 + 人工门
- **修改范围**：仅测试与文档，不加新功能。
- **不修改范围**：A~E 已冻结逻辑。
- **输入**：全量 eval + `run_20260911T_jsonfix` 真实产物。
- **输出**：`PHASE4_DELIVERY_REPORT.md` 更新、人工复核清单、是否进入 Phase 5 结论。
- **测试**：`python -m evals.run_evals`（基线 3793/0/0，需保持 0 失败）。
- **真实验收**：正文 5,000~8,000 字符、8 区块齐全、审计可回查。
- **停止边界**：不 commit、不进 Phase 5，等人工确认本规格后再实施。

> 批次依赖：A → B/C/D（可并行）→ E → F。批次 B/C/D 之间无依赖，可并行。批次 D 的「定向重跑白名单」**不在 D 内执行**，
> 需人工确认后另起独立批次（不得在本规格未确认前触发任何重跑）。

---

## 十二、最终输出

### 12.1 本规格路径
`d:\Claude_shouxin_ver1\shouxinbaogaoshengcheng_ceshi-_ver1\PHASE4_REPORT_RESTRUCTURE_SPEC.md`

### 12.2 当前报告的 10 项最重要问题（按严重度排序）

1. **无封面/口径声明与核心摘要**——读者无法一屏获知主体、报告期、结论、数据范围。
2. **正文与审计未分离**——claim_id/snapshot_id/错误码/返工指令混入正文，可读性差且泄露机器审计痕迹。
3. **公司章 5 项 REPORT_BLOCKED/JOB_BLOCKED/SECTION_BLOCKED 未在章首统一提示**，阻断风险藏在审计附录。
4. **行业章把联营/合营企业当可比公司**（`claim_59c2e14cde75ee2a12ec1d51`），属方向性错误。
5. **财务章「季度 yoy 增长率 NOT_APPLICABLE」但正文可能展示季度增长率**，违反期间可比性政策。
6. **财务章 EBITDA/FCF 未取得但未见显式「未取得」标注**，有被读者误当正式指标的风险。
7. **跨章节收入口径冲突**（财务季度快照 vs 行业年度数）未标注，可能被读成一致结论。
8. **42KB 篇幅远超 5,000~8,000 目标**，信息冗余与碎片化并存（公司章 10 小节、财务章 10 小节）。
9. **26+26+11 条 `aspect_uncovered` 大量积压**，其中部分是语义覆盖误报、部分是真实缺口，未经 LLM 复核无法区分。
10. **`absence_contradicts_gap` 类断言**（「不存在逾期/不存在重大变更」）在证据缺失时仍用断言式措辞，违反「缺证据 ≠ 事实不存在」。

### 12.3 需人工确认的业务选择（编码前必须拍板）

| # | 决策点 | 建议默认 | 影响 |
|---|---|---|---|
| 1 | 状态语义是否采用「三态+二标注」（§九） | 采用，但单独评审指纹影响 | publication_id 指纹字段 |
| 2 | 行业可比公司缺失时是否「如实降级 + 定向重跑白名单」 | 是，重跑白名单仅限 §7.5 5 项 | 行业章完整性 |
| 3 | 财务「季度 yoy」是否彻底不展示 | 是 | 财务章内容 |
| 4 | 公司章 7 子结构 vs 保留 10 小节 | 采用 7 子结构 | 公司章结构 |
| 5 | 篇幅上限是否硬性 8,000 字符 | 是，超出即 fail | 压缩策略 |
| 6 | 联营/合营企业是否一律排除出「可比公司」子节 | 是 | 行业章正确性 |

### 12.4 可直接采用的技术默认值（无需再议）

- 8 区块结构（§三）；脚注 `[N]` + 引用来源表（§八）；审计附录默认折叠。
- 公司章 BusinessSegmentFactPack / 控制关系 / 债务 三张表（§五）。
- 财务章 5 小节收敛 + 7 项业务决定（§六）。
- 行业章 10 子节 + 4 硬规则（§七）。
- `formula_period_comparability` 期间可比性分类（§六 决定 3）。
- 关键缺口优先级 `(1)WAITING_HUMAN/CONFLICT (2)subject/solvency/key_financial/credit_scheme (3)核心结论 (4)其他`（§八 8.4）。

### 12.5 是否具备开始重构的条件

**具备，但需先完成 §12.3 的 6 项人工确认。**

- 技术前置已全部就绪：上游 Contract / sc_decisions / Formula Registry / 发布态投影代码均已读透；
  真实 `run_20260911T_jsonfix` 数据齐备，无需重跑任何 Worker/LLM/检索。
- 唯一阻塞点是 §12.3 中会影响 `publication_id` 指纹与报告结构的 6 项业务选择，须由人工拍板后方可进入批次 A。

---

## 附：本规格未触及（明确不在此轮范围）

- 不修改 `standard_v2.yaml` / `sc_decisions.yaml` / `required_aspects_review.md` / `FORMULA_REVIEW.md`。
- 不修改任何 Prompt（含 `publication_editor.txt`）。
- 不重跑 Research Worker / Router / Retriever / Web Search / Financial Pipeline。
- 不 commit、不进 Phase 5。
- 不把真实公司（宁德时代/300750）的事实、数字、实体名写入任何通用段落骨架——仅 §一 诊断如实引用既有数据。
