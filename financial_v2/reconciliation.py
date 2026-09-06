"""A4 跨来源对账（§7.5/7.6/7.7/7.8）：comparison group 分组、舍入容差、issue 生成、
current run 原子切换。

职责（纯确定性，无 RAG / LLM / OCR，不修改来源值，不平均/投票/自动选值）：
- 按八维 comparison_key（company_id / standard_item_code / statement_type /
  report_period / period_type / statement_scope / currency / restatement_version）
  聚合多个 record_set 的记录；任一维度缺失 → INSUFFICIENT_SCOPE，不混组。
- 每个标准值依据其明确展示精度（candidate.min_display_increment × 单位换算）形成
  可能真实值区间 [value - 0.5×inc, value + 0.5×inc]；精度未知退化为点区间（仅严格
  相等）。区间共同相交 → MATCHED；无法由舍入解释 → CONFLICT。
- 单一独立来源 → SINGLE_SOURCE；同源同事实异值 → DUPLICATE_SOURCE_CONFLICT（按
  CONFLICT 处理）。多来源一致不求和/不平均/不重复计数。
- CONFLICT / INSUFFICIENT_SCOPE 生成 extraction_issue（不修改来源值）；SINGLE_SOURCE /
  MATCHED 不产生 issue。
- 一次 run 绑定 company + 输入 current Record Set IDs + 规则版本 + input hash，单事务
  原子提交 run + group results + issues + current 指针；写失败保留旧 current。

影响映射（impact_item_codes / impact_section_contracts）由静态报表→章节契约映射确定，
不调用 LLM。Formula Registry 与 Snapshot 留 A6（本模块不实现指标）。

CLI: python -m financial_v2.reconciliation --company <id> --record-set <id> [--record-set <id> ...]
     [--validate-only] [--no-current] [--db <path>]
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from financial_v2 import checks
from financial_v2 import normalization as norm
from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

RECONCILIATION_RULE_VERSION = "1.0"
ROUNDING_TOLERANCE_RULE_VERSION = "1.0"

# 报表类型 → Section Contract 章节（静态契约映射，供 impact_section_contracts 使用）。
SECTION_BY_STATEMENT_TYPE: dict[str, str] = {
    "balance_sheet": "financial.position",
    "income_statement": "financial.performance",
    "cash_flow": "financial.cash_flow",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class ReconciliationGroup:
    """一次对账组的纯计算结果（未持久化；issue 由编排层单独构造）。"""

    comparison_key: str
    state: str                    # SINGLE_SOURCE | MATCHED | CONFLICT | INSUFFICIENT_SCOPE
    candidate_record_ids: list[str]
    std_values: list[str]         # 去重后的标准值（十进制字符串）
    diff_detail: dict
    impact_item_codes: list[str]
    impact_section_contracts: list[str]


@dataclass
class ReconciliationResult:
    run_id: str
    company_id: str
    input_record_set_ids: list[str]
    groups: list[ReconciliationGroup]
    single_source_count: int
    matched_count: int
    conflict_count: int
    insufficient_scope_count: int
    groups_committed: int
    issues_committed: int
    run_reused: bool
    current_switched: bool


# ---------------------------------------------------------------------------
# 纯函数：展示精度区间 / 共同相交 / 维度完整性
# ---------------------------------------------------------------------------

@dataclass
class _Interval:
    record_id: str
    source_version: str
    value: Decimal
    low: Decimal
    high: Decimal
    precision_known: bool


def _value_interval(record: S.SourceFinancialRecord,
                    candidates_by_id: dict[str, S.ExtractedFinancialCell],
                    record_set_source: dict[str, str]) -> _Interval | None:
    """由记录构造展示精度区间（元）。精度未知 → low==high==value 的点区间。"""
    value = checks._std_value_decimal(record, candidates_by_id)
    if value is None:
        return None
    cand = candidates_by_id.get(record.candidate_id)
    inc_display = cand.min_display_increment if cand is not None else None
    mult = norm.unit_to_yuan(record.raw_unit) if record.raw_unit else None
    if inc_display is not None and mult is not None and inc_display > 0:
        half = (inc_display * mult) / 2
        return _Interval(record.record_id, record_set_source.get(record.record_set_version, ""),
                         value, value - half, value + half, True)
    return _Interval(record.record_id, record_set_source.get(record.record_set_version, ""),
                     value, value, value, False)


def _common_intersection(intervals: list[_Interval]) -> tuple[Decimal, Decimal] | None:
    """全部区间共同相交 [max(low), min(high)]；不相交返回 None。"""
    if not intervals:
        return None
    low = max(i.low for i in intervals)
    high = min(i.high for i in intervals)
    return (low, high) if low <= high else None


def _missing_dimensions(record: S.SourceFinancialRecord) -> list[str]:
    dims = {
        "company_id": record.company_id,
        "standard_item_code": record.standard_item_code,
        "statement_type": record.statement_type,
        "report_period": record.report_period,
        "period_type": record.period_type,
        "statement_scope": record.statement_scope,
        "currency": record.currency,
        "restatement_version": record.restatement_version,
    }
    return [k for k, v in dims.items() if v is None or v == ""]


def _insufficient_scope_key(record_id: str) -> str:
    """INSUFFICIENT_SCOPE 记录的合成组键（每记录唯一，不互相混组）。"""
    return "is-" + hashlib.sha256(f"insufficient_scope|{record_id}".encode("utf-8")).hexdigest()[:24]


def _interval_detail(interval: _Interval) -> dict:
    return {
        "record_id": interval.record_id,
        "source_version": interval.source_version,
        "value": str(interval.value),
        "low": str(interval.low),
        "high": str(interval.high),
        "precision_known": interval.precision_known,
    }


def _evaluate_group(records: list[S.SourceFinancialRecord],
                    candidates_by_id: dict[str, S.ExtractedFinancialCell],
                    record_set_source: dict[str, str]) -> ReconciliationGroup:
    """对同一 comparison_key 的记录判定状态并构造纯结果。"""
    first = records[0]
    statement_type = first.statement_type
    standard_item_code = first.standard_item_code

    intervals = [iv for iv in
                 (_value_interval(r, candidates_by_id, record_set_source) for r in records)
                 if iv is not None]
    source_versions = sorted({iv.source_version for iv in intervals if iv.source_version})
    distinct_values = sorted({str(iv.value) for iv in intervals})

    inter = _common_intersection(intervals)
    if len(source_versions) == 1:
        state = "SINGLE_SOURCE" if len(distinct_values) <= 1 else "CONFLICT"
    else:
        state = "MATCHED" if inter is not None else "CONFLICT"

    diff_detail = {
        "standard_item_code": standard_item_code,
        "statement_type": statement_type,
        "source_count": len(source_versions),
        "source_versions": source_versions,
        "intervals": [_interval_detail(iv) for iv in intervals],
        "common_intersection": ([str(inter[0]), str(inter[1])] if inter is not None else None),
        "distinct_values": distinct_values,
        "precision_known": all(iv.precision_known for iv in intervals),
    }

    return ReconciliationGroup(
        comparison_key=S.comparison_key(
            first.company_id, first.standard_item_code, first.statement_type,
            first.report_period, first.period_type, first.statement_scope,
            first.currency, first.restatement_version),
        state=state,
        candidate_record_ids=sorted(iv.record_id for iv in intervals),
        std_values=distinct_values,
        diff_detail=diff_detail,
        impact_item_codes=[standard_item_code] if standard_item_code else [],
        impact_section_contracts=[SECTION_BY_STATEMENT_TYPE[statement_type]]
        if statement_type in SECTION_BY_STATEMENT_TYPE else [],
    )


def _evaluate_insufficient(record: S.SourceFinancialRecord,
                           missing: list[str]) -> ReconciliationGroup:
    return ReconciliationGroup(
        comparison_key=_insufficient_scope_key(record.record_id),
        state="INSUFFICIENT_SCOPE",
        candidate_record_ids=[record.record_id],
        std_values=[str(record.std_value)] if record.std_value is not None else [],
        diff_detail={
            "missing_dimensions": missing,
            "standard_item_code": record.standard_item_code,
            "statement_type": record.statement_type,
            "record_id": record.record_id,
            "raw_item_text": record.raw_item_text,
        },
        impact_item_codes=[record.standard_item_code] if record.standard_item_code else [],
        impact_section_contracts=[SECTION_BY_STATEMENT_TYPE[record.statement_type]]
        if record.statement_type in SECTION_BY_STATEMENT_TYPE else [],
    )


def group_records(records: list[S.SourceFinancialRecord],
                  candidates_by_id: dict[str, S.ExtractedFinancialCell],
                  record_set_source: dict[str, str]) -> list[ReconciliationGroup]:
    """把跨 record_set 的记录按八维 comparison_key 分组并逐组判定状态。"""
    by_key: dict[str, list[S.SourceFinancialRecord]] = {}
    insufficient: list[tuple[S.SourceFinancialRecord, list[str]]] = []
    for r in records:
        missing = _missing_dimensions(r)
        if missing:
            insufficient.append((r, missing))
            continue
        ck = S.comparison_key(
            r.company_id, r.standard_item_code, r.statement_type, r.report_period,
            r.period_type, r.statement_scope, r.currency, r.restatement_version)
        by_key.setdefault(ck, []).append(r)

    groups = [_evaluate_group(recs, candidates_by_id, record_set_source)
              for recs in by_key.values()]
    groups += [_evaluate_insufficient(r, missing) for r, missing in insufficient]
    groups.sort(key=lambda g: (g.state, g.comparison_key))
    return groups


# ---------------------------------------------------------------------------
# 身份派生（run_id / input_hash / issue_id，均确定性 → 幂等重放）
# ---------------------------------------------------------------------------

def _input_hash(records: list[S.SourceFinancialRecord]) -> str:
    parts = sorted(
        f"{r.record_set_version}:{r.record_id}:{r.standard_item_code}:{r.report_period}:{r.std_value}"
        for r in records)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _derive_run_id(company_id: str, record_set_ids: list[str], input_hash: str) -> str:
    raw = json.dumps({
        "kind": "reconciliation",
        "company_id": company_id,
        "input_record_set_ids": sorted(record_set_ids),
        "reconciliation_rule_version": RECONCILIATION_RULE_VERSION,
        "rounding_tolerance_rule_version": ROUNDING_TOLERANCE_RULE_VERSION,
        "input_hash": input_hash,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "run-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _make_conflict_issue(rep_record_set: str, run_id: str, group: ReconciliationGroup,
                         now: str) -> S.ExtractionIssue:
    detail = {
        "run_id": run_id,
        "comparison_key": group.comparison_key,
        "state": "CONFLICT",
        "standard_item_code": group.diff_detail.get("standard_item_code"),
        "statement_type": group.diff_detail.get("statement_type"),
        "source_versions": group.diff_detail.get("source_versions", []),
        "distinct_values": group.std_values,
        "record_ids": group.candidate_record_ids,
        "diff_detail": group.diff_detail,
    }
    canonical = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    issue_id = "iss-" + hashlib.sha256(
        f"{rep_record_set}|RECONCILIATION_CONFLICT|{group.comparison_key}|{canonical}"
        .encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=rep_record_set,
        issue_type="RECONCILIATION_CONFLICT",
        candidate_id=None,
        comparison_key=group.comparison_key,
        detail=detail,
        created_at=now,
    )


def _make_insufficient_issue(rep_record_set: str, run_id: str, group: ReconciliationGroup,
                             now: str) -> S.ExtractionIssue:
    detail = {
        "run_id": run_id,
        "comparison_key": group.comparison_key,
        "state": "INSUFFICIENT_SCOPE",
        "missing_dimensions": group.diff_detail.get("missing_dimensions", []),
        "standard_item_code": group.diff_detail.get("standard_item_code"),
        "statement_type": group.diff_detail.get("statement_type"),
        "record_ids": group.candidate_record_ids,
    }
    canonical = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    issue_id = "iss-" + hashlib.sha256(
        f"{rep_record_set}|INSUFFICIENT_SCOPE|{group.comparison_key}|{canonical}"
        .encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=rep_record_set,
        issue_type="INSUFFICIENT_SCOPE",
        candidate_id=None,
        comparison_key=group.comparison_key,
        detail=detail,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 编排：读记录 → 分组/容差 → 原子落盘（run + groups + issues + current 指针）
# ---------------------------------------------------------------------------

def run_reconciliation(company_id: str, record_set_ids: list[str], *,
                       persist: bool = True,
                       set_current: bool = True) -> ReconciliationResult:
    """对多个 record_set 的记录做跨来源对账并（可选）原子持久化。"""
    if not record_set_ids:
        raise ValueError("record_set_ids 不能为空")
    record_set_ids = sorted(set(record_set_ids))

    records: list[S.SourceFinancialRecord] = []
    record_set_source: dict[str, str] = {}
    for rs_id in record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is None:
            raise KeyError(f"record_set 不存在: {rs_id}")
        recs = store.list_records(rs_id)
        if not recs:
            continue  # 空 record_set（无合格记录）不参与分组。
        records.extend(recs)
        record_set_source[rs_id] = rs.source_version

    if not records:
        raise KeyError("输入 record_sets 下无任何标准化记录")

    # 记录公司归属必须与请求 company_id 一致（不跨公司对账）。
    company_ids = {r.company_id for r in records}
    if company_ids != {company_id}:
        raise ValueError(
            f"记录公司归属与请求不符: {sorted(company_ids)} != {[company_id]}")

    candidates_by_id: dict[str, S.ExtractedFinancialCell] = {}
    for rs_id in record_set_ids:
        for c in store.list_candidates(rs_id):
            candidates_by_id[c.candidate_id] = c

    groups = group_records(records, candidates_by_id, record_set_source)

    input_hash = _input_hash(records)
    run_id = _derive_run_id(company_id, record_set_ids, input_hash)
    now = _utcnow()
    rep_record_set = record_set_ids[0]  # issue.record_set_version 的锚定集合（跨源冲突无单值）。

    group_results = [S.ReconciliationGroupResult(
        run_id=run_id,
        comparison_key=g.comparison_key,
        state=g.state,
        candidate_record_ids=g.candidate_record_ids,
        std_values=g.std_values,
        diff_detail=g.diff_detail,
        impact_item_codes=g.impact_item_codes,
        impact_section_contracts=g.impact_section_contracts,
        created_at=now,
    ) for g in groups]

    issues: list[S.ExtractionIssue] = []
    for g in groups:
        if g.state == "CONFLICT":
            issues.append(_make_conflict_issue(rep_record_set, run_id, g, now))
        elif g.state == "INSUFFICIENT_SCOPE":
            issues.append(_make_insufficient_issue(rep_record_set, run_id, g, now))

    groups_committed = 0
    issues_committed = 0
    run_reused = False
    current_switched = False
    if persist:
        run = S.ReconciliationRun(
            run_id=run_id,
            company_id=company_id,
            input_record_set_ids=record_set_ids,
            rule_versions={
                "reconciliation_rule_version": RECONCILIATION_RULE_VERSION,
                "rounding_tolerance_rule_version": ROUNDING_TOLERANCE_RULE_VERSION,
            },
            input_hash=input_hash,
            created_at=now,
        )
        commit = store.commit_reconciliation_atomic(
            run, group_results, issues, set_current=set_current)
        run_reused = commit.run_reused
        groups_committed = commit.groups_inserted
        issues_committed = commit.issues_inserted
        current_switched = commit.current_switched

    return ReconciliationResult(
        run_id=run_id,
        company_id=company_id,
        input_record_set_ids=record_set_ids,
        groups=groups,
        single_source_count=sum(1 for g in groups if g.state == "SINGLE_SOURCE"),
        matched_count=sum(1 for g in groups if g.state == "MATCHED"),
        conflict_count=sum(1 for g in groups if g.state == "CONFLICT"),
        insufficient_scope_count=sum(1 for g in groups if g.state == "INSUFFICIENT_SCOPE"),
        groups_committed=groups_committed,
        issues_committed=issues_committed,
        run_reused=run_reused,
        current_switched=current_switched,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.reconciliation",
        description="A4 跨来源对账（comparison group 分组 / 舍入容差 / issue / current 切换）")
    parser.add_argument("--company", required=True, help="company_id")
    parser.add_argument("--record-set", required=True, action="append",
                        dest="record_sets", help="输入 record_set_version（可多次）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只对账不落盘（不写 run / groups / issues / current）")
    parser.add_argument("--no-current", action="store_true",
                        help="落盘但不切换 current 指针")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省 data/financial_v2.db）")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)
    result = run_reconciliation(
        args.company, args.record_sets,
        persist=not args.validate_only, set_current=not args.no_current)

    summary = {
        "run_id": result.run_id,
        "company_id": result.company_id,
        "input_record_set_ids": result.input_record_set_ids,
        "single_source_count": result.single_source_count,
        "matched_count": result.matched_count,
        "conflict_count": result.conflict_count,
        "insufficient_scope_count": result.insufficient_scope_count,
        "groups_committed": result.groups_committed,
        "issues_committed": result.issues_committed,
        "run_reused": result.run_reused,
        "current_switched": result.current_switched,
        "groups": [
            {
                "state": g.state,
                "standard_item_code": g.diff_detail.get("standard_item_code"),
                "statement_type": g.diff_detail.get("statement_type"),
                "source_count": g.diff_detail.get("source_count"),
                "distinct_values": g.std_values,
                "record_ids": g.candidate_record_ids,
            }
            for g in result.groups
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
