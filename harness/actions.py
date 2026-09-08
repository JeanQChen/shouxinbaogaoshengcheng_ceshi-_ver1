"""Phase 3 Batch B 动作协议：11 动作枚举 + LLM 输出解析 + 一次格式修复。

职责边界（编码前计划 §5/§6）：
- LLM 只能从 `LLM_SELECTABLE_ACTIONS`（10 个）里选动作；`SNAPSHOT_EXTERNAL` 是
  Rules 内部动作 + 审计事件，LLM 不得直接发起（fetch 成功后由 Rules 自动 snapshot）；
- 动作输出严格按 JSON Schema + 类型校验，非法仅 1 次格式修复，仍非法 → FAILED；
- 动作→工具映射在此定义，DB 动作的 standard_item_code / formula_id / target_period
  由 Rules 层从 Router filters 注入，LLM 不自造科目代码 / 公式 ID；
- 终态动作（ANSWER / STOP_WITH_GAP / REQUEST_HUMAN）不映射工具。

CLI: python -m harness.actions --self-check
"""

from __future__ import annotations

import json

from harness import schema as H

# ---------------------------------------------------------------------------
# 动作白名单
# ---------------------------------------------------------------------------

# LLM 可见面（10 个；不含 Rules 内部的 SNAPSHOT_EXTERNAL）。
LLM_SELECTABLE_ACTIONS = (
    "SEARCH_LOCAL",
    "INSPECT_EVIDENCE",
    "LOOKUP_COMPANY_FIELD",
    "LOOKUP_FINANCIAL_METRIC",
    "COMPARE_FINANCIAL_PERIODS",
    "SEARCH_EXTERNAL",
    "FETCH_EXTERNAL",
    "ANSWER",
    "STOP_WITH_GAP",
    "REQUEST_HUMAN",
)

# Rules 内部动作（不在 LLM 可见面，用于审计与自动固化快照）。
RULES_INTERNAL_ACTIONS = ("SNAPSHOT_EXTERNAL",)

# 完整 11 动作枚举。
ACTIONS = LLM_SELECTABLE_ACTIONS + RULES_INTERNAL_ACTIONS

# 终态动作（不映射工具）。
TERMINAL_ACTIONS = ("ANSWER", "STOP_WITH_GAP", "REQUEST_HUMAN")

# 动作 → 工具名（终态为 None）。
ACTION_TOOL = {
    "SEARCH_LOCAL": "search_evidence",
    "INSPECT_EVIDENCE": "inspect_evidence",
    "LOOKUP_COMPANY_FIELD": "lookup_company_field",
    "LOOKUP_FINANCIAL_METRIC": "lookup_financial_metric",
    "COMPARE_FINANCIAL_PERIODS": "compare_financial_periods",
    "SEARCH_EXTERNAL": "search_external_sources",
    "FETCH_EXTERNAL": "fetch_external_content",
    "SNAPSHOT_EXTERNAL": "snapshot_external_source",
    "ANSWER": None,
    "STOP_WITH_GAP": None,
    "REQUEST_HUMAN": None,
}

# 动作 → 允许路由（用于 Registry.execute(route=...) 的门控校验提示；实际门控在 Registry）。
ACTION_ROUTES = {
    "SEARCH_LOCAL": ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    "INSPECT_EVIDENCE": ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    "LOOKUP_COMPANY_FIELD": ("DB_LOOKUP",),
    "LOOKUP_FINANCIAL_METRIC": ("DB_LOOKUP",),
    "COMPARE_FINANCIAL_PERIODS": ("DB_LOOKUP",),
    "SEARCH_EXTERNAL": ("EXTERNAL_RESEARCH",),
    "FETCH_EXTERNAL": ("EXTERNAL_RESEARCH",),
    "SNAPSHOT_EXTERNAL": ("EXTERNAL_RESEARCH",),
}


# ---------------------------------------------------------------------------
# 校验异常
# ---------------------------------------------------------------------------

class ActionParseError(ValueError):
    """动作输出解析/校验失败（fail-closed）。"""


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------

# 各动作允许的参数键（LLM 只填「变量部分」，公司/科目/期间等由 Rules 注入）。
_ALLOWED_ARGS: dict[str, frozenset[str]] = {
    "SEARCH_LOCAL": frozenset({"query", "k", "evidence_type"}),
    "INSPECT_EVIDENCE": frozenset({"evidence_id", "evidence_ids"}),
    "LOOKUP_COMPANY_FIELD": frozenset(),
    "LOOKUP_FINANCIAL_METRIC": frozenset(),
    "COMPARE_FINANCIAL_PERIODS": frozenset({"period_a", "period_b"}),
    "SEARCH_EXTERNAL": frozenset({"query", "limit", "freshness"}),
    "FETCH_EXTERNAL": frozenset({"url"}),
    "SNAPSHOT_EXTERNAL": frozenset(),
    "ANSWER": frozenset({"note"}),
    "STOP_WITH_GAP": frozenset({"reason"}),
    "REQUEST_HUMAN": frozenset({"reason"}),
}

