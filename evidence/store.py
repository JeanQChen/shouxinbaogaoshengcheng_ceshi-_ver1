"""Evidence Store：SQLite 权威存储 + 原子提交 + 查询。

- 独立库 data/evidence.db，与 data/credit.db 分离。
- 表：documents（内容版本）/ evidence_sets（规则集合）/ evidence_blocks（权威证据）
  / evidence_references（引用守卫）/ progress_events / checkpoints（运行态）。
- commit_document 承担「文档 + 集合 + 块 + checkpoint」的同一事务切换，
  保证构建失败时绝不暴露半完成版本（任务书 §8）。
- 连接模式与 financial/db.py 一致：per-call connect/close，PRAGMA foreign_keys=ON。

删除策略（E1-04）：Phase 1 仅提供可删除性检查（check_delete）与停用
（deactivate_set），不物理删除；被引用版本永远拒绝删除。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evidence import schema as S
from evidence.ids import (
    derive_document_version,
    file_sha256,
)
from evidence.schema import (
    Checkpoint,
    CommitResult,
    DeleteCheckResult,
    DocumentContext,
    DocumentRecord,
    EvidenceBlock,
    EvidenceRef,
    ProgressEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/evidence.db")

# 模块级，由 init_db() 设置，后续操作复用本路径。
_db_path: Path | None = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Evidence DB not initialized. Call init_db() first.")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化 Evidence SQLite 数据库，创建全部表。"""
    global _db_path
    _db_path = Path(db_path)
    conn = _get_conn()
    try:
        conn.executescript(build_ddl())
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def build_ddl() -> str:
    """返回全部建表语句（幂等）。"""
    return """
CREATE TABLE IF NOT EXISTS documents (
    company_id            TEXT NOT NULL,
    document_id           TEXT NOT NULL,
    document_version      TEXT NOT NULL,
    source_name           TEXT NOT NULL,
    source_path           TEXT,
    source_type           TEXT NOT NULL,
    material_group        TEXT NOT NULL,
    file_sha256           TEXT NOT NULL,
    file_size             INTEGER NOT NULL,
    page_count            INTEGER,
    declared_company_name TEXT,
    detected_company_names TEXT,
    parser_version        TEXT NOT NULL,
    status                TEXT NOT NULL,
    quality_flags         TEXT,
    created_at            TEXT NOT NULL,
    PRIMARY KEY (company_id, document_id, document_version)
);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id, document_id);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(company_id, file_sha256);
-- 每份业务文档最多一个 current 内容版本。
CREATE UNIQUE INDEX IF NOT EXISTS uq_current_document
    ON documents(company_id, document_id) WHERE status = 'current';

CREATE TABLE IF NOT EXISTS evidence_sets (
    company_id           TEXT NOT NULL,
    document_id          TEXT NOT NULL,
    document_version     TEXT NOT NULL,
    evidence_set_version TEXT NOT NULL,
    dependency_versions  TEXT NOT NULL,
    status               TEXT NOT NULL,
    block_count          INTEGER,
    created_at           TEXT NOT NULL,
    PRIMARY KEY (company_id, document_id, document_version, evidence_set_version),
    FOREIGN KEY (company_id, document_id, document_version)
        REFERENCES documents(company_id, document_id, document_version)
);
-- 每个文档版本最多一个 current 证据集合。
CREATE UNIQUE INDEX IF NOT EXISTS uq_current_set
    ON evidence_sets(company_id, document_id, document_version) WHERE status = 'current';

CREATE TABLE IF NOT EXISTS evidence_blocks (
    evidence_id          TEXT PRIMARY KEY,
    schema_version       TEXT NOT NULL,
    company_id           TEXT NOT NULL,
    document_id          TEXT NOT NULL,
    document_version     TEXT NOT NULL,
    evidence_set_version TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    source_type          TEXT NOT NULL,
    source_uri           TEXT,
    page_number          INTEGER NOT NULL,
    block_index          INTEGER NOT NULL,
    section_path         TEXT,
    evidence_type        TEXT NOT NULL,
    text                 TEXT NOT NULL,
    structured_payload   TEXT,
    report_period        TEXT,
    published_at         TEXT,
    entities             TEXT,
    quality_flags        TEXT,
    content_hash         TEXT NOT NULL,
    builder_version      TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    UNIQUE (company_id, document_id, document_version, evidence_set_version, page_number, block_index),
    FOREIGN KEY (company_id, document_id, document_version, evidence_set_version)
        REFERENCES evidence_sets(company_id, document_id, document_version, evidence_set_version)
);
CREATE INDEX IF NOT EXISTS idx_blocks_doc ON evidence_blocks(company_id, document_id, document_version);
CREATE INDEX IF NOT EXISTS idx_blocks_page ON evidence_blocks(company_id, document_id, document_version, page_number);

CREATE TABLE IF NOT EXISTS evidence_references (
    reference_id   TEXT PRIMARY KEY,
    evidence_id    TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    referenced_by  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (evidence_id) REFERENCES evidence_blocks(evidence_id)
);
CREATE INDEX IF NOT EXISTS idx_references_evidence ON evidence_references(evidence_id);

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
CREATE INDEX IF NOT EXISTS idx_progress_run ON progress_events(run_id, created_at);

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
CREATE INDEX IF NOT EXISTS idx_checkpoint_run ON checkpoints(run_id, created_at);
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


def _row_to_document(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        document_id=row["document_id"],
        document_version=row["document_version"],
        company_id=row["company_id"],
        source_name=row["source_name"],
        source_path=row["source_path"],
        source_type=row["source_type"],
        material_group=row["material_group"],
        file_sha256=row["file_sha256"],
        file_size=row["file_size"],
        page_count=row["page_count"],
        declared_company_name=row["declared_company_name"],
        detected_company_names=_json_loads(row["detected_company_names"]) or [],
        parser_version=row["parser_version"],
        status=row["status"],
        quality_flags=_json_loads(row["quality_flags"]) or [],
        created_at=row["created_at"],
    )


def _row_to_evidence(row: sqlite3.Row) -> EvidenceBlock:
    return EvidenceBlock(
        evidence_id=row["evidence_id"],
        schema_version=row["schema_version"],
        company_id=row["company_id"],
        document_id=row["document_id"],
        document_version=row["document_version"],
        evidence_set_version=row["evidence_set_version"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        source_uri=row["source_uri"],
        page_number=row["page_number"],
        block_index=row["block_index"],
        section_path=_json_loads(row["section_path"]) or [],
        evidence_type=row["evidence_type"],
        text=row["text"],
        structured_payload=_json_loads(row["structured_payload"]),
        report_period=row["report_period"],
        published_at=row["published_at"],
        entities=_json_loads(row["entities"]) or [],
        quality_flags=_json_loads(row["quality_flags"]) or [],
        content_hash=row["content_hash"],
        builder_version=row["builder_version"],
        created_at=row["created_at"],
    )


def _row_to_progress(row: sqlite3.Row) -> ProgressEvent:
    return ProgressEvent(
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


def _row_to_checkpoint(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=row["checkpoint_id"],
        run_id=row["run_id"],
        stage_id=row["stage_id"],
        state_version=row["state_version"],
        artifact_refs=_json_loads(row["artifact_refs"]) or [],
        input_hashes=_json_loads(row["input_hashes"]) or {},
        dependency_versions=_json_loads(row["dependency_versions"]) or {},
        resolution_refs=_json_loads(row["resolution_refs"]) or [],
        completed_unit_ids=_json_loads(row["completed_unit_ids"]) or [],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Document Registry
# ---------------------------------------------------------------------------

def _auto_document_id(company_id: str, sha256: str, conn: sqlite3.Connection) -> str:
    """自动生成稳定 document_id（E1-03）。

    先按内容哈希识别复用同一业务文档；无法可靠判断时（不同内容）不合并，
    生成新的 document_id。文件名不作为唯一身份。
    """
    row = conn.execute(
        "SELECT document_id FROM documents WHERE company_id=? AND file_sha256=? "
        "ORDER BY created_at LIMIT 1",
        (company_id, sha256),
    ).fetchone()
    if row is not None:
        return row["document_id"]
    return "doc-" + uuid.uuid4().hex[:16]


def register_document(file_path: str, context: DocumentContext) -> DocumentRecord:
    """登记一份文档版本（不改变 current，不做 Evidence 构建）。

    幂等：同一内容（同 sha256）再次登记返回现有记录，不改变任何状态。
    文件名相同但内容变化会生成新 document_version。
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    sha = file_sha256(str(p))
    doc_version = derive_document_version(sha)
    now = _utcnow()

    conn = _get_conn()
    try:
        document_id = context.document_id
        if document_id is None:
            document_id = _auto_document_id(context.company_id, sha, conn)

        existing = conn.execute(
            "SELECT * FROM documents WHERE company_id=? AND document_id=? AND document_version=?",
            (context.company_id, document_id, doc_version),
        ).fetchone()
        if existing is not None:
            return _row_to_document(existing)

        conn.execute(
            "INSERT INTO documents (company_id, document_id, document_version, source_name, "
            "source_path, source_type, material_group, file_sha256, file_size, page_count, "
            "declared_company_name, detected_company_names, parser_version, status, quality_flags, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                context.company_id, document_id, doc_version, context.source_name,
                context.source_path or str(p), context.source_type, context.material_group,
                sha, p.stat().st_size, None, context.declared_company_name,
                _json_dumps(context.detected_company_names), S.PARSER_VERSION,
                "registered", _json_dumps([]), now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM documents WHERE company_id=? AND document_id=? AND document_version=?",
            (context.company_id, document_id, doc_version),
        ).fetchone()
        return _row_to_document(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# commit_document（原子编排）
# ---------------------------------------------------------------------------

def _load_document(conn, company_id, document_id, document_version) -> DocumentRecord:
    row = conn.execute(
        "SELECT * FROM documents WHERE company_id=? AND document_id=? AND document_version=?",
        (company_id, document_id, document_version),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"文档记录不存在: {(company_id, document_id, document_version)}")
    return _row_to_document(row)


def _insert_checkpoint_row(
    conn,
    checkpoint_id: str,
    run_id: str,
    stage_id: str,
    artifact_refs: list[str],
    input_hashes: dict[str, str],
    dependency_versions: dict[str, str],
    completed_unit_ids: list[str],
) -> None:
    conn.execute(
        "INSERT INTO checkpoints (checkpoint_id, run_id, stage_id, state_version, artifact_refs, "
        "input_hashes, dependency_versions, resolution_refs, completed_unit_ids, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            checkpoint_id, run_id, stage_id, 1,
            _json_dumps(artifact_refs), _json_dumps(input_hashes),
            _json_dumps(dependency_versions), _json_dumps([]),
            _json_dumps(completed_unit_ids), _utcnow(),
        ),
    )


