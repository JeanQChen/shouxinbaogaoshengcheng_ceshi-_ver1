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
from typing import Any, Protocol

from contracts.schema_v2 import (
    REQUIRED_ASPECT_FIELDS,
    SOURCE_GRADES,
)
from harness.schema import CITATION_TYPES, COMPLETION_STATUSES, ENTAILMENT_VERDICTS

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

# TopicResearchPack 序列化 schema 版本（to_dict/from_dict 契约版本）。
# v2：JSON payload/schema 解释语义升级（set_complete 独立枚举证明 + SourcePolicyRef 唯一绑定），
#     无新增 SQLite 列；由迁移 2 记录该解释语义升级（见 topic_store.topic_schema_migrations）。
# v3：DEPENDENCY_VERSION_KEYS 增 set_enumerator（正式 SetEnumerationVerifier 进入依赖指纹），
#     与 Store schema v3（topic_material_payload 表）是两个独立版本维度，不重新耦合。
TOPIC_PACK_SCHEMA_VERSION = "3"
# Store 结构 schema 版本（独立维度，与 TOPIC_PACK_SCHEMA_VERSION 解耦；断言
# init_topic_store 时 STORE_SCHEMA_VERSION == MIGRATIONS[-1][0]）。
STORE_SCHEMA_VERSION = "3"
# StatusDerivation 推导规则版本（derive_pack_status 语义版本）。
STATUS_DERIVATION_RULE_VERSION = "1"
# 依赖版本字典允许的键（不允许任意语义 dict）。
DEPENDENCY_VERSION_KEYS = (
    "contract",
    "source_policy",
    "topic_schema",
    "assessor",
    "validator",
    "set_enumerator",
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

# 权威类型 → 来源类（source_class，对齐 contracts.schema_v2 / source_policy_v1 的 source_classes）。
# financial_snapshot 是公司结构化披露（非 external），与 company disclosure 同属「非外部」来源。
AUTHORITY_SOURCE_CLASS_BY_TYPE = {
    "evidence": "company_industry",
    "financial_snapshot": "structured_db",
    "external_snapshot": "external",
}

# 行业风险传导四层（对齐 contracts.schema_v2.TRANSMISSION_LAYERS）。
TRANSMISSION_LAYERS = (
    "industry_background",
    "conditional_transmission",
    "company_exposure",
    "actual_company_impact",
)

# sufficiency gate 评估器版本与规则 ID。规则本身由冻结输入（EvidenceRequirement +
# SourcePolicy + transmission_layers）派生，绝不硬编码 Topic 名单。
SUFFICIENCY_ASSESSOR_VERSION = "1"
KEY_CONCLUSION_RULE = "key_conclusion_ab_c"
KEY_CONCLUSION_RULE_VERSION = "1"
TRANSMISSION_SUFFICIENCY_RULES = {
    "industry_background": ("industry_background_external", "1"),
    "conditional_transmission": ("conditional_transmission_verified_fact", "1"),
    "company_exposure": ("company_exposure_company_disclosure", "1"),
    "actual_company_impact": ("actual_company_impact_company_disclosure", "1"),
}

# support eligibility 政策版本（usage-scope gate 的派生结果版本）。
SUPPORT_ELIGIBILITY_POLICY_VERSION = "1"

# set_complete 评估器版本 / 规则版本 / 可信 verifier 版本（Fix 4 强制一致）。
SET_COMPLETENESS_ASSESSOR_VERSION = "1"
SET_COMPLETENESS_RULE_VERSION = "1"
SET_COMPLETENESS_VERIFIER_VERSION = "1"
# set_complete 独立枚举 verifier 版本（Fix 2）。SetEnumerationVerifier 是受信任、版本化、
# 确定性的运行时依赖：Store 只能校验它返回的 payload_hash / boundary_identity / enumerated
# 集合关系与真实解析 payload 身份一致，无法证明任意注入实现「内部确实读取过 payload bytes」。
# R2 须实现正式确定性枚举器并由唯一正式组合入口注入；接线前生产运行链不得将 set_complete
# aspect 提升为 covered。此版本必须进入 R2 dependency fingerprint（本轮只记录该硬门）。
SET_ENUMERATION_VERIFIER_VERSION = "1"

# 条件性行业传导 inference 政策（版本化允许枚举；冻结 Contract 只枚举字段名，不枚举字段值，
# 故 policy 身份/版本与 direction 枚举由本 schema 版本化定义，Store 据此 fail-closed 校验）。
CONDITIONAL_INFERENCE_POLICY_ID = "conditional-transmission-inference-v1"
CONDITIONAL_INFERENCE_POLICY_VERSION = "1"
INFERENCE_DIRECTIONS = ("industry_to_company",)


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
class SourceClassGroup:
    """required_any_of 的一个可选组：一组来源类 + 可选最低等级 + 可选事实类别。"""

    source_classes: tuple[str, ...]
    min_grade: str | None = None
    kind: str | None = None

    def __post_init__(self) -> None:
        if not self.source_classes:
            raise SchemaValidationError("SourceClassGroup.source_classes 必须非空")
        if self.min_grade is not None:
            _get_enum(self.min_grade, SOURCE_GRADES, "SourceClassGroup", "min_grade")

    def to_dict(self) -> dict:
        return {
            "source_classes": list(self.source_classes),
            "min_grade": self.min_grade,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceClassGroup":
        d = _reject_unknown(d, {"source_classes", "min_grade", "kind"}, "SourceClassGroup")
        return cls(
            source_classes=_get_str_tuple(d, "source_classes", "SourceClassGroup"),
            min_grade=_get_str(d, "min_grade", "SourceClassGroup", allow_none=True),
            kind=_get_str(d, "kind", "SourceClassGroup", allow_none=True),
        )


@dataclass(frozen=True)
class EvidenceAuthorityPolicy:
    """冻结证据需求的 authority 使用范围（required_any_of / supplemental_only / inference_lineage）。

    这是「来源使用资格」而非「来源权威」：required_any_of 决定哪些来源类可作为 formal
    事实支撑该 aspect；supplemental_only 决定哪些来源类只能补充、不能独立支撑；
    inference_lineage_required 决定是否需要推断链。
    """

    required_any_of: tuple[SourceClassGroup, ...]
    supplemental_only: tuple[str, ...] = ()
    inference_lineage_required: bool = False

    def __post_init__(self) -> None:
        if not self.required_any_of:
            raise SchemaValidationError("EvidenceAuthorityPolicy.required_any_of 必须非空")

    def to_dict(self) -> dict:
        return {
            "required_any_of": [g.to_dict() for g in self.required_any_of],
            "supplemental_only": list(self.supplemental_only),
            "inference_lineage_required": self.inference_lineage_required,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceAuthorityPolicy":
        d = _reject_unknown(d, {"required_any_of", "supplemental_only", "inference_lineage_required"},
                            "EvidenceAuthorityPolicy")
        return cls(
            required_any_of=tuple(SourceClassGroup.from_dict(x) for x in _as_list(
                d.get("required_any_of"), "EvidenceAuthorityPolicy", "required_any_of")),
            supplemental_only=_get_str_tuple(d, "supplemental_only", "EvidenceAuthorityPolicy"),
            inference_lineage_required=_get_bool(d, "inference_lineage_required", "EvidenceAuthorityPolicy"),
        )


@dataclass(frozen=True)
class EvidenceRequirementRef:
    """证据需求权威引用（绑定 requirement ID + 所属 Contract SHA + requirement 指纹 + schema/version
    + 来源类使用资格）。source_classes / authority 用于 usage-scope gate（Fix 1），可空表示
    无冻结使用资格信息（旧合成引用不触发 usage-scope gate）。"""

    requirement_id: str
    contract_sha256: str
    requirement_fingerprint: str
    schema_version: str
    source_classes: tuple[str, ...] = ()
    authority: EvidenceAuthorityPolicy | None = None

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
            "source_classes": list(self.source_classes),
            "authority": self.authority.to_dict() if self.authority is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "EvidenceRequirementRef":
        d = _reject_unknown(d, {"requirement_id", "contract_sha256", "requirement_fingerprint",
                                "schema_version", "source_classes", "authority"},
                            "EvidenceRequirementRef")
        auth = d.get("authority")
        return cls(
            requirement_id=_get_str(d, "requirement_id", "EvidenceRequirementRef"),
            contract_sha256=_get_str(d, "contract_sha256", "EvidenceRequirementRef"),
            requirement_fingerprint=_get_str(d, "requirement_fingerprint", "EvidenceRequirementRef"),
            schema_version=_get_str(d, "schema_version", "EvidenceRequirementRef"),
            source_classes=_get_str_tuple(d, "source_classes", "EvidenceRequirementRef"),
            authority=EvidenceAuthorityPolicy.from_dict(auth) if auth is not None else None,
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
    formula_version: str | None = None
    item_code: str | None = None
    period: str | None = None

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialLocator.snapshot_id 必须非空")
        # item_code 与 formula_id 至少一个有效。
        if not self.item_code and not self.formula_id:
            raise SchemaValidationError("FinancialLocator 至少需要 item_code 或 formula_id 之一")
        # Fix 2：formula_id 存在 → 必须可验证 formula_version；item-only 不得伪造公式版本。
        if self.formula_id is not None and not self.formula_version:
            raise SchemaValidationError("FinancialLocator 引用 formula_id 必须携带 formula_version")
        if self.formula_id is None and self.formula_version is not None:
            raise SchemaValidationError("FinancialLocator.formula_version 不得脱离 formula_id 存在")

    def to_dict(self) -> dict:
        return {
            "locator_type": self.locator_type,
            "snapshot_id": self.snapshot_id,
            "company_id": self.company_id,
            "scope": self.scope,
            "report_as_of": self.report_as_of,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "item_code": self.item_code,
            "period": self.period,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialLocator":
        d = _reject_unknown(d, {"locator_type", "snapshot_id", "company_id", "scope", "report_as_of",
                                "formula_id", "formula_version", "item_code", "period"}, "FinancialLocator")
        if d.get("locator_type") not in (None, "financial_snapshot"):
            raise SchemaValidationError(
                f"FinancialLocator.locator_type 必须为 'financial_snapshot'，得到 {d.get('locator_type')!r}")
        return cls(
            snapshot_id=_get_str(d, "snapshot_id", "FinancialLocator"),
            company_id=_get_str(d, "company_id", "FinancialLocator", allow_empty=True) or "",
            scope=_get_str(d, "scope", "FinancialLocator", allow_empty=True) or "",
            report_as_of=_get_str(d, "report_as_of", "FinancialLocator", allow_empty=True) or "",
            formula_id=_get_str(d, "formula_id", "FinancialLocator", allow_none=True),
            formula_version=_get_str(d, "formula_version", "FinancialLocator", allow_none=True),
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
    formula_version: str | None = None
    period: str | None = None
    verdict: str = "rejected"
    reason: str = ""
    validator_version: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            raise SchemaValidationError("FinancialSnapshotAuthorityAssessment.snapshot_id 必须非空")
        _get_enum(self.validity, FINANCIAL_VALIDITIES, "FinancialSnapshotAuthorityAssessment", "validity")
        _get_enum(self.verdict, AUTHORITY_VERDICTS, "FinancialSnapshotAuthorityAssessment", "verdict")
        # Fix 2：formula_id 存在 → 必须可验证 formula_version；item-only 不得伪造公式版本。
        if self.formula_id is not None and not self.formula_version:
            raise SchemaValidationError(
                "FinancialSnapshotAuthorityAssessment 引用 formula_id 必须携带 formula_version")
        if self.formula_id is None and self.formula_version is not None:
            raise SchemaValidationError(
                "FinancialSnapshotAuthorityAssessment.formula_version 不得脱离 formula_id 存在")

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
            "formula_version": self.formula_version,
            "period": self.period,
            "verdict": self.verdict,
            "reason": self.reason,
            "validator_version": self.validator_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FinancialSnapshotAuthorityAssessment":
        d = _reject_unknown(d, {"authority_type", "snapshot_id", "company_id", "scope", "currency",
                                "purpose", "report_as_of", "is_current", "validity", "report_blocked",
                                "quarantine", "item_code", "formula_id", "formula_version", "period",
                                "verdict", "reason", "validator_version"}, "FinancialSnapshotAuthorityAssessment")
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
            formula_version=_get_str(d, "formula_version", "FinancialSnapshotAuthorityAssessment",
                                     allow_none=True),
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
        if not _is_sha256_hex(self.created_dependency_fingerprint):
            raise SchemaValidationError(
                "MaterialPayloadRef.created_dependency_fingerprint 必须为 64 位 sha256 hex")

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


@dataclass(frozen=True)
class ResolvedPayload:
    """payload resolver 的解析结果（不可变 payload 的身份 + 内容哈希）。"""

    object_type: str
    authority_identity: str
    version: str
    locator: MaterialLocator
    content_hash: str
    payload_bytes: bytes | None = None


class PayloadResolver(Protocol):
    """R1-B 最小 payload resolver 边界（依赖注入；不直接依赖 Evidence/Financial/External Store）。"""

    def resolve(self, payload_ref: MaterialPayloadRef) -> ResolvedPayload | None:
        """解析 payload_ref 到不可变 payload；dangling（目标不存在）→ None。"""
        ...


def verify_material_payload_ref(payload_ref: MaterialPayloadRef,
                                resolver: PayloadResolver) -> ResolvedPayload:
    """校验 payload_ref 可解析且身份/哈希一致。

    dangling / object_type 不符 / authority_identity 不符 / version 不符 / locator 不一致 /
    content_hash 不符 / payload 字节哈希不匹配 → 全部 fail-closed（SchemaValidationError）。
    """
    resolved = resolver.resolve(payload_ref)
    if resolved is None:
        raise SchemaValidationError(
            f"MaterialPayloadRef dangling：{payload_ref.object_type}:{payload_ref.authority_identity}"
            f"@{payload_ref.version}")
    if resolved.object_type != payload_ref.object_type:
        raise SchemaValidationError(
            f"MaterialPayloadRef object_type 不符：期望 {payload_ref.object_type!r}，"
            f"得到 {resolved.object_type!r}")
    if resolved.authority_identity != payload_ref.authority_identity:
        raise SchemaValidationError(
            f"MaterialPayloadRef authority_identity 不符：期望 {payload_ref.authority_identity!r}，"
            f"得到 {resolved.authority_identity!r}")
    if resolved.version != payload_ref.version:
        raise SchemaValidationError(
            f"MaterialPayloadRef version 不符：期望 {payload_ref.version!r}，得到 {resolved.version!r}")
    if resolved.locator.to_dict() != payload_ref.locator.to_dict():
        raise SchemaValidationError("MaterialPayloadRef locator 与解析目标不一致")
    if resolved.content_hash != payload_ref.content_hash:
        raise SchemaValidationError(
            f"MaterialPayloadRef content_hash 不符：期望 {payload_ref.content_hash!r}，"
            f"得到 {resolved.content_hash!r}")
    if resolved.payload_bytes is not None:
        if hashlib.sha256(resolved.payload_bytes).hexdigest() != payload_ref.content_hash:
            raise SchemaValidationError("MaterialPayloadRef payload 内容哈希不匹配")
    return resolved


def recompute_authority_verdict(authority: AuthorityAssessment) -> str:
    """确定性重算权威结论（不信任调用方自填 verdict）。

    权威门只判断「来源及事实载体是否真实、完整、可回查、版本有效」，不判断「该来源能否独立
    证明公司级结论」（后者属于 aspect usage-scope gate / sufficiency gate，见 Fix 1/Fix 4）。

    - Evidence / Financial：满足该来源类型支持「正式事实」的全部资格字段 → authoritative。
      Financial 的 item_code / formula_id 至少一个有效（Fix 2：item-only / formula-only 合法）。
    - External：fetched 正文非空 + content_hash 有效 + canonical URL/domain 有效 + grade != D
      + 时间资格有效 + 来源身份一致 → authoritative（A/B/C 单条 external 事实可过权威门，
      但「权威」≠「充分」，是否可作 formal 行业事实由 usage-scope gate 判定；D → rejected）。
    """
    if isinstance(authority, EvidenceAuthorityAssessment):
        ok = (authority.is_current_document and authority.is_current_set
              and authority.fetched_inspected_nonempty
              and bool(authority.document_id) and bool(authority.company_id)
              and (authority.page is not None or authority.block_range is not None)
              and _is_sha256_hex(authority.content_hash))
        return "authoritative" if ok else "rejected"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        ok = (authority.is_current and authority.validity == "valid"
              and not authority.report_blocked and not authority.quarantine
              and bool(authority.company_id) and bool(authority.scope)
              and bool(authority.currency) and bool(authority.purpose)
              and bool(authority.report_as_of)
              and (authority.item_code is not None or authority.formula_id is not None)
              and authority.period is not None)
        return "authoritative" if ok else "rejected"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        ok = (authority.fetched_nonempty and _is_sha256_hex(authority.content_hash)
              and bool(authority.canonical_url) and bool(authority.domain)
              and authority.source_grade != "D" and authority.min_grade_met
              and authority.time_qualified)
        return "authoritative" if ok else "rejected"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def authority_source_identity(authority: AuthorityAssessment) -> str:
    """三类权威的来源身份字符串（material authority ↔ fact source_authority ↔ CitationRef 一致性域）。"""
    if isinstance(authority, EvidenceAuthorityAssessment):
        return f"evidence:{authority.evidence_id}"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        return f"financial_snapshot:{authority.snapshot_id}"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return f"external_snapshot:{authority.source_snapshot_id}"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def citation_source_identity(citation: CitationRef) -> str:
    """CitationRef 的来源身份（与 authority_source_identity 同域，供一致性比对）。"""
    if citation.ref_type == "evidence":
        return f"evidence:{citation.evidence_id or ''}"
    if citation.ref_type == "structured":
        return f"financial_snapshot:{citation.snapshot_id or ''}"
    if citation.ref_type == "external":
        return f"external_snapshot:{citation.source_snapshot_id or ''}"
    raise SchemaValidationError(f"未知 CitationRef.ref_type: {citation.ref_type!r}")


def authority_source_class(authority: AuthorityAssessment) -> str:
    """三类权威 → 来源类（source_class）。用于 usage-scope gate（Fix 1）。"""
    if isinstance(authority, EvidenceAuthorityAssessment):
        return "company_industry"
    if isinstance(authority, FinancialSnapshotAuthorityAssessment):
        return "structured_db"
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return "external"
    raise TypeError(f"未知 authority 类型: {type(authority).__name__}")


def authority_source_grade(authority: AuthorityAssessment) -> str | None:
    """来源等级（A/B/C/D）。仅 external 有 grade；evidence/financial 为公司披露/结构化，
    不属于 external 分级体系 → None。"""
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return authority.source_grade
    return None


def authority_independence_domain(authority: AuthorityAssessment) -> str | None:
    """独立性域（仅 external 有意义，用于 sufficiency 的「≥2 相互独立 C」复算）。"""
    if isinstance(authority, ExternalSnapshotAuthorityAssessment):
        return authority.independence_domain or None
    return None


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
    # Fix 2：structured 的 locator↔authority 必须形成完整财务身份闭环。
    # item-only / formula-only / 双身份 各按规则强制一致，跨身份错配、无谓 formula、版本/period
    # 不对称一律拒绝（不允许 locator 与 authority 跨身份错配，也不允许 item-only 伪造 formula）。
    if material_type == "structured":
        validate_financial_identity(locator, authority)


def validate_financial_identity(locator: "FinancialLocator",
                                authority: "FinancialSnapshotAuthorityAssessment") -> None:
    """locator ↔ authority 财务身份闭环（snapshot/item/formula/formula_version/period）。"""
    if locator.snapshot_id != authority.snapshot_id:
        raise SchemaValidationError(
            f"financial locator.snapshot_id={locator.snapshot_id!r} 与 "
            f"authority.snapshot_id={authority.snapshot_id!r} 不一致")
    if (locator.period or "") != (authority.period or ""):
        raise SchemaValidationError(
            f"financial locator.period={locator.period!r} 与 "
            f"authority.period={authority.period!r} 不一致")
    # item 身份：两侧必须一致地给出/缺失，且值相等（item-only 不得混入 formula）。
    if (locator.item_code is not None) != (authority.item_code is not None):
        raise SchemaValidationError(
            "financial item_code 身份不对称：locator/authority 一侧 item-only 另一侧无 item")
    if locator.item_code is not None and locator.item_code != authority.item_code:
        raise SchemaValidationError(
            f"financial locator.item_code={locator.item_code!r} 与 "
            f"authority.item_code={authority.item_code!r} 不一致")
    # formula 身份：两侧必须一致地给出/缺失，formula_id + formula_version 都相等。
    if (locator.formula_id is not None) != (authority.formula_id is not None):
        raise SchemaValidationError(
            "financial formula_id 身份不对称：locator/authority 一侧 formula-only 另一侧无 formula")
    if locator.formula_id is not None and locator.formula_id != authority.formula_id:
        raise SchemaValidationError(
            f"financial locator.formula_id={locator.formula_id!r} 与 "
            f"authority.formula_id={authority.formula_id!r} 不一致")
    if locator.formula_id is not None and (locator.formula_version or "") != (authority.formula_version or ""):
        raise SchemaValidationError(
            f"financial locator.formula_version={locator.formula_version!r} 与 "
            f"authority.formula_version={authority.formula_version!r} 不一致")


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
        # material 与 payload_ref 的 typed 身份必须严格一致（§三.1：不得只验证 resolver 自报字段）。
        if self.payload_ref.object_type != self.material_type:
            raise SchemaValidationError(
                f"ResearchMaterial.material_type={self.material_type!r} 与 "
                f"payload_ref.object_type={self.payload_ref.object_type!r} 不一致")
        if self.payload_ref.locator.to_dict() != self.locator.to_dict():
            raise SchemaValidationError("ResearchMaterial.locator 与 payload_ref.locator 不一致")

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
class InferenceLineage:
    """条件性行业传导的推断血缘（Fix 3 类型化载体，绑定 inference SupportedFact）。

    字段名与冻结 Contract `er_ind_transmission_conditional.authority.inference_lineage.fields`
    逐一对应（inference_policy_ref / channel / direction / conditions / limitation /
    derived_from_fact_ids），另附版本化 rule_version。缺任一必填字段 → 构造即 fail-closed。
    """

    inference_policy_ref: str
    rule_version: str
    channel: str
    direction: str
    conditions: tuple[str, ...]
    limitation: tuple[str, ...]
    derived_from_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.inference_policy_ref:
            raise SchemaValidationError("InferenceLineage.inference_policy_ref 必须非空")
        if not self.rule_version:
            raise SchemaValidationError("InferenceLineage.rule_version 必须非空")
        if not self.channel:
            raise SchemaValidationError("InferenceLineage.channel 必须非空")
        _get_enum(self.direction, INFERENCE_DIRECTIONS, "InferenceLineage", "direction")
        if not self.conditions:
            raise SchemaValidationError("InferenceLineage.conditions 必须非空")
        if not self.limitation:
            raise SchemaValidationError("InferenceLineage.limitation 必须非空")
        if not self.derived_from_fact_ids:
            raise SchemaValidationError("InferenceLineage.derived_from_fact_ids 必须非空")
        if len(set(self.derived_from_fact_ids)) != len(self.derived_from_fact_ids):
            raise SchemaValidationError("InferenceLineage.derived_from_fact_ids 不得重复")

    def to_dict(self) -> dict:
        return {
            "inference_policy_ref": self.inference_policy_ref,
            "rule_version": self.rule_version,
            "channel": self.channel,
            "direction": self.direction,
            "conditions": list(self.conditions),
            "limitation": list(self.limitation),
            "derived_from_fact_ids": list(self.derived_from_fact_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "InferenceLineage":
        d = _reject_unknown(d, {"inference_policy_ref", "rule_version", "channel", "direction",
                                "conditions", "limitation", "derived_from_fact_ids"}, "InferenceLineage")
        return cls(
            inference_policy_ref=_get_str(d, "inference_policy_ref", "InferenceLineage"),
            rule_version=_get_str(d, "rule_version", "InferenceLineage"),
            channel=_get_str(d, "channel", "InferenceLineage"),
            direction=_get_str(d, "direction", "InferenceLineage"),
            conditions=_get_str_tuple(d, "conditions", "InferenceLineage"),
            limitation=_get_str_tuple(d, "limitation", "InferenceLineage"),
            derived_from_fact_ids=_get_str_tuple(d, "derived_from_fact_ids", "InferenceLineage"),
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
    # 本事实取得的 required_fields 标识（用于 required_fields_complete 覆盖证明；无运行时集合证明）。
    obtained_fields: tuple[str, ...] = ()
    # Fix 3：inference fact 的类型化推断血缘（普通 fact 为 None）。
    inference_lineage: InferenceLineage | None = None

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
        if self.fact_type == "inference" and self.inference_lineage is None:
            raise SchemaValidationError("SupportedFact.fact_type=inference 必须携带 inference_lineage")
        if self.fact_type == "fact" and self.inference_lineage is not None:
            raise SchemaValidationError("SupportedFact.fact_type=fact 不得携带 inference_lineage")

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
            "obtained_fields": list(self.obtained_fields),
            "inference_lineage": self.inference_lineage.to_dict() if self.inference_lineage else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SupportedFact":
        d = _reject_unknown(d, {"fact_id", "text", "fact_type", "aspect_ids", "citation_refs",
                                "source_authority", "value_identity", "semantic_tags", "period",
                                "scope", "confidence", "obtained_fields", "inference_lineage"},
                            "SupportedFact")
        vi = d.get("value_identity")
        il = d.get("inference_lineage")
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
            obtained_fields=_get_str_tuple(d, "obtained_fields", "SupportedFact"),
            inference_lineage=InferenceLineage.from_dict(il) if il is not None else None,
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
class FrozenSourcePolicySnapshot:
    """冻结 SourcePolicy 的最小投影（sufficiency 复算所需的独立冻结输入）。

    只携带可确定性复算 sufficiency 的字段；绝不含 Pack 自证布尔（不信任调用方）。
    key_industry_topics 由 source_policy.load_source_policy 派生，不硬编码。
    """

    policy_id: str
    policy_version: str
    content_fingerprint: str
    key_industry_topics: tuple[str, ...] = ()
    key_conclusion_rule: str = KEY_CONCLUSION_RULE
    key_conclusion_rule_version: str = KEY_CONCLUSION_RULE_VERSION

    def __post_init__(self) -> None:
        if not self.policy_id or not self.policy_version:
            raise SchemaValidationError("FrozenSourcePolicySnapshot.policy_id/policy_version 必须非空")
        if not _is_sha256_hex(self.content_fingerprint):
            raise SchemaValidationError("FrozenSourcePolicySnapshot.content_fingerprint 必须为 64 位 sha256 hex")
        if not self.key_conclusion_rule or not self.key_conclusion_rule_version:
            raise SchemaValidationError("FrozenSourcePolicySnapshot.key_conclusion_rule/version 必须非空")

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "content_fingerprint": self.content_fingerprint,
            "key_industry_topics": list(self.key_industry_topics),
            "key_conclusion_rule": self.key_conclusion_rule,
            "key_conclusion_rule_version": self.key_conclusion_rule_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "FrozenSourcePolicySnapshot":
        d = _reject_unknown(d, {"policy_id", "policy_version", "content_fingerprint",
                                "key_industry_topics", "key_conclusion_rule",
                                "key_conclusion_rule_version"}, "FrozenSourcePolicySnapshot")
        return cls(
            policy_id=_get_str(d, "policy_id", "FrozenSourcePolicySnapshot"),
            policy_version=_get_str(d, "policy_version", "FrozenSourcePolicySnapshot"),
            content_fingerprint=_get_str(d, "content_fingerprint", "FrozenSourcePolicySnapshot"),
            key_industry_topics=_get_str_tuple(d, "key_industry_topics", "FrozenSourcePolicySnapshot"),
            key_conclusion_rule=_get_str(d, "key_conclusion_rule", "FrozenSourcePolicySnapshot"),
            key_conclusion_rule_version=_get_str(d, "key_conclusion_rule_version", "FrozenSourcePolicySnapshot"),
        )


class SourcePolicyResolver(Protocol):
    """R1-B 最小 SourcePolicy 解析边界（依赖注入；不直接信任调用方构造的 FrozenSourcePolicySnapshot）。

    resolve 从独立冻结来源按 SourcePolicyRef 解析不可变政策投影；不可解析 / dangling → None。
    """

    def resolve(self, source_policy_ref: SourcePolicyRef) -> FrozenSourcePolicySnapshot | None:
        """解析 SourcePolicyRef 到不可变冻结投影；dangling → None。"""
        ...


def verify_frozen_source_policy(source_policy_ref: SourcePolicyRef,
                                resolved: FrozenSourcePolicySnapshot | None) -> FrozenSourcePolicySnapshot:
    """校验 resolver 返回的冻结 SourcePolicy 与 SourcePolicyRef 身份闭合一致（Fix 1 fail-closed）。

    dangling / policy_id 不一致 / policy_version 不一致 / content_fingerprint 不一致 /
    key_industry_topics 为空（伪造）/ key_conclusion_rule·version 与可信常量不一致 → 全部拒绝。
    绝不把调用方任意构造的 FrozenSourcePolicySnapshot 当作可信输入，也绝不通过空 topic 名单
    或省略 resolver 关闭 sufficiency gate。
    """
    if resolved is None:
        raise SchemaValidationError(
            f"SourcePolicyRef 无法解析（dangling）：{source_policy_ref.policy_id}"
            f"@{source_policy_ref.policy_version}")
    if resolved.policy_id != source_policy_ref.policy_id:
        raise SchemaValidationError(
            f"SourcePolicy policy_id 不一致：期望 {source_policy_ref.policy_id!r}，"
            f"得到 {resolved.policy_id!r}")
    if resolved.policy_version != source_policy_ref.policy_version:
        raise SchemaValidationError(
            f"SourcePolicy policy_version 不一致：期望 {source_policy_ref.policy_version!r}，"
            f"得到 {resolved.policy_version!r}")
    if resolved.content_fingerprint != source_policy_ref.content_fingerprint:
        raise SchemaValidationError(
            f"SourcePolicy content_fingerprint 不一致：期望 {source_policy_ref.content_fingerprint!r}，"
            f"得到 {resolved.content_fingerprint!r}")
    if not resolved.key_industry_topics:
        raise SchemaValidationError("SourcePolicy key_industry_topics 为空（伪造；fail-closed）")
    if resolved.key_conclusion_rule != KEY_CONCLUSION_RULE:
        raise SchemaValidationError(
            f"SourcePolicy key_conclusion_rule 不一致：期望 {KEY_CONCLUSION_RULE!r}，"
            f"得到 {resolved.key_conclusion_rule!r}")
    if resolved.key_conclusion_rule_version != KEY_CONCLUSION_RULE_VERSION:
        raise SchemaValidationError(
            f"SourcePolicy key_conclusion_rule_version 不一致：期望 {KEY_CONCLUSION_RULE_VERSION!r}，"
            f"得到 {resolved.key_conclusion_rule_version!r}")
    return resolved


@dataclass(frozen=True)
class SupportEligibilityAssessment:
    """usage-scope gate 派生结果（Fix 1）：某 aspect 允许哪些来源类作 required / supplemental。

    由冻结 EvidenceRequirementRef.source_classes/authority 确定性派生
    （derive_support_eligibility），不硬编码 Topic ID，不含自由文本。
    """

    aspect_id: str
    policy_version: str
    required_source_classes: tuple[str, ...]
    supplemental_only_source_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("SupportEligibilityAssessment.aspect_id 必须非空")
        if not self.policy_version:
            raise SchemaValidationError("SupportEligibilityAssessment.policy_version 必须非空")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "policy_version": self.policy_version,
            "required_source_classes": list(self.required_source_classes),
            "supplemental_only_source_classes": list(self.supplemental_only_source_classes),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SupportEligibilityAssessment":
        d = _reject_unknown(d, {"aspect_id", "policy_version", "required_source_classes",
                                "supplemental_only_source_classes"}, "SupportEligibilityAssessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SupportEligibilityAssessment"),
            policy_version=_get_str(d, "policy_version", "SupportEligibilityAssessment"),
            required_source_classes=_get_str_tuple(d, "required_source_classes", "SupportEligibilityAssessment"),
            supplemental_only_source_classes=_get_str_tuple(d, "supplemental_only_source_classes",
                                                           "SupportEligibilityAssessment"),
        )


@dataclass(frozen=True)
class SetCompletenessAssessment:
    """set_complete 的类型化证明（Fix 3）：在明确权威披露范围内完整归拢枚举。

    绑定 material 身份 + 文档版本 + 章节/表边界 + expected/observed 成员 + 排除理由 +
    supporting material/fact + scope_complete + 评估器/推导版本 + Contract/dependency 指纹。
    """

    aspect_id: str
    rule_version: str
    source_material_ids: tuple[str, ...]
    document_version: str
    source_boundary: str
    expected_member_ids: tuple[str, ...]
    observed_member_ids: tuple[str, ...]
    excluded_member_ids: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    supporting_material_ids: tuple[str, ...]
    supporting_fact_ids: tuple[str, ...]
    scope_complete: bool
    assessor_version: str
    contract_sha256: str
    dependency_fingerprint: str

    def __post_init__(self) -> None:
        if not self.aspect_id:
            raise SchemaValidationError("SetCompletenessAssessment.aspect_id 必须非空")
        if not self.rule_version:
            raise SchemaValidationError("SetCompletenessAssessment.rule_version 必须非空")
        if not self.source_material_ids:
            raise SchemaValidationError("SetCompletenessAssessment.source_material_ids 必须非空")
        if not self.document_version or not self.source_boundary:
            raise SchemaValidationError("SetCompletenessAssessment.document_version/source_boundary 必须非空")
        if not self.expected_member_ids:
            raise SchemaValidationError("SetCompletenessAssessment.expected_member_ids 必须非空")
        if len(self.excluded_member_ids) != len(self.exclusion_reasons):
            raise SchemaValidationError(
                "SetCompletenessAssessment.excluded_member_ids 与 exclusion_reasons 必须一一对应")
        if not _is_sha256_hex(self.contract_sha256):
            raise SchemaValidationError("SetCompletenessAssessment.contract_sha256 必须为 64 位 sha256 hex")
        if not _is_sha256_hex(self.dependency_fingerprint):
            raise SchemaValidationError("SetCompletenessAssessment.dependency_fingerprint 必须为 64 位 sha256 hex")

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "rule_version": self.rule_version,
            "source_material_ids": list(self.source_material_ids),
            "document_version": self.document_version,
            "source_boundary": self.source_boundary,
            "expected_member_ids": list(self.expected_member_ids),
            "observed_member_ids": list(self.observed_member_ids),
            "excluded_member_ids": list(self.excluded_member_ids),
            "exclusion_reasons": list(self.exclusion_reasons),
            "supporting_material_ids": list(self.supporting_material_ids),
            "supporting_fact_ids": list(self.supporting_fact_ids),
            "scope_complete": self.scope_complete,
            "assessor_version": self.assessor_version,
            "contract_sha256": self.contract_sha256,
            "dependency_fingerprint": self.dependency_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SetCompletenessAssessment":
        d = _reject_unknown(d, {
            "aspect_id", "rule_version", "source_material_ids", "document_version",
            "source_boundary", "expected_member_ids", "observed_member_ids", "excluded_member_ids",
            "exclusion_reasons", "supporting_material_ids", "supporting_fact_ids", "scope_complete",
            "assessor_version", "contract_sha256", "dependency_fingerprint",
        }, "SetCompletenessAssessment")
        return cls(
            aspect_id=_get_str(d, "aspect_id", "SetCompletenessAssessment"),
            rule_version=_get_str(d, "rule_version", "SetCompletenessAssessment"),
            source_material_ids=_get_str_tuple(d, "source_material_ids", "SetCompletenessAssessment"),
            document_version=_get_str(d, "document_version", "SetCompletenessAssessment"),
            source_boundary=_get_str(d, "source_boundary", "SetCompletenessAssessment"),
            expected_member_ids=_get_str_tuple(d, "expected_member_ids", "SetCompletenessAssessment"),
            observed_member_ids=_get_str_tuple(d, "observed_member_ids", "SetCompletenessAssessment"),
            excluded_member_ids=_get_str_tuple(d, "excluded_member_ids", "SetCompletenessAssessment"),
            exclusion_reasons=_get_str_tuple(d, "exclusion_reasons", "SetCompletenessAssessment"),
            supporting_material_ids=_get_str_tuple(d, "supporting_material_ids", "SetCompletenessAssessment"),
            supporting_fact_ids=_get_str_tuple(d, "supporting_fact_ids", "SetCompletenessAssessment"),
            scope_complete=_get_bool(d, "scope_complete", "SetCompletenessAssessment"),
            assessor_version=_get_str(d, "assessor_version", "SetCompletenessAssessment",
                                      allow_empty=True) or "",
            contract_sha256=_get_str(d, "contract_sha256", "SetCompletenessAssessment"),
            dependency_fingerprint=_get_str(d, "dependency_fingerprint", "SetCompletenessAssessment"),
        )


@dataclass(frozen=True)
class SetCompletenessVerdict:
    """SetCompletenessVerifier 的确定性判定（不信任 Pack 自填 scope_complete）。"""

    set_complete: bool
    verifier_version: str
    reason: str = ""


class SetCompletenessVerifier(Protocol):
    """R1-B 最小 set_complete 可信评估边界（依赖注入；确定性复算集合关系）。

    不得直接信任 SetCompletenessAssessment.scope_complete；由 verifier 从集合关系 +
    依赖指纹确定性复算 set_complete。verifier 缺失 / 版本不符 / 身份不符 / 复算 False →
    fail-closed。
    """

    def verify(self, assessment: "SetCompletenessAssessment",
               dependency_fingerprint: str) -> SetCompletenessVerdict | None:
        """复算 set_complete；无法验证 → None。"""
        ...


def compute_set_completeness_verdict(assessment: "SetCompletenessAssessment",
                                     dependency_fingerprint: str) -> SetCompletenessVerdict:
    """确定性复算 set_complete 集合关系（引用实现；不信任 scope_complete 布尔）。

    校验：成员 ID 唯一非空；observed ∩ excluded 空；expected = observed ∪ excluded；
    每个 excluded 有非空 reason；dependency_fingerprint 与当前 Pack/Requirement 严格一致。
    contract_sha256 / rule_version / assessor_version / 成员绑定由 Store 单独强校验。
    """
    reasons: list[str] = []
    expected = assessment.expected_member_ids
    observed = assessment.observed_member_ids
    excluded = assessment.excluded_member_ids
    excl_reasons = assessment.exclusion_reasons
    all_members = expected + observed + excluded
    if any(not m for m in all_members):
        reasons.append("member id 为空")
    if len(set(expected)) != len(expected):
        reasons.append("expected 含重复 member id")
    if len(set(observed)) != len(observed):
        reasons.append("observed 含重复 member id")
    if len(set(excluded)) != len(excluded):
        reasons.append("excluded 含重复 member id")
    if set(observed) & set(excluded):
        reasons.append("observed ∩ excluded 非空")
    if set(expected) != (set(observed) | set(excluded)):
        reasons.append("expected != observed ∪ excluded")
    if len(excluded) != len(excl_reasons) or any(not r for r in excl_reasons):
        reasons.append("excluded 成员缺非空 reason")
    if assessment.dependency_fingerprint != dependency_fingerprint:
        reasons.append("dependency_fingerprint 与当前 Pack/Requirement 不一致")
    return SetCompletenessVerdict(
        set_complete=(len(reasons) == 0),
        verifier_version=SET_COMPLETENESS_VERIFIER_VERSION,
        reason="; ".join(reasons),
    )


@dataclass(frozen=True)
class SetEnumerationResult:
    """set_complete 的独立枚举结果（Fix 2）：受信任枚举器产出的确定性枚举成员集合。

    不含 Pack 自证布尔。enumerated_member_ids 是枚举器从明确边界 + payload 枚举得到的成员集合；
    payload_hash / boundary_identity 由枚举器对真实 payload 与边界确定性计算，Store 据此与
    assessment 自填 expected/observed/excluded 及实际解析 payload 身份/hash 交叉复核。
    material_type_supported=False 表示该 material 类型无法枚举（Store fail-closed）。

    信任边界：这些字段是「受信任、版本化、确定性的枚举器」的自报结果。Store 只能校验它们
    与真实解析 payload 的身份/哈希/边界/集合关系是否一致，无法证明该枚举器内部确实读取过
    payload bytes。R2 由唯一正式组合入口注入正式枚举器后才建立该信任。
    """

    material_type_supported: bool
    enumerated_member_ids: tuple[str, ...] = ()
    payload_hash: str = ""
    boundary_identity: str = ""
    verifier_version: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.verifier_version:
            raise SchemaValidationError("SetEnumerationResult.verifier_version 必须非空")
        if self.material_type_supported:
            if not _is_sha256_hex(self.payload_hash):
                raise SchemaValidationError(
                    "SetEnumerationResult.payload_hash 必须为 64 位 sha256 hex")
            if not self.boundary_identity:
                raise SchemaValidationError("SetEnumerationResult.boundary_identity 必须非空")
            if not self.enumerated_member_ids:
                raise SchemaValidationError("SetEnumerationResult.enumerated_member_ids 必须非空")
            if any(not m for m in self.enumerated_member_ids):
                raise SchemaValidationError("SetEnumerationResult.enumerated_member_ids 含空成员")

    def to_dict(self) -> dict:
        return {
            "material_type_supported": self.material_type_supported,
            "enumerated_member_ids": list(self.enumerated_member_ids),
            "payload_hash": self.payload_hash,
            "boundary_identity": self.boundary_identity,
            "verifier_version": self.verifier_version,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SetEnumerationResult":
        d = _reject_unknown(d, {
            "material_type_supported", "enumerated_member_ids", "payload_hash",
            "boundary_identity", "verifier_version", "reason"}, "SetEnumerationResult")
        return cls(
            material_type_supported=_get_bool(d, "material_type_supported", "SetEnumerationResult"),
            enumerated_member_ids=_get_str_tuple(d, "enumerated_member_ids", "SetEnumerationResult"),
            payload_hash=_get_str(d, "payload_hash", "SetEnumerationResult", allow_empty=True) or "",
            boundary_identity=_get_str(d, "boundary_identity", "SetEnumerationResult",
                                       allow_empty=True) or "",
            verifier_version=_get_str(d, "verifier_version", "SetEnumerationResult"),
            reason=_get_str(d, "reason", "SetEnumerationResult", allow_empty=True) or "",
        )


class SetEnumerationVerifier(Protocol):
    """R1-B 最小独立枚举边界（Fix 2）：受信任、版本化、确定性的运行时依赖。

    枚举器从 Store 解析出的 resolved_payloads 枚举集合成员，并确定性计算 payload_hash /
    boundary_identity。Store 交叉复核 material/payload 身份、payload hash、document version、
    source boundary、dependency fingerprint、以及 enumerated/expected/observed/excluded 集合关系；
    任何一项不符 → fail-closed。

    信任边界（不得高估）：Store 无法证明一个任意注入的 Python 实现「内部确实读取过 payload
    bytes」；payload 缺失 / bytes 不可用 / 边界不可验证 / 不支持该 material 类型 → 返回
    material_type_supported=False 或 None（Store fail-closed）。

    R2 硬门：R2 必须实现正式、版本化、确定性的文档枚举器，且必须由唯一正式组合入口注入；
    正式枚举器接线之前，生产运行链不得将 set_complete aspect 提升为 covered。R2 后续计划必须
    把枚举器版本纳入 dependency fingerprint（本轮只记录该硬门，不实现 R2）。测试 fake 只证明
    接口与 Store 绑定关系成立，不代表正式文档枚举已实现。
    """

    def enumerate(self, assessment: "SetCompletenessAssessment",
                  materials: tuple["ResearchMaterial", ...],
                  resolved_payloads: tuple["ResolvedPayload", ...],
                  dependency_fingerprint: str) -> SetEnumerationResult | None:
        """枚举成员；无法枚举（payload 缺失/bytes 不可用/不支持类型）→ None 或 supported=False。"""
        ...


def compute_boundary_identity(document_version: str, source_boundary: str) -> str:
    """边界身份指纹（Fix 2）：把 document_version + source_boundary 固化为确定性 sha256。

    枚举器与 Store 各自对同一 (document_version, source_boundary) 计算，须一致；不一致即
    枚举边界与 assessment 自填边界不匹配（fail-closed）。
    """
    return sha256_canonical({
        "document_version": document_version,
        "source_boundary": source_boundary,
    })


def compute_source_payload_hash(resolved_payloads: tuple["ResolvedPayload", ...]) -> str:
    """来源 payload 集合的确定性哈希（枚举器与 Store 各自独立计算，须一致）。

    以 content_hash（已由 verify_material_payload_ref 复核 == sha256(payload_bytes)）为规范形，
    使枚举器与 Store 无需各自重算字节哈希即得同一值；绑定枚举结果到真实 payload 身份。
    枚举结果 payload_hash 与该值不一致 → 判定枚举结果与真实解析 payload 身份不一致（fail-closed）。
    """
    return sha256_canonical([rp.content_hash for rp in resolved_payloads])


def derive_support_eligibility(snap: TopicAspectRequirementSnapshot) -> SupportEligibilityAssessment:
    """从冻结 EvidenceRequirementRef 派生 aspect 的 usage-scope（Fix 1）。

    required = 所有 required_any_of 组 source_classes 的并集；supplemental_only = 各 ref 的
    supplemental_only 并集。无冻结使用资格信息（source_classes/authority 全空）→ 两者皆空
    （Store 此时不触发 usage-scope gate，仅权威门 + coverage + sufficiency）。
    """
    required: set[str] = set()
    supplemental: set[str] = set()
    for er in snap.evidence_requirement_ids:
        if er.authority is not None:
            for grp in er.authority.required_any_of:
                required |= set(grp.source_classes)
            supplemental |= set(er.authority.supplemental_only)
        else:
            required |= set(er.source_classes)
    return SupportEligibilityAssessment(
        aspect_id=snap.aspect_id,
        policy_version=SUPPORT_ELIGIBILITY_POLICY_VERSION,
        required_source_classes=tuple(sorted(required)),
        supplemental_only_source_classes=tuple(sorted(supplemental)),
    )


def recompute_sufficiency(aspect: AspectResearchResult,
                          facts: tuple[SupportedFact, ...],
                          source_policy: FrozenSourcePolicySnapshot | None) -> SufficiencyAssessment | None:
    """确定性复算 sufficiency（Fix 4）。规则来源 = 冻结输入，绝不硬编码 Topic 名单。

    优先级：transmission_layers（四层各判）> key_industry_topics（key_conclusion_ab_c）> 无门。
    复算 threshold_met / independent_c_count / supporting ids 全部来自 facts 的真实 authority
    grade / canonical domain / source class；调用方自填 SufficiencyAssessment 必须与本函数一致。
    """
    snap = aspect.requirement_snapshot
    layers = snap.transmission_layers
    layer = layers[0] if layers else None

    fact_ids = tuple(f.fact_id for f in facts)
    source_ids = tuple(authority_source_identity(f.source_authority) for f in facts)
    fact_types = tuple(f.fact_type for f in facts)
    triples = [(authority_source_class(f.source_authority),
                authority_source_grade(f.source_authority),
                authority_independence_domain(f.source_authority))
               for f in facts]

    def build(rule: str, rule_version: str, threshold_met: bool,
              independent_c_count: int) -> SufficiencyAssessment:
        return SufficiencyAssessment(
            aspect_id=aspect.aspect_id, conclusion_id=None,
            supporting_fact_ids=fact_ids, supporting_source_ids=source_ids,
            rule=rule, rule_version=rule_version, threshold_met=threshold_met,
            independent_c_count=independent_c_count, assessor_version=SUFFICIENCY_ASSESSOR_VERSION,
        )

    if layer in TRANSMISSION_SUFFICIENCY_RULES:
        rule, rule_version = TRANSMISSION_SUFFICIENCY_RULES[layer]
        if layer in ("company_exposure", "actual_company_impact"):
            # 公司暴露 / 实际影响必须由公司披露（非 external）支撑；external 仅 supplemental。
            non_external = any(sc != "external" for sc, _g, _d in triples)
            return build(rule, rule_version, non_external, 0)
        if layer == "conditional_transmission":
            # 需 external / company_industry 的 verified inference fact（fact_type=inference 且带类型化血缘）。
            ok = any(sc in ("external", "company_industry") and g != "D" and ft == "inference"
                     and f.inference_lineage is not None
                     for (sc, g, _d), ft, f in zip(triples, fact_types, facts))
            return build(rule, rule_version, ok, 0)
        # industry_background：external A/B/C（权威门已排除 D）。
        ok = any(g in ("A", "B", "C") for _sc, g, _d in triples)
        return build(rule, rule_version, ok, 0)

    if source_policy is not None and snap.topic_id in source_policy.key_industry_topics:
        ab = any(g in ("A", "B") for _sc, g, _d in triples)
        c_domains = {d for _sc, g, d in triples if g == "C" and d}
        independent_c_count = len(c_domains)
        threshold_met = ab or independent_c_count >= 2
        return build(source_policy.key_conclusion_rule, source_policy.key_conclusion_rule_version,
                     threshold_met, independent_c_count)

    return None


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
    support_eligibility: SupportEligibilityAssessment | None = None
    set_completeness: SetCompletenessAssessment | None = None

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
        if self.support_eligibility is not None and self.support_eligibility.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.support_eligibility.aspect_id={self.support_eligibility.aspect_id!r} "
                f"与 aspect_id={self.aspect_id!r} 不一致")
        if self.set_completeness is not None and self.set_completeness.aspect_id != self.aspect_id:
            raise SchemaValidationError(
                f"AspectResearchResult.set_completeness.aspect_id={self.set_completeness.aspect_id!r} "
                f"与 aspect_id={self.aspect_id!r} 不一致")

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
            "support_eligibility": self.support_eligibility.to_dict()
                if self.support_eligibility is not None else None,
            "set_completeness": self.set_completeness.to_dict()
                if self.set_completeness is not None else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectResearchResult":
        d = _reject_unknown(d, {"aspect_id", "question_ids", "requirement_snapshot", "status",
                                "supported_fact_ids", "material_ids", "attempted_need_ids",
                                "unresolved_ids", "not_found_audit_id", "authority_assessment",
                                "sufficiency_assessment", "support_eligibility",
                                "set_completeness"}, "AspectResearchResult")
        aa = d.get("authority_assessment")
        sa = d.get("sufficiency_assessment")
        se = d.get("support_eligibility")
        sc = d.get("set_completeness")
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
            support_eligibility=SupportEligibilityAssessment.from_dict(se) if se is not None else None,
            set_completeness=SetCompletenessAssessment.from_dict(sc) if sc is not None else None,
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
        """内容身份规范形：除 run_id / pack_id / dependency_fingerprint 外的全部稳定字段。

        含 process/coverage/status_derivation/usage/uncertain_calls/outcome_refs，使同一
        pack_id 只能对应同一份不可变 Pack 内容（改任一字段即改 pack_id，杜绝静默复用）。
        """
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
            "outcome_refs": self.outcome_refs,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "not_found_audits": [n.to_dict() for n in self.not_found_audits],
            "unresolved": [u.to_dict() for u in self.unresolved],
            "external_funnel": self.external_funnel.to_dict() if self.external_funnel else None,
            "usage": self.usage.to_dict(),
            "uncertain_calls": [u.to_dict() for u in self.uncertain_calls],
            "process_status": self.process_status.to_dict(),
            "coverage_status": self.coverage_status.to_dict(),
            "status_derivation": self.status_derivation.to_dict(),
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


def verify_pack_payloads(pack: TopicResearchPack, resolver: PayloadResolver) -> None:
    """校验 Pack 内全部 material 的 payload_ref 可解析（dangling/类型/版本/locator/hash → fail-closed）。

    由 commit/finalize 边界注入 resolver 调用；R1-B 不直接依赖 Evidence/Financial/External
    Store，离线测试使用 fake resolver。
    """
    for m in pack.materials:
        verify_material_payload_ref(m.payload_ref, resolver)


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
