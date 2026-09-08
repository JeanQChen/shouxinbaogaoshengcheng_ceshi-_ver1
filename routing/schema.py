"""Phase 2 Router + Hybrid Retrieval 公共契约（dataclass + 枚举白名单）。

本模块只定义声明式数据结构与枚举常量，不含 I/O、不含业务计算、不调用 Router。
所有 Router / Retriever / 评测层共同引用同一份字段语义，避免散落的中文名硬编码。

与上位设计的对齐：
- 五路由、EvidencePack 状态、reason code、failure code 来自 ROUTER_HYBRID 任务书 §4；
- `RouterResult` 承载 fallback 不可用/失败的语义，`RouteDecision.route` 永不接受 None；
- `RouteContext` 区分「能力支持」(supported_*) 与「数据可用」(available_*)，见编码前
  契约修正 A：Router 只看能力是否支持，不看当前值是否存在；
- `EvidencePack` 携带 `failure_code` 与 `structured_results`，DB 结果不得伪造成
  EvidenceRef，见契约修正 B/C。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

# Router 规则版本（进入 trace / 评测，规则变更需递增）。
RULE_VERSION = "v2-rule-1.0"


# ---------------------------------------------------------------------------
# 枚举白名单（冻结常量，与 evidence/schema.py 的声明式风格保持一致）
# ---------------------------------------------------------------------------

# 证据类型（与 evidence.schema.EVIDENCE_TYPES 对齐；routing 不依赖 evidence 层）。
EVIDENCE_TYPES = ("paragraph", "heading", "table", "table_row")

# 检索通道。
CHANNELS = ("sparse", "dense")

# 五路由。
ROUTES = (
    "DB_LOOKUP",
    "DIRECT_EVIDENCE",
    "STANDARD_RAG",
    "DEEP_RETRIEVAL",
    "EXTERNAL_RESEARCH",
)

# Router 结果状态。
ROUTER_STATUSES = ("DECIDED", "FALLBACK_UNAVAILABLE", "FAILED")

# EvidencePack 状态。
EVIDENCE_PACK_STATUSES = (
    "COMPLETED",
    "EMPTY",
    "PARTIAL",
    "DB_RESULT_AVAILABLE",
    "DB_FIELD_UNAVAILABLE",
    "EXTERNAL_RESEARCH_NOT_IMPLEMENTED",
    "ROUTER_FALLBACK_UNAVAILABLE",
    "FAILED",
)

# 路由判定依据（reason code）。
REASON_CODES = (
    "REGISTERED_DB_FIELD",
    "REGISTERED_FINANCIAL_METRIC",
    "EXACT_DOCUMENT_FIELD",
    "EXPLICIT_EXTERNAL_RECENCY",
    "CROSS_DOCUMENT_OR_CONFLICT",
    "SECTION_TOPIC_SYNTHESIS",
    "AMBIGUOUS_RULE_MATCH",
    "LLM_FALLBACK_DECISION",
    # Track A 公平对照：不调 Router，对全部 ELIGIBLE_LOCAL 用同一固定本地 Hybrid 决策。
    "TRACK_A_FIXED_LOCAL",
    # time_scope 无法可靠解析为可比期间 → 交 fallback（禁止字符串字典序猜测）。
    "TIME_SCOPE_UNPARSEABLE",
)

# 检索失败码。
FAILURE_CODES = (
    "INDEX_NOT_FOUND",
    "INDEX_VERSION_MISMATCH",
    "EVIDENCE_SET_NOT_CURRENT",
    "SPARSE_FAILED",
    "DENSE_FAILED",
    "BOTH_CHANNELS_FAILED",
    "EMPTY_AFTER_FILTER",
    "TIMEOUT",
    "DB_SNAPSHOT_UNAVAILABLE",
    "UNSUPPORTED_ROUTE",
    # trace 落盘失败（可观测性硬要求，fail-closed）。
    "TRACE_WRITE_FAILED",
)

# 判定置信度。
CONFIDENCE_LEVELS = ("high", "low")

# 决策来源。
DECIDED_BY = ("rule", "llm_fallback")

# 结构化结果类型（DB 结果不得伪造成 EvidenceRef）。
RESULT_TYPES = ("financial_field", "financial_metric")

# DB target 类型。
DB_TARGET_TYPES = ("field", "metric")

# 本地检索 metadata filter 白名单（retriever_v2 硬过滤 / 合法 filter 之外拒绝）。
LOCAL_FILTER_KEYS = (
    "company_id",
    "document_id",
    "document_version",
    "evidence_set_version",
    "source_type",
    "section",
    "period",
    "evidence_type",
)

# DB target 结构化字段（RouteDecision.filters 携带的可执行目标，见契约修正 C）。
#
# 契约修正 2：DB 查询区分两类期间——
#   - snapshot_as_of_date：用于选择 current snapshot（限定快照的 as_of_date）；
#   - target_period：用于在快照内选择 SnapshotItem / MetricResult 的 report_period；
# scope/currency/purpose 限定快照键；formula_version 来自 Formula Registry（禁止猜测）。
# 「period」自 Phase 2 Commit 2 起废弃，不再作为 DB target key。
DB_TARGET_KEYS = (
    "db_target_type",
    "standard_item_code",
    "formula_id",
    "formula_version",
    "snapshot_as_of_date",
    "target_period",
    "scope",
    "currency",
    "purpose",
)


# ---------------------------------------------------------------------------
# 校验异常
# ---------------------------------------------------------------------------

class RoutingValidationError(ValueError):
    """契约校验失败（fail-closed：抛错，不降级、不猜测）。"""


# ---------------------------------------------------------------------------
# 公共 dataclass
# ---------------------------------------------------------------------------

@dataclass
class InformationNeed:
    """一次信息需求（对应评测数据集一条 case 的检索侧投影）。"""

    need_id: str
    section_id: str
    question: str
    required_evidence_types: list[str]
    required_source_types: list[str]
    time_scope: str | None
    priority: str
    depends_on: list[str]


@dataclass
class RouteContext:
    """路由所需的静态能力清单（不执行检索、不预知 evidence_id 命中）。

    契约修正 A：supported_* 决定 route（能力是否支持），available_* 决定 DB executor
    返回结果还是 DB_FIELD_UNAVAILABLE（当前值是否可用）。两者来源不同，禁止混淆。
    """

    company_id: str
    report_as_of: str | None
    available_document_ids: list[str]
    available_source_types: list[str]
    supported_db_fields: list[str]
    supported_metric_ids: list[str]
    available_db_fields: list[str]
    available_metric_ids: list[str]
    external_research_enabled: bool
    # DB 取数限定维度（决定 current snapshot 键；executor 从 decision.filters 精确透传）。
    scope: str = "consolidated"
    currency: str = "CNY"
    purpose: str = "credit_analysis"


@dataclass
class RetrievalBudget:
    """一次检索的预算（Track A 唯一数据流下固定，不按路由变）。"""

    candidate_k_sparse: int
    candidate_k_dense: int
    fusion_k: int
    context_k: int
    timeout_ms: int


@dataclass
class RouteDecision:
    """一次路由决策（route 恒为五路由之一，永不接受 None）。"""

    need_id: str
    route: str
    reason_code: str
    filters: dict
    budget: RetrievalBudget
    fallback_routes: list[str]
    decided_by: str          # rule | llm_fallback
    rule_version: str
    confidence: str


@dataclass
class RouterResult:
    """Router 返回：用 status 区分「可判定」与「fallback 不可用/失败」。

    decision=None 仅在 status != DECIDED 时出现；禁止把 None 塞进 RouteDecision。
    """

    status: str              # DECIDED | FALLBACK_UNAVAILABLE | FAILED
    decision: RouteDecision | None
    error_code: str | None
    trace_id: str
    reason_code: str | None = None  # 触发 fallback 的原因（如 TIME_SCOPE_UNPARSEABLE）


@dataclass
class EvidenceRef:
    """一条已检索到的本地证据（来自 Evidence Store 的 current 证据块）。

    注意：与本模块同名的 evidence.schema.EvidenceRef 不同——那是证据引用守卫，
    这是携带融合分数/通道的检索结果。
    """

    evidence_id: str
    document_id: str
    evidence_set_version: str
    source_name: str
    source_type: str
    page_number: int | None
    evidence_type: str
    text: str
    structured_payload: dict | None
    score: float | None
    rank: int
    retrieval_channels: list[str]
    channel_ranks: dict[str, int]


@dataclass
class StructuredResultRef:
    """一条 DB_LOOKUP 结构化结果（财务字段/指标），不伪造成 EvidenceRef。

    契约修正 C：本地文档检索结果放 EvidencePack.evidence，DB 结果放
    EvidencePack.structured_results；两者 lineage 独立，供 Phase 4 Claim 分别引用。
    """

    result_type: str         # financial_field | financial_metric
    snapshot_id: str
    item_code: str | None
    formula_id: str | None
    formula_version: str | None
    period: str
    raw_value: str | None
    display_value: str | None
    unit: str | None
    status: str
    reason_code: str | None
    input_record_refs: list[str]
    input_snapshot_item_refs: list[str]


@dataclass
class EvidencePack:
    """一次信息需求的检索/取数结果。

    契约修正 B：route_decision 可空（仅 ROUTER_FALLBACK_UNAVAILABLE/FAILED 可无）；
    failure_code 承载失败码；structured_results 承载 DB 结构化结果。
    """

    need_id: str
    status: str
    route_decision: RouteDecision | None
    evidence: list[EvidenceRef] = field(default_factory=list)
    structured_results: list[StructuredResultRef] = field(default_factory=list)
    unresolved_conflicts: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    retrieval_trace_id: str = ""
    failure_code: str | None = None
