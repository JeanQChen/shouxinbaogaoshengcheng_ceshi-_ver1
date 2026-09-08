"""Routing 契约校验（纯函数，fail-closed）。

所有校验失败抛 RoutingValidationError，不降级、不猜测、不静默吞错。

跨公司/current Evidence 校验依赖 Evidence Store 的权威关系，validator 保持纯函数：
调用者可选传入 `authority`（evidence_id → {company_id, document_id,
evidence_set_version, is_current}）供 validator 做结构化比较，不自行查询数据库
（契约修正 4）。
"""

from __future__ import annotations

from routing import schema as S


# 单通道失败码（PARTIAL 状态必须携带其中之一）。
_SINGLE_CHANNEL_FAILURES = {"SPARSE_FAILED", "DENSE_FAILED"}

# 路由层失败码（RouterResult 的 error_code 会透传到 EvidencePack.failure_code）。
_ROUTER_ERROR_CODES = ("ROUTER_FALLBACK_UNAVAILABLE", "ROUTER_FALLBACK_SCHEMA_FAILURE")

# 合法 failure_code 全集。
_VALID_FAILURE_CODES = set(S.FAILURE_CODES) | set(_ROUTER_ERROR_CODES)

# 必须有合法 RouteDecision 的 EvidencePack 状态（契约修正 B）。
_REQUIRES_DECISION = {
    "COMPLETED",
    "EMPTY",
    "PARTIAL",
    "DB_RESULT_AVAILABLE",
    "DB_FIELD_UNAVAILABLE",
    "EXTERNAL_RESEARCH_NOT_IMPLEMENTED",
}

# 所有 filter key 白名单（本地 metadata + DB target）。
_ALL_FILTER_KEYS = set(S.LOCAL_FILTER_KEYS) | set(S.DB_TARGET_KEYS)


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise S.RoutingValidationError(msg)


def _require_str(value: str, field: str) -> None:
    _require(isinstance(value, str) and value.strip(), f"{field} 必须为非空字符串")


def validate_need(need: S.InformationNeed) -> None:
    """校验 InformationNeed：空 question / 缺 need_id 失败关闭。"""
    _require(isinstance(need, S.InformationNeed), "need 必须是 InformationNeed")
    _require_str(need.need_id, "need_id")
    _require_str(need.section_id, "section_id")
    _require_str(need.question, "question")
    for fname in ("required_evidence_types", "required_source_types", "depends_on"):
        v = getattr(need, fname)
        _require(isinstance(v, list), f"{fname} 必须是 list")
        _require(all(isinstance(x, str) for x in v), f"{fname} 元素必须是 str")


def validate_context(context: S.RouteContext) -> None:
    """校验 RouteContext：company_id 非空、四清单为 str list、开关为 bool。"""
    _require(isinstance(context, S.RouteContext), "context 必须是 RouteContext")
    _require_str(context.company_id, "company_id")
    for fname in (
        "available_document_ids",
        "available_source_types",
        "supported_db_fields",
        "supported_metric_ids",
        "available_db_fields",
        "available_metric_ids",
    ):
        v = getattr(context, fname)
        _require(isinstance(v, list), f"{fname} 必须是 list")
        _require(all(isinstance(x, str) for x in v), f"{fname} 元素必须是 str")
    _require(isinstance(context.external_research_enabled, bool),
             "external_research_enabled 必须是 bool")
    _require_str(context.scope, "context.scope")
    _require_str(context.currency, "context.currency")
    _require_str(context.purpose, "context.purpose")


def validate_budget(budget: S.RetrievalBudget) -> None:
    """校验检索预算：全为正整数，且 context_k ≤ fusion_k。"""
    _require(isinstance(budget, S.RetrievalBudget), "budget 必须是 RetrievalBudget")
    for fname in ("candidate_k_sparse", "candidate_k_dense", "fusion_k",
                  "context_k", "timeout_ms"):
        v = getattr(budget, fname)
        _require(isinstance(v, int) and not isinstance(v, bool) and v > 0,
                 f"{fname} 必须为正整数")
    _require(budget.context_k <= budget.fusion_k,
             f"context_k({budget.context_k}) 不得大于 fusion_k({budget.fusion_k})")


