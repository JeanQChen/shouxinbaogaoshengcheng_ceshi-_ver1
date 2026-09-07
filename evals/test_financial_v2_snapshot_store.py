"""Eval: financial_v2 快照 / 指标持久化层（A6 Commit 2：store.py）。

用法: python -m evals.test_financial_v2_snapshot_store

覆盖（任务书 §13 store 层矩阵）：
- 快照单事务原子提交：头部 + items + exceptions + 首条 valid 事件 + current 指针
  + switch log + checkpoint 同事务落盘；任一失败全量回滚；
- 严格复用：同 snapshot_id 深比对（头 + 全部 items + 全部 exceptions），一致 → reused，
  不一致 → StorageConflictError 且不覆盖历史；
- 准入拒绝：跨 scope/currency 的 item、批内重复 comparison_key / exception 键；
- 指标单事务原子提交 / 复用 / 冲突回滚（批内中途冲突不留半批）；
- Decimal 权威（amount_text）逐位精确往返；
- 读函数（get_snapshot / list_snapshot_items / get_snapshot_item / list_snapshot_exceptions /
  get_current_snapshot / list_metric_results / get_metric_result）；
- 不可变触发器（snapshot_item / financial_snapshot UPDATE/DELETE 阻断）。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_snap_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


_COMPANY = "ACME"
_AS_OF = "2024-12-31"
_SCOPE = "consolidated"
_CURRENCY = "CNY"
_PURPOSE = "report"
_TS = "2026-01-01T00:00:00Z"


def _snapshot_header(*, record_set_ids=None, reconciliation_run_id=None,
                     source_versions=None, resolution_versions=None,
                     restatement_selection=None, policy_adjustments=None,
                     required_formula_versions=None, report_blocked=False) -> S.FinancialSnapshot:
    record_set_ids = record_set_ids if record_set_ids is not None else ["rs-aaaa"]
    source_versions = source_versions if source_versions is not None else ["sv-1111"]
    resolution_versions = resolution_versions if resolution_versions is not None else []
    restatement_selection = restatement_selection if restatement_selection is not None else {}
    policy_adjustments = policy_adjustments if policy_adjustments is not None else []
    required_formula_versions = required_formula_versions if required_formula_versions is not None else {}
    snapshot_id = S.derive_snapshot_id(
        _COMPANY, _SCOPE, _CURRENCY, _AS_OF, _PURPOSE, record_set_ids,
        reconciliation_run_id, source_versions, resolution_versions,
        restatement_selection, policy_adjustments, required_formula_versions,
        S.SNAPSHOT_BUILDER_VERSION, S.ADMISSION_RULE_VERSION)
    return S.FinancialSnapshot(
        snapshot_id=snapshot_id,
        snapshot_version="sv-" + snapshot_id[-12:],
        company_id=_COMPANY,
        as_of_date=_AS_OF,
        scope=_SCOPE,
        currency=_CURRENCY,
        purpose=_PURPOSE,
        source_versions=source_versions,
        resolution_versions=resolution_versions,
        record_set_ids=record_set_ids,
        reconciliation_run_id=reconciliation_run_id,
        restatement_selection=restatement_selection,
        policy_adjustments=policy_adjustments,
        required_formula_versions=required_formula_versions,
        snapshot_builder_version=S.SNAPSHOT_BUILDER_VERSION,
        admission_rule_version=S.ADMISSION_RULE_VERSION,
        report_blocked=report_blocked,
        created_at=_TS,
    )


def _item(snapshot_id: str, ck: str, code: str, amount: Decimal | None,
          *, unit: str | None = "wan_yuan", source_refs=None) -> S.SnapshotItem:
    return S.SnapshotItem(
        snapshot_id=snapshot_id, comparison_key=ck, standard_item_code=code,
        amount=amount, unit=unit, report_period=_AS_OF, period_type="annual",
        statement_type="balance_sheet", statement_scope=_SCOPE, currency=_CURRENCY,
        restatement_version="original",
        source_refs=source_refs if source_refs is not None else ["rec-0001"],
        resolution_id=None)


def _exception(snapshot_id: str, ck: str, code: str,
               etype: str = "MISSING_REQUIRED_ITEM") -> S.SnapshotException:
    return S.SnapshotException(
        snapshot_id=snapshot_id, comparison_key=ck, standard_item_code=code,
        exception_type=etype, blocking_reason="缺必需科目", impact_scope=[], detail={})


def _metric(snapshot_id: str, formula_id: str, version: str, period: str,
            raw: Decimal | None, *, display: Decimal | None = None,
            status: str = "CALCULATED_EXACT", reason_code: str | None = None) -> S.MetricResult:
    mid = S.derive_metric_result_id(snapshot_id, formula_id, version, period)
    return S.MetricResult(
        metric_result_id=mid, snapshot_id=snapshot_id, formula_id=formula_id,
        formula_version=version, period=period, raw_value=raw,
        display_value=display if display is not None else raw,
        unit="ratio" if raw is not None else None,
        input_snapshot_item_refs=["ck-aa"], input_record_refs=["rec-0001"],
        status=status, reason_code=reason_code, calculation_detail={}, created_at=_TS)


def _checkpoint(run_id: str, stage_id: str, artifact_refs: list[str]) -> S.Checkpoint:
    return S.Checkpoint(
        checkpoint_id="cp-" + run_id, run_id=run_id, stage_id=stage_id, state_version=1,
        artifact_refs=artifact_refs, input_hashes={}, dependency_versions={},
        resolution_refs=[], completed_unit_ids=[], created_at=_TS)


def _count(db: str, sql: str, args=()) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


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

    db = _tmp_db()
    try:
        store.init_db(db)

        # ---- 快照新提交：头 + items + exceptions + valid + current + switch log + checkpoint ----
        snap = _snapshot_header()
        items = [
            _item(snap.snapshot_id, "ck-aa", "current_assets", Decimal("123.4500")),
            _item(snap.snapshot_id, "ck-bb", "current_liabilities", Decimal("80.0000")),
        ]
        exceptions = [_exception(snap.snapshot_id, "ck-cc", "total_assets")]
        cp = _checkpoint("run-snap", "SNAPSHOT_BUILD", [snap.snapshot_id])
        res = store.commit_snapshot_atomic(snap, items, exceptions, checkpoint=cp)
        check(not res.reused and res.items_inserted == 2 and res.exceptions_inserted == 1,
              "快照新提交：items=2 / exceptions=1 写入")
        check(res.current_switched is True, "快照新提交切换 current 指针")
        check(_count(db, "SELECT COUNT(*) FROM financial_snapshot WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 1, "financial_snapshot 头落库")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_item WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 2, "snapshot_item 两行落库")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_exception WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 1, "snapshot_exception 一行落库")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_validity WHERE snapshot_id=? AND status='valid'",
                     (snap.snapshot_id,)) == 1, "首条 valid 有效性事件落库")
        check(_count(db, "SELECT COUNT(*) FROM checkpoints WHERE checkpoint_id=?",
                     (cp.checkpoint_id,)) == 1, "checkpoint 与快照同事务落库")
        check(_count(db, "SELECT COUNT(*) FROM current_snapshot WHERE company_id=? AND purpose=?",
                     (_COMPANY, _PURPOSE)) == 1, "current_snapshot 指针落库")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_switch_log WHERE new_snapshot_id=?",
                     (snap.snapshot_id,)) == 1, "snapshot_switch_log 追加切换记录")

        # ---- Decimal 权威（amount_text）逐位精确往返 ----
        item_back = store.get_snapshot_item(snap.snapshot_id, "ck-aa")
        check(item_back is not None and item_back.amount == Decimal("123.4500"),
              "item Decimal 经 amount_text 逐位精确往返（123.4500）")
        snap_back = store.get_snapshot(snap.snapshot_id)
        check(snap_back is not None and snap_back.record_set_ids == ["rs-aaaa"],
              "get_snapshot 头部读回一致")

        # ---- 严格复用：同头 + 同 items + 同 exceptions → reused ----
        res2 = store.commit_snapshot_atomic(snap, items, exceptions)
        check(res2.reused and res2.items_inserted == 0 and res2.exceptions_inserted == 0,
              "严格复用：同内容二次提交 reused=True 且零新增")
        check(res2.current_switched is False, "复用且已 current → 不重复切换")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_validity WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 1, "复用不产生重复 valid 事件")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_item WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 2, "复用不产生重复 item 行")

        # ---- 冲突：同 id 但 item 内容不同 → StorageConflictError（不覆盖） ----
        conflict_items = [
            _item(snap.snapshot_id, "ck-aa", "current_assets", Decimal("999.0")),
            _item(snap.snapshot_id, "ck-bb", "current_liabilities", Decimal("80.0000")),
        ]
        raised = False
        try:
            store.commit_snapshot_atomic(snap, conflict_items, exceptions)
        except store.StorageConflictError:
            raised = True
        check(raised, "同 snapshot_id 不同 item → StorageConflictError")
        item_after = store.get_snapshot_item(snap.snapshot_id, "ck-aa")
        check(item_after.amount == Decimal("123.4500"), "冲突不覆盖历史 item 值")

        # ---- 准入拒绝：跨 scope item → ValidationError ----
        bad_item = S.SnapshotItem(
            snapshot_id=snap.snapshot_id, comparison_key="ck-dd",
            standard_item_code="x", amount=Decimal("1"), unit="wan_yuan",
            report_period=_AS_OF, period_type="annual", statement_type="balance_sheet",
            statement_scope="parent", currency=_CURRENCY, restatement_version="original",
            source_refs=["rec-0001"], resolution_id=None)
        raised = False
        try:
            store.commit_snapshot_atomic(snap, [bad_item], [])
        except validator.ValidationError:
            raised = True
        check(raised, "跨 scope item 被拒绝（ValidationError）")

        # ---- 准入拒绝：批内重复 comparison_key → ValidationError 且零写入 ----
        dup_items = [
            _item(snap.snapshot_id, "ck-ee", "x", Decimal("1")),
            _item(snap.snapshot_id, "ck-ee", "y", Decimal("2")),
        ]
        raised = False
        try:
            store.commit_snapshot_atomic(snap, dup_items, [])
        except validator.ValidationError:
            raised = True
        check(raised, "批内重复 comparison_key 被拒绝")

        # ---- 准入拒绝：批内重复 exception 键 → ValidationError ----
        dup_exc = [
            _exception(snap.snapshot_id, "ck-ff", "z"),
            _exception(snap.snapshot_id, "ck-ff", "z"),
        ]
        raised = False
        try:
            store.commit_snapshot_atomic(snap, [], dup_exc)
        except validator.ValidationError:
            raised = True
        check(raised, "批内重复 exception 键被拒绝")

        # ---- current 切换：第二个快照（同范围不同 record_set）成为 current ----
        snap2 = _snapshot_header(record_set_ids=["rs-bbbb"])
        items2 = [_item(snap2.snapshot_id, "ck-aa", "current_assets", Decimal("500.0"))]
        res3 = store.commit_snapshot_atomic(snap2, items2, [])
        check(res3.current_switched is True, "第二快照切换 current")
        cur = store.get_current_snapshot(_COMPANY, _SCOPE, _CURRENCY, _AS_OF, _PURPOSE)
        check(cur is not None and cur.snapshot_id == snap2.snapshot_id,
              "get_current_snapshot 返回最新快照")
        check(_count(db, "SELECT COUNT(*) FROM snapshot_switch_log") == 2,
              "snapshot_switch_log 两条切换记录")

        # ---- 不可变触发器：snapshot_item / financial_snapshot 阻断 UPDATE/DELETE ----
        conn = sqlite3.connect(db)
        try:
            blocked = False
            try:
                conn.execute("UPDATE snapshot_item SET amount_text='0' WHERE snapshot_id=?",
                             (snap.snapshot_id,))
            except sqlite3.IntegrityError:
                blocked = True
            check(blocked, "snapshot_item UPDATE 被不可变触发器阻断")
            blocked = False
            try:
                conn.execute("DELETE FROM financial_snapshot WHERE snapshot_id=?",
                             (snap.snapshot_id,))
            except sqlite3.IntegrityError:
                blocked = True
            check(blocked, "financial_snapshot DELETE 被不可变触发器阻断")
        finally:
            conn.close()

        # ---- 指标原子提交：快照必须存在；插入 / 复用 / 读回 ----
        m1 = _metric(snap.snapshot_id, "current_ratio", "1.0", _AS_OF, Decimal("1.543125"))
        mres = store.commit_metrics_atomic([m1], checkpoint=_checkpoint("run-metric", "CALCULATION", [m1.metric_result_id]))
        check(mres.inserted == 1 and mres.reused == 0, "指标新提交 inserted=1")
        check(_count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                     (snap.snapshot_id,)) == 1, "metric_result 落库")
        check(_count(db, "SELECT COUNT(*) FROM checkpoints WHERE checkpoint_id=?",
                     ("cp-run-metric",)) == 1, "指标 checkpoint 同事务落库")

        mb = store.get_metric_result(snap.snapshot_id, "current_ratio", "1.0", _AS_OF)
        check(mb is not None and mb.raw_value == Decimal("1.543125"),
              "metric Decimal 逐位精确读回（1.543125）")

        mres2 = store.commit_metrics_atomic([m1])
        check(mres2.inserted == 0 and mres2.reused == 1, "指标严格复用 reused=1 且零新增")

        # ---- 指标缺失快照 → KeyError ----
        ghost = _metric("snap-nonexistent", "current_ratio", "1.0", _AS_OF, Decimal("1"))
        raised = False
        try:
            store.commit_metrics_atomic([ghost])
        except KeyError:
            raised = True
        check(raised, "指标引用不存在的快照 → KeyError")

        # ---- 指标批内中途冲突 → 全量回滚（不留半批） ----
        m_new = _metric(snap.snapshot_id, "quick_ratio", "1.0", _AS_OF, Decimal("1.0"))
        m_conflict = _metric(snap.snapshot_id, "current_ratio", "1.0", _AS_OF, Decimal("9.9"))
        # m_conflict 与已存在的 m1 同 metric_result_id 但 raw 不同 → StorageConflictError
        raised = False
        try:
            store.commit_metrics_atomic([m_new, m_conflict])
        except store.StorageConflictError:
            raised = True
        check(raised, "指标批内冲突 → StorageConflictError")
        check(_count(db, "SELECT COUNT(*) FROM metric_result WHERE formula_id=? AND snapshot_id=?",
                     ("quick_ratio", snap.snapshot_id)) == 0,
              "批内冲突全量回滚，未留半批 quick_ratio")

        # ---- 指标批内重复 id → ValidationError ----
        raised = False
        try:
            store.commit_metrics_atomic([m_new, _metric(snap.snapshot_id, "quick_ratio", "1.0", _AS_OF, Decimal("2.0"))])
        except validator.ValidationError:
            raised = True
        check(raised, "指标批内重复 metric_result_id → ValidationError")

        # ---- 指标非成功状态：raw/display None 合法落库 ----
        m_miss = _metric(snap.snapshot_id, "interest_coverage", "1.0", _AS_OF, None,
                         status="MISSING_INPUT", reason_code="MISSING_REQUIRED_ITEM")
        store.commit_metrics_atomic([m_miss])
        mb2 = store.get_metric_result(snap.snapshot_id, "interest_coverage", "1.0", _AS_OF)
        check(mb2 is not None and mb2.raw_value is None and mb2.status == "MISSING_INPUT"
              and mb2.reason_code == "MISSING_REQUIRED_ITEM",
              "非成功状态指标（raw/display None）合法落库")

        # ---- 读函数 list_snapshot_items / list_snapshot_exceptions / list_metric_results ----
        check(len(store.list_snapshot_items(snap.snapshot_id)) == 2, "list_snapshot_items 返回 2 条")
        check(len(store.list_snapshot_exceptions(snap.snapshot_id)) == 1, "list_snapshot_exceptions 返回 1 条")
        check(len(store.list_metric_results(snap.snapshot_id)) == 2,
              "list_metric_results 返回 2 条（current_ratio + interest_coverage）")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
