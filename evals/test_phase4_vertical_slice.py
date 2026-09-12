"""Eval: Phase 4 纵向切片验收 Runner（三样本契约 + 纯组装自检）。

用法: python -m evals.test_phase4_vertical_slice

纯组装（无 I/O / LLM）。真实 300750 落地由
`python -m evaluation.run_phase4_vertical_slice`（缺库 skip，见下方）。覆盖：
- 三样本 SectionTask 契约与 standard_v2.yaml 一致（aspect / evidence_kind /
  source_classes / blocking / missing_policy）；
- derive_topic_queries 查询分工（business 纯本地 / scale 纯外部 / transmission 两者）；
- _self_check 纯组装产出（business 表格 + 段落校验 / industry 来源表 + 周期推断）；
- 预算三样本冻结默认值。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.run_phase4_vertical_slice import (
    SAMPLES, SAMPLE_TOPIC, build_sample_tasks, _self_check,
)
from planning.topic_research import TopicResearchContext, derive_topic_queries
from sections.topic_research import budget_for


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

    # 1) 三样本契约
    tasks = build_sample_tasks()
    check(set(tasks) == set(SAMPLES) and len(tasks) == 3, "三样本 task 齐全")

    business = tasks["company_business_main"]
    bq = business.questions[0]
    check(len(bq.required_aspects) == 5 and bq.evidence_requirements[0]["evidence_kind"] == "table"
          and bq.evidence_requirements[0]["source_classes"] == ["company_industry"],
          "business 契约：5 aspect / table / company_industry")
    check(bq.blocking_policy == ("SECTION_BLOCKED",) and bq.impact_scope == ("subject",),
          "business 契约：SECTION_BLOCKED / subject")

    scale = tasks["industry_scale_cycle"].questions[0]
    check(len(scale.required_aspects) == 4 and scale.evidence_requirements[0]["evidence_kind"] == "web"
          and scale.evidence_requirements[0]["source_classes"] == ["external"]
          and scale.missing_policy == "proxy_allowed",
          "scale 契约：4 aspect / web / external / proxy_allowed")

    trans = tasks["industry_risk_transmission"].questions[0]
    check(len(trans.required_aspects) == 3
          and trans.evidence_requirements[0]["source_classes"] == ["company_industry", "external"]
          and trans.impact_scope == ("solvency",),
          "transmission 契约：3 aspect / 本地+外部 / solvency")

    # 2) 查询分工
    ctx = TopicResearchContext(company_id="300750", company_name="宁德时代",
                               industry_names=("动力电池",), report_as_of="2026-03-31")
    plans = {s: derive_topic_queries(tasks[s], SAMPLE_TOPIC[s], ctx) for s in SAMPLES}
    check(all(a.local_query and not a.external_query
              for a in plans["company_business_main"].aspects),
          "business 纯本地查询")
    check(all(a.external_query and not a.local_query
              for a in plans["industry_scale_cycle"].aspects),
          "scale 纯外部查询")
    check(all(a.local_query and a.external_query
              for a in plans["industry_risk_transmission"].aspects),
          "transmission 本地+外部查询")

    # 3) 三样本冻结预算
    check((budget_for("company_business_main").max_tool_calls,
           budget_for("industry_scale_cycle").max_tool_calls,
           budget_for("industry_risk_transmission").max_tool_calls) == (12, 12, 16),
          "三样本预算 max_tool_calls 冻结")

    # 4) 纯组装自检
    sc = _self_check()
    check(sc["aspect_counts"] == {"company_business_main": 5,
                                  "industry_scale_cycle": 4,
                                  "industry_risk_transmission": 3},
          "自检 aspect 计数")
    check(sc["business_chapter_ok"] and sc["business_table_rows"] >= 1,
          "自检 business 表格 + 校验通过")
    check(sc["industry_chapter_ok"] and sc["industry_source_table_rows"] == 2
          and sc["industry_cycle_judgment_present"],
          "自检 industry 来源表 + 周期推断")
    check(sc["markdown_rendered"], "自检 Markdown 渲染")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
