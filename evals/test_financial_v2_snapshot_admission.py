"""Eval: financial_v2 快照准入闭环定点修复（A6/A7 收口专项）。

用法: python -m evals.test_financial_v2_snapshot_admission

覆盖（Snapshot 准入闭环三项定点修复）：
- Fix 3 必算公式输入闭环：缺必需科目 → MISSING_REQUIRED_ITEM + report_blocked；
  非必算公式缺口不阻断；利息保障 FINANCE_EXPENSES 代理不算缺口；季报增长率 NA 不算缺口；
  仅检查 request.required_formula_ids（不扫全部 28 项）。
- Fix 1 上游候选/问题血缘：未确认关键映射 → UNCONFIRMED_MAPPING + 阻断 + 排除记录；
  有效映射决议 + 已产出记录 → 不再重复报 UNCONFIRMED_MAPPING。
- Fix 2 同源勾稽失败：真实 FAIL → CHECK_FAILED（携带 run/check/record/item 引用）+ 排除
  受影响记录 + 命中关键 → 阻断；无关 record_set 的 FAIL 不污染本快照。
- 原子性：异常 / checkpoint / current 切换同一事务落盘。
- 真实 300750 样本主链输出（必算公式缺口 + 阻断结果，不硬编码金额）。

全部合成 fixture（公司无关）+ 真实样本，临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import checks
from financial_v2 import formulas
from financial_v2 import reconciliation as recon
from financial_v2 import resolutions as res
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_admit_")
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
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"


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
        created_at=_TS)


class _Seed:
    """直接构造标准化记录并落盘（跳过候选/映射，全字段可控）。"""

    def __init__(self, company: str = "ACME"):
        self.company = company
        self._n = 0

    def _register(self, ext_id: str, file_hash: str) -> tuple[str, str]:
        self._n += 1
        source_document_id = S.scope_source_document_id(self.company, ext_id)
        source_version = S.derive_source_version(source_document_id, file_hash)
        store.register_source_atomic(
            S.FinancialSourceDocument(
                source_document_id=source_document_id, company_id=self.company,
                source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                declared_company_name=self.company, detected_company_name=self.company,
                subject_match_status="matched", created_at=_TS),
            S.FinancialSourceVersion(
                source_version=source_version, source_document_id=source_document_id,
                file_sha256=file_hash, file_type="xlsx", file_size=100,
                document_id=None, document_version=None, created_at=_TS))
        return source_document_id, source_version

    def register(self, ext_id: str) -> tuple[str, str]:
        return self._register(ext_id, hashlib.sha256(ext_id.encode()).hexdigest())

    def _persist(self, source_document_id: str, source_version: str,
                 specs: list[dict]) -> str:
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = []
        for i, sp in enumerate(specs):
            item_code = sp["item_code"]
            value = sp.get("value")
            loc = _locator(i + 2)
            r = S.SourceFinancialRecord(
                record_id="", record_set_version=rs, company_id=self.company,
                standard_item_code=item_code,
                statement_type=sp.get("statement_type", "balance_sheet"),
                raw_item_text=item_code, raw_value=value, raw_unit="wan_yuan",
                raw_currency=sp.get("currency", "CNY"), std_value=value,
                std_unit="yuan", std_currency=sp.get("currency", "CNY"),
                conversion_rule_version="1.0",
                report_period=sp.get("period", "2024-12-31"),
                period_type=sp.get("period_type", "annual"),
                statement_scope=sp.get("scope", "consolidated"),
                currency=sp.get("currency", "CNY"),
                restatement_version=sp.get("restatement", "0"),
                locator=loc, mapping_mode=sp.get("mapping_mode", "rule"),
                confidence=1.0, record_hash="", quality_flags=[],
                created_at=_TS, candidate_id=sp.get("candidate_id"))
            r.record_hash = validator._record_hash(r)
            r.record_id = S.derive_record_id(rs, S.record_identity_fields(r))
            records.append(r)

        record_set = S.FinancialRecordSet(
            record_set_version=rs, source_version=source_version,
            extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={},
            report_periods=sorted({r.report_period for r in records}),
            currency="CNY", unit="wan_yuan", statement_scope="consolidated",
            audit_status="audited", block_count=0, record_count=len(records),
            created_at=_TS, input_candidate_set_version=rs)
        store.commit_normalization_atomic(record_set, records, [], source_document_id)
        return rs

    def persist_records(self, ext_id: str, specs: list[dict]) -> tuple[str, str]:
        """新来源登记 + 记录集落盘，返回 (record_set_version, source_document_id)。"""
        source_document_id, source_version = self.register(ext_id)
        rs = self._persist(source_document_id, source_version, specs)
        return rs, source_document_id

    def persist_llm_suggested(self, ext_id: str, item_code: str = "TOTAL_ASSETS",
                              ) -> tuple[str, str]:
        """Fix 1 专用：落盘一个 mapping_mode=llm_suggested 的记录，并提交其上游候选。

        返回 (output_record_set_version, candidate_id)。record_set.input_candidate_set_version
        精确指向候选输入版本（非 source_version 模糊回扫），供 _unresolved_mapping_exceptions
        读取血缘。
        """
        source_document_id, source_version = self.register(ext_id)
        input_rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cand = _make_candidate(input_rs, source_version, self.company, item_code,
                               "balance_sheet", Decimal("1000.00"))
        store.commit_extracted_candidates([cand], [], source_document_id)

        output_rs = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {"llm": "suggested"})
        loc = _locator(2)
        r = S.SourceFinancialRecord(
            record_id="", record_set_version=output_rs, company_id=self.company,
            standard_item_code=item_code, statement_type="balance_sheet",
            raw_item_text=item_code, raw_value=Decimal("1000.00"), raw_unit="wan_yuan",
            raw_currency="CNY", std_value=Decimal("1000.00"), std_unit="yuan",
            std_currency="CNY", conversion_rule_version="1.0",
            report_period="2024-12-31", period_type="annual",
            statement_scope="consolidated", currency="CNY", restatement_version="0",
            locator=loc, mapping_mode="llm_suggested", confidence=1.0, record_hash="",
            quality_flags=[], created_at=_TS, candidate_id=cand.candidate_id)
        r.record_hash = validator._record_hash(r)
        r.record_id = S.derive_record_id(output_rs, S.record_identity_fields(r))
        record_set = S.FinancialRecordSet(
            record_set_version=output_rs, source_version=source_version,
            extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={"llm": "suggested"},
            report_periods=["2024-12-31"], currency="CNY", unit="wan_yuan",
            statement_scope="consolidated", audit_status="audited", block_count=0,
            record_count=1, created_at=_TS, input_candidate_set_version=input_rs)
        store.commit_normalization_atomic(record_set, [r], [], source_document_id)
        return output_rs, cand.candidate_id


def _commit_balance_sheet_via_checks(company: str, ext_id: str,
                                     equity_decimal: Decimal) -> str:
    """Fix 2 专用：候选 → 标准化 → 同源勾稽，返回输出 record_set_version。

    资产=1000 万元、负债=600 万元、权益=equity 万元；权益 400 恒等 PASS，398 恒等 FAIL
    （缺口 2 万元 > 容差 1.5 万元）。
    """
    source_document_id = S.scope_source_document_id(company, ext_id)
    file_hash = hashlib.sha256(ext_id.encode()).hexdigest()
    source_version = S.derive_source_version(source_document_id, file_hash)
    store.register_source_atomic(
        S.FinancialSourceDocument(
            source_document_id=source_document_id, company_id=company,
            source_name=f"{ext_id}.xlsx", source_class="financial_statement",
            declared_company_name=company, detected_company_name=company,
            subject_match_status="matched", created_at=_TS),
        S.FinancialSourceVersion(
            source_version=source_version, source_document_id=source_document_id,
            file_sha256=file_hash, file_type="xlsx", file_size=100,
            document_id=None, document_version=None, created_at=_TS))
    input_rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
    cands = [
        _make_candidate(input_rs, source_version, company, "资产总计",
                        "balance_sheet", Decimal("1000"), row=2),
        _make_candidate(input_rs, source_version, company, "负债合计",
                        "balance_sheet", Decimal("600"), row=3),
        _make_candidate(input_rs, source_version, company, "所有者权益合计",
                        "balance_sheet", equity_decimal, row=4),
    ]
    store.commit_extracted_candidates(cands, [], source_document_id)
    from financial_v2 import normalization as norm
    norm_result = norm.normalize_record_set(input_rs, persist=True)
    checks.run_checks(norm_result.record_set_version, persist=True)
    return norm_result.record_set_version


def _request(company, record_set_ids, *, reconciliation_run_id=None,
             required_formula_ids=None, as_of_date="2024-12-31",
             run_id="run-admit") -> snapshots.SnapshotBuildRequest:
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date=as_of_date, scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=reconciliation_run_id,
        required_formula_ids=required_formula_ids if required_formula_ids is not None else [],
        restatement_selection={}, policy_adjustments={}, run_id=run_id)


def _item_by_code(items, code):
    return next((it for it in items if it.standard_item_code == code), None)


def _exc_by_type(exceptions, etype):
    return [e for e in exceptions if e.exception_type == etype]


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

    db = _tmp_db()
    try:
        store.init_db(db)
        seed = _Seed("ACME")

        # ---- 场景 1（Fix 3）：缺必需科目 → MISSING_REQUIRED_ITEM + 阻断 ----
        rs1, _ = seed.persist_records("admit-1", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        r1 = snapshots.build_snapshot(
            _request("ACME", [rs1], required_formula_ids=["SOLV_DEBT_RATIO"]))
        gap1 = _exc_by_type(r1.exceptions, "MISSING_REQUIRED_ITEM")
        check(len(gap1) == 1 and gap1[0].standard_item_code == "TOTAL_LIABILITIES",
              f"场景1：缺 TOTAL_LIABILITIES 固化 MISSING_REQUIRED_ITEM（{len(gap1)}）")
        check(gap1[0].detail.get("formula_ids") == ["SOLV_DEBT_RATIO"]
              and gap1[0].detail.get("missing_item_code") == "TOTAL_LIABILITIES",
              "场景1：MISSING_REQUIRED_ITEM detail 携带 formula_ids + missing_item_code")
        check(r1.report_blocked, "场景1：必需公式缺输入 → report_blocked=True")

        # ---- 场景 2（Fix 3）：非必算公式缺口不阻断 ----
        rs2, _ = seed.persist_records("admit-2", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("500.00")},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("300.00")},
        ])
        r2 = snapshots.build_snapshot(
            _request("ACME", [rs2], required_formula_ids=["SOLV_CURRENT_RATIO"]))
        check(not _exc_by_type(r2.exceptions, "MISSING_REQUIRED_ITEM")
              and not r2.report_blocked,
              "场景2：只检查 requested 公式，非必算（如资产负债率缺 TOTAL_LIABILITIES）不阻断")

        # ---- 场景 3（Fix 3）：利息保障 FINANCE_EXPENSES 代理不算缺口 ----
        rs3, _ = seed.persist_records("admit-3", [
            {"item_code": "TOTAL_PROFIT", "value": Decimal("100.00"),
             "statement_type": "income_statement"},
            {"item_code": "FINANCE_EXPENSES", "value": Decimal("20.00"),
             "statement_type": "income_statement"},
        ])
        r3 = snapshots.build_snapshot(
            _request("ACME", [rs3], required_formula_ids=["SOLV_INTEREST_COVER"]))
        check(not _exc_by_type(r3.exceptions, "MISSING_REQUIRED_ITEM")
              and not r3.report_blocked,
              "场景3：INTEREST_EXPENSE 缺失但 FINANCE_EXPENSES 在 → 代理口径，非缺口不阻断")

        # ---- 场景 4（Fix 3）：季报增长率 NOT_APPLICABLE 不算缺口 ----
        rs4, _ = seed.persist_records("admit-4", [
            {"item_code": "TOTAL_REVENUE", "value": Decimal("1000.00"),
             "statement_type": "income_statement", "period": "2024-09-30",
             "period_type": "quarterly"},
        ])
        r4 = snapshots.build_snapshot(_request(
            "ACME", [rs4], required_formula_ids=["GROWTH_REVENUE"],
            as_of_date="2024-09-30"))
        check(not _exc_by_type(r4.exceptions, "MISSING_REQUIRED_ITEM")
              and not r4.report_blocked,
              "场景4：季报增长率 NOT_APPLICABLE → 非缺口不阻断")

        # ---- 场景 5（Fix 1）：未确认关键映射 → UNCONFIRMED_MAPPING + 阻断 + 排除 ----
        rs5, _ = seed.persist_llm_suggested("admit-5", "TOTAL_ASSETS")
        r5 = snapshots.build_snapshot(_request("ACME", [rs5], run_id="run-5"))
        um5 = _exc_by_type(r5.exceptions, "UNCONFIRMED_MAPPING")
        check(len(um5) == 1 and um5[0].detail.get("critical") is True,
              "场景5：未确认关键映射（TOTAL_ASSETS）→ UNCONFIRMED_MAPPING critical=True")
        check(r5.report_blocked, "场景5：关键未确认映射 → report_blocked=True")
        check(_item_by_code(r5.items, "TOTAL_ASSETS") is None,
              "场景5：未确认映射记录不作为可计算值（排除）")

        # ---- 场景 6（Fix 1）：有效映射决议 + 已产出记录 → 不再重复报 ----
        rs6, cand6 = seed.persist_llm_suggested("admit-6", "TOTAL_ASSETS")
        batch = res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="human",
            items=[res.MappingResolutionItem(
                candidate_id=cand6, chosen_item_code="TOTAL_ASSETS",
                reason_code="SCOPE_MATCH", note=None)]))
        check(batch.committed, "场景6：映射决议提交成功")
        r6 = snapshots.build_snapshot(_request("ACME", [rs6], run_id="run-6"))
        check(not _exc_by_type(r6.exceptions, "UNCONFIRMED_MAPPING")
              and not r6.report_blocked,
              "场景6：有效决议 + 已产出记录 → 不再报 UNCONFIRMED_MAPPING，不阻断")

        # ---- 场景 7（Fix 2）：真实勾稽 FAIL 命中关键 → CHECK_FAILED + 排除 + 阻断 ----
        rs7 = _commit_balance_sheet_via_checks("ACME", "admit-7", Decimal("398"))
        r7 = snapshots.build_snapshot(_request("ACME", [rs7], run_id="run-7"))
        cf7 = _exc_by_type(r7.exceptions, "CHECK_FAILED")
        check(len(cf7) == 1, f"场景7：勾稽 FAIL 固化 CHECK_FAILED（{len(cf7)}）")
        check(cf7[0].detail.get("critical") is True
              and cf7[0].detail.get("check_type") == "BALANCE_SHEET_IDENTITY",
              "场景7：CHECK_FAILED 命中关键（三大表）→ critical=True + check_type")
        check(cf7[0].detail.get("run_id") and cf7[0].detail.get("check_id")
              and cf7[0].detail.get("record_ids") and cf7[0].detail.get("item_codes"),
              "场景7：CHECK_FAILED 携带 run/check/record/item 引用")
        check(r7.report_blocked, "场景7：勾稽失败命中关键 → report_blocked=True")
        check(_item_by_code(r7.items, "TOTAL_ASSETS") is None
              and _item_by_code(r7.items, "TOTAL_LIABILITIES") is None
              and _item_by_code(r7.items, "TOTAL_EQUITY") is None,
              "场景7：勾稽失败受影响记录不作为可计算值")

        # ---- 场景 8（Fix 2）：无关 record_set 的 FAIL 不污染本快照 ----
        rs8a = _commit_balance_sheet_via_checks("ACME", "admit-8a", Decimal("400"))
        rs8b = _commit_balance_sheet_via_checks("ACME", "admit-8b", Decimal("398"))
        r8 = snapshots.build_snapshot(_request("ACME", [rs8a], run_id="run-8"))
        check(not _exc_by_type(r8.exceptions, "CHECK_FAILED") and not r8.report_blocked,
              "场景8：无关 record_set（admit-8b）的 FAIL 不污染本快照")
        check(_item_by_code(r8.items, "TOTAL_ASSETS") is not None
              and _item_by_code(r8.items, "TOTAL_LIABILITIES") is not None
              and _item_by_code(r8.items, "TOTAL_EQUITY") is not None,
              "场景8：本快照三大表正常入项")

        # ---- 场景 9：异常 / checkpoint / current 切换同一事务落盘 ----
        rs9, _ = seed.persist_records("admit-9", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        r9 = snapshots.build_snapshot(_request(
            "ACME", [rs9], required_formula_ids=["SOLV_DEBT_RATIO"], run_id="run-9"))
        snap9 = store.get_snapshot(r9.snapshot.snapshot_id)
        exc9 = store.list_snapshot_exceptions(r9.snapshot.snapshot_id)
        cur9 = snapshots.current_snapshot("ACME", "consolidated", "CNY",
                                          "2024-12-31", "credit_analysis")
        check(snap9 is not None and snap9.report_blocked,
              "场景9：快照头部已落盘（report_blocked 持久化）")
        check(len(exc9) == len(r9.exceptions) and exc9,
              "场景9：异常与快照同事务落盘")
        check(cur9 is not None and cur9.snapshot_id == r9.snapshot.snapshot_id,
              "场景9：current 指针同事务切换")
        conn = sqlite3.connect(db)
        cp_count = conn.execute(
            "SELECT COUNT(*) AS c FROM checkpoints WHERE run_id=? AND stage_id='SNAPSHOT_BUILD'",
            ("run-9",),
        ).fetchone()[0]
        conn.close()
        check(cp_count == 1, "场景9：checkpoint 同事务写入（SNAPSHOT_BUILD 1 条）")

    finally:
        _cleanup_db(db)

    # ---- 场景 10：真实 300750 样本主链输出（必算公式缺口 + 阻断结果） ----
    from scripts import run_financial_v2_chain as chain

    samples = Path(__file__).resolve().parent.parent / "data" / "samples" / "300750" / "financial"
    db10 = _tmp_db()
    try:
        store.init_db(db10)
        files = [
            str(samples / "NDSD_BALANCESHEET_2023-2026Q1.xlsx"),
            str(samples / "NDSD_CASH_2023-2026Q1.xlsx"),
            str(samples / "NDSD_EFFORT_2023-2026Q1.xlsx"),
        ]
        summary = chain.run(
            "300750", files, scope="consolidated", currency="CNY",
            declared_name="宁德时代", detected_name="宁德时代",
            persist=True, sample_items=10, target_period=None)
        check("error" not in summary,
              f"场景10：300750 主链无异常（{summary.get('error')}）")
        check(bool(summary.get("snapshot_id")), "场景10：产出 snapshot_id")
        check(summary.get("required_formula_ids") == formulas.DEFAULT_REQUIRED_FORMULA_IDS,
              "场景10：摘要含默认必算公式集合（11 项）")
        check(isinstance(summary.get("exception_type_counts"), dict),
              "场景10：摘要含 exception_type_counts")
        for e in summary.get("exceptions", []):
            check({"exception_type", "standard_item_code", "comparison_key",
                   "blocking_reason"} <= set(e.keys()),
                  f"场景10：异常摘要字段完整（{e.get('exception_type')}）")
    finally:
        _cleanup_db(db10)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
