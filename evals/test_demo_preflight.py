"""Eval: scripts.demo_preflight 严格只读语义（Phase 3 收口专项测试）。

用法: python -m evals.test_demo_preflight

覆盖（用户 §三 专项测试）：
- temp 库路径生效、不读/不建默认库路径（DEFAULT_DB_PATH 不被触碰）；
- 缺失 DB → fail，且运行后仍不存在（不 init_db 建表）；
- 空库 / 缺 schema（0 字节）→ fail；
- validity 缺失 / stale / superseded → 全 fail（None 绝不默认 valid）；
- report_blocked / quarantine → fail；
- Evidence 无 current set → fail；
- provider / key 缺失 → fail，且输出绝不打印 Key；
- 完整健康环境 → pass；
- 运行前后 DB 文件 hash 不变（真正只读）。

公司无关：一律用合成公司 "ACME"，不引入 300750 / 宁德时代分支。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evidence import store as estore
from evals.test_context import _seed_records, _specs
from financial_v2 import progress
from financial_v2 import snapshots
from financial_v2 import store as fstore
from scripts import demo_preflight as pf

_TS = "2026-01-01T00:00:00Z"
_COMPANY = "ACME"


# ---------------------------------------------------------------------------
# 环境构造（只写临时库；preflight 是被测对象，本处仅构造输入）
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_pf_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _db_hash(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _mutate(db: str, sql: str, params: tuple = ()) -> None:
    """打开→执行→提交→关闭（try/finally，异常也不留半开连接）。"""
    conn = sqlite3.connect(db)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _build_healthy_fin(company: str, fin_db: str) -> None:
    """真实主链：标准化记录 → 快照 + 指标 → current 指针 + validity=valid。"""
    fstore.init_db(fin_db)
    rs = _seed_records(company, "ok", _specs({
        "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
        "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
        "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
        "NET_PROFIT": Decimal("120"),
    }, "2024-12-31"))
    req = snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id="run-pf")
    res = progress.run_pipeline(req)
    assert res.final_state == "completed", f"healthy fin build failed: {res.final_state}"


def _build_blocked_fin(company: str, fin_db: str) -> None:
    """真实主链但缺必算公式输入 → MISSING_REQUIRED_ITEM 恒阻断 → report_blocked=True。

    financial_snapshot 表是不可变表（UPDATE 触发禁止），故 report_blocked 只能经真实
    构建链产生，本测试用「缺 CURRENT_ASSETS/CURRENT_LIABILITIES 但必算 SOLV_CURRENT_RATIO」
    确定性触发 report_blocked，而非绕过约束直改快照行。
    """
    fstore.init_db(fin_db)
    rs = _seed_records(company, "blocked", _specs({
        "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
        "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
        "NET_PROFIT": Decimal("120"),
    }, "2024-12-31"))
    req = snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id="run-pf-blocked")
    res = progress.run_pipeline(req)
    assert res.final_state == "waiting_human", f"blocked fin build: {res.final_state}"


def _seed_evidence(ev_db: str, company: str, doc_status: str = "current",
                   set_status: str = "current") -> None:
    conn = sqlite3.connect(ev_db)
    try:
        conn.execute(
            "INSERT INTO documents (company_id, document_id, document_version, source_name, "
            "source_type, material_group, file_sha256, file_size, parser_version, status, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company, "doc-1", "v1", "年报.pdf", "annual_report", "company_docs",
             "a" * 64, 1024, "pdf_parser_v1", doc_status, _TS))
        conn.execute(
            "INSERT INTO evidence_sets (company_id, document_id, document_version, "
            "evidence_set_version, dependency_versions, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (company, "doc-1", "v1", "es-1", "{}", set_status, _TS))
        conn.commit()
    finally:
        conn.close()


def _build_healthy_ev(company: str, ev_db: str) -> None:
    estore.init_db(ev_db)
    _seed_evidence(ev_db, company)


def _snapshot_id(fin_db: str) -> str:
    conn = sqlite3.connect(fin_db)
    try:
        return conn.execute("SELECT snapshot_id FROM current_snapshot LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _set_validity(fin_db: str, snap_id: str, status: str | None) -> None:
    """status=None 表示删除全部 validity 事件（latest → None）。"""
    if status is None:
        _mutate(fin_db, "DELETE FROM snapshot_validity WHERE snapshot_id=?", (snap_id,))
    else:
        _mutate(fin_db, "UPDATE snapshot_validity SET status=? WHERE snapshot_id=?",
                (status, snap_id))


def _run(company: str, fin_db: str, ev_db: str) -> dict:
    return pf.run(company=company, scope="consolidated", currency="CNY",
                  purpose="credit_analysis", fin_db=fin_db, ev_db=ev_db)


def _check_by_name(result: dict, name: str) -> dict | None:
    for c in result["checks"]:
        if c["name"] == name:
            return c
    return None


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ===================== 健康环境 + 逐项失败态 =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        _build_healthy_fin(_COMPANY, fin_db)
        _build_healthy_ev(_COMPANY, ev_db)
        snap_id = _snapshot_id(fin_db)
        h_before_fin = _db_hash(fin_db)
        h_before_ev = _db_hash(ev_db)

        # ---- 1. temp 库路径生效 + 不读默认库路径 ----
        bogus_dir = Path(tempfile.mkdtemp())
        bogus_fin = bogus_dir / "default_fin.db"
        bogus_ev = bogus_dir / "default_ev.db"
        orig_fdef, orig_edef = fstore.DEFAULT_DB_PATH, estore.DEFAULT_DB_PATH
        fstore.DEFAULT_DB_PATH = bogus_fin
        estore.DEFAULT_DB_PATH = bogus_ev
        try:
            result = _run(_COMPANY, fin_db, ev_db)
        finally:
            fstore.DEFAULT_DB_PATH = orig_fdef
            estore.DEFAULT_DB_PATH = orig_edef
        check(result["ok"] is True, "temp 库路径生效：健康环境 preflight 全通过")
        check(not bogus_fin.exists() and not bogus_ev.exists(),
              "preflight 未读/未建默认库路径（DEFAULT_DB_PATH 不被触碰）")

        # ---- 11. 完整健康环境 → pass（逐项关键检查） ----
        check(result["failed"] == 0, f"健康环境 failed=0（实际 {result['failed']}）")
        check(_check_by_name(result, "financial.schema_version")["ok"] is True,
              "健康环境 schema 版本校验通过")
        check(_check_by_name(result, "financial.validity")["ok"] is True,
              "健康环境 validity == valid 判定通过")
        check(_check_by_name(result, "financial.snapshot_item_count")["ok"] is True
              and _check_by_name(result, "financial.metric_result_count")["ok"] is True,
              "健康环境 snapshot_item / metric_result 均非空")
        check(_check_by_name(result, "evidence.current_set_inventory")["ok"] is True,
              "健康环境 evidence current set 非空判定通过")

        # ---- 12. 运行前后 DB 文件 hash 不变（真正只读） ----
        check(_db_hash(fin_db) == h_before_fin and _db_hash(ev_db) == h_before_ev,
              "preflight 运行前后 fin/ev 库文件 hash 不变（只读不写库）")

        # ---- provider / key：缺失 fail + 输出不打印 Key ----
        orig_provider, orig_key = config.EXTERNAL_SEARCH_PROVIDER, config.BOCHA_API_KEY
        try:
            config.EXTERNAL_SEARCH_PROVIDER = "bocha"
            config.BOCHA_API_KEY = ""
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.bocha_api_key_present")["ok"] is False,
                  "key 缺失 → provider.bocha_api_key_present fail")

            config.BOCHA_API_KEY = "SUPERSECRET_KEY_9f3a"
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.bocha_api_key_present")["ok"] is True,
                  "key 已设置 → provider.bocha_api_key_present pass")
            check("SUPERSECRET_KEY_9f3a" not in json.dumps(r, ensure_ascii=False),
                  "输出绝不打印 API Key（序列化结果不含 Key 值）")

            config.EXTERNAL_SEARCH_PROVIDER = "other"
            config.BOCHA_API_KEY = "k"
            r = _run(_COMPANY, fin_db, ev_db)
            check(_check_by_name(r, "provider.external_search_provider")["ok"] is False,
                  "provider != bocha → provider.external_search_provider fail")
        finally:
            config.EXTERNAL_SEARCH_PROVIDER = orig_provider
            config.BOCHA_API_KEY = orig_key

        # ---- 5. validity stale ----
        _set_validity(fin_db, snap_id, "stale")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.validity")["ok"] is False,
              "validity=stale → financial.validity fail")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 5. validity superseded ----
        _set_validity(fin_db, snap_id, "superseded")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.validity")["ok"] is False,
              "validity=superseded → financial.validity fail")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 4. validity 缺失（None） ----
        _set_validity(fin_db, snap_id, None)
        r = _run(_COMPANY, fin_db, ev_db)
        v = _check_by_name(r, "financial.validity")
        check(r["ok"] is False and v["ok"] is False and "None" in v["detail"],
              "validity 缺失(None) → fail（绝不默认 valid）")
        _set_validity(fin_db, snap_id, "valid")

        # ---- 7. quarantine ----
        _mutate(fin_db, "INSERT INTO quarantine (quarantine_id, object_type, object_id, "
                "reason, quarantined_at) VALUES (?,?,?,?,?)",
                ("q1", "financial_snapshot", snap_id, "test", _TS))
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.quarantine")["ok"] is False,
              "快照被 quarantine → financial.quarantine fail")
        _mutate(fin_db, "DELETE FROM quarantine WHERE quarantine_id='q1'")

        # ---- 9. Evidence 无 current set ----
        # 清掉 evidence_sets 的 current 状态 → JOIN 为空 → inventory 空 → fail。
        _mutate(ev_db, "UPDATE evidence_sets SET status='superseded'")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False
              and _check_by_name(r, "evidence.current_set_inventory")["ok"] is False,
              "Evidence 无 current set → evidence.current_set_inventory fail")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ===================== 7. report_blocked（真实阻断快照） =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        _build_blocked_fin(_COMPANY, fin_db)
        _build_healthy_ev(_COMPANY, ev_db)
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False and _check_by_name(r, "financial.report_blocked")["ok"] is False,
              "report_blocked=true → financial.report_blocked fail")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    # ===================== 2. 缺失 DB → fail 且仍缺失 =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    _cleanup_db(fin_db)  # 确保不存在
    _cleanup_db(ev_db)
    r = _run(_COMPANY, fin_db, ev_db)
    check(r["ok"] is False, "缺失 fin/ev 库 → preflight fail-closed")
    check(not Path(fin_db).exists() and not Path(ev_db).exists(),
          "缺失库运行后仍不存在（preflight 不 init_db 建表）")
    check(_check_by_name(r, "financial.db_readonly_open") is not None
          and _check_by_name(r, "evidence.db_readonly_open") is not None,
          "缺失库返回 db_readonly_open 失败检查（非抛错崩溃）")

    # ===================== 3. 空库 / 缺 schema → fail =====================
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        Path(fin_db).write_bytes(b"")  # 0 字节空文件
        Path(ev_db).write_bytes(b"")
        r = _run(_COMPANY, fin_db, ev_db)
        check(r["ok"] is False, "空库 → preflight fail-closed")
        check(_check_by_name(r, "financial.schema_version")["ok"] is False
              and _check_by_name(r, "financial.required_tables")["ok"] is False,
              "空库 schema 版本 + 缺表均 fail")
        check(_check_by_name(r, "evidence.current_set_inventory")["ok"] is False,
              "空 evidence 库 → current_set_inventory fail（不抛错崩溃）")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
