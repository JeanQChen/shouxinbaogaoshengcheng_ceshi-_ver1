"""Eval: financial_v2 V1 只读兼容适配（A7 Commit 7：adapters.py）。

用法: python -m evals.test_financial_v2_adapters

覆盖（任务书 §10 / §13.1 adapter 只读）：
- metrics_table_from_snapshot：读取已持久化 MetricResult 重建 MetricsTableV2
  （periods / status_counts / results 数量），只读不重算不落盘；
- report_financial_payload：快照头 + 指标行（formula_id/version/status/reason/
  raw/display/unit/note/refs）+ 异常 + 证据引用 + report_blocked/validity；
- 精确态 note=None；代理态 note 含 FINANCE_EXPENSES 代理说明；缺输入 note 含缺失科目；
- 未知快照 → KeyError；未计算指标的空快照 → 空 metrics 不崩溃；
- 只读性：打桩 store 全部 commit 原子接口，adapter 调用不触发任何写。

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

from financial_v2 import adapters
from financial_v2 import metrics
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_adapt_")
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

_STMT = {
    "CURRENT_ASSETS": "balance_sheet", "CURRENT_LIABILITIES": "balance_sheet",
    "TOTAL_LIABILITIES": "balance_sheet", "TOTAL_ASSETS": "balance_sheet",
    "TOTAL_EQUITY": "balance_sheet", "INVENTORY": "balance_sheet",
    "PREPAYMENTS": "balance_sheet", "ACCOUNTS_RECEIVABLE": "balance_sheet",
    "TOTAL_REVENUE": "income_statement", "OPERATING_COST": "income_statement",
    "NET_PROFIT": "income_statement", "OPERATING_PROFIT": "income_statement",
    "TOTAL_PROFIT": "income_statement", "INTEREST_EXPENSE": "income_statement",
    "FINANCE_EXPENSES": "income_statement", "OPERATING_CASH_FLOW": "cash_flow",
}


class _Seed:
    """直接构造标准化记录并落盘（跳过候选/映射，全字段可控）。"""

    def __init__(self, company: str = "ACME"):
        self.company = company
        self._n = 0

    def persist_records(self, ext_id: str, specs: list[dict]) -> str:
        self._n += 1
        source_document_id = S.scope_source_document_id(self.company, ext_id)
        source_version = S.derive_source_version(
            source_document_id, hashlib.sha256(ext_id.encode()).hexdigest())
        store.register_source_atomic(
            S.FinancialSourceDocument(
                source_document_id=source_document_id, company_id=self.company,
                source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                declared_company_name=self.company, detected_company_name=self.company,
                subject_match_status="matched", created_at=_TS),
            S.FinancialSourceVersion(
                source_version=source_version, source_document_id=source_document_id,
                file_sha256=hashlib.sha256(ext_id.encode()).hexdigest(), file_type="xlsx",
                file_size=100, document_id=None, document_version=None, created_at=_TS))

        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = []
        for i, sp in enumerate(specs):
            r = S.SourceFinancialRecord(
                record_id="", record_set_version=rs, company_id=self.company,
                standard_item_code=sp["item_code"],
                statement_type=sp.get("statement_type", "balance_sheet"),
                raw_item_text=sp["item_code"], raw_value=sp["value"], raw_unit="wan_yuan",
                raw_currency="CNY", std_value=sp["value"], std_unit="yuan",
                std_currency="CNY", conversion_rule_version="1.0",
                report_period=sp["period"], period_type="annual",
                statement_scope="consolidated", currency="CNY",
                restatement_version="0", locator=_locator(i + 2),
                mapping_mode="rule", confidence=1.0, record_hash="", quality_flags=[],
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


def _specs(values: dict[str, Decimal], period: str) -> list[dict]:
    return [{"item_code": code, "value": val, "period": period,
             "statement_type": _STMT.get(code, "balance_sheet")}
            for code, val in values.items()]


def _request(company, record_set_ids, *, run_id="run-adpt"):
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id=run_id)


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
        seed = _Seed("ACME")

        # ---- 全输入快照 + 整批计算（供 adapter 读取） ----
        current = {
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
            "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
            "TOTAL_EQUITY": Decimal("200"), "INVENTORY": Decimal("50"),
            "PREPAYMENTS": Decimal("10"), "ACCOUNTS_RECEIVABLE": Decimal("90"),
            "TOTAL_REVENUE": Decimal("1000"), "OPERATING_COST": Decimal("600"),
            "NET_PROFIT": Decimal("120"), "OPERATING_PROFIT": Decimal("160"),
            "TOTAL_PROFIT": Decimal("150"), "INTEREST_EXPENSE": Decimal("20"),
            "OPERATING_CASH_FLOW": Decimal("200"),
        }
        prior = {
            "TOTAL_ASSETS": Decimal("400"), "TOTAL_LIABILITIES": Decimal("250"),
            "TOTAL_EQUITY": Decimal("150"), "INVENTORY": Decimal("40"),
            "ACCOUNTS_RECEIVABLE": Decimal("70"), "TOTAL_REVENUE": Decimal("800"),
            "NET_PROFIT": Decimal("100"), "OPERATING_CASH_FLOW": Decimal("150"),
        }
        rs = seed.persist_records(
            "full", _specs(current, "2024-12-31") + _specs(prior, "2023-12-31"))
        snap = snapshots.build_snapshot(_request("ACME", [rs])).snapshot
        snap_id = snap.snapshot_id
        metrics.compute_all(snap_id)

        # ---- metrics_table_from_snapshot（只读） ----
        table = adapters.metrics_table_from_snapshot(snap_id)
        check(table.snapshot_id == snap_id, "metrics_table snapshot_id 正确")
        check(len(table.periods) == 2 and sorted(table.periods) == ["2023-12-31", "2024-12-31"],
              f"metrics_table periods 两期（{sorted(table.periods)}）")
        check(len(table.results) == _count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                                           (snap_id,)),
              "metrics_table results 数量 == 持久化 metric_result 数量")
        check(table.status_counts.get("CALCULATED_EXACT", 0) > 0,
              "metrics_table status_counts 含 CALCULATED_EXACT")

        # ---- report_financial_payload 头 ----
        payload = adapters.report_financial_payload(snap_id)
        check(payload.snapshot_id == snap_id and payload.company_id == "ACME",
              "payload snapshot_id/company_id 正确")
        check(payload.as_of_date == "2024-12-31" and payload.scope == "consolidated"
              and payload.currency == "CNY" and payload.purpose == "credit_analysis",
              "payload 头字段（as_of/scope/currency/purpose）正确")
        check(payload.report_blocked is False and payload.validity == "valid",
              "payload report_blocked=False 且 validity=valid")
        check(payload.exceptions == [], "全输入快照 exceptions 为空")
        check(payload.periods == ["2023-12-31", "2024-12-31"],
              "payload periods 两期有序")

        # ---- 指标行内容 + 精确态 note=None ----
        check(len(payload.metrics) == len(table.results), "payload.metrics 数量一致")
        cur = next(r for r in payload.metrics
                   if r.formula_id == "SOLV_CURRENT_RATIO" and r.period == "2024-12-31")
        check(cur.status == "CALCULATED_EXACT" and cur.note is None,
              "精确态行 note=None")
        check(cur.formula_version == "1.0" and cur.name is not None,
              "指标行含 formula_version 与展示名")
        check(cur.raw_value == Decimal("2") and cur.display_value == Decimal("2.00")
              and cur.unit == "ratio",
              "指标行 raw/display/unit 正确（raw 未舍入，display 舍入）")
        check(len(cur.input_snapshot_item_refs) >= 2 and len(cur.input_record_refs) >= 2,
              "指标行含 snapshot_item refs 与 record refs")

        # ---- 证据引用汇总（去重 + 排序） ----
        check(len(payload.snapshot_item_refs) >= 2 and len(payload.source_record_refs) >= 2,
              "payload 汇总证据引用非空")
        check(payload.snapshot_item_refs == sorted(set(payload.snapshot_item_refs)),
              "snapshot_item_refs 去重且有序")
        check(payload.source_record_refs == sorted(set(payload.source_record_refs)),
              "source_record_refs 去重且有序")

        # ---- 只读性：打桩全部 store 原子写入接口，adapter 不触发 ----
        _patched = ("commit_metrics_atomic", "commit_snapshot_atomic",
                    "commit_normalization_atomic", "register_source_atomic")
        _originals = {fn: getattr(store, fn) for fn in _patched}
        write_calls: list[str] = []

        def _boom(*_a, **_k):
            write_calls.append("write")
            raise AssertionError("adapter 不应触发任何 store 写入")
        for fn_name in _patched:
            setattr(store, fn_name, _boom)
        try:
            adapters.metrics_table_from_snapshot(snap_id)
            adapters.report_financial_payload(snap_id)
        finally:
            for fn_name in _patched:
                setattr(store, fn_name, _originals[fn_name])
        check(write_calls == [], "adapter 只读：未触发任何 store 原子写入接口")

        # ---- 未知快照 → KeyError ----
        for fn in (lambda: adapters.metrics_table_from_snapshot("snap-nope"),
                   lambda: adapters.report_financial_payload("snap-nope")):
            raised = False
            try:
                fn()
            except KeyError:
                raised = True
            check(raised, "未知 snapshot_id → KeyError")

        # ---- 空指标快照（未计算）→ 空 metrics 不崩溃 ----
        rs_e = seed.persist_records("empty", _specs({"TOTAL_ASSETS": Decimal("500")},
                                                    "2024-12-31"))
        snap_e = snapshots.build_snapshot(_request("ACME", [rs_e], run_id="run-empty")).snapshot
        payload_e = adapters.report_financial_payload(snap_e.snapshot_id)
        check(payload_e.metrics == [] and payload_e.periods == [],
              "未计算快照 → 空 metrics / 空 periods 不崩溃")
        table_e = adapters.metrics_table_from_snapshot(snap_e.snapshot_id)
        check(table_e.results == [] and table_e.status_counts == {},
              "未计算快照 → 空 metrics_table")

        # ---- 代理态 note（缺真实利息费用，用财务费用代理） ----
        rs_p = seed.persist_records("proxy", _specs({
            "TOTAL_PROFIT": Decimal("150"), "FINANCE_EXPENSES": Decimal("30"),
        }, "2024-12-31"))
        snap_p = snapshots.build_snapshot(_request("ACME", [rs_p], run_id="run-proxy")).snapshot
        m_p = metrics.compute_metric(snap_p.snapshot_id, "SOLV_INTEREST_COVER",
                                     "2024-12-31", persist=True)
        check(m_p.status == "CALCULATED_PROXY" and m_p.reason_code == "PROXY_FINANCE_EXPENSES",
              "利息保障代理态落盘")
        payload_p = adapters.report_financial_payload(snap_p.snapshot_id)
        row_p = next(r for r in payload_p.metrics if r.formula_id == "SOLV_INTEREST_COVER")
        check(row_p.status == "CALCULATED_PROXY" and row_p.reason_code == "PROXY_FINANCE_EXPENSES",
              "payload 代理行状态/原因正确")
        check(row_p.note is not None and "代理" in row_p.note and "FINANCE_EXPENSES" in row_p.note,
              f"payload 代理行 note 含代理说明（{row_p.note}）")

        # ---- _note_for 缺输入说明（含缺失科目） ----
        fake = S.MetricResult(
            metric_result_id="mr-x", snapshot_id="snap-x", formula_id="SOLV_CURRENT_RATIO",
            formula_version="1.0", period="2024-12-31", raw_value=None, display_value=None,
            unit=None, input_snapshot_item_refs=[], input_record_refs=[], status="MISSING_INPUT",
            reason_code="MISSING_REQUIRED_ITEM",
            calculation_detail={"missing": ["CURRENT_ASSETS"]}, created_at=_TS)
        note_m = adapters._note_for(fake)
        check(note_m is not None and "CURRENT_ASSETS" in note_m,
              f"_note_for 缺输入 note 含缺失科目（{note_m}）")
        fake_ok = S.MetricResult(
            metric_result_id="mr-y", snapshot_id="snap-x", formula_id="SOLV_CURRENT_RATIO",
            formula_version="1.0", period="2024-12-31", raw_value=Decimal("2"),
            display_value=Decimal("2.00"), unit="ratio", input_snapshot_item_refs=[],
            input_record_refs=[], status="CALCULATED_EXACT", reason_code=None,
            calculation_detail={}, created_at=_TS)
        check(adapters._note_for(fake_ok) is None, "_note_for 精确态 → None")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
