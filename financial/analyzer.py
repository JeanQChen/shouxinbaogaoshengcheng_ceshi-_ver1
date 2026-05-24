"""Agent 2: 财务分析。

流程：SQL 取数 → Python 算指标 → LLM 写解读文字。
LLM 不计算任何数字，所有指标提前算好喂入 prompt。
"""

import logging
from datetime import datetime

from financial.db import init_db, list_periods, query_metric
from financial.metrics import compute_all, MetricsTable
from retrieval.retriever import retrieve
from financial.schema import (
    TOTAL_ASSETS, CURRENT_ASSETS, NON_CURRENT_ASSETS,
    TOTAL_LIABILITIES, CURRENT_LIABILITIES, NON_CURRENT_LIABILITIES,
    TOTAL_EQUITY, MINORITY_INTEREST,
    CASH_AND_EQUIVALENTS, INVENTORY,
    ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED, NOTES_RECEIVABLE,
    ACCOUNTS_PAYABLE, SHORT_TERM_BORROWINGS, LONG_TERM_BORROWINGS,
    FIXED_ASSETS, CONSTRUCTION_IN_PROGRESS, INTANGIBLE_ASSETS, GOODWILL,
    LONG_TERM_EQUITY_INVEST, RIGHT_OF_USE_ASSET,
    DEFERRED_TAX_ASSETS, DEFERRED_TAX_LIABILITIES,
    TRADING_FINANCIAL_ASSETS, OTHER_CURRENT_ASSETS, OTHER_NON_CURRENT_ASSETS,
    CONTRACT_LIABILITIES, LEASE_LIABILITIES,
    EMPLOYEE_BENEFITS_PAYABLE, TAXES_PAYABLE, OTHER_PAYABLES,
    OTHER_CURRENT_LIABILITIES, PROVISIONS, DEFERRED_INCOME,
    TOTAL_LIABILITIES_AND_EQUITY,
    TOTAL_REVENUE, OPERATING_COST, TOTAL_OPERATING_COST,
    NET_PROFIT, NET_PROFIT_PARENT, TOTAL_PROFIT, OPERATING_PROFIT,
    SALES_EXPENSES, ADMIN_EXPENSES, R_AND_D_EXPENSES, FINANCE_EXPENSES,
    TAXES_AND_SURCHARGES, INVESTMENT_INCOME, FAIR_VALUE_CHANGE_GAINS,
    CREDIT_IMPAIRMENT_LOSS, ASSET_IMPAIRMENT_LOSS,
    ASSET_DISPOSAL_INCOME, OTHER_INCOME,
    NON_OPERATING_INCOME, NON_OPERATING_EXPENSES, INCOME_TAX,
    MINORITY_INTEREST_PROFIT,
    OPERATING_CASH_FLOW, INVESTING_CASH_FLOW, FINANCING_CASH_FLOW,
    NET_CASH_INCREASE,
    CASH_RECEIVED_FROM_SALES, CASH_PAID_FOR_GOODS,
    BEGINNING_CASH_BALANCE, ENDING_CASH_BALANCE,
)
from llm.client import chat, load_prompt
from reporting.template import SectionSpec, ReportSection

logger = logging.getLogger(__name__)


def _format_metrics_table(m: MetricsTable) -> str:
    """将 MetricsTable 格式化为 LLM 可读的 Markdown 表格。"""
    if not m.by_period:
        return "（无数据）"

    periods = sorted(m.by_period.keys())
    all_metrics = list(m.by_period[periods[0]].keys())

    lines = ["| 指标 | " + " | ".join(periods) + " |"]
    lines.append("|------" + "|------" * len(periods) + "|")

    for metric in all_metrics:
        vals = []
        for p in periods:
            v = m.by_period[p].get(metric)
            if v is None:
                vals.append("N/A")
            else:
                vals.append(f"{v:.4f}")
        lines.append(f"| {metric} | " + " | ".join(vals) + " |")

    return "\n".join(lines)


