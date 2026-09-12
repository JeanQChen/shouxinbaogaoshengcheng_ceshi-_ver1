"""Phase 4 纵向切片 — 主题研究查询规划（确定性，无 LLM、无 I/O）。

.. warning::
    **EXPERIMENTAL**（P3 Harness 内部查询规划候选，本轮仅做接口设计 + 弃用标记）。
    非正式 Phase 4 运行链：正式链的查询规划在 ``sections.research_common`` /
    ``routing.router`` 内，不由本模块承载。

把 ``SectionTask`` 中某个 ``topic_id`` 的必答问题（PlannedQuestion）拆解为
「方面级查询计划」：每个 ``required_aspect`` 派生一条 ``AspectQuery``，明确本地
Evidence 检索查询（search_evidence / search_tables）与外部检索查询
（search_external_sources），供纵向预览的缺口驱动补检直接消费。

本模块**不做路由、不做补检、不调 run_question**，只输出确定性的查询计划。查询词由
``TopicResearchContext``（公司名 / 行业名 / 报告时点）与契约字段确定性拼接，不写死
任何公司、行业或股票代码，也不引入 query-planner LLM prompt。

CLI: python -m planning.topic_research --self-check
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from planning import schema as PS

# 实验性主题查询规划（非正式运行链，§四/§九）。
EXPERIMENTAL_TOPIC_QUERY_PLAN = True

# 版本常量（查询计划派生规则变更需递增，进入 aspect_id / query_id 内容寻址）。
QUERY_PLAN_VERSION = "topic-query-plan-v1"


def _sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicResearchContext:
    """主题研究的显式输入主体（公司 + 行业 + 报告时点）。

    行业名必须来自上游主体材料 / 契约输入 / 显式参数，**绝不写死**。
    ``industry_names`` 为空是合法态（行业未识别），此时外部查询回退到公司名。
    """

    company_id: str
    company_name: str
    industry_names: tuple[str, ...]
    report_as_of: str | None = None


@dataclass(frozen=True)
class AspectQuery:
    """一个必答 aspect 的确定性查询计划。"""

    aspect_id: str
    aspect_text: str
    query_id: str
    topic_id: str
    question_id: str
    priority: str
    evidence_kind: str                 # table | paragraph | web | field | structured_db | ...
    source_classes: tuple[str, ...]    # company_industry | financial | external | ...
    required_fields: tuple[str, ...]
    freshness_policy: str | None
    minimum_sources: int
    local_query: str | None            # search_evidence / search_tables 用
    external_query: str | None         # search_external_sources 用

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_classes"] = list(self.source_classes)
        d["required_fields"] = list(self.required_fields)
        return d


@dataclass(frozen=True)
class TopicQueryPlan:
    """一个 topic 的完整查询计划。"""

    topic_id: str
    context: TopicResearchContext
    aspects: tuple[AspectQuery, ...]
    query_plan_version: str = QUERY_PLAN_VERSION

    def aspect_ids(self) -> list[str]:
        return [a.aspect_id for a in self.aspects]

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "context": {
                "company_id": self.context.company_id,
                "company_name": self.context.company_name,
                "industry_names": list(self.context.industry_names),
                "report_as_of": self.context.report_as_of,
            },
            "aspects": [a.to_dict() for a in self.aspects],
            "query_plan_version": self.query_plan_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TopicQueryPlan":
        ctx = d["context"]
        context = TopicResearchContext(
            company_id=ctx["company_id"],
            company_name=ctx["company_name"],
            industry_names=tuple(ctx.get("industry_names") or []),
            report_as_of=ctx.get("report_as_of"),
        )
        aspects = tuple(
            AspectQuery(
                aspect_id=a["aspect_id"],
                aspect_text=a["aspect_text"],
                query_id=a["query_id"],
                topic_id=a["topic_id"],
                question_id=a["question_id"],
                priority=a["priority"],
                evidence_kind=a["evidence_kind"],
                source_classes=tuple(a.get("source_classes") or []),
                required_fields=tuple(a.get("required_fields") or []),
                freshness_policy=a.get("freshness_policy"),
                minimum_sources=int(a.get("minimum_sources") or 1),
                local_query=a.get("local_query"),
                external_query=a.get("external_query"),
            )
            for a in (d.get("aspects") or [])
        )
        return cls(topic_id=d["topic_id"], context=context, aspects=aspects,
                   query_plan_version=d.get("query_plan_version") or QUERY_PLAN_VERSION)


# ---------------------------------------------------------------------------
# 确定性派生
# ---------------------------------------------------------------------------

def _primary_requirement(question: PS.PlannedQuestion) -> dict | None:
    """取问题首个 evidence_requirement（切片内问题单 requirement 为主；多 requirement
    时按 evidence_kind 优先级取首个非 calculation 声明）。"""
    reqs = list(question.evidence_requirements)
    if not reqs:
        return None
    for r in reqs:
        if r.get("evidence_kind") in ("table", "table_row", "paragraph", "web",
                                      "field", "structured_db"):
            return r
    return reqs[0]


def _derive_queries(context: TopicResearchContext, aspect: str,
                    source_classes: tuple[str, ...]) -> tuple[str | None, str | None]:
    """由主体 + aspect + 来源类别确定性拼接本地/外部查询词。

    - 本地查询（company_industry / financial / structured_db）：``公司名 + aspect``。
    - 外部查询（external）：``行业名 + aspect``；行业名为空时回退公司名。
    不引入 LLM、不写死公司/行业。
    """
    local: str | None = None
    external: str | None = None
    if any(c in source_classes for c in ("company_industry", "financial",
                                         "structured_db")):
        local = f"{context.company_name} {aspect}".strip()
    if "external" in source_classes:
        base = " ".join(context.industry_names).strip() or context.company_name
        external = f"{base} {aspect}".strip()
    return local, external


def derive_aspect_query(topic_id: str, question: PS.PlannedQuestion, aspect: str,
                        context: TopicResearchContext) -> AspectQuery:
    """一个 aspect → 一条 AspectQuery（确定性，同输入同输出）。"""
    req = _primary_requirement(question) or {}
    source_classes = tuple(req.get("source_classes") or [])
    required_fields = tuple(req.get("required_fields") or [])
    local_q, external_q = _derive_queries(context, aspect, source_classes)

    aspect_id = f"asp_{_sha256_json([topic_id, question.question_id, aspect])[:24]}"
    query_id = f"q_{_sha256_json([aspect_id, local_q, external_q,
                                  req.get('evidence_kind') or '',
                                  list(source_classes)])[:24]}"
    return AspectQuery(
        aspect_id=aspect_id,
        aspect_text=aspect,
        query_id=query_id,
        topic_id=topic_id,
        question_id=question.question_id,
        priority=question.priority,
        evidence_kind=req.get("evidence_kind") or "",
        source_classes=source_classes,
        required_fields=required_fields,
        freshness_policy=req.get("freshness_policy"),
        minimum_sources=int(req.get("minimum_sources") or 1),
        local_query=local_q,
        external_query=external_q,
    )


def derive_topic_queries(task: PS.SectionTask, topic_id: str,
                         context: TopicResearchContext) -> TopicQueryPlan:
    """把 task 内 topic_id 的全部必答 aspect 拆解为查询计划。

    契约：``context`` 必须显式提供（公司名 / 行业名 / 报告时点），行业名来自上游
    主体材料或契约输入，绝不写死。
    """
    aspects: list[AspectQuery] = []
    for q in task.questions:
        if q.topic_id != topic_id:
            continue
        for aspect in q.required_aspects:
            aspects.append(derive_aspect_query(topic_id, q, aspect, context))
    return TopicQueryPlan(topic_id=topic_id, context=context, aspects=tuple(aspects))


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    """合成覆盖三样本的 SectionTask，验证派生是确定性、无公司硬编码的。"""
    context = TopicResearchContext(
        company_id="300750", company_name="宁德时代",
        industry_names=("动力电池",), report_as_of="2026-03-31")

    def _q(qid, topic, aspects, kind, sources, req_fields=()):
        return PS.PlannedQuestion(
            question_id=qid, question=f"{qid} 问题", priority="P0", topic_id=topic,
            required_aspects=tuple(aspects),
            evidence_requirements=(
                {"requirement_id": "er_x", "evidence_kind": kind,
                 "source_classes": list(sources), "minimum_sources": 1,
                 "required_fields": list(req_fields)},),
        )

    business_q = _q("company_business_main", "company_business",
                    ["主营业务构成", "各业务收入及收入占比", "各业务成本与毛利构成",
                     "产业链位置", "对应报告期与口径"],
                    "table", ["company_industry"],
                    ["主营业务", "收入构成", "毛利构成"])
    scale_q = _q("industry_scale_cycle", "industry_scale_cycle",
                 ["行业规模", "增速", "当前周期位置", "数据截止日期与统计口径"],
                 "web", ["external"], ["行业规模", "增速", "数据截止日期", "统计口径"])
    transmission_q = _q("industry_risk_transmission", "industry_risk_transmission",
                        ["对收入的传导", "对成本与资本开支的传导", "对现金流与偿债能力的传导"],
                        "paragraph", ["company_industry", "external"])

    task = PS.SectionTask(
        task_id="task_selfcheck", plan_id="plan_selfcheck", section_id="mixed",
        title="混合", purpose="自检", research_policy="harness",
        topic_ids=("company_business", "industry_scale_cycle",
                   "industry_risk_transmission"),
        questions=(business_q, scale_q, transmission_q),
        output_requirements=(), evaluation_rule_ids=(),
        allowed_capabilities=(), blocking_rules=())

    plans = {t: derive_topic_queries(task, t, context)
             for t in ("company_business", "industry_scale_cycle",
                       "industry_risk_transmission")}

    # 确定性：同输入重算，query_id / aspect_id 全部一致。
    plans2 = {t: derive_topic_queries(task, t, context) for t in plans}
    deterministic = all(
        plans[t].to_dict() == plans2[t].to_dict() for t in plans)

    # 本地/外部查询分工：company_business 纯本地，industry_scale_cycle 纯外部，
    # risk_transmission 两者皆有。
    business = plans["company_business"]
    scale = plans["industry_scale_cycle"]
    transmission = plans["industry_risk_transmission"]

    return {
        "query_plan_version": QUERY_PLAN_VERSION,
        "deterministic": deterministic,
        "company_business_aspect_count": len(business.aspects),
        "company_business_all_local_only": all(
            a.local_query and not a.external_query for a in business.aspects),
        "industry_scale_cycle_aspect_count": len(scale.aspects),
        "industry_scale_cycle_all_external_only": all(
            a.external_query and not a.local_query for a in scale.aspects),
        "transmission_aspect_count": len(transmission.aspects),
        "transmission_has_both": all(
            a.local_query and a.external_query for a in transmission.aspects),
        "industry_name_in_external_query": all(
            "动力电池" in (a.external_query or "") for a in scale.aspects),
        "sample_plan": plans["industry_scale_cycle"].to_dict(),
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m planning.topic_research",
        description="主题研究查询规划自检（确定性，不读库、不调 LLM）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main())
