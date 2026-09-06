"""Streamlit 主入口 — 只负责 UI + 调 agent，不写业务逻辑。

UI 职责：
  - 接收用户输入（公司名、文件、模板选择）
  - 调用各 agent 并展示进度
  - Markdown 实时预览
  - Word 下载按钮（W4）
"""

import logging
import sys
from pathlib import Path

import streamlit as st

from config import DEMO_MODE

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 页面配置
st.set_page_config(
    page_title="授信报告生成器",
    page_icon="📊",
    layout="wide",
)

# ── 常量 ──
SAMPLE_DIR = Path("data/samples/300750/financial")
ANNOUNCEMENT_DIR = Path("data/samples/300750/announcements")
TEMPLATE_DIR = Path("templates")
FINANCIAL_FILES = {
    "资产负债表": "NDSD_BALANCESHEET_2023-2026Q1.xlsx",
    "利润表": "NDSD_EFFORT_2023-2026Q1.xlsx",
    "现金流量表": "NDSD_CASH_2023-2026Q1.xlsx",
}
ANNOUNCEMENT_FILES = [
    "NDSD_KCZ_2026.pdf",       # 科创债募集说明书（最详细，优先）
    "NDSD_2025_year.pdf",
    "NDSD_2024_year.pdf",
]
CHROMA_DB_PATH = "data/chroma"

# ── 工具 ──


def _ensure_db():
    from financial.db import init_db
    init_db()


def _clear_chroma_collection(company_id: str, collection: str = "company_docs") -> None:
    """清除某公司在 ChromaDB 的旧索引数据。"""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        name = f"{collection}__{company_id}"
        client.delete_collection(name)
        logger.info("Cleared ChromaDB collection: %s", name)
    except Exception:
        # 集合不存在时 delete_collection 会抛异常，忽略即可
        pass


def _clear_company(company_id: str) -> int:
    """删除某公司的旧数据（report_meta + 三张表），返回删除行数。"""
    from financial.db import _get_conn
    conn = _get_conn()
    try:
        deleted = 0
        for table in ("balance_sheet", "income_statement", "cash_flow"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE report_id IN "
                "(SELECT id FROM report_meta WHERE company_id = ?)",
                (company_id,),
            )
            deleted += cur.rowcount
        cur = conn.execute("DELETE FROM report_meta WHERE company_id = ?", (company_id,))
        deleted += cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def _parse_and_save(file_path: str, company_id: str) -> dict:
    """解析 Excel 并入库，返回摘要。"""
    from parsers.excel_parser import parse as excel_parse
    from parsers.schema_mapper import map_to_schema
    from financial.db import insert_report

    parsed = excel_parse(file_path)
    result = map_to_schema(parsed, company_id)
    if result.rows:
        report_id = insert_report(result.report_meta, result.rows)
        return {
            "file": Path(file_path).name,
            "report_id": report_id,
            "rows": len(result.rows),
            "unmapped": len(result.unmapped_items),
            "period": result.report_meta.get("report_period", ""),
        }
    return {
        "file": Path(file_path).name,
        "report_id": None,
        "rows": 0,
        "unmapped": len(result.unmapped_items),
        "period": "",
    }


def _parse_and_index_pdf(file_path: str, company_id: str) -> tuple[dict, object]:
    """解析一次 PDF 并索引到 ChromaDB（V1）。

    返回 (摘要 dict, PdfParseResult)。同一份 PdfParseResult 可再交给 Evidence
    Builder 消费，避免同一 PDF 被完整解析两次。
    """
    from parsers.pdf_parser import parse as pdf_parse
    from retrieval.indexer import index_pdf

    result = pdf_parse(file_path)
    summary = {
        "file": Path(file_path).name,
        "chunks": len(result.chunks),
        "pages": result.page_count,
        "collection": "",
    }
    if result.chunks:
        summary["collection"] = index_pdf(
            result.chunks, company_id, "company_docs",
            source_file=Path(file_path).name,
        )
    else:
        summary["quality"] = result.metadata.get("quality", "unknown")
    return summary, result


def _process_pdf(file_path: str, company_id: str) -> dict:
    """解析 PDF 并索引到 ChromaDB，返回摘要（Demo 一键生成路径，不构建 Evidence）。"""
    summary, _ = _parse_and_index_pdf(file_path, company_id)
    return summary


