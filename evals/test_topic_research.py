"""Eval: Phase 4 纵向切片 — 主题研究查询规划（确定性 / 无公司硬编码）。

用法: python -m evals.test_topic_research

覆盖：
- derive_topic_queries 显式接收 TopicResearchContext（公司名/行业名/报告时点）；
- 同输入同输出（aspect_id / query_id 内容寻址确定性）；
- 三样本查询分工：company_business 纯本地、industry_scale_cycle 纯外部、
  industry_risk_transmission 本地+外部；
- 行业名来自 context，非硬编码：换公司/行业 → 查询词随 context 变；
- 换 context 不影响 aspect 数量（仅查询词变化）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import schema as PS
from planning.topic_research import (
    TopicResearchContext, derive_topic_queries,
)


def _question(qid, topic, aspects, kind, sources, req_fields=()):
    return PS.PlannedQuestion(
        question_id=qid, question=f"{qid} 问题", priority="P0", topic_id=topic,
        required_aspects=tuple(aspects),
        evidence_requirements=(
            {"requirement_id": "er_x", "evidence_kind": kind,
             "source_classes": list(sources), "minimum_sources": 1,
             "required_fields": list(req_fields)},),
    )


def _task():
    return PS.SectionTask(
        task_id="task_t", plan_id="plan_t", section_id="mixed",
        title="混合", purpose="测试", research_policy="harness",
        topic_ids=("company_business", "industry_scale_cycle",
                   "industry_risk_transmission"),
        questions=(
            _question("company_business_main", "company_business",
                      ["主营业务构成", "各业务收入及收入占比", "各业务成本与毛利构成",
                       "产业链位置", "对应报告期与口径"],
                      "table", ["company_industry"],
                      ["主营业务", "收入构成", "毛利构成"]),
            _question("industry_scale_cycle", "industry_scale_cycle",
                      ["行业规模", "增速", "当前周期位置", "数据截止日期与统计口径"],
                      "web", ["external"],
                      ["行业规模", "增速", "数据截止日期", "统计口径"]),
            _question("industry_risk_transmission", "industry_risk_transmission",
                      ["对收入的传导", "对成本与资本开支的传导", "对现金流与偿债能力的传导"],
                      "paragraph", ["company_industry", "external"]),
        ),
        output_requirements=(), evaluation_rule_ids=(),
        allowed_capabilities=(), blocking_rules=())


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

    ctx = TopicResearchContext(
        company_id="300750", company_name="宁德时代",
        industry_names=("动力电池",), report_as_of="2026-03-31")
    task = _task()

    business = derive_topic_queries(task, "company_business", ctx)
    scale = derive_topic_queries(task, "industry_scale_cycle", ctx)
    transmission = derive_topic_queries(task, "industry_risk_transmission", ctx)

    # 1) aspect 数量与契约一致
    check(len(business.aspects) == 5, "company_business 拆解 5 个 aspect")
    check(len(scale.aspects) == 4, "industry_scale_cycle 拆解 4 个 aspect")
    check(len(transmission.aspects) == 3, "industry_risk_transmission 拆解 3 个 aspect")

    # 2) 确定性（同输入同输出）
    check(business.to_dict() == derive_topic_queries(task, "company_business", ctx).to_dict(),
          "确定性：company_business 同输入同输出")
    check(scale.to_dict() == derive_topic_queries(task, "industry_scale_cycle", ctx).to_dict(),
          "确定性：industry_scale_cycle 同输入同输出")

    # 3) 查询分工
    check(all(a.local_query and not a.external_query for a in business.aspects),
          "company_business 全部仅本地查询")
    check(all(a.external_query and not a.local_query for a in scale.aspects),
          "industry_scale_cycle 全部仅外部查询")
    check(all(a.local_query and a.external_query for a in transmission.aspects),
          "industry_risk_transmission 本地 + 外部查询")

    # 4) 行业名来自 context（外部查询含行业名）
    check(all("动力电池" in (a.external_query or "") for a in scale.aspects),
          "外部查询词含 context 行业名")
    check(all("宁德时代" in (a.local_query or "") for a in business.aspects),
          "本地查询词含 context 公司名")

    # 5) 换 context（换公司/行业）→ 查询词随变，结构不变（证明无硬编码）
    ctx2 = TopicResearchContext(
        company_id="600519", company_name="贵州茅台",
        industry_names=("白酒",), report_as_of="2026-03-31")
    scale2 = derive_topic_queries(task, "industry_scale_cycle", ctx2)
    business2 = derive_topic_queries(task, "company_business", ctx2)
    check(len(scale2.aspects) == len(scale.aspects), "换公司不影响 aspect 数量")
    check(all("白酒" in (a.external_query or "") for a in scale2.aspects),
          "换行业后外部查询随 context 变（白酒）")
    check(all("贵州茅台" in (a.local_query or "") for a in business2.aspects),
          "换公司后本地查询随 context 变（贵州茅台）")
    check(scale2.aspects[0].query_id != scale.aspects[0].query_id,
          "换 context 后 query_id 变化（内容寻址）")

    # 6) 契约字段透传
    check(scale.aspects[0].evidence_kind == "web"
          and scale.aspects[0].source_classes == ("external",),
          "evidence_kind / source_classes 透传")
    check(set(business.aspects[0].required_fields) == {"主营业务", "收入构成", "毛利构成"},
          "required_fields 透传")

    # 7) 序列化往返
    rt = derive_topic_queries(task, "industry_scale_cycle", ctx)
    from planning.topic_research import TopicQueryPlan
    rt2 = TopicQueryPlan.from_dict(json.loads(json.dumps(rt.to_dict())))
    check(rt2.to_dict() == rt.to_dict(), "TopicQueryPlan to_dict/from_dict 往返一致")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
