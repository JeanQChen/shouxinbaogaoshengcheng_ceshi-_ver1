"""Eval: RouteContext 构造器（routing/context.py）—— Phase 2 Commit 3。

用法: python -m evals.test_context

覆盖：
- 纯派生：可用字段过滤（amount None 排除）、可用指标过滤（仅 CALCULATED）、
  current 文档去重；
- 集成（临时 DB）：build_route_context 由真实快照派生 report_as_of /
  available_db_fields / available_metric_ids，supported_* 恒非空；
- 无财务数据公司 → available_* 空、report_as_of None，但不抛错（合法空态）。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import store as estore
from financial_v2 import progress
from financial_v2 import schema as FS
from financial_v2 import snapshots
from financial_v2 import store as fstore
from financial_v2 import validator
from routing import context


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_ctx_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> FS.SourceLocator:
    return FS.SourceLocator(kind="excel", excel=FS.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"

_STMT = {
    "CURRENT_ASSETS": "balance_sheet", "CURRENT_LIABILITIES": "balance_sheet",
    "TOTAL_ASSETS": "balance_sheet", "TOTAL_LIABILITIES": "balance_sheet",
    "TOTAL_EQUITY": "balance_sheet", "TOTAL_REVENUE": "income_statement",
    "NET_PROFIT": "income_statement",
}


def _seed_records(company: str, ext_id: str, specs: list[dict]) -> str:
    """直接构造标准化记录并落盘（跳过候选/映射），返回 record_set_version。"""
    source_document_id = FS.scope_source_document_id(company, ext_id)
    source_version = FS.derive_source_version(
        source_document_id, hashlib.sha256(ext_id.encode()).hexdigest())
    fstore.register_source_atomic(
        FS.FinancialSourceDocument(
            source_document_id=source_document_id, company_id=company,
            source_name=f"{ext_id}.xlsx", source_class="financial_statement",
            declared_company_name=company, detected_company_name=company,
            subject_match_status="matched", created_at=_TS),
        FS.FinancialSourceVersion(
            source_version=source_version, source_document_id=source_document_id,
            file_sha256=hashlib.sha256(ext_id.encode()).hexdigest(), file_type="xlsx",
            file_size=100, document_id=None, document_version=None, created_at=_TS))

    rs = FS.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
    records = []
    for i, sp in enumerate(specs):
        r = FS.SourceFinancialRecord(
            record_id="", record_set_version=rs, company_id=company,
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
        r.record_id = FS.derive_record_id(rs, FS.record_identity_fields(r))
        records.append(r)

    record_set = FS.FinancialRecordSet(
        record_set_version=rs, source_version=source_version,
        extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
        normalization_rule_version="1.0", dependency_versions={},
        report_periods=sorted({r.report_period for r in records}),
        currency="CNY", unit="wan_yuan", statement_scope="consolidated",
        audit_status="audited", block_count=0, record_count=len(records),
        created_at=_TS, input_candidate_set_version=rs)
    fstore.commit_normalization_atomic(record_set, records, [], source_document_id)
    return rs


def _specs(values: dict[str, Decimal], period: str) -> list[dict]:
    return [{"item_code": code, "value": val, "period": period,
             "statement_type": _STMT.get(code, "balance_sheet")}
            for code, val in values.items()]


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

    # ---- 纯派生：可用字段 ----
    items = [
        SimpleNamespace(standard_item_code="TOTAL_ASSETS", amount=Decimal("500")),
        SimpleNamespace(standard_item_code="NET_PROFIT", amount=None),
    ]
    check(context._available_field_codes(items) == {"TOTAL_ASSETS"},
          "amount 为 None 的字段不计入 available_db_fields")

    # ---- 纯派生：可用指标 ----
    mrs = [
        SimpleNamespace(formula_id="SOLV_CURRENT_RATIO", status="CALCULATED_EXACT"),
        SimpleNamespace(formula_id="PROF_ROE", status="MISSING_INPUT"),
        SimpleNamespace(formula_id="PROF_GROSS_MARGIN", status="CALCULATED_PROXY"),
        SimpleNamespace(formula_id="PROF_ROA", status="ZERO_DENOMINATOR"),
    ]
    check(context._available_metric_ids(mrs) == {"SOLV_CURRENT_RATIO", "PROF_GROSS_MARGIN"},
          "仅 CALCULATED_EXACT/PROXY 计入 available_metric_ids")

    # ---- 纯派生：current 文档去重 ----
    docs = [
        SimpleNamespace(document_id="d1", source_type="annual_report", status="current"),
        SimpleNamespace(document_id="d1", source_type="annual_report", status="superseded"),
        SimpleNamespace(document_id="d2", source_type="announcement", status="current"),
        SimpleNamespace(document_id="d3", source_type="annual_report", status="registered"),
    ]
    ids, types = context._extract_current_documents(docs)
    check(ids == ["d1", "d2"] and types == ["annual_report", "announcement"],
          "仅 status=current 计入，且按 document_id 去重")

    # ---- 集成：真实快照 → RouteContext ----
    fin_db = _tmp_db()
    ev_db = _tmp_db()
    try:
        fstore.init_db(fin_db)
        estore.init_db(ev_db)

        rs = _seed_records("ACME", "ok", _specs({
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
            "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
            "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
            "NET_PROFIT": Decimal("120"),
        }, "2024-12-31"))
        req = snapshots.SnapshotBuildRequest(
            company_id="ACME", as_of_date="2024-12-31", scope="consolidated",
            currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
            reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
            restatement_selection={}, policy_adjustments={}, run_id="run-ctx")
        res = progress.run_pipeline(req)
        check(res.final_state == "completed", "前置：run_pipeline 成功构建快照+指标")

        ctx = context.build_route_context("ACME")
        check(ctx.company_id == "ACME", "company_id 回填")
        check(ctx.report_as_of == "2024-12-31", "report_as_of = 快照 as_of_date")
        check(ctx.scope == "consolidated" and ctx.currency == "CNY"
              and ctx.purpose == "credit_analysis",
              "RouteContext 回填 scope/currency/purpose 维度")
        check(set(ctx.available_db_fields) == {
            "CURRENT_ASSETS", "CURRENT_LIABILITIES", "TOTAL_ASSETS",
            "TOTAL_LIABILITIES", "TOTAL_EQUITY", "TOTAL_REVENUE", "NET_PROFIT"},
            f"available_db_fields 来自快照条目（{ctx.available_db_fields}）")
        avail_metrics = set(ctx.available_metric_ids)
        check("SOLV_CURRENT_RATIO" in avail_metrics,
              "必算指标 SOLV_CURRENT_RATIO 计入 available_metric_ids")
        check(avail_metrics <= set(ctx.supported_metric_ids),
              "available_metric_ids ⊆ supported_metric_ids（可用是能力的子集）")
        check("PROF_GROSS_MARGIN" not in avail_metrics,
              "缺输入指标（毛利率缺营业成本）不计入可用")
        check("TOTAL_ASSETS" in ctx.supported_db_fields
              and "NET_PROFIT" in ctx.supported_db_fields,
              "supported_db_fields 来自注册表（恒非空）")
        check("SOLV_CURRENT_RATIO" in ctx.supported_metric_ids
              and "PROF_ROE" in ctx.supported_metric_ids,
              "supported_metric_ids 来自 Formula Registry（恒非空）")
        check(ctx.available_document_ids == [] and ctx.available_source_types == [],
              "无 evidence 文档时 available_document_ids 为空")
        check(ctx.external_research_enabled is True, "外部检索默认开启")

        # ---- 无财务数据公司 → 合法空态 ----
        empty = context.build_route_context("NO-CO")
        check(empty.report_as_of is None, "无快照公司 report_as_of=None")
        check(empty.available_db_fields == [] and empty.available_metric_ids == [],
              "无快照公司 available_* 为空")
        check(len(empty.supported_db_fields) > 0 and len(empty.supported_metric_ids) > 0,
              "无快照公司 supported_* 仍恒非空（路由能力不受数据影响）")

        # ---- 健康门：report_blocked 快照不进 available_* ----
        blocked_snap = SimpleNamespace(snapshot_id="snap-blocked",
                                       as_of_date="2024-12-31", report_blocked=True)
        orig_resolve = context._resolve_snapshot
        context._resolve_snapshot = lambda *a, **k: blocked_snap
        try:
            blocked_ctx = context.build_route_context("ACME")
            check(blocked_ctx.report_as_of == "2024-12-31",
                  "report_blocked 快照仍回填 report_as_of")
            check(blocked_ctx.available_db_fields == []
                  and blocked_ctx.available_metric_ids == [],
                  "report_blocked 快照不进 available_*（健康门）")
        finally:
            context._resolve_snapshot = orig_resolve
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
