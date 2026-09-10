"""Streamlit 入口 — Phase 4 薄预览 + Phase 3 V2 实验面板。

职责（任务书 §15 / 约束 #12）：
- **Phase 4 主流程（薄）**：收集用户输入，调用 `sections.service.run_phase4` 生成三章节
  （规划 + Worker + 评估 + 定向返工），只读展示 Phase4RunResult 的三章节 Preview DTO
  与 RunManifest 摘要。不在此写任何业务逻辑（不解析 Excel/PDF、不索引 ChromaDB、
  不构建 Evidence、不算财务指标、不写评估规则、不做返工、不导出 Word）。
- **Phase 3 V2 实验面板（冻结，原样保留）**：`_render_financial_v2_confirmation` /
  `_render_financial_v2_snapshot` 是 Phase 3 Batch A/B 的薄 UI（默认关闭，仅调
  financial_v2 服务接口），受 `evals.test_financial_v2_resolutions_ui` /
  `evals.test_financial_v2_progress` 冻结测试约束，不得删除或改写。

依赖数据库（financial_v2 快照 + evidence 证据）必须事先准备好；缺失时只报错提示，
不在此自动重建。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import streamlit as st

from config import DEMO_MODE

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="授信报告生成器（Phase 4）",
    page_icon="📊",
    layout="wide",
)

# ── 常量（与 sections.service.ServiceConfig 默认一致） ──
FIN_DB = "data/financial_v2.db"
EV_DB = "data/evidence.db"

SECTION_OPTIONS = ("company", "financial", "industry")
SECTION_LABELS = {
    "company": "公司信用",
    "financial": "财务分析",
    "industry": "行业研究",
}

_DECISION_ICON = {"PASS": "✅", "REWORK": "🔁", "BLOCKED": "⛔"}


# ---------------------------------------------------------------------------
# Phase 4 薄预览（只调用 sections.service，展示 DTO）
# ---------------------------------------------------------------------------

def _severity_icon(severity: str) -> str:
    return {
        "blocking": "⛔",
        "red": "🔴",
        "orange": "🟠",
        "yellow": "🟡",
        "rework": "🔁",
    }.get(severity, "⚪")


def _job_id() -> str:
    return "job_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _friendly_error(e: Exception) -> str:
    """把异常映射为友好提示（只展示，不吞错）。"""
    if isinstance(e, FileNotFoundError):
        return (
            f"依赖数据库不存在：{e}。\n\n"
            "请先准备财务快照与 Evidence 证据（运行数据准备脚本 / `make demo-data`），"
            "再生成报告。"
        )
    if isinstance(e, ValueError):
        return str(e)
    return f"{type(e).__name__}: {e}"


def _render_manifest(result) -> None:
    """只读展示 RunManifest 摘要。"""
    m = result.manifest
    st.caption(
        f"run_id `{result.run_id}` · manifest `{m.manifest_id[:16]}…` · "
        f"plan `{result.plan_id[:16]}…` · 代码指纹 `{m.code_fingerprint[:12]}…`"
    )
    with st.expander("🔎 RunManifest 详情", expanded=False):
        st.json(
            {
                "job_id": result.job_id,
                "run_id": result.run_id,
                "plan_id": result.plan_id,
                "manifest_id": result.manifest_id,
                "code_fingerprint": m.code_fingerprint,
                "phase3_closure_fingerprint": m.phase3_closure_fingerprint,
                "created_at": m.created_at,
                "frozen": m.frozen,
            },
            expanded=False,
        )


def _render_preview(p, outcome) -> None:
    """只读展示单个章节的 Preview DTO + 评估问题 / 未解决缺口详情。"""
    decision_icon = _DECISION_ICON.get(p.decision, "⬜")
    status_icon = {
        "COMPLETED": "✅",
        "SECTION_BLOCKED": "⛔",
        "FAILED": "❌",
    }.get(p.status, "🟠")

    st.subheader(f"{status_icon} {p.title}（{p.section_id}）")

    if p.error:
        st.error(p.error)
        return

    flags = []
    if p.decision:
        flags.append(f"决策 {decision_icon} `{p.decision}`")
    flags.append(f"claims {p.claim_count}")
    flags.append(f"unresolved {p.unresolved_count}")
    if p.issue_count:
        flags.append(f"issues {p.issue_count}")
    if p.rework_attempted:
        flags.append("🔁 已返工")
    if p.final_check_passed is not None:
        flags.append("最终检查 " + ("✅" if p.final_check_passed else "❌"))
    flags.append(f"LLM Evaluator 调用 {p.llm_evaluator_calls}")
    st.caption(" · ".join(flags))

    if outcome is not None and outcome.evaluation is not None:
        for issue in outcome.evaluation.issues:
            st.warning(
                f"{_severity_icon(issue.severity)} `{issue.rule_id}`"
                f"（{issue.severity}）— {issue.detail}"
            )

    if outcome is not None and outcome.section_result is not None:
        for u in outcome.section_result.unresolved:
            st.info(f"⚠️ `{u.reason_code}` — {u.detail}")

    st.markdown(p.markdown or "*（无正文）*")
    st.divider()


def _render_result(result) -> None:
    st.success("✅ Phase 4 三章节生成完成" if result.success
               else "⚠️ Phase 4 生成存在阻断或失败（见各章节）")
    _render_manifest(result)
    st.divider()
    for outcome, preview in zip(result.sections, result.previews()):
        _render_preview(preview, outcome)


# ---------------------------------------------------------------------------
# Phase 3 V2 实验面板（冻结，原样保留）
# ---------------------------------------------------------------------------

def _render_batch_result(label: str, result) -> None:
    """只读展示批量确认结果（committed / errors / 接受条数）。"""
    if result.committed:
        st.success(f"✅ {label}：已提交 {len(result.accepted)} 条决议")
    else:
        st.error(f"❌ {label}：提交失败（committed=false），未写入任何决议")
        for e in result.errors:
            st.write(f"   - [{e.field}] {e.message}")


def _render_financial_v2_confirmation(company_id: str) -> None:
    """A5 集中确认面板（薄 UI）。

    只做四件事：调用 list_pending()、展示来源/坐标/差异/影响/过期状态、收集批量
    选择 + reason code + note、调用两个批量提交接口并展示 committed/errors 与刷新后的
    真实状态。科目映射、容差、冲突选择默认值、事务、失效、权限规则一律不在此实现。
    无 pending 时不渲染确认区。
    """
    from financial_v2 import resolutions as res
    from financial_v2 import schema as V2S
    from financial_v2 import store as v2store

    v2store.init_db(v2store.DEFAULT_DB_PATH)
    pending = res.list_pending(company_id)

    if not pending.items:
        return

    st.subheader("🧪 V2 财务对账确认")
    st.caption(
        f"待确认 {len(pending.items)} 项：科目映射 {pending.mapping_confirmation_count}，"
        f"冲突选源 {pending.value_source_resolution_count}，"
        f"补充材料 {pending.insufficient_scope_count}"
    )

    reason_codes = list(V2S.RESOLUTION_REASON_CODES)
    mapping_choices: dict[str, tuple[str, str | None, str, str]] = {}
    value_choices: dict[str, tuple[str, list[str], list[str], str, str]] = {}

    for it in pending.items:
        if it.issue_type == res.ISSUE_TYPE_INSUFFICIENT_SCOPE:
            st.warning(
                f"⚠️ `{it.payload.get('raw_item_text') or it.payload.get('standard_item_code')}`："
                f"scope / 币种 / 期间 / 主体不明，需补充或更正材料，不接受替代值输入。"
            )
            continue

        if it.issue_type == res.ISSUE_TYPE_MAPPING:
            stale_tag = " ⏳（已过期）" if it.stale else ""
            st.markdown(
                f"**映射待确认**：`{it.payload.get('raw_item_text')}`"
                f"（{it.payload.get('statement_type')}{stale_tag}）"
            )
            conflicting = it.payload.get("conflicting_rules") or []
            codes = [c.get("standard_item_code") for c in conflicting
                     if c.get("standard_item_code")]
            opts = ["__none__"] + codes + ["__unconfirmable__"]
            fmt = {"__none__": "（未选择）", "__unconfirmable__": "无法确认 / 需补充材料"}
            chosen = st.radio(
                "选择标准科目（系统展示候选）", options=opts, index=0,
                format_func=lambda x: fmt.get(x, x),
                key=f"v2_m_{it.issue_id}",
            )
            reason = st.selectbox(
                "reason code", options=reason_codes, key=f"v2_mr_{it.issue_id}")
            note = st.text_input(
                "note（OTHER_WITH_NOTE 必填）", key=f"v2_mn_{it.issue_id}")
            if chosen != "__none__":
                mapping_choices[it.issue_id] = (
                    it.payload["candidate_id"],
                    None if chosen == "__unconfirmable__" else chosen,
                    reason, note.strip() or None)
            st.divider()
        else:  # VALUE_SOURCE_RESOLUTION
            st.markdown(
                f"**冲突选源**：`{it.payload.get('standard_item_code')}`"
                f"（{it.payload.get('statement_type')}），"
                f"标准值 {it.payload.get('std_values')}"
            )
            sources = it.payload.get("sources") or []
            src_labels = [
                f"{s['record_id']} | std {s['std_value']} {s['std_unit']}"
                f" | {s['report_period']} {s['scope']} {s['currency']}"
                for s in sources
            ]
            src_opts = ["__none__"] + list(range(len(sources)))
            chosen = st.radio(
                "选择接受来源（不预选默认值）", options=src_opts, index=0,
                format_func=lambda i: "（未选择）" if i == "__none__" else src_labels[i],
                key=f"v2_v_{it.issue_id}",
            )
            reason = st.selectbox(
                "reason code", options=reason_codes, key=f"v2_vr_{it.issue_id}")
            note = st.text_input(
                "note（OTHER_WITH_NOTE 必填）", key=f"v2_vn_{it.issue_id}")
            if chosen != "__none__":
                acc = [sources[chosen]["record_id"]]
                rej = [s["record_id"] for i, s in enumerate(sources) if i != chosen]
                value_choices[it.issue_id] = (
                    it.payload["group_id"], acc, rej, reason, note.strip() or None)
            st.divider()

    if not mapping_choices and not value_choices:
        return

    if st.button("提交 V2 确认", type="primary"):
        if mapping_choices:
            mreq = res.MappingResolutionBatchRequest(
                company_id=company_id, operator="demo",
                items=[res.MappingResolutionItem(
                    candidate_id=cid, chosen_item_code=chosen,
                    reason_code=reason, note=note)
                    for (cid, chosen, reason, note) in mapping_choices.values()])
            _render_batch_result("科目映射确认", res.submit_mapping_resolutions(mreq))
        if value_choices:
            vreq = res.ValueResolutionBatchRequest(
                company_id=company_id, operator="demo",
                items=[res.ValueResolutionItem(
                    group_id=gid, accepted_record_ids=acc,
                    rejected_record_ids=rej, reason_code=reason, note=note)
                    for (gid, acc, rej, reason, note) in value_choices.values()])
            _render_batch_result("冲突来源确认", res.submit_value_resolutions(vreq))
        st.rerun()


def _render_financial_v2_snapshot(company_id: str) -> None:
    """A7 V2 快照/指标展示（薄 UI，默认关闭）。

    只调用 progress 公开接口（build_request_for_company / run_pipeline）并读取其返回的
    progress 摘要与 adapters 载荷，展示快照状态、缺口（异常）和指标；不复制公式、
    准入或计算业务逻辑。
    """
    from financial_v2 import progress as v2progress

    st.subheader("🧪 V2 财务快照与指标（实验性）")
    if not company_id:
        st.warning("请先输入公司股票代码。")
        return

    if st.button("🚀 运行 V2 财务快照流水线", type="primary"):
        try:
            request = v2progress.build_request_for_company(company_id)
            st.session_state["v2_pipeline_result"] = v2progress.run_pipeline(
                request, persist=True)
        except Exception as e:
            st.error(f"V2 流水线运行失败：{e}")
            logger.exception("V2 pipeline UI failed")
            return

    result = st.session_state.get("v2_pipeline_result")
    if result is None:
        st.caption("点击上方按钮，基于当前 Record Set / Reconciliation 构建不可变快照并计算指标。")
        return

    # ── 进度（真实持久化事件） ──
    st.markdown(f"**终态**：`{result.final_state}`")
    for s in result.progress.stages:
        icon = "✅" if s.status == "completed" else ("❌" if s.status == "failed" else "🔄")
        extra = f"（{s.completed_units}/{s.total_units}）" if s.total_units else ""
        err = f"：{s.error_code}" if s.error_code else ""
        st.write(f"   {icon} {s.label}{extra}{err}")

    payload = result.payload
    if payload is None:
        st.warning(f"运行失败：{result.error}")
        return

    # ── 快照状态 ──
    st.markdown(
        f"**快照**：`{payload.snapshot_id}`（company={payload.company_id}，"
        f"validity={payload.validity}，report_blocked={payload.report_blocked}）"
    )

    # ── 缺口（异常） ──
    if payload.exceptions:
        st.markdown(f"**缺口 {len(payload.exceptions)} 项**：")
        for e in payload.exceptions:
            st.write(f"   ⚠️ `{e.standard_item_code or e.comparison_key}` — {e.exception_type}"
                     f"（{e.blocking_reason}）")
    else:
        st.write("   ✅ 无快照异常")

    # ── 指标 ──
    if payload.metrics:
        st.markdown(f"**指标 {len(payload.metrics)} 项**（期间 {', '.join(payload.periods)}）：")
        for m in payload.metrics:
            if m.status == "CALCULATED_EXACT":
                val = f"{m.display_value}{'%' if m.unit == 'percent' else ''}"
                st.write(f"   ✅ `{m.formula_id}` {m.period} = {val}"
                         + (f"（{m.note}）" if m.note else ""))
            elif m.status == "CALCULATED_PROXY":
                st.write(f"   🟠 `{m.formula_id}` {m.period} = {m.display_value}"
                         f"（代理：{m.note}）")
            else:
                st.write(f"   ⚪ `{m.formula_id}` {m.period} — {m.status}"
                         + (f"（{m.note}）" if m.note else ""))
    else:
        st.write("   （无已计算指标）")


def _generate_report(company_id: str, company_name: str, credit_type: str,
                     report_as_of: str, enabled: tuple[str, ...],
                     external_research: bool):
    """Phase 4 报告生成（薄）：只调 sections.service，返回 Phase4RunResult。

    规划、Worker、评估、返工、RunManifest 全部由 sections.service 编排，本函数不写
    任何业务逻辑。返回对象交给 _render_result 展示 DTO。
    """
    from planning import report_planner as planner
    from sections import service as service

    job_id = _job_id()
    job = planner.build_job_input(
        job_id, company_id, company_name, credit_type, report_as_of,
        "standard_v2", enabled,
        scope="consolidated", currency="CNY", purpose="credit_analysis",
        fin_db=FIN_DB, ev_db=EV_DB,
    )
    cfg = service.ServiceConfig(external_research_enabled=external_research)
    return service.run_phase4(job, service_cfg=cfg)


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📊 授信报告生成器 — Phase 4 章节预览")
    st.caption("薄展示：只调用 `sections.service`，不承担任何业务判断。")

    # ── 输入（Sidebar） ──
    with st.sidebar:
        st.header("⚙️ 输入")
        if DEMO_MODE:
            st.info("🎯 DEMO 模式已启用")

        company_id = st.text_input(
            "公司股票代码",
            value="300750" if DEMO_MODE else "",
            placeholder="如 300750、600519",
            help="A 股六位股票代码",
        ).strip()
        company_name = st.text_input(
            "公司名称",
            value="宁德时代" if DEMO_MODE else "",
            placeholder="如 宁德时代",
        ).strip()

        from contracts import schema as CS

        credit_type = st.selectbox(
            "授信类型",
            options=CS.CREDIT_TYPES,
            index=CS.CREDIT_TYPES.index("other"),
            help="影响契约中 applies_when 条件对主题/问题的适用性",
        )

        report_as_of = st.text_input(
            "报告基准日",
            value="2026-03-31",
            placeholder="如 2026-03-31",
            help="YYYY-MM-DD，用于财务快照锁定与时效判定",
        ).strip()

        enabled = st.multiselect(
            "启用章节",
            options=list(SECTION_OPTIONS),
            default=list(SECTION_OPTIONS),
            format_func=lambda s: SECTION_LABELS[s],
        )

        external_research = st.checkbox(
            "🔎 启用外部检索（互联网）",
            value=True,
            help="公司信用 / 行业研究 Worker 是否联网检索；需 Bocha Key",
        )

        st.divider()

        # ── V2 财务对账确认开关（实验性，默认关闭）──
        enable_v2 = st.checkbox(
            "🧪 启用 V2 财务对账确认（实验性）",
            value=False,
            help=(
                "默认关闭。开启后展示 V2 财务对账的待确认面板（科目映射 / 冲突来源选择），"
                "数据来自 CLI 运行的 financial_v2 对账结果。无待确认项时不显示面板。"
            ),
        )

        # ── V2 财务快照与指标开关（实验性，默认关闭）──
        enable_v2_snapshot = st.checkbox(
            "🧪 启用 V2 财务快照与指标（实验性）",
            value=False,
            help=(
                "默认关闭。开启后基于当前 V2 Record Set / Reconciliation 构建不可变快照、"
                "计算版本化指标，并只读展示快照状态、缺口与指标。"
            ),
        )

        st.divider()
        st.caption(
            "服务读取既有 financial_v2 快照 + evidence 证据。"
            "数据缺失时只报错，不在此导入文件。"
        )

    # ── Phase 4 生成 ──
    if st.button("🚀 生成 Phase 4 报告", type="primary", use_container_width=True):
        if not company_id:
            st.error("请输入公司股票代码。")
        elif not enabled:
            st.error("请至少启用一个章节。")
        elif not report_as_of:
            st.error("请输入报告基准日。")
        else:
            try:
                with st.spinner("正在规划 + 生成三章节（评估与定向返工）..."):
                    result = _generate_report(
                        company_id, company_name or company_id, credit_type,
                        report_as_of, tuple(enabled), external_research)
            except Exception as e:  # noqa: BLE001 - 顶层统一友好报错
                logger.exception("Phase 4 生成失败")
                st.error(_friendly_error(e))
                st.session_state.pop("phase4_result", None)
            else:
                st.session_state["phase4_result"] = result

    result = st.session_state.get("phase4_result")
    if result is not None:
        _render_result(result)

    # ── V2 财务对账确认（实验性，独立于 Phase 4 主流程，默认不渲染）──
    if enable_v2:
        _render_financial_v2_confirmation(company_id)

    # ── V2 财务快照与指标（实验性，独立于 Phase 4 主流程，默认不渲染）──
    if enable_v2_snapshot:
        _render_financial_v2_snapshot(company_id)


if __name__ == "__main__":
    main()
