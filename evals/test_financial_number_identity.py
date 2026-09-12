"""Eval: financial_v2 财务数字身份 + 收入/成本类别（A5 失败回归）。

用法: python -m evals.test_financial_number_identity

回归背景（任务书 §12 P0 / §18）：实际 NDSD_KCZ_2026.pdf 物理第50页
- 表5-10 主营业务收入构成表：2024/2025 动力电池 25,304,133.7 / 31,650,636.9 万元；
- 表5-11 主营业务成本构成表：2024/2025 动力电池 19,246,128.2 / 24,106,439.7 万元。
旧错误 Claim 把表5-11 成本金额叫「收入」，entailment 只做数值存在校验（找得到同一数字
即 SUPPORTED）而误判通过。

本模块验证确定性「数字身份 + 收入/成本类别」判定：成本数字身份在 claim 标「收入」时
必须返回 mismatch（阻断采纳）；同口径不同值 → CONFLICT；金额相同但身份不同 →
DIFFERENT_ITEM（金额相同不意味着指标相同）；单位换算后相等 → SAME（不算冲突）。

全部纯函数（无 I/O / LLM / DB），合成 fixture（宁德时代等名只作 case fixture，不进通用
路由/模板分支）。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2.number_identity import (
    FinancialNumberIdentity,
    classify_revenue_cost,
    check_revenue_cost_label,
    comparison_relation,
    same_scope,
)


def _revenue_id(period: str = "2025-12-31", value: Decimal | None = None) -> FinancialNumberIdentity:
    # value 不参与身份（身份与金额分离）；仅用于可读性，此处忽略入参。
    return FinancialNumberIdentity(
        subject="宁德时代", period=period, period_type="annual", scope="consolidated",
        currency="CNY", item_code="OPERATING_REVENUE", business_segment="动力电池",
        amount_column="金额", revenue_cost_category="revenue",
        row_column_source="表5-10 主营业务收入构成表")


def _cost_id(period: str = "2025-12-31") -> FinancialNumberIdentity:
    return FinancialNumberIdentity(
        subject="宁德时代", period=period, period_type="annual", scope="consolidated",
        currency="CNY", item_code="OPERATING_COST", business_segment="动力电池",
        amount_column="金额", revenue_cost_category="cost",
        row_column_source="表5-11 主营业务成本构成表")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- 收入/成本类别派生（科目代码 / 表题）----
    check(classify_revenue_cost(item_code="OPERATING_REVENUE") == "revenue",
          "A5：OPERATING_REVENUE → revenue")
    check(classify_revenue_cost(item_code="TOTAL_REVENUE") == "revenue",
          "A5：TOTAL_REVENUE → revenue")
    check(classify_revenue_cost(item_code="OPERATING_COST") == "cost",
          "A5：OPERATING_COST → cost")
    check(classify_revenue_cost(item_code="TOTAL_OPERATING_COST") == "cost",
          "A5：TOTAL_OPERATING_COST → cost")
    check(classify_revenue_cost(item_code="SALES_EXPENSES") == "other",
          "A5：非收入/成本科目 → other（不冒充收入/成本）")
    check(classify_revenue_cost(item_code=None, table_title="主营业务收入构成表") == "revenue",
          "A5：表题「主营业务收入构成表」→ revenue")
    check(classify_revenue_cost(item_code=None, table_title="主营业务成本构成表") == "cost",
          "A5：表题「主营业务成本构成表」→ cost")
    check(classify_revenue_cost(item_code=None, table_title="主营业务收入成本构成表") is None,
          "A5：表题同时含收入+成本 → None（不猜）")
    check(classify_revenue_cost(item_code=None, table_title=None) is None,
          "A5：无代码无表题 → None")

    # ---- 核心：成本数字不能被采纳为收入 ----
    check(check_revenue_cost_label(_cost_id(), "revenue") == "mismatch",
          "A5：成本数字身份 + claim 标「收入」→ mismatch（阻断采纳）")
    check(check_revenue_cost_label(_cost_id(), "cost") == "match",
          "A5：成本数字身份 + claim 标「成本」→ match")
    check(check_revenue_cost_label(_revenue_id(), "revenue") == "match",
          "A5：收入数字身份 + claim 标「收入」→ match")
    check(check_revenue_cost_label(_revenue_id(), "cost") == "mismatch",
          "A5：收入数字身份 + claim 标「成本」→ mismatch")
    check(check_revenue_cost_label(_cost_id(), None) == "unknown",
          "A5：claim 无收入/成本标签 → unknown（不做语义猜测）")
    check(check_revenue_cost_label(_cost_id(), "other") == "unknown",
          "A5：claim 标签非收入/成本 → unknown")

    # ---- §13.4：金额相同不意味着指标相同 / 单位等价 / 同口径冲突 ----
    # 表5-10 收入 2025 = 31,650,636.9 万元；表5-11 成本 2025 = 24,106,439.7 万元。
    rel = comparison_relation(
        _revenue_id(), Decimal("31650636.9"), "wan_yuan",
        _cost_id(), Decimal("24106439.7"), "wan_yuan")
    check(rel == "DIFFERENT_ITEM",
          "A5：收入 vs 成本（同期间同板块）→ DIFFERENT_ITEM（金额相同也不意味着指标相同）")

    # 单位换算后相等：4亿元 == 40,000万元，不算冲突。
    a = _revenue_id()
    b = _revenue_id()
    rel_unit = comparison_relation(a, Decimal("4"), "yi_yuan",
                                  b, Decimal("40000"), "wan_yuan")
    check(rel_unit == "SAME", "A5：4亿元 vs 40000万元（同口径）→ SAME（单位等价不算冲突）")

    # 同口径不同值 → CONFLICT。
    rel_conflict = comparison_relation(
        _revenue_id("2025-12-31"), Decimal("31650636.9"), "wan_yuan",
        _revenue_id("2025-12-31"), Decimal("25000000.0"), "wan_yuan")
    check(rel_conflict == "CONFLICT", "A5：同口径收入不同值 → CONFLICT（阻断受影响结论）")

    # 期间不同 → 并列说明，不判冲突。
    rel_period = comparison_relation(
        _revenue_id("2024-12-31"), Decimal("25304133.7"), "wan_yuan",
        _revenue_id("2025-12-31"), Decimal("31650636.9"), "wan_yuan")
    check(rel_period == "DIFFERENT_PERIOD",
          "A5：仅期间不同 → DIFFERENT_PERIOD（并列说明，不判冲突）")

    # 主体/板块不同 → DIFFERENT_SUBJECT。
    seg_a = _revenue_id()
    seg_b = FinancialNumberIdentity(
        subject="宁德时代", period="2025-12-31", period_type="annual", scope="consolidated",
        currency="CNY", item_code="OPERATING_REVENUE", business_segment="储能电池",
        amount_column="金额", revenue_cost_category="revenue",
        row_column_source="表5-10 主营业务收入构成表")
    check(comparison_relation(seg_a, Decimal("31650636.9"), "wan_yuan",
                              seg_b, Decimal("31650636.9"), "wan_yuan") == "DIFFERENT_SUBJECT",
          "A5：仅业务板块不同 → DIFFERENT_SUBJECT（金额相同也不意味着指标相同）")

    # 任一值缺失 → INCOMPARABLE。
    check(comparison_relation(_revenue_id(), None, "wan_yuan",
                              _revenue_id(), Decimal("1"), "wan_yuan") == "INCOMPARABLE",
          "A5：任一值缺失 → INCOMPARABLE")

    # same_scope 关键维度一致才算同口径。
    check(same_scope(_revenue_id(), _revenue_id()), "A5：同身份 → same_scope True")
    check(not same_scope(_revenue_id(), _cost_id()), "A5：收入 vs 成本 → same_scope False")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
