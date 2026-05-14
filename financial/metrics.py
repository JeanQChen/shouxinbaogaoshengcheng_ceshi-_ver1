"""财务指标计算函数（纯 Python，不含 LLM）。

所有数值从 db.query_metric() 取，不调 LLM。
缺数据静默跳过，值为 None。
"""

import logging
from dataclasses import dataclass

from financial.db import query_metric, list_periods
from financial.schema import (
    TOTAL_ASSETS, CURRENT_ASSETS, TOTAL_LIABILITIES, CURRENT_LIABILITIES,
    TOTAL_EQUITY, INVENTORY, TOTAL_REVENUE, OPERATING_COST, NET_PROFIT,
    TOTAL_PROFIT, FINANCE_EXPENSES, OPERATING_CASH_FLOW,
    INVESTING_CASH_FLOW, FINANCING_CASH_FLOW,
    OPERATING_PROFIT, SALES_EXPENSES, ADMIN_EXPENSES, R_AND_D_EXPENSES,
    ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED,
)

logger = logging.getLogger(__name__)


# ── 公共类型 ──

@dataclass
class MetricsTable:
    by_period: dict[str, dict[str, float | None]]     # period → {metric_name: value}
    yoy_changes: dict[str, dict[str, float | None]]   # period → {metric_name: yoy_pct}


# ── 工具 ──

def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _fetch(company_id: str, code: str, periods: list[str]) -> dict[str, float | None]:
    """查询某科目在所有期间的值，返回 {period: value}。"""
    result: dict[str, float | None] = {}
    for p in periods:
        result[p] = query_metric(company_id, code, p)
    return result


def _fetch_many(company_id: str, codes: list[str], periods: list[str]) -> dict[str, dict[str, float | None]]:
    """批量查询一组科目，返回 {code: {period: value}}。"""
    return {code: _fetch(company_id, code, periods) for code in codes}


def _growth_rate(current: float | None, previous: float | None) -> float | None:
    """计算增长率 = (current - previous) / |previous|。任一端缺失则返回 None。"""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


# ── 主入口 ──

