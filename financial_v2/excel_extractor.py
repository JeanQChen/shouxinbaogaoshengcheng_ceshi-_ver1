"""financial_v2 A2：Excel 确定性抽取（真实坐标原始候选层，无 LLM / RAG / OCR）。

职责边界（本模块只做「读取 + 候选生成 + 可选持久化」，不做映射/标准化/对账）：
- 通过 openpyxl 读真实 workbook（sheet/row/column/A1 地址/合并区/公式/缓存值），
  不从 pandas 行号反推坐标；
- 复用 V1 `parsers.excel_parser.parse()` 公共接口仅作格式/单位交叉校验（不调用其私有函数）；
- 每个候选保存真实坐标、原始文本、Decimal 解析值、公式文本与缓存值、期间/单位/
  scope/币种候选与检测依据；
- 未知维度显式 None，绝不默认元/CNY/consolidated，不伪造标准记录；
- 失败分类（FILE_HASH_MISMATCH / UNSUPPORTED_FORMAT / CLASSIFICATION_REQUIRED /
  PERIOD_UNRESOLVED / PARSE_FAILED / HEADER_UNRESOLVED）进入 issue，不回退 LLM/OCR 取数。

对外接口（任务书 §5.1）：
    extract_excel(source_version, policy, persist=True) -> ExcelExtractionResult
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "excel_extractor"

# 版本化常量（参与 record_set_version / dependency_versions 身份）。
EXTRACTOR_VERSION = "1.0"
STATEMENT_ALIAS_VERSION = "1"
PERIOD_PARSE_VERSION = "1"
DEFAULT_MAPPING_RULE_VERSION = "1.0"
DEFAULT_NORMALIZATION_RULE_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 公共类型
# ---------------------------------------------------------------------------

@dataclass
class ExcelExtractionPolicy:
    """A2 抽取策略：实际文件路径（哈希复核必需）+ 各规则版本（进 record_set 身份）。"""

    file_path: str
    extractor_version: str = EXTRACTOR_VERSION
    mapping_rule_version: str = DEFAULT_MAPPING_RULE_VERSION
    normalization_rule_version: str = DEFAULT_NORMALIZATION_RULE_VERSION
    dependency_versions: dict[str, str] = field(default_factory=dict)


@dataclass
class ExcelTableRegion:
    """识别出的一个报表区域（表类型 + 表头行 + 科目列 + 期间列 + 单位/scope）。"""

    sheet_name: str
    statement_type: str | None
    header_row: int | None
    item_column: int | None
    period_columns: dict[int, str] = field(default_factory=dict)  # col -> 标准期间
    period_types: dict[int, str] = field(default_factory=dict)    # col -> period_type
    unit: str | None = None
    unit_text: str | None = None
    scope: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class ExcelExtractionResult:
    source_version: str
    record_set_version: str
    candidates: list[S.ExtractedFinancialCell]
    records: list[S.SourceFinancialRecord]      # A2 阶段恒空（A4 mapping/normalization 产出）
    issues: list[S.ExtractionIssue]
    table_regions: list[ExcelTableRegion]
    reused: bool


# ---------------------------------------------------------------------------
# 报表类型识别（版本化确定性别名 + 关键行组合）
# ---------------------------------------------------------------------------

_STATEMENT_ALIASES: dict[str, list[str]] = {
    "balance_sheet": ["资产负债表"],
    "income_statement": ["利润表", "损益表", "利润及利润分配表", "收益表"],
    "cash_flow": ["现金流量表"],
}

_STATEMENT_KEY_ROWS: dict[str, list[str]] = {
    "balance_sheet": ["资产总计", "负债合计", "所有者权益合计"],
    "income_statement": ["营业收入", "净利润"],
    "cash_flow": ["经营活动产生的现金流量净额"],
}

# 表名命中时的排除词（避免「现金流量表补充资料」被误判为主表；首版保守，暂不启用拆分）。
_ITEM_HEADER_KEYWORDS = ["项目", "科目", "指标", "附注"]


def _normalize_text(s: str) -> str:
    """Unicode / 空白 / 标点归一化（表名与科目别名比较前）。"""
    return re.sub(r"[\s　 ]", "", str(s)).strip()


def _match_statement_alias(text: str) -> list[str]:
    """返回文本命中的报表类型列表（可能为空，可能多命中 → 歧义由调用方处理）。"""
    normalized = _normalize_text(text)
    if not normalized:
        return []
    hits: list[str] = []
    for stmt, aliases in _STATEMENT_ALIASES.items():
        for alias in aliases:
            if _normalize_text(alias) in normalized:
                hits.append(stmt)
                break
    return hits


def _detect_statement_type(ws, sheet_name: str) -> tuple[str | None, dict]:
    """按优先级识别 sheet 的报表类型：sheet 名 → 顶部区域表名 → 关键行组合。

    返回 (statement_type | None, evidence)。None 表示无唯一结论 → CLASSIFICATION_REQUIRED。
    """
    # 1. sheet 名明确命中。
    sheet_hits = _match_statement_alias(sheet_name)
    if len(sheet_hits) == 1:
        return sheet_hits[0], {"matched_by": "sheet_name", "matched_text": sheet_name}

    # 2. 顶部限定区域（前 10 行）表名命中。
    title_hits: list[str] = []
    title_text: str | None = None
    for row in ws.iter_rows(min_row=1, max_row=min(10, ws.max_row), values_only=True):
        for cell in row:
            if cell is None:
                continue
            hits = _match_statement_alias(str(cell))
            for h in hits:
                if h not in title_hits:
                    title_hits.append(h)
                    title_text = str(cell)
    if len(title_hits) == 1:
        return title_hits[0], {"matched_by": "title", "matched_text": title_text}

    # 3. 关键行组合（前 60 行内统计各类别关键科目命中数）。
    key_scores: dict[str, int] = {k: 0 for k in _STATEMENT_KEY_ROWS}
    for row in ws.iter_rows(min_row=1, max_row=min(60, ws.max_row), values_only=True):
        for cell in row:
            if cell is None:
                continue
            cell_norm = _normalize_text(str(cell))
            for stmt, keys in _STATEMENT_KEY_ROWS.items():
                for k in keys:
                    if _normalize_text(k) == cell_norm:
                        key_scores[stmt] += 1
    best = max(key_scores.values())
    if best > 0:
        top = [k for k, v in key_scores.items() if v == best]
        if len(top) == 1:
            return top[0], {"matched_by": "key_rows", "scores": key_scores}

    return None, {"matched_by": None, "title_hits": title_hits, "scores": key_scores}


# ---------------------------------------------------------------------------
# 期间解析（版本化确定性；相对词仅在锚定明确日期时解析）
# ---------------------------------------------------------------------------

_PERIOD_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 更具体的完整日期优先，避免「2024年6月30日」被「2024年」年度前缀误吞。
    (re.compile(r"(\d{4})[年./\-](\d{1,2})[月./\-](\d{1,2})[日号]?"), "date"),
    (re.compile(r"(\d{4})[年./\-](\d{1,2})[月./\-]?"), "month"),
    (re.compile(r"(\d{4})年?第?\s*([一二三四1-4])\s*季度"), "quarterly"),
    (re.compile(r"(\d{4})年?([一二三四1-4])季报"), "quarterly"),
    (re.compile(r"(\d{4})[Qq]([1-4])"), "quarterly"),
    (re.compile(r"(\d{4})年?半年?度?"), "interim"),
    (re.compile(r"(\d{4})年?中报"), "interim"),
    (re.compile(r"(\d{4})年?年度?"), "annual"),
    (re.compile(r"^(\d{4})$"), "year"),
]

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _period_type_from_month(month: int, day: int) -> str:
    if month == 12 and day == 31:
        return "annual"
    if month == 6 and day == 30:
        return "interim"
    return "quarterly"


def parse_period(value) -> tuple[str | None, str | None]:
    """把表头单元格值解析为 (标准期间 "YYYY-MM-DD", period_type)。无法解析返回 (None, None)。

    支持 Excel 日期、YYYY-MM-DD、YYYY年MM月DD日、YYYY年报/半年报/季报、纯年份。
    相对词（本期/上期/期末/期初）不在本函数解析——需调用方用明确锚点另行解析。
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d"), _period_type_from_month(value.month, value.day)
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d"), _period_type_from_month(value.month, value.day)
    if isinstance(value, (int, float)):
        return None, None
    text = _normalize_text(str(value))
    if not text:
        return None, None
    for pattern, kind in _PERIOD_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        year = int(m.group(1))
        if kind == "quarterly":
            q = _CN_NUM.get(m.group(2), 3)
            month, day = _QUARTER_END[q]
            return f"{year:04d}-{month:02d}-{day:02d}", "quarterly"
        if kind == "interim":
            return f"{year:04d}-06-30", "interim"
        if kind == "annual":
            return f"{year:04d}-12-31", "annual"
        if kind == "date":
            month = min(max(int(m.group(2)), 1), 12)
            day = min(max(int(m.group(3)), 1), 31)
            return f"{year:04d}-{month:02d}-{day:02d}", _period_type_from_month(month, day)
        if kind == "month":
            month = min(max(int(m.group(2)), 1), 12)
            day = 31 if month in (1, 3, 5, 7, 8, 10, 12) else 30
            return f"{year:04d}-{month:02d}-{day:02d}", _period_type_from_month(month, day)
        if kind == "year":
            return f"{year:04d}-12-31", "annual"
    return None, None


