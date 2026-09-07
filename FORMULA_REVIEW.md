# FORMULA_REVIEW — A2～A5 完成后公式口径复核（暂停门 §15）

> 生成时间：2026-09-07
> 状态：**2026-09-07 已完成本轮业务口径确认；可据本文编写 A6 开发任务书，但本文更新本身不实施 A6。**
> 数据来源：V1 `financial/metrics.py` 现实现 + V2 抽取覆盖（`financial_v2/mapping.py` 内置规则）。

本文件按任务书 §15 逐项列出 V1 现有指标公式、输入科目（V2 标准代码）、期间/scope 口径、
异常规则、材料可得性、与 V1 口径差异。经业务方逐项确认后，首版采用的口径标注为
`BUSINESS_CONFIRMED`；仍受材料可得性约束的代理值和缺失值必须在结果中显式披露，不能由
LLM 补算或把代理口径伪装成精确口径。

## 0. 符号与约定

- **输入科目代码**：与 `financial/schema.py` 常量名一致（V2 `standard_item_code` 同为英文大写）。
- **版本**：本文件全部公式均为 V1 基线版本 `1.0`（「FORMULA_REVIEW 阶段」的冻结口径）。
  A6 Formula Registry 落地时才分配正式版本号并升版；本文件不预注册 A6 版本号，也不把
  任何待确认项提前升版为 confirmed。
- **期间口径**：`end` = 期末时点余额；`flow` = 本报告期流量；`avg` = 期初期末平均余额
  `(期初 + 期末) / 2`，期初 = 上一报告期同科目值；`flow/end`、`flow/avg` 表示分子与
  分母分别采用对应口径。
- **报告期**：比率/周转/费用类取「本期」（同一 `report_period` + `period_type`，annual 或
  quarterly）；正式成长率首版仅计算年报同比（本期年报对最近上一个年报）。同一公式的
  所有输入科目必须来自同一报告期，不得跨期混比。
- **季报累计值限制**：利润表与现金流量表在季报口径通常为「年初至今累计值」。V2 首版不生成
  正式季报增长率，也不将累计值直接相减后当作环比。系统可在研究文字中描述季度表现，或在
  数据完整时推导单季度值作为辅助估算，但必须标记 `ESTIMATED_NON_SCORING`，不得进入正式
  指标表、评分或严谨跨期比较。
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
| `SOLV_QUICK_RATIO` | 1.0 | 速动比率 | (流动资产 − 存货 − 预付款项 − 经确认缺乏流动性的其他流动资产) / 流动负债 | `CURRENT_ASSETS` − `INVENTORY` − `PREPAYMENTS` − qualified(`OTHER_CURRENT_ASSETS`) | `CURRENT_LIABILITIES` | end | 本期 | consolidated | 必需扣除项缺失 → missing；其他流动资产仅在明细确认缺乏流动性时扣除；分母 0 → missing |
| `SOLV_DEBT_RATIO` | 1.0 | 资产负债率 | 负债 / 资产 | `TOTAL_LIABILITIES` | `TOTAL_ASSETS` | end | 本期 | consolidated | 分母 0 → missing |
| `SOLV_INTEREST_COVER` | 1.0 | 利息保障倍数 | 优先：(利润总额 + 利息费用) / 利息费用；代理：(利润总额 + 财务费用) / 财务费用 | `TOTAL_PROFIT` + `INTEREST_EXPENSE`；代理为 `TOTAL_PROFIT` + `FINANCE_EXPENSES` | `INTEREST_EXPENSE`；缺失时代理为 `FINANCE_EXPENSES` | flow | 本期 | consolidated | 精确分母缺失时允许代理并标记 `PROXY_FINANCE_EXPENSES`；代理分母也缺失或分母为 0 → missing |
| `SOLV_EQUITY_MULT` | 1.0 | 权益乘数 | 资产 / 权益 | `TOTAL_ASSETS` | `TOTAL_EQUITY` | end | 本期 | consolidated | 分母 0 → missing |

**已确认口径：**

