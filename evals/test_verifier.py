"""Eval: Agent 6 — 回检。

Mock SQLite + akshare，测试三类规则 + 标注。
用法: python -m evals.test_verifier
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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

    import agents.verifier as agent
    from agents.verifier import _NumericalClaim

    # ── 构造测试报告（包含刻意植入的错误）──
    TEST_REPORT = """# 授信分析报告 — 测试公司

## 一、公司主体信用分析

测试股份有限公司（股票代码：000001）是一家测试企业。
公司实际控制人为张三。

## 二、财务分析

### 偿债能力

资产负债率降至 70.00%（数据库实际 60%，差异 16.7% → 应触发 yellow）。
流动比率为 1.60，保持稳定。
速动比率为 1.20 倍。

### 资产负债表

| 项目 | 金额 |
|------|------|
| 资产总计 | 500.00亿元 |
| 负债合计 | 300.00亿元 |

2022 年公司营收为 100.00亿元（超 2 年 → 应触发 orange）。

## 三、行业分析

（略）

## 四、综合授信意见

建议给予授信额度 200 亿元。
"""

    # ── Mock SQLite query_metric ──
    _orig_query_metric = agent.query_metric

    def mock_query_metric(company_id, item_code, period):
        """返回受控值用于验证。"""
        from financial.schema import (
            TOTAL_ASSETS, CURRENT_ASSETS, TOTAL_LIABILITIES, CURRENT_LIABILITIES,
            TOTAL_EQUITY, INVENTORY, TOTAL_REVENUE,
        )
        mock_data = {
            # 资产总计: 报告 500亿=50B元, 实际 480亿=48B元 → 差异 4.2%，不触发
            TOTAL_ASSETS: 48_000_000_000,
            CURRENT_ASSETS: 30_000_000_000,
            # 负债合计: 报告 300亿=30B元, 实际 28B元 → 差异 7.1%，触发
            TOTAL_LIABILITIES: 28_000_000_000,
            CURRENT_LIABILITIES: 18_000_000_000,
            TOTAL_EQUITY: 20_000_000_000,
            INVENTORY: 5_000_000_000,
            TOTAL_REVENUE: 10_000_000_000,
        }
        return mock_data.get(item_code)

    # ── Mock akshare ──
    import external.akshare_client as ak
    _orig_get_company_info = ak.get_company_info

    def mock_get_company_info(stock_code):
        if stock_code == "000001":
            return {"company_name": "平安银行股份有限公司", "industry": "银行业"}
        elif stock_code == "300750":
            return {"company_name": "宁德时代新能源科技股份有限公司", "industry": "电气设备"}
        return {}

    # ── Patch ──
    agent.query_metric = mock_query_metric
    agent.list_periods = lambda cid: ["2023-12-31", "2024-12-31", "2025-12-31"]
    agent.init_db = lambda *a, **kw: None
    ak.get_company_info = mock_get_company_info

    try:
        # ── Test 1: 提取数值声明 ──
        claims = agent._extract_numerical_claims(TEST_REPORT)
        check(len(claims) >= 3, f"_extract_numerical_claims finds claims (got {len(claims)})")
        # 应该找到 70.00%（资产负债率）, 1.60（流动比率）, 1.20（速动比率）, 500.00亿元, 300.00亿元, 100.00亿元

        # ── Test 2: Yellow — 超出阈值 (>5%) ──
        yellow_issues = agent._check_yellow_rules(claims, "000001")
        check(len(yellow_issues) >= 1, f"Yellow: flags deviation >5% (got {len(yellow_issues)})")
        if yellow_issues:
            check(yellow_issues[0].severity == "yellow", "Yellow: severity is yellow")
            check("yellow" == yellow_issues[0].severity, "Yellow: severity verified")

        # ── Test 3: Yellow — 差异细节 ──
        if yellow_issues:
            iss = yellow_issues[0]
            check(len(iss.detail) > 0, f"Yellow: detail non-empty: {iss.detail[:60]}")
            check(len(iss.evidence) > 0, "Yellow: evidence non-empty")

        # ── Test 4: Red — 公司名称不匹配 ──
        red_issues = agent._check_red_rules(TEST_REPORT, "000001")
        check(len(red_issues) >= 1, f"Red: flags entity mismatch (got {len(red_issues)})")

        # ── Test 5: Red — 公司名称匹配时无问题 ──
        REPORT_MATCH = "# 报告\n平安银行股份有限公司（股票代码：000001）主营业务为银行业务。\n平安银行在2025年表现良好。"
        red_match = agent._check_red_rules(REPORT_MATCH, "000001")
        # 应该只检查股票代码（有）和公司全称（有"平安银行股份有限公司"）
        code_issues = [i for i in red_match if "股票代码" in i.rule]
        check(len(code_issues) == 0, f"Red: stock code present → no issue (got {len(code_issues)})")

        # ── Test 6: Orange — 过时年份 ──
        orange_issues = agent._check_orange_rules(TEST_REPORT, "000001")
        check(len(orange_issues) >= 1, f"Orange: flags stale year (got {len(orange_issues)})")
        orange_years = [i for i in orange_issues if "2022" in i.location]
        check(len(orange_years) >= 1, "Orange: flags 2022 as stale")

        # ── Test 7: Orange — 当前年份不触发 ──
        REPORT_CURRENT = "# 报告\n2025 年公司业绩持续增长。2026 年展望良好。"
        orange_current = agent._check_orange_rules(REPORT_CURRENT, "000001")
        check(len(orange_current) == 0, f"Orange: current years not flagged (got {len(orange_current)})")

        # ── Test 8: 标注插入 ──
        annotated = agent._annotate_markdown(TEST_REPORT, yellow_issues + red_issues + orange_issues)
        check("回检摘要" in annotated, "Annotation: includes summary header")
        check("🟡" in annotated or "yellow" in annotated.lower(), "Annotation: yellow markers present")
        check("回检摘要" in annotated, "Annotation: summary present (dup verify)")

        # ── Test 9: 空报告 ──
        empty_issues_y = agent._check_yellow_rules(
            agent._extract_numerical_claims(""), "000001"
        )
        check(len(empty_issues_y) == 0, "Empty report → no yellow issues")
        empty_annotated = agent._annotate_markdown("", [])
        check(empty_annotated == "", "Empty report → unchanged by annotation")

        # ── Test 10: VerificationResult 完整性 ──
        result = agent.run(TEST_REPORT, "000001")
        check(isinstance(result, agent.VerificationResult), "run returns VerificationResult")
        check(len(result.annotated_markdown) > len(TEST_REPORT),
              "Annotated markdown is longer than original")
        check(isinstance(result.issues, list), "issues is a list")

        # ── Test 11: Issue 数据结构 ──
        if result.issues:
            iss = result.issues[0]
            check(iss.severity in ("yellow", "red", "orange"), f"Issue severity valid: {iss.severity}")
            check(len(iss.rule) > 0, "Issue rule non-empty")
            check(len(iss.detail) > 0, "Issue detail non-empty")
            check(isinstance(iss.evidence, list), "Issue evidence is list")

        # ── Test 12: 股票代码缺失检测 ──
        REPORT_NO_CODE = "# 报告\n测试公司是一家好公司。"
        red_no_code = agent._check_red_rules(REPORT_NO_CODE, "000001")
        code_issues = [i for i in red_no_code if "股票代码" in i.rule]
        check(len(code_issues) >= 1, "Red: missing stock code flagged")

    finally:
        agent.query_metric = _orig_query_metric
        ak.get_company_info = _orig_get_company_info

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, "
          f"{result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