def _format_growth_table(m: MetricsTable) -> str:
    """将 yoy_changes 格式化为 Markdown 表格。"""
    if not m.yoy_changes:
        return "（无增长率数据）"

    periods = sorted(m.yoy_changes.keys())
    all_growth = list(m.yoy_changes[periods[0]].keys())

    lines = ["| 指标 | " + " | ".join(periods) + " |"]
    lines.append("|------" + "|------" * len(periods) + "|")

    for metric in all_growth:
        vals = []
        for p in periods:
            v = m.yoy_changes[p].get(metric)
            if v is None:
                vals.append("N/A")
            else:
                vals.append(f"{v:+.2%}")
        lines.append(f"| {metric} | " + " | ".join(vals) + " |")

    return "\n".join(lines)


# 需要在 prompt 中展示的关键科目（分报表展示）
_BALANCE_ITEMS = [
    # 大类
    ("资产总计", TOTAL_ASSETS),
    ("流动资产合计", CURRENT_ASSETS),
    ("非流动资产合计", NON_CURRENT_ASSETS),
    ("负债合计", TOTAL_LIABILITIES),
    ("流动负债合计", CURRENT_LIABILITIES),
    ("非流动负债合计", NON_CURRENT_LIABILITIES),
    ("所有者权益合计", TOTAL_EQUITY),
    ("负债和所有者权益总计", TOTAL_LIABILITIES_AND_EQUITY),
    # 资产重要科目
    ("货币资金", CASH_AND_EQUIVALENTS),
    ("交易性金融资产", TRADING_FINANCIAL_ASSETS),
    ("应收票据", NOTES_RECEIVABLE),
    ("应收账款", ACCOUNTS_RECEIVABLE),
    ("存货", INVENTORY),
    ("其他流动资产", OTHER_CURRENT_ASSETS),
    ("固定资产", FIXED_ASSETS),
    ("在建工程", CONSTRUCTION_IN_PROGRESS),
    ("使用权资产", RIGHT_OF_USE_ASSET),
    ("无形资产", INTANGIBLE_ASSETS),
    ("商誉", GOODWILL),
    ("长期股权投资", LONG_TERM_EQUITY_INVEST),
    ("递延所得税资产", DEFERRED_TAX_ASSETS),
    ("其他非流动资产", OTHER_NON_CURRENT_ASSETS),
    # 负债重要科目
    ("短期借款", SHORT_TERM_BORROWINGS),
    ("应付账款", ACCOUNTS_PAYABLE),
    ("合同负债", CONTRACT_LIABILITIES),
    ("应付职工薪酬", EMPLOYEE_BENEFITS_PAYABLE),
    ("应交税费", TAXES_PAYABLE),
    ("其他应付款", OTHER_PAYABLES),
    ("其他流动负债", OTHER_CURRENT_LIABILITIES),
    ("长期借款", LONG_TERM_BORROWINGS),
    ("租赁负债", LEASE_LIABILITIES),
    ("递延所得税负债", DEFERRED_TAX_LIABILITIES),
    ("递延收益", DEFERRED_INCOME),
    ("预计负债", PROVISIONS),
    # 权益
    ("少数股东权益", MINORITY_INTEREST),
]

