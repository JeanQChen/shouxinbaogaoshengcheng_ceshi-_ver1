"""Eval: active_snapshot_id 锁定 + resume 快照一致 —— Phase 3 Batch B Change 2/3。

用法: python -m evals.test_harness_snapshot_lock

断言（纯逻辑 + mock LLM/fake Registry，无真实 Store/网络）：
- active_snapshot_id 是 run 级冻结输入，_reset_answer_derived_state 不清除/不重写；
- run_question 注入 RouteContext 时，active_snapshot_id 由 context.snapshot_id 一次性锁定，
  ANSWER 评估（含 _reset_answer_derived_state）之后保持不变；
- context 为 None（无真实快照路径）时 active_snapshot_id 保持 None；
- resume 时 RunManifest.snapshot_id 不一致 → manifest_mismatches 列出 snapshot_id（fail-closed）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_actual_path_41 as RUN
from harness import checkpoint as C
from harness import runtime as RT
from harness import schema as H
from llm import client as llm_client
from routing import schema as RS
from tools import contracts as TC
from tools import registry as REG


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _router_result(route: str) -> RS.RouterResult:
    budget = RS.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1,
                                fusion_k=1, context_k=1, timeout_ms=1000)
    decision = RS.RouteDecision(
        need_id="n1", route=route, reason_code="TEST", filters={}, budget=budget,
        fallback_routes=[], decided_by="rule", rule_version=RS.RULE_VERSION,
        confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                           trace_id="t")


def _context(snapshot_id: str | None = "S1") -> RS.RouteContext:
    return RS.RouteContext(
        company_id="300750", report_as_of="2025-12-31",
        available_document_ids=[], available_source_types=[],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=False,
        scope="consolidated", currency="CNY", purpose="credit_analysis",
        available_periods=["2024-12-31", "2025-12-31"],
        snapshot_id=snapshot_id)


def _spec(name, required, props, routes):
    return TC.ToolSpec(
        name=name, version="v1", description="fake",
        input_schema={"type": "object", "additionalProperties": False,
                      "required": required, "properties": props},
        output_schema={"type": "object"}, allowed_routes=routes,
        max_results=10, timeout_ms=1000, retry_policy="none", cost_class="local")


def _fake_registry() -> REG.ToolRegistry:
    audit = Path(tempfile.mkdtemp())
    reg = REG.ToolRegistry(audit_dir=audit)
    reg.register(_spec("search_evidence", ["company_id", "query"],
                       {"company_id": {"type": "string"}, "query": {"type": "string"},
                        "k": {"type": "integer"}},
                       ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL")),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="search_evidence", tool_version="v1",
                     status="SUCCESS", data={"evidence_count": 1, "items": [
                         {"evidence_id": "e1", "source_name": "s", "page_number": 3,
                          "evidence_type": "paragraph", "snippet": "实际控制人为曾毓群",
                          "score": 0.9, "rank": 1}]},
                     evidence_ids=["e1"], error_code=None, message=None,
                     retryable=False, trace_id="t"))
    reg.register(_spec("inspect_evidence", ["evidence_id"],
                       {"evidence_id": {"type": "string"}},
                       ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL")),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="inspect_evidence", tool_version="v1",
                     status="SUCCESS",
                     data={"evidence_id": "e1", "document_id": "d1",
                           "source_name": "s", "source_type": "annual_report",
                           "page_number": 3, "section_path": "控制关系",
                           "evidence_type": "paragraph",
                           "report_period": "2024-12-31",
                           "text": "实际控制人为曾毓群", "structured_payload": None},
                     evidence_ids=["e1"], error_code=None, message=None,
                     retryable=False, trace_id="t"))
    return reg


class MockLLM:
    def __init__(self, actions, answers):
        self._actions = list(actions)
        self._answers = list(answers)

    def select_action(self, prompt_vars):
        return llm_client.LLMResponse(text=self._actions.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")

    def generate_answer(self, prompt_vars):
        return llm_client.LLMResponse(text=self._answers.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")


_ANSWER = ('{"answer_text": "实控人为曾毓群", '
           '"claims": [{"claim_id": "c1", "text": "实控人为曾毓群", '
           '"kind": "fact", "citation_refs": [0]}], '
           '"citations": [{"ref_type": "evidence", "evidence_id": "e1"}], '
           '"aspects": [{"aspect_id": "a1", "text": "q", '
           '"claim_ids": ["c1"]}], '
           '"unresolved_items": [], "confidence": "high"}')


def _manifest(snapshot_id: str | None) -> C.RunManifest:
    return C.RunManifest(
        run_id="r", dataset_sha256="d", company_id="300750",
        report_as_of="2025-12-31", contract_version=H.HARNESS_VERSION,
        router_fingerprint=RS.RULE_VERSION, prompt_versions={}, model="m",
        budget={}, evidence_fingerprint="e", snapshot_id=snapshot_id,
        external_policy_version="v", harness_fingerprint="h")


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

    # ---- _reset_answer_derived_state 不清除 active_snapshot_id ----
    state = H.ResearchState(
        run_id="r", case_id="c", question_id="q", company_id="300750",
        section_id="company", original_question="q", need=_need())
    state.active_snapshot_id = "S1"
    state.structured_provenance = {"c1": object()}
    state.entailment_summary = [{"claim_id": "c1"}]
    state.unsupported_claims = ["c1: 旧缺口"]
    state.entailment_verdicts = [H.EntailmentVerdict(claim_id="c1")]
    state.entailment_evaluator_failed = True
    RT._reset_answer_derived_state(state)
    check(state.active_snapshot_id == "S1",
          "_reset_answer_derived_state 不清除 active_snapshot_id（保持锁定值 S1）")
    check(state.structured_provenance == {} and state.entailment_summary == []
          and state.unsupported_claims == [] and state.entailment_verdicts == []
          and state.entailment_evaluator_failed is False,
          "_reset_answer_derived_state 清除答案派生项（structured_provenance/summary/unsupported/verdicts/evaluator）")

    # ---- run_question 注入 context → active_snapshot_id 一次性锁定 ----
    llm = MockLLM(['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
                   '{"action": "ANSWER", "arguments": {}}'], [_ANSWER])
    o = RT.run_question(need=_need(), route_result=_router_result("DIRECT_EVIDENCE"),
                        registry=_fake_registry(), llm=llm, run_id="r", case_id="c",
                        company_id="300750", section_id="company",
                        trace_enabled=False, context=_context(snapshot_id="S1"))
    check(o.state.active_snapshot_id == "S1",
          "run_question：context.snapshot_id=S1 → active_snapshot_id 锁定为 S1")

    # ---- context=None → active_snapshot_id 保持 None ----
    llm2 = MockLLM(['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
                    '{"action": "ANSWER", "arguments": {}}'], [_ANSWER])
    o2 = RT.run_question(need=_need(), route_result=_router_result("DIRECT_EVIDENCE"),
                         registry=_fake_registry(), llm=llm2, run_id="r2", case_id="c",
                         company_id="300750", section_id="company",
                         trace_enabled=False, context=None)
    check(o2.state.active_snapshot_id is None,
          "run_question：context=None → active_snapshot_id 保持 None")

    # ---- resume：RunManifest.snapshot_id 不一致 → fail-closed ----
    check(RUN.manifest_mismatches(_manifest("S1"), _manifest("S1")) == [],
          "manifest_mismatches：snapshot_id 一致 → 空（可 resume）")
    check(RUN.manifest_mismatches(_manifest("S1"), _manifest("S2")) == ["snapshot_id"],
          "manifest_mismatches：snapshot_id 漂移 S1→S2 → 列出 snapshot_id（拒绝 resume）")
    check(RUN.manifest_mismatches(_manifest("S1"), _manifest(None)) == ["snapshot_id"],
          "manifest_mismatches：snapshot_id 漂移 S1→None → 列出 snapshot_id")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
