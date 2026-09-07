# FORMULA_REVIEW — A2～A5 完成后公式口径复核（暂停门 §15）

> 生成时间：2026-09-07
> 状态：**A6 Formula Registry 之前必须经业务确认，本文件为暂停门交付物，不进入 A6。**
> 数据来源：V1 `financial/metrics.py` 现实现 + V2 抽取覆盖（`financial_v2/mapping.py` 内置规则）。

本文件按任务书 §15 逐项列出 V1 现有指标公式、输入科目（V2 标准代码）、期间/scope 口径、
异常规则、材料可得性、与 V1 口径差异，并标注 `PROPOSED_DEFAULT`（暂拟默认口径，**未经业务
确认，不可视为 confirmed**）或 `BUSINESS_CONFIRMATION_REQUIRED`（口径有歧义或定义缺失，
需业务确认后才可在 A6 实现）。任何 `PROPOSED_DEFAULT` 未经业务确认前不得进入 A6 实现。

## 0. 符号与约定

- **输入科目代码**：与 `financial/schema.py` 常量名一致（V2 `standard_item_code` 同为英文大写）。
- **版本**：本文件全部公式均为 V1 基线版本 `1.0`（「FORMULA_REVIEW 阶段」的冻结口径）。
  A6 Formula Registry 落地时才分配正式版本号并升版；本文件不预注册 A6 版本号，也不把
  任何待确认项提前升版为 confirmed。
- **期间口径**：`end` = 期末值（报表日余额/流量）；`avg` = 期初期末平均值
  `(期初 + 期末) / 2`，期初 = 上一报告期同科目值。
- **报告期**：比率/周转/费用类取「本期」（同一 `report_period` + `period_type`，annual 或
  quarterly）；成长类取「本期 + 前期」（年报同比 = 最近上一个年报；季报环比 = 紧前期间）。
  同一公式的所有输入科目必须来自同一报告期，不得跨期混比。
- **季报累计值限制**：利润表与现金流量表在季报口径通常为「年初至今累计值」。环比（QoQ）
  **不得直接用累计值相减**，须先由累计值推导单季度值，或改用同期同比（YoY）。两种口径待
  业务确认（见 §5），确认前成长类季报环比不得进入 A6 实现。
- **scope**：V1 与 V2 均要求 `consolidated`（合并口径）；`parent`（母公司口径）不得与合并口径混用。
- **单位**：V2 标准值为 `yuan`；比率无量纲；增长率无量纲（百分比）。
- **负值规则**：比率类负分子/分母不阻断计算（保留符号供研判），仅分母为 0 判 `missing`；
  增长率分母取 `|前期|`，负前期仍可算但符号含义需业务确认；期间不足（缺前期）判 `missing`。
- **舍入**：V1 指标计算未做显式舍入（`float` 全精度）；A6 落地时建议统一输出 2 位小数（比率）
  与 2 位小数百分比（增长率），需业务确认。

## 1. 偿债能力（5 项）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `SOLV_CURRENT_RATIO` | 1.0 | 流动比率 | 流动 / 流动负债 | `CURRENT_ASSETS` | `CURRENT_LIABILITIES` | end | 本期 | consolidated | 分母 0 → missing |
| `SOLV_QUICK_RATIO` | 1.0 | 速动比率 | (流动 − 存货) / 流动负债 | `CURRENT_ASSETS` − `INVENTORY` | `CURRENT_LIABILITIES` | end | 本期 | consolidated | 任一分子缺失 → missing；分母 0 → missing |
| `SOLV_DEBT_RATIO` | 1.0 | 资产负债率 | 负债 / 资产 | `TOTAL_LIABILITIES` | `TOTAL_ASSETS` | end | 本期 | consolidated | 分母 0 → missing |
| `SOLV_INTEREST_COVER` | 1.0 | 利息保障倍数 | 息税前利润 / 财务费用 | `TOTAL_PROFIT` + `FINANCE_EXPENSES` | `FINANCE_EXPENSES` | end | 本期 | consolidated | 分母 0 → missing |
| `SOLV_EQUITY_MULT` | 1.0 | 权益乘数 | 资产 / 权益 | `TOTAL_ASSETS` | `TOTAL_EQUITY` | end | 本期 | consolidated | 分母 0 → missing |