# 各动作必需参数键。
_REQUIRED_ARGS: dict[str, frozenset[str]] = {
    "SEARCH_LOCAL": frozenset({"query"}),
    "INSPECT_EVIDENCE": frozenset(),          # evidence_id 或 evidence_ids 二选一
    "LOOKUP_COMPANY_FIELD": frozenset(),
    "LOOKUP_FINANCIAL_METRIC": frozenset(),
    "COMPARE_FINANCIAL_PERIODS": frozenset({"period_a", "period_b"}),
    "SEARCH_EXTERNAL": frozenset({"query"}),
    "FETCH_EXTERNAL": frozenset({"url"}),
    "SNAPSHOT_EXTERNAL": frozenset(),
    "ANSWER": frozenset(),
    "STOP_WITH_GAP": frozenset(),
    "REQUEST_HUMAN": frozenset(),
}


def validate_action(action: str | None, arguments) -> list[str]:
    """校验一个动作及其参数，返回错误列表（空=合法）。"""
    if action not in ACTIONS:
        return [f"未知动作: {action}"]
    if action in RULES_INTERNAL_ACTIONS:
        return [f"动作 {action} 为 Rules 内部动作，LLM 不得直接发起"]
    if not isinstance(arguments, dict):
        return ["arguments 必须为 object"]

    errors: list[str] = []
    allowed = _ALLOWED_ARGS[action]
    for k in arguments:
        if k not in allowed:
            errors.append(f"未知参数: {k}")
    for k in _REQUIRED_ARGS[action]:
        if k not in arguments:
            errors.append(f"缺少必需参数: {k}")

    # INSPECT_EVIDENCE：evidence_id（单）或 evidence_ids（数组）二选一。
    if action == "INSPECT_EVIDENCE":
        has_single = "evidence_id" in arguments
        has_multi = "evidence_ids" in arguments
        if has_single and has_multi:
            errors.append("evidence_id 与 evidence_ids 不能同时出现")
        if not has_single and not has_multi:
            errors.append("缺少 evidence_id 或 evidence_ids")
        if has_single and not isinstance(arguments["evidence_id"], str):
            errors.append("evidence_id 必须为 string")
        if has_multi:
            ids = arguments["evidence_ids"]
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                errors.append("evidence_ids 必须为 string 数组")
            elif not (1 <= len(ids) <= 20):
                errors.append("evidence_ids 长度必须在 1~20")

    # 字符串参数类型。
    for key in ("query", "period_a", "period_b", "url", "reason", "note"):
        if key in arguments and not isinstance(arguments[key], str):
            errors.append(f"{key} 必须为 string")

    # 整数参数。
    for key in ("k", "limit"):
        if key in arguments and (not isinstance(arguments[key], int)
                                 or isinstance(arguments[key], bool)):
            errors.append(f"{key} 必须为 integer")

    # SEARCH_LOCAL 可选 evidence_type 白名单。
    if action == "SEARCH_LOCAL" and "evidence_type" in arguments:
        et = arguments["evidence_type"]
        if et not in ("paragraph", "heading", "table", "table_row"):
            errors.append(f"evidence_type 非法: {et}")

    return errors


# ---------------------------------------------------------------------------
# JSON 解析 / 一次格式修复
# ---------------------------------------------------------------------------

def _parse_json_obj(raw: str) -> dict:
    """把原始文本解析为 dict；非法 JSON 抛 ActionParseError。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ActionParseError(f"非法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise ActionParseError("输出必须为 JSON object")
    return data


def parse_action(raw: str) -> H.ActionCall:
    """把 LLM 原始输出解析并校验为 ActionCall；非法抛 ActionParseError。"""
    raw = (raw or "").strip()
    if not raw:
        raise ActionParseError("空输出")
    data = _parse_json_obj(raw)
    action = data.get("action")
    arguments = data.get("arguments", {})
    errors = validate_action(action, arguments)
    if errors:
        raise ActionParseError("; ".join(errors))
    tool_name = ACTION_TOOL[action]
    return H.ActionCall(action=action, arguments=arguments, tool_name=tool_name,
                        raw=raw, source="llm")


def _repair_json(raw: str) -> str | None:
    """仅做「格式」修复：剥离代码围栏 / 提取首个 {...} 平衡块。不改语义。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    lo = s.find("{")
    hi = s.rfind("}")
    if lo == -1 or hi == -1 or hi <= lo:
        return None
    return s[lo:hi + 1]


def repair_once(raw: str, error: str) -> H.ActionCall | None:
    """一次格式修复：剥离围栏/提取 JSON 块后重解析；仍非法返回 None。

    只修格式（围栏/包裹文字），不修语义（非法动作/参数仍返回 None）。
    """
    cleaned = _repair_json(raw)
    if cleaned is None:
        return None
    try:
        return parse_action(cleaned)
    except ActionParseError:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.actions", description="动作协议自检")
    parser.add_argument("--self-check", action="store_true",
                        help="打印动作协议自检（不发起 LLM/工具）")
    args = parser.parse_args(argv)

    if args.self_check:
        ok = parse_action('{"action": "SEARCH_EXTERNAL", "arguments": {"query": "宁德时代 市场份额"}}')
        fenced = repair_once(
            '```json\n{"action": "ANSWER", "arguments": {}}\n```', "")
        summary = {
            "actions": list(ACTIONS),
            "llm_selectable": list(LLM_SELECTABLE_ACTIONS),
            "rules_internal": list(RULES_INTERNAL_ACTIONS),
            "action_to_tool": ACTION_TOOL,
            "smoke_parse_ok": ok.action == "SEARCH_EXTERNAL" and ok.tool_name == "search_external_sources",
            "smoke_repair_ok": fenced is not None and fenced.action == "ANSWER",
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