def _validate_filters(route: str, filters: dict) -> None:
    """校验 RouteDecision.filters：未知 key 拒绝；DB_LOOKUP 必须有合法 DB target。"""
    _require(isinstance(filters, dict), "filters 必须是 dict")
    unknown = set(filters.keys()) - _ALL_FILTER_KEYS
    _require(not unknown, f"未知 filter key: {sorted(unknown)}")
    if route == "DB_LOOKUP":
        target_type = filters.get("db_target_type")
        _require(target_type in S.DB_TARGET_TYPES,
                 "DB_LOOKUP 的 filters 必须含合法 db_target_type")
        if target_type == "field":
            _require_str(filters.get("standard_item_code") or "", "standard_item_code")
        else:
            _require_str(filters.get("formula_id") or "", "formula_id")
    elif route == "EXTERNAL_RESEARCH":
        _require(not filters, "EXTERNAL_RESEARCH 不得携带检索 filter")


def validate_decision(decision: S.RouteDecision) -> None:
    """校验 RouteDecision：route/reason_code/decided_by/confidence/budget 均合法。"""
    _require(isinstance(decision, S.RouteDecision), "decision 必须是 RouteDecision")
    _require_str(decision.need_id, "decision.need_id")
    _require(decision.route in S.ROUTES, f"非法 route: {decision.route!r}")
    _require(decision.reason_code in S.REASON_CODES,
             f"非法 reason_code: {decision.reason_code!r}")
    _require(decision.decided_by in S.DECIDED_BY,
             f"非法 decided_by: {decision.decided_by!r}")
    _require(decision.confidence in S.CONFIDENCE_LEVELS,
             f"非法 confidence: {decision.confidence!r}")
    _require_str(decision.rule_version, "rule_version")
    validate_budget(decision.budget)
    _validate_filters(decision.route, decision.filters)
    _require(isinstance(decision.fallback_routes, list), "fallback_routes 必须是 list")
    _require(all(r in S.ROUTES for r in decision.fallback_routes),
             "fallback_routes 含非法 route")


def validate_result(result: S.RouterResult) -> None:
    """校验 RouterResult：status 与 decision 的充要关系（DECIDED ⇔ decision 非 None）。"""
    _require(isinstance(result, S.RouterResult), "result 必须是 RouterResult")
    _require(result.status in S.ROUTER_STATUSES, f"非法 RouterResult.status: {result.status!r}")
    _require_str(result.trace_id, "trace_id")
    decided = result.status == "DECIDED"
    _require(decided == (result.decision is not None),
             "DECIDED ⇔ decision 非 None 的充要关系被破坏")
    if result.decision is not None:
        validate_decision(result.decision)
    if not decided:
        _require(result.error_code is not None, "非 DECIDED 必须携带 error_code")


def _validate_evidence_ref(ref: S.EvidenceRef) -> None:
    _require(isinstance(ref, S.EvidenceRef), "evidence 元素必须是 EvidenceRef")
    _require_str(ref.evidence_id, "evidence_id")
    _require_str(ref.document_id, "document_id")
    _require_str(ref.evidence_set_version, "evidence_set_version")
    _require(ref.page_number is None or (isinstance(ref.page_number, int)
                                         and ref.page_number > 0),
             "page_number 必须为正整数或 None")
    _require(ref.evidence_type in S.EVIDENCE_TYPES, f"非法 evidence_type: {ref.evidence_type!r}")
    _require(ref.rank >= 0, "rank 必须非负")
    _require(ref.retrieval_channels and set(ref.retrieval_channels) <= {"sparse", "dense"},
             "retrieval_channels 必须为 {sparse, dense} 的非空子集")
    _require(set(ref.channel_ranks.keys()) <= set(ref.retrieval_channels),
             "channel_ranks 的 key 必须 ⊆ retrieval_channels")


def _validate_structured_ref(ref: S.StructuredResultRef) -> None:
    _require(isinstance(ref, S.StructuredResultRef),
             "structured_results 元素必须是 StructuredResultRef")
    _require(ref.result_type in S.RESULT_TYPES, f"非法 result_type: {ref.result_type!r}")
    _require_str(ref.snapshot_id, "snapshot_id")
    _require_str(ref.period, "period")
    if ref.result_type == "financial_field":
        _require_str(ref.item_code or "", "item_code（financial_field 必填）")
        _require(ref.formula_id is None, "financial_field 不得携带 formula_id")
    else:
        _require_str(ref.formula_id or "", "formula_id（financial_metric 必填）")
        _require_str(ref.formula_version or "", "formula_version（financial_metric 必填）")
        _require(ref.item_code is None, "financial_metric 不得携带 item_code")


