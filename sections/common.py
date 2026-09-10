"""Phase 4 章节 Worker 公共助手（财务/公司/行业 Worker 共用，无 I/O、无 LLM）。

只放纯确定性工具：版本常量、科目/公式展示名映射、金额格式化、marker 解析、数字复核。
- 科目展示名来自 financial_v2.mapping.build_builtin_rules()（V1 中文名单一来源，不二次硬编码）。
- 公式展示名来自 financial_v2.formulas.build_registry()。
- 金额单位：SnapshotItem.amount 已归一化为「元」（financial_v2.normalization std_unit="yuan"），
  展示时按量级换算为 亿元/万元/元，权威值保留元 Decimal，供人工复核表逐项对齐。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from financial_v2 import formulas, mapping

# 版本常量（变更必须递增；进入 section_version / dependency_fingerprint 派生）
RENDERER_VERSION = "p4-fin-renderer-v1"
RULES_VERSION = "p4-fin-rules-v1"
PROMPT_VERSION = "section_financial_v2"
WORKER_VERSION = "p4-fin-worker-v1"

# 契约固化的政策常量（非财务计算值，LLM 可原文引用，不计入「LLM 算了数字」）。
# 重大科目关注阈值 15%（contracts standard_v2.yaml），近三年观察窗口。
POLICY_CONSTANT_TOKENS = ("15%", "15", "百分之十五")

_TWO_DP = Decimal("0.01")

_UNIT_LABELS = {
    "yuan": "元",
    "wan_yuan": "万元",
    "qian_yuan": "千元",
    "yi_yuan": "亿元",
    "baiwan_yuan": "百万元",
    "qianwan_yuan": "千万元",
    "unknown": "元",
}


def item_label_map() -> dict[str, str]:
    """standard_item_code → 中文展示名（单一来源 V1 中文名，经 mapping 内置规则）。"""
    rules = mapping.build_builtin_rules()
    return {
        r.standard_item_code: (r.aliases[0] if r.aliases else r.standard_item_code)
        for r in rules
    }


def formula_name_map() -> dict[str, str]:
    """formula_id → 中文展示名。"""
    return {fid: fd.name for fid, fd in formulas.build_registry().items()}


def unit_label(unit: str | None) -> str:
    return _UNIT_LABELS.get(unit or "", unit or "元")


def _strip_decimal(d: Decimal) -> str:
    """Decimal → 紧凑十进制文本（去尾部零，但保留必要小数，如 15.23 / 15 / 4310.15）。"""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _grouped(d: Decimal) -> str:
    """Decimal → 千分位 + 2 位小数（ROUND_HALF_UP），去尾部零。"""
    q = d.quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    s = f"{q:,.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def format_yuan_amount(value: Decimal) -> str:
    """把「元」金额换算为可读字符串（≥1亿 → 亿元，≥1万 → 万元，否则元）。"""
    if value is None:
        return ""
    if abs(value) >= Decimal("100000000"):
        return f"{_grouped(value / Decimal('100000000'))}亿元"
    if abs(value) >= Decimal("10000"):
        return f"{_grouped(value / Decimal('10000'))}万元"
    return f"{_grouped(value)}元"


def format_metric_display(formula_id: str, display_value: Decimal) -> str:
    """按公式展示单位渲染指标 display_value（percent → ×100 后加 %，ratio 无量纲，yuan 金额）。

    注意：display_value 已是 formulas.round_display 的产物（percent 已 ×100），此处只加单位。
    """
    if display_value is None:
        return ""
    kind = formulas.unit_for(formula_id)
    if kind == "percent":
        return f"{_strip_decimal(display_value)}%"
    if kind == "yuan":
        return format_yuan_amount(display_value)
    return _strip_decimal(display_value)  # ratio 无量纲倍数


# ---------------------------------------------------------------------------
# marker 解析 + 数字复核（LLM 只写 [[fact_id]] 占位，禁止手写数字）
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"\[\[([A-Za-z0-9_.\-]+)\]\]")
_NUMBER_RE = re.compile(r"\d")


def bare_number_tokens(text: str) -> list[str]:
    """返回文本中出现在 marker 之外的数字子串（用于 fail-closed 复核）。

    把 [[...]] 占位整体剔除后再找数字：LLM 手写的任何数字（百分比/金额/比率）都会被捕获。
    """
    without_markers = _MARKER_RE.sub("", text)
    # 命中政策常量（15% 阈值等）不计入「LLM 算了数字」。
    cleaned = without_markers
    for tok in POLICY_CONSTANT_TOKENS:
        cleaned = cleaned.replace(tok, "")
    return _NUMBER_RE.findall(cleaned)


def resolve_markers(text: str, fact_by_id: dict[str, str]) -> tuple[str, list[str]]:
    """把 [[fact_id]] 占位替换为该 fact 的 display 字符串；返回 (resolved_text, errors)。

    未解析的 fact_id 计入 errors（fail-closed：绝不静默保留占位）。
    """
    errors: list[str] = []

    def _sub(m):
        fid = m.group(1)
        disp = fact_by_id.get(fid)
        if disp is None:
            errors.append(f"unresolved_fact_id:{fid}")
            return f"[[{fid}]]"
        return disp

    resolved = _MARKER_RE.sub(_sub, text)
    return resolved, errors