- `SOLV_INTEREST_COVER` 优先采用真实 `INTEREST_EXPENSE`；未取得时允许使用
  `FINANCE_EXPENSES` 作为近似代理。代理结果必须显式标注，不参与“精确口径”比较。
- `SOLV_QUICK_RATIO` 采用严谨口径。`OTHER_CURRENT_ASSETS` 不能机械全额扣除，只有明细能够
  证明其缺乏快速变现能力时才扣除；必要扣除项无法取得时返回 missing，不假设为 0。
- 本节五项均为 **BUSINESS_CONFIRMED**。

## 2. 盈利能力（5 项）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `PROF_GROSS_MARGIN` | 1.0 | 毛利率 | 毛利 / 营业收入 | `TOTAL_REVENUE` − `OPERATING_COST` | `TOTAL_REVENUE` | flow | 本期 | consolidated | 分母 0 → missing |
| `PROF_NET_MARGIN` | 1.0 | 净利率 | 净利 / 营业收入 | `NET_PROFIT` | `TOTAL_REVENUE` | flow | 本期 | consolidated | 分母 0 → missing |
| `PROF_ROE` | 1.0 | ROE | 净利 / 权益 | `NET_PROFIT` | `TOTAL_EQUITY` | flow/end | 本期 | consolidated | 分母 0 → missing |
| `PROF_ROA` | 1.0 | ROA | 净利 / 资产 | `NET_PROFIT` | `TOTAL_ASSETS` | flow/end | 本期 | consolidated | 分母 0 → missing |
| `PROF_OPER_MARGIN` | 1.0 | 营业利润率 | 营业利润 / 营业收入 | `OPERATING_PROFIT` | `TOTAL_REVENUE` | flow | 本期 | consolidated | 分母 0 → missing |

**已确认口径：**

- 毛利率、净利率、营业利润率及其他以收入为分母的首版指标统一使用 `TOTAL_REVENUE`。
- `PROF_ROE` / `PROF_ROA` 分子统一使用 `NET_PROFIT`。
- `PROF_ROE` 使用期末 `TOTAL_EQUITY`，`PROF_ROA` 使用期末 `TOTAL_ASSETS`；结果分别注明
  “期末净资产口径”和“期末总资产口径”，不得称为加权平均 ROE/ROA。
- 本节五项均为 **BUSINESS_CONFIRMED**。

## 3. 营运能力（3 项，期初期末平均）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `OPER_ASSET_TURNOVER` | 1.0 | 总资产周转率 | 营收 / 平均总资产 | `TOTAL_REVENUE` | avg(`TOTAL_ASSETS`) | flow/avg | 本期 | consolidated | 缺前期 → missing；分母 0 → missing |
| `OPER_INV_TURNOVER` | 1.0 | 存货周转率 | 成本 / 平均存货 | `OPERATING_COST` | avg(`INVENTORY`) | flow/avg | 本期 | consolidated | 缺前期 → missing；分母 0 → missing |
| `OPER_AR_TURNOVER` | 1.0 | 应收账款周转率 | 营收 / 平均应收 | `TOTAL_REVENUE` | 优先 avg(`ACCOUNTS_RECEIVABLE`)；同口径两期均缺时整体回退 avg(`ACCOUNTS_RECEIVABLE_COMBINED`) | flow/avg | 本期 | consolidated | 无法取得同一科目口径的期初、期末余额 → missing；禁止两期混用不同科目；分母 0 → missing |

**已确认口径：**

- 应收账款周转率优先使用本期和期初均存在的 `ACCOUNTS_RECEIVABLE`。仅当两个时点均无法
  取得该科目、但均存在 `ACCOUNTS_RECEIVABLE_COMBINED` 时，整组回退组合口径。不得一端用
  单独应收账款、另一端用组合科目，也不因“最新一期有值”而跨口径计算。
- 期初值依赖「上一报告期同科目」，首个报告期无期初 → 周转率 missing（**期间不足规则**）。
  V2 A2/A3 抽取多期间列时可得前期；仅单期间材料时该三项不可算，属正常缺输入。
- 本节三项均为 **BUSINESS_CONFIRMED**。