**口径差异 / 待确认：**

- `SOLV_INTEREST_COVER`（利息保障倍数）：V1 以 `FINANCE_EXPENSES`（财务费用）作分母，
  且分子 EBIT = 利润总额 + 财务费用。严格口径应为「利息费用（`INTEREST_EXPENSE`）」，且
  EBIT 不含财务费用中的汇兑损益、手续费等非利息项。**BUSINESS_CONFIRMATION_REQUIRED**。
- `SOLV_QUICK_RATIO`（速动比率）：V1 仅减存货；严谨速动资产常再剔除预付款项、其他流动资产等。
  **BUSINESS_CONFIRMATION_REQUIRED**。

## 2. 盈利能力（5 项）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `PROF_GROSS_MARGIN` | 1.0 | 毛利率 | 毛利 / 营业收入 | `TOTAL_REVENUE` − `OPERATING_COST` | `TOTAL_REVENUE` | end | 本期 | consolidated | 分母 0 → missing |
| `PROF_NET_MARGIN` | 1.0 | 净利率 | 净利 / 营业收入 | `NET_PROFIT` | `TOTAL_REVENUE` | end | 本期 | consolidated | 分母 0 → missing |
| `PROF_ROE` | 1.0 | ROE | 净利 / 权益 | `NET_PROFIT` | `TOTAL_EQUITY` | end | 本期 | consolidated | 分母 0 → missing |
| `PROF_ROA` | 1.0 | ROA | 净利 / 资产 | `NET_PROFIT` | `TOTAL_ASSETS` | end | 本期 | consolidated | 分母 0 → missing |
| `PROF_OPER_MARGIN` | 1.0 | 营业利润率 | 营业利润 / 营业收入 | `OPERATING_PROFIT` | `TOTAL_REVENUE` | end | 本期 | consolidated | 分母 0 → missing |

**口径差异 / 待确认：**

- 上述「营业收入」分母在 V1 一律取 `TOTAL_REVENUE`（营业总收入）。分析惯例有时用
  `OPERATING_REVENUE`（营业收入，不含利息/手续费等）。**BUSINESS_CONFIRMATION_REQUIRED**。
- `PROF_ROE` / `PROF_ROA` 分子 V1 取 `NET_PROFIT`（净利润合计）；惯例常用
  `NET_PROFIT_PARENT`（归母净利润）。**BUSINESS_CONFIRMATION_REQUIRED**。
- ROE 期间口径常用「期初期末平均权益」，V1 取期末值。**BUSINESS_CONFIRMATION_REQUIRED**。

## 3. 营运能力（3 项，期初期末平均）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `OPER_ASSET_TURNOVER` | 1.0 | 总资产周转率 | 营收 / 平均总资产 | `TOTAL_REVENUE` | avg(`TOTAL_ASSETS`) | avg | 本期 | consolidated | 缺前期 → missing；分母 0 → missing |
| `OPER_INV_TURNOVER` | 1.0 | 存货周转率 | 成本 / 平均存货 | `OPERATING_COST` | avg(`INVENTORY`) | avg | 本期 | consolidated | 缺前期 → missing；分母 0 → missing |
| `OPER_AR_TURNOVER` | 1.0 | 应收账款周转率 | 营收 / 平均应收 | `TOTAL_REVENUE` | avg(`ACCOUNTS_RECEIVABLE` ∨ `ACCOUNTS_RECEIVABLE_COMBINED`) | avg | 本期 | consolidated | 缺前期 → missing；分母 0 → missing |

**口径差异 / 待确认：**

