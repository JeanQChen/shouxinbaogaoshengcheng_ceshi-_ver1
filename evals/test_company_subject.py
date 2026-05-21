"""Eval: Agent 3 — 公司主体信用分析。

Mock retrieval + LLM，不加载 BGE-M3。

用法: python -m evals.test_company_subject
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

    from retrieval.retriever import RetrievedChunk

    # ── Mock retrieval ──
    mock_chunks = [
        RetrievedChunk(
            text="宁德时代新能源科技股份有限公司是全球领先的新能源创新科技公司，"
                 "主要从事动力电池及储能电池的研发、生产及销售。公司总部位于福建省宁德市。",
            page_number=5, chunk_index=0, source_file="NDSD_2025_year.pdf", score=0.85,
        ),
        RetrievedChunk(
            text="公司实际控制人为曾毓群先生，通过宁德时代新能源科技股份有限公司"
                 "持有公司股份。公司董事会由9名董事组成。",
            page_number=42, chunk_index=3, source_file="NDSD_2025_year.pdf", score=0.72,
        ),
        RetrievedChunk(
            text="报告期内公司无重大诉讼、仲裁事项。公司为子公司提供担保余额为"
                 "人民币 123亿元。",
            page_number=88, chunk_index=1, source_file="NDSD_2025_year.pdf", score=0.68,
        ),
    ]

    def mock_retrieve(company_id, collection, query, k=5, db_path="data/chroma"):
        # Return different slices for different queries
        if "公司治理" in query:
            return mock_chunks[1:2]
        elif "担保" in query or "诉讼" in query or "风险" in query:
            return mock_chunks[2:3]
        else:
            return mock_chunks[0:1]

    # ── Mock LLM ──
    from evals.conftest import mock_chat_factory

    mock_chat = mock_chat_factory({
        "公司主体信用分析": (
            "### 公司概况与业务模式\n\n"
            "宁德时代是全球领先的新能源创新科技公司（来源：[1]），主要从事动力电池"
            "及储能电池业务。\n\n"
            "### 竞争地位\n\n"
            "公司在动力电池领域市场占有率全球领先。\n\n"
            "### 公司治理\n\n"
            "实际控制人为曾毓群先生（来源：[2]）。\n\n"
            "### 重大事项\n\n"
            "报告期内无重大诉讼（来源：[3]）。对外担保余额123亿元。\n\n"
            "### 总体信用评估\n\n"
            "公司主体信用状况良好。"
        ),
    })

    # ── Patch agent imports ──
    import agents.company_subject as agent

    _orig_retrieve = agent.retrieve
    _orig_chat = agent.chat

    agent.retrieve = mock_retrieve
    agent.chat = mock_chat

    from reporting.template import SectionSpec

    try:
        spec = SectionSpec(
            section_id="company_subject.公司主体分析",
            title="公司主体信用分析",
            agent_id="company_subject",
            guidance="覆盖公司概况与业务模式、竞争地位、公司治理、重大事项。",
        )

        # ── Test 1: Normal run ──
        result = agent.run("300750", spec)
        check(result.section_id == spec.section_id,
              "ReportSection.section_id preserved")
        check(result.title == spec.title,
              "ReportSection.title preserved")
        check(len(result.content) > 100,
              f"Content generated ({len(result.content)} chars)")
        check("公司主体信用分析" in result.content or "宁德时代" in result.content,
              "Content references company")
        check(len(result.citations) > 0,
              f"Citations built ({len(result.citations)} citations)")

        # ── Test 2: Dedup logic ──
        dup_chunks = [
            RetrievedChunk(
                text="测试文本A", page_number=1, chunk_index=0,
                source_file="test.pdf", score=0.9,
            ),
            RetrievedChunk(
                text="测试文本A", page_number=1, chunk_index=0,
                source_file="test.pdf", score=0.9,
            ),
            RetrievedChunk(
                text="测试文本B", page_number=2, chunk_index=0,
                source_file="test.pdf", score=0.7,
            ),
        ]
        unique = agent._dedup_chunks(dup_chunks)
        check(len(unique) == 2, f"_dedup_chunks: 3→2 (got {len(unique)})")
        check(unique[0].score == 0.9, "Dedup keeps highest score first")

        # ── Test 3: Format chunks ──
        formatted = agent._format_chunks(mock_chunks)
        check("NDSD_2025_year.pdf" in formatted,
              "Format includes source file name")
        check("第5页" in formatted, "Format includes page number")

        # ── Test 4: Empty chunks → graceful ──
        empty_result = agent._format_chunks([])
        check("未检索到" in empty_result, "Empty chunks → placeholder text")

        # ── Test 5: No retrieval → graceful ──
        agent.retrieve = lambda *a, **kw: []  # return empty
        no_chunks_result = agent.run("300750", spec)
        check("无法生成" in no_chunks_result.content,
              "No chunks → graceful degradation")

        # ── Test 6: Web search function exists ──
        check(callable(agent._web_search),
              "_web_search is callable")

    finally:
        agent.retrieve = _orig_retrieve
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
