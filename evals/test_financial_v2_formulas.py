"""Eval: financial_v2 Formula Registry + 白名单 callable（A6 Commit 4：formulas.py）。

用法: python -m evals.test_financial_v2_formulas

覆盖（任务书 §7 / §8.2-8.3 / §13 公式矩阵）：
- Registry 完整性：28 项（FORMULA_REVIEW §1-5 的 25 项 + §6 的 EBITDA/有息负债/自由现金流）；
- 每个公式正常、缺输入、代理、部分输入、零分母、无前期、不适用状态；
- fail-closed：未知公式 KeyError；
- 全程 Decimal（raw_value 未舍入）、缺输入 ≠ 0、代理显式 reason_code、季报不生成正式增长率；
- 应收账款周转率：组合口径回退 + 禁止混用；
- 速动比率条件扣除（policy 解析后的非速动 OTHER_CURRENT_ASSETS 金额）；
- 展示层 ROUND_HALF_UP 舍入边界；
- 公式定义幂等落盘 + 读回。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import formulas as F
from financial_v2 import store


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_formula_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


_CURRENT: dict[str, Decimal] = {
    "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
    "TOTAL_LIABILITIES": Decimal("300"), "TOTAL_ASSETS": Decimal("500"),
    "TOTAL_EQUITY": Decimal("200"), "INVENTORY": Decimal("50"),
    "PREPAYMENTS": Decimal("10"), "OTHER_CURRENT_ASSETS": Decimal("40"),
    "TOTAL_REVENUE": Decimal("1000"), "OPERATING_COST": Decimal("600"),
    "NET_PROFIT": Decimal("120"), "OPERATING_PROFIT": Decimal("160"),
    "TOTAL_PROFIT": Decimal("150"), "INTEREST_EXPENSE": Decimal("20"),
    "FINANCE_EXPENSES": Decimal("30"), "SALES_EXPENSES": Decimal("40"),
    "ADMIN_EXPENSES": Decimal("50"), "R_AND_D_EXPENSES": Decimal("10"),
    "OPERATING_CASH_FLOW": Decimal("200"), "INVESTING_CASH_FLOW": Decimal("-80"),
    "FINANCING_CASH_FLOW": Decimal("-40"),
    "ACCOUNTS_RECEIVABLE": Decimal("90"), "ACCOUNTS_RECEIVABLE_COMBINED": Decimal("110"),
    "SHORT_TERM_BORROWINGS": Decimal("50"), "LONG_TERM_BORROWINGS": Decimal("80"),
    "BONDS_PAYABLE": Decimal("30"), "LEASE_LIABILITIES": Decimal("10"),
    "NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR": Decimal("20"),
    F.DEPRECIATION: Decimal("25"), F.AMORTIZATION: Decimal("5"),
    F.CAPEX: Decimal("60"),
}

_PRIOR: dict[str, Decimal] = {
    "TOTAL_ASSETS": Decimal("400"), "INVENTORY": Decimal("40"),
    "ACCOUNTS_RECEIVABLE": Decimal("70"), "ACCOUNTS_RECEIVABLE_COMBINED": Decimal("90"),
    "TOTAL_REVENUE": Decimal("800"), "NET_PROFIT": Decimal("100"),
    "TOTAL_LIABILITIES": Decimal("250"), "TOTAL_EQUITY": Decimal("150"),
    "OPERATING_CASH_FLOW": Decimal("150"), "INVESTING_CASH_FLOW": Decimal("-60"),
    "FINANCING_CASH_FLOW": Decimal("-30"),
}

# 全输入时每个公式的期望 raw_value（未舍入）。
_EXPECTED: dict[str, str] = {
    "SOLV_CURRENT_RATIO": "2",
    "SOLV_QUICK_RATIO": "1.4",
    "SOLV_DEBT_RATIO": "0.6",
    "SOLV_INTEREST_COVER": "8.5",
    "SOLV_EQUITY_MULT": "2.5",
    "PROF_GROSS_MARGIN": "0.4",
    "PROF_NET_MARGIN": "0.12",
    "PROF_ROE": "0.6",
    "PROF_ROA": "0.24",
    "PROF_OPER_MARGIN": "0.16",
    "OPER_ASSET_TURNOVER": "2.222222222222222222",
    "OPER_INV_TURNOVER": "13.33333333333333333",
    "OPER_AR_TURNOVER": "12.5",
    "CASH_OCF_TO_NP": "1.666666666666666667",
    "CASH_OCF_TO_ASSET": "0.4",
    "CASH_OCF_TO_REV": "0.2",
    "EXP_PERIOD_RATE": "0.13",
    "GROWTH_REVENUE": "0.25",
    "GROWTH_NET_PROFIT": "0.2",
    "GROWTH_ASSET": "0.25",
    "GROWTH_LIABILITY": "0.2",
    "GROWTH_EQUITY": "0.3333333333333333333",
    "GROWTH_OCF": "0.3333333333333333333",
    "GROWTH_ICF": "-0.3333333333333333333",
    "GROWTH_FINANCING_CASH_FLOW": "-0.3333333333333333333",
    "EBITDA": "200",
    "INTEREST_BEARING_DEBT": "190",
    "FREE_CASH_FLOW": "140",
}


def _close(a: Decimal, b: Decimal, eps: str = "0.000001") -> bool:
    return abs(a - b) < Decimal(eps)


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

    # ---- Registry 完整性 ----
    reg = F.build_registry()
    check(len(reg) == 28, f"registry 定义 28 项（实际 {len(reg)}）")
    check(len(F._CALLABLES) == 28, f"白名单 callable 28 项（实际 {len(F._CALLABLES)}）")
    check(len(F.ACTIVE_FORMULA_VERSIONS) == 28, "ACTIVE_FORMULA_VERSIONS 28 项")
    check(set(reg) == set(F._CALLABLES) == set(_EXPECTED),
          "registry/callable/期望三集合一致")
    check(set(F.ACTIVE_FORMULA_VERSIONS.values()) == {F.FORMULA_VERSION},
          "活跃版本均为 FORMULA_VERSION")
    check(all(fd.scope_requirement == "consolidated" for fd in reg.values()),
          "全部公式 scope_requirement=consolidated")
    check(all(fd.impl_version == F.IMPL_VERSION for fd in reg.values()),
          "全部公式 impl_version 与口径版本分离")
    check(all(fd.python_impl == F._CALLABLES[fd.formula_id].__name__ for fd in reg.values()),
          "python_impl 指向白名单 callable 名")
    check(all(fd.rounding_rule.endswith(":ROUND_HALF_UP:2") for fd in reg.values()),
          "全部公式舍入规则 ROUND_HALF_UP:2")

    # ---- fail-closed ----
    for fn in (lambda: F.get_formula("NOPE"), lambda: F.resolve_callable("NOPE"),
               lambda: F.compute_formula("NOPE", {})):
        raised = False
        try:
            fn()
        except KeyError:
            raised = True
        check(raised, "未知公式 → KeyError（fail-closed）")

    # ---- 每个公式：正常 + 期望值（全程 Decimal，raw 未舍入） ----
    for fid, exp_s in _EXPECTED.items():
        out = F.compute_formula(fid, _CURRENT, _PRIOR)
        exp = Decimal(exp_s)
        check(out.status == "CALCULATED_EXACT", f"{fid} 全输入 → CALCULATED_EXACT")
        if out.raw_value is not None:
            check(_close(out.raw_value, exp), f"{fid} raw_value ≈ {exp_s}（得 {out.raw_value}）")

    # ---- 缺输入 ≠ 0：抽样（比率 / 求和 / 代理输入缺失） ----
    miss = F.compute_formula("SOLV_CURRENT_RATIO", {"CURRENT_ASSETS": Decimal("200")})
    check(miss.status == "MISSING_INPUT" and miss.reason_code == "MISSING_REQUIRED_ITEM"
          and miss.raw_value is None, "流动比率缺分母 → MISSING_INPUT（不当 0）")

    miss = F.compute_formula("EBITDA", {"TOTAL_PROFIT": Decimal("150"),
                                        "INTEREST_EXPENSE": Decimal("20")})
    check(miss.status == "MISSING_INPUT" and miss.raw_value is None,
          "EBITDA 缺折旧/摊销 → MISSING_INPUT（不补算）")

    miss = F.compute_formula("FREE_CASH_FLOW", {"OPERATING_CASH_FLOW": Decimal("200")})
    check(miss.status == "MISSING_INPUT" and miss.raw_value is None,
          "FCF 缺 CAPEX → MISSING_INPUT（不补算）")

    miss = F.compute_formula("EXP_PERIOD_RATE", {k: _CURRENT[k] for k in _CURRENT
                                                 if k not in ("R_AND_D_EXPENSES",)})
    check(miss.status == "MISSING_INPUT", "期间费用率任一项缺失 → MISSING_INPUT")

    # ---- 代理：利息保障倍数 ----
    proxy = F.compute_formula("SOLV_INTEREST_COVER",
                              {"TOTAL_PROFIT": Decimal("150"),
                               "FINANCE_EXPENSES": Decimal("30")})
    check(proxy.status == "CALCULATED_PROXY"
          and proxy.reason_code == "PROXY_FINANCE_EXPENSES"
          and _close(proxy.raw_value, Decimal("6")),
          "利息保障倍数缺真实利息费用 + 有财务费用 → CALCULATED_PROXY")

    miss = F.compute_formula("SOLV_INTEREST_COVER", {"TOTAL_PROFIT": Decimal("150")})
    check(miss.status == "MISSING_INPUT", "利息保障倍数两分母均缺 → MISSING_INPUT")

    # ---- 部分输入：有息负债 ----
    partial_cur = {k: _CURRENT[k] for k in
                   ("SHORT_TERM_BORROWINGS", "LONG_TERM_BORROWINGS", "BONDS_PAYABLE")}
    partial = F.compute_formula("INTEREST_BEARING_DEBT", partial_cur)
    check(partial.status == "PARTIAL_INPUT" and partial.raw_value is None
          and partial.detail.get("included") is not None and partial.detail.get("missing"),
          "有息负债部分合计项缺失 → PARTIAL_INPUT（披露已纳入/缺失）")

    # ---- 零分母 ----
    zero = F.compute_formula("SOLV_CURRENT_RATIO",
                             {"CURRENT_ASSETS": Decimal("200"),
                              "CURRENT_LIABILITIES": Decimal("0")})
    check(zero.status == "ZERO_DENOMINATOR" and zero.raw_value is None,
          "流动比率分母 0 → ZERO_DENOMINATOR")

    zero = F.compute_formula("GROWTH_REVENUE", {"TOTAL_REVENUE": Decimal("120")},
                             {"TOTAL_REVENUE": Decimal("0")})
    check(zero.status == "ZERO_DENOMINATOR", "增长率上年为 0 → ZERO_DENOMINATOR")

    # ---- 无前期：周转率 + 增长率 ----
    miss = F.compute_formula("OPER_ASSET_TURNOVER",
                             {"TOTAL_REVENUE": Decimal("1000"),
                              "TOTAL_ASSETS": Decimal("500")})
    check(miss.status == "MISSING_INPUT" and miss.reason_code == "MISSING_PRIOR_PERIOD",
          "周转率缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)")

    miss = F.compute_formula("GROWTH_REVENUE", {"TOTAL_REVENUE": Decimal("120")})
    check(miss.status == "MISSING_INPUT" and miss.reason_code == "MISSING_PRIOR_PERIOD",
          "增长率缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)")

    # ---- 不适用：季报增长率 ----
    na = F.compute_formula("GROWTH_REVENUE", {"TOTAL_REVENUE": Decimal("120")},
                           {"TOTAL_REVENUE": Decimal("100")}, period_type="quarterly")
    check(na.status == "NOT_APPLICABLE" and na.raw_value is None,
          "季报请求正式增长率 → NOT_APPLICABLE")

    # ---- 应收账款周转率：组合回退 + 禁止混用 ----
    fb = F.compute_formula("OPER_AR_TURNOVER",
                           {"TOTAL_REVENUE": Decimal("1000"),
                            "ACCOUNTS_RECEIVABLE_COMBINED": Decimal("110")},
                           {"ACCOUNTS_RECEIVABLE_COMBINED": Decimal("90")})
    check(fb.status == "CALCULATED_EXACT" and _close(fb.raw_value, Decimal("10")),
          "应收周转率组合口径整体回退 → 10")

    mixed = F.compute_formula("OPER_AR_TURNOVER",
                              {"TOTAL_REVENUE": Decimal("1000"),
                               "ACCOUNTS_RECEIVABLE": Decimal("90")},
                              {"ACCOUNTS_RECEIVABLE_COMBINED": Decimal("90")})
    check(mixed.status == "MISSING_INPUT"
          and mixed.reason_code == "MIXED_RECEIVABLE_BASIS_FORBIDDEN",
          "应收周转率一端单独一端组合 → 混用禁止")

    # ---- 速动比率条件扣除 ----
    qr_none = F.compute_formula("SOLV_QUICK_RATIO", _CURRENT)
    check(qr_none.status == "CALCULATED_EXACT" and _close(qr_none.raw_value, Decimal("1.4"))
          and qr_none.detail.get("excluded_other_current_asset_applied") is False,
          "速动比率默认不机械扣除 OTHER_CURRENT_ASSETS")

    qr_pol = F.compute_formula("SOLV_QUICK_RATIO", _CURRENT,
                               policy={"quick_ratio_excluded_other_current_asset_amount":
                                       Decimal("40")})
    check(qr_pol.status == "CALCULATED_EXACT" and _close(qr_pol.raw_value, Decimal("1.0"))
          and qr_pol.detail.get("excluded_other_current_asset_applied") is True,
          "速动比率经确认非速动 OTHER_CURRENT_ASSETS 追加扣除")

    # ---- 展示层舍入 ROUND_HALF_UP 边界（不反向写回 raw） ----
    check(F.round_display("SOLV_CURRENT_RATIO", Decimal("2.345")) == Decimal("2.35"),
          "ratio 2.345 → 2.35（ROUND_HALF_UP）")
    check(F.round_display("SOLV_CURRENT_RATIO", Decimal("2.344")) == Decimal("2.34"),
          "ratio 2.344 → 2.34（ROUND_HALF_UP）")
    check(F.round_display("PROF_ROE", Decimal("0.152345")) == Decimal("15.23"),
          "percent 0.152345 → 15.23（×100 + ROUND_HALF_UP）")
    check(F.unit_for("PROF_ROE") == "percent" and F.unit_for("SOLV_CURRENT_RATIO") == "ratio"
          and F.unit_for("EBITDA") == "yuan", "unit_for 分类正确")

    # ---- 落盘 + 读回（临时库） ----
    db = _tmp_db()
    try:
        store.init_db(db)
        n1 = F.ensure_formulas_persisted()
        check(n1 == 28, f"ensure_formulas_persisted 落盘 28 条（实际 {n1}）")
        all_rows = store.list_formula_definitions()
        check(len(all_rows) == 28, f"list_formula_definitions 读回 28 条（实际 {len(all_rows)}）")
        fd = store.get_formula_definition("SOLV_INTEREST_COVER", F.FORMULA_VERSION)
        check(fd is not None and fd.proxy_rule.get("reason_code") == "PROXY_FINANCE_EXPENSES",
              "公式定义读回 proxy_rule 正确")
        fd = store.get_formula_definition("SOLV_QUICK_RATIO", F.FORMULA_VERSION)
        check(fd is not None and "OTHER_CURRENT_ASSETS" in fd.input_item_codes,
              "速动比率输入科目含 OTHER_CURRENT_ASSETS")
        n2 = F.ensure_formulas_persisted()
        check(n2 == 28 and len(store.list_formula_definitions()) == 28,
              "重复落盘幂等（INSERT OR IGNORE）不产生重复行")
    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
