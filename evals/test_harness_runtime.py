"""Eval: harness 受限研究循环 runtime —— Phase 3 Batch B commit 4。

用法: python -m evals.test_harness_runtime

断言（mock LLM + fake Registry，无真实 LLM/工具/网络）：
- _render_template：{{key}} 替换、JSON 花括号原样保留；
- parse_answer：合法解析 / 非法 ref_type fail-closed；
- run_question 全路径：SEARCH_LOCAL→ANSWER 成功（evidence_id 可回查、状态累计）；
- 外部 fetch 成功 → Rules 自动 snapshot（external_snapshot_id 固化、fetch+snapshot 两次工具）；
- 引用不可回查（虚构 evidence_id）→ FAILED MODEL_OUTPUT_INVALID；
- STOP_WITH_GAP → COMPLETED_WITH_GAPS；REQUEST_HUMAN → WAITING_HUMAN；
- 预算耗尽 → BLOCKED（BUDGET_ITERATIONS / BUDGET_TOOL_CALLS）；
- 路由未 DECIDED → NOT_IMPLEMENTED。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import runtime as RT
from harness import schema as H
from harness import policies as P
from harness import trace as T
from llm import client as llm_client
from routing import schema as RS
from tools import contracts as TC
from tools import registry as R


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _router_result(route: str) -> RS.RouterResult:
    budget = RS.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1,
                                fusion_k=1, context_k=1, timeout_ms=1000)
    decision = RS.RouteDecision(
        need_id="n1", route=route, reason_code="TEST", filters={}, budget=budget,
        fallback_routes=[], decided_by="rule", rule_version=RS.RULE_VERSION,
        confidence="high")
    return RS.RouterResult(status="DECIDED", decision=decision, error_code=None,
                           trace_id="t")


def _spec(name, required, props, routes):
    return TC.ToolSpec(
        name=name, version="v1", description="fake",
        input_schema={"type": "object", "additionalProperties": False,
                      "required": required, "properties": props},
        output_schema={"type": "object"}, allowed_routes=routes,
        max_results=10, timeout_ms=1000, retry_policy="none", cost_class="local")


def _fake_registry() -> R.ToolRegistry:
    audit = Path(tempfile.mkdtemp())
    reg = R.ToolRegistry(audit_dir=audit)
    reg.register(_spec("search_evidence", ["company_id", "query"],
                       {"company_id": {"type": "string"}, "query": {"type": "string"},
                        "k": {"type": "integer"}},
                       ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL")),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="search_evidence", tool_version="v1",
                     status="SUCCESS", data={"evidence_count": 1, "items": [
                         {"evidence_id": "e1", "source_name": "s", "page_number": 3,
                          "evidence_type": "paragraph", "snippet": "实际控制人为曾毓群",
                          "score": 0.9, "rank": 1}]},
                     evidence_ids=["e1"], error_code=None, message=None,
                     retryable=False, trace_id="t"))
    reg.register(_spec("inspect_evidence", ["evidence_id"],
                       {"evidence_id": {"type": "string"}},
                       ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL")),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="inspect_evidence", tool_version="v1",
                     status="SUCCESS",
                     data={"evidence_id": "e1", "document_id": "d1",
                           "source_name": "s", "source_type": "annual_report",
                           "page_number": 3, "section_path": "控制关系",
                           "evidence_type": "paragraph",
                           "report_period": "2024-12-31",
                           "text": "实际控制人为曾毓群", "structured_payload": None},
                     evidence_ids=["e1"], error_code=None, message=None,
                     retryable=False, trace_id="t"))
    reg.register(_spec("fetch_external_content", ["url"],
                       {"url": {"type": "string"}}, ("EXTERNAL_RESEARCH",)),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="fetch_external_content", tool_version="v1",
                     status="SUCCESS", data={"canonical_url": a["url"],
                                             "content_text": "正文内容", "content_hash": "h1"},
                     error_code=None, message=None, retryable=False, trace_id="t"))
    reg.register(_spec("snapshot_external_source",
                       ["company_id", "canonical_url", "content_text"],
                       {"company_id": {"type": "string"},
                        "canonical_url": {"type": "string"},
                        "content_text": {"type": "string"},
                        "content_hash": {"type": "string"},
                        "content_type": {"type": "string"},
                        "http_status": {"type": "integer"},
                        "file_hash": {"type": "string"},
                        "page_count": {"type": "integer"},
                        "original_url": {"type": "string"},
                        "provider": {"type": "string"},
                        "query": {"type": "string"},
                        "title": {"type": "string"},
                        "snippet": {"type": "string"},
                        "published_at": {"type": "string"},
                        "source_grade": {"type": "string"}}, ("EXTERNAL_RESEARCH",)),
                 lambda a: TC.ToolResult(
                     call_id="", tool_name="snapshot_external_source", tool_version="v1",
                     status="SUCCESS", data={"source_snapshot_id": "snap1"},
                     external_snapshot_ids=["snap1"], error_code=None, message=None,
                     retryable=False, trace_id="t"))
    return reg


class MockLLM:
    def __init__(self, actions: list[str], answers: list[str] | None = None):
        self._actions = list(actions)
        self._answers = list(answers or [])

    def select_action(self, prompt_vars):
        return llm_client.LLMResponse(text=self._actions.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")

    def generate_answer(self, prompt_vars):
        return llm_client.LLMResponse(text=self._answers.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")


class MockEntailLLM:
    """mock LLM：动作/答案 + 批量 entailment（可编程 verdict 序列 + 独立 usage）。"""

    def __init__(self, actions, answers, verdicts, entailment_tokens=(30, 40)):
        self._actions = list(actions)
        self._answers = list(answers)
        self._verdicts = list(verdicts)
        self._et = entailment_tokens
        self.entailment_calls = 0

    def select_action(self, prompt_vars):
        return llm_client.LLMResponse(text=self._actions.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")

    def generate_answer(self, prompt_vars):
        return llm_client.LLMResponse(text=self._answers.pop(0), input_tokens=10,
                                      output_tokens=20, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")

    def evaluate_entailment_batch(self, prompt_vars):
        self.entailment_calls += 1
        it, ot = self._et
        return llm_client.LLMResponse(text=self._verdicts.pop(0), input_tokens=it,
                                      output_tokens=ot, latency_ms=1, model="mock",
                                      call_id="m", finish_reason="stop")


def _run(route, llm, reg=None):
    return RT.run_question(
        need=_need(), route_result=_router_result(route),
        registry=reg or _fake_registry(), llm=llm, run_id="r", case_id="c",
        company_id="300750", section_id="company", trace_enabled=False)


# 答案 JSON（mock 问题的 required_aspect 由 TEXT_FALLBACK 派生为单方面 a1="q"，
# 故 COMPLETED 用答案须自报 aspects 覆盖 a1）。
_ANSWER_EVIDENCE = ('{"answer_text": "实控人为曾毓群", '
                    '"claims": [{"claim_id": "c1", "text": "实控人为曾毓群", '
                    '"kind": "fact", "citation_refs": [0]}], '
                    '"citations": [{"ref_type": "evidence", "evidence_id": "e1"}], '
                    '"aspects": [{"aspect_id": "a1", "text": "q", '
                    '"claim_ids": ["c1"]}], '
                    '"unresolved_items": [], "confidence": "high"}')

_ANSWER_EXTERNAL = ('{"answer_text": "近期无重大处罚", '
                    '"claims": [{"claim_id": "c1", "text": "近期无重大处罚", '
                    '"kind": "fact", "citation_refs": [0]}], '
                    '"citations": [{"ref_type": "external", '
                    '"source_snapshot_id": "snap1"}], '
                    '"aspects": [{"aspect_id": "a1", "text": "q", '
                    '"claim_ids": ["c1"]}], '
                    '"unresolved_items": [], "confidence": "high"}')

_ANSWER_GHOST = ('{"answer_text": "x", "claims": [{"claim_id": "c1", "text": "x", '
                 '"kind": "fact", "citation_refs": [0]}], '
                 '"citations": [{"ref_type": "evidence", "evidence_id": "ghost"}], '
                 '"unresolved_items": [], "confidence": "high"}')

# 未自报 aspects 的答案 → 触发 G2 未覆盖方面缺口（用于补检到预算耗尽测试）。
_ANSWER_NO_ASPECT = ('{"answer_text": "实控人为曾毓群", '
                     '"claims": [{"claim_id": "c1", "text": "实控人为曾毓群", '
                     '"kind": "fact", "citation_refs": [0]}], '
                     '"citations": [{"ref_type": "evidence", "evidence_id": "e1"}], '
                     '"unresolved_items": [], "confidence": "high"}')


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- _render_template ----
    out = RT._render_template("{{a}} {x} {{b}}", {"a": "1", "b": "2"})
    check(out == "1 {x} 2", "_render_template：{{key}} 替换且保留 JSON 花括号")

    # ---- parse_answer ----
    ans = RT.parse_answer(_ANSWER_EVIDENCE, "n1")
    check(ans.answer_text == "实控人为曾毓群" and len(ans.claims) == 1
          and ans.claims[0].citation_refs == [0]
          and ans.citations[0].ref_type == "evidence"
          and ans.citations[0].evidence_id == "e1", "parse_answer：合法解析")
    try:
        RT.parse_answer('{"answer_text":"x","claims":[],'
                        '"citations":[{"ref_type":"bogus"}]}', "n1")
        check(False, "parse_answer：非法 ref_type 应抛错")
    except ValueError:
        check(True, "parse_answer：非法 ref_type fail-closed")

    # ---- 本地检索 → 答案成功 ----
    llm = MockLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.success is True and o.completion_status == "COMPLETED"
          and o.state.status == "COMPLETED", "本地检索→答案：COMPLETED")
    check(o.state.evidence_ids == ["e1"] and len(o.state.tool_history) == 1,
          "状态累计 evidence_id + 工具历史 1 条")

    # ---- 重复动作去重 ----
    llm = MockLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.success is True and o.completion_status == "COMPLETED",
          "重复动作去重后仍可完成 COMPLETED")
    check(len(o.state.tool_history) == 1,
          "重复 SEARCH_LOCAL 未重复执行（tool_history 仅 1 条）")
    check(len(o.state.executed_action_keys) == 1,
          "executed_action_keys 记录 1 个幂等 key")
    check(len(o.state.rejected_duplicate_actions) == 1
          and o.state.rejected_duplicate_actions[0]["action"] == "SEARCH_LOCAL"
          and o.state.rejected_duplicate_actions[0]["source"] == "model_proposed"
          and o.state.rejected_duplicate_actions[0].get("key"),
          "重复动作写入 rejected_duplicate_actions（含 key + source）")

    # ---- ANSWER 带未覆盖方面 → 补检到预算耗尽 → COMPLETED_WITH_GAPS ----
    # 修订：max_rounds=5，补检循环走满「search + 4 次 ANSWER」直到末回合无空间再带缺口结束。
    llm = MockLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}',
         '{"action": "ANSWER", "arguments": {}}',
         '{"action": "ANSWER", "arguments": {}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_NO_ASPECT, _ANSWER_NO_ASPECT, _ANSWER_NO_ASPECT, _ANSWER_NO_ASPECT])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.success is False and o.completion_status == "COMPLETED_WITH_GAPS"
          and o.state.status == "COMPLETED_WITH_GAPS",
          "ANSWER 带未覆盖方面 → 预算耗尽 → COMPLETED_WITH_GAPS")
    check(any("未覆盖方面" in u for u in o.state.unresolved_items),
          "缺口写回 unresolved_items（未覆盖方面）")

    # ---- 外部 fetch 自动 snapshot ----
    llm = MockLLM(
        ['{"action": "FETCH_EXTERNAL", "arguments": {"url": "https://x.com/n"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EXTERNAL])
    o = _run("EXTERNAL_RESEARCH", llm)
    check(o.success is True and o.state.external_snapshot_ids == ["snap1"],
          "外部 fetch→自动 snapshot 固化")
    tools = [r.result.tool_name for r in o.state.tool_history]
    check(tools == ["fetch_external_content", "snapshot_external_source"],
          "fetch + 自动 snapshot 两条工具记录")
    check(any(r.auto for r in o.state.tool_history), "snapshot 标记为 auto")

    # ---- 虚构引用 → FAILED ----
    llm = MockLLM(['{"action": "ANSWER", "arguments": {}}'], [_ANSWER_GHOST])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.success is False and o.completion_status == "FAILED"
          and o.stop_reason == "MODEL_OUTPUT_INVALID", "虚构 evidence_id → FAILED")

    # ---- STOP_WITH_GAP ----
    llm = MockLLM(['{"action": "STOP_WITH_GAP", "arguments": {"reason": "缺关键材料"}}'])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.state.status == "COMPLETED_WITH_GAPS"
          and o.completion_status == "COMPLETED_WITH_GAPS" and o.success is False
          and "缺关键材料" in o.state.unresolved_items, "STOP_WITH_GAP → COMPLETED_WITH_GAPS")

    # ---- REQUEST_HUMAN ----
    llm = MockLLM(['{"action": "REQUEST_HUMAN", "arguments": {"reason": "需确认"}}'])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.state.status == "WAITING_HUMAN" and o.completion_status == "UNRESOLVED",
          "REQUEST_HUMAN → WAITING_HUMAN / UNRESOLVED")

    # ---- 预算耗尽（回合，INSPECT_EVIDENCE 不计分项） ----
    # 修订：DEFAULT_BUDGET.max_rounds 由 3 → 5（完整「search→inspect A→inspect B→ANSWER」
    # 需 ≥4 回合）。此处用显式小预算（max_rounds=2）确定性触发回合耗尽，不依赖新默认值。
    small = P.ResearchBudget(
        max_rounds=2, max_tool_calls=5, max_local_searches=2,
        max_external_searches=2, max_fetches=2, max_action_repairs=1,
        max_added_needs=2, max_consecutive_no_new_evidence=5,
        max_tokens=8000, max_elapsed_ms=120000, max_retries_per_call=1)
    llm = MockLLM([
        '{"action": "INSPECT_EVIDENCE", "arguments": {"evidence_id": "e1"}}',
        '{"action": "INSPECT_EVIDENCE", "arguments": {"evidence_id": "e1"}}'])
    o = RT.run_question(need=_need(), route_result=_router_result("DIRECT_EVIDENCE"),
                        registry=_fake_registry(), llm=llm, run_id="r", case_id="c",
                        company_id="300750", section_id="company",
                        budget=small, trace_enabled=False)
    check(o.state.status == "BLOCKED" and o.stop_reason == "BUDGET_ITERATIONS",
          "预算耗尽（回合）→ BLOCKED BUDGET_ITERATIONS")

    # ---- 修订：默认 5 回合足以走完 search→inspect→ANSWER（收敛不被回合预算腰斩） ----
    llm = MockLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "INSPECT_EVIDENCE", "arguments": {"evidence_id": "e1"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.success is True and o.completion_status == "COMPLETED"
          and o.state.usage.rounds == 3,
          "默认预算内 search→inspect→ANSWER 三回合收敛 COMPLETED（rounds=3 ≤ max_rounds=5）")

    # ---- 本地搜索分项预算超限 ----
    llm = MockLLM([
        '{"action": "SEARCH_LOCAL", "arguments": {"query": "a"}}',
        '{"action": "SEARCH_LOCAL", "arguments": {"query": "b"}}',
        '{"action": "SEARCH_LOCAL", "arguments": {"query": "c"}}'])
    o = _run("DIRECT_EVIDENCE", llm)
    check(o.state.status == "BLOCKED" and o.stop_reason == "BUDGET_TOOL_CALLS",
          "本地搜索分项超限 → BLOCKED BUDGET_TOOL_CALLS")

    # ---- 路由未 DECIDED ----
    bad = RS.RouterResult(status="FALLBACK_UNAVAILABLE", decision=None,
                          error_code="ROUTER_FALLBACK_UNAVAILABLE", trace_id="t")
    o = RT.run_question(need=_need(), route_result=bad, registry=_fake_registry(),
                        llm=MockLLM([]), run_id="r", case_id="c", company_id="300750",
                        trace_enabled=False)
    check(o.completion_status == "NOT_IMPLEMENTED" and o.stop_reason == "PATH_NOT_IMPLEMENTED",
          "路由未 DECIDED → NOT_IMPLEMENTED")

    # ---- 回归：动作/答案 LLM 调用关闭推理 + 头部空间 ----
    # 真实冒烟发现：DeepSeek-V4-Pro 为推理模型，推理内容计入 output_tokens，开启推理会
    # finish_reason=max_tokens 且正文为空 → ACTION_SCHEMA_INVALID。此处固定动作/答案均
    # thinking={"type": "disabled"} 且 max_tokens 动作≥2048、答案≥4096，防回退。
    captured: dict[str, dict] = {}
    def _fake_chat(messages, system=None, model=None, max_tokens=4096,
                   prompt_version=None, thinking=None):
        captured[prompt_version] = {"max_tokens": max_tokens, "thinking": thinking}
        return llm_client.LLMResponse(text='{"action":"ANSWER","arguments":{}}',
                                      input_tokens=10, output_tokens=20, latency_ms=1,
                                      model=model or "mock", call_id="m",
                                      finish_reason="stop")

    orig_chat = llm_client.chat_with_usage
    llm_client.chat_with_usage = _fake_chat
    try:
        rllm = RT.RealResearchLLM(model="deepseek-v4-pro")
        rllm.select_action({"company_id": "300750", "section_id": "company",
                            "question": "q", "route": "STANDARD_RAG",
                            "route_reason": "r", "current_goal": "g", "round": 1,
                            "allowed_actions": "- SEARCH_LOCAL\n- ANSWER",
                            "evidence_summary": "（无）", "unresolved": "（无）",
                            "budget_left": "rounds 1/3", "search_candidates": ""})
        rllm.generate_answer({"company_id": "300750", "section_id": "company",
                              "question": "q", "route": "STANDARD_RAG",
                              "available_material": "（无）", "unresolved": "（无）"})
    finally:
        llm_client.chat_with_usage = orig_chat
    act = captured.get("research_action_v1", {})
    ans = captured.get("research_answer_v1", {})
    check(act.get("thinking") == {"type": "disabled"},
          "动作选择关闭推理 thinking=disabled")
    check(ans.get("thinking") == {"type": "disabled"},
          "答案解析关闭推理 thinking=disabled")
    check(act.get("max_tokens", 0) >= 2048,
          "动作选择 max_tokens ≥ 2048（推理关闭后仍留头部）")
    check(ans.get("max_tokens", 0) >= 4096,
          "答案解析 max_tokens ≥ 4096（推理关闭后仍留头部）")

    # ---- 跨 ANSWER 状态污染修复（answer_revision 清理 + 版本标记）----
    # 第一答案 entailment UNSUPPORTED → 补检 → 第二答案 SUPPORTED：
    # 最终结果不残留第一答案的陈旧 unsupported；answer_revision=2；trace 保留两次判定。
    unsupported_v = json.dumps({"verdicts": [
        {"claim_id": "c1", "citation_ids": ["0"], "verdict": "UNSUPPORTED",
         "reason": "口径不一致"}]}, ensure_ascii=False)
    supported_v = json.dumps({"verdicts": [
        {"claim_id": "c1", "citation_ids": ["0"], "verdict": "SUPPORTED",
         "reason": "口径一致"}]}, ensure_ascii=False)
    poll_run = "revpoll_" + Path(tempfile.mkdtemp()).name
    e_llm = MockEntailLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE, _ANSWER_EVIDENCE],
        [unsupported_v, supported_v])
    o = RT.run_question(
        need=_need(), route_result=_router_result("DIRECT_EVIDENCE"),
        registry=_fake_registry(), llm=e_llm, run_id=poll_run, case_id="c",
        company_id="300750", section_id="company", trace_enabled=True)
    check(o.success is True and o.completion_status == "COMPLETED",
          "跨 ANSWER：UNSUPPORTED→补检→SUPPORTED 最终 COMPLETED")
    check(o.state.answer_revision == 2,
          "answer_revision 递增到 2（两次 ANSWER 评估）")
    check(o.state.unsupported_claims == [],
          "最终不残留第一答案的陈旧 unsupported_claims")
    check(all(v.verdict == "SUPPORTED" for v in o.state.entailment_verdicts),
          "最终 entailment_verdicts 只含最新答案版本（SUPPORTED）")
    check(not any(x.startswith("引用不支持结论") for x in o.state.unresolved_items),
          "unresolved_items 不含第一答案的陈旧 UNSUPPORTED 记录")
    trace_lines = T.trace_path(poll_run, "n1").read_text(encoding="utf-8").strip().splitlines()
    ent_events = [json.loads(l) for l in trace_lines
                  if json.loads(l).get("event") == "ENTAILMENT"]
    check(len(ent_events) == 2
          and ent_events[0]["verdicts"][0]["verdict"] == "UNSUPPORTED"
          and ent_events[1]["verdicts"][0]["verdict"] == "SUPPORTED",
          "trace 保留两次 ENTAILMENT 判定历史（UNSUPPORTED→SUPPORTED）")
    check(all(ev.get("answer_revision") is not None for ev in ent_events)
          and ent_events[0]["answer_revision"] != ent_events[1]["answer_revision"],
          "ENTAILMENT trace 事件带 answer_revision 且逐轮递增")

    # ---- entailment usage 记账（3 类 LLM 分别 + 合计）----
    e2 = MockEntailLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE],
        [supported_v],
        entailment_tokens=(300, 400))
    o = _run("DIRECT_EVIDENCE", e2)
    check(o.success is True and o.completion_status == "COMPLETED",
          "entailment 记账：含 entailment 的题目正常 COMPLETED")
    u = o.state.usage
    check(u.llm_calls == 4 and e2.entailment_calls == 1,
          "llm_calls 合计=4（2 动作 + 1 答案 + 1 entailment）")
    check(u.llm_by_category.get("action", {}).get("calls") == 2
          and u.llm_by_category.get("answer", {}).get("calls") == 1
          and u.llm_by_category.get("entailment", {}).get("calls") == 1,
          "llm_by_category 三类（action/answer/entailment）分别计数")
    check(u.input_tokens == 10 + 10 + 10 + 300 and u.output_tokens == 20 + 20 + 20 + 400,
          "entailment tokens 计入合计 input/output（无重复计数）")
    check(u.llm_by_category.get("entailment", {}).get("input_tokens") == 300
          and u.llm_by_category.get("entailment", {}).get("output_tokens") == 400,
          "entailment 分类独立记 input/output tokens")
    check(u.llm_latency_ms == 4,
          "llm_latency_ms 累计 4 次调用 latency（各 1ms）")

    # ---- 预算纳入 entailment：小 max_tokens 下 entailment 使合计超限 → BUDGET_TOKENS ----
    small_tok = P.ResearchBudget(
        max_rounds=5, max_tool_calls=5, max_local_searches=2,
        max_external_searches=2, max_fetches=2, max_action_repairs=1,
        max_added_needs=2, max_consecutive_no_new_evidence=5,
        max_tokens=100, max_elapsed_ms=120000, max_retries_per_call=1)
    e3 = MockEntailLLM(
        ['{"action": "SEARCH_LOCAL", "arguments": {"query": "实际控制人"}}',
         '{"action": "ANSWER", "arguments": {}}'],
        [_ANSWER_EVIDENCE],
        [unsupported_v],
        entailment_tokens=(500, 500))
    o = RT.run_question(need=_need(), route_result=_router_result("DIRECT_EVIDENCE"),
                        registry=_fake_registry(), llm=e3, run_id="r", case_id="c",
                        company_id="300750", section_id="company",
                        budget=small_tok, trace_enabled=False)
    check(P.check_budget(o.state, small_tok) == "BUDGET_TOKENS",
          "预算检查识别 entailment 计入后的 token 超限 → BUDGET_TOKENS")
    check(e3.entailment_calls == 1,
          "entailment 超预算后当前判定已落盘、未再发起新 LLM 调用")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
