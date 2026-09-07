"""A7 真实样本主链验收 harness：Excel → Record Set → Reconciliation → Snapshot → Metric → Adapter。

用途（任务书 §13.2 / §16）：在临时库把 N 份已登记的 Excel 报表走完 V2 财务主链，
打印结构化验收摘要（来源 / 对账 / 快照 / 指标状态分布 / 抽查指标溯源 / 快照条目坐标回查）。
只调用 financial_v2 公开接口，不硬编码任何公司 / 金额 / 记录 ID / 固定 sheet / 页码。

用法:
  python -m scripts.run_financial_v2_chain \
    --company 300750 --db <临时库.db> \
    --scope consolidated --currency CNY \
    --declared-name 宁德时代 --detected-name 宁德时代 \
    --excel <资产负债.xlsx> --excel <利润.xlsx> --excel <现金流.xlsx> \
    [--period 2025-12-31] [--sample-items 10] [--validate-only]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from financial_v2 import excel_extractor as ex
from financial_v2 import metadata_confirmation as meta
from financial_v2 import normalization
from financial_v2 import progress
from financial_v2 import reconciliation as recon
from financial_v2 import schema as S
from financial_v2 import source_registry
from financial_v2 import store

logger = logging.getLogger(__name__)

_DEPS = {"openpyxl": openpyxl.__version__}
_EXTRACTOR_VERSION = "1.0"
_MAPPING_VERSION = "1.0"
_NORMALIZATION_VERSION = "1.0"

# 人工复核抽查指标（任务书 §13.2.6 要求的 8 项）。
_SAMPLE_FORMULAS = [
    "SOLV_CURRENT_RATIO", "SOLV_QUICK_RATIO", "SOLV_DEBT_RATIO",
    "PROF_ROE", "PROF_ROA", "OPER_AR_TURNOVER", "SOLV_INTEREST_COVER",
    "GROWTH_REVENUE",
]


def _latest_annual(periods: list[str]) -> str | None:
    annual = [p for p in periods if p.endswith("-12-31")]
    return max(annual) if annual else None


def _metric_row_to_dict(m) -> dict:
    return {
        "formula_id": m.formula_id,
        "formula_version": m.formula_version,
        "name": m.name,
        "period": m.period,
        "status": m.status,
        "reason_code": m.reason_code,
        "raw_value": str(m.raw_value) if m.raw_value is not None else None,
        "display_value": str(m.display_value) if m.display_value is not None else None,
        "unit": m.unit,
        "note": m.note,
        "input_snapshot_item_refs": m.input_snapshot_item_refs,
        "input_record_refs": m.input_record_refs,
    }


def _item_to_dict(it: S.SnapshotItem, addresses: list[str]) -> dict:
    return {
        "standard_item_code": it.standard_item_code,
        "report_period": it.report_period,
        "period_type": it.period_type,
        "statement_type": it.statement_type,
        "amount": str(it.amount) if it.amount is not None else None,
        "unit": it.unit,
        "source_refs_count": len(it.source_refs),
        "excel_addresses": addresses,
    }


def _record_address(rec: S.SourceFinancialRecord) -> str:
    loc = rec.locator
    if loc is not None and loc.kind == "excel" and loc.excel is not None:
        return f"{loc.excel.sheet_name}!{loc.excel.cell_address}"
    if loc is not None and loc.kind == "pdf" and loc.pdf is not None:
        return f"pdf p{loc.pdf.page_number}"
    return "?"


def run(company: str, excel_files: list[str], *, scope: str, currency: str,
        declared_name: str | None, detected_name: str | None,
        persist: bool, sample_items: int, target_period: str | None) -> dict:
    sources: list[dict] = []
    record_set_ids: list[str] = []

    for i, fp in enumerate(excel_files):
        path = str(Path(fp).resolve())
        source_name = Path(fp).name
        ctx = S.FinancialSourceContext(
            company_id=company, source_name=source_name,
            source_class="financial_statement",
            external_document_id=f"doc-{company}-{i}-{source_name}",
            declared_company_name=declared_name, detected_company_name=detected_name)
        reg = source_registry.register_source(path, ctx)
        sv = reg.version.source_version
        doc_id = reg.document.source_document_id

        pol = ex.ExcelExtractionPolicy(
            file_path=path, extractor_version=_EXTRACTOR_VERSION,
            mapping_rule_version=_MAPPING_VERSION,
            normalization_rule_version=_NORMALIZATION_VERSION, dependency_versions=_DEPS)
        ext = ex.extract_excel(sv, pol, persist=persist)

        meta.confirm(company, doc_id, "statement_scope", scope,
                     "user_declaration", "真实样本验收结构化声明", "acceptance")
        meta.confirm(company, doc_id, "currency", currency,
                     "user_declaration", "真实样本验收结构化声明", "acceptance")

        npol = normalization.NormalizationPolicy(
            extractor_version=_EXTRACTOR_VERSION, mapping_rule_version=_MAPPING_VERSION,
            normalization_rule_version=_NORMALIZATION_VERSION, dependency_versions=_DEPS)
        norm = normalization.normalize_record_set(ext.record_set_version, npol,
                                                  persist=persist)

        record_set_ids.append(norm.record_set_version)
        sources.append({
            "file": source_name,
            "subject_match_status": reg.document.subject_match_status,
            "source_version": sv,
            "candidates": len(ext.candidates),
            "extract_issues": len(ext.issues),
            "normalized": norm.normalized_count,
            "blocked": norm.blocked_count,
            "record_set_version": norm.record_set_version,
        })

    rec = recon.run_reconciliation(company, record_set_ids, persist=persist)
    reconciliation = {
        "run_id": rec.run_id,
        "input_record_set_ids": rec.input_record_set_ids,
        "single_source_count": rec.single_source_count,
        "matched_count": rec.matched_count,
        "conflict_count": rec.conflict_count,
        "insufficient_scope_count": rec.insufficient_scope_count,
    }

    req = progress.build_request_for_company(company)
    res = progress.run_pipeline(req, persist=persist)
    payload = res.payload

    if target_period is None and payload is not None:
        target_period = _latest_annual(payload.periods)

    summary: dict = {
        "company": company,
        "record_sets": sources,
        "reconciliation": reconciliation,
        "final_state": res.final_state,
        "snapshot_id": res.snapshot_id,
        "report_blocked": res.report_blocked,
    }
    if res.error:
        summary["error"] = res.error

    if payload is not None:
        sample_metrics = [
            _metric_row_to_dict(m)
            for m in payload.metrics
            if m.formula_id in _SAMPLE_FORMULAS and m.period == target_period
        ]
        sample_metrics.sort(key=lambda d: _SAMPLE_FORMULAS.index(d["formula_id"]))
        summary["as_of_date"] = payload.as_of_date
        summary["scope"] = payload.scope
        summary["currency"] = payload.currency
        summary["validity"] = payload.validity
        summary["periods"] = payload.periods
        summary["target_period"] = target_period
        summary["metric_count"] = len(payload.metrics)
        summary["metric_status_counts"] = res.metrics.status_counts if res.metrics else {}
        summary["exception_count"] = len(payload.exceptions)
        summary["exceptions"] = [
            {"item_code": e.standard_item_code, "period": e.report_period,
             "reason": e.reason}
            for e in payload.exceptions[:20]
        ]
        summary["sample_metrics"] = sample_metrics

    if res.snapshot_id:
        items = store.list_snapshot_items(res.snapshot_id)
        sample = items[:sample_items]
        addresses_by_record: dict[str, str] = {}
        for it in sample:
            for rid in it.source_refs:
                if rid not in addresses_by_record:
                    recd = store.get_record(rid)
                    addresses_by_record[rid] = _record_address(recd) if recd else "?"
        summary["snapshot_item_count"] = len(items)
        summary["snapshot_items_sample"] = [
            _item_to_dict(it, [addresses_by_record.get(r, "?") for r in it.source_refs])
            for it in sample
        ]

    return summary


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_financial_v2_chain",
        description="V2 财务主链真实样本验收 harness（临时库）")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--db", default=None, help="SQLite 路径（缺省用临时库，不污染生产库）")
    parser.add_argument("--excel", action="append", required=True,
                        help="Excel 报表路径（可重复）")
    parser.add_argument("--scope", default="consolidated", help="确认的 statement_scope")
    parser.add_argument("--currency", default="CNY", help="确认的 currency")
    parser.add_argument("--declared-name", default=None, help="声明公司名")
    parser.add_argument("--detected-name", default=None, help="检测公司名")
    parser.add_argument("--period", default=None, help="抽查指标目标期间（缺省取最新年报期）")
    parser.add_argument("--sample-items", type=int, default=10, help="抽查快照条目数")
    parser.add_argument("--validate-only", action="store_true",
                        help="只走主链不落盘（快照/指标不持久化）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    own_db = False
    db = args.db
    if db is None:
        fd, db = tempfile.mkstemp(suffix=".db", prefix="fv2_accept_")
        os.close(fd)
        own_db = True

    try:
        store.init_db(db)
        summary = run(args.company, args.excel, scope=args.scope,
                      currency=args.currency, declared_name=args.declared_name,
                      detected_name=args.detected_name,
                      persist=not args.validate_only,
                      sample_items=args.sample_items, target_period=args.period)
        summary["db"] = db
        summary["persist"] = not args.validate_only
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        if own_db:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(db + suffix)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
