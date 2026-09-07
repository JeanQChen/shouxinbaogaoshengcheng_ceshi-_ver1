"""A4 同源勾稽（§7.4）：单一 record_set 内的跨科目恒等式核对。

职责边界（纯确定性，无 RAG / LLM / OCR，不修改来源值）：
- 四类勾稽（资产负债表恒等式、现金余额勾稽、净利与现金流起点、收入成本附注构成），
  规则版本化、纯 Python/Decimal 计算；
- 缺少输入 → NOT_RUN_MISSING_INPUT（不等于通过）；不满足 → FAIL 并生成 CHECK_FAILED
  问题；通过 → PASS；
- 勾稽结果持久化到 reconciliation_check（绑定 reconciliation_run），幂等可重放。

金额精确性：record.std_value 为 Decimal（权威十进制文本落库，非二进制 float）；
本模块优先经 candidate_id 回读候选层的 Decimal 解析值 × 单位换算还原精确元值，
回读不可用时直接采用 record.std_value（同为 Decimal，不损失精度）。

CLI: python -m financial_v2.checks --record-set <id> [--validate-only] [--db <path>]
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from financial_v2 import normalization as norm
from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

CHECK_RULE_VERSION = "1.0"
TOLERANCE_RULE_VERSION = "1.0"

# 标准科目代码（与 mapping.py 写入 record.standard_item_code 的值一致）。
TOTAL_ASSETS = "TOTAL_ASSETS"
TOTAL_LIABILITIES = "TOTAL_LIABILITIES"
TOTAL_EQUITY = "TOTAL_EQUITY"
BEGINNING_CASH_BALANCE = "BEGINNING_CASH_BALANCE"
ENDING_CASH_BALANCE = "ENDING_CASH_BALANCE"
NET_CASH_INCREASE = "NET_CASH_INCREASE"
NET_PROFIT = "NET_PROFIT"
OPERATING_REVENUE = "OPERATING_REVENUE"
OPERATING_COST = "OPERATING_COST"
# 首版 A2/A3 不抽取附注 / 现金流补充资料，以下为「字段可得时」预留的确定性代码，
# 避免未来中文名二次硬编码漂移；当前恒缺失 → 对应勾稽恒为 NOT_RUN_MISSING_INPUT。
NET_PROFIT_CASH_FLOW_START = "NET_PROFIT_CASH_FLOW_START"
OPERATING_REVENUE_NOTES_TOTAL = "OPERATING_REVENUE_NOTES_TOTAL"
OPERATING_COST_NOTES_TOTAL = "OPERATING_COST_NOTES_TOTAL"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class CheckEvaluation:
    """一次勾稽的纯计算结果（含期间、输入 record ID、左右值、差异、容差、状态）。"""

    check_type: str
    status: str                     # PASS | FAIL | NOT_RUN_MISSING_INPUT
    period: str | None
    input_record_ids: list[str]
    left_value: Decimal | None
    right_value: Decimal | None
    diff: Decimal | None
    tolerance: Decimal | None
    detail: dict = field(default_factory=dict)


@dataclass
class ChecksResult:
    run_id: str
    record_set_version: str
    company_id: str
    evaluations: list[CheckEvaluation]
    pass_count: int
    fail_count: int
    not_run_count: int
    checks_committed: int
    issues_committed: int
    reused: bool


# ---------------------------------------------------------------------------
# 金额精确解析（候选 Decimal 优先，record.std_value float 兜底）
# ---------------------------------------------------------------------------

def _std_value_decimal(record: S.SourceFinancialRecord,
                       candidates_by_id: dict[str, S.ExtractedFinancialCell]) -> Decimal | None:
    """返回 record 的精确标准值（元）。候选 Decimal × 单位换算优先，失败退回 record.std_value。"""
    if record.std_value is None:
        return None
    cand = candidates_by_id.get(record.candidate_id)
    if (cand is not None and cand.parsed_numeric_value is not None
            and cand.unit_candidate is not None):
        mult = norm.unit_to_yuan(cand.unit_candidate)
        if mult is not None:
            return cand.parsed_numeric_value * mult
    return record.std_value


def _build_value_index(
    records: list[S.SourceFinancialRecord],
    resolve,
) -> dict[tuple[str, str, str], list[tuple[str, Decimal]]]:
    """按 (statement_type, standard_item_code, report_period) 聚合精确标准值。"""
    index: dict[tuple[str, str, str], list[tuple[str, Decimal]]] = {}
    for r in records:
        v = resolve(r)
        if v is None:
            continue
        key = (r.statement_type, r.standard_item_code, r.report_period)
        index.setdefault(key, []).append((r.record_id, v))
    return index


def _resolve_one(index, key) -> tuple[str, Decimal | None, list[str]]:
    """返回 (status, value, record_ids)；status ∈ ok / missing / ambiguous。"""
    entries = index.get(key, [])
    if not entries:
        return "missing", None, []
    if len(entries) > 1:
        return "ambiguous", None, [rid for rid, _ in entries]
    rid, v = entries[0]
    return "ok", v, [rid]


# ---------------------------------------------------------------------------
# 容差（版本化：0.5 × 单位换算元值 × 参与项数）
# ---------------------------------------------------------------------------

def tolerance_yuan(unit: str | None, n_terms: int) -> Decimal:
    """同源勾稽容差（元）：展示单位粒度的一半 × 参与项数。

    单位未知（正常不会发生：标准化后每条记录均有已知单位）→ 零容差（严格相等）。
    """
    mult = norm.unit_to_yuan(unit) if unit else None
    if mult is None:
        return Decimal("0")
    return (mult / 2) * n_terms


def _common_raw_unit(records: list[S.SourceFinancialRecord], record_set_unit: str | None) -> str | None:
    if record_set_unit is not None:
        return record_set_unit
    units = {r.raw_unit for r in records}
    return next(iter(units)) if len(units) == 1 else None


# ---------------------------------------------------------------------------
# 四类勾稽（纯函数）
# ---------------------------------------------------------------------------

def evaluate_balance_sheet_identity(index, unit: str | None,
                                    periods: set[str]) -> list[CheckEvaluation]:
    """资产总计 ≈ 负债合计 + 所有者权益合计（按期间逐项）。"""
    evals: list[CheckEvaluation] = []
    for p in sorted(periods):
        sa, a, a_ids = _resolve_one(index, ("balance_sheet", TOTAL_ASSETS, p))
        sl, l, l_ids = _resolve_one(index, ("balance_sheet", TOTAL_LIABILITIES, p))
        se, e, e_ids = _resolve_one(index, ("balance_sheet", TOTAL_EQUITY, p))
        ids = sorted(set(a_ids + l_ids + e_ids))
        if not (sa == sl == se == "ok"):
            detail = {
                "missing_codes": [c for c, s in [(TOTAL_ASSETS, sa), (TOTAL_LIABILITIES, sl),
                                                 (TOTAL_EQUITY, se)] if s == "missing"],
                "ambiguous_codes": [c for c, s in [(TOTAL_ASSETS, sa), (TOTAL_LIABILITIES, sl),
                                                   (TOTAL_EQUITY, se)] if s == "ambiguous"],
            }
            evals.append(CheckEvaluation("BALANCE_SHEET_IDENTITY", "NOT_RUN_MISSING_INPUT",
                                         p, ids, None, None, None, None, detail))
            continue
        left, right = a, l + e
        diff = left - right
        tol = tolerance_yuan(unit, 3)
        status = "PASS" if abs(diff) <= tol else "FAIL"
        evals.append(CheckEvaluation("BALANCE_SHEET_IDENTITY", status, p, ids,
                                     left, right, diff, tol,
                                     {"left": str(left), "right": str(right)}))
    return evals


def evaluate_cash_balance_reconciliation(index, unit: str | None,
                                         periods: set[str]) -> list[CheckEvaluation]:
    """期末现金及现金等价物余额 ≈ 期初余额 + 现金及现金等价物净增加额（字段可得时）。"""
    evals: list[CheckEvaluation] = []
    for p in sorted(periods):
        sb, b, b_ids = _resolve_one(index, ("cash_flow", BEGINNING_CASH_BALANCE, p))
        se, e, e_ids = _resolve_one(index, ("cash_flow", ENDING_CASH_BALANCE, p))
        sn, n, n_ids = _resolve_one(index, ("cash_flow", NET_CASH_INCREASE, p))
        ids = sorted(set(b_ids + e_ids + n_ids))
        codes = [(BEGINNING_CASH_BALANCE, sb), (ENDING_CASH_BALANCE, se), (NET_CASH_INCREASE, sn)]
        if not (sb == se == sn == "ok"):
            detail = {
                "missing_codes": [c for c, s in codes if s == "missing"],
                "ambiguous_codes": [c for c, s in codes if s == "ambiguous"],
            }
            evals.append(CheckEvaluation("CASH_BALANCE_RECONCILIATION", "NOT_RUN_MISSING_INPUT",
                                         p, ids, None, None, None, None, detail))
            continue
        left, right = e, b + n
        diff = left - right
        tol = tolerance_yuan(unit, 3)
        status = "PASS" if abs(diff) <= tol else "FAIL"
        evals.append(CheckEvaluation("CASH_BALANCE_RECONCILIATION", status, p, ids,
                                     left, right, diff, tol,
                                     {"left": str(left), "right": str(right)}))
    return evals


def evaluate_net_income_cash_start(index, unit: str | None,
                                   periods: set[str]) -> list[CheckEvaluation]:
    """利润表净利润 vs 现金流量表补充资料净利润起点（字段可得时；首版无补充资料 → 恒 NOT_RUN）。"""
    evals: list[CheckEvaluation] = []
    for p in sorted(periods):
        si, ni, ni_ids = _resolve_one(index, ("income_statement", NET_PROFIT, p))
        sc, nf, nf_ids = _resolve_one(index, ("cash_flow", NET_PROFIT_CASH_FLOW_START, p))
        ids = sorted(set(ni_ids + nf_ids))
        if not (si == sc == "ok"):
            missing = [c for c, s in [(NET_PROFIT, si), (NET_PROFIT_CASH_FLOW_START, sc)]
                       if s == "missing"]
            evals.append(CheckEvaluation(
                "NET_INCOME_CASH_START", "NOT_RUN_MISSING_INPUT", p, ids,
                None, None, None, None, {"missing_codes": missing}))
            continue
        left, right = ni, nf
        diff = left - right
        tol = tolerance_yuan(unit, 2)
        status = "PASS" if abs(diff) <= tol else "FAIL"
        evals.append(CheckEvaluation("NET_INCOME_CASH_START", status, p, ids,
                                     left, right, diff, tol,
                                     {"left": str(left), "right": str(right)}))
    return evals


def evaluate_revenue_cost_breakdown(index, unit: str | None,
                                    periods: set[str]) -> list[CheckEvaluation]:
    """收入/成本附注构成合计 vs 主表营业收入/营业成本（口径一致时；首版无附注 → 恒 NOT_RUN）。"""
    evals: list[CheckEvaluation] = []
    for p in sorted(periods):
        srev, rev, rev_ids = _resolve_one(index, ("income_statement", OPERATING_REVENUE, p))
        scost, cost, cost_ids = _resolve_one(index, ("income_statement", OPERATING_COST, p))
        srn, rn, rn_ids = _resolve_one(index, ("income_statement", OPERATING_REVENUE_NOTES_TOTAL, p))
        scn, cn, cn_ids = _resolve_one(index, ("income_statement", OPERATING_COST_NOTES_TOTAL, p))
        ids = sorted(set(rev_ids + cost_ids + rn_ids + cn_ids))
        if not (srev == scost == srn == scn == "ok"):
            evals.append(CheckEvaluation(
                "REVENUE_COST_BREAKDOWN", "NOT_RUN_MISSING_INPUT", p, ids,
                None, None, None, None,
                {"missing_codes": [OPERATING_REVENUE_NOTES_TOTAL, OPERATING_COST_NOTES_TOTAL]}))
            continue
        rev_diff = rev - rn
        cost_diff = cost - cn
        tol = tolerance_yuan(unit, 2)
        # 收入、成本两条口径各自成立才 PASS；任一不成立即 FAIL。
        if abs(rev_diff) <= tol and abs(cost_diff) <= tol:
            status = "PASS"
        else:
            status = "FAIL"
        evals.append(CheckEvaluation("REVENUE_COST_BREAKDOWN", status, p, ids,
                                     rev, rn, rev_diff, tol,
                                     {"left": str(rev), "right": str(rn)}))
    return evals


# ---------------------------------------------------------------------------
# 身份派生（run_id / input_hash / check_id，均确定性 → 幂等重放）
# ---------------------------------------------------------------------------

def _input_hash(records: list[S.SourceFinancialRecord]) -> str:
    parts = sorted(f"{r.record_id}:{r.std_value}" for r in records)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _derive_run_id(company_id: str, record_set_ids: list[str], input_hash: str) -> str:
    raw = json.dumps({
        "company_id": company_id,
        "input_record_set_ids": sorted(record_set_ids),
        "check_rule_version": CHECK_RULE_VERSION,
        "tolerance_rule_version": TOLERANCE_RULE_VERSION,
        "input_hash": input_hash,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "run-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _check_id(run_id: str, record_set_version: str, ev: CheckEvaluation) -> str:
    raw = json.dumps({
        "run_id": run_id,
        "record_set_version": record_set_version,
        "check_type": ev.check_type,
        "period": ev.period,
        "input_record_ids": sorted(ev.input_record_ids),
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "chk-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _to_reconciliation_check(run_id: str, record_set_version: str,
                             ev: CheckEvaluation, now: str) -> S.ReconciliationCheck:
    def _d(v: Decimal | None) -> str | None:
        return str(v) if v is not None else None

    return S.ReconciliationCheck(
        check_id=_check_id(run_id, record_set_version, ev),
        run_id=run_id,
        record_set_version=record_set_version,
        check_type=ev.check_type,
        input_record_ids=sorted(ev.input_record_ids),
        left_value=_d(ev.left_value),
        right_value=_d(ev.right_value),
        diff=_d(ev.diff),
        tolerance=_d(ev.tolerance),
        status=ev.status,
        created_at=now,
    )


def _make_check_issue(record_set_version: str, run_id: str, ev: CheckEvaluation,
                      now: str) -> S.ExtractionIssue:
    detail = {
        "check_type": ev.check_type,
        "run_id": run_id,
        "period": ev.period,
        "diff": str(ev.diff) if ev.diff is not None else None,
        "tolerance": str(ev.tolerance) if ev.tolerance is not None else None,
        "affected_item_codes": sorted(ev.detail.get("affected_item_codes", [])),
        "input_record_ids": sorted(ev.input_record_ids),
    }
    canonical = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    issue_id = "iss-" + hashlib.sha256(
        f"{record_set_version}|CHECK_FAILED|{ev.check_type}|{canonical}".encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=record_set_version,
        issue_type="CHECK_FAILED",
        candidate_id=None,
        comparison_key=None,
        detail=detail,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 编排：读记录 → 精确值索引 → 四类勾稽 → 原子落盘（run + checks + FAIL 问题）
# ---------------------------------------------------------------------------

def run_checks(record_set_version: str, persist: bool = True) -> ChecksResult:
    """对某 record_set 的全部标准化记录跑四类同源勾稽并持久化。"""
    records = store.list_records(record_set_version)
    if not records:
        raise KeyError(f"record_set 下无记录: {record_set_version}")
    rs = store.get_record_set(record_set_version)
    if rs is None:
        raise KeyError(f"record_set 不存在: {record_set_version}")

    company_ids = {r.company_id for r in records}
    if len(company_ids) != 1:
        raise ValueError(f"记录跨公司: {sorted(company_ids)}")
    company_id = next(iter(company_ids))

    candidates_by_id = {c.candidate_id: c for c in store.list_candidates(record_set_version)}
    resolve = lambda r: _std_value_decimal(r, candidates_by_id)
    index = _build_value_index(records, resolve)

    unit = _common_raw_unit(records, rs.unit)
    periods_bs = {r.report_period for r in records if r.statement_type == "balance_sheet"}
    periods_cf = {r.report_period for r in records if r.statement_type == "cash_flow"}
    periods_inc = {r.report_period for r in records if r.statement_type == "income_statement"}

    evaluations: list[CheckEvaluation] = []
    evaluations += evaluate_balance_sheet_identity(index, unit, periods_bs)
    evaluations += evaluate_cash_balance_reconciliation(index, unit, periods_cf)
    evaluations += evaluate_net_income_cash_start(index, unit, periods_inc)
    evaluations += evaluate_revenue_cost_breakdown(index, unit, periods_inc)

    # 每类勾稽若无任何期间可评估（该表完全缺失），补一条 NOT_RUN。
    if not periods_bs:
        evaluations.append(CheckEvaluation(
            "BALANCE_SHEET_IDENTITY", "NOT_RUN_MISSING_INPUT", None, [], None, None, None, None,
            {"missing_codes": [TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY]}))
    if not periods_cf:
        evaluations.append(CheckEvaluation(
            "CASH_BALANCE_RECONCILIATION", "NOT_RUN_MISSING_INPUT", None, [], None, None, None, None,
            {"missing_codes": [BEGINNING_CASH_BALANCE, ENDING_CASH_BALANCE, NET_CASH_INCREASE]}))
    if not periods_inc:
        evaluations.append(CheckEvaluation(
            "NET_INCOME_CASH_START", "NOT_RUN_MISSING_INPUT", None, [], None, None, None, None,
            {"missing_codes": [NET_PROFIT]}))
        evaluations.append(CheckEvaluation(
            "REVENUE_COST_BREAKDOWN", "NOT_RUN_MISSING_INPUT", None, [], None, None, None, None,
            {"missing_codes": [OPERATING_REVENUE, OPERATING_COST]}))

    input_hash = _input_hash(records)
    run_id = _derive_run_id(company_id, [record_set_version], input_hash)
    now = _utcnow()

    checks = [_to_reconciliation_check(run_id, record_set_version, ev, now)
              for ev in evaluations]
    fail_issues = [_make_check_issue(record_set_version, run_id, ev, now)
                   for ev in evaluations if ev.status == "FAIL"]

    checks_committed = 0
    issues_committed = 0
    reused = False
    if persist:
        run = S.ReconciliationRun(
            run_id=run_id,
            company_id=company_id,
            input_record_set_ids=[record_set_version],
            rule_versions={
                "check_rule_version": CHECK_RULE_VERSION,
                "tolerance_rule_version": TOLERANCE_RULE_VERSION,
            },
            input_hash=input_hash,
            created_at=now,
        )
        reused = store.commit_reconciliation_run(run)
        checks_committed = store.commit_reconciliation_checks(checks)
        if fail_issues:
            issues_committed = store.commit_issues(fail_issues)

    return ChecksResult(
        run_id=run_id,
        record_set_version=record_set_version,
        company_id=company_id,
        evaluations=evaluations,
        pass_count=sum(1 for e in evaluations if e.status == "PASS"),
        fail_count=sum(1 for e in evaluations if e.status == "FAIL"),
        not_run_count=sum(1 for e in evaluations if e.status == "NOT_RUN_MISSING_INPUT"),
        checks_committed=checks_committed,
        issues_committed=issues_committed,
        reused=reused,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m financial_v2.checks",
                                     description="A4 同源勾稽（资产负债表恒等式 / 现金余额 / 净利起点 / 收入成本构成）")
    parser.add_argument("--record-set", required=True, help="record_set_version")
    parser.add_argument("--validate-only", action="store_true",
                        help="只计算不落盘（不写 run / checks / 问题）")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省 data/financial_v2.db）")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)
    result = run_checks(args.record_set, persist=not args.validate_only)

    summary = {
        "run_id": result.run_id,
        "record_set_version": result.record_set_version,
        "company_id": result.company_id,
        "pass_count": result.pass_count,
        "fail_count": result.fail_count,
        "not_run_count": result.not_run_count,
        "checks_committed": result.checks_committed,
        "issues_committed": result.issues_committed,
        "reused": result.reused,
        "checks": [
            {
                "check_type": e.check_type,
                "status": e.status,
                "period": e.period,
                "left": str(e.left_value) if e.left_value is not None else None,
                "right": str(e.right_value) if e.right_value is not None else None,
                "diff": str(e.diff) if e.diff is not None else None,
                "tolerance": str(e.tolerance) if e.tolerance is not None else None,
            }
            for e in result.evaluations
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