def validate_pack(pack: S.EvidencePack, authority: dict[str, dict] | None = None) -> None:
    """校验 EvidencePack：status/decision/failure_code 组合 + 结果结构与跨公司守卫。

    authority（可选）：evidence_id → {company_id, document_id, evidence_set_version,
    is_current}，由调用者从 Evidence Store 权威关系解析后传入；validator 只做结构化
    比较，不查询数据库。
    """
    _require(isinstance(pack, S.EvidencePack), "pack 必须是 EvidencePack")
    _require_str(pack.need_id, "need_id")
    _require(pack.status in S.EVIDENCE_PACK_STATUSES, f"非法 status: {pack.status!r}")

    # status/decision 组合校验（契约修正 B）。
    if pack.status in _REQUIRES_DECISION:
        _require(pack.route_decision is not None, f"{pack.status} 必须携带 RouteDecision")
    if pack.route_decision is not None:
        validate_decision(pack.route_decision)

    # failure_code 组合校验。
    if pack.failure_code is not None:
        _require(pack.failure_code in _VALID_FAILURE_CODES,
                 f"非法 failure_code: {pack.failure_code!r}")
    if pack.status == "FAILED":
        _require(pack.failure_code is not None, "FAILED 必须携带 failure_code")
    if pack.status == "COMPLETED":
        _require(pack.failure_code is None, "COMPLETED 不得携带失败码")
    if pack.status == "PARTIAL":
        _require(pack.failure_code in _SINGLE_CHANNEL_FAILURES,
                 "PARTIAL 必须携带单通道失败码（SPARSE_FAILED / DENSE_FAILED）")

    # 结果结构（契约修正 C）。
    _require(isinstance(pack.evidence, list), "evidence 必须是 list")
    _require(isinstance(pack.structured_results, list), "structured_results 必须是 list")
    for ref in pack.evidence:
        _validate_evidence_ref(ref)
    for ref in pack.structured_results:
        _validate_structured_ref(ref)
    if pack.status == "DB_RESULT_AVAILABLE":
        _require(not pack.evidence, "DB_RESULT_AVAILABLE 不得携带本地 evidence")
        _require(pack.structured_results, "DB_RESULT_AVAILABLE 必须携带 structured_results")
    if pack.status == "DB_FIELD_UNAVAILABLE":
        _require(not pack.evidence and not pack.structured_results,
                 "DB_FIELD_UNAVAILABLE 时两类结果列表必须为空")
        _require(pack.missing_requirements, "DB_FIELD_UNAVAILABLE 必须写明缺失项")

    _require(isinstance(pack.unresolved_conflicts, list), "unresolved_conflicts 必须是 list")
    _require(isinstance(pack.missing_requirements, list), "missing_requirements 必须是 list")

    # current Evidence Set 守卫（仅在调用者提供权威映射时执行）。
    # 跨公司比较由 validate_authority_company 单独承担（需要传入目标 company_id）。
    if authority is not None:
        _require(isinstance(authority, dict), "authority 必须是 dict")
        for ref in pack.evidence:
            info = authority.get(ref.evidence_id)
            _require(info is not None, f"evidence_id 无权威映射: {ref.evidence_id}")
            _require(info.get("document_id") == ref.document_id,
                     f"document_id 与权威映射不符: {ref.evidence_id}")
            _require(info.get("evidence_set_version") == ref.evidence_set_version,
                     f"evidence_set_version 与权威映射不符: {ref.evidence_id}")
            _require(info.get("is_current") is True,
                     f"Evidence Set 非 current: {ref.evidence_id}")


def validate_authority_company(pack: S.EvidencePack, company_id: str,
                               authority: dict[str, dict]) -> None:
    """跨公司守卫：每条 evidence 的权威 company_id 必须等于 company_id。

    独立于 validate_pack 提供，便于调用者先解析权威关系再校验；纯结构化比较。
    """
    for ref in pack.evidence:
        info = authority.get(ref.evidence_id)
        _require(info is not None, f"evidence_id 无权威映射: {ref.evidence_id}")
        _require(info.get("company_id") == company_id,
                 f"跨公司 Evidence: {ref.evidence_id} 属于 {info.get('company_id')!r}")
