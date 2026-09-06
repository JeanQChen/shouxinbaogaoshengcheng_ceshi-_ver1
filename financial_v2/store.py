"""financial_v2 Store：SQLite 权威存储 + 追加迁移 + 事务 + 不可变触发器 + 查询。

- 独立库 data/financial_v2.db，与 V1 data/credit.db、Phase 1 data/evidence.db 分离。
- 不可变历史事实表（financial_source_version / financial_record_set /
  source_financial_record / resolution_record / financial_snapshot / snapshot_item）
  由 BEFORE UPDATE/DELETE 触发器保护，不依赖代码约定。
- 迁移为「追加式」：历史 v1 DDL 冻结不再修改，v2 以独立 migration 函数追加；
  schema_migrations 记录已应用版本；新建库一次到位，旧 v1 库原地升级；
  迁移失败完整回滚；结构与 migration 记录不一致时失败关闭。
- 来源登记与记录集提交均为「单事务原子接口」：
    * register_source_atomic —— 校验 + 文档头复用/插入 + 内容版本复用/插入，全回滚；
    * commit_record_set —— 校验 + 归属链 + 逐记录公司归属 + 严格复用（含隔离/
      完整性/current 指针原子切换）+ 写 record_set + 写 records + 写后复核，全回滚。
- 重复写入禁用 INSERT OR IGNORE：先读后严格比对，冲突显式报错回滚（StorageConflictError）；
  复用路径检测到存储损坏（record_hash/id 与内容不符）则记录 quarantine 并抛
  StorageCorruptionError，不隔离健康对象。
- 连接模式与 evidence/store.py 一致：per-call connect/close，PRAGMA foreign_keys=ON。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from financial_v2 import schema as S
from financial_v2 import validator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/financial_v2.db")

# 模块级，由 init_db() 设置，后续操作复用本路径。
_db_path: Path | None = None


class StorageConflictError(Exception):
    """存储冲突：重复提交同一身份但内容不一致，显式报错（不静默覆盖/不隔离）。"""


class StorageCorruptionError(Exception):
    """存储损坏：复用前完整性校验失败，已记录 quarantine（区别于普通参数不一致）。"""


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


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class RegisterSourceResult:
    document: S.FinancialSourceDocument
    version: S.FinancialSourceVersion
    reused: bool
    subject_blocked: bool


@dataclass
class CommitRecordSetResult:
    record_set: S.FinancialRecordSet
    reused: bool
    current_switched: bool   # 复用且原本非 current → True；新提交恒 True；复用且已是 current → False
    record_count: int


# ---------------------------------------------------------------------------
# DDL（v1 冻结 + v2 当前）
# ---------------------------------------------------------------------------

def _immutable_trigger_sqls(table: str) -> list[str]:
    """为不可变历史事实表生成 UPDATE/DELETE 阻断触发器（单条语句列表）。"""
    return [
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update\n"
        f"    BEFORE UPDATE ON {table}\n"
        f"BEGIN\n    SELECT RAISE(ABORT, '{table} is immutable (UPDATE forbidden)');\nEND;",
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete\n"
        f"    BEFORE DELETE ON {table}\n"
        f"BEGIN\n    SELECT RAISE(ABORT, '{table} is immutable (DELETE forbidden)');\nEND;",
    ]


def _immutable_triggers(table: str) -> str:
    """返回可直接拼进 DDL 文本的触发器语句串。"""
    return "\n" + "\n".join(_immutable_trigger_sqls(table)) + "\n"


def build_ddl() -> str:
    """返回最新（v2）建表语句（幂等）。"""
    return _build_ddl_v2()


def _build_ddl_v1() -> str:
    """历史 v1 DDL（冻结，不再修改；仅用于旧库迁移与迁移测试）。"""
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
-- 内容版本（不可变历史事实；v1 含抽取占位列，v2 迁移瘦身）
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
-- 记录集合（不可变历史事实；v1 无抽取事实列，v2 迁移追加）
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
""" + _shared_ddl_tail()


