"""Eval: metrics 集成（种子 DB）。

用法: python -m evals.test_metrics_db
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases.config import (
    COMPANY_ID, EXPECTED_MIN_METRIC_COUNT, EXPECTED_PERIODS_MIN,
    MIN_CURRENT_RATIO, MAX_CURRENT_RATIO,
)
from evals.conftest import seed_in_memory_db


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

    # Seed DB
    try:
        db_path, files_loaded = seed_in_memory_db(COMPANY_ID)
    except Exception as e:
        details.append(f"SKIP: Cannot seed DB: {type(e).__name__}: {e}")
        return {"passed": 0, "failed": 0, "skipped": 1, "details": details}

    if files_loaded == 0:
        os.unlink(db_path)
        details.append("SKIP: No sample files found for seeding")
        return {"passed": 0, "failed": 0, "skipped": 1, "details": details}

    # Switch financial.db to use this temp DB
    import financial.db as fdb
    fdb._db_path = Path(db_path)

    try:
        from financial.metrics import compute_all
        from financial.db import list_periods, query_metric

        periods = list_periods(COMPANY_ID)
        check(len(periods) >= EXPECTED_PERIODS_MIN,
              f"periods count ≥ {EXPECTED_PERIODS_MIN} (got {len(periods)}: {periods})")

        result = compute_all(COMPANY_ID, periods)

        check(len(result.by_period) == len(periods),
              f"by_period has {len(result.by_period)} entries (matching {len(periods)} periods)")

        for p in periods:
            metrics = result.by_period.get(p, {})
            metric_count = sum(1 for v in metrics.values() if v is not None)
            # First period has no prior-period turnover ratios (3 metrics None);
            # also 利息保障倍数/经营现金流 could be None. Threshold = 12 is safe.
            check(metric_count >= 12,
                  f"[{p}] ≥12 non-None metrics (got {metric_count})")

        # Ratio bounds
        for p in periods:
            cr = result.by_period[p].get("流动比率")
            if cr is not None:
                check(MIN_CURRENT_RATIO <= cr <= MAX_CURRENT_RATIO,
                      f"[{p}] 流动比率={cr:.4f} in [{MIN_CURRENT_RATIO}, {MAX_CURRENT_RATIO}]")

            dta = result.by_period[p].get("资产负债率")
            if dta is not None:
                check(0 <= dta <= 1.5,
                      f"[{p}] 资产负债率={dta:.4f} in [0, 1.5]")

            roe = result.by_period[p].get("ROE")
            if roe is not None:
                check(-2.0 <= roe <= 2.0,
                      f"[{p}] ROE={roe:.4f} in [-2, 2]")

        # No NaN in results
        import math
        nan_count = 0
        for p, metrics in result.by_period.items():
            for name, val in metrics.items():
                if val is not None and math.isnan(val):
                    nan_count += 1
        check(nan_count == 0, f"No NaN values in metrics (found {nan_count})")

        # Growth rates computed
        check(len(result.yoy_changes) > 0,
              f"yoy_changes has entries (got {len(result.yoy_changes)} periods)")

        # Total assets should be positive (billions for 宁德时代)
        for p in periods:
            ta = query_metric(COMPANY_ID, "TOTAL_ASSETS", p)
            if ta is not None:
                check(ta > 0, f"[{p}] TOTAL_ASSETS > 0 ({ta:,.0f})")

    finally:
        fdb._db_path = None
        import os
        try:
            os.unlink(db_path)
        except OSError:
            pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
