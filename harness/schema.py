"""Phase 3 Batch B 受限研究循环公共契约（dataclass + 枚举白名单）。

本模块只定义声明式数据结构与枚举常量，不含 I/O、不含业务计算、不调用 LLM / Router /
Registry。harness 各层（actions / policies / state / checkpoint / trace / runtime）与
41 问 Actual-Path Runner 共同引用同一份字段语义。

状态模型（三层，见编码前计划 §10）：
- RunState —— 一次 batch run 的生命周期（本批次最小化，见 checkpoint.py）；
- ResearchState —— 单个 KeyQuestion 的研究状态（用户 §五 9 态，权威）；
- NeedState —— 补检产生的有界子 need（Rules 内部生成，LLM 不直接发）。

成功/缺口口径（编码前计划 §13）：
- COMPLETED = 简短答案 + 可回查引用 + 无影响核心答案的 unresolved + 路径已真实实现；
- COMPLETED_WITH_GAPS = 有可引用部分答案但含 unresolved（不计严格成功，单独统计）；
- Gold document/page 只用于 Runner 完成后的离线诊断，绝不进入本模块任何运行时字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from routing import schema as RS
from tools import contracts as TC

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

HARNESS_VERSION = "v1"

# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# 问题级研究状态（用户 §五 9 态，权威）。
QUESTION_STATUSES = (
    "PENDING",               # 已创建，尚未路由
    "ROUTED",                # Router 已 DECIDED，等待首个动作
    "RESEARCHING",           # 正在执行工具/补检
    "ANSWER_READY",          # LLM 已产出答案，等待充分性检查
    "COMPLETED",             # 充分性通过（严格成功）
    "COMPLETED_WITH_GAPS",   # 有可引用答案但含 unresolved（不计严格成功）
    "WAITING_HUMAN",         # REQUEST_HUMAN，需客户经理确认
    "BLOCKED",               # 路由未实现 / 预算耗尽且无可引用答案
    "FAILED",                # 技术异常 / 模型输出非法
)

# 一次 batch run 的生命周期（任务书 §7.3 run 级子集）。
RUN_STATUSES = ("CREATED", "RUNNING", "PAUSED", "COMPLETED", "FAILED")

# 子 need 状态（任务书 §7.3 need 级）。
NEED_STATUSES = (
    "PENDING",
    "IN_PROGRESS",
    "RESOLVED",
    "NOT_FOUND_AFTER_SEARCH",
    "UNRESOLVED_BUDGET",
    "WAITING_USER",
    "FAILED",
)

# 停止原因（任务书 §7.6 + 编码前计划 §10 补充）。
STOP_REASONS = (
    "COMPLETED",                     # 充分答案 + 引用 + 无核心 unresolved
    "COMPLETED_WITH_GAPS",           # 有可引用答案但含 unresolved（单独统计）
    "NOT_FOUND_AFTER_SEARCH",        # 已检索但未找到支持结论（≠ 事实不存在）
    "NO_MORE_HIGH_VALUE_ACTION",     # 无更高价值动作可做
    "BUDGET_ITERATIONS",             # 回合耗尽
    "BUDGET_TOOL_CALLS",             # 工具调用次数耗尽
    "BUDGET_TOKENS",                 # 已知 token 预算耗尽
    "BUDGET_ELAPSED",                # 耗时预算耗尽
    "BUDGET_EXTERNAL",               # 外部调用预算耗尽
    "BUDGET_EXHAUSTED",              # 预算耗尽且无可引用答案 → BLOCKED
    "CONSECUTIVE_NO_NEW_EVIDENCE",   # 连续 N 轮无新证据
    "PATH_NOT_IMPLEMENTED",          # 路由未实现 → BLOCKED
    "ACTION_SCHEMA_INVALID",         # 动作输出非法（超 1 次修复）→ FAILED
    "MODEL_OUTPUT_INVALID",          # 答案输出非法 → FAILED
    "FATAL_TOOL_ERROR",              # 工具致命错误
    "WAITING_USER",                  # REQUEST_HUMAN
    "VERSION_INCOMPATIBLE",          # 输入版本不兼容
    "SESSION_POISONED",              # 会话被污染
)

# 完成状态（answer 的终态口径，区别于问题级 status）。
COMPLETION_STATUSES = (
    "COMPLETED",
    "COMPLETED_WITH_GAPS",
    "UNRESOLVED",
    "NOT_IMPLEMENTED",
    "FAILED",
)

# 答案 claim 类别（事实 vs 研判）。
CLAIM_KINDS = ("fact", "inference")

# 引用类别（本地证据 / 结构化 DB 结果 / 外部快照）。
CITATION_TYPES = ("evidence", "structured", "external")

# 答案置信度。
CONFIDENCE_LEVELS = ("high", "low")

# 研究错误类别（与 stop_reason 正交：stop_reason 描述「为何停止」，
# error_code 描述「哪里失败」；两者共同区分用户 §五 9 种失败类型）。
RESEARCH_ERROR_CODES = (
    "PATH_NOT_IMPLEMENTED",
    "ACTION_SCHEMA_INVALID",
    "ANSWER_SCHEMA_INVALID",
    "MODEL_OUTPUT_INVALID",
    "ROUTER_FAILED",
    "TOOL_FATAL",
    "SNAPSHOT_FAILED",
    "CHECKPOINT_WRITE_FAILED",
    "TRACE_WRITE_FAILED",
    "BUDGET_EXHAUSTED",
    "VERSION_INCOMPATIBLE",
    "SESSION_POISONED",
)


# ---------------------------------------------------------------------------
# 校验异常
# ---------------------------------------------------------------------------

class HarnessValidationError(ValueError):
    """harness 契约校验失败（fail-closed：抛错，不降级、不猜测）。"""


# ---------------------------------------------------------------------------
# 答案与引用
# ---------------------------------------------------------------------------

@dataclass
class CitationRef:
    """一条可回查引用（三类 ref_type，见 CITATION_TYPES）。

    解析键与真实 Store 对齐：
    - evidence    → evidence_id 指向 evidence.store.get_evidence（可选 page_number）；
    - structured  → snapshot_id + (formula_id + formula_version + period 指标) 或
                    (item_code + period 字段)，对应 financial_v2.store.get_metric_result /
                    list_snapshot_items；
    - external    → source_snapshot_id 指向 external_v2.store.get_snapshot。
    """

    ref_type: str                            # CITATION_TYPES 之一
    evidence_id: str | None = None
    snapshot_id: str | None = None           # structured
    item_code: str | None = None             # structured: financial_field
    formula_id: str | None = None            # structured: financial_metric
    formula_version: str | None = None       # structured: financial_metric
    period: str | None = None                # structured
    source_snapshot_id: str | None = None    # external
    page_number: int | None = None           # evidence


@dataclass
class Claim:
    """答案中的一个断言（事实或研判），必须带至少一个 citation 下标。"""

    claim_id: str
    text: str
    kind: str                                # fact | inference
    citation_refs: list[int]                 # 指向 ResearchAnswer.citations 的下标


@dataclass
class AspectAnswer:
    """答案对单个 required-aspect 的覆盖自述（LLM 输出，供 G2 门核对）。"""

    aspect_id: str                          # 对应 required_aspects 的 aspect_id（a1, a2, ...）
    text: str                               # 该方面的简短回答要点
    claim_ids: list[str] = field(default_factory=list)  # 支撑该方面的 claim_id 列表


@dataclass
class ResearchAnswer:
    """一个 KeyQuestion 的简短答案（中间评测产物，非正式章节）。"""

    question_id: str
    answer_text: str
    claims: list[Claim] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    confidence: str = "low"
    completion_status: str = "UNRESOLVED"
    aspects: list[AspectAnswer] = field(default_factory=list)  # 逐 required-aspect 覆盖


@dataclass
class InspectedMaterial:
    """已捕获的 Evidence 正文/摘要（inspect_evidence 全文，search 结果为 snippet）。

    供 G3/G4 校验「证据正文是否真的支撑 claim」使用；gold 不进入本结构。
    is_snippet=True 表示仅有检索摘要（≤200 字符），非全文。
    """

    evidence_id: str
    document_id: str = ""
    source_name: str = ""
    source_type: str = ""
    page_number: int | None = None
    section_path: str = ""
    evidence_type: str = ""
    report_period: str | None = None
    text: str = ""
    structured_payload: dict | None = None
    is_snippet: bool = False


# ---------------------------------------------------------------------------
# 动作协议（LLM 可见面由 actions.py 的 ACTIONS 定义，这里只承载解析后的结果）
# ---------------------------------------------------------------------------

@dataclass
class ActionCall:
    """一个已解析的动作（LLM 输出经严格校验，或 Rules 内部生成）。"""

    action: str                              # actions.ACTIONS 之一
    arguments: dict
    tool_name: str | None = None             # 映射到的工具名；终态动作（ANSWER/STOP_WITH_GAP/REQUEST_HUMAN）为 None
    raw: str = ""                            # LLM 原始输出（审计）
    source: str = "llm"                      # llm | rules（rules 内部动作，如自动 snapshot）


# ---------------------------------------------------------------------------
# 工具历史
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """一次工具调用及其结果（供 state/trace 复用）。"""

    call: TC.ToolCall
    result: TC.ToolResult
    elapsed_ms: int = 0
    auto: bool = False                       # True = Rules 自动（不计 LLM 动作回合）


# ---------------------------------------------------------------------------
# 账本
# ---------------------------------------------------------------------------

@dataclass
class UsageLedger:
    """单题账本（本批次只统计当前题；batch 级累计账本由 Batch C 接管）。"""

    rounds: int = 0
    tool_calls: int = 0
    local_searches: int = 0
    external_searches: int = 0
    fetches: int = 0
    snapshots: int = 0
    action_repairs: int = 0
    llm_calls: int = 0
    input_tokens: int = 0                    # 已知 usage 累计；None 时不加
    output_tokens: int = 0
    usage_unknown_calls: int = 0             # provider 未返回 usage 的次数
    elapsed_ms: int = 0
    added_needs: int = 0
    consecutive_no_new_evidence: int = 0


# ---------------------------------------------------------------------------
# 子 need（补检）
# ---------------------------------------------------------------------------

@dataclass
class NeedState:
    """补检产生的有界子 need（Rules 内部生成）。"""

    need_id: str
    parent_need_id: str | None
    question: str
    status: str = "PENDING"                  # NEED_STATUSES 之一
    created_round: int = 0


# ---------------------------------------------------------------------------
# 研究状态（问题级）
# ---------------------------------------------------------------------------

@dataclass
class ResearchState:
    """单个 KeyQuestion 的研究状态（可变更，跨回合累计）。"""

    run_id: str
    case_id: str
    question_id: str
    company_id: str
    section_id: str | None
    original_question: str
    need: RS.InformationNeed
    status: str = "PENDING"                  # QUESTION_STATUSES 之一
    route_result: RS.RouterResult | None = None
    current_goal: str = ""
    actions: list[ActionCall] = field(default_factory=list)
    tool_history: list[ToolCallRecord] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    structured_refs: list[RS.StructuredResultRef] = field(default_factory=list)
    external_snapshot_ids: list[str] = field(default_factory=list)
    answered_claims: list[Claim] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    required_aspects: list = field(default_factory=list)  # list[dict] = Aspect.asdict
    aspect_source: str = ""                                # SECTION_CONTRACT/DATASET_MAPPING/TEXT_FALLBACK
    inspected_evidence: dict = field(default_factory=dict)  # evidence_id -> InspectedMaterial
    stop_reason: str | None = None
    usage: UsageLedger = field(default_factory=UsageLedger)
    checkpoint_id: str | None = None
    input_versions: dict = field(default_factory=dict)   # 冻结输入版本指纹
    budget: dict = field(default_factory=dict)           # ResearchBudget 的 asdict 快照


# ---------------------------------------------------------------------------
# 研究错误 / 终态产物
# ---------------------------------------------------------------------------

@dataclass
class ResearchError:
    """研究循环中的错误（stage 定位失败层，error_code 定位失败类型）。"""

    error_code: str                          # RESEARCH_ERROR_CODES 之一
    message: str
    stage: str                               # router | action | tool | answer | snapshot | checkpoint | trace


@dataclass
class ResearchOutcome:
    """单题最终产物（Runner 与 Phase 4 交接的单元）。"""

    state: ResearchState
    answer: ResearchAnswer | None
    success: bool
    completion_status: str
    stop_reason: str
