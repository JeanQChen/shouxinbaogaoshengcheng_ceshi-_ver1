"""Eval: harness checkpoint（RunManifest + 题目级 outcome SQLite 持久化）—— commit 3。

用法: python -m evals.test_harness_checkpoint

断言（纯 SQLite，无 LLM / 工具执行）：
- RunManifest as_dict ↔ manifest_from_dict 往返；
- init_db + write_run_manifest + get_run_manifest 往返；
- write_question_outcome 落盘 + load_outcome 读取 JSON；
- list_completed_questions 只返回终态（COMPLETED/COMPLETED_WITH_GAPS/BLOCKED/FAILED/
  WAITING_HUMAN），排除非终态（RESEARCHING）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import checkpoint as C
from harness import schema as H
from routing import schema as RS


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _router_result() -> RS.RouterResult:
    budget = RS.RetrievalBudget(candidate_k_sparse=20, candidate_k_dense=20,
                                fusion_k=10, context_k=5, timeout_ms=5000)
    decision = RS.RouteDecision(
        need_id="n1", route="DIRECT_EVIDENCE", reason_code="EXACT_DOCUMENT_FIELD",
        filters={}, budget=budget, fallback_routes=[], decided_by="rule",
        rule_version=RS.RULE_VERSION, confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                           trace_id="t1")


def _state(status: str = "COMPLETED") -> H.ResearchState:
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="300750",
                         section_id="company", original_question="q", need=_need())
    st.route_result = _router_result()
    st.status = status
    return st


def _outcome(status: str = "COMPLETED") -> H.ResearchOutcome:
    st = _state(status)
    ans = H.ResearchAnswer(question_id="q1", answer_text="x",
                           claims=[H.Claim(claim_id="c1", text="t", kind="fact",
                                           citation_refs=[0])],
                           citations=[H.CitationRef(ref_type="evidence", evidence_id="e1")],
                           completion_status="COMPLETED")
    return H.ResearchOutcome(state=st, answer=ans, success=True,
                             completion_status="COMPLETED", stop_reason="COMPLETED")


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

    # ---- RunManifest 往返 ----
    m = C.RunManifest(
        run_id="r1", dataset_sha256="d", company_id="300750", report_as_of=None,
        contract_version="v1", router_fingerprint="rf", prompt_versions={"a": "1"},
        model="deepseek-v4-pro", budget={"max_tokens": 8000}, evidence_fingerprint="ef",
        snapshot_id=None, external_policy_version="v1", harness_fingerprint="hf")
    back = C.manifest_from_dict(m.as_dict())
    check(back == m, "RunManifest as_dict ↔ manifest_from_dict 往返相等")

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "harness.db"

        # ---- manifest 落盘/读取 ----
        C.init_db(db)
        C.write_run_manifest(m, db)
        got = C.get_run_manifest("r1", db)
        check(got is not None and got.run_id == "r1" and got.company_id == "300750"
              and got.prompt_versions == {"a": "1"}, "manifest 落盘后读回一致")
        check(C.get_run_manifest("missing", db) is None, "不存在 run → None")

        # ---- outcome 落盘/读取 ----
        C.write_question_outcome("r1", _outcome(), db)
        raw = C.load_outcome("r1", "q1", db)
        check(raw is not None and raw["success"] is True
              and raw["completion_status"] == "COMPLETED"
              and raw["state"]["question_id"] == "q1", "outcome 落盘后读回 JSON")
        check(C.load_outcome("r1", "q_missing", db) is None, "不存在题 → None")

        # ---- list_completed_questions ----

        def _with_qid(qid: str, status: str) -> H.ResearchOutcome:
            o = _outcome(status)
            o.state.question_id = qid
            return o

        C.write_question_outcome("r1", _with_qid("q_gap", "COMPLETED_WITH_GAPS"), db)
        C.write_question_outcome("r1", _with_qid("q_blocked", "BLOCKED"), db)
        C.write_question_outcome("r1", _with_qid("q_researching", "RESEARCHING"), db)
        done = C.list_completed_questions("r1", db)
        check("q1" in done and "q_gap" in done and "q_blocked" in done,
              "终态 COMPLETED / COMPLETED_WITH_GAPS / BLOCKED 被返回")
        check("q_researching" not in done, "非终态 RESEARCHING 被排除")
        check(len(done) == 3, "仅 3 个终态被返回")

        # 另造一个 FAILED 终态题，验证多题集合。
        C.write_question_outcome("r1", _with_qid("q2", "FAILED"), db)
        done2 = C.list_completed_questions("r1", db)
        check(done2 == {"q1", "q_gap", "q_blocked", "q2"}, "多题终态集合 = 4 题")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
