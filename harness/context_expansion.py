"""R2：扩读编排（ContextExpansion）。

从 seed 出发、在明确来源边界内受控扩读。**所有真实读取都经现有 ToolRegistry 正式链**：
``ContextExpansion → ToolRegistry.execute → inspect_evidence_bounded ToolSpec/adapter →
ReadonlyEvidenceReader → evidence.db (mode=ro + query_only)``。

本模块不直接调用 ``ReadonlyEvidenceReader``（那是 adapter 内部实现），不建立第二套
Router/Harness/ToolRegistry 循环；每次真实读取 = 一次 ToolCall/ToolResult + ExpansionStep。

停止只使用 R2 可观察的结构信号（§7.2）：章节边界、文档版本隔离、连续性断裂、续表/截断标记
闭合、无新增材料、硬预算到顶、交叉引用目标不存在。**绝不凭「语义已完整」停止。**
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from harness.evidence_reader import (
    RESOLVE_SEED_IDENTITY_TOOL_NAME,
    TOOL_NAME,
    EvidenceReadResult,
    table_title_of,
)
from harness import heading_structure as HS
from harness.topic_boundary import (
    TOPIC_IN_TOPIC,
    TOPIC_OUT_OF_TOPIC,
    BOUNDARY_POLICY_UNAVAILABLE,
    BOUNDARY_SEMANTICS_VERIFIED,
    block_start_topic_class,
    boundary_eligibility,
    classify_heading_topic,
    find_topic_boundary,
    is_structurally_outer_heading,
    verify_boundary_semantics,
    verification_cases_from_material,
)
from tools import contracts as C

# 扩读方向（请求级，§4.1）。
EXPANSION_DIRECTIONS = ("adjacent_blocks", "table_continuation", "explicit_reference")

# 预算轴（§7.2，版本化 policy 初始值；不按公司/case 调参）。
BUDGET_AXES = (
    "adjacent_blocks_before",
    "adjacent_blocks_after",
    "adjacent_pages",
    "table_continuation",
    "explicit_references",
    "max_bytes",
    "max_tokens",
    "no_new_material_steps",
    "per_seed_cap",
    "per_request_cap",
)

# 未读原因（§4.9）。
UNREAD_REASONS = ("boundary", "budget", "authority", "error")

# 边界处置（§三.2 版本化边界语义）。R2 只做边界成员判定 + aspect 资格筛选，
# 不做 R3 语义事实充分性；语义无法自动判定 → context_candidate，正式 supporting 留给 R3。
BOUNDARY_DISPOSITION_VERSION = "1"
DISPOSITION_SEED = "seed"
DISPOSITION_INSIDE_BOUNDARY = "inside_boundary"
DISPOSITION_CONTEXT_CANDIDATE = "context_candidate"
DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL = "outside_boundary_sentinel"
DISPOSITION_UNREAD_INSIDE_BOUNDARY = "unread_inside_boundary"
DISPOSITION_REJECTED_BOUNDARY_MISMATCH = "rejected_boundary_mismatch"
DISPOSITION_DUPLICATE = "duplicate"
# 修复 A.2：向后扩读时，若随后发现属于上一章节的标题/结构边界，已暂存块被撤回，只留
# boundary trace（绝不进 material/context association/aspect links）。
DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION = "rolled_back_previous_section"
# P1-A.4：向前扩读遇到**文档结构证明的兄弟小节标题**（层级 ≤ 主题小节标题层级）→
# 主题小节在此关闭，该块按 outside_boundary_sentinel 处置（原始字节不采纳），但
# reason_code 明确记为结构性关闭（**不是**关键词证明的主题外），且绝不回溯撤回同 Topic 材料。
REASON_TOPIC_SECTION_CLOSED = "topic_section_closed_sibling_heading"

BOUNDARY_DISPOSITIONS = (
    DISPOSITION_SEED,
    DISPOSITION_INSIDE_BOUNDARY,
    DISPOSITION_CONTEXT_CANDIDATE,
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    DISPOSITION_UNREAD_INSIDE_BOUNDARY,
    DISPOSITION_REJECTED_BOUNDARY_MISMATCH,
    DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION,
    DISPOSITION_DUPLICATE,
)

# 修复 A.3：混合块的主题内前缀片段投影（fragment）是独立可关联材料，其 link disposition
# 必须是 ``fragment_projection``（非 raw sentinel 的 ``outside_boundary_sentinel``）。
DISPOSITION_FRAGMENT_PROJECTION = "fragment_projection"

# 交叉引用标记：命名章节引用（详见/参见）与结构性表引用（见下表/如下表/下表/续表/接上表）。
_NAMED_REFERENCE_MARKERS = ("详见", "参见")
_TABLE_REFERENCE_MARKERS = ("见下表", "如下表", "下表", "续表", "接上表")
# 命名引用目标终止符（句末/分号）；换行不作终止，因 PDF 提取会把目标折行。
_REFERENCE_TARGET_TERMINATORS = ("。", "；")


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpansionBudget:
    """单次扩读预算（§7.2 初始值，冻结为版本化 policy）。"""

    adjacent_blocks_before: int = 3
    adjacent_blocks_after: int = 5
    adjacent_pages: int = 2
    table_continuation: int = 3
    explicit_references: int = 3
    max_bytes: int = 64 * 1024
    max_tokens: int = 16 * 1024
    no_new_material_steps: int = 2
    per_seed_cap: int = 6
    per_request_cap: int = 24

    def as_limits(self) -> dict[str, int]:
        return {a: getattr(self, a) for a in BUDGET_AXES}


# 预算 profile 版本（§十二 Codex 结论 2）：使验收所用预算的「来源/版本」可显式记录；
# 生产默认与验收扩展窗口分离，绝不把验收窗口并入生产默认。
BUDGET_PROFILE_VERSION = "1"

# 验收 budget profile（evaluation-only）：更长扩读窗口，供真实材料验收滚至真实结构边界。
# 通用、版本化、显式记录；绝不用于生产默认（生产默认 = ExpansionBudget()）。
ACCEPTANCE_BUDGET = ExpansionBudget(
    adjacent_blocks_before=4,
    adjacent_blocks_after=16,
    adjacent_pages=10,
    table_continuation=4,
    explicit_references=4,
    max_bytes=256 * 1024,
    max_tokens=64 * 1024,
    no_new_material_steps=4,
    per_seed_cap=32,
    per_request_cap=128,
)

_BUDGET_PROFILES: dict[str, ExpansionBudget] = {
    "production": ExpansionBudget(),
    "acceptance": ACCEPTANCE_BUDGET,
}


def budget_profile(name: str) -> ExpansionBudget:
    """按名称取版本化 budget profile（production | acceptance）。未知名称 → ValueError。"""
    if name not in _BUDGET_PROFILES:
        raise ValueError(f"未知 budget profile {name!r}（可选 {sorted(_BUDGET_PROFILES)}）")
    return _BUDGET_PROFILES[name]


def _estimate_bytes(text: str, structured_payload: dict | None = None) -> int:
    n = len(text.encode("utf-8"))
    if structured_payload:
        n += len(str(structured_payload).encode("utf-8"))
    return n


def _estimate_tokens(text: str) -> int:
    # 估算：中文约 1 字符 1 token，ASCII 约 4 字符 1 token；此处用保守的字符数近似。
    return max(1, len(text))


def _detect_reference_targets(text: str) -> tuple[str, ...]:
    """提取 seed 文本中的显式交叉引用**目标**（不是仅标记词）。

    通用确定性提取（无 LLM/公司硬编码）：
    - 结构性表引用（见下表/如下表/下表/续表/接上表）→ 返回标记本身，executor 按
      「seed 之后同 section 第一个 table」确定性解析；
    - 命名引用（详见/参见 …）→ 取标记后到句末/分号为止的目标文本（含「N、标题」
      编号，供 find_by_section_reference 解析叶子编号），例如
      「详见 …“24、所有权或使用权受到限制的资产”」→ 该目标文本。
    提取不到目标 → 空（该方向不产生读取）。
    """
    targets: list[str] = []
    for m in _TABLE_REFERENCE_MARKERS:
        if m in text:
            targets.append(m)
    for m in _NAMED_REFERENCE_MARKERS:
        idx = text.find(m)
        while idx != -1:
            tail = text[idx + len(m):]
            end = len(tail)
            for term in _REFERENCE_TARGET_TERMINATORS:
                p = tail.find(term)
                if p != -1 and p < end:
                    end = p
            target = tail[:end].strip(" \t\r\n“”‘’\"'（）()：:，,")
            if target:
                targets.append(target)
            idx = text.find(m, idx + len(m))
    return tuple(targets)


# ---------------------------------------------------------------------------
# 公共类型（§4）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpansionSeed:
    evidence_id: str
    page_number: int
    block_index: int
    section_path: tuple[str, ...]
    evidence_type: str
    text: str
    content_hash: str


@dataclass(frozen=True)
class ExpansionCandidateRef:
    evidence_id: str
    page_number: int
    block_index: int
    section_path: tuple[str, ...]
    evidence_type: str
    content_hash: str
    distance: int
    relation: str  # adjacent | continuation | reference


@dataclass(frozen=True)
class BoundaryDecision:
    """逐块确定性边界决策（§三.2）：权威、边界成员、aspect 资格、supporting 四者分离。

    R2 只判定权威 + 边界成员 + aspect 资格；supporting 事实充分性留给 R3。
    """

    evidence_id: str
    disposition: str  # 见 BOUNDARY_DISPOSITIONS
    reason_code: str
    relation: str  # seed | adjacent | continuation | reference
    direction: str | None  # seed | adjacent_blocks_before/after | table_continuation | explicit_reference
    page_number: int
    block_index: int
    section_path: tuple[str, ...]
    evidence_type: str
    content_hash: str
    document_id: str
    document_version: str
    evidence_set_version: str
    seed_section_path: tuple[str, ...]
    path_quality: str  # reliable | unreliable | empty
    structural_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "disposition": self.disposition,
            "reason_code": self.reason_code,
            "relation": self.relation,
            "direction": self.direction,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "section_path": list(self.section_path),
            "evidence_type": self.evidence_type,
            "content_hash": self.content_hash,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "seed_section_path": list(self.seed_section_path),
            "path_quality": self.path_quality,
            "structural_signals": list(self.structural_signals),
        }

    def decision_identity(self, aspect_id: str | None) -> tuple:
        """边界处置身份（P1-A.6）：至少含 aspect_id + evidence_id + 方向 + 边界结论。

        绝不只用 ``evidence_id``：同一 evidence 被不同 aspect、以不同方向、得到不同边界
        结论时，身份必须互不相同（否则一条决策会覆盖另一条，污染互不相同的 aspect 结论）。
        locator 维度由 ``FragmentProjection.fragment_identity`` 单独表达（原始哨兵 vs
        同块片段投影是两种不同对象）。
        """
        return ("boundary_decision", aspect_id or "", self.evidence_id, self.direction or "",
                self.relation, self.reason_code, self.disposition,
                tuple(self.section_path), self.content_hash)


# 表格行摊平特征：路径段内含 2+ 连续空白（列分隔）或多字段，说明 section_path 是
# 表格行文本而非真实章节标题（candidate-13 异常路径）。通用结构信号，非公司/案例专用。
_SECTION_PATH_FIELD_SEP = re.compile(r"\s{2,}")

# 子主题标题编号（新主题开始，仅在粗粒度 section 内作 context_candidate 保守标记，
# 不据此排除）。通用结构信号。
_SUBHEADING_NUMBERING = re.compile(
    r"^\s*(?:"
    r"[（(]\s*[一二三四五六七八九十百千\d]+\s*[）)]\s*"  # （一）（二）（1）（2）
    r"|[一二三四五六七八九十百千\d]+[、.．]"              # 一、 二、 1、 2.
    r"|第\s*[一二三四五六七八九十百千\d]+\s*[章节条款]"
    r")")
# 条目枚举（一是/二是/三是）→ 继续同一主题，非新标题。
_ITEM_ENUMERATION = re.compile(r"^\s*[一二三四五六七八九十百千\d]+\s*是")


def section_path_quality(section_path: tuple[str, ...]) -> str:
    """判定 section_path 是否可作为可信标题层级（reliable | unreliable | empty）。

    unreliable：路径段内含 2+ 连续空白分隔（表格行摊平）或 ≥3 字段，说明 section_path
    是表格行文本而非真实章节标题，不能用于边界判定（candidate-13 异常路径）。
    """
    if not section_path or not any(section_path):
        return "empty"
    for seg in section_path:
        if not seg:
            continue
        if _SECTION_PATH_FIELD_SEP.search(seg):
            return "unreliable"
        if len(seg.split()) >= 3:
            return "unreliable"
    return "reliable"


def _starts_new_subheading(text: str | None) -> bool:
    """块文本是否以新的子主题标题编号开头（非「一是/二是」条目枚举）。"""
    s = unicodedata.normalize("NFC", (text or "").strip())
    if _ITEM_ENUMERATION.match(s):
        return False  # 一是/二是 → 条目枚举，继续
    return bool(_SUBHEADING_NUMBERING.match(s))


def _detect_new_heading_inside(text: str | None) -> str | None:
    """块内新标题（§三.3 混合块）：块首延续当前主题、块中出现新子主题标题。

    返回检测到的新标题片段（供 structural_signals 保守标记）；未检测到返回 None。
    只识别非块首的子标题编号（（一）/1、/第X节/一、），「一是/二是」条目枚举不算新标题。
    通用结构信号，非公司/案例专用。
    """
    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s:
        return None
    # 先按句读/换行切分，找非首段的新标题。
    parts = [p.strip() for p in re.split(r"(?<=[。；;！？!?\n\r])", s) if p.strip()]
    for p in parts[1:]:
        if _ITEM_ENUMERATION.match(p):
            continue
        if _SUBHEADING_NUMBERING.match(p):
            return p[:40]
    # 兜底：段内出现编号（（一）/1、/第X节）且非块首 → 混合块边界信号。
    for m in _SUBHEADING_NUMBERING.finditer(s):
        if m.start() == 0:
            continue
        head = m.group(0).strip()
        tail = s[m.end():m.end() + 1]
        if _ITEM_ENUMERATION.match(head + tail):
            continue
        return s[m.start():m.start() + 40]
    return None


def section_path_relation(seed_path: tuple[str, ...], block_path: tuple[str, ...]) -> str:
    """层级路径关系（§四：通用确定性层级边界，不做固定页码/公司硬编码）。

    - ``same``：完全一致；
    - ``sub``：block 是 seed 的合法子路径（seed 为 block 前缀，block 更深）→ 层级内继续；
    - ``super``：block 是 seed 的祖先（block 为 seed 前缀，block 更浅）→ 新上级章节 = 边界；
    - ``sibling``：既非子也非祖先（不同分支）→ 新同级章节 = 边界；
    - ``empty``：任一路径为空 → 无法层级判定。
    """
    if not seed_path or not block_path:
        return "empty"
    if block_path == seed_path:
        return "same"
    if len(block_path) > len(seed_path) and block_path[:len(seed_path)] == seed_path:
        return "sub"
    if len(block_path) < len(seed_path) and seed_path[:len(block_path)] == block_path:
        return "super"
    return "sibling"


@dataclass(frozen=True)
class ExpansionStep:
    step_index: int
    action: str  # resolve_seed | inspect_bounded | stop
    tool_call: C.ToolCall | None
    inputs: dict
    outputs: tuple[str, ...]  # 实际读取的 evidence_ids（不含 material_id）
    stop_reason: str | None
    budget_remaining: dict
    # ToolResult 状态（§三.6）：工具错误绝不伪装成「completed within source boundary」。
    result_status: str | None = None
    result_error_code: str | None = None
    result_trace_id: str | None = None
    # 工具版本（§三：确定性指纹绑定 tool_name+tool_version，不绑定 result_trace_id/call_id）。
    tool_version: str | None = None
    # 逐块 adopt/reject 理由（§三.6）：(evidence_id, outcome) outcome ∈ adopt|dup|boundary|budget。
    block_outcomes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ContextExpansionTrace:
    trace_id: str
    request: "ContextExpansionRequest"
    steps: tuple[ExpansionStep, ...]


@dataclass(frozen=True)
class UnreadScope:
    reason: str  # boundary | budget | authority | error
    scope_desc: str
    refs: tuple[ExpansionCandidateRef, ...]
    budget_axis: str | None
    # P1-C.6：本未读范围的**因果停止原因**（如 "hard budget (adjacent_pages)" /
    # "cross reference target dangling"），绝不与最终聚合的 stop_reason summary 混为一谈。
    stop_reason: str | None = None


@dataclass(frozen=True)
class FragmentProjection:
    """修复 A：mixed block 的主题内前缀片段投影（保留完整原子材料，另建有界关联）。

    引用**完整原子 EvidenceBlock**（``evidence_id``/``content_hash`` 不变），仅投影
    「首个主题外标题之前」的主题内前缀（``prefix_text = block.text[:char_offset]``）。
    不新建材料类型、不修改原文/来源 content_hash；真正有界关联经 locator.offset 表达。
    """

    evidence_id: str
    block: EvidenceReadResult
    char_offset: int                # 边界标题起始字符位置
    prefix_text: str                # 主题内相关前缀（原文[:char_offset] 的 strip 后）
    out_of_topic_heading: str       # 首个边界标题（主题外或结构性关闭的兄弟小节标题）

    def fragment_identity(self) -> tuple:
        """片段投影身份（P1-A.6）：与同 evidence 的原始哨兵身份互不相同（locator 维度）。"""
        return ("fragment_projection", self.evidence_id, self.char_offset,
                self.block.content_hash)


@dataclass(frozen=True)
class ContextExpansionRequest:
    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    seed: ExpansionSeed
    directions: tuple[str, ...]
    budget: ExpansionBudget
    dependency_fingerprint: str
    # 修复 A：主题边界分类按 aspect 版本化；None 表示不启用主题边界分类（回退既有逻辑）。
    aspect_id: str | None = None


@dataclass(frozen=True)
class ExpansionResult:
    seed: ExpansionSeed
    adopted: tuple[EvidenceReadResult, ...]
    candidates_unread: tuple[ExpansionCandidateRef, ...]
    stop_reason: str
    unread_scope: UnreadScope
    trace: ContextExpansionTrace
    budget_consumed: dict
    # current 判定（由 verify_seed ToolResult 决定，非调用方硬编码）。
    seed_is_current_document: bool = False
    seed_is_current_set: bool = False
    # evidence_id → 扩读关系（seed|adjacent|continuation|reference），供 reference assembly 投影。
    adopted_relations: dict[str, str] = field(default_factory=dict)
    # 逐块确定性边界决策（§三.2）；哨兵/拒绝块在此有记录，但不在 candidates_unread 中。
    boundary_decisions: tuple[BoundaryDecision, ...] = ()
    # 修复 A：mixed block 主题内前缀片段投影（保留完整原子材料的有界关联，非新材料类型）。
    fragment_projections: tuple[FragmentProjection, ...] = ()
    # P1-A.2：边界资格状态（正式扩读路径**消费**它，绝不被绕过）。四态取值见
    # ``harness.topic_boundary``；未验证的 aspect 材料不得被验收为 accepted。
    boundary_status: str = ""
    boundary_eligible: bool = False
    boundary_reason: str = ""
    boundary_verification: dict = field(default_factory=dict)
    # P1-A.3：主题小节标题层级（文档结构权威）与其来源（section_path_leaf/first_heading/unknown）。
    topic_level: int | None = None
    topic_level_source: str = "unknown"

    @property
    def outside_boundary_sentinels(self) -> tuple[BoundaryDecision, ...]:
        """边界外哨兵：证明边界已探索的块，不属于未读。"""
        return tuple(d for d in self.boundary_decisions
                     if d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL)

    @property
    def unread_inside_boundary(self) -> tuple[BoundaryDecision, ...]:
        """边界内真未读：仅预算/工具错误/悬挂引用/未闭合续表的块。"""
        return tuple(d for d in self.boundary_decisions
                     if d.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def expand(request: ContextExpansionRequest, registry, *,
           run_id: str = "cli", route: str = "DIRECT_EVIDENCE") -> ExpansionResult:
    """对单个 seed 执行受控扩读，返回 ExpansionResult。

    读取链：verify_seed + 各方向 bounded ToolCall，全部经 ``registry.execute``。
    seed mismatch / 版本不符 → fail-closed（adopted 为空、stop_reason=authority）。
    各方向独立停止（§三.1）：一个方向命中边界/预算不中止其他方向；工具错误单独显式
    记录并作为最终 stop_reason（绝不伪装成「completed within source boundary」）。
    """
    seed = request.seed
    budget = request.budget
    trace_id = uuid.uuid4().hex
    steps: list[ExpansionStep] = []
    adopted: dict[str, EvidenceReadResult] = {}
    adopted_relations: dict[str, str] = {}
    candidates_unread: dict[str, ExpansionCandidateRef] = {}
    boundary_decisions: list[BoundaryDecision] = []
    fragments: list[FragmentProjection] = []
    consumed: dict[str, int] = {a: 0 for a in BUDGET_AXES}
    # 每方向独立停止原因（direction → stop reason）；工具错误独立收集。
    direction_stops: dict[str, str] = {}
    tool_errors: list[str] = []
    no_new_stop: str | None = None
    unread_reason: str | None = None
    unread_axis: str | None = None
    # P1-C.6：未读的**因果停止原因**（预算/边界/dangling 各自的方向停止串），
    # 与 unread_reason/unread_axis 同步记录，绝不与最终聚合 final_stop 混写。
    unread_stop_reason: str | None = None
    no_new_streak = 0
    # P1-A.2/A.3：主题小节层级与边界资格（seed 复验后确定；未知则不做结构性关闭）。
    topic_level: int | None = None
    topic_level_source = "unknown"
    boundary_status = ""
    boundary_eligible = False
    boundary_reason = ""
    boundary_verification: dict = {}

    def remaining(axis: str) -> int:
        return max(0, budget.as_limits()[axis] - consumed.get(axis, 0))

    def snapshot_remaining() -> dict:
        return {a: remaining(a) for a in BUDGET_AXES}

    def record_step(action: str, call: C.ToolCall | None, inputs: dict,
                    outputs: tuple[str, ...], step_stop: str | None,
                    result_status: str | None = None,
                    result_error_code: str | None = None,
                    result_trace_id: str | None = None,
                    tool_version: str | None = None,
                    block_outcomes: tuple[tuple[str, str], ...] = ()) -> None:
        steps.append(ExpansionStep(
            step_index=len(steps), action=action, tool_call=call,
            inputs=dict(inputs), outputs=tuple(outputs),
            stop_reason=step_stop, budget_remaining=snapshot_remaining(),
            result_status=result_status, result_error_code=result_error_code,
            result_trace_id=result_trace_id, tool_version=tool_version,
            block_outcomes=block_outcomes))

    def issue(arguments: dict, idem: str, tool_name: str = TOOL_NAME
              ) -> tuple[C.ToolCall, C.ToolResult]:
        call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=dict(arguments),
            idempotency_key=idem, need_id="", batch_id="")
        result = registry.execute(call, route=route, run_id=run_id)
        return call, result

    def base_args() -> dict:
        return {
            "company_id": request.company_id,
            "document_id": request.document_id,
            "document_version": request.document_version,
            "evidence_set_version": request.evidence_set_version,
        }

    def candidate(block: EvidenceReadResult, relation: str) -> ExpansionCandidateRef:
        distance = abs((block.page_number - seed.page_number) * 1000
                       + (block.block_index - seed.block_index))
        return ExpansionCandidateRef(
            evidence_id=block.evidence_id, page_number=block.page_number,
            block_index=block.block_index, section_path=block.section_path,
            evidence_type=block.evidence_type, content_hash=block.content_hash,
            distance=distance, relation=relation)

    def record_decision(block: EvidenceReadResult, disposition: str, reason_code: str,
                        relation: str, direction: str | None,
                        signals: tuple[str, ...] = ()) -> None:
        boundary_decisions.append(BoundaryDecision(
            evidence_id=block.evidence_id, disposition=disposition, reason_code=reason_code,
            relation=relation, direction=direction, page_number=block.page_number,
            block_index=block.block_index, section_path=block.section_path,
            evidence_type=block.evidence_type, content_hash=block.content_hash,
            document_id=block.document_id, document_version=block.document_version,
            evidence_set_version=block.evidence_set_version,
            seed_section_path=seed.section_path,
            path_quality=section_path_quality(block.section_path),
            structural_signals=signals))

    def classify(block: EvidenceReadResult, relation: str,
                 section_check: bool, page_check: bool, direction: str,
                 block_budget_axis: str | None = None
                 ) -> tuple[str, str | None, str | None, str | None]:
        """返回 (outcome, stop_reason, unread_reason, unread_axis)。

        边界语义（§三.2）：章节/身份断裂块 = outside_boundary_sentinel（证明边界已探索，
        不进 candidates_unread、不采纳）；只有边界内预算耗尽才 = unread_inside_boundary。
        页距（adjacent_pages）与方向块数（block_budget_axis）是**预算轴**，绝不据此判哨兵。
        """
        if block.evidence_id in adopted or block.evidence_id in candidates_unread:
            record_decision(block, DISPOSITION_DUPLICATE, "duplicate", relation, direction)
            return "dup", None, None, None
        # 文档/版本/集合身份一致性（防御；正常已被 reader fail-closed）。
        if (block.document_id != request.document_id
                or block.document_version != request.document_version
                or block.evidence_set_version != request.evidence_set_version):
            record_decision(block, DISPOSITION_REJECTED_BOUNDARY_MISMATCH, "identity_mismatch",
                            relation, direction,
                            ("identity", block.document_id, block.document_version,
                             block.evidence_set_version))
            return "boundary", "boundary mismatch (identity)", None, None
        # 章节边界（§四 层级）：seed 的合法子路径继续；新同级/祖先 = 哨兵（边界已探索，非未读）。
        # 修复 D：块的 section_path 为摊平表行（unreliable，candidate-13 异常路径）时，
        # 不能据其判兄弟/祖先章节边界（会把跨页续表/附注续误判为越界）。此时按同级连续性
        # 继续，真正的主题断裂由主题边界（修复 A）或可靠 section_path 的 sibling/super 裁决。
        if section_check and block.section_path and seed.section_path:
            block_pq = section_path_quality(block.section_path)
            if block_pq != "unreliable":
                rel = section_path_relation(seed.section_path, block.section_path)
                if rel in ("sibling", "super"):
                    record_decision(block, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                                    f"section_boundary_{rel}", relation, direction,
                                    ("section_path", tuple(seed.section_path),
                                     tuple(block.section_path), rel))
                    return "boundary", "unrelated section boundary", None, None
        # 方向块数预算（§三.2：块数是预算轴，非边界）。
        if (block_budget_axis is not None
                and consumed[block_budget_axis] >= budget.as_limits()[block_budget_axis]):
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY,
                            f"budget_{block_budget_axis}", relation, direction,
                            ("budget_axis", block_budget_axis))
            return "budget", f"hard budget ({block_budget_axis})", "budget", block_budget_axis
        # 页距预算（§三.2：adjacent_pages 是预算轴，绝不据此判哨兵）。
        if page_check and abs(block.page_number - seed.page_number) > budget.adjacent_pages:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY, "budget_adjacent_pages",
                            relation, direction,
                            ("budget_axis", "adjacent_pages", "page_distance",
                             seed.page_number, block.page_number))
            return "budget", "hard budget (adjacent_pages)", "budget", "adjacent_pages"
        # 边界内预算耗尽 → 真未读。
        if len(adopted) >= budget.per_seed_cap:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY, "budget_per_seed_cap",
                            relation, direction, ("budget_axis", "per_seed_cap"))
            return "budget", "hard budget (per_seed_cap)", "budget", "per_seed_cap"
        if len(adopted) >= budget.per_request_cap:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY, "budget_per_request_cap",
                            relation, direction, ("budget_axis", "per_request_cap"))
            return "budget", "hard budget (per_request_cap)", "budget", "per_request_cap"
        add_bytes = _estimate_bytes(block.text, block.structured_payload)
        if consumed["max_bytes"] + add_bytes > budget.max_bytes:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY, "budget_max_bytes",
                            relation, direction, ("budget_axis", "max_bytes"))
            return "budget", "hard budget (max_bytes)", "budget", "max_bytes"
        add_tokens = _estimate_tokens(block.text)
        if consumed["max_tokens"] + add_tokens > budget.max_tokens:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            record_decision(block, DISPOSITION_UNREAD_INSIDE_BOUNDARY, "budget_max_tokens",
                            relation, direction, ("budget_axis", "max_tokens"))
            return "budget", "hard budget (max_tokens)", "budget", "max_tokens"
        # 修复 A：主题边界（通用/版本化/公司无关）。相邻块若出现主题外标题，该块为主题外
        # sentinel（记录主题内前缀片段投影 + 停止该方向扩读），绝不把 安全生产/在建工程/
        # 未来规划/行业 等主题外内容采纳进 formal/context 材料。仅对 adjacent 关系生效
        # （table_continuation/explicit_reference 的目标块不做主题边界分类）。
        if request.aspect_id is not None and relation == "adjacent":
            tb = find_topic_boundary(block.text, request.aspect_id,
                                     topic_level=topic_level)
            if tb.has_out_of_topic:
                record_decision(
                    block, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                    "topic_boundary_out_of_topic", relation, direction,
                    ("topic_boundary", tb.out_of_topic_heading or "",
                     "stop_direction", "true",
                     "heading_level", str(tb.closure_level),
                     "topic_level", str(tb.topic_level),
                     "relevant_prefix", tb.relevant_prefix[:200]))
                fragments.append(FragmentProjection(
                    evidence_id=block.evidence_id, block=block,
                    char_offset=tb.char_offset or 0, prefix_text=tb.relevant_prefix,
                    out_of_topic_heading=tb.out_of_topic_heading or ""))
                return "boundary", "topic boundary (out-of-topic heading)", None, None
            # 块首即主题外标题（无主题内前缀）：直接哨兵停止，无片段投影。
            if block_start_topic_class(block.text, request.aspect_id) == TOPIC_OUT_OF_TOPIC:
                record_decision(
                    block, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                    "topic_boundary_out_of_topic", relation, direction,
                    ("topic_boundary", block.text.strip()[:40],
                     "stop_direction", "true"))
                return "boundary", "topic boundary (out-of-topic heading)", None, None
            # P1-A.4：文档结构证明的**兄弟小节标题**（层级 ≤ 主题小节标题层级）→ 主题小节
            # 在此关闭。它**不是**主题外（不冒充 out_of_topic），也不回溯撤回同 Topic 材料；
            # 只关闭边界、停止该方向，并保留主题内前缀片段投影。
            if tb.has_closure:
                signals = ("topic_section_closed", tb.closure_heading or "",
                           "closure_kind", tb.closure_kind or "",
                           "heading_level", str(tb.closure_level),
                           "topic_level", str(tb.topic_level),
                           "topic_level_source", topic_level_source)
                if not tb.relevant_prefix:
                    # 块首即兄弟小节标题：整块不属于本主题小节（无前缀可投影）。
                    record_decision(
                        block, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                        REASON_TOPIC_SECTION_CLOSED, relation, direction, signals)
                    return "boundary", "topic section closed (sibling heading)", None, None
                record_decision(
                    block, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                    REASON_TOPIC_SECTION_CLOSED, relation, direction,
                    signals + ("relevant_prefix", tb.relevant_prefix[:200]))
                fragments.append(FragmentProjection(
                    evidence_id=block.evidence_id, block=block,
                    char_offset=tb.char_offset or 0, prefix_text=tb.relevant_prefix,
                    out_of_topic_heading=tb.closure_heading or ""))
                return "boundary", "topic section closed (sibling heading)", None, None
        # 采纳：inside_boundary 或 context_candidate（保守，均非 supporting）。
        mixed_heading = _detect_new_heading_inside(block.text)
        if mixed_heading is not None:
            # 混合块（§三.3 / P1-1）：块首延续当前主题、块中出现新子标题。主题内部
            # （同 section_path 或其子路径）的板块子标题说明仍属当前主题的受控扩展，
            # 不得因标题形式直接停止扩读；只保守标 context_candidate（绝不自动
            # supporting）。真正的主题断裂由 section_path sibling/super → sentinel 单独裁决。
            disposition = DISPOSITION_CONTEXT_CANDIDATE
            reason_code = "mixed_block_context_candidate"
            signals: tuple[str, ...] = ("mixed_block", mixed_heading)
        else:
            pq = section_path_quality(seed.section_path)
            if pq == "unreliable":
                disposition = DISPOSITION_CONTEXT_CANDIDATE
                reason_code = "section_path_unreliable"
                signals: tuple[str, ...] = ("seed_section_path_unreliable",)
            elif _starts_new_subheading(block.text):
                disposition = DISPOSITION_CONTEXT_CANDIDATE
                reason_code = "coarse_section_sub_heading_transition"
                signals = ("sub_heading_transition",)
            else:
                disposition = DISPOSITION_INSIDE_BOUNDARY
                reason_code = "same_section_continuity"
                signals = ()
        adopted[block.evidence_id] = block
        adopted_relations[block.evidence_id] = relation
        consumed["max_bytes"] += add_bytes
        consumed["max_tokens"] += add_tokens
        record_decision(block, disposition, reason_code, relation, direction, signals)
        return "adopt", None, None, None

    def do_rolling_read(mode: str, axis: str, relation: str,
                        section_check: bool, page_check: bool, direction: str,
                        extra: dict | None = None,
                        anchor_page: int | None = None,
                        anchor_block: int | None = None) -> None:
        """滚动 frontier 有界扩读（§三.1/三.2）：以最后确认仍在边界内的块为 frontier，
        继续请求下一批，直到真实结构边界或明确预算停止。绝不整篇加载、绝不
        list_document_evidence；每批用 limit+1 探针显化 has_more。"""
        nonlocal unread_reason, unread_axis, unread_stop_reason, no_new_streak, no_new_stop
        if direction in direction_stops:
            return
        # P1-C.3：锚点默认 seed；后续扩读材料中发现的引用以**发起块**位置为锚（「见下表」
        # 类结构表引用相对发起块解析，而非 seed）。
        frontier_page = seed.page_number if anchor_page is None else anchor_page
        frontier_block = seed.block_index if anchor_block is None else anchor_block
        direction_budget = budget.as_limits()[axis]
        # 修复 A.2：向后方向已暂存（尚未确定归属）的块；一旦发现上一章节标题即撤回。
        backward_pending: list[str] = []
        while direction not in direction_stops:
            remaining_dir = max(0, direction_budget - consumed[axis])
            remaining_request = max(0, budget.per_request_cap - consumed["per_request_cap"])
            batch = min(remaining_dir, remaining_request)
            if batch <= 0:
                # 预算已耗尽：classify 在上一批的探针块处已把第一个未读块记为
                # unread_inside_boundary（block_budget_axis / per_request_cap）。
                break
            probe_limit = batch + 1
            args = base_args()
            args["mode"] = mode
            args["limit"] = probe_limit
            args["page_number"] = frontier_page
            args["block_index"] = frontier_block
            args.update(extra or {})
            call, res = issue(args, f"{trace_id}:{mode}:{len(steps)}")
            blocks: list[EvidenceReadResult] = []
            if res.status == "SUCCESS":
                blocks = [EvidenceReadResult.from_dict(d)
                          for d in res.data.get("blocks", [])]
            elif res.status in ("FATAL_ERROR", "RETRYABLE_ERROR"):
                tool_errors.append(f"{res.status}:{res.error_code or ''}")
                direction_stops[direction] = "tool error"
                record_step("inspect_bounded", call, args, (), direction_stops[direction],
                            result_status=res.status, result_error_code=res.error_code,
                            result_trace_id=res.trace_id, tool_version=res.tool_version)
                return
            elif res.status == "EMPTY" and mode == "explicit_reference" and \
                    (extra or {}).get("reference_target"):
                # 交叉引用目标 dangling/不可解析 → 记 unread，停该方向（§7.2，独立于其他目标）。
                direction_stops[direction] = "cross reference target dangling"
                if unread_reason is None:
                    unread_reason = "boundary"
                    unread_stop_reason = "cross reference target dangling"
                record_step("inspect_bounded", call, args, (), direction_stops[direction],
                            result_status=res.status, result_error_code=res.error_code,
                            result_trace_id=res.trace_id, tool_version=res.tool_version)
                return
            has_more = len(blocks) == probe_limit
            outcomes: list[tuple[str, str]] = []
            adopted_in_batch = 0
            for b in blocks:
                # 修复 A.2：向后扩读回滚。编号子标题在向后方向上是「上一章节 / seed 自身章节」
                # 的结构边界信号（与主题分类正交）。仅 aspect_id 可用时启用：无主题分类依据时
                # 保守维持旧行为（不据此停止/撤回）。
                if direction == "adjacent_blocks_before" and request.aspect_id is not None:
                    start_sub = _starts_new_subheading(b.text)
                    # 块首编号子标题 → heading=块首文本；否则块内出现的编号子标题（混合块）
                    # 也可能属于上一章节（如「（二）董事及高级管理人员的主要工作经历」），
                    # 对非主题内编号子标题同样触发回滚（补齐真实 v5 高管材料落点）。
                    inner_sub = None if start_sub else _detect_new_heading_inside(b.text)
                    heading = b.text.strip() if start_sub else inner_sub
                    if heading is not None:
                        heading_level = HS.leading_heading_level(heading)
                        topic = classify_heading_topic(
                            heading, request.aspect_id,
                            level=heading_level, topic_level=topic_level)
                        if start_sub and topic == TOPIC_IN_TOPIC:
                            # seed 自身主题标题：classify 采纳为 context_candidate 后停止向后
                            # （到达 seed 自身章节起点；pending 内容块属 seed 自身章节，保留）。
                            outcome, stop, ureason, uaxis = classify(
                                b, relation, section_check, page_check, direction,
                                block_budget_axis=axis)
                            outcomes.append((b.evidence_id, outcome))
                            if outcome == "adopt":
                                adopted_in_batch += 1
                                consumed[axis] += 1
                                consumed["per_request_cap"] += 1
                                frontier_page = b.page_number
                                frontier_block = b.block_index
                                backward_pending.append(b.evidence_id)
                            direction_stops[direction] = "seed section heading reached (backward)"
                            break
                        # P1-A.4：撤回前提是**结构性证明**的越界 —— 要么 external 关键词
                        # 证明主题外（out_of_topic），要么文档标题层级证明它是主题小节的
                        # 同级/更浅标题。ambiguous 且层级未被证明 → 绝不自动撤回同 Topic
                        # 材料（旧实现在此处据 ambiguous 撤回，属审计 P1-A.4 缺陷）。
                        outer_proven = (
                            topic == TOPIC_OUT_OF_TOPIC
                            or is_structurally_outer_heading(
                                heading, level=heading_level, topic_level=topic_level))
                        if topic != TOPIC_IN_TOPIC and outer_proven:
                            # 上一章节标题（块首或块内）：撤回该方向已暂存块（只留 boundary
                            # trace，绝不进 material/context association/aspect links），
                            # 标题所在块记为哨兵。
                            for eid in backward_pending:
                                blk = adopted.pop(eid, None)
                                if blk is not None:
                                    adopted_relations.pop(eid, None)
                                    record_decision(
                                        blk, DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION,
                                        "backward_previous_section_heading", relation, direction,
                                        ("previous_section_heading", heading[:40]))
                            backward_pending.clear()
                            record_decision(
                                b, DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
                                "backward_previous_section_heading", relation, direction,
                                ("previous_section_heading", heading[:40]))
                            outcomes.append((b.evidence_id, "boundary"))
                            direction_stops[direction] = "previous section heading (backward rollback)"
                            break
                        # 块内编号子标题为主题内（如「（三）各业务板块经营情况」）→ 混合块，
                        # 落到下方 classify() 的 mixed_block_context_candidate（P1-1，保守）。
                outcome, stop, ureason, uaxis = classify(
                    b, relation, section_check, page_check, direction,
                    block_budget_axis=axis)
                outcomes.append((b.evidence_id, outcome))
                if outcome == "adopt":
                    adopted_in_batch += 1
                    consumed[axis] += 1
                    consumed["per_request_cap"] += 1
                    frontier_page = b.page_number
                    frontier_block = b.block_index
                    if direction == "adjacent_blocks_before":
                        backward_pending.append(b.evidence_id)
                if outcome in ("boundary", "budget"):
                    direction_stops.setdefault(direction, stop or outcome)
                    if ureason is not None and unread_reason is None:
                        unread_reason = ureason
                        unread_axis = uaxis
                        unread_stop_reason = stop
                    break
            new_ids = tuple(eid for eid, o in outcomes if o == "adopt")
            record_step("inspect_bounded", call, args, new_ids,
                        direction_stops.get(direction),
                        result_status=res.status, result_error_code=res.error_code,
                        result_trace_id=res.trace_id, tool_version=res.tool_version,
                        block_outcomes=tuple(outcomes))
            if not has_more:
                break
            if adopted_in_batch == 0:
                # has_more 但本批无 adopt（全 dup/拒绝）→ 前进 frontier 避免死循环，并计
                # 入 no_new_material_steps 上限（§三.4）。
                if blocks:
                    frontier_page = blocks[-1].page_number
                    frontier_block = blocks[-1].block_index
                no_new_streak += 1
                if no_new_streak > budget.no_new_material_steps:
                    no_new_stop = no_new_stop or "no new material"
                    if unread_reason is None:
                        unread_reason = "budget"
                        unread_stop_reason = "no new material"
                    break
            else:
                no_new_streak = 0

    # -- Step 0：resolve seed（fail-closed，经独立 resolve_seed_identity ToolSpec） --
    seed_args = base_args()
    seed_args.update({
        "evidence_id": seed.evidence_id,
        "page_number": seed.page_number,
        "block_index": seed.block_index,
        "section_path": list(seed.section_path),
        "content_hash": seed.content_hash,
    })
    seed_call, seed_res = issue(seed_args, f"{trace_id}:seed:{seed.evidence_id}",
                                tool_name=RESOLVE_SEED_IDENTITY_TOOL_NAME)
    if seed_res.status != "SUCCESS":
        msg = seed_res.message or seed_res.status
        record_step("resolve_seed", seed_call, {"seed_evidence_id": seed.evidence_id},
                    (), f"seed fail-closed: {msg}",
                    result_status=seed_res.status, result_error_code=seed_res.error_code,
                    result_trace_id=seed_res.trace_id, tool_version=seed_res.tool_version)
        unread = UnreadScope(
            reason="authority",
            scope_desc=f"seed {seed.evidence_id} 复验失败，未进行任何扩读：{msg}",
            refs=(), budget_axis=None)
        return ExpansionResult(
            seed=seed, adopted=(), candidates_unread=(), stop_reason="authority",
            unread_scope=unread,
            trace=ContextExpansionTrace(trace_id=trace_id, request=request,
                                        steps=tuple(steps)),
            budget_consumed=dict(consumed))

    seed_block = EvidenceReadResult.from_dict(seed_res.data["blocks"][0])
    adopted[seed_block.evidence_id] = seed_block
    adopted_relations[seed_block.evidence_id] = "seed"
    # -- P1-A.2/A.3：消费边界资格状态（由 seed 自身材料 + section_path 派生） --
    # 主题小节标题层级来自**文档自身编号形式**（section_path 叶子命中优先），验证用例同理；
    # 二者都不来自策略关键词，故构成对策略的独立验证。
    if request.aspect_id is not None:
        topic_level, topic_level_source = HS.topic_level_of(
            seed_block.text, seed.section_path)
        _verification = verify_boundary_semantics(
            request.aspect_id,
            verification_cases_from_material(
                request.aspect_id, (seed_block.text,), seed.section_path))
        _elig = boundary_eligibility(request.aspect_id, verification=_verification)
        boundary_status = _elig.status
        boundary_eligible = _elig.eligible
        boundary_reason = _elig.reason
        boundary_verification = _verification.to_dict()
    record_decision(seed_block, DISPOSITION_SEED, "seed", "seed", "seed",
                    ("seed_section_path_quality", section_path_quality(seed.section_path),
                     "boundary_status", boundary_status or "not_applicable",
                     "topic_level_source", topic_level_source))
    consumed["per_seed_cap"] += 1
    # seed 同样计入 per_request_cap（classify 的 len(adopted) 门槛含 seed）。
    consumed["per_request_cap"] += 1
    consumed["max_bytes"] += _estimate_bytes(seed_block.text, seed_block.structured_payload)
    consumed["max_tokens"] += _estimate_tokens(seed_block.text)
    seed_is_current_document = bool(seed_res.data.get("is_current_document"))
    seed_is_current_set = bool(seed_res.data.get("is_current_set"))
    seed_table_title = table_title_of(seed_block)
    record_step("resolve_seed", seed_call, {"seed_evidence_id": seed.evidence_id},
                (seed_block.evidence_id,), None,
                result_status=seed_res.status, result_error_code=seed_res.error_code,
                result_trace_id=seed_res.trace_id, tool_version=seed_res.tool_version)

    # -- P1-A.2 fail-closed：边界策略不可用 → 绝不扩读（除 seed 外不采纳任何块） --
    # 「不可用」= 该 aspect 的主题边界无法从冻结契约派生 → 没有任何边界权威，任何向前/
    # 向后读取都无法判定是否越界，故 fail-closed：只保留已复验的 seed，显式记 boundary。
    # （策略可用但**未独立验证**的情形不在此处硬停：扩读仍受结构门约束，但材料不得被
    # 验收为 accepted —— 见 ExpansionResult.boundary_status 与验收侧门。）
    if request.aspect_id is not None and boundary_status == BOUNDARY_POLICY_UNAVAILABLE:
        record_step("stop", None, {"boundary_status": boundary_status}, (),
                    "boundary policy unavailable: no expansion")
        return ExpansionResult(
            seed=seed, adopted=tuple(adopted.values()), candidates_unread=(),
            stop_reason="boundary policy unavailable: no expansion",
            unread_scope=UnreadScope(
                reason="boundary",
                scope_desc=("主题边界策略不可用（boundary_policy_unavailable）："
                            f"{boundary_reason or request.aspect_id}；除 seed 外未进行任何扩读"),
                refs=(), budget_axis=None),
            trace=ContextExpansionTrace(trace_id=trace_id, request=request,
                                        steps=tuple(steps)),
            budget_consumed=dict(consumed),
            seed_is_current_document=seed_is_current_document,
            seed_is_current_set=seed_is_current_set,
            adopted_relations=dict(adopted_relations),
            boundary_decisions=tuple(boundary_decisions),
            fragment_projections=tuple(fragments),
            boundary_status=boundary_status, boundary_eligible=boundary_eligible,
            boundary_reason=boundary_reason,
            boundary_verification=boundary_verification,
            topic_level=topic_level, topic_level_source=topic_level_source)

    # -- 各方向（独立停止） --
    for direction in request.directions:
        if direction == "adjacent_blocks":
            # §三.1：adjacent_before / adjacent_after 用**独立**停止键，前一方向命中
            # 边界/预算/空/工具错误绝不阻止后一方向继续读取。
            do_rolling_read("adjacent_before", "adjacent_blocks_before",
                            "adjacent", True, True, "adjacent_blocks_before")
            do_rolling_read("adjacent_after", "adjacent_blocks_after",
                            "adjacent", True, True, "adjacent_blocks_after")
        elif direction == "table_continuation":
            # §四：续表必须落在同一权威边界（同 section）+ 合理页/块连续性；无 title/continued_from
            # 身份信号绝不猜（executor 层 fail-closed），跨 section/跨多页的同名表不采纳。
            # P1-C.1：续表**滚动**闭合（limit+1 探针 + has_more），预算耗尽时把首个未读
            # 续表块显式记 unread_inside_boundary，绝不 single read_once 静默丢弃链尾。
            do_rolling_read("table_continuation", "table_continuation",
                            "continuation", True, True, "table_continuation",
                            extra={"table_title": seed_table_title} if seed_table_title else None)
        elif direction == "explicit_reference":
            markers = _detect_reference_targets(seed.text)
            if not markers:
                continue  # 无交叉引用 → 该方向不产生读取，也不伪造正读。
            # P1-C.2：每个引用目标**独立**有界滚动读取（独立停止/未读/预算消耗）。
            # 旧实现把多目标 join 成单一 reference_target，仅解析最后一个编号、漏掉其余目标。
            for i, target in enumerate(markers):
                direction_key = "explicit_reference" if i == 0 else f"explicit_reference:{i}"
                do_rolling_read("explicit_reference", "explicit_references",
                                "reference", False, False, direction_key,
                                extra={"reference_target": target})

    # P1-C.3：跟随后续扩读材料中新发现的显式引用（不只扫描 seed 文本）。
    # 有界**单遍**：仅对方向循环结束时已采纳的**非 seed**块扫描引用；每个目标经
    # seen_targets 去重只 follow 一次（绝不无限递归）；锚点用**发起块**位置，保证
    # 「见下表」类结构表引用相对发起块解析。发现引用仍受 explicit_references 预算
    # 约束（do_rolling_read 内部按 consumed["explicit_references"] 扣减）。
    if "explicit_reference" in request.directions:
        seen_targets = set(_detect_reference_targets(seed.text))
        for blk in list(adopted.values()):
            if blk.evidence_id == seed.evidence_id:
                continue
            for target in _detect_reference_targets(blk.text):
                if target in seen_targets:
                    continue
                seen_targets.add(target)
                direction_key = f"explicit_reference:discovered:{len(seen_targets)}"
                do_rolling_read("explicit_reference", "explicit_references",
                                "reference", False, False, direction_key,
                                extra={"reference_target": target},
                                anchor_page=blk.page_number,
                                anchor_block=blk.block_index)

    # 最终停止原因聚合：工具错误 > 各方向停止（按**实际处理序**，即 dict 插入序）>
    # 无新增 > 默认。按插入序而非固定名单，确保 per-target 的 explicit_reference:N 停止
    # 也能被正确聚合（P1-C.2 每个目标独立停止）。
    if tool_errors:
        final_stop = "tool error: " + "; ".join(tool_errors)
    elif direction_stops:
        final_stop = next(iter(direction_stops.values()))
    elif no_new_stop is not None:
        final_stop = no_new_stop
    else:
        final_stop = "completed within source boundary"

    # 未读范围诚实显化：凡被记为 candidate 的块 + 停止原因合并。
    unread_refs = tuple(sorted(candidates_unread.values(),
                               key=lambda c: (c.page_number, c.block_index, c.evidence_id)))
    # P1-C.6：未读因果链按方向/块保留，绝不因另一方向命中边界而改写。
    # candidates_unread 只由 classify 的**预算分支**写入（预算返回才写 candidate），
    # 故 refs 非空 ⟹ 预算未读；reason/axis/stop_reason 已由 classify/rolling 记录，
    # 此处**绝不**用最终聚合 final_stop 覆盖（旧实现把「向后边界 + 向前 adjacent_pages
    # 预算」的预算未读伪标为 boundary，属审计 P1-C.6 缺陷）。
    if not unread_refs:
        # 无未读候选：干净完成或 dangling/结构边界型停止 → 无预算因果链，reason=None。
        unread_reason = None
        unread_axis = None
        unread_stop_reason = None
    scope_desc = _describe_unread_scope(final_stop, unread_refs, unread_axis)
    # §三.3：无未读候选且无停止原因的干净完成，绝不写 reason="error"；仅在有未读范围时
    # 携带真实原因（budget + 预算轴 + 因果停止）。空范围 → reason=None。
    unread = UnreadScope(
        reason=unread_reason,
        scope_desc=scope_desc, refs=unread_refs, budget_axis=unread_axis,
        stop_reason=unread_stop_reason)

    adopted_tuple = tuple(adopted.values())
    return ExpansionResult(
        seed=seed, adopted=adopted_tuple, candidates_unread=unread_refs,
        stop_reason=final_stop, unread_scope=unread,
        trace=ContextExpansionTrace(trace_id=trace_id, request=request,
                                    steps=tuple(steps)),
        budget_consumed=dict(consumed),
        seed_is_current_document=seed_is_current_document,
        seed_is_current_set=seed_is_current_set,
        adopted_relations=dict(adopted_relations),
        boundary_decisions=tuple(boundary_decisions),
        fragment_projections=tuple(fragments),
        boundary_status=boundary_status, boundary_eligible=boundary_eligible,
        boundary_reason=boundary_reason,
        boundary_verification=boundary_verification,
        topic_level=topic_level, topic_level_source=topic_level_source)


def _describe_unread_scope(stop_reason: str,
                           refs: tuple[ExpansionCandidateRef, ...],
                           axis: str | None) -> str:
    if not refs:
        return f"停止原因：{stop_reason}；无未读候选。"
    head = refs[0]
    loc = f"P{head.page_number} 第{head.block_index}块"
    if len(refs) > 1:
        loc += f" 等 {len(refs)} 块"
    axis_note = f"（预算轴 {axis}）" if axis else ""
    return f"停止原因：{stop_reason}；未读范围自 {loc}{axis_note}。"
