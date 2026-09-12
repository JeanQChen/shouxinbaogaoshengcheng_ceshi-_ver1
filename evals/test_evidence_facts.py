"""Eval: Evidence 背书的结构化事实 + A5 收入/成本校验（B1/B2 生产接线验收）。

用法: python -m evals.test_evidence_facts

验收链（任务书 §13.4 / §16 B / §17.1 + 本轮「新材料层第一个通用切片」）：
    inspected Evidence → table_context.extract_note_tables → note_detail.parse_note_table
    → EvidenceStructuredFact → CitationRef(ref_type=evidence, evidence_id, evidence_fact_id)
    → revenue_cost_precheck（A5）→ entailment_summary（UNSUPPORTED/revenue_cost_mismatch）

定点验收：
- 表5-10 收入写作收入 → 不 mismatch；
- 表5-11 成本写作成本 → 不 mismatch；
- 表5-11 成本写作收入 → revenue_cost_mismatch → UNSUPPORTED；
- 同金额不同类别不得相互替代（成本金额写「收入」仍阻断）；
- 缺表题/缺表头/单位不明/期间不明 → fail-closed 不产 fact；
- 不得用手工构造最终 CitationRef 代替端到端（本测试经 build_evidence_facts 派生）。

全部纯函数（无 I/O / LLM / DB），合成 fixture（宁德时代等名只作 case fixture）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import evidence_facts as EF
from harness import entailment as E
from harness import schema as H
from harness import structured_provenance as SP
from routing import schema as RS


def _block(eid, text, *, page=50, doc="d1", sec="财务"):
    return H.InspectedMaterial(
        evidence_id=eid, document_id=doc, source_name="NDSD_KCZ_2026.pdf",
        source_type="pdf", page_number=page, section_path=sec, text=text)


# 真实 NDSD_KCZ_2026 表5-10/表5-11 结构（多级交错表头，表5-11 跨块）。
T5_10 = ("表 5-10发行人主营业务收入构成表\n\n单位：万元，%\n"
         "  项目  2025年  2024年  2023年\n  金额  占比  金额  占比  金额  占比\n\n"
         "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n")
T5_11_A = "表 5-11发行人主营业务成本构成表\n\n单位：万元，%\n"
T5_11_B = ("项目  2025年  2024年度  2023年度\n  金额  占比  金额  占比  金额  占比\n"
           "动力电池系统  24,106,439.7  77.2  19,246,128.2  70.4  22,171,419.3  71.7\n")


def _state_with(blocks: list[H.InspectedMaterial]) -> H.ResearchState:
    need = RS.InformationNeed(
        need_id="q", section_id="fin", question="主营业务构成？",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])
    st = H.ResearchState(
        run_id="r", case_id="c", question_id="q", company_id="300750",
        section_id="fin", original_question="主营业务构成？", need=need)
    for b in blocks:
        st.inspected_evidence[b.evidence_id] = b
    st.evidence_structured_facts = EF.build_evidence_facts(
        list(st.inspected_evidence.values()), subject="宁德时代")
    return st


def _answer(text: str, evidence_id: str, evidence_fact_id: str | None = None) -> H.ResearchAnswer:
    return H.ResearchAnswer(
        question_id="q", answer_text=text,
        claims=[H.Claim(claim_id="c1", text=text, kind="fact", citation_refs=[0])],
        citations=[H.CitationRef(ref_type="evidence", evidence_id=evidence_id,
                                 evidence_fact_id=evidence_fact_id)])


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- 1) Evidence → EvidenceStructuredFact（类别/值/期间/板块/evidence_id 绑定）----
    blocks = [_block("ev-t5-10", T5_10), _block("ev-t5-11-a", T5_11_A),
              _block("ev-t5-11-b", T5_11_B, sec="财务")]
    facts = EF.build_evidence_facts(blocks, subject="宁德时代")
    rev = [f for f in facts if f.item_code == "OPERATING_REVENUE" and f.period == "2025-12-31"]
    cst = [f for f in facts if f.item_code == "OPERATING_COST" and f.period == "2025-12-31"]
    check(len(rev) == 1 and rev[0].value is not None
          and str(rev[0].value) == "316506369000.0",
          "链：表5-10 动力电池 2025 收入 → 316,506,369,000 元（revenue）")
    check(len(cst) == 1 and str(cst[0].value) == "241064397000.0",
          "链：表5-11 动力电池 2025 成本 → 241,064,397,000 元（cost）")
    check(cst[0].revenue_cost_category == "cost" and rev[0].revenue_cost_category == "revenue",
          "链：表题 → item_code → 收入/成本类别（不混淆）")
    check(cst[0].business_segment == "动力电池系统" and cst[0].unit == "wan_yuan",
          "链：业务板块 + 源单位来自表格上下文")
    check(cst[0].evidence_id == "ev-t5-11-b" and cst[0].evidence_fact_id.startswith("ef-"),
          "链：fact 绑定现有 evidence_id（数据块，含表体数值）+ content-addressed evidence_fact_id")
    check(all("表5-11" in f.row_column_source and "P50" in f.row_column_source for f in cst),
          "链：fact 保留表题 + 物理页 + 行列位置（row_column_source）")

    # ---- 2) A5：表5-10 收入写收入 → 不 mismatch ----
    st = _state_with(blocks)
    ans_rev = _answer("2025年动力电池系统营业收入为316506369000元", "ev-t5-10")
    pc_rev = E.deterministic_prechecks(st, ans_rev)
    check(pc_rev["c1"]["revenue_cost_mismatch"] is False,
          "A5：表5-10 收入写作收入 → 不 mismatch")

    # ---- 3) A5：表5-11 成本写成本 → 不 mismatch ----
    ans_cost = _answer("2025年动力电池系统营业成本为241064397000元", "ev-t5-11-b")
    pc_cost = E.deterministic_prechecks(st, ans_cost)
    check(pc_cost["c1"]["revenue_cost_mismatch"] is False,
          "A5：表5-11 成本写作成本 → 不 mismatch")

    # ---- 4) A5：表5-11 成本写收入 → revenue_cost_mismatch（跨块表）----
    ans_bad = _answer("2025年动力电池系统营业收入为241064397000元", "ev-t5-11-b")
    pc_bad = E.deterministic_prechecks(st, ans_bad)
    check(pc_bad["c1"]["revenue_cost_mismatch"] is True,
          "A5：表5-11 成本写作收入 → revenue_cost_mismatch（同金额不得替代）")
    # 跨块表（表题/单位在前块、数值在后块）在 value_presence 层被 value_missing 优先拦截
    # （单位未随块传播，属既有 harness 行为）；revenue_cost_mismatch→UNSUPPORTED 的完整
    # 终局在单块构成表下单独验收（见 case 5）。

    # ---- 5) 绑定 evidence_fact_id（无歧义单 fact）后 A5 仍走 specific 类别 ----
    single = _block("ev-single",
                    "表5-11 主营业务成本构成表\n\n单位：万元\n项目  2025年\n"
                    "动力电池系统  24,106,439.7\n", sec="成本")
    st2 = _state_with([single])
    one = EF.build_evidence_facts([single], subject="宁德时代")
    check(len(one) == 1, "绑定：单期单板块构成表 → 恰好 1 个 fact")
    ans_single = _answer("2025年动力电池系统营业收入为241064397000元", "ev-single")
    E.bind_evidence_facts(ans_single, one)
    check(ans_single.citations[0].evidence_fact_id == one[0].evidence_fact_id,
          "绑定：无歧义时 evidence 引用绑定 evidence_fact_id")
    pc_single = E.deterministic_prechecks(st2, ans_single)
    check(pc_single["c1"]["revenue_cost_mismatch"] is True,
          "绑定：经 evidence_fact_id specific 类别判定成本写收入 → mismatch")
    summary_single = SP.entailment_summary(st2, pc_single)
    unsup = [s for s in summary_single if s["claim_id"] == "c1"
             and s["verdict"] == "UNSUPPORTED"
             and s["reason"].startswith("revenue_cost_mismatch")]
    check(len(unsup) == 1,
          "A5：entailment_summary 将 revenue_cost_mismatch 汇总为 UNSUPPORTED（单块构成表）")

    # ---- 6) fail-closed：缺表题 / 缺表头 / 单位不明 / 期间不明 → 不产 fact ----
    no_title = _block("e1", "单位：万元\n项目  2025年\n动力电池系统  24,106,439.7\n")
    no_header = _block("e2", "表5-11 主营业务成本构成表\n单位：万元\n动力电池系统  24,106,439.7\n")
    bad_unit = _block("e3", "表5-11 主营业务成本构成表\n单位：?\n项目  2025年\n动力电池系统  24,106,439.7\n")
    no_period = _block("e4", "表5-11 主营业务成本构成表\n单位：万元\n项目  金额\n动力电池系统  24,106,439.7\n")
    for name, blk in (("缺表题", no_title), ("缺表头", no_header),
                      ("单位不明", bad_unit), ("期间不明", no_period)):
        f = EF.build_evidence_facts([blk], subject="宁德时代")
        check(len(f) == 0, f"fail-closed：{name} → 不产 EvidenceStructuredFact")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
