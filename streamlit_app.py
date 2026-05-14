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
TEMPLATE_DIR = Path("templates")
FINANCIAL_FILES = {
    "资产负债表": "NDSD_BALANCESHEET_2023-2026Q1.xlsx",
    "利润表": "NDSD_EFFORT_2023-2026Q1.xlsx",
    "现金流量表": "NDSD_CASH_2023-2026Q1.xlsx",
}

# ── 工具 ──


def _ensure_db():
    from financial.db import init_db
    init_db()


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


def _generate_report(company_id: str, template_path: str, **variables: str) -> str:
    """生成完整报告：解析模板 → 调 agent → 组装。"""
    from reporting.template import parse_template
    from reporting.assembler import assemble
    from reporting.template import ReportSection

    sections_spec, _ = parse_template(template_path)

    generated: list[ReportSection] = []

    for spec in sections_spec:
        if spec.agent_id == "financial":
            from financial.analyzer import run as financial_run
            section = financial_run(company_id, spec)
            generated.append(section)
        else:
            generated.append(ReportSection(
                section_id=spec.section_id,
                title=spec.title,
                content=f"*（{spec.agent_id} agent 尚未实现，此处为占位内容）*\n",
                citations=[],
                generated_by=spec.agent_id,
            ))

    return assemble(generated, template_path, **variables)


# ── 主界面 ──


def main() -> None:
    st.title("📊 授信报告生成器")
    st.caption("Credit Analysis Report Generator — Demo v0.2")

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
        st.caption("© 2026 授信报告生成器 Demo")

    # ── Step 1: 上传财务文件 ──
    st.header("📋 第一步：导入财务数据")

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

    # ── Step 2: 解析入库 ──
    st.header("🔧 第二步：解析入库")

    if st.button("开始解析", type="primary", use_container_width=True):
        if not company_id:
            st.error("请输入公司股票代码。")
            return

        _ensure_db()

        # 清除该公司旧数据，防止重复入库
        old_rows = _clear_company(company_id)
        if old_rows > 0:
            st.write(f"🧹 清除旧数据 {old_rows} 行")

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

            status.update(label="解析完成", state="complete", expanded=False)

        total_rows = sum(s["rows"] for s in summaries)
        if total_rows == 0:
            st.error("没有数据入库，请检查文件格式。")
            return

        # 收集所有入库的期间
        all_periods = sorted(set(s["period"] for s in summaries if s["period"]))
        st.success(f"入库完成：{total_rows} 行财务数据，{len(summaries)} 份报表，{len(all_periods)} 个期间。")
        st.session_state["data_ready"] = True
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

            st.write("💰 运行财务分析 Agent...")
            try:
                report_md = _generate_report(cid, tpl, **template_vars)
                st.session_state["report_md"] = report_md
                st.write("   ✅ 财务分析完成")
            except Exception as e:
                st.error(f"报告生成失败：{e}")
                logger.exception("Report generation failed")
                return

            status.update(label="报告生成完成", state="complete", expanded=False)

        st.success("报告生成完成！")
        st.session_state["report_ready"] = True

    # ── Step 4: 报告预览 ──
    if st.session_state.get("report_ready") and "report_md" in st.session_state:
        st.header("📄 报告预览")

        report = st.session_state["report_md"]

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
            st.button(
                "📄 导出 Word（待实现）",
                disabled=True,
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
