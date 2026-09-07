"""Eval: financial_v2 v6 快照/指标 schema + validator（A6 Commit 1）。

用法: python -m evals.test_financial_v2_snapshot_schema

覆盖：
- snapshot_id 派生确定性 + 对头部组件（record_set_ids / restatement_selection /
  policy_adjustments / required_formula_versions / 构建器与准入规则版本）的敏感性；
- PolicyAdjustment 序列化往返 + 白名单（调整类型/决策/record_refs 约束）；
- validate_snapshot：合法通过；snapshot_id 篡改 / 空 record_set_ids / 空公式版本被拒绝；
- validate_snapshot_item：合法通过；非 Decimal 金额 / 缺维度被拒绝；
- validate_metric_result：七状态互斥（EXACT/PROXY 需值；非成功不得产出数值且必须非空
  reason_code；PROXY 与失败状态 reason_code 白名单）；
- validate_formula_definition：name / impl_version / proxy_rule / input_item_codes 校验。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import validator as V
from financial_v2.validator import ValidationError


def _pa(**overrides) -> S.PolicyAdjustment:
    base = dict(
        adjustment_type="quick_ratio_other_current_asset",
        decision="NO_ADDITIONAL_EXCLUSION_CONFIRMED",
        record_refs=[], reason_code="DEFAULT_POLICY", note="", operator="op",
        confirmed_at="t",
    )
    base.update(overrides)
    return S.PolicyAdjustment(**base)


def _snap(**overrides) -> S.FinancialSnapshot:
    base = dict(
        snapshot_id="", snapshot_version="ver-1", company_id="300750",
        as_of_date="2024-12-31", scope="consolidated", currency="CNY",
        purpose="report", source_versions=["sv-1"], resolution_versions=["rv-1"],
        record_set_ids=["rs-1"], reconciliation_run_id=None,
        restatement_selection={}, policy_adjustments=[_pa()],
        required_formula_versions={"SOLV_CURRENT_RATIO": "1.0"},
        snapshot_builder_version=S.SNAPSHOT_BUILDER_VERSION,
        admission_rule_version=S.ADMISSION_RULE_VERSION,
        report_blocked=False, created_at="t",
    )
    base.update(overrides)
    if base["snapshot_id"] == "":
        base["snapshot_id"] = S.derive_snapshot_id(
            base["company_id"], base["scope"], base["currency"], base["as_of_date"],
            base["purpose"], base["record_set_ids"], base["reconciliation_run_id"],
            base["source_versions"], base["resolution_versions"],
            base["restatement_selection"], base["policy_adjustments"],
            base["required_formula_versions"], base["snapshot_builder_version"],
            base["admission_rule_version"])
    return S.FinancialSnapshot(**base)


def _item(**overrides) -> S.SnapshotItem:
    base = dict(
        snapshot_id="snap-1", comparison_key="ck-1", standard_item_code="TOTAL_ASSETS",
        amount=Decimal("1000"), unit="yuan", report_period="2024-12-31",
        period_type="annual", statement_type="balance_sheet",
        statement_scope="consolidated", currency="CNY", restatement_version="0",
        source_refs=["rec-1"], resolution_id=None,
    )
    base.update(overrides)
    return S.SnapshotItem(**base)


def _metric(**overrides) -> S.MetricResult:
    base = dict(
        metric_result_id="mr-1", snapshot_id="snap-1", formula_id="SOLV_CURRENT_RATIO",
        formula_version="1.0", period="2024-12-31", raw_value=Decimal("1.5"),
        display_value=Decimal("1.50"), unit="times",
        input_snapshot_item_refs=["ck-a", "ck-b"], input_record_refs=["rec-1", "rec-2"],
        status="CALCULATED_EXACT", reason_code=None, calculation_detail={},
        created_at="t",
    )
    base.update(overrides)
    return S.MetricResult(**base)


def _formula(**overrides) -> S.FormulaDefinition:
    base = dict(
        formula_id="SOLV_CURRENT_RATIO", formula_version="1.0", name="流动比率",
        input_item_codes=["CURRENT_ASSETS", "CURRENT_LIABILITIES"],
        period_requirement="end", scope_requirement="consolidated",
        python_impl="impl", missing_rule="missing", zero_denominator_rule="missing",
        rounding_rule="ROUND_HALF_UP", impl_version="1.0", proxy_rule={},
        effective_at="t",
    )
    base.update(overrides)
    return S.FormulaDefinition(**base)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def _expect_validation_error(fn, msg):
        try:
            fn()
            check(False, msg)
        except ValidationError:
            check(True, msg)

    # ------------------------------------------------------------------
    # snapshot_id 派生确定性 + 组件敏感性
    # ------------------------------------------------------------------
    s1 = _snap()
    s2 = _snap()
    check(s1.snapshot_id == s2.snapshot_id, "snapshot_id 派生确定性（同输入）")

    # record_refs 顺序不影响 id（policy_adjustment_to_dict 排序）。
    pa_a = _pa(decision="EXCLUDE_CONFIRMED_RECORDS", record_refs=["rec-x", "rec-a"])
    pa_b = _pa(decision="EXCLUDE_CONFIRMED_RECORDS", record_refs=["rec-a", "rec-x"])
    sid_a = _snap(policy_adjustments=[pa_a]).snapshot_id
    sid_b = _snap(policy_adjustments=[pa_b]).snapshot_id
    check(sid_a == sid_b, "policy_adjustments record_refs 顺序不影响 snapshot_id")

    # restatement_selection / required_formula_versions dict 顺序不影响 id。
    sid_c = _snap(restatement_selection={"ck-1": "0", "ck-2": "1"}).snapshot_id
    sid_d = _snap(restatement_selection={"ck-2": "1", "ck-1": "0"}).snapshot_id
    check(sid_c == sid_d, "restatement_selection 键顺序不影响 snapshot_id")

    for desc, kw in [
        ("record_set_ids", dict(record_set_ids=["rs-2"])),
        ("reconciliation_run_id", dict(reconciliation_run_id="run-1")),
        ("as_of_date", dict(as_of_date="2023-12-31")),
        ("policy_adjustments", dict(policy_adjustments=[_pa(decision="EXCLUDE_CONFIRMED_RECORDS", record_refs=["rec-z"])])),
        ("required_formula_versions", dict(required_formula_versions={"SOLV_QUICK_RATIO": "1.0"})),
        ("snapshot_builder_version", dict(snapshot_builder_version="9.9")),
        ("admission_rule_version", dict(admission_rule_version="9.9")),
    ]:
        check(_snap(**kw).snapshot_id != s1.snapshot_id,
              f"snapshot_id 随 {desc} 变化而不同")

    # ------------------------------------------------------------------
    # PolicyAdjustment 序列化 + 白名单
    # ------------------------------------------------------------------
    pa_ex = _pa(decision="EXCLUDE_CONFIRMED_RECORDS", record_refs=["rec-x", "rec-a"],
                reason_code="OTHER", note="n")
    d = S.policy_adjustment_to_dict(pa_ex)
    check(d["record_refs"] == ["rec-a", "rec-x"], "policy_adjustment_to_dict 排序 record_refs")
    pa_rt = S.policy_adjustment_from_dict(d)
    check(pa_rt.adjustment_type == pa_ex.adjustment_type
          and pa_rt.decision == pa_ex.decision
          and sorted(pa_rt.record_refs) == sorted(pa_ex.record_refs),
          "policy_adjustment_from_dict 往返保留关键字段")

    V.validate_policy_adjustment(pa_ex)
    check(True, "validator 通过 EXCLUDE_CONFIRMED_RECORDS（带 record_refs）")

    _expect_validation_error(
        lambda: V.validate_policy_adjustment(_pa(decision="EXCLUDE_CONFIRMED_RECORDS", record_refs=[])),
        "validator 拒绝 EXCLUDE_CONFIRMED_RECORDS 缺 record_refs")
    _expect_validation_error(
        lambda: V.validate_policy_adjustment(_pa(decision="NO_ADDITIONAL_EXCLUSION_CONFIRMED", record_refs=["rec-1"])),
        "validator 拒绝 NO_ADDITIONAL_EXCLUSION_CONFIRMED 携带 record_refs")
    _expect_validation_error(
        lambda: V.validate_policy_adjustment(_pa(decision="BOGUS")),
        "validator 拒绝非法 decision")
    _expect_validation_error(
        lambda: V.validate_policy_adjustment(_pa(adjustment_type="bogus_type")),
        "validator 拒绝非法 adjustment_type")

    # ------------------------------------------------------------------
    # validate_snapshot
    # ------------------------------------------------------------------
    V.validate_snapshot(s1)
    check(True, "validator 通过合法快照")

    tampered = S.FinancialSnapshot(
        **{f: getattr(s1, f) for f in s1.__dataclass_fields__})
    tampered.snapshot_id = "snap-deadbeef"
    _expect_validation_error(lambda: V.validate_snapshot(tampered),
                             "validator 拒绝 snapshot_id 与头部字段重算不一致")

    _expect_validation_error(lambda: V.validate_snapshot(_snap(record_set_ids=[])),
                             "validator 拒绝空 record_set_ids")

    _expect_validation_error(
        lambda: V.validate_snapshot(_snap(required_formula_versions={"F": ""})),
        "validator 拒绝空公式版本值")

    _expect_validation_error(
        lambda: V.validate_snapshot(_snap(scope="bogus")),
        "validator 拒绝非法 scope")

    # ------------------------------------------------------------------
    # validate_snapshot_item
    # ------------------------------------------------------------------
    V.validate_snapshot_item(_item())
    check(True, "validator 通过合法快照条目")

    _expect_validation_error(
        lambda: V.validate_snapshot_item(_item(amount=1.0)),
        "validator 拒绝非 Decimal amount（float）")
    _expect_validation_error(
        lambda: V.validate_snapshot_item(_item(amount=Decimal("nan"))),
        "validator 拒绝非有限 Decimal amount（NaN）")
    _expect_validation_error(
        lambda: V.validate_snapshot_item(_item(report_period="")),
        "validator 拒绝空 report_period")
    _expect_validation_error(
        lambda: V.validate_snapshot_item(_item(statement_type="bogus")),
        "validator 拒绝非法 statement_type")
    _expect_validation_error(
        lambda: V.validate_snapshot_item(_item(source_refs=[])),
        "validator 拒绝 snapshot_item source_refs 为空")

    # ------------------------------------------------------------------
    # validate_metric_result：七状态互斥
    # ------------------------------------------------------------------
    V.validate_metric_result(_metric())
    check(True, "validator 通过 CALCULATED_EXACT（raw+display+unit，reason None）")

    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="CALCULATED_EXACT", reason_code="X")),
        "validator 拒绝 CALCULATED_EXACT 带 reason_code")

    proxy_ok = _metric(status="CALCULATED_PROXY", reason_code="PROXY_FINANCE_EXPENSES")
    V.validate_metric_result(proxy_ok)
    check(True, "validator 通过 CALCULATED_PROXY（reason_code=PROXY_FINANCE_EXPENSES）")

    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="CALCULATED_PROXY", reason_code=None)),
        "validator 拒绝 CALCULATED_PROXY 缺 reason_code")

    missing_ok = _metric(status="MISSING_INPUT", raw_value=None, display_value=None,
                         unit=None, reason_code="MISSING_REQUIRED_ITEM")
    V.validate_metric_result(missing_ok)
    check(True, "validator 通过 MISSING_INPUT（raw/display None + reason_code）")

    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="MISSING_INPUT", reason_code=None)),
        "validator 拒绝 MISSING_INPUT 缺 reason_code")
    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="MISSING_INPUT", raw_value=Decimal("1"))),
        "validator 拒绝 MISSING_INPUT 产出 raw_value")
    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="MISSING_INPUT", reason_code="BOGUS")),
        "validator 拒绝 MISSING_INPUT 非白名单 reason_code")

    zd_ok = _metric(status="ZERO_DENOMINATOR", raw_value=None, display_value=None,
                    unit=None, reason_code="ZERO_DENOMINATOR")
    V.validate_metric_result(zd_ok)
    check(True, "validator 通过 ZERO_DENOMINATOR（自描述 reason_code）")

    na_ok = _metric(status="NOT_APPLICABLE", raw_value=None, display_value=None,
                    unit=None, reason_code="NOT_APPLICABLE")
    V.validate_metric_result(na_ok)
    check(True, "validator 通过 NOT_APPLICABLE（自描述 reason_code）")

    blocked_ok = _metric(status="BLOCKED_BY_SNAPSHOT", raw_value=None, display_value=None,
                         unit=None, reason_code="UNRESOLVED_CONFLICT")
    V.validate_metric_result(blocked_ok)
    check(True, "validator 通过 BLOCKED_BY_SNAPSHOT（reason_code=UNRESOLVED_CONFLICT）")

    _expect_validation_error(
        lambda: V.validate_metric_result(_metric(status="CALCULATED_EXACT", raw_value=Decimal("nan"))),
        "validator 拒绝非有限 Decimal raw_value（NaN）")

    # ------------------------------------------------------------------
    # validate_formula_definition
    # ------------------------------------------------------------------
    V.validate_formula_definition(_formula())
    check(True, "validator 通过合法公式定义")

    _expect_validation_error(
        lambda: V.validate_formula_definition(_formula(name="")),
        "validator 拒绝空 name")
    _expect_validation_error(
        lambda: V.validate_formula_definition(_formula(input_item_codes=[])),
        "validator 拒绝空 input_item_codes")
    _expect_validation_error(
        lambda: V.validate_formula_definition(_formula(impl_version="")),
        "validator 拒绝空 impl_version")
    _expect_validation_error(
        lambda: V.validate_formula_definition(_formula(proxy_rule=[])),
        "validator 拒绝非 dict proxy_rule")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
