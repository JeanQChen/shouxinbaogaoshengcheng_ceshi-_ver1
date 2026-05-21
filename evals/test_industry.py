"""Eval: Agent 4 — 行业分析。

Mock web_search + akshare + LLM。

用法: python -m evals.test_industry
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os = __import__("os")
os.environ["EVAL_MOCK_LLM"] = "true"


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

    # ── Mock web search ──
    def mock_search_web(query, max_results=5):
        return (
            "搜索结果（3 条）：\n"
            "- [1] **锂电池行业2025年展望**\n"
            "  全球动力电池装机量同比增长35%，中国市场占比62%。\n"
            "- [2] **新能源产业政策梳理**\n"
            "  工信部发布《新能源汽车产业发展规划》。\n"
            "- [3] **动力电池竞争格局**\n"
            "  宁德时代全球市占率37.6%，比亚迪16.1%。\n"
        )

    # ── Mock akshare ──
    def mock_get_company_info(stock_code):
        return {"industry": "电气设备", "company_name": "宁德时代"}

    # ── Mock LLM ──
    from evals.conftest import mock_chat_factory
    mock_chat = mock_chat_factory({
        "行业分析": (
            "### 行业概况与景气度\n\n"
            "锂电池行业持续高景气，全球动力电池装机量保持快速增长。\n\n"
            "### 竞争格局\n\n"
            "行业集中度高，宁德时代全球市占率37.6%，保持领先。\n\n"
            "### 政策环境\n\n"
            "新能源产业政策持续利好。\n\n"
            "### 行业影响评估\n\n"
            "行业景气对公司经营构成正面支撑。"
        ),
    })

    # ── Patch ──
    import agents.industry as agent
    import external.web_search as ws
    import external.akshare_client as ak

    _orig_search_web = ws.search_web
    _orig_get_company_info = ak.get_company_info
    _orig_chat = agent.chat

    ws.search_web = mock_search_web
    ak.get_company_info = mock_get_company_info
    agent.chat = mock_chat

    from reporting.template import SectionSpec

    try:
        spec = SectionSpec(
            section_id="industry.行业分析",
            title="行业分析",
            agent_id="industry",
            guidance="覆盖行业概况与景气度、竞争格局、政策环境。",
        )

        # ── Test 1: Normal run ──
        result = agent.run("300750", "电气设备", spec)
        check(result.section_id == spec.section_id,
              "ReportSection.section_id preserved")
        check(result.title == spec.title,
              "ReportSection.title preserved")
        check(len(result.content) > 50,
              f"Content generated ({len(result.content)} chars)")
        check("行业概况" in result.content or "景气度" in result.content,
              "Content covers industry overview")
        check(result.generated_by == "industry_analyzer",
              "generated_by set correctly")
        check(len(result.citations) > 0,
              f"Citations built ({len(result.citations)} citations)")

        # ── Test 2: Format company info ──
        info = {"industry": "电气设备", "company_name": "宁德时代"}
        formatted = agent._format_company_info(info)
        check("电气设备" in formatted, "Company info includes industry")
        check("宁德时代" in formatted, "Company info includes company name")

        # ── Test 3: Empty company info ──
        empty = agent._format_company_info({})
        check("暂无公开信息" in empty, "Empty company info → placeholder")

        # ── Test 4: Without industry ──
        result2 = agent.run("000001", "银行业", spec)
        check(len(result2.content) > 0,
              "Agent runs with different industry code")

    finally:
        ws.search_web = _orig_search_web
        ak.get_company_info = _orig_get_company_info
        agent.chat = _orig_chat

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, "
          f"{result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
