"""B1 表格上下文（纯确定性，无 I/O / LLM / RAG / OCR）：表题 + 行列 + 单位 + 期间。

任务书 §13.1 / §16 B：研究系统交付「完整材料」而非零散 ID；「表头在前块、成本数字在
后块」要恢复表题单位列。本模块提供两层能力：

1. 已结构化的表格上下文结构 ``TableContext`` 与跨页接续 ``expand_table_context``
   （受控：仅在 document_version + section_path 一致、块顺序相邻、无新表题时接续）；
2. 原始 Evidence 文本 → ``TableContext`` 的确定性抽取 ``extract_note_tables``
   （针对附注构成表格式：表题 + 单位 + 多级表头扁平化 + 表体行）。

本模块只搬运/恢复表格原文与定位，不做金额 Decimal 归一化（属 B2 note_detail 职责），
不做 PDF 附注自由文本语义解析（不含收入/成本类别判定，只保留表题/表头/单位原文）。
金额列/占比列的语义绑定由 note_detail.parse_note_table 依据扁平化表头完成。

放在 financial_v2 而非 sections：harness 的 claim 求值层需要读取本模块（sections 与
harness 存在环），且本模块零 planning/sections 依赖。

CLI: python -m financial_v2.table_context --self-check
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量 / 正则
# ---------------------------------------------------------------------------

# 新表题/表头信号（命中即视为「不同表」起点，不并入——不盲拼不同表）。
_NEW_TABLE_TITLE_RE = ("表", "项目", "科目", "指标", "单位", "金额", "占比")

# 表题：`表 5-10发行人主营业务收入构成表` / `表5-11 主营业务成本构成表`。
_TABLE_TITLE_RE = re.compile(r"^表\s*(\d+)-(\d+)\s*(.*)$")

# 单位行：`单位：万元，%` / `单位:千元`。
_UNIT_LINE_RE = re.compile(r"^单位\s*[:：]\s*(.*)$")

# 数值 token（千分位逗号 / 小数 / 百分号）。
_NUMERIC_TOKEN_RE = re.compile(r"^-?[\d,]+(\.\d+)?%?$")

# 年份（表头期间绑定，note_detail._period_from_header 同样使用 20xx/19xx）。
_YEAR_RE = re.compile(r"(20\d{2}|19\d{2})")

# 标签表头词（首行表头的行标签列）。
_LABEL_HEADER_WORDS = ("项目", "科目", "指标", "名称", "产品", "业务", "板块", "客户", "区域")

# 占比/比例列信号（二级表头）。
_PERCENT_HEADER_WORDS = ("占比", "比例", "%")

# 单位文本 → 仅保留货币单位部分（丢弃 占比/% 说明）。
_UNIT_SEPARATORS = ("，", ",", "、", "；", "%")


# ---------------------------------------------------------------------------
# 表格上下文结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableContext:
    """一张表（或其接续后的完整片段）的结构化上下文。

    保留表题、期间、币种、单位、多级表头、表体、行列来源与脚注；跨页续表用
    continued_from 指向前一块。truncated=True 表示表体被截断/无法确认完整，禁止
    据其宣称「完整覆盖」。金额不在此处解析为 Decimal —— 那是 B2 note_detail 的职责。
    """

    table_id: str
    table_title: str               # 如「表5-11 主营业务成本构成表」
    headers: tuple[str, ...]       # 表头（含多级表头扁平化后的行）
    rows: tuple[tuple[str, ...], ...]  # 表体行（每行一列字符串，可能含金额原文）
    period: str | None = None
    currency: str | None = None
    unit: str | None = None
    footnotes: tuple[str, ...] = ()
    row_column_source: str | None = None   # 物理页 + 行列定位（如「P50 表5-11」）
    continued_from: str | None = None      # 跨页续表：前块 table_id
    truncated: bool = False


# ---------------------------------------------------------------------------
# 跨页接续（受控上下文扩展）
# ---------------------------------------------------------------------------

def _looks_like_table_title(text: str) -> bool:
    """保守判断一段文本首行是否像「表题/表头」开头（命中即视为新表起点，不接续）。"""
    first = (text or "").strip().splitlines()[0].strip() if text and text.strip() else ""
    if not first:
        return False
    return any(k in first for k in _NEW_TABLE_TITLE_RE) and len(first) < 80


def _rows_from_text(text: str) -> list[tuple[str, ...]]:
    """把一段正文按行切分为表体行（每行一个字符串单元格；不做列对齐，保持原文）。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return [tuple([ln]) for ln in lines]


