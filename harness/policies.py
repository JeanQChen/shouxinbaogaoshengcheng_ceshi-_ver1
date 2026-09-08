"""Phase 3 Batch B 预算 / 补检 / 去重 / 停止规则（纯函数，无 I/O）。

职责边界（编码前计划 §7/§20）：
- 预算默认值集中在此（ResearchBudget + DEFAULT_BUDGET），读入并写入 trace，不散落硬编码；
- 补检必须改变 query/route/filter/time scope 等 ≥1 项，相同调用靠 dedup_key 去重；
- Harness 只消费 Retriever V2 返回的 EvidencePack 与既有降级状态，不改变 Phase 2 的
  sparse/dense/fusion 策略、权重、Top-K 或超时降级逻辑；
- max_tokens 指单题 input+output 已知 usage 总预算；Provider 不返回 usage 时不能假装
  未超预算，须同时依赖回合 / 调用次数 / 耗时停止。

CLI: python -m harness.policies --self-check
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from harness import schema as H
from tools import contracts as TC

# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchBudget:
    """单题研究预算（本批次只统计当前题；batch 级累计由 Batch C 接管）。"""

    max_rounds: int
    max_tool_calls: int
    max_local_searches: int
    max_external_searches: int
    max_fetches: int
    max_action_repairs: int
    max_added_needs: int
    max_consecutive_no_new_evidence: int
    max_tokens: int                 # 单题 input+output 已知 usage 总预算
    max_elapsed_ms: int
    max_retries_per_call: int

    def as_dict(self) -> dict:
        return {
            "max_rounds": self.max_rounds,
            "max_tool_calls": self.max_tool_calls,
            "max_local_searches": self.max_local_searches,
            "max_external_searches": self.max_external_searches,
            "max_fetches": self.max_fetches,
            "max_action_repairs": self.max_action_repairs,
            "max_added_needs": self.max_added_needs,
            "max_consecutive_no_new_evidence": self.max_consecutive_no_new_evidence,
            "max_tokens": self.max_tokens,
            "max_elapsed_ms": self.max_elapsed_ms,
            "max_retries_per_call": self.max_retries_per_call,
        }


# 默认预算（用户 §三：3 回合 / 5 工具调用 / 2 本地 / 2 外部搜索 / 2 fetch / 1 动作格式修复）。
DEFAULT_BUDGET = ResearchBudget(
    max_rounds=3,
    max_tool_calls=5,
    max_local_searches=2,
    max_external_searches=2,
    max_fetches=2,
    max_action_repairs=1,
    max_added_needs=2,
    max_consecutive_no_new_evidence=2,
    max_tokens=8000,
    max_elapsed_ms=120000,
    max_retries_per_call=1,
)


# ---------------------------------------------------------------------------
# 补检触发条件（§三 8 条件）
# ---------------------------------------------------------------------------

SUPPLEMENT_TRIGGERS = (
    "LOCAL_SEARCH_EMPTY",
    "LOCAL_PARTIAL_COVERAGE",
    "MISSING_REQUIRED_TOPIC",
    "FINANCIAL_UNAVAILABLE",
    "EXTERNAL_SNIPPET_WITHOUT_SNAPSHOT",
    "INSUFFICIENT_SOURCE_FOR_CONCLUSION",
    "EVIDENCE_CONFLICT",
    "EXPLICIT_UNRESOLVED",
)


def supplement_triggers(state: H.ResearchState) -> list[str]:
    """从可观测状态推导当前命中的补检触发条件（诊断用，供 runtime 决定补检/停止）。"""
    triggers: list[str] = []
    seen_search = False
    seen_external_search = False

    for rec in state.tool_history:
        tool = rec.result.tool_name
        status = rec.result.status
        data = rec.result.data or {}

        if tool in ("search_evidence", "search_tables"):
            seen_search = True
            if status == "EMPTY":
                triggers.append("LOCAL_SEARCH_EMPTY")
            elif status == "PARTIAL":
                triggers.append("LOCAL_PARTIAL_COVERAGE")
            if data.get("missing_requirements"):
                triggers.append("MISSING_REQUIRED_TOPIC")

        if tool in ("lookup_company_field", "lookup_financial_metric"):
            if status == "EMPTY" and rec.result.error_code == "DB_FIELD_UNAVAILABLE":
                triggers.append("FINANCIAL_UNAVAILABLE")

        if tool == "compare_evidence":
            for pair in data.get("pairs", []):
                if pair.get("value_relation") == "conflict":
                    triggers.append("EVIDENCE_CONFLICT")
                    break

        if tool == "search_external_sources":
            seen_external_search = True

    # 外部搜索已执行但尚无快照 → 只有摘要，不能作关键事实。
    if seen_external_search and not state.external_snapshot_ids:
        triggers.append("EXTERNAL_SNIPPET_WITHOUT_SNAPSHOT")

    if state.unresolved_items:
        triggers.append("EXPLICIT_UNRESOLVED")

    # 尚无任何可引用材料 + 无答案 → 来源不足以支撑结论。
    if (not state.evidence_ids and not state.structured_refs
            and not state.external_snapshot_ids and not state.answered_claims):
        if seen_search or seen_external_search:
            triggers.append("INSUFFICIENT_SOURCE_FOR_CONCLUSION")

    # 去重保序。
    return list(dict.fromkeys(triggers))


def has_gap(state: H.ResearchState) -> bool:
    """是否存在需要补检或记为缺口的条件。"""
    return bool(supplement_triggers(state))


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def dedup_key(call: TC.ToolCall) -> str:
    """规范化参数哈希（幂等键）：同 tool + 同参数 → 同 key，防止重复调用。"""
    payload = {
        "tool": call.tool_name,
        "args": sorted(call.arguments.items()),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 预算检查
# ---------------------------------------------------------------------------

def check_budget(state: H.ResearchState, budget: ResearchBudget) -> str | None:
    """任一预算超限则返回具体 stop_reason；未超限返回 None。

    max_tokens 仅对「已知 usage」生效；存在 usage 未知调用时跳过 token 检查，
    转而依赖回合 / 调用次数 / 耗时（编码前同步补正）。
    """
    u = state.usage
    if u.rounds > budget.max_rounds:
        return "BUDGET_ITERATIONS"
    if u.tool_calls > budget.max_tool_calls:
        return "BUDGET_TOOL_CALLS"
    if u.local_searches > budget.max_local_searches:
        return "BUDGET_TOOL_CALLS"
    if u.external_searches > budget.max_external_searches or u.fetches > budget.max_fetches:
        return "BUDGET_EXTERNAL"
    if u.usage_unknown_calls == 0 and (u.input_tokens + u.output_tokens) > budget.max_tokens:
        return "BUDGET_TOKENS"
    if u.elapsed_ms > budget.max_elapsed_ms:
        return "BUDGET_ELAPSED"
    if u.consecutive_no_new_evidence > budget.max_consecutive_no_new_evidence:
        return "CONSECUTIVE_NO_NEW_EVIDENCE"
    return None


def budget_has_room(state: H.ResearchState, budget: ResearchBudget) -> bool:
    """是否还有至少一次补检/动作空间（未超预算且还剩回合）。"""
    if check_budget(state, budget) is not None:
        return False
    return state.usage.rounds < budget.max_rounds


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.policies", description="预算/补检/去重规则自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps({
            "default_budget": DEFAULT_BUDGET.as_dict(),
            "supplement_triggers": list(SUPPLEMENT_TRIGGERS),
            "dedup_key_sample": dedup_key(TC.ToolCall(
                call_id="x", tool_name="search_evidence",
                arguments={"company_id": "300750", "query": "实际控制人"},
                idempotency_key="k", need_id="n", batch_id="b")),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