def _build_ddl_v2() -> str:
    """当前最新（v2）DDL：内容版本只存文件事实，记录集合承载抽取事实。"""
    return """
-- ---------------------------------------------------------------------------
-- 来源登记（业务文档头，可受控更新 subject_match_status）
--   source_document_id 为「含 company 命名空间的内部全局唯一 ID」（A1 修订 4）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_source_document (
    source_document_id     TEXT PRIMARY KEY,
    company_id             TEXT NOT NULL,
    source_name            TEXT NOT NULL,
    source_class           TEXT NOT NULL,
    declared_company_name  TEXT,
    detected_company_name  TEXT,
    subject_match_status   TEXT NOT NULL,
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_src_doc_company ON financial_source_document(company_id);

-- ---------------------------------------------------------------------------
-- 内容版本（不可变历史事实，仅保存登记时真实已知的文件事实，A1 修订 1）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_source_version (
    source_version     TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL REFERENCES financial_source_document(source_document_id),
    file_sha256        TEXT NOT NULL,
    file_type          TEXT NOT NULL,
    file_size          INTEGER NOT NULL,
    document_id        TEXT,
    document_version   TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (source_document_id, file_sha256)
);
CREATE INDEX IF NOT EXISTS idx_src_ver_doc ON financial_source_version(source_document_id);
""" + _immutable_triggers("financial_source_version") + """

-- ---------------------------------------------------------------------------
-- 记录集合（不可变历史事实；承载抽取产物与规则版本）
--   dependency_versions 以 canonical JSON 文本纳入唯一身份（A1 修订 5）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS financial_record_set (
    record_set_version         TEXT PRIMARY KEY,
    source_version             TEXT NOT NULL REFERENCES financial_source_version(source_version),
    extractor_name             TEXT,
    extractor_version          TEXT NOT NULL,
    mapping_rule_version       TEXT NOT NULL,
    normalization_rule_version TEXT NOT NULL,
    dependency_versions        TEXT NOT NULL,
    report_periods             TEXT NOT NULL,
    currency                   TEXT,
    unit                       TEXT,
    statement_scope            TEXT,
    audit_status               TEXT,
    block_count                INTEGER NOT NULL,
    record_count               INTEGER NOT NULL,
    created_at                 TEXT NOT NULL,
    UNIQUE (source_version, extractor_version, mapping_rule_version, normalization_rule_version, dependency_versions)
);
""" + _immutable_triggers("financial_record_set") + """

-- current_record_set 指针（可原子切换，非历史事实）
CREATE TABLE IF NOT EXISTS current_record_set (
    source_document_id  TEXT PRIMARY KEY,
    record_set_version  TEXT NOT NULL REFERENCES financial_record_set(record_set_version),
    switched_at         TEXT NOT NULL
);
""" + _shared_ddl_tail()


