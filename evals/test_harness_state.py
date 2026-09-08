"""Eval: harness 状态机 + 成功判定 + 答案引用校验 —— Phase 3 Batch B commit 3。

用法: python -m evals.test_harness_state

断言（纯逻辑，无 I/O / LLM / 工具执行）：
- set_status 合法/非法（9 态、18 停止因，非法 fail-closed）；
- validate_answer：缺答案 / 空文本 / 无 claim / claim 无引用 / 引用越界 /
  三类引用可回查性（evidence / structured / external）；
- evaluate_success：未路由 → NOT_IMPLEMENTED；无答案 → FAILED；含 unresolved →
  COMPLETED_WITH_GAPS（不计严格成功）；全通过 → COMPLETED（success）；
- is_sufficient / has_citable_material。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as H
from harness import state as S
from routing import schema as RS


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _router_result(route: str = "DIRECT_EVIDENCE") -> RS.RouterResult:
    budget = RS.RetrievalBudget(candidate_k_sparse=20, candidate_k_dense=20,
                                fusion_k=10, context_k=5, timeout_ms=5000)
    decision = RS.RouteDecision(
        need_id="n1", route=route, reason_code="EXACT_DOCUMENT_FIELD", filters={},
        budget=budget, fallback_routes=[], decided_by="rule",
        rule_version=RS.RULE_VERSION, confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                           trace_id="t1")


def _structured_ref() -> RS.StructuredResultRef:
    return RS.StructuredResultRef(
        result_type="financial_field", snapshot_id="snap1", item_code="ITEM_A",
        formula_id=None, formula_version=None, period="2024-12-31",
        raw_value="1.0", display_value="1.0", unit="yuan", status="SUCCESS",
        reason_code=None, input_record_refs=[], input_snapshot_item_refs=[])


def _state(**overrides) -> H.ResearchState:
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="300750",
                         section_id="company", original_question="q", need=_need())
    st.route_result = _router_result()
    for k, v in overrides.items():
        setattr(st, k, v)
    return st


def _answer(citations: list[H.CitationRef],
            unresolved: list[str] | None = None) -> H.ResearchAnswer:
    claims = [H.Claim(claim_id="c1", text="实控人为曾毓群", kind="fact",
                      citation_refs=[0])]
    return H.ResearchAnswer(question_id="q1", answer_text="实控人为曾毓群",
                            claims=claims, citations=citations,
                            unresolved_items=unresolved or [],
                            completion_status="COMPLETED")


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

    def raises(exc_type, fn):
        try:
            fn()
            return False
        except exc_type:
            return True

    # ---- set_status ----
    st = _state()
    S.set_status(st, "COMPLETED", "COMPLETED")
    check(st.status == "COMPLETED" and st.stop_reason == "COMPLETED",
          "set_status：合法状态+停止因")
    check(raises(H.HarnessValidationError, lambda: S.set_status(_state(), "BOGUS")),
          "set_status：非法状态 fail-closed")
    check(raises(H.HarnessValidationError,
                 lambda: S.set_status(_state(), "FAILED", "BOGUS_REASON")),
          "set_status：非法 stop_reason fail-closed")

    # ---- validate_answer 结构 ----
    check(S.validate_answer(None, _state()) == ["answer_missing"],
          "validate_answer：None → answer_missing")
    check("empty_answer_text" in S.validate_answer(
        H.ResearchAnswer(question_id="q1", answer_text=""), _state()),
        "validate_answer：空文本 → empty_answer_text")
    no_claims = H.ResearchAnswer(question_id="q1", answer_text="x")
    check("no_claims" in S.validate_answer(no_claims, _state()),
          "validate_answer：无 claim → no_claims")
    claim_no_cit = H.ResearchAnswer(
        question_id="q1", answer_text="x",
        claims=[H.Claim(claim_id="c1", text="t", kind="fact", citation_refs=[])])
    check(any("无引用" in e for e in S.validate_answer(claim_no_cit, _state())),
          "validate_answer：claim 无引用报错")
    oob = H.ResearchAnswer(
        question_id="q1", answer_text="x",
        claims=[H.Claim(claim_id="c1", text="t", kind="fact", citation_refs=[5])],
        citations=[H.CitationRef(ref_type="evidence", evidence_id="e1")])
    check(any("越界" in e for e in S.validate_answer(oob, _state())),
          "validate_answer：引用下标越界报错")

    # ---- validate_answer 引用可回查 ----
    ev_ok = _state(evidence_ids=["e1"])
    check(S.validate_answer(_answer([H.CitationRef(ref_type="evidence", evidence_id="e1")]),
                            ev_ok) == [],
          "validate_answer：evidence 引用可回查")
    ev_bad = _state(evidence_ids=["e2"])
    check(any("不在已取得材料" in e for e in S.validate_answer(
        _answer([H.CitationRef(ref_type="evidence", evidence_id="e1")]), ev_bad)),
        "validate_answer：evidence 引用不可回查报错")

    st_ok = _state(structured_refs=[_structured_ref()])
    structured_cit = H.CitationRef(ref_type="structured", snapshot_id="snap1",
                                   item_code="ITEM_A", period="2024-12-31")
    check(S.validate_answer(_answer([structured_cit]), st_ok) == [],
          "validate_answer：structured(item) 引用可回查")
    st_mismatch = _state(structured_refs=[_structured_ref()])
    bad_structured = H.CitationRef(ref_type="structured", snapshot_id="snap1",
                                   item_code="ITEM_B", period="2024-12-31")
    check(any("无法匹配" in e for e in S.validate_answer(
        _answer([bad_structured]), st_mismatch)),
        "validate_answer：structured 引用无法匹配报错")

    ext_ok = _state(external_snapshot_ids=["ext1"])
    check(S.validate_answer(_answer([H.CitationRef(ref_type="external",
                                                   source_snapshot_id="ext1")]),
                            ext_ok) == [],
          "validate_answer：external 引用可回查")
    ext_bad = _state(external_snapshot_ids=["ext2"])
    check(any("不在已取得快照" in e for e in S.validate_answer(
        _answer([H.CitationRef(ref_type="external", source_snapshot_id="ext1")]),
        ext_bad)),
        "validate_answer：external 引用不可回查报错")

    # ---- evaluate_success ----
    no_route = _state()
    no_route.route_result = None
    r = S.evaluate_success(no_route, _answer([H.CitationRef(
        ref_type="evidence", evidence_id="e1")]))
    check(r["completion_status"] == "NOT_IMPLEMENTED" and r["success"] is False,
          "evaluate_success：未路由 → NOT_IMPLEMENTED")

    r = S.evaluate_success(_state(), None)
    check(r["completion_status"] == "FAILED" and r["success"] is False,
          "evaluate_success：无答案 → FAILED")

    st_gap = _state(structured_refs=[_structured_ref()])
    r = S.evaluate_success(st_gap, _answer([structured_cit], unresolved=["缺变更时间"]))
    check(r["completion_status"] == "COMPLETED_WITH_GAPS" and r["success"] is False,
          "evaluate_success：unresolved → COMPLETED_WITH_GAPS（不计成功）")

    st_ok2 = _state(structured_refs=[_structured_ref()])
    r = S.evaluate_success(st_ok2, _answer([structured_cit]))
    check(r["completion_status"] == "COMPLETED" and r["success"] is True,
          "evaluate_success：全通过 → COMPLETED")

    # ---- is_sufficient / has_citable_material ----
    check(S.is_sufficient(st_ok2, _answer([structured_cit])) is True,
          "is_sufficient：充分")
    check(S.is_sufficient(st_gap, _answer([structured_cit], unresolved=["x"])) is False,
          "is_sufficient：含 unresolved 不充分")
    check(S.has_citable_material(_state(structured_refs=[_structured_ref()])) is True,
          "has_citable_material：有结构化引用")
    check(S.has_citable_material(_state()) is False, "has_citable_material：无材料")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
