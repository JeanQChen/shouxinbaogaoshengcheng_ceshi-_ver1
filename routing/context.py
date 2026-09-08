"""Phase 2 RouteContext 构造器（只读 I/O 层）。

把 company_id 折叠为 Router 所需的静态能力清单（RouteContext）：

- supported_db_fields / supported_metric_ids —— 静态能力，来自注册表，恒非空，
  决定 route（契约修正 A：只看能力是否支持）；
- available_db_fields / available_metric_ids —— 当前快照真实可用值，决定 DB executor
  返回 DB_RESULT_AVAILABLE 还是 DB_FIELD_UNAVAILABLE；
- available_document_ids / available_source_types —— 当前 evidence 可检索文档；
- report_as_of —— 当前快照 as_of_date（无快照为 None）；
- external_research_enabled —— 外部检索开关（默认开：当前为 Claude built-in web search）。

只读、不写任何库、不执行检索。无财务数据是合法空态（available_* 为空、report_as_of
为 None），不抛错；库未初始化等真实错误 fail-closed 抛错。

CLI: python -m routing.context --company 300750
"""

from __future__ import annotations

import logging

from evidence import store as estore
from financial_v2 import progress
from financial_v2 import snapshots
from financial_v2 import store as fstore
from routing import db_targets
from routing import schema as S

logger = logging.getLogger(__name__)

# 指标「可用」仅两种计算态（有真实值）；其余 MISSING_INPUT / PARTIAL_INPUT /
# ZERO_DENOMINATOR / NOT_APPLICABLE / BLOCKED_BY_SNAPSHOT 一律视为不可用。
_AVAILABLE_METRIC_STATUSES = ("CALCULATED_EXACT", "CALCULATED_PROXY")

# 外部检索默认开启：当前互联网检索走 Claude built-in web search（CLAUDE.md 技术栈）。
_DEFAULT_EXTERNAL_RESEARCH = True


# ---------------------------------------------------------------------------
# 纯派生函数（可独立测试，无 I/O）
# ---------------------------------------------------------------------------

def _available_field_codes(items) -> set[str]:
    """快照条目中有真实金额的 standard_item_code 集合。"""
    return {it.standard_item_code for it in items if it.amount is not None}


def _available_metric_ids(metric_results) -> set[str]:
    """已计算出真实值的 formula_id 集合（CALCULATED_EXACT/PROXY）。"""
    return {mr.formula_id for mr in metric_results if mr.status in _AVAILABLE_METRIC_STATUSES}


def _extract_current_documents(records) -> tuple[list[str], list[str]]:
    """由 DocumentRecord 列表派生 (available_document_ids, available_source_types)。

    status=current 的文档才可检索；按 document_id 去重（一份文档仅一个 current 版本）。
    """
    seen: dict[str, str] = {}
    for d in records:
        if d.status != "current":
            continue
        seen[d.document_id] = d.source_type
    ids = list(seen.keys())
    return ids, [seen[i] for i in ids]


# ---------------------------------------------------------------------------
# 只读数据源
# ---------------------------------------------------------------------------

def _current_document_records(company_id: str) -> list:
    """当前可检索文档记录：status=current 且有 current evidence set。"""
    out = []
    for d in estore.list_documents(company_id):
        if d.status != "current":
            continue
        if estore.current_document_version(company_id, d.document_id) is None:
            continue
        out.append(d)
    return out


def _resolve_snapshot(company_id: str, scope: str, currency: str,
                      as_of_date: str | None, purpose: str):
    """定位当前快照；无财务数据是合法空态（返回 None）。"""
    if as_of_date is not None:
        return snapshots.current_snapshot(company_id, scope, currency, as_of_date, purpose)
    try:
        req = progress.build_request_for_company(
            company_id, scope=scope, currency=currency, purpose=purpose)
    except ValueError as e:  # 无 current Record Set（合法空态，非错误）
        logger.info("公司 %s 无 current Record Set，快照置空: %s", company_id, e)
        return None
    return snapshots.current_snapshot(company_id, scope, currency, req.as_of_date, purpose)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_route_context(company_id: str, *, scope: str = "consolidated",
                        currency: str = "CNY", as_of_date: str | None = None,
                        purpose: str = "credit_analysis",
                        external_research_enabled: bool | None = None,
                        ) -> S.RouteContext:
    """把 company_id 折叠为 Router 所需 RouteContext（只读）。"""
    docs = _current_document_records(company_id)
    doc_ids, source_types = _extract_current_documents(docs)

    snap = _resolve_snapshot(company_id, scope, currency, as_of_date, purpose)
    report_as_of = snap.as_of_date if snap is not None else None
    # 健康门：report_blocked 快照不得贡献 available_*（DB executor 会返回
    # DB_FIELD_UNAVAILABLE，而非把被阻断快照的值当作可计算值）。
    healthy = snap is not None and not snap.report_blocked
    if not healthy:
        avail_fields: list[str] = []
        avail_metrics: list[str] = []
    else:
        avail_fields = sorted(
            _available_field_codes(fstore.list_snapshot_items(snap.snapshot_id)))
        avail_metrics = sorted(
            _available_metric_ids(fstore.list_metric_results(snap.snapshot_id)))

    return S.RouteContext(
        company_id=company_id,
        report_as_of=report_as_of,
        available_document_ids=doc_ids,
        available_source_types=source_types,
        supported_db_fields=db_targets.supported_db_fields(),
        supported_metric_ids=db_targets.supported_metric_ids(),
        available_db_fields=avail_fields,
        available_metric_ids=avail_metrics,
        external_research_enabled=(
            _DEFAULT_EXTERNAL_RESEARCH if external_research_enabled is None
            else external_research_enabled),
        scope=scope,
        currency=currency,
        purpose=purpose,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m routing.context",
        description="构建 RouteContext（只读，不执行检索）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--as-of-date", dest="as_of_date", default=None)
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH),
                        help="financial_v2 SQLite 库路径（dev/test 注入临时库）")
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH),
                        help="evidence SQLite 库路径（dev/test 注入临时库）")
    args = parser.parse_args(argv)

    fstore.init_db(args.fin_db)
    estore.init_db(args.ev_db)

    ctx = build_route_context(args.company_id, scope=args.scope, currency=args.currency,
                              as_of_date=args.as_of_date, purpose=args.purpose)
    print(json.dumps({
        "company_id": ctx.company_id,
        "report_as_of": ctx.report_as_of,
        "available_document_ids": ctx.available_document_ids,
        "available_source_types": ctx.available_source_types,
        "supported_db_fields": ctx.supported_db_fields,
        "supported_metric_ids": ctx.supported_metric_ids,
        "available_db_fields": ctx.available_db_fields,
        "available_metric_ids": ctx.available_metric_ids,
        "external_research_enabled": ctx.external_research_enabled,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