def _shared_ddl_tail() -> str:
    """v1/v2 均未变化的共享 DDL（从 source_financial_record 到 schema_migrations）。"""
    return """
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
    event_id           TEXT PRIMARY KEY,
    resolution_id      TEXT NOT NULL REFERENCES resolution_record(resolution_id),
    status             TEXT NOT NULL,
    invalidated_by     TEXT,
    invalidated_reason TEXT,
    event_at           TEXT NOT NULL
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
    event_id           TEXT PRIMARY KEY,
    snapshot_id        TEXT NOT NULL REFERENCES financial_snapshot(snapshot_id),
    status             TEXT NOT NULL,
    invalidated_by     TEXT,
    invalidated_reason TEXT,
    event_at           TEXT NOT NULL
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
# 迁移：v1 → v2（受控建新表 + 复制校验 + 替换）
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _assert_copy_preserved(conn: sqlite3.Connection, src: str, dst: str, key_col: str) -> None:
    """切换前检查行数与关键字段，不一致即失败（迁移回滚）。"""
    old = conn.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
    new = conn.execute(f"SELECT COUNT(*) FROM {dst}").fetchone()[0]
    if old != new:
        raise RuntimeError(f"迁移表 {src} 行数不一致: 原 {old} != 新 {new}")
    old_keys = {r[0] for r in conn.execute(f"SELECT {key_col} FROM {src}")}
    new_keys = {r[0] for r in conn.execute(f"SELECT {key_col} FROM {dst}")}
    if old_keys != new_keys:
        raise RuntimeError(f"迁移表 {src} 关键字段 {key_col} 不一致")


def _swap_financial_source_document(conn: sqlite3.Connection) -> None:
    tmp = "financial_source_document__mig"
    conn.execute(f"""
        CREATE TABLE {tmp} (
            source_document_id     TEXT PRIMARY KEY,
            company_id             TEXT NOT NULL,
            source_name            TEXT NOT NULL,
            source_class           TEXT NOT NULL,
            declared_company_name  TEXT,
            detected_company_name  TEXT,
            subject_match_status   TEXT NOT NULL,
            created_at             TEXT NOT NULL
        )
    """)
    conn.execute(f"""
        INSERT INTO {tmp} (source_document_id, company_id, source_name, source_class,
            declared_company_name, detected_company_name, subject_match_status, created_at)
        SELECT source_document_id, company_id, source_name, source_class,
            declared_company_name, detected_company_name, subject_match_status, created_at
        FROM financial_source_document
    """)
    _assert_copy_preserved(conn, "financial_source_document", tmp, "source_document_id")
    conn.execute("DROP TABLE financial_source_document")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO financial_source_document")
    conn.execute("CREATE INDEX idx_src_doc_company ON financial_source_document(company_id)")


def _swap_financial_source_version(conn: sqlite3.Connection) -> None:
    tmp = "financial_source_version__mig"
    conn.execute(f"""
        CREATE TABLE {tmp} (
            source_version     TEXT PRIMARY KEY,
            source_document_id TEXT NOT NULL REFERENCES financial_source_document(source_document_id),
            file_sha256        TEXT NOT NULL,
            file_type          TEXT NOT NULL,
            file_size          INTEGER NOT NULL,
            document_id        TEXT,
            document_version   TEXT,
            created_at         TEXT NOT NULL,
            UNIQUE (source_document_id, file_sha256)
        )
    """)
    conn.execute(f"""
        INSERT INTO {tmp} (source_version, source_document_id, file_sha256, file_type,
            file_size, document_id, document_version, created_at)
        SELECT source_version, source_document_id, file_sha256, file_type,
            file_size, document_id, document_version, created_at
        FROM financial_source_version
    """)
    _assert_copy_preserved(conn, "financial_source_version", tmp, "source_version")
    conn.execute("DROP TABLE financial_source_version")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO financial_source_version")
    conn.execute("CREATE INDEX idx_src_ver_doc ON financial_source_version(source_document_id)")
    for sql in _immutable_trigger_sqls("financial_source_version"):
        conn.execute(sql)


def _swap_financial_record_set(conn: sqlite3.Connection) -> None:
    tmp = "financial_record_set__mig"
    conn.execute(f"""
        CREATE TABLE {tmp} (
            record_set_version         TEXT PRIMARY KEY,
            source_version             TEXT NOT NULL REFERENCES financial_source_version(source_version),
            extractor_name             TEXT,
            extractor_version          TEXT NOT NULL,
            mapping_rule_version       TEXT NOT NULL,
            normalization_rule_version TEXT NOT NULL,
            dependency_versions        TEXT NOT NULL,
            report_periods             TEXT NOT NULL,
            currency                   TEXT,
            unit                       TEXT,
            statement_scope            TEXT,
            audit_status               TEXT,
            block_count                INTEGER NOT NULL,
            record_count               INTEGER NOT NULL,
            created_at                 TEXT NOT NULL,
            UNIQUE (source_version, extractor_version, mapping_rule_version, normalization_rule_version, dependency_versions)
        )
    """)
    conn.execute(f"""
        INSERT INTO {tmp} (record_set_version, source_version, extractor_version,
            mapping_rule_version, normalization_rule_version, dependency_versions,
            report_periods, block_count, record_count, created_at)
        SELECT record_set_version, source_version, extractor_version,
            mapping_rule_version, normalization_rule_version, dependency_versions,
            '[]', block_count, record_count, created_at
        FROM financial_record_set
    """)
    _assert_copy_preserved(conn, "financial_record_set", tmp, "record_set_version")
    conn.execute("DROP TABLE financial_record_set")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO financial_record_set")
    for sql in _immutable_trigger_sqls("financial_record_set"):
        conn.execute(sql)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2：文档头去冗余唯一键 + 内容版本瘦身 + 记录集合承载抽取事实。

    迁移前先验证来源确为 v1 结构，否则失败关闭（不假装成功）。
    """
    ver_cols = _table_columns(conn, "financial_source_version")
    if "currency" not in ver_cols or "report_periods" not in ver_cols:
        raise RuntimeError(
            "迁移 v1→v2 前提不满足：financial_source_version 已无 v1 占位列 "
            "(currency/report_periods)，可能已是 v2 或结构损坏，拒绝迁移")
    rs_cols = _table_columns(conn, "financial_record_set")
    if "extractor_name" in rs_cols:
        raise RuntimeError(
            "迁移 v1→v2 前提不满足：financial_record_set 已含 v2 列 extractor_name，拒绝迁移")

    _swap_financial_source_document(conn)
    _swap_financial_source_version(conn)
    _swap_financial_record_set(conn)


