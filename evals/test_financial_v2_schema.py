"""Eval: financial_v2 schema + validator（A1 修订）。

用法: python -m evals.test_financial_v2_schema

覆盖：
- 身份/版本/比较键派生确定性、跨公司/跨业务文档不碰撞；
- 公司作用域 source_document_id（A1 修订 4）：同外部编号不同公司不碰撞；
- 内容版本只含文件事实（A1 修订 1）：不含币种/scope/审计/抽取器/期间占位字段；
- 规则版本变化 / 依赖版本变化产生新 record_set_version（A1 修订 5）；
- record_set_version 重算校验（依赖版本纳入身份）；
- 坐标变化产生新 record_id；
- record_hash 覆盖原始科目文本 / raw 值单位币种 / 标准值单位币种 / conversion rule /
  mapping mode / 期间 scope restatement / 完整 locator（A1 修订 11）；
- validator 对非法坐标 / 非法枚举 / id/hash 篡改 / OTHER_WITH_NOTE / 空来源引用的拒绝。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import validator as V
from financial_v2.validator import ValidationError


def _sample_record(**overrides) -> S.SourceFinancialRecord:
    locator = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="资产负债表", row_number=5, column_number=2, cell_address="B5",
        row_header="资产总计", column_header="2024-12-31", unit_text="元",
    ))
    base = dict(
        company_id="300750",
        standard_item_code="TOTAL_ASSETS",
        statement_type="balance_sheet",
        raw_item_text="资产总计",
        raw_value=1000.0,
        raw_unit="yuan",
        raw_currency="CNY",
        std_value=1000.0,
        std_unit="yuan",
        std_currency="CNY",
        conversion_rule_version="1",
        report_period="2024-12-31",
        period_type="annual",
        statement_scope="consolidated",
        currency="CNY",
        restatement_version="0",
        locator=locator,
        mapping_mode="rule",
        confidence=1.0,
        record_hash="",
        quality_flags=[],
        created_at="t",
    )
    base.update(overrides)
    record_set_version = base.get("record_set_version", "rs-x")
    identity = S.record_identity_fields(S.SourceFinancialRecord(
        record_id="", record_set_version=record_set_version,
        **{k: v for k, v in base.items() if k != "record_set_version"}))
    base["record_id"] = S.derive_record_id(record_set_version, identity)
    r = S.SourceFinancialRecord(record_set_version=record_set_version, **base)
    r.record_hash = V._record_hash(r)
    return r


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def _expect_validation_error(fn, msg):
        try:
            fn()
            check(False, msg)
        except ValidationError:
            check(True, msg)

    # ------------------------------------------------------------------
    # 身份 / 版本 / 比较键派生
    # ------------------------------------------------------------------
    sv1 = S.derive_source_version("sd-1", "a" * 64)
    sv2 = S.derive_source_version("sd-1", "a" * 64)
    check(sv1 == sv2, "source_version 派生确定性（同 doc + 同内容）")

    sv_other_doc = S.derive_source_version("sd-2", "a" * 64)
    check(sv1 != sv_other_doc, "source_version 不因同一文件用于不同业务文档而碰撞")

    sv_other_sha = S.derive_source_version("sd-1", "b" * 64)
    check(sv1 != sv_other_sha, "source_version 内容不同则不同")

    # 公司作用域 source_document_id（A1 修订 4）
    sid_a = S.scope_source_document_id("300750", "BS_2024")
    sid_a2 = S.scope_source_document_id("300750", "BS_2024")
    sid_b = S.scope_source_document_id("600000", "BS_2024")
    check(sid_a == sid_a2, "scope_source_document_id 派生确定性")
    check(sid_a != sid_b, "同外部编号不同公司 → 不同内部 id（不碰撞）")

    rs1 = S.derive_record_set_version(sv1, "0.1", "0.1", "0.1", {"pdfplumber": "0.11.4"})
    rs2 = S.derive_record_set_version(sv1, "0.1", "0.1", "0.1", {"pdfplumber": "0.11.4"})
    check(rs1 == rs2, "record_set_version 派生确定性")

    rs_rule_change = S.derive_record_set_version(sv1, "0.2", "0.1", "0.1", {"pdfplumber": "0.11.4"})
    check(rs1 != rs_rule_change, "抽取器版本变化 → 新 record_set_version")

    rs_dep_change = S.derive_record_set_version(sv1, "0.1", "0.1", "0.1", {"pdfplumber": "0.12.0"})
    check(rs1 != rs_dep_change, "依赖版本变化 → 新 record_set_version（A1 修订 5）")

    ck = S.comparison_key("300750", "TOTAL_ASSETS", "balance_sheet", "2024-12-31",
                          "annual", "consolidated", "CNY", "0")
    ck_same = S.comparison_key("300750", "TOTAL_ASSETS", "balance_sheet", "2024-12-31",
                               "annual", "consolidated", "CNY", "0")
    check(ck == ck_same, "comparison_key 派生确定性")

    for field_desc, kwargs in [
        ("company", dict(company_id="600000")),
        ("item", dict(standard_item_code="NET_PROFIT")),
        ("statement_type", dict(statement_type="income_statement")),
        ("period", dict(report_period="2023-12-31")),
        ("period_type", dict(period_type="interim")),
        ("scope", dict(statement_scope="parent")),
        ("currency", dict(currency="USD")),
        ("restatement", dict(restatement_version="1")),
    ]:
        base = dict(company_id="300750", standard_item_code="TOTAL_ASSETS",
                    statement_type="balance_sheet", report_period="2024-12-31",
                    period_type="annual", statement_scope="consolidated",
                    currency="CNY", restatement_version="0")
        base.update(kwargs)
        other = S.comparison_key(**base)
        check(other != ck, f"comparison_key 随 {field_desc} 变化而不同")

    # 坐标变化 → 新 record_id
    rec_a = _sample_record()
    rec_b = _sample_record()
    check(rec_a.record_id == rec_b.record_id, "record_id 同内容同坐标稳定")

    rec_c = _sample_record(locator=S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="资产负债表", row_number=6, column_number=2, cell_address="B6",
        row_header="资产总计", column_header="2024-12-31", unit_text="元")))
    check(rec_a.record_id != rec_c.record_id, "坐标变化 → 新 record_id")

    # ------------------------------------------------------------------
    # 内容版本只含文件事实（A1 修订 1）
    # ------------------------------------------------------------------
    ver_fields = {f.name for f in S.FinancialSourceVersion.__dataclass_fields__.values()}
    for absent in ("currency", "statement_scope", "audit_status", "extractor_name",
                   "report_periods", "extractor_version", "mapping_rule_version",
                   "normalization_rule_version", "quality_flags"):
        check(absent not in ver_fields, f"内容版本不含抽取占位字段 {absent}")

    ver = S.FinancialSourceVersion(
        source_version="sv-x", source_document_id="sd-x", file_sha256="a" * 64,
        file_type="xlsx", file_size=100, document_id=None, document_version=None,
        created_at="t")
    V.validate_source_version(ver)
    check(True, "内容版本仅含文件事实通过校验")

    # ------------------------------------------------------------------
    # 记录集合：抽取事实可空 + record_set_version 重算（A1 修订 5）
    # ------------------------------------------------------------------
    def _rs(**overrides):
        base = dict(
            record_set_version="", source_version="sv-x", extractor_name=None,
            extractor_version="0.1", mapping_rule_version="0.1",
            normalization_rule_version="0.1", dependency_versions={},
            report_periods=[], currency=None, unit=None, statement_scope=None,
            audit_status=None, block_count=0, record_count=0, created_at="t")
        base.update(overrides)
        if base["record_set_version"] == "":
            base["record_set_version"] = S.derive_record_set_version(
                base["source_version"], base["extractor_version"],
                base["mapping_rule_version"], base["normalization_rule_version"],
                base["dependency_versions"])
        return S.FinancialRecordSet(**base)

    rs_ok = _rs()
    V.validate_record_set(rs_ok)
    check(True, "记录集合抽取事实为 None 通过校验（未知显式空）")

    _expect_validation_error(
        lambda: V.validate_record_set(_rs(record_set_version="rs-wrong")),
        "record_set_version 与派生规则重算不一致被拒绝")

    _expect_validation_error(
        lambda: V.validate_record_set(_rs(currency="USD")),
        "记录集合非法 currency 被拒绝")

    # ------------------------------------------------------------------
    # validator：非法坐标
    # ------------------------------------------------------------------
    _expect_validation_error(
        lambda: V.validate_locator(S.SourceLocator(kind="pdf", pdf=S.PdfCellLocator(
            document_id="d", document_version="v", pdf_page=0, row_index=0,
            column_index=0, bbox=[0, 1, 2, 3]))),
        "validator 拒绝 pdf_page=0（非 1-based）")

    _expect_validation_error(
        lambda: V.validate_locator(S.SourceLocator(kind="pdf", pdf=S.PdfCellLocator(
            document_id="d", document_version="v", pdf_page=1, row_index=0,
            column_index=0, bbox=[2, 1, 0, 3]))),
        "validator 拒绝 bbox x1 <= x0")

    _expect_validation_error(
        lambda: V.validate_locator(S.SourceLocator(kind="pdf", pdf=S.PdfCellLocator(
            document_id="d", document_version="v", pdf_page=1, row_index=0,
            column_index=0, bbox=[0, 1, 2, float('nan')]))),
        "validator 拒绝 bbox 含 NaN")

    _expect_validation_error(
        lambda: V.validate_locator(S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
            sheet_name="s", row_number=0, column_number=1, cell_address="A0"))),
        "validator 拒绝 excel row_number=0")

    # ------------------------------------------------------------------
    # validator：非法枚举 / id / hash / 决议
    # ------------------------------------------------------------------
    rec = _sample_record()
    V.validate_record(rec)
    check(True, "validator 通过合法记录")

    tampered_id = S.SourceFinancialRecord(
        **{f: getattr(rec, f) for f in rec.__dataclass_fields__})
    tampered_id.record_id = "rec-deadbeef"
    _expect_validation_error(lambda: V.validate_record(tampered_id),
                             "validator 拒绝 record_id 与内容/坐标重算不一致")

    bad_scope = _sample_record(statement_scope="parent")
    bad_scope.statement_scope = "bogus"
    _expect_validation_error(lambda: V.validate_record(bad_scope),
                             "validator 拒绝非法 statement_scope")

    res_other_without_note = S.ResolutionRecord(
        resolution_id="r1", group_id="g1", candidate_set_hash="h", source_hashes=["s"],
        comparison_key=ck, rule_versions={}, accepted_record_ids=["rec-1"],
        rejected_record_ids=[], reason_code="OTHER_WITH_NOTE", note=None,
        operator="op", confirmed_at="t",
    )
    _expect_validation_error(lambda: V.validate_resolution(res_other_without_note),
                             "validator 拒绝 OTHER_WITH_NOTE 缺 note")

    res_audited = S.ResolutionRecord(
        resolution_id="r2", group_id="g2", candidate_set_hash="h", source_hashes=["s"],
        comparison_key=ck, rule_versions={}, accepted_record_ids=["rec-1"],
        rejected_record_ids=[], reason_code="AUDITED_SOURCE", note=None,
        operator="op", confirmed_at="t",
    )
    V.validate_resolution(res_audited)
    check(True, "validator 通过 AUDITED_SOURCE（无 note 合法）")

    item_empty_refs = S.SnapshotItem(
        snapshot_id="snap-1", comparison_key=ck, standard_item_code="TOTAL_ASSETS",
        amount=Decimal("1.0"), unit="yuan", report_period="2024-12-31",
        period_type="annual", statement_type="balance_sheet",
        statement_scope="consolidated", currency="CNY", restatement_version="0",
        source_refs=[], resolution_id=None,
    )
    _expect_validation_error(lambda: V.validate_snapshot_item(item_empty_refs),
                             "validator 拒绝 snapshot_item source_refs 为空")

    metric_ok_no_value = S.MetricResult(
        metric_result_id="mr-1", snapshot_id="snap-1", formula_id="f1",
        formula_version="1", period="2024", raw_value=None, display_value=None,
        unit="%", input_snapshot_item_refs=[], input_record_refs=[],
        status="CALCULATED_EXACT", reason_code=None, calculation_detail={},
        created_at="t",
    )
    _expect_validation_error(lambda: V.validate_metric_result(metric_ok_no_value),
                             "validator 拒绝 CALCULATED_EXACT 但 raw_value=None")

    # ------------------------------------------------------------------
    # record_hash 覆盖（A1 修订 11）
    # ------------------------------------------------------------------
    base_rec = _sample_record()
    h_base = V._record_hash(base_rec)
    for field, val in [
        ("raw_item_text", "改动后的科目"),
        ("raw_value", 9999.0),
        ("raw_unit", "wan_yuan"),
        ("raw_currency", "USD"),
        ("std_value", 9999.0),
        ("std_unit", "wan_yuan"),
        ("std_currency", "USD"),
        ("conversion_rule_version", "2"),
        ("mapping_mode", "human_confirmed"),
        ("report_period", "2023-12-31"),
        ("period_type", "interim"),
        ("statement_scope", "parent"),
        ("currency", "USD"),
        ("restatement_version", "1"),
    ]:
        tampered = S.SourceFinancialRecord(
            **{f: getattr(base_rec, f) for f in base_rec.__dataclass_fields__})
        setattr(tampered, field, val)
        check(V._record_hash(tampered) != h_base, f"record_hash 覆盖字段 {field}")

    tampered_loc = S.SourceFinancialRecord(
        **{f: getattr(base_rec, f) for f in base_rec.__dataclass_fields__})
    tampered_loc.locator = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="资产负债表", row_number=99, column_number=2, cell_address="B99"))
    check(V._record_hash(tampered_loc) != h_base, "record_hash 覆盖完整 locator")

    # raw provenance 篡改：record_id 不变但 record_hash 变 → validator 拒绝
    for field, val in [
        ("raw_item_text", "改动"),
        ("raw_value", 9999.0),
        ("raw_unit", "wan_yuan"),
        ("conversion_rule_version", "2"),
        ("mapping_mode", "llm_suggested"),
    ]:
        r2 = _sample_record()
        orig_id = r2.record_id
        setattr(r2, field, val)
        check(r2.record_id == orig_id, f"{field} 不参与 record_id（防沿用旧 id 的关键）")
        _expect_validation_error(lambda r=r2: V.validate_record(r),
                                 f"validator 拒绝 {field} 篡改（record_hash 不符）")

    # ------------------------------------------------------------------
    # validator：来源上下文
    # ------------------------------------------------------------------
    _expect_validation_error(
        lambda: V.validate_source_context(S.FinancialSourceContext(
            company_id="", source_name="f.xlsx", source_class="financial_statement")),
        "validator 拒绝空 company_id")

    _expect_validation_error(
        lambda: V.validate_source_context(S.FinancialSourceContext(
            company_id="300750", source_name="f.xlsx", source_class="bogus")),
        "validator 拒绝非法 source_class")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
