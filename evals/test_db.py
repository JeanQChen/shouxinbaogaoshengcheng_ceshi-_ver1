"""Eval: 数据库 CRUD。

用法: python -m evals.test_db
"""

import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsers.schema_mapper import NormalizedRow


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

    import os
    import tempfile

    from financial.db import init_db, insert_report, query_metric, list_periods, get_company_info

    # Use temp file DB (NOT :memory: — SQLite :memory: is per-connection,
    # and insert_report opens a new connection that wouldn't see init_db's tables)
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="eval_db_")
    os.close(fd)
    init_db(tmp_path)

    # Verify tables exist
    import sqlite3
    from financial.db import _get_conn
    conn = _get_conn()
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {r["name"] for r in tables}
        for t in ("report_meta", "balance_sheet", "income_statement", "cash_flow"):
            check(t in table_names, f"Table '{t}' exists in sqlite_master")
    finally:
        conn.close()

    # Insert test data
    meta = {
        "company_id": "300750",
        "company_name": "宁德时代",
        "stock_code": "300750",
        "report_type": "annual",
        "statement_scope": "consolidated",
        "source_file": "test.xlsx",
    }

    rows = [
        NormalizedRow(
            item_code="TOTAL_ASSETS", item_name_cn="资产总计", amount=100_000_000_000_000,
            period="2024-12-31", statement_type="balance_sheet", category="asset",
        ),
        NormalizedRow(
            item_code="TOTAL_LIABILITIES", item_name_cn="负债合计", amount=60_000_000_000_000,
            period="2024-12-31", statement_type="balance_sheet", category="liability",
        ),
        NormalizedRow(
            item_code="TOTAL_EQUITY", item_name_cn="所有者权益合计", amount=40_000_000_000_000,
            period="2024-12-31", statement_type="balance_sheet", category="equity",
        ),
        NormalizedRow(
            item_code="TOTAL_REVENUE", item_name_cn="营业总收入", amount=50_000_000_000_000,
            period="2024-12-31", statement_type="income_statement", category="revenue",
        ),
        NormalizedRow(
            item_code="TOTAL_ASSETS", item_name_cn="资产总计", amount=120_000_000_000_000,
            period="2025-12-31", statement_type="balance_sheet", category="asset",
        ),
        NormalizedRow(
            item_code="NET_PROFIT", item_name_cn="净利润", amount=float('nan'),
            period="2024-12-31", statement_type="income_statement", category="profit",
        ),
        NormalizedRow(
            item_code="NET_PROFIT", item_name_cn="净利润", amount=float('inf'),
            period="2024-12-31", statement_type="income_statement", category="profit",
        ),
    ]

    report_id = insert_report(meta, rows)
    check(len(report_id) == 12, f"insert_report returns 12-char ID (got '{report_id}')")

    # query_metric
    val = query_metric("300750", "TOTAL_ASSETS", "2024-12-31")
    check(val is not None and abs(val - 100_000_000_000_000) < 1,
          f"query_metric TOTAL_ASSETS 2024-12-31 ≈ 100万亿 (got {val})")

    # query_metric should also work with Chinese name
    val_cn = query_metric("300750", "资产总计", "2024-12-31")
    check(val_cn is not None and abs(val_cn - 100_000_000_000_000) < 1,
          f"query_metric by Chinese name returns correct value (got {val_cn})")

    # Cross-table query: TOTAL_REVENUE in income_statement
    val_rev = query_metric("300750", "TOTAL_REVENUE", "2024-12-31")
    check(val_rev is not None and abs(val_rev - 50_000_000_000_000) < 1,
          f"query_metric TOTAL_REVENUE cross-table (got {val_rev})")

    # NaN should be filtered (not inserted)
    val_nan = query_metric("300750", "NET_PROFIT", "2024-12-31")
    check(val_nan is None, f"NaN amount filtered: query returns None (got {val_nan})")

    # Inf should be filtered
    val_inf = query_metric("300750", "NET_PROFIT", "2024-12-31")
    check(val_inf is None, f"Inf amount filtered (got {val_inf})")

    # Missing query returns None
    val_missing = query_metric("300750", "NONEXISTENT_CODE", "2024-12-31")
    check(val_missing is None, f"Non-existent code returns None (got {val_missing})")

    # list_periods
    periods = list_periods("300750")
    check(len(periods) >= 2, f"list_periods returns ≥2 periods (got {len(periods)}: {periods})")
    check(periods == sorted(periods), f"periods are sorted (got {periods})")

    # list_periods for unknown company
    empty_periods = list_periods("999999")
    check(len(empty_periods) == 0, f"list_periods for unknown company returns [] (got {empty_periods})")

    # get_company_info
    info = get_company_info("300750")
    check(isinstance(info, dict), f"get_company_info returns dict (got {type(info).__name__})")
    check(info.get("stock_code") == "300750",
          f"get_company_info stock_code correct (got {info.get('stock_code')})")

    # get_company_info for unknown
    empty_info = get_company_info("999999")
    check(empty_info.get("company_name") == "", f"Unknown company returns empty name")

    # Cleanup
    import financial.db as fdb
    fdb._db_path = None
    try:
        os.unlink(tmp_path)
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
