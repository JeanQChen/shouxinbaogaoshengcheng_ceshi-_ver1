"""Eval: financial_v2 store（A1 commit 2）。

用法: python -m evals.test_financial_v2_store

覆盖：
- DDL 建表 + 6 类不可变历史事实表的 UPDATE/DELETE 触发器；
- 文档头可更新 subject_match（唯一允许的更新）；
- 重复来源版本/重复文档显式报错（无 INSERT OR IGNORE 掩盖）；
- 记录集合 + 记录的原子写入与写后核对；
- current_record_set 原子切换；
- quarantine 隔离（不修改被保护行）；
- progress 事件独立落盘；
- 跨公司查询隔离；
- 临时 DB 注入，不污染 data/*.db。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator


def _make_doc(company_id="300750", source_document_id="sd-1") -> S.FinancialSourceDocument:
    return S.FinancialSourceDocument(
        source_document_id=source_document_id, company_id=company_id,
        source_name="NDSD_BALANCESHEET.xlsx", source_class="financial_statement",
        declared_company_name="宁德时代", detected_company_name="宁德时代",
        subject_match_status="matched", created_at="2026-01-01T00:00:00Z",
    )


def _make_version(source_document_id="sd-1", file_sha256="a" * 64,
                  source_version=None) -> S.FinancialSourceVersion:
    if source_version is None:
        source_version = S.derive_source_version(source_document_id, file_sha256)
    return S.FinancialSourceVersion(
        source_version=source_version, source_document_id=source_document_id,
        file_sha256=file_sha256, file_type="xlsx", file_size=1024,
        document_id=None, document_version=None, report_periods=["2024-12-31"],
        currency="CNY", statement_scope="consolidated", audit_status="audited",
        extractor_name="excel_extractor", extractor_version="0.1",
        mapping_rule_version="0.1", normalization_rule_version="0.1",
        quality_flags=[], created_at="2026-01-01T00:00:00Z",
    )


def _make_record_set(source_version, record_set_version=None) -> S.FinancialRecordSet:
    if record_set_version is None:
        record_set_version = S.derive_record_set_version(
            source_version, "0.1", "0.1", "0.1", {"openpyxl": "3.1.2"})
    return S.FinancialRecordSet(
        record_set_version=record_set_version, source_version=source_version,
        extractor_version="0.1", mapping_rule_version="0.1",
        normalization_rule_version="0.1", dependency_versions={"openpyxl": "3.1.2"},
        block_count=1, record_count=1, created_at="2026-01-01T00:00:00Z",
    )


def _make_record(record_set_version, company_id="300750", row_number=5) -> S.SourceFinancialRecord:
    locator = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="资产负债表", row_number=row_number, column_number=2,
        cell_address=f"B{row_number}", row_header="资产总计",
        column_header="2024-12-31", unit_text="元"))
    r = S.SourceFinancialRecord(
        record_id="", record_set_version=record_set_version, company_id=company_id,
        standard_item_code="TOTAL_ASSETS", statement_type="balance_sheet",
        raw_item_text="资产总计", raw_value=1000.0, raw_unit="yuan", raw_currency="CNY",
        std_value=1000.0, std_unit="yuan", std_currency="CNY",
        conversion_rule_version="1", report_period="2024-12-31", period_type="annual",
        statement_scope="consolidated", currency="CNY", restatement_version="0",
        locator=locator, mapping_mode="rule", confidence=1.0, record_hash="",
        quality_flags=[], created_at="2026-01-01T00:00:00Z",
    )
    r.record_id = S.derive_record_id(record_set_version, S.record_identity_fields(r))
    r.record_hash = validator._record_hash(r)
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

    # 临时 DB（非 :memory:，per-call connect 需要共享文件）
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_store_")
    os.close(fd)
    try:
        store.init_db(tmp_path)

        # ---- DDL 建表 ----
        conn = sqlite3.connect(tmp_path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("financial_source_document", "financial_source_version",
                  "financial_record_set", "source_financial_record", "resolution_record",
                  "financial_snapshot", "snapshot_item", "current_snapshot",
                  "snapshot_validity", "quarantine", "progress_events", "schema_migrations"):
            check(t in tables, f"表 {t} 存在")

        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for t in ("financial_source_version", "financial_record_set",
                  "source_financial_record", "resolution_record",
                  "financial_snapshot", "snapshot_item"):
            check(f"trg_{t}_no_update" in triggers and f"trg_{t}_no_delete" in triggers,
                  f"不可变触发器存在: {t}")
        conn.close()

        # ---- 来源文档头：可更新 subject_match ----
        doc = _make_doc()
        store.insert_source_document(doc)
        check(store.get_source_document("sd-1") is not None, "插入并读取来源文档头")

        store.update_subject_match("sd-1", "mismatch", "其他公司")
        check(store.get_source_document("sd-1").subject_match_status == "mismatch",
              "文档头允许更新 subject_match_status")

        # 重复插入文档头 → 显式报错
        try:
            store.insert_source_document(doc)
            check(False, "重复插入文档头被拒绝")
        except sqlite3.IntegrityError:
            check(True, "重复插入文档头被拒绝（无静默忽略）")

        # ---- 内容版本 ----
        v = _make_version()
        store.insert_source_version(v)
        check(store.get_source_version(v.source_version) is not None, "插入并读取内容版本")

        # 同 (source_document_id, file_sha256) 重复 → 报错
        dup = _make_version(source_version="sv-manual")
        try:
            store.insert_source_version(dup)
            check(False, "重复内容版本被拒绝")
        except sqlite3.IntegrityError:
            check(True, "重复内容版本被拒绝（UNIQUE(source_document_id, file_sha256)）")

        # ---- 内容版本不可变 ----
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("UPDATE financial_source_version SET file_size=999 WHERE source_version=?",
                         (v.source_version,))
            conn.commit()
            check(False, "内容版本 UPDATE 被触发器阻断")
        except sqlite3.IntegrityError:
            check(True, "内容版本 UPDATE 被触发器阻断")
        try:
            conn.execute("DELETE FROM financial_source_version WHERE source_version=?",
                         (v.source_version,))
            conn.commit()
            check(False, "内容版本 DELETE 被触发器阻断")
        except sqlite3.IntegrityError:
            check(True, "内容版本 DELETE 被触发器阻断")
        conn.close()

        # ---- 记录集合 + 记录 ----
        rs = _make_record_set(v.source_version)
        store.insert_record_set(rs)
        check(store.get_record_set(rs.record_set_version) is not None, "插入并读取记录集合")

        rec = _make_record(rs.record_set_version)
        store.insert_records([rec], rs.record_set_version)
        check(store.count_records(rs.record_set_version) == 1, "记录写入落库数量正确")

        # 记录不可变
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("UPDATE source_financial_record SET std_value=999 WHERE record_id=?",
                         (rec.record_id,))
            conn.commit()
            check(False, "来源记录 UPDATE 被触发器阻断")
        except sqlite3.IntegrityError:
            check(True, "来源记录 UPDATE 被触发器阻断")
        conn.close()

        # ---- current_record_set 原子切换 ----
        store.set_current_record_set("sd-1", rs.record_set_version)
        cur = store.get_current_record_set("sd-1")
        check(cur is not None and cur.record_set_version == rs.record_set_version,
              "current_record_set 指针切换成功")

        # 切换到不存在的 record_set → 报错且不改变现有指针
        try:
            store.set_current_record_set("sd-1", "rs-nonexistent")
            check(False, "切换到不存在 record_set 被拒绝")
        except KeyError:
            check(True, "切换到不存在 record_set 被拒绝")
        check(store.get_current_record_set("sd-1").record_set_version == rs.record_set_version,
              "失败切换不影响旧 current")

        # ---- quarantine 隔离 ----
        store.quarantine("financial_record_set", rs.record_set_version, "完整性损坏")
        check(store.is_quarantined("financial_record_set", rs.record_set_version),
              "quarantine 记录并查询到隔离对象")
        # 被隔离对象的历史行仍在（不物理删除）
        check(store.get_record_set(rs.record_set_version) is not None,
              "隔离不删除被保护历史行")

        # ---- progress 事件 ----
        ev = S.ProgressEvent(event_id="evt-1", run_id="run-1", stage_id="EXTRACTION",
                             status="failed", message_code="解析失败", completed_units=None,
                             total_units=None, error_code="PARSE_FAILED", recoverable=False,
                             created_at="2026-01-01T00:00:00Z")
        store.record_progress(ev)
        check(store.latest_progress("run-1").event_id == "evt-1", "progress 事件落盘并可读")
        check(len(store.history_progress("run-1")) == 1, "progress 历史可读")

        # ---- 跨公司隔离 ----
        store.insert_source_document(_make_doc(company_id="600000", source_document_id="sd-2"))
        check(store.count_source_documents("300750") == 1, "跨公司查询隔离（company A 不返回 B）")
        check(store.list_source_documents("300750")[0].source_document_id == "sd-1",
              "list 按公司过滤")

    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(tmp_path + suffix)
            except FileNotFoundError:
                pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
