"""Eval: financial_v2 Snapshot 准入闭环 P0 定点修复回归（A6/A7 收口，最后一次）。

用法: python -m evals.test_financial_v2_snapshot_admission_p0

覆盖 10 项回归（对应 P0 修复四项 + 快照身份/原子性）：
  1. 有效映射决议 → SnapshotItem 确实准入（不只「不报异常」）。
  2. 同一输入 unconfirmed→confirmed 映射 → snapshot_id 变化。
  3. 完全未产出 SourceFinancialRecord 的候选被审计（uncovered_candidate）。
  4. 不可识别非关键候选 → 非阻断诊断，不机械阻断整份报告。
  5. 勾稽检查结果变化 → snapshot_id 变化。
  6. 相同映射/勾稽依赖重跑 → 严格复用（reused=True，同 snapshot_id）。
  7. 未知 required_formula_id → 立即 ValidationError + 零 DB 残留。
  8. 活跃公式版本变化 → snapshot_id 变化（且快照用 ACTIVE_FORMULA_VERSIONS，非硬编码）。
  9. as_of_date 不在快照条目 → 阻断缺口（不得静默「无缺口」）。
  10. 原子提交失败 → 旧 current 不变、无半成品残留。

全部合成 fixture + 临时 DB 注入，不污染生产库。
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

from financial_v2 import formulas
from financial_v2 import resolutions as res
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_p0_")
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
                    value: Decimal | None, *, row: int = 2) -> S.ExtractedFinancialCell:
    locator = _locator(row)
    raw_value_text = str(value) if value is not None else None
    cid = S.derive_candidate_id(record_set_version, locator, raw_item_text, raw_value_text)
    return S.ExtractedFinancialCell(
        candidate_id=cid, record_set_version=record_set_version, company_id=company_id,
        source_version=source_version, statement_type_candidate=statement_type,
        raw_item_text=raw_item_text, raw_value_text=raw_value_text,
        parsed_numeric_value=value, formula_text=None, cached_formula_value=None,
        period_text="2024-12-31", period_candidate="2024-12-31", period_type_candidate="annual",
        scope_candidate="consolidated", currency_candidate="CNY", unit_candidate="wan_yuan",
        restatement_candidate=None, min_display_increment=Decimal("0.01"),
        locator=locator, detection_evidence={}, status="EXTRACTED", quality_flags=[],
        created_at=_TS)


class _Seed:
    def __init__(self, company: str = "ACME"):
        self.company = company

    def _register(self, ext_id: str, file_hash: str) -> tuple[str, str]:
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

    def _record(self, rs: str, source_version: str, item_code: str, value: Decimal,
                *, period: str = "2024-12-31", candidate_id: str | None = None,
                mapping_mode: str = "rule") -> S.SourceFinancialRecord:
        r = S.SourceFinancialRecord(
            record_id="", record_set_version=rs, company_id=self.company,
            standard_item_code=item_code, statement_type="balance_sheet",
            raw_item_text=item_code, raw_value=value, raw_unit="wan_yuan",
            raw_currency="CNY", std_value=value, std_unit="yuan", std_currency="CNY",
            conversion_rule_version="1.0", report_period=period, period_type="annual",
            statement_scope="consolidated", currency="CNY", restatement_version="0",
            locator=_locator(2), mapping_mode=mapping_mode, confidence=1.0,
            record_hash="", quality_flags=[], created_at=_TS, candidate_id=candidate_id)
        r.record_hash = validator._record_hash(r)
        r.record_id = S.derive_record_id(rs, S.record_identity_fields(r))
        return r

    def persist_records(self, ext_id: str, specs: list[dict]) -> tuple[str, str]:
        """新来源登记 + 规则映射记录集落盘，返回 (record_set_version, source_document_id)。"""
        source_document_id, source_version = self.register(ext_id)
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = [
            self._record(rs, source_version, sp["item_code"], sp["value"],
                         period=sp.get("period", "2024-12-31"))
            for sp in specs
        ]
        record_set = S.FinancialRecordSet(
            record_set_version=rs, source_version=source_version, extractor_name=None,
            extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={},
            report_periods=sorted({r.report_period for r in records}),
            currency="CNY", unit="wan_yuan", statement_scope="consolidated",
            audit_status="audited", block_count=0, record_count=len(records),
            created_at=_TS, input_candidate_set_version=rs)
        store.commit_normalization_atomic(record_set, records, [], source_document_id)
        return rs, source_document_id

    def persist_llm_suggested(self, ext_id: str, item_code: str = "TOTAL_ASSETS",
                              ) -> tuple[str, str]:
        """落盘一个 mapping_mode=llm_suggested 记录 + 其上游候选，返回 (output_rs, candidate_id)。"""
        source_document_id, source_version = self.register(ext_id)
        input_rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cand = _make_candidate(input_rs, source_version, self.company, item_code,
                               "balance_sheet", Decimal("1000.00"))
        store.commit_extracted_candidates([cand], [], source_document_id)
        output_rs = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {"llm": "suggested"})
        r = self._record(output_rs, source_version, item_code, Decimal("1000.00"),
                         candidate_id=cand.candidate_id, mapping_mode="llm_suggested")
        record_set = S.FinancialRecordSet(
            record_set_version=output_rs, source_version=source_version, extractor_name=None,
            extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={"llm": "suggested"},
            report_periods=["2024-12-31"], currency="CNY", unit="wan_yuan",
            statement_scope="consolidated", audit_status="audited", block_count=0,
            record_count=1, created_at=_TS, input_candidate_set_version=input_rs)
        store.commit_normalization_atomic(record_set, [r], [], source_document_id)
        return output_rs, cand.candidate_id

    def persist_with_uncovered(self, ext_id: str, item_code: str = "CURRENT_ASSETS",
                               ) -> tuple[str, str]:
        """落盘一个规则映射记录 + 一个完全未产出的候选，返回 (output_rs, uncovered_candidate_id)。"""
        source_document_id, source_version = self.register(ext_id)
        input_rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        cand_ok = _make_candidate(input_rs, source_version, self.company, "流动资产",
                                  "balance_sheet", Decimal("500.00"), row=2)
        cand_unc = _make_candidate(input_rs, source_version, self.company, "其他",
                                   "balance_sheet", None, row=3)
        store.commit_extracted_candidates([cand_ok, cand_unc], [], source_document_id)
        output_rs = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {"uncovered": "1"})
        r = self._record(output_rs, source_version, item_code, Decimal("500.00"),
                         candidate_id=cand_ok.candidate_id)
        record_set = S.FinancialRecordSet(
            record_set_version=output_rs, source_version=source_version, extractor_name=None,
            extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={"uncovered": "1"},
            report_periods=["2024-12-31"], currency="CNY", unit="wan_yuan",
            statement_scope="consolidated", audit_status="audited", block_count=0,
            record_count=1, created_at=_TS, input_candidate_set_version=input_rs)
        store.commit_normalization_atomic(record_set, [r], [], source_document_id)
        return output_rs, cand_unc.candidate_id


def _request(company, record_set_ids, *, reconciliation_run_id=None,
             required_formula_ids=None, as_of_date="2024-12-31",
             run_id="run-p0") -> snapshots.SnapshotBuildRequest:
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


def _derive_base(**kw) -> str:
    b = dict(
        company_id="ACME", scope="consolidated", currency="CNY", as_of_date="2024-12-31",
        purpose="credit_analysis", record_set_ids=["rs-1"], reconciliation_run_id=None,
        source_versions=["sv-1"], resolution_versions=[],
        restatement_selection={}, policy_adjustments=[],
        required_formula_versions={"SOLV_CURRENT_RATIO": "1.0"},
        snapshot_builder_version=S.SNAPSHOT_BUILDER_VERSION,
        admission_rule_version=S.ADMISSION_RULE_VERSION,
        admission_dependencies=None,
    )
    b.update(kw)
    return S.derive_snapshot_id(
        b["company_id"], b["scope"], b["currency"], b["as_of_date"], b["purpose"],
        b["record_set_ids"], b["reconciliation_run_id"], b["source_versions"],
        b["resolution_versions"], b["restatement_selection"], b["policy_adjustments"],
        b["required_formula_versions"], b["snapshot_builder_version"],
        b["admission_rule_version"], b["admission_dependencies"])


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

        # ---- 1. 有效映射决议 → SnapshotItem 确实准入 ----
        rs1, cand1 = seed.persist_llm_suggested("p0-1", "TOTAL_ASSETS")
        res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="human",
            items=[res.MappingResolutionItem(
                candidate_id=cand1, chosen_item_code="TOTAL_ASSETS",
                reason_code="SCOPE_MATCH", note=None)]))
        r1 = snapshots.build_snapshot(_request("ACME", [rs1], run_id="run-p0-1"))
        check(_item_by_code(r1.items, "TOTAL_ASSETS") is not None,
              "1: 有效映射决议后 TOTAL_ASSETS SnapshotItem 确实准入")
        check(not _exc_by_type(r1.exceptions, "UNCONFIRMED_MAPPING"),
              "1: 有效映射决议后不再报 UNCONFIRMED_MAPPING")

        # ---- 2. 同一输入 unconfirmed→confirmed 映射 → snapshot_id 变化 ----
        rs2, cand2 = seed.persist_llm_suggested("p0-2", "TOTAL_ASSETS")
        r2a = snapshots.build_snapshot(_request("ACME", [rs2], run_id="run-p0-2a"))
        res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="human",
            items=[res.MappingResolutionItem(
                candidate_id=cand2, chosen_item_code="TOTAL_ASSETS",
                reason_code="SCOPE_MATCH", note=None)]))
        r2b = snapshots.build_snapshot(_request("ACME", [rs2], run_id="run-p0-2b"))
        check(r2a.snapshot.snapshot_id != r2b.snapshot.snapshot_id,
              "2: unconfirmed→confirmed 映射派生新 snapshot_id")
        check(r2a.report_blocked and not r2b.report_blocked,
              "2: unconfirmed 阻断 / confirmed 解除阻断")

        # ---- 3. 完全未产出 SourceFinancialRecord 的候选被审计 ----
        rs3, unc3 = seed.persist_with_uncovered("p0-3", "CURRENT_ASSETS")
        r3 = snapshots.build_snapshot(_request("ACME", [rs3], run_id="run-p0-3"))
        unc_exc = [e for e in _exc_by_type(r3.exceptions, "UNCONFIRMED_MAPPING")
                   if e.detail.get("reason") == "uncovered_candidate"]
        check(any(e.detail.get("candidate_id") == unc3 for e in unc_exc),
              "3: 未产出候选被审计为 uncovered_candidate 诊断")

        # ---- 4. 不可识别非关键候选 → 不机械阻断整份报告 ----
        rs4, _ = seed.persist_with_uncovered("p0-4", "CURRENT_ASSETS")
        r4 = snapshots.build_snapshot(_request("ACME", [rs4], run_id="run-p0-4"))
        check(not r4.report_blocked,
              "4: 不可识别非关键候选不机械阻断报告（report_blocked=False）")

        # ---- 5. 勾稽检查结果变化 → snapshot_id 变化（身份含 status）----
        sid_pass = _derive_base(admission_dependencies={
            "mapping_resolution_ids": [], "candidate_input_set_versions": ["rs-x"],
            "reconciliation_check_identities": ["chk|PASS"]})
        sid_fail = _derive_base(admission_dependencies={
            "mapping_resolution_ids": [], "candidate_input_set_versions": ["rs-x"],
            "reconciliation_check_identities": ["chk|FAIL"]})
        check(sid_pass != sid_fail,
              "5: 勾稽检查结果变化派生新 snapshot_id")

        # ---- 6. 相同映射/勾稽依赖重跑 → 严格复用 ----
        rs6, _ = seed.persist_records("p0-6", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("500.00")},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("300.00")},
        ])
        req6 = _request("ACME", [rs6], required_formula_ids=["SOLV_CURRENT_RATIO"],
                        run_id="run-p0-6")
        r6a = snapshots.build_snapshot(req6)
        r6b = snapshots.build_snapshot(req6)
        check(r6a.snapshot.snapshot_id == r6b.snapshot.snapshot_id,
              "6: 相同依赖重跑得到同一 snapshot_id")
        check(r6b.reused, "6: 相同依赖重跑 → strict reuse（reused=True）")

        # ---- 7. 未知 required_formula_id → 立即 fail + 零 DB 残留 ----
        rs7, _ = seed.persist_records("p0-7", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        conn = sqlite3.connect(db)
        snap_before = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
        exc_before = conn.execute("SELECT COUNT(*) FROM snapshot_exception").fetchone()[0]
        conn.close()
        try:
            snapshots.build_snapshot(_request(
                "ACME", [rs7], required_formula_ids=["BOGUS_FORMULA"], run_id="run-p0-7"))
            check(False, "7: 未知公式 ID 应抛 ValidationError")
        except validator.ValidationError:
            check(True, "7: 未知 required_formula_id → 立即 ValidationError")
        conn = sqlite3.connect(db)
        snap_after = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
        exc_after = conn.execute("SELECT COUNT(*) FROM snapshot_exception").fetchone()[0]
        conn.close()
        check(snap_after == snap_before and exc_after == exc_before,
              "7: 未知公式 ID 拒绝后零快照/异常 DB 残留")

        # ---- 8. 活跃公式版本 → 快照用 ACTIVE_FORMULA_VERSIONS + 版本变化改 id ----
        rs8, _ = seed.persist_records("p0-8", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("500.00")},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("300.00")},
        ])
        r8 = snapshots.build_snapshot(_request(
            "ACME", [rs8], required_formula_ids=["SOLV_CURRENT_RATIO"], run_id="run-p0-8"))
        check(r8.snapshot.required_formula_versions["SOLV_CURRENT_RATIO"]
              == formulas.ACTIVE_FORMULA_VERSIONS["SOLV_CURRENT_RATIO"],
              "8: 快照公式版本取自 ACTIVE_FORMULA_VERSIONS（非硬编码）")
        check(_derive_base(required_formula_versions={"SOLV_CURRENT_RATIO": "1.0"})
              != _derive_base(required_formula_versions={"SOLV_CURRENT_RATIO": "2.0"}),
              "8: 活跃公式版本变化派生新 snapshot_id")

        # ---- 9. as_of_date 不在快照条目 → 阻断缺口（不得静默无缺口）----
        rs9, _ = seed.persist_records("p0-9", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00"), "period": "2023-12-31"},
            {"item_code": "TOTAL_LIABILITIES", "value": Decimal("600.00"), "period": "2023-12-31"},
        ])
        r9 = snapshots.build_snapshot(_request(
            "ACME", [rs9], required_formula_ids=["SOLV_DEBT_RATIO"],
            as_of_date="2024-12-31", run_id="run-p0-9"))
        gap9 = _exc_by_type(r9.exceptions, "MISSING_REQUIRED_ITEM")
        check(len(gap9) > 0 and r9.report_blocked,
              "9: as_of_date 缺失必算输入 → 阻断缺口（不静默无缺口）")

        # ---- 10. 原子提交失败 → 旧 current 不变、无半成品残留 ----
        rs10, _ = seed.persist_records("p0-10", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("500.00")},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("300.00")},
        ])
        r10a = snapshots.build_snapshot(_request(
            "ACME", [rs10], required_formula_ids=["SOLV_CURRENT_RATIO"], run_id="run-p0-10"))
        old_current = snapshots.current_snapshot("ACME", "consolidated", "CNY",
                                                 "2024-12-31", "credit_analysis").snapshot_id
        rs10b, _ = seed.persist_records("p0-10b", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        conn = sqlite3.connect(db)
        snap_before = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
        conn.execute("CREATE TRIGGER trg_p0_fail_item BEFORE INSERT ON snapshot_item "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            snapshots.build_snapshot(_request(
                "ACME", [rs10b], required_formula_ids=[], run_id="run-p0-10b"))
            check(False, "10: 注入 item 写入失败应抛异常")
        except Exception:
            check(True, "10: 原子提交中途失败被触发")
        conn = sqlite3.connect(db)
        conn.execute("DROP TRIGGER trg_p0_fail_item")
        conn.commit()
        snap_after = conn.execute("SELECT COUNT(*) FROM financial_snapshot").fetchone()[0]
        conn.close()
        cur10 = snapshots.current_snapshot("ACME", "consolidated", "CNY",
                                           "2024-12-31", "credit_analysis")
        check(cur10 is not None and cur10.snapshot_id == old_current,
              "10: 提交失败后旧 current 指针保持不变")
        check(snap_after == snap_before, "10: 提交失败后无半成品快照残留")

        # ---- 11. 未产出候选新增 active 决议 → 新 snapshot_id（身份含未产出决议）----
        rs11, unc11 = seed.persist_with_uncovered("p0-11", "CURRENT_ASSETS")
        r11a = snapshots.build_snapshot(_request("ACME", [rs11], run_id="run-p0-11a"))
        res.submit_mapping_resolutions(res.MappingResolutionBatchRequest(
            company_id="ACME", operator="human",
            items=[res.MappingResolutionItem(
                candidate_id=unc11, chosen_item_code="TOTAL_ASSETS",
                reason_code="SCOPE_MATCH", note=None)]))
        # 未重跑 normalization（仍无产出记录），仅新增 active 决议 → 快照身份必须变化。
        r11b = snapshots.build_snapshot(_request("ACME", [rs11], run_id="run-p0-11b"))
        check(r11a.snapshot.snapshot_id != r11b.snapshot.snapshot_id,
              "11: 未产出候选新增 active 决议 → 新 snapshot_id（不重跑 normalization）")
        unc_a = [e for e in r11a.exceptions if e.detail.get("reason") == "uncovered_candidate"]
        unc_b = [e for e in r11b.exceptions if e.detail.get("reason") == "uncovered_candidate"]
        check(unc_a and unc_b and unc_a[0].standard_item_code != unc_b[0].standard_item_code,
              "11: 新增决议后未产出候选异常内容变化（UNMAPPED → 决议科目）")
        r11c = snapshots.build_snapshot(_request("ACME", [rs11], run_id="run-p0-11c"))
        check(r11c.reused and r11c.snapshot.snapshot_id == r11b.snapshot.snapshot_id,
              "11: 相同决议状态重跑 → 复用同一 snapshot_id")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
