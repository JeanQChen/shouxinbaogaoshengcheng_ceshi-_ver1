"""Eval: financial_v2 store 追加式迁移（A1 定点修复 1）。

用法: python -m evals.test_financial_v2_migration

覆盖：
- 全新库一次到位（v2 schema + schema_migrations 记录全部版本）；
- 从真实旧 v1 DDL 升级到 v2：v1 数据（文档头/内容版本文件事实/记录集合关键字段）
  迁移后可读；v2 新列 + 唯一键 + 触发器就位；v1 占位列移除；
- 幂等重跑：第二次 init 不再迁移、数据不变；
- 故障注入：迁移事务失败完整回滚，保留原 v1 库（结构 + 数据 + schema_migrations）；
- 一致性：SCHEMA_VERSION == 最新 migration == 实际结构；
- 结构 / migration 记录不一致 → 失败关闭（不假装成功）。
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


_V1_DOC = (
    "sd-v1", "300750", "BS.xlsx", "financial_statement",
    "宁德时代", "宁德时代", "matched", "2026-01-01T00:00:00Z",
)
_V1_VERSION = (
    "sv-v1", "sd-v1", "a" * 64, "xlsx", 1024, None, None,
    '["2024-12-31"]', "CNY", "consolidated", "audited", "excel_extractor",
    "0.1", "0.1", "0.1", "[]", "2026-01-01T00:00:00Z",
)
_V1_RECORD_SET = (
    "rs-v1", "sv-v1", "0.1", "0.1", "0.1", "{}", 1, 1, "2026-01-01T00:00:00Z",
)

# v1 真实来源子记录（引用 rs-v1），用于验证迁移后子记录存活 + FK 完整。
_V1_RECORD = (
    "rec-v1", "rs-v1", "300750", "TOTAL_ASSETS", "balance_sheet",
    "资产总计", 1000.0, "yuan", "CNY", 1000.0, "yuan", "CNY",
    "1", "2024-12-31", "annual", "consolidated", "CNY", "0",
    None, "rule", 1.0, "h-v1", "[]", "2026-01-01T00:00:00Z",
)


def _build_v1_db(path: str) -> None:
    """构造一个「真实旧 v1 库」：冻结 v1 DDL + schema_migrations=['1'] + 一行 v1 数据。"""
    conn = sqlite3.connect(path)
    conn.executescript(store._build_ddl_v1())
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES ('1', '2026-01-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO financial_source_document (source_document_id, company_id, source_name, "
        "source_class, declared_company_name, detected_company_name, subject_match_status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)", _V1_DOC)
    conn.execute(
        "INSERT INTO financial_source_version (source_version, source_document_id, file_sha256, "
        "file_type, file_size, document_id, document_version, report_periods, currency, "
        "statement_scope, audit_status, extractor_name, extractor_version, mapping_rule_version, "
        "normalization_rule_version, quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _V1_VERSION)
    conn.execute(
        "INSERT INTO financial_record_set (record_set_version, source_version, extractor_version, "
        "mapping_rule_version, normalization_rule_version, dependency_versions, block_count, "
        "record_count, created_at) VALUES (?,?,?,?,?,?,?,?,?)", _V1_RECORD_SET)
    conn.execute(
        "INSERT INTO source_financial_record (record_id, record_set_version, company_id, "
        "standard_item_code, statement_type, raw_item_text, raw_value, raw_unit, raw_currency, "
        "std_value, std_unit, std_currency, conversion_rule_version, report_period, period_type, "
        "statement_scope, currency, restatement_version, locator, mapping_mode, confidence, "
        "record_hash, quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _V1_RECORD)
    conn.commit()
    conn.close()


def _applied_versions(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY rowid")]


def _build_v2_db(path: str) -> None:
    """构造一个「真实旧 v2 库」：冻结 v2 DDL + schema_migrations=['1','2']（无 v3 表/列）。"""
    conn = sqlite3.connect(path)
    conn.executescript(store._build_ddl_v2())
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES ('1', '2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES ('2', '2026-01-01T00:00:00Z')")
    conn.commit()
    conn.close()


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

    def tmp_db():
        fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_mig_")
        os.close(fd)
        return p

    def cleanup(p):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(p + suffix)
            except FileNotFoundError:
                pass

    # ---- 全新库一次到位 ----
    p1 = tmp_db()
    try:
        store.init_db(p1)
        check(store.applied_schema_version() == "4", "全新库 schema_migrations 最新版本 == '4'")
        check(S.SCHEMA_VERSION == "4" == store.applied_schema_version(),
              "SCHEMA_VERSION == 最新 migration 版本 == 实际应用版本")
        conn = sqlite3.connect(p1)
        rs_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_record_set)")}
        for col in ("extractor_name", "report_periods", "currency", "unit",
                    "statement_scope", "audit_status"):
            check(col in rs_cols, f"全新库 financial_record_set 含 v2 列 {col}")
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        for col in ("currency", "report_periods", "extractor_name", "quality_flags"):
            check(col not in ver_cols, f"全新库 financial_source_version 无 v1 占位列 {col}")
        # v3 结构探针：record 溯源列 + 新增表。
        rec_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_financial_record)")}
        check("candidate_id" in rec_cols, "全新库 source_financial_record 含 v3 列 candidate_id")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("extracted_financial_cell", "mapping_rule", "extraction_issue",
                  "mapping_resolution", "mapping_resolution_validity", "mapping_resolution_head",
                  "reconciliation_run", "reconciliation_group_result", "reconciliation_check",
                  "current_reconciliation"):
            check(t in tables, f"全新库含 v3 表 {t}")
        # 不可变候选层触发器就位。
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for t in ("extracted_financial_cell", "extraction_issue", "mapping_resolution",
                  "reconciliation_run", "reconciliation_group_result", "reconciliation_check"):
            check(f"trg_{t}_no_update" in triggers and f"trg_{t}_no_delete" in triggers,
                  f"全新库 {t} 不可变触发器就位")
        conn.close()
    finally:
        cleanup(p1)

    # ---- 真实旧 v1 DDL → v2 ----
    p2 = tmp_db()
    try:
        _build_v1_db(p2)
        store.init_db(p2)

        conn = sqlite3.connect(p2)
        check(_applied_versions(conn) == ["1", "2", "3", "4"], "迁移后 schema_migrations == ['1','2','3','4']")

        # v1 数据迁移后可读（文档头 + 内容版本文件事实 + 记录集合关键字段）。
        doc = store.get_source_document("sd-v1")
        check(doc is not None and doc.company_id == "300750" and doc.source_name == "BS.xlsx",
              "v1 文档头迁移后可读")
        ver = store.get_source_version("sv-v1")
        check(ver is not None and ver.file_sha256 == "a" * 64 and ver.file_type == "xlsx"
              and ver.file_size == 1024 and ver.document_id is None,
              "v1 内容版本文件事实迁移后保留")
        rs = store.get_record_set("rs-v1")
        check(rs is not None and rs.source_version == "sv-v1"
              and rs.extractor_version == "0.1" and rs.record_count == 1,
              "v1 记录集合关键字段迁移后保留")
        check(rs is not None and rs.report_periods == [] and rs.currency is None
              and rs.extractor_name is None, "v2 新列以空/None 补齐（不猜测）")

        # v2 新列 + 唯一键 + 触发器就位。
        rs_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_record_set)")}
        check("extractor_name" in rs_cols and "report_periods" in rs_cols,
              "迁移后 record_set 含 v2 新列")
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        check("currency" not in ver_cols and "report_periods" not in ver_cols,
              "迁移后 source_version 移除 v1 占位列")
        uniq_ver = {r[1] for r in conn.execute("PRAGMA index_list(financial_source_version)") if r[2] == 1}
        check(len(uniq_ver) == 2, "迁移后 source_version 保留主键 + 复合唯一键 (source_document_id, file_sha256)")
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        check("trg_financial_source_version_no_update" in triggers
              and "trg_financial_record_set_no_delete" in triggers,
              "迁移后不可变触发器重建就位")
        conn.close()

        # 幂等重跑：第二次 init 不重迁移、数据不变。
        store.init_db(p2)
        conn = sqlite3.connect(p2)
        check(_applied_versions(conn) == ["1", "2", "3", "4"], "第二次 init 不追加迁移记录")
        check(store.get_source_version("sv-v1").file_sha256 == "a" * 64, "第二次 init 数据不变")
        conn.close()
    finally:
        cleanup(p2)

    # ---- 故障注入：迁移事务失败 → 完整回滚，保留原 v1 库 ----
    p3 = tmp_db()
    try:
        _build_v1_db(p3)
        conn = sqlite3.connect(p3)
        conn.execute("CREATE TRIGGER trg_fail_mig BEFORE INSERT ON schema_migrations "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            store.init_db(p3)
            check(False, "迁移失败被注入触发")
        except (sqlite3.IntegrityError, RuntimeError):
            check(True, "迁移失败被注入触发")
        conn = sqlite3.connect(p3)
        conn.execute("DROP TRIGGER trg_fail_mig")
        conn.commit()
        # 回滚后原 v1 库完整：结构仍 v1 + 数据保留 + schema_migrations 仍 ['1']。
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        check("currency" in ver_cols, "回滚后 source_version 仍为 v1 结构（占位列还在）")
        check(conn.execute("SELECT COUNT(*) FROM financial_source_version").fetchone()[0] == 1,
              "回滚后 v1 数据保留")
        check(_applied_versions(conn) == ["1"], "回滚后 schema_migrations 仍为 ['1']")
        conn.close()
        # 故障清除后可正常迁移。
        store.init_db(p3)
        check(store.applied_schema_version() == "4", "故障清除后重跑迁移成功")
    finally:
        cleanup(p3)

    # ---- 结构 / migration 记录不一致 → 失败关闭 ----
    p4 = tmp_db()
    try:
        store.init_db(p4)
        conn = sqlite3.connect(p4)
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES ('99', '2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()
        try:
            store.init_db(p4)
            check(False, "migration 记录序列不合法被拒绝")
        except RuntimeError:
            check(True, "migration 记录序列不合法 → 失败关闭")
    finally:
        cleanup(p4)

    # ---- 结构缺 v2 列但记录声称 v2 → 失败关闭 ----
    p5 = tmp_db()
    try:
        store.init_db(p5)
        conn = sqlite3.connect(p5)
        # 用老结构覆盖 record_set（无 v2 列），但保留 schema_migrations='2'，模拟结构被篡改。
        conn.execute("DROP TABLE financial_record_set")
        conn.execute("CREATE TABLE financial_record_set (record_set_version TEXT PRIMARY KEY, source_version TEXT)")
        conn.commit()
        conn.close()
        try:
            store.init_db(p5)
            check(False, "结构缺 v2 列被拒绝")
        except RuntimeError:
            check(True, "结构缺 v2 列但记录声称 v2 → 失败关闭")
    finally:
        cleanup(p5)

    # ---- v1 真实子记录迁移存活 + foreign_key_check 为空 ----
    p6 = tmp_db()
    try:
        _build_v1_db(p6)
        store.init_db(p6)
        conn = sqlite3.connect(p6)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM source_financial_record WHERE record_id='rec-v1'").fetchone()[0]
        check(cnt == 1, "迁移后 source_financial_record 子记录仍存在")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        check(len(fk) == 0, "迁移后 foreign_key_check 为空")
        conn.close()
    finally:
        cleanup(p6)

    # ---- 注入孤立外键 → 迁移失败并完整回滚到 v1；修复后可迁移 ----
    p7 = tmp_db()
    try:
        _build_v1_db(p7)
        conn = sqlite3.connect(p7)
        conn.execute(
            "INSERT INTO source_financial_record (record_id, record_set_version, company_id, "
            "standard_item_code, statement_type, raw_item_text, raw_value, raw_unit, raw_currency, "
            "std_value, std_unit, std_currency, conversion_rule_version, report_period, period_type, "
            "statement_scope, currency, restatement_version, locator, mapping_mode, confidence, "
            "record_hash, quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("rec-orphan", "rs-orphan", "300750", "TOTAL_ASSETS", "balance_sheet",
             "资产总计", 1.0, "yuan", "CNY", 1.0, "yuan", "CNY", "1", "2024-12-31",
             "annual", "consolidated", "CNY", "0", None, "rule", 1.0, "h-orphan", "[]",
             "2026-01-01T00:00:00Z"))
        conn.commit()
        conn.close()
        try:
            store.init_db(p7)
            check(False, "孤立外键 → 迁移失败")
        except RuntimeError:
            check(True, "孤立外键 → 迁移失败（foreign_key_check 在 COMMIT 前拦截）")
        conn = sqlite3.connect(p7)
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        check("currency" in ver_cols, "回滚后仍 v1 结构（占位列还在）")
        check(_applied_versions(conn) == ["1"], "回滚后 migration 记录仍只有 ['1']")
        check(conn.execute(
            "SELECT COUNT(*) FROM source_financial_record WHERE record_id='rec-v1'").fetchone()[0] == 1,
              "回滚后旧子记录保留")
        # 修复故障：临时去掉不可变 DELETE 触发器，删除孤儿行，再恢复触发器。
        conn.execute("DROP TRIGGER trg_source_financial_record_no_delete")
        conn.execute("DELETE FROM source_financial_record WHERE record_set_version='rs-orphan'")
        for sql in store._immutable_trigger_sqls("source_financial_record"):
            conn.execute(sql)
        conn.commit()
        conn.close()
        store.init_db(p7)
        check(store.applied_schema_version() == "4", "修复故障后正常迁移到 v4")
        conn = sqlite3.connect(p7)
        check(len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 0,
              "修复后迁移 foreign_key_check 为空")
        conn.close()
    finally:
        cleanup(p7)

    # ---- 真实旧 v2 DDL → v3（追加表 + candidate_id 列）----
    p8 = tmp_db()
    try:
        _build_v2_db(p8)
        conn = sqlite3.connect(p8)
        rec_cols_v2 = {r[1] for r in conn.execute("PRAGMA table_info(source_financial_record)")}
        check("candidate_id" not in rec_cols_v2, "v2 库 source_financial_record 无 candidate_id 列（迁移前）")
        conn.close()

        store.init_db(p8)
        conn = sqlite3.connect(p8)
        check(_applied_versions(conn) == ["1", "2", "3", "4"], "v2→v4 迁移后 schema_migrations == ['1','2','3','4']")
        rec_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_financial_record)")}
        check("candidate_id" in rec_cols, "v2→v4 后 source_financial_record 追加 candidate_id 列")
        check("raw_value_text" in rec_cols and "std_value_text" in rec_cols,
              "v2→v4 后 source_financial_record 追加权威十进制文本列")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("extracted_financial_cell", "extraction_issue", "mapping_rule",
                  "mapping_resolution", "reconciliation_run", "reconciliation_group_result",
                  "reconciliation_check", "current_reconciliation"):
            check(t in tables, f"v2→v3 后含新表 {t}")
        check(len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 0,
              "v2→v3 迁移后 foreign_key_check 为空")
        conn.close()

        # 幂等重跑。
        store.init_db(p8)
        conn = sqlite3.connect(p8)
        check(_applied_versions(conn) == ["1", "2", "3", "4"], "v2→v4 第二次 init 不追加迁移记录")
        conn.close()
    finally:
        cleanup(p8)

    # ---- v2→v3 迁移失败完整回滚（保留原 v2 库）----
    p9 = tmp_db()
    try:
        _build_v2_db(p9)
        conn = sqlite3.connect(p9)
        conn.execute("CREATE TRIGGER trg_fail_v3 BEFORE INSERT ON schema_migrations "
                     "WHEN NEW.version='3' BEGIN SELECT RAISE(ABORT, 'injected v3'); END;")
        conn.commit()
        conn.close()
        try:
            store.init_db(p9)
            check(False, "v2→v3 迁移失败被注入触发")
        except (sqlite3.IntegrityError, RuntimeError):
            check(True, "v2→v3 迁移失败被注入触发")
        conn = sqlite3.connect(p9)
        conn.execute("DROP TRIGGER trg_fail_v3")
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("extracted_financial_cell" not in tables, "回滚后无 v3 新表")
        check(_applied_versions(conn) == ["1", "2"], "回滚后 schema_migrations 仍为 ['1','2']")
        conn.close()
        store.init_db(p9)
        check(store.applied_schema_version() == "4", "故障清除后重跑迁移到 v4 成功")
    finally:
        cleanup(p9)

    # ---- 合成测试：版本顺序不依赖字符串大小（"9" vs "10"）----
    orig_migrations = store.MIGRATIONS
    try:
        store.MIGRATIONS = [("9", None), ("10", None)]
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_migrations VALUES ('9','x')")
        conn.execute("INSERT INTO schema_migrations VALUES ('10','x')")
        check(store._latest_applied_version(conn) == "10",
              "版本 '9'/'10' 都已应用 → 最新为 '10'（声明顺序，非字符串 MAX 的 '9'）")
        conn.close()

        conn2 = sqlite3.connect(":memory:")
        conn2.row_factory = sqlite3.Row
        conn2.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn2.execute("INSERT INTO schema_migrations VALUES ('9','x')")
        check(store._latest_applied_version(conn2) == "9", "仅应用 '9' 时最新为 '9'")
        conn2.close()

        conn3 = sqlite3.connect(":memory:")
        conn3.row_factory = sqlite3.Row
        conn3.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn3.execute("INSERT INTO schema_migrations VALUES ('10','x')")
        try:
            store._latest_applied_version(conn3)
            check(False, "非前缀（缺 '9'）被拒绝")
        except RuntimeError:
            check(True, "非前缀应用序列（缺 '9'）→ 失败关闭")
        conn3.close()
    finally:
        store.MIGRATIONS = orig_migrations

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
