"""Contract v2 数据模型（R1-A 候选资产，仅声明式 schema，不接正式 runtime）。

本模块定义 Contract v2 的声明式结构：52 问逐 question、逐 aspect 的稳定字段集合、
枚举白名单与内容指纹工具。它与 v1（``contracts/schema.py``）完全隔离：

- v1 保持原解码与正式运行行为（``CONTRACT_VERSION = "v1"``，``required_aspects`` 为
  ``list[str]`` 自然语言列表）。
- v2 使用稳定 ``aspect_id`` 与可组合 ``coverage_rules``；每 aspect 至少 22 字段
  （本模块 REQUIRED_ASPECT_FIELDS 共 22 字段，覆盖 R1-A 授权书 §五/§六 与 12 段裁决 §二）。

本模块不 import 任何正式 service / runtime / Worker / Writer，不执行 I/O，不调用 LLM，
不写数据库。它只被 ``contracts/loader_v2.py`` / ``validator_v2.py`` 与离线测试引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

CONTRACT_VERSION_V2 = "v2"
# 唯一的 v2 载体（不得覆盖 standard_v2.yaml 的 v1 历史语义）。
CONTRACT_V2_ASSET = "templates/contracts/standard_v3.yaml"

SECTION_ORDER = ["company", "financial", "industry", "synthesizer"]

# 四个章节的 H2 数量（8/5/9，公司/财务/行业；synthesizer 为 Phase 5 承载，不在本报告 H2）。
H2_TOC_COUNTS = {"company": 8, "financial": 5, "industry": 9, "synthesizer": 0}

# ---------------------------------------------------------------------------
# 执行归属（52 问四类，28/13/3/8 —— 权威，见 R1-A 授权书 §四）
# ---------------------------------------------------------------------------

PRODUCER_KINDS = (
    "topic_harness",            # 28 问：公司 20 + 行业 8
    "financial_workflow",       # 13 问：财务（除 fin_risk_summary）
    "phase4_section_derived",   # 3 问：company_credit_summary / fin_risk_summary / industry_monitoring
    "phase5_synthesizer",       # 8 问：全部 synth_*
)

# 三道章节派生题（不进入 Topic Harness，也不推迟到 Phase 5）。
DERIVED_QUESTION_IDS = (
    "company_credit_summary",
    "fin_risk_summary",
    "industry_monitoring",
)

# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# aspect kind：描述该 aspect 的研究/产出形态。
ASPECT_KINDS = (
    "fact_set",            # 一组离散事实/字段集合
    "all_disclosed_items", # 权威披露集合的完整枚举（有集合锚点）
    "single_judgment",     # 单一定性判断（如核心风险、综合评价）
    "event_set",           # 负面/事件类（诉讼/处罚/违约/失信/质押/舆情/收并购等）
    "financial_metric",    # 财务指标 aspect
    "derived_summary",     # 章节派生总结
    "synthesizer",         # Phase 5 综合
    "search_audit",        # 检索范围与截止日期记录
)

# 可组合覆盖规则（不再有单一 coverage_mode）。covered = 所有适用规则同时满足。
# 按 producer_kind 分四组；每组规则只适用于对应 producer（见 PRODUCER_COVERAGE_RULES）。
COVERAGE_RULES = (
    # topic_harness
    "set_complete",            # 权威集合全量覆盖（配合 complete_set_rule 锚点）
    "required_fields_complete",# required_fields 全部取得
    "minimum_sources",         # 来源数量门槛（来自 evidence requirement）
    "direct_support",          # 每条事实均有直接引用（fact → citation → material/snapshot）
    "search_audit",            # 检索范围与截止日期已记录（仅负面核验/not_found/集合完整性/外部时效）
    "applicability",           # applicability_policy 已评估（not_applicable 是终态分支）
    # financial_workflow
    "snapshot_authority",      # 数字权威来自已校验 FinancialSnapshot（不作自证）
    "item_formula_identity",   # 指标/科目身份与 formula_id 一致
    "period",                  # 期间明确（年度/时点/季度，不混算）
    "scope",                   # 合并/母公司口径明确
    "currency_unit",           # 币种与单位明确
    "required_input_completeness",  # 计算所需输入字段完整
    "calculation_provenance",  # 计算链路可溯源（Python/SQL，LLM 不计算）
    "comparability",           # 期间可比性按公式 period_requirement 判定
    # phase4_section_derived
    "upstream_aspect_coverage",# 上游 required aspect 覆盖率
    "pack_claim_identity",     # 输入 Pack/Claim 身份一致
    "dependency_lineage",      # 依赖血缘可溯源
    "conflict_propagation",    # 上游冲突/未解决正确传播
    # phase5_synthesizer
    "upstream_dependency_coverage",  # 上游依赖覆盖
    "synthesis_lineage",       # 综合血缘可溯源
    "conflict_limitation_propagation",  # 冲突/限制传播
)

# 每类 producer 允许的 coverage rules（validator 据此做兼容性检查）。
# 关键不变式：financial/derived/synth 不得机械要求 search_audit。
PRODUCER_COVERAGE_RULES = {
    "topic_harness": (
        "set_complete", "required_fields_complete", "minimum_sources",
        "direct_support", "search_audit", "applicability",
    ),
    "financial_workflow": (
        "required_fields_complete", "minimum_sources", "direct_support",
        "snapshot_authority", "item_formula_identity", "period", "scope",
        "currency_unit", "required_input_completeness", "calculation_provenance",
        "comparability",
    ),
    "phase4_section_derived": (
        "upstream_aspect_coverage", "pack_claim_identity", "dependency_lineage",
        "conflict_propagation",
    ),
    "phase5_synthesizer": (
        "upstream_dependency_coverage", "synthesis_lineage",
        "conflict_limitation_propagation", "applicability",
    ),
}

# 五类 canonical 时间政策（权威，见 R1-A 授权书 §八）。
TIME_POLICIES = (
    "CURRENT_AS_OF_WITH_24M_CHANGES",
    "RECENT_24M_WITH_OPEN_TAIL",
    "SINCE_INCEPTION_MILESTONES",
    "THREE_YEARS_PLUS_LATEST",
    "STRUCTURAL_5Y_CURRENT",
)

BLOCKING_LEVELS = ("NONE", "SECTION_BLOCKED", "REPORT_BLOCKED", "JOB_BLOCKED")

IMPACT_SCOPES = ("subject", "solvency", "key_financial", "credit_scheme")

# 展示层级（进入正文的档位）与内容角色（如何呈现）——两个独立维度（R1-A §六）。
# 不再使用「正文段落+表格」「诊断槽位」等双用途字符串。
DISPLAY_TIERS = (
    "required_body",       # 必入正文（阻断/核心内容，缺失须显式缺口）
    "optional_body",       # 可选正文（有证据则写，否则写 not_found，不阻断）
    "diagnostic_only",     # 仅诊断（附录/脚注，不进入正文判断）
)

CONTENT_ROLES = (
    "paragraph",           # 正文段落
    "table",               # 表格
    "paragraph_and_table", # 正文段落 + 表格
    "risk_note",           # 风险提示（负面/事件类正文表达）
    "search_scope_note",   # 检索范围说明
    "audit_only",          # 仅审计附录
)

# 承载落点（粗粒度路由：普通正文 H2 / 章节派生总结 H2 / Phase 5）。
OUTPUT_DESTINATIONS = (
    "body",                # 本章既定 H2 正文
    "derived_summary",     # 章节派生总结 H2（co-h8 / fin-h5 / ind-h9）
    "phase5_synthesizer",  # Phase 5 综合承载
)

BUSINESS_REVIEW_STATUSES = ("CONFIRMED", "BUSINESS_REVIEW_REQUIRED")

# 候选资产生命周期状态（R1-A §九）：candidate → approved → frozen；
# candidate 阶段 approved_at / frozen_at 必须为 null。
LIFECYCLE_STATUSES = ("candidate", "approved", "frozen")

# v2 在 v1 基础上补齐 fetch_external_content（v1 ALLOWED_CAPABILITIES 缺此项）。
ALLOWED_CAPABILITIES_V2 = (
    "search_evidence",
    "search_tables",
    "inspect_evidence",
    "compare_evidence",
    "verify_claim",
    "search_external_sources",
    "fetch_external_content",     # v2 新增（v1 缺）
    "lookup_financial_metric",
    "lookup_company_field",
)

# 来源等级（与 sections/industry_source_policy.py P3-B02 一致）。
SOURCE_GRADES = ("A", "B", "C", "D")

# 行业风险传导四层语义（静态 TransmissionRequirement 只存这四层标签与通道）。
TRANSMISSION_LAYERS = (
    "industry_background",
    "conditional_transmission",
    "company_exposure",
    "actual_company_impact",
)

# 行业风险传导的四条独立通道（R1-A §四：需求/收入、原材料/成本、产能/资本开支、现金流/偿债）。
TRANSMISSION_CHANNELS = (
    "demand_revenue",        # 需求/收入
    "raw_material_cost",     # 原材料/成本
    "capacity_capex",        # 产能/资本开支
    "cashflow_solvency",     # 现金流/偿债
)

# ---------------------------------------------------------------------------
# 字段约束：每个 aspect 至少含这些字段（22 项，覆盖 R1-A §五/§六）。
# display_semantics 已拆为 display_tier + content_role 两个独立维度。
# ---------------------------------------------------------------------------

REQUIRED_ASPECT_FIELDS = (
    "aspect_id",
    "question_id",
    "topic_id",
    "requirement_text",
    "kind",
    "producer_kind",
    "execution_path",
    "required_fields",
    "coverage_rules",
    "complete_set_rule",
    "evidence_requirement_ids",
    "source_policy_ref",
    "time_scope",
    "display_tier",
    "content_role",
    "missing_policy",
    "blocking_policy",
    "applicability_policy",
    "impact_scope",
    "output_destination",
    "derived_from",
    "business_review_status",
)

# 负面/事件类 aspect 的核心字段与扩展字段（R1-A §七）。
EVENT_CORE_FIELDS = ("事件类别", "日期", "涉及主体", "当前状态", "来源和检索范围")
EVENT_EXTENSION_FIELDS = ("金额或规模", "进展", "处理结果", "对经营现金流信用风险的影响")

# 负面事件两类正文表达（R1-A §三：正文只允许这两种，禁止断言「该事项不存在」）。
NEGATIVE_BODY_ALLOWED_FORMS = (
    "已检索到以下事项",
    "在明确列示的检索范围内未发现相关事项",
)
NEGATIVE_BODY_FORBIDDEN_FORMS = (
    "该事项不存在",
    "无诉讼",
    "无处罚",
    "无失信",
)

# derived_from_scope 的合法键（R1-A §十.4：typed include/exclude 语义，替代 ``company:*`` 通配）。
DERIVED_FROM_SCOPE_KEYS = (
    "include_sections",        # 上游章节（必填，非空）
    "exclude_producer_kinds",  # 排除的 producer（如 synth 派生排除 phase5_synthesizer）
    "exclude_display_tiers",   # 排除的展示层级（默认排除 diagnostic_only）
    "exclude_aspect_ids",      # 显式排除的 aspect_id
    "exclude_terminal_states", # 排除不合格运行时终态（NOT_APPLICABLE/UNRESOLVED/BLOCKED/UNSUPPORTED）
)

# 派生输入的资格门槛（R1-A §八）：这些运行时终态不得作为 supported 输入进入派生 aspect。
DERIVED_INPUT_INELIGIBLE_STATES = (
    "NOT_APPLICABLE",
    "UNRESOLVED",
    "BLOCKED",
    "UNSUPPORTED",
)

# ---------------------------------------------------------------------------
# 派生输入资格政策（R1-A §四，typed 版本化）
# ---------------------------------------------------------------------------
# 把「哪些输入可进入 derived / synth」定义为一份显式、逐字段可校验的政策，替代
# 「非空列表 + 白名单」式的弱约束。validator 强制该政策完整：删任一必需字段即失败。
#
# 运行时状态值镜像 harness/schema.py（本模块不 import 运行时，只存常量）：
#   QUESTION_STATUSES（9）/ COMPLETION_STATUSES（5）/ ENTAILMENT_VERDICTS（3）。
# 分五个正交门：问题态、Pack/Section 完成态、Claim 支持判定、不合格事实终态、负向观测例外，
# 再加展示层排除与两把门控开关（合格引用 / 无未解决冲突）。
DERIVED_ELIGIBILITY_POLICY_ID = "derived-input-eligibility-v1"

DERIVED_ELIGIBILITY_POLICY = {
    "policy_id": DERIVED_ELIGIBILITY_POLICY_ID,
    "schema_version": "derived-input-eligibility-v1",
    # 问题研究状态（QUESTION_STATUSES）：COMPLETED / COMPLETED_WITH_GAPS 可进入派生。
    "question_states_eligible": ["COMPLETED", "COMPLETED_WITH_GAPS"],
    "question_states_ineligible": [
        "PENDING", "ROUTED", "RESEARCHING", "ANSWER_READY",
        "WAITING_HUMAN", "BLOCKED", "FAILED",
    ],
    # Pack/Section 完成状态（COMPLETION_STATUSES）：UNRESOLVED/BLOCKED 等不算完整输入。
    "pack_section_states_eligible": ["COMPLETED", "COMPLETED_WITH_GAPS"],
    "pack_section_states_ineligible": ["UNRESOLVED", "NOT_IMPLEMENTED", "FAILED"],
    # Claim 支持判定（ENTAILMENT_VERDICTS）：UNSUPPORTED 不进派生；PARTIAL 非完整支持，保守判不可进。
    "claim_verdicts_eligible": ["SUPPORTED"],
    "claim_verdicts_ineligible": ["PARTIAL", "UNSUPPORTED"],
    # 不得作为 supported 事实进入派生的终态（NOT_PROVIDED/NOT_APPLICABLE/CONFLICT/WAITING_HUMAN）。
    "unsupported_fact_states": ["NOT_PROVIDED", "NOT_APPLICABLE", "CONFLICT", "WAITING_HUMAN"],
    # 负向观测例外：NOT_FOUND_AFTER_SEARCH 仅当 SearchAudit 合格时算合格负向观测。
    "negative_observation_state": "NOT_FOUND_AFTER_SEARCH",
    "negative_observation_condition": "search_audit_qualified",
    # 展示层排除：diagnostic_only 不作正文派生输入。
    "excludes_display_tiers": ["diagnostic_only"],
    # 门控开关：必须有合格引用；必须无未解决冲突。
    "requires_qualified_citation": True,
    "requires_no_unresolved_conflict": True,
}
DERIVED_ELIGIBILITY_POLICY_KEYS = tuple(DERIVED_ELIGIBILITY_POLICY.keys())

# ---------------------------------------------------------------------------
# 传导 Evidence 权威约束（R1-A §二）
# ---------------------------------------------------------------------------
# 四层传导的 Evidence 需求必须用 authority 结构表达 required_any_of / supplemental_only /
# inference_lineage，把「权威身份」变成可机器判定的结构，并保持三类权威身份分离：
#   Evidence-backed fact = company_industry；FinancialSnapshot = structured_db / financial；
#   ExternalSnapshot = external（仅行业背景可用；公司暴露/实际影响只能是补充性来源）。
EVIDENCE_AUTHORITY_KEYS = ("required_any_of", "supplemental_only", "inference_lineage")

# 条件性传导（conditional_transmission）的 inference 血缘字段（缺任一字段即失败）。
CONDITIONAL_INFERENCE_LINEAGE_FIELDS = (
    "inference_policy_ref",
    "channel",
    "direction",
    "conditions",
    "limitation",
    "derived_from_fact_ids",
)

# 四层 → 传导证据需求 id（validator 据此逐层校验权威结构）。
TRANSMISSION_EVIDENCE_BY_LAYER = {
    "industry_background": "er_ind_transmission_background",
    "conditional_transmission": "er_ind_transmission_conditional",
    "company_exposure": "er_ind_transmission_exposure",
    "actual_company_impact": "er_ind_transmission_impact",
}

# ---------------------------------------------------------------------------
# 内容指纹
# ---------------------------------------------------------------------------

import hashlib
import json


def sha256_text(text: str) -> str:
    """对文本内容求 sha256（UTF-8）。用于资产指纹与 drift 校验。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    """对可 JSON 化的对象求稳定 sha256（sort_keys，ensure_ascii=False）。"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def lifecycle_errors(raw: dict[str, Any]) -> list[str]:
    """候选资产生命周期校验（R1-A §九，三态 fail-closed）。

    candidate → approved → frozen 单调推进，各态字段约束：
    - candidate：created_at 非空，approved_at / frozen_at 均为 null；
    - approved：created_at + approved_at 非空，frozen_at 为 null；
    - frozen：approved_at + frozen_at 均非空（frozen 蕴含 approved）。
    矛盾组合（approved 缺 approved_at、frozen 缺 approved_at/frozen_at、candidate 带审批/冻结时间）
    一律报错。各资产 validator 复用本函数，避免四处重复判断。
    """
    errs: list[str] = []
    status = raw.get("status")
    if status is None:
        errs.append("缺少 status（候选资产应为 candidate）")
        return errs
    if status not in LIFECYCLE_STATUSES:
        errs.append(f"未知 status {status!r}")
        return errs

    created_at = raw.get("created_at")
    approved_at = raw.get("approved_at")
    frozen_at = raw.get("frozen_at")

    if created_at in (None, ""):
        errs.append("缺少 created_at")
    if status == "candidate":
        if approved_at is not None:
            errs.append("candidate 状态 approved_at 必须为 null")
        if frozen_at is not None:
            errs.append("candidate 状态 frozen_at 必须为 null")
    elif status == "approved":
        if approved_at in (None, ""):
            errs.append("approved 状态必须提供 approved_at")
        if frozen_at is not None:
            errs.append("approved 状态 frozen_at 必须为 null")
    elif status == "frozen":
        if approved_at in (None, ""):
            errs.append("frozen 状态必须提供 approved_at（frozen 蕴含 approved）")
        if frozen_at in (None, ""):
            errs.append("frozen 状态必须提供 frozen_at")
    return errs


# 冻结阶段排除在内容指纹之外的键（R1-A 冻结收口 §二）：生命周期元数据 + 指纹本身。
# 冻结动作只改这五个键，业务内容不变，因此冻结前后的 content_fingerprint 必须一致；
# 任何业务内容漂移都会改变指纹，从而被 frozen_asset_errors 捕获。
_FREEZE_EXCLUDE_KEYS = ("status", "created_at", "approved_at", "frozen_at",
                        "content_sha256")


def content_fingerprint(doc: dict[str, Any]) -> str:
    """冻结资产内容指纹（R1-A 冻结收口 §二）。

    对资产文档中除生命周期元数据与指纹字段外的全部业务内容求稳定 sha256
    （sort_keys，ensure_ascii=False）。冻结只改被排除的五个键，不改变业务内容，
    故冻结前（candidate）与冻结后（frozen）的内容指纹一致；业务内容漂移则不一致。
    """
    payload = {k: v for k, v in doc.items() if k not in _FREEZE_EXCLUDE_KEYS}
    return sha256_json(payload)


def frozen_asset_errors(doc: dict[str, Any]) -> list[str]:
    """冻结资产生命周期 + 内容指纹 fail-closed（R1-A §九 / 冻结收口 §二）。

    在 lifecycle_errors（三态 fail-closed）基础上，当 status == "frozen" 时额外校验：
    - 必须提供 content_sha256；
    - content_sha256 必须与当前业务内容指纹一致（防冻结后内容漂移）。
    非 frozen 状态（candidate/approved）仍走原 lifecycle_errors 语义，不强制指纹。
    """
    errs = lifecycle_errors(doc)
    if doc.get("status") == "frozen":
        declared = doc.get("content_sha256")
        if declared in (None, ""):
            errs.append("frozen 状态必须提供 content_sha256")
        else:
            computed = content_fingerprint(doc)
            if declared != computed:
                errs.append(f"content_sha256 与当前内容指纹不一致（声明 {declared!r} "
                            f"≠ 计算 {computed!r}）")
    return errs


# ---------------------------------------------------------------------------
# 声明式组件（只承载解析后的结构，无业务计算）
# ---------------------------------------------------------------------------

@dataclass
class AspectV2:
    """Contract v2 的一个稳定 aspect（稳定 aspect_id，逐字段可校验）。"""

    aspect_id: str
    question_id: str
    topic_id: str
    requirement_text: str
    kind: str
    producer_kind: str
    execution_path: str
    required_fields: list[str]
    coverage_rules: list[str]
    complete_set_rule: str
    evidence_requirement_ids: list[str]
    source_policy_ref: str
    time_scope: str
    display_tier: str
    content_role: str
    missing_policy: str
    blocking_policy: list[str]
    applicability_policy: str | None
    impact_scope: list[str]
    output_destination: str
    derived_from: list[str]
    business_review_status: str
    business_review_reason: str = ""
    derived_from_scope: dict[str, Any] | None = None
    transmission_layers: list[str] = field(default_factory=list)
    transmission_channel: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuestionV2:
    question_id: str
    topic_id: str
    question: str
    priority: str
    producer_kind: str
    execution_path: str
    blocking_policy: list[str]
    missing_policy: str
    aspects: list[AspectV2]


@dataclass
class TopicV2:
    topic_id: str
    title: str
    producer_kind: str
    questions: list[QuestionV2]


@dataclass
class SectionV2:
    section_id: str
    title: str
    purpose: str
    research_policy: str
    allowed_capabilities: list[str]
    topics: list[TopicV2]

    def all_questions(self) -> list[QuestionV2]:
        return [q for t in self.topics for q in t.questions]

    def all_aspects(self) -> list[AspectV2]:
        return [a for t in self.topics for q in t.questions for a in q.aspects]


@dataclass
class ContractV2:
    contract_version: str
    sections: list[SectionV2]
    time_policies: dict[str, dict[str, str]]
    missing_policies: dict[str, str]
    coverage_rules_registry: dict[str, str]
    allowed_capabilities: list[str]
    source_policy_ref: str
    status: str = "candidate"
    created_at: str = ""
    approved_at: str | None = None
    frozen_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def all_questions(self) -> list[QuestionV2]:
        return [q for s in self.sections for q in s.all_questions()]

    def all_aspects(self) -> list[AspectV2]:
        return [a for s in self.sections for a in s.all_aspects()]


@dataclass
class ContractV2ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
