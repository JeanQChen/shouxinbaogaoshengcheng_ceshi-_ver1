"""Eval: 结构化子 need 派生 —— Phase 3 Batch B 用户 §三。

用法: python -m evals.test_harness_structured_needs

断言（纯逻辑，无 LLM / I/O / Router / Registry）：
- hybrid：原始问题直接点名字段（经营活动现金流净额 → OPERATING_CASH_FLOW，source=question）；
- 纯指标：数值方面「净利率」→ PROF_NET_MARGIN（source=aspect）；
- 语义不匹配：数值方面无法精确表达（各业务收入及收入占比）→ rejected_aspects；
- 非数值方面不参与派生；aspect 与 question 同目标去重；
- target_expression 还原正式表达式（指标中文名 / 字段首别名）；
- target_period 从原始问题提取年份；db_tool_args 组装字段 vs 指标工具参数；
- classify_aspects 标注 db/rejected/evidence 通道。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import structured_needs as SN


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

    # ---- hybrid：问题直接点名字段 ----
    hyb = SN.derive_structured_subneeds(
        "合并口径下，2025年经营活动现金流净额是多少？变化原因是什么？",
        [{"aspect_id": "a1", "text": "现金流结构", "source": "DATASET_MAPPING"}])
    check(len(hyb.subneeds) == 1, "hybrid：派生 1 个子 need")
    if hyb.subneeds:
        s = hyb.subneeds[0]
        check(s.source == "question" and s.aspect_id is None,
              "hybrid：source=question、aspect_id=None（来自原始问题而非方面）")
        check(s.target_type == "field" and s.standard_item_code == "OPERATING_CASH_FLOW",
              "hybrid：target_type=field、code=OPERATING_CASH_FLOW")
    check(hyb.rejected_aspects == [], "hybrid：非数值方面「现金流结构」不派生也不拒绝")

    # ---- 纯指标：数值方面「净利率」 ----
    met = SN.derive_structured_subneeds(
        "净利率如何变化？",
        [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}])
    check(len(met.subneeds) == 1, "纯指标：派生 1 个子 need")
    if met.subneeds:
        s = met.subneeds[0]
        check(s.source == "aspect" and s.aspect_id == "a1",
              "纯指标：source=aspect、aspect_id=a1")
        check(s.target_type == "metric" and s.formula_id == "PROF_NET_MARGIN",
              "纯指标：target_type=metric、formula_id=PROF_NET_MARGIN")

    # ---- 语义不匹配：数值方面无法精确表达 → rejected ----
    rej = SN.derive_structured_subneeds(
        "2025年市占率是多少？",
        [{"aspect_id": "a1", "text": "市占率", "source": "DATASET_MAPPING"}])
    check(len(rej.subneeds) == 0, "语义不匹配：不派生 DB 子 need（不冒充 DB 结果）")
    check(len(rej.rejected_aspects) == 1
          and rej.rejected_aspects[0].aspect_id == "a1"
          and rej.rejected_aspects[0].reason == "no_deterministic_db_target",
          "语义不匹配：方面落入 rejected_aspects（reason=no_deterministic_db_target）")

    # ---- 业务分块子范围限定：segment 毛利率 ≠ 公司整体毛利率 → 语义不匹配 ----
    seg = SN.derive_structured_subneeds(
        "宁德时代2025年动力电池业务毛利率是多少？同比如何变化？",
        [{"aspect_id": "a1", "text": "毛利率", "source": "DATASET_MAPPING"}])
    check(len(seg.subneeds) == 0,
          "业务分块：不把「动力电池业务毛利率」冒充公司整体 PROF_GROSS_MARGIN")
    check(len(seg.rejected_aspects) == 1
          and seg.rejected_aspects[0].reason == "segment_scope_qualifier",
          "业务分块：方面落入 rejected（reason=segment_scope_qualifier）")

    # ---- 去重：aspect 与 question 同目标只派生一次 ----
    dup = SN.derive_structured_subneeds(
        "毛利率是多少？",
        [{"aspect_id": "a1", "text": "毛利率", "source": "DATASET_MAPPING"}])
    check(len(dup.subneeds) == 1 and dup.subneeds[0].formula_id == "PROF_GROSS_MARGIN",
          "去重：aspect「毛利率」与 question 同目标 → 仅 1 个子 need（PROF_GROSS_MARGIN）")

    # ---- target_expression：指标中文名 / 字段首别名 ----
    from harness.structured_needs import StructuredSubNeed
    expr_metric = SN.target_expression(StructuredSubNeed(
        sub_need_id="x", source="aspect", aspect_id="a1", text="净利率",
        target_type="metric", standard_item_code=None,
        formula_id="PROF_NET_MARGIN", formula_version="v1"))
    check(expr_metric == "净利率", "target_expression：指标 → 公式中文名「净利率」")
    expr_field = SN.target_expression(StructuredSubNeed(
        sub_need_id="x", source="question", aspect_id=None, text="经营活动现金流净额",
        target_type="field", standard_item_code="OPERATING_CASH_FLOW",
        formula_id=None, formula_version=None))
    check(expr_field == "经营活动产生的现金流量净额",
          "target_expression：字段 → 首别名「经营活动产生的现金流量净额」")

    # ---- target_period：从原始问题提取年份 ----
    check(SN.target_period("2025年经营活动现金流净额是多少？") == "2025-12-31",
          "target_period：单年份 → 2025-12-31")
    check(SN.target_period("经营活动现金流净额是多少？") is None,
          "target_period：无年份 → None")
    check(SN.target_period("2024年较2025年如何变化？") is None,
          "target_period：≥2 年份 → None（跨期比较不落单值 DB target）")

    # ---- db_tool_args：字段 vs 指标 ----
    field_filters = {
        "db_target_type": "field", "standard_item_code": "OPERATING_CASH_FLOW",
        "snapshot_as_of_date": "2025-12-31", "target_period": "2025-12-31",
        "scope": "consolidated", "currency": "CNY", "purpose": "credit_analysis"}
    fname, fargs = SN.db_tool_args("300750", field_filters)
    check(fname == "lookup_company_field"
          and fargs["company_id"] == "300750"
          and fargs["standard_item_code"] == "OPERATING_CASH_FLOW"
          and fargs["target_period"] == "2025-12-31",
          "db_tool_args：字段 → lookup_company_field + standard_item_code + dims")
    metric_filters = {
        "db_target_type": "metric", "formula_id": "PROF_NET_MARGIN",
        "formula_version": "v1", "snapshot_as_of_date": "2025-12-31",
        "target_period": "2025-12-31", "scope": "consolidated",
        "currency": "CNY", "purpose": "credit_analysis"}
    mname, margs = SN.db_tool_args("300750", metric_filters)
    check(mname == "lookup_financial_metric"
          and margs["formula_id"] == "PROF_NET_MARGIN"
          and margs["formula_version"] == "v1",
          "db_tool_args：指标 → lookup_financial_metric + formula_id/version")

    # ---- classify_aspects：db / rejected / evidence ----
    aspects = [
        {"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"},
        {"aspect_id": "a2", "text": "现金流结构", "source": "DATASET_MAPPING"},
        {"aspect_id": "a3", "text": "各业务收入及收入占比", "source": "DATASET_MAPPING"},
    ]
    subneeds = [{"aspect_id": "a1", "status": "RESOLVED"}]
    rejected = [{"aspect_id": "a3"}]
    cls = SN.classify_aspects(aspects, subneeds, rejected)
    chans = {c["aspect_id"]: c["channel"] for c in cls}
    check(chans == {"a1": "db", "a2": "evidence", "a3": "rejected"},
          "classify_aspects：db/rejected/evidence 通道正确")

    # ---- Change 3：跨期/趋势结构化子 need ----
    avail = ["2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31"]

    # resolve_comparison_periods：两个显式年份 → [Y1-12-31, Y2-12-31]。
    check(SN.resolve_comparison_periods("2024年较2025年净利率如何变化？", avail)
          == ["2024-12-31", "2025-12-31"],
          "resolve_comparison_periods：两个显式年份 → 升序两期")
    # 单年份 + 趋势词 → 小于该年的最近年报期 + 该年。
    check(SN.resolve_comparison_periods("2025年净利率如何变化？", avail)
          == ["2024-12-31", "2025-12-31"],
          "resolve_comparison_periods：单年份+趋势词 → 上一期+该期")
    # 近三年 → 最近 3 个年报期。
    check(SN.resolve_comparison_periods("近三年净利率趋势如何？", avail)
          == ["2023-12-31", "2024-12-31", "2025-12-31"],
          "resolve_comparison_periods：近三年 → 最近 3 个年报期")
    # 不可靠（无年份 + 趋势词）→ []。
    check(SN.resolve_comparison_periods("净利率如何变化？", avail) == [],
          "resolve_comparison_periods：无年份趋势词 → []（不猜）")
    # 显式年份不在 available → []。
    check(SN.resolve_comparison_periods("2022年较2023年净利率如何变化？", avail) == [],
          "resolve_comparison_periods：年份不在 available → []（fail-closed）")

    # compare_tool_args：period_a/period_b + dims，无 target_period。
    cmp_filters = {
        "db_target_type": "metric", "formula_id": "PROF_NET_MARGIN",
        "formula_version": "v1", "snapshot_as_of_date": "2025-12-31",
        "scope": "consolidated", "currency": "CNY", "purpose": "credit_analysis"}
    cname, cargs = SN.compare_tool_args("300750", cmp_filters,
                                        ["2024-12-31", "2025-12-31"])
    check(cname == "compare_financial_periods"
          and cargs["formula_id"] == "PROF_NET_MARGIN"
          and cargs["period_a"] == "2024-12-31"
          and cargs["period_b"] == "2025-12-31"
          and "target_period" not in cargs
          and cargs["scope"] == "consolidated",
          "compare_tool_args：compare_financial_periods + period_a/b + dims（无 target_period）")

    # derive：单年份「如何变化」+ 两年 available → metric compare（2 期）。
    met = SN.derive_structured_subneeds(
        "2025年合并口径下，销售利润率（净利率）如何变化？",
        [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}],
        available_periods=avail)
    check(len(met.subneeds) == 1 and met.subneeds[0].period_mode == "compare"
          and met.subneeds[0].periods == ("2024-12-31", "2025-12-31"),
          "derive：纯指标「如何变化」→ compare 子 need（替代 single）")
    check(met.gaps == [], "derive：compare 无 gap")

    # derive：近三年 → metric trend（3 期）。
    met3 = SN.derive_structured_subneeds(
        "近三年净利率趋势如何？",
        [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}],
        available_periods=avail)
    check(len(met3.subneeds) == 1 and met3.subneeds[0].period_mode == "trend"
          and met3.subneeds[0].periods == ("2023-12-31", "2024-12-31", "2025-12-31"),
          "derive：近三年 → trend 子 need（3 期）")

    # derive：趋势但缺上一期 → 保留 single + 记 gap（不猜、不冒充比较）。
    met_gap = SN.derive_structured_subneeds(
        "2025年合并口径下，销售利润率（净利率）如何变化？",
        [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}],
        available_periods=["2025-12-31"])
    check(len(met_gap.subneeds) == 1 and met_gap.subneeds[0].period_mode == "single",
          "derive：缺上一期 → 保留 single")
    check(len(met_gap.gaps) == 1 and met_gap.gaps[0]["reason"] == "trend_prior_missing",
          "derive：缺上一期 → 记 gap（trend_prior_missing）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
