# Required Aspects 业务复核文档

> **状态：4 题已由业务确认（CONFIRMED_AS_REQUIRED ×2 / CONFIRMED_AS_REQUIRED_AFTER_CORRECTION ×2），
> 48 题 DERIVED_NO_SEMANTIC_CHANGE 自动通过**
> 本文档针对 `templates/contracts/standard_v2.yaml` 中 52 个 KeyQuestion 的 `required_aspects` 字段做业务复核，
> 判断每个 aspect 是「问题原文的自然分解」还是「新增需求」。原 4 题 `BUSINESS_REVIEW_REQUIRED`
> 已于 2026-09 由业务逐题确认（结果见下），确认结论落于各题「语义变更状态」列。
>
> 复核口径：逐题比对 `question` 原文 vs `required_aspects`（含 `evidence_requirements[].required_fields` 与
> `calculation_requirements` 的贡献），只判「aspect 是否从问题原文派生」；业务确认后按确认结果修订 YAML。

---

## 结论摘要

| 指标 | 数量 |
|------|------|
| 问题总数（KeyQuestion） | 52 |
| P0 问题数 | 22 |
| DERIVED_NO_SEMANTIC_CHANGE（自然分解，自动通过） | 48 |
| CONFIRMED_AS_REQUIRED（业务确认必答） | 2 |
| CONFIRMED_AS_REQUIRED_AFTER_CORRECTION（业务确认并修订 aspects） | 2 |

**4 题业务确认结果（均为 P0，全部集中在 P0 专项汇总中）：**

| question_id | 业务确认结论 | 最终 required_aspects 修订 | 章节 |
|-------------|--------------|------------------------------|------|
| company_subject_match | CONFIRMED_AS_REQUIRED | 「经营状态」确认为必答（无修订） | company |
| company_business_main | CONFIRMED_AS_REQUIRED_AFTER_CORRECTION | 补入「各业务成本与毛利构成」「产业链位置」（「对应报告期与口径」已确认） | company |
| company_debt_guarantee | CONFIRMED_AS_REQUIRED_AFTER_CORRECTION | 补回「发债情况」「金融机构借款」（「担保范围口径与报告期」已确认） | company |
| industry_scale_cycle | CONFIRMED_AS_REQUIRED | 「数据截止日期与统计口径」确认为必答（无修订） | industry |

> **说明**：上述 4 题新增项均来自契约内 `required_fields` / `completion_rules`（即「按规则派生」而非「从问题原文自然切分」），
> 属业务口径补强。业务已确认这些 aspect 确应为必答方面；其中 company_business_main 与 company_debt_guarantee
> 的原文「成本/毛利构成、产业链位置」「发债、金融机构借款」此前未落入 aspects，本次一并补入 YAML。
> 映射层保持原 scope：COMP-R1 仍只映射「主营业务构成 / 各业务收入及收入占比 / 对应报告期与口径」，
> COMP-CR1 仍只映射「授信额度 / 对外担保余额 / 担保口径」，不随 YAML 扩张（`covered_aspects` 已钉住）。

---

## P0 专项汇总（22 题，须优先复核）

| 章节 | question_id | 语义变更状态 | 需人工确认 |
|------|-------------|--------------|-----------|
| company | company_subject_match | **CONFIRMED_AS_REQUIRED** | 否（已确认） |
| company | company_identity_basic | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| company | company_control_chain | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| company | company_business_main | **CONFIRMED_AS_REQUIRED_AFTER_CORRECTION** | 否（已确认） |
| company | company_debt_credit | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| company | company_debt_guarantee | **CONFIRMED_AS_REQUIRED_AFTER_CORRECTION** | 否（已确认） |
| company | company_credit_summary | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_statements_availability | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_consistency | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_balance_structure | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_solvency | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_profitability | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_cashflow | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| financial | fin_risk_summary | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| industry | industry_scale_cycle | **CONFIRMED_AS_REQUIRED** | 否（已确认） |
| industry | industry_position | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| industry | industry_risk_transmission | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| synthesizer | synth_scheme_overview | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| synthesizer | synth_risks | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| synthesizer | synth_repayment | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| synthesizer | synth_evaluation | DERIVED_NO_SEMANTIC_CHANGE | 否 |
| synthesizer | synth_disclaimer | DERIVED_NO_SEMANTIC_CHANGE | 否 |

---