def _read_applied_versions(conn: sqlite3.Connection) -> list[str]:
    return [r["version"] for r in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY rowid")]


def _run_migration(conn: sqlite3.Connection, version: str, fn: Callable[[sqlite3.Connection], None]) -> None:
    """单条迁移：切 FK → 事务内执行迁移 + 记录版本 → 恢复 FK → FK 完整性复核。"""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        try:
            fn(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                (version, _utcnow()),
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"迁移 {version} 后外键校验失败: {bad[:5]}")


def _apply_pending_migrations(conn: sqlite3.Connection) -> None:
    applied = _read_applied_versions(conn)
    known = [v for v, _ in MIGRATIONS]
    expected_prefix = known[:len(applied)]
    if list(applied) != expected_prefix:
        raise RuntimeError(
            f"schema_migrations 版本序列不合法: {applied}（期望前缀 {expected_prefix}），拒绝")
    pending = [(v, fn) for v, fn in MIGRATIONS if v not in set(applied)]
    for version, fn in pending:
        if fn is None:
            raise RuntimeError(f"迁移 {version} 无实现但结构已存在，拒绝")
        _run_migration(conn, version, fn)
    _verify_structure_matches_latest(conn)


def _verify_structure_matches_latest(conn: sqlite3.Connection) -> None:
    """结构/migration 记录一致性探针：不一致即失败关闭。"""
    latest = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    if latest != MIGRATIONS[-1][0]:
        raise RuntimeError(f"schema_migrations 最新版本 {latest} != 期望 {MIGRATIONS[-1][0]}")
    rs_cols = _table_columns(conn, "financial_record_set")
    for col in ("extractor_name", "report_periods", "currency", "unit",
                "statement_scope", "audit_status"):
        if col not in rs_cols:
            raise RuntimeError(f"结构校验失败：financial_record_set 缺列 {col}")
    ver_cols = _table_columns(conn, "financial_source_version")
    for col in ("currency", "report_periods", "extractor_name", "quality_flags"):
        if col in ver_cols:
            raise RuntimeError(f"结构校验失败：financial_source_version 残留 v1 占位列 {col}")


# 迁移列表（追加式；已应用版本记录在 schema_migrations 表）。
MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None] | None]] = [
    ("1", None),                # v1 初始 DDL（历史冻结，不再修改）
    ("2", _migrate_v1_to_v2),   # v1 → v2：内容版本瘦身 + 记录集合抽取事实
]


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化 financial_v2 SQLite 数据库，应用全部未执行迁移。

    - 全新库：一次建出最新 v2 schema，并记录全部迁移为已应用；
    - 已有库：校验 schema_migrations 为已知前缀，逐条执行未应用迁移；
    - 任一步失败回滚，结构与 migration 记录不一致即失败关闭。
    """
    global _db_path
    _db_path = Path(db_path)
    if S.SCHEMA_VERSION != MIGRATIONS[-1][0]:
        raise RuntimeError(
            f"SCHEMA_VERSION {S.SCHEMA_VERSION} != 最新 migration {MIGRATIONS[-1][0]}")

    conn = _get_conn()
    try:
        if not _table_exists(conn, "schema_migrations"):
            conn.executescript(build_ddl())
            now = _utcnow()
            for version, _ in MIGRATIONS:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?,?)",
                    (version, now),
                )
            conn.commit()
        else:
            _apply_pending_migrations(conn)
        _verify_structure_matches_latest(conn)
    finally:
        conn.close()


def applied_schema_version() -> str | None:
    """返回当前数据库已应用的最新 migration 版本（供一致性探针/测试）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return row["v"]
    finally:
        conn.close()


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
        created_at=row["created_at"],
    )


def _row_to_record_set(row: sqlite3.Row) -> S.FinancialRecordSet:
    return S.FinancialRecordSet(
        record_set_version=row["record_set_version"],
        source_version=row["source_version"],
        extractor_name=row["extractor_name"],
        extractor_version=row["extractor_version"],
        mapping_rule_version=row["mapping_rule_version"],
        normalization_rule_version=row["normalization_rule_version"],
        dependency_versions=_json_loads(row["dependency_versions"]) or {},
        report_periods=_json_loads(row["report_periods"]) or [],
        currency=row["currency"],
        unit=row["unit"],
        statement_scope=row["statement_scope"],
        audit_status=row["audit_status"],
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
# 来源登记：原子接口（A1 修订 2）
# ---------------------------------------------------------------------------

def _assert_reuse_compatible(existing_row: sqlite3.Row, incoming: S.FinancialSourceDocument) -> None:
    """既有 source_document_id 复用校验（A1 修订 10）。"""
    if existing_row["company_id"] != incoming.company_id:
        raise validator.ValidationError(
            f"跨公司借用已有 source_document_id: {incoming.source_document_id} "
            f"属于 {existing_row['company_id']!r}，非 {incoming.company_id!r}")
    if existing_row["source_class"] != incoming.source_class:
        raise validator.ValidationError(
            f"source_class 静默改变: {existing_row['source_class']!r} → {incoming.source_class!r}")
    existing_declared = existing_row["declared_company_name"]
    if (existing_declared and incoming.declared_company_name
            and existing_declared != incoming.declared_company_name):
        raise validator.ValidationError(
            f"declared_company_name 静默改变: {existing_declared!r} → {incoming.declared_company_name!r}")


def _merge_subject(existing_row: sqlite3.Row, incoming: S.FinancialSourceDocument) -> tuple[str, str | None]:
    """主体匹配受控更新（A1 修订 10）。"""
    existing_status = existing_row["subject_match_status"]
    existing_detected = existing_row["detected_company_name"]
    if incoming.detected_company_name is None:
        return existing_status, existing_detected
    if existing_status in ("matched", "mismatch") and incoming.subject_match_status == "unverified":
        return existing_status, existing_detected
    return incoming.subject_match_status, incoming.detected_company_name


def _insert_source_document_conn(conn: sqlite3.Connection, doc: S.FinancialSourceDocument) -> None:
    conn.execute(
        "INSERT INTO financial_source_document (source_document_id, company_id, source_name, "
        "source_class, declared_company_name, detected_company_name, subject_match_status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (doc.source_document_id, doc.company_id, doc.source_name, doc.source_class,
         doc.declared_company_name, doc.detected_company_name,
         doc.subject_match_status, doc.created_at),
    )


