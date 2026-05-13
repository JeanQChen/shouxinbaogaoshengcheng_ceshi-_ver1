"""Agent 3: 公司主体信用分析。

数据来源：
  1. ChromaDB RAG（公司公告、年报全文等内部文档）
  2. Claude web search tool（近期新闻、舆情等外部信息）
"""

from reporting.template import SectionSpec, ReportSection


def run(company_id: str, section_spec: SectionSpec) -> ReportSection:
    """生成公司主体信用分析章节。

    1. 从 retrieval.retriever 查 company_docs__<id>
    2. 通过 Claude web search tool 检索近期新闻
    3. 合并喂给 LLM 生成章节
    """
    raise NotImplementedError
