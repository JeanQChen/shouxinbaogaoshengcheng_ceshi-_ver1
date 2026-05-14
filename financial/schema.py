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
FIXED_ASSETS = "固定资产"
CONSTRUCTION_IN_PROGRESS = "在建工程"
INTANGIBLE_ASSETS = "无形资产"
GOODWILL = "商誉"
LONG_TERM_DEFERRED_EXPENSES = "长期待摊费用"
LONG_TERM_EQUITY_INVEST = "长期股权投资"
RIGHT_OF_USE_ASSET = "使用权资产"
DEFERRED_TAX_ASSETS = "递延所得税资产"
DEFERRED_TAX_LIABILITIES = "递延所得税负债"
DEFERRED_INCOME = "递延收益"
OTHER_RECEIVABLES = "其他应收款"
OTHER_PAYABLES = "其他应付款"
INTEREST_RECEIVABLE = "应收利息"
INTEREST_PAYABLE = "应付利息"
DIVIDEND_RECEIVABLE = "应收股利"
DIVIDEND_PAYABLE = "应付股利"
NON_CURRENT_ASSET_DUE_WITHIN_ONE_YEAR = "一年内到期的非流动资产"
NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR = "一年内到期的非流动负债"
TRADING_FINANCIAL_ASSETS = "交易性金融资产"
TRADING_FINANCIAL_LIABILITIES = "交易性金融负债"
DERIVATIVE_FINANCIAL_ASSETS = "衍生金融资产"
DERIVATIVE_FINANCIAL_LIABILITIES = "衍生金融负债"
OTHER_CURRENT_ASSETS = "其他流动资产"
OTHER_NON_CURRENT_ASSETS = "其他非流动资产"
OTHER_CURRENT_LIABILITIES = "其他流动负债"
OTHER_NON_CURRENT_LIABILITIES = "其他非流动负债"
OTHER_EQUITY_INSTRUMENT_INVEST = "其他权益工具投资"
OTHER_NON_CURRENT_FINANCIAL_ASSETS = "其他非流动金融资产"
OTHER_COMPREHENSIVE_INCOME = "其他综合收益"
AVAILABLE_FOR_SALE_FINANCIAL_ASSETS = "可供出售金融资产"
ASSETS_HELD_FOR_SALE = "持有待售资产"
CONTRACT_ASSETS = "合同资产"
CONTRACT_LIABILITIES = "合同负债"
LEASE_LIABILITIES = "租赁负债"
PREPAYMENTS = "预付款项"
ADVANCES_FROM_CUSTOMERS = "预收款项"
PROVISIONS = "预计负债"
BONDS_PAYABLE = "应付债券"
EMPLOYEE_BENEFITS_PAYABLE = "应付职工薪酬"
TAXES_PAYABLE = "应交税费"
LONG_TERM_RECEIVABLES = "长期应收款"
LONG_TERM_PAYABLES = "长期应付款"
PAID_IN_CAPITAL = "实收资本"
CAPITAL_RESERVE = "资本公积"
SURPLUS_RESERVE = "盈余公积"
UNDISTRIBUTED_PROFIT = "未分配利润"
TREASURY_STOCK = "库存股"
SPECIAL_RESERVE = "专项储备"
MINORITY_INTEREST = "少数股东权益"
TOTAL_LIABILITIES_AND_EQUITY = "负债和所有者权益总计"

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


# ── 代码 ↔ 中文名 互查 ──

_CODE_MAP: dict[str, str] | None = None
_CN_MAP: dict[str, str] | None = None


def _build_maps() -> None:
    global _CODE_MAP, _CN_MAP
    if _CODE_MAP is not None:
        return
    _CODE_MAP = {}
    _CN_MAP = {}
    for name, value in list(globals().items()):
        if name.isupper() and isinstance(value, str) and not name.startswith("DDL"):
            _CODE_MAP[name] = value
            _CN_MAP[value] = name


def _ensure_maps() -> None:
    _build_maps()
    assert _CODE_MAP is not None
    assert _CN_MAP is not None


def get_cn_name(code: str) -> str:
    """code → 中文名，用于 prompt 中解释标准代码。"""
    _ensure_maps()
    return _CODE_MAP.get(code, code)  # type: ignore[union-attr]


def get_code_by_cn(cn: str) -> str | None:
    """中文名 → code。"""
    _ensure_maps()
    return _CN_MAP.get(cn)  # type: ignore[union-attr]


def get_all_codes() -> dict[str, str]:
    """返回 {CODE: 中文名} 全部映射，用于 LLM prompt。"""
    _ensure_maps()
    return dict(_CODE_MAP)  # type: ignore[arg-type]


# ── 表名 ──

TABLE_BALANCE_SHEET = "balance_sheet"
TABLE_INCOME_STATEMENT = "income_statement"
TABLE_CASH_FLOW = "cash_flow"
ALL_STATEMENT_TABLES = (TABLE_BALANCE_SHEET, TABLE_INCOME_STATEMENT, TABLE_CASH_FLOW)


# ── 科目 → 归属表 路由 ──

_BALANCE_CATEGORIES = {"asset", "liability", "equity"}
_INCOME_CATEGORIES = {"revenue", "cost", "profit"}
_CASH_ACTIVITIES = {"operating", "investing", "financing"}


def table_for_row(category: str | None, activity_type: str | None) -> str:
    """根据 category 和 activity_type 决定 NormalizedRow 写入哪张表。"""
    if category in _BALANCE_CATEGORIES:
        return TABLE_BALANCE_SHEET
    if category in _INCOME_CATEGORIES:
        return TABLE_INCOME_STATEMENT
    if activity_type in _CASH_ACTIVITIES:
        return TABLE_CASH_FLOW
    return TABLE_BALANCE_SHEET  # fallback


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


def build_all_ddl() -> str:
    """返回完整的建表 SQL（report_meta + 三张财务表）。"""
    parts = [DDL_REPORT_META]
    for t in ALL_STATEMENT_TABLES:
        parts.append(DDL_FINANCIAL_STATEMENT.format(table_name=t))
    return "\n".join(parts)