_INCOME_ITEMS = [
    ("营业总收入", TOTAL_REVENUE),
    ("营业总成本", TOTAL_OPERATING_COST),
    ("营业成本", OPERATING_COST),
    ("税金及附加", TAXES_AND_SURCHARGES),
    ("销售费用", SALES_EXPENSES),
    ("管理费用", ADMIN_EXPENSES),
    ("研发费用", R_AND_D_EXPENSES),
    ("财务费用", FINANCE_EXPENSES),
    ("投资收益", INVESTMENT_INCOME),
    ("公允价值变动收益", FAIR_VALUE_CHANGE_GAINS),
    ("信用减值损失", CREDIT_IMPAIRMENT_LOSS),
    ("资产减值损失", ASSET_IMPAIRMENT_LOSS),
    ("资产处置收益", ASSET_DISPOSAL_INCOME),
    ("其他收益", OTHER_INCOME),
    ("营业利润", OPERATING_PROFIT),
    ("营业外收入", NON_OPERATING_INCOME),
    ("营业外支出", NON_OPERATING_EXPENSES),
    ("利润总额", TOTAL_PROFIT),
    ("所得税费用", INCOME_TAX),
    ("净利润", NET_PROFIT),
    ("归母净利润", NET_PROFIT_PARENT),
    ("少数股东损益", MINORITY_INTEREST_PROFIT),
]

_CASH_ITEMS = [
    ("销售商品、提供劳务收到的现金", CASH_RECEIVED_FROM_SALES),
    ("购买商品、接受劳务支付的现金", CASH_PAID_FOR_GOODS),
    ("经营活动现金流量净额", OPERATING_CASH_FLOW),
    ("投资活动现金流量净额", INVESTING_CASH_FLOW),
    ("筹资活动现金流量净额", FINANCING_CASH_FLOW),
    ("现金及现金等价物净增加额", NET_CASH_INCREASE),
    ("期初现金及现金等价物余额", BEGINNING_CASH_BALANCE),
    ("期末现金及现金等价物余额", ENDING_CASH_BALANCE),
]

# ── 科目明细检索 ──

# 可能需要审计报告附注明细的科目（列入可检索范围）
_AUDIT_NOTE_ACCOUNTS: list[tuple[str, str]] = [
    ("货币资金", CASH_AND_EQUIVALENTS),
    ("应收账款", ACCOUNTS_RECEIVABLE),
    ("存货", INVENTORY),
    ("固定资产", FIXED_ASSETS),
    ("在建工程", CONSTRUCTION_IN_PROGRESS),
    ("无形资产", INTANGIBLE_ASSETS),
    ("商誉", GOODWILL),
    ("长期股权投资", LONG_TERM_EQUITY_INVEST),
    ("短期借款", SHORT_TERM_BORROWINGS),
    ("长期借款", LONG_TERM_BORROWINGS),
    ("应付账款", ACCOUNTS_PAYABLE),
    ("合同负债", CONTRACT_LIABILITIES),
    ("其他应付款", OTHER_PAYABLES),
    ("其他流动资产", OTHER_CURRENT_ASSETS),
    ("使用权资产", RIGHT_OF_USE_ASSET),
]

_ACCOUNT_QUERY_TPL: dict[str, str] = {
    "货币资金": "{account} {year} 受限资金 保证金 银行存款 占比 明细",
    "应收账款": "{account} {year} 前五大欠款方 账龄 坏账准备 计提 明细",
    "存货": "{account} {year} 库龄 跌价准备 发出商品 在产品 明细",
    "固定资产": "{account} {year} 折旧 成新率 闲置 减值 明细",
    "在建工程": "{account} {year} 项目 进度 转入固定资产 明细",
    "无形资产": "{account} {year} 构成 摊销 减值 明细",
    "商誉": "{account} {year} 减值测试 可收回金额 现金流预测 明细",
    "长期股权投资": "{account} {year} 被投资单位 持股比例 权益法 明细",
    "短期借款": "{account} {year} 信用借款 保证借款 质押借款 利率 明细",
    "长期借款": "{account} {year} 抵押 质押 担保 到期日 利率 明细",
    "应付账款": "{account} {year} 账龄 供应商 集中度 明细",
    "合同负债": "{account} {year} 预收款项 账龄 性质 明细",
    "其他应付款": "{account} {year} 性质 账龄 关联方 明细",
    "其他流动资产": "{account} {year} 构成 明细 分类",
    "使用权资产": "{account} {year} 租赁 折旧 明细",
}


