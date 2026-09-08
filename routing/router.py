"""Phase 2 Router：规则优先，冲突/低置信度才调用可注入 LLM fallback。

规则判定完全基于 InformationNeed 的需求特征与 RouteContext 的静态能力清单，
不执行检索、不预知 evidence_id 命中（契约修正 2）。

有效判定顺序（对应任务书 §5 规则 1-5）：
1. DB 目标 + 明确外部时效 → 冲突，交 fallback；
2. EXTERNAL_RESEARCH —— 外部来源/时效/时间窗口晚于本地截止（数据不在上传文档内）；
3. DEEP_RETRIEVAL  —— 跨期/变化/冲突（趋势非单值字段查询，优先于 DB）；
4. DB_LOOKUP       —— 确定性解析到 supported 字段/指标，且无外部/深信号；
5. DIRECT_EVIDENCE —— 单个明确字段/日期/人数/名称/表格项目；
6. STANDARD_RAG    —— 其余专题归纳（兜底）。

fallback：仅当「DB 目标已解析 + 明确外部时效信号」冲突，或规则低置信度时调用；
provider 未注入返回 FALLBACK_UNAVAILABLE，非法输出返回 FAILED，禁止猜测五路由。

CLI: python -m routing.router --case evaluation/datasets/router/sample.json
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from typing import Callable

from llm import client as llm_client
from routing import audit_v2
from routing import db_targets
from routing import periods
from routing import schema as S
from routing.validator import validate_context, validate_decision, validate_need

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 预算（Track B / 系统能力用；Track A 使用固定唯一预算，不走此表）
# ---------------------------------------------------------------------------

_BUDGET_STANDARD = S.RetrievalBudget(
    candidate_k_sparse=20, candidate_k_dense=20, fusion_k=20, context_k=10,
    timeout_ms=5000)
_BUDGET_DIRECT = S.RetrievalBudget(
    candidate_k_sparse=10, candidate_k_dense=10, fusion_k=10, context_k=10,
    timeout_ms=5000)
_BUDGET_DEEP = S.RetrievalBudget(
    candidate_k_sparse=40, candidate_k_dense=40, fusion_k=40, context_k=10,
    timeout_ms=5000)
_BUDGET_NOOP = S.RetrievalBudget(  # DB / EXTERNAL 不检索，占位合法预算
    candidate_k_sparse=1, candidate_k_dense=1, fusion_k=1, context_k=1,
    timeout_ms=1000)


def _budget_for_route(route: str) -> S.RetrievalBudget:
    return {
        "DIRECT_EVIDENCE": _BUDGET_DIRECT,
        "DEEP_RETRIEVAL": _BUDGET_DEEP,
        "STANDARD_RAG": _BUDGET_STANDARD,
    }.get(route, _BUDGET_NOOP)


# ---------------------------------------------------------------------------
# 需求特征关键词信号（确定性、版本化，随 RULE_VERSION 冻结）
# ---------------------------------------------------------------------------

# 外部来源词：这类数据天然来自联网/行情/公告，不在上传财报文档内。
_EXTERNAL_SOURCE_TERMS = (
    "市值", "股价", "行情", "涨跌", "市盈率", "市净率", "新闻", "处罚", "监管",
    "政策", "评级", "研报", "融资余额", "北向资金", "最新公告", "股价表现",
    "股价走势", "收购", "并购", "重组",
)

# 时效词：明确要求「最新/当前」。
_EXTERNAL_RECENCY_TERMS = (
    "最新", "近期", "最近", "当前", "截至", "目前", "实时", "今日", "现阶段",
)

# 跨页/跨文件/时间线/变化/冲突/多跳信号。
_DEEP_TERMS = (
    "变化", "同比", "环比", "趋势", "演变", "对比", "比较", "相比", "差异",
    "变动", "过程", "历年", "近三年", "近五年", "是否一致", "核对", "复核",
    "矛盾", "穿透", "关联方", "关联关系", "控制链", "原因", "为什么", "如何变化",
    "多期", "多个年度", "三年", "五年",
)

# 单事实提问词。
_DIRECT_WH_TERMS = (
    "是多少", "是谁", "何时", "几位", "哪家", "什么时间", "哪一年", "哪年",
    "多少", "股票代码", "代码", "几家", "哪些子公司", "哪一家",
)

# 专题归纳词（STANDARD）。
_SYNTHESIS_TERMS = (
    "如何", "哪些", "方面", "历程", "格局", "战略", "计划", "优势", "地位",
    "现状", "情况", "分析", "评价", "影响", "概述", "特点", "模式", "竞争",
    "风险", "发展历程", "核心竞争力", "业务发展", "结构", "规划", "布局",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(t in text for t in terms)


def _time_scope_late(need: S.InformationNeed, context: S.RouteContext) -> bool:
    """time_scope 是否晚于本地材料截止时间（契约修正 2：禁止字符串字典序比较）。

    两者先规范化为可比 (year, month, day) 再比较；任一无法解析则视为「无法判定为晚」
    （不触发 EXTERNAL 时效信号，由 route() 的 TIME_SCOPE_UNPARSEABLE 分支单独处理）。
    """
    if not need.time_scope or not context.report_as_of:
        return False
    ts = periods.parse_period(need.time_scope)
    ra = periods.parse_period(context.report_as_of)
    if ts is None or ra is None:
        return False
    return ts > ra


def _time_scope_unparseable(need: S.InformationNeed) -> bool:
    """time_scope 存在但无法可靠解析为可比期间（交 fallback，禁止猜测）。"""
    return bool(need.time_scope) and periods.parse_period(need.time_scope) is None


def _has_external_signal(need: S.InformationNeed, context: S.RouteContext) -> bool:
    """EXTERNAL 三条需求特征（契约修正 2）：外部来源词 / 时效词 / 时间窗口晚于本地。"""
    q = need.question
    return (_contains_any(q, _EXTERNAL_SOURCE_TERMS)
            or _contains_any(q, _EXTERNAL_RECENCY_TERMS)
            or _time_scope_late(need, context))


def _has_deep_signal(need: S.InformationNeed) -> bool:
    return _contains_any(need.question, _DEEP_TERMS)


def _has_direct_signal(need: S.InformationNeed) -> bool:
    return (_contains_any(need.question, _DIRECT_WH_TERMS)
            and not _contains_any(need.question, _SYNTHESIS_TERMS))


# ---------------------------------------------------------------------------
# 决策构造
# ---------------------------------------------------------------------------

def _decision(need: S.InformationNeed, route: str, reason_code: str,
              filters: dict, confidence: str = "high") -> S.RouteDecision:
    return S.RouteDecision(
        need_id=need.need_id, route=route, reason_code=reason_code, filters=filters,
        budget=_budget_for_route(route), fallback_routes=[],
        decided_by="rule", rule_version=S.RULE_VERSION, confidence=confidence,
    )


def _db_filters(target: db_targets.DbTarget, context: S.RouteContext,
                question: str) -> dict:
    """由 DbTarget + context 组装可执行 DB target（契约修正 C + 修正 2）。

    区分两类期间：
    - snapshot_as_of_date = context.report_as_of（选择 current snapshot）；
    - target_period = 问题中的目标报告期，缺省回退为 snapshot_as_of_date；
    scope/currency/purpose 从 context 透传（限定快照键）；formula_version 来自
    DbTarget（db_targets 从 Formula Registry 读取，禁止猜测/硬编码）。
    """
    filters: dict = {"db_target_type": target.target_type}
    if target.target_type == "field":
        filters["standard_item_code"] = target.standard_item_code
    else:
        filters["formula_id"] = target.formula_id
        filters["formula_version"] = target.formula_version
    snapshot_as_of = context.report_as_of or ""
    filters["snapshot_as_of_date"] = snapshot_as_of
    filters["target_period"] = (
        db_targets.resolve_target_period(question) or snapshot_as_of)
    filters["scope"] = context.scope
    filters["currency"] = context.currency
    filters["purpose"] = context.purpose
    return filters


def _target_supported(target: db_targets.DbTarget, context: S.RouteContext) -> bool:
    """DB 目标是否属于 supported 能力（修正 A：只看能力，不看当前值）。"""
    if target.target_type == "field":
        return target.standard_item_code in context.supported_db_fields
    return target.formula_id in context.supported_metric_ids


def _decided(decision: S.RouteDecision, trace_id: str) -> S.RouterResult:
    validate_decision(decision)
    return S.RouterResult(status="DECIDED", decision=decision, error_code=None,
                          trace_id=trace_id)


# ---------------------------------------------------------------------------
# fallback 协议
# ---------------------------------------------------------------------------

def _fill_prompt(need: S.InformationNeed, context: S.RouteContext) -> str:
    prompt = llm_client.load_prompt("router_v2.txt")
    return (
        prompt.replace("{{question}}", need.question)
        .replace("{{available_source_types}}", ", ".join(context.available_source_types))
        .replace("{{supported_db_fields}}", ", ".join(context.supported_db_fields))
        .replace("{{supported_metric_ids}}", ", ".join(context.supported_metric_ids))
    )


def make_llm_fallback(model: str | None = None) -> Callable:
    """构造 LLM fallback：返回 RouteDecision（decided_by=llm_fallback），非法输出抛错。"""
    def _fallback(need: S.InformationNeed, context: S.RouteContext) -> S.RouteDecision:
        filled = _fill_prompt(need, context)
        completion = llm_client.chat([{"role": "user", "content": filled}],
                                     model=model, max_tokens=256)
        data = json.loads(completion)
        decision = S.RouteDecision(
            need_id=need.need_id, route=data["route"], reason_code=data["reason_code"],
            filters={}, budget=_budget_for_route(data["route"]), fallback_routes=[],
            decided_by="llm_fallback", rule_version=S.RULE_VERSION,
            confidence=data.get("confidence", "low"),
        )
        validate_decision(decision)
        return decision
    return _fallback


def _resolve_fallback(need: S.InformationNeed, context: S.RouteContext,
                      fallback: Callable | None, trace_id: str,
                      reason: str | None = None) -> S.RouterResult:
    if fallback is None:
        return S.RouterResult(status="FALLBACK_UNAVAILABLE", decision=None,
                              error_code="ROUTER_FALLBACK_UNAVAILABLE", trace_id=trace_id,
                              reason_code=reason)
    try:
        decision = fallback(need, context)
        decision = replace(decision, decided_by="llm_fallback", rule_version=S.RULE_VERSION)
        validate_decision(decision)
        return S.RouterResult(status="DECIDED", decision=decision, error_code=None,
                              trace_id=trace_id, reason_code=reason)
    except Exception as e:  # 非法 JSON / 非法 route / 网络异常
        logger.warning("router fallback 失败: %s", e)
        return S.RouterResult(status="FAILED", decision=None,
                              error_code="ROUTER_FALLBACK_SCHEMA_FAILURE", trace_id=trace_id,
                              reason_code=reason)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def route(need: S.InformationNeed, context: S.RouteContext,
          fallback: Callable | None = None) -> S.RouterResult:
    """规则优先路由；冲突 → fallback（未注入则 FALLBACK_UNAVAILABLE）。

    每次调用末尾落盘一条 Router 审计（logs/router_v2/，与 Retrieval trace 分离）；
    落盘失败 fail-closed 抛 RouterAuditError。
    """
    result = _route(need, context, fallback)
    audit_v2.write_router_audit(result, need, context)
    return result


def _route(need: S.InformationNeed, context: S.RouteContext,
           fallback: Callable | None = None) -> S.RouterResult:
    """路由判定核心（不落盘，由 route() 包装审计）。"""
    validate_need(need)
    validate_context(context)
    trace_id = uuid.uuid4().hex

    db_target = db_targets.resolve_db_target(need.question)
    db_supported = db_target is not None and _target_supported(db_target, context)

    # 0. time_scope 无法可靠解析 → 交 fallback（禁止猜测，契约修正 2）。
    if _time_scope_unparseable(need):
        return _resolve_fallback(need, context, fallback, trace_id,
                                 reason="TIME_SCOPE_UNPARSEABLE")

    external_signal = _has_external_signal(need, context)

    # 1. DB 目标 + 明确外部时效 → 冲突，交 fallback。
    if db_supported and external_signal:
        return _resolve_fallback(need, context, fallback, trace_id)

    # 2. EXTERNAL：外部来源/时效/时间窗口晚于本地（数据不在上传文档内）。
    if external_signal:
        return _decided(_decision(
            need, "EXTERNAL_RESEARCH", "EXPLICIT_EXTERNAL_RECENCY", {}), trace_id)

    # 3. DEEP：跨期/变化/冲突（趋势非单值字段查询，优先于 DB）。
    if _has_deep_signal(need):
        return _decided(_decision(
            need, "DEEP_RETRIEVAL", "CROSS_DOCUMENT_OR_CONFLICT", {}), trace_id)

    # 4. DB：确定性解析 + supported（不看当前值），且无外部/深信号。
    if db_supported:
        return _decided(_decision(
            need, "DB_LOOKUP",
            "REGISTERED_DB_FIELD" if db_target.target_type == "field"
            else "REGISTERED_FINANCIAL_METRIC",
            _db_filters(db_target, context, need.question)), trace_id)

    # 5. DIRECT：单个明确字段/日期/人数/名称/表格项目。
    if _has_direct_signal(need):
        return _decided(_decision(
            need, "DIRECT_EVIDENCE", "EXACT_DOCUMENT_FIELD", {}), trace_id)

    # 6. STANDARD（兜底）。
    return _decided(_decision(
        need, "STANDARD_RAG", "SECTION_TOPIC_SYNTHESIS", {}, confidence="high"), trace_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m routing.router",
                                     description="Phase 2 规则优先 Router")
    parser.add_argument("--case", required=True, help="JSON 文件（含 need + context）")
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.case).read_text(encoding="utf-8"))
    need = S.InformationNeed(**raw["need"])
    context = S.RouteContext(**raw["context"])
    result = route(need, context)
    print(json.dumps({
        "status": result.status,
        "decision": None if result.decision is None else {
            "route": result.decision.route,
            "reason_code": result.decision.reason_code,
            "filters": result.decision.filters,
            "decided_by": result.decision.decided_by,
        },
        "error_code": result.error_code,
        "trace_id": result.trace_id,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
