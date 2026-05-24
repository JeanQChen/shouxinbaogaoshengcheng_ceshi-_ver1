"""Agent 4: 行业素材提取。

数据来源（多层回退）：
  1. akshare（行业分类、公司基本信息）
  2. ChromaDB RAG（上传报告中关于所处行业的描述）
  3. Web search（行业新闻、研报、政策）
  4. 兜底："制造业"（标注为未成功获取，可能不准确）
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec

logger = logging.getLogger(__name__)


def _resolve_industry(company_id: str, company_name: str = "") -> tuple[str, str]:
    """多层回退确定行业分类。

    Returns:
        (industry_name, source_description)
    """
    from external.akshare_client import get_company_info

    # Layer 1: akshare
    try:
        info = get_company_info(company_id)
        industry = info.get("industry", "") or info.get("所属行业", "")
        if industry and industry != "None" and len(industry) > 1:
            return (industry, "akshare 公开信息")
        company_name = company_name or info.get("company_name", "") or info.get("公司名称", "")
    except Exception:
        logger.warning("akshare industry lookup failed for %s", company_id)

    # Layer 2: 从 ChromaDB 检索报告中描述的行业
    try:
        from retrieval.retriever import retrieve
        chunks = retrieve(company_id, "company_docs", "所属行业 行业分类 公司所处行业", k=5)
        if chunks:
            # 从检索结果中提取行业关键词
            import re
            for c in chunks:
                # 常见行业描述模式
                patterns = [
                    r'公司所处[的]?行业[为是]\s*[：:]*\s*([^\n，。；]{4,30})',
                    r'所属行业[为是]?\s*[：:]*\s*([^\n，。；]{4,30})',
                    r'行业[：:]\s*([^\n]{4,30})',
                    r'属于\s*([^\n，。；]{4,20})\s*行业',
                ]
                for pat in patterns:
                    m = re.search(pat, c.text)
                    if m:
                        industry = m.group(1).strip()
                        if len(industry) >= 2:
                            return (industry, f"上传报告（{c.source_file} 第{c.page_number}页）")
    except Exception:
        logger.warning("ChromaDB industry lookup failed", exc_info=True)

    # Layer 3: Web search
    if company_name:
        try:
            from external.web_search import search_web
            result = search_web(f"{company_name} 所属行业 申万行业分类", max_results=3)
            if result and "未检索到" not in result:
                # 尝试从搜索结果中提取行业名
                import re
                industry_match = re.search(r'行业[：:]\s*(\S{2,20})', result)
                if industry_match:
                    return (industry_match.group(1).strip(), "互联网检索推断")
        except Exception:
            logger.warning("Web search industry lookup failed", exc_info=True)

    # Layer 4: 兜底
    return ("制造业", "行业信息未成功获取，使用默认分类，可能不准确")


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


def _industry_docs_search(company_id: str, industry_name: str) -> str:
    """检索 industry_docs collection（如果存在）。"""
    try:
        from retrieval.retriever import retrieve
        chunks = retrieve(company_id, "industry_docs",
                         f"{industry_name} 行业分析 竞争格局 政策 市场规模", k=5)
        if chunks:
            lines: list[str] = []
            for i, c in enumerate(chunks, 1):
                lines.append(f"[行业文档 {i}] (来源: {c.source_file}, 第{c.page_number}页)")
                lines.append(f"    {c.text.strip()[:800]}")
            return "\n".join(lines)
    except ValueError:
        pass
    except Exception:
        logger.warning("Industry docs retrieval failed", exc_info=True)
    return "（未上传行业相关文档。）"


def run(company_id: str, industry_code: str, section_spec: SectionSpec) -> ReportSection:
    """生成行业分析素材（供 synthesizer 使用）。

    1. 多层回退确定行业分类
    2. akshare 获取公司基本信息
    3. Web search 检索行业新闻、研报
    4. 必要时检索 ChromaDB industry_docs + company_docs
    5. LLM 整理为结构化素材
    """
    # ── 1. 行业识别 ──
    from external.akshare_client import get_company_info
    company_info_raw = get_company_info(company_id)
    company_name = (
        company_info_raw.get("company_name", "") or
        company_info_raw.get("公司名称", "") or
        ""
    )
    industry_name, industry_source = _resolve_industry(company_id, company_name)
    logger.info("Industry resolved: '%s' (source: %s)", industry_name, industry_source)

    company_info_text = _format_company_info(company_info_raw)

    # ── 2. Web search ──
    from external.web_search import search_web

    search_queries = [
        f"{company_name or company_id} {industry_name} 行业 发展现状 市场规模",
        f"{industry_name} 行业政策 监管 趋势",
        f"{industry_name} 竞争格局 市场集中度 主要企业",
        f"{industry_name} 产业链 上下游 供给 需求",
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
    # Company docs (uploaded reports may contain industry description)
    company_docs_text = "（未检索到报告中的行业描述。）"
    try:
        from retrieval.retriever import retrieve
        chunks = retrieve(company_id, "company_docs",
                         f"{industry_name} 行业现状 竞争 市场 发展", k=5)
        if chunks:
            lines = ["### 上传报告中关于行业的描述"]
            for i, c in enumerate(chunks, 1):
                lines.append(f"[{i}] (来源: {c.source_file}, 第{c.page_number}页, "
                           f"节段: {c.section_title or '-'})")
                lines.append(f"    {c.text.strip()[:800]}")
            company_docs_text = "\n".join(lines)
    except ValueError:
        pass
    except Exception:
        logger.warning("Company docs industry retrieval failed", exc_info=True)

    # Industry docs
    industry_docs_text = _industry_docs_search(company_id, industry_name)

    # ── 4. LLM 整理素材 ──
    prompt = load_prompt("industry")
    prompt = prompt.format(
        company_id=company_id,
        company_name=company_name or company_id,
        industry_name=industry_name,
        industry_source=industry_source,
        company_info=company_info_text,
        web_search_results=web_search_results,
        retrieved_chunks=(company_docs_text + "\n\n" + industry_docs_text),
        guidance=section_spec.guidance or (
            "整理行业分析素材，覆盖行业发展现状与规模、竞争格局与各家优势、"
            "行业上下游情况、公司行业地位与竞争优势。最后总结优势劣势。"
        ),
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的行业研究员。从检索材料中整理行业分析素材。不写最终报告散文，不超过2500字。",
        max_tokens=4096,
    )

    # ── 5. citations ──
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
        generated_by="industry_material",
    )


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = "300750"
    industry = ""
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
        guidance="覆盖行业发展现状与规模、竞争格局与各家优势、行业上下游情况、公司行业地位与竞争优势。",
    )

    result = run(company, industry, spec)
    print(f"## {result.title}\n")
    print(result.content)

    if result.citations:
        print(f"\n--- {len(result.citations)} citations ---")