def expand_table_context(seed: dict, candidates: list[dict]) -> TableContext:
    """把 seed（命中块）与候选块按受控规则接续成完整表格上下文。

    接续条件（全部满足才并入）：
    - candidate.document_version == seed.document_version 且 section_path 一致；
    - candidate 的 order（或 page）在 seed 之后（相邻后续块）；
    - candidate 首行不含新表题/表头信号（不盲拼不同表）。

    不同年度/不同主体的判别不在本函数（属 B2 结构化接口的期间/主体绑定）；本函数只保证
    同一 document_version + section_path + 无新表题 的最小安全边界。truncated 由 seed 透传。
    """
    rows: list[tuple[str, ...]] = list(_rows_from_text(seed.get("text", "")))
    continued_from: str | None = None

    ordered = sorted(candidates,
                     key=lambda c: (c.get("order", c.get("page", 0) or 0))
                     if c.get("order") is not None else 1 << 30)
    for c in ordered:
        if c.get("document_version") != seed.get("document_version"):
            continue
        if c.get("section_path") != seed.get("section_path"):
            continue
        c_order = c.get("order")
        s_order = seed.get("order")
        if c_order is not None and s_order is not None:
            if c_order <= s_order:
                continue
        elif (c.get("page") or 0) <= (seed.get("page") or 0):
            continue
        text = c.get("text", "") or ""
        if _looks_like_table_title(text):
            continue
        c_rows = _rows_from_text(text)
        if c_rows:
            continued_from = continued_from or seed.get("table_id")
            rows.extend(c_rows)

    return TableContext(
        table_id=seed.get("table_id") or "table",
        table_title=seed.get("table_title", "") or "",
        headers=tuple(seed.get("headers", ()) or ()),
        rows=tuple(rows),
        period=seed.get("period"),
        currency=seed.get("currency"),
        unit=seed.get("unit"),
        footnotes=tuple(seed.get("footnotes", ()) or ()),
        row_column_source=seed.get("row_column_source"),
        continued_from=continued_from,
        truncated=bool(seed.get("truncated", False)),
    )


# ---------------------------------------------------------------------------
# 原始 Evidence 文本 → 附注构成表上下文（确定性抽取，fail-closed）
# ---------------------------------------------------------------------------

def _parse_unit(unit_text: str) -> str | None:
    """单位行内容 → 货币单位原文（丢弃 占比/% 说明）。「万元，%」→「万元」；未知→None。"""
    t = (unit_text or "").strip()
    for sep in _UNIT_SEPARATORS:
        idx = t.find(sep)
        if idx != -1:
            t = t[:idx]
    t = t.strip()
    return t or None


def _is_label_header(h: str) -> bool:
    return any(w in h for w in _LABEL_HEADER_WORDS)


def _is_percent_header(h: str) -> bool:
    return any(w in h for w in _PERCENT_HEADER_WORDS)


def _flatten_headers(level1_tokens: list[str], level2_tokens: list[str]) -> tuple[str, ...]:
    """把两级表头扁平化为单层列头。

    level1 = 标签列 + 年度列（如「项目 2025年 2024年 2023年」）；
    level2 = 每年 金额/占比 子列（如「金额 占比 金额 占比 金额 占比」，可能缺失）。
    扁平化结果：["项目", "2025年金额", "2025年占比", "2024年金额", ...]；
    无 level2 时：["项目", "2025年", "2024年", ...]。
    """
    label = level1_tokens[0] if level1_tokens else "项目"
    years = level1_tokens[1:] if len(level1_tokens) > 1 else []
    headers = [label]
    if level2_tokens:
        per_year = len(level2_tokens) // len(years) if years else 0
        for i, year in enumerate(years):
            for j in range(per_year):
                sub = level2_tokens[i * per_year + j] if i * per_year + j < len(level2_tokens) else ""
                headers.append(f"{year}{sub}" if sub else year)
    else:
        headers.extend(years)
    return tuple(headers)


def _is_data_row(tokens: list[str]) -> bool:
    """一行是否为数据行：≥2 token，首 token 为行标签（非纯数值），其余全为数值。"""
    if len(tokens) < 2:
        return False
    if _NUMERIC_TOKEN_RE.match(tokens[0] or ""):
        return False
    return all(_NUMERIC_TOKEN_RE.match(t) for t in tokens[1:])