- 应收账款周转率 V1 优先取 `ACCOUNTS_RECEIVABLE`，缺失时回退 `ACCOUNTS_RECEIVABLE_COMBINED`
  （应收票据及应收账款）。两口径不可直接混比，需明确其一。**BUSINESS_CONFIRMATION_REQUIRED**。
- 期初值依赖「上一报告期同科目」，首个报告期无期初 → 周转率 missing（**期间不足规则**）。
  V2 A2/A3 抽取多期间列时可得前期；仅单期间材料时该三项不可算，属正常缺输入。

## 4. 现金流 / 费用（4 项）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `CASH_OCF_TO_NP` | 1.0 | 经营现金流/净利润 | 经营现金流 / 净利 | `OPERATING_CASH_FLOW` | `NET_PROFIT` | end | 本期 | consolidated | 分母 0 → missing |
| `CASH_OCF_TO_ASSET` | 1.0 | 现金流/总资产 | 经营现金流 / 资产 | `OPERATING_CASH_FLOW` | `TOTAL_ASSETS` | end | 本期 | consolidated | 分母 0 → missing |
| `CASH_OCF_TO_REV` | 1.0 | 现金流/营业收入 | 经营现金流 / 营收 | `OPERATING_CASH_FLOW` | `TOTAL_REVENUE` | end | 本期 | consolidated | 分母 0 → missing |
| `EXP_PERIOD_RATE` | 1.0 | 期间费用率 | 期间费用 / 营收 | `SALES_EXPENSES` + `ADMIN_EXPENSES` + `R_AND_D_EXPENSES` + `FINANCE_EXPENSES` | `TOTAL_REVENUE` | end | 本期 | consolidated | 任一项缺失 → missing；分母 0 → missing |

**口径差异 / 待确认：** 无重大歧义；期间费用范围（是否含财务费用）默认确认即可，但若需细分口径请业务确认。

## 5. 成长（8 项，同比/环比）

| Formula ID | 版本 | 名称 | 表达式 | 科目 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|
| `GROWTH_REVENUE` | 1.0 | 营收增长率 | (本期 − 前期) / \|前期\| | `TOTAL_REVENUE` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_NET_PROFIT` | 1.0 | 净利增长率 | (本期 − 前期) / \|前期\| | `NET_PROFIT` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_ASSET` | 1.0 | 资产增长率 | (本期 − 前期) / \|前期\| | `TOTAL_ASSETS` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_LIABILITY` | 1.0 | 负债增长率 | (本期 − 前期) / \|前期\| | `TOTAL_LIABILITIES` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_EQUITY` | 1.0 | 净资产增长率 | (本期 − 前期) / \|前期\| | `TOTAL_EQUITY` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_OCF` | 1.0 | 经营现金流增长率 | (本期 − 前期) / \|前期\| | `OPERATING_CASH_FLOW` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_ICF` | 1.0 | 投资现金流增长率 | (本期 − 前期) / \|前期\| | `INVESTING_CASH_FLOW` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |
| `GROWTH_FINANCING_CASH_FLOW` | 1.0 | 筹资现金流增长率 | (本期 − 前期) / \|前期\| | `FINANCING_CASH_FLOW` | 同比/环比 | 本期+前期 | consolidated | 前期 0 → missing |

**口径差异 / 待确认：** 分母取绝对值 `|前期|`，负前期仍可算（符号含义需业务确认）；
前期为 0 时视为不可算。年报期同比取「最近上一个年报」。季报期环比（QoQ）存在口径未决：
利润表（营收/净利）与现金流量表（经营/投资/筹资现金流）在季报通常为「年初至今累计值」，
**不得直接用累计值相减作环比**。两种待确认口径（均为 `BUSINESS_CONFIRMATION_REQUIRED`，
确认前成长类季报环比不得进入 A6 实现）：

1. **同期同比（YoY）**：季报一律取「去年同期累计值」同比，规避累计值环比失真；
2. **由累计值推导单季度后环比**：先由连续两期累计值相减得到单季度值，再对单季度值做环比。