def compute_all(company_id: str, periods: list[str]) -> MetricsTable:
    """计算全部核心指标，返回各期值和同比增长率。

    包含 ~19 个指标：
      偿债（5）：流动比率、速动比率、资产负债率、利息保障倍数、权益乘数
      盈利（5）：毛利率、净利率、ROE、ROA、营业利润率
      营运（3）：总资产周转率、存货周转率、应收账款周转率
      现金流（3）：经营现金流/净利润、现金流/总资产、现金流/营业收入
      费用（1）：期间费用率
      成长（2）：营收增长率、净利增长率（见 yoy_changes）

    yoy_changes 仅对年报期（-12-31）计算相邻同比，跳过季报避免季节性干扰。
    """
    all_codes = [
        TOTAL_ASSETS, CURRENT_ASSETS, TOTAL_LIABILITIES, CURRENT_LIABILITIES,
        TOTAL_EQUITY, INVENTORY, TOTAL_REVENUE, OPERATING_COST, NET_PROFIT,
        TOTAL_PROFIT, FINANCE_EXPENSES, OPERATING_CASH_FLOW,
        INVESTING_CASH_FLOW, FINANCING_CASH_FLOW,
        OPERATING_PROFIT, SALES_EXPENSES, ADMIN_EXPENSES, R_AND_D_EXPENSES,
        ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED,
    ]
    data = _fetch_many(company_id, all_codes, periods)

    by_period: dict[str, dict[str, float | None]] = {}

    for i, p in enumerate(periods):
        ca = data[CURRENT_ASSETS].get(p)
        cl = data[CURRENT_LIABILITIES].get(p)
        inv = data[INVENTORY].get(p)
        ta = data[TOTAL_ASSETS].get(p)
        tl = data[TOTAL_LIABILITIES].get(p)
        te = data[TOTAL_EQUITY].get(p)
        rev = data[TOTAL_REVENUE].get(p)
        cost = data[OPERATING_COST].get(p)
        np_ = data[NET_PROFIT].get(p)
        tp = data[TOTAL_PROFIT].get(p)
        fe = data[FINANCE_EXPENSES].get(p)
        ocf = data[OPERATING_CASH_FLOW].get(p)
        op = data[OPERATING_PROFIT].get(p)
        se = data[SALES_EXPENSES].get(p)
        ae = data[ADMIN_EXPENSES].get(p)
        rde = data[R_AND_D_EXPENSES].get(p)
        ar = data[ACCOUNTS_RECEIVABLE].get(p) or data[ACCOUNTS_RECEIVABLE_COMBINED].get(p)

        ebit = (tp + fe) if tp is not None and fe is not None else None
        gp = (rev - cost) if rev is not None and cost is not None else None
        quick_numerator = (ca - inv) if ca is not None and inv is not None else None
        period_expenses = (
            se + ae + rde + fe
            if se is not None and ae is not None and rde is not None and fe is not None
            else None
        )

        # 营运：前期数据算平均值
        if i > 0:
            prev_p = periods[i - 1]
            prev_ta = data[TOTAL_ASSETS].get(prev_p)
            prev_inv = data[INVENTORY].get(prev_p)
            prev_ar = data[ACCOUNTS_RECEIVABLE].get(prev_p) or data[ACCOUNTS_RECEIVABLE_COMBINED].get(prev_p)
            avg_ta = (ta + prev_ta) / 2 if ta is not None and prev_ta is not None else None
            avg_inv = (inv + prev_inv) / 2 if inv is not None and prev_inv is not None else None
            avg_ar = (ar + prev_ar) / 2 if ar is not None and prev_ar is not None else None
        else:
            avg_ta = avg_inv = avg_ar = None

        metrics: dict[str, float | None] = {}

        # 偿债
        metrics["流动比率"] = _safe_div(ca, cl)
        metrics["速动比率"] = _safe_div(quick_numerator, cl)
        metrics["资产负债率"] = _safe_div(tl, ta)
        metrics["利息保障倍数"] = _safe_div(ebit, fe)
        metrics["权益乘数"] = _safe_div(ta, te)

        # 盈利
        metrics["毛利率"] = _safe_div(gp, rev)
        metrics["净利率"] = _safe_div(np_, rev)
        metrics["ROE"] = _safe_div(np_, te)
        metrics["ROA"] = _safe_div(np_, ta)
        metrics["营业利润率"] = _safe_div(op, rev)

        # 营运
        metrics["总资产周转率"] = _safe_div(rev, avg_ta)
        metrics["存货周转率"] = _safe_div(cost, avg_inv)
        metrics["应收账款周转率"] = _safe_div(rev, avg_ar)

        # 现金流
        metrics["经营现金流/净利润"] = _safe_div(ocf, np_)
        metrics["现金流/总资产"] = _safe_div(ocf, ta)
        metrics["现金流/营业收入"] = _safe_div(ocf, rev)

        # 费用
        metrics["期间费用率"] = _safe_div(period_expenses, rev)

        by_period[p] = metrics

    # 增长变化：年报同比（vs 上一个年报），季报环比（vs 紧前期间）
    yoy: dict[str, dict[str, float | None]] = {}
    annual_set = {p for p in periods if p.endswith("-12-31")}

    growth_pairs = [
        ("营收增长率", TOTAL_REVENUE),
        ("净利增长率", NET_PROFIT),
        ("资产增长率", TOTAL_ASSETS),
        ("负债增长率", TOTAL_LIABILITIES),
        ("净资产增长率", TOTAL_EQUITY),
        ("经营现金流增长率", OPERATING_CASH_FLOW),
        ("投资现金流增长率", INVESTING_CASH_FLOW),
        ("筹资现金流增长率", FINANCING_CASH_FLOW),
    ]

    for i in range(1, len(periods)):
        curr = periods[i]

        if curr in annual_set:
            annuals = sorted(p for p in periods if p in annual_set and p < curr)
            if not annuals:
                continue
            prev = annuals[-1]
        else:
            prev = periods[i - 1]

        changes: dict[str, float | None] = {}
        for label, code in growth_pairs:
            changes[label] = _growth_rate(
                data[code].get(curr),
                data[code].get(prev),
            )

        yoy[curr] = changes

    return MetricsTable(by_period=by_period, yoy_changes=yoy)


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--company" and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not company:
        company = "300750"

    from financial.db import init_db, list_periods
    init_db()
    periods = list_periods(company)

    if not periods:
        print("No data in DB. Run schema_mapper --save first.")
        sys.exit(1)

    print(f"Periods: {periods}")
    result = compute_all(company, periods)

    print("\n=== by_period ===")
    for p in periods:
        print(f"\n{p}:")
        for metric, val in result.by_period[p].items():
            if val is not None:
                print(f"  {metric}: {val:.4f}")
            else:
                print(f"  {metric}: N/A")

    if result.yoy_changes:
        print("\n=== growth_changes (annual=同比, quarterly=环比) ===")
        for p, changes in result.yoy_changes.items():
            print(f"\n{p}:")
            for metric, val in changes.items():
                if val is not None:
                    print(f"  {metric}: {val:+.2%}")
                else:
                    print(f"  {metric}: N/A")
