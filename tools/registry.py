"""Tool Registry：唯一工具执行入口（参数校验 + 路由门控 + 超时 + 重试 + audit）。

Harness 只能通过 `ToolRegistry.execute` 执行工具，不得直接 import 适配器。

硬约束（PHASE3 任务书 §6.2）：
- 参数先按 ToolSpec.input_schema 校验，未知字段默认拒绝；
- `allowed_routes` 越权 → TOOL_NOT_ALLOWED；
- 重试只针对明确 retryable 错误（retry_policy=retryable_only 且状态 RETRYABLE_ERROR）；
  参数错误、权限错误、契约错误、未注册一律不重试；
- 超时软熔断：某 (run_id, tool_name) 超时后标记 circuit-open，后续同 run_id 同工具调用
  直接 TOOL_CIRCUIT_OPEN（不启动新线程、不重试）；不同 run_id 互不影响；
- 工具日志（audit）落盘失败 fail-closed：不产生无审计 ToolResult；
- EMPTY 是合法结果，不得改写为「未发现风险」。

CLI: python -m tools.registry list
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tools import contracts as C

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_DIR = Path("logs/tools")

# executor 签名：接收 arguments -> 返回 ToolResult（或抛异常）。
ToolExecutor = Callable[[dict], C.ToolResult]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _synthetic_result(call: C.ToolCall, status: str, error_code: str,
                      message: str) -> C.ToolResult:
    """非 executor 路径（未注册/越权/参数错/超时/内部错）的结构化结果。"""
    return C.ToolResult(
        call_id=call.call_id, tool_name=call.tool_name, tool_version="",
        status=status, data={}, error_code=error_code, message=message,
        retryable=(status == "RETRYABLE_ERROR"), retries=0,
        trace_id=uuid.uuid4().hex)


class ToolRegistry:
    """工具注册表：注册 spec+executor，execute 是唯一执行入口。"""

    def __init__(self, audit_dir: Path | str = DEFAULT_AUDIT_DIR):
        self._specs: dict[str, C.ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}
        self._audit_dir = Path(audit_dir)
        # 软超时熔断：某 (run_id, tool_name) 一旦超时，该 run_id 内该工具标记 circuit-open，
        # 后续同 run_id 同工具调用直接返回 TOOL_CIRCUIT_OPEN，不再启动新线程。
        self._poisoned: set[tuple[str, str]] = set()

    # -- 注册 ---------------------------------------------------------------

    def register(self, spec: C.ToolSpec, executor: ToolExecutor) -> None:
        if spec.name in self._specs:
            raise C.ToolValidationError(f"工具重复注册: {spec.name}")
        if spec.version == "":
            raise C.ToolValidationError(f"工具 {spec.name} 缺少 version")
        if spec.retry_policy not in C.RETRY_POLICIES:
            raise C.ToolValidationError(
                f"工具 {spec.name} retry_policy 非法: {spec.retry_policy}")
        if spec.cost_class not in C.COST_CLASSES:
            raise C.ToolValidationError(
                f"工具 {spec.name} cost_class 非法: {spec.cost_class}")
        for r in spec.allowed_routes:
            if r not in routing_routes():
                raise C.ToolValidationError(
                    f"工具 {spec.name} allowed_routes 含未知路由: {r}")
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def get(self, name: str) -> C.ToolSpec | None:
        return self._specs.get(name)

    def list(self) -> list[C.ToolSpec]:
        return sorted(self._specs.values(), key=lambda s: s.name)

    # -- 执行 ---------------------------------------------------------------

    def execute(self, call: C.ToolCall, *, route: str | None = None,
                run_id: str = "cli", max_retries: int = 0) -> C.ToolResult:
        """执行一次工具调用（含路由门控、参数校验、超时与重试），并落盘 audit。

        - route 为当前 InformationNeed 的 RouteDecision.route（用于 allowed_routes 门控）；
        - max_retries 为重试上限（重试只发生在 retryable_only 且 RETRYABLE_ERROR）；
        - 超时软熔断：超时后该 (run_id, tool_name) 被标记 circuit-open，后续同 run_id 同工具
          调用直接 TOOL_CIRCUIT_OPEN，不启动新线程、不重试；
        - audit 落盘失败 fail-closed：返回 INTERNAL_ERROR，不产生无审计结果。
        """
        spec = self._specs.get(call.tool_name)
        if spec is None:
            result = _synthetic_result(
                call, "FATAL_ERROR", "TOOL_NOT_FOUND",
                f"未注册工具: {call.tool_name}")
            return self._audit_or_fail_closed(call, route, run_id, result, [])

        if route is not None and spec.allowed_routes and route not in spec.allowed_routes:
            result = _synthetic_result(
                call, "FATAL_ERROR", "TOOL_NOT_ALLOWED",
                f"工具 {call.tool_name} 不允许在路由 {route} 下调用")
            return self._audit_or_fail_closed(call, route, run_id, result, [])

        try:
            C.enforce_arguments(spec, call.arguments)
        except C.ToolValidationError as e:
            result = _synthetic_result(
                call, "FATAL_ERROR", "INVALID_ARGUMENTS", str(e))
            return self._audit_or_fail_closed(call, route, run_id, result, [])

        # 软超时熔断：同一 run_id 内该工具已超时 → 直接拒绝，不启动新线程。
        if (run_id, call.tool_name) in self._poisoned:
            result = _synthetic_result(
                call, "FATAL_ERROR", "TOOL_CIRCUIT_OPEN",
                f"工具 {call.tool_name} 在 run_id={run_id} 内已超时熔断，拒绝再次执行")
            return self._audit_or_fail_closed(call, route, run_id, result, [])

        attempts: list[C.ToolResult] = []
        attempt = 0
        final: C.ToolResult
        while True:
            attempt += 1
            raw = self._run_attempt(spec, call)
            attempts.append(raw)
            final = raw
            if raw.error_code == "TOOL_TIMEOUT":
                # 软超时熔断：标记 circuit-open，且不重试（超时不触发新线程/重试）。
                self._poisoned.add((run_id, call.tool_name))
                break
            if not (raw.status == "RETRYABLE_ERROR"
                    and spec.retry_policy == "retryable_only"
                    and attempt <= max_retries):
                break

        result = dataclasses.replace(final, retries=attempt - 1)
        return self._audit_or_fail_closed(call, route, run_id, result, attempts)

    def _run_attempt(self, spec: C.ToolSpec, call: C.ToolCall) -> C.ToolResult:
        executor = self._executors[spec.name]
        t0 = time.perf_counter()
        try:
            if spec.timeout_ms <= 0:
                raw = executor(call.arguments)
            else:
                raw = self._run_with_timeout(executor, call.arguments,
                                             spec.timeout_ms / 1000.0)
        except _ExecutorTimeout:
            return _synthetic_result(
                call, "RETRYABLE_ERROR", "TOOL_TIMEOUT",
                f"工具 {call.tool_name} 超过 {spec.timeout_ms}ms 超时")
        except Exception as e:  # executor 内部异常 → 契约/内部错误，不重试
            logger.warning("工具 %s 执行异常: %s", call.tool_name, e, exc_info=True)
            return _synthetic_result(
                call, "FATAL_ERROR", "INTERNAL_ERROR",
                f"{type(e).__name__}: {e}")

        latency_ms = int((time.perf_counter() - t0) * 1000.0)
        if not isinstance(raw, C.ToolResult):
            return _synthetic_result(
                call, "FATAL_ERROR", "TOOL_CONTRACT_ERROR",
                f"工具 {call.tool_name} executor 返回非 ToolResult: "
                f"{type(raw).__name__}")

        return dataclasses.replace(
            raw, call_id=call.call_id, tool_name=spec.name,
            tool_version=spec.version, latency_ms=latency_ms,
            retryable=(raw.status == "RETRYABLE_ERROR"), retries=0,
            trace_id=raw.trace_id or uuid.uuid4().hex)

    @staticmethod
    def _run_with_timeout(executor: ToolExecutor, arguments: dict,
                          timeout_s: float) -> C.ToolResult:
        # 软超时：ThreadPoolExecutor.shutdown(wait=False) 无法取消正在运行的线程，
        # 只能做到「不再等待孤儿线程」。语义边界：
        #   - 超时后本方法抛 _ExecutorTimeout，由 execute 标记 (run_id, tool_name) circuit-open；
        #   - 孤儿线程的迟到结果被 Future 丢弃，不会作为成功结果回传，也不会触发重试；
        #   - 真正的运行时间上限依赖底层 HTTP connect/read timeout（external_v2.fetch/providers）
        #     与 snapshot 幂等去重（Store 唯一约束 + SELECT-first 复用），本层不引入进程池/Celery。
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(executor, arguments)
            try:
                return fut.result(timeout=timeout_s)
            except FuturesTimeout:
                raise _ExecutorTimeout()
        finally:
            pool.shutdown(wait=False)

    # -- audit --------------------------------------------------------------

    def _audit_or_fail_closed(self, call: C.ToolCall, route: str | None,
                              run_id: str, result: C.ToolResult,
                              attempts: list[C.ToolResult]) -> C.ToolResult:
        """统一 audit fail-closed 语义：audit 成功返回原结果，失败返回 INTERNAL_ERROR。

        所有 execute 返回路径（含未注册/越权/参数错等提前返回）都必须经过本方法，
        保证「未审计的结果不得被报告为正常结果」。
        """
        if self._audit(call, route, run_id, result, attempts):
            return result
        return _synthetic_result(
            call, "FATAL_ERROR", "INTERNAL_ERROR",
            "工具 audit 落盘失败（fail-closed，丢弃本次结果）")

    def _audit(self, call: C.ToolCall, route: str | None, run_id: str,
               result: C.ToolResult, attempts: list[C.ToolResult]) -> bool:
        """落盘一条 audit 记录到 logs/tools/<run_id>/<call_id>.jsonl。

        文件名用 call_id（唯一），同一 call_id 不会互相覆盖。返回是否成功。
        """
        record = {
            "timestamp": _now_iso(),
            "call_id": call.call_id,
            "tool_name": call.tool_name,
            "tool_version": result.tool_version,
            "run_id": run_id,
            "route": route,
            "need_id": call.need_id,
            "batch_id": call.batch_id,
            "idempotency_key": call.idempotency_key,
            "arguments": call.arguments,
            "status": result.status,
            "error_code": result.error_code,
            "message": result.message,
            "latency_ms": result.latency_ms,
            "cost": result.cost,
            "retries": result.retries,
            "attempts": [
                {"attempt": i + 1, "status": a.status, "error_code": a.error_code,
                 "message": a.message, "latency_ms": a.latency_ms}
                for i, a in enumerate(attempts)
            ],
            "trace_id": result.trace_id,
        }
        try:
            out_dir = self._audit_dir / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{call.call_id}.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.error("工具 audit 落盘失败: %s", e, exc_info=True)
            return False


class _ExecutorTimeout(Exception):
    """Registry 层软超时信号。"""


def routing_routes() -> tuple[str, ...]:
    """五路由白名单（延迟导入，避免 registry 顶层依赖 routing 的循环）。"""
    from routing import schema as routing_schema
    return routing_schema.ROUTES


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m tools.registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出已注册工具")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.cmd == "list":
        try:
            from tools import adapters
            registry = adapters.build_default_registry()
        except ImportError:
            logger.info("adapters 未就绪，展示空注册表")
            registry = ToolRegistry()

        specs = registry.list()
        if not specs:
            print("（无已注册工具）")
            return 0
        for s in specs:
            routes = ",".join(s.allowed_routes) or "*"
            print(f"{s.name} {s.version}  routes=[{routes}]  "
                  f"retry={s.retry_policy}  cost={s.cost_class}  "
                  f"max={s.max_results}  timeout={s.timeout_ms}ms")
        return 0

    return 2


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
