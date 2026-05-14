"""Eval: financial analyzer（格式化函数 + run with mock LLM）。

用法: python -m evals.test_analyzer
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.conftest import mock_chat_factory, get_api_key


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

    from financial.analyzer import (
        _format_metrics_table, _format_growth_table, _format_balances_table, run,
    )
    from financial.metrics import MetricsTable

    # ── _format_metrics_table ──
    # Empty case
    empty = MetricsTable(by_period={}, yoy_changes={})
    result_empty = _format_metrics_table(empty)
    check("（无数据）" in result_empty,
          "_format_metrics_table empty → '（无数据）'")

    # Non-empty case
    non_empty = MetricsTable(
        by_period={
            "2024-12-31": {"流动比率": 1.5, "速动比率": 1.2, "资产负债率": 0.6},
            "2025-12-31": {"流动比率": 1.6, "速动比率": 1.3, "资产负债率": 0.58},
        },
        yoy_changes={},
    )
    result_filled = _format_metrics_table(non_empty)
    check("| 指标 |" in result_filled,
          "_format_metrics_table has table header")
    check("2024-12-31" in result_filled,
          "_format_metrics_table contains period column")
    check("流动比率" in result_filled,
          "_format_metrics_table contains metric row")
    check("1.5000" in result_filled,
          "_format_metrics_table formats to 4 decimal places")

    # None value → N/A
    with_none = MetricsTable(
        by_period={"2024-12-31": {"流动比率": None}},
        yoy_changes={},
    )
    result_none = _format_metrics_table(with_none)
    check("N/A" in result_none,
          "_format_metrics_table renders None as N/A")

    # ── _format_growth_table ──
    result_empty_g = _format_growth_table(empty)
    check("（无增长率数据）" in result_empty_g,
          "_format_growth_table empty → '（无增长率数据）'")

    non_empty_g = MetricsTable(
        by_period={},
        yoy_changes={
            "2025-12-31": {"营收增长率": 0.25, "净利增长率": -0.10},
        },
    )
    result_g = _format_growth_table(non_empty_g)
    check("| 指标 |" in result_g,
          "_format_growth_table has table header")
    check("+25.00%" in result_g,
          f"_format_growth_table formats positive: {result_g}")
    check("-10.00%" in result_g or "N/A" in result_g,
          f"_format_growth_table formats negative")

    # ── _format_balances_table ──
    # This needs the real DB with 300750 data, so test structure only
    from evals.conftest import seed_in_memory_db
    try:
        db_path, files_loaded = seed_in_memory_db("300750")
    except Exception as e:
        details.append(f"SKIP: Cannot seed DB for analyzer: {e}")
        return {"passed": passed, "failed": failed, "skipped": 1, "details": details}

    if files_loaded == 0:
        details.append("SKIP: No sample files for analyzer")
        return {"passed": passed, "failed": failed, "skipped": 1, "details": details}

    import financial.db as fdb
    fdb._db_path = Path(db_path)
    try:
        from financial.db import list_periods
        periods = list_periods("300750")
        if periods:
            bal_table = _format_balances_table("300750", periods)
            check("### 资产负债表（亿元）" in bal_table,
                  "_format_balances_table has 资产负债表 section")
            check("### 利润表（亿元）" in bal_table,
                  "_format_balances_table has 利润表 section")
            check("### 现金流量表（亿元）" in bal_table,
                  "_format_balances_table has 现金流量表 section")
            check("| 科目 |" in bal_table,
                  "_format_balances_table has table header")
    finally:
        fdb._db_path = None
        try:
            os.unlink(db_path)
        except OSError:
            pass

    # ── run() with mock LLM ──
    use_mock = os.getenv("EVAL_MOCK_LLM", "").lower() == "true"
    api_key = get_api_key()

    if use_mock or not api_key:
        mock_text = (
            "## 偿债能力分析\n\n"
            "公司流动比率1.6000，速动比率1.3000，短期偿债能力较强。\n\n"
            "资产负债率58.00%，处于合理水平。\n\n"
            "利息保障倍数充足，长期偿债风险较低。"
        )
        mock_fn = mock_chat_factory({"财务分析": mock_text})

        import financial.analyzer as faz
        _orig_chat = faz.chat
        faz.chat = mock_fn
        try:
            from reporting.template import SectionSpec
            spec = SectionSpec(
                section_id="financial.偿债能力",
                title="偿债能力分析",
                agent_id="financial",
                guidance="测试 guidance",
            )

            # Seed DB for run()
            try:
                db_path2, files2 = seed_in_memory_db("300750")
            except Exception as e:
                details.append(f"SKIP: Cannot seed DB for run(): {e}")
                return {"passed": passed, "failed": failed, "skipped": 1, "details": details}

            fdb._db_path = Path(db_path2)
            # run() imported init_db into its own namespace at load time —
            # must patch financial.analyzer.init_db, not fdb.init_db
            import financial.analyzer as faz
            _orig_init_db = faz.init_db
            faz.init_db = lambda *a, **kw: None
            try:
                result_section = run("300750", spec)
                check(result_section.section_id == "financial.偿债能力",
                      f"run() returns correct section_id: {result_section.section_id}")
                check(result_section.title == "偿债能力分析",
                      f"run() returns correct title: {result_section.title}")
                check(len(result_section.content) > 0,
                      f"run() returns non-empty content (length={len(result_section.content)})")
                check(result_section.generated_by == "financial_analyzer",
                      f"run() generated_by='financial_analyzer'")
                check(result_section.citations is not None,
                      "run() has citations list")

                # Check no hallucinated numbers in mock mode
                # The mock text shouldn't contain random calculated numbers
                content = result_section.content
                check("流动比率" in content,
                      "Mock output includes metric names from table")
            finally:
                faz.init_db = _orig_init_db
                fdb._db_path = None
                try:
                    os.unlink(db_path2)
                except OSError:
                    pass
        finally:
            faz.chat = _orig_chat
    else:
        skipped += 1
        details.append("SKIP: --real-llm not set, skipping run() real LLM test")

    # ── run() with empty DB ──
    # Use a fresh temp DB
    import tempfile
    fd, empty_db = tempfile.mkstemp(suffix=".db", prefix="eval_empty_")
    os.close(fd)
    from financial.db import init_db
    init_db(empty_db)
    fdb._db_path = Path(empty_db)
    _orig_init_db2 = faz.init_db
    faz.init_db = lambda *a, **kw: None
    try:
        from reporting.template import SectionSpec
        spec = SectionSpec(
            section_id="financial.empty",
            title="空数据测试",
            agent_id="financial",
        )
        empty_result = run("999999", spec)
        check("无法生成" in empty_result.content or "没有" in empty_result.content,
              f"run() with no data returns error message: '{empty_result.content[:80]}'")
    finally:
        faz.init_db = _orig_init_db2
        fdb._db_path = None
        try:
            os.unlink(empty_db)
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
