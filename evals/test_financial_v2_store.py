"""Eval: financial_v2 store（A1 修订）。

用法: python -m evals.test_financial_v2_store

覆盖：
- DDL 建表 + 6 类不可变历史事实表的 UPDATE/DELETE 触发器；
- 内容版本无抽取占位列、记录集合含抽取事实列（A1 修订 1）；
- register_source_atomic 原子登记：幂等复用、跨公司/class/声明名复用拒绝、
  主体匹配受控合并、故障注入不残留文档头（A1 修订 2/10）；
- commit_record_set 原子提交：严格复用/存储冲突、跨文档跨公司指针拒绝、
  隔离拒绝、故障注入不残留半成品（A1 修订 6/7/8）；
- 依赖版本变化产生两个 Record Set（A1 修订 5）；
- snapshot/resolution validity 确定性读取 + 状态机（幂等 + 非法倒退拒绝，A1 修订 9）；
- quarantine / progress / 跨公司隔离；
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


def _make_doc(company_id="300750", source_document_id="sd-1", source_class="financial_statement",
              declared="宁德时代", detected="宁德时代", subject="matched") -> S.FinancialSourceDocument:
    return S.FinancialSourceDocument(
        source_document_id=source_document_id, company_id=company_id,
        source_name="NDSD_BALANCESHEET.xlsx", source_class=source_class,
        declared_company_name=declared, detected_company_name=detected,
        subject_match_status=subject, created_at="2026-01-01T00:00:00Z",
    )


def _make_version(source_document_id="sd-1", file_sha256="a" * 64,
                  source_version=None) -> S.FinancialSourceVersion:
    if source_version is None:
        source_version = S.derive_source_version(source_document_id, file_sha256)
    return S.FinancialSourceVersion(
        source_version=source_version, source_document_id=source_document_id,
        file_sha256=file_sha256, file_type="xlsx", file_size=1024,
        document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z",
    )


def _make_record_set(source_version, dependency_versions=None, extractor_version="0.1",
                     record_set_version=None, record_count=1) -> S.FinancialRecordSet:
    dep = dependency_versions or {"openpyxl": "3.1.2"}
    if record_set_version is None:
        record_set_version = S.derive_record_set_version(
            source_version, extractor_version, "0.1", "0.1", dep)
    return S.FinancialRecordSet(
        record_set_version=record_set_version, source_version=source_version,
        extractor_name="excel_extractor", extractor_version=extractor_version,
        mapping_rule_version="0.1", normalization_rule_version="0.1",
        dependency_versions=dep, report_periods=["2024-12-31"],
        currency="CNY", unit="yuan", statement_scope="consolidated",
        audit_status="audited", block_count=1, record_count=record_count,
        created_at="2026-01-01T00:00:00Z",
    )


def _make_record(record_set_version, company_id="300750", row_number=5,
                 raw_value=1000.0) -> S.SourceFinancialRecord:
    locator = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="资产负债表", row_number=row_number, column_number=2,
        cell_address=f"B{row_number}", row_header="资产总计",
        column_header="2024-12-31", unit_text="元"))
    r = S.SourceFinancialRecord(
        record_id="", record_set_version=record_set_version, company_id=company_id,
        standard_item_code="TOTAL_ASSETS", statement_type="balance_sheet",
        raw_item_text="资产总计", raw_value=raw_value, raw_unit="yuan", raw_currency="CNY",
        std_value=raw_value, std_unit="yuan", std_currency="CNY",
        conversion_rule_version="1", report_period="2024-12-31", period_type="annual",
        statement_scope="consolidated", currency="CNY", restatement_version="0",
        locator=locator, mapping_mode="rule", confidence=1.0, record_hash="",
        quality_flags=[], created_at="2026-01-01T00:00:00Z",
    )
    r.record_id = S.derive_record_id(record_set_version, S.record_identity_fields(r))
    r.record_hash = validator._record_hash(r)
    return r


def _insert_snapshot_row(conn: sqlite3.Connection, snapshot_id="snap-1") -> None:
    conn.execute(
        "INSERT INTO financial_snapshot (snapshot_id, snapshot_version, company_id, as_of_date, "
        "scope, currency, purpose, source_versions, resolution_versions, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (snapshot_id, "1", "300750", "2024-12-31", "consolidated", "CNY", "review",
         "[]", "[]", "2026-01-01T00:00:00Z"),
    )


def _insert_resolution_row(conn: sqlite3.Connection, resolution_id="res-1") -> None:
    conn.execute(
        "INSERT INTO resolution_record (resolution_id, group_id, candidate_set_hash, source_hashes, "
        "comparison_key, rule_versions, accepted_record_ids, rejected_record_ids, reason_code, "
        "note, operator, confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (resolution_id, "g1", "h", "[]", "ck-x", "{}", '["rec-1"]', "[]", "AUDITED_SOURCE",
         None, "op", "2026-01-01T00:00:00Z"),
    )


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

        # 内容版本无抽取占位列 / 记录集合含抽取事实列（A1 修订 1）
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        for absent in ("currency", "statement_scope", "audit_status", "extractor_name",
                       "report_periods", "extractor_version"):
            check(absent not in ver_cols, f"financial_source_version 无占位列 {absent}")
        rs_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_record_set)")}
        for present in ("extractor_name", "report_periods", "currency", "unit",
                        "statement_scope", "audit_status"):
            check(present in rs_cols, f"financial_record_set 含抽取事实列 {present}")
        conn.close()

        # ---- register_source_atomic 原子登记（A1 修订 2）----
        r1 = store.register_source_atomic(_make_doc(), _make_version())
        check(r1.reused is False, "首次登记 reused=False")
        check(r1.subject_blocked is False, "matched 不阻断")
        check(store.get_source_document("sd-1") is not None, "登记并读取文档头")
        check(store.get_source_version(r1.version.source_version) is not None, "登记并读取内容版本")

        # 幂等复用：同 (source_document_id, file_sha256)
        r2 = store.register_source_atomic(_make_doc(), _make_version())
        check(r2.reused is True, "同内容重复登记 reused=True")
        check(r2.version.source_version == r1.version.source_version, "重复登记返回相同 source_version")

        # 新内容版本（不同 sha，同 source_document_id）
        v2 = _make_version(file_sha256="b" * 64)
        r3 = store.register_source_atomic(_make_doc(), v2)
        check(r3.reused is False, "同文档不同内容 → 新内容版本")
        check(len(store.list_source_versions("sd-1")) == 2, "同文档两个内容版本并存")

        # 主体受控合并：空检测不覆盖 matched（A1 修订 10）
        r4 = store.register_source_atomic(
            _make_doc(detected=None, subject="unverified"), _make_version(file_sha256="c" * 64))
        check(r4.document.subject_match_status == "matched",
              "空检测不覆盖 matched（不降级）")

        # 主体受控合并：matched → mismatch（有依据更正）
        r5 = store.register_source_atomic(
            _make_doc(detected="比亚迪", subject="mismatch"), _make_version(file_sha256="d" * 64))
        check(r5.document.subject_match_status == "mismatch", "matched → mismatch 允许")
        check(r5.subject_blocked is True, "mismatch 阻断")

        # mismatch 不被空检测覆盖
        r6 = store.register_source_atomic(
            _make_doc(detected=None, subject="unverified"), _make_version(file_sha256="e" * 64))
        check(r6.document.subject_match_status == "mismatch",
              "mismatch 不被空检测覆盖")

        # 跨公司复用拒绝（A1 修订 10）
        try:
            store.register_source_atomic(
                _make_doc(company_id="600000", detected=None, subject="unverified"),
                _make_version(file_sha256="f" * 64))
            check(False, "跨公司复用被拒绝")
        except validator.ValidationError:
            check(True, "跨公司借用已有 source_document_id 被拒绝")

        # source_class 静默改变拒绝
        try:
            store.register_source_atomic(
                _make_doc(source_class="credit_report", detected=None, subject="unverified"),
                _make_version(file_sha256="f" * 64))
            check(False, "source_class 改变被拒绝")
        except validator.ValidationError:
            check(True, "source_class 静默改变被拒绝")

        # declared_company_name 静默改变拒绝
        try:
            store.register_source_atomic(
                _make_doc(declared="比亚迪", detected=None, subject="unverified"),
                _make_version(file_sha256="f" * 64))
            check(False, "declared 改变被拒绝")
        except validator.ValidationError:
            check(True, "declared_company_name 静默改变被拒绝")

        # ---- 故障注入：版本插入失败不残留文档头（A1 修订 2）----
        conn = sqlite3.connect(tmp_path)
        conn.execute("CREATE TRIGGER tmp_fail_version BEFORE INSERT ON financial_source_version "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            store.register_source_atomic(_make_doc(source_document_id="sd-fail"),
                                         _make_version("sd-fail", "g" * 64))
            check(False, "版本插入失败被注入触发")
        except sqlite3.IntegrityError:
            check(True, "版本插入失败（注入）")
        conn = sqlite3.connect(tmp_path)
        conn.execute("DROP TRIGGER tmp_fail_version")
        conn.commit()
        conn.close()
        check(store.get_source_document("sd-fail") is None, "版本插入失败不残留文档头")

        # ---- commit_record_set 原子提交（A1 修订 6/7/8）----
        sv1 = r1.version.source_version
        rs = _make_record_set(sv1)
        rec = _make_record(rs.record_set_version)
        c1 = store.commit_record_set(rs, [rec], "sd-1")
        check(c1.reused is False, "首次提交 record_set reused=False")
        check(store.count_records(rs.record_set_version) == 1, "记录落库数量正确")
        check(store.get_current_record_set("sd-1").record_set_version == rs.record_set_version,
              "current_record_set 指针切换成功")

        # 严格复用：完全一致 → reused=True
        c2 = store.commit_record_set(rs, [rec], "sd-1")
        check(c2.reused is True, "完全一致重复提交 reused=True")

        # 存储冲突：同 record_set_version 但记录内容不一致（A1 修订 8）
        rec_bad = _make_record(rs.record_set_version, raw_value=9999.0)
        try:
            store.commit_record_set(rs, [rec_bad], "sd-1")
            check(False, "内容不一致被拒绝")
        except store.StorageConflictError:
            check(True, "同 record_set_version 内容不一致 → StorageConflictError")
        check(store.get_current_record_set("sd-1").record_set_version == rs.record_set_version,
              "冲突不切换 current（旧 current 保留）")

        # record_count 不一致
        rs_bad_count = _make_record_set(sv1, record_count=2)
        try:
            store.commit_record_set(rs_bad_count, [rec], "sd-1")
            check(False, "record_count 不一致被拒绝")
        except validator.ValidationError:
            check(True, "record_count 与记录数不一致被拒绝")

        # 依赖版本变化 → 两个 Record Set（A1 修订 5）
        store.register_source_atomic(_make_doc(source_document_id="sd-dep"),
                                     _make_version("sd-dep", "m" * 64))
        sv_dep = S.derive_source_version("sd-dep", "m" * 64)
        rs_a = _make_record_set(sv_dep, dependency_versions={"openpyxl": "3.1.2"})
        rs_b = _make_record_set(sv_dep, dependency_versions={"openpyxl": "3.2.0"})
        check(rs_a.record_set_version != rs_b.record_set_version,
              "依赖版本变化 → 不同 record_set_version")
        store.commit_record_set(rs_a, [_make_record(rs_a.record_set_version)], "sd-dep")
        store.commit_record_set(rs_b, [_make_record(rs_b.record_set_version)], "sd-dep")
        check(len(store.list_record_sets(sv_dep)) == 2, "同来源不同依赖 → 两个 Record Set 并存")

        # 跨文档指针拒绝（A1 修订 7）
        store.register_source_atomic(_make_doc(source_document_id="sd-2"),
                                     _make_version("sd-2", "h" * 64))
        sv2 = S.derive_source_version("sd-2", "h" * 64)
        rs_other_doc = _make_record_set(sv2)
        try:
            store.commit_record_set(rs_other_doc, [_make_record(rs_other_doc.record_set_version)],
                                    "sd-1")
            check(False, "跨文档指针被拒绝")
        except ValueError:
            check(True, "source_version 不属于目标文档 → 拒绝")

        # 跨公司指针拒绝（A1 修订 7）
        store.register_source_atomic(_make_doc(company_id="600000", source_document_id="sd-600"),
                                     _make_version("sd-600", "i" * 64))
        sv600 = S.derive_source_version("sd-600", "i" * 64)
        rs_other_co = _make_record_set(sv600)
        try:
            store.commit_record_set(rs_other_co, [_make_record(rs_other_co.record_set_version)],
                                    "sd-1")
            check(False, "跨公司指针被拒绝")
        except ValueError:
            check(True, "跨公司 source_version → 拒绝")

        # 隔离拒绝（A1 修订 7）：source_version 被隔离 → 不得 commit
        store.register_source_atomic(_make_doc(source_document_id="sd-q"),
                                     _make_version("sd-q", "j" * 64))
        svq = S.derive_source_version("sd-q", "j" * 64)
        store.quarantine("financial_source_version", svq, "完整性损坏")
        rs_q = _make_record_set(svq)
        try:
            store.commit_record_set(rs_q, [_make_record(rs_q.record_set_version)], "sd-q")
            check(False, "隔离 source_version 不得 commit")
        except ValueError:
            check(True, "被隔离 source_version 不得提交 record_set")

        # 隔离 record_set 不得设为 current（直接验证归属校验）
        conn = store._get_conn()
        try:
            store.quarantine("financial_record_set", rs.record_set_version, "损坏")
            try:
                store._set_current_record_set_conn(conn, "sd-1", rs.record_set_version)
                check(False, "隔离 record_set 不得设为 current")
            except ValueError:
                check(True, "被隔离 record_set 不得设为 current")
        finally:
            conn.close()

        # ---- 故障注入：记录插入失败不残留 record_set / 不切 current（A1 修订 6）----
        store.register_source_atomic(_make_doc(source_document_id="sd-fail2"),
                                     _make_version("sd-fail2", "k" * 64))
        sv_fail2 = S.derive_source_version("sd-fail2", "k" * 64)
        rs_fail = _make_record_set(sv_fail2)
        rec_fail = _make_record(rs_fail.record_set_version)
        conn = sqlite3.connect(tmp_path)
        conn.execute("CREATE TRIGGER tmp_fail_rec BEFORE INSERT ON source_financial_record "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            store.commit_record_set(rs_fail, [rec_fail], "sd-fail2")
            check(False, "记录插入失败被注入触发")
        except sqlite3.IntegrityError:
            check(True, "记录插入失败（注入）")
        conn = sqlite3.connect(tmp_path)
        conn.execute("DROP TRIGGER tmp_fail_rec")
        conn.commit()
        conn.close()
        check(store.get_record_set(rs_fail.record_set_version) is None, "记录插入失败不残留 record_set")
        check(store.get_current_record_set("sd-fail2") is None, "记录插入失败不切 current")

        # ---- 不可变触发器（原始 SQL）----
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("UPDATE financial_source_version SET file_size=999 WHERE source_version=?",
                         (sv1,))
            conn.commit()
            check(False, "内容版本 UPDATE 被触发器阻断")
        except sqlite3.IntegrityError:
            check(True, "内容版本 UPDATE 被触发器阻断")
        try:
            conn.execute("DELETE FROM financial_source_version WHERE source_version=?", (sv1,))
            conn.commit()
            check(False, "内容版本 DELETE 被触发器阻断")
        except sqlite3.IntegrityError:
            check(True, "内容版本 DELETE 被触发器阻断")
        conn.close()

        # ---- validity 状态机（A1 修订 9）----
        conn = sqlite3.connect(tmp_path)
        _insert_snapshot_row(conn, "snap-1")
        _insert_snapshot_row(conn, "snap-2")
        _insert_resolution_row(conn, "res-1")
        conn.commit()
        conn.close()

        check(store.latest_snapshot_validity("snap-1") is None, "初始无快照有效性事件")
        check(store.record_snapshot_validity("snap-1", "valid") is not None, "首条 valid 生效")
        check(store.latest_snapshot_validity("snap-1") == "valid", "latest_snapshot_validity 读 valid")
        check(store.record_snapshot_validity("snap-1", "valid") is None, "同状态重复事件幂等")
        check(store.record_snapshot_validity("snap-1", "stale", invalidated_by="snap-2") is not None,
              "valid → stale 生效")
        check(store.latest_snapshot_validity("snap-1") == "stale", "latest 读 stale")
        try:
            store.record_snapshot_validity("snap-1", "valid")
            check(False, "非法倒退 stale→valid 被拒绝")
        except validator.ValidationError:
            check(True, "非法倒退 stale → valid 被拒绝")
        store.record_snapshot_validity("snap-1", "superseded")
        check(store.latest_snapshot_validity("snap-1") == "superseded", "stale → superseded")
        try:
            store.record_snapshot_validity("snap-2", "stale")
            check(False, "首条非 valid 被拒绝")
        except validator.ValidationError:
            check(True, "快照有效性首条事件必须为 valid")

        check(store.record_resolution_validity("res-1", "active") is not None, "决议首条 active 生效")
        check(store.latest_resolution_validity("res-1") == "active", "latest_resolution_validity 读 active")
        store.record_resolution_validity("res-1", "stale")
        check(store.latest_resolution_validity("res-1") == "stale", "active → stale")
        try:
            store.record_resolution_validity("res-1", "active")
            check(False, "非法倒退 stale→active 被拒绝")
        except validator.ValidationError:
            check(True, "非法倒退 stale → active 被拒绝")

        # ---- progress 事件 ----
        ev = S.ProgressEvent(event_id="evt-1", run_id="run-1", stage_id="EXTRACTION",
                             status="failed", message_code="解析失败", completed_units=None,
                             total_units=None, error_code="PARSE_FAILED", recoverable=False,
                             created_at="2026-01-01T00:00:00Z")
        store.record_progress(ev)
        check(store.latest_progress("run-1").event_id == "evt-1", "progress 事件落盘并可读")

        # ---- 跨公司隔离 ----
        store.register_source_atomic(_make_doc(company_id="600000", source_document_id="sd-2b"),
                                     _make_version("sd-2b", "l" * 64))
        check(store.count_source_documents("300750") >= 1, "跨公司查询隔离（company A 不返回 B 独立文档）")
        check(all(d.company_id == "300750" for d in store.list_source_documents("300750")),
              "list 按公司过滤")

        # ====================================================================
        # 定点修复 2：commit_record_set 逐记录公司归属校验（全回滚）
        # ====================================================================
        store.register_source_atomic(_make_doc(source_document_id="sd-mix"),
                                     _make_version("sd-mix", "n" * 64))
        sv_mix = S.derive_source_version("sd-mix", "n" * 64)
        rs_mix = _make_record_set(sv_mix, record_count=2)
        rec_ok = _make_record(rs_mix.record_set_version, company_id="300750")
        rec_wrong_co = _make_record(rs_mix.record_set_version, company_id="600000", row_number=6)
        try:
            store.commit_record_set(rs_mix, [rec_ok, rec_wrong_co], "sd-mix")
            check(False, "混合公司记录被拒绝")
        except validator.ValidationError:
            check(True, "逐记录公司归属校验拒绝混合公司记录（record.company_id != 权威公司）")
        check(store.get_record_set(rs_mix.record_set_version) is None, "混合公司记录不残留 record_set")
        check(store.count_records(rs_mix.record_set_version) == 0, "混合公司记录不残留 records")
        check(store.get_current_record_set("sd-mix") is None, "混合公司记录不切 current")

        # ====================================================================
        # 定点修复 3：复用路径隔离 + 完整性校验
        # ====================================================================
        # (3a) source_version 被隔离 → 复用路径拒绝
        store.register_source_atomic(_make_doc(source_document_id="sd-rq1"),
                                     _make_version("sd-rq1", "o" * 64))
        sv_rq1 = S.derive_source_version("sd-rq1", "o" * 64)
        rs_rq1 = _make_record_set(sv_rq1)
        rec_rq1 = _make_record(rs_rq1.record_set_version)
        store.commit_record_set(rs_rq1, [rec_rq1], "sd-rq1")
        store.quarantine("financial_source_version", sv_rq1, "完整性损坏")
        try:
            store.commit_record_set(rs_rq1, [rec_rq1], "sd-rq1")
            check(False, "复用路径 source_version 隔离被拒绝")
        except ValueError:
            check(True, "复用路径 source_version 被隔离 → 不复用/不切 current")

        # (3b) record_set 被隔离 → 复用路径拒绝
        store.register_source_atomic(_make_doc(source_document_id="sd-rq2"),
                                     _make_version("sd-rq2", "p" * 64))
        sv_rq2 = S.derive_source_version("sd-rq2", "p" * 64)
        rs_rq2 = _make_record_set(sv_rq2)
        rec_rq2 = _make_record(rs_rq2.record_set_version)
        store.commit_record_set(rs_rq2, [rec_rq2], "sd-rq2")
        store.quarantine("financial_record_set", rs_rq2.record_set_version, "损坏")
        try:
            store.commit_record_set(rs_rq2, [rec_rq2], "sd-rq2")
            check(False, "复用路径 record_set 隔离被拒绝")
        except ValueError:
            check(True, "复用路径 record_set 被隔离 → 不复用/不切 current")

        # (3c) record_set 含被隔离子记录 → 复用路径拒绝
        store.register_source_atomic(_make_doc(source_document_id="sd-rq3"),
                                     _make_version("sd-rq3", "q" * 64))
        sv_rq3 = S.derive_source_version("sd-rq3", "q" * 64)
        rs_rq3 = _make_record_set(sv_rq3)
        rec_rq3 = _make_record(rs_rq3.record_set_version)
        store.commit_record_set(rs_rq3, [rec_rq3], "sd-rq3")
        store.quarantine("source_financial_record", rec_rq3.record_id, "记录损坏")
        try:
            store.commit_record_set(rs_rq3, [rec_rq3], "sd-rq3")
            check(False, "复用路径含被隔离子记录被拒绝")
        except ValueError:
            check(True, "复用路径含被隔离子记录 → 不复用/不切 current")

        # (3d) 完整性损坏 → 记录 quarantine 并报 StorageCorruptionError
        store.register_source_atomic(_make_doc(source_document_id="sd-rq4"),
                                     _make_version("sd-rq4", "r" * 64))
        sv_rq4 = S.derive_source_version("sd-rq4", "r" * 64)
        rs_rq4 = _make_record_set(sv_rq4)
        rec_rq4 = _make_record(rs_rq4.record_set_version)
        store.commit_record_set(rs_rq4, [rec_rq4], "sd-rq4")
        conn = sqlite3.connect(tmp_path)
        conn.execute("DROP TRIGGER trg_source_financial_record_no_update")
        conn.execute("UPDATE source_financial_record SET record_hash='corrupted' WHERE record_id=?",
                     (rec_rq4.record_id,))
        for sql in store._immutable_trigger_sqls("source_financial_record"):
            conn.execute(sql)
        conn.commit()
        conn.close()
        try:
            store.commit_record_set(rs_rq4, [rec_rq4], "sd-rq4")
            check(False, "完整性损坏被拒绝")
        except store.StorageCorruptionError:
            check(True, "复用前完整性校验失败 → StorageCorruptionError")
        check(store.is_quarantined("financial_record_set", rs_rq4.record_set_version),
              "损坏 record_set 被记录 quarantine")
        check(store.get_current_record_set("sd-rq4").record_set_version == rs_rq4.record_set_version,
              "损坏不切换 current（旧 current 保留）")

        # ====================================================================
        # 定点修复 4：复用路径 current 指针处理（同事务原子）
        # ====================================================================
        # (4a) 已存在且已 current → reused=True + current_switched=False
        store.register_source_atomic(_make_doc(source_document_id="sd-c1"),
                                     _make_version("sd-c1", "s" * 64))
        sv_c1 = S.derive_source_version("sd-c1", "s" * 64)
        rs_c1 = _make_record_set(sv_c1)
        rec_c1 = _make_record(rs_c1.record_set_version)
        store.commit_record_set(rs_c1, [rec_c1], "sd-c1")
        c_again = store.commit_record_set(rs_c1, [rec_c1], "sd-c1")
        check(c_again.reused is True and c_again.current_switched is False,
              "已 current → reused=True 且 current_switched=False")
        check(store.get_current_record_set("sd-c1").record_set_version == rs_c1.record_set_version,
              "已 current 后指针不变")

        # (4b) 已存在但 current 缺失 → 复用并原子补切 current
        store.register_source_atomic(_make_doc(source_document_id="sd-c2"),
                                     _make_version("sd-c2", "t" * 64))
        sv_c2 = S.derive_source_version("sd-c2", "t" * 64)
        rs_c2 = _make_record_set(sv_c2)
        rec_c2 = _make_record(rs_c2.record_set_version)
        store.commit_record_set(rs_c2, [rec_c2], "sd-c2")
        conn = sqlite3.connect(tmp_path)
        conn.execute("DELETE FROM current_record_set WHERE source_document_id='sd-c2'")
        conn.commit()
        conn.close()
        c_missing = store.commit_record_set(rs_c2, [rec_c2], "sd-c2")
        check(c_missing.reused is True and c_missing.current_switched is True,
              "current 缺失 → 复用并补切 current（current_switched=True）")
        check(store.get_current_record_set("sd-c2").record_set_version == rs_c2.record_set_version,
              "current 已补切到本记录集")

        # (4c) 已存在但 current 指向其他版本 → 复用并切换
        store.register_source_atomic(_make_doc(source_document_id="sd-c3"),
                                     _make_version("sd-c3", "u" * 64))
        sv_c3 = S.derive_source_version("sd-c3", "u" * 64)
        rs_old = _make_record_set(sv_c3, dependency_versions={"openpyxl": "3.1.2"})
        rs_new = _make_record_set(sv_c3, dependency_versions={"openpyxl": "3.2.0"})
        store.commit_record_set(rs_old, [_make_record(rs_old.record_set_version)], "sd-c3")
        store.commit_record_set(rs_new, [_make_record(rs_new.record_set_version)], "sd-c3")
        check(store.get_current_record_set("sd-c3").record_set_version == rs_new.record_set_version,
              "current 初始指向新版")
        c_back = store.commit_record_set(rs_old, [_make_record(rs_old.record_set_version)], "sd-c3")
        check(c_back.reused is True and c_back.current_switched is True,
              "current 指向其他版本 → 复用并切换（current_switched=True）")
        check(store.get_current_record_set("sd-c3").record_set_version == rs_old.record_set_version,
              "current 切回本记录集")

        # (4d) 复用切换故障 → 旧 current 保留
        conn = sqlite3.connect(tmp_path)
        conn.execute("CREATE TRIGGER tmp_fail_current BEFORE UPDATE ON current_record_set "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            store.commit_record_set(rs_new, [_make_record(rs_new.record_set_version)], "sd-c3")
            check(False, "current 切换故障被注入")
        except sqlite3.IntegrityError:
            check(True, "复用切换 current 故障被注入")
        conn = sqlite3.connect(tmp_path)
        conn.execute("DROP TRIGGER tmp_fail_current")
        conn.commit()
        conn.close()
        check(store.get_current_record_set("sd-c3").record_set_version == rs_old.record_set_version,
              "复用切换故障后旧 current 保留")

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
