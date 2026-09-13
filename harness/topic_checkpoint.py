"""R1-B：TopicResearchPack 只读 checkpoint 加载（供 R3 调度器恢复）。

- 只读：绝不调用 init_topic_store（不建库、不写库、不迁移），只做 SELECT；
- 目标库不存在 → 返回 None / 空列表（不创建文件）；
- 复用 topic_store 的重建逻辑（_row_to_pack），但不暴露任何写接口。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness import topic_schema as TS
from harness import topic_store as Store


@dataclass(frozen=True)
class TopicCheckpoint:
    """一个只读 checkpoint 视图：身份 + 完整 Pack + 读取时间。"""

    identity: TS.PackIdentity
    pack: TS.TopicResearchPack
    loaded_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _readonly_conn(db_path: Path) -> sqlite3.Connection | None:
    """打开只读连接；库不存在返回 None（绝不创建）。"""
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _to_checkpoint(conn: sqlite3.Connection, row: sqlite3.Row) -> TopicCheckpoint:
    pack = Store._row_to_pack(conn, row)
    return TopicCheckpoint(identity=pack.identity(), pack=pack, loaded_at=_utcnow())


def load_checkpoint(identity: TS.PackIdentity,
                    db_path: str | Path = Store.DEFAULT_DB_PATH) -> TopicCheckpoint | None:
    """读取当前 Pack 作为恢复 checkpoint（只读；库不存在 → None）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return None
    try:
        k = identity.key()
        row = conn.execute(
            "SELECT p.* FROM topic_current c JOIN topic_pack p ON p.pack_id = c.pack_id "
            "WHERE c.task_id=? AND c.company_id=? AND c.report_as_of=? AND c.contract_fingerprint=? "
            "AND c.source_policy_version=? AND c.section_id=? AND c.topic_id=?",
            k,
        ).fetchone()
        return _to_checkpoint(conn, row) if row else None
    finally:
        conn.close()


def load_checkpoint_by_pack_id(pack_id: str,
                               db_path: str | Path = Store.DEFAULT_DB_PATH) -> TopicCheckpoint | None:
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM topic_pack WHERE pack_id=?", (pack_id,)).fetchone()
        return _to_checkpoint(conn, row) if row else None
    finally:
        conn.close()


def list_checkpoints(db_path: str | Path = Store.DEFAULT_DB_PATH) -> list[TopicCheckpoint]:
    """列出所有 current Pack checkpoint（只读）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT p.* FROM topic_current c JOIN topic_pack p ON p.pack_id = c.pack_id "
            "ORDER BY c.task_id, c.company_id, c.topic_id").fetchall()
        return [_to_checkpoint(conn, r) for r in rows]
    finally:
        conn.close()


def load_history(identity: TS.PackIdentity,
                 db_path: str | Path = Store.DEFAULT_DB_PATH) -> list[TopicCheckpoint]:
    """列出某身份的全部历史 Pack checkpoint（含非 current，只读）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM topic_pack WHERE task_id=? AND company_id=? AND "
            "COALESCE(report_as_of,'')=? AND contract_fingerprint=? AND "
            "source_policy_version=? AND section_id=? AND topic_id=? ORDER BY rowid",
            (identity.task_id, identity.company_id, identity.report_as_of or "",
             identity.contract_fingerprint, identity.source_policy_version,
             identity.section_id, identity.topic_id),
        ).fetchall()
        return [_to_checkpoint(conn, r) for r in rows]
    finally:
        conn.close()


def verify_dependency_fingerprint(pack: TS.TopicResearchPack,
                                  expected_dependency_fingerprint: str) -> bool:
    """只读校验：Pack 的 dependency_fingerprint 是否等于当前运行环境期望指纹。

    - 相等 → 可安全重放/消费；
    - 不等 → stale（消费方据此追加 stale|invalidated|quarantined 事件，不进入 writer
      消费、不自动升级）。dependency_fingerprint 已复合 contract_fingerprint /
      source_policy_version / dependency_versions（见 TS.compute_dependency_fingerprint），
      故 contract_fingerprint 变化也会使本校验不通过。
    - 本函数只读：不 init、不建库、不写库、不迁移、不追加事件。
    """
    if not expected_dependency_fingerprint:
        raise ValueError("expected_dependency_fingerprint 必须非空")
    return pack.dependency_fingerprint == expected_dependency_fingerprint
