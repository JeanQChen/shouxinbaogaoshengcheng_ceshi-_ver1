"""R1-B：TopicResearchPack Store 只读 CLI。

只读：不写库、不建库、不迁移、不 commit。仅 --self-check（结构/migration 一致性）与
读视图（--list / --pack / --current）。输出 JSON 摘要（非完整 Pack），供人工检查与
离线测试。

用法:
    python -m harness.topic_store_cli --self-check [--db path]
    python -m harness.topic_store_cli --list [--db path]
    python -m harness.topic_store_cli --pack <pack_id> [--db path]
    python -m harness.topic_store_cli --current --task-id T --company-id C \
        --contract-fingerprint F --source-policy-version V --section-id S --topic-id U \
        [--report-as-of D] [--db path]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from harness import topic_checkpoint as Checkpoint
from harness import topic_schema as TS
from harness import topic_store as Store


def _readonly_conn(db_path: Path) -> sqlite3.Connection | None:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _pack_summary(pack: TS.TopicResearchPack) -> dict:
    return {
        "pack_id": pack.pack_id,
        "schema_version": pack.schema_version,
        "topic_id": pack.topic_id,
        "section_id": pack.section_id,
        "task_id": pack.task_id,
        "company_id": pack.company_id,
        "process_status": pack.process_status.status,
        "coverage_status": pack.coverage_status.status,
        "covered_aspects": list(pack.coverage_status.covered_aspect_ids),
        "gap_aspects": list(pack.coverage_status.gap_aspect_ids),
        "aspect_count": len(pack.aspect_results),
        "material_count": len(pack.materials),
        "fact_count": len(pack.facts),
        "conflict_count": len(pack.conflicts),
        "unresolved_count": len(pack.unresolved),
    }


def self_check_cmd(db: str) -> dict:
    db_path = Path(db)
    conn = _readonly_conn(db_path)
    if conn is None:
        return {"ok": False, "detail": f"DB 不存在（只读不建库）: {db_path}"}
    try:
        Store._verify_structure_matches_latest(conn)
        return {"ok": True, "schema_version": Store._latest_applied_version(conn),
                "detail": "topic store structure matches latest"}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


def list_cmd(db: str) -> dict:
    cps = Checkpoint.list_checkpoints(db)
    return {"ok": True, "count": len(cps),
            "packs": [_pack_summary(c.pack) for c in cps]}


def pack_cmd(db: str, pack_id: str) -> dict:
    cp = Checkpoint.load_checkpoint_by_pack_id(pack_id, db)
    if cp is None:
        return {"ok": False, "detail": f"pack 不存在: {pack_id}"}
    return {"ok": True, "pack": _pack_summary(cp.pack)}


def current_cmd(db: str, a: argparse.Namespace) -> dict:
    if not (a.task_id and a.company_id and a.contract_fingerprint
            and a.source_policy_version and a.section_id and a.topic_id):
        return {"ok": False, "detail": "缺 current 身份必填参数（task/company/contract-fingerprint/"
                                       "source-policy-version/section/topic）"}
    identity = TS.PackIdentity(
        task_id=a.task_id, company_id=a.company_id, report_as_of=a.report_as_of,
        contract_fingerprint=a.contract_fingerprint,
        source_policy_version=a.source_policy_version,
        section_id=a.section_id, topic_id=a.topic_id,
    )
    cp = Checkpoint.load_checkpoint(identity, db)
    if cp is None:
        return {"ok": False, "detail": "current pack 不存在"}
    return {"ok": True, "pack": _pack_summary(cp.pack)}


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="topic_store_cli",
                                description="R1-B TopicResearchPack Store 只读 CLI")
    p.add_argument("--db", default=str(Store.DEFAULT_DB_PATH))
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--pack", default=None, metavar="PACK_ID")
    p.add_argument("--current", action="store_true")
    p.add_argument("--task-id", default=None)
    p.add_argument("--company-id", default=None)
    p.add_argument("--report-as-of", default=None)
    p.add_argument("--contract-fingerprint", default=None)
    p.add_argument("--source-policy-version", default=None)
    p.add_argument("--section-id", default=None)
    p.add_argument("--topic-id", default=None)
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> dict:
    a = _parse(argv)
    if a.self_check:
        return self_check_cmd(a.db)
    if a.list:
        return list_cmd(a.db)
    if a.pack:
        return pack_cmd(a.db, a.pack)
    if a.current:
        return current_cmd(a.db, a)
    return {"ok": False, "detail": "未指定动作（--self-check / --list / --pack / --current）"}


def main(argv: list[str] | None = None) -> int:
    result = run(argv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
