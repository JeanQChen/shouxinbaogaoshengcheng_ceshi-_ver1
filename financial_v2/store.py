"""financial_v2 Store：SQLite 权威存储 + 迁移 + 事务 + 不可变触发器 + 查询。

- 独立库 data/financial_v2.db，与 V1 data/credit.db、Phase 1 data/evidence.db 分离。
- 不可变历史事实表（financial_source_version / financial_record_set /
  source_financial_record / resolution_record / financial_snapshot / snapshot_item）
  由 BEFORE UPDATE/DELETE 触发器保护，不依赖代码约定（v3 修订 6/12）。
- building/failed 运行态写入 progress_events（复用 Phase 1 字段语义），不落到
  历史事实表的状态列（v3 修订 3）；finalized 对象在完整事务成功后直接提交，
  再原子切换 current 指针。
- 重复写入禁用 INSERT OR IGNORE：先读后严格比对，冲突显式报错回滚（任务书 §7）。
- 存储损坏隔离到 quarantine 表，不修改被保护历史行（v3 修订 13）。
- 连接模式与 evidence/store.py 一致：per-call connect/close，PRAGMA foreign_keys=ON。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from financial_v2 import schema as S
from financial_v2 import validator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/financial_v2.db")

# 模块级，由 init_db() 设置，后续操作复用本路径。
_db_path: Path | None = None

# 迁移列表（追加式；已应用版本记录在 schema_migrations 表）。A2～A6 在各自阶段
# 追加新迁移条目（如 ("2", _ddl_v2)），历史迁移不删除、不重写。
MIGRATIONS: list[tuple[str, str]] = [
    ("1", None),  # 占位，DDL 由 build_ddl() 生成
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("financial_v2 DB not initialized. Call init_db() first.")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化 financial_v2 SQLite 数据库，应用全部未执行迁移。"""
    global _db_path
    _db_path = Path(db_path)
    conn = _get_conn()
    try:
        conn.executescript(build_ddl())
        # 记录已应用的迁移版本（幂等）。
        now = _utcnow()
        for version, _ in MIGRATIONS:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?,?)",
                (version, now),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def _immutable_triggers(table: str) -> str:
    """为不可变历史事实表生成 UPDATE/DELETE 阻断触发器。"""
    return f"""
CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update
    BEFORE UPDATE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is immutable (UPDATE forbidden)');
END;
CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
    BEFORE DELETE ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is immutable (DELETE forbidden)');
END;
"""


def build_ddl() -> str:
    """返回全部建表语句（幂等）。"""
    return _build_ddl_v1()