## 全量逐题复核表

图例：
- `DERIVED_NO_SEMANTIC_CHANGE`：aspect 为问题原文的自然分解/合并，无新增语义，自动通过。
- `CONFIRMED_AS_REQUIRED`：业务已确认该新增 aspect 确应为必答方面（无需修订 YAML）。
- `CONFIRMED_AS_REQUIRED_AFTER_CORRECTION`：业务确认新增 aspect 必答，且原文已列但未落入 aspects 的方面已补入 YAML。
- `需人工确认`：`否` = 自动通过或已确认；`是` = 待确认（当前 4 题均已确认，无剩余待确认）。

### 1. 公司信用研究（company，21 题）

| question_id | 问题（原文） | required_aspects | priority | 语义变更状态 | 需人工确认 | 备注 |
|-------------|-------------|------------------|----------|--------------|-----------|------|
| company_subject_match | 核对输入企业名称、材料内主体、股票代码与公开企业信息是否一致 | 输入企业名称与材料主体一致性；股票代码；经营状态 | P0 | **CONFIRMED_AS_REQUIRED** | 否（已确认） | 新增「经营状态」已确认必答（来自 required_fields） |
| company_identity_basic | 成立日期、办公地址、法定代表人、注册资本、实缴资本和经营范围 | 成立日期；办公地址；法定代表人；注册资本；实缴资本；经营范围 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 六项逐一对齐 |
| company_identity_history | 历史沿革：重大改革、股权变更、法定代表人变更、上市及重大募资事项 | 重大改革与股权变更；法定代表人变更；上市及重大募资事项 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「重大改革」+「股权变更」合并为一项 |
| company_control_chain | 控股股东、实际控制人及控制链条（含可视化关系图）… | 控股股东；实际控制人；持股比例与控制链条 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「持股比例」为「控制链条」的构成展开；「可视化关系图」由 output_requirements(or_company_structure) 兜底 |
| company_subsidiaries | 主要子公司、集团结构与重要关联方 | 主要子公司；集团结构；重要关联方 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| company_business_main | 主营业务、收入/成本/毛利构成与产业链位置… | 主营业务构成；各业务收入及收入占比；各业务成本与毛利构成；产业链位置；对应报告期与口径 | P0 | **CONFIRMED_AS_REQUIRED_AFTER_CORRECTION** | 否（已确认） | 确认「对应报告期与口径」必答，并补入原文「各业务成本与毛利构成」「产业链位置」 |
| company_business_model | 采购/生产/销售模式、技术路线、成本与竞争能力 | 采购生产销售模式；技术路线；成本与竞争能力 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| company_customer_concentration | 客户集中度（客户名称依法未披露时标注…） | 客户集中度；依法未披露时的标注 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「依法未披露」来自原文括号 |
| company_supplier_concentration | 供应商集中度 | 供应商集中度 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 单一 aspect |
| company_competitiveness | 核心竞争力 | 核心竞争力 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 单一 aspect |
| company_rd_capacity | 研发能力、发展计划与在建工程 | 研发能力；发展计划；在建工程 | P2 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| company_governance | 治理结构、内控、管理层稳定性及主要管理人员履历 | 治理结构与内控；管理层稳定性；主要管理人员履历 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「治理结构」+「内控」合并为一项 |
| company_litigation | 重大诉讼、违约、处罚、失信与退市风险（无发现也须记录检索范围与截止日期） | 重大诉讼与处罚；失信与退市风险；检索范围与截止日期 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「违约」并入「重大诉讼与处罚」（口径合并）；「检索范围与截止日期」来自原文括号 |
| company_related_transactions | 关联交易（采购/销售/担保）的合规性与占比 | 关联采购销售担保；关联交易占比；合规性 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| company_pledge_sentiment | 股权质押与舆情 | 股权质押情况；舆情 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| company_debt_credit | 授信额度及已用规模；材料明显缺失无法核实时不生成对应偿债结论 | 公司整体授信额度及使用情况 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「公司整体」为范围限定（防局部当合计），语义可从原文派生 |
| company_debt_guarantee | 发债、金融机构借款和对外担保（结构化表格呈现）… | 发债情况；金融机构借款；公司整体对外担保余额；担保范围口径与报告期 | P0 | **CONFIRMED_AS_REQUIRED_AFTER_CORRECTION** | 否（已确认） | 确认「担保范围口径与报告期」必答，并补回原文「发债情况」「金融机构借款」 |
| company_profit_quality | 非主营损益（投资收益、公允价值变动、减值、营业外收支）及利润质量 | 非主营损益构成；利润质量 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 括号内四项并入「非主营损益构成」 |
| company_investment | 重大投资、收并购、资产出售与定向增发等影响经营的事件 | 重大投资与收并购；资产出售；定向增发 | P2 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| company_equity_incentive | 股权激励计划及进展（无相关计划时为 NOT_APPLICABLE…） | 股权激励计划及进展；无计划时为不适用 | P2 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「无计划时不适用」来自原文括号 |
| company_credit_summary | 公司核心信用优势、风险、偿债影响与未解决问题 | 核心信用优势；核心风险；偿债影响与未解决问题 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |

### 2. 财务分析（financial，14 题）

| question_id | 问题（原文） | required_aspects | priority | 语义变更状态 | 需人工确认 | 备注 |
|-------------|-------------|------------------|----------|--------------|-----------|------|
| fin_statements_availability | 最新完整年度三张主表…与审计意见齐备；趋势原则上覆盖近三年… | 资产负债表齐备；利润表齐备；现金流量表齐备；审计意见；近三年趋势覆盖 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 原文「最新季度/半年」的纳入/披露缺口未单列为 aspect（由 missing_policy 兜底） |
| fin_audit_opinion | 审计意见、会计师事务所、报告期、合并范围与单位… | 审计意见；会计师事务所；报告期；合并范围；金额单位 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 五项逐一对齐 |
| fin_consistency | 三张报表及附注勾稽一致；多来源数字冲突时保留各来源值并生成 reconciliation issue… | 三表勾稽一致性；多来源冲突保留各来源并生成 reconciliation | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| fin_balance_structure | 资产负债结构与重大科目变化；科目占资产或负债 15% 以上强制分析… | 资产负债结构；重大科目变化；15%以上科目强制分析 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| fin_solvency | 短期与长期偿债能力、净资产水平与刚性债务结构 | 短期偿债能力；长期偿债能力；净资产水平；刚性债务结构 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 四项逐一对齐；5 个比率来自 calculation_requirements（契约定义） |
| fin_profitability | 盈利能力与利润质量 | 毛利率；净利率；ROE；ROA；利润质量 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 毛利率/净利率/ROE/ROA 来自 calculation_requirements（契约定义，非新增） |
| fin_operating | 营运效率（应收账款、存货、总资产周转） | 应收账款周转；存货周转；总资产周转 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| fin_cashflow | 现金流结构与现金保障程度（经营现金流能否覆盖债务与贷款偿付） | 现金流结构；经营现金流覆盖债务能力 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| fin_growth | 增长趋势、异常变动、可能原因与杜邦分析 | 营收增长率；净利增长率；异常变动及原因；杜邦分析 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 营收/净利增长率来自 calculation_requirements（契约定义，非新增） |
| fin_asset_quality | 非主营损益、减值、受限资产、商誉、开发支出对利润和资产质量的影响 | 非主营损益与减值；受限资产；商誉与开发支出 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| fin_risk_summary | 财务风险结论及其对当前授信方案的影响 | 财务风险结论；对授信方案的影响 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| fin_working_capital_needs | 流动资金需求测算（收入增长预测、营运资金周转、存货/应收/预付/预收/应付） | 收入增长预测；营运资金周转；存货应收预付预收应付 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 条件题（applies_when），三项逐一对齐 |
| fin_trade_finance_focus | 贸易融资按业务类型选择关键科目（如应收账款、存货、预付）重点分析 | 关键科目选择；应收账款存货预付重点分析 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 条件题（applies_when），两项逐一对齐 |
| fin_capital_capacity | 固定资产/项目贷款：第一阶段仅从公司财务角度评价资本实力与现有项目现金流 | 资本实力；现有项目现金流 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 条件题（applies_when），两项逐一对齐 |

### 3. 行业研究（industry，9 题）

