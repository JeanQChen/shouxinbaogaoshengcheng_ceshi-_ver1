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
from harness._readonly_sqlite import open_readonly_conn


@dataclass(frozen=True)
class TopicCheckpoint:
    """一个只读 checkpoint 视图：身份 + 完整 Pack + 读取时间。"""

    identity: TS.PackIdentity
    pack: TS.TopicResearchPack
    loaded_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _readonly_conn(db_path: Path) -> sqlite3.Connection | None:
    """打开严格只读连接（mode=ro + PRAGMA query_only=ON）；库不存在返回 None（绝不创建）。"""
    return open_readonly_conn(db_path)


def _checkpoint_from(conn: sqlite3.Connection, pack_id: str) -> TopicCheckpoint:
    """从已验证 pack_id 构造 checkpoint（走 Store 的完整性复核，损坏 fail-closed）。"""
    pack = Store._verify_reconstructed(conn, pack_id)
    return TopicCheckpoint(identity=pack.identity(), pack=pack, loaded_at=_utcnow())


def load_checkpoint(identity: TS.PackIdentity,
                    db_path: str | Path = Store.DEFAULT_DB_PATH) -> TopicCheckpoint | None:
    """读取当前 Pack 作为恢复 checkpoint（只读；库不存在 → None）。

    fail-closed：current 指向的 Pack 最新事件为 stale|invalidated|quarantined → 不返回
    （失效 Pack 不可作为可恢复 current）；行/子行/指纹损坏 → StorageCorruptionError。
    """
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return None
    try:
        k = identity.key()
        row = conn.execute(
            "SELECT pack_id FROM topic_current WHERE task_id=? AND company_id=? AND "
            "report_as_of=? AND contract_fingerprint=? AND source_policy_version=? "
            "AND section_id=? AND topic_id=?", k).fetchone()
        if row is None:
            return None
        pack_id = row["pack_id"]
        if Store._terminal_invalidation_conn(conn, pack_id) is not None:
            return None
        return _checkpoint_from(conn, pack_id)
    finally:
        conn.close()


def load_checkpoint_by_pack_id(pack_id: str,
                               db_path: str | Path = Store.DEFAULT_DB_PATH) -> TopicCheckpoint | None:
    """按 pack_id 读任意历史 Pack（显式历史读，含失效；损坏 fail-closed）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT 1 FROM topic_pack WHERE pack_id=?", (pack_id,)).fetchone()
        if row is None:
            return None
        return _checkpoint_from(conn, pack_id)
    finally:
        conn.close()


def list_checkpoints(db_path: str | Path = Store.DEFAULT_DB_PATH) -> list[TopicCheckpoint]:
    """列出所有「可用」current Pack checkpoint（跳过失效 current；损坏 fail-closed）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT pack_id FROM topic_current ORDER BY task_id, company_id, topic_id").fetchall()
        out: list[TopicCheckpoint] = []
        for r in rows:
            pack_id = r["pack_id"]
            if Store._terminal_invalidation_conn(conn, pack_id) is not None:
                continue
            out.append(_checkpoint_from(conn, pack_id))
        return out
    finally:
        conn.close()


def load_history(identity: TS.PackIdentity,
                 db_path: str | Path = Store.DEFAULT_DB_PATH) -> list[TopicCheckpoint]:
    """列出某身份的全部历史 Pack checkpoint（含非 current/失效，只读；损坏 fail-closed）。"""
    conn = _readonly_conn(Path(db_path))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT pack_id FROM topic_pack WHERE task_id=? AND company_id=? AND "
            "COALESCE(report_as_of,'')=? AND contract_fingerprint=? AND "
            "source_policy_version=? AND section_id=? AND topic_id=? ORDER BY rowid",
            (identity.task_id, identity.company_id, identity.report_as_of or "",
             identity.contract_fingerprint, identity.source_policy_version,
             identity.section_id, identity.topic_id),
        ).fetchall()
        return [_checkpoint_from(conn, r["pack_id"]) for r in rows]
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
