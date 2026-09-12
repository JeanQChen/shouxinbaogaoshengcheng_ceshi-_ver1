"""A5：财务数字身份 + 收入/成本类别（纯确定性，无 I/O / LLM / OCR / 附注解析）。

任务书 §12 P0 / §13.4 / §17.1 / §18：财务数字必须先绑定身份（主体、期间、合并口径、
币种、科目或指标、业务板块、金额/比例列、收入/成本类别、版本、行列来源）再发布。
金额相同不意味着指标相同；单位换算后相等不算冲突；期间或主体不同要并列说明；同口径
不同值才进入冲突处理。实际 NDSD_KCZ_2026 表5-11「主营业务成本构成表」的金额被当成
收入采纳，entailment 只做数值存在校验（找得到同一数字即 SUPPORTED），未校验数字含义，
故误判通过——本模块提供确定性「成本不能被采纳为收入」的判定基础。

本模块只做纯派生与判定（可独立单元测试，不依赖 DB / Registry / LLM）：
- FinancialNumberIdentity：§13.4 数值身份（不可变，缺字段显式 None）；
- classify_revenue_cost：确定性把 科目代码/表题 归类为 revenue/cost/other；
- check_revenue_cost_label：claim 的收入/成本类别 vs 数字身份类别 → match/mismatch/unknown；
- same_scope / comparison_relation：同口径比较 +「单位等价不算冲突、同口径不同值才冲突」。

claim 侧的 收入/成本 类别来自结构化绑定（claim 生成的 item_code / 表题），不从自由文本
猜测；本模块只接收已解析的 claimed_label（"revenue"/"cost"），不做脆弱的中文文本语义
抽取（「非营业收入」含「营业收入」等坑由结构化接口在上游解决，不在此用正则硬猜）。

明确不在本模块做 PDF 附注解析（表题→金额对位、多级表头、跨页续表在 B1 完整表格上下文 /
B2 财务结构化接口里闭环）。本模块是被后续结构化接口调用的纯函数，不是解析器。

CLI: python -m financial_v2.number_identity --self-check
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from decimal import Decimal

from financial_v2 import normalization as norm

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 收入/成本类别（§13.4「收入/成本类别」维度的归一化值）。
REVENUE_COST_CATEGORIES = ("revenue", "cost", "other")

# 明确表示「收入」的标准科目代码（与 mapping.py 写入 record.standard_item_code 一致）。
_REVENUE_ITEM_CODES = frozenset({
    "OPERATING_REVENUE",
    "TOTAL_REVENUE",
})

# 明确表示「成本」的标准科目代码。
_COST_ITEM_CODES = frozenset({
    "OPERATING_COST",
    "TOTAL_OPERATING_COST",
})

# comparison_relation 的判定结果。
COMPARISON_RELATIONS = (
    "SAME",              # 同口径 + 数值等价（单位换算后相等）
    "CONFLICT",          # 同口径 + 数值不等 → 阻断受影响结论
    "DIFFERENT_PERIOD",  # 仅期间/期间类型不同
    "DIFFERENT_SUBJECT",  # 仅主体/业务板块不同
    "DIFFERENT_ITEM",     # 科目/指标/金额列/收入成本类别不同（金额相同也不意味着指标相同）
    "DIFFERENT_SCOPE",    # 合并口径/币种不同
    "INCOMPARABLE",       # 任一值缺失或身份不足，无法判定
)


# ---------------------------------------------------------------------------
# 数值身份
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FinancialNumberIdentity:
    """财务数字的完整身份（§13.4）。缺字段显式 None，绝不默认猜测。

    revenue_cost_category 由 classify_revenue_cost 派生（或由结构化接口直接给定），
    是「成本不能被采纳为收入」判定的关键维度。
    """

    subject: str | None = None                # 主体（公司 / 业务板块主体）
    period: str | None = None                 # 报告期，如 "2025-12-31"
    period_type: str | None = None            # annual / interim / quarterly
    scope: str | None = None                  # consolidated / parent
    currency: str | None = None               # CNY
    item_code: str | None = None              # 标准科目代码 / 指标 formula_id
    business_segment: str | None = None       # 业务板块（如「动力电池」）
    amount_column: str | None = None          # 金额 / 占比 / 比例（列语义）
    revenue_cost_category: str | None = None  # revenue / cost / other
    version: str | None = None                # 内容/公式版本
    row_column_source: str | None = None      # 行列来源（表题 + 物理页 + 行列定位）

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "period": self.period,
            "period_type": self.period_type,
            "scope": self.scope,
            "currency": self.currency,
            "item_code": self.item_code,
            "business_segment": self.business_segment,
            "amount_column": self.amount_column,
            "revenue_cost_category": self.revenue_cost_category,
            "version": self.version,
            "row_column_source": self.row_column_source,
        }


# ---------------------------------------------------------------------------
# 收入/成本类别派生与判定
# ---------------------------------------------------------------------------

def classify_revenue_cost(*, item_code: str | None = None,
                          table_title: str | None = None) -> str | None:
    """确定性归类 收入/成本 类别。

    优先级：item_code（权威标准代码）> table_title（表题文本）。
    - 明确收入代码/表题 → "revenue"；明确成本代码/表题 → "cost"；
    - 给出已知非收入/成本科目代码 → "other"；
    - 表题同时含「收入」「成本」（如「收入成本构成表」）或均不含 → None（不猜）。
    """
    if item_code:
        if item_code in _REVENUE_ITEM_CODES:
            return "revenue"
        if item_code in _COST_ITEM_CODES:
            return "cost"
        return "other"

    if table_title:
        t = table_title
        has_revenue = ("收入" in t) or ("营收" in t)
        has_cost = ("成本" in t) or ("费用" in t)
        if has_revenue and not has_cost:
            return "revenue"
        if has_cost and not has_revenue:
            return "cost"
        return None  # 同时含收入与成本，或均不含 → 不猜

    return None


def check_revenue_cost_label(identity: FinancialNumberIdentity,
                             claimed_label: str | None) -> str:
    """claim 的收入/成本标签 与 数字身份类别 的确定性判定。

    返回 "match" / "mismatch" / "unknown"。
    - 身份类别或 claim 标签任一缺失/非收入成本 → "unknown"（不做语义猜测，交给上层）；
    - 两者都是收入成本且一致 → "match"；不一致 → "mismatch"（成本被当收入/收入被当成本）。
    """
    cat = identity.revenue_cost_category
    if cat not in ("revenue", "cost") or claimed_label not in ("revenue", "cost"):
        return "unknown"
    return "match" if cat == claimed_label else "mismatch"


# ---------------------------------------------------------------------------
# 同口径比较（§13.4 / §17.1：金额相同≠指标相同；单位等价不算冲突；同口径不同值才冲突）
# ---------------------------------------------------------------------------

# 口径关键维度：这些一致才算「同一指标/同一口径」。version / row_column_source 是溯源
# 坐标而非口径语义，不参与同口径判定。
_SCOPE_FIELDS = ("subject", "period", "period_type", "scope", "currency",
                 "item_code", "business_segment", "amount_column",
                 "revenue_cost_category")


def same_scope(a: FinancialNumberIdentity, b: FinancialNumberIdentity) -> bool:
    """两数字是否同口径（§13.4 关键维度全一致）。"""
    return all(getattr(a, f) == getattr(b, f) for f in _SCOPE_FIELDS)


def _first_diff_field(a: FinancialNumberIdentity, b: FinancialNumberIdentity) -> str | None:
    for f in _SCOPE_FIELDS:
        if getattr(a, f) != getattr(b, f):
            return f
    return None


def comparison_relation(a: FinancialNumberIdentity, a_value: Decimal | None,
                        a_unit: str | None,
                        b: FinancialNumberIdentity, b_value: Decimal | None,
                        b_unit: str | None) -> str:
    """两数字的关系判定（§13.4 冲突处理规则的确定性表达）。

    - 任一值缺失 / 单位无法换算到元 → INCOMPARABLE；
    - 同口径 + 换算后相等 → SAME；同口径 + 不等 → CONFLICT；
    - 仅期间不同 → DIFFERENT_PERIOD；仅主体/板块不同 → DIFFERENT_SUBJECT；
    - 仅合并口径/币种不同 → DIFFERENT_SCOPE；科目/指标/金额列/收入成本类别不同 →
      DIFFERENT_ITEM（金额相同也不意味着指标相同）。
    """
    if a_value is None or b_value is None:
        return "INCOMPARABLE"
    a_mult = norm.unit_to_yuan(a_unit)
    b_mult = norm.unit_to_yuan(b_unit)
    if a_mult is None or b_mult is None:
        return "INCOMPARABLE"

    if same_scope(a, b):
        return "SAME" if a_value * a_mult == b_value * b_mult else "CONFLICT"

    diff = _first_diff_field(a, b)
    if diff in ("period", "period_type"):
        return "DIFFERENT_PERIOD"
    if diff in ("subject", "business_segment"):
        return "DIFFERENT_SUBJECT"
    if diff in ("scope", "currency"):
        return "DIFFERENT_SCOPE"
    return "DIFFERENT_ITEM"


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.number_identity",
        description="财务数字身份 + 收入/成本类别自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # NDSD_KCZ_2026 表5-10 收入 / 表5-11 成本（实际错误 fixture）。
        revenue_id = FinancialNumberIdentity(
            subject="宁德时代", period="2025-12-31", period_type="annual",
            scope="consolidated", currency="CNY", item_code="OPERATING_REVENUE",
            business_segment="动力电池", amount_column="金额",
            revenue_cost_category="revenue",
            row_column_source="表5-10 主营业务收入构成表")
        cost_id = FinancialNumberIdentity(
            subject="宁德时代", period="2025-12-31", period_type="annual",
            scope="consolidated", currency="CNY", item_code="OPERATING_COST",
            business_segment="动力电池", amount_column="金额",
            revenue_cost_category="cost",
            row_column_source="表5-11 主营业务成本构成表")

        print(json.dumps({
            "classify_revenue_item": classify_revenue_cost(item_code="OPERATING_REVENUE"),
            "classify_cost_item": classify_revenue_cost(item_code="OPERATING_COST"),
            "classify_revenue_title": classify_revenue_cost(table_title="主营业务收入构成表"),
            "classify_cost_title": classify_revenue_cost(table_title="主营业务成本构成表"),
            "classify_ambiguous_title": classify_revenue_cost(table_title="主营业务收入成本构成表"),
            "cost_claimed_as_revenue": check_revenue_cost_label(cost_id, "revenue"),
            "cost_claimed_as_cost": check_revenue_cost_label(cost_id, "cost"),
            "revenue_claimed_as_revenue": check_revenue_cost_label(revenue_id, "revenue"),
            "cost_vs_revenue_relation": comparison_relation(
                cost_id, Decimal("24106439.7"), "wan_yuan",
                revenue_id, Decimal("31650636.9"), "wan_yuan"),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
