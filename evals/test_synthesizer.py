"""Eval: Agent 5 — 综合授信意见。

Mock LLM，测试基于章节摘要的综合生成。

用法: python -m evals.test_synthesizer
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

    from reporting.template import ReportSection, SectionSpec

    # ── 构造测试章节 ──
    sections = [
        ReportSection(
            section_id="company_subject.公司主体分析",
            title="公司主体信用分析",
            content="### 公司概况\n\n宁德时代是全球领先的动力电池企业。\n\n### 公司治理\n\n实际控制人为曾毓群。",
            citations=[],
            generated_by="company_subject_analyzer",
        ),
        ReportSection(
            section_id="financial.财务分析",
            title="财务分析",
            content="### 偿债能力\n\n资产负债率持续下降至61.94%，流动比率1.60。\n\n### 盈利能力\n\n毛利率持续提升至26.27%。",
            citations=[],
            generated_by="financial_analyzer",
        ),
        ReportSection(
            section_id="industry.行业分析",
            title="行业分析",
            content="### 行业概况\n\n锂电池行业持续高景气。\n\n### 竞争格局\n\nCR3超过55%。",
            citations=[],
            generated_by="industry_analyzer",
        ),
    ]

    # ── Mock LLM ──
    from evals.conftest import mock_chat_factory
    mock_chat = mock_chat_factory({
        "综合授信": (
            "### 综合评估\n\n"
            "公司主体信用优质，财务表现稳健，行业前景良好。综合评估：支持授信。\n\n"
            "### 授信建议\n\n"
            "建议给予综合授信额度人民币500亿元，期限3年，信用方式为主。\n\n"
            "### 风险提示与缓释措施\n\n"
            "1. **存货增长风险**：存货增速超营收增速，需监控存货周转率变化。\n"
            "2. **投资扩张风险**：投资活动现金流持续大额净流出，关注投资回报。\n"
            "3. **行业竞争风险**：关注二线电池厂产能扩张对毛利率的挤压。"
        ),
    })

    # ── Patch ──
    import agents.synthesizer as agent
    _orig_chat = agent.chat
    agent.chat = mock_chat

    try:
        spec = SectionSpec(
            section_id="synthesizer.综合授信意见",
            title="综合授信意见",
            agent_id="synthesizer",
            guidance="基于以上三方面分析，给出综合授信建议。",
        )

        # ── Test 1: Normal run ──
        result = agent.run("300750", sections, spec)
        check(result.section_id == spec.section_id,
              "ReportSection.section_id preserved")
        check(result.title == spec.title,
              "ReportSection.title preserved")
        check(result.generated_by == "synthesizer",
              "generated_by set correctly")
        check(len(result.content) > 50,
              f"Content generated ({len(result.content)} chars)")
        check("综合评估" in result.content,
              "Content includes 综合评估 heading")
        check("授信建议" in result.content,
              "Content includes 授信建议 heading")
        check("风险提示" in result.content,
              "Content includes 风险提示 heading")
        check("500亿元" in result.content,
              "Content includes specific credit amount")

        # ── Test 2: Empty sections graceful ──
        empty_sections: list[ReportSection] = []
        result2 = agent.run("000001", empty_sections, spec)
        check(len(result2.content) > 50,
              "synthesizer handles empty sections list gracefully")

        # ── Test 3: _summarize ──
        short = "短文本"
        check(agent._summarize(short) == short,
              "_summarize: short text unchanged")
        long = "A" * 3000
        check(len(agent._summarize(long)) <= 2000 + 30,
              "_summarize: long text truncated near max_chars")

    finally:
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