## 4. 现金流 / 费用（4 项）

| Formula ID | 版本 | 名称 | 表达式 | 分子 | 分母 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|---|
| `CASH_OCF_TO_NP` | 1.0 | 经营现金流/净利润 | 经营现金流 / 净利 | `OPERATING_CASH_FLOW` | `NET_PROFIT` | flow | 本期 | consolidated | 分母 0 → missing |
| `CASH_OCF_TO_ASSET` | 1.0 | 现金流/总资产 | 经营现金流 / 资产 | `OPERATING_CASH_FLOW` | `TOTAL_ASSETS` | flow/end | 本期 | consolidated | 分母 0 → missing |
| `CASH_OCF_TO_REV` | 1.0 | 现金流/营业收入 | 经营现金流 / 营收 | `OPERATING_CASH_FLOW` | `TOTAL_REVENUE` | flow | 本期 | consolidated | 分母 0 → missing |
| `EXP_PERIOD_RATE` | 1.0 | 期间费用率 | 期间费用 / 营收 | `SALES_EXPENSES` + `ADMIN_EXPENSES` + `R_AND_D_EXPENSES` + `FINANCE_EXPENSES` | `TOTAL_REVENUE` | flow | 本期 | consolidated | 任一项缺失 → missing；分母 0 → missing |

**已确认口径：** 四项按表中定义执行；期间费用包含财务费用。本节四项均为
**BUSINESS_CONFIRMED**。

## 5. 成长（8 项，正式指标仅年报同比）

| Formula ID | 版本 | 名称 | 表达式 | 科目 | 期间口径 | 报告期 | scope | 异常规则 |
|---|---|---|---|---|---|---|---|---|
| `GROWTH_REVENUE` | 1.0 | 营收增长率 | (本年 − 上年) / \|上年\| | `TOTAL_REVENUE` | 年报同比 | 本年+上年 | consolidated | 上年 0 → missing |
| `GROWTH_NET_PROFIT` | 1.0 | 净利增长率 | (本年 − 上年) / \|上年\| | `NET_PROFIT` | 年报同比 | 本年+上年 | consolidated | 上年 0 → missing |
| `GROWTH_ASSET` | 1.0 | 资产增长率 | (本年末 − 上年末) / \|上年末\| | `TOTAL_ASSETS` | 年报同比 | 本年末+上年末 | consolidated | 上年末 0 → missing |
| `GROWTH_LIABILITY` | 1.0 | 负债增长率 | (本年末 − 上年末) / \|上年末\| | `TOTAL_LIABILITIES` | 年报同比 | 本年末+上年末 | consolidated | 上年末 0 → missing |
| `GROWTH_EQUITY` | 1.0 | 净资产增长率 | (本年末 − 上年末) / \|上年末\| | `TOTAL_EQUITY` | 年报同比 | 本年末+上年末 | consolidated | 上年末 0 → missing |
| `GROWTH_OCF` | 1.0 | 经营现金流增长率 | (本年 − 上年) / \|上年\| | `OPERATING_CASH_FLOW` | 年报同比 | 本年+上年 | consolidated | 上年 0 → missing |
| `GROWTH_ICF` | 1.0 | 投资现金流增长率 | (本年 − 上年) / \|上年\| | `INVESTING_CASH_FLOW` | 年报同比 | 本年+上年 | consolidated | 上年 0 → missing |
| `GROWTH_FINANCING_CASH_FLOW` | 1.0 | 筹资现金流增长率 | (本年 − 上年) / \|上年\| | `FINANCING_CASH_FLOW` | 年报同比 | 本年+上年 | consolidated | 上年 0 → missing |

**已确认口径：** 正式成长指标首版仅计算年报同比。分母取绝对值 `|上年|`；上年为负数时
仍可计算，但必须保留负基数提示，上年为 0 时返回 missing。季报不生成正式增长率；由累计值
推导的单季度表现只能作为 `ESTIMATED_NON_SCORING` 辅助研判。本节八项均为
**BUSINESS_CONFIRMED**。

