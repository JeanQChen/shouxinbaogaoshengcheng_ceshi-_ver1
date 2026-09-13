"""R1-B：唯一 TopicResearchPack typed schema + AspectV2 冻结投影 + 三类权威/locator 联合
+ 双轴 Pack 状态 + ResearchOutcome 兼容输入边界。

纯声明式模块：不 I/O、不写库、不调 LLM / Router / ToolRegistry / 网络。它只被
``harness/topic_store.py`` / ``harness/topic_checkpoint.py`` / ``harness/topic_store_cli.py``
与离线测试引用。

设计要点（R1B_IMPLEMENTATION_PLAN.md + 编码前最终收口裁决）：

- 所有类型 ``@dataclass(frozen=True)``（不可变）。
- 每个类型配 ``to_dict`` / ``from_dict``；``from_dict`` 对未知字段、缺必填字段、非法枚举、
  错误嵌套类型全部 fail-closed（抛 ``SchemaValidationError``）。
- 状态 / discriminator / 枚举一律严格解析（不允许自由字符串），跨状态空间同名字符串被拒绝。
- JSON canonical 序列化确定（sort_keys、Decimal→str）；内容身份（pack_id）不含 run_id /
  timestamp / call_id / 日志路径 / 隐藏推理。
- ``required`` 语义不落字段（§五 强制裁决）：一个 aspect 是否 required 由「是否属于本 Topic
  冻结 requirement 集合」确定，与 display_tier / content_role / 是否检索到内容无关。

状态空间隔离（架构约束 1）：aspect 状态 / Pack 双轴状态与 ``harness.schema`` 的
QuestionStatus / CompletionStatus / EntailmentVerdict 是不同状态空间，禁止字符串名互通。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from contracts.schema_v2 import (
    REQUIRED_ASPECT_FIELDS,
    SOURCE_GRADES,
)
from harness.schema import CITATION_TYPES, COMPLETION_STATUSES, ENTAILMENT_VERDICTS

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

# TopicResearchPack 序列化 schema 版本（to_dict/from_dict 契约版本）。
TOPIC_PACK_SCHEMA_VERSION = "1"
# StatusDerivation 推导规则版本（derive_pack_status 语义版本）。
STATUS_DERIVATION_RULE_VERSION = "1"
# 依赖版本字典允许的键（不允许任意语义 dict）。
DEPENDENCY_VERSION_KEYS = (
    "contract",
    "source_policy",
    "topic_schema",
    "assessor",
    "validator",
)

# ---------------------------------------------------------------------------
# 枚举白名单（本状态空间）
# ---------------------------------------------------------------------------

# aspect 层结果状态（5 态；not_found 只属于 aspect 层，不是 Pack 流程状态）。
ASPECT_RESULT_STATUSES = (
    "covered",
    "partial",
    "not_found",
    "blocked",
    "not_applicable",
)

# Pack 双轴之一：研究流程是否已停止（正交于 coverage）。
PACK_PROCESS_STATUSES = (
    "pending",
    "running",
    "finished",
    "stopped_by_budget",
    "blocked",
    "failed",
)

# Pack 双轴之二：required aspect 内容覆盖（正交于 process）。
PACK_COVERAGE_STATUSES = (
    "complete",
    "complete_with_gaps",
    "insufficient",
    "unavailable",
)

# material 类型（与 locator / authority 变体严格绑定）。
MATERIAL_TYPES = (
    "evidence_span",
    "table_context",
    "structured",
    "external_snapshot",
)

# 已采用事实类别（镜像 harness.schema.CLAIM_KINDS 的 fact/inference 子集；
# retrieval_observation 是运行时诊断，不得作为 adopted fact）。
FACT_TYPES = ("fact", "inference")

# 三类来源权威 discriminator / locator discriminator。
AUTHORITY_TYPES = ("evidence", "financial_snapshot", "external_snapshot")

# 权威结论（authority gate 输出，供关键结论 sufficiency 参考；不合并三类来源判据）。
AUTHORITY_VERDICTS = ("authoritative", "supplemental_only", "rejected")

# 财务快照有效性命中值（FinancialSnapshotAuthorityAssessment.validity）。
FINANCIAL_VALIDITIES = ("valid", "stale", "superseded", "invalid")

# 冲突类别与状态。
CONFLICT_CATEGORIES = (
    "value_conflict",
    "source_conflict",
    "scope_conflict",
    "period_conflict",
    "unit_conflict",
)
CONFLICT_STATUSES = ("open", "resolved", "escalated")

# 缺口原因码与影响范围（impact 沿用契约 IMPACT_SCOPES 语义）。
GAP_REASON_CODES = (
    "not_found",
    "blocked",
    "budget_exhausted",
    "source_rejected",
    "unresolved",
)
GAP_IMPACTS = ("subject", "solvency", "key_financial", "credit_scheme")

# 运行消耗 metric 枚举（UsageEntry.metric）。
USAGE_METRICS = (
    "rounds",
    "tool_calls",
    "local_searches",
    "external_searches",
    "fetches",
    "snapshots",
    "llm_calls",
    "input_tokens",
    "output_tokens",
    "elapsed_ms",
)

# 预算耗尽 / 硬 block / 运行期失败 的停止原因（derive_pack_status 用于 process 轴判定）。
_BUDGET_STOP_PREFIXES = ("BUDGET",)
_BLOCK_STOP_CODES = ("PATH_NOT_IMPLEMENTED", "REPORT_BLOCKED", "SECTION_BLOCKED", "JOB_BLOCKED")
_FATAL_STOP_CODES = (
    "ACTION_SCHEMA_INVALID",
    "MODEL_OUTPUT_INVALID",
    "FATAL_TOOL_ERROR",
    "VERSION_INCOMPATIBLE",
    "SESSION_POISONED",
)

# material type → locator discriminator / authority discriminator 的严格绑定。
_LOCATOR_TYPE_BY_MATERIAL = {
    "evidence_span": "evidence",
    "table_context": "evidence",
    "structured": "financial_snapshot",
    "external_snapshot": "external_snapshot",
}
_AUTHORITY_TYPE_BY_MATERIAL = {
    "evidence_span": "evidence",
    "table_context": "evidence",
    "structured": "financial_snapshot",
    "external_snapshot": "external_snapshot",
}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class SchemaValidationError(ValueError):
    """schema 反序列化 / 构造 fail-closed（未知字段、缺必填、非法枚举、错误嵌套类型）。"""


class StateAdaptationError(ValueError):
    """状态空间适配失败：输入不在已知枚举内，绝不静默映射为「合格」。"""


# ---------------------------------------------------------------------------
# canonical 序列化 / 指纹
# ---------------------------------------------------------------------------

def _to_json_value(v: Any) -> Any:
    """把 frozen 对象图转换为 JSON 安全 primitive（Decimal→str，tuple→list，嵌套→to_dict）。"""
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, tuple):
        return [_to_json_value(x) for x in v]
    if isinstance(v, list):
        return [_to_json_value(x) for x in v]
    if isinstance(v, (frozenset, set)):
        return sorted((_to_json_value(x) for x in v), key=lambda s: str(s))
    if isinstance(v, dict):
        return {k: _to_json_value(x) for k, x in v.items()}
    if hasattr(v, "to_dict"):
        return v.to_dict()
    raise TypeError(f"不可序列化类型: {type(v).__name__}")


def canonical_json(obj: Any) -> str:
    """确定性 JSON 字符串（sort_keys、Decimal→str、ensure_ascii=False）。"""
    return json.dumps(_to_json_value(obj), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def sha256_canonical(obj: Any) -> str:
    """对对象图求稳定 sha256（确定性规范形）。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _is_sha256_hex(v: str) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


# ---------------------------------------------------------------------------
# from_dict 解析助手（全部 fail-closed）
# ---------------------------------------------------------------------------

def _reject_unknown(d: Any, allowed: set[str], typename: str) -> dict:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"{typename}.from_dict 需要 dict，得到 {type(d).__name__}")
    unknown = set(d) - allowed
    if unknown:
        raise SchemaValidationError(f"{typename} 含未知字段: {sorted(unknown)}")
    return d


