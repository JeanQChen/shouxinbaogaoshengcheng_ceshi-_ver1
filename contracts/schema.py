"""Section Contract 数据模型。

本模块只定义声明式配置语义，不包含任何可执行 Python 表达式或业务计算。
条件（Condition）与缺失策略（MissingPolicy）均为受限白名单结构，供后续
Planner / Retriever / Worker / Evaluator / Assurance 共同引用同一份章节语义。

重要：契约必须通用适用于所有 A 股上市公司，不得围绕某家公司、某个行业或
某项业务写死。41 问基线仅是第一份真实测试集，用于映射通用 question_id，
不反向决定本 Schema 或阻断规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

# ---------------------------------------------------------------------------
# 白名单枚举
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "v1"

# 第一阶段章节顺序（固定，不含项目分析；项目分析属于产品第二阶段）
SECTION_ORDER = ["company", "financial", "industry", "synthesizer"]

# 授信类型（显示名称与内部代码分离，见 DESIGN_V2.md §5.1）
CREDIT_TYPES = [
    "working_capital",   # 流动资金贷款
    "trade_finance",     # 贸易融资
    "fixed_asset",       # 固定资产贷款
    "project_loan",      # 项目贷款
    "other",             # 其他
]

# 优先级
PRIORITIES = ["P0", "P1", "P2"]

# 研究策略：workflow=确定性流程；harness=受限研究循环；conditional_harness=条件触发的受限循环
RESEARCH_POLICIES = ["workflow", "harness", "conditional_harness"]

# 问题证据状态（“为何尚未满足”）。注意：无 BLOCKED 状态，阻断后果由
# BLOCKING_LEVELS 单独表达，二者正交（状态说明“缺什么”，阻断等级说明“后果多大”）。
QUESTION_STATES = [
    "SATISFIED",              # 已取得满足要求的证据/计算并形成结果
    "NOT_APPLICABLE",         # 经条件判断不适用（如无股权激励计划）
    "NOT_PROVIDED",           # 所需材料未上传或字段未提供
    "NOT_FOUND_AFTER_SEARCH", # 已执行检索但未找到支持结论（≠事实不存在）
    "CONFLICT",               # 多来源/口径冲突尚未解决
    "WAITING_HUMAN",          # 必须客户经理确认后才能继续受影响部分
]

# 阻断等级（“未完成的后果”）。NOT_FOUND_AFTER_SEARCH / NOT_PROVIDED 均不等于
# 事实不存在；非核心信息缺失通常允许带缺口预览。
BLOCKING_LEVELS = [
    "NONE",            # 不阻断，允许带缺口预览
    "SECTION_BLOCKED", # 该章节无法形成有效结论
    "REPORT_BLOCKED",  # 允许生成带问题预览，但禁止正式导出
    "JOB_BLOCKED",     # 主体等基础前提错误，整个任务暂停
]

# 条件白名单操作符（声明式，禁止任意表达式）
CONDITION_KINDS = ["always", "credit_type_in", "credit_type_not_in",
                   "all_of", "any_of", "none_of"]
CONDITION_OPS = ["in", "not_in"]
CONDITION_FIELDS = ["credit_type"]

# 证据类型
EVIDENCE_KINDS = ["field", "table", "table_row", "paragraph", "web",
                  "structured_db", "calculation", "claim"]

# 来源类别
SOURCE_CLASSES = ["company_industry", "financial", "project", "external",
                  "structured_db"]

# 能力白名单（Agent 可见工具，见 DESIGN_V2.md §8.1）
ALLOWED_CAPABILITIES = [
    "search_evidence",
    "search_tables",
    "inspect_evidence",
    "compare_evidence",
    "verify_claim",
    "search_external_sources",
    "lookup_financial_metric",
    "lookup_company_field",
]

# 41 问映射的角色
COVERAGE_ROLES = ["full", "partial", "supporting", "out_of_scope"]

# 影响范围（SC-04：上游问题影响什么，决定综合章节是否阻止受影响结论）
# 空列表 = 非核心缺口，仅带缺口预览，不阻断受影响结论。
IMPACT_SCOPES = ["subject", "solvency", "key_financial", "credit_scheme"]

# 行业证据来源分级（SC-03：A/B/C/D，声明式；不实现自动评级）
# A=监管/政府/交易所，B=行业协会/研究机构/公司公告，C=券商/财经媒体/头部披露，
# D=来源不明/聚合转载（不得作为关键结论唯一依据）。
SOURCE_GRADES = ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# 声明式条件
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """受限声明式条件。

    仅支持白名单 kind / op / field。叶子条件 kind 为 always / credit_type_in /
    credit_type_not_in；组合条件为 all_of / any_of / none_of（children 递归）。
    """

    kind: str
    field: str | None = None
    op: str | None = None
    value: list[str] | None = None
    children: list["Condition"] = dc_field(default_factory=list)

    def matches(self, credit_type: str) -> bool:
        """按授信类型求值条件。未知 kind 一律返回 False（校验器会先行拦截）。"""
        if self.kind == "always":
            return True
        if self.kind == "credit_type_in":
            return bool(self.value) and credit_type in (self.value or [])
        if self.kind == "credit_type_not_in":
            return bool(self.value) and credit_type not in (self.value or [])
        if self.kind == "all_of":
            return all(c.matches(credit_type) for c in self.children)
        if self.kind == "any_of":
            return any(c.matches(credit_type) for c in self.children)
        if self.kind == "none_of":
            return not any(c.matches(credit_type) for c in self.children)
        return False


# ---------------------------------------------------------------------------
# 契约组件
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRequirement:
    requirement_id: str
    evidence_kind: str
    source_classes: list[str]
    minimum_sources: int
    freshness_policy: str | None = None
    required_fields: list[str] = dc_field(default_factory=list)


@dataclass
class KeyQuestion:
    question_id: str
    question: str
    priority: str
    evidence_requirements: list[EvidenceRequirement]
    calculation_requirements: list[str] = dc_field(default_factory=list)
    analysis_requirements: list[str] = dc_field(default_factory=list)
    missing_policy: str = "write_not_found"   # 引用命名缺失策略 policy_id
    blocking_policy: list[str] = dc_field(default_factory=list)  # 阻断后果集合（可复合），空=NONE
    impact_scope: list[str] = dc_field(default_factory=list)     # SC-04 影响范围（空=非核心）
    # 该问题必须覆盖的各个可校验方面（通用声明，不写死公司/行业）。
    # harness 据此逐方面核对 claim 覆盖；空 = 回退到 evidence_requirements.required_fields
    # 或问题原文（见 harness.aspects 三级派生）。
    required_aspects: list[str] = dc_field(default_factory=list)


@dataclass
class TopicContract:
    topic_id: str
    title: str
    required: bool
    key_questions: list[KeyQuestion]
    applies_when: Condition | None = None


@dataclass
class OutputRequirement:
    requirement_id: str
    kind: str
    description: str


@dataclass
class CompletionRule:
    rule_id: str
    scope_id: str
    condition: Condition
    outcome: str


@dataclass
class EvaluationRule:
    rule_id: str
    description: str


@dataclass
class MissingPolicy:
    """命名缺失策略：规定“缺失/未检索到/冲突”时如何书写与处理。"""

    policy_id: str
    description: str


@dataclass
class SectionContract:
    contract_version: str
    section_id: str
    title: str
    purpose: str
    required_topics: list[TopicContract]
    output_requirements: list[OutputRequirement] = dc_field(default_factory=list)
    completion_rules: list[CompletionRule] = dc_field(default_factory=list)
    evaluation_rules: list[EvaluationRule] = dc_field(default_factory=list)
    allowed_capabilities: list[str] = dc_field(default_factory=list)
    research_policy: str = "harness"
    # 缺失策略目录（loader 解析 policies 后附加到每个 section，便于校验与复核表渲染）
    missing_policies: list[MissingPolicy] = dc_field(default_factory=list)

    def all_questions(self) -> list[KeyQuestion]:
        return [q for t in self.required_topics for q in t.key_questions]

    def question_ids(self) -> list[str]:
        return [q.question_id for q in self.all_questions()]


# ---------------------------------------------------------------------------
# 校验 / 解析 / 复核结果
# ---------------------------------------------------------------------------

@dataclass
class ContractValidationResult:
    valid: bool
    errors: list[str] = dc_field(default_factory=list)


@dataclass
class ResolvedTopic:
    topic_id: str
    applies: bool
    questions: list[KeyQuestion]


@dataclass
class ResolvedSectionContract:
    section_id: str
    credit_type: str
    enabled: bool
    topics: list[ResolvedTopic]


@dataclass
class BaselineContractMapping:
    case_id: str
    question_ids: list[str]
    coverage_role: str   # full | partial | supporting | out_of_scope
    note: str


@dataclass
class ContractReviewMatrix:
    full: list[str]           # 完整覆盖的 question_id
    partial: list[str]        # 部分覆盖的 question_id
    uncovered: list[str]      # 尚无 baseline case 的 question_id
    out_of_scope: list[str]   # 无法映射的 case_id
    rows: list[dict[str, Any]]  # 复核表行
