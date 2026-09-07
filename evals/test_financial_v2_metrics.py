"""Eval: financial_v2 指标计算（A6 Commit 5：metrics.py）。

用法: python -m evals.test_financial_v2_metrics

覆盖（任务书 §8 / §13.2 指标矩阵）：
- 显式绑定 snapshot_id + formula_id + period；未知快照/公式 fail-closed KeyError；
- 全输入 → CALCULATED_EXACT 且 raw_value 未舍入（Decimal 权威），display_value
  ROUND_HALF_UP 展示值、unit 分类（ratio/percent/yuan）；
- 双引用溯源：input_snapshot_item_refs（comparison_key）+ input_record_refs（record_id）；
- compute_all 整批：periods 派生、status_counts、单事务落盘 + checkpoint、幂等重放
  全复用不重复写（不新增指标行 / 不新增 checkpoint）；
- 缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)；零分母 → ZERO_DENOMINATOR；
- 未解决冲突阻断依赖科目 → BLOCKED_BY_SNAPSHOT(UNRESOLVED_CONFLICT)，无关科目不受影响；
- 速动比率 policy_adjustments 条件扣除（快照头解析 → 真实记录金额）。

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

from financial_v2 import formulas as F
from financial_v2 import metrics
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_metric_")
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

# 科目 → 报表类型（指标计算按 item_code 聚合，statement_type 仅影响 comparison_key 唯一性）。
_STMT = {
    "CURRENT_ASSETS": "balance_sheet", "CURRENT_LIABILITIES": "balance_sheet",
    "TOTAL_LIABILITIES": "balance_sheet", "TOTAL_ASSETS": "balance_sheet",
    "TOTAL_EQUITY": "balance_sheet", "INVENTORY": "balance_sheet",
    "PREPAYMENTS": "balance_sheet", "OTHER_CURRENT_ASSETS": "balance_sheet",
    "ACCOUNTS_RECEIVABLE": "balance_sheet", "ACCOUNTS_RECEIVABLE_COMBINED": "balance_sheet",
    "SHORT_TERM_BORROWINGS": "balance_sheet", "LONG_TERM_BORROWINGS": "balance_sheet",
    "BONDS_PAYABLE": "balance_sheet", "LEASE_LIABILITIES": "balance_sheet",
    "NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR": "balance_sheet",
    "TOTAL_REVENUE": "income_statement", "OPERATING_COST": "income_statement",
    "NET_PROFIT": "income_statement", "OPERATING_PROFIT": "income_statement",
    "TOTAL_PROFIT": "income_statement", "INTEREST_EXPENSE": "income_statement",
    "FINANCE_EXPENSES": "income_statement", "SALES_EXPENSES": "income_statement",
    "ADMIN_EXPENSES": "income_statement", "R_AND_D_EXPENSES": "income_statement",
    "OPERATING_CASH_FLOW": "cash_flow", "INVESTING_CASH_FLOW": "cash_flow",
    "FINANCING_CASH_FLOW": "cash_flow",
    F.DEPRECIATION: "cash_flow", F.AMORTIZATION: "cash_flow", F.CAPEX: "cash_flow",
}

# 全输入 fixture（与 test_financial_v2_formulas 同源，保证期望值一致）。
_CURRENT: dict[str, Decimal] = {
    "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
    "TOTAL_LIABILITIES": Decimal("300"), "TOTAL_ASSETS": Decimal("500"),
    "TOTAL_EQUITY": Decimal("200"), "INVENTORY": Decimal("50"),
    "PREPAYMENTS": Decimal("10"), "OTHER_CURRENT_ASSETS": Decimal("40"),
    "TOTAL_REVENUE": Decimal("1000"), "OPERATING_COST": Decimal("600"),
    "NET_PROFIT": Decimal("120"), "OPERATING_PROFIT": Decimal("160"),
    "TOTAL_PROFIT": Decimal("150"), "INTEREST_EXPENSE": Decimal("20"),
    "FINANCE_EXPENSES": Decimal("30"), "SALES_EXPENSES": Decimal("40"),
    "ADMIN_EXPENSES": Decimal("50"), "R_AND_D_EXPENSES": Decimal("10"),
    "OPERATING_CASH_FLOW": Decimal("200"), "INVESTING_CASH_FLOW": Decimal("-80"),
    "FINANCING_CASH_FLOW": Decimal("-40"),
    "ACCOUNTS_RECEIVABLE": Decimal("90"), "ACCOUNTS_RECEIVABLE_COMBINED": Decimal("110"),
    "SHORT_TERM_BORROWINGS": Decimal("50"), "LONG_TERM_BORROWINGS": Decimal("80"),
    "BONDS_PAYABLE": Decimal("30"), "LEASE_LIABILITIES": Decimal("10"),
    "NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR": Decimal("20"),
    F.DEPRECIATION: Decimal("25"), F.AMORTIZATION: Decimal("5"),
    F.CAPEX: Decimal("60"),
}

_PRIOR: dict[str, Decimal] = {
    "TOTAL_ASSETS": Decimal("400"), "INVENTORY": Decimal("40"),
    "ACCOUNTS_RECEIVABLE": Decimal("70"), "ACCOUNTS_RECEIVABLE_COMBINED": Decimal("90"),
    "TOTAL_REVENUE": Decimal("800"), "NET_PROFIT": Decimal("100"),
    "TOTAL_LIABILITIES": Decimal("250"), "TOTAL_EQUITY": Decimal("150"),
    "OPERATING_CASH_FLOW": Decimal("150"), "INVESTING_CASH_FLOW": Decimal("-60"),
    "FINANCING_CASH_FLOW": Decimal("-30"),
}

# 全输入时部分公式的期望 raw_value（未舍入）。
_EXPECTED: dict[str, str] = {
    "SOLV_CURRENT_RATIO": "2",
    "SOLV_QUICK_RATIO": "1.4",
    "SOLV_DEBT_RATIO": "0.6",
    "SOLV_INTEREST_COVER": "8.5",
    "PROF_GROSS_MARGIN": "0.4",
    "PROF_ROE": "0.6",
    "PROF_ROA": "0.24",
    "GROWTH_REVENUE": "0.25",
    "OPER_ASSET_TURNOVER": "2.222222222222222222",
    "OPER_AR_TURNOVER": "12.5",
    "CASH_OCF_TO_NP": "1.666666666666666667",
    "EBITDA": "200",
    "INTEREST_BEARING_DEBT": "190",
    "FREE_CASH_FLOW": "140",
}

_ALL_FORMULAS = list(F.ACTIVE_FORMULA_VERSIONS.keys())


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


def _request(company, record_set_ids, *, policy_adjustments=None, run_id="run-metr"):
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=None, required_formula_ids=list(_ALL_FORMULAS),
        restatement_selection={}, policy_adjustments=policy_adjustments or {},
        run_id=run_id)


def _specs(values: dict[str, Decimal], period: str) -> list[dict]:
    return [{"item_code": code, "value": val, "period": period,
             "statement_type": _STMT.get(code, "balance_sheet")}
            for code, val in values.items()]


def _record_id(rs: str, code: str, period: str) -> str:
    return next(r.record_id for r in store.list_records(rs)
                if r.standard_item_code == code and r.report_period == period)


def _close(a: Decimal, b: Decimal, eps: str = "0.000001") -> bool:
    return abs(a - b) < Decimal(eps)


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

        # ---- 全输入快照（31 科目 × 两期）----
        rs_full = seed.persist_records(
            "full", _specs(_CURRENT, "2024-12-31") + _specs(_PRIOR, "2023-12-31"))
        snap_res = snapshots.build_snapshot(_request("ACME", [rs_full]))
        snap_id = snap_res.snapshot.snapshot_id
        check(not snap_res.report_blocked, "全输入快照 report_blocked=False")
        check(snap_res.current_switched, "首次构建切换 current")

        # ---- 未知快照/公式 fail-closed ----
        for fn in (lambda: metrics.compute_metric("snap-nope", "SOLV_CURRENT_RATIO", "2024-12-31"),
                   lambda: metrics.compute_metric(snap_id, "NOPE", "2024-12-31")):
            raised = False
            try:
                fn()
            except KeyError:
                raised = True
            check(raised, "未知 snapshot_id / formula_id → KeyError（fail-closed）")

        # ---- 全输入 → CALCULATED_EXACT + raw 未舍入 + unit + display 舍入 ----
        for fid, exp_s in _EXPECTED.items():
            m = metrics.compute_metric(snap_id, fid, "2024-12-31", persist=False)
            exp = Decimal(exp_s)
            check(m.status == "CALCULATED_EXACT", f"{fid} 全输入 → CALCULATED_EXACT")
            if m.raw_value is not None:
                check(_close(m.raw_value, exp), f"{fid} raw_value ≈ {exp_s}（得 {m.raw_value}）")
                check(m.display_value is not None and m.unit is not None,
                      f"{fid} 有 display_value/unit")

        m = metrics.compute_metric(snap_id, "OPER_ASSET_TURNOVER", "2024-12-31", persist=False)
        check(m.raw_value == Decimal("1000") / Decimal("450"),
              "资产周转率 raw_value 未舍入（1000/450）")
        check(m.display_value == Decimal("2.22"), "资产周转率 display ROUND_HALF_UP → 2.22")
        check(m.unit == "ratio", "资产周转率 unit=ratio")

        m = metrics.compute_metric(snap_id, "PROF_ROE", "2024-12-31", persist=False)
        check(m.unit == "percent" and m.display_value == Decimal("60.00"),
              "ROE unit=percent 且 display ×100 → 60.00（raw 0.6）")

        m = metrics.compute_metric(snap_id, "EBITDA", "2024-12-31", persist=False)
        check(m.unit == "yuan" and m.display_value == Decimal("200.00"),
              "EBITDA unit=yuan 且 display 200.00")

        # ---- 双引用溯源 ----
        m = metrics.compute_metric(snap_id, "SOLV_CURRENT_RATIO", "2024-12-31", persist=False)
        check(len(m.input_snapshot_item_refs) >= 2, "流动比率双引用（item refs ≥2）")
        check(len(m.input_record_refs) >= 2, "流动比率双引用（record refs ≥2）")
        check(all(ref.startswith("ck-") for ref in m.input_snapshot_item_refs),
              "input_snapshot_item_refs 为 comparison_key（ck- 前缀）")

        # ---- 持久化 + 读回 + 幂等（compute_metric 单条不写 checkpoint）----
        m1 = metrics.compute_metric(snap_id, "SOLV_CURRENT_RATIO", "2024-12-31", persist=True)
        mread = store.get_metric_result(snap_id, "SOLV_CURRENT_RATIO", "1.0", "2024-12-31")
        check(mread is not None and mread.raw_value == Decimal("2"),
              "compute_metric persist=True 落库读回")
        check(_count(db, "SELECT COUNT(*) FROM checkpoints WHERE run_id=?",
                     (f"metrics:{snap_id}",)) == 0,
              "单个 compute_metric 不写 checkpoint")

        # ---- compute_all：整批 + checkpoint + 幂等重放 ----
        table = metrics.compute_all(snap_id)
        check(len(table.periods) == 2, f"compute_all periods 派生为两期（{table.periods}）")
        check(len(table.results) == 28 * 2, f"compute_all 28 公式 × 2 期（{len(table.results)}）")
        check(table.status_counts.get("CALCULATED_EXACT", 0) >= 28,
              "compute_all 2024 期至少 28 个 CALCULATED_EXACT")
        check(_count(db, "SELECT COUNT(*) FROM checkpoints WHERE run_id=?",
                     (f"metrics:{snap_id}",)) == 1,
              "compute_all 落盘一次 checkpoint")

        cnt_before = _count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                            (snap_id,))
        table2 = metrics.compute_all(snap_id)
        cnt_after = _count(db, "SELECT COUNT(*) FROM metric_result WHERE snapshot_id=?",
                           (snap_id,))
        check(cnt_after == cnt_before, "compute_all 幂等重放不新增指标行")
        check(_count(db, "SELECT COUNT(*) FROM checkpoints WHERE run_id=?",
                     (f"metrics:{snap_id}",)) == 1,
              "compute_all 幂等重放不重复写 checkpoint（全复用跳过）")

        # ---- 缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD) ----
        rs_noprior = seed.persist_records(
            "noprior", _specs({"TOTAL_REVENUE": Decimal("1000")}, "2024-12-31"))
        snap_np = snapshots.build_snapshot(_request("ACME", [rs_noprior], run_id="run-np"))
        g = metrics.compute_metric(snap_np.snapshot.snapshot_id, "GROWTH_REVENUE",
                                   "2024-12-31", persist=False)
        check(g.status == "MISSING_INPUT" and g.reason_code == "MISSING_PRIOR_PERIOD",
              "增长率缺前期 → MISSING_INPUT(MISSING_PRIOR_PERIOD)")

        # ---- 零分母 → ZERO_DENOMINATOR ----
        rs_zero = seed.persist_records("zero", _specs({
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("0"),
        }, "2024-12-31"))
        snap_z = snapshots.build_snapshot(_request("ACME", [rs_zero], run_id="run-zero"))
        z = metrics.compute_metric(snap_z.snapshot.snapshot_id, "SOLV_CURRENT_RATIO",
                                   "2024-12-31", persist=False)
        check(z.status == "ZERO_DENOMINATOR" and z.raw_value is None,
              "流动比率分母 0 → ZERO_DENOMINATOR")

        # ---- 未解决冲突 → BLOCKED_BY_SNAPSHOT(UNRESOLVED_CONFLICT)，无关科目不受影响 ----
        rs_conf = seed.persist_records("conf", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("500"), "period": "2024-12-31"},
            {"item_code": "TOTAL_ASSETS", "value": Decimal("700"), "period": "2024-12-31"},
            {"item_code": "TOTAL_LIABILITIES", "value": Decimal("300"), "period": "2024-12-31"},
            {"item_code": "CURRENT_ASSETS", "value": Decimal("200"), "period": "2024-12-31"},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("100"), "period": "2024-12-31"},
        ])
        snap_c = snapshots.build_snapshot(_request("ACME", [rs_conf], run_id="run-conf"))
        check(snap_c.report_blocked, "冲突快照 report_blocked=True")
        dr = metrics.compute_metric(snap_c.snapshot.snapshot_id, "SOLV_DEBT_RATIO",
                                    "2024-12-31", persist=False)
        check(dr.status == "BLOCKED_BY_SNAPSHOT" and dr.reason_code == "UNRESOLVED_CONFLICT",
              "资产负债率依赖冲突 TOTAL_ASSETS → BLOCKED_BY_SNAPSHOT(UNRESOLVED_CONFLICT)")
        cr = metrics.compute_metric(snap_c.snapshot.snapshot_id, "SOLV_CURRENT_RATIO",
                                    "2024-12-31", persist=False)
        check(cr.status == "CALCULATED_EXACT" and _close(cr.raw_value, Decimal("2")),
              "无关科目（流动比率）不受冲突影响仍计算")

        # ---- 速动比率 policy_adjustments 条件扣除（快照头解析 → 真实记录金额）----
        rs_qr = seed.persist_records("qr", _specs({
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
            "INVENTORY": Decimal("50"), "PREPAYMENTS": Decimal("10"),
            "OTHER_CURRENT_ASSETS": Decimal("40"),
        }, "2024-12-31"))
        snap_qr0 = snapshots.build_snapshot(_request("ACME", [rs_qr], run_id="run-qr0"))
        qr0 = metrics.compute_metric(snap_qr0.snapshot.snapshot_id, "SOLV_QUICK_RATIO",
                                     "2024-12-31", persist=False)
        check(_close(qr0.raw_value, Decimal("1.4")), "速动比率无 policy → 1.4")

        oca_id = _record_id(rs_qr, "OTHER_CURRENT_ASSETS", "2024-12-31")
        snap_qr1 = snapshots.build_snapshot(_request(
            "ACME", [rs_qr], run_id="run-qr1",
            policy_adjustments={"quick_ratio_other_current_asset": [oca_id]}))
        qr1 = metrics.compute_metric(snap_qr1.snapshot.snapshot_id, "SOLV_QUICK_RATIO",
                                     "2024-12-31", persist=False)
        check(_close(qr1.raw_value, Decimal("1.0")),
              "速动比率经 policy 扣除非速动 OTHER_CURRENT_ASSETS → 1.0")
        check(qr1.calculation_detail.get("excluded_other_current_asset_applied") is True,
              "速动比率 policy 扣除在 calculation_detail 标记")

    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _count(db: str, sql: str, params: tuple) -> int:
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
