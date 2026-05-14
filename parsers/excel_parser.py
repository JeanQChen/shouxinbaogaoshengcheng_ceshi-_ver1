"""Excel → pandas DataFrame，检测报告期、合并范围、金额单位和数据格式。

支持两种常见格式：
  格式A（一期一文件）: 每个 Excel 对应一个报告期，各 sheet 只有一列金额。
  格式B（一表多期）:  每个 sheet 列是多个报告期（如 2024-12-31 / 2023-12-31）。
  详见 _detect_format()。
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

logger = logging.getLogger(__name__)


# ── 公共类型 ──


@dataclass
class ExcelParseResult:
    sheets: dict[str, pd.DataFrame]
    detected_period: str | None = None       # 推断的主报告期，如 "2024-12-31"
    detected_scope: str | None = None        # "consolidated" | "parent"
    metadata: dict = field(default_factory=dict)
    # metadata 必含字段：
    #   "source_file"       — 源文件名
    #   "total_sheets"      — Excel 总 sheet 数
    #   "parsed_sheets"     — 成功解析的 sheet 数
    #   "error_sheets"      — 解析失败的 sheet 名列表
    #   "format"            — "multi_period" | "single_period"
    #   "unit"              — "yuan" | "wan_yuan" | "qian_yuan" | "unknown"
    #   "detected_periods"  — [str]（仅 multi_period 时填充全部报告期列表）


# ── 报告期检测 ──

# 多模式日期匹配，按优先级排列（季报/年报在前专有格式，date 兜底）
_PERIOD_PATTERNS: list[tuple[str, str]] = [
    # (regex, period_format) — 捕获年份+季/月/日
    (r"(\d{4})Q([1-4])", "quarterly"),                               # 2026Q1
    (r"(\d{4})年?第?\s*([一二三四1-4])\s*季度", "quarterly"),       # 2024年第一季度, 2024年一季度
    (r"(\d{4})年?([一二三四1-4])季报", "quarterly"),                 # 2024年一季报, 2024年三季报
    (r"(\d{4})年?半年度", "semi-annual"),                            # 2024年半年度
    (r"(\d{4})年?中报", "semi-annual"),                              # 2024年中报
    (r"(\d{4})年?年报", "annual"),                                    # 2024年年报
    (r"(\d{4})[−\-/年](\d{1,2})[−\-/月](\d{1,2})", "date"),       # 2024-12-31, 2024年12月31日
    (r"(\d{4})\s*年度?", "annual"),                                  # 2024年度, 2024 年度
    (r"(\d{4})[−\-/](\d{1,2})[−\-/]?$", "date"),                   # 2024-12, 2024/12
]

# 合并 vs 母公司
_SCOPE_PATTERNS: list[tuple[str, str]] = [
    (r"合并.*报表|合并.*资产负债|合并.*利润|合并.*现金|^合并$", "consolidated"),
    (r"母公司.*报表|母公司.*资产负债|母公司.*利润|母公司.*现金|^母公司$", "parent"),
]

# 表头关键词
_HEADER_KEYWORDS = ["项目", "科目", "指标", "附注", "金额", "期末余额", "年初余额", "本期金额", "上期金额"]

# 金额单位检测关键词（按长到短匹配，子串靠后，避免"元"误吃"万元"）
_UNIT_PATTERNS: list[tuple[str, str]] = [
    ("人民币千万元", "qianwan_yuan"),
    ("人民币百万元", "baiwan_yuan"),
    ("人民币万元", "wan_yuan"),
    ("人民币千元", "qian_yuan"),
    ("人民币亿元", "yi_yuan"),
    ("人民币元", "yuan"),
    ("千万元", "qianwan_yuan"),
    ("百万元", "baiwan_yuan"),
    ("亿元", "yi_yuan"),
    ("万元", "wan_yuan"),
    ("千元", "qian_yuan"),
    ("￥", "yuan"),
    ("元", "yuan"),
]


# ── 格式检测 ──


def _detect_format(sheets: dict[str, pd.DataFrame]) -> str:
    """检测数据格式。

    返回 "multi_period"（格式B）或 "single_period"（格式A）。
    检测方法：取第一个非空 sheet，看列头里是否存在 2 个以上匹配日期正则的值。
    """
    for df in sheets.values():
        if df.empty:
            continue
        period_count = 0
        for col_name in df.columns:
            col_str = str(col_name).strip().replace(" ", "").replace("\n", "")
            for pattern, _ in _PERIOD_PATTERNS:
                if re.search(pattern, col_str):
                    period_count += 1
                    break
        if period_count >= 2:
            logger.info("Detected multi_period format (%d period columns)", period_count)
            return "multi_period"
        return "single_period"
    return "single_period"


def _detect_all_periods(
    sheets: dict[str, pd.DataFrame],
    sheet_names: list[str],
) -> list[str]:
    """从所有 sheet 的列头和前 5 行中提取全部出现过的报告期字符串。

    用于 multi_period 格式；每个列可能对应一个报告期。
    """
    periods: list[str] = []
    seen: set[str] = set()

    for df in sheets.values():
        if df.empty:
            continue
        for col_name in df.columns:
            col_str = str(col_name).strip().replace(" ", "").replace("\n", "")
            p = _parse_period_text(col_str)
            if p and p not in seen:
                seen.add(p)
                periods.append(p)

    if not periods:
        # 降级到 sheet 名和前几行
        candidates = list(sheet_names)
        for name, df in sheets.items():
            for _, row in df.head(5).iterrows():
                candidates.append(" ".join(str(v) for v in row.values if v is not None and str(v).strip()))
        for text in candidates:
            p = _parse_period_text(str(text))
            if p and p not in seen:
                seen.add(p)
                periods.append(p)

    return periods


# ── 单位检测 ──


def _detect_unit(
    sheets: dict[str, pd.DataFrame],
    sheet_names: list[str],
    raw_texts: list[str] | None = None,
) -> str:
    """检测金额单位，返回 "yuan" / "wan_yuan" / "qian_yuan" / "yi_yuan" / "unknown"。

    从 sheet 名称、原始表头行（raw_texts）、DataFrame 前 3 行中搜索单位声明。
    """
    candidates = list(sheet_names)
    if raw_texts:
        candidates.extend(raw_texts)
    for name, df in sheets.items():
        if df.empty:
            continue
        for _, row in df.head(3).iterrows():
            candidates.append(" ".join(str(v) for v in row.values if v is not None and str(v).strip()))

    for text in candidates:
        text_str = str(text)
        for keyword, unit in _UNIT_PATTERNS:
            if keyword in text_str:
                logger.info("Detected unit: %s (matched '%s')", unit, keyword)
                return unit
    return "unknown"


def _normalize_amount(value: float, unit: str) -> float:
    """将检测到的金额值统一转换为元。"""
    if unit == "yi_yuan":
        return value * 100_000_000
    if unit == "qianwan_yuan":
        return value * 10_000_000
    if unit == "baiwan_yuan":
        return value * 1_000_000
    if unit == "wan_yuan":
        return value * 10_000
    if unit == "qian_yuan":
        return value * 1_000
    return value


# ── 日期解析 ──


def _parse_period_text(text: str) -> str | None:
    """将文本中的日期信息解析为标准格式 "YYYY-MM-DD"，返回 None 表示未匹配。"""
    for pattern, period_type in _PERIOD_PATTERNS:
        m = re.search(pattern, str(text))
        if m:
            year = int(m.group(1))
            if period_type == "annual":
                return f"{year}-12-31"
            if period_type == "date":
                month = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else 12
                day = int(m.group(3)) if m.lastindex and m.lastindex >= 3 else 31
                month = min(max(month, 1), 12)
                day = min(max(day, 1), 31)
                return f"{year:04d}-{month:02d}-{day:02d}"
            if period_type == "semi-annual":
                return f"{year}-06-30"
            if period_type == "quarterly":
                quarter_map: dict[str, int] = {"一": 3, "1": 3, "二": 6, "2": 6, "三": 9, "3": 9, "四": 12, "4": 12}
                q_val = m.group(2) if m.lastindex and m.lastindex >= 2 else "3"
                month = quarter_map.get(str(q_val), 9)
                day = 31 if month in (3, 12) else 30
                return f"{year:04d}-{month:02d}-{day:02d}"
    return None


# ── 辅助函数 ──


def _fill_merged_cells(ws: openpyxl.worksheet.worksheet.Worksheet) -> None:
    """将合并单元格的值填充到所有被合并区域。"""
    for merged_range in ws.merged_cells.ranges:
        min_col = merged_range.min_col
        max_col = merged_range.max_col
        min_row = merged_range.min_row
        max_row = merged_range.max_row
        top_left_value = ws.cell(min_row, min_col).value
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                if row == min_row and col == min_col:
                    continue
                ws.cell(row, col).value = top_left_value


def _sheet_to_dataframe(ws: openpyxl.worksheet.worksheet.Worksheet) -> pd.DataFrame | None:
    """openpyxl worksheet → pandas DataFrame，自动定位表头行。"""
    _fill_merged_cells(ws)

    rows = [[cell.value for cell in row] for row in ws.iter_rows()]

    if not rows:
        return None

    header_row_idx = _find_header_row(rows)
    if header_row_idx is None:
        logger.warning("Sheet %s: could not find header row, skipping", ws.title)
        return None

    data_rows = rows[header_row_idx:]
    if len(data_rows) < 2:
        return None

    df = pd.DataFrame(data_rows[1:], columns=data_rows[0])
    df.dropna(how="all", inplace=True)
    df.dropna(axis=1, how="all", inplace=True)

    df.columns = [_clean_col_name(c) for c in df.columns]

    return df


def _find_header_row(rows: list[list]) -> int | None:
    """在前 20 行中找包含账务表头关键词的行。"""
    for i, row in enumerate(rows[:20]):
        text = " ".join(str(c) for c in row if c is not None)
        matches = sum(1 for kw in _HEADER_KEYWORDS if kw in text)
        if matches >= 3:
            return i
    for i, row in enumerate(rows[:30]):
        non_null = sum(1 for c in row if c is not None and str(c).strip())
        if non_null >= 4:
            return i
    return None


def _clean_col_name(name) -> str:
    """去除列名中的换行符和多余空格。"""
    if not isinstance(name, str):
        return str(name) if name is not None else ""
    return re.sub(r"\s+", "", name.replace("\n", "").replace("\r", "")).strip()


def _detect_scope(
    sheets: dict[str, pd.DataFrame],
    sheet_names: list[str],
    raw_texts: list[str] | None = None,
) -> str | None:
    """从 sheet 名称、原始表头行、前 5 行推断是合并报表还是母公司报表。"""
    candidates = list(sheet_names)
    if raw_texts:
        candidates.extend(raw_texts)
    for name, df in sheets.items():
        for _, row in df.head(5).iterrows():
            candidates.append(" ".join(str(v) for v in row.values if v is not None and str(v).strip()))

    for text in candidates:
        for pattern, scope in _SCOPE_PATTERNS:
            if re.search(pattern, str(text)):
                return scope

    logger.info("Could not determine scope, defaulting to 'consolidated'")
    return "consolidated"


# ── 主入口 ──


def parse(file_path: str) -> ExcelParseResult:
    """解析财务 Excel 文件，返回所有 sheet 的 DataFrame 及元信息。

    内部自动检测格式：
      - multi_period（格式B）：列头含多期日期，detected_periods 列出全部报告期
      - single_period（格式A）：每 sheet 一列金额，detected_period 为唯一报告期
    """
    path = Path(file_path).resolve()
    logger.info("Parsing %s", path.name)

    wb = openpyxl.load_workbook(path, data_only=True)
    sheets: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    raw_texts: list[str] = []

    for ws_name in wb.sheetnames:
        ws = wb[ws_name]
        # 在 DataFrame 转换前收集原始行文本（单位/范围声明常在表头上方）
        for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
            raw_texts.append(" ".join(str(c) for c in row if c is not None))
        try:
            df = _sheet_to_dataframe(ws)
            if df is not None and not df.empty:
                sheets[ws_name] = df
        except Exception as e:
            logger.warning("Sheet %s: parse failed — %s", ws_name, e)
            errors.append(ws_name)

    wb.close()

    if not sheets:
        raise ValueError(f"No usable sheets found in {path.name}")

    sheet_names = list(sheets.keys())

    fmt = _detect_format(sheets)
    scope = _detect_scope(sheets, sheet_names, raw_texts)
    unit = _detect_unit(sheets, sheet_names, raw_texts)

    if fmt == "multi_period":
        all_periods = sorted(_detect_all_periods(sheets, sheet_names))
        main_period = all_periods[-1] if all_periods else None
        detected_periods = all_periods
    else:
        main_period = _parse_period_text(
            " ".join(sheet_names + [str(c) for df in sheets.values() for c in df.columns[:5]])
        )
        detected_periods = [main_period] if main_period else []

    metadata = {
        "source_file": path.name,
        "total_sheets": len(wb.sheetnames),
        "parsed_sheets": len(sheets),
        "error_sheets": errors,
        "format": fmt,
        "unit": unit,
        "detected_periods": detected_periods,
    }

    logger.info("Parsed %d/%d sheets, format=%s, period=%s, scope=%s, unit=%s",
                len(sheets), len(wb.sheetnames), fmt, main_period, scope, unit)

    return ExcelParseResult(
        sheets=sheets,
        detected_period=main_period,
        detected_scope=scope,
        metadata=metadata,
    )


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m parsers.excel_parser <file_path>", file=sys.stderr)
        sys.exit(1)

    result = parse(sys.argv[1])
    summary = {
        "detected_period": result.detected_period,
        "detected_scope": result.detected_scope,
        "sheets": {name: len(df) for name, df in result.sheets.items()},
        "metadata": result.metadata,
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