def _fetch_balance(company_id: str, code: str, period: str) -> float | None:
    """查询单科目余额，含 AR 回退。"""
    v = query_metric(company_id, code, period)
    if v is None and code == ACCOUNTS_RECEIVABLE:
        v = query_metric(company_id, ACCOUNTS_RECEIVABLE_COMBINED, period)
    return v


def _get_priority_accounts(company_id: str, periods: list[str]) -> list[dict]:
    """筛选需要做科目明细检索的重点科目。

    入选条件（满足任一即可）：
      1. 最新期余额占总资产 > 10%
      2. 相比上一期同比变动 > 30%
    """
    if len(periods) < 1:
        return []

    latest = periods[-1]
    total_assets = query_metric(company_id, TOTAL_ASSETS, latest)
    if total_assets is None or total_assets == 0:
        return []

    prev = periods[-2] if len(periods) >= 2 else None

    priority: list[dict] = []
    for name, code in _AUDIT_NOTE_ACCOUNTS:
        balance = _fetch_balance(company_id, code, latest)
        if balance is None:
            continue

        pct = balance / total_assets * 100

        yoy_pct: float | None = None
        if prev:
            prev_balance = _fetch_balance(company_id, code, prev)
            if prev_balance and prev_balance != 0:
                yoy_pct = (balance - prev_balance) / abs(prev_balance) * 100

        if pct > 10 or (yoy_pct is not None and abs(yoy_pct) > 30):
            priority.append({
                "name": name,
                "code": code,
                "balance_yi": balance / 100_000_000,
                "pct_of_assets": pct,
                "yoy_pct": yoy_pct,
            })

    priority.sort(key=lambda x: x["balance_yi"] or 0, reverse=True)
    return priority


def _retrieve_account_details(
    company_id: str,
    priority_accounts: list[dict],
    periods: list[str],
) -> str:
    """RAG 检索审计报告附注中的科目明细。

    限制：
      - 每科目检索 k=4，取 top-2（按相似度）
      - 每 chunk 截断到 200 字
      - 总输出硬上限 6000 字符
    """
    if not priority_accounts:
        return "（无重点关注科目，跳过科目明细分析。）"

    # 用最新年报期年份（审计报告才有附注），避免用 Q1 等季报年份
    annuals = sorted(p for p in periods if p.endswith("-12-31"))
    year = annuals[-1][:4] if annuals else periods[-1][:4]

    parts: list[str] = []
    total_chars = 0
    MAX_TOTAL = 6000

    for acc in priority_accounts:
        name = acc["name"]
        tpl = _ACCOUNT_QUERY_TPL.get(name, "{account} {year} 明细 附注")
        query = tpl.format(account=name, year=f"{year}年")

        try:
            chunks = retrieve(company_id, "company_docs", query, k=4)
        except ValueError:
            logger.debug("Collection not found for account detail retrieval")
            continue

        if not chunks:
            continue

        # 表头
        header = f"\n#### {name}（占总资产 {acc['pct_of_assets']:.1f}%"
        if acc["yoy_pct"] is not None and abs(acc["yoy_pct"]) > 0.05:
            direction = "+" if acc["yoy_pct"] > 0 else ""
            header += f"，同比 {direction}{acc['yoy_pct']:.1f}%"
        header += "）"

        if total_chars + len(header) > MAX_TOTAL:
            break
        parts.append(header)
        total_chars += len(header)

        kept = 0
        for c in chunks:
            if kept >= 2:
                break
            snippet = c.text.strip()[:200]
            if not snippet:
                continue
            ref = f"- [来源: {c.source_file or '未知'}, 第{c.page_number}页] {snippet}"
            if total_chars + len(ref) > MAX_TOTAL:
                break
            parts.append(ref)
            total_chars += len(ref)
            kept += 1

        if kept == 0:
            parts[-1] += " *(无有效检索结果)*"

    if not parts:
        return "（审计报告附注检索无结果，跳过科目明细分析。）"

    return "\n".join(parts)


