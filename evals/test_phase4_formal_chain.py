"""Eval: 正式主链离线集成（§八）—— Contract → planner.plan → SectionTask → Worker
→ research_common → Router → harness.run_question → ToolRegistry(mock) →
ResearchOutcome → SectionClaim → SectionResult。

用法: python -m evals.test_phase4_formal_chain

证明（无真实 LLM/博查/网络，全部 mock + 只读）：
 1. Worker 走 P2 Router（routing.router.route）+ P3 Harness（harness.runtime.run_question）；
    ``sections.topic_research.run_topic``（纵向预览并行循环）不被调用；
 2. Contract 身份沿主链保留（task_id 确定性派生，provenance 校验 fail-closed）；
 3. AspectCoverage 来自 Harness（只读投影，非并行循环产物）；
 4. D 级外部事实不能形成行业关键结论（industry_source_policy 移除 + unresolved）；
 5. 0 合格 claim → 显式 unresolved（no_valid_claim），不伪作结论；
 6. SectionResult 可被 chapter_writer 纯渲染候选（PURE_RENDER_CANDIDATE）承接；
 7. 无第二套 ResearchState / 预算（仅 harness.runtime 内部一个 state，每问一个预算）。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader as CL
from harness import policies as P
from harness import runtime as RT
from harness import schema as HS
from planning import report_planner as planner
from planning import schema as PS
from routing import router as router_mod
from routing import schema as RS
from sections import chapter_writer as CHW
from sections import company_worker as CW
from sections import industry_source_policy as ISP
from sections import research_common as RC
from sections import schema as SS
from sections import topic_research as STR

from evals.test_harness_runtime import MockLLM, _fake_registry

CONTRACTS_PATH = "templates/contracts/standard_v2.yaml"
COMPANY_ID = "300750"
COMPANY_NAME = "宁德时代"
REPORT_AS_OF = "2025-12-31"


def _build_context() -> RS.RouteContext:
    """合成 RouteContext（能力/数据全空 → 无 DB/external/结构化子 need，纯 RAG）。"""
    return RS.RouteContext(
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        available_document_ids=[], available_source_types=[],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=False,
        scope="consolidated", currency="CNY", purpose="credit_analysis",
        available_periods=[], snapshot_id=None)


def _make_research_question(reg, llm):
    """注入缝：复用 Phase 3 公共 run_question（与 _make_research_question 同构，trace 关闭）。"""
    def _run(need: RS.InformationNeed, route_result: RS.RouterResult,
             context: RS.RouteContext | None) -> HS.ResearchOutcome:
        return RT.run_question(
            need=need, route_result=route_result, registry=reg, llm=llm,
            budget=P.DEFAULT_BUDGET, run_id="run_fc",
            case_id=f"company:{need.need_id}",
            company_id=COMPANY_ID, section_id="company",
            trace_enabled=False, context=context)
    return _run


def _run_worker(task, llm, reg):
    """在「run_topic 必须不调用」的守卫下跑一次 company_worker（返回 (result, spy)。"""
    import routing.audit_v2 as audit_v2

    calls = {"run_topic": 0}
    orig_run_topic = STR.run_topic

    def _spy_run_topic(*_a, **_k):
        calls["run_topic"] += 1

    orig_write = audit_v2.write_router_audit
    STR.run_topic = _spy_run_topic
    audit_v2.write_router_audit = lambda *_a, **_k: None
    try:
        result = CW.run_task(
            task, company_id=COMPANY_ID, company_name=COMPANY_NAME, run_id="run_fc",
            registry=reg, llm=llm, build_context=_build_context,
            route_fn=router_mod.route, research_question=_make_research_question(reg, llm),
            evidence_db=None, financial_db=None, external_db=None, harness_db=None,
            checkpoint=False)
    finally:
        STR.run_topic = orig_run_topic
        audit_v2.write_router_audit = orig_write
    return result, calls


# 答案 JSON：5 个 fact claim 各引用 evidence e1，5 个 required-aspect 全覆盖。
# c2 为数值方面（各业务收入及收入占比）：用「数十亿元」满足 _has_number（亿/元 hint）
# 但不含 \d，避免 value_missing（_AMOUNT_RE 要求 \d）。
_ANSWER_JSON = json.dumps({
    "answer_text": "主营业务构成及各业务收入成本毛利构成、产业链位置与对应报告期口径均已核实披露",
    "claims": [
        {"claim_id": "c1", "text": "主营业务构成已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c2", "text": "各业务收入及收入占比已核实并披露，规模达数十亿元",
         "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c3", "text": "各业务成本与毛利构成已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c4", "text": "产业链位置已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c5", "text": "对应报告期与口径已核实并披露", "kind": "fact", "citation_refs": [0]},
    ],
    "citations": [{"ref_type": "evidence", "evidence_id": "e1"}],
    "aspects": [
        {"aspect_id": "a1", "text": "主营业务构成", "claim_ids": ["c1"]},
        {"aspect_id": "a2", "text": "各业务收入及收入占比", "claim_ids": ["c2"]},
        {"aspect_id": "a3", "text": "各业务成本与毛利构成", "claim_ids": ["c3"]},
        {"aspect_id": "a4", "text": "产业链位置", "claim_ids": ["c4"]},
        {"aspect_id": "a5", "text": "对应报告期与口径", "claim_ids": ["c5"]},
    ],
    "unresolved_items": [],
    "confidence": "high",
}, ensure_ascii=False)


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

    # ------------------------------------------------------------------ Part 1
    # Contract → planner.plan → SectionTask（确定性派生 + provenance fail-closed）。
    contracts = CL.load_contracts(CONTRACTS_PATH)
    fp = planner.contract_file_fingerprint(CONTRACTS_PATH)
    job = PS.ReportJobInput(
        job_id="j_fc", company_id=COMPANY_ID, company_name=COMPANY_NAME,
        credit_type="other", report_as_of=REPORT_AS_OF,
        enabled_sections=("company", "industry"))
    plan = planner.plan(job, contracts, fp)
    by_sec = {s.section_id: s for s in contracts}
    comp_task = next(t for t in plan.section_tasks if t.section_id == "company")

    ok, diffs = planner.validate_section_task_provenance(comp_task, by_sec["company"], "other")
    check(ok and not diffs, "provenance 校验通过（SectionTask 由契约确定性派生）")
    check(comp_task.dependency_versions.get("contract_sha256") == fp,
          "task 记录 contract_sha256（Contract 身份可回查）")
    check(comp_task.task_id == PS.derive_task_id(plan.plan_id, "company"),
          "task_id 确定性派生（plan_id+section_id）")

    tampered = dataclasses.replace(comp_task, questions=tuple(
        dataclasses.replace(q, required_aspects=("被篡改",)) for q in comp_task.questions))
    ok2, diffs2 = planner.validate_section_task_provenance(tampered, by_sec["company"], "other")
    check((not ok2) and bool(diffs2), "篡改 required_aspects → provenance fail-closed")

    # ------------------------------------------------------------------ Part 2
    # 正式主链：单问题 company_business_main → Worker → Router → Harness → SectionResult。
    q_main = next(q for q in comp_task.questions
                  if q.question_id == "company_business_main")
    comp_single = dataclasses.replace(comp_task, questions=(q_main,))

    llm = MockLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "主营业务构成"}}',
         '{"action": "INSPECT_EVIDENCE", "arguments": {"evidence_id": "e1"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_JSON])
    wr, spy = _run_worker(comp_single, llm, _fake_registry())

    check(spy["run_topic"] == 0, "sections.topic_research.run_topic 未被调用（无并行循环）")
    check(wr.claim_count == 5, "5 个有效 claim 进入 SectionResult")
    check(wr.unresolved_count == 0, "0 unresolved（全部方面覆盖）")
    sr = wr.section_result
    check(sr.status == "COMPLETED", "章节状态 COMPLETED")
    check(sr.task_id == comp_task.task_id, "Contract 身份保留（task_id 不变）")

    qo = wr.question_outcomes[0]
    check(qo["route"] == "STANDARD_RAG", "路由经 P2 Router = STANDARD_RAG")
    check(qo["completion_status"] == "COMPLETED", "问题 completion_status = COMPLETED")
    cov = qo["aspect_coverage"]
    check(cov["complete"] is True and cov["total_count"] == 5 and cov["covered_count"] == 5,
          "AspectCoverage 来自 Harness（complete + 5/5）")
    check("研究结论" in sr.markdown, "SectionResult.markdown 含「研究结论」")

    # ------------------------------------------------------------------ Part 3
    # D 级外部事实不能形成行业关键结论。
    d_ass = ISP.assess_industry_sources(
        [ISP.CitedSource("D", "https://unknown.example/a", None)])
    check(d_ass.d_only is True and d_ass.key_conclusion_supported is False,
          "D-only 来源判定：d_only=True 且不支持关键结论")

    claim_key = SS.SectionClaim(
        claim_id="c_key", section_id="industry", topic_id="industry_scale_cycle",
        question_ids=("q_key",), text="行业规模约 X 亿元", claim_type="fact",
        citation_refs=(HS.CitationRef(ref_type="external", source_snapshot_id="s_d"),))
    labels = {"s_d": {"source_grade": "D", "canonical_url": "https://unknown.example/a",
                      "published_at": None}}

    class _Q:
        def __init__(self, qid):
            self.question_id = qid
            self.evidence_requirements = []

    class _Task:
        def __init__(self, questions):
            self.questions = questions

    kept, extra, _ = ISP.apply_industry_source_policy(
        [claim_key], labels, _Task([_Q("q_key")]), None, section_id="industry")
    check(len(kept) == 0 and len(extra) == 1
          and extra[0].reason_code == "insufficient_industry_sources",
          "D 级外部关键结论被移除 + insufficient_industry_sources unresolved")

    # ------------------------------------------------------------------ Part 4
    # 0 合格 claim → 显式 unresolved，不伪作结论。
    llm_gap = MockLLM(
        ['{"action": "STOP_WITH_GAP", "arguments": {"reason": "缺关键材料"}}'], [])
    wr_gap, spy_gap = _run_worker(comp_single, llm_gap, _fake_registry())
    check(spy_gap["run_topic"] == 0, "缺口路径同样不调用 run_topic")
    check(wr_gap.claim_count == 0, "0 claim（不伪作结论）")
    check(wr_gap.unresolved_count >= 1, "生成显式 unresolved")
    check(wr_gap.section_result.status == "SECTION_BLOCKED", "章节 SECTION_BLOCKED")
    check(any(u.reason_code == "no_valid_claim" for u in wr_gap.section_result.unresolved),
          "0 合格 claim → no_valid_claim（非伪结论）")

    gap = CHW.build_gap_chapter(
        topic_id="company_business", question_id="company_business_main", kind="business")
    check(gap.chapter_ok is False, "gap 章节 chapter_ok=False")
    check("DATA_GAP" in gap.markdown, "gap markdown 含 DATA_GAP")

    # ------------------------------------------------------------------ Part 5
    # 纵向预览模块弃用标记 + 纯渲染候选承接。
    check(CHW.PURE_RENDER_CANDIDATE is True, "chapter_writer 标记 PURE_RENDER_CANDIDATE")
    check(STR.EXPERIMENTAL_TOPIC_RESEARCH is True and STR.NOT_A_FORMAL_RUNTIME_PATH is True,
          "sections.topic_research 标记 EXPERIMENTAL / NOT_A_FORMAL_RUNTIME_PATH")

    import planning.topic_research as PTR
    check(PTR.EXPERIMENTAL_TOPIC_QUERY_PLAN is True,
          "planning.topic_research 标记 EXPERIMENTAL_TOPIC_QUERY_PLAN")

    check(bool(sr.markdown) and "研究结论" in sr.markdown,
          "SectionResult.markdown 可被 chapter_writer 纯渲染候选承接")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
