"""Eval: financial_v2 A3 电子 PDF 财务表格抽取（真实 PDF 坐标候选层）。

用法: python -m evals.test_financial_v2_pdf_extractor

覆盖（A3，§12.3）：
- 电子 PDF 三张主表识别、真实 cell bbox、1-based 物理页；
- 绝对日期列（YYYY年度/YYYY-MM-DD）与相对词（期末余额/期初余额 + 锚点）解析；
- 页码越界拒绝；哈希不匹配 / 损坏 / 依赖缺失 / 低文本质量 / 无表 / 表头未解析；
- document_id/document_version 缺失拒绝正式记录；
- 重复运行严格复用；抽取器版本变化生成新 record_set_version。

全部合成 ASCII fixture（公司无关，raw PDF 字节构造，pdfplumber 确定性网格），
临时 DB / 临时目录注入，不污染生产库；真实样本仅作集成验收（不参与通用断言）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import pdf_table_extractor as ex


# ---------------------------------------------------------------------------
# 临时 DB / 文件
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_pdf_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _tmp_pdf(prefix: str = "eval_pdf_") -> str:
    fd, p = tempfile.mkstemp(suffix=".pdf", prefix=prefix)
    os.close(fd)
    return p


# ---------------------------------------------------------------------------
# 合成 PDF 生成器（raw PDF 字节；ASCII 文本 + 网格线，pdfplumber 确定性检出）
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _has_cjk(pages: list[list[dict]]) -> bool:
    for tables in pages:
        for t in tables:
            for s in ([t.get("title"), t.get("unit")] + list(t["header"]) +
                      [c for r in t["rows"] for c in r]):
                if s and any(ord(ch) > 255 for ch in str(s)):
                    return True
    return False


def _utf16be_hex(s: str) -> str:
    return s.encode("utf-16-be").hex().upper()


def build_pdf(path: str, pages: list[list[dict]]) -> None:
    """生成多页 PDF。pages[i] = 该页的表格列表，每个表 dict：
    {title, unit, header, rows, x0, y_top, col_width, row_height}。

    文本纯 ASCII 时用 Helvetica + latin-1；含中文（如期末余额/期初余额）时自动切换
    Type0 CID 字体（STSong-Light / UniGB-UCS2-H）+ UTF-16BE 十六进制串，pdfplumber
    以 Unicode 反解，支持真实样本文档使用的相对期间词。
    """
    use_cid = _has_cjk(pages)
    n_pages = len(pages)
    if use_cid:
        # 对象编号严格连续（1..N）：Catalog, Pages, Type0 字体, 子字体, 字体描述符, 页面…, 内容流…
        base = 6
        kids = " ".join(f"{base + i} 0 R" for i in range(n_pages))
        objs = [
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {n_pages} >> endobj",
            "3 0 obj << /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            "/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >> endobj",
            "4 0 obj << /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
            "/FontDescriptor 5 0 R /DW 1000 >> endobj",
            "5 0 obj << /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
            "/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 "
            "/CapHeight 880 /StemV 70 >> endobj",
        ]
    else:
        base = 4
        kids = " ".join(f"{base + i} 0 R" for i in range(n_pages))
        objs = [
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {n_pages} >> endobj",
            "3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        ]
    page_ids = list(range(base, base + n_pages))
    content_ids = list(range(base + n_pages, base + 2 * n_pages))

    def _show(x: int, y: int, size: int, txt: str) -> str:
        if use_cid:
            return f"BT /F3 {size} Tf {x} {y} Td <{_utf16be_hex(txt)}> Tj ET"
        return f"BT /F3 {size} Tf {x} {y} Td ({_escape(txt)}) Tj ET"

    streams: list[str] = []
    for tables in pages:
        ops: list[str] = []
        for t in tables:
            x0 = t["x0"]; y_top = t["y_top"]; cw = t["col_width"]; rh = t["row_height"]
            header = t["header"]; rows = t["rows"]
            ncols = len(header); nrows = len(rows) + 1
            xN = x0 + ncols * cw; y_bottom = y_top - nrows * rh
            if t.get("title"):
                ops.append(_show(x0, y_top + 20, 12, t["title"]))
            if t.get("unit"):
                ops.append(_show(x0, y_top + 8, 9, t["unit"]))
            for r in range(nrows + 1):
                y = y_top - r * rh
                ops.append(f"{x0} {y} m {xN} {y} l S")
            for c in range(ncols + 1):
                x = x0 + c * cw
                ops.append(f"{x} {y_top} m {x} {y_bottom} l S")
            for r in range(nrows):
                y = y_top - r * rh
                rowcells = header if r == 0 else rows[r - 1]
                for c in range(ncols):
                    txt = rowcells[c] if c < len(rowcells) else ""
                    if txt:
                        ops.append(_show(x0 + c * cw + 4, y - 13, 9, str(txt)))
        streams.append("\n".join(ops))
    for pi in range(n_pages):
        objs.append(
            f"{page_ids[pi]} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F3 3 0 R >> >> /Contents {content_ids[pi]} 0 R >> endobj")
    for i, s in enumerate(streams):
        objs.append(f"{content_ids[i]} 0 obj << /Length {len(s.encode('latin-1'))} >> stream\n{s}\nendstream endobj")

    out = ["%PDF-1.4"]
    offsets = [0]
    for o in objs:
        offsets.append(sum(len(x.encode("latin-1")) + 1 for x in out))
        out.append(o)
    xref_pos = sum(len(x.encode("latin-1")) + 1 for x in out)
    out.append("xref")
    out.append(f"0 {len(objs) + 1}")
    out.append("0000000000 65535 f ")
    for off in offsets[1:]:
        out.append(f"{off:010d} 00000 n ")
    out.append("trailer")
    out.append(f"<< /Size {len(objs) + 1} /Root 1 0 R >>")
    out.append("startxref")
    out.append(str(xref_pos))
    out.append("%%EOF")
    with open(path, "w", encoding="latin-1") as f:
        f.write("\n".join(out))


# ---------------------------------------------------------------------------
# 来源登记（PDF：document_id/document_version 已关联 Phase 1）
# ---------------------------------------------------------------------------

def _register(db_path: str, file_path: str, company_id: str = "ACME",
              source_name: str = "report.pdf", document_id: str = "doc-ev-1",
              document_version: str = "v1", subject: str = "matched") -> tuple[str, str]:
    store.init_db(db_path)
    file_hash = ex.sha256_file(file_path)
    external_id = f"doc-{company_id}-{source_name}"
    source_document_id = S.scope_source_document_id(company_id, external_id)
    source_version = S.derive_source_version(source_document_id, file_hash)
    doc = S.FinancialSourceDocument(
        source_document_id=source_document_id, company_id=company_id, source_name=source_name,
        source_class="financial_statement", declared_company_name="Acme",
        detected_company_name="Acme", subject_match_status=subject,
        created_at="2026-01-01T00:00:00Z")
    version = S.FinancialSourceVersion(
        source_version=source_version, source_document_id=source_document_id,
        file_sha256=file_hash, file_type="pdf", file_size=Path(file_path).stat().st_size,
        document_id=document_id, document_version=document_version,
        created_at="2026-01-01T00:00:00Z")
    store.register_source_atomic(doc, version)
    return source_document_id, source_version


def _policy(file_path: str, **kw) -> ex.PdfFinancialExtractionPolicy:
    return ex.PdfFinancialExtractionPolicy(
        file_path=file_path,
        dependency_versions={"pdfplumber": ex.PDFPLUMBER_VERSION},
        **kw,
    )


def _bs_table(title="Balance Sheet", unit="Unit: yuan", header=("Item", "2024-12-31", "2023-12-31"),
              rows=(("CASH", "1000", "900"), ("RECEIVABLES", "(200)", "-150"),
                    ("TOTAL ASSETS", "1,234.56", "1,000"))):
    return {"title": title, "unit": unit, "header": list(header), "rows": [list(r) for r in rows],
            "x0": 50, "y_top": 700, "col_width": 120, "row_height": 25}


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

    # ---- 纯函数：页范围 / 相对期间 / 锚点 ----
    check(ex.parse_page_range(None, 5) == [1, 2, 3, 4, 5], "页范围 None → 全部")
    check(ex.parse_page_range("1-3", 5) == [1, 2, 3], "页范围 1-3")
    check(ex.parse_page_range("2,4", 5) == [2, 4], "页范围 2,4")
    try:
        ex.parse_page_range("6", 5)
        check(False, "页码越界应拒绝")
    except ValueError:
        check(True, "页码越界被拒绝")

    check(ex.prior_period_end("2024-12-31", "annual") == "2023-12-31", "期初（annual）")
    check(ex.prior_period_end("2024-06-30", "interim") is None, "期初（interim）不确定 → None")

    check(ex.resolve_period_header("2024-12-31", None, None) == ("2024-12-31", "annual"),
          "绝对日期无需锚点")
    check(ex.resolve_period_header("期末余额", "2024-12-31", "annual") == ("2024-12-31", "annual"),
          "期末余额 + 锚点")
    check(ex.resolve_period_header("期初余额", "2024-12-31", "annual") == ("2023-12-31", "annual"),
          "期初余额 + 锚点")
    check(ex.resolve_period_header("期末余额", None, None) == (None, None), "相对词无锚点 → None")
    check(ex.resolve_period_header("本期", "2024-12-31", "annual") == (None, None), "未支持相对词 → None")

    ap, apt = ex.extract_anchor_period("合并资产负债表\n2024年12月31日\n单位：千元")
    check(ap == "2024-12-31" and apt == "annual", "页面锚点提取")

    # ---- 单元格 bbox 读取（None cell / 越界 → None，绝不伪造 [0,0,0,0]）----
    class _Cell(list):
        pass

    class _Row:
        def __init__(self, cells):
            self.cells = cells

    class _Tbl:
        def __init__(self, rows):
            self.rows = rows

    check(ex._cell_bbox(_Tbl([_Row([_Cell([1.0, 2.0, 3.0, 4.0]), _Cell([5.0, 6.0, 7.0, 8.0])])]), 0, 1)
          == [5.0, 6.0, 7.0, 8.0], "有效 cell → bbox 列表")
    check(ex._cell_bbox(_Tbl([_Row([_Cell([1.0, 2.0, 3.0, 4.0]), None])]), 0, 1) is None,
          "None cell → None（不崩溃）")
    check(ex._cell_bbox(_Tbl([_Row([_Cell([1.0, 2.0, 3.0, 4.0])])]), 0, 5) is None, "列越界 → None")
    check(ex._cell_bbox(_Tbl([_Row([_Cell([1.0, 2.0, 3.0, 4.0])])]), 9, 0) is None, "行越界 → None")

    # ---- None-bbox 单元格：record issue + skip（不伪造 [0,0,0,0] 占位）----
    db_bbox = _tmp_db()
    pdf_bbox = _tmp_pdf(prefix="eval_bbox_")
    orig_pp = ex.pdfplumber
    try:
        with open(pdf_bbox, "wb") as f:
            f.write(b"%PDF-1.4\n%%EOF\n")
        source_document_id, source_version = _register(db_bbox, pdf_bbox)

        grid = [
            ["Item", "2024-12-31", "2023-12-31"],
            ["CASH", "1000", "900"],
            ["TOTAL ASSETS", "1234", "1000"],
        ]
        tbl_rows = [
            _Row([_Cell([10.0, 700.0, 130.0, 725.0]), _Cell([130.0, 700.0, 250.0, 725.0]),
                  _Cell([250.0, 700.0, 370.0, 725.0])]),
            _Row([_Cell([10.0, 675.0, 130.0, 700.0]), _Cell([130.0, 675.0, 250.0, 700.0]),
                  None]),  # 第二期间列单元格 bbox 缺失
            _Row([_Cell([10.0, 650.0, 130.0, 675.0]), _Cell([130.0, 650.0, 250.0, 675.0]),
                  _Cell([250.0, 650.0, 370.0, 675.0])]),
        ]

        class _FakeTable:
            def __init__(self):
                self.rows = tbl_rows
                self.bbox = [10.0, 700.0, 370.0, 625.0]

            def extract(self):
                return [list(r) for r in grid]

        class _FakePage:
            def __init__(self):
                self._text = "ACME Inc. Consolidated Balance Sheet 2024-12-31"
                self._tables = [_FakeTable()]

            def extract_text(self):
                return self._text

            def find_tables(self, settings=None):
                return self._tables

        class _FakePdf:
            def __init__(self):
                self.pages = [_FakePage()]

            def close(self):
                pass

        class _FakePdfPlumber:
            def __init__(self, pdf):
                self._pdf = pdf

            def open(self, path):
                return self._pdf

        ex.pdfplumber = _FakePdfPlumber(_FakePdf())
        result = ex.extract_pdf(source_version, _policy(pdf_bbox), persist=True)

        bbox_issues = [i for i in result.issues if i.issue_type == "CELL_BBOX_UNAVAILABLE"]
        check(len(bbox_issues) == 1,
              f"bbox 缺失单元格 → 1 条 CELL_BBOX_UNAVAILABLE（实际 {len(bbox_issues)}）")
        check(len(result.candidates) == 3,
              f"跳过 bbox 缺失单元格，其余 3 候选保留（实际 {len(result.candidates)}）")
        all_bbox = [c.locator.pdf.bbox for c in result.candidates if c.locator and c.locator.pdf]
        check(all(b != [0.0, 0.0, 0.0, 0.0] for b in all_bbox), "无 [0,0,0,0] 占位 bbox")
        persisted_types = {i.issue_type for i in store.list_extraction_issues(result.record_set_version)}
        check("CELL_BBOX_UNAVAILABLE" in persisted_types, "CELL_BBOX_UNAVAILABLE 持久化可审计")
    finally:
        ex.pdfplumber = orig_pp
        _cleanup_db(db_bbox)
        try:
            os.remove(pdf_bbox)
        except FileNotFoundError:
            pass

    # ---- 三张主表合成 PDF ----
    db = _tmp_db()
    pdf = _tmp_pdf()
    try:
        build_pdf(pdf, [
            [_bs_table("Balance Sheet", "Unit: yuan")],
            [_bs_table("Income Statement", "Unit: yuan",
                       header=("Item", "2024-12-31", "2023-12-31"),
                       rows=(("REVENUE", "50000", "45000"), ("NET INCOME", "8000", "7000")))],
            [_bs_table("Cash Flow Statement", "Unit: yuan",
                       header=("Item", "2024-12-31", "2023-12-31"),
                       rows=(("OPERATING CASH", "12000", "11000"),))],
        ])
        source_document_id, source_version = _register(db, pdf)
        result = ex.extract_pdf(source_version, _policy(pdf), persist=True)

        check(len(result.issues) == 0, f"三表合成 PDF 无 issue（实际 {[i.issue_type for i in result.issues]}）")
        check({r.statement_type for r in result.table_regions}
              == {"balance_sheet", "income_statement", "cash_flow"}, "三表类型识别")
        # BS 3 items × 2 periods = 6; IS 2×2=4; CF 1×2=2 → 12。
        check(len(result.candidates) == 12, f"候选总数 12（实际 {len(result.candidates)}）")

        by_item = {c.raw_item_text: c for c in result.candidates if c.period_candidate == "2024-12-31"}
        check(by_item["CASH"].parsed_numeric_value == Decimal("1000"), "CASH 2024")
        check(by_item["RECEIVABLES"].parsed_numeric_value == Decimal("-200"), "括号负数")
        check(by_item["TOTAL ASSETS"].parsed_numeric_value == Decimal("1234.56"), "千分位小数")
        check(any(c.raw_item_text == "REVENUE" and c.parsed_numeric_value == Decimal("50000")
                  for c in result.candidates), "REVENUE 50000")

        # 真实 PDF 坐标回查（kind=pdf + cell bbox + 行列索引）。
        cell = next(c for c in result.candidates if c.raw_item_text == "CASH"
                    and c.period_candidate == "2024-12-31")
        check(cell.locator.kind == "pdf", "locator kind=pdf")
        check(cell.locator.pdf.pdf_page == 1, "物理页 1-based")
        check(cell.locator.pdf.row_index >= 1 and cell.locator.pdf.column_index == 1,
              f"行列索引（row {cell.locator.pdf.row_index}, col {cell.locator.pdf.column_index}）")
        check(len(cell.locator.pdf.bbox) == 4 and cell.locator.pdf.bbox[2] > cell.locator.pdf.bbox[0]
              and cell.locator.pdf.bbox[3] > cell.locator.pdf.bbox[1], "cell bbox 合法")
        check(cell.locator.pdf.document_id == "doc-ev-1" and cell.locator.pdf.document_version == "v1",
              "document id/version 回查")

        # 重复运行严格复用。
        result2 = ex.extract_pdf(source_version, _policy(pdf), persist=True)
        check(result2.reused is True, "重复运行严格复用")

        # 抽取器版本变化 → 新 record_set_version。
        result3 = ex.extract_pdf(source_version, _policy(pdf, extractor_version="1.1"), persist=True)
        check(result3.record_set_version != result.record_set_version, "版本变化生成新 record_set_version")
    finally:
        _cleanup_db(db)
        try:
            os.remove(pdf)
        except FileNotFoundError:
            pass

    # ---- 相对期间（期末/期初 + 锚点）----
    db2 = _tmp_db()
    pdf2 = _tmp_pdf()
    try:
        # 有锚点：页面标题含绝对报告期 → 期末/期初 解析为具体期间。
        build_pdf(pdf2, [[_bs_table(
            title="资产负债表 2024年12月31日", unit="单位：元",
            header=("项目", "期末余额", "期初余额"),
            rows=(("货币资金", "1000", "900"),))]])
        _, sv = _register(db2, pdf2)
        result = ex.extract_pdf(sv, _policy(pdf2), persist=False)
        check(len(result.issues) == 0, f"相对期间有锚点无 issue（实际 {[i.issue_type for i in result.issues]}）")
        cash_end = next((c for c in result.candidates if c.raw_item_text == "货币资金"
                         and c.period_text == "期末余额"), None)
        cash_beg = next((c for c in result.candidates if c.raw_item_text == "货币资金"
                         and c.period_text == "期初余额"), None)
        check(cash_end is not None and cash_end.period_candidate == "2024-12-31",
              f"期末余额 → 2024-12-31（实际 {cash_end.period_candidate if cash_end else None}）")
        check(cash_beg is not None and cash_beg.period_candidate == "2023-12-31",
              f"期初余额 → 2023-12-31（实际 {cash_beg.period_candidate if cash_beg else None}）")

        # 无锚点：页面文本无绝对日期 → 相对词无法解析 → 不生成可对账记录。
        pdf2b = _tmp_pdf(prefix="eval_noor_")
        build_pdf(pdf2b, [[_bs_table(
            title="Balance Sheet", unit="Unit: yuan",
            header=("Item", "期末余额", "期初余额"),
            rows=(("CASH", "1000", "900"),))]])
        _, sv2 = _register(db2, pdf2b)
        result2 = ex.extract_pdf(sv2, _policy(pdf2b), persist=False)
        check(len(result2.candidates) == 0 and
              any(i.issue_type == "HEADER_UNRESOLVED" for i in result2.issues),
              "无锚点相对期间不生成可对账期间（HEADER_UNRESOLVED）")
    finally:
        _cleanup_db(db2)
        for p in (pdf2, pdf2b):
            try:
                os.remove(p)
            except (FileNotFoundError, UnboundLocalError):
                pass

    # ---- 低文本质量 / 无表 ----
    db3 = _tmp_db()
    pdf3 = _tmp_pdf()
    try:
        # 近空白 PDF（仅一行极短文本，< 20 字符）。
        with open(pdf3, "w", encoding="latin-1") as f:
            f.write("%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                    "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                    "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    "/Contents 4 0 R /Resources << /Font << /F3 5 0 R >> >> >> endobj\n"
                    "4 0 obj << /Length 30 >> stream\nBT /F3 9 Tf 50 700 Td (x) Tj ET\nendstream endobj\n"
                    "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
                    "trailer << /Root 1 0 R /Size 6 >>\n%%EOF\n")
        _, sv = _register(db3, pdf3)
        result = ex.extract_pdf(sv, _policy(pdf3), persist=False)
        check(any(i.issue_type == "LOW_TEXT_QUALITY" for i in result.issues), "低文本质量")
    finally:
        _cleanup_db(db3)
        try:
            os.remove(pdf3)
        except FileNotFoundError:
            pass

    # ---- 哈希不匹配 ----
    db4 = _tmp_db()
    pdf4 = _tmp_pdf()
    try:
        build_pdf(pdf4, [[_bs_table()]])
        _, sv = _register(db4, pdf4)
        with open(pdf4, "ab") as f:
            f.write(b"\x00")
        result = ex.extract_pdf(sv, _policy(pdf4), persist=False)
        check(any(i.issue_type == "FILE_HASH_MISMATCH" for i in result.issues), "哈希不匹配")
        check(len(result.candidates) == 0, "哈希不匹配不产生候选")
    finally:
        _cleanup_db(db4)
        try:
            os.remove(pdf4)
        except FileNotFoundError:
            pass

    # ---- 未关联 Phase 1 document ----
    db5 = _tmp_db()
    pdf5 = _tmp_pdf()
    try:
        build_pdf(pdf5, [[_bs_table()]])
        _, sv = _register(db5, pdf5, document_id=None, document_version=None)
        result = ex.extract_pdf(sv, _policy(pdf5), persist=False)
        check(any(i.issue_type == "DOCUMENT_LINK_UNAVAILABLE" for i in result.issues),
              "document 未关联 → 拒绝")
        check(len(result.candidates) == 0, "未关联不产生候选")
    finally:
        _cleanup_db(db5)
        try:
            os.remove(pdf5)
        except FileNotFoundError:
            pass

    # ---- 未关联 Phase 1 document（persist=True 仍持久化问题，不得静默丢弃）----
    db5b = _tmp_db()
    pdf5b = _tmp_pdf()
    try:
        build_pdf(pdf5b, [[_bs_table()]])
        _, sv = _register(db5b, pdf5b, document_id=None, document_version=None)
        result = ex.extract_pdf(sv, _policy(pdf5b), persist=True)
        check(any(i.issue_type == "DOCUMENT_LINK_UNAVAILABLE" for i in result.issues),
              "persist=True 时 DOCUMENT_LINK_UNAVAILABLE 仍在返回 issues")
        persisted = store.list_extraction_issues(result.record_set_version)
        check(any(i.issue_type == "DOCUMENT_LINK_UNAVAILABLE" for i in persisted),
              "persist=True 时 DOCUMENT_LINK_UNAVAILABLE 落盘 extraction_issue（可审计）")
    finally:
        _cleanup_db(db5b)
        try:
            os.remove(pdf5b)
        except FileNotFoundError:
            pass

    # ---- subject mismatch ----
    db6 = _tmp_db()
    pdf6 = _tmp_pdf()
    try:
        build_pdf(pdf6, [[_bs_table()]])
        _, sv = _register(db6, pdf6, subject="mismatch")
        result = ex.extract_pdf(sv, _policy(pdf6), persist=False)
        check(any(i.issue_type == "SUBJECT_MISMATCH" for i in result.issues), "subject mismatch")
    finally:
        _cleanup_db(db6)
        try:
            os.remove(pdf6)
        except FileNotFoundError:
            pass

    # ---- 损坏 PDF / 后缀 ----
    db7 = _tmp_db()
    bad = _tmp_pdf(prefix="eval_bad_")
    try:
        with open(bad, "wb") as f:
            f.write(b"not a real pdf at all")
        file_hash = ex.sha256_file(bad)
        source_document_id = S.scope_source_document_id("ACME", "doc-bad")
        source_version = S.derive_source_version(source_document_id, file_hash)
        store.init_db(db7)
        store.register_source_atomic(
            S.FinancialSourceDocument(source_document_id=source_document_id, company_id="ACME",
                                      source_name="bad.pdf", source_class="financial_statement",
                                      declared_company_name="Acme", detected_company_name="Acme",
                                      subject_match_status="matched", created_at="2026-01-01T00:00:00Z"),
            S.FinancialSourceVersion(source_version=source_version, source_document_id=source_document_id,
                                     file_sha256=file_hash, file_type="pdf", file_size=len(b"not a real pdf at all"),
                                     document_id="d", document_version="v",
                                     created_at="2026-01-01T00:00:00Z"))
        result = ex.extract_pdf(source_version, _policy(bad), persist=False)
        check(any(i.issue_type == "UNSUPPORTED_FORMAT" for i in result.issues), "损坏 PDF")
    finally:
        _cleanup_db(db7)
        try:
            os.remove(bad)
        except FileNotFoundError:
            pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
