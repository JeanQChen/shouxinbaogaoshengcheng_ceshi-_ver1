"""A7-1 V1 只读兼容适配（任务书 §10）：Snapshot/MetricResult → 可消费财务载荷。

职责边界（CLAUDE.md / 任务书硬要求）：
- 只读：仅读取 Snapshot / MetricResult / SnapshotException，不写 V1 `credit.db`，
  不调用 V1 跨来源求和查询（不 import `financial.metrics` / `financial.db`）；
- 返回结构供后续 Financial Worker 直接消费：指标值、展示值、状态、代理/缺失说明、
  期间、公式版本、证据引用（snapshot_item comparison_key + 底层 record_id）和
  `report_blocked`；
- 不在此任务调用 LLM 生成财务分析文字；
- 权威金额 raw_value 以 Decimal 返回，展示 display_value 已按公式规则舍入，绝不回写；
- V1 默认路径保持不变；V2 仅在消费方显式 feature flag + 有效 current snapshot 下启用，
  本模块只提供读取接口，不做开关判断（开关在 Streamlit / Worker 层）。

CLI:
  python -m financial_v2.adapters --snapshot <id> [--metrics-only] [--db <path>]
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from financial_v2 import formulas
from financial_v2 import metrics
from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

# 人类可读的代理/缺失说明（reason_code → 文案模板）。仅用于展示说明，
# 不含任何数值，不参与计算；未知 reason_code 回退到 reason_code 原串。
_NOTE_TEMPLATES: dict[str, str] = {
    "PROXY_FINANCE_EXPENSES": "利息保障倍数代理：缺少真实 INTEREST_EXPENSE，以 FINANCE_EXPENSES 近似",
    "MISSING_PRIOR_PERIOD": "缺少上一报告期输入（avg/同比无法计算）",
    "MISSING_REQUIRED_ITEM": "缺少必需输入科目",
    "MIXED_RECEIVABLE_BASIS_FORBIDDEN": "应收账款两口径禁止交叉拼接",
    "UNRESOLVED_CONFLICT": "存在未解决冲突，阻断计算",
    "AMBIGUOUS_RESTATEMENT": "重述选择不明，阻断计算",
    "SNAPSHOT_STALE": "快照已失效（stale/superseded），不作为当前报告输入",
    "QUARANTINED_INPUT": "输入已被隔离（quarantine）",
    "ZERO_DENOMINATOR": "分母为零",
    "NOT_APPLICABLE": "该期间不适用（非年报正式增长）",
}


@dataclass
class FinancialMetricRow:
    """单个指标的只读视图（供 Financial Worker 消费）。"""

    metric_result_id: str
    formula_id: str
    formula_version: str
    name: str | None
    period: str
    status: str
    reason_code: str | None
    raw_value: Decimal | None
    display_value: Decimal | None
    unit: str | None
    note: str | None
    input_snapshot_item_refs: list[str]
    input_record_refs: list[str]


@dataclass
class FinancialAnalysisPayload:
    """一个快照的完整只读财务载荷（§10）。"""

    snapshot_id: str
    company_id: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    report_blocked: bool
    validity: str | None
    periods: list[str]
    metrics: list[FinancialMetricRow]
    exceptions: list[S.SnapshotException]
    snapshot_item_refs: list[str]     # 全部指标引用的 SnapshotItem comparison_key（去重）
    source_record_refs: list[str]     # 全部指标引用的底层 SourceRecord.record_id（去重）


# ---------------------------------------------------------------------------
# 展示说明（reason_code → 文案，不造数）
# ---------------------------------------------------------------------------

def _note_for(m: S.MetricResult) -> str | None:
    """由 status/reason_code/calculation_detail 生成人类可读说明（精确态返回 None）。"""
    if m.status == "CALCULATED_EXACT":
        return None
    detail = m.calculation_detail or {}
    missing = detail.get("missing")
    base = _NOTE_TEMPLATES.get(m.reason_code or "", m.reason_code or "未知原因")
    if isinstance(missing, list) and missing:
        return f"{base}: {', '.join(str(x) for x in missing)}"
    return base


def _formula_name(formula_id: str, version: str) -> str | None:
    """读取公式展示名（纯读，fail-soft：未知公式返回 None，不阻断）。"""
    try:
        return formulas.get_formula(formula_id, version).name
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# 对外接口（§10）
# ---------------------------------------------------------------------------

def metrics_table_from_snapshot(snapshot_id: str) -> metrics.MetricsTableV2:
    """读取已持久化的 MetricResult，重建 MetricsTableV2（只读，不重算、不落盘）。"""
    snap = store.get_snapshot(snapshot_id)
    if snap is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")
    results = store.list_metric_results(snapshot_id)
    periods = sorted({r.period for r in results})
    return metrics.MetricsTableV2(
        snapshot_id=snapshot_id,
        periods=periods,
        results=results,
        status_counts=dict(Counter(r.status for r in results)),
    )


def report_financial_payload(snapshot_id: str) -> FinancialAnalysisPayload:
    """读取快照头 + 指标 + 异常，组装 FinancialAnalysisPayload（只读）。"""
    snap = store.get_snapshot(snapshot_id)
    if snap is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")
    results = store.list_metric_results(snapshot_id)
    exceptions = store.list_snapshot_exceptions(snapshot_id)
    validity = store.latest_snapshot_validity(snapshot_id)

    rows: list[FinancialMetricRow] = []
    snapshot_item_refs: list[str] = []
    source_record_refs: list[str] = []
    for m in results:
        rows.append(FinancialMetricRow(
            metric_result_id=m.metric_result_id,
            formula_id=m.formula_id,
            formula_version=m.formula_version,
            name=_formula_name(m.formula_id, m.formula_version),
            period=m.period,
            status=m.status,
            reason_code=m.reason_code,
            raw_value=m.raw_value,
            display_value=m.display_value,
            unit=m.unit,
            note=_note_for(m),
            input_snapshot_item_refs=list(m.input_snapshot_item_refs),
            input_record_refs=list(m.input_record_refs),
        ))
        snapshot_item_refs.extend(m.input_snapshot_item_refs)
        source_record_refs.extend(m.input_record_refs)

    periods = sorted({r.period for r in results})
    return FinancialAnalysisPayload(
        snapshot_id=snapshot_id,
        company_id=snap.company_id,
        as_of_date=snap.as_of_date,
        scope=snap.scope,
        currency=snap.currency,
        purpose=snap.purpose,
        report_blocked=snap.report_blocked,
        validity=validity,
        periods=periods,
        metrics=rows,
        exceptions=exceptions,
        snapshot_item_refs=sorted(set(snapshot_item_refs)),
        source_record_refs=sorted(set(source_record_refs)),
    )


# ---------------------------------------------------------------------------
# CLI（§11 / CLAUDE.md：每个核心模块可独立运行）
# ---------------------------------------------------------------------------

def _dec(v: Decimal | None) -> str | None:
    return str(v) if v is not None else None


def _metric_row_to_dict(r: FinancialMetricRow) -> dict:
    return {
        "metric_result_id": r.metric_result_id,
        "formula_id": r.formula_id,
        "formula_version": r.formula_version,
        "name": r.name,
        "period": r.period,
        "status": r.status,
        "reason_code": r.reason_code,
        "raw_value": _dec(r.raw_value),
        "display_value": _dec(r.display_value),
        "unit": r.unit,
        "note": r.note,
        "input_snapshot_item_refs": r.input_snapshot_item_refs,
        "input_record_refs": r.input_record_refs,
    }


def _payload_to_dict(p: FinancialAnalysisPayload) -> dict:
    return {
        "snapshot_id": p.snapshot_id,
        "company_id": p.company_id,
        "as_of_date": p.as_of_date,
        "scope": p.scope,
        "currency": p.currency,
        "purpose": p.purpose,
        "report_blocked": p.report_blocked,
        "validity": p.validity,
        "periods": p.periods,
        "metrics": [_metric_row_to_dict(r) for r in p.metrics],
        "exceptions": [
            {
                "comparison_key": e.comparison_key,
                "standard_item_code": e.standard_item_code,
                "exception_type": e.exception_type,
                "blocking_reason": e.blocking_reason,
                "impact_scope": e.impact_scope,
            }
            for e in p.exceptions
        ],
        "snapshot_item_refs": p.snapshot_item_refs,
        "source_record_refs": p.source_record_refs,
    }


def _table_to_dict(t: metrics.MetricsTableV2) -> dict:
    return {
        "snapshot_id": t.snapshot_id,
        "periods": t.periods,
        "status_counts": t.status_counts,
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.adapters", description="A7-1 V1 只读兼容适配")
    parser.add_argument("--db", default=str(store.DEFAULT_DB_PATH),
                        help="SQLite 库路径（dev/test 注入临时库）")
    parser.add_argument("--snapshot", required=True, dest="snapshot_id")
    parser.add_argument("--metrics-only", action="store_true",
                        help="只输出 metrics_table（指标数量/状态分布）")
    args = parser.parse_args(argv)
    store.init_db(args.db)

    if args.metrics_only:
        table = metrics_table_from_snapshot(args.snapshot_id)
        print(json.dumps(_table_to_dict(table), ensure_ascii=False, indent=2))
        return 0

    payload = report_financial_payload(args.snapshot_id)
    print(json.dumps(_payload_to_dict(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
