"""Eval: financial_v2 同源勾稽（A4 checks）。

用法: python -m evals.test_financial_v2_checks

覆盖（§7.4）：
- 容差（版本化 Decimal：未知单位零容差、已知单位 0.5×元值×项数）；
- 精确值解析（候选 Decimal × 单位换算优先，record.std_value float 兜底，None 透传）；
- 值索引 ok/missing/ambiguous；
- 四类勾稽（恒等式 / 现金余额 / 净利起点 / 收入成本构成）的 PASS / FAIL / NOT_RUN_MISSING_INPUT；
- 端到端 run_checks：落盘 reconciliation_run + reconciliation_check + CHECK_FAILED 问题；
  幂等重放；validate-only 不落盘；缺输入不伪造通过。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import checks
from financial_v2 import normalization as norm
from financial_v2 import schema as S
from financial_v2 import store


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_checks_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> S.SourceLocator:
    return S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2,
        cell_address=f"B{row}", row_header="科目", column_header="2024-12-31", unit_text="万元"))


def _make_candidate(record_set_version: str, source_version: str, company_id: str,
                    raw_item_text: str, statement_type: str | None,
                    value: Decimal | None, *, unit: str = "wan_yuan",
                    period: str = "2024-12-31", row: int = 2) -> S.ExtractedFinancialCell:
    locator = _locator(row)
    raw_value_text = str(value) if value is not None else None
    cid = S.derive_candidate_id(record_set_version, locator, raw_item_text, raw_value_text)
    return S.ExtractedFinancialCell(
        candidate_id=cid, record_set_version=record_set_version, company_id=company_id,
        source_version=source_version, statement_type_candidate=statement_type,
        raw_item_text=raw_item_text, raw_value_text=raw_value_text,
        parsed_numeric_value=value, formula_text=None, cached_formula_value=None,
        period_text=period, period_candidate=period, period_type_candidate="annual",
        scope_candidate="consolidated", currency_candidate="CNY", unit_candidate=unit,
        restatement_candidate=None, min_display_increment=Decimal("0.01"),
        locator=locator, detection_evidence={}, status="EXTRACTED", quality_flags=[],
        created_at="2026-01-01T00:00:00Z")


def _register(db: str) -> tuple[str, str]:
    store.init_db(db)
    source_document_id = S.scope_source_document_id("ACME", "doc-check")
    file_hash = "e" * 64
    source_version = S.derive_source_version(source_document_id, file_hash)
    store.register_source_atomic(
        S.FinancialSourceDocument(
            source_document_id=source_document_id, company_id="ACME", source_name="check.xlsx",
            source_class="financial_statement", declared_company_name="Acme",
            detected_company_name="Acme", subject_match_status="matched",
            created_at="2026-01-01T00:00:00Z"),
        S.FinancialSourceVersion(
            source_version=source_version, source_document_id=source_document_id,
            file_sha256=file_hash, file_type="xlsx", file_size=100,
            document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z"))
    return source_document_id, source_version


def _record(candidate_id: str, std_value: float | None, *,
            statement_type: str = "balance_sheet", item_code: str = "TOTAL_ASSETS",
            period: str = "2024-12-31", raw_unit: str = "wan_yuan") -> S.SourceFinancialRecord:
    loc = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=2, column_number=2, cell_address="B2"))
    rec = S.SourceFinancialRecord(
        record_id="", record_set_version="rs-x", company_id="ACME",
        standard_item_code=item_code, statement_type=statement_type, raw_item_text="x",
        raw_value=std_value, raw_unit=raw_unit, raw_currency="CNY",
        std_value=std_value, std_unit="yuan", std_currency="CNY",
        conversion_rule_version="1.0", report_period=period, period_type="annual",
        statement_scope="consolidated", currency="CNY", restatement_version="0",
        locator=loc, mapping_mode="rule", confidence=1.0, record_hash="",
        quality_flags=[], created_at="2026-01-01T00:00:00Z", candidate_id=candidate_id)
    rec.record_id = S.derive_record_id(rec.record_set_version, S.record_identity_fields(rec))
    return rec


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

    # ---- 容差（版本化 Decimal）----
    check(checks.tolerance_yuan("wan_yuan", 3) == Decimal("15000"),
          f"万元 × 3 项 → 15000 元（实际 {checks.tolerance_yuan('wan_yuan', 3)}）")
    check(checks.tolerance_yuan("yi_yuan", 2) == Decimal("100000000"),
          "亿元 × 2 项 → 1e8 元")
    check(checks.tolerance_yuan(None, 3) == Decimal("0"), "未知单位 → 零容差（严格相等）")
    check(checks.tolerance_yuan("unknown", 1) == Decimal("0"), "unknown 单位 → 零容差")

    # ---- 精确值解析 ----
    c = _make_candidate("rs-x", "sv-x", "ACME", "货币资金", "balance_sheet",
                        Decimal("1000"), unit="wan_yuan")
    by_id = {c.candidate_id: c}
    r = _record(c.candidate_id, 10000000.0, item_code="CASH_AND_EQUIVALENTS")
    check(checks._std_value_decimal(r, by_id) == Decimal("10000000"),
          "候选 Decimal × 单位换算还原精确元值")
    r_fallback = _record(None, 10000000.0)
    check(checks._std_value_decimal(r_fallback, by_id) == Decimal("10000000.0"),
          "无 candidate_id → float 兜底 Decimal(str(...))")
    r_none = _record(None, None)
    check(checks._std_value_decimal(r_none, by_id) is None, "std_value=None → None")

    # ---- 值索引 ok / missing / ambiguous ----
    r1 = _record("c1", 10.0, item_code="TOTAL_ASSETS")
    r2 = _record("c2", 6.0, item_code="TOTAL_LIABILITIES")
    r3 = _record("c3", 6.5, item_code="TOTAL_LIABILITIES")  # 与 r2 同 key → ambiguous
    index = checks._build_value_index([r1, r2, r3], lambda rec: Decimal(str(rec.std_value)))
    st, v, ids = checks._resolve_one(index, ("balance_sheet", "TOTAL_ASSETS", "2024-12-31"))
    check(st == "ok" and v == Decimal("10.0") and ids == [r1.record_id], "单值命中 → ok")
    st, v, ids = checks._resolve_one(index, ("balance_sheet", "TOTAL_LIABILITIES", "2024-12-31"))
    check(st == "ambiguous" and v is None and len(ids) == 2, "同 key 两条 → ambiguous")
    st, v, ids = checks._resolve_one(index, ("balance_sheet", "TOTAL_EQUITY", "2024-12-31"))
    check(st == "missing" and v is None, "无 key → missing")

    # ---- 端到端 PASS：恒等式 + 现金余额，附注/补充资料 NOT_RUN ----
    db = _tmp_db()
    try:
        source_document_id, source_version = _register(db)
        record_set_version = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cands = [
            _make_candidate(record_set_version, source_version, "ACME", "资产总计",
                            "balance_sheet", Decimal("1000"), row=2),
            _make_candidate(record_set_version, source_version, "ACME", "负债合计",
                            "balance_sheet", Decimal("600"), row=3),
            _make_candidate(record_set_version, source_version, "ACME", "所有者权益合计",
                            "balance_sheet", Decimal("400"), row=4),
            _make_candidate(record_set_version, source_version, "ACME", "期初现金及现金等价物余额",
                            "cash_flow", Decimal("40"), row=5),
            _make_candidate(record_set_version, source_version, "ACME", "期末现金及现金等价物余额",
                            "cash_flow", Decimal("100"), row=6),
            _make_candidate(record_set_version, source_version, "ACME", "现金及现金等价物净增加额",
                            "cash_flow", Decimal("60"), row=7),
            _make_candidate(record_set_version, source_version, "ACME", "净利润",
                            "income_statement", Decimal("80.5"), unit="yi_yuan", row=8),
            _make_candidate(record_set_version, source_version, "ACME", "营业收入",
                            "income_statement", Decimal("50000"), row=9),
            _make_candidate(record_set_version, source_version, "ACME", "营业成本",
                            "income_statement", Decimal("40000"), row=10),
        ]
        store.commit_extracted_candidates(cands, [], source_document_id)
        norm_result = norm.normalize_record_set(record_set_version, persist=True)
        check(norm_result.normalized_count == 9, f"标准化 9 条记录（实际 {norm_result.normalized_count}）")

        result = checks.run_checks(record_set_version, persist=True)
        by_type = {e.check_type: e for e in result.evaluations}
        check(result.pass_count == 2 and result.fail_count == 0 and result.not_run_count == 2,
              f"2 PASS + 0 FAIL + 2 NOT_RUN（实际 {result.pass_count}/{result.fail_count}/{result.not_run_count}）")
        check(by_type["BALANCE_SHEET_IDENTITY"].status == "PASS", "资产负债表恒等式 PASS")
        check(by_type["BALANCE_SHEET_IDENTITY"].diff == Decimal("0"),
              f"恒等式差异 0（实际 {by_type['BALANCE_SHEET_IDENTITY'].diff}）")
        check(by_type["CASH_BALANCE_RECONCILIATION"].status == "PASS", "现金余额勾稽 PASS")
        check(by_type["NET_INCOME_CASH_START"].status == "NOT_RUN_MISSING_INPUT",
              "净利起点缺现金流补充资料 → NOT_RUN")
        check(by_type["REVENUE_COST_BREAKDOWN"].status == "NOT_RUN_MISSING_INPUT",
              "收入成本附注缺 → NOT_RUN")

        check(result.checks_committed == 4, f"落盘 4 条 check（实际 {result.checks_committed}）")
        check(result.issues_committed == 0, "无 FAIL → 无 CHECK_FAILED 问题")

        persisted = store.list_reconciliation_checks(result.run_id)
        check(len(persisted) == 4, "reconciliation_check 表 4 条")
        run_row = store.get_reconciliation_run(result.run_id)
        check(run_row is not None and run_row.company_id == "ACME", "reconciliation_run 已落库")

        # 幂等重放。
        result2 = checks.run_checks(record_set_version, persist=True)
        check(result2.reused is True, "二次勾稽 run 复用")
        check(result2.checks_committed == 0 and result2.issues_committed == 0,
              "二次勾稽不重复写 check/问题")
        check(len(store.list_reconciliation_checks(result.run_id)) == 4, "check 未重复")
    finally:
        _cleanup_db(db)

    # ---- 端到端 FAIL：权益缺口 2 万 > 容差 → CHECK_FAILED ----
    db2 = _tmp_db()
    try:
        source_document_id, source_version = _register(db2)
        record_set_version = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cands = [
            _make_candidate(record_set_version, source_version, "ACME", "资产总计",
                            "balance_sheet", Decimal("1000"), row=2),
            _make_candidate(record_set_version, source_version, "ACME", "负债合计",
                            "balance_sheet", Decimal("600"), row=3),
            _make_candidate(record_set_version, source_version, "ACME", "所有者权益合计",
                            "balance_sheet", Decimal("398"), row=4),
        ]
        store.commit_extracted_candidates(cands, [], source_document_id)
        norm.normalize_record_set(record_set_version, persist=True)
        result = checks.run_checks(record_set_version, persist=True)

        bs = next(e for e in result.evaluations if e.check_type == "BALANCE_SHEET_IDENTITY")
        check(bs.status == "FAIL", f"权益缺口 → FAIL（diff={bs.diff}, tol={bs.tolerance}）")
        check(bs.diff == Decimal("20000"), f"差异 20000 元（实际 {bs.diff}）")
        check(bs.tolerance == Decimal("15000"), f"容差 15000 元（实际 {bs.tolerance}）")
        check(result.issues_committed == 1, f"FAIL → 落盘 1 条 CHECK_FAILED（实际 {result.issues_committed}）")

        issues = store.list_extraction_issues(record_set_version)
        check_issues = [i for i in issues if i.issue_type == "CHECK_FAILED"]
        check(len(check_issues) == 1, "extraction_issue 表 1 条 CHECK_FAILED")
        check(check_issues[0].candidate_id is None, "CHECK_FAILED 不绑定单一候选")
        check(check_issues[0].detail.get("check_type") == "BALANCE_SHEET_IDENTITY",
              "CHECK_FAILED 详情含 check_type")
    finally:
        _cleanup_db(db2)

    # ---- validate-only 不落盘 ----
    db3 = _tmp_db()
    try:
        source_document_id, source_version = _register(db3)
        record_set_version = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cands = [
            _make_candidate(record_set_version, source_version, "ACME", "资产总计",
                            "balance_sheet", Decimal("1000"), row=2),
            _make_candidate(record_set_version, source_version, "ACME", "负债合计",
                            "balance_sheet", Decimal("600"), row=3),
            _make_candidate(record_set_version, source_version, "ACME", "所有者权益合计",
                            "balance_sheet", Decimal("400"), row=4),
        ]
        store.commit_extracted_candidates(cands, [], source_document_id)
        norm.normalize_record_set(record_set_version, persist=True)
        result = checks.run_checks(record_set_version, persist=False)
        check(result.pass_count >= 1, "validate-only 仍计算（PASS ≥ 1）")
        check(result.checks_committed == 0, "validate-only 不写 check")
        check(store.get_reconciliation_run(result.run_id) is None, "validate-only 不写 run")
    finally:
        _cleanup_db(db3)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
