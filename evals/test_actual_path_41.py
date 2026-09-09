"""Eval: 41 问 Actual-Path Runner 纯函数层 —— Phase 3 Batch B commit 5。

用法: python -m evals.test_actual_path_41

断言（无 LLM / 无工具 / 无网络；只测可独立验证的纯函数）：
- _build_need：从 case 构造 InformationNeed（只取 question 原文，不碰 gold_answer）；
- classify_completion：五态映射 + 路由级确定性下限（EXTERNAL 无快照 / DB 无结构化 /
  本地无证据 → PARTIAL；有材料 → FULL）；
- manifest_mismatches：RunManifest 全量一致 → 空；任一字段漂移 → 列出该字段（fail-closed）；
- _build_run_manifest：确定性指纹（dataset sha / prompt sha / harness sha 稳定）；
- _diagnose_local_pages：gold 本地页与已取得 Evidence 页的离线诊断（仅诊断）；
- _aggregate：完成/路由/来源覆盖/预算停止等计数正确。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_actual_path_41 as R
from evaluation.schema import (
    GoldEvidenceGroup,
    GoldEvidenceTarget,
    RetrievalEvalCase,
)
from harness import checkpoint as C
from harness import schema as H
from harness import policies as P
from routing import schema as RS


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _case(case_id="COMP-S1", question="成立时间？", priority="P0",
          time_scope="2025", targets=None, section_id="company",
          expected_route_v2="DIRECT_EVIDENCE") -> RetrievalEvalCase:
    return RetrievalEvalCase(
        case_id=case_id, company_id="300750", section_id=section_id,
        question=question, expected_route_raw="STRUCTURED",
        expected_route_v2=expected_route_v2, priority=priority,
        time_scope=time_scope, gold_evidence_raw={}, notes="",
        gold_answer=None,
        gold_evidence_groups=targets or [],
    )


def _local_targets() -> list[GoldEvidenceGroup]:
    return [GoldEvidenceGroup(
        group_id="local__D", requirement="all", channel="local",
        targets=[GoldEvidenceTarget(
            document_id="D", pdf_page=36, mapping_status="verified",
            source_note="P36", mapping_note="pdf 36")])]


def _state(route="DIRECT_EVIDENCE", *, evidence=None, structured=None,
           external=None) -> H.ResearchState:
    need = RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])
    st = H.ResearchState(run_id="r", case_id="c", question_id="n1",
                         company_id="300750", section_id="company",
                         original_question="q", need=need)
    budget = RS.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1,
                                fusion_k=1, context_k=1, timeout_ms=1000)
    decision = RS.RouteDecision(
        need_id="n1", route=route, reason_code="TEST", filters={}, budget=budget,
        fallback_routes=[], decided_by="rule", rule_version=RS.RULE_VERSION,
        confidence="high")
    st.route_result = RS.RouterResult(status="DECIDED", decision=decision,
                                      error_code=None, trace_id="t")
    st.evidence_ids = evidence or []
    st.external_snapshot_ids = external or []
    st.structured_refs = structured or []
    return st


def _outcome(cs, route="DIRECT_EVIDENCE", *, evidence=None, structured=None,
             external=None) -> H.ResearchOutcome:
    st = _state(route, evidence=evidence, structured=structured, external=external)
    return H.ResearchOutcome(state=st, answer=None, success=(cs == "COMPLETED"),
                             completion_status=cs, stop_reason=cs)


def _manifest(**overrides) -> C.RunManifest:
    base = dict(
        run_id="r1", dataset_sha256="d", company_id="300750", report_as_of=None,
        contract_version="v1", router_fingerprint="rf",
        prompt_versions={"a": "1"}, model="deepseek-v4-pro", budget={"max_tokens": 8000},
        evidence_fingerprint="ef", snapshot_id=None, external_policy_version="v1",
        harness_fingerprint="hf")
    base.update(overrides)
    return C.RunManifest(**base)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

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

    # ---- _build_need ----
    need = R._build_need(_case(question="成立时间？", time_scope="2025", priority="P1"))
    check(need.need_id == "COMP-S1" and need.question == "成立时间？"
          and need.time_scope == "2025" and need.priority == "P1"
          and need.section_id == "company", "_build_need：只取 question 原文 + 元数据")

    # ---- classify_completion：五态映射 ----
    check(R.classify_completion(_outcome("COMPLETED"), "DIRECT_EVIDENCE") == "PARTIAL",
          "本地路由 COMPLETED 但无证据 → PARTIAL（确定性下限）")
    check(R.classify_completion(_outcome("NOT_IMPLEMENTED"), None) == "NOT_IMPLEMENTED",
          "NOT_IMPLEMENTED → NOT_IMPLEMENTED")
    check(R.classify_completion(_outcome("FAILED"), "DIRECT_EVIDENCE") == "FAILED",
          "FAILED → FAILED")
    check(R.classify_completion(_outcome("UNRESOLVED"), "DIRECT_EVIDENCE") == "UNRESOLVED",
          "UNRESOLVED → UNRESOLVED")
    check(R.classify_completion(_outcome("COMPLETED_WITH_GAPS"), "DIRECT_EVIDENCE") == "PARTIAL",
          "COMPLETED_WITH_GAPS → PARTIAL")

    # ---- 路由级确定性下限 ----
    check(R.classify_completion(_outcome("COMPLETED", "EXTERNAL_RESEARCH",
                                         external=["snap1"]), "EXTERNAL_RESEARCH") == "FULL",
          "EXTERNAL + 有快照 → FULL")
    check(R.classify_completion(_outcome("COMPLETED", "EXTERNAL_RESEARCH"),
                                "EXTERNAL_RESEARCH") == "PARTIAL",
          "EXTERNAL + 无快照 → PARTIAL（snippet 不算完整）")
    check(R.classify_completion(_outcome("COMPLETED", "DB_LOOKUP"),
                                "DB_LOOKUP") == "PARTIAL",
          "DB_LOOKUP + 无结构化结果 → PARTIAL")
    check(R.classify_completion(_outcome("COMPLETED", "DIRECT_EVIDENCE",
                                         evidence=["e1"]), "DIRECT_EVIDENCE") == "FULL",
          "本地 + 有证据 → FULL")

    # ---- manifest_mismatches ----
    check(R.manifest_mismatches(_manifest(), _manifest()) == [],
          "manifest 全量一致 → 空 mismatches")
    m2 = _manifest(dataset_sha256="CHANGED")
    check(R.manifest_mismatches(_manifest(), m2) == ["dataset_sha256"],
          "dataset_sha256 漂移 → 列出该字段（fail-closed）")

    # ---- _build_run_manifest 确定性 ----
    with tempfile.TemporaryDirectory() as td:
        ev = str(Path(td) / "ev.db")
        fin = str(Path(td) / "fin.db")
        ctx = RS.RouteContext(
            company_id="300750", report_as_of=None, available_document_ids=[],
            available_source_types=[], supported_db_fields=[],
            supported_metric_ids=[], available_db_fields=[],
            available_metric_ids=[], external_research_enabled=True)
        m = R._build_run_manifest(
            run_id="r2", dataset_path="evaluation/datasets/v1_baseline.jsonl",
            company_id="300750", report_as_of=None, model="deepseek-v4-pro",
            budget=P.DEFAULT_BUDGET.as_dict(), context=ctx,
            ev_db_path=ev, fin_db_path=fin)
        check(m.run_id == "r2" and m.contract_version == H.HARNESS_VERSION
              and m.router_fingerprint == RS.RULE_VERSION
              and m.snapshot_id is None, "_build_run_manifest：字段填充 + 空态 snapshot None")
        check(len(m.prompt_versions) == 2
              and all(len(v) == 64 for v in m.prompt_versions.values()),
              "prompt 指纹为 sha256（64 hex）")
        check(len(m.harness_fingerprint) == 64 and len(m.evidence_fingerprint) == 64,
              "harness/evidence 指纹为 sha256")
        # 确定性：同输入两次结果一致。
        m2 = R._build_run_manifest(
            run_id="r2", dataset_path="evaluation/datasets/v1_baseline.jsonl",
            company_id="300750", report_as_of=None, model="deepseek-v4-pro",
            budget=P.DEFAULT_BUDGET.as_dict(), context=ctx,
            ev_db_path=ev, fin_db_path=fin)
        check(m == m2, "_build_run_manifest 确定性（同输入同指纹）")

    # ---- _diagnose_local_pages ----
    case = _case(targets=_local_targets())
    check(R._diagnose_local_pages(_case(targets=[]), set()) == {"applicable": False},
          "无本地 gold 组 → applicable False")
    diag = R._diagnose_local_pages(case, {("D", 36)})
    check(diag["applicable"] is True and diag["n_required_pages"] == 1
          and diag["required_page_coverage"] == 1.0 and diag["page_hit"] is True,
          "命中 gold 页 → coverage 1.0 / page_hit True")
    diag2 = R._diagnose_local_pages(case, {("D", 10)})
    check(diag2["required_page_coverage"] == 0.0 and diag2["page_hit"] is False,
          "未命中 → coverage 0.0 / page_hit False")

    # ---- _aggregate ----
    rec_full = {
        "case_id": "A", "priority": "P0",
        "route": {"route": "DIRECT_EVIDENCE"},
        "completion": {"actual_status": "FULL", "stop_reason": "COMPLETED"},
        "evidence_ids": ["e1"], "structured_refs": [], "external_snapshot_ids": [],
        "tool_calls": [{"tool": "search_evidence", "status": "SUCCESS", "error_code": None,
                        "elapsed_ms": 10, "auto": False}],
        "usage": {"llm_calls": 2, "tool_calls": 1, "local_searches": 1,
                  "external_searches": 0, "fetches": 0, "snapshots": 0,
                  "input_tokens": 100, "output_tokens": 50,
                  "usage_unknown_calls": 0, "elapsed_ms": 500},
        "page_diagnosis": {"applicable": True, "required_page_coverage": 1.0,
                           "page_hit": True},
    }
    rec_gap = {
        "case_id": "B", "priority": "P1",
        "route": {"route": "EXTERNAL_RESEARCH"},
        "completion": {"actual_status": "PARTIAL", "stop_reason": "BUDGET_EXTERNAL"},
        "evidence_ids": [], "structured_refs": [], "external_snapshot_ids": [],
        "tool_calls": [{"tool": "search_external_sources", "status": "EMPTY",
                        "error_code": "RETRIEVAL_EMPTY", "elapsed_ms": 20, "auto": False}],
        "usage": {"llm_calls": 1, "tool_calls": 1, "local_searches": 0,
                  "external_searches": 1, "fetches": 0, "snapshots": 0,
                  "input_tokens": 80, "output_tokens": 40,
                  "usage_unknown_calls": 0, "elapsed_ms": 900},
        "page_diagnosis": {"applicable": False},
    }
    agg = R._aggregate([rec_full, rec_gap])
    check(agg["n_cases"] == 2, "_aggregate：n_cases=2")
    check(agg["completion_distribution"]["FULL"] == 1
          and agg["completion_distribution"]["PARTIAL"] == 1, "完成分布 FULL=1 PARTIAL=1")
    check(agg["p0_completion_distribution"]["FULL"] == 1, "P0 单列 FULL=1")
    check(agg["route_distribution"] == {"DIRECT_EVIDENCE": 1, "EXTERNAL_RESEARCH": 1},
          "路由分布正确")
    check(agg["source_coverage"]["n_with_evidence"] == 1
          and agg["source_coverage"]["n_with_external"] == 0, "来源覆盖正确")
    check(agg["tool_empty"] == 1 and agg["budget_stops"] == 1, "空结果 + 预算停止计数")
    check(agg["latency_ms"]["p50"] == 900 and agg["tokens"]["avg"] == 135.0,
          "耗时 p50 + token avg")

    # ---- _record_case 新字段（修订①③④：aspects / entailment / 去重审计）----
    st = _state("DIRECT_EVIDENCE", evidence=["e1"])
    st.required_aspects = [
        {"aspect_id": "a1", "text": "主营业务构成", "source": "DATASET_MAPPING"},
        {"aspect_id": "a2", "text": "各业务收入及收入占比", "source": "DATASET_MAPPING"},
    ]
    st.aspect_source = "DATASET_MAPPING"
    st.inspected_evidence = {
        "e1": H.InspectedMaterial(
            evidence_id="e1", document_id="d1", source_name="年报",
            page_number=36, report_period="2024-12-31", text="动力电池收入占比 65%",
            is_snippet=False),
    }
    st.entailment_verdicts = [
        H.EntailmentVerdict(claim_id="c1", citation_ids=["0"], verdict="SUPPORTED",
                            reason="证据正文含具体占比", scope_consistency="consistent",
                            period_consistency="consistent", unit_consistency="consistent",
                            subject_consistency="consistent"),
    ]
    st.unsupported_claims = []
    st.entailment_evaluator_failed = False
    st.rejected_duplicate_actions = [
        {"round": 1, "action": "INSPECT_EVIDENCE", "tool": "inspect_evidence",
         "key": "k1", "source": "model_proposed"},
    ]
    ans = H.ResearchAnswer(
        question_id="n1", answer_text="动力电池收入占比 65%",
        claims=[H.Claim(claim_id="c1", text="动力电池收入占比 65%", kind="fact",
                        citation_refs=[0])],
        citations=[H.CitationRef(ref_type="evidence", evidence_id="e1", page_number=36)],
        aspects=[H.AspectAnswer(aspect_id="a1", text="动力电池", claim_ids=["c1"]),
                 H.AspectAnswer(aspect_id="a2", text="占比 65%", claim_ids=["c1"])],
        confidence="high")
    outcome = H.ResearchOutcome(state=st, answer=ans, success=True,
                                completion_status="COMPLETED", stop_reason="COMPLETED")
    rec = R._record_case(_case(case_id="COMP-R1", question="主营业务及收入占比？"),
                         st.route_result, outcome, {("d1", 36)})
    check(rec["required_aspects"] == st.required_aspects
          and rec["aspect_source"] == "DATASET_MAPPING",
          "_record_case：required_aspects + aspect_source")
    check(len(rec["aspects"]) == 2 and rec["aspects"][0]["answered"] is True
          and rec["aspects"][1]["has_number"] is True,
          "_record_case：逐 aspect 覆盖 + 数值方面有数字")
    check(rec["uncovered_aspects"] == [], "_record_case：无未覆盖方面")
    check(len(rec["entailment_verdicts"]) == 1
          and rec["entailment_verdicts"][0]["verdict"] == "SUPPORTED",
          "_record_case：entailment 判定")
    check(rec["answer"]["claims"][0]["entailment"]["verdict"] == "SUPPORTED"
          and rec["answer"]["claims"][0]["evidence_summary"][0]["text"] == "动力电池收入占比 65%",
          "_record_case：claim 挂 entailment + 证据正文摘要")
    check(rec["entailment_evaluator_failed"] is False
          and len(rec["rejected_duplicate_actions"]) == 1,
          "_record_case：entailment 失败标记 + 去重审计")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
