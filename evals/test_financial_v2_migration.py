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
    conn.commit()
    conn.close()


def _applied_versions(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY rowid")]


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
        check(store.applied_schema_version() == "2", "全新库 schema_migrations 最新版本 == '2'")
        check(S.SCHEMA_VERSION == "2" == store.applied_schema_version(),
              "SCHEMA_VERSION == 最新 migration 版本 == 实际应用版本")
        conn = sqlite3.connect(p1)
        rs_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_record_set)")}
        for col in ("extractor_name", "report_periods", "currency", "unit",
                    "statement_scope", "audit_status"):
            check(col in rs_cols, f"全新库 financial_record_set 含 v2 列 {col}")
        ver_cols = {r[1] for r in conn.execute("PRAGMA table_info(financial_source_version)")}
        for col in ("currency", "report_periods", "extractor_name", "quality_flags"):
            check(col not in ver_cols, f"全新库 financial_source_version 无 v1 占位列 {col}")
        conn.close()
    finally:
        cleanup(p1)

    # ---- 真实旧 v1 DDL → v2 ----
    p2 = tmp_db()
    try:
        _build_v1_db(p2)
        store.init_db(p2)

        conn = sqlite3.connect(p2)
        check(_applied_versions(conn) == ["1", "2"], "迁移后 schema_migrations == ['1','2']")

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
        check(_applied_versions(conn) == ["1", "2"], "第二次 init 不追加迁移记录")
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
        check(store.applied_schema_version() == "2", "故障清除后重跑迁移成功")
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

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