## 6. 信用分析关键定义（V1 未实现，A6 需业务确认后再建）

| 定义 | 现状 | 输入可得性 | 结论 |
|---|---|---|---|
| **EBITDA**（税息折旧摊销前利润） | V1 常量 `EBITDA` 已定义但 `metrics.py` **未计算**（仅算 EBIT = 利润总额 + 财务费用） | 折旧/摊销在现金流量表附注，A2/A3 主表**未抽取** | **BUSINESS_CONFIRMATION_REQUIRED** |
| **利息费用**（`INTEREST_EXPENSE`） | V1 用 `FINANCE_EXPENSES` 代替 | `INTEREST_EXPENSE` 在利润表附注，主表通常不可得 | **BUSINESS_CONFIRMATION_REQUIRED** |
| **有息负债** | V1 未定义、未计算 | 短期/长期借款、应付债券、租赁负债可抽取（`SHORT_TERM_BORROWINGS` 等） | **BUSINESS_CONFIRMATION_REQUIRED**（口径需定义） |
| **自由现金流（FCF）** | V1 未定义、未计算 | 需 CAPEX（购建固定资产/无形资产支付的现金），现金流表附注可抽取但主表**未抽取** | **BUSINESS_CONFIRMATION_REQUIRED**（定义 + 输入缺失） |

## 7. 输入科目抽取覆盖核对（A2/A3 mapping 内置规则）

V1 全部公式输入科目（第 1–5 节）在 V2 `financial_v2/mapping.py` 内置规则中**均有对应**
（`CURRENT_ASSETS` / `CURRENT_LIABILITIES` / `TOTAL_ASSETS` / `TOTAL_LIABILITIES` /
`TOTAL_EQUITY` / `INVENTORY` / `TOTAL_REVENUE` / `OPERATING_COST` / `NET_PROFIT` /
`TOTAL_PROFIT` / `FINANCE_EXPENSES` / `OPERATING_PROFIT` / `SALES_EXPENSES` /
`ADMIN_EXPENSES` / `R_AND_D_EXPENSES` / `ACCOUNTS_RECEIVABLE` /
`ACCOUNTS_RECEIVABLE_COMBINED` / `OPERATING_CASH_FLOW` / `INVESTING_CASH_FLOW` /
`FINANCING_CASH_FLOW`）。以上仅证明「输入科目代码在 mapping 内置规则中存在」，**不等于
真实材料已验证可抽取**；本轮未对三张主表做真实 PDF 抽取验收前，不声称「材料可得性满足」。

缺失项仅影响第 6 节的进阶定义（EBITDA 折旧摊销、利息费用、CAPEX）。

## 8. 汇总判定

| 结论 | 数量 | 说明 |
|---|---|---|
| `PROPOSED_DEFAULT`（未经业务确认） | 偿债 3（流动比率 / 资产负债率 / 权益乘数）、盈利 2（净利率 / 营业利润率）、现金流 3、费用 1、成长 8（仅年报同比口径） | 表达式无歧义、输入可得，但**未获业务确认**，不得视为 confirmed |
| `BUSINESS_CONFIRMATION_REQUIRED` | 速动比率、利息保障倍数、毛利率、ROE、ROA、应收账款周转率、EBITDA、利息费用、有息负债、自由现金流、成长类季报环比口径 | 口径有歧义或定义缺失 |

全部公式当前版本均为 V1 基线 `1.0`；进入 A6 Formula Registry 后才分配正式版本号并升版。

## 9. 暂停门结论

- 未实现 A6 Formula Registry / FinancialSnapshot / 指标计算 / A7。
- 未把任何 `PROPOSED_DEFAULT` 或 `BUSINESS_CONFIRMATION_REQUIRED` 项改为 confirmed。
- 待业务对第 1–6 节口径逐项确认后，再进入 A6。