| question_id | 问题（原文） | required_aspects | priority | 语义变更状态 | 需人工确认 | 备注 |
|-------------|-------------|------------------|----------|--------------|-----------|------|
| industry_definition | 行业定义、边界与公司所属细分领域 | 行业定义与边界；公司所属细分领域 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| industry_scale_cycle | 行业规模、增速与当前周期位置（缺单一数字不阻断，可用代理指标） | 行业规模；增速；当前周期位置；数据截止日期与统计口径 | P0 | **CONFIRMED_AS_REQUIRED** | 否（已确认） | 新增「数据截止日期与统计口径」已确认必答（来自 required_fields） |
| industry_supply_demand | 供需关系、价格与成本驱动因素及对经营的影响 | 供需关系；价格与成本驱动；对经营的影响 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| industry_competition | 竞争格局、集中度与主要参与者 | 竞争格局与集中度；主要参与者 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| industry_policy | 政策、监管、技术替代与外部冲击 | 政策与监管；技术替代；外部冲击 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| industry_position | 公司行业地位与相对竞争能力 | 公司行业地位；相对竞争能力 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| industry_comparables | 选择 3～5 家可比公司并做相对比较（…） | 可比公司选择；相对比较 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「3~5 家目标而非门禁」由 completion_rules 兜底，不单列 aspect |
| industry_risk_transmission | 行业风险向借款人收入、成本、资本开支、现金流和偿债能力的传导 | 对收入的传导；对成本与资本开支的传导；对现金流与偿债能力的传导 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| industry_monitoring | 行业结论有效期与监测指标 | 结论有效期；监测指标 | P2 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |

### 4. 综合方案评价（synthesizer，8 题）

| question_id | 问题（原文） | required_aspects | priority | 语义变更状态 | 需人工确认 | 备注 |
|-------------|-------------|------------------|----------|--------------|-----------|------|
| synth_scheme_overview | 授信主体、授信类型与用户输入方案概览 | 授信主体；授信类型；授信金额与期限；增信措施 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 「授信金额与期限」「增信措施」来自 required_fields，为「方案概览」的构成展开 |
| synth_strengths | 支持该方案的核心优势 | 支持方案的核心优势 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 单一 aspect |
| synth_risks | 该方案面临的核心风险及风险传导 | 核心风险；风险传导 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| synth_repayment | 第一还款来源与现有增信措施的有效性 | 第一还款来源；现有增信措施有效性 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| synth_evaluation | 授信方案的优点、缺点与综合评价 | 方案优点；方案缺点；综合评价 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 三项逐一对齐 |
| synth_mismatch | 若方案明显不合理，指出具体不匹配项及依据；否则不主动改写… | 明显不合理时的具体不匹配项及依据 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 单一 aspect（「否则不改写」为约束，由 completion_rules 兜底） |
| synth_unresolved | 仍未解决的信息缺口和需人工确认事项 | 未解决信息缺口；需人工确认事项 | P1 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 两项逐一对齐 |
| synth_disclaimer | 明确声明"本评价由 AI 生成，仅供参考" | AI生成声明 | P0 | DERIVED_NO_SEMANTIC_CHANGE | 否 | 单一 aspect |

---

## SC-01 ~ SC-05 状态说明

本文档只复核 `required_aspects` 的**派生语义**，不改变 `contracts/sc_decisions.yaml` 中
SC-01 ~ SC-05 的既有 `status` 字段（当前均为 `confirmed`，见 `contracts/review/section_contract_review.md`）。
SC-01 ~ SC-05 是**章节契约决策**，与本文件逐题 aspect 复核分属两个层面，互不覆盖。

---

## Phase 4 前置条件

进入 Phase 4（完整 41 问 / 全量评估）前，须满足：

1. ✅ 本文件 4 题 `BUSINESS_REVIEW_REQUIRED` 已由业务**逐一确认**（2026-09）：
   `CONFIRMED_AS_REQUIRED` ×2（company_subject_match / industry_scale_cycle）、
   `CONFIRMED_AS_REQUIRED_AFTER_CORRECTION` ×2（company_business_main / company_debt_guarantee）。
2. ✅ 确认结果已落于本文档各题「语义变更状态」列，`需人工确认` 由「是」改为「否（已确认）」；
   YAML 已按确认结果补入 company_business_main 的「各业务成本与毛利构成」「产业链位置」、
   company_debt_guarantee 的「发债情况」「金融机构借款」。
3. ✅ 映射层 scope 已钉住：COMP-R1 / COMP-CR1 增 `covered_aspects`，不随 YAML 扩张（见上文说明）。
4. ✅ SC-01 ~ SC-05 维持 `confirmed`，未改动（见 §SC-01 ~ SC-05 状态说明）。