def _insert_block(conn, block: EvidenceBlock) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO evidence_blocks (evidence_id, schema_version, company_id, document_id, "
        "document_version, evidence_set_version, source_name, source_type, source_uri, page_number, "
        "block_index, section_path, evidence_type, text, structured_payload, report_period, published_at, "
        "entities, quality_flags, content_hash, builder_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            block.evidence_id, block.schema_version, block.company_id, block.document_id,
            block.document_version, block.evidence_set_version, block.source_name, block.source_type,
            block.source_uri, block.page_number, block.block_index, _json_dumps(block.section_path),
            block.evidence_type, block.text, _json_dumps(block.structured_payload),
            block.report_period, block.published_at, _json_dumps(block.entities),
            _json_dumps(block.quality_flags), block.content_hash, block.builder_version,
            block.created_at,
        ),
    )


def commit_document(
    document: DocumentRecord,
    blocks: list[EvidenceBlock],
    evidence_set_version: str,
    run_id: str,
    input_hashes: dict[str, str],
    dependency_versions: dict[str, str],
) -> CommitResult:
    """原子提交一份文档版本的全部 Evidence（任务书 §8 / v3-final 三路径）。

    路径 A（全新构建）：单事务插入集合 + 块 + checkpoint，然后旧 current
      → retired/superseded、新 registered/building → current。
    路径 B（幂等复用）：目标集合已 current，不重写 Evidence，为本次新 run
      写入独立 checkpoint（reused=块数 / written=0）。
    路径 C（重新激活）：目标集合为 retired，先做依赖版本 + 完整性（块数 +
      content_hash）校验，再在同一事务内重新激活。
    """
    company_id = document.company_id
    document_id = document.document_id
    document_version = document.document_version
    key = (company_id, document_id, document_version, evidence_set_version)

    conn = _get_conn()
    try:
        set_row = conn.execute(
            "SELECT status, block_count, dependency_versions FROM evidence_sets "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            key,
        ).fetchone()

        if set_row is not None and set_row["status"] == "current":
            return _reuse_existing(conn, document, evidence_set_version, run_id, key, set_row)
        if set_row is not None and set_row["status"] == "retired":
            return _reactivate(conn, document, blocks, evidence_set_version, run_id,
                               input_hashes, dependency_versions, key, set_row)
        if set_row is not None and set_row["status"] == "building":
            raise RuntimeError(f"evidence_sets 存在 building 残留（上一次事务未收尾）: {key}")

        return _commit_fresh(conn, document, blocks, evidence_set_version, run_id,
                             input_hashes, dependency_versions, key)
    finally:
        conn.close()


