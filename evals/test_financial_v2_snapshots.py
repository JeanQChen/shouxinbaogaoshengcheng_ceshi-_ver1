"""Eval: financial_v2 快照构建（A6 Commit 3：snapshots.py）。

用法: python -m evals.test_financial_v2_snapshots

覆盖（任务书 §13.1 快照矩阵）：
- 单来源 / 两来源一致：MATCHED 只存一次（source_refs 全保留）、SINGLE_SOURCE 单来源；
  inspection 识别单来源键；current_snapshot 返回最新快照；
- 严格复用：同请求重放 reused=True 且同 snapshot_id；
- 未解决冲突禁止入项（UNRESOLVED_CONFLICT + report_blocked=True）；active resolution 入项
  且携带 resolution_id；失效（stale）resolution 不入项；
- scope / 期间 / 重述分离；重述选择不明生成 AMBIGUOUS_RESTATEMENT，明确选择后只入选定版本；
- Decimal 高精度 / 负值逐位精确；0 item 缺口快照可审计且 report_blocked=True；
- 政策调整：非法类型 / 引用不存在记录拒绝；跨公司拒绝 + 构建失败不切换 current；
  reconciliation run 输入集合不一致拒绝；
- 定向失效：record set 更新只 stale 相关快照，无关公司不受影响（独立公司隔离验证）。

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

from financial_v2 import reconciliation as recon
from financial_v2 import resolutions as res
from financial_v2 import schema as S
from financial_v2 import snapshots
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


def _locator(row: int) -> S.SourceLocator:
    return S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"


class _Seed:
    """直接构造标准化记录并落盘（跳过候选/映射，全字段可控）。"""

    def __init__(self, company: str = "ACME"):
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

    def register(self, ext_id: str) -> tuple[str, str]:
        return self._register(ext_id, hashlib.sha256(ext_id.encode()).hexdigest())

    def _persist(self, source_document_id: str, source_version: str,
                 specs: list[dict]) -> str:
        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = []
        for i, sp in enumerate(specs):
            item_code = sp["item_code"]
            value = sp.get("value")
            loc = _locator(i + 2)
            r = S.SourceFinancialRecord(
                record_id="", record_set_version=rs, company_id=self.company,
                standard_item_code=item_code,
                statement_type=sp.get("statement_type", "balance_sheet"),
                raw_item_text=item_code, raw_value=value, raw_unit="wan_yuan",
                raw_currency=sp.get("currency", "CNY"), std_value=value,
                std_unit="yuan", std_currency=sp.get("currency", "CNY"),
                conversion_rule_version="1.0",
                report_period=sp.get("period", "2024-12-31"),
                period_type=sp.get("period_type", "annual"),
                statement_scope=sp.get("scope", "consolidated"),
                currency=sp.get("currency", "CNY"),
                restatement_version=sp.get("restatement", "0"),
                locator=loc, mapping_mode=sp.get("mapping_mode", "rule"),
                confidence=1.0, record_hash="", quality_flags=[],
                created_at=_TS, candidate_id=None)
            r.record_hash = validator._record_hash(r)
            r.record_id = S.derive_record_id(rs, S.record_identity_fields(r))
            records.append(r)

        record_set = S.FinancialRecordSet(
            record_set_version=rs, source_version=source_version,
            extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={},
            report_periods=sorted({r.report_period for r in records}),
            currency="CNY", unit="wan_yuan", statement_scope="consolidated",
            audit_status="audited", block_count=0, record_count=len(records),
            created_at=_TS, input_candidate_set_version=rs)
        store.commit_normalization_atomic(record_set, records, [], source_document_id)
        return rs

    def persist_records(self, ext_id: str, specs: list[dict]) -> tuple[str, str]:
        """新来源登记 + 记录集落盘，返回 (record_set_version, source_document_id)。"""
        source_document_id, source_version = self.register(ext_id)
        rs = self._persist(source_document_id, source_version, specs)
        return rs, source_document_id

    def persist_new_version(self, ext_id: str, hash_suffix: str,
                            specs: list[dict]) -> str:
        """同一 source_document_id 的新内容版本（不同 file_sha256）+ 记录集落盘。

        复用 register_source_atomic 追加新内容版本，并原子切换该 source 的
        current_record_set 指针 → 触发定向失效检测。返回新 record_set_version。
        """
        file_hash = hashlib.sha256((ext_id + hash_suffix).encode()).hexdigest()
        source_document_id, source_version = self._register(ext_id, file_hash)
        return self._persist(source_document_id, source_version, specs)


def _request(company, record_set_ids, *, reconciliation_run_id=None,
             required_formula_ids=None, restatement_selection=None,
             policy_adjustments=None, run_id="run-snap") -> snapshots.SnapshotBuildRequest:
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=reconciliation_run_id,
        required_formula_ids=required_formula_ids or [],
        restatement_selection=restatement_selection or {},
        policy_adjustments=policy_adjustments or {}, run_id=run_id)


def _item_by_code(items, code):
    return next((it for it in items if it.standard_item_code == code), None)


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
        seed = _Seed("ACME")

        # ---- 单来源 / 多来源一致 / 单来源识别 ----
        rs_a, _ = seed.persist_records("doc-a", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
            {"item_code": "CURRENT_ASSETS", "value": Decimal("500.00")},
        ])
        rs_b, _ = seed.persist_records("doc-b", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00")},
        ])
        run = recon.run_reconciliation("ACME", [rs_a, rs_b], persist=True)
        result = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_b], reconciliation_run_id=run.run_id))
        ta = _item_by_code(result.items, "TOTAL_ASSETS")
        ca = _item_by_code(result.items, "CURRENT_ASSETS")
        check(ta is not None and ta.amount == Decimal("1000.00"),
              "MATCHED：TOTAL_ASSETS 标准值只存一次（1000.00）")
        check(len(ta.source_refs) == 2, "MATCHED：source_refs 保留全部两个一致来源")
        check(ca is not None and len(ca.source_refs) == 1,
              "SINGLE_SOURCE：CURRENT_ASSETS 单一来源引用")
        check(not result.report_blocked, "全 MATCHED/SINGLE_SOURCE → report_blocked=False")
        check(result.current_switched, "首次构建切换 current")

        insp = snapshots.inspect_snapshot(result.snapshot.snapshot_id)
        check(ca.comparison_key in insp.single_source_keys,
              "inspection 识别 CURRENT_ASSETS 为单来源")
        check(ta.comparison_key not in insp.single_source_keys,
              "inspection 不把 MATCHED 识别为单来源")
        check(snapshots.current_snapshot("ACME", "consolidated", "CNY", "2024-12-31",
                                         "credit_analysis").snapshot_id == result.snapshot.snapshot_id,
              "current_snapshot 返回最新快照")

        # ---- 严格复用：同请求重放 → reused=True ----
        result2 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_b], reconciliation_run_id=run.run_id))
        check(result2.reused and result2.snapshot.snapshot_id == result.snapshot.snapshot_id,
              "严格复用：同请求重放 reused=True 且同 snapshot_id")

        # ---- 未解决冲突禁止入项 + report_blocked ----
        rs_c, _ = seed.persist_records("doc-c", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("2000.00")},
        ])
        run2 = recon.run_reconciliation("ACME", [rs_a, rs_c], persist=True)
        res3 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_c], reconciliation_run_id=run2.run_id))
        ta3 = _item_by_code(res3.items, "TOTAL_ASSETS")
        check(ta3 is None, "未解决冲突：TOTAL_ASSETS 不入项")
        conflict_exc = [e for e in res3.exceptions if e.exception_type == "UNRESOLVED_CONFLICT"]
        check(len(conflict_exc) == 1, "未解决冲突：固化 UNRESOLVED_CONFLICT 异常")
        check(res3.report_blocked, "关键缺口 → report_blocked=True")

        # ---- active resolution 可入项 ----
        conflict_group = next(
            g for g in store.list_reconciliation_group_results(run2.run_id)
            if g.state == "CONFLICT" and g.comparison_key == conflict_exc[0].comparison_key)
        accepted = [rid for rid in conflict_group.candidate_record_ids
                    if store.get_record(rid).std_value == Decimal("1000.00")]
        rejected = [rid for rid in conflict_group.candidate_record_ids
                    if store.get_record(rid).std_value == Decimal("2000.00")]
        batch = res.submit_value_resolutions(res.ValueResolutionBatchRequest(
            company_id="ACME", operator="human", items=[res.ValueResolutionItem(
                group_id=conflict_group.comparison_key, accepted_record_ids=accepted,
                rejected_record_ids=rejected, reason_code="AUDITED_SOURCE", note="年审优先")]))
        check(batch.committed, "冲突决议提交成功（accepted ∪ rejected 覆盖全组）")
        res4 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_c], reconciliation_run_id=run2.run_id))
        ta4 = _item_by_code(res4.items, "TOTAL_ASSETS")
        check(ta4 is not None and ta4.amount == Decimal("1000.00")
              and ta4.resolution_id is not None,
              "active resolution：TOTAL_ASSETS 入项（1000.00，携带 resolution_id）")
        check(not res4.report_blocked, "决议后无阻断异常 → report_blocked=False")

        # ---- invalidated（stale）resolution 不可入项 ----
        active_res_id = ta4.resolution_id
        store.invalidate_resolution_record(active_res_id, invalidated_by="human",
                                           invalidated_reason="复核更正")
        res5 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_c], reconciliation_run_id=run2.run_id))
        check(_item_by_code(res5.items, "TOTAL_ASSETS") is None,
              "失效决议后 TOTAL_ASSETS 不再入项")
        check(any(e.exception_type == "UNRESOLVED_CONFLICT" for e in res5.exceptions),
              "失效决议 → 冲突重新固化")

        # ---- scope 分离：parent 记录不入 consolidated 快照 ----
        rs_d, _ = seed.persist_records("doc-d", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("3000.00"), "scope": "parent"},
        ])
        run3 = recon.run_reconciliation("ACME", [rs_a, rs_d], persist=True)
        res6 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_d], reconciliation_run_id=run3.run_id))
        check(_item_by_code(res6.items, "TOTAL_ASSETS").amount == Decimal("1000.00"),
              "scope 分离：parent 记录不入 consolidated 快照")

        # ---- 期间分离：不同 report_period 各自成项 ----
        rs_e, _ = seed.persist_records("doc-e", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("900.00"), "period": "2023-12-31"},
        ])
        run4 = recon.run_reconciliation("ACME", [rs_a, rs_e], persist=True)
        res7 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_e], reconciliation_run_id=run4.run_id))
        periods = {it.report_period for it in res7.items if it.standard_item_code == "TOTAL_ASSETS"}
        check(periods == {"2024-12-31", "2023-12-31"},
              "期间分离：2024/2023 各成独立项，不合并")

        # ---- 重述歧义：未选择 → AMBIGUOUS_RESTATEMENT；选择 → 入项 ----
        rs_f, _ = seed.persist_records("doc-f", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1000.00"), "restatement": "1"},
        ])
        run5 = recon.run_reconciliation("ACME", [rs_a, rs_f], persist=True)
        res8 = snapshots.build_snapshot(
            _request("ACME", [rs_a, rs_f], reconciliation_run_id=run5.run_id))
        check(any(e.exception_type == "AMBIGUOUS_RESTATEMENT" for e in res8.exceptions),
              "重述选择不明 → AMBIGUOUS_RESTATEMENT 异常")
        base_key = S.comparison_key("ACME", "TOTAL_ASSETS", "balance_sheet", "2024-12-31",
                                    "annual", "consolidated", "CNY", "__RESTATEMENT_ANY__")
        res9 = snapshots.build_snapshot(_request(
            "ACME", [rs_a, rs_f], reconciliation_run_id=run5.run_id,
            restatement_selection={base_key: "0"}))
        ta9 = _item_by_code(res9.items, "TOTAL_ASSETS")
        check(ta9 is not None and ta9.restatement_version == "0",
              "重述明确选择：只入选定版本")

        # ---- Decimal 高精度 / 负值逐位精确 ----
        rs_g, _ = seed.persist_records("doc-g", [
            {"item_code": "NET_PROFIT", "value": Decimal("-9876543.210987654321")},
        ])
        run6 = recon.run_reconciliation("ACME", [rs_g], persist=True)
        res10 = snapshots.build_snapshot(
            _request("ACME", [rs_g], reconciliation_run_id=run6.run_id))
        np_ = _item_by_code(res10.items, "NET_PROFIT")
        check(np_ is not None and np_.amount == Decimal("-9876543.210987654321"),
              "负值高精度 Decimal 逐位精确入项")

        # ---- 0 item 缺口快照可审计 + report_blocked ----
        rs_h, _ = seed.persist_records("doc-h", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("500.00")},
        ])
        rec_h = store.list_records(rs_h)[0]
        store.quarantine("source_financial_record", rec_h.record_id, "测试隔离")
        run7 = recon.run_reconciliation("ACME", [rs_h], persist=True)
        res11 = snapshots.build_snapshot(
            _request("ACME", [rs_h], reconciliation_run_id=run7.run_id))
        check(len(res11.items) == 0 and res11.report_blocked,
              "0 item 缺口快照可提交且 report_blocked=True")
        check(any(e.exception_type == "QUARANTINED_INPUT" for e in res11.exceptions),
              "隔离输入固化 QUARANTINED_INPUT 异常")

        # ---- 政策调整：非法类型拒绝 / 引用不存在拒绝 ----
        raised = False
        try:
            snapshots.build_snapshot(_request(
                "ACME", [rs_a], reconciliation_run_id=None,
                policy_adjustments={"bogus_type": []}))
        except validator.ValidationError:
            raised = True
        check(raised, "policy_adjustment 非法类型被拒绝")

        raised = False
        try:
            snapshots.build_snapshot(_request(
                "ACME", [rs_a], reconciliation_run_id=None,
                policy_adjustments={"quick_ratio_other_current_asset": ["rec-not-exist"]}))
        except validator.ValidationError:
            raised = True
        check(raised, "policy_adjustment 引用不存在记录被拒绝")

        # ---- 跨公司拒绝 + 构建失败不切换 current ----
        seed2 = _Seed("OTHER")
        rs_other, _ = seed2.persist_records("doc-other", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("1.00")},
        ])
        before = snapshots.current_snapshot("ACME", "consolidated", "CNY", "2024-12-31",
                                            "credit_analysis").snapshot_id
        raised = False
        try:
            snapshots.build_snapshot(_request("ACME", [rs_other], reconciliation_run_id=None))
        except ValueError:
            raised = True
        check(raised, "跨公司记录 → ValueError（构建失败）")
        after = snapshots.current_snapshot("ACME", "consolidated", "CNY", "2024-12-31",
                                           "credit_analysis").snapshot_id
        check(before == after, "构建失败不切换 current")

        # ---- 非法 reconciliation run（输入集合不一致）→ ValueError ----
        raised = False
        try:
            snapshots.build_snapshot(
                _request("ACME", [rs_a, rs_b], reconciliation_run_id=run7.run_id))
        except ValueError:
            raised = True
        check(raised, "reconciliation run 输入集合不一致 → ValueError")

        # ---- 定向失效：record set 更新只 stale 相关快照（独立公司隔离验证） ----
        seed_s = _Seed("STALECO")
        rs_s1, _ = seed_s.persist_records("s-doc", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("100.00")},
        ])
        run_s1 = recon.run_reconciliation("STALECO", [rs_s1], persist=True)
        snap_s = snapshots.build_snapshot(
            _request("STALECO", [rs_s1], reconciliation_run_id=run_s1.run_id))
        stale_before = snapshots.detect_stale_snapshot(snap_s.snapshot.snapshot_id)
        check(not stale_before.stale, "依赖未变 → 不 stale")
        # 同一 source 的新内容版本 → current_record_set 指针更新 → 旧快照定向失效。
        seed_s.persist_new_version("s-doc", "-v2", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("100.00")},
        ])
        stale_after = snapshots.detect_stale_snapshot(snap_s.snapshot.snapshot_id)
        check(stale_after.stale, "record set 更新 → 旧快照 stale")
        check(any("record_set" in r or "source_version" in r for r in stale_after.reasons),
              "stale 原因包含 record_set/source 依赖")

        # 无关公司（OTHER）从未构建快照 → current 查询为空。
        check(snapshots.current_snapshot("OTHER", "consolidated", "CNY", "2024-12-31",
                                         "credit_analysis") is None,
              "无关公司 current 查询为空")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
