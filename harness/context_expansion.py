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

import uuid
from dataclasses import dataclass

from harness.evidence_reader import TOOL_NAME, EvidenceReadResult
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

# 交叉引用结构性标记（仅用于 trace/inputs 记录，不参与 LLM 判断）。
_REFERENCE_MARKERS = ("详见", "参见", "见下表", "见下表", "如下", "续表", "接上表")


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


def _estimate_bytes(text: str, structured_payload: dict | None = None) -> int:
    n = len(text.encode("utf-8"))
    if structured_payload:
        n += len(str(structured_payload).encode("utf-8"))
    return n


def _estimate_tokens(text: str) -> int:
    # 估算：中文约 1 字符 1 token，ASCII 约 4 字符 1 token；此处用保守的字符数近似。
    return max(1, len(text))


def _detect_reference_targets(text: str) -> tuple[str, ...]:
    return tuple(m for m in _REFERENCE_MARKERS if m in text)


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
class ExpansionStep:
    step_index: int
    action: str  # resolve_seed | inspect_bounded | stop
    tool_call: C.ToolCall | None
    inputs: dict
    outputs: tuple[str, ...]  # 实际读取的 evidence_ids（不含 material_id）
    stop_reason: str | None
    budget_remaining: dict


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


@dataclass(frozen=True)
class ExpansionResult:
    seed: ExpansionSeed
    adopted: tuple[EvidenceReadResult, ...]
    candidates_unread: tuple[ExpansionCandidateRef, ...]
    stop_reason: str
    unread_scope: UnreadScope
    trace: ContextExpansionTrace
    budget_consumed: dict


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def expand(request: ContextExpansionRequest, registry, *,
           run_id: str = "cli", route: str = "DIRECT_EVIDENCE") -> ExpansionResult:
    """对单个 seed 执行受控扩读，返回 ExpansionResult。

    读取链：verify_seed + 各方向 bounded ToolCall，全部经 ``registry.execute``。
    seed mismatch / 版本不符 → fail-closed（adopted 为空、stop_reason=authority）。
    """
    seed = request.seed
    budget = request.budget
    trace_id = uuid.uuid4().hex
    steps: list[ExpansionStep] = []
    adopted: dict[str, EvidenceReadResult] = {}
    candidates_unread: dict[str, ExpansionCandidateRef] = {}
    consumed: dict[str, int] = {a: 0 for a in BUDGET_AXES}
    stop_reason: str | None = None
    unread_reason: str | None = None
    unread_axis: str | None = None
    no_new_streak = 0

    def remaining(axis: str) -> int:
        return max(0, budget.as_limits()[axis] - consumed.get(axis, 0))

    def snapshot_remaining() -> dict:
        return {a: remaining(a) for a in BUDGET_AXES}

    def record_step(action: str, call: C.ToolCall | None, inputs: dict,
                    outputs: tuple[str, ...], step_stop: str | None) -> None:
        steps.append(ExpansionStep(
            step_index=len(steps), action=action, tool_call=call,
            inputs=dict(inputs), outputs=tuple(outputs),
            stop_reason=step_stop, budget_remaining=snapshot_remaining()))

    def issue(arguments: dict, idem: str) -> tuple[C.ToolCall, C.ToolResult]:
        call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME, arguments=dict(arguments),
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

    def classify(block: EvidenceReadResult, relation: str,
                 section_check: bool, page_check: bool) -> str:
        nonlocal stop_reason, unread_reason, unread_axis
        if block.evidence_id in adopted or block.evidence_id in candidates_unread:
            return "dup"
        if section_check and block.section_path and block.section_path != seed.section_path:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            stop_reason = stop_reason or "unrelated section boundary"
            unread_reason = unread_reason or "boundary"
            return "boundary"
        if page_check and abs(block.page_number - seed.page_number) > budget.adjacent_pages:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            stop_reason = stop_reason or "continuity break"
            unread_reason = unread_reason or "boundary"
            return "boundary"
        if len(adopted) >= budget.per_seed_cap:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            stop_reason = stop_reason or "hard budget (per_seed_cap)"
            unread_reason = unread_reason or "budget"
            unread_axis = unread_axis or "per_seed_cap"
            return "budget"
        add_bytes = _estimate_bytes(block.text, block.structured_payload)
        if consumed["max_bytes"] + add_bytes > budget.max_bytes:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            stop_reason = stop_reason or "hard budget (max_bytes)"
            unread_reason = unread_reason or "budget"
            unread_axis = unread_axis or "max_bytes"
            return "budget"
        add_tokens = _estimate_tokens(block.text)
        if consumed["max_tokens"] + add_tokens > budget.max_tokens:
            candidates_unread[block.evidence_id] = candidate(block, relation)
            stop_reason = stop_reason or "hard budget (max_tokens)"
            unread_reason = unread_reason or "budget"
            unread_axis = unread_axis or "max_tokens"
            return "budget"
        adopted[block.evidence_id] = block
        consumed["max_bytes"] += add_bytes
        consumed["max_tokens"] += add_tokens
        return "adopt"

    def do_read(mode: str, limit: int, axis: str, relation: str,
                section_check: bool, page_check: bool,
                extra: dict | None = None) -> None:
        nonlocal stop_reason, unread_reason, unread_axis, no_new_streak
        if stop_reason is not None:
            return
        args = base_args()
        args["mode"] = mode
        args["limit"] = int(limit)
        args["page_number"] = seed.page_number
        args["block_index"] = seed.block_index
        args.update(extra or {})
        call, res = issue(args, f"{trace_id}:{mode}:{len(steps)}")
        blocks: list[EvidenceReadResult] = []
        if res.status == "SUCCESS":
            blocks = [EvidenceReadResult.from_dict(d)
                      for d in res.data.get("blocks", [])]
        consumed[axis] += len(blocks)
        new_ids: list[str] = []
        for b in blocks:
            outcome = classify(b, relation, section_check, page_check)
            if outcome == "adopt":
                new_ids.append(b.evidence_id)
            elif outcome == "boundary":
                break
            # budget/dup → 继续尝试后续块
        record_step("inspect_bounded", call, args, tuple(new_ids),
                    stop_reason if stop_reason else None)
        if not new_ids:
            no_new_streak += 1
            if no_new_streak > budget.no_new_material_steps:
                stop_reason = stop_reason or "no new material"
                unread_reason = unread_reason or "budget"
        else:
            no_new_streak = 0

    # -- Step 0：resolve seed（fail-closed） --
    seed_args = base_args()
    seed_args.update({
        "mode": "verify_seed",
        "evidence_id": seed.evidence_id,
        "page_number": seed.page_number,
        "block_index": seed.block_index,
        "section_path": list(seed.section_path),
        "content_hash": seed.content_hash,
    })
    seed_call, seed_res = issue(seed_args, f"{trace_id}:seed:{seed.evidence_id}")
    if seed_res.status != "SUCCESS":
        msg = seed_res.message or seed_res.status
        record_step("resolve_seed", seed_call, {"seed_evidence_id": seed.evidence_id},
                    (), f"seed fail-closed: {msg}")
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
    consumed["per_seed_cap"] += 1
    consumed["max_bytes"] += _estimate_bytes(seed_block.text, seed_block.structured_payload)
    consumed["max_tokens"] += _estimate_tokens(seed_block.text)
    record_step("resolve_seed", seed_call, {"seed_evidence_id": seed.evidence_id},
                (seed_block.evidence_id,), None)

    # -- 各方向 --
    for direction in request.directions:
        if stop_reason is not None:
            break
        if direction == "adjacent_blocks":
            do_read("adjacent_before", budget.adjacent_blocks_before,
                    "adjacent_blocks_before", "adjacent", True, True)
            do_read("adjacent_after", budget.adjacent_blocks_after,
                    "adjacent_blocks_after", "adjacent", True, True)
        elif direction == "table_continuation":
            do_read("table_continuation", budget.table_continuation,
                    "table_continuation", "continuation", False, False)
        elif direction == "explicit_reference":
            markers = _detect_reference_targets(seed.text)
            do_read("explicit_reference", budget.explicit_references,
                    "explicit_references", "reference", False, False,
                    extra={"reference_target": " ".join(markers)} if markers else None)

    final_stop = stop_reason or "completed within source boundary"

    # 未读范围诚实显化：凡被记为 candidate 的块 + 停止原因合并。
    unread_refs = tuple(sorted(candidates_unread.values(),
                               key=lambda c: (c.page_number, c.block_index, c.evidence_id)))
    if unread_refs and unread_reason is None:
        unread_reason = "budget"
    scope_desc = _describe_unread_scope(final_stop, unread_refs, unread_axis)
    unread = UnreadScope(
        reason=unread_reason or ("boundary" if unread_refs else "error"),
        scope_desc=scope_desc, refs=unread_refs, budget_axis=unread_axis)

    adopted_tuple = tuple(adopted.values())
    return ExpansionResult(
        seed=seed, adopted=adopted_tuple, candidates_unread=unread_refs,
        stop_reason=final_stop, unread_scope=unread,
        trace=ContextExpansionTrace(trace_id=trace_id, request=request,
                                    steps=tuple(steps)),
        budget_consumed=dict(consumed))


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
