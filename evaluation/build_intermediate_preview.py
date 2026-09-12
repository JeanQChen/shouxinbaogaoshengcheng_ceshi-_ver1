"""B1/B2 生产接线中间预览（case-specific 验收 runner，只读、确定性、无 LLM）。

本模块是 **300750（宁德时代）的 case-specific 验收 runner**，只做一次小范围中间预览：
主营业务构成 / 分业务收入 / 分业务成本 / 收入占比 / 财务近三年主线。产物标注
``INTERMEDIATE_PREVIEW``，非正式报告，未通过任何章节质量门。生成后即停止，不进入完整
B3、不重跑 41 问、不进入 Phase 5。

派生链（全部确定性，金额由 Python 归一化）：
    inspected Evidence（evidence.db，表5-10/5-11）→ table_context.extract_note_tables
    → note_detail.parse_note_table → evidence_facts.build_evidence_facts
    → harness.revenue_cost_precheck / deterministic_prechecks（A5 收入/成本校验）
    → sections.financial_worker._select_focus_periods（B2b 财务近三年主线）。

只读约束（复用 ``sections.citation_authority`` 的 SQLite ``mode=ro`` 只读连接，缺库 / 缺表 /
无有效 current 快照 fail-closed，**绝不调用 init_db，不创建、不迁移、不改任何库**）：
- ``document_id`` 由 evidence 内容发现（含「主营业务收入构成」表题的文档）；
- ``subject`` 由财务源文档 ``financial_source_document.declared_company_name`` 派生；
- ``active_snapshot_id`` 由 ``current_snapshot`` 指针派生并做权威性校验。

case-specific fail-closed：``--company`` 仅接受 300750，其它主体直接拒绝，不会复用
300750 数据产出错主体报告。

CLI: python -m evaluation.build_intermediate_preview --company 300750
     [--ev-db data/evidence.db] [--fin-db data/financial_v2.db]
     [--out-root evaluation/results] [--run-id <id>]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import store as estore
from financial_v2 import evidence_facts as EF
from financial_v2 import note_detail as ND
from financial_v2 import table_context as TC
from financial_v2.number_identity import item_code_from_title
from harness import entailment as E
from harness import schema as H
from routing import schema as RS
from sections import citation_authority as CA
from sections import financial_worker as FW

log = logging.getLogger("build_intermediate_preview")

PREVIEW_MARKER = "INTERMEDIATE_PREVIEW"
PARSER_VERSION = EF.PARSER_VERSION

# ---- case-specific 验收 fixture（本 runner 只支持这一组主体）----
FIXTURE_COMPANY_ID = "300750"
FIXTURE_SUBJECT = "宁德时代"
TARGET_TABLE_MARKER = "主营业务收入构成"   # 表5-10 表题标记（发现 document_id + 起点）
COST_ROW_MARKER = "其他业务成本"          # 表5-11 末行标签（表块终点）
SCOPE = "consolidated"
CURRENCY = "CNY"
PURPOSES = ("credit_analysis", "report")  # 报告用途 current 指针偏好顺序


class AcceptanceRunnerError(RuntimeError):
    """case-specific 验收 runner 越界 / 缺库 / 缺表 / 无有效 current 快照（fail-closed）。"""


# ---------------------------------------------------------------------------
# 只读访问层（复用 citation_authority 的 mode=ro 连接；不 init_db、不创建、不迁移）
# ---------------------------------------------------------------------------

def _ro_subject(fin_db: str, company_id: str) -> str | None:
    """只读：从财务源文档派生主体名（declared_company_name，须唯一一致）。"""
    conn = CA._ro_conn(fin_db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id=? AND declared_company_name IS NOT NULL",
            (company_id,)).fetchall()
    finally:
        conn.close()
    names = [r["declared_company_name"] for r in rows]
    if not names:
        return None
    return names[0] if len(set(names)) == 1 else None  # 不一致 → 歧义（fail-closed）


def _ro_discover_document_id(ev_db: str, company_id: str) -> str | None:
    """只读：发现含目标表题的文档（表5-10/5-11 所在募集说明书）；多文档歧义 → None。"""
    conn = CA._ro_conn(ev_db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT document_id FROM evidence_blocks "
            "WHERE company_id=? AND text LIKE ?",
            (company_id, f"%{TARGET_TABLE_MARKER}%")).fetchall()
    finally:
        conn.close()
    ids = [r["document_id"] for r in rows]
    return ids[0] if len(ids) == 1 else None


def _ro_list_document_evidence(ev_db: str, company_id: str, document_id: str) -> list:
    """只读：列出某文档 Evidence 块（按物理页 + 块序）。"""
    conn = CA._ro_conn(ev_db)
    try:
        rows = conn.execute(
            "SELECT * FROM evidence_blocks WHERE company_id=? AND document_id=? "
            "ORDER BY page_number, block_index",
            (company_id, document_id)).fetchall()
        return [estore._row_to_evidence(r) for r in rows]
    finally:
        conn.close()


def _ro_current_snapshot_id(fin_db: str, company_id: str) -> str:
    """只读：current_snapshot 指针 → 最新 as_of + 报告用途偏好的（consolidated, CNY）快照。"""
    conn = CA._ro_conn(fin_db)
    try:
        rows = conn.execute(
            "SELECT snapshot_id, as_of_date, purpose FROM current_snapshot "
            "WHERE company_id=? AND scope=? AND currency=?",
            (company_id, SCOPE, CURRENCY)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise AcceptanceRunnerError(
            f"{company_id} 无 current 快照指针（{SCOPE}/{CURRENCY}，fail-closed）")
    order = {p: i for i, p in enumerate(PURPOSES)}
    rows.sort(key=lambda r: (r["as_of_date"], order.get(r["purpose"], len(PURPOSES))))
    return rows[-1]["snapshot_id"]


def _assert_valid_snapshot(fin_db: str, company_id: str, snapshot_id: str):
    """只读：快照权威性 fail-closed（存在 + 主体匹配 + valid + 未阻断 + 未隔离）。"""
    snap = CA._ro_snapshot_get(fin_db, snapshot_id)
    if snap is None:
        raise AcceptanceRunnerError(f"快照不存在: {snapshot_id}")
    if snap.company_id != company_id:
        raise AcceptanceRunnerError(
            f"快照主体不符: {snap.company_id} != {company_id}（拒绝错主体报告）")
    validity = CA._ro_latest_snapshot_validity(fin_db, snapshot_id)
    if validity != "valid":
        raise AcceptanceRunnerError(f"快照有效性非 valid（{validity!r}，fail-closed）")
    if snap.report_blocked:
        raise AcceptanceRunnerError(f"快照 report_blocked，禁止生成（fail-closed）")
    if CA._ro_is_quarantined(fin_db, "financial_snapshot", snapshot_id):
        raise AcceptanceRunnerError(f"快照已隔离，禁止使用（fail-closed）")
    return snap


# ---------------------------------------------------------------------------
# 表块选取（表5-10 / 表5-11）
# ---------------------------------------------------------------------------

def _note_table_blocks(blocks: list) -> list:
    """从已排序的 evidence 块中截取表5-10/5-11 两个注记表块。

    表5-10（整表单块）+ 表5-11（表题/单位在前块、表头/数据在后块的跨块续表）：从含
    「主营业务收入构成」的表题块起，到其后首个含「其他业务成本」的数据块止（表5-11
    末行标签，紧邻起点之后；正文中再出现该词不干扰截取）。
    """
    start = next((i for i, b in enumerate(blocks)
                  if TARGET_TABLE_MARKER in (b.text or "")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(blocks))
                if COST_ROW_MARKER in (blocks[i].text or "")), None)
    if end is None:
        return []
    return blocks[start:end + 1]


def _to_inspected(b) -> H.InspectedMaterial:
    """estore EvidenceBlock → harness InspectedMaterial（section_path 列表 → str）。"""
    return H.InspectedMaterial(
        evidence_id=b.evidence_id,
        document_id=b.document_id or "",
        source_name=b.source_name or "",
        source_type=b.source_type or "",
        page_number=b.page_number,
        section_path=".".join(b.section_path or []),
        evidence_type=b.evidence_type or "",
        report_period=getattr(b, "report_period", None),
        text=b.text or "",
        is_snippet=False,
    )


# ---------------------------------------------------------------------------
# 格式化辅助
# ---------------------------------------------------------------------------

def _fmt_yuan(v: Decimal | None) -> str:
    if v is None:
        return "—"
    i = v.to_integral_value()
    return format(i, ",.0f") if i == v else format(v, ",.2f")


def _fmt_pct(v: Decimal | None) -> str:
    return "—" if v is None else f"{float(v):.1f}%"


# ---------------------------------------------------------------------------
# 派生（facts / 占比 / A5 / B2b）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactRow:
    segment: str
    values: tuple[Decimal | None, ...]  # 按 periods 顺序


def _fact_table(facts: list[EF.EvidenceStructuredFact],
                periods: list[str]) -> list[FactRow]:
    """金额列 fact → 按业务板块 × 期间 的矩阵（保持源表行序）。"""
    seg_order: list[str] = []
    by_seg: dict[str, dict[str, Decimal | None]] = defaultdict(dict)
    for f in facts:
        seg = f.business_segment or ""
        if seg not in by_seg:
            seg_order.append(seg)
        by_seg[seg][f.period] = f.value
    return [FactRow(seg, tuple(by_seg[seg].get(p) for p in periods))
            for seg in seg_order]


def _percent_rows(materials: list[H.InspectedMaterial]) -> list[dict]:
    """占比列 → note_detail 解析（本轮仅解析，未提升为 EvidenceStructuredFact）。"""
    ordered = sorted(materials, key=lambda m: (m.page_number or 0, 0))
    tables = TC.extract_note_tables([(m.page_number or 0, m.text or "") for m in ordered])
    rows: list[dict] = []
    for t, _bidx in tables:
        item_code = item_code_from_title(t.table_title)
        res = ND.parse_note_table(t, subject=FIXTURE_SUBJECT, item_code=item_code,
                                  period_type="annual")
        for it in res.items:
            if it.amount_column != "占比":
                continue
            label = (it.row_label or "").strip()
            if not it.period or label in ("", "合计", "总计", "小计"):
                continue
            rows.append({
                "table": t.table_title,
                "segment": label,
                "period": it.period,
                "percent": it.value,
            })
    return rows


def _a5_rows(materials: list[H.InspectedMaterial],
             facts: list[EF.EvidenceStructuredFact]) -> list[dict]:
    """A5 收入/成本校验演示（确定性，无 LLM）：三例验收 + 一例 specific 绑定。"""
    state = H.ResearchState(
        run_id="preview", case_id="300750", question_id="q", company_id="300750",
        section_id="fin", original_question="主营业务构成？",
        need=RS.InformationNeed(
            need_id="q", section_id="fin", question="主营业务构成？",
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="P0", depends_on=[]))
    for m in materials:
        state.inspected_evidence[m.evidence_id] = m
    state.evidence_structured_facts = facts

    rev_eid = next((m.evidence_id for m in materials
                    if TARGET_TABLE_MARKER in (m.text or "")), "")
    cost_eid = next((m.evidence_id for m in materials
                     if COST_ROW_MARKER in (m.text or "")), "")
    dyn_cost_2025 = next(
        (f for f in facts
         if f.revenue_cost_category == "cost" and f.business_segment == "动力电池系统"
         and f.period == "2025-12-31"), None)

    def _case(text: str, eid: str, fid: str | None = None) -> dict:
        ans = H.ResearchAnswer(
            question_id="q", answer_text=text,
            claims=[H.Claim(claim_id="c1", text=text, kind="fact", citation_refs=[0])],
            citations=[H.CitationRef(ref_type="evidence", evidence_id=eid,
                                     evidence_fact_id=fid)])
        pc = E.deterministic_prechecks(state, ans)
        c = pc.get("c1", {})
        rc = c.get("revenue_cost", {})
        return {
            "claim": text,
            "evidence_id": eid,
            "evidence_fact_id": fid,
            "hint": rc.get("hint"),
            "categories": rc.get("categories", []),
            "mismatch": bool(c.get("revenue_cost_mismatch")),
        }

    rows = [
        _case("2025年动力电池系统营业收入为316,506,369,000元", rev_eid),
        _case("2025年动力电池系统营业成本为241,064,397,000元", cost_eid),
        _case("2025年动力电池系统营业收入为241,064,397,000元", cost_eid),
    ]
    if dyn_cost_2025 is not None:
        rows.append(_case("2025年动力电池系统营业收入为241,064,397,000元",
                          cost_eid, dyn_cost_2025.evidence_fact_id))
    return rows


def _b2b_summary(fin_db: str, snap) -> dict:
    """B2b 财务近三年主线（只读快照 items + financial_worker._select_focus_periods）。"""
    items = CA._ro_snapshot_items(fin_db, snap.snapshot_id)
    scoped = [it for it in items
              if it.statement_scope == snap.scope and it.currency == snap.currency]
    periods, note = FW._select_focus_periods(scoped, as_of_date=snap.as_of_date)
    return {
        "snapshot_id": snap.snapshot_id,
        "as_of_date": snap.as_of_date,
        "scope": snap.scope,
        "currency": snap.currency,
        "focus_periods": periods,
        "note": note,
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _render_report(*, run_id: str, subject: str, document_id: str,
                   materials: list[H.InspectedMaterial],
                   rev_facts: list[EF.EvidenceStructuredFact],
                   cst_facts: list[EF.EvidenceStructuredFact],
                   percent_rows: list[dict], a5_rows: list[dict],
                   b2b: dict) -> str:
    periods = ["2023-12-31", "2024-12-31", "2025-12-31"]
    rev_table = _fact_table(rev_facts, periods)
    cst_table = _fact_table(cst_facts, periods)
    pct_by_seg: dict[str, dict[str, Decimal | None]] = defaultdict(dict)
    pct_order: list[str] = []
    for r in percent_rows:
        if "成本" in r["table"]:
            continue  # 本轮只展示收入占比（表5-10）
        if r["segment"] not in pct_by_seg:
            pct_order.append(r["segment"])
        pct_by_seg[r["segment"]][r["period"]] = r["percent"]

    lines: list[str] = []
    A = lines.append
    A(f"# 中间预览 · B1/B2 生产接线（{PREVIEW_MARKER}）\n")
    A("> **非正式产物，未通过任何章节质量门。** 本轮只演示「新材料层第一个通用切片」的")
    A("> 真实数据链：inspected Evidence → 表格上下文 → EvidenceStructuredFact → A5 收入/成本校验，")
    A("> 以及 B2b 财务近三年主线。金额由 Python 归一化，无 LLM 参与，不重新检索、不重算指标、")
    A("> 不扩展 financial_worker 读取 Evidence、不伪造 snapshot_id。\n")
    A(f"- 公司：{subject}（{FIXTURE_COMPANY_ID}）")
    A(f"- 输入：{document_id} · 主营业务情况 P50 表5-10 / 表5-11")
    A(f"- run_id：`{run_id}`")
    A(f"- parser_version：`{PARSER_VERSION}`")
    A(f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")

    A("## 0. 输入证据（表题 / 物理页 / 区块）\n")
    for m in materials:
        first = (m.text or "").strip().splitlines()[0][:30] if (m.text or "").strip() else ""
        A(f"- `{m.evidence_id}` — P{m.page_number} · {m.section_path or '—'} · 首行：{first}")
    A("")

    A("## 1. 分业务收入（表5-10 → EvidenceStructuredFact，金额列，单位：元）\n")
    A("| 业务板块 | 2025年 | 2024年 | 2023年 |")
    A("|---|---:|---:|---:|")
    for r in rev_table:
        A(f"| {r.segment} | {_fmt_yuan(r.values[2])} | {_fmt_yuan(r.values[1])} | {_fmt_yuan(r.values[0])} |")
    A("")

    A("## 2. 分业务成本（表5-11 → EvidenceStructuredFact，金额列，单位：元）\n")
    A("| 业务板块 | 2025年 | 2024年 | 2023年 |")
    A("|---|---:|---:|---:|")
    for r in cst_table:
        A(f"| {r.segment} | {_fmt_yuan(r.values[2])} | {_fmt_yuan(r.values[1])} | {_fmt_yuan(r.values[0])} |")
    A("")

    A("## 3. 收入占比（表5-10 → note_detail 占比列，本轮仅解析未提升为 fact）\n")
    A("| 业务板块 | 2025年 | 2024年 | 2023年 |")
    A("|---|---:|---:|---:|")
    for seg in pct_order:
        v = pct_by_seg[seg]
        A(f"| {seg} | {_fmt_pct(v.get(periods[2]))} | {_fmt_pct(v.get(periods[1]))} | {_fmt_pct(v.get(periods[0]))} |")
    A("")

    A("## 4. A5 收入/成本校验（确定性，evidence_fact_id 绑定 + evidence_id 兜底）\n")
    A("| claim 语义 | 引用证据 | hint | 事实类别 | mismatch | 判定 |")
    A("|---|---|---|---|---:|---|")
    for r in a5_rows:
        verdict = "revenue_cost_mismatch → UNSUPPORTED" if r["mismatch"] else "不 mismatch（SUPPORTED 侧）"
        cats = "+".join(r["categories"]) if r["categories"] else "—"
        eid = r["evidence_fact_id"] or r["evidence_id"]
        A(f"| {r['claim']} | `{eid[:16]}…` | {r['hint'] or '—'} | {cats} | "
          f"{r['mismatch']} | {verdict} |")
    A("")

    note = b2b["note"]
    A("## 5. 财务近三年主线（B2b `_select_focus_periods`，财务快照权威链）\n")
    A(f"- 快照：`{b2b['snapshot_id']}` · as_of `{b2b['as_of_date']}` · "
      f"{b2b['scope']} / {b2b['currency']}")
    A(f"- 年度主线：{', '.join(p[:4] for p in note['annual_periods_available'])}"
      f"（full_three_years={note['full_three_years']}，共 {note['annual_years']} 个年度）")
    A(f"- 季度/中报补充：{note['sub_annual_supplement'] or '—'}（独立列，不与年度同比）")
    A(f"- 焦点期间（periods）：{', '.join(b2b['focus_periods'])}\n")

    A("## 派生统计\n")
    A(f"- EvidenceStructuredFact：收入 {len(rev_facts)} 条 / 成本 {len(cst_facts)} 条")
    A(f"- 占比行（表5-10，未提升为 fact）：{len([r for r in percent_rows if '成本' not in r['table']])} 条")
    A(f"- evidence_fact_id：content-addressed（`ef-` 前缀），绑定真实 evidence_id，无 SnapshotAuthority\n")

    A("---")
    A(f"*{PREVIEW_MARKER} · 停止边界：本预览生成后不进入完整 B3 / 41 问重跑 / Phase 5。*\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def run(company_id: str, *, out_root: str, run_id: str | None = None,
        ev_db: str | None = None, fin_db: str | None = None) -> dict:
    """生成一次中间预览（只读；case-specific，非 300750 直接 fail-closed）。"""
    run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "_" + uuid.uuid4().hex[:8])
    ev_db = ev_db or _ev_db_path()
    fin_db = fin_db or _fin_db_path()

    # case-specific fail-closed：本 runner 只接受 300750（宁德时代）。
    if company_id != FIXTURE_COMPANY_ID:
        raise AcceptanceRunnerError(
            f"本模块是 {FIXTURE_COMPANY_ID}（{FIXTURE_SUBJECT}）的 case-specific 验收 runner，"
            f"不支持其它 company（收到 {company_id}）；拒绝产出错主体报告")

    # 由权威数据派生 document_id / subject / active_snapshot_id（均只读）。
    document_id = _ro_discover_document_id(ev_db, company_id)
    if not document_id:
        raise AcceptanceRunnerError(
            f"{company_id} 未发现含「{TARGET_TABLE_MARKER}」表题的文档（fail-closed）")
    subject = _ro_subject(fin_db, company_id) or FIXTURE_SUBJECT
    if subject != FIXTURE_SUBJECT:
        raise AcceptanceRunnerError(
            f"主体名不符: {subject!r} != {FIXTURE_SUBJECT!r}（拒绝错主体报告）")
    snapshot_id = _ro_current_snapshot_id(fin_db, company_id)
    snap = _assert_valid_snapshot(fin_db, company_id, snapshot_id)

    # 证据块（只读）+ 表5-10/5-11 截取。
    blocks = _note_table_blocks(_ro_list_document_evidence(ev_db, company_id, document_id))
    if not blocks:
        raise AcceptanceRunnerError(f"{company_id}/{document_id} 未发现表5-10/5-11 表块")
    materials = [_to_inspected(b) for b in blocks]

    facts = EF.build_evidence_facts(materials, subject=subject)
    rev_facts = [f for f in facts if f.revenue_cost_category == "revenue"]
    cst_facts = [f for f in facts if f.revenue_cost_category == "cost"]
    percent_rows = _percent_rows(materials)
    a5_rows = _a5_rows(materials, facts)
    b2b = _b2b_summary(fin_db, snap)

    report_md = _render_report(
        run_id=run_id, subject=subject, document_id=document_id, materials=materials,
        rev_facts=rev_facts, cst_facts=cst_facts, percent_rows=percent_rows,
        a5_rows=a5_rows, b2b=b2b)

    out_dir = Path(out_root) / f"intermediate_preview_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "report.md").write_text(report_md, encoding="utf-8")
    (out_dir / "facts.json").write_text(
        json.dumps([f.to_dict() for f in facts], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "manifest.json").write_text(
        json.dumps({
            "preview_marker": PREVIEW_MARKER,
            "run_id": run_id,
            "company_id": company_id,
            "subject": subject,
            "document_id": document_id,
            "snapshot_id": snapshot_id,
            "evidence_ids": [m.evidence_id for m in materials],
            "fact_count": len(facts),
            "revenue_fact_count": len(rev_facts),
            "cost_fact_count": len(cst_facts),
            "percent_row_count": len([r for r in percent_rows if "成本" not in r["table"]]),
            "b2b": b2b,
            "parser_version": PARSER_VERSION,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8")

    log.info("preview written to %s", out_dir)
    return {
        "run_id": run_id,
        "output_dir": str(out_dir),
        "report_path": str(out_dir / "report.md"),
        "company_id": company_id,
        "subject": subject,
        "document_id": document_id,
        "snapshot_id": snapshot_id,
        "snapshot_company_id": snap.company_id,
        "fact_count": len(facts),
        "revenue_fact_count": len(rev_facts),
        "cost_fact_count": len(cst_facts),
        "a5_results": [{"hint": r["hint"], "categories": r["categories"],
                        "mismatch": r["mismatch"]} for r in a5_rows],
    }


# ---------------------------------------------------------------------------
# 路径 / CLI
# ---------------------------------------------------------------------------

def _ev_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "evidence.db")


def _fin_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "financial_v2.db")


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.build_intermediate_preview",
        description="B1/B2 生产接线中间预览（case-specific、只读、无 LLM）")
    parser.add_argument("--company", default=FIXTURE_COMPANY_ID)
    parser.add_argument("--ev-db", default=None)
    parser.add_argument("--fin-db", default=None)
    parser.add_argument("--out-root", default=str(
        Path(__file__).resolve().parent / "results"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = run(args.company, out_root=args.out_root, run_id=args.run_id,
                 ev_db=args.ev_db, fin_db=args.fin_db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