## 6. 信用分析关键定义（V1 未实现，业务定义已确认）

| 定义 | 现状 | 输入可得性 | 结论 |
|---|---|---|---|
| **EBITDA**（税息折旧摊销前利润） | `TOTAL_PROFIT + INTEREST_EXPENSE + DEPRECIATION + AMORTIZATION` | 折旧/摊销等输入未可靠抽取时返回 `MISSING_INPUT` | **BUSINESS_CONFIRMED** |
| **利息费用**（`INTEREST_EXPENSE`） | 采用报表或附注明确披露的真实利息费用；`FINANCE_EXPENSES` 仅可作为利息保障倍数的代理输入，不改变本定义 | 主表不可得时继续检索附注；仍缺失则 `MISSING_INPUT` | **BUSINESS_CONFIRMED** |
| **有息负债** | 短期借款 + 长期借款 + 应付债券 + 租赁负债 + 一年内到期的有息非流动负债；其他项目仅在明细确认计息时纳入 | 任一合计项混含非有息部分且无法拆分时标记 `PARTIAL_INPUT`，披露已纳入范围 | **BUSINESS_CONFIRMED** |
| **自由现金流（FCF）** | `OPERATING_CASH_FLOW − CAPEX`；CAPEX 取购建固定资产、无形资产和其他长期资产支付的现金 | CAPEX 未可靠抽取时返回 `MISSING_INPUT` | **BUSINESS_CONFIRMED** |

以上定义已经业务确认，但 A6 只能在相应输入可追溯且完整时计算；输入缺失不得由 LLM 估算。

## 7. 输入科目抽取覆盖核对（A2/A3 mapping 内置规则）

V1 全部公式输入科目（第 1–5 节）在 V2 `financial_v2/mapping.py` 内置规则中**均有对应**
（`CURRENT_ASSETS` / `CURRENT_LIABILITIES` / `TOTAL_ASSETS` / `TOTAL_LIABILITIES` /
`TOTAL_EQUITY` / `INVENTORY` / `TOTAL_REVENUE` / `OPERATING_COST` / `NET_PROFIT` /
`TOTAL_PROFIT` / `FINANCE_EXPENSES` / `OPERATING_PROFIT` / `SALES_EXPENSES` /
`ADMIN_EXPENSES` / `R_AND_D_EXPENSES` / `ACCOUNTS_RECEIVABLE` /
`ACCOUNTS_RECEIVABLE_COMBINED` / `OPERATING_CASH_FLOW` / `INVESTING_CASH_FLOW` /
`FINANCING_CASH_FLOW`）。以上仅证明「输入科目代码在 mapping 内置规则中存在」，**不等于
真实材料已验证可抽取**；本轮未对三张主表做真实 PDF 抽取验收前，不声称「材料可得性满足」。

缺失项影响第 6 节的进阶定义（EBITDA 折旧摊销、利息费用、CAPEX）；缺失时按 §6 返回
`MISSING_INPUT`/`PARTIAL_INPUT`，不阻止其他输入完整的指标计算。

## 8. 汇总判定

| 结论 | 数量 | 说明 |
|---|---|---|
| `BUSINESS_CONFIRMED` | 第 1～5 节 25 项指标口径；第 6 节 EBITDA、利息费用、有息负债、自由现金流定义 | 已由业务方确认；A6 仍须执行输入完整性、代理标识和缺失规则 |
| `NOT_IMPLEMENTED_AS_FORMAL_METRIC` | 季报增长率/环比 | 首版不进入正式指标表或评分；仅允许 `ESTIMATED_NON_SCORING` 辅助研判 |

全部公式当前版本均为 V1 基线 `1.0`；进入 A6 Formula Registry 后才分配正式版本号并升版。

## 9. 暂停门结论

- 本轮仅完成业务口径确认，尚未实现 A6 Formula Registry / FinancialSnapshot / 指标计算 / A7。
- 第 1～6 节口径已确认，可据此编写 A6 开发任务书。
- A6 必须保留精确值/代理值/缺失值的状态差异，禁止 LLM 补算；季报增长不得进入正式指标。
