"""Eval: Phase 4 Batch A — Planner 上游指纹解析严格只读。

不调用 LLM / Embedding / Chroma / 互联网。测试前置用 init_db 建出「健康但空」的库，
被测代码 resolve_fingerprints 本身必须不建库、不迁移、不改字节。

覆盖：
1. 只读不改字节：financial_v2 / evidence 两库文件 hash 前后一致。
2. 健康但空 → 空指纹 + snapshot_id=None（合法空态，不抛错）。
3. 缺失库 → fail-closed 抛错，且不创建文件。
4. 损坏库（非 SQLite 文件）→ fail-closed 抛错。
5. schema 版本不符（缺最新 migration）→ fail-closed，且不触发迁移（hash 不变）。

用法: python -m evals.test_planner_readonly
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planning import schema as PS  # noqa: E402
from planning.report_planner import resolve_fingerprints  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    from evidence import store as estore
    from financial_v2 import store as fstore

    tmpdir = Path(tempfile.mkdtemp(prefix="planner_readonly_"))
    fin_db = tmpdir / "financial_v2.db"
    ev_db = tmpdir / "evidence.db"

    # 测试前置：init_db 建出「健康但空」的库（schema 齐全，无业务数据）。
    fstore.init_db(fin_db)
    estore.init_db(ev_db)

    # 1-2. 只读不改字节 + 健康但空 → 空指纹/None
    fin_before = _sha256(fin_db)
    ev_before = _sha256(ev_db)
    ev_fp, snap = resolve_fingerprints("300750", str(fin_db), str(ev_db))
    check(_sha256(fin_db) == fin_before, "financial_v2 库 hash 不变（只读）")
    check(_sha256(ev_db) == ev_before, "evidence 库 hash 不变（只读）")
    check(ev_fp == PS.sha256_json([]), "无 current 证据 → 空指纹")
    check(snap is None, "无快照 → snapshot_id=None（健康但空）")

    # 3. 缺失库 → fail-closed 且不创建文件
    missing = tmpdir / "missing_fin.db"
    try:
        resolve_fingerprints("300750", str(missing), str(ev_db))
        check(False, "缺失 financial_v2 库应抛错")
    except FileNotFoundError:
        check(True, "缺失 financial_v2 库抛 FileNotFoundError")
    check(not missing.exists(), "缺失 financial_v2 库未被创建")

    missing_ev = tmpdir / "missing_ev.db"
    try:
        resolve_fingerprints("300750", str(fin_db), str(missing_ev))
        check(False, "缺失 evidence 库应抛错")
    except FileNotFoundError:
        check(True, "缺失 evidence 库抛 FileNotFoundError")
    check(not missing_ev.exists(), "缺失 evidence 库未被创建")

    # 4. 损坏库（非 SQLite 文件）→ fail-closed
    corrupt = tmpdir / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    try:
        resolve_fingerprints("300750", str(corrupt), str(ev_db))
        check(False, "损坏 financial_v2 库应抛错")
    except RuntimeError:
        check(True, "损坏 financial_v2 库抛 RuntimeError")

    # 5. schema 版本不符（缺最新 migration）→ fail-closed 且不迁移（hash 不变）
    conn = sqlite3.connect(str(fin_db))
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version='7'")
        conn.commit()
    finally:
        conn.close()
    fin_before_mismatch = _sha256(fin_db)
    try:
        resolve_fingerprints("300750", str(fin_db), str(ev_db))
        check(False, "schema 版本不符应抛错")
    except RuntimeError:
        check(True, "schema 版本不符抛 RuntimeError（fail-closed）")
    check(_sha256(fin_db) == fin_before_mismatch, "版本不符不触发迁移（hash 不变）")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
