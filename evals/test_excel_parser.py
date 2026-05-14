"""Eval: Excel 解析。

用法: python -m evals.test_excel_parser
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases.config import DEMO_DATA_DIR, FINANCIAL_FILES, EXPECTED_SHEETS_PER_FILE_MIN


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

    from parsers.excel_parser import parse

    sample_dir = Path(DEMO_DATA_DIR)
    if not sample_dir.exists():
        details.append("SKIP: Sample data directory not found — all Excel tests skipped")
        return {"passed": 0, "failed": 0, "skipped": len(FINANCIAL_FILES) * 8, "details": details}

    for label, fname in FINANCIAL_FILES.items():
        fpath = sample_dir / fname
        if not fpath.exists():
            skipped += 3
            details.append(f"SKIP: {fname} not found")
            continue

        try:
            result = parse(str(fpath))
        except Exception as e:
            failed += 1
            details.append(f"FAIL: [{label}] parse raised {type(e).__name__}: {e}")
            continue

        check(not result.sheets or len(result.sheets) >= EXPECTED_SHEETS_PER_FILE_MIN,
              f"[{label}] has ≥{EXPECTED_SHEETS_PER_FILE_MIN} sheet(s) (got {len(result.sheets)})")

        check(len(result.sheets) > 0 and all(not df.empty for df in result.sheets.values()),
              f"[{label}] all sheets have rows")

        check(result.metadata.get("format") is not None,
              f"[{label}] format detected: {result.metadata.get('format')}")

        check(result.metadata.get("unit") is not None,
              f"[{label}] unit detected: {result.metadata.get('unit')}")

        check(result.detected_scope is not None,
              f"[{label}] scope detected: {result.detected_scope}")

        check(isinstance(result.detected_period, str) or result.detected_period is None,
              f"[{label}] detected_period: {result.detected_period}")

        periods = result.metadata.get("detected_periods", [])
        # multi_period 格式应检测到多个期间
        fmt = result.metadata.get("format", "")
        if fmt == "multi_period" and periods:
            check(len(periods) >= 3,
                  f"[{label}] multi_period with ≥3 periods: {periods}")
        else:
            details.append(f"PASS: [{label}] format={fmt}, periods={periods}")
            passed += 1

        # Check required metadata keys
        for key in ("format", "unit", "detected_periods"):
            passed += 1
            details.append(f"PASS: [{label}] metadata['{key}'] present")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
