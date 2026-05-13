"""SQLite 表结构 + 标准化科目代码常量。

所有 agent 通过代码引用这些常量，不用中文名。
"""

# ── 资产负债表关键项 ──
TOTAL_ASSETS = "资产总计"
CURRENT_ASSETS = "流动资产合计"
NON_CURRENT_ASSETS = "非流动资产合计"
TOTAL_LIABILITIES = "负债合计"
CURRENT_LIABILITIES = "流动负债合计"
NON_CURRENT_LIABILITIES = "非流动负债合计"
TOTAL_EQUITY = "所有者权益合计"
CASH_AND_EQUIVALENTS = "货币资金"
ACCOUNTS_RECEIVABLE = "应收账款"
INVENTORY = "存货"
ACCOUNTS_PAYABLE = "应付账款"
SHORT_TERM_BORROWINGS = "短期借款"
LONG_TERM_BORROWINGS = "长期借款"

# ── 利润表关键项 ──
TOTAL_REVENUE = "营业总收入"
OPERATING_REVENUE = "营业收入"
OPERATING_COST = "营业成本"
SALES_EXPENSES = "销售费用"
ADMIN_EXPENSES = "管理费用"
R_AND_D_EXPENSES = "研发费用"
FINANCE_EXPENSES = "财务费用"
OPERATING_PROFIT = "营业利润"
TOTAL_PROFIT = "利润总额"
INCOME_TAX = "所得税费用"
NET_PROFIT = "净利润"
NET_PROFIT_PARENT = "归属于母公司所有者的净利润"

# ── 现金流量表关键项 ──
OPERATING_CASH_FLOW = "经营活动产生的现金流量净额"
INVESTING_CASH_FLOW = "投资活动产生的现金流量净额"
FINANCING_CASH_FLOW = "筹资活动产生的现金流量净额"
NET_CASH_INCREASE = "现金及现金等价物净增加额"

# ── 派生指标代码（用于 MetricsTable，不对应原始表中字段）──
GROSS_PROFIT = "毛利润"
EBIT = "息税前利润"
EBITDA = "税息折旧摊销前利润"


# ── SQLite DDL ──

DDL_REPORT_META = """
CREATE TABLE IF NOT EXISTS report_meta (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    company_name    TEXT,
    stock_code      TEXT,
    report_period   TEXT NOT NULL,
    report_type     TEXT NOT NULL,
    statement_scope TEXT NOT NULL,
    source_file     TEXT,
    imported_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_FINANCIAL_STATEMENT = """
CREATE TABLE IF NOT EXISTS {table_name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       TEXT NOT NULL REFERENCES report_meta(id),
    item_code       TEXT NOT NULL,
    item_name_cn    TEXT NOT NULL,
    amount          REAL NOT NULL,
    category        TEXT,
    activity_type   TEXT
);
"""
