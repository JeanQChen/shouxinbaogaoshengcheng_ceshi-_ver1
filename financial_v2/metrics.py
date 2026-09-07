"""A6-4 指标计算（任务书 §8）：Snapshot → MetricResult。

职责边界（CLAUDE.md / 任务书硬要求）：
- 所有计算显式绑定 snapshot_id；禁止 V2 计算入口只有 company_id，禁止回读 V1 财务表拼数；
- 全程 Decimal（raw_value 未舍入权威值），展示层 ROUND_HALF_UP，缺输入 ≠ 0；
- 数字全部由 formulas.py 白名单 callable 计算，本模块只做「快照取数 → 组装上下文 → 调 callable
  → 组装 MetricResult」；不自己算任何指标，不调用 LLM；
- 结果持久化走 store.commit_metrics_atomic 单事务原子提交/严格复用；compute_all 整批先内存
  验证再一次性落盘（含 checkpoint），单个 compute_metric 不写 checkpoint。

状态与原因码见 formulas.py 与 schema.METRIC_STATUSES/METRIC_REASON_CODES。本模块新增两类
BLOCKED_BY_SNAPSHOT 的判定：
- 快照 validity ∈ {stale, superseded} → SNAPSHOT_STALE；
- 公式依赖科目的快照异常 ∈ {UNRESOLVED_CONFLICT, AMBIGUOUS_RESTATEMENT, QUARANTINED_INPUT,
  STALE_RESOLUTION} → 对应 reason_code（STALE_RESOLUTION 归入 UNRESOLVED_CONFLICT）。

CLI:
  python -m financial_v2.metrics compute --snapshot <id> --formula <id> --period <period> [--validate-only]
  python -m financial_v2.metrics compute-all --snapshot <id> [--period <period>] [--validate-only]
  （--db 为全局参数，须置于子命令前）
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from financial_v2 import formulas
from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

# 快照异常类型 → BLOCKED_BY_SNAPSHOT reason_code（其余异常类型视为输入缺失，走 MISSING_INPUT）。
_BLOCKING_REASON: dict[str, str] = {
    "UNRESOLVED_CONFLICT": "UNRESOLVED_CONFLICT",
    "AMBIGUOUS_RESTATEMENT": "AMBIGUOUS_RESTATEMENT",
    "QUARANTINED_INPUT": "QUARANTINED_INPUT",
    "STALE_RESOLUTION": "UNRESOLVED_CONFLICT",
}

# 需要前期（avg / 同比）的期间要求。
_PRIOR_PERIOD_REQUIREMENTS = frozenset({"flow/avg", "yoy_flow", "yoy_end"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MetricsTableV2:
    """compute_all 的整批结果（§8.1）。"""

    snapshot_id: str
    periods: list[str]
    results: list[S.MetricResult]
    status_counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 期间推导 / 取数
# ---------------------------------------------------------------------------

def _prior_period(period: str, period_type: str) -> str | None:
    """由当前报告期推导上一报告期（annual → 上一年年末；quarterly → 上一季末；interim → 上半年末）。"""
    try:
        y, m, _ = (int(x) for x in period.split("-"))
    except (ValueError, AttributeError):
        return None
    if period_type == "annual":
        return f"{y - 1:04d}-12-31"
    if period_type == "quarterly":
        return {"03": f"{y - 1:04d}-12-31", "06": f"{y:04d}-03-31",
                "09": f"{y:04d}-06-30", "12": f"{y:04d}-09-30"}.get(f"{m:02d}")
    if period_type == "interim":
        return f"{y:04d}-06-30" if m == 12 else f"{y - 1:04d}-12-31"
    return None


def _items_for_period(items: list[S.SnapshotItem], period: str,
                      scope: str, currency: str) -> list[S.SnapshotItem]:
    return [it for it in items
            if it.report_period == period and it.statement_scope == scope
            and it.currency == currency]


def _period_type_of(items: list[S.SnapshotItem], period: str,
                    scope: str, currency: str) -> str:
    types = {it.period_type for it in _items_for_period(items, period, scope, currency)}
    if not types:
        raise KeyError(f"snapshot 无期间 {period}")
    if len(types) != 1:
        raise ValueError(f"期间 {period} period_type 不唯一: {sorted(types)}")
    return next(iter(types))


def _gather(items: list[S.SnapshotItem], codes: set[str],
            scope: str, currency: str) -> dict[str, dict[str, tuple]]:
    """返回 {report_period: {standard_item_code: (amount, comparison_key, source_refs)}}。"""
    by_period: dict[str, dict[str, tuple]] = {}
    for it in items:
        if it.standard_item_code not in codes or it.amount is None:
            continue
        if it.statement_scope != scope or it.currency != currency:
            continue
        by_period.setdefault(it.report_period, {})[it.standard_item_code] = (
            it.amount, it.comparison_key, it.source_refs)
    return by_period


def _amounts(period_map: dict[str, tuple]) -> dict[str, Decimal]:
    return {code: v[0] for code, v in period_map.items()}


def _collect_refs(current: dict[str, tuple], prior: dict[str, tuple]
                  ) -> tuple[list[str], list[str]]:
    item_refs: list[str] = []
    record_refs: list[str] = []
    for pm in (current, prior):
        for _code, (_amount, ck, refs) in pm.items():
            item_refs.append(ck)
            record_refs.extend(refs)
    return item_refs, record_refs


# ---------------------------------------------------------------------------
# 政策调整解析（速动比率条件扣除）
# ---------------------------------------------------------------------------

def _quick_ratio_policy_amount(snapshot: S.FinancialSnapshot) -> Decimal | None:
    """解析速动比率 policy_adjustments 的确认扣除金额（真实记录引用，绝不凭空造数）。"""
    for pa in snapshot.policy_adjustments:
        if pa.adjustment_type != "quick_ratio_other_current_asset":
            continue
        if pa.decision != "EXCLUDE_CONFIRMED_RECORDS":
            continue
        total = Decimal("0")
        for ref in pa.record_refs:
            rec = store.get_record(ref)
            if rec is not None and rec.std_value is not None:
                total += rec.std_value
        return total
    return None


# ---------------------------------------------------------------------------
# 结果组装
# ---------------------------------------------------------------------------

def _build_metric(snapshot_id: str, fd: S.FormulaDefinition, period: str,
                  outcome: formulas.FormulaOutcome, item_refs: list[str],
                  record_refs: list[str]) -> S.MetricResult:
    raw = outcome.raw_value
    status = outcome.status
    unit: str | None = None
    display: Decimal | None = None
    if raw is not None:
        unit = formulas.unit_for(fd.formula_id)
        display = formulas.round_display(fd.formula_id, raw)
    detail = dict(outcome.detail)
    detail["formula_version"] = fd.formula_version
    detail["impl_version"] = fd.impl_version
    return S.MetricResult(
        metric_result_id=S.derive_metric_result_id(snapshot_id, fd.formula_id,
                                                   fd.formula_version, period),
        snapshot_id=snapshot_id,
        formula_id=fd.formula_id,
        formula_version=fd.formula_version,
        period=period,
        raw_value=raw,
        display_value=display,
        unit=unit,
        input_snapshot_item_refs=sorted(set(item_refs)),
        input_record_refs=sorted(set(record_refs)),
        status=status,
        reason_code=outcome.reason_code,
        calculation_detail=detail,
        created_at=_utcnow(),
    )


def _build_blocked(snapshot_id: str, fd: S.FormulaDefinition, period: str,
                   reason_code: str, detail: dict) -> S.MetricResult:
    return S.MetricResult(
        metric_result_id=S.derive_metric_result_id(snapshot_id, fd.formula_id,
                                                   fd.formula_version, period),
        snapshot_id=snapshot_id,
        formula_id=fd.formula_id,
        formula_version=fd.formula_version,
        period=period,
        raw_value=None,
        display_value=None,
        unit=None,
        input_snapshot_item_refs=[],
        input_record_refs=[],
        status="BLOCKED_BY_SNAPSHOT",
        reason_code=reason_code,
        calculation_detail={"formula_version": fd.formula_version,
                            "impl_version": fd.impl_version, **detail},
        created_at=_utcnow(),
    )


# ---------------------------------------------------------------------------
# 对外接口（§8.1）
# ---------------------------------------------------------------------------

def _resolve_formula(snapshot: S.FinancialSnapshot, formula_id: str) -> S.FormulaDefinition:
    """按快照锁定的公式版本解析定义（无锁定则回退活跃版本），未知公式 fail-closed。"""
    version = snapshot.required_formula_versions.get(formula_id) \
        or formulas.ACTIVE_FORMULA_VERSIONS.get(formula_id)
    if version is None:
        raise KeyError(f"公式未注册或未锁定: {formula_id}")
    return formulas.get_formula(formula_id, version)


def compute_metric(snapshot_id: str, formula_id: str, period: str,
                   persist: bool = True) -> S.MetricResult:
    """计算单个指标（显式绑定 snapshot_id + formula_id + period）。"""
    snapshot = store.get_snapshot(snapshot_id)
    if snapshot is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")
    fd = _resolve_formula(snapshot, formula_id)

    # 快照失效 → 阻断（§9：stale 快照不得作为当前报告输入）。
    validity = store.latest_snapshot_validity(snapshot_id)
    if validity in ("stale", "superseded"):
        result = _build_blocked(snapshot_id, fd, period, "SNAPSHOT_STALE",
                                {"validity": validity})
    else:
        result = _compute_live(snapshot, fd, period)

    if persist:
        store.commit_metrics_atomic([result])
    return result


def _compute_live(snapshot: S.FinancialSnapshot, fd: S.FormulaDefinition,
                  period: str) -> S.MetricResult:
    items = store.list_snapshot_items(snapshot.snapshot_id)

    # 公式依赖科目被阻断异常影响 → BLOCKED_BY_SNAPSHOT。
    exceptions = store.list_snapshot_exceptions(snapshot.snapshot_id)
    blocking = {
        exc.standard_item_code: _BLOCKING_REASON[exc.exception_type]
        for exc in exceptions
        if exc.exception_type in _BLOCKING_REASON and exc.standard_item_code
    }
    for code in fd.input_item_codes:
        if code in blocking:
            return _build_blocked(snapshot.snapshot_id, fd, period, blocking[code],
                                  {"blocked_item": code})

    period_type = _period_type_of(items, period, snapshot.scope, snapshot.currency)
    codes = set(fd.input_item_codes)
    gathered = _gather(items, codes, snapshot.scope, snapshot.currency)

    current_map = gathered.get(period, {})
    prior_map: dict[str, tuple] = {}
    if fd.period_requirement in _PRIOR_PERIOD_REQUIREMENTS:
        prior_period = _prior_period(period, period_type)
        if prior_period is not None:
            prior_map = gathered.get(prior_period, {})

    policy: dict = {}
    if fd.formula_id == "SOLV_QUICK_RATIO":
        excl = _quick_ratio_policy_amount(snapshot)
        if excl is not None:
            policy["quick_ratio_excluded_other_current_asset_amount"] = excl

    outcome = formulas.compute_formula(
        fd.formula_id, _amounts(current_map), _amounts(prior_map),
        policy=policy, period_type=period_type)

    item_refs, record_refs = _collect_refs(current_map, prior_map)
    return _build_metric(snapshot.snapshot_id, fd, period, outcome, item_refs, record_refs)


def compute_all(snapshot_id: str, periods: list[str] | None = None,
                persist: bool = True) -> MetricsTableV2:
    """整批计算全部注册公式（§8.1/§8.4：先内存验证，再单事务落盘）。"""
    snapshot = store.get_snapshot(snapshot_id)
    if snapshot is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")
    items = store.list_snapshot_items(snapshot_id)
    if periods is None:
        periods = sorted({it.report_period for it in items})
    if not periods:
        periods = [snapshot.as_of_date]

    formula_ids = list(formulas.ACTIVE_FORMULA_VERSIONS.keys())
    results: list[S.MetricResult] = []
    for fid in formula_ids:
        for period in periods:
            results.append(compute_metric(snapshot_id, fid, period, persist=False))

    if persist and results:
        checkpoint = _make_checkpoint(snapshot_id, results)
        store.commit_metrics_atomic(results, checkpoint=checkpoint)

    return MetricsTableV2(
        snapshot_id=snapshot_id,
        periods=list(periods),
        results=results,
        status_counts=dict(Counter(r.status for r in results)),
    )


def _make_checkpoint(snapshot_id: str, results: list[S.MetricResult]) -> S.Checkpoint:
    now = _utcnow()
    return S.Checkpoint(
        checkpoint_id="cp-" + hashlib.sha256(
            f"metrics|{snapshot_id}".encode("utf-8")).hexdigest()[:24],
        run_id=f"metrics:{snapshot_id}",
        stage_id="CALCULATION",
        state_version=1,
        artifact_refs=sorted(r.metric_result_id for r in results),
        input_hashes={snapshot_id: snapshot_id},
        dependency_versions={"snapshot_id": snapshot_id},
        resolution_refs=[],
        completed_unit_ids=[],
        created_at=now,
    )


# ---------------------------------------------------------------------------
# CLI（§11）
# ---------------------------------------------------------------------------

def _metric_to_dict(m: S.MetricResult) -> dict:
    return {
        "metric_result_id": m.metric_result_id,
        "snapshot_id": m.snapshot_id,
        "formula_id": m.formula_id,
        "formula_version": m.formula_version,
        "period": m.period,
        "status": m.status,
        "reason_code": m.reason_code,
        "raw_value": str(m.raw_value) if m.raw_value is not None else None,
        "display_value": str(m.display_value) if m.display_value is not None else None,
        "unit": m.unit,
        "input_snapshot_item_refs": m.input_snapshot_item_refs,
        "input_record_refs": m.input_record_refs,
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.metrics", description="A6-4 指标计算")
    parser.add_argument("--db", default=str(store.DEFAULT_DB_PATH),
                        help="SQLite 库路径（dev/test 注入临时库）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("compute", help="计算单个指标")
    p_one.add_argument("--snapshot", required=True, dest="snapshot_id")
    p_one.add_argument("--formula", required=True, dest="formula_id")
    p_one.add_argument("--period", required=True)
    p_one.add_argument("--validate-only", action="store_true", help="只算不落盘")

    p_all = sub.add_parser("compute-all", help="整批计算全部公式")
    p_all.add_argument("--snapshot", required=True, dest="snapshot_id")
    p_all.add_argument("--period", action="append", default=None, help="指定期间（可重复）")
    p_all.add_argument("--validate-only", action="store_true", help="只算不落盘")

    args = parser.parse_args(argv)
    store.init_db(args.db)

    if args.cmd == "compute":
        m = compute_metric(args.snapshot_id, args.formula_id, args.period,
                           persist=not args.validate_only)
        print(json.dumps(_metric_to_dict(m), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "compute-all":
        table = compute_all(args.snapshot_id, periods=args.period,
                            persist=not args.validate_only)
        print(json.dumps({
            "snapshot_id": table.snapshot_id,
            "periods": table.periods,
            "status_counts": table.status_counts,
            "results": [_metric_to_dict(r) for r in table.results],
        }, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
