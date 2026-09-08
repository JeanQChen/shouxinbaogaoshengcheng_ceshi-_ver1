"""外部来源快照 Store：SQLite 追加存储 + 不可变快照 + 幂等复用。

- 独立库 data/external_sources.db（与 evidence/financial 分离）。
- 表：source_snapshots（不可变；content_hash 相同 → 复用，内容变化 → 新版本，
  历史快照永不覆盖）。
- 连接模式与 evidence/store.py 一致：per-call connect/close，WAL + foreign_keys=OFF
  （无跨表外键），追加迁移（CREATE TABLE IF NOT EXISTS）。
- 正文（content_text）属于审计数据，不复制进 LLM 日志；API key/cookie/Authorization
  永不落盘。

CLI: python -m external_v2.store inspect --company 300750
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from external_v2 import schema as S

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/external_sources.db")

_db_path: Path | None = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("External Sources DB not initialized. Call init_db() first.")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化外部来源快照 SQLite 数据库。"""
    global _db_path
    _db_path = Path(db_path)
    conn = _get_conn()
    try:
        conn.executescript(build_ddl())
        conn.commit()
    finally:
        conn.close()


def build_ddl() -> str:
    return """
CREATE TABLE IF NOT EXISTS source_snapshots (
    source_snapshot_id  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL,
    canonical_url       TEXT NOT NULL,
    original_url        TEXT NOT NULL,
    provider            TEXT NOT NULL,
    query               TEXT NOT NULL,
    title               TEXT,
    snippet             TEXT,
    published_at        TEXT,
    fetched_at          TEXT NOT NULL,
    content_type        TEXT,
    http_status         INTEGER,
    content_text        TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    source_grade        TEXT,
    content_version     INTEGER NOT NULL,
    status              TEXT NOT NULL,
    error_code          TEXT,
    retrieval_metadata  TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_company
    ON source_snapshots(company_id, created_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_url
    ON source_snapshots(company_id, canonical_url, content_version);
-- 相同 (company, canonical_url, content_hash) 幂等复用：重复内容只保留一条。
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_content
    ON source_snapshots(company_id, canonical_url, content_hash);
"""


def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if obj else "{}"


def _json_loads(s: str | None):
    if not s:
        return {}
    return json.loads(s)


def _row_to_snapshot(row: sqlite3.Row) -> S.ExternalSourceSnapshot:
    return S.ExternalSourceSnapshot(
        source_snapshot_id=row["source_snapshot_id"],
        canonical_url=row["canonical_url"],
        original_url=row["original_url"],
        provider=row["provider"],
        query=row["query"],
        title=row["title"] or "",
        snippet=row["snippet"] or "",
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        content_type=row["content_type"],
        http_status=row["http_status"],
        content_text=row["content_text"],
        content_hash=row["content_hash"],
        source_grade=row["source_grade"],
        status=row["status"],
        error_code=row["error_code"],
        retrieval_metadata=_json_loads(row["retrieval_metadata"]),
        company_id=row["company_id"],
        content_version=row["content_version"],
    )


def store_snapshot(snap: S.ExternalSourceSnapshot, company_id: str) -> S.ExternalSourceSnapshot:
    """去重后落库，返回带最终 source_snapshot_id 的不可变快照。

    相同 (company_id, canonical_url, content_hash) → 复用既有快照（不新写）；
    内容变化（不同 content_hash）→ 新 content_version 的新快照；历史永不覆盖。
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM source_snapshots "
            "WHERE company_id=? AND canonical_url=? AND content_hash=?",
            (company_id, snap.canonical_url, snap.content_hash),
        ).fetchone()
        if row is not None:
            return _row_to_snapshot(row)

        ver_row = conn.execute(
            "SELECT COALESCE(MAX(content_version), 0) AS v FROM source_snapshots "
            "WHERE company_id=? AND canonical_url=?",
            (company_id, snap.canonical_url),
        ).fetchone()
        content_version = int(ver_row["v"]) + 1
        sid = "ext-" + uuid.uuid4().hex[:20]
        created_at = _utcnow()

        conn.execute(
            "INSERT INTO source_snapshots (source_snapshot_id, company_id, canonical_url, "
            "original_url, provider, query, title, snippet, published_at, fetched_at, "
            "content_type, http_status, content_text, content_hash, source_grade, "
            "content_version, status, error_code, retrieval_metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid, company_id, snap.canonical_url, snap.original_url, snap.provider,
                snap.query, snap.title, snap.snippet, snap.published_at, snap.fetched_at,
                snap.content_type, snap.http_status, snap.content_text, snap.content_hash,
                snap.source_grade, content_version, snap.status, snap.error_code,
                _json_dumps(snap.retrieval_metadata), created_at,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM source_snapshots WHERE source_snapshot_id=?", (sid,)
        ).fetchone()
        return _row_to_snapshot(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_snapshot(source_snapshot_id: str) -> S.ExternalSourceSnapshot | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM source_snapshots WHERE source_snapshot_id=?",
            (source_snapshot_id,),
        ).fetchone()
        return _row_to_snapshot(row) if row else None
    finally:
        conn.close()


def list_snapshots(company_id: str) -> list[S.ExternalSourceSnapshot]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM source_snapshots WHERE company_id=? "
            "ORDER BY created_at DESC, rowid DESC",
            (company_id,),
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]
    finally:
        conn.close()


def latest_snapshot(company_id: str, canonical_url: str) -> S.ExternalSourceSnapshot | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM source_snapshots "
            "WHERE company_id=? AND canonical_url=? "
            "ORDER BY content_version DESC, rowid DESC LIMIT 1",
            (company_id, canonical_url),
        ).fetchone()
        return _row_to_snapshot(row) if row else None
    finally:
        conn.close()


def find_by_content_hash(content_hash: str) -> list[S.ExternalSourceSnapshot]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM source_snapshots WHERE content_hash=? ORDER BY created_at DESC",
            (content_hash,),
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]
    finally:
        conn.close()


def _snapshot_summary(s: S.ExternalSourceSnapshot) -> dict:
    return {
        "source_snapshot_id": s.source_snapshot_id,
        "content_version": s.content_version,
        "canonical_url": s.canonical_url,
        "provider": s.provider,
        "title": s.title,
        "source_grade": s.source_grade,
        "published_at": s.published_at,
        "fetched_at": s.fetched_at,
        "status": s.status,
        "error_code": s.error_code,
        "content_hash": s.content_hash[:16],
        "content_length": len(s.content_text),
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m external_v2.store")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_inspect = sub.add_parser("inspect", help="查看某公司的外部来源快照概览")
    p_inspect.add_argument("--company", required=True)

    args = parser.parse_args(argv)
    init_db()
    if args.cmd == "inspect":
        snaps = list_snapshots(args.company)
        print(json.dumps({
            "company_id": args.company,
            "count": len(snaps),
            "snapshots": [_snapshot_summary(s) for s in snaps],
        }, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