def _build_ddl_v1() -> str:
    return """
-- ---------------------------------------------------------------------------
-- 来源登记（业务文档头，可更新 subject_match_status）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_source_document (
    source_document_id     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL,
    source_name            TEXT NOT NULL,
    source_class           TEXT NOT NULL,
    declared_company_name  TEXT,
    detected_company_name  TEXT,
    subject_match_status   TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    UNIQUE (company_id, source_document_id)
);
CREATE INDEX IF NOT EXISTS idx_src_doc_company ON financial_source_document(company_id);

-- ---------------------------------------------------------------------------
-- 内容版本（不可变历史事实）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_source_version (
    source_version            TEXT PRIMARY KEY,
    source_document_id        TEXT NOT NULL REFERENCES financial_source_document(source_document_id),
    file_sha256               TEXT NOT NULL,
    file_type                 TEXT NOT NULL,
    file_size                 INTEGER NOT NULL,
    document_id               TEXT,
    document_version          TEXT,
    report_periods            TEXT NOT NULL,
    currency                  TEXT NOT NULL,
    statement_scope           TEXT NOT NULL,
    audit_status              TEXT NOT NULL,
    extractor_name            TEXT NOT NULL,
    extractor_version         TEXT NOT NULL,
    mapping_rule_version      TEXT NOT NULL,
    normalization_rule_version TEXT NOT NULL,
    quality_flags             TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    UNIQUE (source_document_id, file_sha256)
);
CREATE INDEX IF NOT EXISTS idx_src_ver_doc ON financial_source_version(source_document_id);
""" + _immutable_triggers("financial_source_version") + """

-- ---------------------------------------------------------------------------
-- 记录集合（不可变历史事实；无 building/ready 状态列）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_record_set (
    record_set_version         TEXT PRIMARY KEY,
    source_version             TEXT NOT NULL REFERENCES financial_source_version(source_version),
    extractor_version          TEXT NOT NULL,
    mapping_rule_version       TEXT NOT NULL,
    normalization_rule_version TEXT NOT NULL,
    dependency_versions        TEXT NOT NULL,
    block_count                INTEGER NOT NULL,
    record_count               INTEGER NOT NULL,
    created_at                 TEXT NOT NULL,
    UNIQUE (source_version, extractor_version, mapping_rule_version, normalization_rule_version)
);
""" + _immutable_triggers("financial_record_set") + """

-- current_record_set 指针（可原子切换，非历史事实）
CREATE TABLE IF NOT EXISTS current_record_set (
    source_document_id  TEXT PRIMARY KEY,
    record_set_version  TEXT NOT NULL REFERENCES financial_record_set(record_set_version),
    switched_at         TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 来源记录（不可变历史事实）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_financial_record (
    record_id               TEXT PRIMARY KEY,
    record_set_version      TEXT NOT NULL REFERENCES financial_record_set(record_set_version),
    company_id              TEXT NOT NULL,
    standard_item_code      TEXT NOT NULL,
    statement_type          TEXT NOT NULL,
    raw_item_text           TEXT NOT NULL,
    raw_value               REAL,
    raw_unit                TEXT NOT NULL,
    raw_currency            TEXT NOT NULL,
    std_value               REAL,
    std_unit                TEXT NOT NULL,
    std_currency            TEXT NOT NULL,
    conversion_rule_version TEXT NOT NULL,
    report_period           TEXT NOT NULL,
    period_type             TEXT NOT NULL,
    statement_scope         TEXT NOT NULL,
    currency                TEXT NOT NULL,
    restatement_version     TEXT NOT NULL,
    locator                 TEXT,
    mapping_mode            TEXT NOT NULL,
    confidence              REAL NOT NULL,
    record_hash             TEXT NOT NULL,
    quality_flags           TEXT NOT NULL,
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_record_set ON source_financial_record(record_set_version);
CREATE INDEX IF NOT EXISTS idx_record_company ON source_financial_record(company_id);
""" + _immutable_triggers("source_financial_record") + """

-- ---------------------------------------------------------------------------
-- 对账（A4 填充业务，DDL 先行）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_group (
    comparison_key        TEXT PRIMARY KEY,
    state                 TEXT NOT NULL,
    candidate_record_ids  TEXT NOT NULL,
    std_values            TEXT NOT NULL,
    diff_detail           TEXT NOT NULL,
    created_at            TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 决议（不可变审计事件，无 status 列）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resolution_record (
    resolution_id        TEXT PRIMARY KEY,
    group_id             TEXT NOT NULL,
    candidate_set_hash   TEXT NOT NULL,
    source_hashes        TEXT NOT NULL,
    comparison_key       TEXT NOT NULL,
    rule_versions        TEXT NOT NULL,
    accepted_record_ids  TEXT NOT NULL,
    rejected_record_ids  TEXT NOT NULL,
    reason_code          TEXT NOT NULL,
    note                 TEXT,
    operator             TEXT NOT NULL,
    confirmed_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resolution_group ON resolution_record(group_id);
""" + _immutable_triggers("resolution_record") + """

-- resolution_validity（追加事件）+ resolution_head（指针）
CREATE TABLE IF NOT EXISTS resolution_validity (
    event_id          TEXT PRIMARY KEY,
    resolution_id     TEXT NOT NULL REFERENCES resolution_record(resolution_id),
    status            TEXT NOT NULL,
    invalidated_by    TEXT,
    invalidated_reason TEXT,
    event_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resval_resolution ON resolution_validity(resolution_id, event_at);

CREATE TABLE IF NOT EXISTS resolution_head (
    group_id       TEXT PRIMARY KEY,
    resolution_id  TEXT NOT NULL REFERENCES resolution_record(resolution_id),
    updated_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 快照（不可变，无 stale/retired 状态列）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_snapshot (
    snapshot_id         TEXT PRIMARY KEY,
    snapshot_version    TEXT NOT NULL,
    company_id          TEXT NOT NULL,
    as_of_date          TEXT NOT NULL,
    scope               TEXT NOT NULL,
    currency            TEXT NOT NULL,
    purpose             TEXT NOT NULL,
    source_versions     TEXT NOT NULL,
    resolution_versions TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_company ON financial_snapshot(company_id, as_of_date);
""" + _immutable_triggers("financial_snapshot") + """

CREATE TABLE IF NOT EXISTS snapshot_item (
    snapshot_id        TEXT NOT NULL REFERENCES financial_snapshot(snapshot_id),
    comparison_key     TEXT NOT NULL,
    standard_item_code TEXT NOT NULL,
    amount             REAL,
    unit               TEXT,
    source_refs        TEXT NOT NULL,
    resolution_id      TEXT,
    PRIMARY KEY (snapshot_id, comparison_key)
);
""" + _immutable_triggers("snapshot_item") + """

CREATE TABLE IF NOT EXISTS snapshot_exception (
    snapshot_id        TEXT NOT NULL REFERENCES financial_snapshot(snapshot_id),
    comparison_key     TEXT NOT NULL,
    standard_item_code TEXT NOT NULL,
    exception_type     TEXT NOT NULL,
    blocking_reason    TEXT NOT NULL,
    impact_scope       TEXT NOT NULL,
    detail             TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, comparison_key, standard_item_code, exception_type)
);

-- current_snapshot 指针（唯一范围含 company/scope/currency/as_of/purpose）
CREATE TABLE IF NOT EXISTS current_snapshot (
    company_id   TEXT NOT NULL,
    scope        TEXT NOT NULL,
    currency     TEXT NOT NULL,
    as_of_date   TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    snapshot_id  TEXT NOT NULL REFERENCES financial_snapshot(snapshot_id),
    switched_at  TEXT NOT NULL,
    UNIQUE (company_id, scope, currency, as_of_date, purpose)
);

-- snapshot_validity（追加事件）+ snapshot_switch_log（审计）
CREATE TABLE IF NOT EXISTS snapshot_validity (
    event_id          TEXT PRIMARY KEY,
    snapshot_id       TEXT NOT NULL REFERENCES financial_snapshot(snapshot_id),
    status            TEXT NOT NULL,
    invalidated_by    TEXT,
    invalidated_reason TEXT,
    event_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapval_snapshot ON snapshot_validity(snapshot_id, event_at);

CREATE TABLE IF NOT EXISTS snapshot_switch_log (
    log_id          TEXT PRIMARY KEY,
    old_snapshot_id TEXT,
    new_snapshot_id TEXT NOT NULL,
    reason          TEXT NOT NULL,
    switched_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 公式（A6 填充业务，DDL 先行）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS formula_definition (
    formula_id            TEXT NOT NULL,
    formula_version       TEXT NOT NULL,
    input_item_codes      TEXT NOT NULL,
    period_requirement    TEXT NOT NULL,
    scope_requirement     TEXT NOT NULL,
    python_impl           TEXT NOT NULL,
    missing_rule          TEXT NOT NULL,
    zero_denominator_rule TEXT NOT NULL,
    rounding_rule         TEXT NOT NULL,
    effective_at          TEXT NOT NULL,
    PRIMARY KEY (formula_id, formula_version)
);

-- ---------------------------------------------------------------------------
-- 隔离（不修改被保护历史行）+ 运行态
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id  TEXT PRIMARY KEY,
    object_type    TEXT NOT NULL,
    object_id      TEXT NOT NULL,
    reason         TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_object ON quarantine(object_type, object_id);

CREATE TABLE IF NOT EXISTS progress_events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    stage_id        TEXT NOT NULL,
    status          TEXT NOT NULL,
    message_code    TEXT NOT NULL,
    completed_units INTEGER,
    total_units     INTEGER,
    error_code      TEXT,
    recoverable     INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fv2_progress_run ON progress_events(run_id, created_at);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id       TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    stage_id            TEXT NOT NULL,
    state_version       INTEGER NOT NULL,
    artifact_refs       TEXT,
    input_hashes        TEXT,
    dependency_versions TEXT,
    resolution_refs     TEXT,
    completed_unit_ids  TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fv2_checkpoint_run ON checkpoints(run_id, created_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# 序列化 / 反序列化
# ---------------------------------------------------------------------------

def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(s: str | None):
    if not s:
        return None
    return json.loads(s)


def _row_to_source_document(row: sqlite3.Row) -> S.FinancialSourceDocument:
    return S.FinancialSourceDocument(
        source_document_id=row["source_document_id"],
        company_id=row["company_id"],
        source_name=row["source_name"],
        source_class=row["source_class"],
        declared_company_name=row["declared_company_name"],
        detected_company_name=row["detected_company_name"],
        subject_match_status=row["subject_match_status"],
        created_at=row["created_at"],
    )


def _row_to_source_version(row: sqlite3.Row) -> S.FinancialSourceVersion:
    return S.FinancialSourceVersion(
        source_version=row["source_version"],
        source_document_id=row["source_document_id"],
        file_sha256=row["file_sha256"],
        file_type=row["file_type"],
        file_size=row["file_size"],
        document_id=row["document_id"],
        document_version=row["document_version"],
        report_periods=_json_loads(row["report_periods"]) or [],
        currency=row["currency"],
        statement_scope=row["statement_scope"],
        audit_status=row["audit_status"],
        extractor_name=row["extractor_name"],
        extractor_version=row["extractor_version"],
        mapping_rule_version=row["mapping_rule_version"],
        normalization_rule_version=row["normalization_rule_version"],
        quality_flags=_json_loads(row["quality_flags"]) or [],
        created_at=row["created_at"],
    )


def _row_to_record_set(row: sqlite3.Row) -> S.FinancialRecordSet:
    return S.FinancialRecordSet(
        record_set_version=row["record_set_version"],
        source_version=row["source_version"],
        extractor_version=row["extractor_version"],
        mapping_rule_version=row["mapping_rule_version"],
        normalization_rule_version=row["normalization_rule_version"],
        dependency_versions=_json_loads(row["dependency_versions"]) or {},
        block_count=row["block_count"],
        record_count=row["record_count"],
        created_at=row["created_at"],
    )


def _row_to_record(row: sqlite3.Row) -> S.SourceFinancialRecord:
    return S.SourceFinancialRecord(
        record_id=row["record_id"],
        record_set_version=row["record_set_version"],
        company_id=row["company_id"],
        standard_item_code=row["standard_item_code"],
        statement_type=row["statement_type"],
        raw_item_text=row["raw_item_text"],
        raw_value=row["raw_value"],
        raw_unit=row["raw_unit"],
        raw_currency=row["raw_currency"],
        std_value=row["std_value"],
        std_unit=row["std_unit"],
        std_currency=row["std_currency"],
        conversion_rule_version=row["conversion_rule_version"],
        report_period=row["report_period"],
        period_type=row["period_type"],
        statement_scope=row["statement_scope"],
        currency=row["currency"],
        restatement_version=row["restatement_version"],
        locator=S.locator_from_dict(_json_loads(row["locator"])),
        mapping_mode=row["mapping_mode"],
        confidence=row["confidence"],
        record_hash=row["record_hash"],
        quality_flags=_json_loads(row["quality_flags"]) or [],
        created_at=row["created_at"],
    )


def _row_to_progress(row: sqlite3.Row) -> S.ProgressEvent:
    return S.ProgressEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        stage_id=row["stage_id"],
        status=row["status"],
        message_code=row["message_code"],
        completed_units=row["completed_units"],
        total_units=row["total_units"],
        error_code=row["error_code"],
        recoverable=bool(row["recoverable"]),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# 来源文档头（可更新 subject_match_status）
# ---------------------------------------------------------------------------

def insert_source_document(doc: S.FinancialSourceDocument) -> None:
    """插入业务文档头。重复 source_document_id 显式报错（不静默忽略）。"""
    validator.validate_source_document(doc)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO financial_source_document (source_document_id, company_id, "
            "source_name, source_class, declared_company_name, detected_company_name, "
            "subject_match_status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (doc.source_document_id, doc.company_id, doc.source_name, doc.source_class,
             doc.declared_company_name, doc.detected_company_name,
             doc.subject_match_status, doc.created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_source_document(source_document_id: str) -> S.FinancialSourceDocument | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM financial_source_document WHERE source_document_id=?",
            (source_document_id,),
        ).fetchone()
        return _row_to_source_document(row) if row else None
    finally:
        conn.close()


def update_subject_match(source_document_id: str, subject_match_status: str,
                         detected_company_name: str | None) -> None:
    """细化主体匹配状态（登记后随检测结果更新，仅此一处允许更新文档头）。"""
    if subject_match_status not in S.SUBJECT_MATCH_STATUSES:
        raise validator.ValidationError(f"subject_match_status 非法: {subject_match_status!r}")
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE financial_source_document SET subject_match_status=?, detected_company_name=? "
            "WHERE source_document_id=?",
            (subject_match_status, detected_company_name, source_document_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"source_document 不存在: {source_document_id}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def find_source_document_id_by_sha256(company_id: str, file_sha256: str) -> str | None:
    """按 (company_id, file_sha256) 查找已登记的业务文档 id（用于幂等复用）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT d.source_document_id FROM financial_source_document d "
            "JOIN financial_source_version v ON v.source_document_id = d.source_document_id "
            "WHERE d.company_id=? AND v.file_sha256=? LIMIT 1",
            (company_id, file_sha256),
        ).fetchone()
        return row["source_document_id"] if row else None
    finally:
        conn.close()