def _commit_fresh(conn, document, blocks, set_version, run_id, input_hashes, dependency_versions, key):
    company_id, document_id, document_version, _ = key
    now = _utcnow()
    evidence_ids = [b.evidence_id for b in blocks]
    checkpoint_id = "ckpt-" + uuid.uuid4().hex[:16]

    try:
        conn.execute(
            "INSERT INTO evidence_sets (company_id, document_id, document_version, evidence_set_version, "
            "dependency_versions, status, block_count, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (company_id, document_id, document_version, set_version,
             _json_dumps(dependency_versions), "building", len(blocks), now),
        )
        for b in blocks:
            _insert_block(conn, b)
        # checkpoint 与产物在同一事务内，仅在持久化成功后存在。
        _insert_checkpoint_row(conn, checkpoint_id, run_id, "PERSISTING_EVIDENCE",
                               evidence_ids, input_hashes, dependency_versions, evidence_ids)

        # 切换：先降级旧 current，再提升新 current（满足部分唯一索引）。
        conn.execute(
            "UPDATE documents SET status='superseded' "
            "WHERE company_id=? AND document_id=? AND status='current' AND document_version != ?",
            (company_id, document_id, document_version),
        )
        conn.execute(
            "UPDATE documents SET status='current', page_count=? "
            "WHERE company_id=? AND document_id=? AND document_version=?",
            (document.page_count, company_id, document_id, document_version),
        )
        conn.execute(
            "UPDATE evidence_sets SET status='retired' "
            "WHERE company_id=? AND document_id=? AND document_version=? AND status='current' "
            "AND evidence_set_version != ?",
            (company_id, document_id, document_version, set_version),
        )
        conn.execute(
            "UPDATE evidence_sets SET status='current' "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            key,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    doc = _load_document(conn, company_id, document_id, document_version)
    return CommitResult(document=doc, evidence_set_version=set_version,
                        written=len(blocks), reused=0,
                        checkpoint_id=checkpoint_id, evidence_ids=evidence_ids)


def _reuse_existing(conn, document, set_version, run_id, key, set_row):
    """路径 B：目标集合已 current，幂等复用，不重写 Evidence。"""
    company_id, document_id, document_version, _ = key
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM evidence_blocks "
        "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
        key,
    ).fetchone()["c"]
    checkpoint_id = "ckpt-" + uuid.uuid4().hex[:16]
    try:
        _insert_checkpoint_row(
            conn, checkpoint_id, run_id, "PERSISTING_EVIDENCE", [],
            {"file_sha256": document.file_sha256},
            _json_loads(set_row["dependency_versions"]) or {},
            [],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return CommitResult(document=_load_document(conn, company_id, document_id, document_version),
                        evidence_set_version=set_version, written=0, reused=count,
                        checkpoint_id=checkpoint_id, evidence_ids=[])


def _reactivate(conn, document, blocks, set_version, run_id, input_hashes, dependency_versions, key, set_row):
    """路径 C：目标集合为 retired，校验通过后重新激活。"""
    company_id, document_id, document_version, _ = key
    # 完整性：块数 + content_hash。
    stored_hashes = {r["content_hash"] for r in conn.execute(
        "SELECT content_hash FROM evidence_blocks "
        "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
        key,
    ).fetchall()}
    if len(stored_hashes) != len(blocks):
        raise RuntimeError(
            f"重新激活失败：块数不匹配（存储 {len(stored_hashes)} vs 传入 {len(blocks)}）")
    for b in blocks:
        if b.content_hash not in stored_hashes:
            raise RuntimeError(f"重新激活失败：content_hash 不匹配 evidence_id={b.evidence_id}")
    # 依赖版本一致性。
    if (_json_loads(set_row["dependency_versions"]) or {}) != dependency_versions:
        raise RuntimeError("重新激活失败：依赖版本不兼容")

    checkpoint_id = "ckpt-" + uuid.uuid4().hex[:16]
    try:
        _insert_checkpoint_row(conn, checkpoint_id, run_id, "PERSISTING_EVIDENCE", [],
                               input_hashes, dependency_versions, [])
        conn.execute(
            "UPDATE evidence_sets SET status='retired' "
            "WHERE company_id=? AND document_id=? AND document_version=? AND status='current' "
            "AND evidence_set_version != ?",
            (company_id, document_id, document_version, set_version),
        )
        conn.execute(
            "UPDATE evidence_sets SET status='current' "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            key,
        )
        conn.execute(
            "UPDATE documents SET status='superseded' "
            "WHERE company_id=? AND document_id=? AND status='current' AND document_version != ?",
            (company_id, document_id, document_version),
        )
        conn.execute(
            "UPDATE documents SET status='current' "
            "WHERE company_id=? AND document_id=? AND document_version=?",
            (company_id, document_id, document_version),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return CommitResult(document=_load_document(conn, company_id, document_id, document_version),
                        evidence_set_version=set_version, written=0, reused=len(blocks),
                        checkpoint_id=checkpoint_id, evidence_ids=[])


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def get_document(company_id: str, document_id: str, document_version: str) -> DocumentRecord | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE company_id=? AND document_id=? AND document_version=?",
            (company_id, document_id, document_version),
        ).fetchone()
        return _row_to_document(row) if row else None
    finally:
        conn.close()


def list_documents(company_id: str) -> list[DocumentRecord]:
    """列出某公司的全部文档版本（含 registered/superseded/current）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM documents WHERE company_id=? ORDER BY document_id, created_at",
            (company_id,),
        ).fetchall()
        return [_row_to_document(r) for r in rows]
    finally:
        conn.close()


def current_document_version(company_id: str, document_id: str) -> str | None:
    """返回某业务文档当前的（已持久化 Evidence 的）内容版本。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT d.document_version FROM documents d "
            "JOIN evidence_sets es ON es.company_id=d.company_id AND es.document_id=d.document_id "
            "AND es.document_version=d.document_version AND es.status='current' "
            "WHERE d.company_id=? AND d.document_id=? AND d.status='current' LIMIT 1",
            (company_id, document_id),
        ).fetchone()
        return row["document_version"] if row else None
    finally:
        conn.close()


