"""Eval: routing 契约（schema + validator）—— Phase 2 Commit 1。

用法: python -m evals.test_routing_schema

覆盖：
- RouterResult status/decision 充要关系（DECIDED ⇔ decision 非 None）；
- InformationNeed / RouteContext / RetrievalBudget / RouteDecision / EvidencePack /
  StructuredResultRef 的字段与枚举白名单；
- EvidencePack status/decision/failure_code 组合校验（契约修正 B）；
- DB 结果不伪造成 EvidenceRef（契约修正 C）；
- 跨公司 / current Evidence Set 权威守卫（契约修正 4，validator 保持纯函数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import schema as S
from routing import validator as V
from routing.schema import RoutingValidationError


# ---------------------------------------------------------------------------
# 合法样例构造
# ---------------------------------------------------------------------------

def _need(**over) -> S.InformationNeed:
    base = dict(
        need_id="N1", section_id="SEC-FIN", question="2024 年总资产是多少？",
        required_evidence_types=["paragraph"], required_source_types=["annual_report"],
        time_scope=None, priority="P0", depends_on=[],
    )
    base.update(over)
    return S.InformationNeed(**base)


def _context(**over) -> S.RouteContext:
    base = dict(
        company_id="300750", report_as_of="2025-06-30",
        available_document_ids=["NDSD_2025_year"],
        available_source_types=["annual_report"],
        supported_db_fields=["TOTAL_ASSETS"], supported_metric_ids=["PROF_ROE"],
        available_db_fields=["TOTAL_ASSETS"], available_metric_ids=["PROF_ROE"],
        external_research_enabled=False,
    )
    base.update(over)
    return S.RouteContext(**base)


def _budget(**over) -> S.RetrievalBudget:
    base = dict(candidate_k_sparse=20, candidate_k_dense=20, fusion_k=20,
                context_k=10, timeout_ms=5000)
    base.update(over)
    return S.RetrievalBudget(**base)


def _decision(**over) -> S.RouteDecision:
    base = dict(
        need_id="N1", route="STANDARD_RAG", reason_code="SECTION_TOPIC_SYNTHESIS",
        filters={}, budget=_budget(), fallback_routes=[],
        decided_by="rule", rule_version=S.RULE_VERSION, confidence="high",
    )
    base.update(over)
    return S.RouteDecision(**base)


def _evidence_ref(**over) -> S.EvidenceRef:
    base = dict(
        evidence_id="ev-1", document_id="NDSD_2025_year",
        evidence_set_version="set-abc", source_name="rpt.pdf",
        source_type="annual_report", page_number=3, evidence_type="paragraph",
        text="文本", structured_payload=None, score=0.8, rank=0,
        retrieval_channels=["sparse", "dense"], channel_ranks={"sparse": 1, "dense": 2},
    )
    base.update(over)
    return S.EvidenceRef(**base)


def _structured_ref(**over) -> S.StructuredResultRef:
    base = dict(
        result_type="financial_field", snapshot_id="snap-1", item_code="TOTAL_ASSETS",
        formula_id=None, formula_version=None, period="2024-12-31",
        raw_value="1000", display_value="1000", unit="yuan", status="CALCULATED_EXACT",
        reason_code=None, input_record_refs=["rec-1"], input_snapshot_item_refs=["ck-1"],
    )
    base.update(over)
    return S.StructuredResultRef(**base)


def _db_field_filters(**over) -> dict:
    """合法 DB_LOOKUP field target 五元组 filter（契约修正 2）。"""
    base = dict(
        db_target_type="field", standard_item_code="TOTAL_ASSETS",
        snapshot_as_of_date="2024-12-31", target_period="2024-12-31",
        scope="consolidated", currency="CNY", purpose="credit_analysis",
    )
    base.update(over)
    return base


def _db_metric_filters(**over) -> dict:
    """合法 DB_LOOKUP metric target 五元组 filter（含 formula_version）。"""
    base = dict(
        db_target_type="metric", formula_id="PROF_ROE", formula_version="1.0",
        snapshot_as_of_date="2024-12-31", target_period="2024-12-31",
        scope="consolidated", currency="CNY", purpose="credit_analysis",
    )
    base.update(over)
    return base


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

    def expect_err(fn, msg, substr=None):
        try:
            fn()
            check(False, f"{msg}（未抛错）")
        except RoutingValidationError as e:
            if substr is not None and substr not in str(e):
                check(False, f"{msg}（信息不符: {e}）")
            else:
                check(True, msg)

    # ------------------------------------------------------------------
    # RouterResult 充要关系
    # ------------------------------------------------------------------
    V.validate_result(S.RouterResult(status="DECIDED", decision=_decision(),
                                     error_code=None, trace_id="t1"))
    check(True, "validate_result 接受合法 DECIDED")

    expect_err(lambda: V.validate_result(S.RouterResult(
        status="DECIDED", decision=None, error_code=None, trace_id="t1")),
        "DECIDED 但 decision=None 被拒绝", "decision")

    expect_err(lambda: V.validate_result(S.RouterResult(
        status="FALLBACK_UNAVAILABLE", decision=_decision(),
        error_code="ROUTER_FALLBACK_UNAVAILABLE", trace_id="t1")),
        "非 DECIDED 却带 decision 被拒绝", "decision")

    expect_err(lambda: V.validate_result(S.RouterResult(
        status="FALLBACK_UNAVAILABLE", decision=None, error_code=None, trace_id="t1")),
        "非 DECIDED 缺 error_code 被拒绝", "error_code")

    expect_err(lambda: V.validate_result(S.RouterResult(
        status="NOPE", decision=None, error_code="x", trace_id="t1")),
        "非法 status 被拒绝", "status")

    # ------------------------------------------------------------------
    # InformationNeed
    # ------------------------------------------------------------------
    V.validate_need(_need())
    check(True, "validate_need 接受合法 need")
    expect_err(lambda: V.validate_need(_need(question="   ")), "空 question 被拒绝")
    expect_err(lambda: V.validate_need(_need(need_id="")), "空 need_id 被拒绝")

    # ------------------------------------------------------------------
    # RouteContext
    # ------------------------------------------------------------------
    V.validate_context(_context())
    check(True, "validate_context 接受合法 context")
    expect_err(lambda: V.validate_context(_context(company_id="")), "空 company_id 被拒绝")
    expect_err(lambda: V.validate_context(_context(
        external_research_enabled="yes")), "非 bool 开关被拒绝", "bool")

    # ------------------------------------------------------------------
    # RetrievalBudget
    # ------------------------------------------------------------------
    expect_err(lambda: V.validate_budget(_budget(candidate_k_sparse=0)),
               "非正 candidate_k 被拒绝", "正整数")
    expect_err(lambda: V.validate_budget(_budget(context_k=21, fusion_k=20)),
               "context_k > fusion_k 被拒绝", "fusion_k")

    # ------------------------------------------------------------------
    # RouteDecision
    # ------------------------------------------------------------------
    V.validate_decision(_decision())
    check(True, "validate_decision 接受合法 decision")
    expect_err(lambda: V.validate_decision(_decision(route="RAG")), "非法 route 被拒绝")
    expect_err(lambda: V.validate_decision(_decision(reason_code="NOPE")),
               "非法 reason_code 被拒绝")
    expect_err(lambda: V.validate_decision(_decision(decided_by="guess")),
               "非法 decided_by 被拒绝")
    expect_err(lambda: V.validate_decision(_decision(confidence="maybe")),
               "非法 confidence 被拒绝")
    expect_err(lambda: V.validate_decision(_decision(filters={"bogus": 1})),
               "未知 filter key 被拒绝", "filter")

    # DB_LOOKUP 必须带合法 DB target
    V.validate_decision(_decision(route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
                                  filters=_db_field_filters()))
    check(True, "DB_LOOKUP field target 被接受")
    V.validate_decision(_decision(route="DB_LOOKUP",
                                  reason_code="REGISTERED_FINANCIAL_METRIC",
                                  filters=_db_metric_filters()))
    check(True, "DB_LOOKUP metric target（含 formula_version）被接受")
    expect_err(lambda: V.validate_decision(_decision(
        route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD", filters={})),
        "DB_LOOKUP 缺 db_target_type 被拒绝", "db_target_type")
    expect_err(lambda: V.validate_decision(_decision(
        route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
        filters={"db_target_type": "field"})),
        "DB_LOOKUP field 缺 standard_item_code 被拒绝", "standard_item_code")
    expect_err(lambda: V.validate_decision(_decision(
        route="DB_LOOKUP", reason_code="REGISTERED_FINANCIAL_METRIC",
        filters={"db_target_type": "metric", "formula_id": "PROF_ROE",
                 "snapshot_as_of_date": "2024-12-31", "target_period": "2024-12-31",
                 "scope": "consolidated", "currency": "CNY",
                 "purpose": "credit_analysis"})),
        "DB_LOOKUP metric 缺 formula_version 被拒绝", "formula_version")
    expect_err(lambda: V.validate_decision(_decision(
        route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
        filters=_db_field_filters(snapshot_as_of_date=""))),
        "DB_LOOKUP field 缺 snapshot_as_of_date 被拒绝", "snapshot_as_of_date")

    # ------------------------------------------------------------------
    # EvidencePack 组合校验（契约修正 B）
    # ------------------------------------------------------------------
    def _pack(**over):
        base = dict(need_id="N1", status="COMPLETED",
                    route_decision=_decision(),
                    evidence=[_evidence_ref()], structured_results=[],
                    retrieval_trace_id="tr1")
        base.update(over)
        return S.EvidencePack(**base)

    V.validate_pack(_pack())
    check(True, "validate_pack 接受合法 COMPLETED")

    expect_err(lambda: V.validate_pack(_pack(status="COMPLETED", route_decision=None)),
               "COMPLETED 缺 decision 被拒绝", "RouteDecision")
    expect_err(lambda: V.validate_pack(_pack(status="COMPLETED",
                                             failure_code="TIMEOUT")),
               "COMPLETED 带失败码被拒绝", "失败码")
    expect_err(lambda: V.validate_pack(_pack(status="FAILED", failure_code=None)),
               "FAILED 缺 failure_code 被拒绝", "failure_code")

    # ROUTER_FALLBACK_UNAVAILABLE 可无 decision
    V.validate_pack(S.EvidencePack(
        need_id="N1", status="ROUTER_FALLBACK_UNAVAILABLE", route_decision=None,
        failure_code="ROUTER_FALLBACK_UNAVAILABLE"))
    check(True, "ROUTER_FALLBACK_UNAVAILABLE 无 decision 被接受")

    # PARTIAL 必须带单通道失败码
    V.validate_pack(_pack(status="PARTIAL", failure_code="SPARSE_FAILED"))
    check(True, "PARTIAL + SPARSE_FAILED 被接受")
    expect_err(lambda: V.validate_pack(_pack(status="PARTIAL", failure_code="TIMEOUT")),
               "PARTIAL 带非单通道失败码被拒绝", "单通道")

    # ------------------------------------------------------------------
    # DB 结果不伪造成 EvidenceRef（契约修正 C）
    # ------------------------------------------------------------------
    V.validate_pack(S.EvidencePack(
        need_id="N1", status="DB_RESULT_AVAILABLE", route_decision=_decision(
            route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
            filters=_db_field_filters()),
        evidence=[], structured_results=[_structured_ref()]))
    check(True, "DB_RESULT_AVAILABLE 仅 structured_results 被接受")

    expect_err(lambda: V.validate_pack(S.EvidencePack(
        need_id="N1", status="DB_RESULT_AVAILABLE", route_decision=_decision(
            route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
            filters=_db_field_filters()),
        evidence=[_evidence_ref()], structured_results=[])),
        "DB_RESULT_AVAILABLE 携带本地 evidence 被拒绝", "evidence")

    expect_err(lambda: V.validate_pack(S.EvidencePack(
        need_id="N1", status="DB_FIELD_UNAVAILABLE", route_decision=_decision(
            route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
            filters=_db_field_filters()),
        evidence=[], structured_results=[], missing_requirements=[])),
        "DB_FIELD_UNAVAILABLE 缺 missing_requirements 被拒绝", "缺失")

    # financial_metric 必须带 formula_id + formula_version，不带 item_code
    V.validate_pack(S.EvidencePack(
        need_id="N1", status="DB_RESULT_AVAILABLE", route_decision=_decision(
            route="DB_LOOKUP", reason_code="REGISTERED_FINANCIAL_METRIC",
            filters=_db_metric_filters()),
        evidence=[], structured_results=[_structured_ref(
            result_type="financial_metric", item_code=None, formula_id="PROF_ROE",
            formula_version="1.0")]))
    check(True, "financial_metric 结构化结果被接受")

    expect_err(lambda: V.validate_pack(S.EvidencePack(
        need_id="N1", status="DB_RESULT_AVAILABLE", route_decision=_decision(
            route="DB_LOOKUP", reason_code="REGISTERED_FINANCIAL_METRIC",
            filters=_db_metric_filters()),
        evidence=[], structured_results=[_structured_ref(
            result_type="financial_metric", item_code=None, formula_id="PROF_ROE",
            formula_version=None)])),
        "financial_metric 缺 formula_version 被拒绝", "formula_version")

    # ------------------------------------------------------------------
    # 跨公司 / current Evidence Set 权威守卫
    # ------------------------------------------------------------------
    authority = {"ev-1": {"company_id": "300750", "document_id": "NDSD_2025_year",
                          "evidence_set_version": "set-abc", "is_current": True}}
    V.validate_pack(_pack(), authority=authority)
    check(True, "validate_pack 接受匹配的权威映射")

    expect_err(lambda: V.validate_pack(_pack(), authority={
        "ev-1": {"company_id": "300750", "document_id": "NDSD_2025_year",
                 "evidence_set_version": "set-abc", "is_current": False}}),
        "非 current Evidence Set 被拒绝", "current")

    V.validate_authority_company(_pack(), "300750", authority)
    check(True, "validate_authority_company 接受同公司证据")
    expect_err(lambda: V.validate_authority_company(_pack(), "600000", authority),
               "跨公司 Evidence 被拒绝", "跨公司")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
