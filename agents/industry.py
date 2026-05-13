"""Agent 4: 行业分析。

主要依赖 Claude web search tool 检索互联网研报。
必要时检索 industry_docs__<id>（用户上传的行业 PDF）。
"""

from reporting.template import SectionSpec, ReportSection


def run(company_id: str, industry_code: str, section_spec: SectionSpec) -> ReportSection:
    """生成行业分析章节。

    1. 通过 Claude web search tool 检索行业研报、政策、竞争格局
    2. 必要时检索 ChromaDB industry_docs__<id>
    3. LLM 生成章节
    """
    raise NotImplementedError