def _build_evidence_from_parsed(parsed, file_path: str, company_id: str) -> dict:
    """用已解析结果构建 Evidence（不重复解析），返回展示状态摘要。

    纯编排：把 PdfParseResult 交给 evidence.builder.run_pipeline_from_parsed，
    状态用 builder.classify_status 归类为 completed/empty/failed。任何构建失败
    都以 failed 返回，绝不把失败误报为成功。
    """
    from evidence import builder as evidence_builder

    try:
        summary = evidence_builder.run_pipeline_from_parsed(
            parsed, file_path, company_id,
            source_type=None, material_group="company_industry", store_it=True,
        )
        count = summary["evidence"]["count"]
        return {
            "status": evidence_builder.classify_status(count),
            "source_type": evidence_builder.infer_source_type(Path(file_path).name),
            "run_id": summary["run_id"],
            "count": count,
            "error": None,
        }
    except Exception as e:
        logger.exception("Evidence build failed for %s", file_path)
        return {
            "status": evidence_builder.classify_status(0, error=e),
            "source_type": evidence_builder.infer_source_type(Path(file_path).name),
            "run_id": None,
            "count": 0,
            "error": str(e),
        }


def _render_evidence_progress(run_id: str) -> None:
    """只读展示某次 Evidence 构建的真实进度事件。

    仅订阅 evidence.progress 已落盘的事件并渲染，不包含解析、计数或恢复逻辑
    （这些都在 evidence/ 包内）。状态与真实 ProgressEvent 一一对应。
    """
    from evidence import progress as evidence_progress

    events = evidence_progress.history(run_id)
    if not events:
        return
    for ev in events:
        if ev.status not in ("completed", "failed"):
            continue
        label = evidence_progress.STAGE_LABELS.get(ev.stage_id, ev.stage_id)
        if ev.status == "completed":
            if ev.completed_units is not None and ev.total_units:
                st.write(f"   ✅ {label}（{ev.completed_units}/{ev.total_units}）")
            else:
                st.write(f"   ✅ {label}")
        else:
            st.write(f"   ❌ {label}：{ev.error_code or ev.message_code}")


def _render_evidence_status(label: str, ev: dict) -> None:
    """只读展示单个 PDF 的 Evidence 构建结果（completed/empty/failed）。"""
    if ev["status"] == "completed":
        st.write(f"   ✅ Evidence：{ev['count']} 条证据（{ev['source_type']}），可回查")
        if ev["run_id"]:
            _render_evidence_progress(ev["run_id"])
    elif ev["status"] == "empty":
        st.write(f"   ⚠️ Evidence：0 条有效证据（{ev['source_type']}），无可回查块")
    else:
        st.write(f"   ❌ Evidence 构建失败：{ev['error']}")


