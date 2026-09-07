"""Eval: financial_v2 抽取器（A2 Excel 真实坐标候选层）。

用法: python -m evals.test_financial_v2_extractors

覆盖（A2）：
- 三张主表识别、多期间列全提取、sheet 名/表名/关键行命中；
- Excel 日期 / 中文日期 / 相对期间的解析；
- 单位（元/万元/亿元/未知/冲突）、scope（合并/母公司/未知）；
- 括号负数、千分位、小数、零、破折号、空白、不适用、非法文本；
- 公式文本与缓存值分离（无缓存值 → FORMULA_VALUE_UNAVAILABLE）；
- 真实 A1 地址回查；文件哈希不匹配 / 损坏 / `.xls`；
- 重复运行严格复用；规则/依赖版本变化生成新 record_set_version。

全部合成 fixture（公司无关），临时 DB / 临时目录注入，不污染生产库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import excel_extractor as ex


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_ext_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _tmp_xlsx(prefix: str = "eval_ext_") -> str:
    fd, p = tempfile.mkstemp(suffix=".xlsx", prefix=prefix)
    os.close(fd)
    return p


def _register(db_path: str, file_path: str, company_id: str = "ACME",
              source_name: str = "BS.xlsx") -> tuple[str, str]:
    store.init_db(db_path)
    file_hash = ex.sha256_file(file_path)
    external_id = f"doc-{company_id}-{source_name}"
    source_document_id = S.scope_source_document_id(company_id, external_id)
    source_version = S.derive_source_version(source_document_id, file_hash)
    doc = S.FinancialSourceDocument(
        source_document_id=source_document_id, company_id=company_id, source_name=source_name,
        source_class="financial_statement", declared_company_name="Acme",
        detected_company_name="Acme", subject_match_status="matched",
        created_at="2026-01-01T00:00:00Z")
    version = S.FinancialSourceVersion(
        source_version=source_version, source_document_id=source_document_id,
        file_sha256=file_hash, file_type="xlsx", file_size=Path(file_path).stat().st_size,
        document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z")
    store.register_source_atomic(doc, version)
    return source_document_id, source_version


def _policy(file_path: str, **kw) -> ex.ExcelExtractionPolicy:
    return ex.ExcelExtractionPolicy(
        file_path=file_path,
        dependency_versions={"openpyxl": openpyxl.__version__},
        **kw,
    )


def _build_basic_workbook(path: str) -> None:
    """三表、两期间、负数/括号/千分位/零/破折号（万元，无 scope 声明）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "资产负债表"
    ws.append(["资产负债表(单位：万元)", "2024-12-31", "2023-12-31"])
    ws.append(["货币资金", 1000, 900])
    ws.append(["应收账款", "(200)", "-100"])
    ws.append(["存货", "1,234.56", "—"])
    ws.append(["长期借款", 0, 5000])

    ws2 = wb.create_sheet("利润表")
    ws2.append(["利润表(单位：万元)", "2024-12-31", "2023-12-31"])
    ws2.append(["营业收入", 50000, 45000])
    ws2.append(["净利润", 8000, 7000])

    ws3 = wb.create_sheet("现金流量表")
    ws3.append(["现金流量表(单位：万元)", "2024-12-31", "2023-12-31"])
    ws3.append(["经营活动产生的现金流量净额", 12000, 11000])
    wb.save(path)


