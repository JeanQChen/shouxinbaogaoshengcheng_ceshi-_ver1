"""Eval: DB target resolver（确定性、fail-closed、无 LLM）—— Phase 2 Commit 2。

用法: python -m evals.test_db_targets

覆盖（契约修正 A/C）：
- 单字段 / 单指标确定性解析；
- 嵌套别名消解（归母净利润 ⊃ 净利润）；
- 多指标歧义返回 None（不越级到字段）；
- 非财务问题返回 None；
- supported 注册表来源（Mapping Registry / Formula Registry）稳定非空。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import db_targets as T


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

    def resolve(q):
        return T.resolve_db_target(q)

    # ---- 单字段 ----
    t = resolve("2024 年总资产是多少？")
    check(t is not None and t.target_type == "field"
          and t.standard_item_code == "TOTAL_ASSETS", "总资产 → TOTAL_ASSETS 字段")

    t = resolve("2025 年归母净利润是多少？")
    check(t is not None and t.standard_item_code == "NET_PROFIT_PARENT",
          "归母净利润 → NET_PROFIT_PARENT（嵌套消解，不被净利润抢注）")

    t = resolve("经营活动现金流量净额是多少？")
    check(t is not None and t.standard_item_code == "OPERATING_CASH_FLOW",
          "经营现金流 → OPERATING_CASH_FLOW")

    # ---- 单指标 ----
    t = resolve("净资产收益率是多少？")
    check(t is not None and t.target_type == "metric"
          and t.formula_id == "PROF_ROE" and t.formula_version is not None,
          "净资产收益率 → PROF_ROE 指标（含 formula_version）")

    t = resolve("毛利率是多少？")
    check(t is not None and t.target_type == "metric"
          and t.formula_id == "PROF_GROSS_MARGIN", "毛利率 → PROF_GROSS_MARGIN")

    # ---- 多指标歧义 → None（不越级到字段） ----
    t = resolve("流动比率、速动比率、资产负债率分别是多少？")
    check(t is None, "多指标歧义返回 None（不拆成「负债」字段）")

    # ---- 非财务 → None ----
    check(resolve("公司主营业务是什么？") is None, "非财务问题返回 None")
    check(resolve("公司成立于哪一年？") is None, "成立年份返回 None")
    check(resolve("") is None, "空问题返回 None")
    check(resolve("   ") is None, "空白问题返回 None")

    # ---- 子类/明细语义：受限资金 ≠ 货币资金总额 ----
    t = resolve("2025年合并口径下，货币资金中受限资金占比多少？")
    check(t is None, "受限资金 → 不映射到货币资金总额（CASH_AND_EQUIVALENTS），fail-closed")
    t = resolve("受限货币资金是多少？")
    check(t is None, "受限货币资金 → None（子类，非聚合）")
    t = resolve("货币资金中质押的保证金有多少？")
    check(t is None, "货币资金中质押/保证金 → None（受限子类）")

    # ---- 子类/明细语义：机器设备净值 ≠ 固定资产总额 ----
    t = resolve("2025年合并口径下，固定资产中机器设备的年末净值是多少？")
    check(t is None, "机器设备净值 → 不映射到固定资产总额（FIXED_ASSETS），fail-closed")
    t = resolve("固定资产中房屋及建筑物的原值是多少？")
    check(t is None, "房屋及建筑物 → None（固定资产子类）")

    # ---- 精确字段仍可 DB（无子类阻断词）----
    t = resolve("2025年货币资金总额是多少？")
    check(t is not None and t.standard_item_code == "CASH_AND_EQUIVALENTS",
          "精确「货币资金总额」仍映射 CASH_AND_EQUIVALENTS（无受限子类）")
    t = resolve("2025年固定资产原值是多少？")
    check(t is not None and t.standard_item_code == "FIXED_ASSETS",
          "精确「固定资产原值」仍映射 FIXED_ASSETS（无子类阻断词）")

    # ---- 子类担保 → 不映射到任何「全部对外担保」聚合（fail-closed）----
    t = resolve("为股东及实际控制人提供担保的金额是多少？")
    check(t is None, "子类担保（为股东）→ 不映射到任何担保聚合字段（DB 无此字段，fail-closed）")

    # ---- supported 注册表 ----
    fields = T.supported_db_fields()
    metrics = T.supported_metric_ids()
    check("TOTAL_ASSETS" in fields and "NET_PROFIT" in fields,
          "supported_db_fields 来自 Mapping Registry（含 TOTAL_ASSETS/NET_PROFIT）")
    check("PROF_ROE" in metrics and "SOLV_CURRENT_RATIO" in metrics,
          "supported_metric_ids 来自 Formula Registry（含 PROF_ROE/SOLV_CURRENT_RATIO）")
    check(len(fields) > 20 and len(metrics) >= 11,
          f"注册表规模合理（fields={len(fields)}, metrics={len(metrics)}）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