def list_source_documents(company_id: str) -> list[S.FinancialSourceDocument]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM financial_source_document WHERE company_id=? ORDER BY created_at",
            (company_id,),
        ).fetchall()
        return [_row_to_source_document(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 内容版本（不可变）
# ---------------------------------------------------------------------------

def insert_source_version(v: S.FinancialSourceVersion) -> None:
    validator.validate_source_version(v)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO financial_source_version (source_version, source_document_id, "
            "file_sha256, file_type, file_size, document_id, document_version, "
            "report_periods, currency, statement_scope, audit_status, extractor_name, "
            "extractor_version, mapping_rule_version, normalization_rule_version, "
            "quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v.source_version, v.source_document_id, v.file_sha256, v.file_type,
             v.file_size, v.document_id, v.document_version, _json_dumps(v.report_periods),
             v.currency, v.statement_scope, v.audit_status, v.extractor_name,
             v.extractor_version, v.mapping_rule_version, v.normalization_rule_version,
             _json_dumps(v.quality_flags), v.created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_source_version(source_version: str) -> S.FinancialSourceVersion | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM financial_source_version WHERE source_version=?",
            (source_version,),
        ).fetchone()
        return _row_to_source_version(row) if row else None
    finally:
        conn.close()


def get_source_version_by_content(source_document_id: str, file_sha256: str) -> S.FinancialSourceVersion | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM financial_source_version "
            "WHERE source_document_id=? AND file_sha256=?",
            (source_document_id, file_sha256),
        ).fetchone()
        return _row_to_source_version(row) if row else None
    finally:
        conn.close()


def list_source_versions(source_document_id: str) -> list[S.FinancialSourceVersion]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM financial_source_version WHERE source_document_id=? ORDER BY created_at",
            (source_document_id,),
        ).fetchall()
        return [_row_to_source_version(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 记录集合（不可变）+ current 指针（原子切换）
# ---------------------------------------------------------------------------

def insert_record_set(rs: S.FinancialRecordSet) -> None:
    validator.validate_record_set(rs)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO financial_record_set (record_set_version, source_version, "
            "extractor_version, mapping_rule_version, normalization_rule_version, "
            "dependency_versions, block_count, record_count, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rs.record_set_version, rs.source_version, rs.extractor_version,
             rs.mapping_rule_version, rs.normalization_rule_version,
             _json_dumps(rs.dependency_versions), rs.block_count, rs.record_count,
             rs.created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_record_set(record_set_version: str) -> S.FinancialRecordSet | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM financial_record_set WHERE record_set_version=?",
            (record_set_version,),
        ).fetchone()
        return _row_to_record_set(row) if row else None
    finally:
        conn.close()


def list_record_sets(source_version: str) -> list[S.FinancialRecordSet]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM financial_record_set WHERE source_version=? ORDER BY created_at",
            (source_version,),
        ).fetchall()
        return [_row_to_record_set(r) for r in rows]
    finally:
        conn.close()


def set_current_record_set(source_document_id: str, record_set_version: str) -> None:
    """原子切换 current_record_set 指针（新 record_set 完整提交后调用）。"""
    conn = _get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM financial_record_set WHERE record_set_version=?",
            (record_set_version,),
        ).fetchone()
        if exists is None:
            raise KeyError(f"record_set 不存在: {record_set_version}")
        conn.execute(
            "INSERT INTO current_record_set (source_document_id, record_set_version, switched_at) "
            "VALUES (?,?,?) "
            "ON CONFLICT(source_document_id) DO UPDATE SET "
            "record_set_version=excluded.record_set_version, switched_at=excluded.switched_at",
            (source_document_id, record_set_version, _utcnow()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current_record_set(source_document_id: str) -> S.FinancialRecordSet | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT rs.* FROM current_record_set c "
            "JOIN financial_record_set rs ON rs.record_set_version = c.record_set_version "
            "WHERE c.source_document_id=?",
            (source_document_id,),
        ).fetchone()
        return _row_to_record_set(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 来源记录（不可变）
# ---------------------------------------------------------------------------

def insert_records(records: list[S.SourceFinancialRecord], record_set_version: str) -> None:
    """在单事务内插入一批来源记录；写后核对实际行数，任一不符回滚。"""
    if not records:
        raise ValueError("records 不能为空")
    validator.validate_records(records, record_set_version)
    conn = _get_conn()
    try:
        for r in records:
            conn.execute(
                "INSERT INTO source_financial_record (record_id, record_set_version, "
                "company_id, standard_item_code, statement_type, raw_item_text, raw_value, "
                "raw_unit, raw_currency, std_value, std_unit, std_currency, "
                "conversion_rule_version, report_period, period_type, statement_scope, "
                "currency, restatement_version, locator, mapping_mode, confidence, "
                "record_hash, quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.record_id, r.record_set_version, r.company_id, r.standard_item_code,
                 r.statement_type, r.raw_item_text, r.raw_value, r.raw_unit, r.raw_currency,
                 r.std_value, r.std_unit, r.std_currency, r.conversion_rule_version,
                 r.report_period, r.period_type, r.statement_scope, r.currency,
                 r.restatement_version, _json_dumps(S.locator_to_dict(r.locator)),
                 r.mapping_mode, r.confidence, r.record_hash, _json_dumps(r.quality_flags),
                 r.created_at),
            )
        actual = conn.execute(
            "SELECT COUNT(*) AS c FROM source_financial_record WHERE record_set_version=?",
            (record_set_version,),
        ).fetchone()["c"]
        if actual != len(records):
            raise RuntimeError(
                f"来源记录写入校验失败：实际落库 {actual} 行 != 传入 {len(records)} 行")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_records(record_set_version: str) -> list[S.SourceFinancialRecord]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM source_financial_record WHERE record_set_version=? ORDER BY record_id",
            (record_set_version,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def count_records(record_set_version: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM source_financial_record WHERE record_set_version=?",
            (record_set_version,),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 隔离（quarantine）
# ---------------------------------------------------------------------------

def quarantine(object_type: str, object_id: str, reason: str) -> None:
    """把损坏对象记录到 quarantine（不修改被保护历史行）。"""
    if object_type not in S.QUARANTINE_OBJECT_TYPES:
        raise validator.ValidationError(f"quarantine object_type 非法: {object_type!r}")
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO quarantine (quarantine_id, object_type, object_id, reason, quarantined_at) "
            "VALUES (?,?,?,?,?)",
            ("q-" + uuid.uuid4().hex[:16], object_type, object_id, reason, _utcnow()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_quarantined(object_type: str, object_id: str) -> bool:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM quarantine WHERE object_type=? AND object_id=? LIMIT 1",
            (object_type, object_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 进度事件 / checkpoint（运行态，与历史事实表分离）
# ---------------------------------------------------------------------------

def record_progress(event: S.ProgressEvent) -> None:
    """写入进度事件（独立连接，失败事件不随主提交一起回滚）。"""
    validator.validate_progress_event(event)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO progress_events (event_id, run_id, stage_id, status, message_code, "
            "completed_units, total_units, error_code, recoverable, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event.event_id, event.run_id, event.stage_id, event.status, event.message_code,
             event.completed_units, event.total_units, event.error_code,
             int(event.recoverable), event.created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def latest_progress(run_id: str) -> S.ProgressEvent | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM progress_events WHERE run_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return _row_to_progress(row) if row else None
    finally:
        conn.close()


def history_progress(run_id: str) -> list[S.ProgressEvent]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM progress_events WHERE run_id=? ORDER BY created_at ASC, rowid ASC",
            (run_id,),
        ).fetchall()
        return [_row_to_progress(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 查询：跨公司隔离辅助
# ---------------------------------------------------------------------------

def count_source_documents(company_id: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM financial_source_document WHERE company_id=?",
            (company_id,),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_inspect(company_id: str) -> dict:
    docs = list_source_documents(company_id)
    result = {"company_id": company_id, "documents": []}
    for d in docs:
        versions = []
        for v in list_source_versions(d.source_document_id):
            current = get_current_record_set(d.source_document_id)
            versions.append({
                "source_version": v.source_version,
                "file_sha256": v.file_sha256[:16],
                "file_type": v.file_type,
                "file_size": v.file_size,
                "report_periods": v.report_periods,
                "statement_scope": v.statement_scope,
                "currency": v.currency,
                "extractor_version": v.extractor_version,
                "current_record_set": (current.record_set_version
                                       if current and current.source_version == v.source_version
                                       else None),
            })
        result["documents"].append({
            "source_document_id": d.source_document_id,
            "source_name": d.source_name,
            "source_class": d.source_class,
            "subject_match_status": d.subject_match_status,
            "versions": versions,
        })
    return result


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m financial_v2.store",
                                     description="financial_v2 Store CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inspect = sub.add_parser("inspect", help="查看某公司的财务来源登记概览")
    p_inspect.add_argument("--company", required=True, help="公司标识（company_id）")

    args = parser.parse_args(argv)
    init_db()
    if args.cmd == "inspect":
        print(json.dumps(_cli_inspect(args.company), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