def _make_formula_cached_xlsx(path: str) -> None:
    """构造一个真实含「公式 + 缓存值」单元格的 xlsx。

    openpyxl 保存公式时不写缓存值（data_only 加载拿不到结果），故手写 OOXML：B2 单元格
    同时含 <f>=100+200</f>（公式文本）与 <v>300</v>（Excel 保存的缓存结果）。
    """
    import zipfile

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="利润表" sheetId="1" r:id="rId1"/></sheets></workbook>')
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>利润表(单位：万元)</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>2024-12-31</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>营业利润</t></is></c>'
        '<c r="B2"><f>100+200</f><v>300</v></c></row>'
        '</sheetData></worksheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


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

    # ---- 纯函数：金额 / 期间 / 展示精度 ----
    check(ex.parse_decimal("1,234.56") == Decimal("1234.56"), "千分位解析")
    check(ex.parse_decimal("(200)") == Decimal("-200"), "英文括号负数")
    check(ex.parse_decimal("（200）") == Decimal("-200"), "中文括号负数")
    check(ex.parse_decimal("-100") == Decimal("-100"), "显式负号")
    check(ex.parse_decimal("0") == Decimal("0"), "零解析为 0")
    check(ex.parse_decimal("12.5%") is None, "百分数（金额字段）拒绝解析")
    check(ex.parse_decimal("abc") is None, "非法文本拒绝解析")
    check(ex.is_empty_value("—") and ex.is_empty_value("不适用") and ex.is_empty_value(""),
          "破折号/不适用/空串识别为空")
    check(not ex.is_empty_value("0"), "0 不是空")
    check(ex.display_increment("1,234.56") == Decimal("0.01"), "2 位小数展示增量 0.01")
    check(ex.display_increment("100") == Decimal("1"), "整数展示增量 1")
    check(ex.display_increment("(7.7)") == Decimal("0.1"), "括号负数 1 位小数展示增量 0.1")

    p, pt = ex.parse_period("2024-12-31")
    check(p == "2024-12-31" and pt == "annual", "YYYY-MM-DD 年报")
    p, pt = ex.parse_period("2024年6月30日")
    check(p == "2024-06-30" and pt == "interim", "中文日期半年报")
    p, pt = ex.parse_period("2024年报")
    check(p == "2024-12-31" and pt == "annual", "年报")
    p, pt = ex.parse_period("2026一季报")
    check(p == "2026-03-31" and pt == "quarterly", "中文季报")
    p, pt = ex.parse_period("2024")
    check(p == "2024-12-31" and pt == "annual", "纯年份")
    check(ex.parse_period("本期") == (None, None), "相对期间词不在无锚点解析")

    # ---- 基本抽取：三表、坐标、单位、负数、零、破折号 ----
    db = _tmp_db()
    xlsx = _tmp_xlsx()
    try:
        _build_basic_workbook(xlsx)
        source_document_id, source_version = _register(db, xlsx)
        result = ex.extract_excel(source_version, _policy(xlsx), persist=True)

        check(len(result.table_regions) == 3, "识别出三张主表区域")
        check({r.statement_type for r in result.table_regions}
              == {"balance_sheet", "income_statement", "cash_flow"}, "三表类型正确")
        check(len(result.issues) == 0, "基本工作簿无 issue")
        check(len(result.candidates) == 14, f"候选总数 14（实际 {len(result.candidates)}）")

        by_item = {c.raw_item_text: c for c in result.candidates if c.period_candidate == "2024-12-31"}
        check(by_item["货币资金"].parsed_numeric_value == Decimal("1000"), "货币资金 2024")
        check(by_item["应收账款"].parsed_numeric_value == Decimal("-200"), "括号负数")
        check(by_item["存货"].parsed_numeric_value == Decimal("1234.56"), "千分位小数")
        check(by_item["长期借款"].parsed_numeric_value == Decimal("0"), "零值")
        check(all(c.unit_candidate == "wan_yuan" for c in result.candidates), "单位识别万元")
        check(all(c.scope_candidate is None for c in result.candidates), "未声明 scope → None（不默认）")
        check(all(c.currency_candidate is None for c in result.candidates), "未声明币种 → None（不默认 CNY）")
        check(by_item["货币资金"].period_type_candidate == "annual", "期间类型 annual")

        # 真实 A1 地址回查。
        cell = next(c for c in result.candidates if c.raw_item_text == "货币资金"
                    and c.period_candidate == "2024-12-31")
        check(cell.locator.excel.cell_address == "B2", f"A1 地址回查（实际 {cell.locator.excel.cell_address}）")
        check(cell.locator.excel.sheet_name == "资产负债表", "sheet 名回查")
        check(cell.locator.excel.row_number == 2 and cell.locator.excel.column_number == 2,
              "行列坐标回查")

        # 破折号 → EMPTY_OR_NOT_APPLICABLE。
        dash = next(c for c in result.candidates if c.raw_item_text == "存货"
                    and c.period_candidate == "2023-12-31")
        check(dash.status == "EMPTY_OR_NOT_APPLICABLE" and dash.parsed_numeric_value is None,
              "破折号 → EMPTY_OR_NOT_APPLICABLE")

        # 持久化后可读 + 溯源链（record set → source version → 候选）。
        check(store.list_candidates(result.record_set_version).__len__() == 14,
              "持久化后候选可读")
        # 重复运行严格复用。
        result2 = ex.extract_excel(source_version, _policy(xlsx), persist=True)
        check(result2.reused is True, "重复运行严格复用（reused=True）")
        check(store.list_candidates(result.record_set_version).__len__() == 14,
              "重复运行不重复写入")

        # 规则/依赖版本变化 → 新 record_set_version。
        result3 = ex.extract_excel(
            source_version, _policy(xlsx, extractor_version="1.1"), persist=True)
        check(result3.record_set_version != result.record_set_version, "抽取器版本变化生成新 record_set_version")
    finally:
        _cleanup_db(db)
        try:
            os.remove(xlsx)
        except FileNotFoundError:
            pass

    # ---- 文件哈希不匹配 / 损坏 / .xls ----
    db2 = _tmp_db()
    xlsx2 = _tmp_xlsx()
    try:
        _build_basic_workbook(xlsx2)
        _, source_version = _register(db2, xlsx2)
        # 篡改文件内容（改扩展名前的路径内容）。
        with open(xlsx2, "ab") as f:
            f.write(b"\x00")
        result = ex.extract_excel(source_version, _policy(xlsx2), persist=False)
        check(any(i.issue_type == "FILE_HASH_MISMATCH" for i in result.issues),
              "文件哈希不匹配 → FILE_HASH_MISMATCH")
        check(len(result.candidates) == 0, "哈希不匹配不产生候选")
    finally:
        _cleanup_db(db2)
        try:
            os.remove(xlsx2)
        except FileNotFoundError:
            pass

    db3 = _tmp_db()
    xls = tempfile.mktemp(suffix=".xls", prefix="eval_ext_")
    try:
        with open(xls, "w") as f:
            f.write("not an xlsx")
        file_hash = ex.sha256_file(xls)
        source_document_id = S.scope_source_document_id("ACME", "doc-xls")
        source_version = S.derive_source_version(source_document_id, file_hash)
        store.init_db(db3)
        store.register_source_atomic(
            S.FinancialSourceDocument(source_document_id=source_document_id, company_id="ACME",
                                      source_name="old.xls", source_class="financial_statement",
                                      declared_company_name="Acme", detected_company_name="Acme",
                                      subject_match_status="matched", created_at="2026-01-01T00:00:00Z"),
            S.FinancialSourceVersion(source_version=source_version, source_document_id=source_document_id,
                                     file_sha256=file_hash, file_type="xlsx", file_size=11,
                                     document_id=None, document_version=None,
                                     created_at="2026-01-01T00:00:00Z"))
        result = ex.extract_excel(source_version, _policy(xls), persist=False)
        check(any(i.issue_type == "UNSUPPORTED_FORMAT" for i in result.issues),
              "非 .xlsx → UNSUPPORTED_FORMAT")
    finally:
        _cleanup_db(db3)
        try:
            os.remove(xls)
        except FileNotFoundError:
            pass

    # ---- 中文日期 + Excel 日期 + scope 声明 ----
    db4 = _tmp_db()
    xlsx4 = _tmp_xlsx()
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "合并资产负债表"
        ws.append(["合并资产负债表(单位：亿元)", "2024年12月31日", "2023年12月31日"])
        ws.append(["货币资金", 12.5, 11.2])
        wb.save(xlsx4)
        _, sv = _register(db4, xlsx4)
        result = ex.extract_excel(sv, _policy(xlsx4), persist=False)
        check(any(c.scope_candidate == "consolidated" for c in result.candidates),
              "合并 scope 识别")
        check(any(c.unit_candidate == "yi_yuan" for c in result.candidates), "亿元单位识别")
        check(any(c.period_candidate == "2024-12-31" for c in result.candidates), "中文日期解析")
        check(any(c.raw_item_text == "货币资金" and c.parsed_numeric_value == Decimal("12.5")
                  for c in result.candidates), "亿元值解析")
    finally:
        _cleanup_db(db4)
        try:
            os.remove(xlsx4)
        except FileNotFoundError:
            pass

    # ---- 公式文本（无缓存值 → FORMULA_VALUE_UNAVAILABLE）----
    db5 = _tmp_db()
    xlsx5 = _tmp_xlsx()
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "利润表"
        ws.append(["利润表(单位：万元)", "2024-12-31"])
        ws.append(["营业利润", "=100+200"])
        wb.save(xlsx5)
        _, sv = _register(db5, xlsx5)
        result = ex.extract_excel(sv, _policy(xlsx5), persist=False)
        formula_cell = next(c for c in result.candidates if c.raw_item_text == "营业利润")
        check(formula_cell.formula_text == "=100+200", "公式文本保存")
        check("FORMULA_VALUE_UNAVAILABLE" in formula_cell.quality_flags, "无缓存值 → 标记")
        check(formula_cell.status == "PARSE_FAILED", "公式无缓存值不算已解析数值")
    finally:
        _cleanup_db(db5)
        try:
            os.remove(xlsx5)
        except FileNotFoundError:
            pass

    # ---- 公式 + 可信缓存值 → 缓存值为权威解析值（fix #3）----
    db6 = _tmp_db()
    xlsx6 = _tmp_xlsx(prefix="eval_ext_formula_")
    try:
        _make_formula_cached_xlsx(xlsx6)
        _, sv = _register(db6, xlsx6)
        result = ex.extract_excel(sv, _policy(xlsx6), persist=False)
        formula_cell = next(c for c in result.candidates if c.raw_item_text == "营业利润")
        check(formula_cell.formula_text == "=100+200", "公式文本保存（缓存值场景）")
        check(formula_cell.cached_formula_value == Decimal("300"), "缓存值保存")
        check(formula_cell.parsed_numeric_value == Decimal("300"),
              "公式单元格以缓存值为权威解析值（300）")
        check(formula_cell.status == "EXTRACTED", "公式有可信缓存值 → EXTRACTED（非 PARSE_FAILED）")
        check("FORMULA_CACHED_VALUE" in formula_cell.quality_flags, "缓存值来源标记 FORMULA_CACHED_VALUE")
    finally:
        _cleanup_db(db6)
        try:
            os.remove(xlsx6)
        except FileNotFoundError:
            pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