def current_evidence_set(company_id: str, document_id: str, document_version: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT evidence_set_version FROM evidence_sets "
            "WHERE company_id=? AND document_id=? AND document_version=? AND status='current' LIMIT 1",
            (company_id, document_id, document_version),
        ).fetchone()
        return row["evidence_set_version"] if row else None
    finally:
        conn.close()


def get_evidence(evidence_id: str) -> EvidenceBlock | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM evidence_blocks WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        return _row_to_evidence(row) if row else None
    finally:
        conn.close()


def list_document_evidence(
    company_id: str,
    document_id: str,
    document_version: str | None = None,
    evidence_set_version: str | None = None,
) -> list[EvidenceBlock]:
    """列出某文档版本的 Evidence；未指定版本时列出全部版本。"""
    conn = _get_conn()
    try:
        sql = ("SELECT * FROM evidence_blocks WHERE company_id=? AND document_id=?")
        params: list = [company_id, document_id]
        if document_version is not None:
            sql += " AND document_version=?"
            params.append(document_version)
        if evidence_set_version is not None:
            sql += " AND evidence_set_version=?"
            params.append(evidence_set_version)
        sql += " ORDER BY page_number, block_index"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_evidence(r) for r in rows]
    finally:
        conn.close()


def count_evidence(company_id: str, document_id: str, document_version: str, evidence_set_version: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_blocks "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            (company_id, document_id, document_version, evidence_set_version),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 引用守卫 / 删除检查 / 停用
# ---------------------------------------------------------------------------

def add_reference(reference_id: str, evidence_id: str, reference_type: str, referenced_by: str) -> None:
    """登记一条 Evidence 引用（供未来 Claim/Citation 及测试使用）。"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO evidence_references (reference_id, evidence_id, reference_type, referenced_by, created_at) "
            "VALUES (?,?,?,?,?)",
            (reference_id, evidence_id, reference_type, referenced_by, _utcnow()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_delete(evidence_id: str) -> DeleteCheckResult:
    """删除前检查（E1-04：Phase 1 只检查不物理删除）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM evidence_blocks WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        if row is None:
            return DeleteCheckResult(deletable=False, referenced=False, references=[],
                                     reason="Evidence 不存在")
        refs = [r["reference_id"] for r in conn.execute(
            "SELECT reference_id FROM evidence_references WHERE evidence_id=?", (evidence_id,)
        ).fetchall()]
        if refs:
            return DeleteCheckResult(deletable=False, referenced=True, references=refs,
                                     reason="被引用，拒绝删除")
        return DeleteCheckResult(deletable=False, referenced=False, references=[],
                                 reason="Phase 1 不提供物理删除（仅停用）")
    finally:
        conn.close()


def deactivate_set(company_id: str, document_id: str, document_version: str, evidence_set_version: str) -> None:
    """停用某证据集合（E1-04 允许的停用，非物理删除）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM evidence_sets "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            (company_id, document_id, document_version, evidence_set_version),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"证据集合不存在: {(company_id, document_id, document_version, evidence_set_version)}")
        conn.execute(
            "UPDATE evidence_sets SET status='retired' "
            "WHERE company_id=? AND document_id=? AND document_version=? AND evidence_set_version=?",
            (company_id, document_id, document_version, evidence_set_version),
        )
        if row["status"] == "current":
            # 停用 current 集合后，该文档版本不再作为默认可用版本。
            conn.execute(
                "UPDATE documents SET status='superseded' "
                "WHERE company_id=? AND document_id=? AND document_version=?",
                (company_id, document_id, document_version),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 进度事件 / checkpoint 持久化（progress.py 语义层复用）
# ---------------------------------------------------------------------------

def record_progress(event: ProgressEvent) -> None:
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


def latest_progress(run_id: str) -> ProgressEvent | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM progress_events WHERE run_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return _row_to_progress(row) if row else None
    finally:
        conn.close()


def history_progress(run_id: str) -> list[ProgressEvent]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM progress_events WHERE run_id=? ORDER BY created_at ASC, rowid ASC",
            (run_id,),
        ).fetchall()
        return [_row_to_progress(r) for r in rows]
    finally:
        conn.close()


def latest_checkpoint(run_id: str) -> Checkpoint | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return _row_to_checkpoint(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_inspect(company_id: str, document_id: str | None) -> dict:
    docs = list_documents(company_id)
    if document_id is not None:
        docs = [d for d in docs if d.document_id == document_id]
    result = {"company_id": company_id, "documents": []}
    for d in docs:
        entry = {
            "document_id": d.document_id,
            "document_version": d.document_version,
            "status": d.status,
            "source_name": d.source_name,
            "source_type": d.source_type,
            "material_group": d.material_group,
            "file_sha256": d.file_sha256[:16],
            "page_count": d.page_count,
            "current_set": current_evidence_set(d.company_id, d.document_id, d.document_version),
        }
        current = current_evidence_set(d.company_id, d.document_id, d.document_version)
        if current is not None:
            entry["block_count"] = count_evidence(
                d.company_id, d.document_id, d.document_version, current)
        result["documents"].append(entry)
    return result


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m evidence.store",
                                     description="Evidence Store CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inspect = sub.add_parser("inspect", help="查看某公司的文档与 Evidence 概览")
    p_inspect.add_argument("--company", required=True, help="公司标识（company_id）")
    p_inspect.add_argument("--document-id", required=False, default=None)

    args = parser.parse_args(argv)
    init_db()
    if args.cmd == "inspect":
        print(json.dumps(_cli_inspect(args.company, args.document_id),
                         ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
