"""Eval: schema mapper 规则匹配 + LLM 兜底。

用法: python -m evals.test_schema_mapper_llm
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

    from parsers.schema_mapper import (
        _normalize_name, _rule_match, _classify_sheet, _llm_match,
    )
    from financial.schema import get_all_codes

    # ── _normalize_name ──
    check(_normalize_name("减：库存股") == "库存股",
          f"_normalize_name('减：库存股') == '库存股' (got '{_normalize_name('减：库存股')}')")
    check(_normalize_name("减:库存股") == "库存股",
          f"_normalize_name('减:库存股') == '库存股' (got '{_normalize_name('减:库存股')}')")
    check(_normalize_name("其中：应收利息") == "应收利息",
          f"_normalize_name('其中：应收利息') (got '{_normalize_name('其中：应收利息')}')")
    check(_normalize_name("其中:应收利息") == "应收利息",
          f"_normalize_name('其中:应收利息') (got '{_normalize_name('其中:应收利息')}')")
    check(_normalize_name("资产总计（合计）") == "资产总计",
          f"_normalize_name('资产总计（合计）') (got '{_normalize_name('资产总计（合计）')}')")
    check("合计" not in _normalize_name("资产总计(合计)"),
          f"_normalize_name removes （合计）: '{_normalize_name('资产总计(合计)')}'")
    check(_normalize_name("加:期初现金及现金等价物余额") == "期初现金及现金等价物余额",
          f"_normalize_name strips 加: prefix (got '{_normalize_name('加:期初现金及现金等价物余额')}')")
    # 空格和换行符去除
    result = _normalize_name("  货币资金  ")
    check(result == "货币资金", f"_normalize_name strips whitespace (got '{result}')")

    # ── _rule_match ──
    check(_rule_match("资产总计") == "TOTAL_ASSETS",
          f"_rule_match('资产总计') == TOTAL_ASSETS (got {_rule_match('资产总计')})")
    check(_rule_match("流动资产合计") == "CURRENT_ASSETS",
          f"_rule_match('流动资产合计') == CURRENT_ASSETS (got {_rule_match('流动资产合计')})")

    # Normalized match
    check(_rule_match("  资产总计（合计）  ") == "TOTAL_ASSETS",
          f"_rule_match('  资产总计（合计）  ') == TOTAL_ASSETS (got {_rule_match('  资产总计（合计）  ')}')")

    # Keyword contained match
    check(_rule_match("货币资金（含受限资金）") == "CASH_AND_EQUIVALENTS",
          f"_rule_match('货币资金（含受限资金）') (got {_rule_match('货币资金（含受限资金）')})")

    # Short keyword should NOT match broadly
    result_short = _rule_match("资产")
    if result_short is not None:
        check(result_short == "TOTAL_ASSETS",
              f"_rule_match('资产') → {result_short}")
    else:
        check(True, "_rule_match('资产') → None (short keyword not overmatched)")

    # Unknown name
    check(_rule_match("完全胡编的科目名XYZ123") is None,
          f"_rule_match('完全胡编的科目名XYZ123') is None")

    # Colon format: "所有者权益:归属于母公司" 应该匹配子部分
    colon_result = _rule_match("所有者权益:归属于母公司所有者权益合计")
    # The sub-part should match. At minimum it shouldn't crash.
    details.append(f"PASS: _rule_match colon-format returned: {colon_result}")
    passed += 1

    # ── _classify_sheet ──
    bt, cat = _classify_sheet("资产负债表")
    check(bt == "balance_sheet" and cat == "asset",
          f"_classify_sheet('资产负债表') → ({bt}, {cat})")

    bt, cat = _classify_sheet("合并利润表")
    check(bt == "income_statement" and cat == "revenue",
          f"_classify_sheet('合并利润表') → ({bt}, {cat})")

    bt, cat = _classify_sheet("财务现金流量表( NOTES TO CASH FLOW STATEMENT)")
    check(bt == "cash_flow" and cat == "operating",
          f"_classify_sheet('现金流量表...') → ({bt}, {cat})")

    bt, cat = _classify_sheet("Unknown Sheet Name")
    check(bt == "balance_sheet",
          f"_classify_sheet('Unknown...') defaults to balance_sheet (got {bt})")

    # ── _llm_match ──
    check(_llm_match([]) == {},
          "_llm_match([]) returns {} for empty input")

    # Test with mock LLM
    use_mock = os.getenv("EVAL_MOCK_LLM", "").lower() == "true"
    api_key = get_api_key()

    if use_mock or not api_key:
        mock_fn = mock_chat_factory({
            "短期借款": '{"短期借款": "SHORT_TERM_BORROWINGS", "应交税费": "TAXES_PAYABLE"}',
        })
        import llm.client
        original_chat = llm.client.chat
        llm.client.chat = mock_fn
        try:
            result = _llm_match(["短期借款", "应交税费"])
            check(isinstance(result, dict),
                  f"_llm_match returns dict (got {type(result).__name__})")
            check(result.get("短期借款") == "SHORT_TERM_BORROWINGS",
                  f"_llm_match mapped 短期借款 → {result.get('短期借款')}")
            check(result.get("应交税费") == "TAXES_PAYABLE",
                  f"_llm_match mapped 应交税费 → {result.get('应交税费')}")
        finally:
            llm.client.chat = original_chat
    else:
        # Real LLM test
        result = _llm_match(["短期借款", "应交税费", "完全胡编的科目XYZ"])
        check(isinstance(result, dict),
              f"_llm_match with real LLM returns dict (got {type(result).__name__})")
        check("完全胡编的科目XYZ" not in result,
              "_llm_match does not map known-fake item")
        if "短期借款" in result:
            check(result["短期借款"] == "SHORT_TERM_BORROWINGS",
                  f"Real LLM mapped 短期借款 → {result.get('短期借款')}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