def _insert_source_version_conn(conn: sqlite3.Connection, v: S.FinancialSourceVersion) -> None:
    conn.execute(
        "INSERT INTO financial_source_version (source_version, source_document_id, file_sha256, "
        "file_type, file_size, document_id, document_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (v.source_version, v.source_document_id, v.file_sha256, v.file_type,
         v.file_size, v.document_id, v.document_version, v.created_at),
    )


def register_source_atomic(
    document: S.FinancialSourceDocument,
    version: S.FinancialSourceVersion,
) -> RegisterSourceResult:
    """原子登记：单事务内完成校验 + 文档头复用/插入 + 内容版本复用/插入。"""
    validator.validate_source_document(document)
    validator.validate_source_version(version)
    if version.source_document_id != document.source_document_id:
        raise validator.ValidationError(
            f"version.source_document_id 与 document.source_document_id 不一致: "
            f"{version.source_document_id!r} != {document.source_document_id!r}")

    conn = _get_conn()
    try:
        existing_version = conn.execute(
            "SELECT * FROM financial_source_version WHERE source_document_id=? AND file_sha256=?",
            (document.source_document_id, version.file_sha256),
        ).fetchone()
        if existing_version is not None:
            existing_doc = conn.execute(
                "SELECT * FROM financial_source_document WHERE source_document_id=?",
                (document.source_document_id,),
            ).fetchone()
            return RegisterSourceResult(
                document=_row_to_source_document(existing_doc),
                version=_row_to_source_version(existing_version),
                reused=True,
                subject_blocked=(existing_doc["subject_match_status"] == "mismatch"),
            )

        existing_doc = conn.execute(
            "SELECT * FROM financial_source_document WHERE source_document_id=?",
            (document.source_document_id,),
        ).fetchone()
        if existing_doc is not None:
            _assert_reuse_compatible(existing_doc, document)
            new_status, new_detected = _merge_subject(existing_doc, document)
            if new_status != existing_doc["subject_match_status"] or new_detected != existing_doc["detected_company_name"]:
                conn.execute(
                    "UPDATE financial_source_document SET subject_match_status=?, detected_company_name=? "
                    "WHERE source_document_id=?",
                    (new_status, new_detected, document.source_document_id),
                )
        else:
            _insert_source_document_conn(conn, document)

        _insert_source_version_conn(conn, version)

        final_doc = _row_to_source_document(conn.execute(
            "SELECT * FROM financial_source_document WHERE source_document_id=?",
            (document.source_document_id,),
        ).fetchone())
        conn.commit()
        return RegisterSourceResult(
            document=final_doc, version=version, reused=False,
            subject_blocked=(final_doc.subject_match_status == "mismatch"),
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 记录集合：原子提交（A1 修订 6/7/8 + 逐记录公司归属 + 复用 current 处理）
# ---------------------------------------------------------------------------

def _insert_record_set_conn(conn: sqlite3.Connection, rs: S.FinancialRecordSet) -> None:
    conn.execute(
        "INSERT INTO financial_record_set (record_set_version, source_version, extractor_name, "
        "extractor_version, mapping_rule_version, normalization_rule_version, dependency_versions, "
        "report_periods, currency, unit, statement_scope, audit_status, block_count, record_count, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rs.record_set_version, rs.source_version, rs.extractor_name,
         rs.extractor_version, rs.mapping_rule_version, rs.normalization_rule_version,
         _json_dumps(rs.dependency_versions), _json_dumps(rs.report_periods),
         rs.currency, rs.unit, rs.statement_scope, rs.audit_status,
         rs.block_count, rs.record_count, rs.created_at),
    )


def _insert_record_conn(conn: sqlite3.Connection, r: S.SourceFinancialRecord) -> None:
    conn.execute(
        "INSERT INTO source_financial_record (record_id, record_set_version, company_id, "
        "standard_item_code, statement_type, raw_item_text, raw_value, raw_unit, raw_currency, "
        "std_value, std_unit, std_currency, conversion_rule_version, report_period, period_type, "
        "statement_scope, currency, restatement_version, locator, mapping_mode, confidence, "
        "record_hash, quality_flags, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r.record_id, r.record_set_version, r.company_id, r.standard_item_code,
         r.statement_type, r.raw_item_text, r.raw_value, r.raw_unit, r.raw_currency,
         r.std_value, r.std_unit, r.std_currency, r.conversion_rule_version,
         r.report_period, r.period_type, r.statement_scope, r.currency,
         r.restatement_version, _json_dumps(S.locator_to_dict(r.locator)),
         r.mapping_mode, r.confidence, r.record_hash, _json_dumps(r.quality_flags),
         r.created_at),
    )


def _require_not_quarantined(conn: sqlite3.Connection, object_type: str, object_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM quarantine WHERE object_type=? AND object_id=? LIMIT 1",
        (object_type, object_id),
    ).fetchone()
    if row is not None:
        raise ValueError(f"{object_type} 已隔离，不得设为 current: {object_id}")