# ---------------------------------------------------------------------------
# 金额解析（Decimal；不转 float；公式文本与缓存值分离）
# ---------------------------------------------------------------------------

_EMPTY_TOKENS = {"", "—", "–", "-", "－", "不适用", "n/a", "无", "null", "nan"}


def is_empty_value(raw) -> bool:
    if raw is None:
        return True
    return _normalize_text(str(raw)).lower() in _EMPTY_TOKENS


def parse_decimal(raw) -> Decimal | None:
    """解析金额字符串为有限 Decimal；无法解析（含百分数）返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    elif s.startswith("（") and s.endswith("）"):
        neg, s = True, s[1:-1].strip()
    s = s.replace(",", "").replace("，", "").replace(" ", "").replace(" ", "")
    if not s or s.endswith("%"):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return -d if neg else d


def display_increment(raw) -> Decimal:
    """由明确展示精度推导最小展示增量：2 位小数 → 0.01；整数 → 1。"""
    s = str(raw).strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    elif s.startswith("（") and s.endswith("）"):
        neg, s = True, s[1:-1].strip()
    s = s.lstrip("+-").replace(",", "").replace("，", "").replace(" ", "")
    if "." not in s:
        return Decimal("1")
    places = len(s.split(".", 1)[1])
    return Decimal("0.1") ** places


# ---------------------------------------------------------------------------
# 单位 / scope 检测（版本化；未知显式 None）
# ---------------------------------------------------------------------------

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
    ("元", "yuan"),
]

_SCOPE_PATTERNS: list[tuple[str, str]] = [
    (re.compile(r"合并.*(资产负债表|利润表|现金流量表|报表)"), "consolidated"),
    (re.compile(r"母公司.*(资产负债表|利润表|现金流量表|报表)"), "parent"),
]


def _detect_unit(title_texts: list[str]) -> tuple[str | None, str | None]:
    """从表名/表头/单位栏文本检测单位；未知返回 (None, None)。"""
    for text in title_texts:
        for keyword, unit in _UNIT_PATTERNS:
            if keyword in str(text):
                return unit, keyword
    return None, None


def _detect_scope(title_texts: list[str]) -> str | None:
    for text in title_texts:
        for pattern, scope in _SCOPE_PATTERNS:
            if pattern.search(str(text)):
                return scope
    return None


# ---------------------------------------------------------------------------
# 文件哈希
# ---------------------------------------------------------------------------

def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 表头 / 期间列定位
# ---------------------------------------------------------------------------

def _locate_header(ws) -> tuple[int | None, int | None, dict[int, str], dict[int, str]]:
    """定位表头行、科目列、期间列。

    返回 (header_row, item_column, period_columns {col: period}, period_types {col: type})。
    - header_row：前 20 行中第一个含 ≥1 个可解析期间列的行；
    - item_column：最左的非期间列（含「项目/科目」关键词者优先）；
    - period_columns：表头行中每个可解析为期间的列。
    """
    max_row = min(20, ws.max_row or 20)
    for r in range(1, max_row + 1):
        row_values = {c: ws.cell(r, c).value for c in range(1, ws.max_column + 1)}
        period_cols: dict[int, str] = {}
        period_types: dict[int, str] = {}
        for c, v in row_values.items():
            p, pt = parse_period(v)
            if p is not None:
                period_cols[c] = p
                period_types[c] = pt
        if not period_cols:
            continue
        # 科目列：优先含「项目/科目/指标」关键词的列；否则最左非期间列。
        item_col = None
        for c, v in row_values.items():
            if c in period_cols:
                continue
            if v is not None and any(k in _normalize_text(str(v)) for k in _ITEM_HEADER_KEYWORDS):
                item_col = c
                break
        if item_col is None:
            for c in range(1, ws.max_column + 1):
                if c not in period_cols:
                    item_col = c
                    break
        return r, item_col, period_cols, period_types
    return None, None, {}, {}


# ---------------------------------------------------------------------------
# 候选生成
# ---------------------------------------------------------------------------

def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _make_issue(
    record_set_version: str,
    issue_type: str,
    detail: dict,
    candidate_id: str | None = None,
    comparison_key: str | None = None,
    now: str = "",
) -> S.ExtractionIssue:
    issue_id = "iss-" + hashlib.sha256(
        f"{record_set_version}|{issue_type}|{candidate_id or ''}|{comparison_key or ''}"
        f"|{_canonical(detail)}".encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=record_set_version,
        issue_type=issue_type,
        candidate_id=candidate_id,
        comparison_key=comparison_key,
        detail=detail,
        created_at=now,
    )


def extract_excel(
    source_version: str,
    policy: ExcelExtractionPolicy,
    persist: bool = True,
) -> ExcelExtractionResult:
    """抽取一份已登记的 Excel 来源，生成原始候选 + 问题（可选持久化）。

    - 校验 source_version 已登记 + 文件哈希再次匹配；
    - `.xls` / 损坏 / 加密 → 对应 issue（不回退 LLM/OCR）；
    - 识别三张主表区域 → 逐单元格生成带真实坐标的候选；
    - 未知维度显式 None，不伪造标准值。
    """
    sv = store.get_source_version(source_version)
    if sv is None:
        raise KeyError(f"source_version 未登记: {source_version}")
    source_document_id = sv.source_document_id
    company_id = store.get_source_document(source_document_id).company_id
    now = store._utcnow()

    record_set_version = S.derive_record_set_version(
        source_version,
        policy.extractor_version,
        policy.mapping_rule_version,
        policy.normalization_rule_version,
        policy.dependency_versions,
    )

    path = Path(policy.file_path)
    candidates: list[S.ExtractedFinancialCell] = []
    issues: list[S.ExtractionIssue] = []
    regions: list[ExcelTableRegion] = []

    def _issue(issue_type: str, detail: dict, candidate_id: str | None = None) -> None:
        issues.append(_make_issue(record_set_version, issue_type, detail, candidate_id, now=now))

    # 哈希复核。
    if not path.exists():
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": "missing"})
        return ExcelExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    actual_hash = sha256_file(path)
    if actual_hash != sv.file_sha256:
        _issue("FILE_HASH_MISMATCH", {"file": str(path), "registered": sv.file_sha256[:16],
                                      "actual": actual_hash[:16]})
        return ExcelExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    if path.suffix.lower() != ".xlsx":
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "suffix": path.suffix})
        return ExcelExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    # 读 workbook（两次：公式文本 + 缓存值分离）。
    try:
        wb_formula = openpyxl.load_workbook(path, data_only=False)
    except Exception as e:  # noqa: BLE001 — 损坏/加密/无法打开需区分错误码并落 issue
        logger.warning("workbook 打开失败: %s", e)
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": f"open_failed: {type(e).__name__}"})
        return ExcelExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    try:
        wb_data = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:  # noqa: BLE001
        wb_formula.close()
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": f"open_failed: {type(e).__name__}"})
        return ExcelExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    try:
        for ws in wb_formula.worksheets:
            ws_data = wb_data[ws.title]
            statement_type, stmt_evidence = _detect_statement_type(ws, ws.title)
            if statement_type is None:
                _issue("CLASSIFICATION_REQUIRED",
                       {"sheet": ws.title, "evidence": stmt_evidence})
                continue

            header_row, item_col, period_cols, period_types = _locate_header(ws)
            if header_row is None or item_col is None or not period_cols:
                _issue("HEADER_UNRESOLVED", {"sheet": ws.title, "statement_type": statement_type})
                regions.append(ExcelTableRegion(
                    sheet_name=ws.title, statement_type=statement_type,
                    header_row=None, item_column=None, period_columns={}, period_types={},
                    evidence=stmt_evidence))
                continue

            # 单位 / scope：从表名 + 表头附近文本检测（保存依据，不默认）。
            title_texts: list[str] = []
            for c in range(1, ws.max_column + 1):
                for r in range(1, min(header_row + 1, ws.max_row + 1)):
                    v = ws.cell(r, c).value
                    if v is not None:
                        title_texts.append(str(v))
            unit, unit_text = _detect_unit(title_texts)
            scope = _detect_scope(title_texts)

            regions.append(ExcelTableRegion(
                sheet_name=ws.title, statement_type=statement_type, header_row=header_row,
                item_column=item_col, period_columns=dict(period_cols),
                period_types=dict(period_types), unit=unit, unit_text=unit_text, scope=scope,
                evidence=stmt_evidence))

            # 逐数据行生成候选（数据行从 header_row+1 开始）。
            for r in range(header_row + 1, ws.max_row + 1):
                item_val = ws.cell(r, item_col).value
                if item_val is None or not str(item_val).strip():
                    continue
                raw_item_text = str(item_val).strip()
                for col in sorted(period_cols):
                    period = period_cols[col]
                    period_type = period_types.get(col)
                    header_text = ws.cell(header_row, col).value
                    cell = ws.cell(r, col)
                    cell_data = ws_data.cell(r, col)
                    raw_value_text = None if cell.value is None else str(cell.value)
                    value_text_for_parse = str(cell.value) if cell.value is not None else None

                    # 公式文本与缓存值分离。
                    formula_text = cell.value if isinstance(cell.value, str) and str(cell.value).startswith("=") else None
                    cached_formula_value: Decimal | None = None
                    if formula_text is not None:
                        cached_formula_value = parse_decimal(cell_data.value)
                    elif cell_data.value is not None and isinstance(cell_data.value, (int, float)):
                        cached_formula_value = parse_decimal(cell_data.value)

                    parsed_numeric_value: Decimal | None
                    status: str
                    quality_flags: list[str] = []
                    if is_empty_value(cell.value):
                        status = "EMPTY_OR_NOT_APPLICABLE"
                        parsed_numeric_value = None
                    else:
                        parsed_numeric_value = parse_decimal(cell.value)
                        if parsed_numeric_value is None:
                            status = "PARSE_FAILED"
                        else:
                            status = "EXTRACTED"
                    if formula_text is not None and cached_formula_value is None:
                        quality_flags.append("FORMULA_VALUE_UNAVAILABLE")

                    locator = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
                        sheet_name=ws.title, row_number=r, column_number=col,
                        cell_address=f"{get_column_letter(col)}{r}",
                        row_header=raw_item_text,
                        column_header=str(header_text) if header_text is not None else None,
                        unit_text=unit_text,
                    ))
                    evidence = {
                        "statement_type": stmt_evidence,
                        "unit": {"matched_text": unit_text} if unit_text else None,
                        "period_header_text": str(header_text) if header_text is not None else None,
                        "alias_version": STATEMENT_ALIAS_VERSION,
                        "period_parse_version": PERIOD_PARSE_VERSION,
                    }
                    candidate_id = S.derive_candidate_id(
                        record_set_version, locator, raw_item_text, raw_value_text)
                    candidates.append(S.ExtractedFinancialCell(
                        candidate_id=candidate_id,
                        record_set_version=record_set_version,
                        company_id=company_id,
                        source_version=source_version,
                        statement_type_candidate=statement_type,
                        raw_item_text=raw_item_text,
                        raw_value_text=raw_value_text,
                        parsed_numeric_value=parsed_numeric_value,
                        formula_text=formula_text,
                        cached_formula_value=cached_formula_value,
                        period_text=str(header_text) if header_text is not None else None,
                        period_candidate=period,
                        period_type_candidate=period_type,
                        scope_candidate=scope,
                        currency_candidate=None,
                        unit_candidate=unit,
                        restatement_candidate=None,
                        min_display_increment=(
                            display_increment(value_text_for_parse)
                            if parsed_numeric_value is not None else None),
                        locator=locator,
                        detection_evidence=evidence,
                        status=status,
                        quality_flags=quality_flags,
                        created_at=now,
                    ))
    finally:
        wb_formula.close()
        wb_data.close()

    reused = False
    if persist and (candidates or issues):
        res = store.commit_extracted_candidates(candidates, issues, source_document_id)
        reused = res.reused

    return ExcelExtractionResult(
        source_version=source_version,
        record_set_version=record_set_version,
        candidates=candidates,
        records=[],
        issues=issues,
        table_regions=regions,
        reused=reused,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="python -m financial_v2.excel_extractor",
                                     description="Excel 确定性抽取（真实坐标候选层）")
    parser.add_argument("xlsx", help="Excel 文件路径")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--source-document", required=True, help="来源文档内部 ID")
    parser.add_argument("--extractor-version", default=EXTRACTOR_VERSION)
    parser.add_argument("--mapping-rule-version", default=DEFAULT_MAPPING_RULE_VERSION)
    parser.add_argument("--normalization-rule-version", default=DEFAULT_NORMALIZATION_RULE_VERSION)
    parser.add_argument("--validate-only", action="store_true",
                        help="只抽取摘要，不写生产/演示数据库")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省用 data/financial_v2.db）")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)
    path = Path(args.xlsx).resolve()
    file_hash = sha256_file(path)
    source_version = S.derive_source_version(args.source_document, file_hash)
    policy = ExcelExtractionPolicy(
        file_path=str(path),
        extractor_version=args.extractor_version,
        mapping_rule_version=args.mapping_rule_version,
        normalization_rule_version=args.normalization_rule_version,
        dependency_versions={"openpyxl": openpyxl.__version__},
    )
    result = extract_excel(source_version, policy, persist=not args.validate_only)
    summary = {
        "source_version": result.source_version,
        "record_set_version": result.record_set_version,
        "candidates": len(result.candidates),
        "records": len(result.records),
        "issues": len(result.issues),
        "reused": result.reused,
        "table_regions": [
            {
                "sheet": r.sheet_name,
                "statement_type": r.statement_type,
                "header_row": r.header_row,
                "item_column": r.item_column,
                "period_columns": r.period_columns,
                "unit": r.unit,
                "scope": r.scope,
            }
            for r in result.table_regions
        ],
        "issue_types": sorted({i.issue_type for i in result.issues}),
        "sample_candidates": [
            {
                "candidate_id": c.candidate_id[:16],
                "item": c.raw_item_text,
                "period": c.period_candidate,
                "status": c.status,
                "value": str(c.parsed_numeric_value) if c.parsed_numeric_value is not None else None,
                "locator": c.locator.excel.cell_address if c.locator and c.locator.excel else None,
            }
            for c in result.candidates[:5]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
