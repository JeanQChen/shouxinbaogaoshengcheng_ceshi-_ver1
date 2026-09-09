"""Phase 3 Batch B checkpoint：RunManifest + 题目级完成态（SQLite 持久化）。

本批次实现（编码前计划 §11 / 修订二）：
- 每次 run 保存不可变 RunManifest（dataset/company/契约/路由/提示词/模型/预算/证据/快照/
  外部策略/代码指纹）；
- 每题完成时落盘题目级 outcome（status / completion_status / stop_reason / success + 全量 JSON）；
- `--resume-run-id` 先比较当前输入与原 RunManifest：完全兼容只跳过已终态提交的题，
  任一关键字段不同 fail-closed 拒绝 resume。

本批次不实现（Batch C）：单题中途恢复、WAITING_HUMAN 后继续、跨题总预算恢复、
细粒度对象失效传播。

CLI: python -m harness.checkpoint --db <tmp> --self-check
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from harness import schema as H

DEFAULT_DB_PATH = Path("data/harness.db")

_db_path: Path | None = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# RunManifest（不可变）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunManifest:
    """一次 run 的不可变输入指纹（用于 resume 兼容性校验，fail-closed）。"""

    run_id: str
    dataset_sha256: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    router_fingerprint: str
    prompt_versions: dict
    model: str
    budget: dict
    evidence_fingerprint: str
    snapshot_id: str | None
    external_policy_version: str
    harness_fingerprint: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def manifest_from_dict(d: dict) -> RunManifest:
    return RunManifest(**d)


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """初始化 harness SQLite（追加建表，不破坏历史）。"""
    global _db_path
    _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS run_manifest (
                run_id TEXT PRIMARY KEY,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_outcome (
                run_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                status TEXT NOT NULL,
                completion_status TEXT NOT NULL,
                stop_reason TEXT,
                success INTEGER NOT NULL,
                outcome_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, question_id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("harness checkpoint DB 未初始化：先调用 init_db()")
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _set_db_path_for_test(db_path: Path | str) -> None:
    """测试专用：直接覆盖 db_path（不经 init_db 的建表副作用）。"""
    global _db_path
    _db_path = Path(db_path)


# ---------------------------------------------------------------------------
# RunManifest 读写
# ---------------------------------------------------------------------------

def write_run_manifest(manifest: RunManifest, db_path: Path | str | None = None) -> None:
    if db_path is not None:
        init_db(db_path)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_manifest (run_id, manifest_json, created_at) "
            "VALUES (?, ?, ?)",
            (manifest.run_id, json.dumps(manifest.as_dict(), ensure_ascii=False), _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def get_run_manifest(run_id: str, db_path: Path | str | None = None) -> RunManifest | None:
    if db_path is not None:
        init_db(db_path)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT manifest_json FROM run_manifest WHERE run_id=?", (run_id,)
        ).fetchone()
        return manifest_from_dict(json.loads(row["manifest_json"])) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 题目级 outcome
# ---------------------------------------------------------------------------

def _outcome_to_dict(outcome: H.ResearchOutcome) -> dict:
    state = dataclasses.asdict(outcome.state)
    answer = dataclasses.asdict(outcome.answer) if outcome.answer is not None else None
    return {
        "state": state,
        "answer": answer,
        "success": outcome.success,
        "completion_status": outcome.completion_status,
        "stop_reason": outcome.stop_reason,
    }


def _json_default(o):
    """JSON 序列化兜底：Decimal → str（财务金额/指标值），其余类型仍 fail-loud。"""
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def write_question_outcome(run_id: str, outcome: H.ResearchOutcome,
                           db_path: Path | str | None = None) -> None:
    """落盘单题终态 outcome（幂等覆盖同 run+question）。"""
    if db_path is not None:
        init_db(db_path)
    st = outcome.state
    payload = json.dumps(_outcome_to_dict(outcome), ensure_ascii=False,
                         default=_json_default)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO question_outcome "
            "(run_id, question_id, status, completion_status, stop_reason, success, "
            " outcome_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, st.question_id, st.status, outcome.completion_status,
             outcome.stop_reason, 1 if outcome.success else 0, payload, _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


# 终态集合：这些状态的题可被 resume 跳过。
_TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_GAPS", "BLOCKED", "FAILED", "WAITING_HUMAN"}


def list_completed_questions(run_id: str, db_path: Path | str | None = None) -> set[str]:
    """返回该 run 已到终态（可跳过）的 question_id 集合。"""
    if db_path is not None:
        init_db(db_path)
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT question_id, status FROM question_outcome WHERE run_id=?", (run_id,)
        ).fetchall()
        return {r["question_id"] for r in rows if r["status"] in _TERMINAL_STATUSES}
    finally:
        conn.close()


def load_outcome(run_id: str, question_id: str,
                 db_path: Path | str | None = None) -> dict | None:
    """读取单题 outcome 的 JSON dict（审计/预览用；不重建对象）。"""
    if db_path is not None:
        init_db(db_path)
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT outcome_json FROM question_outcome WHERE run_id=? AND question_id=?",
            (run_id, question_id),
        ).fetchone()
        return json.loads(row["outcome_json"]) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import sys
    import tempfile

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.checkpoint", description="checkpoint 自检")
    parser.add_argument("--db", default=None, help="SQLite 库路径（默认临时库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        db = args.db or str(Path(tempfile.mkdtemp()) / "harness.db")
        init_db(db)
        m = RunManifest(
            run_id="self", dataset_sha256="x", company_id="300750", report_as_of=None,
            contract_version="v1", router_fingerprint="x", prompt_versions={},
            model="deepseek-v4-pro", budget={}, evidence_fingerprint="x", snapshot_id=None,
            external_policy_version="v1", harness_fingerprint="x")
        write_run_manifest(m, db)
        back = get_run_manifest("self", db)
        print(json.dumps({
            "db": db,
            "manifest_roundtrip_ok": back is not None and back.run_id == "self",
            "completed": sorted(list_completed_questions("self", db)),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
