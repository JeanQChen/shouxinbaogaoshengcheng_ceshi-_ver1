"""Agent 3: 公司主体素材提取。

角色转变：从「写公司分析章节」→「从文档中提取事实素材供 synthesizer 使用」。
输出不是最终报告章节，而是供 synthesizer 写作的结构化素材。

数据来源：
  1. ChromaDB RAG（公司公告、年报全文、债券募集说明书等内部文档）
  2. Web search + akshare（近期新闻、工商信息、舆情等外部信息）
"""

import logging

from llm.client import chat, load_prompt
from reporting.template import Citation, ReportSection, SectionSpec
from retrieval.retriever import retrieve, retrieve_multi, RetrievedChunk

logger = logging.getLogger(__name__)

# BGE-M3 推荐 query instruction prefix（提升检索质量）
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# 按用户要求的 8 个子维度定向检索（自然语言 query，BGE-M3 友好）
_RETRIEVAL_QUERIES = [
    # 1. 企业基本情况
    _QUERY_PREFIX + "公司全称、注册资本、法定代表人、成立日期、经济性质、主营业务概述、经营范围、员工人数、专利数量、主要荣誉",
    # 2. 历史沿革
    _QUERY_PREFIX + "公司设立、股权变更、名称变更、发展历程、改制、增资、上市、重大资产重组等历史沿革信息",
    # 3. 股权结构
    _QUERY_PREFIX + "持股5%以上的股东名称和持股比例、控股股东、实际控制人及其控制路径、股权质押情况",
    # 4. 主要子公司
    _QUERY_PREFIX + "主要控股参股子公司名称、持股比例、注册资本、业务性质、营收规模、总资产",
    # 5. 经营情况（最重要——多个角度）
    _QUERY_PREFIX + "主营业务产品介绍、采购模式、生产模式、销售模式、前五大客户名称及销售占比、前五大供应商名称及采购占比",
    # 5b. 收入成本构成（定向搜索年报"主营业务分析"节段）
    _QUERY_PREFIX + "营业收入构成 按产品分类 按地区分类 营业成本构成 毛利率 近三年各产品收入金额和占比",
    # 6. 核心竞争力 + 未来发展
    _QUERY_PREFIX + "公司核心竞争力、技术优势、研发投入规模及占比、核心专利、品牌优势、发展战略、募投项目",
    # 7. 公司治理
    _QUERY_PREFIX + "公司治理结构、股东大会、董事会、监事会设置、内控制度、关联交易管理制度及执行情况",
    # 8. 董事会和管理人员
    _QUERY_PREFIX + "董事会成员姓名职务年龄履历、董事长简介、总经理简介、财务负责人简介",
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
        section_info = f", 节段: {c.section_title}" if c.section_title else ""
        lines.append(f"[{i}] (来源: {c.source_file}, 类型: {c.source_type}, "
                     f"第{c.page_number}页{section_info}, 匹配度={c.score:.3f})")
        lines.append(f"    {c.text.strip()[:1200]}")
        lines.append("")
    return "\n".join(lines)


def _web_search(company_id: str) -> str:
    """搜索公司近期新闻和工商信息，返回格式化文本。"""
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

        # 公司基本信息（工商信息）
        try:
            info = get_company_info(company_id)
            if info:
                key_names = {
                    "company_name": "公司名称", "industry": "所属行业",
                    "total_market_cap": "总市值", "business_scope": "经营范围",
                    "introduction": "公司介绍", "register_address": "注册地址",
                    "register_capital": "注册资本",
                }
                lines = ["### 工商/公开信息"]
                for k, label in key_names.items():
                    v = info.get(k) or info.get(label, "")
                    if v and v != "None":
                        lines.append(f"- {label}: {v}")
                parts.append("\n".join(lines))
        except Exception:
            pass

        # 公司名搜索重大事项
        try:
            info = get_company_info(company_id)
            company_name = info.get("company_name") or info.get("公司名称") or ""
            if company_name:
                for search_q in [
                    f"{company_name} 重大事项 诉讼 担保 处罚",
                    f"{company_name} 股权结构 控股股东 实际控制人",
                ]:
                    try:
                        result = search_web(search_q, max_results=5)
                        if result and "未检索到" not in result:
                            parts.append(f"### 互联网检索: {search_q}\n{result}")
                    except Exception:
                        pass
        except Exception:
            pass

        if parts:
            return "\n\n".join(parts)
    except Exception:
        pass

    return f"（暂无公司 {company_id} 的外部新闻数据。）"


def run(company_id: str, section_spec: SectionSpec) -> ReportSection:
    """生成公司主体素材（供 synthesizer 使用）。

    1. 从 ChromaDB 8 维度定向检索内部文档
    2. 合并去重后格式化为 prompt 输入
    3. LLM 提取事实素材——不是最终报告 prose
    """
    # ── 1. 多角度检索 ──
    all_chunks: list[RetrievedChunk] = []
    for query in _RETRIEVAL_QUERIES:
        try:
            chunks = retrieve(company_id, "company_docs", query, k=8)
            all_chunks.extend(chunks)
        except ValueError as e:
            logger.warning("Retrieval failed for query '%.40s': %s", query, e)
            continue

    unique_chunks = _dedup_chunks(all_chunks)
    logger.info("Retrieved %d chunks (%d unique) for company_subject (9 queries × k=8)",
                len(all_chunks), len(unique_chunks))

    if not unique_chunks:
        return ReportSection(
            section_id=section_spec.section_id,
            title=section_spec.title,
            content="*无法生成：未在内部文档库中检索到该公司相关文档。请先上传 PDF 公告或年报。*",
            citations=[],
            generated_by="company_subject_material",
        )

    # 取 top 50 chunks（保证信息覆盖 8 个维度）
    top_chunks = unique_chunks[:50]

    # 统计来源
    from collections import Counter
    source_dist = Counter(c.source_file for c in top_chunks)
    type_dist = Counter(c.source_type for c in top_chunks)
    section_dist = Counter(c.section_title for c in top_chunks if c.section_title)
    logger.info("Material sources: files=%s, types=%s, top_sections=%s",
                dict(source_dist), dict(type_dist), dict(section_dist.most_common(5)))

    # ── 2. 格式化输入 ──
    formatted_chunks = _format_chunks(top_chunks)
    web_results = _web_search(company_id)

    # ── 3. LLM 提取素材 ──
    prompt = load_prompt("company_subject")
    prompt = prompt.format(
        retrieved_chunks=formatted_chunks,
        web_search_results=web_results,
        guidance=section_spec.guidance or (
            "从文档和公开信息中提取公司事实素材，按1.1-1.8八个维度组织。"
            "信息不足的维度标记[待补充]，不编造。标注来源编号。"
        ),
    )

    content = chat(
        messages=[{"role": "user", "content": prompt}],
        system="你是一个专业的资料整理员。你的任务是从检索材料中提取事实信息，按维度整理成结构化素材。不写最终报告散文。尽量完整提取，不要省略细节——特别是人员履历、业务描述等长文本，逐条完整列出。",
        max_tokens=8192,
    )

    # ── 4. 构建 citations ──
    citations: list[Citation] = []
    for c in top_chunks[:10]:
        citations.append(Citation(
            source_type="chromadb",
            source_ref=f"page={c.page_number},chunk={c.chunk_index},"
                       f"source={c.source_file},type={c.source_type}",
            snippet=c.text[:200],
        ))

    return ReportSection(
        section_id=section_spec.section_id,
        title=section_spec.title,
        content=content.strip(),
        citations=citations,
        generated_by="company_subject_material",
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
        section_id="company_subject.公司基本情况",
        title="公司基本情况和经营情况",
        agent_id="company_subject",
        guidance="从上传材料和公开信息中提取公司事实，按1.1-1.8子维度组织素材。",
    )

    result = run(company, spec)
    print(f"## {result.title}\n")
    print(result.content)

    if result.citations:
        print(f"\n--- {len(result.citations)} citations ---")
        for c in result.citations[:5]:
            print(f"  [{c.source_type}] {c.source_ref}")
