"""Eval: Tool 契约层（tools/contracts.py）—— Phase 3 Batch A。

用法: python -m evals.test_tool_contracts

断言（纯声明式，无 I/O、不调用工具）：
- 工具名/状态/错误码/重试策略/成本类别枚举完整且无重复；
- ToolResult 状态与错误码合法性；
- validate_arguments：缺必需参数、未知字段拒绝、类型错误、enum、数值上下限、
  字符串长度、数组元素数、嵌套对象未知字段；
- enforce_arguments 在非法参数时抛 ToolValidationError。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import contracts as C
from routing import schema as routing_schema


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

    # ---- 枚举完整性 ----
    check(len(C.TOOL_STATUSES) == 5, "工具结果状态恰为 5 态")
    check(set(C.TOOL_STATUSES) == {"SUCCESS", "PARTIAL", "EMPTY",
                                   "RETRYABLE_ERROR", "FATAL_ERROR"},
          "状态集合与任务书 §6.2 一致")
    check(len(C.TOOL_ERROR_CODES) == 13, "最低错误码恰为 13 个")
    check(len(set(C.TOOL_ERROR_CODES)) == len(C.TOOL_ERROR_CODES),
          "错误码无重复")
    check("verify_claim" not in C.TOOL_NAMES, "verify_claim 不注册（Phase 4/5 边界）")
    check("search_evidence" in C.TOOL_NAMES and "lookup_company_field" in C.TOOL_NAMES,
          "首批本地工具已含 search_evidence / lookup_company_field")
    check(C.RETRY_POLICIES == ("none", "retryable_only"),
          "重试策略枚举与任务书一致")
    check(C.COST_CLASSES == ("local", "db", "external"), "成本类别枚举完整")
    check(set(C.TOOL_NAMES) & {"verify_claim"} == set(),
          "verify_claim 不在 TOOL_NAMES 白名单")

    # ---- ToolResult 语义 ----
    r = C.ToolResult(call_id="c1", tool_name="search_evidence",
                     tool_version="v1", status="EMPTY", data={})
    check(r.is_empty() and not r.is_error(), "EMPTY 是合法结果且非错误")
    r_err = C.ToolResult(call_id="c2", tool_name="x", tool_version="v1",
                         status="RETRYABLE_ERROR", data={}, retryable=True)
    check(r_err.is_error() and r_err.status in C.RETRYABLE_STATUSES,
          "RETRYABLE_ERROR 是可重试错误")

    # ---- validate_arguments ----
    spec = C.ToolSpec(
        name="search_evidence", version="v1", description="本地检索",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["company_id", "query"],
            "properties": {
                "company_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1, "maxLength": 100},
                "k": {"type": "integer", "minimum": 1, "maximum": 20},
                "filters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"period": {"type": "string"}},
                },
            },
        },
        output_schema={"type": "object"},
        allowed_routes=("STANDARD_RAG", "DEEP_RETRIEVAL", "DIRECT_EVIDENCE"),
        max_results=10, timeout_ms=5000, retry_policy="none", cost_class="local",
    )

    check(C.validate_arguments(spec, {"company_id": "300750", "query": "q"}) == [],
          "合法参数无错误")
    errs = C.validate_arguments(spec, {"company_id": "300750"})
    check(any("缺少必需参数: query" in e for e in errs), "缺必需参数被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "evil": True})
    check(any("未知参数: evil" in e for e in errs), "未知字段被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": ""})
    check(any("query" in e and "长度小于" in e for e in errs), "字符串长度下限生效")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q", "k": 99})
    check(any("k" in e and "上限" in e for e in errs), "数值上限生效")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q", "k": "5"})
    check(any("k" in e and "类型错误" in e for e in errs), "整数类型错误被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "filters": {"evil": "x"}})
    check(any("filters" in e and "未知字段" in e for e in errs),
          "嵌套对象未知字段被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "filters": {"period": "2024-12-31"}})
    check(errs == [], "嵌套对象合法字段通过")
    errs = C.validate_arguments(spec, ["not", "a", "dict"])
    check(any("object" in e for e in errs), "非 dict arguments 被拒绝")

    # ---- enforce_arguments ----
    try:
        C.enforce_arguments(spec, {"company_id": "300750", "query": "q", "k": 99})
        check(False, "非法参数 enforce 应抛异常")
    except C.ToolValidationError:
        check(True, "enforce_arguments 非法参数抛 ToolValidationError")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