def _generate_report(company_id: str, template_path: str, **variables: str) -> tuple[str, list]:
    """生成完整报告：解析模板 → 三 agent 并行产出素材 → synthesizer 主笔 → 回检。

    新架构：synthesizer 是报告主笔，拿三份素材 + 模板全文写出完整报告。
    不再走 assemble() 拼接。

    Returns (report_markdown, verification_issues).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from reporting.template import parse_template, ReportSection, SectionSpec

    sections_spec, template_text = parse_template(template_path)

    # ── 分组 agent ──
    parallel_specs: list[SectionSpec] = []
    synthesizer_spec: SectionSpec | None = None

    seen_agents: set[str] = set()
    for spec in sections_spec:
        if spec.agent_id in seen_agents:
            continue
        seen_agents.add(spec.agent_id)
        if spec.agent_id == "synthesizer":
            synthesizer_spec = spec
        elif spec.agent_id in ("financial", "company_subject", "industry"):
            parallel_specs.append(spec)

    # ── 获取行业信息 ──
    industry_code = ""
    try:
        from external.akshare_client import get_company_info
        info = get_company_info(company_id)
        industry_code = info.get("industry", "") or info.get("所属行业", "") or ""
    except Exception:
        pass

    # ── 并行运行三 agent（产出素材） ──
    generated: dict[str, ReportSection] = {}

    def run_agent(spec: SectionSpec) -> tuple[str, ReportSection]:
        if spec.agent_id == "financial":
            from financial.analyzer import run as financial_run
            return (spec.agent_id, financial_run(company_id, spec))
        elif spec.agent_id == "company_subject":
            from agents.company_subject import run as company_subject_run
            return (spec.agent_id, company_subject_run(company_id, spec))
        elif spec.agent_id == "industry":
            from agents.industry import run as industry_run
            return (spec.agent_id, industry_run(company_id, industry_code, spec))
        else:
            placeholder = ReportSection(
                section_id=spec.section_id, title=spec.title,
                content=f"*（{spec.agent_id} agent 尚未实现，此处为占位内容）*\n",
                citations=[], generated_by=spec.agent_id,
            )
            return (spec.agent_id, placeholder)

    if parallel_specs:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(run_agent, spec): spec for spec in parallel_specs}
            for future in as_completed(futures):
                agent_id, section = future.result()
                generated[agent_id] = section

    # ── 综合 agent（报告主笔） ──
    if synthesizer_spec:
        try:
            from agents.synthesizer import run as synthesizer_run
            all_sections = list(generated.values())
            report_md = synthesizer_run(
                company_id,
                all_sections,
                synthesizer_spec,
                template_text=template_text,
                template_vars=variables,
            )
            # synthesizer 现在返回完整报告，直接取 content
            report_md = report_md.content
        except Exception:
            logger.exception("Synthesizer failed")
            # Fallback: 拼接各素材
            parts = [f"# 授信分析报告\n\n> 报告对象：{variables.get('company_name', company_id)}"]
            for s in all_sections:
                parts.append(f"\n---\n\n{s.content}")
            report_md = "\n".join(parts)
    else:
        # 无 synthesizer spec：拼接素材
        parts = [f"# 授信分析报告\n\n> 报告对象：{variables.get('company_name', company_id)}"]
        for s in generated.values():
            parts.append(f"\n---\n\n{s.content}")
        report_md = "\n".join(parts)

    # ── 回检 ──
    verification_issues = []
    try:
        from agents.verifier import run as verifier_run
        verification = verifier_run(report_md, company_id)
        report_md = verification.annotated_markdown
        verification_issues = verification.issues
    except Exception:
        logger.warning("Verifier failed", exc_info=True)

    return report_md, verification_issues


# ── 主界面 ──


ERROR_MESSAGES = {
    "NetworkError": "网络连接失败，请检查网络后重试。DEMO 模式下可忽略此错误（使用本地缓存数据）。",
    "NoDataError": "未找到该公司的财务数据。请确认已上传正确的财务报表 Excel 文件。",
    "LLMTimeout": "AI 模型响应超时，请稍后重试。",
    "PDFParseError": "PDF 解析失败，可能是扫描件或加密文件。请尝试其他 PDF，或在无 PDF 情况下继续。",
    "GenericError": "报告生成过程中出现错误，请检查终端日志了解详情。",
}

def _classify_error(e: Exception) -> str:
    """将异常映射为用户友好的错误提示。"""
    msg = str(e).lower()
    if "network" in msg or "connect" in msg or "timeout" in msg or "dns" in msg:
        return ERROR_MESSAGES["NetworkError"]
    if "no data" in msg or "empty" in msg:
        return ERROR_MESSAGES["NoDataError"]
    if "timeout" in msg or "timed out" in msg:
        return ERROR_MESSAGES["LLMTimeout"]
    if "pdf" in msg or "parse" in msg:
        return ERROR_MESSAGES["PDFParseError"]
    return ERROR_MESSAGES["GenericError"]


def _render_step_indicator(current_step: int) -> None:
    """渲染四步进度指示器。"""
    steps = [
        ("导入数据", "📋"),
        ("数据处理", "🔧"),
        ("生成报告", "📝"),
        ("预览导出", "📄"),
    ]
    cols = st.columns(4)
    for i, (col, (name, icon)) in enumerate(zip(cols, steps)):
        with col:
            if i < current_step:
                st.markdown(f"✅ ~~{icon} {name}~~")
            elif i == current_step:
                st.markdown(f"**🔄 {icon} {name}**")
            else:
                st.markdown(f"⬜ {icon} {name}")


def main() -> None:
    st.title("📊 授信报告生成器")
    st.caption("Credit Analysis Report Generator — Demo v0.5")

    # ── 步骤指示器 ──
    current_step = 0
    if st.session_state.get("data_ready"):
        current_step = 1
    if st.session_state.get("report_ready"):
        current_step = 2
    # Step 3 is reached after viewing the report
    _render_step_indicator(current_step)
    st.divider()

    # ── Sidebar ──
    with st.sidebar:
        st.header("⚙️ 设置")

        if DEMO_MODE:
            st.info("🎯 **DEMO 模式已启用**")

        # 公司标识
        company_id = st.text_input(
            "公司股票代码",
            value="300750" if DEMO_MODE else "",
            placeholder="如 300750、600519",
            help="A 股六位股票代码",
        ).strip()

        st.divider()

        # 模板选择
        template_choice = st.selectbox(
            "报告模板",
            options=["standard", "simple"],
            format_func=lambda x: "标准授信报告" if x == "standard" else "简版授信报告",
        )
        template_path = str(TEMPLATE_DIR / f"{template_choice}.md")

        st.divider()

        # ── Demo 一键生成 ──
        if DEMO_MODE:
            if st.button("🚀 一键生成 Demo 报告", type="primary", use_container_width=True):
                _ensure_db()

                cid = company_id or "300750"
                tpl = template_path

                # 清除旧数据
                old_rows = _clear_company(cid)
                _clear_chroma_collection(cid)

                # 解析财务 Excel
                excel_summaries = []
                for label, fname in FINANCIAL_FILES.items():
                    fpath = SAMPLE_DIR / fname
                    if fpath.exists():
                        try:
                            summary = _parse_and_save(str(fpath), cid)
                            excel_summaries.append({**summary, "label": label})
                        except Exception as e:
                            logger.exception("Demo parse failed for %s", fpath)

                total_rows = sum(s["rows"] for s in excel_summaries)
                all_periods = sorted(set(s["period"] for s in excel_summaries if s["period"]))

                # 解析 PDF
                pdf_ok = False
                for fname in ANNOUNCEMENT_FILES:
                    fpath = ANNOUNCEMENT_DIR / fname
                    if fpath.exists():
                        try:
                            pdf_summary = _process_pdf(str(fpath), cid)
                            pdf_ok = pdf_summary["chunks"] > 0
                        except Exception as e:
                            logger.exception("Demo PDF failed for %s", fpath)

                # 获取模板变量
                from financial.db import init_db as _idb, get_company_info
                _idb()
                info = get_company_info(cid)
                from datetime import datetime as _dt
                template_vars = {
                    "company_name": info.get("company_name") or cid,
                    "stock_code": info.get("stock_code") or cid,
                    "report_period": info.get("report_period") or "/".join(all_periods),
                    "generated_at": _dt.now().strftime("%Y-%m-%d"),
                }

                # 生成报告
                with st.status("正在生成授信报告...", expanded=True) as status:
                    st.write("⚡ 并行运行分析 Agent...")
                    try:
                        report_md, verification_issues = _generate_report(cid, tpl, **template_vars)
                        st.session_state["report_md"] = report_md
                        st.session_state["verification_issues"] = verification_issues
                        st.write(f"   ✅ 数据入库: {total_rows} 行，{len(excel_summaries)} 份报表")
                        if pdf_ok:
                            st.write("   ✅ PDF 公告已索引")
                        st.write("   ✅ 公司主体分析、财务分析、行业分析完成")
                        st.write("   ✅ 综合授信意见完成")
                        if verification_issues:
                            yellow = sum(1 for i in verification_issues if i.severity == "yellow")
                            red = sum(1 for i in verification_issues if i.severity == "red")
                            orange = sum(1 for i in verification_issues if i.severity == "orange")
                            st.write(f"   ✅ 回检完成（{len(verification_issues)} 项：🟡{yellow} 🔴{red} 🟠{orange}）")
                    except Exception as e:
                        st.error(f"报告生成失败：{e}")
                        logger.exception("Demo generation failed")
                        status.update(label="报告生成失败", state="error")
                        return
                    status.update(label="报告生成完成", state="complete", expanded=False)

                st.session_state["data_ready"] = True
                st.session_state["pdf_ready"] = pdf_ok
                st.session_state["company_id"] = cid
                st.session_state["template_path"] = tpl
                st.session_state["parsed_periods"] = all_periods
                st.session_state["report_ready"] = True
                st.success(f"Demo 报告生成完成！（{total_rows} 行财务数据，{len(all_periods)} 个期间）")
                st.rerun()

        st.caption("© 2026 授信报告生成器 Demo")

    # ── Step 1: 上传文件 ──
    st.header("📋 第一步：导入数据")

    # --- 财务 Excel ---
    st.subheader("📊 财务报表 (Excel)")

    if DEMO_MODE:
        st.info("DEMO 模式：使用预置宁德时代（300750）样本数据，无需上传。")
        files_to_process: list[tuple[str, str]] = []
        for label, fname in FINANCIAL_FILES.items():
            fpath = SAMPLE_DIR / fname
            if fpath.exists():
                files_to_process.append((label, str(fpath)))
        if not files_to_process:
            st.error(f"样本数据目录 {SAMPLE_DIR} 不存在或为空，请先运行 `make demo-data`。")
            return
    else:
        uploaded = st.file_uploader(
            "上传财务报表 Excel（可多选）",
            type=["xlsx"],
            accept_multiple_files=True,
            help="支持资产负债表、利润表、现金流量表。当前仅支持 .xlsx 格式。",
        )
        if not uploaded:
            st.warning("请上传至少一份财务报表 Excel 文件，或启用 DEMO 模式。")
            return
        # 保存上传文件到临时目录
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        files_to_process = []
        for f in uploaded:
            tmppath = tmpdir / f.name
            tmppath.write_bytes(f.read())
            files_to_process.append((f.name, str(tmppath)))

    # --- PDF 公告 ---
    st.subheader("📑 公司公告 (PDF，推荐)")

    if DEMO_MODE:
        pdf_files_to_process: list[tuple[str, str]] = []
        for fname in ANNOUNCEMENT_FILES:
            fpath = ANNOUNCEMENT_DIR / fname
            if fpath.exists():
                pdf_files_to_process.append((fname, str(fpath)))
        if pdf_files_to_process:
            st.write(f"✅ 已加载 {len(pdf_files_to_process)} 份 PDF 公告："
                     f"{', '.join(f[0] for f in pdf_files_to_process)}")
        else:
            st.write("（无预置 PDF 公告）")
    else:
        pdf_uploaded = st.file_uploader(
            "上传公司公告 PDF（可多选，可选）",
            type=["pdf"],
            accept_multiple_files=True,
            help="上传年报、公告等 PDF 文件用于公司主体分析。",
        )
        pdf_files_to_process = []
        if pdf_uploaded:
            import tempfile
            pdf_tmpdir = Path(tempfile.mkdtemp())
            for f in pdf_uploaded:
                tmppath = pdf_tmpdir / f.name
                tmppath.write_bytes(f.read())
                pdf_files_to_process.append((f.name, str(tmppath)))

    # ── Step 2: 数据处理 ──
    st.header("🔧 第二步：数据处理")

    build_evidence = st.checkbox(
        "🔗 同时构建可追溯证据链（Evidence，用于证据定位与回查）",
        value=True,
        help=(
            "默认开启：解析 PDF 时同一份解析结果既索引到 V1 检索，也构建可回查 Evidence "
            "（按公司/文件版本/物理页），二者共享一次解析，互不影响。Evidence 构建失败"
            "不影响 V1 检索，仅按失败/部分状态展示。"
        ),
    )

    if st.button("开始解析", type="primary", use_container_width=True):
        if not company_id:
            st.error("请输入公司股票代码。")
            return

        _ensure_db()

        # --- 处理财务 Excel ---
        old_rows = _clear_company(company_id)
        if old_rows > 0:
            st.write(f"🧹 清除旧财务数据 {old_rows} 行")

        with st.status("正在解析财务报表...", expanded=True) as status:
            summaries: list[dict] = []
            for label, fpath in files_to_process:
                st.write(f"📄 解析 {label}：{Path(fpath).name}")
                try:
                    summary = _parse_and_save(fpath, company_id)
                    summaries.append({**summary, "label": label})
                    if summary["rows"] > 0:
                        st.write(
                            f"   ✅ {label} → {summary['rows']} 行入库"
                            + (f"，{summary['unmapped']} 项未匹配" if summary["unmapped"] else "")
                        )
                    else:
                        st.write(f"   ⚠️ {label} → 0 行入库，{summary['unmapped']} 项未匹配")
                except Exception as e:
                    st.write(f"   ❌ {label} 解析失败：{e}")
                    logger.exception("Parse failed for %s", fpath)

            status.update(label="财务报表解析完成", state="complete", expanded=False)

        total_rows = sum(s["rows"] for s in summaries)
        if total_rows == 0:
            st.error("没有财务数据入库，请检查文件格式。")
            return

        all_periods = sorted(set(s["period"] for s in summaries if s["period"]))
        st.success(f"财务入库完成：{total_rows} 行数据，{len(summaries)} 份报表，{len(all_periods)} 个期间。")

        # --- 处理 PDF 公告（一次解析：V1 索引 + 可选 Evidence 两路消费）---
        pdf_ok = False
        if pdf_files_to_process:
            _clear_chroma_collection(company_id)

            with st.status("正在解析 PDF 公告...", expanded=True) as pdf_status:
                pdf_summaries: list[dict] = []
                for label, fpath in pdf_files_to_process:
                    st.write(f"📑 解析 {label}...")
                    try:
                        pdf_summary, parsed = _parse_and_index_pdf(fpath, company_id)
                        pdf_summaries.append({**pdf_summary, "label": label})
                        if pdf_summary["chunks"] > 0:
                            st.write(
                                f"   ✅ {label} → {pdf_summary['chunks']} 个文本块"
                                f"（{pdf_summary['pages']} 页），已索引到 ChromaDB"
                            )
                        else:
                            st.write(
                                f"   ⚠️ {label} → 0 个文本块"
                                f"（{pdf_summary.get('quality', 'unknown')} 质量）"
                            )
                        # Evidence 复用同一份解析结果，不重复解析；失败/部分仅如实展示。
                        if build_evidence:
                            _render_evidence_status(label, _build_evidence_from_parsed(parsed, fpath, company_id))
                    except Exception as e:
                        st.write(f"   ❌ {label} 解析失败：{e}")
                        logger.exception("PDF parse failed for %s", fpath)

                pdf_status.update(label="PDF 公告处理完成", state="complete", expanded=False)

            total_chunks = sum(s["chunks"] for s in pdf_summaries)
            if total_chunks > 0:
                st.success(f"PDF 处理完成：{total_chunks} 个文本块，{len(pdf_summaries)} 份文件。")
                pdf_ok = True
            else:
                if pdf_summaries:
                    st.warning(f"PDF 解析未产生有效文本块（可能为扫描件或空白文件）。")
        else:
            st.info("（未上传 PDF 公告，公司主体分析将依赖其他数据源。）")

        st.session_state["data_ready"] = True
        st.session_state["pdf_ready"] = pdf_ok
        st.session_state["company_id"] = company_id
        st.session_state["template_path"] = template_path
        st.session_state["parsed_periods"] = all_periods
    else:
        if "data_ready" not in st.session_state:
            st.session_state["data_ready"] = False

    # ── Step 3: 生成报告 ──
    st.header("📝 第三步：生成授信报告")

    can_generate = st.session_state.get("data_ready", False)

    if st.button("生成报告", type="primary", use_container_width=True, disabled=not can_generate):
        cid = st.session_state.get("company_id", company_id)
        tpl = st.session_state.get("template_path", template_path)

        pdf_ready = st.session_state.get("pdf_ready", False)

        with st.status("正在生成授信报告...", expanded=True) as status:
            st.write("🔍 解析报告模板...")
            from reporting.template import parse_template
            sections, _ = parse_template(tpl)
            st.write(f"   模板包含 {len(sections)} 个章节")

            # 获取公司信息用于模板变量替换
            from financial.db import init_db as _idb, get_company_info
            _idb()
            info = get_company_info(cid)
            from datetime import datetime as _dt
            template_vars = {
                "company_name": info.get("company_name") or cid,
                "stock_code": info.get("stock_code") or cid,
                "report_period": info.get("report_period") or "/".join(st.session_state.get("parsed_periods", [])),
                "generated_at": _dt.now().strftime("%Y-%m-%d"),
            }

            st.write("⚡ 并行运行公司主体、财务、行业分析 Agent...")
            try:
                report_md, verification_issues = _generate_report(cid, tpl, **template_vars)
                st.session_state["report_md"] = report_md
                st.session_state["verification_issues"] = verification_issues
                st.write("   ✅ 公司主体分析完成")
                st.write("   ✅ 财务分析完成")
                if pdf_ready:
                    st.write("   ✅ 行业分析完成")
                st.write("   ✅ 综合授信意见完成")
                if verification_issues:
                    yellow = sum(1 for i in verification_issues if i.severity == "yellow")
                    red = sum(1 for i in verification_issues if i.severity == "red")
                    orange = sum(1 for i in verification_issues if i.severity == "orange")
                    st.write(f"   ✅ 回检完成（{len(verification_issues)} 项：🟡{yellow} 🔴{red} 🟠{orange}）")
            except Exception as e:
                friendly_msg = _classify_error(e)
                st.error(f"报告生成失败：{friendly_msg}")
                logger.exception("Report generation failed")
                return

            status.update(label="报告生成完成", state="complete", expanded=False)

        st.success("报告生成完成！")
        st.session_state["report_ready"] = True

    # ── Step 4: 报告预览 ──
    if st.session_state.get("report_ready") and "report_md" in st.session_state:
        st.header("📄 报告预览")

        report = st.session_state["report_md"]
        verification_issues = st.session_state.get("verification_issues", [])

        # 回检摘要
        if verification_issues:
            yellow = sum(1 for i in verification_issues if i.severity == "yellow")
            red = sum(1 for i in verification_issues if i.severity == "red")
            orange = sum(1 for i in verification_issues if i.severity == "orange")
            st.info(
                f"🔍 **回检摘要**：发现 {len(verification_issues)} 项问题"
                f"（🟡 数值存疑: {yellow}，🔴 实体异常: {red}，🟠 时效提醒: {orange}）。"
                f"报告中已用彩色标签标注，请复核后再导出。"
            )

        # 简单回检标注：高亮占位内容
        import re
        report_display = re.sub(
            r"\*（(.+?)agent 尚未实现.+?）\*",
            r"🚧 *\1（待实现）*",
            report,
        )

        st.markdown(report_display)

        st.divider()

        # 下载按钮
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "⬇️ 下载 Markdown",
                data=report,
                file_name=f"credit_report_{st.session_state.get('company_id', 'unknown')}.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with col2:
            # Word 导出
            word_exported = False
            if st.button("📄 导出 Word", type="primary", use_container_width=True):
                try:
                    from reporting.word_exporter import export as word_export
                    import tempfile, os as _os
                    fd, tmp_path = tempfile.mkstemp(suffix=".docx")
                    _os.close(fd)
                    word_export(report, tmp_path)
                    with open(tmp_path, "rb") as f:
                        word_data = f.read()
                    st.session_state["word_data"] = word_data
                    st.session_state["word_ready"] = True
                    word_exported = True
                except NotImplementedError:
                    st.warning("Word 导出功能尚未实现，请先下载 Markdown。")
                except Exception as e:
                    st.error(f"Word 导出失败: {e}")
                    logger.exception("Word export failed")

            if st.session_state.get("word_ready"):
                st.download_button(
                    "⬇️ 下载 Word 报告",
                    data=st.session_state["word_data"],
                    file_name=f"credit_report_{st.session_state.get('company_id', 'unknown')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )

        # ── 使用提示 ──
        with st.expander("💡 使用提示", expanded=False):
            st.markdown("""
            - **回检标注**：报告中的 🟡 黄色高亮为数值差异提示（报告值与数据库差异 > 5%），🔴 红色为实体核查异常（公司名/代码与公开信息不一致），🟠 橙色为时效性提醒（引用数据可能过时）。请复核后导出。
            - **引用来源**：公司主体分析中的 [N] 标注对应"内部文档检索结果"部分的编号，可追溯信息来源。
            - **财务数据**：财务分析章节中的所有指标由系统精确计算，来自上传的 Excel 财务报表。
            - **行业分析**：基于互联网公开检索结果，标注"据公开信息"的为直接引用，"研判"的为行业判断。
            - **回检规则**：可在 `agents/verifier.py` 中自定义阈值（当前数值差异阈值为 5%）。
            """)


if __name__ == "__main__":
    main()
