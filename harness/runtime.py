"""Phase 3 Batch B 受限研究循环 runtime（最小有界，非自由 ReAct）。

职责边界（编码前计划 §14/§15 + 用户修订）：
- 单题循环：动作选择 → 严格解析/一次修复 → Registry 执行 → 状态累计 → 预算检查 →
  补检/终止；不自由发散，不跨题共享状态；
- 所有工具经 `ToolRegistry.execute`（唯一执行入口），不允许 runtime 直连适配器；
- DB 动作的 standard_item_code / formula_id / target_period 由 Rules 从 RouterDecision.filters
  注入，LLM 不自造科目代码 / 公式 ID；
- 外部 fetch 成功后由 Rules 自动 SNAPSHOT_EXTERNAL（修订四），LLM 不直接发快照；
- LLM 不算数字、搜索摘要不作正式引用、不把「未检索到」写成「不存在」；
- 成功判定走 `harness.state.evaluate_success`（确定性主判据，gold 不进入运行时）；
- 每次研究事件经 `harness.trace.emit` 落盘 JSONL（可观测性硬要求）。

CLI: python -m harness.runtime --self-check
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
import uuid
from typing import Protocol

from harness import actions as A
from harness import aspects as ASP
from harness import entailment as E
from harness import policies as P
from harness import schema as H
from harness import state as S
from harness import structured_needs as SN
from harness import trace as T
from llm import client as llm_client
from routing import router as router_mod
from routing import schema as RS
from tools import contracts as TC
from tools import registry as R


# ---------------------------------------------------------------------------
# LLM 注入协议（生产用 RealResearchLLM，测试用 MockLLM）
# ---------------------------------------------------------------------------

class ResearchLLM(Protocol):
    def select_action(self, prompt_vars: dict) -> "llm_client.LLMResponse": ...
    def generate_answer(self, prompt_vars: dict) -> "llm_client.LLMResponse": ...


class RealResearchLLM:
    """生产 LLM：渲染 prompt 模板 → chat_with_usage，返回 LLMResponse。

    DeepSeek-V4-Pro 为推理模型，推理内容计入 output_tokens：若开启推理，会
    `finish_reason=max_tokens` 且正文为空（推理耗尽额度）→ ACTION_SCHEMA_INVALID。
    故结构化动作/答案调用显式 `thinking={"type": "disabled"}`，并给足 max_tokens 头部。
    """

    def __init__(self, model: str | None = None):
        self.model = model

    def select_action(self, prompt_vars: dict) -> "llm_client.LLMResponse":
        prompt = _render_template(llm_client.load_prompt("research_action_v1"), prompt_vars)
        return llm_client.chat_with_usage(
            [{"role": "user", "content": prompt}], model=self.model, max_tokens=2048,
            prompt_version="research_action_v1", thinking={"type": "disabled"})

    def generate_answer(self, prompt_vars: dict) -> "llm_client.LLMResponse":
        prompt = _render_template(llm_client.load_prompt("research_answer_v1"), prompt_vars)
        return llm_client.chat_with_usage(
            [{"role": "user", "content": prompt}], model=self.model, max_tokens=4096,
            prompt_version="research_answer_v1", thinking={"type": "disabled"})

    def evaluate_entailment_batch(self, prompt_vars: dict) -> "llm_client.LLMResponse":
        prompt = _render_template(llm_client.load_prompt("research_entailment_v1"), prompt_vars)
        return llm_client.chat_with_usage(
            [{"role": "user", "content": prompt}], model=self.model, max_tokens=4096,
            prompt_version="research_entailment_v1", thinking={"type": "disabled"})


def _render_template(template: str, vars: dict) -> str:
    """把 {{key}} 占位符替换为值（JSON 花括号原样保留，不受 .format 影响）。"""
    def _sub(m: "re.Match") -> str:
        return str(vars.get(m.group(1), ""))
    return re.sub(r"\{\{(\w+)\}\}", _sub, template)


# ---------------------------------------------------------------------------
# 状态构造 / 终止
# ---------------------------------------------------------------------------

def _new_state(need: RS.InformationNeed, route_result: RS.RouterResult, *,
               run_id: str, case_id: str, company_id: str, section_id: str | None,
               budget: P.ResearchBudget) -> H.ResearchState:
    st = H.ResearchState(
        run_id=run_id, case_id=case_id, question_id=need.need_id, company_id=company_id,
        section_id=section_id, original_question=need.question, need=need)
    st.route_result = route_result
    st.budget = budget.as_dict()
    # 契约优先派生 required-aspect（SECTION_CONTRACT / DATASET_MAPPING / TEXT_FALLBACK）。
    aspects = ASP.derive_required_aspects(
        need.question, section_id=section_id, case_id=case_id,
        question_id=need.need_id)
    st.required_aspects = [dataclasses.asdict(a) for a in aspects]
    st.aspect_source = ASP.aspect_source(aspects)
    return st


def _finalize(state: H.ResearchState, answer: H.ResearchAnswer | None,
              stop_reason: str | None) -> H.ResearchOutcome:
    """从终态 state 派生 (success, completion_status, stop_reason)。"""
    status = state.status
    if status == "COMPLETED":
        return H.ResearchOutcome(state, answer, True, "COMPLETED",
                                 stop_reason or "COMPLETED")
    if status == "COMPLETED_WITH_GAPS":
        return H.ResearchOutcome(state, answer, False, "COMPLETED_WITH_GAPS",
                                 stop_reason or "COMPLETED_WITH_GAPS")
    if status == "WAITING_HUMAN":
        return H.ResearchOutcome(state, answer, False, "UNRESOLVED",
                                 stop_reason or "WAITING_USER")
    if status == "FAILED":
        return H.ResearchOutcome(state, answer, False, "FAILED",
                                 stop_reason or "MODEL_OUTPUT_INVALID")
    if status == "BLOCKED":
        if stop_reason == "PATH_NOT_IMPLEMENTED":
            return H.ResearchOutcome(state, answer, False, "NOT_IMPLEMENTED",
                                     "PATH_NOT_IMPLEMENTED")
        return H.ResearchOutcome(state, answer, False, "UNRESOLVED",
                                 stop_reason or "BUDGET_EXHAUSTED")
    # 理论不可达（防漏）。
    return H.ResearchOutcome(state, answer, False, "FAILED", stop_reason or "FAILED")


# ---------------------------------------------------------------------------
# 账本 / 状态累计
# ---------------------------------------------------------------------------

# LLM 调用分类（动作选择 / 答案生成 / entailment）——分别记账 + 合计，供报告区分展示。
LLM_CATEGORY_ACTION = "action"
LLM_CATEGORY_ANSWER = "answer"
LLM_CATEGORY_ENTAILMENT = "entailment"


def _add_llm_usage(state: H.ResearchState, resp: "llm_client.LLMResponse",
                   category: str | None = None) -> None:
    """累计一次 LLM 调用到账本（合计 + 可选分类）。不重复计数：同一 resp 只调一次。"""
    u = state.usage
    u.llm_calls += 1
    u.llm_latency_ms += resp.latency_ms or 0
    if resp.input_tokens is not None and resp.output_tokens is not None:
        u.input_tokens += resp.input_tokens
        u.output_tokens += resp.output_tokens
    else:
        u.usage_unknown_calls += 1

    if category is not None:
        cat = u.llm_by_category.setdefault(category, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "unknown_calls": 0, "latency_ms": 0,
        })
        cat["calls"] += 1
        cat["latency_ms"] += resp.latency_ms or 0
        if resp.input_tokens is not None and resp.output_tokens is not None:
            cat["input_tokens"] += resp.input_tokens
            cat["output_tokens"] += resp.output_tokens
        else:
            cat["unknown_calls"] += 1


def _same_ref(a: RS.StructuredResultRef, b: RS.StructuredResultRef) -> bool:
    return (a.snapshot_id == b.snapshot_id
            and a.item_code == b.item_code
            and a.formula_id == b.formula_id
            and a.formula_version == b.formula_version
            and a.period == b.period)


def _apply_tool_result(state: H.ResearchState, result: TC.ToolResult,
                       budget: P.ResearchBudget) -> None:
    u = state.usage
    u.tool_calls += 1
    if result.tool_name == "search_evidence":
        u.local_searches += 1
    elif result.tool_name == "search_external_sources":
        u.external_searches += 1
    elif result.tool_name == "fetch_external_content":
        u.fetches += 1
    elif result.tool_name == "snapshot_external_source":
        u.snapshots += 1

    added = 0
    for eid in result.evidence_ids:
        if eid not in state.evidence_ids:
            state.evidence_ids.append(eid)
            added += 1
    for ref in result.structured_result_refs:
        if not any(_same_ref(r, ref) for r in state.structured_refs):
            state.structured_refs.append(ref)
            added += 1
    for sid in result.external_snapshot_ids:
        if sid not in state.external_snapshot_ids:
            state.external_snapshot_ids.append(sid)
            added += 1

    if added > 0:
        u.consecutive_no_new_evidence = 0
    else:
        u.consecutive_no_new_evidence += 1


def _make_call(tool_name: str, args: dict, state: H.ResearchState) -> TC.ToolCall:
    call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=args,
                       idempotency_key="", need_id=state.need.need_id,
                       batch_id=state.run_id)
    key = P.dedup_key(call)
    return TC.ToolCall(call_id=call.call_id, tool_name=tool_name, arguments=args,
                       idempotency_key=key, need_id=state.need.need_id,
                       batch_id=state.run_id)


def _inject_db_dims(out: dict, filters: dict) -> None:
    for k in ("snapshot_as_of_date", "target_period", "scope", "currency", "purpose"):
        if filters.get(k):
            out.setdefault(k, filters[k])


def _inject_args(action: str, args: dict, state: H.ResearchState,
                 route_result: RS.RouterResult) -> dict:
    """Rules 注入公司代码 + DB 动作的科目/公式/期间（LLM 不自造）。"""
    out = dict(args)
    filters = route_result.decision.filters if route_result.decision else {}
    tool = A.ACTION_TOOL[action]

    if tool in ("search_evidence", "lookup_company_field", "lookup_financial_metric",
                "compare_financial_periods"):
        out.setdefault("company_id", state.company_id)

    if action == "LOOKUP_COMPANY_FIELD":
        out["standard_item_code"] = filters.get("standard_item_code", "")
        _inject_db_dims(out, filters)
    elif action == "LOOKUP_FINANCIAL_METRIC":
        out["formula_id"] = filters.get("formula_id", "")
        if filters.get("formula_version"):
            out["formula_version"] = filters["formula_version"]
        _inject_db_dims(out, filters)
    elif action == "COMPARE_FINANCIAL_PERIODS":
        out["formula_id"] = filters.get("formula_id", "")
        if filters.get("formula_version"):
            out["formula_version"] = filters["formula_version"]
        _inject_db_dims(out, filters)
    return out


# ---------------------------------------------------------------------------
# 外部 fetch 成功 → Rules 自动 snapshot
# ---------------------------------------------------------------------------

def _auto_snapshot(state: H.ResearchState, fetch_action: H.ActionCall,
                   fetch_result: TC.ToolResult, registry: R.ToolRegistry,
                   route: str, budget: P.ResearchBudget) -> None:
    """fetch SUCCESS 且 content_text 非空 → 自动固化不可变快照（修订四）。"""
    data = fetch_result.data or {}
    content_text = data.get("content_text")
    if not content_text:
        return
    snap_args = {
        "company_id": state.company_id,
        "canonical_url": data.get("canonical_url") or fetch_action.arguments.get("url", ""),
        "content_text": content_text,
    }
    for k in ("original_url", "provider", "query", "title", "snippet", "published_at",
              "content_type", "content_hash", "source_grade", "file_hash"):
        if data.get(k) is not None:
            snap_args[k] = data[k]
    if data.get("http_status") is not None:
        snap_args["http_status"] = data["http_status"]
    if data.get("page_count") is not None:
        snap_args["page_count"] = data["page_count"]

    snap_call = _make_call("snapshot_external_source", snap_args, state)
    snap_result = registry.execute(snap_call, route=route, run_id=state.run_id,
                                   max_retries=0)
    state.tool_history.append(H.ToolCallRecord(
        call=snap_call, result=snap_result, elapsed_ms=snap_result.latency_ms, auto=True))
    _apply_tool_result(state, snap_result, budget)
    T.emit(state.run_id, state.question_id, "SNAPSHOT_AUTO",
           {"status": snap_result.status, "source_snapshot_id":
            (snap_result.external_snapshot_ids or [None])[0]})


# ---------------------------------------------------------------------------
# prompt 变量构造
# ---------------------------------------------------------------------------

def _allowed_actions_for_route(route: str) -> list[str]:
    acts: list[str] = []
    for a in A.LLM_SELECTABLE_ACTIONS:
        if a in A.TERMINAL_ACTIONS:
            acts.append(a)
        elif a in A.ACTION_ROUTES and route in A.ACTION_ROUTES[a]:
            acts.append(a)
    return acts


def _evidence_summary(state: H.ResearchState) -> str:
    lines: list[str] = []
    if state.evidence_ids:
        lines.append("本地证据 evidence_id（可引用）: " + ", ".join(state.evidence_ids))
    if state.structured_refs:
        for r in state.structured_refs:
            key = r.formula_id or r.item_code or "?"
            val = r.display_value or r.raw_value or "?"
            lines.append(f"结构化结果 {key} period={r.period} value={val}")
    if state.external_snapshot_ids:
        lines.append("外部快照 source_snapshot_id（可引用）: "
                     + ", ".join(state.external_snapshot_ids))
    return "\n".join(lines) if lines else "（无）"


def _search_candidates(state: H.ResearchState) -> str:
    urls: list[str] = []
    for rec in state.tool_history:
        if rec.result.tool_name == "search_external_sources":
            for r in (rec.result.data or {}).get("results", []):
                if r.get("url"):
                    urls.append(r["url"])
    if not urls:
        return ""
    return "FETCH_EXTERNAL 的 url 只能取自以下候选：\n" + "\n".join(f"- {u}" for u in urls[:10])


def _budget_left(state: H.ResearchState, budget: P.ResearchBudget) -> str:
    u = state.usage
    return (f"rounds {u.rounds}/{budget.max_rounds}, tool_calls {u.tool_calls}/"
            f"{budget.max_tool_calls}, local {u.local_searches}/{budget.max_local_searches}, "
            f"external {u.external_searches}/{budget.max_external_searches}, "
            f"fetch {u.fetches}/{budget.max_fetches}, elapsed {u.elapsed_ms}ms")


def _action_prompt_vars(state: H.ResearchState, route: str, reason: str,
                        budget: P.ResearchBudget) -> dict:
    return {
        "company_id": state.company_id,
        "section_id": state.section_id or "",
        "question": state.original_question,
        "route": route,
        "route_reason": reason,
        "current_goal": state.current_goal or state.original_question,
        "round": state.usage.rounds,
        "allowed_actions": "\n".join(f"- {a}" for a in _allowed_actions_for_route(route)),
        "evidence_summary": _evidence_summary(state),
        "unresolved": "\n".join(f"- {x}" for x in state.unresolved_items) or "（无）",
        "budget_left": _budget_left(state, budget),
        "search_candidates": _search_candidates(state),
        "required_aspects": _aspects_text(state),
        "rejected_duplicates": _rejected_duplicates_text(state),
        "already_inspected": _already_inspected_text(state),
        "must_converge": "是（本回合是最后一个可用回合，必须选 ANSWER/STOP_WITH_GAP/REQUEST_HUMAN）"
                         if _must_converge(state, budget) else "否",
    }


def _available_material(state: H.ResearchState) -> str:
    parts: list[str] = []
    # 结构化结果：value 是代码算好的精确值（StructuredResult 注入），模型原样照抄，
    # 禁止换算单位、四舍五入、重新计算。
    if state.structured_refs:
        lines: list[str] = []
        for r in state.structured_refs:
            val = r.display_value if r.display_value is not None else r.raw_value
            unit = r.unit or ""
            val_str = f"{val} {unit}".strip() if val is not None else "（值不可用）"
            if r.item_code:
                lines.append(f'  {{"snapshot_id": "{r.snapshot_id}", "item_code": "{r.item_code}", '
                             f'"period": "{r.period}", "value": "{val_str}"}}')
            else:
                lines.append(f'  {{"snapshot_id": "{r.snapshot_id}", "formula_id": "{r.formula_id}", '
                             f'"formula_version": "{r.formula_version}", "period": "{r.period}", '
                             f'"value": "{val_str}"}}')
        parts.append("结构化结果（value 为代码算好的精确值，原样照抄，禁止换算/改写）：\n"
                     + "\n".join(lines))
    if state.evidence_ids:
        parts.append("本地证据 evidence_id：" + ", ".join(state.evidence_ids))
    # 已取得正文/摘要：数字只能从这里逐字抄（或抄上面结构化 value），不得凭记忆补数。
    if state.inspected_evidence:
        ev_lines: list[str] = []
        for eid, mat in state.inspected_evidence.items():
            kind = "摘要" if mat.is_snippet else "全文"
            ev_lines.append(
                f"### evidence_id={eid} [{kind}] doc={mat.document_id or '-'} "
                f"page={mat.page_number} period={mat.report_period or '-'}\n"
                f"{E.bounded_text(mat.text)}")
        parts.append("已取得正文/摘要（数字只能逐字抄这里，或抄上面结构化 value）：\n"
                     + "\n".join(ev_lines))
    if state.external_snapshot_ids:
        parts.append("外部快照 source_snapshot_id：" + ", ".join(state.external_snapshot_ids))
    return "\n\n".join(parts) if parts else "（无）"


def _aspects_text(state: H.ResearchState) -> str:
    rows = []
    for a in state.required_aspects:
        rows.append(f'- {a.get("aspect_id", "")}: {a.get("text", "")}')
    return "\n".join(rows) if rows else "（无）"


def _rejected_duplicates_text(state: H.ResearchState) -> str:
    rej = getattr(state, "rejected_duplicate_actions", None) or []
    if not rej:
        return "（无）"
    return "\n".join(
        f'- round {r.get("round", "?")} {r.get("action", "?")} {r.get("tool", "?")}'
        for r in rej)


def _already_inspected_text(state: H.ResearchState) -> str:
    """已取得全文的 evidence_id（inspect 后）——不再建议对其发起 INSPECT_EVIDENCE。"""
    insp = [eid for eid, m in (state.inspected_evidence or {}).items() if not m.is_snippet]
    if not insp:
        return "（无）"
    return ", ".join(insp)


def _must_converge(state: H.ResearchState, budget: P.ResearchBudget) -> bool:
    """末回合且已有可引用材料 → 必须收敛到终态动作（ANSWER/STOP_WITH_GAP/REQUEST_HUMAN）。"""
    last_round = state.usage.rounds >= budget.max_rounds
    has_material = bool(state.evidence_ids or state.structured_refs
                        or state.external_snapshot_ids)
    return last_round and has_material


def _answer_prompt_vars(state: H.ResearchState, route: str) -> dict:
    return {
        "company_id": state.company_id,
        "section_id": state.section_id or "",
        "question": state.original_question,
        "route": route,
        "available_material": _available_material(state),
        "unresolved": "\n".join(f"- {x}" for x in state.unresolved_items) or "（无）",
        "required_aspects": _aspects_text(state),
    }


# ---------------------------------------------------------------------------
# 答案解析
# ---------------------------------------------------------------------------

_KNOWN_CIT_FIELDS = ("ref_type", "evidence_id", "snapshot_id", "item_code", "formula_id",
                     "formula_version", "period", "source_snapshot_id", "page_number")


def _citation_from_dict(d: dict) -> H.CitationRef:
    ref_type = d.get("ref_type")
    if ref_type not in H.CITATION_TYPES:
        raise ValueError(f"非法 ref_type: {ref_type}")
    return H.CitationRef(**{k: d[k] for k in _KNOWN_CIT_FIELDS if k in d})


def parse_answer(raw: str, question_id: str) -> H.ResearchAnswer:
    """把 LLM 原始输出解析为 ResearchAnswer；非法抛 ValueError（fail-closed）。"""
    cleaned = A._repair_json(raw)
    if cleaned is None:
        raise ValueError("无法从输出提取 JSON")
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("答案必须为 JSON object")

    claims: list[H.Claim] = []
    for i, c in enumerate(data.get("claims", [])):
        kind = c.get("kind", "fact")
        if kind not in H.CLAIM_KINDS:
            raise ValueError(f"claim[{i}] kind 非法: {kind}")
        citation_refs = c.get("citation_refs", [])
        if not isinstance(citation_refs, list) or not all(
                isinstance(x, int) and not isinstance(x, bool) for x in citation_refs):
            raise ValueError(f"claim[{i}] citation_refs 必须为 int 数组")
        claims.append(H.Claim(
            claim_id=str(c.get("claim_id") or f"c{i}"),
            text=str(c.get("text", "")),
            kind=kind,
            citation_refs=citation_refs))

    citations = [_citation_from_dict(c) for c in data.get("citations", [])]

    aspects = []
    for a in data.get("aspects", []):
        if not isinstance(a, dict):
            continue
        aspects.append(H.AspectAnswer(
            aspect_id=str(a.get("aspect_id", "")),
            text=str(a.get("text", "")),
            claim_ids=[str(c) for c in a.get("claim_ids", [])],
        ))

    return H.ResearchAnswer(
        question_id=question_id,
        answer_text=str(data.get("answer_text", "")),
        claims=claims,
        citations=citations,
        unresolved_items=[str(x) for x in data.get("unresolved_items", [])],
        confidence=data.get("confidence", "low") if data.get("confidence") in H.CONFIDENCE_LEVELS else "low",
        aspects=aspects,
    )


def _generate_answer(state: H.ResearchState, llm: ResearchLLM, route: str,
                     budget: P.ResearchBudget) -> H.ResearchAnswer | None:
    resp = llm.generate_answer(_answer_prompt_vars(state, route))
    _add_llm_usage(state, resp, LLM_CATEGORY_ANSWER)
    try:
        answer = parse_answer(resp.text, state.question_id)
        state.unresolved_items = list(dict.fromkeys(
            state.unresolved_items + (answer.unresolved_items or [])))
        return answer
    except Exception:
        return None


def _merge_gaps(state: H.ResearchState, ev: dict) -> None:
    """把成功判定返回的缺口（未覆盖方面/不支持 claim）写回 unresolved_items（去重）。"""
    new: list[str] = []
    for u in ev.get("uncovered_aspects", []):
        new.append(f"未覆盖方面: {u}")
    for c in ev.get("unsupported_claims", []):
        new.append(f"引用不支持结论: {c}")
    state.unresolved_items = list(dict.fromkeys(state.unresolved_items + new))


# 答案派生缺口的统一前缀（_merge_gaps 写入；跨 ANSWER 重置时按此清除）。
_ANSWER_DERIVED_GAP_PREFIXES = ("未覆盖方面:", "引用不支持结论:")


def _reset_answer_derived_state(state: H.ResearchState) -> None:
    """清除上一答案派生的临时判定，保留研究历史（跨 ANSWER 状态污染修复）。

    每次 ANSWER 评估前调用：上一答案的 UNSUPPORTED / 未覆盖方面是「上一答案版本的临时
    缺口」，不应残留到最终判定。保留：工具历史、已捕获正文（inspected_evidence）、
    已拒绝重复动作、已取得可引用材料（evidence/structured/external）、以及非答案派生的
    unresolved_items（STOP_WITH_GAP / REQUEST_HUMAN 等研究历史缺口）。
    """
    state.unsupported_claims = []
    state.entailment_verdicts = []
    state.entailment_evaluator_failed = False
    state.unresolved_items = [
        x for x in state.unresolved_items
        if not x.startswith(_ANSWER_DERIVED_GAP_PREFIXES)
    ]


# ---------------------------------------------------------------------------
# 结构化子 need（§三：原始财务问题接入 Financial Snapshot）
# ---------------------------------------------------------------------------

def _run_structured_subneeds(state: H.ResearchState, context: RS.RouteContext,
                             registry: R.ToolRegistry, budget: P.ResearchBudget,
                             run_id: str, trace_enabled: bool) -> None:
    """原始问题 → required aspects → 数值/指标方面 → 结构化子 need → Router/DB resolver
    → ToolRegistry → StructuredResultRef 汇入 state.structured_refs（确定性，Rules 内部）。

    - 父路由保持不变（state.route_result 不动）；子 need 路由单独记录在
      state.structured_subneeds，不篡改 Track B。
    - 仅可精确表达的 field/metric 派生；数值方面无法精确表达 →
      state.semantic_mismatches_rejected（不冒充 DB 结果）。
    - 目标报告期取自原始问题（如「2025年」→ 2025-12-31），与 snapshot_as_of_date 分离。
    - 数字来自 StructuredResult（LLM 只解读，不重新算数）。
    """
    derivation = SN.derive_structured_subneeds(
        state.original_question, state.required_aspects)
    state.semantic_mismatches_rejected = [
        dataclasses.asdict(r) for r in derivation.rejected_aspects]
    qperiod = SN.target_period(state.original_question)

    records: list[dict] = []
    for sn in derivation.subneeds:
        rec = {
            "sub_need_id": sn.sub_need_id, "source": sn.source,
            "aspect_id": sn.aspect_id, "text": sn.text,
            "target_type": sn.target_type,
            "standard_item_code": sn.standard_item_code,
            "formula_id": sn.formula_id,
            "formula_version": sn.formula_version,
            "route": None, "reason_code": None, "status": "NOT_ROUTED",
            "n_structured": 0, "period": None, "error_code": None,
        }
        expr = SN.target_expression(sn)
        sub_need = RS.InformationNeed(
            need_id=f"{state.question_id}__{sn.sub_need_id}",
            section_id=state.section_id, question=expr,
            required_evidence_types=[], required_source_types=[],
            time_scope=None, priority="normal", depends_on=[])
        try:
            rr = router_mod.route(sub_need, context)
        except Exception as e:  # noqa: BLE001 — 路由审计落盘失败，诚实标记不 crash
            rec["status"] = "ROUTE_FAILED"
            rec["error_code"] = f"ROUTER_FAILED:{type(e).__name__}"
            records.append(rec)
            continue
        if rr.status != "DECIDED" or rr.decision is None:
            rec["status"] = "ROUTE_FAILED"
            rec["error_code"] = rr.error_code or rr.status
            records.append(rec)
            continue
        rec["route"] = rr.decision.route
        rec["reason_code"] = rr.decision.reason_code
        if rr.decision.route != "DB_LOOKUP":
            rec["status"] = "NOT_DB_ROUTED"
            records.append(rec)
            continue
        tool_name, args = SN.db_tool_args(state.company_id, rr.decision.filters)
        # 目标报告期取自原始问题（子 need 表达式「净利率」不含年份）。
        if qperiod is not None:
            args["target_period"] = qperiod
        call = TC.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=args,
            idempotency_key=P.dedup_key(TC.ToolCall(
                call_id="", tool_name=tool_name, arguments=args,
                idempotency_key="", need_id=sub_need.need_id, batch_id=state.run_id)),
            need_id=sub_need.need_id, batch_id=state.run_id)
        result = registry.execute(call, route="DB_LOOKUP", run_id=state.run_id,
                                  max_retries=budget.max_retries_per_call)
        state.tool_history.append(H.ToolCallRecord(
            call=call, result=result, elapsed_ms=result.latency_ms, auto=True))
        _apply_tool_result(state, result, budget)
        if result.structured_result_refs:
            rec["status"] = "RESOLVED"
            rec["n_structured"] = len(result.structured_result_refs)
            rec["period"] = result.structured_result_refs[0].period
        else:
            rec["status"] = "UNAVAILABLE"
            rec["error_code"] = result.error_code
        records.append(rec)
        if trace_enabled:
            T.emit(run_id, state.question_id, "STRUCTURED_SUBNEED",
                   {"sub_need_id": sn.sub_need_id, "route": rec["route"],
                    "status": rec["status"], "n_structured": rec["n_structured"],
                    "error_code": rec["error_code"]})
    state.structured_subneeds = records


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

def run_question(*, need: RS.InformationNeed, route_result: RS.RouterResult,
                 registry: R.ToolRegistry, llm: ResearchLLM,
                 budget: P.ResearchBudget = P.DEFAULT_BUDGET,
                 run_id: str, case_id: str, company_id: str,
                 section_id: str | None = None,
                 trace_enabled: bool = True,
                 context: RS.RouteContext | None = None) -> H.ResearchOutcome:
    """跑单题受限研究循环，返回 ResearchOutcome（终态 state + answer）。"""
    state = _new_state(need, route_result, run_id=run_id, case_id=case_id,
                       company_id=company_id, section_id=section_id, budget=budget)
    t0 = time.perf_counter()

    if route_result.status != "DECIDED" or route_result.decision is None:
        S.set_status(state, "BLOCKED", "PATH_NOT_IMPLEMENTED")
        return _finalize(state, None, "PATH_NOT_IMPLEMENTED")

    route = route_result.decision.route
    reason = route_result.decision.reason_code or ""
    S.set_status(state, "ROUTED")
    if trace_enabled:
        T.emit(run_id, need.need_id, "ROUTED", {"route": route, "reason_code": reason})

    # §三：结构化子 need（原始财务问题接入 Financial Snapshot，Rules 内部确定性）。
    # 仅在注入 RouteContext（真实快照路径）时运行；父路由判定保持 Phase 2 原样。
    if context is not None:
        _run_structured_subneeds(state, context, registry, budget, run_id, trace_enabled)

    answer: H.ResearchAnswer | None = None
    stop_reason: str | None = None

    while True:
        state.usage.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        over = P.check_budget(state, budget)
        if over is not None:
            stop_reason = over
            break
        if state.usage.rounds >= budget.max_rounds:
            stop_reason = "BUDGET_ITERATIONS"
            break

        state.usage.rounds += 1

        # 1. 动作选择（一次修复）
        raw = llm.select_action(_action_prompt_vars(state, route, reason, budget))
        _add_llm_usage(state, raw, LLM_CATEGORY_ACTION)
        action: H.ActionCall | None
        try:
            action = A.parse_action(raw.text)
        except A.ActionParseError as e:
            action = A.repair_once(raw.text, str(e))
        if action is None:
            stop_reason = "ACTION_SCHEMA_INVALID"
            S.set_status(state, "FAILED", stop_reason)
            break
        state.actions.append(action)
        if trace_enabled:
            T.emit(run_id, need.need_id, "ACTION",
                   {"round": state.usage.rounds, "action": action.action,
                    "arguments": action.arguments})

        # 2. 终态动作
        if action.action == "ANSWER":
            # 每次 ANSWER 评估前清除上一答案派生的临时判定（unsupported_claims /
            # entailment_verdicts / evaluator 失败 / 未覆盖方面缺口），保留研究历史；
            # 答案版本号递增，本轮评估结果带 answer_revision（最终判定只读最新版本）。
            _reset_answer_derived_state(state)
            state.answer_revision += 1
            answer = _generate_answer(state, llm, route, budget)
            if answer is None:
                stop_reason = "MODEL_OUTPUT_INVALID"
                S.set_status(state, "FAILED", stop_reason)
                break
            # 回填 evidence 引用页码（只补空，不重写）。
            E.backfill_page_numbers(answer, state)
            # G3 确定性预检 + G4 批量 entailment（每问 1 次调用，fail-closed）。
            prechecks = E.deterministic_prechecks(state, answer)
            if hasattr(llm, "evaluate_entailment_batch"):
                try:
                    state.entailment_verdicts = E.evaluate_entailment_batch(
                        state, answer, llm, prechecks,
                        on_response=lambda r: _add_llm_usage(
                            state, r, LLM_CATEGORY_ENTAILMENT))
                    for v in state.entailment_verdicts:
                        if v.verdict == "UNSUPPORTED":
                            state.unsupported_claims.append(
                                f"{v.claim_id}: entailment UNSUPPORTED: {v.reason}")
                    if trace_enabled:
                        T.emit(run_id, need.need_id, "ENTAILMENT",
                               {"answer_revision": state.answer_revision,
                                "verdicts": [dataclasses.asdict(v)
                                             for v in state.entailment_verdicts]})
                except Exception as e:  # noqa: BLE001 — 记失败（fail-closed），不吞错
                    state.entailment_evaluator_failed = True
                    if trace_enabled:
                        T.emit(run_id, need.need_id, "ENTAILMENT",
                               {"answer_revision": state.answer_revision,
                                "failed": True, "error": str(e)})
            S.set_status(state, "ANSWER_READY")
            ev = S.evaluate_success(state, answer)
            if ev["completion_status"] == "COMPLETED":
                S.set_status(state, "COMPLETED", "COMPLETED")
                stop_reason = "COMPLETED"
            elif ev["completion_status"] == "COMPLETED_WITH_GAPS":
                # 缺口写回 unresolved；预算有空间则继续补检，否则带缺口结束。
                _merge_gaps(state, ev)
                if P.budget_has_room(state, budget):
                    S.set_status(state, "RESEARCHING")
                    # 不 break：进入下一轮补检。
                else:
                    S.set_status(state, "COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS")
                    stop_reason = "COMPLETED_WITH_GAPS"
            elif ev["completion_status"] == "FAILED":
                S.set_status(state, "FAILED", "MODEL_OUTPUT_INVALID")
                stop_reason = "MODEL_OUTPUT_INVALID"
            else:
                S.set_status(state, "BLOCKED", "PATH_NOT_IMPLEMENTED")
                stop_reason = "PATH_NOT_IMPLEMENTED"
            if trace_enabled:
                T.emit(run_id, need.need_id, "ANSWER",
                       {"answer_revision": state.answer_revision,
                        "completion_status": state.status,
                        "claims": len(answer.claims),
                        "citations": len(answer.citations),
                        "unresolved": state.unresolved_items,
                        "uncovered_aspects": ev.get("uncovered_aspects", [])})
            if state.status in ("COMPLETED", "COMPLETED_WITH_GAPS", "FAILED", "BLOCKED"):
                break
            continue

        if action.action == "STOP_WITH_GAP":
            gap = (action.arguments or {}).get("reason", "")
            if gap:
                state.unresolved_items.append(gap)
            S.set_status(state, "COMPLETED_WITH_GAPS", "COMPLETED_WITH_GAPS")
            stop_reason = "COMPLETED_WITH_GAPS"
            break

        if action.action == "REQUEST_HUMAN":
            r = (action.arguments or {}).get("reason", "")
            if r:
                state.unresolved_items.append(r)
            S.set_status(state, "WAITING_HUMAN", "WAITING_USER")
            stop_reason = "WAITING_USER"
            break

        # 3. 执行工具动作
        S.set_status(state, "RESEARCHING")
        tool_name = A.ACTION_TOOL[action.action]
        if tool_name is None:
            stop_reason = "ACTION_SCHEMA_INVALID"
            S.set_status(state, "FAILED", stop_reason)
            break
        args = _inject_args(action.action, action.arguments, state, route_result)
        call = _make_call(tool_name, args, state)
        key = call.idempotency_key
        # 重复动作去重：同 tool + 同参数已执行过 → 拒绝（不调 registry、不写 tool_history）。
        if key in state.executed_action_keys:
            state.usage.consecutive_no_new_evidence += 1
            state.rejected_duplicate_actions.append({
                "round": state.usage.rounds, "action": action.action,
                "tool": tool_name, "key": key, "source": "model_proposed"})
            if trace_enabled:
                T.emit(run_id, need.need_id, "DUPLICATE_ACTION",
                       {"round": state.usage.rounds, "action": action.action,
                        "tool": tool_name, "key": key, "tool_called": False})
            continue
        result = registry.execute(call, route=route, run_id=state.run_id,
                                  max_retries=budget.max_retries_per_call)
        state.executed_action_keys.append(key)
        state.tool_history.append(H.ToolCallRecord(
            call=call, result=result, elapsed_ms=result.latency_ms))
        _apply_tool_result(state, result, budget)
        # 捕获可校验正文/摘要（供 G3 数字/口径预检 + G4 entailment）。
        E.capture_inspected(state, result)
        if trace_enabled:
            T.emit(run_id, need.need_id, "TOOL_RESULT",
                   {"tool": result.tool_name, "status": result.status,
                    "error_code": result.error_code,
                    "evidence_ids": result.evidence_ids,
                    "structured": len(result.structured_result_refs),
                    "snapshots": result.external_snapshot_ids})

        # 4. fetch 成功 → Rules 自动快照
        if (result.tool_name == "fetch_external_content"
                and result.status == "SUCCESS"):
            _auto_snapshot(state, action, result, registry, route, budget)

        # 5. 致命工具错误 → 停止
        if result.is_error():
            stop_reason = "FATAL_TOOL_ERROR"
            S.set_status(state, "FAILED", stop_reason)
            break

    # 循环结束（预算耗尽 / 连续无新证据）但无终态。
    if state.status not in H.QUESTION_STATUSES[4:]:
        if answer is not None:
            # 已有带缺口答案（可引用）但预算耗尽无法继续补检 → 带缺口结束。
            S.set_status(state, "COMPLETED_WITH_GAPS", stop_reason or "COMPLETED_WITH_GAPS")
            stop_reason = "COMPLETED_WITH_GAPS"
        else:
            S.set_status(state, "BLOCKED", stop_reason or "BUDGET_EXHAUSTED")

    state.usage.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    outcome = _finalize(state, answer, stop_reason)
    if trace_enabled:
        T.emit(run_id, need.need_id, "STOP",
               {"status": state.status, "stop_reason": outcome.stop_reason,
                "success": outcome.success, "completion_status": outcome.completion_status,
                "tool_calls": state.usage.tool_calls, "llm_calls": state.usage.llm_calls,
                "input_tokens": state.usage.input_tokens,
                "output_tokens": state.usage.output_tokens,
                "llm_latency_ms": state.usage.llm_latency_ms,
                "llm_by_category": state.usage.llm_by_category,
                "elapsed_ms": state.usage.elapsed_ms})
    return outcome


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class _MockLLM:
    """CLI 自检用：直接选 STOP_WITH_GAP，走通循环不触发工具/网络。"""

    def select_action(self, prompt_vars: dict) -> "llm_client.LLMResponse":
        return llm_client.LLMResponse(
            text='{"action": "STOP_WITH_GAP", "arguments": {"reason": "自检样例缺口"}}',
            input_tokens=10, output_tokens=20, latency_ms=1, model="mock",
            call_id="mock", finish_reason="stop")

    def generate_answer(self, prompt_vars: dict) -> "llm_client.LLMResponse":
        return llm_client.LLMResponse(text="", input_tokens=0, output_tokens=0,
                                      latency_ms=1, model="mock", call_id="mock",
                                      finish_reason="stop")


def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.runtime", description="受限研究循环自检")
    parser.add_argument("--self-check", action="store_true",
                        help="以 mock LLM 走通一轮循环（不发起真实 LLM/工具）")
    args = parser.parse_args(argv)

    if args.self_check:
        # 模板渲染 smoke（无遗留 {{ 占位符）。
        act_tpl = llm_client.load_prompt("research_action_v1")
        ans_tpl = llm_client.load_prompt("research_answer_v1")
        sample_vars = {"company_id": "300750", "section_id": "company", "question": "q",
                       "route": "STANDARD_RAG", "route_reason": "r", "current_goal": "g",
                       "round": 1, "allowed_actions": "- SEARCH_LOCAL\n- ANSWER",
                       "evidence_summary": "（无）", "unresolved": "（无）",
                       "budget_left": "rounds 1/5", "search_candidates": "",
                       "available_material": "（无）", "required_aspects": "（无）",
                       "rejected_duplicates": "（无）", "already_inspected": "（无）",
                       "must_converge": "否"}
        act_rendered = _render_template(act_tpl, sample_vars)
        ans_rendered = _render_template(ans_tpl, sample_vars)
        no_leftover = "{{" not in act_rendered and "{{" not in ans_rendered

        # 答案解析 smoke。
        sample_answer = ('{"answer_text": "实控人为曾毓群", '
                         '"claims": [{"claim_id": "c1", "text": "实控人为曾毓群", '
                         '"kind": "fact", "citation_refs": [0]}], '
                         '"citations": [{"ref_type": "external", '
                         '"source_snapshot_id": "ext1"}], '
                         '"unresolved_items": [], "confidence": "high"}')
        parsed_ok = False
        try:
            a = parse_answer(sample_answer, "q1")
            parsed_ok = a.claims and a.claims[0].citation_refs == [0]
        except Exception:
            parsed_ok = False

        # 走通一轮循环（STOP_WITH_GAP，无工具/网络）。
        need = RS.InformationNeed(
            need_id="self-q", section_id="company", question="自检问题",
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="P0", depends_on=[])
        budget = RS.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1,
                                    fusion_k=1, context_k=1, timeout_ms=1000)
        decision = RS.RouteDecision(
            need_id="self-q", route="STANDARD_RAG", reason_code="SECTION_TOPIC_SYNTHESIS",
            filters={}, budget=budget, fallback_routes=[], decided_by="rule",
            rule_version=RS.RULE_VERSION, confidence="high")
        rr = RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                             trace_id="self")
        outcome = run_question(need=need, route_result=rr, registry=R.ToolRegistry(),
                               llm=_MockLLM(), run_id="self", case_id="self",
                               company_id="300750", section_id="company",
                               trace_enabled=False)

        print(json.dumps({
            "templates_render_no_leftover": no_leftover,
            "answer_parse_ok": parsed_ok,
            "loop_smoke": {
                "status": outcome.state.status,
                "completion_status": outcome.completion_status,
                "stop_reason": outcome.stop_reason,
                "success": outcome.success,
                "unresolved": outcome.state.unresolved_items,
            },
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
