"""Eval: financial_v2 定向失效（A6 Commit 6：invalidation.py）。

用法: python -m evals.test_financial_v2_invalidation

覆盖（任务书 §9 / §13.1 快照定向 stale）：
- 依赖未变 → 不 stale；record set 更新 → 定向失效（追加 stale 事件，幂等不重复写）；
- stale 后 current_snapshot 返回 None；compute_metric 返回 BLOCKED_BY_SNAPSHOT(SNAPSHOT_STALE)
  且不落盘新结果；历史 MetricResult 保留审计；
- 定向隔离：只失效依赖变更对象的快照，无关公司/无关快照保持 valid；
- resolution / source 定向失效（经 resolution_versions / source_versions 依赖筛选）；
- stale_snapshot_ids 列出当前失效快照。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import invalidation
from financial_v2 import metrics
from financial_v2 import reconciliation as recon
from financial_v2 import resolutions as res
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_inval_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> S.SourceLocator:
    return S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"


class _Seed:
    """直接构造标准化记录并落盘（跳过候选/映射，全字段可控）。"""

    def __init__(self, company: str):
        self.company = company
        self._n = 0

    def _register(self, ext_id: str, file_hash: str) -> tuple[str, str]:
        self._n += 1
        source_document_id = S.scope_source_document_id(self.company, ext_id)
        source_version = S.derive_source_version(source_document_id, file_hash)
        store.register_source_atomic(
            S.FinancialSourceDocument(
                source_document_id=source_document_id, company_id=self.company,
                source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                declared_company_name=self.company, detected_company_name=self.company,
                subject_match_status="matched", created_at=_TS),
            S.FinancialSourceVersion(
                source_version=source_version, source_document_id=source_document_id,
                file_sha256=file_hash, file_type="xlsx", file_size=100,
                document_id=None, document_version=None, created_at=_TS))
        return source_document_id, source_version

    def _persist(self, source_document_id: str, source_version: str,
                 specs: list[dict]) -> str:
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = []
        for i, sp in enumerate(specs):
            r = S.SourceFinancialRecord(
                record_id="", record_set_version=rs, company_id=self.company,
                standard_item_code=sp["item_code"],
                statement_type="balance_sheet", raw_item_text=sp["item_code"],
                raw_value=sp["value"], raw_unit="wan_yuan", raw_currency="CNY",
                std_value=sp["value"], std_unit="yuan", std_currency="CNY",
                conversion_rule_version="1.0", report_period="2024-12-31",
                period_type="annual", statement_scope="consolidated", currency="CNY",
                restatement_version="0", locator=_locator(i + 2), mapping_mode="rule",
                confidence=1.0, record_hash="", quality_flags=[], created_at=_TS,
                candidate_id=None)
            r.record_hash = validator._record_hash(r)
            r.record_id = S.derive_record_id(rs, S.record_identity_fields(r))
            records.append(r)
        record_set = S.FinancialRecordSet(
            record_set_version=rs, source_version=source_version, extractor_name=None,
            extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={},
            report_periods=sorted({r.report_period for r in records}), currency="CNY",
            unit="wan_yuan", statement_scope="consolidated", audit_status="audited",
            block_count=0, record_count=len(records), created_at=_TS,
            input_candidate_set_version=rs)
        store.commit_normalization_atomic(record_set, records, [], source_document_id)
        return rs

    def persist_records(self, ext_id: str, specs: list[dict]) -> str:
        source_document_id, source_version = self._register(
            ext_id, hashlib.sha256(ext_id.encode()).hexdigest())
        return self._persist(source_document_id, source_version, specs)

    def persist_new_version(self, ext_id: str, hash_suffix: str,
                            specs: list[dict]) -> str:
        """同一 source_document_id 的新内容版本 → current_record_set 指针更新。"""
        file_hash = hashlib.sha256((ext_id + hash_suffix).encode()).hexdigest()
        source_document_id, source_version = self._register(ext_id, file_hash)
        return self._persist(source_document_id, source_version, specs)


def _request(company, record_set_ids, *, run_id="run-inv"):
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO",
                                                          "SOLV_DEBT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id=run_id)


def _build(company, rs, *, run_id="run-inv"):
    return snapshots.build_snapshot(_request(company, [rs], run_id=run_id))


def _count(db: str, sql: str, params: tuple = ()) -> int:
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
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
        seed_a = _Seed("ACME")
        seed_b = _Seed("BCO")

        # ---- 公司 A 快照 + 指标落盘 ----
        rs_a = seed_a.persist_records("a-doc", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("200")},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("100")},
            {"item_code": "TOTAL_ASSETS", "value": Decimal("500")},
            {"item_code": "TOTAL_LIABILITIES", "value": Decimal("300")},
        ])
        snap_a = _build("ACME", rs_a).snapshot
        table = metrics.compute_all(snap_a.snapshot_id)
        mr_before = _count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                           (snap_a.snapshot_id,))
        check(mr_before > 0, "快照已落盘指标结果")

        # ---- 依赖未变 → 不 stale ----
        r0 = invalidation.invalidate_snapshot(snap_a.snapshot_id, "human", "check")
        check(not r0.stale and r0.event_id is None, "依赖未变 → 不 stale 且不写事件")
        check(store.latest_snapshot_validity(snap_a.snapshot_id) == "valid",
              "未漂移快照 validity 仍 valid")

        # ---- 公司 B 独立快照（隔离对照） ----
        rs_b = seed_b.persist_records("b-doc", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("999")},
        ])
        snap_b = _build("BCO", rs_b).snapshot

        # ---- record set 更新 → 定向失效（只 A 漂移，B 不受影响） ----
        seed_a.persist_new_version("a-doc", "-v2", [
            {"item_code": "CURRENT_ASSETS", "value": Decimal("210")},
        ])
        results = invalidation.invalidate_for_record_set(rs_a, "human", "新版本")
        check(len(results) == 1 and results[0].snapshot_id == snap_a.snapshot_id,
              "record set 更新 → 定向命中 1 个依赖快照")
        check(results[0].stale and results[0].event_id is not None,
              "依赖快照追加 stale 事件")
        check(store.latest_snapshot_validity(snap_a.snapshot_id) == "stale",
              "A 快照 latest_snapshot_validity → stale")
        check(store.latest_snapshot_validity(snap_b.snapshot_id) == "valid",
              "无关公司 B 快照保持 valid（定向隔离）")
        check(invalidation.stale_snapshot_ids() == [snap_a.snapshot_id],
              "stale_snapshot_ids 只列 A 快照")

        # ---- stale 后 current_snapshot 返回 None + compute_metric 阻断且不落盘 ----
        check(snapshots.current_snapshot("ACME", "consolidated", "CNY", "2024-12-31",
                                         "credit_analysis") is None,
              "stale 后 current_snapshot 返回 None")
        m = metrics.compute_metric(snap_a.snapshot_id, "SOLV_CURRENT_RATIO",
                                   "2024-12-31", persist=True)
        check(m.status == "BLOCKED_BY_SNAPSHOT" and m.reason_code == "SNAPSHOT_STALE",
              "stale 后 compute_metric → BLOCKED_BY_SNAPSHOT(SNAPSHOT_STALE)")
        check(_count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                     (snap_a.snapshot_id,)) == mr_before,
              "stale 快照冻结：compute_metric 不落盘新结果，历史指标保留")

        # ---- 幂等：再次失效不产生新 stale 事件 ----
        r2 = invalidation.invalidate_snapshot(snap_a.snapshot_id, "human", "again")
        check(r2.stale and r2.event_id is None, "已 stale → 幂等不重复写事件")

        # ---- 无关 record_set 更新不影响 A（定向：只有真正依赖者漂移） ----
        results2 = invalidation.invalidate_for_record_set("rs-nonexistent", "human", "x")
        check(results2 == [], "不存在的 record_set → 无命中快照")
        check(store.latest_snapshot_validity(snap_b.snapshot_id) == "valid",
              "B 快照仍 valid（未被无关失效波及）")

        # ---- source 定向失效（经 source_version → source_document_id 映射） ----
        sd_b = S.scope_source_document_id("BCO", "b-doc")
        res_src = invalidation.invalidate_for_source(sd_b, "human", "source 更新")
        # 先不漂移：B 的 source 未更新，invalid 不应 stale。
        check(all(not r.stale for r in res_src), "source 未漂移 → 不 stale")
        seed_b.persist_new_version("b-doc", "-v2", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000")},
        ])
        res_src2 = invalidation.invalidate_for_source(sd_b, "human", "source 更新")
        check(len(res_src2) == 1 and res_src2[0].stale
              and res_src2[0].snapshot_id == snap_b.snapshot_id,
              "source 更新 → 定向失效依赖该 source 的快照 B")
        check(store.latest_snapshot_validity(snap_b.snapshot_id) == "stale",
              "B 快照经 source 定向失效 → stale")

        # ---- resolution 定向失效（经 resolution_versions 依赖筛选） ----
        seed_c = _Seed("CCO")
        rs_c1 = seed_c.persist_records("c-doc1", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        rs_c2 = seed_c.persist_records("c-doc2", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("2000.00")},
        ])
        run_c = recon.run_reconciliation("CCO", [rs_c1, rs_c2], persist=True)
        group = next(g for g in store.list_reconciliation_group_results(run_c.run_id)
                     if g.state == "CONFLICT")
        accepted = [rid for rid in group.candidate_record_ids
                    if store.get_record(rid).std_value == Decimal("1000.00")]
        rejected = [rid for rid in group.candidate_record_ids
                    if store.get_record(rid).std_value == Decimal("2000.00")]
        batch = res.submit_value_resolutions(res.ValueResolutionBatchRequest(
            company_id="CCO", operator="human", items=[res.ValueResolutionItem(
                group_id=group.comparison_key, accepted_record_ids=accepted,
                rejected_record_ids=rejected, reason_code="AUDITED_SOURCE", note="")]))
        snap_c = snapshots.build_snapshot(snapshots.SnapshotBuildRequest(
            company_id="CCO", as_of_date="2024-12-31", scope="consolidated",
            currency="CNY", purpose="credit_analysis", record_set_ids=[rs_c1, rs_c2],
            reconciliation_run_id=run_c.run_id,
            required_formula_ids=["SOLV_DEBT_RATIO"], restatement_selection={},
            policy_adjustments={}, run_id="run-c")).snapshot
        res_id = snap_c.resolution_versions[0]
        check(res_id is not None, "快照 C 记录 resolution 依赖")
        # 先失效决议本身，再定向传播到依赖快照。
        store.invalidate_resolution_record(res_id, "human", "决议复核")
        res_inv = invalidation.invalidate_for_resolution(res_id, "human", "决议复核")
        check(len(res_inv) == 1 and res_inv[0].stale
              and res_inv[0].snapshot_id == snap_c.snapshot_id,
              "resolution 失效 → 定向失效依赖该决议的快照 C")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
