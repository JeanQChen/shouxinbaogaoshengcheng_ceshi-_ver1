"""Eval: harness 预算 / 补检 / 去重规则 —— Phase 3 Batch B commit 3。

用法: python -m evals.test_harness_budget

断言（纯逻辑，无 I/O / LLM / 工具执行）：
- ResearchBudget / DEFAULT_BUDGET 默认值（3 回合 / 5 调用 / 2 本地 / 2 外部搜索 / 2 fetch /
  1 修复 / 2 added needs / 2 连续无新证据 / 8000 tokens / 120000ms / 1 retry）；
- dedup_key：同 tool+args → 同 key；不同 args → 不同 key；
- check_budget：回合/调用/外部/token（仅 usage 已知）/耗时/连续无新证据 各命中对应 stop_reason，
  且 usage 未知时跳过 token 检查；
- supplement_triggers：本地搜索空 / 外部搜索无快照 / 显式 unresolved / 证据冲突 / 财务不可用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import policies as P
from harness import schema as H
from routing import schema as RS
from tools import contracts as TC


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _state(**overrides) -> H.ResearchState:
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="300750",
                         section_id="company", original_question="q", need=_need())
    for k, v in overrides.items():
        setattr(st, k, v)
    return st


def _call(tool_name: str, arguments: dict) -> TC.ToolCall:
    return TC.ToolCall(call_id="cid", tool_name=tool_name, arguments=arguments,
                       idempotency_key="k", need_id="n1", batch_id="b1")


def _record(tool_name: str, status: str, data: dict | None = None,
            error_code: str | None = None) -> H.ToolCallRecord:
    res = TC.ToolResult(call_id="cid", tool_name=tool_name, tool_version="v1",
                        status=status, data=data or {}, error_code=error_code)
    return H.ToolCallRecord(call=_call(tool_name, {}), result=res)


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

    # ---- 默认预算 ----
    b = P.DEFAULT_BUDGET
    check(b.max_rounds == 3 and b.max_tool_calls == 5 and b.max_local_searches == 2
          and b.max_external_searches == 2 and b.max_fetches == 2
          and b.max_action_repairs == 1 and b.max_added_needs == 2
          and b.max_consecutive_no_new_evidence == 2 and b.max_tokens == 8000
          and b.max_elapsed_ms == 120000 and b.max_retries_per_call == 1,
          "DEFAULT_BUDGET 全部默认值")
    d = b.as_dict()
    check(d["max_tokens"] == 8000 and "max_elapsed_ms" in d, "as_dict 含 max_tokens/elapsed")

    # ---- dedup_key ----
    k1 = P.dedup_key(_call("search_evidence", {"company_id": "300750", "query": "实控人"}))
    k2 = P.dedup_key(_call("search_evidence", {"company_id": "300750", "query": "实控人"}))
    k3 = P.dedup_key(_call("search_evidence", {"company_id": "300750", "query": "毛利率"}))
    check(k1 == k2, "dedup_key：同 tool+args 同 key")
    check(k1 != k3, "dedup_key：不同 args 不同 key")

    # ---- check_budget 逐项 ----
    check(P.check_budget(_state(), b) is None, "check_budget：默认未超限 → None")
    st = _state()
    st.usage.rounds = 4
    check(P.check_budget(st, b) == "BUDGET_ITERATIONS", "回合超限 → BUDGET_ITERATIONS")

    st = _state()
    st.usage.tool_calls = 6
    check(P.check_budget(st, b) == "BUDGET_TOOL_CALLS", "工具调用超限 → BUDGET_TOOL_CALLS")

    st = _state()
    st.usage.local_searches = 3
    check(P.check_budget(st, b) == "BUDGET_TOOL_CALLS", "本地搜索超限 → BUDGET_TOOL_CALLS")

    st = _state()
    st.usage.external_searches = 3
    check(P.check_budget(st, b) == "BUDGET_EXTERNAL", "外部搜索超限 → BUDGET_EXTERNAL")

    st = _state()
    st.usage.fetches = 3
    check(P.check_budget(st, b) == "BUDGET_EXTERNAL", "fetch 超限 → BUDGET_EXTERNAL")

    st = _state()
    st.usage.input_tokens = 9000
    check(P.check_budget(st, b) == "BUDGET_TOKENS", "已知 token 超限 → BUDGET_TOKENS")

    st = _state()
    st.usage.input_tokens = 9000
    st.usage.usage_unknown_calls = 1
    check(P.check_budget(st, b) is None, "usage 未知调用时跳过 token 检查")

    st = _state()
    st.usage.elapsed_ms = 130000
    check(P.check_budget(st, b) == "BUDGET_ELAPSED", "耗时超限 → BUDGET_ELAPSED")

    st = _state()
    st.usage.consecutive_no_new_evidence = 3
    check(P.check_budget(st, b) == "CONSECUTIVE_NO_NEW_EVIDENCE",
          "连续无新证据超限 → CONSECUTIVE_NO_NEW_EVIDENCE")

    # ---- budget_has_room ----
    check(P.budget_has_room(_state(), b) is True, "默认有空间")
    st = _state()
    st.usage.rounds = b.max_rounds
    check(P.budget_has_room(st, b) is False, "rounds 满 → 无空间")

    # ---- supplement_triggers ----
    st = _state(tool_history=[_record("search_evidence", "EMPTY")])
    check("LOCAL_SEARCH_EMPTY" in P.supplement_triggers(st),
          "本地搜索 EMPTY → LOCAL_SEARCH_EMPTY")

    st = _state(tool_history=[_record("search_external_sources", "SUCCESS")])
    check("EXTERNAL_SNIPPET_WITHOUT_SNAPSHOT" in P.supplement_triggers(st),
          "外部搜索已执行但无快照 → EXTERNAL_SNIPPET_WITHOUT_SNAPSHOT")

    st = _state(unresolved_items=["缺实控人变更时间"])
    check("EXPLICIT_UNRESOLVED" in P.supplement_triggers(st),
          "显式 unresolved → EXPLICIT_UNRESOLVED")

    st = _state(tool_history=[_record(
        "compare_evidence", "SUCCESS", {"pairs": [{"value_relation": "conflict"}]})])
    check("EVIDENCE_CONFLICT" in P.supplement_triggers(st),
          "compare_evidence 冲突 → EVIDENCE_CONFLICT")

    st = _state(tool_history=[_record(
        "lookup_financial_metric", "EMPTY", error_code="DB_FIELD_UNAVAILABLE")])
    check("FINANCIAL_UNAVAILABLE" in P.supplement_triggers(st),
          "财务字段不可用 → FINANCIAL_UNAVAILABLE")

    # ---- has_gap ----
    check(P.has_gap(_state(unresolved_items=["x"])) is True, "has_gap：有缺口")
    check(P.has_gap(_state()) is False, "has_gap：无缺口")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