def _insert_quarantine_conn(conn: sqlite3.Connection, object_type: str, object_id: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO quarantine (quarantine_id, object_type, object_id, reason, quarantined_at) "
        "VALUES (?,?,?,?,?)",
        ("q-" + uuid.uuid4().hex[:16], object_type, object_id, reason, _utcnow()),
    )


def _validate_record_set_ownership(conn: sqlite3.Connection, source_document_id: str, record_set_version: str) -> None:
    """校验 record_set → source_version → source_document_id 完整归属链（A1 修订 7）。"""
    rs = conn.execute(
        "SELECT * FROM financial_record_set WHERE record_set_version=?",
        (record_set_version,),
    ).fetchone()
    if rs is None:
        raise KeyError(f"record_set 不存在: {record_set_version}")
    sv = conn.execute(
        "SELECT source_document_id FROM financial_source_version WHERE source_version=?",
        (rs["source_version"],),
    ).fetchone()
    if sv is None:
        raise ValueError(f"record_set 的 source_version 悬空: {rs['source_version']}")
    if sv["source_document_id"] != source_document_id:
        raise ValueError(
            f"record_set 归属链不符：source_version 属于 {sv['source_document_id']!r}，"
            f"非 {source_document_id!r}（拒绝跨文档/跨公司指针）")
    _require_not_quarantined(conn, "financial_source_version", rs["source_version"])
    _require_not_quarantined(conn, "financial_record_set", record_set_version)
    rec_q = conn.execute(
        "SELECT 1 FROM quarantine WHERE object_type='source_financial_record' AND object_id IN "
        "(SELECT record_id FROM source_financial_record WHERE record_set_version=?) LIMIT 1",
        (record_set_version,),
    ).fetchone()
    if rec_q is not None:
        raise ValueError(f"record_set 含被隔离记录: {record_set_version}")


def _set_current_record_set_conn(conn: sqlite3.Connection, source_document_id: str, record_set_version: str) -> None:
    _validate_record_set_ownership(conn, source_document_id, record_set_version)
    conn.execute(
        "INSERT INTO current_record_set (source_document_id, record_set_version, switched_at) "
        "VALUES (?,?,?) "
        "ON CONFLICT(source_document_id) DO UPDATE SET "
        "record_set_version=excluded.record_set_version, switched_at=excluded.switched_at",
        (source_document_id, record_set_version, _utcnow()),
    )


def _authoritative_company_id(conn: sqlite3.Connection, source_version: str) -> str:
    """沿 source_version → source_document_id → 文档头 读取权威 company_id。"""
    row = conn.execute(
        "SELECT d.company_id FROM financial_source_document d "
        "JOIN financial_source_version v ON v.source_document_id = d.source_document_id "
        "WHERE v.source_version = ?", (source_version,),
    ).fetchone()
    if row is None:
        raise KeyError(f"source_version 无归属文档: {source_version}")
    return row["company_id"]


def _existing_records_integrity_ok(conn: sqlite3.Connection, record_set_version: str) -> bool:
    """复用前完整性校验：现有每条记录的 record_id / record_hash 与内容重算一致。"""
    rows = conn.execute(
        "SELECT * FROM source_financial_record WHERE record_set_version=? ORDER BY record_id",
        (record_set_version,),
    ).fetchall()
    for row in rows:
        try:
            validator.validate_record(_row_to_record(row))
        except Exception:
            return False
    return True


def _record_set_header_identical(incoming: S.FinancialRecordSet, row: sqlite3.Row) -> bool:
    return (
        row["source_version"] == incoming.source_version
        and row["extractor_name"] == incoming.extractor_name
        and row["extractor_version"] == incoming.extractor_version
        and row["mapping_rule_version"] == incoming.mapping_rule_version
        and row["normalization_rule_version"] == incoming.normalization_rule_version
        and _json_loads(row["dependency_versions"]) == incoming.dependency_versions
        and _json_loads(row["report_periods"]) == incoming.report_periods
        and row["currency"] == incoming.currency
        and row["unit"] == incoming.unit
        and row["statement_scope"] == incoming.statement_scope
        and row["audit_status"] == incoming.audit_status
        and row["block_count"] == incoming.block_count
        and row["record_count"] == incoming.record_count
    )


def _record_identical(row: sqlite3.Row, rec: S.SourceFinancialRecord) -> bool:
    """逐字段核对记录（A1 修订 8）：record_id/hash + 原始值/标准值/locator/conversion rule。"""
    return (
        row["record_id"] == rec.record_id
        and row["record_set_version"] == rec.record_set_version
        and row["company_id"] == rec.company_id
        and row["standard_item_code"] == rec.standard_item_code
        and row["statement_type"] == rec.statement_type
        and row["raw_item_text"] == rec.raw_item_text
        and row["raw_value"] == rec.raw_value
        and row["raw_unit"] == rec.raw_unit
        and row["raw_currency"] == rec.raw_currency
        and row["std_value"] == rec.std_value
        and row["std_unit"] == rec.std_unit
        and row["std_currency"] == rec.std_currency
        and row["conversion_rule_version"] == rec.conversion_rule_version
        and row["report_period"] == rec.report_period
        and row["period_type"] == rec.period_type
        and row["statement_scope"] == rec.statement_scope
        and row["currency"] == rec.currency
        and row["restatement_version"] == rec.restatement_version
        and _json_loads(row["locator"]) == S.locator_to_dict(rec.locator)
        and row["mapping_mode"] == rec.mapping_mode
        and row["confidence"] == rec.confidence
        and row["record_hash"] == rec.record_hash
    )


