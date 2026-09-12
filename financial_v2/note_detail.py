"""B2 财务附注明细结构化接口（纯确定性，无 I/O / LLM / OCR / 附注自由文本解析）。

任务书 §13.4 / §16 B / §17.1：财务数字必须先结构化再发布；「表头在前块、成本数字在
后块」要恢复表题单位和列，成本不能被采纳为收入；金额相同不意味着指标相同。本模块把
B1 的完整表格上下文（TableContext，表题 + 表头 + 表体 + 单位 + 期间 + 行列来源，跨页已
由 material_bundle.expand_table_context 合并）确定性解析为结构化附注明细项：

- 表题 → 收入/成本类别（classify_revenue_cost，科目代码优先于表题）与科目（item_code hint）；
- 表头 → 标签列 / 金额列 / 占比列，并把金额列绑定到期间（表头年份）；
- 表体行 → 业务板块（行标签）+ 金额（Decimal 归一化为元，单位换算版本化）；
- 每项绑定 FinancialNumberIdentity（§13.4 数值身份：主体/期间/期间类型/口径/币种/科目/
  业务板块/金额列/收入成本类别/版本/行列来源）。

明确不在本模块做 PDF 附注自由文本 / OCR 解析（表题→金额对位来自已结构化的 TableContext；
多级表头 / 跨页续表由 B1 完成）。本模块是把已恢复表格上下文结构化并对齐数值身份的纯函数，
是 A5 数字身份 / 收入成本校验接入的桥梁，不被塞进工具适配层或章节 worker。

CLI: python -m financial_v2.note_detail --self-check
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from financial_v2 import normalization as norm
from financial_v2.number_identity import (
    FinancialNumberIdentity,
    check_revenue_cost_label,
    classify_revenue_cost,
    comparison_relation,
)

# ---------------------------------------------------------------------------
# 表结构协议（B1 的 TableContext 结构化满足，避免 financial_v2 → sections 依赖）
# ---------------------------------------------------------------------------


class NoteTable(Protocol):
    """parse_note_table 所需的最小表格结构接口（TableContext 结构化满足）。"""

    table_id: str
    table_title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    period: str | None
    currency: str | None
    unit: str | None
    footnotes: tuple[str, ...]
    row_column_source: str | None
    continued_from: str | None
    truncated: bool


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 标签列表头词（命中即视为「行标签」列，默认第 0 列）。
_LABEL_HEADER_WORDS = (
    "项目", "科目", "指标", "名称", "产品", "业务", "板块", "客户", "区域",
    "用途", "主体", "地点", "内容", "资金来源", "账龄", "担保", "受限",
)

# 占比/比例列表头信号。
_PERCENT_HEADER_WORDS = ("占比", "比例", "%")

# 展示单位 → 标准单位别名（financial_v2 单位表用 *_yuan）。
_UNIT_ALIASES = {
    "元": "yuan", "万元": "wan_yuan", "千元": "qian_yuan", "亿元": "yi_yuan",
    "百万元": "baiwan_yuan", "千万元": "qianwan_yuan",
    "yuan": "yuan", "wan_yuan": "wan_yuan", "qian_yuan": "qian_yuan",
    "yi_yuan": "yi_yuan", "baiwan_yuan": "baiwan_yuan", "qianwan_yuan": "qianwan_yuan",
}

# 空值/不可得 token（出现在数值位，表示无数据）。
_EMPTY_VALUE_TOKENS = {"", "—", "-", "–", "－", "/", "--", "——", "不适用", "无", "n/a", "N/A", "null"}

# 主报告期表头词（绑定为 table.period 本身）。
_MAIN_PERIOD_HEADER_WORDS = ("本期", "本期末", "报告期", "期末", "本年", "本年度", "当期末")

_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})")


# ---------------------------------------------------------------------------
# 结构化附注明细项 + 解析结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StructuredNoteItem:
    """一条结构化附注明细（一行 × 一金额/占比列）。

    value 为归一化为元后的 Decimal（占比列为占比数值，不乘单位）；raw_value 保留原文，
    raw_unit 为标准单位（占比列为 None）。identity 是 §13.4 数值身份，可交给 A5 的
    check_revenue_cost_label / comparison_relation 做「成本不能被采纳为收入」与冲突判定。
    """

    note_id: str
    identity: FinancialNumberIdentity
    value: Decimal | None
    raw_value: str
    raw_unit: str | None
    row_label: str
    row_index: int
    column_label: str
    period: str | None
    amount_column: str          # "金额" | "占比"


@dataclass(frozen=True)
class NoteTableParseResult:
    """一张附注表的解析结果（表题身份 + 明细项 + 解析问题 + 截断透传）。"""

    table_id: str
    table_title: str
    revenue_cost_category: str | None
    items: tuple[StructuredNoteItem, ...]
    parse_issues: tuple[dict, ...]
    skipped_rows: int
    truncated: bool
    unit: str | None


# ---------------------------------------------------------------------------
# 纯函数：表头分类 / 行拆分 / 数值 / 期间绑定
# ---------------------------------------------------------------------------

def _canonical_unit(unit: str | None) -> str | None:
    """展示单位 → 标准单位（未知返回 None，拒绝换算）。"""
    if not unit:
        return None
    return _UNIT_ALIASES.get(unit.strip(), unit.strip() if unit.strip() in norm.UNIT_TO_YUAN else None)


def _is_label_header(h: str) -> bool:
    return any(w in h for w in _LABEL_HEADER_WORDS)


def _is_percent_header(h: str) -> bool:
    return any(w in h for w in _PERCENT_HEADER_WORDS)


def _value_columns(headers: tuple[str, ...]) -> list[tuple[int, str, str]]:
    """把表头展开为「有序数值列」 [(header_index, header_text, kind)]，kind ∈ {amount, percent}。

    标签列 = 首个命中标签词的表头（缺省第 0 列）；其余列按 占比/金额 分类，但**保留原始列序**
    —— 多级表头扁平化后金额/占比可能交错（如「项目 2025年金额 2025年占比 2024年金额 …」），
    数据行的数值 token 也按同一交错顺序排列，必须按列位对号入座，不能先金额后占比分组。
    """
    hs = list(headers)
    if not hs:
        return []
    label_idx = next((i for i, h in enumerate(hs) if _is_label_header(h)), 0)
    cols: list[tuple[int, str, str]] = []
    for i, h in enumerate(hs):
        if i == label_idx:
            continue
        kind = "percent" if _is_percent_header(h) else "amount"
        cols.append((i, h, kind))
    return cols


def _is_empty_value(token: str) -> bool:
    """是否为显式空值/不可得标记（区别于「非空但解析失败」）。"""
    return (token or "").strip() in _EMPTY_VALUE_TOKENS


def _clean_number(token: str) -> Decimal | None:
    """把数值 token 解析为 Decimal（去千分位逗号；空值/不可得 → None）。"""
    t = (token or "").strip().replace(",", "").replace("，", "")
    if t in _EMPTY_VALUE_TOKENS:
        return None
    if t.endswith("%"):
        t = t[:-1].strip()
    try:
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _period_from_header(header: str, main_period: str | None, period_type: str) -> str | None:
    """表头列 → 报告期（年度用 ``{year}-12-31``；主报告期词 → main_period；否则 None）。"""
    h = (header or "").strip()
    if any(w in h for w in _MAIN_PERIOD_HEADER_WORDS):
        return main_period
    m = _YEAR_RE.search(h)
    if m:
        year = m.group(1)
        if period_type == "annual":
            return f"{year}-12-31"
        return f"{year}-12-31" if not main_period else f"{year}-{main_period[5:7]}-{main_period[8:10]}"
    return None


def _row_tokens(row: tuple[str, ...]) -> list[str]:
    """把一行转为 token 列表：单格行按空白拆分（B1 单格表体），多格行按列直取。"""
    if len(row) == 1:
        return (row[0] or "").split()
    return [c.strip() for c in row if c and c.strip()]


def _note_id(identity: FinancialNumberIdentity, raw_value: str, raw_unit: str | None,
             row_label: str, row_index: int, column_label: str) -> str:
    raw = json.dumps({
        "identity": identity.to_dict(),
        "raw_value": raw_value,
        "raw_unit": raw_unit,
        "row_label": row_label,
        "row_index": row_index,
        "column_label": column_label,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "note-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse_note_table(
    table: NoteTable,
    *,
    subject: str,
    scope: str = "consolidated",
    currency: str = "CNY",
    item_code: str | None = None,
    version: str | None = None,
    period_type: str = "annual",
) -> NoteTableParseResult:
    """把一张已恢复表格上下文的附注表解析为结构化明细项（纯函数）。

    - 表题/科目 → 收入成本类别；金额列值归一化为元（unit_to_yuan），占比列存占比数值；
    - 行标签 → 业务板块；金额列表头 → 期间绑定；
    - 行缺少足够数值列 → skipped_rows（如节标题「（一）分产品」）；数值位无法解析 →
      parse_issues（不静默丢数据）；
    - truncated 由 table 透传（诚实声明，本函数不自推断）。
    """
    unit_canonical = _canonical_unit(table.unit)
    mult = norm.unit_to_yuan(unit_canonical) if unit_canonical else None
    category = classify_revenue_cost(item_code=item_code, table_title=table.table_title)
    value_cols = _value_columns(table.headers)
    n_values = len(value_cols)

    items: list[StructuredNoteItem] = []
    parse_issues: list[dict] = []
    skipped = 0

    for row_idx, row in enumerate(table.rows):
        tokens = _row_tokens(row)
        if n_values == 0:
            # 无金额/占比列可绑定（仅标签列或空表头）→ 跳过，不产明细。
            skipped += 1
            continue
        if len(tokens) < n_values + 1:
            # 不足「标签 + 数值列」→ 视为非数据行（节标题/合计说明），跳过不报错。
            skipped += 1
            continue

        label = " ".join(tokens[:-n_values]).strip()
        value_tokens = tokens[-n_values:]

        for col_i, (hdr_idx, hdr_text, kind) in enumerate(value_cols):
            raw = value_tokens[col_i] if col_i < len(value_tokens) else ""
            val = _clean_number(raw)
            if raw.strip() and val is None and not _is_empty_value(raw):
                parse_issues.append({
                    "row_index": row_idx, "column": hdr_text,
                    "reason": f"{kind}_parse_failed", "raw_value": raw,
                })
            period = _period_from_header(hdr_text, table.period, period_type)
            if kind == "amount":
                value_yuan = val * mult if (val is not None and mult is not None) else None
                identity = FinancialNumberIdentity(
                    subject=subject, period=period, period_type=period_type,
                    scope=scope, currency=currency, item_code=item_code,
                    business_segment=label or None, amount_column="金额",
                    revenue_cost_category=category, version=version,
                    row_column_source=table.row_column_source,
                )
                items.append(StructuredNoteItem(
                    note_id=_note_id(identity, raw, unit_canonical, label, row_idx, hdr_text),
                    identity=identity, value=value_yuan, raw_value=raw,
                    raw_unit=unit_canonical, row_label=label, row_index=row_idx,
                    column_label=hdr_text, period=period, amount_column="金额"))
            else:
                identity = FinancialNumberIdentity(
                    subject=subject, period=period, period_type=period_type,
                    scope=scope, currency=currency, item_code=item_code,
                    business_segment=label or None, amount_column="占比",
                    revenue_cost_category=category, version=version,
                    row_column_source=table.row_column_source,
                )
                items.append(StructuredNoteItem(
                    note_id=_note_id(identity, raw, unit_canonical, label, row_idx, hdr_text),
                    identity=identity, value=val, raw_value=raw,
                    raw_unit=None, row_label=label, row_index=row_idx,
                    column_label=hdr_text, period=period, amount_column="占比"))

    return NoteTableParseResult(
        table_id=table.table_id, table_title=table.table_title,
        revenue_cost_category=category, items=tuple(items),
        parse_issues=tuple(parse_issues), skipped_rows=skipped,
        truncated=table.truncated, unit=unit_canonical)


# ---------------------------------------------------------------------------
# 附注明细项 → A5 数字身份校验桥
# ---------------------------------------------------------------------------

def note_item_check_label(item: StructuredNoteItem, claimed_label: str | None) -> str:
    """附注明细项的收入/成本类别 vs claim 标签（包装 check_revenue_cost_label）。

    成本明细被 claim 标「收入」→ "mismatch"（阻断采纳），是 §17.1「成本不能被采纳为
    收入」的确定性判定。
    """
    return check_revenue_cost_label(item.identity, claimed_label)


def note_item_relation(a: StructuredNoteItem, b: StructuredNoteItem) -> str:
    """两条附注明细项的关系判定（包装 comparison_relation，值已归一化为元）。

    同口径不同值 → CONFLICT；收入 vs 成本 → DIFFERENT_ITEM；仅期间不同 → DIFFERENT_PERIOD；
    单位已归一化，等价不误报。
    """
    return comparison_relation(a.identity, a.value, "yuan",
                               b.identity, b.value, "yuan")


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.note_detail",
        description="财务附注明细结构化接口自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # NDSD_KCZ_2026 表5-11 主营业务成本构成表（实际错误 fixture，仅自检）。
        class _T:
            table_id = "t5-11"
            table_title = "表5-11 主营业务成本构成表"
            headers = ("项目", "2024年", "2025年")
            rows = (
                ("动力电池  19,246,128.2  24,106,439.7",),
                ("储能电池  8,000,000.0  9,500,000.0",),
            )
            period = "2025-12-31"
            currency = "CNY"
            unit = "万元"
            footnotes = ()
            row_column_source = "P50 表5-11"
            continued_from = None
            truncated = False

        res = parse_note_table(
            _T(), subject="宁德时代", scope="consolidated", currency="CNY",
            item_code="OPERATING_COST", period_type="annual")
        dyn = [it for it in res.items if it.row_label == "动力电池"]
        dyn_2025 = next((it for it in dyn if it.period == "2025-12-31"), None)
        print(json.dumps({
            "revenue_cost_category": res.revenue_cost_category,
            "item_count": len(res.items),
            "parse_issues": list(res.parse_issues),
            "skipped_rows": res.skipped_rows,
            "unit": res.unit,
            "dongli_2025_value_yuan": str(dyn_2025.value) if dyn_2025 else None,
            "dongli_2025_period": dyn_2025.period if dyn_2025 else None,
            "dongli_2025_segment": dyn_2025.identity.business_segment if dyn_2025 else None,
            "dongli_2025_claimed_revenue": note_item_check_label(dyn_2025, "revenue")
            if dyn_2025 else None,
            "dongli_2025_claimed_cost": note_item_check_label(dyn_2025, "cost")
            if dyn_2025 else None,
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
