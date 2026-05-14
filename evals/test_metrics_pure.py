"""Eval: 指标计算纯函数（_safe_div, _growth_rate, compute_all with mock）。

用法: python -m evals.test_metrics_pure
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases.config import EXPECTED_MIN_METRIC_COUNT, EXPECTED_MIN_GROWTH_COUNT


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ── _safe_div tests ──
    from financial.metrics import _safe_div

    check(_safe_div(10, 2) == 5.0, "_safe_div(10, 2) == 5.0")
    check(_safe_div(10, None) is None, "_safe_div(10, None) is None")
    check(_safe_div(None, 2) is None, "_safe_div(None, 2) is None")
    check(_safe_div(10, 0) is None, "_safe_div(10, 0) is None")
    check(_safe_div(0, 10) == 0.0, "_safe_div(0, 10) == 0.0")

    # ── _growth_rate tests ──
    from financial.metrics import _growth_rate

    check(abs(_growth_rate(120, 100) - 0.2) < 1e-9, "_growth_rate(120, 100) ≈ 0.2")
    check(abs(_growth_rate(80, 100) - (-0.2)) < 1e-9, "_growth_rate(80, 100) ≈ -0.2")
    check(_growth_rate(None, 100) is None, "_growth_rate(None, 100) is None")
    check(_growth_rate(100, None) is None, "_growth_rate(100, None) is None")
    check(_growth_rate(100, 0) is None, "_growth_rate(100, 0) is None")  # zero base

    # ── compute_all with mock ──
    from financial.metrics import compute_all
    from unittest.mock import patch

    # Use the actual schema code values (Chinese strings) as keys
    from financial.schema import (
        TOTAL_ASSETS, CURRENT_ASSETS, TOTAL_LIABILITIES, CURRENT_LIABILITIES,
        TOTAL_EQUITY, INVENTORY, TOTAL_REVENUE, OPERATING_COST, NET_PROFIT,
        TOTAL_PROFIT, FINANCE_EXPENSES, OPERATING_CASH_FLOW,
        INVESTING_CASH_FLOW, FINANCING_CASH_FLOW,
        OPERATING_PROFIT, SALES_EXPENSES, ADMIN_EXPENSES, R_AND_D_EXPENSES,
        ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED,
    )

    synthetic_data = {
        TOTAL_ASSETS: {"2022-12-31": 1000, "2023-12-31": 1200, "2024-12-31": 1500, "2025-12-31": 1800},
        CURRENT_ASSETS: {"2022-12-31": 600, "2023-12-31": 720, "2024-12-31": 900, "2025-12-31": 1080},
        TOTAL_LIABILITIES: {"2022-12-31": 500, "2023-12-31": 600, "2024-12-31": 750, "2025-12-31": 900},
        CURRENT_LIABILITIES: {"2022-12-31": 300, "2023-12-31": 360, "2024-12-31": 450, "2025-12-31": 540},
        TOTAL_EQUITY: {"2022-12-31": 500, "2023-12-31": 600, "2024-12-31": 750, "2025-12-31": 900},
        INVENTORY: {"2022-12-31": 100, "2023-12-31": 120, "2024-12-31": 150, "2025-12-31": 180},
        TOTAL_REVENUE: {"2022-12-31": 800, "2023-12-31": 960, "2024-12-31": 1200, "2025-12-31": 1440},
        OPERATING_COST: {"2022-12-31": 500, "2023-12-31": 600, "2024-12-31": 750, "2025-12-31": 900},
        NET_PROFIT: {"2022-12-31": 100, "2023-12-31": 120, "2024-12-31": 150, "2025-12-31": 180},
        TOTAL_PROFIT: {"2022-12-31": 130, "2023-12-31": 155, "2024-12-31": 195, "2025-12-31": 234},
        FINANCE_EXPENSES: {"2022-12-31": 10, "2023-12-31": 12, "2024-12-31": 15, "2025-12-31": 18},
        OPERATING_CASH_FLOW: {"2022-12-31": 90, "2023-12-31": 110, "2024-12-31": 140, "2025-12-31": 170},
        INVESTING_CASH_FLOW: {"2022-12-31": -50, "2023-12-31": -60, "2024-12-31": -75, "2025-12-31": -90},
        FINANCING_CASH_FLOW: {"2022-12-31": -20, "2023-12-31": -25, "2024-12-31": -30, "2025-12-31": -35},
        OPERATING_PROFIT: {"2022-12-31": 140, "2023-12-31": 165, "2024-12-31": 210, "2025-12-31": 252},
        SALES_EXPENSES: {"2022-12-31": 30, "2023-12-31": 36, "2024-12-31": 45, "2025-12-31": 54},
        ADMIN_EXPENSES: {"2022-12-31": 25, "2023-12-31": 30, "2024-12-31": 38, "2025-12-31": 45},
        R_AND_D_EXPENSES: {"2022-12-31": 35, "2023-12-31": 42, "2024-12-31": 52, "2025-12-31": 63},
        ACCOUNTS_RECEIVABLE: {"2022-12-31": 80, "2023-12-31": 96, "2024-12-31": 120, "2025-12-31": 144},
        ACCOUNTS_RECEIVABLE_COMBINED: {"2022-12-31": 80, "2023-12-31": 96, "2024-12-31": 120, "2025-12-31": 144},
    }

    def mock_query_metric(cid, code, period):
        return synthetic_data.get(code, {}).get(period)

    periods = ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]

    # Patch financial.db.query_metric (where _fetch actually looks it up)
    with patch("financial.metrics.query_metric", side_effect=mock_query_metric):
        result = compute_all("test", periods)

    # Check by_period
    check(len(result.by_period) == 4, f"by_period has 4 periods (got {len(result.by_period)})")

    p_last = result.by_period["2025-12-31"]
    check(len(p_last) >= EXPECTED_MIN_METRIC_COUNT,
          f"last period has ≥{EXPECTED_MIN_METRIC_COUNT} metrics (got {len(p_last)})")

    # Key invariants with synthetic data
    # 流动比率 > 速动比率 (因为库存 > 0)
    for p in periods:
        cr = result.by_period[p].get("流动比率")
        qr = result.by_period[p].get("速动比率")
        if cr is not None and qr is not None:
            check(cr > qr, f"{p}: 流动比率({cr:.4f}) > 速动比率({qr:.4f})")

    # 资产负债率 = TL / TA = 900/1800 = 0.5
    dta = result.by_period["2025-12-31"].get("资产负债率")
    check(dta is not None and abs(dta - 0.5) < 0.01,
          f"资产负债率 ~ 0.5 (got {dta})")

    # ROE = NP / TE = 180/900 = 0.2
    roe = result.by_period["2025-12-31"].get("ROE")
    check(roe is not None and abs(roe - 0.2) < 0.01,
          f"ROE ~ 0.2 (got {roe})")

    # No NaN values
    nan_count = 0
    for p, metrics in result.by_period.items():
        for name, val in metrics.items():
            if val is not None:
                import math
                if math.isnan(val):
                    nan_count += 1
    check(nan_count == 0, f"No NaN in any metric value (found {nan_count})")

    # yoy_changes
    check(len(result.yoy_changes) >= 3,
          f"yoy_changes has ≥3 entries for 4 annual periods (got {len(result.yoy_changes)})")

    # Verify growth rate: revenue 2024 vs 2023 = (1200-960)/960 = 0.25
    y2024 = result.yoy_changes.get("2024-12-31", {})
    if y2024:
        rev_growth = y2024.get("营收增长率")
        if rev_growth is not None:
            check(abs(rev_growth - 0.25) < 0.01,
                  f"2024 营收增长率 ~ 0.25 (got {rev_growth:.4f})")

    # Verify number of growth metrics
    growth_names = set()
    for changes in result.yoy_changes.values():
        growth_names.update(changes.keys())
    check(len(growth_names) >= EXPECTED_MIN_GROWTH_COUNT,
          f"≥{EXPECTED_MIN_GROWTH_COUNT} growth rate types (got {len(growth_names)}: {growth_names})")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