def _format_balances_table(company_id: str, periods: list[str]) -> str:
    """格式化主要科目余额为 Markdown 表格，分三张表展示。

    金额单位转换为亿元，便于阅读。
    """
    def _fetch(code: str) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for p in periods:
            v = query_metric(company_id, code, p)
            # AR 回退
            if v is None and code == ACCOUNTS_RECEIVABLE:
                v = query_metric(company_id, ACCOUNTS_RECEIVABLE_COMBINED, p)
            result[p] = v / 100_000_000 if v is not None else None
        return result

    def _fmt(v: float | None) -> str:
        if v is None:
            return "N/A"
        return f"{v:,.2f}"

    def _build_table(title: str, items: list[tuple[str, str]]) -> str:
        lines = [f"### {title}", ""]
        lines.append("| 科目 | " + " | ".join(periods) + " |")
        lines.append("|------" + "|------" * len(periods) + "|")
        for label, code in items:
            vals = _fetch(code)
            row = " | ".join(_fmt(vals.get(p)) for p in periods)
            lines.append(f"| {label} | {row} |")
        return "\n".join(lines)

    parts = [
        _build_table("资产负债表（亿元）", _BALANCE_ITEMS),
        _build_table("利润表（亿元）", _INCOME_ITEMS),
        _build_table("现金流量表（亿元）", _CASH_ITEMS),
    ]
    return "\n\n".join(parts)


def _compute_oversized_items(company_id: str, periods: list[str]) -> str:
    """检测单科目占合计超过 20% 的重点科目，返回 Markdown 列表。

    分三张表检查：
      - 资产科目 / 资产总计
      - 负债科目 / 负债合计
      - 利润表科目 / 营业总收入（收入类）或营业总成本（成本类）
    """
    if not periods:
        return "（无数据，跳过。）"

    latest = periods[-1]
    total_assets = query_metric(company_id, TOTAL_ASSETS, latest)
    total_liabilities = query_metric(company_id, TOTAL_LIABILITIES, latest)
    total_revenue = query_metric(company_id, TOTAL_REVENUE, latest)
    total_cost = query_metric(company_id, TOTAL_OPERATING_COST, latest)

    oversized: list[str] = []

    # 资产科目
    if total_assets and total_assets > 0:
        for label, code in _BALANCE_ITEMS:
            if code in (TOTAL_ASSETS, CURRENT_ASSETS, NON_CURRENT_ASSETS,
                        TOTAL_LIABILITIES, CURRENT_LIABILITIES, NON_CURRENT_LIABILITIES,
                        TOTAL_EQUITY, TOTAL_LIABILITIES_AND_EQUITY, MINORITY_INTEREST):
                continue  # 跳过合计项和权益小计
            v = _fetch_balance(company_id, code, latest)
            if v and abs(v) / abs(total_assets) > 0.20:
                pct = abs(v) / abs(total_assets) * 100
                oversized.append(f"- **{label}**：占资产总计 {pct:.1f}%（{v/100_000_000:,.2f} 亿元），"
                                f"需单独分析其构成和变动含义。")

    # 负债科目
    if total_liabilities and total_liabilities > 0:
        liability_codes = {
            "短期借款": SHORT_TERM_BORROWINGS,
            "应付账款": ACCOUNTS_PAYABLE,
            "合同负债": CONTRACT_LIABILITIES,
            "应付职工薪酬": EMPLOYEE_BENEFITS_PAYABLE,
            "应交税费": TAXES_PAYABLE,
            "其他应付款": OTHER_PAYABLES,
            "其他流动负债": OTHER_CURRENT_LIABILITIES,
            "长期借款": LONG_TERM_BORROWINGS,
            "租赁负债": LEASE_LIABILITIES,
            "递延所得税负债": DEFERRED_TAX_LIABILITIES,
            "递延收益": DEFERRED_INCOME,
            "预计负债": PROVISIONS,
        }
        for label, code in liability_codes.items():
            v = _fetch_balance(company_id, code, latest)
            if v and abs(v) / abs(total_liabilities) > 0.20:
                pct = abs(v) / abs(total_liabilities) * 100
                oversized.append(f"- **{label}**：占负债合计 {pct:.1f}%（{v/100_000_000:,.2f} 亿元），"
                                f"需单独分析其结构和偿债影响。")

    # 利润表科目（收入/成本类）
    if total_revenue and total_revenue > 0:
        income_detail = {
            "营业成本": OPERATING_COST,
            "销售费用": SALES_EXPENSES,
            "管理费用": ADMIN_EXPENSES,
            "研发费用": R_AND_D_EXPENSES,
            "财务费用": FINANCE_EXPENSES,
            "投资收益": INVESTMENT_INCOME,
            "信用减值损失": CREDIT_IMPAIRMENT_LOSS,
        }
        for label, code in income_detail.items():
            v = query_metric(company_id, code, latest)
            if v and abs(v) / abs(total_revenue) > 0.20:
                pct = abs(v) / abs(total_revenue) * 100
                oversized.append(f"- **{label}**：占营业总收入 {pct:.1f}%（{v/100_000_000:,.2f} 亿元），"
                                f"需单独分析其变动原因及对利润的影响。")

    if not oversized:
        return "（最新一期无单科目占比超过 20% 的项目，无需单独分析。）"

    return "\n".join(oversized)


