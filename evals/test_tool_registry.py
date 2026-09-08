"""Eval: Tool Registry（tools/registry.py）—— Phase 3 Batch A。

用法: python -m evals.test_tool_registry

断言（用假 executor，不触碰真实 Evidence/Financial/Retrieval）：
- register/get/list + 重复注册/非法 spec 拒绝；
- execute 成功透传 metadata（tool_version/latency/trace_id/retryable）；
- 未注册 → TOOL_NOT_FOUND；路由越权 → TOOL_NOT_ALLOWED；参数错 → INVALID_ARGUMENTS；
- 重试只针对 retryable_only 且 RETRYABLE_ERROR，FATAL_ERROR 不重试；
- 超时 → TOOL_TIMEOUT（RETRYABLE_ERROR）；executor 非 ToolResult → TOOL_CONTRACT_ERROR；
- executor 抛异常 → INTERNAL_ERROR；
- audit 用 call_id 唯一文件名落盘；audit 目录不可写 → fail-closed 返回 INTERNAL_ERROR。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import contracts as C
from tools import registry as R


def _spec(name, retry_policy="none", timeout_ms=1000, routes=("STANDARD_RAG",)):
    return C.ToolSpec(
        name=name, version="v1", description="test",
        input_schema={"type": "object", "additionalProperties": False,
                      "required": ["q"], "properties": {"q": {"type": "string"}}},
        output_schema={"type": "object"},
        allowed_routes=tuple(routes), max_results=5, timeout_ms=timeout_ms,
        retry_policy=retry_policy, cost_class="local")


def _call(name, args=None, need="n1", batch="b1"):
    return C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=name, arguments=args or {"q": "x"},
        idempotency_key=uuid.uuid4().hex, need_id=need, batch_id=batch)


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

    audit_dir = tempfile.mkdtemp(prefix="eval_registry_")
    reg = R.ToolRegistry(audit_dir=Path(audit_dir))

    # ---- register ----
    reg.register(_spec("ok_tool"), lambda a: C.ToolResult(
        call_id="", tool_name="ok_tool", tool_version="v1", status="SUCCESS",
        data={"n": 1}, trace_id=uuid.uuid4().hex))
    check(reg.get("ok_tool") is not None, "register + get 成功")
    check(any(s.name == "ok_tool" for s in reg.list()), "list 含已注册工具")
    try:
        reg.register(_spec("ok_tool"), lambda a: None)
        check(False, "重复注册应拒绝")
    except C.ToolValidationError:
        check(True, "重复注册被拒绝")

    # ---- execute 成功 ----
    res = reg.execute(_call("ok_tool"), route="STANDARD_RAG", run_id="run1")
    check(res.status == "SUCCESS" and res.tool_version == "v1", "execute 成功透传 version")
    check(res.trace_id != "" and res.latency_ms >= 0, "execute 填充 trace_id/latency")
    check(res.retryable is False and res.retries == 0, "成功结果 retryable=False/retries=0")

    # ---- 未注册 ----
    res = reg.execute(_call("nope"), run_id="run1")
    check(res.status == "FATAL_ERROR" and res.error_code == "TOOL_NOT_FOUND",
          "未注册工具 → TOOL_NOT_FOUND")

    # ---- 路由越权 ----
    res = reg.execute(_call("ok_tool"), route="DB_LOOKUP", run_id="run1")
    check(res.error_code == "TOOL_NOT_ALLOWED", "路由越权 → TOOL_NOT_ALLOWED")

    # ---- 参数错误 ----
    res = reg.execute(_call("ok_tool", args={"q": "x", "evil": 1}), run_id="run1")
    check(res.error_code == "INVALID_ARGUMENTS", "未知参数 → INVALID_ARGUMENTS")

    # ---- 重试（retryable_only + RETRYABLE_ERROR）----
    calls = {"n": 0}
    def flaky(a):
        calls["n"] += 1
        return C.ToolResult(
            call_id="", tool_name="flaky", tool_version="v1",
            status="RETRYABLE_ERROR", data={}, error_code="TOOL_TIMEOUT",
            trace_id=uuid.uuid4().hex)
    reg.register(_spec("flaky", retry_policy="retryable_only"), flaky)
    res = reg.execute(_call("flaky"), run_id="run1", max_retries=2)
    check(res.status == "RETRYABLE_ERROR" and res.retries == 2 and calls["n"] == 3,
          "retryable 错误按 max_retries 重试（3 次尝试）")

    # ---- 非 retryable 错误不重试 ----
    calls_f = {"n": 0}
    def fatal(a):
        calls_f["n"] += 1
        return C.ToolResult(
            call_id="", tool_name="fatal", tool_version="v1",
            status="FATAL_ERROR", data={}, error_code="INTERNAL_ERROR",
            trace_id=uuid.uuid4().hex)
    reg.register(_spec("fatal", retry_policy="retryable_only"), fatal)
    res = reg.execute(_call("fatal"), run_id="run1", max_retries=2)
    check(res.retries == 0 and calls_f["n"] == 1, "FATAL_ERROR 不重试")

    # ---- 超时 ----
    def slow(a):
        time.sleep(0.2)
        return C.ToolResult(call_id="", tool_name="slow", tool_version="v1",
                            status="SUCCESS", data={}, trace_id=uuid.uuid4().hex)
    reg.register(_spec("slow", timeout_ms=10), slow)
    res = reg.execute(_call("slow"), run_id="run1")
    check(res.status == "RETRYABLE_ERROR" and res.error_code == "TOOL_TIMEOUT",
          "超时 → TOOL_TIMEOUT（RETRYABLE_ERROR）")

    # ---- executor 非 ToolResult ----
    reg.register(_spec("badret"), lambda a: "not a result")
    res = reg.execute(_call("badret"), run_id="run1")
    check(res.error_code == "TOOL_CONTRACT_ERROR", "executor 返回非 ToolResult → 契约错误")

    # ---- executor 抛异常 ----
    def boom(a):
        raise RuntimeError("boom")
    reg.register(_spec("boom"), boom)
    res = reg.execute(_call("boom"), run_id="run1")
    check(res.status == "FATAL_ERROR" and res.error_code == "INTERNAL_ERROR",
          "executor 抛异常 → INTERNAL_ERROR")

    # ---- audit 落盘（call_id 唯一文件名）----
    res = reg.execute(_call("ok_tool"), run_id="run2")
    audit_files = list((Path(audit_dir) / "run2").glob("*.jsonl"))
    check(len(audit_files) == 1 and audit_files[0].stem == res.call_id,
          "audit 以 call_id 唯一文件名落盘")

    # ---- audit fail-closed ----
    blocked = Path(tempfile.mkdtemp(prefix="eval_registry_bad_"))
    (blocked / "run_x").write_text("占位文件，使 mkdir 失败", encoding="utf-8")
    reg_bad = R.ToolRegistry(audit_dir=blocked)
    reg_bad.register(_spec("ok_tool"), lambda a: C.ToolResult(
        call_id="", tool_name="ok_tool", tool_version="v1", status="SUCCESS",
        data={}, trace_id=uuid.uuid4().hex))
    res = reg_bad.execute(_call("ok_tool"), run_id="run_x")
    check(res.status == "FATAL_ERROR" and res.error_code == "INTERNAL_ERROR",
          "audit 落盘失败 → fail-closed INTERNAL_ERROR")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
