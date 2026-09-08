"""Phase 3 Tool Layer 公共契约（dataclass + 枚举白名单 + 参数校验）。

本模块只定义声明式数据结构与校验逻辑，不含 I/O、不含业务计算、不调用任何工具。
所有工具适配器 / Registry / Harness 共同引用同一份字段语义。

对齐（PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md §6.2）：
- ToolSpec / ToolCall / ToolResult；
- 状态五态：SUCCESS | PARTIAL | EMPTY | RETRYABLE_ERROR | FATAL_ERROR；
- 最低 13 个错误码；
- 参数按 input_schema 校验，未知字段默认拒绝（additionalProperties=false）。

工具名白名单与 contracts.schema.ALLOWED_CAPABILITIES 对齐，但 `search_tables` 为
能力门控的可选工具（无可靠表格结构时返回 EMPTY/UNSUPPORTED_FOR_DOCUMENT），
`verify_claim` 属 Phase 4/5 边界，本阶段不实现、也不注册占位工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from routing import schema as routing_schema

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

TOOL_LAYER_VERSION = "v1"

# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# 首批工具名（8 个核心 + 1 个能力门控可选工具）。
TOOL_NAMES = (
    "search_evidence",
    "inspect_evidence",
    "lookup_company_field",
    "lookup_financial_metric",
    "compare_evidence",
    "compare_financial_periods",   # 同公式跨期间 MetricResult 结构化比较（纯 Python，不算新指标）
    "search_external_sources",
    "fetch_external_content",
    "snapshot_external_source",
    "search_tables",  # 能力门控：无表格结构 → EMPTY/UNSUPPORTED_FOR_DOCUMENT
)

# 工具结果状态（固定五态）。
TOOL_STATUSES = (
    "SUCCESS",
    "PARTIAL",
    "EMPTY",
    "RETRYABLE_ERROR",
    "FATAL_ERROR",
)

# 仅明确可重试的错误才允许重试（retryable_only）。
RETRYABLE_STATUSES = ("RETRYABLE_ERROR",)

# 重试策略。
RETRY_POLICIES = ("none", "retryable_only")

# 成本类别。
COST_CLASSES = ("local", "db", "external")

# 最低错误码（任务书 §6.2）。
TOOL_ERROR_CODES = (
    "INVALID_ARGUMENTS",
    "TOOL_NOT_ALLOWED",
    "TOOL_NOT_FOUND",
    "TOOL_CONTRACT_ERROR",
    "TOOL_TIMEOUT",
    "RETRIEVAL_EMPTY",
    "DB_FIELD_UNAVAILABLE",
    "EXTERNAL_SEARCH_UNAVAILABLE",
    "EXTERNAL_FETCH_BLOCKED",
    "EXTERNAL_CONTENT_EMPTY",
    "EXTERNAL_SNAPSHOT_ERROR",
    "SOURCE_UNTRUSTED",
    "INTERNAL_ERROR",
)

# 工具结果附加错误码（超出最低集合，用于能力门控/外部工具细分场景，保持枚举封闭可审计）。
# 任务书 §6.3：search_tables 无结构时返回 EMPTY/UNSUPPORTED_FOR_DOCUMENT。
# 外部工具（external_v2）细分错误码：鉴权/限流/服务端/网络/坏响应/PDF 无文本层。
TOOL_EXTRA_ERROR_CODES = (
    "UNSUPPORTED_FOR_DOCUMENT",
    "EXTERNAL_AUTH_FAILED",
    "EXTERNAL_RATE_LIMITED",
    "EXTERNAL_SERVER_ERROR",
    "EXTERNAL_NETWORK_ERROR",
    "EXTERNAL_BAD_RESPONSE",
    "PDF_TEXT_UNAVAILABLE",
)

# ---------------------------------------------------------------------------
# 校验异常
# ---------------------------------------------------------------------------

class ToolValidationError(ValueError):
    """工具契约/参数校验失败（fail-closed：抛错，不降级、不猜测）。"""


# ---------------------------------------------------------------------------
# 受限 JSON Schema 子集参数校验
# ---------------------------------------------------------------------------

def _check_type(value, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    # 未知类型：fail-closed（我们的 schema 是内部受控的，出现未知 type 是契约错误）。
    raise ToolValidationError(f"schema 含未知 type: {expected}")


def _validate_property(key: str, value, prop: dict, errors: list[str]) -> None:
    t = prop.get("type")
    if t and not _check_type(value, t):
        errors.append(
            f"参数 {key} 类型错误: 期望 {t}, 实际 {type(value).__name__}")
        return

    if "enum" in prop and value not in prop["enum"]:
        errors.append(f"参数 {key} 不在允许值: {prop['enum']}")

    if t in ("integer", "number"):
        if "minimum" in prop and value < prop["minimum"]:
            errors.append(f"参数 {key} 小于下限 {prop['minimum']}")
        if "maximum" in prop and value > prop["maximum"]:
            errors.append(f"参数 {key} 大于上限 {prop['maximum']}")
    elif t == "string":
        if "minLength" in prop and len(value) < prop["minLength"]:
            errors.append(f"参数 {key} 长度小于 {prop['minLength']}")
        if "maxLength" in prop and len(value) > prop["maxLength"]:
            errors.append(f"参数 {key} 长度大于 {prop['maxLength']}")
    elif t == "array":
        if "minItems" in prop and len(value) < prop["minItems"]:
            errors.append(f"参数 {key} 元素数小于 {prop['minItems']}")
        if "maxItems" in prop and len(value) > prop["maxItems"]:
            errors.append(f"参数 {key} 元素数大于 {prop['maxItems']}")
        items = prop.get("items")
        if items:
            for i, item in enumerate(value):
                _validate_property(f"{key}[{i}]", item, items, errors)
    elif t == "object":
        sub_props = prop.get("properties", {})
        sub_additional = prop.get("additionalProperties", True)
        if sub_additional is False:
            for k in value:
                if k not in sub_props:
                    errors.append(f"参数 {key} 含未知字段: {k}")
        for k, v in value.items():
            sp = sub_props.get(k)
            if sp is not None:
                _validate_property(f"{key}.{k}", v, sp, errors)


def validate_arguments(spec: ToolSpec, arguments) -> list[str]:
    """按 ToolSpec.input_schema 校验参数，返回错误列表（空=合法）。

    未知字段默认拒绝（additionalProperties=false）。参数错误属于契约错误，
    一律 fail-closed，不重试、不降级、不猜测。
    """
    errors: list[str] = []
    if not isinstance(arguments, dict):
        return [f"arguments 必须为 object，实际为 {type(arguments).__name__}"]

    schema = spec.input_schema or {}
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    for key in required:
        if key not in arguments:
            errors.append(f"缺少必需参数: {key}")

    if additional is False:
        for key in arguments:
            if key not in properties:
                errors.append(f"未知参数: {key}")

    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None:
            continue
        _validate_property(key, value, prop, errors)

    return errors


def enforce_arguments(spec: ToolSpec, arguments: dict) -> None:
    """参数校验通过则无操作；否则抛 ToolValidationError（fail-closed）。"""
    errors = validate_arguments(spec, arguments)
    if errors:
        raise ToolValidationError("; ".join(errors))


# ---------------------------------------------------------------------------
# 公共 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """工具声明（用途/输入输出 schema/路由门控/预算与成本属性/重试策略）。"""

    name: str
    version: str
    description: str
    input_schema: dict
    output_schema: dict
    allowed_routes: tuple[str, ...]
    max_results: int
    timeout_ms: int
    retry_policy: str
    cost_class: str


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用请求（幂等键 + need/batch 归属）。"""

    call_id: str
    tool_name: str
    arguments: dict
    idempotency_key: str
    need_id: str
    batch_id: str


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结构化结果（完整结构进 State/Trace，给 LLM 只给摘要）。"""

    call_id: str
    tool_name: str
    tool_version: str
    status: str
    data: dict
    evidence_ids: list[str] = field(default_factory=list)
    structured_result_refs: list[routing_schema.StructuredResultRef] = field(
        default_factory=list)
    external_snapshot_ids: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
    latency_ms: int = 0
    cost: str = "0"
    retryable: bool = False
    retries: int = 0
    source_fingerprint: str | None = None
    trace_id: str = ""

    def is_error(self) -> bool:
        return self.status in ("RETRYABLE_ERROR", "FATAL_ERROR")

    def is_empty(self) -> bool:
        return self.status == "EMPTY"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import json

    sample_spec = ToolSpec(
        name="search_evidence", version="v1", description="本地检索",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["company_id", "query"],
            "properties": {
                "company_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1},
                "k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
        output_schema={"type": "object"},
        allowed_routes=("STANDARD_RAG", "DEEP_RETRIEVAL", "DIRECT_EVIDENCE"),
        max_results=10, timeout_ms=5000, retry_policy="none", cost_class="local",
    )

    ok_args = {"company_id": "300750", "query": "实际控制人", "k": 5}
    bad_args = {"company_id": "300750", "query": "x", "unknown": 1, "k": 99}

    print(json.dumps({
        "tool_layer_version": TOOL_LAYER_VERSION,
        "tool_names": list(TOOL_NAMES),
        "statuses": list(TOOL_STATUSES),
        "error_codes": list(TOOL_ERROR_CODES),
        "self_check": {
            "valid_args_errors": validate_arguments(sample_spec, ok_args),
            "invalid_args_errors": validate_arguments(sample_spec, bad_args),
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