def run(company_id: str, section_spec: SectionSpec) -> ReportSection:
    """执行财务分析 agent。

    1. 调用 financial.metrics.compute_all 拿到指标表
    2. 对重点科目从 ChromaDB 检索审计报告附注明细
    3. 把指标表 + 科目明细 + guidance 喂给 LLM
    4. LLM 只写解读文字，不算新指标
    """
    init_db()
    periods = list_periods(company_id)

    if not periods:
        return ReportSection(
            section_id=section_spec.section_id,
            title=section_spec.title,
            content="*无法生成：数据库中没有该公司的财务数据。*",
            citations=[],
            generated_by="financial_analyzer",
        )

    metrics = compute_all(company_id, periods)
    balances = _format_balances_table(company_id, periods)

    # 科目明细检索（从审计报告附注）
    priority = _get_priority_accounts(company_id, periods)
    account_details = _retrieve_account_details(company_id, priority, periods)
    logger.info("Account detail retrieval: %d priority accounts, %d chars",
                len(priority), len(account_details))

    # 单科目占比 >20% 检测
    oversized_items = _compute_oversized_items(company_id, periods)
    logger.info("Oversized items (>20%%): %d chars", len(oversized_items))

    prompt = load_prompt("financial_analysis")
    prompt = prompt.format(
        company_info=f"公司代码：{company_id}",
        balances_table=balances,
        metrics_table=_format_metrics_table(metrics),
        growth_table=_format_growth_table(metrics),
        account_details=account_details,
        oversized_items=oversized_items,
        guidance=section_spec.guidance or "分析公司整体财务状况，覆盖偿债、盈利、营运、现金流四个维度。",
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的授信审批官。只输出 Markdown 格式的财务分析章节正文。",
        max_tokens=4096,
    )

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=[],
        generated_by="financial_analyzer",
    )


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = "300750"
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--company" and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    from reporting.template import SectionSpec

    spec = SectionSpec(
        section_id="financial.偿债能力",
        title="偿债能力分析",
        agent_id="financial",
        guidance="必须包含流动比率、速动比率、资产负债率、利息保障倍数四个指标的当期值、三年趋势。分析短期偿债能力和长期偿债能力。",
    )

    result = run(company, spec)
    print(f"## {result.title}\n")
    print(result.content)
