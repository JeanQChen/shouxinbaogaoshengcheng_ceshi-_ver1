"""Agent 5: 综合 — LLM 轻量衔接各章节，生成综合授信意见。

Contract:
  run(company_id, sections, section_spec) -> ReportSection

LLM 职责：基于前文章节摘要，生成综合授信意见。
不重写各章节内容，只做口径统一和结论提炼。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec

logger = logging.getLogger(__name__)


def _summarize(content: str, max_chars: int = 2000) -> str:
    """截取章节内容的前 max_chars 字符作为摘要。"""
    if not content:
        return "（无内容）"
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n...（后续内容省略）"


def run(company_id: str, sections: list[ReportSection], section_spec: SectionSpec) -> ReportSection:
    """生成综合授信意见章节。

    1. 从前文章节提取摘要
    2. LLM 基于摘要生成综合授信意见
    """
    # ── 1. 提取各章节摘要 ──
    company_subject_summary = "（未生成公司主体分析章节）"
    financial_summary = "（未生成财务分析章节）"
    industry_summary = "（未生成行业分析章节）"

    for s in sections:
        if "company_subject" in s.generated_by:
            company_subject_summary = _summarize(s.content)
        elif "financial" in s.generated_by:
            financial_summary = _summarize(s.content)
        elif "industry" in s.generated_by:
            industry_summary = _summarize(s.content)

    # ── 2. LLM ──
    prompt = load_prompt("synthesizer")
    prompt = prompt.format(
        company_subject_summary=company_subject_summary,
        financial_summary=financial_summary,
        industry_summary=industry_summary,
        guidance=section_spec.guidance or (
            "基于以上公司主体、财务、行业三方面分析，给出综合授信建议。"
            "必须包含：授信额度建议及依据、期限建议、担保要求、主要风险点及缓释措施。"
            "结论须与前文分析保持一致口径。"
        ),
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的授信审批官。只输出 Markdown 格式的综合授信意见章节正文。",
        max_tokens=4096,
    )

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=[],
        generated_by="synthesizer",
    )


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("Usage: python -m agents.synthesizer", file=sys.stderr)
    print("synthesizer 由 streamlit_app 内部调用，无独立 CLI。", file=sys.stderr)
    sys.exit(1)
