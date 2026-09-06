"""financial_v2 A3：电子 PDF 财务表格抽取（真实 PDF 坐标候选层，无 LLM / RAG / OCR）。

职责边界（本模块只做「读取 + 表格定位 + 候选生成 + 可选持久化」，不做映射/标准化/对账）：
- 仅接受文本质量合格的电子 PDF；扫描/低文本质量 fail fast（LOW_TEXT_QUALITY）；
- 再次校验文件 SHA-256 与已登记 Source Version 一致，且 PDF 已关联 Phase 1
  document_id/document_version（关联不明不生成正式候选）；
- 通过 pdfplumber 确定性表格网格（page.find_tables）建立 row/column 索引与 cell bbox，
  绝不从文本流反推网格；网格/表头/期间/单位不可靠时不生成可对账数值；
- 每个候选保存 document id/version、1-based 物理页、table id、table bbox、
  row/column index、cell bbox、原始文本、row/column header、unit text 及
  pdfplumber/table settings/extractor 版本；
- 未知维度显式 None，绝不默认元/CNY/consolidated；
- 跨页拼接仅当同 document version、物理连续页、同表名、兼容列头、相同单位且有
  明确续表标识时进行，否则 CROSS_PAGE_UNCERTAIN 拆开（首版保守：不做跨页合并）。

对外接口（任务书 §6.1）：
    extract_pdf(source_version, policy, persist=True) -> PdfExtractionResult
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2.excel_extractor import (
    parse_period,
    parse_decimal,
    display_increment,
    is_empty_value,
    sha256_file,
)

try:
    import pdfplumber
    PDFPLUMBER_VERSION = pdfplumber.__version__
except Exception:  # noqa: BLE001 — 依赖缺失须可导入并落 DEPENDENCY_ERROR，不在此崩溃
    pdfplumber = None
    PDFPLUMBER_VERSION = "unavailable"

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "pdf_table_extractor"

# 版本化常量（参与 record_set_version / dependency_versions 身份）。
EXTRACTOR_VERSION = "1.0"
STATEMENT_ALIAS_VERSION = "1"
PERIOD_PARSE_VERSION = "1"          # 复用 excel_extractor.parse_period 的期间解析语义
DEFAULT_MAPPING_RULE_VERSION = "1.0"
DEFAULT_NORMALIZATION_RULE_VERSION = "1.0"


# ---------------------------------------------------------------------------
# 公共类型
# ---------------------------------------------------------------------------

@dataclass
class PdfFinancialExtractionPolicy:
    """A3 抽取策略：实际文件路径（哈希复核必需）+ 页范围 + 各规则/依赖版本（进 record_set 身份）。"""

    file_path: str
    pages: str | None = None                 # "1-5" | "1,3,5" | None（全部），1-based 物理页
    extractor_version: str = EXTRACTOR_VERSION
    mapping_rule_version: str = DEFAULT_MAPPING_RULE_VERSION
    normalization_rule_version: str = DEFAULT_NORMALIZATION_RULE_VERSION
    dependency_versions: dict[str, str] = field(default_factory=dict)
    table_settings: dict = field(default_factory=dict)   # pdfplumber table settings


@dataclass
class PdfTableRegion:
    """识别出的一个 PDF 表格区域（document 身份 + 物理页 + 表类型 + 网格 + 单位/scope）。"""

    document_id: str
    document_version: str
    pdf_page: int
    table_id: str
    statement_type: str | None
    table_bbox: list[float]
    header_row_index: int | None
    item_column_index: int | None
    period_columns: dict[int, str] = field(default_factory=dict)   # col -> 标准期间
    period_types: dict[int, str] = field(default_factory=dict)     # col -> period_type
    unit: str | None = None
    unit_text: str | None = None
    scope: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class PdfExtractionResult:
    source_version: str
    record_set_version: str
    candidates: list[S.ExtractedFinancialCell]
    records: list[S.SourceFinancialRecord]      # A3 阶段恒空（A4 mapping/normalization 产出）
    issues: list[S.ExtractionIssue]
    table_regions: list[PdfTableRegion]
    reused: bool


# ---------------------------------------------------------------------------
# 文本归一化 / 报表类型识别（版本化确定性；中文 + 英文别名，英文用于可测试合成 fixture）
# ---------------------------------------------------------------------------

def _normalize_text(s: str) -> str:
    return re.sub(r"[\s　 ]", "", str(s)).strip()


_STATEMENT_ALIASES: dict[str, list[str]] = {
    "balance_sheet": ["资产负债表", "balance sheet", "statement of financial position"],
    "income_statement": ["利润表", "损益表", "利润及利润分配表", "收益表",
                         "income statement", "statement of income"],
    "cash_flow": ["现金流量表", "cash flow statement", "statement of cash flows"],
}

_STATEMENT_KEY_ROWS: dict[str, list[str]] = {
    "balance_sheet": ["资产总计", "负债合计", "所有者权益合计",
                      "total assets", "total liabilities", "total equity"],
    "income_statement": ["营业收入", "营业总收入", "净利润", "revenue", "net income"],
    "cash_flow": ["经营活动产生的现金流量净额", "net cash provided by operating activities"],
}

_ITEM_HEADER_KEYWORDS = ["项目", "科目", "指标", "附注", "item", "items"]


def _match_statement_alias(text: str) -> list[str]:
    # 大小写不敏感：英文别名（balance sheet / Balance Sheet）与中文别名均命中；
    # 中文别名不受大小写影响，统一 lower 无害。
    normalized = _normalize_text(text).lower()
    if not normalized:
        return []
    hits: list[str] = []
    for stmt, aliases in _STATEMENT_ALIASES.items():
        for alias in aliases:
            if _normalize_text(alias).lower() in normalized:
                hits.append(stmt)
                break
    return hits


def _detect_statement_from_texts(texts: list[str], grid_texts: list[str]) -> tuple[str | None, dict]:
    """按优先级识别报表类型：标题文本 → 表格关键行组合。返回 (statement_type|None, evidence)。"""
    # 1. 标题文本命中。
    title_hits: list[str] = []
    title_text: str | None = None
    for text in texts:
        for h in _match_statement_alias(text):
            if h not in title_hits:
                title_hits.append(h)
                title_text = text
    if len(title_hits) == 1:
        return title_hits[0], {"matched_by": "title", "matched_text": title_text}

    # 2. 关键行组合（统计各类别关键科目命中数）。
    key_scores: dict[str, int] = {k: 0 for k in _STATEMENT_KEY_ROWS}
    for text in grid_texts + texts:
        cell_norm = _normalize_text(text).lower()
        for stmt, keys in _STATEMENT_KEY_ROWS.items():
            for k in keys:
                if _normalize_text(k).lower() == cell_norm:
                    key_scores[stmt] += 1
    best = max(key_scores.values())
    if best > 0:
        top = [k for k, v in key_scores.items() if v == best]
        if len(top) == 1:
            return top[0], {"matched_by": "key_rows", "scores": key_scores}

    return None, {"matched_by": None, "title_hits": title_hits, "scores": key_scores}


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
    ("thousand yuan", "qian_yuan"),
    ("million yuan", "baiwan_yuan"),
    ("yuan", "yuan"),
]

_SCOPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"合并.*(资产负债表|利润表|现金流量表|报表)", re.IGNORECASE), "consolidated"),
    (re.compile(r"母公司.*(资产负债表|利润表|现金流量表|报表)", re.IGNORECASE), "parent"),
    (re.compile(r"consolidated", re.IGNORECASE), "consolidated"),
    (re.compile(r"parent(?: company)?", re.IGNORECASE), "parent"),
]


def _detect_unit(title_texts: list[str]) -> tuple[str | None, str | None]:
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
# 期间解析（复用 excel_extractor 绝对日期 + 相对词锚定）
# ---------------------------------------------------------------------------

_RELATIVE_PERIOD_END = ("期末余额", "期末数", "期末")
_RELATIVE_PERIOD_BEGIN = ("期初余额", "期初数", "期初")


def prior_period_end(anchor_period: str, period_type: str | None) -> str | None:
    """由报告期终点推导期初（上一报告期终点）。

    首版仅可靠支持 annual（期末 = 当年 12-31，期初 = 上年 12-31）。interim/quarterly
    的期初边界依赖「年初 vs 季初」口径，无法仅凭日期可靠判定 → 返回 None（不得猜测）。
    """
    if period_type != "annual":
        return None
    m = re.match(r"^(\d{4})-12-31$", anchor_period)
    if not m:
        return None
    return f"{int(m.group(1)) - 1:04d}-12-31"


def resolve_period_header(
    header_text: str | None,
    anchor_period: str | None,
    anchor_period_type: str | None,
) -> tuple[str | None, str | None]:
    """把表头单元格解析为 (标准期间, period_type)。

    绝对日期走 parse_period；相对词（期末/期初）仅在存在明确锚点且锚点可解时解析，
    否则返回 (None, None)（相对期间无锚点不生成可对账记录，任务书 §5.4）。
    """
    if header_text is None:
        return None, None
    text = _normalize_text(header_text)
    if not text:
        return None, None
    # 1. 绝对日期优先。
    p, pt = parse_period(header_text)
    if p is not None:
        return p, pt
    # 2. 相对词（需锚点）。
    if anchor_period is None:
        return None, None
    if text in _RELATIVE_PERIOD_END:
        return anchor_period, anchor_period_type
    if text in _RELATIVE_PERIOD_BEGIN:
        return prior_period_end(anchor_period, anchor_period_type), "annual"
    return None, None


# 仅匹配完整日期或带显式期间标记的年份；不匹配裸 4 位数字（可能是金额/页码，歧义）。
_DATE_TOKEN_RE = re.compile(
    r"\d{4}[年./\-]\d{1,2}[月./\-]\d{1,2}[日号]?"
    r"|\d{4}年?(?:年度|年报|中报|半年报|半年度|季报)"
)


def extract_anchor_period(page_text: str) -> tuple[str | None, str | None]:
    """从页面文本提取报告期锚点：取可解析绝对日期中最晚者（报告期通常为最新期间终点）。"""
    candidates: list[tuple[str, str]] = []
    for token in _DATE_TOKEN_RE.findall(page_text):
        p, pt = parse_period(token)
        if p is not None and pt is not None:
            candidates.append((p, pt))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1]


# ---------------------------------------------------------------------------
# 页范围解析（1-based；越界拒绝）
# ---------------------------------------------------------------------------

def parse_page_range(pages: str | None, page_count: int) -> list[int]:
    """解析页范围说明为 1-based 物理页列表；越界/非法 → ValueError。"""
    if page_count <= 0:
        raise ValueError(f"page_count 必须 > 0: {page_count}")
    if pages is None or not str(pages).strip():
        return list(range(1, page_count + 1))
    result: list[int] = []
    for part in str(pages).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = int(lo), int(hi)
            if lo > hi:
                raise ValueError(f"页范围起点大于终点: {part!r}")
            result.extend(range(lo, hi + 1))
        else:
            result.append(int(part))
    for p in result:
        if p < 1 or p > page_count:
            raise ValueError(f"页码越界: {p}（有效 1..{page_count}）")
    return sorted(set(result))


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


def _find_item_column(header_cells: list[str]) -> int | None:
    """在表头行定位科目列：必须含科目关键词（项目/科目/指标/item…），否则 None。

    不复用「最左非空列」兜底——数据行（如 CASH/1000/900）会因此被误判为表头行，
    并把金额 1000 当成年份解析。无法确认识别科目列时返回 None，交由 HEADER_UNRESOLVED。
    """
    for ci, cell in enumerate(header_cells):
        if cell is not None and any(k in _normalize_text(str(cell)).lower() for k in _ITEM_HEADER_KEYWORDS):
            return ci
    return None


def _locate_header(
    grid: list[list[str]],
    anchor_period: str | None,
    anchor_period_type: str | None,
) -> tuple[int | None, int | None, dict[int, str], dict[int, str]]:
    """定位表头行、科目列、期间列。返回 (header_row, item_col, period_cols, period_types)。

    期间列：表头单元格可解析为绝对日期或可锚定的相对词。
    """
    for ri, row in enumerate(grid):
        item_col = _find_item_column(row)
        if item_col is None:
            continue
        period_cols: dict[int, str] = {}
        period_types: dict[int, str] = {}
        for ci, cell in enumerate(row):
            if ci == item_col:
                continue
            p, pt = resolve_period_header(cell, anchor_period, anchor_period_type)
            if p is not None:
                period_cols[ci] = p
                period_types[ci] = pt
        if period_cols:
            return ri, item_col, period_cols, period_types
    return None, None, {}, {}


def _grid_and_cells_align(table) -> bool:
    """校验 text grid 与 rows[i].cells 的维度一致（网格可靠前提）。"""
    grid = table.extract()
    if len(table.rows) != len(grid):
        return False
    for ri, row in enumerate(table.rows):
        if len(row.cells) != len(grid[ri]):
            return False
    return True


def extract_pdf(
    source_version: str,
    policy: PdfFinancialExtractionPolicy,
    persist: bool = True,
) -> PdfExtractionResult:
    """抽取一份已登记的电子 PDF 来源，生成原始候选 + 问题（可选持久化）。

    - 校验 source_version 已登记 + 文件哈希再次匹配 + subject 未 mismatch +
      document_id/document_version 已关联；
    - 依赖缺失 / 损坏 / 低文本质量 / 无表 / 网格不可靠 / 表头或期间未解析 → 对应 issue；
    - 逐数据行 × 期间列生成带真实 PDF 坐标的候选；
    - 未知维度显式 None，不伪造标准值。
    """
    sv = store.get_source_version(source_version)
    if sv is None:
        raise KeyError(f"source_version 未登记: {source_version}")
    source_document_id = sv.source_document_id
    doc = store.get_source_document(source_document_id)
    company_id = doc.company_id
    now = store._utcnow()

    deps = dict(policy.dependency_versions)
    deps.setdefault("pdfplumber", PDFPLUMBER_VERSION)

    record_set_version = S.derive_record_set_version(
        source_version,
        policy.extractor_version,
        policy.mapping_rule_version,
        policy.normalization_rule_version,
        deps,
    )

    path = Path(policy.file_path)
    candidates: list[S.ExtractedFinancialCell] = []
    issues: list[S.ExtractionIssue] = []
    regions: list[PdfTableRegion] = []

    def _issue(issue_type: str, detail: dict, candidate_id: str | None = None) -> None:
        issues.append(_make_issue(record_set_version, issue_type, detail, candidate_id, now=now))

    # 依赖缺失。
    if pdfplumber is None:
        _issue("DEPENDENCY_ERROR", {"reason": "pdfplumber 不可用", "expected": "0.11.4"})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    # 文件存在 / 后缀 / 哈希。
    if not path.exists():
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": "missing"})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    if path.suffix.lower() != ".pdf":
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "suffix": path.suffix})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    actual_hash = sha256_file(path)
    if actual_hash != sv.file_sha256:
        _issue("FILE_HASH_MISMATCH", {"file": str(path), "registered": sv.file_sha256[:16],
                                      "actual": actual_hash[:16]})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    # subject mismatch / 未关联 Phase 1 document。
    if doc.subject_match_status == "mismatch":
        _issue("SUBJECT_MISMATCH", {"company_id": company_id, "source_document_id": source_document_id})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)
    if not sv.document_id or not sv.document_version:
        _issue("DOCUMENT_LINK_UNAVAILABLE", {
            "source_version": source_version,
            "document_id": sv.document_id,
            "document_version": sv.document_version,
            "reason": "PDF 未关联 Phase 1 document_id/document_version",
        })
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    # 打开 PDF。
    try:
        pdf = pdfplumber.open(path)
    except Exception as e:  # noqa: BLE001 — 损坏/加密/无法打开须区分错误码并落 issue
        logger.warning("PDF 打开失败: %s", e)
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": f"open_failed: {type(e).__name__}"})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    try:
        pages = parse_page_range(policy.pages, len(pdf.pages))
    except ValueError as e:
        pdf.close()
        _issue("UNSUPPORTED_FORMAT", {"file": str(path), "reason": f"page_range: {e}"})
        return PdfExtractionResult(source_version, record_set_version, [], [], issues, [], False)

    table_settings = dict(policy.table_settings)

    try:
        for page_no in pages:
            page = pdf.pages[page_no - 1]
            page_text = page.extract_text() or ""
            # 低文本质量（扫描件/无文本层）fail fast。
            if len(page_text.strip()) < 20:
                _issue("LOW_TEXT_QUALITY", {"pdf_page": page_no, "chars": len(page_text.strip())})
                continue

            anchor_period, anchor_period_type = extract_anchor_period(page_text)

            tables = page.find_tables(table_settings or None) or []
            if not tables:
                _issue("TABLE_NOT_FOUND", {"pdf_page": page_no})
                continue

            for ti, table in enumerate(tables):
                table_id = f"p{page_no}t{ti}"
                grid = table.extract()
                grid_texts = [str(c) for row in grid for c in row if c]
                statement_type, stmt_evidence = _detect_statement_from_texts(
                    [page_text], grid_texts)
                if statement_type is None:
                    _issue("CLASSIFICATION_REQUIRED",
                           {"pdf_page": page_no, "table_id": table_id, "evidence": stmt_evidence})
                    regions.append(PdfTableRegion(
                        document_id=sv.document_id, document_version=sv.document_version,
                        pdf_page=page_no, table_id=table_id, statement_type=None,
                        table_bbox=list(table.bbox), header_row_index=None, item_column_index=None,
                        evidence=stmt_evidence))
                    continue

                # 单位/scope：从页面文本（表名附近）检测，保存依据。
                title_texts = [page_text]
                unit, unit_text = _detect_unit(title_texts)
                scope = _detect_scope(title_texts)

                if not _grid_and_cells_align(table):
                    _issue("TABLE_GRID_UNAVAILABLE",
                           {"pdf_page": page_no, "table_id": table_id,
                            "statement_type": statement_type})
                    regions.append(PdfTableRegion(
                        document_id=sv.document_id, document_version=sv.document_version,
                        pdf_page=page_no, table_id=table_id, statement_type=statement_type,
                        table_bbox=list(table.bbox), header_row_index=None, item_column_index=None,
                        unit=unit, unit_text=unit_text, scope=scope, evidence=stmt_evidence))
                    continue

                header_row, item_col, period_cols, period_types = _locate_header(
                    grid, anchor_period, anchor_period_type)
                if header_row is None or item_col is None or not period_cols:
                    _issue("HEADER_UNRESOLVED",
                           {"pdf_page": page_no, "table_id": table_id,
                            "statement_type": statement_type,
                            "anchor_period": anchor_period})
                    regions.append(PdfTableRegion(
                        document_id=sv.document_id, document_version=sv.document_version,
                        pdf_page=page_no, table_id=table_id, statement_type=statement_type,
                        table_bbox=list(table.bbox), header_row_index=None, item_column_index=None,
                        unit=unit, unit_text=unit_text, scope=scope, evidence=stmt_evidence))
                    continue

                regions.append(PdfTableRegion(
                    document_id=sv.document_id, document_version=sv.document_version,
                    pdf_page=page_no, table_id=table_id, statement_type=statement_type,
                    table_bbox=list(table.bbox), header_row_index=header_row,
                    item_column_index=item_col, period_columns=dict(period_cols),
                    period_types=dict(period_types), unit=unit, unit_text=unit_text,
                    scope=scope, evidence=stmt_evidence))

                # 逐数据行 × 期间列生成候选。
                seen_bboxes: set[tuple[float, ...]] = set()
                for ri in range(header_row + 1, len(grid)):
                    item_val = grid[ri][item_col] if item_col < len(grid[ri]) else None
                    if item_val is None or not str(item_val).strip():
                        continue
                    raw_item_text = str(item_val).strip()
                    for col in sorted(period_cols):
                        if col >= len(grid[ri]):
                            continue
                        raw_value_text = grid[ri][col]
                        period = period_cols[col]
                        period_type = period_types.get(col)
                        header_text = grid[header_row][col] if col < len(grid[header_row]) else None

                        cell_bbox = list(table.rows[ri].cells[col]) if (
                            ri < len(table.rows) and col < len(table.rows[ri].cells)) else None

                        # 合并单元格：同 bbox 只取首个（不扩增为多个独立金额）。
                        if cell_bbox is not None:
                            bbox_key = tuple(round(float(v), 3) for v in cell_bbox)
                            if bbox_key in seen_bboxes:
                                continue
                            seen_bboxes.add(bbox_key)

                        status: str
                        parsed_numeric_value: Decimal | None
                        if is_empty_value(raw_value_text):
                            status = "EMPTY_OR_NOT_APPLICABLE"
                            parsed_numeric_value = None
                        else:
                            parsed_numeric_value = parse_decimal(raw_value_text)
                            status = "EXTRACTED" if parsed_numeric_value is not None else "PARSE_FAILED"

                        locator = S.SourceLocator(kind="pdf", pdf=S.PdfCellLocator(
                            document_id=sv.document_id,
                            document_version=sv.document_version,
                            pdf_page=page_no,
                            row_index=ri,
                            column_index=col,
                            bbox=cell_bbox or [0.0, 0.0, 0.0, 0.0],
                            table_id=table_id,
                            row_header=raw_item_text,
                            column_header=str(header_text) if header_text is not None else None,
                            unit_text=unit_text,
                        ))
                        evidence = {
                            "statement_type": stmt_evidence,
                            "unit": {"matched_text": unit_text} if unit_text else None,
                            "scope": scope,
                            "anchor_period": anchor_period,
                            "anchor_period_type": anchor_period_type,
                            "period_header_text": str(header_text) if header_text is not None else None,
                            "table_bbox": list(table.bbox),
                            "table_settings": table_settings,
                            "pdfplumber_version": PDFPLUMBER_VERSION,
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
                            formula_text=None,
                            cached_formula_value=None,
                            period_text=str(header_text) if header_text is not None else None,
                            period_candidate=period,
                            period_type_candidate=period_type,
                            scope_candidate=scope,
                            currency_candidate=None,
                            unit_candidate=unit,
                            restatement_candidate=None,
                            min_display_increment=(
                                display_increment(raw_value_text)
                                if parsed_numeric_value is not None else None),
                            locator=locator,
                            detection_evidence=evidence,
                            status=status,
                            quality_flags=[],
                            created_at=now,
                        ))
                        # 无法解析的非空字符串 → CELL_PARSE_FAILED（§6.6），不转成 0。
                        if status == "PARSE_FAILED":
                            _issue("CELL_PARSE_FAILED",
                                   {"pdf_page": page_no, "table_id": table_id,
                                    "raw_item_text": raw_item_text, "raw_value_text": raw_value_text},
                                   candidate_id=candidate_id)
    finally:
        pdf.close()

    reused = False
    if persist and (candidates or issues):
        res = store.commit_extracted_candidates(candidates, issues, source_document_id)
        reused = res.reused

    return PdfExtractionResult(
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
    import sys

    parser = argparse.ArgumentParser(prog="python -m financial_v2.pdf_table_extractor",
                                     description="电子 PDF 财务表格抽取（真实坐标候选层）")
    parser.add_argument("pdf", help="PDF 文件路径")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--source-document", required=True, help="来源文档内部 ID")
    parser.add_argument("--pages", default=None, help="页范围，如 1-5 或 1,3,5（1-based）")
    parser.add_argument("--extractor-version", default=EXTRACTOR_VERSION)
    parser.add_argument("--mapping-rule-version", default=DEFAULT_MAPPING_RULE_VERSION)
    parser.add_argument("--normalization-rule-version", default=DEFAULT_NORMALIZATION_RULE_VERSION)
    parser.add_argument("--validate-only", action="store_true",
                        help="只抽取摘要，不写生产/演示数据库")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省用 data/financial_v2.db）")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)
    path = Path(args.pdf).resolve()
    file_hash = sha256_file(path)
    source_version = S.derive_source_version(args.source_document, file_hash)
    policy = PdfFinancialExtractionPolicy(
        file_path=str(path),
        pages=args.pages,
        extractor_version=args.extractor_version,
        mapping_rule_version=args.mapping_rule_version,
        normalization_rule_version=args.normalization_rule_version,
        dependency_versions={"pdfplumber": PDFPLUMBER_VERSION},
    )
    result = extract_pdf(source_version, policy, persist=not args.validate_only)
    summary = {
        "source_version": result.source_version,
        "record_set_version": result.record_set_version,
        "candidates": len(result.candidates),
        "records": len(result.records),
        "issues": len(result.issues),
        "reused": result.reused,
        "table_regions": [
            {
                "pdf_page": r.pdf_page,
                "table_id": r.table_id,
                "statement_type": r.statement_type,
                "header_row_index": r.header_row_index,
                "item_column_index": r.item_column_index,
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
                "locator": {"pdf_page": c.locator.pdf.pdf_page, "row": c.locator.pdf.row_index,
                            "col": c.locator.pdf.column_index} if c.locator and c.locator.pdf else None,
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
