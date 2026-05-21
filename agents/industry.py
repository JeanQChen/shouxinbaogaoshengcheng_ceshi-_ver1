"""Agent 4: 行业分析。

数据来源：
  1. akshare（行业分类、公司基本信息）
  2. Web search（行业新闻、研报、政策）
  3. ChromaDB industry_docs__<id>（用户上传的行业 PDF）
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec

logger = logging.getLogger(__name__)


def _format_company_info(info: dict) -> str:
    """格式化 akshare 公司信息为文本。"""
    if not info:
        return "（暂无公开信息）"
    key_names = {
        "company_name": "公司名称",
        "industry": "所属行业",
        "total_market_cap": "总市值",
        "business_scope": "经营范围",
        "introduction": "公司介绍",
        "register_address": "注册地址",
        "register_capital": "注册资本",
    }
    lines: list[str] = []
    for k, label in key_names.items():
        v = info.get(k) or info.get(label)
        if v and v != "None":
            lines.append(f"- {label}: {v}")
    return "\n".join(lines) if lines else "（暂无公开信息）"


def run(company_id: str, industry_code: str, section_spec: SectionSpec) -> ReportSection:
    """生成行业分析章节。

    1. akshare 获取行业分类和公司基本信息
    2. Web search 检索行业新闻、研报
    3. 必要时检索 ChromaDB industry_docs
    4. LLM 生成章节
    """
    # ── 1. akshare ──
    from external.akshare_client import get_company_info

    company_info_raw = get_company_info(company_id)
    company_info_text = _format_company_info(company_info_raw)
    industry_name = company_info_raw.get("industry", "") or company_info_raw.get("所属行业", "") or industry_code

    # ── 2. Web search ──
    from external.web_search import search_web

    search_queries = [
        f"{industry_name} 行业 发展现状 市场规模 2025",
        f"{industry_name} 行业政策 监管 2025",
        f"{industry_name} 竞争格局 市场集中度",
        f"A股 {industry_name} 上市公司 行业分析",
    ]

    web_parts: list[str] = []
    for q in search_queries:
        try:
            result = search_web(q, max_results=5)
            if result and "未检索到" not in result:
                web_parts.append(result)
        except Exception:
            logger.warning("Web search failed for '%.40s'", q, exc_info=True)

    web_search_results = "\n\n".join(web_parts) if web_parts else "（暂无互联网检索结果）"

    # ── 3. ChromaDB ──
    retrieved_chunks_text = "（未检索到相关行业文档）"
    try:
        from retrieval.retriever import retrieve

        chunks = retrieve(company_id, "industry_docs", f"{industry_name} 行业分析 竞争格局 政策", k=5)
        if chunks:
            lines: list[str] = []
            for i, c in enumerate(chunks, 1):
                lines.append(f"[{i}] (来源: {c.source_file}, 第{c.page_number}页, 匹配度={c.score:.3f})")
                lines.append(f"    {c.text.strip()[:800]}")
            retrieved_chunks_text = "\n".join(lines)
    except ValueError:
        pass  # collection 不存在，忽略
    except Exception:
        logger.warning("ChromaDB retrieval failed", exc_info=True)

    # ── 4. LLM 生成 ──
    prompt = load_prompt("industry")
    prompt = prompt.format(
        company_id=company_id,
        industry_name=industry_name,
        company_info=company_info_text,
        web_search_results=web_search_results,
        retrieved_chunks=retrieved_chunks_text,
        guidance=section_spec.guidance or "全面分析行业景气度、竞争格局、政策环境，评估对授信对象的影响。",
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的行业研究员。只输出 Markdown 格式的行业分析章节正文。",
        max_tokens=4096,
    )

    # ── 5. 构建 citations ──
    citations: list[Citation] = []
    for q in search_queries[:2]:
        citations.append(Citation(
            source_type="web_search",
            source_ref=f"query={q[:80]}",
            snippet=web_search_results[:200],
        ))

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=citations,
        generated_by="industry_analyzer",
    )


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = "300750"
    industry = "电气设备"
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--company" and i + 1 < len(sys.argv):
            company = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--industry" and i + 1 < len(sys.argv):
            industry = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    spec = SectionSpec(
        section_id="industry.行业分析",
        title="行业分析",
        agent_id="industry",
        guidance="覆盖行业概况与景气度、竞争格局、政策环境、行业影响评估。",
    )

    result = run(company, industry, spec)
    print(f"## {result.title}\n")
    print(result.content)

    if result.citations:
        print(f"\n--- {len(result.citations)} citations ---")
