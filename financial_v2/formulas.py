"""A6-3 Formula Registry（任务书 §7）：确定性公式定义 + 白名单 callable。

职责边界（CLAUDE.md §7.1 硬要求）：
- 公式只允许注册代码内白名单 callable；绝不执行数据库/配置文件中的任意表达式、eval()
  或动态代码；
- 运行时权威清单由本模块生成（对照 FORMULA_REVIEW.md，但不解析 Markdown 当业务逻辑）；
- 全部数值在 Python 内用 Decimal 计算，raw_value 保留未舍入权威值，仅展示层舍入；
- 缺输入 ≠ 0；代理值必须显式标注 reason_code；季报不生成正式增长率。

状态与原因码（任务书 §8.2 / schema.METRIC_STATUSES / METRIC_REASON_CODES）：
- 主状态：CALCULATED_EXACT / CALCULATED_PROXY / MISSING_INPUT / PARTIAL_INPUT /
  ZERO_DENOMINATOR / NOT_APPLICABLE / BLOCKED_BY_SNAPSHOT（后三者由调用方/快照层产生，
  本模块只产前五类与 NOT_APPLICABLE）。
- reason_code 至少覆盖 PROXY_FINANCE_EXPENSES / MISSING_PRIOR_PERIOD /
  MISSING_REQUIRED_ITEM / MIXED_RECEIVABLE_BASIS_FORBIDDEN（快照层另有 UNRESOLVED_CONFLICT
  等，见 metrics.py）。

公式范围（FORMULA_REVIEW.md）：
- 第 1～5 节确认的 25 项；
- 第 6 节已确认且输入可得时计算的 3 项：EBITDA、有息负债、自由现金流；
- INTEREST_EXPENSE 是来源科目，不另伪造为计算指标。

CLI:
  python -m financial_v2.formulas list
  python -m financial_v2.formulas inspect --formula SOLV_CURRENT_RATIO [--version 1.0]
  python -m financial_v2.formulas persist [--db <path>]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

# 正式版本号（进入 A6 Formula Registry 后分配的冻结口径）。
FORMULA_VERSION = "1.0"
# 实现版本与业务口径版本分离：口径不变时实现可独立升版。
IMPL_VERSION = "1.0"
EFFECTIVE_AT = "2026-01-01"

# 全部公式的 scope 要求（V1/V2 均只认合并口径）。
SCOPE_REQUIREMENT = "consolidated"

# 展示单位与舍入：rounding_rule = "<unit>:ROUND_HALF_UP:<decimals>"。
# unit ∈ {"ratio" 无量纲倍数, "percent" 百分比(展示 ×100), "yuan" 金额}。
ROUNDING_DECIMALS = 2
_TWO_DP = Decimal("0.01")

# 尚未进入 mapping 内置规则集的科目代码（EBITDA / FCF 依赖）。
# 仅作为公式输入代码引用；无映射规则产出时，metrics 层采集不到 → 公式返回 MISSING_INPUT。
# 不修改 V1 financial/schema.py，也不伪造抽取。
DEPRECIATION = "DEPRECIATION"
AMORTIZATION = "AMORTIZATION"
CAPEX = "CAPEX"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 计算上下文与结果
# ---------------------------------------------------------------------------

@dataclass
class FormulaContext:
    """一次纯计算的输入：金额字典（缺失的科目 key 不存在，绝不把缺失当 0）。"""

    current: dict[str, Decimal]                       # 目标报告期科目 → 金额
    prior: dict[str, Decimal] = field(default_factory=dict)   # 上一报告期（avg/同比用）
    policy: dict = field(default_factory=dict)        # 政策调整解析结果（见各公式）
    period_type: str = "annual"                       # 请求期类型，仅成长公式判 NOT_APPLICABLE


@dataclass
class FormulaOutcome:
    """公式纯计算结果：raw_value 未舍入；非成功状态 raw_value 恒为 None。"""

    raw_value: Decimal | None
    status: str
    reason_code: str | None
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 结果构造助手（统一状态/原因码语义）
# ---------------------------------------------------------------------------

def _ok(value: Decimal, detail: dict | None = None) -> FormulaOutcome:
    return FormulaOutcome(value, "CALCULATED_EXACT", None, detail or {})


def _proxy(value: Decimal, reason_code: str, detail: dict | None = None) -> FormulaOutcome:
    d = detail or {}
    d["proxy_used"] = True
    return FormulaOutcome(value, "CALCULATED_PROXY", reason_code, d)


def _missing(reason_code: str = "MISSING_REQUIRED_ITEM",
             detail: dict | None = None) -> FormulaOutcome:
    return FormulaOutcome(None, "MISSING_INPUT", reason_code, detail or {})


def _partial(reason_code: str = "MISSING_REQUIRED_ITEM",
             detail: dict | None = None) -> FormulaOutcome:
    return FormulaOutcome(None, "PARTIAL_INPUT", reason_code, detail or {})


def _zero(detail: dict | None = None) -> FormulaOutcome:
    return FormulaOutcome(None, "ZERO_DENOMINATOR", "ZERO_DENOMINATOR", detail or {})


def _not_applicable(reason: str = "NOT_APPLICABLE",
                    detail: dict | None = None) -> FormulaOutcome:
    return FormulaOutcome(None, "NOT_APPLICABLE", reason, detail or {})


def _missing_of(current: dict, codes: list[str]) -> list[str]:
    return [c for c in codes if current.get(c) is None]


def _simple_ratio(ctx: FormulaContext, num_code: str, den_code: str) -> FormulaOutcome:
    """期末/流量单一分子、单一分母的比率（无前期、无代理）。"""
    num = ctx.current.get(num_code)
    den = ctx.current.get(den_code)
    if num is None or den is None:
        return _missing(detail={"missing": _missing_of(ctx.current, [num_code, den_code])})
    if den == 0:
        return _zero()
    return _ok(num / den, detail={"numerator": num_code, "denominator": den_code})


def _avg_ratio(ctx: FormulaContext, num_code: str, den_code: str) -> FormulaOutcome:
    """分子流量、分母期初期末平均（需前期，期间不足 → MISSING_PRIOR_PERIOD）。"""
    num = ctx.current.get(num_code)
    cur_den = ctx.current.get(den_code)
    prior_den = ctx.prior.get(den_code)
    if num is None or cur_den is None:
        return _missing(detail={"missing": _missing_of(ctx.current, [num_code, den_code])})
    if prior_den is None:
        return _missing(reason_code="MISSING_PRIOR_PERIOD",
                        detail={"missing_prior": den_code})
    avg = (cur_den + prior_den) / 2
    if avg == 0:
        return _zero(detail={"basis": den_code})
    return _ok(num / avg, detail={"average": True, "denominator": den_code})


def _avg_or_zero(num: Decimal, cur_den: Decimal, prior_den: Decimal,
                 basis: str) -> FormulaOutcome:
    avg = (cur_den + prior_den) / 2
    if avg == 0:
        return _zero(detail={"basis": basis})
    return _ok(num / avg, detail={"average": True, "basis": basis})


# ---------------------------------------------------------------------------
# 偿债能力（FORMULA_REVIEW §1，5 项）
# ---------------------------------------------------------------------------

def solvency_current_ratio(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "CURRENT_ASSETS", "CURRENT_LIABILITIES")


def solvency_quick_ratio(ctx: FormulaContext) -> FormulaOutcome:
    required = ["CURRENT_ASSETS", "INVENTORY", "PREPAYMENTS", "CURRENT_LIABILITIES"]
    missing = _missing_of(ctx.current, required)
    if missing:
        return _missing(detail={"missing": missing})
    den = ctx.current["CURRENT_LIABILITIES"]
    if den == 0:
        return _zero()
    quick = ctx.current["CURRENT_ASSETS"] - ctx.current["INVENTORY"] - ctx.current["PREPAYMENTS"]
    # 可选扣除：经审计确认的非速动 OTHER_CURRENT_ASSETS 金额（由 metrics 层从
    # SnapshotBuildRequest.policy_adjustments 解析真实记录引用后传入，绝不由公式自行判断）。
    excl = ctx.policy.get("quick_ratio_excluded_other_current_asset_amount")
    applied = excl is not None
    if applied:
        quick -= excl
    detail = {"excluded_other_current_asset_applied": applied}
    if applied:
        detail["excluded_other_current_asset_amount"] = str(excl)
    return _ok(quick / den, detail=detail)


def solvency_debt_ratio(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "TOTAL_LIABILITIES", "TOTAL_ASSETS")


def solvency_interest_cover(ctx: FormulaContext) -> FormulaOutcome:
    tp = ctx.current.get("TOTAL_PROFIT")
    ie = ctx.current.get("INTEREST_EXPENSE")
    fe = ctx.current.get("FINANCE_EXPENSES")

    # 精确口径：优先真实利息费用。
    if ie is not None:
        if tp is None:
            return _missing(detail={"missing": ["TOTAL_PROFIT"]})
        if ie == 0:
            return _zero(detail={"basis": "INTEREST_EXPENSE"})
        return _ok((tp + ie) / ie, detail={"basis": "INTEREST_EXPENSE"})

    # 代理口径：真实利息费用缺失且存在财务费用时。
    if fe is not None:
        if tp is None:
            return _missing(detail={"missing": ["TOTAL_PROFIT"]})
        if fe == 0:
            return _zero(detail={"basis": "FINANCE_EXPENSES"})
        return _proxy((tp + fe) / fe, "PROXY_FINANCE_EXPENSES",
                      detail={"basis": "FINANCE_EXPENSES"})

    return _missing(detail={"missing": ["INTEREST_EXPENSE", "FINANCE_EXPENSES"]})


def solvency_equity_multiplier(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "TOTAL_ASSETS", "TOTAL_EQUITY")


# ---------------------------------------------------------------------------
# 盈利能力（FORMULA_REVIEW §2，5 项）
# ---------------------------------------------------------------------------

def profit_gross_margin(ctx: FormulaContext) -> FormulaOutcome:
    rev = ctx.current.get("TOTAL_REVENUE")
    cost = ctx.current.get("OPERATING_COST")
    if rev is None or cost is None:
        return _missing(detail={"missing": _missing_of(ctx.current,
                                                      ["TOTAL_REVENUE", "OPERATING_COST"])})
    if rev == 0:
        return _zero()
    return _ok((rev - cost) / rev)


def profit_net_margin(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "NET_PROFIT", "TOTAL_REVENUE")


def profit_roe(ctx: FormulaContext) -> FormulaOutcome:
    out = _simple_ratio(ctx, "NET_PROFIT", "TOTAL_EQUITY")
    if out.status == "CALCULATED_EXACT":
        out.detail["equity_basis"] = "期末净资产口径"
    return out


def profit_roa(ctx: FormulaContext) -> FormulaOutcome:
    out = _simple_ratio(ctx, "NET_PROFIT", "TOTAL_ASSETS")
    if out.status == "CALCULATED_EXACT":
        out.detail["asset_basis"] = "期末总资产口径"
    return out


def profit_oper_margin(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "OPERATING_PROFIT", "TOTAL_REVENUE")


# ---------------------------------------------------------------------------
# 营运能力（FORMULA_REVIEW §3，3 项，期初期末平均）
# ---------------------------------------------------------------------------

def oper_asset_turnover(ctx: FormulaContext) -> FormulaOutcome:
    return _avg_ratio(ctx, "TOTAL_REVENUE", "TOTAL_ASSETS")


def oper_inventory_turnover(ctx: FormulaContext) -> FormulaOutcome:
    return _avg_ratio(ctx, "OPERATING_COST", "INVENTORY")


def oper_ar_turnover(ctx: FormulaContext) -> FormulaOutcome:
    rev = ctx.current.get("TOTAL_REVENUE")
    if rev is None:
        return _missing(detail={"missing": ["TOTAL_REVENUE"]})

    ar_cur = ctx.current.get("ACCOUNTS_RECEIVABLE")
    ar_prior = ctx.prior.get("ACCOUNTS_RECEIVABLE")
    comb_cur = ctx.current.get("ACCOUNTS_RECEIVABLE_COMBINED")
    comb_prior = ctx.prior.get("ACCOUNTS_RECEIVABLE_COMBINED")

    # 优先：同口径（单独应收账款）两期均存在。
    if ar_cur is not None and ar_prior is not None:
        return _avg_or_zero(rev, ar_cur, ar_prior, "ACCOUNTS_RECEIVABLE")
    # 回退：组合口径两期均存在（仅当单独口径两期均无法完整取得）。
    if comb_cur is not None and comb_prior is not None:
        return _avg_or_zero(rev, comb_cur, comb_prior, "ACCOUNTS_RECEIVABLE_COMBINED")

    # 两期同一口径均无法完整取得 → 判混用（禁止一端单独、另一端组合）。
    if (ar_cur is not None or ar_prior is not None) and \
       (comb_cur is not None or comb_prior is not None):
        return _missing(reason_code="MIXED_RECEIVABLE_BASIS_FORBIDDEN", detail={
            "ar": {"current": ar_cur is not None, "prior": ar_prior is not None},
            "combined": {"current": comb_cur is not None, "prior": comb_prior is not None}})

    # 期间不足（当前有值、前期缺失）。
    if ar_cur is not None:
        return _missing(reason_code="MISSING_PRIOR_PERIOD",
                        detail={"missing_prior": "ACCOUNTS_RECEIVABLE"})
    if comb_cur is not None:
        return _missing(reason_code="MISSING_PRIOR_PERIOD",
                        detail={"missing_prior": "ACCOUNTS_RECEIVABLE_COMBINED"})

    return _missing(detail={"missing": ["ACCOUNTS_RECEIVABLE",
                                        "ACCOUNTS_RECEIVABLE_COMBINED"]})


# ---------------------------------------------------------------------------
# 现金流 / 费用（FORMULA_REVIEW §4，4 项）
# ---------------------------------------------------------------------------

def cash_ocf_to_np(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "OPERATING_CASH_FLOW", "NET_PROFIT")


def cash_ocf_to_asset(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "OPERATING_CASH_FLOW", "TOTAL_ASSETS")


def cash_ocf_to_rev(ctx: FormulaContext) -> FormulaOutcome:
    return _simple_ratio(ctx, "OPERATING_CASH_FLOW", "TOTAL_REVENUE")


def exp_period_rate(ctx: FormulaContext) -> FormulaOutcome:
    codes = ["SALES_EXPENSES", "ADMIN_EXPENSES", "R_AND_D_EXPENSES", "FINANCE_EXPENSES"]
    missing = _missing_of(ctx.current, codes)
    rev = ctx.current.get("TOTAL_REVENUE")
    if rev is None:
        missing.append("TOTAL_REVENUE")
    if missing:
        return _missing(detail={"missing": missing})
    if rev == 0:
        return _zero()
    total_exp = sum(ctx.current[c] for c in codes)
    return _ok(total_exp / rev, detail={"components": codes})


# ---------------------------------------------------------------------------
# 成长（FORMULA_REVIEW §5，8 项，正式指标仅年报同比）
# ---------------------------------------------------------------------------

def _growth_outcome(ctx: FormulaContext, code: str) -> FormulaOutcome:
    if ctx.period_type != "annual":
        return _not_applicable(detail={"code": code, "note": "正式增长率仅年报同比"})
    cur = ctx.current.get(code)
    prior = ctx.prior.get(code)
    if cur is None or prior is None:
        if cur is not None and prior is None:
            return _missing(reason_code="MISSING_PRIOR_PERIOD",
                            detail={"missing_prior": code})
        return _missing(reason_code="MISSING_REQUIRED_ITEM",
                        detail={"missing": [code]})
    if prior == 0:
        return _zero(detail={"code": code, "note": "上年为 0，无法作为增长率分母"})
    return _ok((cur - prior) / abs(prior),
               detail={"code": code, "negative_base": prior < 0})


def growth_revenue(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "TOTAL_REVENUE")


def growth_net_profit(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "NET_PROFIT")


def growth_asset(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "TOTAL_ASSETS")


def growth_liability(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "TOTAL_LIABILITIES")


def growth_equity(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "TOTAL_EQUITY")


def growth_ocf(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "OPERATING_CASH_FLOW")


def growth_icf(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "INVESTING_CASH_FLOW")


def growth_financing_cash_flow(ctx: FormulaContext) -> FormulaOutcome:
    return _growth_outcome(ctx, "FINANCING_CASH_FLOW")


# ---------------------------------------------------------------------------
# 信用分析关键定义（FORMULA_REVIEW §6，3 项，输入可得时计算）
# ---------------------------------------------------------------------------

def ebitda(ctx: FormulaContext) -> FormulaOutcome:
    codes = ["TOTAL_PROFIT", "INTEREST_EXPENSE", DEPRECIATION, AMORTIZATION]
    missing = _missing_of(ctx.current, codes)
    if missing:
        return _missing(detail={"missing": missing})
    return _ok(sum(ctx.current[c] for c in codes), detail={"components": codes})


def interest_bearing_debt(ctx: FormulaContext) -> FormulaOutcome:
    codes = ["SHORT_TERM_BORROWINGS", "LONG_TERM_BORROWINGS", "BONDS_PAYABLE",
             "LEASE_LIABILITIES", "NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR"]
    present = [c for c in codes if ctx.current.get(c) is not None]
    missing = [c for c in codes if c not in present]
    if not present:
        return _missing(detail={"missing": codes})
    if missing:
        # 部分输入缺失（合计项不完整）→ PARTIAL_INPUT，披露已纳入/缺失范围。
        return _partial(detail={"included": present, "missing": missing})
    return _ok(sum(ctx.current[c] for c in codes), detail={"included": codes})


def free_cash_flow(ctx: FormulaContext) -> FormulaOutcome:
    ocf = ctx.current.get("OPERATING_CASH_FLOW")
    capex = ctx.current.get(CAPEX)
    if ocf is None or capex is None:
        return _missing(detail={"missing": _missing_of(ctx.current,
                                                      ["OPERATING_CASH_FLOW", CAPEX])})
    return _ok(ocf - capex, detail={"components": ["OPERATING_CASH_FLOW", CAPEX]})


# ---------------------------------------------------------------------------
# Registry：白名单 callable + 权威定义清单
# ---------------------------------------------------------------------------

_CALLABLES: dict[str, object] = {
    "SOLV_CURRENT_RATIO": solvency_current_ratio,
    "SOLV_QUICK_RATIO": solvency_quick_ratio,
    "SOLV_DEBT_RATIO": solvency_debt_ratio,
    "SOLV_INTEREST_COVER": solvency_interest_cover,
    "SOLV_EQUITY_MULT": solvency_equity_multiplier,
    "PROF_GROSS_MARGIN": profit_gross_margin,
    "PROF_NET_MARGIN": profit_net_margin,
    "PROF_ROE": profit_roe,
    "PROF_ROA": profit_roa,
    "PROF_OPER_MARGIN": profit_oper_margin,
    "OPER_ASSET_TURNOVER": oper_asset_turnover,
    "OPER_INV_TURNOVER": oper_inventory_turnover,
    "OPER_AR_TURNOVER": oper_ar_turnover,
    "CASH_OCF_TO_NP": cash_ocf_to_np,
    "CASH_OCF_TO_ASSET": cash_ocf_to_asset,
    "CASH_OCF_TO_REV": cash_ocf_to_rev,
    "EXP_PERIOD_RATE": exp_period_rate,
    "GROWTH_REVENUE": growth_revenue,
    "GROWTH_NET_PROFIT": growth_net_profit,
    "GROWTH_ASSET": growth_asset,
    "GROWTH_LIABILITY": growth_liability,
    "GROWTH_EQUITY": growth_equity,
    "GROWTH_OCF": growth_ocf,
    "GROWTH_ICF": growth_icf,
    "GROWTH_FINANCING_CASH_FLOW": growth_financing_cash_flow,
    "EBITDA": ebitda,
    "INTEREST_BEARING_DEBT": interest_bearing_debt,
    "FREE_CASH_FLOW": free_cash_flow,
}

# (formula_id, 展示名, 输入科目, 期间要求, 舍入单位, 缺失规则, 零分母规则, 代理规则)。
# 期间要求 ∈ {end, flow, flow/end, flow/avg, yoy_flow, yoy_end}。
_FORMULA_SPECS: list[dict] = [
    # ---- 偿债能力 ----
    {"formula_id": "SOLV_CURRENT_RATIO", "name": "流动比率",
     "input_item_codes": ["CURRENT_ASSETS", "CURRENT_LIABILITIES"],
     "period_requirement": "end", "rounding": "ratio",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "SOLV_QUICK_RATIO", "name": "速动比率",
     "input_item_codes": ["CURRENT_ASSETS", "INVENTORY", "PREPAYMENTS",
                          "OTHER_CURRENT_ASSETS", "CURRENT_LIABILITIES"],
     "period_requirement": "end", "rounding": "ratio",
     "missing_rule": "必需扣除项（存货/预付款项/流动负债）缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR",
     "proxy_rule": {"optional_input": "OTHER_CURRENT_ASSETS",
                    "note": "仅经审计确认非速动时追加扣除，缺确认不等于 0"}},
    {"formula_id": "SOLV_DEBT_RATIO", "name": "资产负债率",
     "input_item_codes": ["TOTAL_LIABILITIES", "TOTAL_ASSETS"],
     "period_requirement": "end", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "SOLV_INTEREST_COVER", "name": "利息保障倍数",
     "input_item_codes": ["TOTAL_PROFIT", "INTEREST_EXPENSE", "FINANCE_EXPENSES"],
     "period_requirement": "flow", "rounding": "ratio",
     "missing_rule": "精确/代理分母均缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR",
     "proxy_rule": {"proxy_input": "FINANCE_EXPENSES", "replaces": "INTEREST_EXPENSE",
                    "reason_code": "PROXY_FINANCE_EXPENSES"}},
    {"formula_id": "SOLV_EQUITY_MULT", "name": "权益乘数",
     "input_item_codes": ["TOTAL_ASSETS", "TOTAL_EQUITY"],
     "period_requirement": "end", "rounding": "ratio",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    # ---- 盈利能力 ----
    {"formula_id": "PROF_GROSS_MARGIN", "name": "毛利率",
     "input_item_codes": ["TOTAL_REVENUE", "OPERATING_COST"],
     "period_requirement": "flow", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "PROF_NET_MARGIN", "name": "净利率",
     "input_item_codes": ["NET_PROFIT", "TOTAL_REVENUE"],
     "period_requirement": "flow", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "PROF_ROE", "name": "ROE",
     "input_item_codes": ["NET_PROFIT", "TOTAL_EQUITY"],
     "period_requirement": "flow/end", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "PROF_ROA", "name": "ROA",
     "input_item_codes": ["NET_PROFIT", "TOTAL_ASSETS"],
     "period_requirement": "flow/end", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "PROF_OPER_MARGIN", "name": "营业利润率",
     "input_item_codes": ["OPERATING_PROFIT", "TOTAL_REVENUE"],
     "period_requirement": "flow", "rounding": "percent",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    # ---- 营运能力 ----
    {"formula_id": "OPER_ASSET_TURNOVER", "name": "总资产周转率",
     "input_item_codes": ["TOTAL_REVENUE", "TOTAL_ASSETS"],
     "period_requirement": "flow/avg", "rounding": "ratio",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "平均余额为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "OPER_INV_TURNOVER", "name": "存货周转率",
     "input_item_codes": ["OPERATING_COST", "INVENTORY"],
     "period_requirement": "flow/avg", "rounding": "ratio",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "平均余额为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "OPER_AR_TURNOVER", "name": "应收账款周转率",
     "input_item_codes": ["TOTAL_REVENUE", "ACCOUNTS_RECEIVABLE",
                          "ACCOUNTS_RECEIVABLE_COMBINED"],
     "period_requirement": "flow/avg", "rounding": "ratio",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)；混用两口径 → "
                    "MISSING_INPUT(MIXED_RECEIVABLE_BASIS_FORBIDDEN)",
     "zero_denominator_rule": "平均余额为 0 → ZERO_DENOMINATOR",
     "proxy_rule": {"fallback": "ACCOUNTS_RECEIVABLE_COMBINED",
                    "note": "禁止一端单独、另一端组合"}},
    # ---- 现金流 / 费用 ----
    {"formula_id": "CASH_OCF_TO_NP", "name": "经营现金流/净利润",
     "input_item_codes": ["OPERATING_CASH_FLOW", "NET_PROFIT"],
     "period_requirement": "flow", "rounding": "ratio",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "CASH_OCF_TO_ASSET", "name": "现金流/总资产",
     "input_item_codes": ["OPERATING_CASH_FLOW", "TOTAL_ASSETS"],
     "period_requirement": "flow/end", "rounding": "ratio",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "CASH_OCF_TO_REV", "name": "现金流/营业收入",
     "input_item_codes": ["OPERATING_CASH_FLOW", "TOTAL_REVENUE"],
     "period_requirement": "flow", "rounding": "ratio",
     "missing_rule": "必需输入缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "EXP_PERIOD_RATE", "name": "期间费用率",
     "input_item_codes": ["SALES_EXPENSES", "ADMIN_EXPENSES", "R_AND_D_EXPENSES",
                          "FINANCE_EXPENSES", "TOTAL_REVENUE"],
     "period_requirement": "flow", "rounding": "percent",
     "missing_rule": "任一项缺失 → MISSING_INPUT",
     "zero_denominator_rule": "分母为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    # ---- 成长（正式指标仅年报同比） ----
    {"formula_id": "GROWTH_REVENUE", "name": "营收增长率",
     "input_item_codes": ["TOTAL_REVENUE"], "period_requirement": "yoy_flow",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_NET_PROFIT", "name": "净利增长率",
     "input_item_codes": ["NET_PROFIT"], "period_requirement": "yoy_flow",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_ASSET", "name": "资产增长率",
     "input_item_codes": ["TOTAL_ASSETS"], "period_requirement": "yoy_end",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年末为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_LIABILITY", "name": "负债增长率",
     "input_item_codes": ["TOTAL_LIABILITIES"], "period_requirement": "yoy_end",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年末为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_EQUITY", "name": "净资产增长率",
     "input_item_codes": ["TOTAL_EQUITY"], "period_requirement": "yoy_end",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年末为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_OCF", "name": "经营现金流增长率",
     "input_item_codes": ["OPERATING_CASH_FLOW"], "period_requirement": "yoy_flow",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_ICF", "name": "投资现金流增长率",
     "input_item_codes": ["INVESTING_CASH_FLOW"], "period_requirement": "yoy_flow",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    {"formula_id": "GROWTH_FINANCING_CASH_FLOW", "name": "筹资现金流增长率",
     "input_item_codes": ["FINANCING_CASH_FLOW"], "period_requirement": "yoy_flow",
     "rounding": "percent",
     "missing_rule": "缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)",
     "zero_denominator_rule": "上年为 0 → ZERO_DENOMINATOR", "proxy_rule": {}},
    # ---- 信用分析关键定义（§6） ----
    {"formula_id": "EBITDA", "name": "EBITDA",
     "input_item_codes": ["TOTAL_PROFIT", "INTEREST_EXPENSE", DEPRECIATION, AMORTIZATION],
     "period_requirement": "flow", "rounding": "yuan",
     "missing_rule": "任一输入（含折旧/摊销）缺失 → MISSING_INPUT",
     "zero_denominator_rule": "不适用（无分母）", "proxy_rule": {}},
    {"formula_id": "INTEREST_BEARING_DEBT", "name": "有息负债",
     "input_item_codes": ["SHORT_TERM_BORROWINGS", "LONG_TERM_BORROWINGS",
                          "BONDS_PAYABLE", "LEASE_LIABILITIES",
                          "NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR"],
     "period_requirement": "end", "rounding": "yuan",
     "missing_rule": "部分合计项缺失 → PARTIAL_INPUT（披露已纳入/缺失）",
     "zero_denominator_rule": "不适用（无分母）", "proxy_rule": {}},
    {"formula_id": "FREE_CASH_FLOW", "name": "自由现金流",
     "input_item_codes": ["OPERATING_CASH_FLOW", CAPEX],
     "period_requirement": "flow", "rounding": "yuan",
     "missing_rule": "任一输入（含 CAPEX）缺失 → MISSING_INPUT",
     "zero_denominator_rule": "不适用（无分母）", "proxy_rule": {}},
]


def build_registry(formula_version: str = FORMULA_VERSION) -> dict[str, S.FormulaDefinition]:
    """构造权威公式定义清单（纯函数，无 I/O）。"""
    registry: dict[str, S.FormulaDefinition] = {}
    for spec in _FORMULA_SPECS:
        fid = spec["formula_id"]
        if fid not in _CALLABLES:
            raise RuntimeError(f"公式 {fid} 无白名单 callable，registry 配置错误")
        registry[fid] = S.FormulaDefinition(
            formula_id=fid,
            formula_version=formula_version,
            name=spec["name"],
            input_item_codes=list(spec["input_item_codes"]),
            period_requirement=spec["period_requirement"],
            scope_requirement=SCOPE_REQUIREMENT,
            python_impl=_CALLABLES[fid].__name__,
            missing_rule=spec["missing_rule"],
            zero_denominator_rule=spec["zero_denominator_rule"],
            rounding_rule=f"{spec['rounding']}:ROUND_HALF_UP:{ROUNDING_DECIMALS}",
            impl_version=IMPL_VERSION,
            proxy_rule=dict(spec["proxy_rule"]),
            effective_at=EFFECTIVE_AT,
        )
    return registry


# 活跃公式版本（任务书 §7.1：ACTIVE_FORMULA_VERSIONS 参与快照身份）。
ACTIVE_FORMULA_VERSIONS: dict[str, str] = {
    fid: FORMULA_VERSION for fid in _CALLABLES
}

# 默认必算公式集合（快照准入默认 required_formula_ids；任务书 §6.3「已请求必算公式」）。
# 依据 CLAUDE.md §financial.metrics「必须包含至少这些指标」：流动比率、速动比率、
# 资产负债率、利息保障倍数、毛利率、净利率、ROE、ROA、营收增长率、净利增长率、
# 经营现金流/净利润。§6 三项目（EBITDA / 有息负债 / 自由现金流）为「输入可得时计算」，
# 不在默认必算集合 —— 只有被显式列入 required_formula_ids 才参与输入缺失阻断。
DEFAULT_REQUIRED_FORMULA_IDS: list[str] = [
    "SOLV_CURRENT_RATIO",
    "SOLV_QUICK_RATIO",
    "SOLV_DEBT_RATIO",
    "SOLV_INTEREST_COVER",
    "PROF_GROSS_MARGIN",
    "PROF_NET_MARGIN",
    "PROF_ROE",
    "PROF_ROA",
    "GROWTH_REVENUE",
    "GROWTH_NET_PROFIT",
    "CASH_OCF_TO_NP",
]


# ---------------------------------------------------------------------------
# 对外接口（fail-closed）
# ---------------------------------------------------------------------------

def get_formula(formula_id: str, version: str | None = None) -> S.FormulaDefinition:
    """读取公式定义。未知 formula_id → KeyError（fail-closed）。

    version 缺省取 ACTIVE_FORMULA_VERSIONS；指定版本未注册同样 KeyError。
    """
    if version is None:
        version = ACTIVE_FORMULA_VERSIONS.get(formula_id)
        if version is None:
            raise KeyError(f"未知公式: {formula_id}")
    fd = build_registry(version).get(formula_id)
    if fd is None:
        raise KeyError(f"未知公式: {formula_id} (version={version})")
    return fd


def resolve_callable(formula_id: str):
    """返回公式白名单 callable。未知 formula_id → KeyError（fail-closed）。"""
    fn = _CALLABLES.get(formula_id)
    if fn is None:
        raise KeyError(f"未知公式: {formula_id}")
    return fn


def compute_formula(formula_id: str, current: dict[str, Decimal],
                    prior: dict[str, Decimal] | None = None,
                    policy: dict | None = None,
                    period_type: str = "annual") -> FormulaOutcome:
    """纯计算入口（供 metrics 层与测试复用，不读写 DB）。"""
    fn = resolve_callable(formula_id)
    return fn(FormulaContext(current=current, prior=prior or {},
                             policy=policy or {}, period_type=period_type))


# ---------------------------------------------------------------------------
# 展示层舍入 / 单位（仅展示，raw_value 权威值不参与）
# ---------------------------------------------------------------------------

def _rounding_kind(formula_id: str) -> str:
    """返回舍入单位 {"ratio", "percent", "yuan"}（fail-closed）。"""
    return get_formula(formula_id).rounding_rule.split(":", 1)[0]


def unit_for(formula_id: str) -> str:
    """返回公式展示单位 {"ratio", "percent", "yuan"}。"""
    return _rounding_kind(formula_id)


def round_display(formula_id: str, raw: Decimal) -> Decimal:
    """按公式舍入规则（ROUND_HALF_UP，2 位小数）计算展示值。

    percent 单位展示为 ×100（如 0.1523 → 15.23）；ratio/yuan 直接 2 位小数。
    """
    kind = _rounding_kind(formula_id)
    if kind == "percent":
        return (raw * 100).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    return raw.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# 落盘（幂等）
# ---------------------------------------------------------------------------

def ensure_formulas_persisted(formula_version: str = FORMULA_VERSION) -> int:
    """幂等落盘内置公式定义（同 (formula_id, version) 已存在则忽略）。返回条数。"""
    registry = build_registry(formula_version)
    for fd in registry.values():
        store.insert_formula_definition(fd)
    return len(registry)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _formula_to_dict(fd: S.FormulaDefinition) -> dict:
    return {
        "formula_id": fd.formula_id,
        "formula_version": fd.formula_version,
        "name": fd.name,
        "input_item_codes": fd.input_item_codes,
        "period_requirement": fd.period_requirement,
        "scope_requirement": fd.scope_requirement,
        "python_impl": fd.python_impl,
        "missing_rule": fd.missing_rule,
        "zero_denominator_rule": fd.zero_denominator_rule,
        "rounding_rule": fd.rounding_rule,
        "impl_version": fd.impl_version,
        "proxy_rule": fd.proxy_rule,
        "effective_at": fd.effective_at,
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.formulas", description="A6-3 Formula Registry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部公式定义摘要")
    p_inspect = sub.add_parser("inspect", help="查看单个公式完整定义")
    p_inspect.add_argument("--formula", required=True, help="formula_id")
    p_inspect.add_argument("--version", default=None, help="公式版本（缺省活跃版本）")
    p_persist = sub.add_parser("persist", help="幂等落盘内置公式定义")
    p_persist.add_argument("--db", default=None, help="financial_v2 SQLite 路径")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        registry = build_registry()
        print(json.dumps([_formula_to_dict(fd) for fd in registry.values()],
                         ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "inspect":
        fd = get_formula(args.formula, args.version)
        print(json.dumps(_formula_to_dict(fd), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "persist":
        store.init_db(args.db or store.DEFAULT_DB_PATH)
        n = ensure_formulas_persisted()
        print(json.dumps({"persisted": n, "formula_version": FORMULA_VERSION},
                         ensure_ascii=False, indent=2))
        return 0

    parser.error("未知子命令")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