def _record_set_identical(conn: sqlite3.Connection, incoming: S.FinancialRecordSet,
                          incoming_records: list[S.SourceFinancialRecord], existing_row: sqlite3.Row) -> bool:
    if not _record_set_header_identical(incoming, existing_row):
        return False
    existing_records = conn.execute(
        "SELECT * FROM source_financial_record WHERE record_set_version=? ORDER BY record_id",
        (incoming.record_set_version,),
    ).fetchall()
    incoming_sorted = sorted(incoming_records, key=lambda r: r.record_id)
    if len(existing_records) != len(incoming_sorted):
        return False
    for er, ir in zip(existing_records, incoming_sorted):
        if not _record_identical(er, ir):
            return False
    return True


def _reuse_existing(conn: sqlite3.Connection, record_set: S.FinancialRecordSet,
                    records: list[S.SourceFinancialRecord], expected_source_document_id: str,
                    existing_row: sqlite3.Row) -> CommitRecordSetResult:
    """严格复用路径（A1 修订 3/4）：隔离 + 完整性 + 逐字段一致 + current 原子切换。"""
    # 完整归属链 + 隔离校验（sv / record_set / 子记录）。
    _validate_record_set_ownership(conn, expected_source_document_id, record_set.record_set_version)
    # 完整性：现有记录 record_id/hash 与内容重算一致，否则记录 quarantine 并报损坏。
    # quarantine 用独立连接落盘（本事务随后回滚，隔离记录必须持久、不得被回滚吞掉）。
    if not _existing_records_integrity_ok(conn, record_set.record_set_version):
        quarantine("financial_record_set", record_set.record_set_version,
                   "复用前完整性校验失败（record_id/record_hash 与内容不符）")
        raise StorageCorruptionError(
            f"record_set 复用前完整性校验失败: {record_set.record_set_version}")
    # 严格逐字段一致，不一致只报冲突（不隔离健康对象）。
    if not _record_set_identical(conn, record_set, records, existing_row):
        raise StorageConflictError(
            f"record_set_version 已存在但内容不一致: {record_set.record_set_version}")
    # current 指针处理：已是 current 直接返回；否则在本事务内原子切换。
    cur = conn.execute(
        "SELECT record_set_version FROM current_record_set WHERE source_document_id=?",
        (expected_source_document_id,),
    ).fetchone()
    if cur is not None and cur["record_set_version"] == record_set.record_set_version:
        return CommitRecordSetResult(
            record_set=_row_to_record_set(existing_row), reused=True,
            current_switched=False, record_count=record_set.record_count)
    _set_current_record_set_conn(conn, expected_source_document_id, record_set.record_set_version)
    return CommitRecordSetResult(
        record_set=_row_to_record_set(existing_row), reused=True,
        current_switched=True, record_count=record_set.record_count)


def commit_record_set(
    record_set: S.FinancialRecordSet,
    records: list[S.SourceFinancialRecord],
    expected_source_document_id: str,
) -> CommitRecordSetResult:
    """原子提交一个记录集合（A1 修订 6/7/8 + 逐记录公司归属 + 复用 current 处理）。

    单事务内：校验 → 归属链 → 逐记录公司归属 → 严格复用（隔离/完整性/current 切换）
    → 写 record_set → 写全部 records → 写后复核 → 原子切换 current → commit。
    任一步失败全部回滚，旧 current 不变，半成品不可见。
    """
    validator.validate_record_set(record_set)
    if not records:
        raise ValueError("records 不能为空")
    validator.validate_records(records, record_set.record_set_version)
    if record_set.record_count != len(records):
        raise validator.ValidationError(
            f"record_count 不一致: 声明 {record_set.record_count} != 实际 {len(records)}")

    conn = _get_conn()
    try:
        # 1. source_version 归属链（record_set 尚未写库）。
        sv = conn.execute(
            "SELECT source_document_id FROM financial_source_version WHERE source_version=?",
            (record_set.source_version,),
        ).fetchone()
        if sv is None:
            raise KeyError(f"source_version 不存在: {record_set.source_version}")
        if sv["source_document_id"] != expected_source_document_id:
            raise ValueError(
                f"source_version 不属于文档 {expected_source_document_id!r} "
                f"（属于 {sv['source_document_id']!r}）")
        _require_not_quarantined(conn, "financial_source_version", record_set.source_version)

        # 2. 逐记录公司归属：每条 record.company_id 必须等于权威 company_id。
        authoritative_company = _authoritative_company_id(conn, record_set.source_version)
        for r in records:
            if r.company_id != authoritative_company:
                raise validator.ValidationError(
                    f"record.company_id 与权威公司不符: {r.company_id!r} != {authoritative_company!r}")

        # 3. 严格复用：record_set_version 已存在 → 隔离/完整性/一致 + current 处理。
        existing = conn.execute(
            "SELECT * FROM financial_record_set WHERE record_set_version=?",
            (record_set.record_set_version,),
        ).fetchone()
        if existing is not None:
            result = _reuse_existing(conn, record_set, records, expected_source_document_id, existing)
        else:
            # 4. 写 record_set + 全部 records。
            _insert_record_set_conn(conn, record_set)
            for r in records:
                _insert_record_conn(conn, r)

            # 5. 写后完整性复核。
            actual = conn.execute(
                "SELECT COUNT(*) AS c FROM source_financial_record WHERE record_set_version=?",
                (record_set.record_set_version,),
            ).fetchone()["c"]
            if actual != len(records):
                raise RuntimeError(
                    f"来源记录写入校验失败：实际落库 {actual} 行 != 传入 {len(records)} 行")

            # 6. 原子切换 current。
            _set_current_record_set_conn(conn, expected_source_document_id, record_set.record_set_version)
            result = CommitRecordSetResult(
                record_set=record_set, reused=False, current_switched=True,
                record_count=len(records))

        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 读查询（公开）
