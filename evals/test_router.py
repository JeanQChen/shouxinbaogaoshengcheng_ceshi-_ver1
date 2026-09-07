"""Eval: Router（规则优先 + fallback 协议）—— Phase 2 Commit 2。

用法: python -m evals.test_router

覆盖（任务书 §11 / 契约修正 A）：
- 五路由命中：DB（字段/指标）、EXTERNAL、DEEP、DIRECT、STANDARD；
- DB 只看能力不看当前值：supported 但 available 缺失仍路由 DB_LOOKUP；
- 冲突（DB 目标 + 外部时效）→ fallback；无 provider → FALLBACK_UNAVAILABLE；
- fallback 非法 route / 非法 JSON → FAILED；
- 公司无关表达不硬编码。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import router as R
from routing import schema as S


def _need(q, need_id="N1", time_scope=None, **over):
    base = dict(
        need_id=need_id, section_id="SEC", question=q,
        required_evidence_types=["paragraph"], required_source_types=["annual_report"],
        time_scope=time_scope, priority="P0", depends_on=[],
    )
    base.update(over)
    return S.InformationNeed(**base)


def _context(**over):
    base = dict(
        company_id="300750", report_as_of="2025-06-30",
        available_document_ids=["NDSD_2025_year"],
        available_source_types=["annual_report"],
        supported_db_fields=["TOTAL_ASSETS", "NET_PROFIT", "NET_PROFIT_PARENT",
                             "OPERATING_CASH_FLOW", "OPERATING_REVENUE"],
        supported_metric_ids=["PROF_ROE", "PROF_GROSS_MARGIN", "SOLV_CURRENT_RATIO"],
        available_db_fields=["TOTAL_ASSETS"], available_metric_ids=["PROF_ROE"],
        external_research_enabled=False,
    )
    base.update(over)
    return S.RouteContext(**base)


def _mock_fallback(decision_or_raiser):
    """构造 fallback callable：返回 decision 或抛出异常。"""
    def _f(need, context):
        if callable(decision_or_raiser):
            return decision_or_raiser(need, context)
        if isinstance(decision_or_raiser, Exception):
            raise decision_or_raiser
        return decision_or_raiser
    return _f


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

    def route_of(q, ctx=None, fb=None, time_scope=None):
        r = R.route(_need(q, time_scope=time_scope), ctx or _context(), fallback=fb)
        return r

    # ---- DB 字段 ----
    r = route_of("2024 年总资产是多少？")
    check(r.status == "DECIDED" and r.decision.route == "DB_LOOKUP"
          and r.decision.reason_code == "REGISTERED_DB_FIELD"
          and r.decision.filters.get("standard_item_code") == "TOTAL_ASSETS",
          "总资产 → DB_LOOKUP(field, TOTAL_ASSETS)")

    # ---- DB 指标 ----
    r = route_of("净资产收益率是多少？")
    check(r.status == "DECIDED" and r.decision.route == "DB_LOOKUP"
          and r.decision.reason_code == "REGISTERED_FINANCIAL_METRIC"
          and r.decision.filters.get("formula_id") == "PROF_ROE",
          "净资产收益率 → DB_LOOKUP(metric, PROF_ROE)")

    # ---- 修正 A：supported 但 available 缺失仍路由 DB（不看当前值）----
    ctx = _context(supported_db_fields=["NET_PROFIT"], available_db_fields=[],
                   available_metric_ids=[])
    r = R.route(_need("2024 年净利润是多少？"), ctx)
    check(r.status == "DECIDED" and r.decision.route == "DB_LOOKUP",
          "supported 但当前缺值仍路由 DB_LOOKUP（修正 A）")

    # ---- EXTERNAL（外部来源词 / 时效词）----
    r = route_of("公司当前市值是多少？")
    check(r.decision.route == "EXTERNAL_RESEARCH"
          and r.decision.reason_code == "EXPLICIT_EXTERNAL_RECENCY",
          "市值 → EXTERNAL_RESEARCH")

    r = route_of("公司有哪些近期监管处罚？")
    check(r.decision.route == "EXTERNAL_RESEARCH", "监管处罚 → EXTERNAL_RESEARCH")

    # ---- DEEP ----
    r = route_of("2023-2025 年营收同比变化趋势如何？")
    check(r.decision.route == "DEEP_RETRIEVAL"
          and r.decision.reason_code == "CROSS_DOCUMENT_OR_CONFLICT",
          "同比变化趋势 → DEEP_RETRIEVAL")

    # ---- DIRECT ----
    r = route_of("公司的实际控制人是谁？")
    check(r.decision.route == "DIRECT_EVIDENCE"
          and r.decision.reason_code == "EXACT_DOCUMENT_FIELD",
          "实际控制人是谁 → DIRECT_EVIDENCE")

    # ---- STANDARD（兜底）----
    r = route_of("公司的核心竞争优势是什么？")
    check(r.decision.route == "STANDARD_RAG"
          and r.decision.reason_code == "SECTION_TOPIC_SYNTHESIS",
          "核心竞争优势 → STANDARD_RAG")

    # ---- 冲突（DB 目标 + 外部时效）→ fallback ----
    # 无 provider → FALLBACK_UNAVAILABLE
    r = route_of("公司最新总资产是多少？")
    check(r.status == "FALLBACK_UNAVAILABLE" and r.decision is None
          and r.error_code == "ROUTER_FALLBACK_UNAVAILABLE",
          "DB+时效冲突且无 fallback → FALLBACK_UNAVAILABLE")

    # 有 provider → DECIDED（llm_fallback）
    fb = _mock_fallback(S.RouteDecision(
        need_id="N1", route="DB_LOOKUP", reason_code="REGISTERED_DB_FIELD",
        filters={"db_target_type": "field", "standard_item_code": "TOTAL_ASSETS"},
        budget=R._BUDGET_NOOP, fallback_routes=[], decided_by="llm_fallback",
        rule_version=S.RULE_VERSION, confidence="high"))
    r = route_of("公司最新总资产是多少？", fb=fb)
    check(r.status == "DECIDED" and r.decision.decided_by == "llm_fallback",
          "冲突 + fallback → DECIDED(llm_fallback)")

    # 非法 route → FAILED
    bad = S.RouteDecision(
        need_id="N1", route="RAG", reason_code="SECTION_TOPIC_SYNTHESIS",
        filters={}, budget=R._BUDGET_NOOP, fallback_routes=[],
        decided_by="llm_fallback", rule_version=S.RULE_VERSION, confidence="high")
    r = route_of("公司最新总资产是多少？", fb=_mock_fallback(bad))
    check(r.status == "FAILED" and r.error_code == "ROUTER_FALLBACK_SCHEMA_FAILURE",
          "fallback 非法 route → FAILED")

    # 非法 JSON → FAILED
    def _raise_json(need, context):
        raise ValueError("非法 JSON")
    r = route_of("公司最新总资产是多少？", fb=_raise_json)
    check(r.status == "FAILED" and r.error_code == "ROUTER_FALLBACK_SCHEMA_FAILURE",
          "fallback 抛异常 → FAILED")

    # ---- 公司无关（不硬编码宁德时代）----
    ctx_other = _context(company_id="600000")
    r = R.route(_need("总资产是多少？", need_id="SYN-1"), ctx_other)
    check(r.decision.route == "DB_LOOKUP", "公司无关表达同样路由 DB（无硬编码）")

    # ---- EXTERNAL 不因「财务」二字误路由 DB ----
    r = route_of("公司的财务情况如何？")
    check(r.decision.route != "DB_LOOKUP", "「财务」二字不触发 DB")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
