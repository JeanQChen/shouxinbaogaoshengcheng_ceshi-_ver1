"""Agent 3: 公司主体信用分析。

数据来源：
  1. ChromaDB RAG（公司公告、年报全文等内部文档）
  2. Web search（近期新闻、舆情等外部信息，当前为占位）
"""

import logging

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec
from retrieval.retriever import retrieve, RetrievedChunk

logger = logging.getLogger(__name__)

_RETRIEVAL_QUERIES = [
    "公司概况 主营业务 业务模式 竞争优势 行业地位",
    "公司治理 股权结构 实际控制人 董事会 管理层",
    "对外担保 重大诉讼 行政处罚 关联交易 风险事项 对外投资",
]


def _dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    seen: set[tuple[int, int, str]] = set()
    unique: list[RetrievedChunk] = []
    for c in sorted(chunks, key=lambda x: x.score, reverse=True):
        key = (c.page_number, c.chunk_index, c.source_file)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _format_chunks(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "（未检索到相关内部文档）"

    lines: list[str] = []
    for i, c in enumerate(chunks, 1):
        lines.append(f"[{i}] (来源: {c.source_file}, 第{c.page_number}页, "
                     f"匹配度={c.score:.3f})")
        lines.append(f"    {c.text.strip()[:800]}")
        lines.append("")
    return "\n".join(lines)


def _web_search(company_id: str) -> str:
    """搜索公司近期新闻和舆情，返回格式化文本。"""
    try:
        from external.web_search import search_web
        from external.akshare_client import get_stock_news, get_company_info

        parts: list[str] = []

        # akshare 个股新闻
        try:
            news = get_stock_news(company_id, limit=10)
            if news:
                lines = ["### 近期新闻（东方财富）"]
                for n in news:
                    lines.append(f"- [{n['time']}] {n['title']}（{n['source']}）")
                parts.append("\n".join(lines))
        except Exception:
            pass

        # 公司名搜索
        try:
            info = get_company_info(company_id)
            company_name = info.get("company_name") or info.get("公司名称") or ""
            if company_name:
                result = search_web(f"{company_name} 重大事项 诉讼 担保 处罚 2025", max_results=5)
                if result and "未检索到" not in result:
                    parts.append(f"### 互联网检索\n{result}")
        except Exception:
            pass

        if parts:
            return "\n\n".join(parts)
    except Exception:
        pass

    return f"（暂无公司 {company_id} 的外部新闻数据，以下分析基于公司内部文档。）"


def run(company_id: str, section_spec: SectionSpec) -> ReportSection:
    """生成公司主体信用分析章节。

    1. 从 ChromaDB 多角度检索内部文档
    2. 合并去重后格式化为 prompt 输入
    3. LLM 撰写定性分析，不计算数字
    """
    # ── 1. 检索 ──
    all_chunks: list[RetrievedChunk] = []
    for query in _RETRIEVAL_QUERIES:
        try:
            chunks = retrieve(company_id, "company_docs", query, k=5)
            all_chunks.extend(chunks)
        except ValueError as e:
            logger.warning("Retrieval failed for query '%.40s': %s", query, e)
            continue

    unique_chunks = _dedup_chunks(all_chunks)
    logger.info("Retrieved %d chunks (%d unique) for company_subject",
                len(all_chunks), len(unique_chunks))

    if not unique_chunks:
        return ReportSection(
            section_id=section_spec.section_id,
            title=section_spec.title,
            content="*无法生成：未在内部文档库中检索到该公司相关文档。请先上传 PDF 公告或年报。*",
            citations=[],
            generated_by="company_subject_analyzer",
        )

    # ── 2. 格式化输入 ──
    formatted_chunks = _format_chunks(unique_chunks)
    web_results = _web_search(company_id)

    # ── 3. LLM 生成 ──
    prompt = load_prompt("company_subject")
    prompt = prompt.format(
        retrieved_chunks=formatted_chunks,
        web_search_results=web_results,
        guidance=section_spec.guidance or "全面分析公司主体信用状况，覆盖业务模式、竞争地位、公司治理、重大风险事项。",
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的授信审批官。只输出 Markdown 格式的公司主体分析章节正文。",
        max_tokens=4096,
    )

    # ── 4. 构建 citations ──
    citations: list[Citation] = []
    for c in unique_chunks[:10]:
        citations.append(Citation(
            source_type="chromadb",
            source_ref=f"page={c.page_number},chunk={c.chunk_index},"
                       f"source={c.source_file}",
            snippet=c.text[:200],
        ))

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=citations,
        generated_by="company_subject_analyzer",
    )


# ── CLI ──

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = "300750"
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--company" and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    spec = SectionSpec(
        section_id="company_subject.公司主体分析",
        title="公司主体信用分析",
        agent_id="company_subject",
        guidance="必须覆盖公司概况、业务模式、竞争优势、公司治理、重大风险事项。外部新闻暂缺，基于内部文档定性分析。",
    )

    result = run(company, spec)
    print(f"## {result.title}\n")
    print(result.content)

    if result.citations:
        print(f"\n--- {len(result.citations)} citations ---")
        for c in result.citations[:5]:
            print(f"  [{c.source_type}] {c.source_ref}")
