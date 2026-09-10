"""Eval: Phase 4 Batch C — 公司/行业研究 Worker（复用 Harness 的确定性转换层）。

不调用真实 LLM / Embedding / Chroma / 互联网。Harness 入口用注入的确定性 fake
（route_fn + research_question 返回 canned ResearchOutcome），build_context 注入合成
RouteContext，sections/external 库用 None（跳过 I/O）。

覆盖（任务书 §11 确定性契约）：
A. build_need 映射（question → InformationNeed 字段直通）。
B. gap_state / missing_policy_state 全量映射（含 transfer_human / valid_no_controller
   → WAITING_HUMAN；WAITING_USER stop_reason → WAITING_HUMAN）。
C. derive_status 优先级（WAITING_HUMAN > SECTION_BLOCKED/JOB_BLOCKED > COMPLETED_WITH_GAPS）。
D. convert_question_outcome：COMPLETED → claims；COMPLETED_WITH_GAPS → claims+缺口；
   UNRESOLVED/NOT_IMPLEMENTED/FAILED → 不写肯定事实仅缺口；retrieval_observation 过滤；
   fact 无引用 / 引用下标越界 → claim 丢弃。
E. citation_label：evidence/structured/external 展示（snippet/URL 不作正式引用）。
F. dependency_fingerprint 幂等 + 对 doc_ids/snapshot_id/task 依赖/版本常量的敏感性。
G. run_task 端到端（公司/行业）：完整覆盖 → COMPLETED；幂等 → 同 section_version；
   依赖变化 → 新 section_version；section_id 错配 → fail-closed；行业来源分级诊断。
H. Store 原子提交（复用 + current 切换 + 失败回滚不切 current）。

用法: python -m evals.test_section_research_workers
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from types import SimpleNamespace  # noqa: E402

from external_v2 import schema as XS  # noqa: E402
from external_v2 import store as extstore  # noqa: E402
from harness import policies as P  # noqa: E402
from harness import schema as HS  # noqa: E402
from planning import schema as PS  # noqa: E402
from routing import schema as RS  # noqa: E402
from sections import citation_authority as CA  # noqa: E402
from sections import company_worker as CW  # noqa: E402
from sections import industry_source_policy as ISP  # noqa: E402
from sections import industry_worker as IW  # noqa: E402
from sections import research_common as RC  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raise(fn, exc_type, msg: str) -> None:
    try:
        fn()
        check(False, f"{msg}（未抛异常）")
    except exc_type:
        check(True, msg)
    except Exception as e:  # noqa: BLE001
        check(False, f"{msg}（抛错类型不对: {type(e).__name__}: {e}）")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

EV = HS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=3)
EXT = HS.CitationRef(ref_type="external", source_snapshot_id="snap_1")
STR = HS.CitationRef(ref_type="structured", snapshot_id="s1", item_code="TOTAL_ASSETS",
                     period="2025-12-31")


def _company_task(*, task_id: str = "task_comp",
                  dependency_versions: dict | None = None) -> PS.SectionTask:
    return PS.SectionTask(
        task_id=task_id, plan_id="plan_1", section_id="company",
        title="公司信用研究", purpose="credit_analysis", research_policy="harness",
        topic_ids=("company_identity", "company_business", "company_debt"),
        questions=(
            PS.PlannedQuestion("company_subject_match", "主体是否一致", "P0",
                               "company_identity", impact_scope=("subject",),
                               blocking_policy=("JOB_BLOCKED",),
                               missing_policy="transfer_human"),
            PS.PlannedQuestion("company_business_main", "主营业务构成", "P0",
                               "company_business", impact_scope=("subject",),
                               blocking_policy=("SECTION_BLOCKED",),
                               missing_policy="write_not_found"),
            PS.PlannedQuestion("company_debt_credit", "授信额度", "P0",
                               "company_debt", impact_scope=("solvency",),
                               blocking_policy=("REPORT_BLOCKED",),
                               missing_policy="write_not_found"),
        ),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions=dependency_versions or {},
    )


def _industry_task(*, task_id: str = "task_ind") -> PS.SectionTask:
    return PS.SectionTask(
        task_id=task_id, plan_id="plan_1", section_id="industry",
        title="行业研究", purpose="credit_analysis", research_policy="harness",
        topic_ids=("industry_definition", "industry_scale_cycle"),
        questions=(
            PS.PlannedQuestion("industry_definition", "行业定义", "P1",
                               "industry_definition", impact_scope=("subject",),
                               missing_policy="write_not_found"),
            PS.PlannedQuestion("industry_scale_cycle", "行业规模增速", "P0",
                               "industry_scale_cycle", missing_policy="proxy_allowed"),
        ),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions={},
    )


def _plan(section_id: str, task: PS.SectionTask) -> PS.ReportPlan:
    return PS.ReportPlan(
        plan_id=task.plan_id, job_id="job_1", company_id="300750", company_name="测试公司",
        credit_type="other", report_as_of="2025-12-31", template_id="standard_v2",
        input_fingerprint="fp", contract_fingerprint="fp_c",
        planner_version=PS.PLANNER_VERSION, section_tasks=(task,),
        created_at="2026-01-01T00:00:00Z")


def _need(qid: str, question: str = "q") -> RS.InformationNeed:
    return RS.InformationNeed(need_id=qid, section_id="company", question=question,
                              required_evidence_types=[], required_source_types=[],
                              time_scope=None, priority="P1", depends_on=[])


def _claim(claim_id: str, text: str, kind: str, refs) -> HS.Claim:
    return HS.Claim(claim_id=claim_id, text=text, kind=kind, citation_refs=list(refs))


def _answer(claims, citations, *, unresolved=(), confidence="high") -> HS.ResearchAnswer:
    return HS.ResearchAnswer(question_id="q", answer_text="a", claims=list(claims),
                             citations=list(citations), unresolved_items=list(unresolved),
                             confidence=confidence)


def _state(qid: str, *, evidence_ids=(), external_snapshot_ids=()) -> HS.ResearchState:
    st = HS.ResearchState(run_id="r", case_id="c", question_id=qid, company_id="300750",
                          section_id="company", original_question="q", need=_need(qid))
    st.evidence_ids = list(evidence_ids)
    st.external_snapshot_ids = list(external_snapshot_ids)
    return st


def _outcome(completion_status: str, stop_reason: str, answer=None, *, qid: str = "q",
             evidence_ids=(), external_snapshot_ids=()) -> HS.ResearchOutcome:
    st = _state(qid, evidence_ids=evidence_ids,
                external_snapshot_ids=external_snapshot_ids)
    return HS.ResearchOutcome(state=st, answer=answer,
                              success=(completion_status == "COMPLETED"),
                              completion_status=completion_status, stop_reason=stop_reason)


def _context(company_id="300750", doc_ids=("d1",), snapshot_id=None,
             external=True) -> RS.RouteContext:
    return RS.RouteContext(
        company_id=company_id, report_as_of="2025-12-31",
        available_document_ids=list(doc_ids), available_source_types=["pdf"],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=external, snapshot_id=snapshot_id)


def _fake_route(need, context):  # noqa: ARG001
    budget = RS.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1, fusion_k=1,
                                context_k=1, timeout_ms=1000)
    decision = RS.RouteDecision(need_id=need.need_id, route="STANDARD_RAG",
                                reason_code="SECTION_TOPIC_SYNTHESIS", filters={},
                                budget=budget, fallback_routes=[], decided_by="rule",
                                rule_version=RS.RULE_VERSION, confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None, trace_id="t")


def _fake_research(outcomes_by_qid):
    def _run(need, route_result, context):  # noqa: ARG001
        return outcomes_by_qid[need.need_id]
    return _run


# 权威校验注入 fake（不读库；lookup 返回 None 或 SimpleNamespace）。
def _ev(page=3, text="正文", company_id="300750", document_id="doc",
        document_version="dv1", evidence_set_version="sv1"):
    return SimpleNamespace(company_id=company_id, document_id=document_id,
                           document_version=document_version,
                           evidence_set_version=evidence_set_version,
                           page_number=page, text=text)


def _ext(status="SNAPSHOTTED", content_text="正文内容，非 URL 非 snippet", company_id="",
         canonical_url="https://stats.gov.cn/x", original_url="https://stats.gov.cn/x",
         snippet="摘要", published_at="2025-01-01"):
    return SimpleNamespace(company_id=company_id, status=status, content_text=content_text,
                           content_hash=XS.content_hash(content_text),
                           canonical_url=canonical_url, original_url=original_url,
                           snippet=snippet, published_at=published_at)


def _snap(company_id="300750", scope="consolidated", currency="CNY",
          purpose="credit_analysis"):
    return SimpleNamespace(company_id=company_id, scope=scope, currency=currency,
                           purpose=purpose)


def _auth(exists=True, is_current=True, validity="valid", report_blocked=False,
          quarantined=False):
    return SimpleNamespace(exists=exists, is_current=is_current, validity=validity,
                           report_blocked=report_blocked, quarantined=quarantined)


def _item(code="TOTAL_ASSETS", period="2025-12-31"):
    return SimpleNamespace(standard_item_code=code, report_period=period)


def _authority(*, evidence_get=None, external_get=None, snapshot_authority=None,
               snapshot_get=None, metric_get=None, item_list=None, formula_get=None):
    return CA.CitationAuthority(
        company_id="300750", context=_context(),
        evidence_get=evidence_get, external_get=external_get,
        snapshot_authority=snapshot_authority, snapshot_get=snapshot_get,
        metric_get=metric_get, item_list=item_list, formula_get=formula_get)


def main():
    task = _company_task()
    qmap = {q.question_id: q for q in task.questions}
    q_subject = qmap["company_subject_match"]
    q_business = qmap["company_business_main"]

    # ---- A. build_need 映射 ----
    need = RC.build_need(task, q_subject)
    check(need.need_id == "company_subject_match", "need_id = question_id")
    check(need.section_id == "company", "need.section_id 直通")
    check(need.question == "主体是否一致", "need.question 直通")
    check(need.priority == "P0", "need.priority 直通")
    check(need.time_scope is None, "time_scope 契约未携带 → None")

    # ---- A2. build_need 承接 Contract 要求（修复四）----
    q_local = PS.PlannedQuestion("q_local_ev", "本地证据问题", "P1", "company_identity",
        evidence_requirements=(
            {"requirement_id": "r1", "evidence_kind": "paragraph",
             "source_classes": ["company_industry"], "minimum_sources": 1,
             "required_fields": ["company_name"]},
        ))
    need_local = RC.build_need(task, q_local)
    check(need_local.required_evidence_types == ["paragraph"],
          "required_evidence_types ← evidence_kind")
    check(need_local.required_source_types == ["company_industry"],
          "required_source_types ← source_classes")
    check(need_local.time_scope is None, "本地证据无时效信号 → time_scope None")
    check(need_local.metadata.get("required_fields") == ["company_name"],
          "metadata.required_fields 承接 required_fields")

    q_ext = PS.PlannedQuestion("q_ext_recent", "近期风险", "P0", "company_legal_risks",
        evidence_requirements=(
            {"requirement_id": "r2", "evidence_kind": "web",
             "source_classes": ["external"], "minimum_sources": 1,
             "freshness_policy": "near_1y"},
        ))
    need_ext = RC.build_need(task, q_ext, report_as_of="2025-12-31")
    check(need_ext.time_scope == "2025-12-31",
          "外部来源 → time_scope=report_as_of（时效截止）")
    check(need_ext.metadata.get("freshness_policy") == "near_1y",
          "metadata.freshness_policy 承接 freshness")

    q_struct = PS.PlannedQuestion("q_struct", "总资产", "P1", "company_debt",
        evidence_requirements=(
            {"requirement_id": "r3", "evidence_kind": "structured_db",
             "source_classes": ["structured_db"], "minimum_sources": 1,
             "required_fields": ["TOTAL_ASSETS"]},
        ))
    need_struct = RC.build_need(task, q_struct)
    check(need_struct.required_evidence_types == ["structured_db"],
          "structured_db 字段问题 → required_evidence_types 非空")
    check(need_struct.required_source_types == ["structured_db"],
          "structured_db 字段问题 → required_source_types 非空")

    need_plain = RC.build_need(task, q_business)
    check(need_plain.required_evidence_types == [] and need_plain.required_source_types == [],
          "无要求问题保持空 Need")

    # ---- B. gap_state / missing_policy_state 映射 ----
    comp_ok = _outcome("COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS",
                       answer=_answer([_claim("c1", "x", "fact", [0])], [EV]))
    check(RC.gap_state(q_subject, comp_ok) == "WAITING_HUMAN",
          "transfer_human → WAITING_HUMAN")
    check(RC.gap_state(q_business, comp_ok) == "NOT_FOUND_AFTER_SEARCH",
          "write_not_found → NOT_FOUND_AFTER_SEARCH")
    check(RC.missing_policy_state("write_not_found_disclose") == "NOT_FOUND_AFTER_SEARCH",
          "write_not_found_disclose → NOT_FOUND_AFTER_SEARCH")
    check(RC.missing_policy_state("missing_material") == "NOT_PROVIDED",
          "missing_material → NOT_PROVIDED")
    check(RC.missing_policy_state("conflict_pause") == "CONFLICT",
          "conflict_pause → CONFLICT")
    check(RC.missing_policy_state("not_applicable_no_plan") == "NOT_APPLICABLE",
          "not_applicable_no_plan → NOT_APPLICABLE")
    check(RC.missing_policy_state("proxy_allowed") == "NOT_PROVIDED",
          "proxy_allowed → NOT_PROVIDED")
    # valid_no_controller（无法确认）→ WAITING_HUMAN
    q_control = PS.PlannedQuestion("company_control_chain", "控制链条", "P0",
                                   "company_control", impact_scope=("subject",),
                                   blocking_policy=("REPORT_BLOCKED",),
                                   missing_policy="valid_no_controller")
    check(RC.gap_state(q_control, comp_ok) == "WAITING_HUMAN",
          "valid_no_controller（无法确认）→ WAITING_HUMAN")
    # WAITING_USER stop_reason 强制 WAITING_HUMAN（即使 missing_policy 是 write_not_found）
    wait_ok = _outcome("UNRESOLVED", "WAITING_USER")
    check(RC.gap_state(q_business, wait_ok) == "WAITING_HUMAN",
          "WAITING_USER stop_reason → WAITING_HUMAN")

    # ---- C. derive_status 优先级（阻断看 blocking_effects，不看 state）----
    def _ur(state="NOT_FOUND_AFTER_SEARCH", blocking=(), qid="q1"):
        return SS.SectionUnresolved(unresolved_id=f"ur_{state}_{''.join(blocking)}",
                                    section_id="c", topic_id="t", question_id=qid,
                                    state=state, reason_code="r", detail="d",
                                    blocking_effects=blocking)
    check(RC.derive_status(()) == "COMPLETED", "无 unresolved → COMPLETED")
    check(RC.derive_status((_ur(),)) == "COMPLETED_WITH_GAPS",
          "非阻断缺口 → COMPLETED_WITH_GAPS")
    check(RC.derive_status((_ur(blocking=("SECTION_BLOCKED",)),)) == "SECTION_BLOCKED",
          "SECTION_BLOCKED 阻断 → SECTION_BLOCKED")
    check(RC.derive_status((_ur(blocking=("REPORT_BLOCKED",)),)) == "SECTION_BLOCKED",
          "REPORT_BLOCKED 阻断 → SECTION_BLOCKED")
    check(RC.derive_status((_ur(blocking=("JOB_BLOCKED",)),)) == "SECTION_BLOCKED",
          "JOB_BLOCKED 阻断 → SECTION_BLOCKED")
    check(RC.derive_status((_ur(state="WAITING_HUMAN"), _ur(blocking=("SECTION_BLOCKED",))))
          == "WAITING_HUMAN", "WAITING_HUMAN 优先于 SECTION_BLOCKED")

    # ---- D. convert_question_outcome ----
    # D1. COMPLETED → claims，无 unresolved
    c_fact = _claim("c1", "输入主体与材料主体一致", "fact", [0])
    c_fact2 = _claim("c2", "主营为动力电池", "fact", [1])
    c_infer = _claim("c3", "偿债能力稳健", "inference", [0])
    ans_full = _answer([c_fact, c_fact2, c_infer], [EV, EXT], confidence="high")
    out_full = _outcome("COMPLETED", "COMPLETED", answer=ans_full, qid="company_subject_match",
                        evidence_ids=["ev_1"], external_snapshot_ids=["snap_1"])
    cl, ur, notes = RC.convert_question_outcome(task, q_subject, out_full, section_id="company")
    check(len(cl) == 3, f"COMPLETED 3 条 claim（got {len(cl)}）")
    check(len(ur) == 0, "COMPLETED 无 unresolved")
    check(notes == [], "COMPLETED 无过滤 note")
    check(all(c.citation_refs for c in cl if c.claim_type in ("fact", "calculation")),
          "fact claim 带引用")
    check(all(c.confidence == "high" for c in cl), "claim 置信度取自 answer.confidence")

    # D2. COMPLETED_WITH_GAPS → claims + 缺口 unresolved（state 按 missing_policy）
    ans_gap = _answer([c_fact], [EV], confidence="low", unresolved=("部分证据缺失",))
    out_gap = _outcome("COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS", answer=ans_gap,
                       qid="company_business_main", evidence_ids=["ev_1"])
    cl2, ur2, _ = RC.convert_question_outcome(task, q_business, out_gap, section_id="company")
    check(len(cl2) == 1, "COMPLETED_WITH_GAPS 保留 1 条 claim")
    check(len(ur2) == 1 and ur2[0].state == "NOT_FOUND_AFTER_SEARCH",
          f"COMPLETED_WITH_GAPS 缺口 state=NOT_FOUND_AFTER_SEARCH（got {[u.state for u in ur2]}）")
    check(ur2[0].attempted_sources == ("ev_1",), "attempted_sources 记录证据")

    # D3. UNRESOLVED / NOT_IMPLEMENTED / FAILED → 不写肯定事实，仅缺口
    for cs, sr, want_state in (("UNRESOLVED", "NOT_FOUND_AFTER_SEARCH", "NOT_FOUND_AFTER_SEARCH"),
                               ("NOT_IMPLEMENTED", "PATH_NOT_IMPLEMENTED", "NOT_FOUND_AFTER_SEARCH"),
                               ("FAILED", "FATAL_TOOL_ERROR", "NOT_FOUND_AFTER_SEARCH")):
        out_u = _outcome(cs, sr, qid="company_business_main")
        cl_u, ur_u, _ = RC.convert_question_outcome(task, q_business, out_u, section_id="company")
        check(len(cl_u) == 0, f"{cs} 不写肯定事实")
        check(len(ur_u) == 1, f"{cs} 生成缺口")
        check(ur_u[0].state == want_state, f"{cs} 缺口 state={want_state}")

    # D4. retrieval_observation 过滤
    c_ro = _claim("ro1", "本次检索未取得客户集中度", "retrieval_observation", [0])
    out_ro = _outcome("COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS",
                      answer=_answer([c_ro, c_fact], [EV]), qid="company_business_main")
    cl4, _, notes4 = RC.convert_question_outcome(task, q_business, out_ro, section_id="company")
    check([c.claim_type for c in cl4] == ["fact"], "retrieval_observation 被过滤")
    check(any("retrieval_observation" in n for n in notes4), "过滤动作记录 note")

    # D5. fact 无引用 / 下标越界 → claim 丢弃
    out_noref = _outcome("COMPLETED", "COMPLETED",
                         answer=_answer([_claim("cn", "无依据", "fact", [])], []),
                         qid="company_business_main")
    cl5, ur5, notes5 = RC.convert_question_outcome(task, q_business, out_noref, section_id="company")
    check(len(cl5) == 0, "fact 无引用被丢弃")
    check(len(ur5) == 1 and ur5[0].reason_code == "no_valid_claim",
          "无有效 claim → no_valid_claim 缺口")
    out_oob = _outcome("COMPLETED", "COMPLETED",
                       answer=_answer([_claim("co", "越界", "fact", [5])], [EV]),
                       qid="company_business_main")
    cl_oob, _, notes_oob = RC.convert_question_outcome(task, q_business, out_oob, section_id="company")
    check(len(cl_oob) == 0 and any("越界" in n for n in notes_oob), "下标越界被丢弃")

    # D6. inference 无引用 → 丢弃（修复一：所有 claim 都需要引用，不止 fact）
    c_infer_noref = _claim("ci", "偿债能力稳健", "inference", [])
    out_infer_noref = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([c_infer_noref, c_fact], [EV]), qid="company_business_main",
        evidence_ids=["ev_1"])
    cl6, ur6, notes6 = RC.convert_question_outcome(task, q_business, out_infer_noref,
                                                   section_id="company")
    check([c.text for c in cl6] == ["输入主体与材料主体一致"],
          f"inference 无引用被丢弃，仅保留带引用 claim（got {[c.text for c in cl6]}）")
    check(any("无引用" in n for n in notes6), "inference 无引用记录 note")
    check(len(ur6) == 0, "仍有有效 claim → 不产生 no_valid_claim 缺口")

    # D7. 权威性校验 fail-closed（修复二：引用不通过权威 → 丢弃，不 fail-open）
    # 结构化：快照不存在 → 丢弃
    auth_no_snap = _authority(snapshot_authority=lambda sid: None)
    out_str = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([_claim("cs", "总资产 100 亿", "fact", [0])], [STR]),
        qid="company_debt_credit")
    cl_s1, _, notes_s1 = RC.convert_question_outcome(task, qmap["company_debt_credit"],
        out_str, section_id="company", authority=auth_no_snap)
    check(len(cl_s1) == 0 and any("snapshot_not_found" in n for n in notes_s1),
          "结构化引用快照不存在 → 丢弃 + snapshot_not_found 诊断")
    # 结构化：快照 validity=stale（失效）→ 丢弃
    auth_stale = _authority(snapshot_authority=lambda sid: _auth(validity="stale"),
                            snapshot_get=lambda sid: _snap())
    cl_s2, _, notes_s2 = RC.convert_question_outcome(task, qmap["company_debt_credit"],
        out_str, section_id="company", authority=auth_stale)
    check(len(cl_s2) == 0 and any("snapshot_stale" in n for n in notes_s2),
          "结构化快照失效（stale）→ 丢弃 + snapshot_stale 诊断")
    # 证据：页码不匹配 → 丢弃
    auth_page = _authority(evidence_get=lambda eid: _ev(page=9))
    out_ev = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([_claim("ce", "事实", "fact", [0])], [EV]),
        qid="company_business_main")
    cl_e1, _, notes_e1 = RC.convert_question_outcome(task, q_business, out_ev,
        section_id="company", authority=auth_page)
    check(len(cl_e1) == 0 and any("evidence_page_mismatch" in n for n in notes_e1),
          "证据页码不匹配 → 丢弃 + evidence_page_mismatch 诊断")
    # 外部：正文为空 → 丢弃
    auth_empty = _authority(external_get=lambda sid: _ext(content_text="   "))
    out_ext = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([_claim("cx", "行业数据", "fact", [0])], [EXT]),
        qid="company_business_main", external_snapshot_ids=["snap_1"])
    cl_x1, _, notes_x1 = RC.convert_question_outcome(task, q_business, out_ext,
        section_id="company", authority=auth_empty)
    check(len(cl_x1) == 0 and any("external_content_empty" in n for n in notes_x1),
          "外部正文为空 → 丢弃 + external_content_empty 诊断")
    # 外部：仅 snippet（正文 == 摘要）→ 丢弃
    auth_snippet = _authority(external_get=lambda sid: _ext(
        content_text="仅摘要", snippet="仅摘要"))
    cl_x2, _, notes_x2 = RC.convert_question_outcome(task, q_business, out_ext,
        section_id="company", authority=auth_snippet)
    check(len(cl_x2) == 0 and any("external_just_snippet" in n for n in notes_x2),
          "外部仅 snippet → 丢弃 + external_just_snippet 诊断")
    # 权威查询异常（lookup 抛错）→ fail-closed 丢弃（不 crash 整题）
    def _boom(sid):
        raise RuntimeError("db missing")
    auth_boom = _authority(external_get=_boom)
    cl_x3, _, notes_x3 = RC.convert_question_outcome(task, q_business, out_ext,
        section_id="company", authority=auth_boom)
    check(len(cl_x3) == 0 and any("authority_query_failed" in n for n in notes_x3),
          "权威查询抛错 → 丢弃 + authority_query_failed 诊断（不 fail-open）")

    # ---- E. citation_label ----
    check(RC.citation_label(EV) == "[来源文件 ev_1，PDF第3页]", "evidence 引用展示")
    check(RC.citation_label(HS.CitationRef(ref_type="evidence", evidence_id="ev_9")) ==
          "[来源文件 ev_9，PDF页码未标注]", "evidence 无页码展示")
    check(RC.citation_label(STR) == "[FinancialSnapshot，2025-12-31，TOTAL_ASSETS]",
          "structured 引用展示")
    check("snap_1" in RC.citation_label(EXT), "external 引用展示含 source_snapshot_id")
    check(RC.citation_label(EXT, {"snap_1": {"name": "某政府网", "published_at": "2025-01-01",
                                             "fetched_at": "2025-06-01",
                                             "source_grade": "A"}})
          == "[[A] 某政府网，2025-01-01，2025-06-01]",
          "external 引用带来源名/日期（A 级带 [A] 分级前缀）")
    check(RC.citation_label(EXT, {"snap_1": {"name": "某政府网", "published_at": "2025-01-01",
                                             "fetched_at": "2025-06-01",
                                             "source_grade": None}})
          == "[某政府网，2025-01-01，2025-06-01]",
          "external 引用无分级时不加前缀")

    # ---- F. dependency_fingerprint 幂等 + 敏感性 ----
    ctx1 = _context(doc_ids=("d1",), snapshot_id="snap_a")
    fp1 = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                    rules_version=CW.RULES_VERSION,
                                    prompt_version=CW.PROMPT_VERSION,
                                    worker_version=CW.WORKER_VERSION)
    fp1b = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                     rules_version=CW.RULES_VERSION,
                                     prompt_version=CW.PROMPT_VERSION,
                                     worker_version=CW.WORKER_VERSION)
    check(fp1 == fp1b, "依赖指纹幂等")
    fp_doc = RC.dependency_fingerprint(task, _context(doc_ids=("d1", "d2"), snapshot_id="snap_a"),
                                       renderer_version=CW.RENDERER_VERSION,
                                       rules_version=CW.RULES_VERSION,
                                       prompt_version=CW.PROMPT_VERSION,
                                       worker_version=CW.WORKER_VERSION)
    check(fp_doc != fp1, "doc_ids 变化 → 指纹变化")
    fp_snap = RC.dependency_fingerprint(task, _context(doc_ids=("d1",), snapshot_id="snap_b"),
                                        renderer_version=CW.RENDERER_VERSION,
                                        rules_version=CW.RULES_VERSION,
                                        prompt_version=CW.PROMPT_VERSION,
                                        worker_version=CW.WORKER_VERSION)
    check(fp_snap != fp1, "snapshot_id 变化 → 指纹变化")
    fp_ver = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                       rules_version=CW.RULES_VERSION,
                                       prompt_version=CW.PROMPT_VERSION,
                                       worker_version="p4-comp-worker-v2")
    check(fp_ver != fp1, "worker 版本变化 → 指纹变化")

    # F2. 依赖指纹扩展（修复五）：model_id / budget / outcome 内容身份 / harness 版本
    fp_model = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                         rules_version=CW.RULES_VERSION,
                                         prompt_version=CW.PROMPT_VERSION,
                                         worker_version=CW.WORKER_VERSION,
                                         model_id="deepseek-v4-pro")
    check(fp_model != fp1, "model_id 变化 → 指纹变化")
    fp_budget = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                          rules_version=CW.RULES_VERSION,
                                          prompt_version=CW.PROMPT_VERSION,
                                          worker_version=CW.WORKER_VERSION,
                                          budget=P.DEFAULT_BUDGET)
    check(fp_budget != fp1, "budget 变化 → 指纹变化")
    fp_oid = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                       rules_version=CW.RULES_VERSION,
                                       prompt_version=CW.PROMPT_VERSION,
                                       worker_version=CW.WORKER_VERSION,
                                       outcome_identities=("oid_1",))
    check(fp_oid != fp1, "outcome 内容身份变化 → 指纹变化")
    fp_harness = RC.dependency_fingerprint(task, ctx1, renderer_version=CW.RENDERER_VERSION,
                                           rules_version=CW.RULES_VERSION,
                                           prompt_version=CW.PROMPT_VERSION,
                                           worker_version=CW.WORKER_VERSION,
                                           harness_version="v2")
    check(fp_harness != fp1, "harness 版本变化 → 指纹变化")

    # F3. 内容身份：幂等 + 内容敏感（修复五）
    oid1 = RC.outcome_content_identity(q_business, out_gap)
    oid2 = RC.outcome_content_identity(q_business, out_gap)
    check(oid1 == oid2, "outcome 内容身份幂等（无时间戳/call_id）")
    out_gap2 = _outcome("COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS",
        answer=_answer([_claim("c1x", "不同的文本", "fact", [0])], [EV]),
        qid="company_business_main")
    check(oid1 != RC.outcome_content_identity(q_business, out_gap2),
          "outcome 内容变化 → 新内容身份")

    # F4. unresolved_id 绑定缺口内容 + 尝试来源（修复五）
    ur_a = RC._make_unresolved(q_business, section_id="company",
        state="NOT_FOUND_AFTER_SEARCH", reason_code="no_valid_claim", detail="详情A",
        attempted=("ev_1",))
    ur_b = RC._make_unresolved(q_business, section_id="company",
        state="NOT_FOUND_AFTER_SEARCH", reason_code="no_valid_claim", detail="详情B",
        attempted=("ev_1",))
    ur_c = RC._make_unresolved(q_business, section_id="company",
        state="NOT_FOUND_AFTER_SEARCH", reason_code="no_valid_claim", detail="详情A",
        attempted=("ev_1",))
    ur_d = RC._make_unresolved(q_business, section_id="company",
        state="NOT_FOUND_AFTER_SEARCH", reason_code="no_valid_claim", detail="详情A",
        attempted=("ev_1", "snap_2"))
    check(ur_a.unresolved_id != ur_b.unresolved_id, "detail 变化 → 新 unresolved_id")
    check(ur_a.unresolved_id == ur_c.unresolved_id, "相同输入 → 同 unresolved_id（幂等）")
    check(ur_a.unresolved_id != ur_d.unresolved_id, "attempted_sources 变化 → 新 unresolved_id")

    # ---- G. run_task 端到端 ----
    outcomes_full = {
        "company_subject_match": out_full,
        "company_business_main": _outcome(
            "COMPLETED", "COMPLETED", qid="company_business_main",
            answer=_answer([_claim("c2b", "主营为动力电池", "fact", [0])], [EXT],
                          confidence="high"),
            external_snapshot_ids=["snap_1"]),
        "company_debt_credit": _outcome(
            "COMPLETED", "COMPLETED", qid="company_debt_credit",
            answer=_answer([_claim("c4", "授信额度 50 亿元", "fact", [0])], [EV], confidence="high"),
            evidence_ids=["ev_1"]),
    }
    wr1 = CW.run_task(task, company_id="300750", company_name="测试公司",
                      build_context=lambda: ctx1, route_fn=_fake_route,
                      research_question=_fake_research(outcomes_full),
                      external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(wr1.section_result.status == "COMPLETED",
          f"完整覆盖 status=COMPLETED（got {wr1.section_result.status}）")
    check(wr1.claim_count == 5, f"claim_count=5（got {wr1.claim_count}）")
    check(wr1.unresolved_count == 0, "无 unresolved")
    check(wr1.section_result.section_id == "company", "section_id=company")
    check(wr1.section_result.source_question_ids ==
          ("company_subject_match", "company_business_main", "company_debt_credit"),
          "source_question_ids 覆盖全部问题")
    check(wr1.section_result.source_run_ids == (), "无 run_id → source_run_ids=()")
    check("authority_report" in wr1.verification
          and wr1.verification["authority_report"]["applied"] is False,
          "verification 含 authority_report（无库时 applied=False）")
    check("source_assessment" in wr1.verification,
          "verification 含 source_assessment（公司无 source_policy → 空 dict）")
    check(all("content_fingerprint" in o for o in wr1.question_outcomes),
          "question_outcomes 记录 content_fingerprint")
    check(wr1.verification["questions_with_claims"] == 3, "3 个问题全部被 claim 覆盖")
    check("研究结论" in wr1.section_result.markdown, "markdown 含研究结论小节")
    check("引用来源" in wr1.section_result.markdown, "markdown 含引用小节")
    check("动力电池" in wr1.section_result.markdown, "markdown 含 claim 正文")

    # 幂等：同输入 → 同 section_version
    wr2 = CW.run_task(task, company_id="300750", company_name="测试公司",
                      build_context=lambda: ctx1, route_fn=_fake_route,
                      research_question=_fake_research(outcomes_full),
                      external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(wr2.section_result.section_version == wr1.section_result.section_version,
          "同输入幂等 → 同 section_version")

    # 依赖变化（doc_ids 增加）→ 新 section_version（claim 相同，指纹不同）
    ctx2 = _context(doc_ids=("d1", "d2"), snapshot_id="snap_a")
    wr3 = CW.run_task(task, company_id="300750", company_name="测试公司",
                      build_context=lambda: ctx2, route_fn=_fake_route,
                      research_question=_fake_research(outcomes_full),
                      external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(wr3.section_result.section_version != wr1.section_result.section_version,
          "依赖变化 → 新 section_version")
    check(wr3.section_result.dependency_fingerprint != wr1.section_result.dependency_fingerprint,
          "依赖指纹随 version 变化")

    # source_run_ids 记录真实 run_id；run_id 不进依赖指纹（修复五：内容身份不含时间戳/call_id）
    wr_runid = CW.run_task(task, company_id="300750", company_name="测试公司",
                           run_id="run_xyz", build_context=lambda: ctx1, route_fn=_fake_route,
                           research_question=_fake_research(outcomes_full),
                           external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(wr_runid.section_result.source_run_ids == ("run_xyz",),
          "source_run_ids 记录 run_id")
    check(wr_runid.section_result.dependency_fingerprint == wr1.section_result.dependency_fingerprint,
          "run_id 变化不影响依赖指纹（run_id 不进内容身份）")

    # 诚实缺口：company_business_main 未找到 → COMPLETED_WITH_GAPS（SECTION_BLOCKED）
    outcomes_gap = dict(outcomes_full)
    outcomes_gap["company_business_main"] = _outcome(
        "UNRESOLVED", "NOT_FOUND_AFTER_SEARCH", qid="company_business_main")
    wr_gap = CW.run_task(task, company_id="300750", company_name="测试公司",
                         build_context=lambda: ctx1, route_fn=_fake_route,
                         research_question=_fake_research(outcomes_gap),
                         external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(wr_gap.section_result.status == "SECTION_BLOCKED",
          f"主营 SECTION_BLOCKED → 章节 SECTION_BLOCKED（got {wr_gap.section_result.status}）")
    bus_ur = [u for u in wr_gap.section_result.unresolved
              if u.question_id == "company_business_main"]
    check(bus_ur and bus_ur[0].state == "NOT_FOUND_AFTER_SEARCH",
          "主营缺口诚实状态 NOT_FOUND_AFTER_SEARCH")

    # section_id 错配 → fail-closed
    expect_raise(lambda: IW.run_task(task, company_id="300750",
                                     build_context=lambda: ctx1, route_fn=_fake_route,
                                     research_question=_fake_research(outcomes_full),
                                     external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None),
                 RC.ResearchWorkerError, "公司 task 交给行业 Worker fail-closed")

    # 行业 Worker：来源分级诊断（external_db=None → 外部来源标 unknown）
    itask = _industry_task()
    iout = {
        "industry_definition": _outcome(
            "COMPLETED", "COMPLETED", qid="industry_definition",
            answer=_answer([_claim("i1", "公司属动力电池行业", "fact", [0])], [EXT]),
            external_snapshot_ids=["snap_1"]),
        "industry_scale_cycle": _outcome(
            "COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS", qid="industry_scale_cycle",
            answer=_answer([_claim("i2", "行业规模约 X 亿元（代理口径）", "inference", [0])], [EV]),
            evidence_ids=["ev_1"]),
    }
    iwr = IW.run_task(itask, company_id="300750", company_name="测试公司",
                      build_context=lambda: ctx1, route_fn=_fake_route,
                      research_question=_fake_research(iout),
                      external_db=None, harness_db=None, checkpoint=False, evidence_db=None, financial_db=None)
    check(iwr.section_result.section_id == "industry", "行业 section_id=industry")
    check(iwr.section_result.status == "COMPLETED_WITH_GAPS",
          f"行业 scale_cycle 代理缺口 → COMPLETED_WITH_GAPS（got {iwr.section_result.status}）")
    check(iwr.verification["source_grade_distribution"] == {"unknown": 1},
          f"来源分级诊断（external_db=None → unknown）（got {iwr.verification['source_grade_distribution']}）")
    check(iwr.verification["source_assessment"].get("grades") == {"unknown": 1},
          "行业 source_assessment 记录来源分级")

    # ---- J. 行业 A/B/C/D 来源规则（修复三）----
    a_src = ISP.assess_industry_sources([ISP.CitedSource("A", "https://stats.gov.cn/a", "2025-01-01")])
    check(a_src.key_conclusion_supported, "单个 A → 关键结论可支撑")
    two_c = ISP.assess_industry_sources([
        ISP.CitedSource("C", "https://eastmoney.com/a", "2025-01-01"),
        ISP.CitedSource("C", "https://thepaper.cn/b", "2025-02-01")])
    check(two_c.key_conclusion_supported and two_c.independent_c_count == 2,
          "两个独立 C → 关键结论可支撑")
    same_dom = ISP.assess_industry_sources([
        ISP.CitedSource("C", "https://eastmoney.com/a", "2025-01-01"),
        ISP.CitedSource("C", "https://eastmoney.com/b", "2025-02-01")])
    check(not same_dom.key_conclusion_supported and same_dom.independent_c_count == 1,
          "同域名两篇 C ≠ 两个独立 C")
    single_c = ISP.assess_industry_sources([ISP.CitedSource("C", "https://eastmoney.com/a", "2025-01-01")])
    check(not single_c.key_conclusion_supported and single_c.single_c_only,
          "单一 C → 仅有限非关键陈述")
    d_only = ISP.assess_industry_sources([ISP.CitedSource("D", "https://unknown.example/a", None)])
    check(not d_only.key_conclusion_supported and d_only.d_only, "D-only 不得支撑关键结论")
    unknown_date = ISP.assess_industry_sources([ISP.CitedSource("A", "https://stats.gov.cn/a", None)])
    check(not unknown_date.supports_strong_timepoint(), "published_at 未知不得支撑强时点")

    # apply_industry_source_policy 返回 3 元组：(processed_claims, extra_unresolved, assessment)。
    # 逐 claim 判定 + 强时点日期规则（关闭前定点修复一/二）。
    labels = {
        "a1": {"source_grade": "A", "canonical_url": "https://stats.gov.cn/a",
               "published_at": "2025-01-01"},
        "b1": {"source_grade": "B", "canonical_url": "https://ndrc.gov.cn/b",
               "published_at": "2025-01-01"},
        "c1": {"source_grade": "C", "canonical_url": "https://eastmoney.com/a",
               "published_at": "2025-01-01"},
        "c2": {"source_grade": "C", "canonical_url": "https://thepaper.cn/b",
               "published_at": "2025-01-01"},
        "c3": {"source_grade": "C", "canonical_url": "https://eastmoney.com/c",
               "published_at": "2025-01-01"},
        "d1": {"source_grade": "D", "canonical_url": "https://unknown.example/a",
               "published_at": None},
        "d_known": {"source_grade": "D", "canonical_url": "https://unknown.example/a",
                    "published_at": "2025-01-01"},
        "a_nodate": {"source_grade": "A", "canonical_url": "https://stats.gov.cn/a",
                     "published_at": None},
        "c_nodate": {"source_grade": "C", "canonical_url": "https://eastmoney.com/z",
                     "published_at": None},
    }

    def _ind_claim(claim_id, qid, topic, sids):
        return SS.SectionClaim(claim_id=claim_id, section_id="industry", topic_id=topic,
                               question_ids=(qid,), text="行业规模 X 亿元", claim_type="fact",
                               citation_refs=tuple(
                                   HS.CitationRef(ref_type="external", source_snapshot_id=s)
                                   for s in sids),
                               confidence="low", impact_scope=())

    # 强时点行业 task（freshness_policy 信号）
    q_strong = PS.PlannedQuestion("ind_strong_tp", "当前行业规模", "P0",
        "industry_scale_cycle",
        evidence_requirements=({"requirement_id": "r", "evidence_kind": "web",
                                "source_classes": ["external"], "minimum_sources": 1,
                                "freshness_policy": "near_1y"},))
    itask_strong = PS.SectionTask(
        task_id="task_ind_strong", plan_id="plan_1", section_id="industry",
        title="行业研究", purpose="credit_analysis", research_policy="harness",
        topic_ids=("industry_scale_cycle",), questions=(q_strong,),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions={})

    # J1. 单一 C 关键主题 → 移除（不升级为无限正式事实）
    kept1, extra1, assess1 = ISP.apply_industry_source_policy(
        (_ind_claim("k1", "industry_scale_cycle", "industry_scale_cycle", ["c1"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kept1) == 0, "单一 C 关键主题 → 从正式结论移除")
    check(len(extra1) == 1 and extra1[0].reason_code == "insufficient_industry_sources",
          "单一 C → insufficient_industry_sources unresolved")
    check(assess1["key_conclusion_supported"] is False, "assessment 记录不可支撑")

    # J2. D-only → 移除 + unresolved
    kept2, extra2, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k2", "industry_scale_cycle", "industry_scale_cycle", ["d1"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kept2) == 0 and extra2
          and extra2[0].reason_code == "insufficient_industry_sources",
          "D-only 关键主题 → 移除 + insufficient_industry_sources")

    # J3. 两个不同域 C → 保留
    kept3, extra3, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k3", "industry_scale_cycle", "industry_scale_cycle", ["c1", "c2"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kept3) == 1 and len(extra3) == 0, "两个不同域 C → 保留关键结论")

    # J4. 同域名两篇 C → 非独立 → 移除
    kept4, extra4, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k4", "industry_scale_cycle", "industry_scale_cycle", ["c1", "c3"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kept4) == 0 and extra4
          and extra4[0].reason_code == "insufficient_industry_sources",
          "同域名两篇 C → 移除（非独立）")

    # J5. 无关 A/B 不能支撑另一条 C-only claim（逐 claim 判定）
    kept5, extra5, _ = ISP.apply_industry_source_policy(
        (_ind_claim("ka", "industry_scale_cycle", "industry_scale_cycle", ["a1"]),
         _ind_claim("kc", "industry_position", "industry_scale_cycle", ["c1"]),),
        labels, itask, _context(), section_id="industry")
    check([c.claim_id for c in kept5] == ["ka"],
          "无关 A/B 不能支撑另一条 C-only claim（逐 claim 判定）")
    check(len(extra5) == 1 and extra5[0].reason_code == "insufficient_industry_sources",
          "C-only claim 独立移除")

    # J6. 强时点 + A 级但发布日期未知 → 不充分（移除）
    kept6, extra6, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k6", "ind_strong_tp", "industry_scale_cycle", ["a_nodate"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kept6) == 0, "A 级但日期未知的强时点 → 不充分（移除）")
    check(extra6 and extra6[0].reason_code == "strong_timepoint_published_at_unknown",
          "强时点日期未知 → strong_timepoint_published_at_unknown")

    # J7. 非强时点 + 日期未知 → 仅 soft 降级，保留
    kept7, extra7, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k7", "industry_scale_cycle", "industry_scale_cycle", ["a_nodate"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kept7) == 1 and len(extra7) == 0,
          "非强时点 A 级日期未知 → 仅 soft 降级，保留")

    # J7b. 强时点混合日期来源：未知日期来源不得贡献 A/B/C 数量或独立来源门槛。
    kmd1, emd1, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k8", "ind_strong_tp", "industry_scale_cycle", ["a_nodate", "d_known"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kmd1) == 0 and emd1
          and emd1[0].reason_code == "strong_timepoint_source_insufficient",
          "未知日期 A + 已知日期 D → 不得通过（移除）")

    kmd2, emd2, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k9", "ind_strong_tp", "industry_scale_cycle", ["a_nodate", "c1"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kmd2) == 0 and emd2
          and emd2[0].reason_code == "strong_timepoint_source_insufficient",
          "未知日期 A + 已知日期单一 C → 不得通过（移除）")

    kmd3, emd3, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k10", "ind_strong_tp", "industry_scale_cycle", ["a_nodate", "b1"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kmd3) == 1 and len(emd3) == 0,
          "未知日期 A + 已知日期 B → 可以通过（保留）")

    kmd4, emd4, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k11", "ind_strong_tp", "industry_scale_cycle", ["c1", "c2"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kmd4) == 1 and len(emd4) == 0,
          "两个已知日期、不同域 C → 可以通过（保留）")

    kmd5, emd5, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k12", "ind_strong_tp", "industry_scale_cycle", ["c_nodate", "c1"]),),
        labels, itask_strong, _context(), section_id="industry")
    check(len(kmd5) == 0 and emd5
          and emd5[0].reason_code == "strong_timepoint_source_insufficient",
          "一个未知日期 C + 一个已知日期 C → 不得按两个 C 通过（移除）")

    # J7c. 非强时点历史事实：未知日期来源仍按原规则处理（A 仍支撑关键结论）。
    kmd6, emd6, _ = ISP.apply_industry_source_policy(
        (_ind_claim("k13", "industry_scale_cycle", "industry_scale_cycle", ["a_nodate", "c1"]),),
        labels, itask, _context(), section_id="industry")
    check(len(kmd6) == 1 and len(emd6) == 0,
          "非强时点历史事实：未知日期 A 仍按原规则支撑（保留）")

    # J8. 默认三库路径（worker + research_common 签名）
    cw_def = inspect.signature(CW.run_task).parameters
    iw_def = inspect.signature(IW.run_task).parameters
    rc_def = inspect.signature(RC.run_task).parameters
    check((cw_def["evidence_db"].default, cw_def["financial_db"].default,
           cw_def["external_db"].default)
          == ("data/evidence.db", "data/financial_v2.db", "data/external_sources.db"),
          "公司 Worker 默认三库路径")
    check((iw_def["evidence_db"].default, iw_def["financial_db"].default,
           iw_def["external_db"].default)
          == ("data/evidence.db", "data/financial_v2.db", "data/external_sources.db"),
          "行业 Worker 默认三库路径")
    check((rc_def["evidence_db"].default, rc_def["financial_db"].default,
           rc_def["external_db"].default)
          == ("data/evidence.db", "data/financial_v2.db", "data/external_sources.db"),
          "research_common.run_task 默认三库路径")

    # J9. 缺库 fail-closed 且不创建（只读连接不建空库）
    _tmp_ro = Path(tempfile.mkdtemp(prefix="ca_ro_test_"))
    _missing_db = _tmp_ro / "nope.db"
    expect_raise(lambda: CA._ro_conn(_missing_db), FileNotFoundError,
                 "只读连接缺库 → FileNotFoundError")
    check(not _missing_db.exists(), "缺库不创建（不 init_db / 不迁移）")
    expect_raise(lambda: CA._ro_evidence_get(_missing_db, "e1"), FileNotFoundError,
                 "只读 evidence 查询缺库 → FileNotFoundError（不创建）")

    # J10. 权威失败原因持久化为 SectionUnresolved（修复四：不只剩 no_valid_claim）
    auth_snap_missing = _authority(snapshot_authority=lambda sid: None)
    out_s10 = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([_claim("cs10", "总资产 100 亿", "fact", [0])], [STR]),
        qid="company_debt_credit")
    cl_s10, ur_s10, _ = RC.convert_question_outcome(
        task, qmap["company_debt_credit"], out_s10, section_id="company",
        authority=auth_snap_missing)
    check(len(cl_s10) == 0 and ur_s10
          and ur_s10[0].reason_code == "citation_authority_failed",
          "引用未通过权威 → citation_authority_failed 持久化（非 no_valid_claim）")
    check("引用类型=structured" in ur_s10[0].detail
          and "snapshot_not_found" in ur_s10[0].detail,
          "detail 记录 ref_type + 失败原因（不含正文/敏感信息）")

    def _boom10(sid):
        raise RuntimeError("db missing")
    auth_boom10 = _authority(external_get=_boom10)
    out_q10 = _outcome("COMPLETED", "COMPLETED",
        answer=_answer([_claim("cq10", "行业数据", "fact", [0])], [EXT]),
        qid="company_business_main", external_snapshot_ids=["snap_1"])
    cl_q10, ur_q10, _ = RC.convert_question_outcome(
        task, q_business, out_q10, section_id="company", authority=auth_boom10)
    check(len(cl_q10) == 0 and ur_q10
          and ur_q10[0].reason_code == "authority_query_failed",
          "权威查询抛错 → authority_query_failed 持久化")
    check("引用类型=external" in ur_q10[0].detail, "detail 记录 ref_type")

    # J11. policy 处理后 claims/status/section_version/Markdown 一致（3 元组 hook）
    def _removing_policy(claims_, external_labels_, task_, context_, *, section_id):
        removed = claims_[-1] if claims_ else None
        kept = tuple(claims_[:-1])
        if removed is None:
            return kept, (), {}
        ur = SS.SectionUnresolved(
            unresolved_id="ur_policy_test", section_id=section_id,
            topic_id=removed.topic_id, question_id=removed.question_ids[0],
            state="NOT_FOUND_AFTER_SEARCH", reason_code="insufficient_industry_sources",
            detail="关键结论来源不足被移除", impact_scope=(), blocking_effects=(),
            attempted_sources=())
        return kept, (ur,), {"removed": 1}

    wr_pol = RC.run_task(task, section_id="company", topic_labels=CW._TOPIC_LABELS,
        renderer_version=CW.RENDERER_VERSION, rules_version=CW.RULES_VERSION,
        prompt_version=CW.PROMPT_VERSION, worker_version=CW.WORKER_VERSION,
        company_id="300750", company_name="测试公司",
        build_context=lambda: ctx1, route_fn=_fake_route,
        research_question=_fake_research(outcomes_full),
        external_db=None, harness_db=None, checkpoint=False,
        evidence_db=None, financial_db=None, source_policy=_removing_policy)
    check(wr_pol.section_result.status == "COMPLETED_WITH_GAPS",
          f"policy 移除 claim → COMPLETED_WITH_GAPS（got {wr_pol.section_result.status}）")
    check(wr_pol.claim_count == 4, f"policy 后 claim_count=4（got {wr_pol.claim_count}）")
    check(wr_pol.unresolved_count == 1, "policy 追加 1 条 unresolved")
    check("授信额度 50 亿元" not in wr_pol.section_result.markdown,
          "被移除 claim 文本不再出现在 Markdown")
    check("关键结论来源不足被移除" in wr_pol.section_result.markdown,
          "unresolved 进入 Markdown")
    check(wr_pol.section_result.section_version != wr1.section_result.section_version,
          "policy 处理改变 section_version")
    check(any(u.reason_code == "insufficient_industry_sources"
              for u in wr_pol.section_result.unresolved),
          "policy unresolved 进入 SectionResult.unresolved")

    # ---- K. build_external_labels 只读 + 强时点混合日期（关闭前定点修复）----
    # K1. 只读 label 查询：缺失库不创建、不修改 store._db_path、诊断明确。
    _tmp_ext = Path(tempfile.mkdtemp(prefix="ext_label_test_"))
    _missing_ext = _tmp_ext / "missing_external.db"
    check(not _missing_ext.exists(), "缺失外部库调用前不存在")
    _db_path_before = extstore._db_path
    labels_miss, diag_miss = RC.build_external_labels(["snap_1"], str(_missing_ext))
    check(labels_miss == {}, "缺失外部库降级为 {}（不产生伪 label）")
    check(diag_miss["status"] == "missing_db", "缺失外部库 diagnostic=missing_db")
    check(not _missing_ext.exists(), "缺失外部库调用后仍未创建（不 init_db / 不迁移）")
    check(extstore._db_path == _db_path_before, "调用后 external_v2.store._db_path 不变")

    # K2. 健康库读取 label + diagnostic ok。
    _healthy_ext = _tmp_ext / "healthy_external.db"
    _hconn = sqlite3.connect(str(_healthy_ext))
    _hconn.executescript(extstore.build_ddl())
    _hconn.execute(
        "INSERT INTO source_snapshots (source_snapshot_id, company_id, canonical_url, "
        "original_url, provider, query, title, snippet, published_at, fetched_at, "
        "content_type, http_status, content_text, content_hash, source_grade, "
        "content_version, status, error_code, retrieval_metadata, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("snap_1", "", "https://stats.gov.cn/x", "https://stats.gov.cn/x", "web", "q",
         "某政府网", "摘要", "2025-01-01", "2025-06-01", "text/html", 200,
         "正文内容，非 URL 非 snippet", "hash123", "A", 1, "SNAPSHOTTED", None, "{}",
         "2025-01-01T00:00:00Z"))
    _hconn.commit()
    _hconn.close()
    labels_ok, diag_ok = RC.build_external_labels(["snap_1"], str(_healthy_ext))
    check(labels_ok.get("snap_1", {}).get("source_grade") == "A", "健康库读取 source_grade=A")
    check(labels_ok.get("snap_1", {}).get("name") == "某政府网", "健康库 title → name")
    check(diag_ok["status"] == "ok" and diag_ok["resolved"] == 1, "健康库 diagnostic ok")

    # K3. 缺表 / 损坏库：不崩溃、不产生伪 label、诊断明确。
    _notable_ext = _tmp_ext / "no_table.db"
    _nconn = sqlite3.connect(str(_notable_ext))
    _nconn.execute("CREATE TABLE unrelated (id INTEGER)")
    _nconn.commit()
    _nconn.close()
    labels_nt, diag_nt = RC.build_external_labels(["snap_1"], str(_notable_ext))
    check(labels_nt == {}, "缺表库不产生伪 label")
    check(diag_nt["status"] == "missing_table_or_corrupt", "缺表库 diagnostic")

    _corrupt_ext = _tmp_ext / "corrupt.db"
    _corrupt_ext.write_bytes(b"this is definitely not a sqlite database file...")
    labels_corr, diag_corr = RC.build_external_labels(["snap_1"], str(_corrupt_ext))
    check(labels_corr == {}, "损坏库不产生伪 label")
    check(diag_corr["status"] in ("missing_table_or_corrupt", "corrupt"),
          "损坏库 diagnostic（不崩溃）")

    # K4. label 查询失败 → 行业来源等级按 unknown fail-closed（不因缺库放行关键 Claim）。
    labels_fail, _ = RC.build_external_labels(["snap_1"], str(_missing_ext))
    check(labels_fail.get("snap_1", {}).get("source_grade", "unknown") == "unknown",
          "label 查询失败 → source_grade unknown")
    kept_fail, extra_fail, _ = ISP.apply_industry_source_policy(
        (_ind_claim("kf", "industry_scale_cycle", "industry_scale_cycle", ["snap_1"]),),
        labels_fail, itask, _context(), section_id="industry")
    check(len(kept_fail) == 0 and extra_fail
          and extra_fail[0].reason_code == "insufficient_industry_sources",
          "label 查询失败 → unknown → 关键 Claim 不因此放行")

    # ---- H. Store 原子提交 ----
    tmpdir = Path(tempfile.mkdtemp(prefix="research_worker_test_"))
    sections_db = tmpdir / "sections.db"
    plan = _plan("company", task)
    ST.init_db(sections_db)
    ST.commit_plan(plan, run_id="run_r")
    r1 = ST.commit_section_result(wr1.section_result, run_id="run_r")
    check(r1.reused is False and r1.current_switched is True,
          "首次 commit 不 reuse 且切 current")
    check(ST.get_current_section(task.task_id) == wr1.section_result.section_result_id,
          "current_section 指向新产物")
    r2 = ST.commit_section_result(wr1.section_result, run_id="run_r2")
    check(r2.reused is True and r2.current_switched is False,
          "重复 commit 复用且不切 current")

    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
