"""Eval: schema 代码映射不变量。

用法: python -m evals.test_schema
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases.config import EXPECTED_MIN_CODE_COUNT
from financial.schema import (
    get_all_codes, get_cn_name, get_code_by_cn,
    table_for_row, build_all_ddl,
    ALL_STATEMENT_TABLES,
    TABLE_BALANCE_SHEET, TABLE_INCOME_STATEMENT, TABLE_CASH_FLOW,
)


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

    # 1. 代码数量
    codes = get_all_codes()
    check(len(codes) >= EXPECTED_MIN_CODE_COUNT,
          f"get_all_codes() returns {len(codes)} entries (min {EXPECTED_MIN_CODE_COUNT})")

    # 2. 无重复值（中文名）
    cn_names = list(codes.values())
    dupes = set()
    for name in cn_names:
        if cn_names.count(name) > 1:
            dupes.add(name)
    check(len(dupes) == 0, f"No duplicate Chinese names (dupes: {dupes})")

    # 3. 往返查找一致性
    roundtrip_ok = 0
    roundtrip_fail = 0
    for code, cn in codes.items():
        result = get_code_by_cn(cn)
        if result == code:
            roundtrip_ok += 1
        else:
            roundtrip_fail += 1
            details.append(f"FAIL: get_code_by_cn('{cn}') = {result}, expected {code}")
    failed += roundtrip_fail
    passed += roundtrip_ok
    details.append(f"PASS: {roundtrip_ok}/{len(codes)} codes round-trip via get_cn_name/get_code_by_cn")

    # 4. 已知查找
    check(get_cn_name("TOTAL_ASSETS") == "资产总计",
          "get_cn_name(TOTAL_ASSETS) == '资产总计'")

    # 5. 缺失 code fallback
    check(get_cn_name("NONEXISTENT_CODE") == "NONEXISTENT_CODE",
          "get_cn_name(NONEXISTENT_CODE) returns fallback")

    # 6. 缺失中文名返回 None
    check(get_code_by_cn("不存在的科目名称XYZ") is None,
          "get_code_by_cn('不存在的科目名称XYZ') is None")

    # 7. table_for_row 路由
    check(table_for_row("asset", None) == TABLE_BALANCE_SHEET,
          "table_for_row('asset', None) → balance_sheet")
    check(table_for_row("liability", None) == TABLE_BALANCE_SHEET,
          "table_for_row('liability', None) → balance_sheet")
    check(table_for_row("equity", None) == TABLE_BALANCE_SHEET,
          "table_for_row('equity', None) → balance_sheet")
    check(table_for_row("revenue", None) == TABLE_INCOME_STATEMENT,
          "table_for_row('revenue', None) → income_statement")
    check(table_for_row("cost", None) == TABLE_INCOME_STATEMENT,
          "table_for_row('cost', None) → income_statement")
    check(table_for_row("profit", None) == TABLE_INCOME_STATEMENT,
          "table_for_row('profit', None) → income_statement")
    check(table_for_row(None, "operating") == TABLE_CASH_FLOW,
          "table_for_row(None, 'operating') → cash_flow")
    check(table_for_row(None, "investing") == TABLE_CASH_FLOW,
          "table_for_row(None, 'investing') → cash_flow")
    check(table_for_row(None, "financing") == TABLE_CASH_FLOW,
          "table_for_row(None, 'financing') → cash_flow")

    # 8. DDL 包含全部 4 张表
    ddl = build_all_ddl()
    for table in ["report_meta", TABLE_BALANCE_SHEET, TABLE_INCOME_STATEMENT, TABLE_CASH_FLOW]:
        check(f"CREATE TABLE {table}" in ddl or f"CREATE TABLE IF NOT EXISTS {table}" in ddl,
              f"DDL contains CREATE TABLE for '{table}'")

    # 9. ALL_STATEMENT_TABLES
    check(len(ALL_STATEMENT_TABLES) == 3,
          f"ALL_STATEMENT_TABLES has 3 entries: {ALL_STATEMENT_TABLES}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