def _table_id(num1: str, num2: str) -> str:
    raw = json.dumps(["note-table", num1, num2], separators=(",", ":"))
    return "tbl-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def extract_note_tables(blocks: list[tuple[int, str]]) -> list[tuple[TableContext, int]]:
    """从已按序排列的 evidence 文本块（(物理页, 文本)）确定性抽取附注构成表上下文。

    返回 [(TableContext, 数据行所在块下标)]，供上层把每条 fact 绑定到「含表体数值的证据块」的
    evidence_id（而非整组首块或表题块，避免一组内多表/跨块表时错绑到无数值的块）。

    识别「表题 → 单位 → 两级表头 → 数据行」的构成表块；跨块接续靠顺序扫描（表题在前块尾、
    表头/数据在后块头也能恢复）。fail-closed：

    - 无表题 / 无表头 / 无数据行 → 不产出该表（不猜）；
    - 数据行 token 数不足「标签 + 数值列」→ 视为非数据行（合计/节标题/页脚/正文），
      该行终止当前表的数据收集（不把正文拼进表）；
    - 单位缺失 → unit=None 透传（是否据此拒绝换算由 note_detail 决定）。

    收入/成本类别判定不在此做（属 note_detail / number_identity），本函数只恢复原文。
    """
    tables: list[tuple[TableContext, int]] = []
    cur: dict | None = None          # 当前表累积状态

    def _flush() -> None:
        nonlocal cur
        if cur is None:
            return
        # fail-closed：须有表题 + 一级表头（含年份，可绑定列）+ 数据行，缺任一不产表。
        if cur["title"] and cur["level1"] and cur["rows"]:
            title = cur["title"]
            num1, num2 = cur["num"]
            headers = _flatten_headers(cur["level1"], cur["level2"])
            tables.append((TableContext(
                table_id=_table_id(num1, num2),
                table_title=title,
                headers=headers,
                rows=tuple((ln,) for ln in cur["rows"]),
                period=None,
                currency=None,
                unit=cur["unit"],
                footnotes=(),
                row_column_source=f"{title} · P{cur['page']}",
                continued_from=None,
                truncated=False,
            ), cur["data_block_idx"]))
        cur = None

    for block_idx, (page, text) in enumerate(blocks):
        for line in (text or "").splitlines():
            ln = line.strip()
            if not ln:
                continue
            tm = _TABLE_TITLE_RE.match(ln)
            if tm:
                _flush()
                num1, num2, rest = tm.group(1), tm.group(2), tm.group(3).strip()
                title = f"表{num1}-{num2}" + (f" {rest}" if rest else "")
                cur = {"title": title, "num": (num1, num2), "page": page,
                       "block_idx": block_idx, "data_block_idx": block_idx,
                       "unit": None, "level1": [], "level2": [], "rows": []}
                continue
            if cur is None:
                continue
            um = _UNIT_LINE_RE.match(ln)
            if um and not cur["unit"]:
                cur["unit"] = _parse_unit(um.group(1))
                continue
            toks = ln.split()
            if not toks:
                continue
            if not cur["level1"] and _is_label_header(toks[0]) and any(
                    _YEAR_RE.search(t) for t in toks):
                cur["level1"] = toks
                continue
            if cur["level1"] and not cur["level2"] and not _is_label_header(toks[0]) and all(
                    _is_percent_header(t) or t.isdigit() or t in ("金额",) for t in toks):
                cur["level2"] = toks
                continue
            if _is_data_row(toks):
                if not cur["rows"]:
                    cur["data_block_idx"] = block_idx  # 首个数据行所在块（数值实质来源）
                cur["rows"].append(" ".join(toks))
                continue
            # 非数据行（正文/节标题/页脚/新表前导）→ 结束当前表。
            _flush()

    _flush()
    return tables


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.table_context",
        description="表格上下文（表题/行列/单位/期间恢复）自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # NDSD_KCZ_2026 表5-10（整表单块）/ 表5-11（跨块）真实文本结构。
        blocks = [
            (50,
             "（二）主营业务情况\n1、主营业务收入分析\n\n"
             "表 5-10发行人主营业务收入构成表\n\n单位：万元，%\n"
             "  项目  2025年  2024年  2023年\n  金额  占比  金额  占比  金额  占比\n\n"
             "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n"
             "储能电池系统  6,243,982.0  14.7  5,729,046.0  15.8  5,990,052.2  14.9\n"
             "合计  42,370,183.3100.0  36,201,255.3 100.0  40,091,704.5 100.0\n\n"
             "发行人主要营业收入来自于动力电池系统。\n\n2、主营业务成本分析\n\n"
             "表 5-11发行人主营业务成本构成表\n\n单位：万元，%\n"),
            (50,
             "项目  2025年  2024年度  2023年度\n  金额  占比  金额  占比  金额  占比\n"
             "动力电池系统  24,106,439.7  77.2  19,246,128.2  70.4  22,171,419.3  71.7\n"
             "49\n"),
        ]
        tables = extract_note_tables(blocks)
        out = []
        for t, bidx in tables:
            out.append({
                "table_id": t.table_id,
                "table_title": t.table_title,
                "block_idx": bidx,
                "headers": list(t.headers),
                "unit": t.unit,
                "rows": len(t.rows),
                "row_column_source": t.row_column_source,
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