# ---------------------------------------------------------------------------

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
# validity：追加事件 + 确定性读取 + 状态机（A1 修订 9）
# ---------------------------------------------------------------------------

_SNAPSHOT_VALIDITY_RANK = {"valid": 0, "stale": 1, "superseded": 2}
_RESOLUTION_VALIDITY_RANK = {"active": 0, "stale": 1, "superseded": 2}


def _append_validity_event(
    table: str,
    id_column: str,
    object_id: str,
    status: str,
    invalidated_by: str | None,
    invalidated_reason: str | None,
    statuses: list[str],
    rank: dict[str, int],
    initial_status: str,
) -> str | None:
    if status not in statuses:
        raise validator.ValidationError(f"status 非法: {status!r}")
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT status FROM {table} WHERE {id_column}=? ORDER BY event_at DESC, rowid DESC LIMIT 1",
            (object_id,),
        ).fetchone()
        if row is None:
            if status != initial_status:
                raise validator.ValidationError(
                    f"{table} 首条事件必须为 {initial_status!r}，收到 {status!r}")
        else:
            current = row["status"]
            if status == current:
                return None
            if rank[status] < rank[current]:
                raise validator.ValidationError(f"非法有效性倒退: {current} → {status}")
        event_id = "v-" + uuid.uuid4().hex[:16]
        conn.execute(
            f"INSERT INTO {table} (event_id, {id_column}, status, invalidated_by, invalidated_reason, event_at) "
            "VALUES (?,?,?,?,?,?)",
            (event_id, object_id, status, invalidated_by, invalidated_reason, _utcnow()),
        )
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _latest_validity(table: str, id_column: str, object_id: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT status FROM {table} WHERE {id_column}=? ORDER BY event_at DESC, rowid DESC LIMIT 1",
            (object_id,),
        ).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def record_snapshot_validity(snapshot_id: str, status: str, invalidated_by: str | None = None,
                             invalidated_reason: str | None = None) -> str | None:
    return _append_validity_event("snapshot_validity", "snapshot_id", snapshot_id, status,
                                  invalidated_by, invalidated_reason, S.SNAPSHOT_VALIDITY_STATUSES,
                                  _SNAPSHOT_VALIDITY_RANK, "valid")


def record_resolution_validity(resolution_id: str, status: str, invalidated_by: str | None = None,
                               invalidated_reason: str | None = None) -> str | None:
    return _append_validity_event("resolution_validity", "resolution_id", resolution_id, status,
                                  invalidated_by, invalidated_reason, S.RESOLUTION_VALIDITY_STATUSES,
                                  _RESOLUTION_VALIDITY_RANK, "active")


def latest_snapshot_validity(snapshot_id: str) -> str | None:
    return _latest_validity("snapshot_validity", "snapshot_id", snapshot_id)


def latest_resolution_validity(resolution_id: str) -> str | None:
    return _latest_validity("resolution_validity", "resolution_id", resolution_id)


# ---------------------------------------------------------------------------
# 隔离（quarantine）
# ---------------------------------------------------------------------------

def quarantine(object_type: str, object_id: str, reason: str) -> None:
    """把损坏对象记录到 quarantine（不修改被保护历史行）。"""
    if object_type not in S.QUARANTINE_OBJECT_TYPES:
        raise validator.ValidationError(f"quarantine object_type 非法: {object_type!r}")
    conn = _get_conn()
    try:
        _insert_quarantine_conn(conn, object_type, object_id, reason)
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
                "current_record_set": (
                    current.record_set_version
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
