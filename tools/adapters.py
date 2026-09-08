"""Phase 3 Tool Layer 本地能力适配器（Evidence / Financial / Retrieval）。

把 Phase 2 的真实本地后端（retrieval.retriever_v2 / evidence.store / financial_v2 快照）
封装为 Registry 可注册的 executor。每个 executor 是 `arguments -> ToolResult` 的纯函数
（不落盘、不重试——这些由 Registry 承担），但会真实调用后端并产生可回查引用。

约束（PHASE3 任务书 §6.3 + 用户修订）：
- `lookup_company_field` 限定：不支持的字段 / 无快照 / 快照阻断 → DB_FIELD_UNAVAILABLE
  （修订 3），不返回字段可用的假阳性，也不把「不存在」写成「无风险」；
- `lookup_financial_metric` 同口径：指标不可用 → DB_FIELD_UNAVAILABLE；
- `compare_evidence` 纯 Python 结构化比较：一致/冲突/缺失/同文档/同期间的结构判定，
  不让 LLM 算数、不做价值判断（修订 4）；
- `search_tables` 能力门控：仅对真实 table/table_row Evidence 开放，无结构返回
  EMPTY/UNSUPPORTED_FOR_DOCUMENT，不从 paragraph 伪造表格坐标；
- 检索工具必须经 `retrieval.retriever_v2.retrieve`（不直连 Chroma 绕过 Trace）；
- 调用方需先 init evidence + financial_v2 两个 Store（与 retriever_v2 约定一致）。

外部工具（search_external_sources / fetch_external_content / snapshot_external_source）
由 `external_v2` 层负责（Batch A commit 4-6），不在此模块实现、不注册占位工具。

CLI:
    python -m tools.adapters search-evidence --company 300750 --query "实际控制人是谁"
    python -m tools.adapters inspect-evidence --evidence-id <evidence_id>
    python -m tools.adapters company-field --company 300750 --field TOTAL_ASSETS
    python -m tools.adapters financial-metric --company 300750 --formula SOLV_CURRENT_RATIO --period 2025-12-31
    python -m tools.adapters compare-evidence --evidence-id <id1> <id2>
    python -m tools.adapters search-tables --company 300750 --query "..."
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from pathlib import Path
from typing import Callable

from evidence import store as estore
from financial_v2 import progress
from financial_v2 import snapshots
from financial_v2 import store as fstore
from routing import context as routing_context
from routing import schema as S
from tools import contracts as C
from tools import registry as R

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 快照限定维度默认值（与 routing.context.RouteContext 默认一致）。
_DEFAULT_SCOPE = "consolidated"
_DEFAULT_CURRENCY = "CNY"
_DEFAULT_PURPOSE = "credit_analysis"

# 可用指标状态（与 retriever_v2 / routing.context 一致）。
_AVAILABLE_METRIC_STATUSES = ("CALCULATED_EXACT", "CALCULATED_PROXY")

# 检索工具内部使用的「传输预算」（真实路由由 Harness 在 Registry 层门控；这里固定走
# hybrid 检索路径，context_k 由调用参数 k 覆盖）。
_SEARCH_BUDGET = S.RetrievalBudget(
    candidate_k_sparse=20, candidate_k_dense=20, fusion_k=20, context_k=10,
    timeout_ms=5000)

# 检索工具的传输决策 route：三路本地路由在 retriever_v2 内都走同一 hybrid 路径，
# 语义区分在 Router/Harness 层；工具层固定为 STANDARD_RAG 作为通用本地检索。
_SEARCH_ROUTE = "STANDARD_RAG"
_SEARCH_REASON_CODE = "SECTION_TOPIC_SYNTHESIS"


# ---------------------------------------------------------------------------
# ToolSpec 定义
# ---------------------------------------------------------------------------

SEARCH_EVIDENCE_SPEC = C.ToolSpec(
    name="search_evidence", version="v1", description="本地 Evidence 混合检索（BM25+稠密→RRF）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "query"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1},
            "k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=20, timeout_ms=30000, retry_policy="none", cost_class="local",
)

INSPECT_EVIDENCE_SPEC = C.ToolSpec(
    name="inspect_evidence", version="v1",
    description="按 evidence_id 读取单条 Evidence 正文、来源、页码、版本",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["evidence_id"],
        "properties": {"evidence_id": {"type": "string", "minLength": 1}},
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=1, timeout_ms=2000, retry_policy="none", cost_class="local",
)

LOOKUP_COMPANY_FIELD_SPEC = C.ToolSpec(
    name="lookup_company_field", version="v1",
    description="从 current Financial Snapshot 读取标准科目字段值（StructuredResultRef）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "standard_item_code"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "standard_item_code": {"type": "string", "minLength": 1},
            "snapshot_as_of_date": {"type": "string"},
            "target_period": {"type": "string"},
            "scope": {"type": "string"},
            "currency": {"type": "string"},
            "purpose": {"type": "string"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DB_LOOKUP",),
    max_results=1, timeout_ms=3000, retry_policy="none", cost_class="db",
)

LOOKUP_FINANCIAL_METRIC_SPEC = C.ToolSpec(
    name="lookup_financial_metric", version="v1",
    description="从 current Financial Snapshot 读取已计算指标（数值/期间/口径/公式版本/溯源）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "formula_id"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "formula_id": {"type": "string", "minLength": 1},
            "formula_version": {"type": "string"},
            "snapshot_as_of_date": {"type": "string"},
            "target_period": {"type": "string"},
            "scope": {"type": "string"},
            "currency": {"type": "string"},
            "purpose": {"type": "string"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DB_LOOKUP",),
    max_results=1, timeout_ms=3000, retry_policy="none", cost_class="db",
)

COMPARE_EVIDENCE_SPEC = C.ToolSpec(
    name="compare_evidence", version="v1",
    description="纯 Python 结构化比较一组 Evidence（同文档/同期间/值一致/冲突/缺失），不做价值判断",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["evidence_ids"],
        "properties": {
            "evidence_ids": {
                "type": "array", "minItems": 2, "maxItems": 20,
                "items": {"type": "string", "minLength": 1},
            },
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=20, timeout_ms=3000, retry_policy="none", cost_class="local",
)

COMPARE_FINANCIAL_PERIODS_SPEC = C.ToolSpec(
    name="compare_financial_periods", version="v1",
    description="同公式跨期间 MetricResult 结构化比较（只比较既有结果，不新算指标，返回溯源）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "formula_id", "period_a", "period_b"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "formula_id": {"type": "string", "minLength": 1},
            "period_a": {"type": "string", "minLength": 1},
            "period_b": {"type": "string", "minLength": 1},
            "formula_version": {"type": "string"},
            "snapshot_as_of_date": {"type": "string"},
            "scope": {"type": "string"},
            "currency": {"type": "string"},
            "purpose": {"type": "string"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DB_LOOKUP",),
    max_results=1, timeout_ms=3000, retry_policy="none", cost_class="db",
)

SEARCH_TABLES_SPEC = C.ToolSpec(
    name="search_tables", version="v1",
    description="本地检索后仅返回 table/table_row Evidence；无表格结构返回 EMPTY/UNSUPPORTED_FOR_DOCUMENT",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "query"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "query": {"type": "string", "minLength": 1},
            "k": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=20, timeout_ms=30000, retry_policy="none", cost_class="local",
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _search_local(company_id: str, query: str, k: int) -> S.EvidencePack:
    """经 retriever_v2 执行一次本地 hybrid 检索（不直连 Chroma，保留 Trace）。

    懒加载 retriever_v2（重依赖：BGE-M3 / chromadb），避免 DB 工具路径被迫加载。
    """
    from retrieval import retriever_v2

    context = routing_context.build_route_context(company_id)
    need = S.InformationNeed(
        need_id="tool-" + uuid.uuid4().hex[:12], section_id="tool_harness",
        question=query, required_evidence_types=[], required_source_types=[],
        time_scope=None, priority="normal", depends_on=[])
    budget = S.RetrievalBudget(
        candidate_k_sparse=_SEARCH_BUDGET.candidate_k_sparse,
        candidate_k_dense=_SEARCH_BUDGET.candidate_k_dense,
        fusion_k=_SEARCH_BUDGET.fusion_k,
        context_k=min(k, 20),
        timeout_ms=_SEARCH_BUDGET.timeout_ms)
    decision = S.RouteDecision(
        need_id=need.need_id, route=_SEARCH_ROUTE, reason_code=_SEARCH_REASON_CODE,
        filters={}, budget=budget, fallback_routes=[], decided_by="rule",
        rule_version=S.RULE_VERSION, confidence="high")
    return retriever_v2.retrieve(need, decision, context)


def _pack_to_result(pack: S.EvidencePack, *, evidence_filter: tuple[str, ...] | None = None
                    ) -> C.ToolResult:
    """把 EvidencePack 翻译为 ToolResult（evidence 摘要 + 可回查 evidence_ids）。"""
    trace_id = pack.retrieval_trace_id or uuid.uuid4().hex
    if pack.status == "FAILED":
        if pack.failure_code == "TIMEOUT":
            return C.ToolResult(
                call_id="", tool_name="", tool_version="", status="RETRYABLE_ERROR",
                data={}, error_code="TOOL_TIMEOUT", message="本地检索超时",
                retryable=True, trace_id=trace_id)
        return C.ToolResult(
            call_id="", tool_name="", tool_version="", status="FATAL_ERROR",
            data={}, error_code="INTERNAL_ERROR",
            message=f"本地检索失败: {pack.failure_code}", retryable=False,
            trace_id=trace_id)

    evidence = pack.evidence
    if evidence_filter is not None:
        evidence = [r for r in evidence if r.evidence_type in evidence_filter]

    if not evidence:
        error_code = "RETRIEVAL_EMPTY"
        status = "EMPTY"
        if evidence_filter is not None:
            # 能力门控：存在检索结果但无表格结构（search_tables）。
            error_code = "UNSUPPORTED_FOR_DOCUMENT"
        return C.ToolResult(
            call_id="", tool_name="", tool_version="", status=status, data={},
            error_code=error_code,
            message=("无表格结构证据" if evidence_filter is not None else "本地检索无结果"),
            retryable=False, trace_id=trace_id)

    status = {"COMPLETED": "SUCCESS", "PARTIAL": "PARTIAL"}.get(pack.status, "PARTIAL")
    evidence_ids = [r.evidence_id for r in evidence]
    data = {
        "evidence_count": len(evidence_ids),
        "pack_status": pack.status,
        "items": [
            {
                "evidence_id": r.evidence_id,
                "source_name": r.source_name,
                "page_number": r.page_number,
                "evidence_type": r.evidence_type,
                "score": r.score,
                "rank": r.rank,
                "snippet": (r.text or "")[:200],
            }
            for r in evidence
        ],
        "missing_requirements": pack.missing_requirements,
    }
    return C.ToolResult(
        call_id="", tool_name="", tool_version="", status=status, data=data,
        evidence_ids=evidence_ids, error_code=None, message=None, retryable=False,
        trace_id=trace_id)


def _resolve_current_snapshot(args: dict) -> tuple[S.FinancialSnapshot | None, str | None]:
    """解析 current 快照；返回 (snapshot, unavailable_reason)。失败不抛错，返回原因。"""
    company_id = args["company_id"]
    scope = args.get("scope") or _DEFAULT_SCOPE
    currency = args.get("currency") or _DEFAULT_CURRENCY
    purpose = args.get("purpose") or _DEFAULT_PURPOSE
    as_of = args.get("snapshot_as_of_date")
    if not as_of:
        try:
            req = progress.build_request_for_company(
                company_id, scope=scope, currency=currency, purpose=purpose)
            as_of = req.as_of_date
        except ValueError as e:
            return None, f"无 current Record Set，无法定位快照: {e}"
    snap = snapshots.current_snapshot(company_id, scope, currency, as_of, purpose)
    if snap is None:
        return None, "无当前快照"
    if snap.report_blocked:
        return None, "快照被阻断（report_blocked），字段不可用"
    return snap, None


def _db_unavailable(tool_name: str, reason: str, retryable: bool = False) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=tool_name, tool_version="", status="EMPTY", data={},
        error_code="DB_FIELD_UNAVAILABLE", message=reason, retryable=retryable,
        trace_id=uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------

def _search_evidence_executor(args: dict) -> C.ToolResult:
    pack = _search_local(args["company_id"], args["query"], args.get("k", 5))
    return _pack_to_result(pack)


def _search_tables_executor(args: dict) -> C.ToolResult:
    pack = _search_local(args["company_id"], args["query"], args.get("k", 5))
    return _pack_to_result(pack, evidence_filter=("table", "table_row"))


def _inspect_evidence_executor(args: dict) -> C.ToolResult:
    eid = args["evidence_id"]
    b = estore.get_evidence(eid)
    if b is None:
        return C.ToolResult(
            call_id="", tool_name="inspect_evidence", tool_version="", status="EMPTY",
            data={}, error_code="RETRIEVAL_EMPTY", message=f"证据不存在: {eid}",
            retryable=False, trace_id=uuid.uuid4().hex)
    data = {
        "evidence_id": b.evidence_id,
        "document_id": b.document_id,
        "document_version": b.document_version,
        "evidence_set_version": b.evidence_set_version,
        "source_name": b.source_name,
        "source_type": b.source_type,
        "page_number": b.page_number,
        "section_path": b.section_path,
        "evidence_type": b.evidence_type,
        "report_period": b.report_period,
        "published_at": b.published_at,
        "text": b.text,
        "structured_payload": b.structured_payload,
    }
    return C.ToolResult(
        call_id="", tool_name="inspect_evidence", tool_version="", status="SUCCESS",
        data=data, evidence_ids=[eid], error_code=None, message=None, retryable=False,
        trace_id=uuid.uuid4().hex)


def _lookup_company_field_executor(args: dict) -> C.ToolResult:
    code = args["standard_item_code"]
    snap, reason = _resolve_current_snapshot(args)
    if snap is None:
        return _db_unavailable("lookup_company_field", reason)
    target_period = args.get("target_period")
    for it in fstore.list_snapshot_items(snap.snapshot_id):
        if it.standard_item_code != code or it.amount is None:
            continue
        if target_period and it.report_period != target_period:
            continue
        ref = S.StructuredResultRef(
            result_type="financial_field", snapshot_id=snap.snapshot_id,
            item_code=code, formula_id=None, formula_version=None,
            period=it.report_period, raw_value=str(it.amount),
            display_value=str(it.amount), unit=it.unit, status="available",
            reason_code=None, input_record_refs=it.source_refs,
            input_snapshot_item_refs=[it.comparison_key])
        data = {
            "result_type": "financial_field", "item_code": code,
            "period": it.report_period, "display_value": str(it.amount),
            "unit": it.unit, "status": "available",
        }
        return C.ToolResult(
            call_id="", tool_name="lookup_company_field", tool_version="",
            status="SUCCESS", data=data, structured_result_refs=[ref],
            error_code=None, message=None, retryable=False, trace_id=uuid.uuid4().hex)
    suffix = f"（期间 {target_period}）" if target_period else ""
    return _db_unavailable("lookup_company_field", f"字段不可用: {code}{suffix}")


def _lookup_financial_metric_executor(args: dict) -> C.ToolResult:
    formula_id = args["formula_id"]
    formula_version = args.get("formula_version")
    snap, reason = _resolve_current_snapshot(args)
    if snap is None:
        return _db_unavailable("lookup_financial_metric", reason)
    target_period = args.get("target_period")
    for mr in fstore.list_metric_results(snap.snapshot_id):
        if mr.formula_id != formula_id or mr.status not in _AVAILABLE_METRIC_STATUSES:
            continue
        if target_period and mr.period != target_period:
            continue
        if formula_version and mr.formula_version != formula_version:
            continue
        ref = S.StructuredResultRef(
            result_type="financial_metric", snapshot_id=snap.snapshot_id,
            item_code=None, formula_id=formula_id, formula_version=mr.formula_version,
            period=mr.period, raw_value=str(mr.raw_value) if mr.raw_value is not None else None,
            display_value=str(mr.display_value) if mr.display_value is not None else None,
            unit=mr.unit, status=mr.status, reason_code=mr.reason_code,
            input_record_refs=mr.input_record_refs,
            input_snapshot_item_refs=mr.input_snapshot_item_refs)
        data = {
            "result_type": "financial_metric", "formula_id": formula_id,
            "formula_version": mr.formula_version, "period": mr.period,
            "display_value": mr.display_value, "unit": mr.unit, "status": mr.status,
        }
        return C.ToolResult(
            call_id="", tool_name="lookup_financial_metric", tool_version="",
            status="SUCCESS", data=data, structured_result_refs=[ref],
            error_code=None, message=None, retryable=False, trace_id=uuid.uuid4().hex)
    suffix = f"（期间 {target_period}）" if target_period else ""
    return _db_unavailable("lookup_financial_metric", f"指标不可用: {formula_id}{suffix}")


def _comparable_values(block) -> list[str]:
    """从 structured_payload 提取可比较的标量（仅字符串化，不做算术）。"""
    p = getattr(block, "structured_payload", None) or {}
    return [f"{k}={p[k]}" for k in sorted(p)
            if isinstance(p[k], (str, int, float)) and not isinstance(p[k], bool)]


def _value_relation(bl, br, same_period: bool) -> str:
    """结构化值关系：equal / conflict（同期间值不同）/ different（异期间）/ not_comparable。"""
    lv, rv = _comparable_values(bl), _comparable_values(br)
    if not lv or not rv:
        return "not_comparable"
    if lv == rv:
        return "equal"
    return "conflict" if same_period else "different"


def _compare_evidence_executor(args: dict) -> C.ToolResult:
    ids = args["evidence_ids"]
    blocks: dict[str, object] = {}
    missing: list[str] = []
    for eid in ids:
        b = estore.get_evidence(eid)
        if b is None:
            missing.append(eid)
        else:
            blocks[eid] = b

    present = [eid for eid in ids if eid in blocks]
    pairs: list[dict] = []
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            l, r = present[i], present[j]
            bl, br = blocks[l], blocks[r]
            same_doc = (bl.document_id == br.document_id
                        and bl.document_version == br.document_version)
            same_period = (bl.report_period is not None
                           and bl.report_period == br.report_period)
            pairs.append({
                "left": l, "right": r,
                "same_document": same_doc,
                "same_period": same_period,
                "evidence_types": [bl.evidence_type, br.evidence_type],
                "value_relation": _value_relation(bl, br, same_period),
            })

    documents: dict[str, list[str]] = {}
    for eid in present:
        documents.setdefault(blocks[eid].document_id, []).append(eid)

    data = {
        "evidence_count": len(ids),
        "missing_ids": missing,
        "documents": documents,
        "pairs": pairs,
    }
    status = "SUCCESS" if not missing else "PARTIAL"
    return C.ToolResult(
        call_id="", tool_name="compare_evidence", tool_version="", status=status,
        data=data, evidence_ids=present,
        error_code=None, message=("部分证据缺失" if missing else None),
        retryable=False, trace_id=uuid.uuid4().hex)


def _metric_ref(mr, formula_id: str) -> S.StructuredResultRef:
    """把 MetricResult 翻译为可回查 StructuredResultRef（溯源快照/公式版本/期间/输入）。"""
    return S.StructuredResultRef(
        result_type="financial_metric", snapshot_id=mr.snapshot_id,
        item_code=None, formula_id=formula_id, formula_version=mr.formula_version,
        period=mr.period,
        raw_value=str(mr.raw_value) if mr.raw_value is not None else None,
        display_value=str(mr.display_value) if mr.display_value is not None else None,
        unit=mr.unit, status=mr.status, reason_code=mr.reason_code,
        input_record_refs=mr.input_record_refs,
        input_snapshot_item_refs=mr.input_snapshot_item_refs)


def _compare_financial_periods_executor(args: dict) -> C.ToolResult:
    """同公式跨期间 MetricResult 结构化比较（修订：只比较既有结果，不新算指标）。"""
    formula_id = args["formula_id"]
    period_a = args["period_a"]
    period_b = args["period_b"]
    formula_version = args.get("formula_version")
    snap, reason = _resolve_current_snapshot(args)
    if snap is None:
        return _db_unavailable("compare_financial_periods", reason)

    def _find(period):
        for mr in fstore.list_metric_results(snap.snapshot_id):
            if mr.formula_id != formula_id or mr.status not in _AVAILABLE_METRIC_STATUSES:
                continue
            if mr.period != period:
                continue
            if formula_version and mr.formula_version != formula_version:
                continue
            return mr
        return None

    mra = _find(period_a)
    mrb = _find(period_b)
    if mra is None and mrb is None:
        return _db_unavailable("compare_financial_periods",
                               f"指标 {formula_id} 在两期间均不可用")

    base = {
        "result_type": "financial_metric_comparison",
        "formula_id": formula_id,
        "snapshot_id": snap.snapshot_id,
        "period_a": period_a,
        "period_b": period_b,
    }

    # 单边缺失 → PARTIAL（合法结果，非错误），显式标出缺失期间。
    if mra is None or mrb is None:
        present = mra if mra is not None else mrb
        missing_period = period_a if mra is None else period_b
        data = {
            **base,
            "formula_version": present.formula_version,
            "a_display_value": _display_of(mra),
            "b_display_value": _display_of(mrb),
            "relation": "missing_period",
            "missing_period": missing_period,
        }
        refs = [_metric_ref(present, formula_id)]
        return C.ToolResult(
            call_id="", tool_name="compare_financial_periods", tool_version="",
            status="PARTIAL", data=data, structured_result_refs=refs,
            error_code=None, message=f"期间 {missing_period} 无指标 {formula_id}",
            retryable=False, trace_id=uuid.uuid4().hex)

    # 公式版本不一致 → fail-closed，不跨版本比较。
    if mra.formula_version != mrb.formula_version:
        return C.ToolResult(
            call_id="", tool_name="compare_financial_periods", tool_version="",
            status="FATAL_ERROR", data=base,
            error_code="TOOL_CONTRACT_ERROR",
            message=(f"两期间公式版本不一致: {period_a}={mra.formula_version} "
                     f"vs {period_b}={mrb.formula_version}"),
            retryable=False, trace_id=uuid.uuid4().hex)

    av = _decimal_value(mra)
    bv = _decimal_value(mrb)
    if av is None or bv is None:
        return _db_unavailable("compare_financial_periods",
                               f"指标 {formula_id} 两期间无值可比较")

    delta = bv - av
    if delta > 0:
        relation = "increased"
    elif delta < 0:
        relation = "decreased"
    else:
        relation = "unchanged"

    data = {
        **base,
        "formula_version": mra.formula_version,
        "a_display_value": str(av),
        "a_unit": mra.unit,
        "b_display_value": str(bv),
        "b_unit": mrb.unit,
        "delta": str(delta),
        "relation": relation,
    }
    return C.ToolResult(
        call_id="", tool_name="compare_financial_periods", tool_version="",
        status="SUCCESS", data=data,
        structured_result_refs=[_metric_ref(mra, formula_id), _metric_ref(mrb, formula_id)],
        error_code=None, message=None, retryable=False, trace_id=uuid.uuid4().hex)


def _display_of(mr) -> str | None:
    """MetricResult 展示值（display 优先，回退 raw；均无则 None）。"""
    if mr is None:
        return None
    v = _decimal_value(mr)
    return str(v) if v is not None else None


def _decimal_value(mr) -> "object | None":
    """MetricResult 数值（display 优先回退 raw，Decimal 或 None）。"""
    v = mr.display_value if mr.display_value is not None else mr.raw_value
    return v


# ---------------------------------------------------------------------------
# Registry 构建
# ---------------------------------------------------------------------------

def build_default_registry(audit_dir: Path | str = R.DEFAULT_AUDIT_DIR) -> R.ToolRegistry:
    """构建默认 Registry（6 个本地工具 + 1 个财务比较 + 3 个外部工具）。

    外部工具（search_external_sources / fetch_external_content / snapshot_external_source）
    由 tools.external_adapters 注册（provider 恒为 bocha，Tavily 不参与运行时）。
    """
    from tools import external_adapters as ext

    reg = R.ToolRegistry(audit_dir=audit_dir)
    reg.register(SEARCH_EVIDENCE_SPEC, _search_evidence_executor)
    reg.register(INSPECT_EVIDENCE_SPEC, _inspect_evidence_executor)
    reg.register(LOOKUP_COMPANY_FIELD_SPEC, _lookup_company_field_executor)
    reg.register(LOOKUP_FINANCIAL_METRIC_SPEC, _lookup_financial_metric_executor)
    reg.register(COMPARE_EVIDENCE_SPEC, _compare_evidence_executor)
    reg.register(COMPARE_FINANCIAL_PERIODS_SPEC, _compare_financial_periods_executor)
    reg.register(SEARCH_TABLES_SPEC, _search_tables_executor)
    ext.register_external_tools(reg)
    return reg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _result_to_dict(result: C.ToolResult) -> dict:
    return {
        "tool_name": result.tool_name,
        "status": result.status,
        "error_code": result.error_code,
        "message": result.message,
        "latency_ms": result.latency_ms,
        "evidence_ids": result.evidence_ids,
        "structured_result_refs": [dataclasses.asdict(r) for r in result.structured_result_refs],
        "external_snapshot_ids": result.external_snapshot_ids,
        "data": result.data,
    }


def _execute_cli(tool_name: str, arguments: dict, route: str, audit_dir: str) -> C.ToolResult:
    reg = build_default_registry(audit_dir=audit_dir)
    call = C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=arguments,
        idempotency_key=uuid.uuid4().hex, need_id="cli", batch_id="cli")
    return reg.execute(call, route=route, run_id="cli")


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m tools.adapters",
        description="本地工具适配器 CLI（经 Registry 执行，落盘 audit）")
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH))
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH))
    parser.add_argument("--audit-dir", default=str(R.DEFAULT_AUDIT_DIR))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_se = sub.add_parser("search-evidence", help="本地 Evidence 混合检索")
    p_se.add_argument("--company", required=True, dest="company_id")
    p_se.add_argument("--query", required=True)
    p_se.add_argument("--k", type=int, default=5)

    p_st = sub.add_parser("search-tables", help="本地表格 Evidence 检索（能力门控）")
    p_st.add_argument("--company", required=True, dest="company_id")
    p_st.add_argument("--query", required=True)
    p_st.add_argument("--k", type=int, default=5)

    p_ie = sub.add_parser("inspect-evidence", help="按 evidence_id 查看单条 Evidence")
    p_ie.add_argument("--evidence-id", required=True, dest="evidence_id")

    p_cf = sub.add_parser("company-field", help="查询标准科目字段值")
    p_cf.add_argument("--company", required=True, dest="company_id")
    p_cf.add_argument("--field", required=True, dest="standard_item_code")
    p_cf.add_argument("--period", dest="target_period", default=None)
    p_cf.add_argument("--as-of", dest="snapshot_as_of_date", default=None)

    p_fm = sub.add_parser("financial-metric", help="查询已计算财务指标")
    p_fm.add_argument("--company", required=True, dest="company_id")
    p_fm.add_argument("--formula", required=True, dest="formula_id")
    p_fm.add_argument("--version", dest="formula_version", default=None)
    p_fm.add_argument("--period", dest="target_period", default=None)
    p_fm.add_argument("--as-of", dest="snapshot_as_of_date", default=None)

    p_ce = sub.add_parser("compare-evidence", help="结构化比较一组 Evidence")
    p_ce.add_argument("--evidence-id", required=True, dest="evidence_ids",
                      nargs="+", metavar="EVIDENCE_ID")

    p_cfp = sub.add_parser("compare-financial-periods", help="同公式跨期间指标比较")
    p_cfp.add_argument("--company", required=True, dest="company_id")
    p_cfp.add_argument("--formula", required=True, dest="formula_id")
    p_cfp.add_argument("--period-a", required=True, dest="period_a")
    p_cfp.add_argument("--period-b", required=True, dest="period_b")
    p_cfp.add_argument("--version", dest="formula_version", default=None)
    p_cfp.add_argument("--as-of", dest="snapshot_as_of_date", default=None)

    p_xs = sub.add_parser("search-external", help="博查外部检索（经 Registry）")
    p_xs.add_argument("--query", required=True)
    p_xs.add_argument("--limit", type=int, default=5)
    p_xs.add_argument("--freshness", default=None)

    p_xf = sub.add_parser("fetch-external", help="安全抓取网页/电子 PDF 正文（经 Registry）")
    p_xf.add_argument("--url", required=True)

    p_snap = sub.add_parser("snapshot-external", help="固化外部来源不可变快照（经 Registry）")
    p_snap.add_argument("--company", required=True, dest="company_id")
    p_snap.add_argument("--url", required=True, dest="canonical_url")
    p_snap.add_argument("--content-text", required=True, dest="content_text")
    p_snap.add_argument("--title", default=None)
    p_snap.add_argument("--snippet", default=None)
    p_snap.add_argument("--query", default=None)
    p_snap.add_argument("--provider", default=None)
    p_snap.add_argument("--content-type", default=None)
    p_snap.add_argument("--http-status", type=int, default=None)
    p_snap.add_argument("--content-hash", default=None)
    p_snap.add_argument("--source-grade", default=None)
    p_snap.add_argument("--file-hash", default=None)
    p_snap.add_argument("--page-count", type=int, default=None)

    args = parser.parse_args(argv)

    fstore.init_db(args.fin_db)
    estore.init_db(args.ev_db)

    route_by_cmd = {
        "search-evidence": "STANDARD_RAG",
        "search-tables": "STANDARD_RAG",
        "inspect-evidence": "DIRECT_EVIDENCE",
        "compare-evidence": "DIRECT_EVIDENCE",
        "company-field": "DB_LOOKUP",
        "financial-metric": "DB_LOOKUP",
        "compare-financial-periods": "DB_LOOKUP",
        "search-external": "EXTERNAL_RESEARCH",
        "fetch-external": "EXTERNAL_RESEARCH",
        "snapshot-external": "EXTERNAL_RESEARCH",
    }
    tool_by_cmd = {
        "search-evidence": "search_evidence",
        "search-tables": "search_tables",
        "inspect-evidence": "inspect_evidence",
        "compare-evidence": "compare_evidence",
        "company-field": "lookup_company_field",
        "financial-metric": "lookup_financial_metric",
        "compare-financial-periods": "compare_financial_periods",
        "search-external": "search_external_sources",
        "fetch-external": "fetch_external_content",
        "snapshot-external": "snapshot_external_source",
    }

    arguments: dict
    if args.cmd == "search-evidence":
        arguments = {"company_id": args.company_id, "query": args.query, "k": args.k}
    elif args.cmd == "search-tables":
        arguments = {"company_id": args.company_id, "query": args.query, "k": args.k}
    elif args.cmd == "inspect-evidence":
        arguments = {"evidence_id": args.evidence_id}
    elif args.cmd == "company-field":
        arguments = {"company_id": args.company_id,
                     "standard_item_code": args.standard_item_code}
        if args.target_period:
            arguments["target_period"] = args.target_period
        if args.snapshot_as_of_date:
            arguments["snapshot_as_of_date"] = args.snapshot_as_of_date
    elif args.cmd == "financial-metric":
        arguments = {"company_id": args.company_id, "formula_id": args.formula_id}
        if args.formula_version:
            arguments["formula_version"] = args.formula_version
        if args.target_period:
            arguments["target_period"] = args.target_period
        if args.snapshot_as_of_date:
            arguments["snapshot_as_of_date"] = args.snapshot_as_of_date
    elif args.cmd == "compare-evidence":
        arguments = {"evidence_ids": args.evidence_ids}
    elif args.cmd == "compare-financial-periods":
        arguments = {"company_id": args.company_id, "formula_id": args.formula_id,
                     "period_a": args.period_a, "period_b": args.period_b}
        if args.formula_version:
            arguments["formula_version"] = args.formula_version
        if args.snapshot_as_of_date:
            arguments["snapshot_as_of_date"] = args.snapshot_as_of_date
    elif args.cmd == "search-external":
        arguments = {"query": args.query, "limit": args.limit}
        if args.freshness:
            arguments["freshness"] = args.freshness
    elif args.cmd == "fetch-external":
        arguments = {"url": args.url}
    elif args.cmd == "snapshot-external":
        arguments = {"company_id": args.company_id, "canonical_url": args.canonical_url,
                     "content_text": args.content_text}
        for key in ("title", "snippet", "query", "provider", "content_type",
                    "content_hash", "source_grade", "file_hash"):
            if getattr(args, key) is not None:
                arguments[key] = getattr(args, key)
        if args.http_status is not None:
            arguments["http_status"] = args.http_status
        if args.page_count is not None:
            arguments["page_count"] = args.page_count
    else:
        return 2

    result = _execute_cli(tool_by_cmd[args.cmd], arguments, route_by_cmd[args.cmd], args.audit_dir)
    print(json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2))
    return 0 if not result.is_error() else 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
