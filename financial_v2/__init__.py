"""financial_v2：财务来源溯源、对账与集中确认基础（Phase 1F-A）。

本包实现 V2 财务数据三层链路：
- 不可变来源记录（SourceFinancialRecord + 坐标 + 版本）；
- 对账 / 决议（ReconciliationGroup → ResolutionRecord）；
- 财务快照（FinancialSnapshot，唯一获准计算输入）。

硬约束（任务书 §3）：独立 data/financial_v2.db；原始来源记录不可覆盖；
LLM 不算数字；不同期间/币种/单位/scope/报表类型/重述版本不得直接对账。
"""