def _get_str(d: dict, key: str, typename: str, allow_none: bool = False,
             allow_empty: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if allow_none:
            return None
        raise SchemaValidationError(f"{typename} 缺必填字段: {key}")
    if not isinstance(v, str):
        raise SchemaValidationError(f"{typename}.{key} 必须为字符串，得到 {type(v).__name__}")
    if v == "" and not allow_empty:
        raise SchemaValidationError(f"{typename}.{key} 必须为非空字符串")
    return v


def _get_bool(d: dict, key: str, typename: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 bool，得到 {v!r}")
    return v


def _get_int(d: dict, key: str, typename: str, allow_none: bool = False) -> int | None:
    v = d.get(key)
    if v is None and allow_none:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise SchemaValidationError(f"{typename}.{key} 必须为 int，得到 {v!r}")
    return v


def _get_str_tuple(d: dict, key: str, typename: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        return default
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list[str]，得到 {type(v).__name__}")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str):
            raise SchemaValidationError(f"{typename}.{key} 含非字符串元素: {x!r}")
        out.append(x)
    return tuple(out)


def _get_int_pair(d: dict, key: str, typename: str, allow_none: bool = False) -> tuple[int, int] | None:
    v = d.get(key)
    if v is None:
        if allow_none:
            return None
        raise SchemaValidationError(f"{typename} 缺必填字段: {key}")
    if (not isinstance(v, list) or len(v) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise SchemaValidationError(f"{typename}.{key} 必须为 [int, int]，得到 {v!r}")
    return (v[0], v[1])


def _get_enum(v: str, allowed: tuple[str, ...], typename: str, key: str) -> str:
    if v not in allowed:
        raise SchemaValidationError(
            f"{typename}.{key} 非法枚举 {v!r}（允许 {allowed}）")
    return v


# ---------------------------------------------------------------------------
# 冻结投影引用类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRequirementRef:
    """证据需求权威引用（绑定 requirement ID + 所属 Contract SHA + requirement 指纹 + schema/version）。"""

    requirement_id: str
    contract_sha256: str
    requirement_fingerprint: str
    schema_version: str

    def __post_init__(self) -> None:
        if not self.requirement_id:
            raise SchemaValidationError("EvidenceRequirementRef.requirement_id 必须非空")
        if not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("EvidenceRequirementRef.contract_sha256 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.requirement_fingerprint):
            raise SchemaValidationError("EvidenceRequirementRef.requirement_fingerprint 必须为 64 位 sha256 hex")
        if not self.schema_version:
            raise SchemaValidationError("EvidenceRequirementRef.schema_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id,
            "contract_sha256": self.contract_sha256,
            "requirement_fingerprint": self.requirement_fingerprint,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceRequirementRef":
        d = _reject_unknown(d, {"requirement_id", "contract_sha256", "requirement_fingerprint", "schema_version"},
                            "EvidenceRequirementRef")
        return cls(
            requirement_id=_get_str(d, "requirement_id", "EvidenceRequirementRef"),
            contract_sha256=_get_str(d, "contract_sha256", "EvidenceRequirementRef"),
            requirement_fingerprint=_get_str(d, "requirement_fingerprint", "EvidenceRequirementRef"),
            schema_version=_get_str(d, "schema_version", "EvidenceRequirementRef"),
        )


@dataclass(frozen=True)
class SourcePolicyRef:
    """来源政策权威引用（绑定 policy version + content fingerprint）。"""

    policy_id: str
    policy_version: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise SchemaValidationError("SourcePolicyRef.policy_id 必须非空")
        if not self.policy_version:
            raise SchemaValidationError("SourcePolicyRef.policy_version 必须非空")
        if not _is_sha256_hex(self.content_fingerprint):
            raise SchemaValidationError("SourcePolicyRef.content_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourcePolicyRef":
        d = _reject_unknown(d, {"policy_id", "policy_version", "content_fingerprint"}, "SourcePolicyRef")
        return cls(
            policy_id=_get_str(d, "policy_id", "SourcePolicyRef"),
            policy_version=_get_str(d, "policy_version", "SourcePolicyRef"),
            content_fingerprint=_get_str(d, "content_fingerprint", "SourcePolicyRef"),
        )


@dataclass(frozen=True)
class DerivedFromScope:
    """typed include/exclude 语义（schema_v2.DERIVED_FROM_SCOPE_KEYS）。"""

    include_sections: tuple[str, ...]
    exclude_producer_kinds: tuple[str, ...] = ()
    exclude_display_tiers: tuple[str, ...] = ("diagnostic_only",)
    exclude_aspect_ids: tuple[str, ...] = ()
    exclude_terminal_states: tuple[str, ...] = ("NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")

    def __post_init__(self) -> None:
        if len(self.include_sections) == 0:
            raise SchemaValidationError("DerivedFromScope.include_sections 必须非空")

    def to_dict(self) -> dict:
        return {
            "include_sections": list(self.include_sections),
            "exclude_producer_kinds": list(self.exclude_producer_kinds),
            "exclude_display_tiers": list(self.exclude_display_tiers),
            "exclude_aspect_ids": list(self.exclude_aspect_ids),
            "exclude_terminal_states": list(self.exclude_terminal_states),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "DerivedFromScope":
        d = _reject_unknown(d, {"include_sections", "exclude_producer_kinds", "exclude_display_tiers",
                                "exclude_aspect_ids", "exclude_terminal_states"}, "DerivedFromScope")
        return cls(
            include_sections=_get_str_tuple(d, "include_sections", "DerivedFromScope"),
            exclude_producer_kinds=_get_str_tuple(d, "exclude_producer_kinds", "DerivedFromScope"),
            exclude_display_tiers=_get_str_tuple(d, "exclude_display_tiers", "DerivedFromScope",
                                                 ("diagnostic_only",)),
            exclude_aspect_ids=_get_str_tuple(d, "exclude_aspect_ids", "DerivedFromScope"),
            exclude_terminal_states=_get_str_tuple(
                d, "exclude_terminal_states", "DerivedFromScope",
                ("NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED")),
        )


@dataclass(frozen=True)
class TopicAspectRequirementSnapshot:
    """冻结 AspectV2 的完整投影。

    前 26 字段名与 ``contracts/schema_v2.py::AspectV2`` 逐一一致（22 REQUIRED_ASPECT_FIELDS
    + 4 扩展字段），不得发明近义字段替代。evidence_requirement_ids 投影为
    ``EvidenceRequirementRef``（绑定指纹），source_policy_ref 投影为 ``SourcePolicyRef``。
    """

    # 22 REQUIRED_ASPECT_FIELDS
    aspect_id: str
    question_id: str
    topic_id: str
    requirement_text: str
    kind: str
    producer_kind: str
    execution_path: str
    required_fields: tuple[str, ...]
    coverage_rules: tuple[str, ...]
    complete_set_rule: str
    evidence_requirement_ids: tuple[EvidenceRequirementRef, ...]
    source_policy_ref: SourcePolicyRef
    time_scope: str
    display_tier: str
    content_role: str
    missing_policy: str
    blocking_policy: tuple[str, ...]
    applicability_policy: str | None
    impact_scope: tuple[str, ...]
    output_destination: str
    derived_from: tuple[str, ...]
    business_review_status: str
    # 4 扩展字段
    business_review_reason: str = ""
    derived_from_scope: DerivedFromScope | None = None
    transmission_layers: tuple[str, ...] = ()
    transmission_channel: str = ""
    # deterministic derived 便捷字段（标注派生，不取代原始冻结字段）
    freshness_window: str | None = None
    # 版本/指纹绑定（回查不可变 typed snapshot 所需）
    contract_version: str = ""
    contract_sha256: str = ""
    canonical_fingerprint: str = ""
    dependency_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.aspect_id or not self.topic_id:
            raise SchemaValidationError("TopicAspectRequirementSnapshot.aspect_id/topic_id 必须非空")
        if self.contract_sha256 and not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("TopicAspectRequirementSnapshot.contract_sha256 必须为 64 位 sha256 hex")
        if self.canonical_fingerprint and not _is_sha256_hex(self.canonical_fingerprint):
            raise SchemaValidationError("TopicAspectRequirementSnapshot.canonical_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "question_id": self.question_id,
            "topic_id": self.topic_id,
            "requirement_text": self.requirement_text,
            "kind": self.kind,
            "producer_kind": self.producer_kind,
            "execution_path": self.execution_path,
            "required_fields": list(self.required_fields),
            "coverage_rules": list(self.coverage_rules),
            "complete_set_rule": self.complete_set_rule,
            "evidence_requirement_ids": [r.to_dict() for r in self.evidence_requirement_ids],
            "source_policy_ref": self.source_policy_ref.to_dict(),
            "time_scope": self.time_scope,
            "display_tier": self.display_tier,
            "content_role": self.content_role,
            "missing_policy": self.missing_policy,
            "blocking_policy": list(self.blocking_policy),
            "applicability_policy": self.applicability_policy,
            "impact_scope": list(self.impact_scope),
            "output_destination": self.output_destination,
            "derived_from": list(self.derived_from),
            "business_review_status": self.business_review_status,
            "business_review_reason": self.business_review_reason,
            "derived_from_scope": self.derived_from_scope.to_dict() if self.derived_from_scope else None,
            "transmission_layers": list(self.transmission_layers),
            "transmission_channel": self.transmission_channel,
            "freshness_window": self.freshness_window,
            "contract_version": self.contract_version,
            "contract_sha256": self.contract_sha256,
            "canonical_fingerprint": self.canonical_fingerprint,
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicAspectRequirementSnapshot":
        allowed = set(REQUIRED_ASPECT_FIELDS) | {
            "business_review_reason", "derived_from_scope", "transmission_layers", "transmission_channel",
            "freshness_window", "contract_version", "contract_sha256", "canonical_fingerprint",
            "dependency_fingerprint",
        }
        d = _reject_unknown(d, allowed, "TopicAspectRequirementSnapshot")
        dfs = d.get("derived_from_scope")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "TopicAspectRequirementSnapshot"),
            question_id=_get_str(d, "question_id", "TopicAspectRequirementSnapshot"),
            topic_id=_get_str(d, "topic_id", "TopicAspectRequirementSnapshot"),
            requirement_text=_get_str(d, "requirement_text", "TopicAspectRequirementSnapshot"),
            kind=_get_str(d, "kind", "TopicAspectRequirementSnapshot"),
            producer_kind=_get_str(d, "producer_kind", "TopicAspectRequirementSnapshot"),
            execution_path=_get_str(d, "execution_path", "TopicAspectRequirementSnapshot"),
            required_fields=_get_str_tuple(d, "required_fields", "TopicAspectRequirementSnapshot"),
            coverage_rules=_get_str_tuple(d, "coverage_rules", "TopicAspectRequirementSnapshot"),
            complete_set_rule=_get_str(d, "complete_set_rule", "TopicAspectRequirementSnapshot",
                                       allow_empty=True),
            evidence_requirement_ids=tuple(
                EvidenceRequirementRef.from_dict(x)
                for x in (_as_list(d.get("evidence_requirement_ids"), "TopicAspectRequirementSnapshot",
                                   "evidence_requirement_ids"))
            ),
            source_policy_ref=SourcePolicyRef.from_dict(_as_dict(
                d.get("source_policy_ref"), "TopicAspectRequirementSnapshot", "source_policy_ref")),
            time_scope=_get_str(d, "time_scope", "TopicAspectRequirementSnapshot"),
            display_tier=_get_str(d, "display_tier", "TopicAspectRequirementSnapshot"),
            content_role=_get_str(d, "content_role", "TopicAspectRequirementSnapshot"),
            missing_policy=_get_str(d, "missing_policy", "TopicAspectRequirementSnapshot"),
            blocking_policy=_get_str_tuple(d, "blocking_policy", "TopicAspectRequirementSnapshot"),
            applicability_policy=_get_str(d, "applicability_policy", "TopicAspectRequirementSnapshot",
                                         allow_none=True),
            impact_scope=_get_str_tuple(d, "impact_scope", "TopicAspectRequirementSnapshot"),
            output_destination=_get_str(d, "output_destination", "TopicAspectRequirementSnapshot"),
            derived_from=_get_str_tuple(d, "derived_from", "TopicAspectRequirementSnapshot"),
            business_review_status=_get_str(d, "business_review_status", "TopicAspectRequirementSnapshot"),
            business_review_reason=_get_str(d, "business_review_reason", "TopicAspectRequirementSnapshot",
                                            allow_empty=True, allow_none=True) or "",
            derived_from_scope=DerivedFromScope.from_dict(dfs) if dfs is not None else None,
            transmission_layers=_get_str_tuple(d, "transmission_layers", "TopicAspectRequirementSnapshot"),
            transmission_channel=_get_str(d, "transmission_channel", "TopicAspectRequirementSnapshot",
                                          allow_empty=True),
            freshness_window=_get_str(d, "freshness_window", "TopicAspectRequirementSnapshot", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicAspectRequirementSnapshot",
                                      allow_empty=True),
            contract_sha256=_get_str(d, "contract_sha256", "TopicAspectRequirementSnapshot",
                                     allow_empty=True),
            canonical_fingerprint=_get_str(d, "canonical_fingerprint", "TopicAspectRequirementSnapshot",
                                           allow_empty=True),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "TopicAspectRequirementSnapshot",
                                            allow_empty=True),
        )


def _as_list(v: Any, typename: str, key: str) -> list:
    if not isinstance(v, list):
        raise SchemaValidationError(f"{typename}.{key} 必须为 list，得到 {type(v).__name__}")
    return v


def _as_dict(v: Any, typename: str, key: str) -> dict:
    if not isinstance(v, dict):
        raise SchemaValidationError(f"{typename}.{key} 必须为 dict，得到 {type(v).__name__}")
    return v


# ---------------------------------------------------------------------------
# Locator 三类联合（按 material_type 区分）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceLocator:
    """evidence_span / table_context 的定位（material_type 绑定）。"""

    locator_type: str = field(init=False, default="evidence")
    document_id: str = ""
    document_version: str = ""
    section_path: str = ""
    page: int | None = None
    table_title: str | None = None
    block_range: tuple[int, int] | None = None
    offset: int | None = None

    def __post_init__(self) -> None:
        # 定位最低要求：page / block_range / section_path 至少一个有效；表格场景保留 table title。
        if self.page is None and self.block_range is None and not self.section_path:
            raise SchemaValidationError("EvidenceLocator 至少需要 page / block_range / section_path 之一")
        if not self.document_id and not self.document_version:
            raise SchemaValidationError("EvidenceLocator 需提供 document_id 或 document_version")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "section_path": self.section_path,
            "page": self.page,
            "table_title": self.table_title,
            "block_range": list(self.block_range) if self.block_range is not None else None,
            "offset": self.offset,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceLocator":
        d = _reject_unknown(d, {"locator_type", "document_id", "document_version", "section_path",
                                "page", "table_title", "block_range", "offset"}, "EvidenceLocator")
        if d.get("locator_type") not in (None, "evidence"):
            raise SchemaValidationError(f"EvidenceLocator.locator_type 必须为 'evidence'，得到 {d.get('locator_type')!r}")
        return cls(
            document_id=_get_str(d, "document_id", "EvidenceLocator", allow_empty=True) or "",
            document_version=_get_str(d, "document_version", "EvidenceLocator", allow_empty=True) or "",
            section_path=_get_str(d, "section_path", "EvidenceLocator", allow_empty=True) or "",
            page=_get_int(d, "page", "EvidenceLocator", allow_none=True),
            table_title=_get_str(d, "table_title", "EvidenceLocator", allow_none=True),
            block_range=_get_int_pair(d, "block_range", "EvidenceLocator", allow_none=True),
            offset=_get_int(d, "offset", "EvidenceLocator", allow_none=True),
        )


@dataclass(frozen=True)
class FinancialLocator:
    """structured（FinancialSnapshot）的定位。"""

    locator_type: str = field(init=False, default="financial_snapshot")
    snapshot_id: str = ""
    company_id: str = ""
    scope: str = ""
    report_as_of: str = ""
    formula_id: str | None = None
    item_code: str | None = None
    period: str | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialLocator.snapshot_id 必须非空")
        # item_code 与 formula_id 至少一个有效。
        if not self.item_code and not self.formula_id:
            raise SchemaValidationError("FinancialLocator 至少需要 item_code 或 formula_id 之一")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "snapshot_id": self.snapshot_id,
            "company_id": self.company_id,
            "scope": self.scope,
            "report_as_of": self.report_as_of,
            "formula_id": self.formula_id,
            "item_code": self.item_code,
            "period": self.period,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialLocator":
        d = _reject_unknown(d, {"locator_type", "snapshot_id", "company_id", "scope", "report_as_of",
                                "formula_id", "item_code", "period"}, "FinancialLocator")
        if d.get("locator_type") not in (None, "financial_snapshot"):
            raise SchemaValidationError(
                f"FinancialLocator.locator_type 必须为 'financial_snapshot'，得到 {d.get('locator_type')!r}")
        return cls(
            snapshot_id=_get_str(d, "snapshot_id", "FinancialLocator"),
            company_id=_get_str(d, "company_id", "FinancialLocator", allow_empty=True) or "",
            scope=_get_str(d, "scope", "FinancialLocator", allow_empty=True) or "",
            report_as_of=_get_str(d, "report_as_of", "FinancialLocator", allow_empty=True) or "",
            formula_id=_get_str(d, "formula_id", "FinancialLocator", allow_none=True),
            item_code=_get_str(d, "item_code", "FinancialLocator", allow_none=True),
            period=_get_str(d, "period", "FinancialLocator", allow_none=True),
        )


@dataclass(frozen=True)
class ExternalLocator:
    """external_snapshot 的定位。"""

    locator_type: str = field(init=False, default="external_snapshot")
    source_snapshot_id: str = ""
    canonical_url: str = ""
    domain: str = ""
    fetched_at: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.source_snapshot_id:
            raise SchemaValidationError("ExternalLocator.source_snapshot_id 必须非空")
        if not self.canonical_url:
            raise SchemaValidationError("ExternalLocator.canonical_url 必须非空")
        if not self.domain:
            raise SchemaValidationError("ExternalLocator.domain 必须非空")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "source_snapshot_id": self.source_snapshot_id,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "fetched_at": self.fetched_at,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalLocator":
        d = _reject_unknown(d, {"locator_type", "source_snapshot_id", "canonical_url", "domain",
                                "fetched_at", "published_at"}, "ExternalLocator")
        if d.get("locator_type") not in (None, "external_snapshot"):
            raise SchemaValidationError(
                f"ExternalLocator.locator_type 必须为 'external_snapshot'，得到 {d.get('locator_type')!r}")
        return cls(
            source_snapshot_id=_get_str(d, "source_snapshot_id", "ExternalLocator"),
            canonical_url=_get_str(d, "canonical_url", "ExternalLocator"),
            domain=_get_str(d, "domain", "ExternalLocator"),
            fetched_at=_get_str(d, "fetched_at", "ExternalLocator", allow_none=True),
            published_at=_get_str(d, "published_at", "ExternalLocator", allow_none=True),
        )


MaterialLocator = EvidenceLocator | FinancialLocator | ExternalLocator


def locator_from_dict(d: Any) -> MaterialLocator:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"MaterialLocator 需要 dict，得到 {type(d).__name__}")
    t = d.get("locator_type")
    if t == "evidence":
        return EvidenceLocator.from_dict(d)
    if t == "financial_snapshot":
        return FinancialLocator.from_dict(d)
    if t == "external_snapshot":
        return ExternalLocator.from_dict(d)
    raise SchemaValidationError(f"未知 locator_type: {t!r}")


# ---------------------------------------------------------------------------
# 三类权威联合（authority gate，架构约束 2）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceAuthorityAssessment:
    """evidence/document 权威（current document/current set/company/正文/hash/定位）。"""

    authority_type: str = field(init=False, default="evidence")
    evidence_id: str = ""
    document_id: str = ""
    document_version: str = ""
    company_id: str = ""
    is_current_document: bool = False
    is_current_set: bool = False
    page: int | None = None
    block_range: tuple[int, int] | None = None
    fetched_inspected_nonempty: bool = False
    content_hash: str = ""
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise SchemaValidationError("EvidenceAuthorityAssessment.evidence_id 必须非空")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "EvidenceAuthorityAssessment", "verdict")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "company_id": self.company_id,
            "is_current_document": self.is_current_document,
            "is_current_set": self.is_current_set,
            "page": self.page,
            "block_range": list(self.block_range) if self.block_range is not None else None,
            "fetched_inspected_nonempty": self.fetched_inspected_nonempty,
            "content_hash": self.content_hash,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "evidence_id", "document_id", "document_version",
                                "company_id", "is_current_document", "is_current_set", "page",
                                "block_range", "fetched_inspected_nonempty", "content_hash", "verdict",
                                "reason", "validator_version"}, "EvidenceAuthorityAssessment")
        if d.get("authority_type") not in (None, "evidence"):
            raise SchemaValidationError(
                f"EvidenceAuthorityAssessment.authority_type 必须为 'evidence'，得到 {d.get('authority_type')!r}")
        return cls(
            evidence_id=_get_str(d, "evidence_id", "EvidenceAuthorityAssessment"),
            document_id=_get_str(d, "document_id", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            document_version=_get_str(d, "document_version", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            company_id=_get_str(d, "company_id", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            is_current_document=_get_bool(d, "is_current_document", "EvidenceAuthorityAssessment"),
            is_current_set=_get_bool(d, "is_current_set", "EvidenceAuthorityAssessment"),
            page=_get_int(d, "page", "EvidenceAuthorityAssessment", allow_none=True),
            block_range=_get_int_pair(d, "block_range", "EvidenceAuthorityAssessment", allow_none=True),
            fetched_inspected_nonempty=_get_bool(d, "fetched_inspected_nonempty", "EvidenceAuthorityAssessment"),
            content_hash=_get_str(d, "content_hash", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            verdict=_get_str(d, "verdict", "EvidenceAuthorityAssessment"),
            reason=_get_str(d, "reason", "EvidenceAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "EvidenceAuthorityAssessment",
                                       allow_empty=True) or "",
        )


@dataclass(frozen=True)
class FinancialSnapshotAuthorityAssessment:
    """FinancialSnapshot 权威（current/valid/company/scope/currency/purpose/report_as_of/blocked/quarantine）。"""

    authority_type: str = field(init=False, default="financial_snapshot")
    snapshot_id: str = ""
    company_id: str = ""
    scope: str = ""
    currency: str = ""
    purpose: str = ""
    report_as_of: str = ""
    is_current: bool = False
    validity: str = "invalid"
    report_blocked: bool = False
    quarantine: bool = False
    item_code: str | None = None
    formula_id: str | None = None
    period: str | None = None
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialSnapshotAuthorityAssessment.snapshot_id 必须非空")
        _get_enum(self.validity, FINANCIAL_VALIDITIES, "FinancialSnapshotAuthorityAssessment", "validity")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "FinancialSnapshotAuthorityAssessment", "verdict")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "snapshot_id": self.snapshot_id,
            "company_id": self.company_id,
            "scope": self.scope,
            "currency": self.currency,
            "purpose": self.purpose,
            "report_as_of": self.report_as_of,
            "is_current": self.is_current,
            "validity": self.validity,
            "report_blocked": self.report_blocked,
            "quarantine": self.quarantine,
            "item_code": self.item_code,
            "formula_id": self.formula_id,
            "period": self.period,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialSnapshotAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "snapshot_id", "company_id", "scope", "currency",
                                "purpose", "report_as_of", "is_current", "validity", "report_blocked",
                                "quarantine", "item_code", "formula_id", "period", "verdict", "reason",
                                "validator_version"}, "FinancialSnapshotAuthorityAssessment")
        if d.get("authority_type") not in (None, "financial_snapshot"):
            raise SchemaValidationError(
                f"FinancialSnapshotAuthorityAssessment.authority_type 必须为 'financial_snapshot'，"
                f"得到 {d.get('authority_type')!r}")
        return cls(
            snapshot_id=_get_str(d, "snapshot_id", "FinancialSnapshotAuthorityAssessment"),
            company_id=_get_str(d, "company_id", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            scope=_get_str(d, "scope", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            currency=_get_str(d, "currency", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            purpose=_get_str(d, "purpose", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            report_as_of=_get_str(d, "report_as_of", "FinancialSnapshotAuthorityAssessment",
                                  allow_empty=True) or "",
            is_current=_get_bool(d, "is_current", "FinancialSnapshotAuthorityAssessment"),
            validity=_get_str(d, "validity", "FinancialSnapshotAuthorityAssessment"),
            report_blocked=_get_bool(d, "report_blocked", "FinancialSnapshotAuthorityAssessment"),
            quarantine=_get_bool(d, "quarantine", "FinancialSnapshotAuthorityAssessment"),
            item_code=_get_str(d, "item_code", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            formula_id=_get_str(d, "formula_id", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            period=_get_str(d, "period", "FinancialSnapshotAuthorityAssessment", allow_none=True),
            verdict=_get_str(d, "verdict", "FinancialSnapshotAuthorityAssessment"),
            reason=_get_str(d, "reason", "FinancialSnapshotAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "FinancialSnapshotAuthorityAssessment",
                                       allow_empty=True) or "",
        )


@dataclass(frozen=True)
class ExternalSnapshotAuthorityAssessment:
    """ExternalSnapshot 权威（fetched 正文/hash/URL/domain/日期/时间资格/A·B·C·D/独立性）。"""

    authority_type: str = field(init=False, default="external_snapshot")
    source_snapshot_id: str = ""
    canonical_url: str = ""
    domain: str = ""
    fetched_nonempty: bool = False
    content_hash: str = ""
    published_at: str | None = None
    time_qualified: bool = False
    source_grade: str = "D"
    min_grade_met: bool = False
    independence_domain: str = ""
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.source_snapshot_id:
            raise SchemaValidationError("ExternalSnapshotAuthorityAssessment.source_snapshot_id 必须非空")
        _get_enum(self.source_grade, SOURCE_GRADES, "ExternalSnapshotAuthorityAssessment", "source_grade")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "ExternalSnapshotAuthorityAssessment", "verdict")

    def to_dict(self) -> dict:
        return {
            "authority_type": self.authority_type,
            "source_snapshot_id": self.source_snapshot_id,
            "canonical_url": self.canonical_url,
            "domain": self.domain,
            "fetched_nonempty": self.fetched_nonempty,
            "content_hash": self.content_hash,
            "published_at": self.published_at,
            "time_qualified": self.time_qualified,
            "source_grade": self.source_grade,
            "min_grade_met": self.min_grade_met,
            "independence_domain": self.independence_domain,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalSnapshotAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "source_snapshot_id", "canonical_url", "domain",
                                "fetched_nonempty", "content_hash", "published_at", "time_qualified",
                                "source_grade", "min_grade_met", "independence_domain", "verdict",
                                "reason", "validator_version"}, "ExternalSnapshotAuthorityAssessment")
        if d.get("authority_type") not in (None, "external_snapshot"):
            raise SchemaValidationError(
                f"ExternalSnapshotAuthorityAssessment.authority_type 必须为 'external_snapshot'，"
                f"得到 {d.get('authority_type')!r}")
        return cls(
            source_snapshot_id=_get_str(d, "source_snapshot_id", "ExternalSnapshotAuthorityAssessment"),
            canonical_url=_get_str(d, "canonical_url", "ExternalSnapshotAuthorityAssessment",
                                   allow_empty=True) or "",
            domain=_get_str(d, "domain", "ExternalSnapshotAuthorityAssessment", allow_empty=True) or "",
            fetched_nonempty=_get_bool(d, "fetched_nonempty", "ExternalSnapshotAuthorityAssessment"),
            content_hash=_get_str(d, "content_hash", "ExternalSnapshotAuthorityAssessment",
                                  allow_empty=True) or "",
            published_at=_get_str(d, "published_at", "ExternalSnapshotAuthorityAssessment", allow_none=True),
            time_qualified=_get_bool(d, "time_qualified", "ExternalSnapshotAuthorityAssessment"),
            source_grade=_get_str(d, "source_grade", "ExternalSnapshotAuthorityAssessment"),
            min_grade_met=_get_bool(d, "min_grade_met", "ExternalSnapshotAuthorityAssessment"),
            independence_domain=_get_str(d, "independence_domain", "ExternalSnapshotAuthorityAssessment",
                                         allow_empty=True) or "",
            verdict=_get_str(d, "verdict", "ExternalSnapshotAuthorityAssessment"),
            reason=_get_str(d, "reason", "ExternalSnapshotAuthorityAssessment", allow_empty=True) or "",
            validator_version=_get_str(d, "validator_version", "ExternalSnapshotAuthorityAssessment",
                                       allow_empty=True) or "",
        )


AuthorityAssessment = (
    EvidenceAuthorityAssessment
    | FinancialSnapshotAuthorityAssessment
    | ExternalSnapshotAuthorityAssessment
)


def authority_from_dict(d: Any) -> AuthorityAssessment:
    if not isinstance(d, dict):
        raise SchemaValidationError(f"AuthorityAssessment 需要 dict，得到 {type(d).__name__}")
    t = d.get("authority_type")
    if t == "evidence":
        return EvidenceAuthorityAssessment.from_dict(d)
    if t == "financial_snapshot":
        return FinancialSnapshotAuthorityAssessment.from_dict(d)
    if t == "external_snapshot":
        return ExternalSnapshotAuthorityAssessment.from_dict(d)
    raise SchemaValidationError(f"未知 authority_type: {t!r}")


# ---------------------------------------------------------------------------
# material / payload / fact / citation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitationRef:
    """一条可回查引用（三类 ref_type，与 harness.schema.CITATION_TYPES 一致）。"""

    ref_type: str
    evidence_id: str | None = None
    evidence_fact_id: str | None = None
    snapshot_id: str | None = None
    item_code: str | None = None
    formula_id: str | None = None
    formula_version: str | None = None
    period: str | None = None
    source_snapshot_id: str | None = None
    page_number: int | None = None

    def __post_init__(self) -> None:
        _get_enum(self.ref_type, CITATION_TYPES, "CitationRef", "ref_type")

    def to_dict(self) -> dict:
        return {
            "ref_type": self.ref_type,
            "evidence_id": self.evidence_id,
            "evidence_fact_id": self.evidence_fact_id,
            "snapshot_id": self.snapshot_id,
            "item_code": self.item_code,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "period": self.period,
            "source_snapshot_id": self.source_snapshot_id,
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "CitationRef":
        d = _reject_unknown(d, {"ref_type", "evidence_id", "evidence_fact_id", "snapshot_id",
                                "item_code", "formula_id", "formula_version", "period",
                                "source_snapshot_id", "page_number"}, "CitationRef")
        return cls(
            ref_type=_get_str(d, "ref_type", "CitationRef"),
            evidence_id=_get_str(d, "evidence_id", "CitationRef", allow_none=True),
            evidence_fact_id=_get_str(d, "evidence_fact_id", "CitationRef", allow_none=True),
            snapshot_id=_get_str(d, "snapshot_id", "CitationRef", allow_none=True),
            item_code=_get_str(d, "item_code", "CitationRef", allow_none=True),
            formula_id=_get_str(d, "formula_id", "CitationRef", allow_none=True),
            formula_version=_get_str(d, "formula_version", "CitationRef", allow_none=True),
            period=_get_str(d, "period", "CitationRef", allow_none=True),
            source_snapshot_id=_get_str(d, "source_snapshot_id", "CitationRef", allow_none=True),
            page_number=_get_int(d, "page_number", "CitationRef", allow_none=True),
        )


@dataclass(frozen=True)
class MaterialPayloadRef:
    """§7 不可变解析引用（替代无法验证的裸字符串）。"""

    object_type: str
    authority_identity: str
    version: str
    content_hash: str
    locator: MaterialLocator
    created_dependency_fingerprint: str

    def __post_init__(self) -> None:
        _get_enum(self.object_type, MATERIAL_TYPES, "MaterialPayloadRef", "object_type")
        if not self.authority_identity:
            raise SchemaValidationError("MaterialPayloadRef.authority_identity 必须非空")
        if not self.version:
            raise SchemaValidationError("MaterialPayloadRef.version 必须非空")
        if not _is_sha256_hex(self.content_hash):
            raise SchemaValidationError("MaterialPayloadRef.content_hash 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "authority_identity": self.authority_identity,
            "version": self.version,
            "content_hash": self.content_hash,
            "locator": self.locator.to_dict(),
            "created_dependency_fingerprint": self.created_dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "MaterialPayloadRef":
        d = _reject_unknown(d, {"object_type", "authority_identity", "version", "content_hash",
                                "locator", "created_dependency_fingerprint"}, "MaterialPayloadRef")
        return cls(
            object_type=_get_str(d, "object_type", "MaterialPayloadRef"),
            authority_identity=_get_str(d, "authority_identity", "MaterialPayloadRef"),
            version=_get_str(d, "version", "MaterialPayloadRef"),
            content_hash=_get_str(d, "content_hash", "MaterialPayloadRef"),
            locator=locator_from_dict(_as_dict(d.get("locator"), "MaterialPayloadRef", "locator")),
            created_dependency_fingerprint=_get_str(d, "created_dependency_fingerprint",
                                                    "MaterialPayloadRef", allow_empty=True) or "",
        )


def _material_consistency(material_type: str, locator: MaterialLocator,
                          authority: AuthorityAssessment) -> None:
    """material type ↔ locator 变体 ↔ authority 变体 三者必须匹配（§8 强制不变量）。"""
    exp_loc = _LOCATOR_TYPE_BY_MATERIAL.get(material_type)
    exp_auth = _AUTHORITY_TYPE_BY_MATERIAL.get(material_type)
    if exp_loc is None:
        raise SchemaValidationError(f"未知 material_type: {material_type!r}")
    if locator.locator_type != exp_loc:
        raise SchemaValidationError(
            f"material_type={material_type!r} 要求 locator_type={exp_loc!r}，"
            f"得到 {locator.locator_type!r}")
    if authority.authority_type != exp_auth:
        raise SchemaValidationError(
            f"material_type={material_type!r} 要求 authority_type={exp_auth!r}，"
            f"得到 {authority.authority_type!r}")


@dataclass(frozen=True)
class ResearchMaterial:
    """一个 material（evidence_span/table_context/structured/external_snapshot）。"""

    material_id: str
    material_type: str
    source_identity: str
    locator: MaterialLocator
    payload_ref: MaterialPayloadRef
    content_hash: str
    authority_assessment: AuthorityAssessment
    context_parent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.material_id:
            raise SchemaValidationError("ResearchMaterial.material_id 必须非空")
        _get_enum(self.material_type, MATERIAL_TYPES, "ResearchMaterial", "material_type")
        if not self.content_hash:
            raise SchemaValidationError("ResearchMaterial.content_hash 必须非空")
        if not _is_sha256_hex(self.content_hash):
            raise SchemaValidationError("ResearchMaterial.content_hash 必须为 64 位 sha256 hex")
        _material_consistency(self.material_type, self.locator, self.authority_assessment)

    def to_dict(self) -> dict:
        return {
            "material_id": self.material_id,
            "material_type": self.material_type,
            "source_identity": self.source_identity,
            "locator": self.locator.to_dict(),
            "payload_ref": self.payload_ref.to_dict(),
            "context_parent_id": self.context_parent_id,
            "content_hash": self.content_hash,
            "authority_assessment": self.authority_assessment.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchMaterial":
        d = _reject_unknown(d, {"material_id", "material_type", "source_identity", "locator",
                                "payload_ref", "context_parent_id", "content_hash",
                                "authority_assessment"}, "ResearchMaterial")
        return cls(
            material_id=_get_str(d, "material_id", "ResearchMaterial"),
            material_type=_get_str(d, "material_type", "ResearchMaterial"),
            source_identity=_get_str(d, "source_identity", "ResearchMaterial", allow_empty=True) or "",
            locator=locator_from_dict(_as_dict(d.get("locator"), "ResearchMaterial", "locator")),
            payload_ref=MaterialPayloadRef.from_dict(_as_dict(d.get("payload_ref"), "ResearchMaterial",
                                                             "payload_ref")),
            context_parent_id=_get_str(d, "context_parent_id", "ResearchMaterial", allow_none=True),
            content_hash=_get_str(d, "content_hash", "ResearchMaterial"),
            authority_assessment=authority_from_dict(
                _as_dict(d.get("authority_assessment"), "ResearchMaterial", "authority_assessment")),
        )


@dataclass(frozen=True)
class ValueIdentity:
    """规范化数字语义（替代裸 dict）。"""

    value_kind: str
    metric: str
    unit: str
    period: str
    scope: str
    amount_canonical: str

    def __post_init__(self) -> None:
        if not self.value_kind or not self.metric:
            raise SchemaValidationError("ValueIdentity.value_kind/metric 必须非空")
        if not self.amount_canonical:
            raise SchemaValidationError("ValueIdentity.amount_canonical 必须非空")

    def to_dict(self) -> dict:
        return {
            "value_kind": self.value_kind,
            "metric": self.metric,
            "unit": self.unit,
            "period": self.period,
            "scope": self.scope,
            "amount_canonical": self.amount_canonical,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ValueIdentity":
        d = _reject_unknown(d, {"value_kind", "metric", "unit", "period", "scope",
                                "amount_canonical"}, "ValueIdentity")
        return cls(
            value_kind=_get_str(d, "value_kind", "ValueIdentity"),
            metric=_get_str(d, "metric", "ValueIdentity"),
            unit=_get_str(d, "unit", "ValueIdentity", allow_empty=True) or "",
            period=_get_str(d, "period", "ValueIdentity", allow_empty=True) or "",
            scope=_get_str(d, "scope", "ValueIdentity", allow_empty=True) or "",
            amount_canonical=_get_str(d, "amount_canonical", "ValueIdentity"),
        )


@dataclass(frozen=True)
class SupportedFact:
    """一条通过校验的 adopted fact（SUPPORTED entailment）。"""

    fact_id: str
    text: str
    fact_type: str
    aspect_ids: tuple[str, ...]
    citation_refs: tuple[CitationRef, ...]
    source_authority: AuthorityAssessment
    value_identity: ValueIdentity | None = None
    semantic_tags: tuple[str, ...] = ()
    period: str | None = None
    scope: str | None = None
    confidence: str | None = None

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise SchemaValidationError("SupportedFact.fact_id 必须非空")
        if not self.text:
            raise SchemaValidationError("SupportedFact.text 必须非空")
        _get_enum(self.fact_type, FACT_TYPES, "SupportedFact", "fact_type")
        if len(self.aspect_ids) == 0:
            raise SchemaValidationError("SupportedFact.aspect_ids 必须非空")
        if self.confidence is not None and self.confidence not in ("high", "low"):
            raise SchemaValidationError(f"SupportedFact.confidence 非法: {self.confidence!r}")

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "text": self.text,
            "fact_type": self.fact_type,
            "aspect_ids": list(self.aspect_ids),
            "citation_refs": [c.to_dict() for c in self.citation_refs],
            "source_authority": self.source_authority.to_dict(),
            "value_identity": self.value_identity.to_dict() if self.value_identity else None,
            "semantic_tags": list(self.semantic_tags),
            "period": self.period,
            "scope": self.scope,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SupportedFact":
        d = _reject_unknown(d, {"fact_id", "text", "fact_type", "aspect_ids", "citation_refs",
                                "source_authority", "value_identity", "semantic_tags", "period",
                                "scope", "confidence"}, "SupportedFact")
        vi = d.get("value_identity")
        return cls(
            fact_id=_get_str(d, "fact_id", "SupportedFact"),
            text=_get_str(d, "text", "SupportedFact"),
            fact_type=_get_str(d, "fact_type", "SupportedFact"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "SupportedFact"),
            citation_refs=tuple(CitationRef.from_dict(x) for x in _as_list(
                d.get("citation_refs"), "SupportedFact", "citation_refs")),
            source_authority=authority_from_dict(_as_dict(d.get("source_authority"), "SupportedFact",
                                                          "source_authority")),
            value_identity=ValueIdentity.from_dict(vi) if vi is not None else None,
            semantic_tags=_get_str_tuple(d, "semantic_tags", "SupportedFact"),
            period=_get_str(d, "period", "SupportedFact", allow_none=True),
            scope=_get_str(d, "scope", "SupportedFact", allow_none=True),
            confidence=_get_str(d, "confidence", "SupportedFact", allow_none=True),
        )


@dataclass(frozen=True)
class ResearchConflict:
    """事实冲突（相关但不足材料进入冲突区并写明原因）。"""

    conflict_id: str
    fact_ids: tuple[str, ...]
    category: str
    detail: str
    status: str

    def __post_init__(self) -> None:
        if not self.conflict_id:
            raise SchemaValidationError("ResearchConflict.conflict_id 必须非空")
        _get_enum(self.category, CONFLICT_CATEGORIES, "ResearchConflict", "category")
        _get_enum(self.status, CONFLICT_STATUSES, "ResearchConflict", "status")

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "fact_ids": list(self.fact_ids),
            "category": self.category,
            "detail": self.detail,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchConflict":
        d = _reject_unknown(d, {"conflict_id", "fact_ids", "category", "detail", "status"},
                            "ResearchConflict")
        return cls(
            conflict_id=_get_str(d, "conflict_id", "ResearchConflict"),
            fact_ids=_get_str_tuple(d, "fact_ids", "ResearchConflict"),
            category=_get_str(d, "category", "ResearchConflict"),
            detail=_get_str(d, "detail", "ResearchConflict", allow_empty=True) or "",
            status=_get_str(d, "status", "ResearchConflict"),
        )


@dataclass(frozen=True)
class NotFoundAudit:
    """搜索未取得的结构化审计（只有 qualified=true 才能投影为 NOT_FOUND_AFTER_SEARCH）。"""

    audit_id: str
    policy_version: str
    required_source_scope: tuple[str, ...]
    attempted_source_types: tuple[str, ...]
    valid_attempt_count: int
    searched_need_ids: tuple[str, ...]
    context_expansion_attempted: bool
    alternative_candidate_ids: tuple[str, ...]
    alternative_sources_attempted: tuple[str, ...]
    time_window: str
    unattempted_candidate_ids: tuple[str, ...]
    budget_exhausted: bool
    qualification_reasons: tuple[str, ...]
    qualified: bool

    def __post_init__(self) -> None:
        if not self.audit_id:
            raise SchemaValidationError("NotFoundAudit.audit_id 必须非空")

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "policy_version": self.policy_version,
            "required_source_scope": list(self.required_source_scope),
            "attempted_source_types": list(self.attempted_source_types),
            "valid_attempt_count": self.valid_attempt_count,
            "searched_need_ids": list(self.searched_need_ids),
            "context_expansion_attempted": self.context_expansion_attempted,
            "alternative_candidate_ids": list(self.alternative_candidate_ids),
            "alternative_sources_attempted": list(self.alternative_sources_attempted),
            "time_window": self.time_window,
            "unattempted_candidate_ids": list(self.unattempted_candidate_ids),
            "budget_exhausted": self.budget_exhausted,
            "qualification_reasons": list(self.qualification_reasons),
            "qualified": self.qualified,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "NotFoundAudit":
        d = _reject_unknown(d, {"audit_id", "policy_version", "required_source_scope",
                                "attempted_source_types", "valid_attempt_count", "searched_need_ids",
                                "context_expansion_attempted", "alternative_candidate_ids",
                                "alternative_sources_attempted", "time_window",
                                "unattempted_candidate_ids", "budget_exhausted",
                                "qualification_reasons", "qualified"}, "NotFoundAudit")
        return cls(
            audit_id=_get_str(d, "audit_id", "NotFoundAudit"),
            policy_version=_get_str(d, "policy_version", "NotFoundAudit"),
            required_source_scope=_get_str_tuple(d, "required_source_scope", "NotFoundAudit"),
            attempted_source_types=_get_str_tuple(d, "attempted_source_types", "NotFoundAudit"),
            valid_attempt_count=_get_int(d, "valid_attempt_count", "NotFoundAudit"),
            searched_need_ids=_get_str_tuple(d, "searched_need_ids", "NotFoundAudit"),
            context_expansion_attempted=_get_bool(d, "context_expansion_attempted", "NotFoundAudit"),
            alternative_candidate_ids=_get_str_tuple(d, "alternative_candidate_ids", "NotFoundAudit"),
            alternative_sources_attempted=_get_str_tuple(d, "alternative_sources_attempted", "NotFoundAudit"),
            time_window=_get_str(d, "time_window", "NotFoundAudit"),
            unattempted_candidate_ids=_get_str_tuple(d, "unattempted_candidate_ids", "NotFoundAudit"),
            budget_exhausted=_get_bool(d, "budget_exhausted", "NotFoundAudit"),
            qualification_reasons=_get_str_tuple(d, "qualification_reasons", "NotFoundAudit"),
            qualified=_get_bool(d, "qualified", "NotFoundAudit"),
        )


@dataclass(frozen=True)
class ResearchGap:
    """缺口结构化保留（缺失事项/已查范围/原因/影响/建议材料类型/未来动作类型）。"""

    unresolved_id: str
    aspect_ids: tuple[str, ...]
    reason_code: str
    detail: str
    attempted_need_ids: tuple[str, ...]
    blocking: bool
    impact: str
    not_found_audit_id: str | None = None

    def __post_init__(self) -> None:
        if not self.unresolved_id:
            raise SchemaValidationError("ResearchGap.unresolved_id 必须非空")
        _get_enum(self.reason_code, GAP_REASON_CODES, "ResearchGap", "reason_code")
        _get_enum(self.impact, GAP_IMPACTS, "ResearchGap", "impact")

    def to_dict(self) -> dict:
        return {
            "unresolved_id": self.unresolved_id,
            "aspect_ids": list(self.aspect_ids),
            "reason_code": self.reason_code,
            "detail": self.detail,
            "attempted_need_ids": list(self.attempted_need_ids),
            "blocking": self.blocking,
            "impact": self.impact,
            "not_found_audit_id": self.not_found_audit_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ResearchGap":
        d = _reject_unknown(d, {"unresolved_id", "aspect_ids", "reason_code", "detail",
                                "attempted_need_ids", "blocking", "impact", "not_found_audit_id"},
                            "ResearchGap")
        return cls(
            unresolved_id=_get_str(d, "unresolved_id", "ResearchGap"),
            aspect_ids=_get_str_tuple(d, "aspect_ids", "ResearchGap"),
            reason_code=_get_str(d, "reason_code", "ResearchGap"),
            detail=_get_str(d, "detail", "ResearchGap", allow_empty=True) or "",
            attempted_need_ids=_get_str_tuple(d, "attempted_need_ids", "ResearchGap"),
            blocking=_get_bool(d, "blocking", "ResearchGap"),
            impact=_get_str(d, "impact", "ResearchGap"),
            not_found_audit_id=_get_str(d, "not_found_audit_id", "ResearchGap", allow_none=True),
        )


# ---------------------------------------------------------------------------
# 运行消耗 / 预算 / 外部漏斗 / 不确定调用
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UsageEntry:
    """运行消耗单项（metric 为枚举）。"""

    metric: str
    value: int | float | str | Decimal
    unit: str

    def __post_init__(self) -> None:
        _get_enum(self.metric, USAGE_METRICS, "UsageEntry", "metric")
        if not isinstance(self.value, (int, float, str, Decimal)) or isinstance(self.value, bool):
            raise SchemaValidationError(f"UsageEntry.value 非法: {self.value!r}")

    def to_dict(self) -> dict:
        return {"metric": self.metric, "value": _to_json_value(self.value), "unit": self.unit}

    @classmethod
    def from_dict(cls, d: Any) -> "UsageEntry":
        d = _reject_unknown(d, {"metric", "value", "unit"}, "UsageEntry")
        return cls(
            metric=_get_str(d, "metric", "UsageEntry"),
            value=d.get("value"),
            unit=_get_str(d, "unit", "UsageEntry", allow_empty=True) or "",
        )


@dataclass(frozen=True)
class BudgetPolicySnapshot:
    """版本化预算政策快照。"""

    schema_version: str
    canonical_hash: str
    tier: str

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise SchemaValidationError("BudgetPolicySnapshot.schema_version 必须非空")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "canonical_hash": self.canonical_hash,
                "tier": self.tier}

    @classmethod
    def from_dict(cls, d: Any) -> "BudgetPolicySnapshot":
        d = _reject_unknown(d, {"schema_version", "canonical_hash", "tier"}, "BudgetPolicySnapshot")
        return cls(
            schema_version=_get_str(d, "schema_version", "BudgetPolicySnapshot"),
            canonical_hash=_get_str(d, "canonical_hash", "BudgetPolicySnapshot", allow_empty=True) or "",
            tier=_get_str(d, "tier", "BudgetPolicySnapshot", allow_empty=True) or "",
        )


@dataclass(frozen=True)
class TopicUsageSnapshot:
    """Pack 运行消耗/预算快照（替代 budget_policy/cumulative_usage/stop_reason 裸字段）。

    stop_reason 仅是 usage/预算停账理由，不是第三套 Pack 状态（Pack 状态见 process/coverage 双轴）。
    """

    budget_policy: BudgetPolicySnapshot
    cumulative_usage: tuple[UsageEntry, ...]
    stop_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "budget_policy": self.budget_policy.to_dict(),
            "cumulative_usage": [u.to_dict() for u in self.cumulative_usage],
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicUsageSnapshot":
        d = _reject_unknown(d, {"budget_policy", "cumulative_usage", "stop_reason"}, "TopicUsageSnapshot")
        return cls(
            budget_policy=BudgetPolicySnapshot.from_dict(_as_dict(
                d.get("budget_policy"), "TopicUsageSnapshot", "budget_policy")),
            cumulative_usage=tuple(UsageEntry.from_dict(x) for x in _as_list(
                d.get("cumulative_usage"), "TopicUsageSnapshot", "cumulative_usage")),
            stop_reason=_get_str(d, "stop_reason", "TopicUsageSnapshot", allow_none=True),
        )


@dataclass(frozen=True)
class ExternalFunnelSnapshot:
    """版本化不透明外部漏斗快照（R1-B 不正式定义内部结构，R4 再解析；不使用任意 dict）。

    canonical_hash 即内容寻址（opaque payload 的 sha256），供 hash mismatch fail-closed 复验；
    R1-B 的 mock Pack 若无外部漏斗可用 None，不得伪造空审计对象。
    """

    schema_version: str
    canonical_hash: str
    producer_version: str

    def __post_init__(self) -> None:
        if not self.schema_version:
            raise SchemaValidationError("ExternalFunnelSnapshot.schema_version 必须非空")
        if not _is_sha256_hex(self.canonical_hash):
            raise SchemaValidationError("ExternalFunnelSnapshot.canonical_hash 必须为 64 位 sha256 hex")
        if not self.producer_version:
            raise SchemaValidationError("ExternalFunnelSnapshot.producer_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "canonical_hash": self.canonical_hash,
            "producer_version": self.producer_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ExternalFunnelSnapshot":
        d = _reject_unknown(d, {"schema_version", "canonical_hash", "producer_version"},
                            "ExternalFunnelSnapshot")
        return cls(
            schema_version=_get_str(d, "schema_version", "ExternalFunnelSnapshot"),
            canonical_hash=_get_str(d, "canonical_hash", "ExternalFunnelSnapshot"),
            producer_version=_get_str(d, "producer_version", "ExternalFunnelSnapshot"),
        )


def verify_external_funnel_payload(snapshot: ExternalFunnelSnapshot, payload_bytes: bytes) -> bool:
    """复验外部漏斗 payload：sha256(payload) == canonical_hash。dangling/hash mismatch → False。"""
    return hashlib.sha256(payload_bytes).hexdigest() == snapshot.canonical_hash


@dataclass(frozen=True)
class UncertainToolCallRecord:
    """不确定工具调用记录（请求已发出但响应未确认持久化；不假设从未执行）。"""

    call_key: str
    tool_name: str
    input_fingerprint: str
    output_fingerprint: str
    outcome: str
    recorded_fingerprint: str

    def __post_init__(self) -> None:
        if not self.call_key or not self.tool_name:
            raise SchemaValidationError("UncertainToolCallRecord.call_key/tool_name 必须非空")

    def to_dict(self) -> dict:
        return {
            "call_key": self.call_key,
            "tool_name": self.tool_name,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "outcome": self.outcome,
            "recorded_fingerprint": self.recorded_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "UncertainToolCallRecord":
        d = _reject_unknown(d, {"call_key", "tool_name", "input_fingerprint", "output_fingerprint",
                                "outcome", "recorded_fingerprint"}, "UncertainToolCallRecord")
        return cls(
            call_key=_get_str(d, "call_key", "UncertainToolCallRecord"),
            tool_name=_get_str(d, "tool_name", "UncertainToolCallRecord"),
            input_fingerprint=_get_str(d, "input_fingerprint", "UncertainToolCallRecord",
                                       allow_empty=True) or "",
            output_fingerprint=_get_str(d, "output_fingerprint", "UncertainToolCallRecord",
                                        allow_empty=True) or "",
            outcome=_get_str(d, "outcome", "UncertainToolCallRecord", allow_empty=True) or "",
            recorded_fingerprint=_get_str(d, "recorded_fingerprint", "UncertainToolCallRecord",
                                          allow_empty=True) or "",
        )


# ---------------------------------------------------------------------------
# 双轴状态 + 派生记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackProcessStatus:
    """研究流程是否已停止（正交于 coverage）。"""

    status: str
    hard_stop_reason: str | None = None

    def __post_init__(self) -> None:
        _get_enum(self.status, PACK_PROCESS_STATUSES, "PackProcessStatus", "status")
        if self.status in ("blocked", "stopped_by_budget", "failed") and not self.hard_stop_reason:
            raise SchemaValidationError(
                f"PackProcessStatus.status={self.status!r} 必须提供 hard_stop_reason")

    def to_dict(self) -> dict:
        return {"status": self.status, "hard_stop_reason": self.hard_stop_reason}

    @classmethod
    def from_dict(cls, d: Any) -> "PackProcessStatus":
        d = _reject_unknown(d, {"status", "hard_stop_reason"}, "PackProcessStatus")
        return cls(
            status=_get_str(d, "status", "PackProcessStatus"),
            hard_stop_reason=_get_str(d, "hard_stop_reason", "PackProcessStatus", allow_none=True),
        )


@dataclass(frozen=True)
class PackCoverageStatus:
    """required aspect 内容覆盖（正交于 process）。"""

    status: str
    covered_aspect_ids: tuple[str, ...]
    gap_aspect_ids: tuple[str, ...]
    not_applicable_aspect_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _get_enum(self.status, PACK_COVERAGE_STATUSES, "PackCoverageStatus", "status")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "covered_aspect_ids": list(self.covered_aspect_ids),
            "gap_aspect_ids": list(self.gap_aspect_ids),
            "not_applicable_aspect_ids": list(self.not_applicable_aspect_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "PackCoverageStatus":
        d = _reject_unknown(d, {"status", "covered_aspect_ids", "gap_aspect_ids",
                                "not_applicable_aspect_ids"}, "PackCoverageStatus")
        return cls(
            status=_get_str(d, "status", "PackCoverageStatus"),
            covered_aspect_ids=_get_str_tuple(d, "covered_aspect_ids", "PackCoverageStatus"),
            gap_aspect_ids=_get_str_tuple(d, "gap_aspect_ids", "PackCoverageStatus"),
            not_applicable_aspect_ids=_get_str_tuple(d, "not_applicable_aspect_ids", "PackCoverageStatus"),
        )


@dataclass(frozen=True)
class AspectStatusEntry:
    """StatusDerivation.per_aspect 的类型化条目（非无约束二元字符串 tuple）。"""

    aspect_id: str
    aspect_status: str

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("AspectStatusEntry.aspect_id 必须非空")
        _get_enum(self.aspect_status, ASPECT_RESULT_STATUSES, "AspectStatusEntry", "aspect_status")

    def to_dict(self) -> dict:
        return {"aspect_id": self.aspect_id, "aspect_status": self.aspect_status}

    @classmethod
    def from_dict(cls, d: Any) -> "AspectStatusEntry":
        d = _reject_unknown(d, {"aspect_id", "aspect_status"}, "AspectStatusEntry")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "AspectStatusEntry"),
            aspect_status=_get_str(d, "aspect_status", "AspectStatusEntry"),
        )


@dataclass(frozen=True)
class StatusDerivation:
    """类型化、版本化的确定性推导记录（derive_pack_status 输出）。"""

    schema_version: str
    rule_version: str
    derivation_fingerprint: str
    per_aspect: tuple[AspectStatusEntry, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "rule_version": self.rule_version,
            "derivation_fingerprint": self.derivation_fingerprint,
            "per_aspect": [e.to_dict() for e in self.per_aspect],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "StatusDerivation":
        d = _reject_unknown(d, {"schema_version", "rule_version", "derivation_fingerprint",
                                "per_aspect"}, "StatusDerivation")
        return cls(
            schema_version=_get_str(d, "schema_version", "StatusDerivation"),
            rule_version=_get_str(d, "rule_version", "StatusDerivation"),
            derivation_fingerprint=_get_str(d, "derivation_fingerprint", "StatusDerivation",
                                            allow_empty=True) or "",
            per_aspect=tuple(AspectStatusEntry.from_dict(x) for x in _as_list(
                d.get("per_aspect"), "StatusDerivation", "per_aspect")),
        )


# ---------------------------------------------------------------------------
# 原子结果 / 资格 / aspect 结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SufficiencyAssessment:
    """sufficiency-gate 记录（绑定 aspect/conclusion + 实际事实/来源 + 规则版本）。"""

    aspect_id: str | None
    conclusion_id: str | None
    supporting_fact_ids: tuple[str, ...]
    supporting_source_ids: tuple[str, ...]
    rule: str
    rule_version: str
    threshold_met: bool
    independent_c_count: int
    assessor_version: str

    def __post_init__(self) -> None:
        if self.aspect_id is None and self.conclusion_id is None:
            raise SchemaValidationError("SufficiencyAssessment 至少需要 aspect_id 或 conclusion_id")
        if not self.rule or not self.rule_version:
            raise SchemaValidationError("SufficiencyAssessment.rule/rule_version 必须非空")
        if self.independent_c_count < 0:
            raise SchemaValidationError("SufficiencyAssessment.independent_c_count 必须 ≥ 0")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "conclusion_id": self.conclusion_id,
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "supporting_source_ids": list(self.supporting_source_ids),
            "rule": self.rule,
            "rule_version": self.rule_version,
            "threshold_met": self.threshold_met,
            "independent_c_count": self.independent_c_count,
            "assessor_version": self.assessor_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SufficiencyAssessment":
        d = _reject_unknown(d, {"aspect_id", "conclusion_id", "supporting_fact_ids",
                                "supporting_source_ids", "rule", "rule_version", "threshold_met",
                                "independent_c_count", "assessor_version"}, "SufficiencyAssessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SufficiencyAssessment", allow_none=True),
            conclusion_id=_get_str(d, "conclusion_id", "SufficiencyAssessment", allow_none=True),
            supporting_fact_ids=_get_str_tuple(d, "supporting_fact_ids", "SufficiencyAssessment"),
            supporting_source_ids=_get_str_tuple(d, "supporting_source_ids", "SufficiencyAssessment"),
            rule=_get_str(d, "rule", "SufficiencyAssessment"),
            rule_version=_get_str(d, "rule_version", "SufficiencyAssessment"),
            threshold_met=_get_bool(d, "threshold_met", "SufficiencyAssessment"),
            independent_c_count=_get_int(d, "independent_c_count", "SufficiencyAssessment"),
            assessor_version=_get_str(d, "assessor_version", "SufficiencyAssessment",
                                      allow_empty=True) or "",
        )


@dataclass(frozen=True)
class AspectResearchResult:
    """topic_harness aspect 的独立状态（每 aspect 一条）。"""

    aspect_id: str
    question_ids: tuple[str, ...]
    requirement_snapshot: TopicAspectRequirementSnapshot
    status: str
    supported_fact_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    attempted_need_ids: tuple[str, ...]
    unresolved_ids: tuple[str, ...]
    not_found_audit_id: str | None = None
    authority_assessment: AuthorityAssessment | None = None
    sufficiency_assessment: SufficiencyAssessment | None = None

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("AspectResearchResult.aspect_id 必须非空")
        _get_enum(self.status, ASPECT_RESULT_STATUSES, "AspectResearchResult", "status")
        # 冻结投影身份：aspect 结果必须绑定同名 aspect 的冻结投影。
        if self.requirement_snapshot.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.aspect_id={self.aspect_id!r} 与 "
                f"requirement_snapshot.aspect_id={self.requirement_snapshot.aspect_id!r} 不一致")
        if self.status == "not_found" and not self.not_found_audit_id:
            raise SchemaValidationError("AspectResearchResult.status=not_found 必须绑定 not_found_audit_id")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "question_ids": list(self.question_ids),
            "requirement_snapshot": self.requirement_snapshot.to_dict(),
            "status": self.status,
            "supported_fact_ids": list(self.supported_fact_ids),
            "material_ids": list(self.material_ids),
            "attempted_need_ids": list(self.attempted_need_ids),
            "unresolved_ids": list(self.unresolved_ids),
            "not_found_audit_id": self.not_found_audit_id,
            "authority_assessment": self.authority_assessment.to_dict()
                if self.authority_assessment is not None else None,
            "sufficiency_assessment": self.sufficiency_assessment.to_dict()
                if self.sufficiency_assessment is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectResearchResult":
        d = _reject_unknown(d, {"aspect_id", "question_ids", "requirement_snapshot", "status",
                                "supported_fact_ids", "material_ids", "attempted_need_ids",
                                "unresolved_ids", "not_found_audit_id", "authority_assessment",
                                "sufficiency_assessment"}, "AspectResearchResult")
        aa = d.get("authority_assessment")
        sa = d.get("sufficiency_assessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "AspectResearchResult"),
            question_ids=_get_str_tuple(d, "question_ids", "AspectResearchResult"),
            requirement_snapshot=TopicAspectRequirementSnapshot.from_dict(_as_dict(
                d.get("requirement_snapshot"), "AspectResearchResult", "requirement_snapshot")),
            status=_get_str(d, "status", "AspectResearchResult"),
            supported_fact_ids=_get_str_tuple(d, "supported_fact_ids", "AspectResearchResult"),
            material_ids=_get_str_tuple(d, "material_ids", "AspectResearchResult"),
            attempted_need_ids=_get_str_tuple(d, "attempted_need_ids", "AspectResearchResult"),
            unresolved_ids=_get_str_tuple(d, "unresolved_ids", "AspectResearchResult"),
            not_found_audit_id=_get_str(d, "not_found_audit_id", "AspectResearchResult", allow_none=True),
            authority_assessment=authority_from_dict(aa) if aa is not None else None,
            sufficiency_assessment=SufficiencyAssessment.from_dict(sa) if sa is not None else None,
        )


@dataclass(frozen=True)
class AtomicOutcomeEligibility:
    """adapt_outcome_completion 输出（原子资格，不产出 Pack 状态）。"""

    eligible: bool
    reason_code: str
    outcome_ref: str

    def to_dict(self) -> dict:
        return {"eligible": self.eligible, "reason_code": self.reason_code, "outcome_ref": self.outcome_ref}

    @classmethod
    def from_dict(cls, d: Any) -> "AtomicOutcomeEligibility":
        d = _reject_unknown(d, {"eligible", "reason_code", "outcome_ref"}, "AtomicOutcomeEligibility")
        return cls(
            eligible=_get_bool(d, "eligible", "AtomicOutcomeEligibility"),
            reason_code=_get_str(d, "reason_code", "AtomicOutcomeEligibility"),
            outcome_ref=_get_str(d, "outcome_ref", "AtomicOutcomeEligibility"),
        )


# ---------------------------------------------------------------------------
# 身份 / 输入 requirement
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackIdentity:
    """current 指针业务键 = (task_id, company_id, report_as_of, contract_fingerprint,
    source_policy_version, section_id, topic_id)。"""

    task_id: str
    company_id: str
    report_as_of: str | None
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str

    def key(self) -> tuple:
        return (self.task_id, self.company_id, self.report_as_of or "", self.contract_fingerprint,
                self.source_policy_version, self.section_id, self.topic_id)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id, "topic_id": self.topic_id,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "PackIdentity":
        d = _reject_unknown(d, {"task_id", "company_id", "report_as_of", "contract_fingerprint",
                                "source_policy_version", "section_id", "topic_id"}, "PackIdentity")
        return cls(
            task_id=_get_str(d, "task_id", "PackIdentity"),
            company_id=_get_str(d, "company_id", "PackIdentity"),
            report_as_of=_get_str(d, "report_as_of", "PackIdentity", allow_none=True),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "PackIdentity"),
            source_policy_version=_get_str(d, "source_policy_version", "PackIdentity"),
            section_id=_get_str(d, "section_id", "PackIdentity"),
            topic_id=_get_str(d, "topic_id", "PackIdentity"),
        )


def validate_dependency_versions(d: Any) -> dict[str, str]:
    """校验并返回排序规范形（不允许任意语义 dict）。"""
    if not isinstance(d, dict):
        raise SchemaValidationError(f"dependency_versions 必须为 dict，得到 {type(d).__name__}")
    out: dict[str, str] = {}
    for k, v in d.items():
        if k not in DEPENDENCY_VERSION_KEYS:
            raise SchemaValidationError(f"dependency_versions 未知键 {k!r}（允许 {DEPENDENCY_VERSION_KEYS}）")
        if not isinstance(v, str) or v == "":
            raise SchemaValidationError(f"dependency_versions[{k}] 必须为非空字符串")
        out[k] = v
    return dict(sorted(out.items()))


def compute_dependency_fingerprint(contract_fingerprint: str, source_policy_version: str,
                                   dependency_versions: dict[str, str]) -> str:
    dv = validate_dependency_versions(dependency_versions)
    return sha256_canonical({
        "contract_fingerprint": contract_fingerprint,
        "source_policy_version": source_policy_version,
        "dependency_versions": dv,
    })


@dataclass(frozen=True)
class TopicResearchRequirement:
    """Pack 的身份/输入上下文（可校验字段；R3 调度器消费）。"""

    task_id: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspects: tuple[TopicAspectRequirementSnapshot, ...]
    allowed_capabilities: tuple[str, ...]
    dependency_versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.topic_id or not self.section_id:
            raise SchemaValidationError("TopicResearchRequirement.topic_id/section_id 必须非空")
        if not self.contract_fingerprint:
            raise SchemaValidationError("TopicResearchRequirement.contract_fingerprint 必须非空")
        # aspect 集合：每条冻结投影 topic_id 必须与本 requirement 一致，且 aspect_id 唯一。
        seen: set[str] = set()
        for a in self.aspects:
            if a.topic_id != self.topic_id:
                raise SchemaValidationError(
                    f"aspect {a.aspect_id!r} topic_id={a.topic_id!r} 与 requirement topic_id={self.topic_id!r} 不一致")
            if a.aspect_id in seen:
                raise SchemaValidationError(f"requirement 含重复 aspect_id: {a.aspect_id!r}")
            seen.add(a.aspect_id)
        validate_dependency_versions(self.dependency_versions)

    def aspect_ids(self) -> tuple[str, ...]:
        return tuple(a.aspect_id for a in self.aspects)

    def dependency_fingerprint(self) -> str:
        return compute_dependency_fingerprint(self.contract_fingerprint, self.source_policy_version,
                                             self.dependency_versions)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_version": self.contract_version, "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version, "section_id": self.section_id,
            "topic_id": self.topic_id, "question_ids": list(self.question_ids),
            "aspects": [a.to_dict() for a in self.aspects],
            "allowed_capabilities": list(self.allowed_capabilities),
            "dependency_versions": dict(self.dependency_versions),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicResearchRequirement":
        d = _reject_unknown(d, {"task_id", "company_id", "report_as_of", "contract_version",
                                "contract_fingerprint", "source_policy_version", "section_id",
                                "topic_id", "question_ids", "aspects", "allowed_capabilities",
                                "dependency_versions"}, "TopicResearchRequirement")
        return cls(
            task_id=_get_str(d, "task_id", "TopicResearchRequirement"),
            company_id=_get_str(d, "company_id", "TopicResearchRequirement"),
            report_as_of=_get_str(d, "report_as_of", "TopicResearchRequirement", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicResearchRequirement"),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "TopicResearchRequirement"),
            source_policy_version=_get_str(d, "source_policy_version", "TopicResearchRequirement"),
            section_id=_get_str(d, "section_id", "TopicResearchRequirement"),
            topic_id=_get_str(d, "topic_id", "TopicResearchRequirement"),
            question_ids=_get_str_tuple(d, "question_ids", "TopicResearchRequirement"),
            aspects=tuple(TopicAspectRequirementSnapshot.from_dict(x) for x in _as_list(
                d.get("aspects"), "TopicResearchRequirement", "aspects")),
            allowed_capabilities=_get_str_tuple(d, "allowed_capabilities", "TopicResearchRequirement"),
            dependency_versions=validate_dependency_versions(d.get("dependency_versions", {})),
        )


# ---------------------------------------------------------------------------
# TopicResearchPack（P3 正式交付物）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicResearchPack:
    schema_version: str
    pack_id: str
    run_id: str
    task_id: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    aspect_results: tuple[AspectResearchResult, ...]
    materials: tuple[ResearchMaterial, ...]
    facts: tuple[SupportedFact, ...]
    outcome_refs: tuple[str, ...]
    external_funnel: ExternalFunnelSnapshot | None
    conflicts: tuple[ResearchConflict, ...]
    not_found_audits: tuple[NotFoundAudit, ...]
    unresolved: tuple[ResearchGap, ...]
    usage: TopicUsageSnapshot
    uncertain_calls: tuple[UncertainToolCallRecord, ...]
    process_status: PackProcessStatus
    coverage_status: PackCoverageStatus
    status_derivation: StatusDerivation
    dependency_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != TOPIC_PACK_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"TopicResearchPack.schema_version 必须为 {TOPIC_PACK_SCHEMA_VERSION!r}，"
                f"得到 {self.schema_version!r}")
        if not self.topic_id:
            raise SchemaValidationError("TopicResearchPack.topic_id 必须非空")
        # aspect 结果恰好覆盖每条 aspect 一次（不缺失、不重复、不混入其他 topic aspect）。
        seen: set[str] = set()
        for r in self.aspect_results:
            if r.requirement_snapshot.topic_id != self.topic_id:
                raise SchemaValidationError(
                    f"aspect_result {r.aspect_id!r} topic_id={r.requirement_snapshot.topic_id!r} "
                    f"与 Pack topic_id={self.topic_id!r} 不一致")
            if r.aspect_id in seen:
                raise SchemaValidationError(f"Pack 含重复 aspect_result: {r.aspect_id!r}")
            seen.add(r.aspect_id)

    # -- 身份 / 指纹 --------------------------------------------------------

    def identity(self) -> PackIdentity:
        return PackIdentity(
            task_id=self.task_id, company_id=self.company_id, report_as_of=self.report_as_of,
            contract_fingerprint=self.contract_fingerprint,
            source_policy_version=self.source_policy_version, section_id=self.section_id,
            topic_id=self.topic_id,
        )

    def content_fingerprint(self) -> str:
        """内容身份规范形（不含 run_id / pack_id / dependency_fingerprint / 双轴状态 /
        status_derivation / usage / uncertain_calls / outcome_refs）。"""
        body = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "question_ids": self.question_ids,
            "aspect_results": [r.to_dict() for r in self.aspect_results],
            "materials": [m.to_dict() for m in self.materials],
            "facts": [f.to_dict() for f in self.facts],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_found_audits": [n.to_dict() for n in self.not_found_audits],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "external_funnel": self.external_funnel.to_dict() if self.external_funnel else None,
        }
        return sha256_canonical(body)

    def compute_pack_id(self) -> str:
        """pack_id = content_fingerprint + dependency_fingerprint（确定性）。"""
        return f"{self.content_fingerprint()}_{self.dependency_fingerprint}"

    def verify_pack_id(self) -> None:
        expected = self.compute_pack_id()
        if self.pack_id and self.pack_id != expected:
            raise SchemaValidationError(
                f"TopicResearchPack.pack_id={self.pack_id!r} 与内容/依赖指纹不符（期望 {expected!r}）")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "company_id": self.company_id,
            "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "question_ids": list(self.question_ids),
            "aspect_results": [r.to_dict() for r in self.aspect_results],
            "materials": [m.to_dict() for m in self.materials],
            "facts": [f.to_dict() for f in self.facts],
            "outcome_refs": list(self.outcome_refs),
            "external_funnel": self.external_funnel.to_dict() if self.external_funnel else None,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_found_audits": [n.to_dict() for n in self.not_found_audits],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "usage": self.usage.to_dict(),
            "uncertain_calls": [u.to_dict() for u in self.uncertain_calls],
            "process_status": self.process_status.to_dict(),
            "coverage_status": self.coverage_status.to_dict(),
            "status_derivation": self.status_derivation.to_dict(),
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TopicResearchPack":
        d = _reject_unknown(d, {
            "schema_version", "pack_id", "run_id", "task_id", "company_id", "report_as_of",
            "contract_version", "contract_fingerprint", "source_policy_version", "section_id",
            "topic_id", "question_ids", "aspect_results", "materials", "facts", "outcome_refs",
            "external_funnel", "conflicts", "not_found_audits", "unresolved", "usage",
            "uncertain_calls", "process_status", "coverage_status", "status_derivation",
            "dependency_fingerprint",
        }, "TopicResearchPack")
        ef = d.get("external_funnel")
        return cls(
            schema_version=_get_str(d, "schema_version", "TopicResearchPack"),
            pack_id=_get_str(d, "pack_id", "TopicResearchPack", allow_empty=True) or "",
            run_id=_get_str(d, "run_id", "TopicResearchPack", allow_empty=True) or "",
            task_id=_get_str(d, "task_id", "TopicResearchPack"),
            company_id=_get_str(d, "company_id", "TopicResearchPack"),
            report_as_of=_get_str(d, "report_as_of", "TopicResearchPack", allow_none=True),
            contract_version=_get_str(d, "contract_version", "TopicResearchPack"),
            contract_fingerprint=_get_str(d, "contract_fingerprint", "TopicResearchPack"),
            source_policy_version=_get_str(d, "source_policy_version", "TopicResearchPack"),
            section_id=_get_str(d, "section_id", "TopicResearchPack"),
            topic_id=_get_str(d, "topic_id", "TopicResearchPack"),
            question_ids=_get_str_tuple(d, "question_ids", "TopicResearchPack"),
            aspect_results=tuple(AspectResearchResult.from_dict(x) for x in _as_list(
                d.get("aspect_results"), "TopicResearchPack", "aspect_results")),
            materials=tuple(ResearchMaterial.from_dict(x) for x in _as_list(
                d.get("materials"), "TopicResearchPack", "materials")),
            facts=tuple(SupportedFact.from_dict(x) for x in _as_list(
                d.get("facts"), "TopicResearchPack", "facts")),
            outcome_refs=_get_str_tuple(d, "outcome_refs", "TopicResearchPack"),
            external_funnel=ExternalFunnelSnapshot.from_dict(ef) if ef is not None else None,
            conflicts=tuple(ResearchConflict.from_dict(x) for x in _as_list(
                d.get("conflicts"), "TopicResearchPack", "conflicts")),
            not_found_audits=tuple(NotFoundAudit.from_dict(x) for x in _as_list(
                d.get("not_found_audits"), "TopicResearchPack", "not_found_audits")),
            unresolved=tuple(ResearchGap.from_dict(x) for x in _as_list(
                d.get("unresolved"), "TopicResearchPack", "unresolved")),
            usage=TopicUsageSnapshot.from_dict(_as_dict(d.get("usage"), "TopicResearchPack", "usage")),
            uncertain_calls=tuple(UncertainToolCallRecord.from_dict(x) for x in _as_list(
                d.get("uncertain_calls"), "TopicResearchPack", "uncertain_calls")),
            process_status=PackProcessStatus.from_dict(_as_dict(
                d.get("process_status"), "TopicResearchPack", "process_status")),
            coverage_status=PackCoverageStatus.from_dict(_as_dict(
                d.get("coverage_status"), "TopicResearchPack", "coverage_status")),
            status_derivation=StatusDerivation.from_dict(_as_dict(
                d.get("status_derivation"), "TopicResearchPack", "status_derivation")),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "TopicResearchPack"),
        )


def finalize_pack(pack: TopicResearchPack) -> TopicResearchPack:
    """回填确定性 pack_id（内容身份 + 依赖指纹）。"""
    pack.verify_pack_id()
    return dataclasses.replace(pack, pack_id=pack.compute_pack_id())


# ---------------------------------------------------------------------------
# 状态适配（架构约束 1：状态空间隔离，未知 fail-closed）
# ---------------------------------------------------------------------------

def outcome_to_ref(outcome: Any) -> str:
    """ResearchOutcome → 只读引用（确定性、不含 run_id）。"""
    qid = getattr(getattr(outcome, "state", None), "question_id", None)
    cs = getattr(outcome, "completion_status", None)
    if not qid or not cs:
        raise StateAdaptationError("outcome 缺 state.question_id / completion_status")
    if cs not in COMPLETION_STATUSES:
        raise StateAdaptationError(f"未知 completion_status: {cs!r}")
    return f"outcome:{qid}:{cs}"


def adapt_outcome_completion(outcome: Any) -> AtomicOutcomeEligibility:
    """单个原子 ResearchOutcome 是否可作 Pack 候选输入（原子资格，不产出 Pack 状态）。

    一个原子 ANSWER/COMPLETED 只结束当前 need，不结束整个 Topic。
    """
    cs = getattr(outcome, "completion_status", None)
    if cs not in COMPLETION_STATUSES:
        raise StateAdaptationError(f"未知 completion_status: {cs!r}")
    ref = outcome_to_ref(outcome)
    if cs == "COMPLETED":
        return AtomicOutcomeEligibility(True, "ATOMIC_COMPLETED", ref)
    if cs == "COMPLETED_WITH_GAPS":
        return AtomicOutcomeEligibility(True, "ATOMIC_COMPLETED_WITH_GAPS", ref)
    return AtomicOutcomeEligibility(False, f"ATOMIC_{cs}", ref)


def adapt_entailment_supported(verdict: str) -> bool:
    """EntailmentVerdict SUPPORTED → fact 可 adopted（其余 fail-closed）。"""
    if verdict not in ENTAILMENT_VERDICTS:
        raise StateAdaptationError(f"未知 EntailmentVerdict: {verdict!r}")
    return verdict == "SUPPORTED"


def adapt_entailment_reject(verdict: str) -> bool:
    """EntailmentVerdict PARTIAL / UNSUPPORTED → fact 不 adopted（候选区）。"""
    if verdict not in ENTAILMENT_VERDICTS:
        raise StateAdaptationError(f"未知 EntailmentVerdict: {verdict!r}")
    return verdict in ("PARTIAL", "UNSUPPORTED")


def _is_budget_stop(stop_reason: str) -> bool:
    return stop_reason.startswith(_BUDGET_STOP_PREFIXES)


def _is_block_stop(stop_reason: str) -> bool:
    return stop_reason in _BLOCK_STOP_CODES


def _is_fatal_stop(stop_reason: str) -> bool:
    return stop_reason in _FATAL_STOP_CODES


def derive_pack_status(required_aspect_ids: tuple[str, ...],
                       aspect_results: tuple[AspectResearchResult, ...],
                       stop_reason: str | None = None) -> tuple[PackProcessStatus, PackCoverageStatus, StatusDerivation]:
    """读取完整 required aspect 集合，确定性派生双轴状态（流程轴 ⊥ 覆盖轴）。

    - 校验：每个 required aspect 恰好一条结果，不缺失、不重复、不混入其他 aspect；
    - not_found 是 aspect 层证据结果，不直接作为 Pack 流程状态；
    - 流程已结束但含合格 not_found → process=finished + coverage=complete_with_gaps。
    """
    if len(set(required_aspect_ids)) != len(required_aspect_ids):
        raise StateAdaptationError("required_aspect_ids 含重复 aspect_id")
    by_id: dict[str, AspectResearchResult] = {}
    for r in aspect_results:
        if r.aspect_id in by_id:
            raise StateAdaptationError(f"aspect_result 重复: {r.aspect_id!r}")
        by_id[r.aspect_id] = r
    for aid in required_aspect_ids:
        if aid not in by_id:
            raise StateAdaptationError(f"缺失 required aspect 结果: {aid!r}")
    foreign = set(by_id) - set(required_aspect_ids)
    if foreign:
        raise StateAdaptationError(f"混入其他 aspect 结果: {sorted(foreign)!r}")

    statuses = {aid: by_id[aid].status for aid in required_aspect_ids}
    covered = tuple(a for a in required_aspect_ids if statuses[a] == "covered")
    not_applicable = tuple(a for a in required_aspect_ids if statuses[a] == "not_applicable")
    not_found = tuple(a for a in required_aspect_ids if statuses[a] == "not_found")
    gaps = tuple(a for a in required_aspect_ids if statuses[a] in ("partial", "not_found", "blocked"))
    terminal = {"covered", "not_applicable", "not_found"}

    all_terminal = all(statuses[a] in terminal for a in required_aspect_ids)

    # coverage 轴
    if all(statuses[a] in ("covered", "not_applicable") for a in required_aspect_ids):
        coverage_status = "complete"
    elif all_terminal:
        coverage_status = "complete_with_gaps"
    else:
        coverage_status = "insufficient"

    # process 轴
    if stop_reason is not None:
        if _is_budget_stop(stop_reason):
            process_status = "stopped_by_budget"
        elif _is_block_stop(stop_reason):
            process_status = "blocked"
        elif _is_fatal_stop(stop_reason):
            process_status = "failed"
        elif all_terminal:
            process_status = "finished"
        else:
            process_status = "blocked"
    else:
        if all_terminal:
            process_status = "finished"
        elif any(statuses[a] == "blocked" for a in required_aspect_ids):
            process_status = "blocked"
        elif len(covered) == 0:
            process_status = "pending"
        else:
            process_status = "running"

    per_aspect = tuple(AspectStatusEntry(a, statuses[a]) for a in required_aspect_ids)
    derivation_fingerprint = sha256_canonical({
        "required_aspect_ids": required_aspect_ids,
        "per_aspect": [e.to_dict() for e in per_aspect],
        "rule_version": STATUS_DERIVATION_RULE_VERSION,
    })
    process = PackProcessStatus(process_status, hard_stop_reason=stop_reason
                                if process_status in ("blocked", "stopped_by_budget", "failed")
                                else None)
    coverage = PackCoverageStatus(
        status=coverage_status, covered_aspect_ids=covered, gap_aspect_ids=gaps,
        not_applicable_aspect_ids=not_applicable)
    derivation = StatusDerivation(
        schema_version=TOPIC_PACK_SCHEMA_VERSION, rule_version=STATUS_DERIVATION_RULE_VERSION,
        derivation_fingerprint=derivation_fingerprint, per_aspect=per_aspect)
    return process, coverage, derivation
