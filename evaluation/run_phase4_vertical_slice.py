"""Phase 4 纵向研究切片 Runner — 契约派生（已移除影子契约）。

.. warning::
    本模块为 **EXPERIMENTAL_VERTICAL_SLICE / NOT_FORMAL_PHASE4_RUNTIME**：它只是
    纵向预览（topic/question/section 切片 + 只读投影）的评测脚手架，**不是**正式
    Phase 4 运行链。正式唯一运行链是
    ``sections.service.run_phase4 → company/industry worker → research_common
    → Router → harness.run_question → ToolRegistry``。

本轮边界：Runner 不再手工重建 PlannedQuestion / SectionTask，也不再实现真实研究循环
（不调 ToolRegistry / 不做 search/fetch/snapshot / 不调 run_topic / 不生成
ResearchOutcome / 不维护研究预算）。只做两件事：

1. **选择 + 身份校验**：从正式 ``ReportPlan`` 选 topic/question/section，经
   ``planner.validate_section_task_provenance`` 漂移校验（CONTRACT_TASK_DRIFT fail-closed）；
2. **只读投影**：对既有 ``SectionResult`` 的只读投影（本期仅保留渲染辅助，无真实研究）。

三种产物 scope（evaluation.contract_slice.ARTIFACT_SCOPES）：
- ``question_slice``  问题级切片（单个 question_id，绝不 chapter_complete=true）；
- ``topic_preview``   主题预览（某 topic 全部 KeyQuestions）；
- ``section_preview`` 章节预览（某 Section 全部 required topics + KeyQuestions）。

CLI（仅评测脚手架，无真实 LLM/博查）:
  python -m evaluation.run_phase4_vertical_slice --self-check  # 纯组装自检
  python -m evaluation.run_phase4_vertical_slice --dry-run     # 正式链路 dry-run（无 DB/LLM/博查）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from contracts import schema as CS
from contracts.loader import load_contracts
from planning import report_planner as planner
from planning import schema as PS
from planning.topic_research import TopicResearchContext, derive_topic_queries
from sections import chapter_writer as CW
from sections.topic_research import assess_external_cell, assess_local_cell

from evaluation import contract_slice as CSL

CONTRACT_PATH = "templates/contracts/standard_v2.yaml"
SLICE_VERSION = "phase4-contract-slice-v1"

# 实验性纵向切片：本模块非正式 Phase 4 运行链，仅评测脚手架（§九）。
EXPERIMENTAL_VERTICAL_SLICE = True
NOT_FORMAL_PHASE4_RUNTIME = True


# ---------------------------------------------------------------------------
# source-intent 配置（topic 维度通用规则，不写死公司/行业/授信类型）
# ---------------------------------------------------------------------------

# 博查 include 用 "|" 分隔域名（见外部检索文档），限定权威来源召回。
SOURCE_INTENT = {
    "industry_scale_cycle": {
        "label": "政府/监管 + 权威研究",
        "include": "gov.cn|ndrc.gov.cn|miit.gov.cn|stats.gov.cn|cninfo.com.cn",
    },
    "industry_risk_transmission": {
        "label": "政府/监管 + 交易所/法定披露",
        "include": "gov.cn|ndrc.gov.cn|miit.gov.cn|pbc.gov.cn|cninfo.com.cn",
    },
}


def _source_intent_for(topic_id: str) -> dict | None:
    cfg = SOURCE_INTENT.get(topic_id)
    if not cfg:
        return None
    return {"include": cfg.get("include"), "exclude": cfg.get("exclude")}


# ---------------------------------------------------------------------------
# 正式契约加载 + 计划（唯一任务来源；无任何手工问题/主题定义）
# ---------------------------------------------------------------------------

def load_formal(contract_path: str = CONTRACT_PATH) -> tuple[list[CS.SectionContract], str]:
    """加载冻结 Section Contract + 契约文件字节 sha256。"""
    contracts = load_contracts(contract_path)
    contract_sha = planner.contract_file_fingerprint(contract_path)
    return contracts, contract_sha


def contract_by_section(contracts: list[CS.SectionContract]) -> dict[str, CS.SectionContract]:
    return {sec.section_id: sec for sec in contracts}


def plan_dry(*, job_id: str, company_id: str, company_name: str, credit_type: str,
             report_as_of: str, enabled_sections: tuple[str, ...],
             contracts: list[CS.SectionContract], contract_sha: str) -> PS.ReportPlan:
    """确定性规划（dry-run：直接构造 ReportJobInput，不做 DB 指纹解析）。"""
    job = PS.ReportJobInput(
        job_id=job_id, company_id=company_id, company_name=company_name,
        credit_type=credit_type, report_as_of=report_as_of,
        enabled_sections=tuple(enabled_sections))
    return planner.plan(job, contracts, contract_sha)


def select_slice(plan: PS.ReportPlan, contracts: list[CS.SectionContract], *,
                 scope: str, section_id: str,
                 topic_id: str | None = None,
                 question_id: str | None = None) -> tuple[PS.SectionTask, dict]:
    """从正式 ReportPlan 选任务 → 漂移校验（fail-closed）→ 切片身份。

    返回 (原始 SectionTask, 切片身份 dict)。任何字段漂移 → ContractDriftError
    (reason=CONTRACT_TASK_DRIFT)。
    """
    task = CSL.find_section_task(plan, section_id)
    sec = contract_by_section(contracts)[section_id]
    CSL.validate_task_against_contract(task, sec, plan.credit_type)
    identity = CSL.slice_identity(task, scope=scope, topic_id=topic_id,
                                  question_id=question_id)
    return task, identity


# ---------------------------------------------------------------------------
# adopted-facts 硬防火墙（obtained MatrixCell 的 fact_ids 白名单）
# ---------------------------------------------------------------------------

def _adopted_fact_ids(matrix) -> tuple[str, ...]:
    ids: list[str] = []
    for c in matrix:
        if c.obtained:
            ids.extend(c.fact_ids)
    return tuple(dict.fromkeys(ids))


def _filter_evidence_to_adopted(facts, adopted_ids) -> tuple[dict, ...]:
    """本地 EvidenceStructuredFact → dict，只保留 adopted fact_ids。"""
    keep = set(adopted_ids)
    return tuple(f.to_dict() for f in facts
                 if getattr(f, "evidence_fact_id", None) in keep)


def _partition_extracted_facts(extracted: tuple[dict, ...],
                               adopted_ids: tuple[str, ...]) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """外部 LLM 提取事实 → (adopted, rejected)。

    防火墙：只采纳 support_verdict == SUPPORTED 且 source_snapshot_id 在 adopted
    白名单内的事实；其余一律 rejected（不进正文）。
    """
    keep = set(adopted_ids)
    adopted: list[dict] = []
    rejected: list[dict] = []
    for f in extracted:
        if (f.get("support_verdict") == "SUPPORTED"
                and f.get("source_snapshot_id") in keep):
            adopted.append(f)
        else:
            rejected.append(f)
    return tuple(adopted), tuple(rejected)


def _transmission_exposure_cell(facts, authority):
    """transmission 双支撑的公司暴露事实（收入/成本结构，表5-10/5-11）。"""
    from planning.topic_research import AspectQuery
    exp_aspect = AspectQuery(
        aspect_id="asp_exposure", aspect_text="收入与成本结构", query_id="q_exposure",
        topic_id="industry_risk_transmission", question_id="industry_risk_transmission",
        priority="P0", evidence_kind="table", source_classes=("company_industry",),
        required_fields=(), freshness_policy=None, minimum_sources=1,
        local_query=None, external_query=None)
    return assess_local_cell(aspect=exp_aspect, facts=facts, authority=authority)


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _business_matrix(aspects, facts, authority) -> tuple:
    return tuple(assess_local_cell(aspect=a, facts=facts, authority=authority)
                 for a in aspects)


def _external_matrix(aspects, gathered_sources, authority, topic_id) -> tuple:
    return tuple(assess_external_cell(aspect=a, sources=gathered_sources,
                                      authority=authority, topic_id=topic_id)
                 for a in aspects)


def _render_business_chapter(evidence_facts, authority, paragraphs,
                             adopted_fact_ids) -> CW.ChapterDraft:
    if not adopted_fact_ids:
        return CW.build_gap_chapter(
            topic_id="company_business", question_id="company_business_main",
            kind="business")
    table, cdisp, fdisp = CW.build_business_table(evidence_facts)
    idx = CW.build_fact_index(evidence_facts, ())
    return CW.build_chapter(
        topic_id="company_business", question_id="company_business_main",
        kind="business", paragraphs=paragraphs, tables=(table,),
        fact_display=fdisp, calc_display=cdisp, authority=authority, fact_index=idx,
        adopted_fact_ids=tuple(adopted_fact_ids))


def _render_industry_chapter(external_facts, authority, paragraphs,
                             adopted_fact_ids) -> CW.ChapterDraft:
    if not adopted_fact_ids:
        return CW.build_gap_chapter(
            topic_id="industry_scale_cycle", question_id="industry_scale_cycle",
            kind="industry")
    table = CW.build_source_table(external_facts)
    idx = CW.build_fact_index((), external_facts)
    fdisp = {f["fact_id"]: f.get("value") or "" for f in external_facts}
    cycle = CW.build_cycle_judgment(external_facts)
    paras = list(paragraphs)
    if cycle:
        from sections.chapter_writer import ParagraphDraft, SentenceDraft
        paras.append(ParagraphDraft(
            topic_id="industry_scale_cycle", question_id="industry_scale_cycle",
            sentences=(SentenceDraft(sentence_type="inference", text=cycle,
                                     fact_ids=tuple(f["fact_id"] for f in external_facts)),)))
    return CW.build_chapter(
        topic_id="industry_scale_cycle", question_id="industry_scale_cycle",
        kind="industry", paragraphs=tuple(paras), tables=(table,),
        fact_display=fdisp, calc_display={}, authority=authority, fact_index=idx,
        adopted_fact_ids=tuple(adopted_fact_ids))


def _render_transmission_chapter(external_facts, exposure_fact_dicts, authority,
                                 paragraphs, adopted_ids, exposure_ids) -> CW.ChapterDraft:
    """transmission 双支撑：行业驱动（外部）+ 公司暴露（本地收入/成本结构）缺一 → DATA_GAP。"""
    if not (adopted_ids and exposure_ids):
        return CW.build_gap_chapter(
            topic_id="industry_risk_transmission",
            question_id="industry_risk_transmission", kind="transmission")
    idx = CW.build_fact_index(exposure_fact_dicts, external_facts)
    fdisp = {f["evidence_fact_id"]: CW.format_yuan_yi(f.get("value"))
             for f in exposure_fact_dicts}
    for f in external_facts:
        fdisp[f["fact_id"]] = f.get("value") or ""
    combined = tuple(adopted_ids) + tuple(exposure_ids)
    return CW.build_chapter(
        topic_id="industry_risk_transmission",
        question_id="industry_risk_transmission", kind="transmission",
        paragraphs=paragraphs, tables=(),
        fact_display=fdisp, calc_display={}, authority=authority, fact_index=idx,
        adopted_fact_ids=combined)


# ---------------------------------------------------------------------------
# 正式编排：契约身份 manifest（dry-run 即产出；无真实 LLM/博查研究分支）
# ---------------------------------------------------------------------------

def _manifest_slices(plan: PS.ReportPlan, contracts: list[CS.SectionContract],
                     contract_sha: str, contract_path: str) -> dict:
    """两个预览（company_business topic_preview + industry section_preview）的
    契约身份与漂移校验结果（纯函数，无 DB/LLM/博查）。"""
    comp_task, comp_id = select_slice(
        plan, contracts, scope="topic_preview", section_id="company",
        topic_id="company_business")
    ind_task, ind_id = select_slice(
        plan, contracts, scope="section_preview", section_id="industry")

    # derive_topic_queries 自检：正式 SectionTask → TopicQueryPlan 数量与契约一致。
    comp_question_ids = [
        q.question_id for q in comp_task.questions if q.topic_id == "company_business"]
    ind_topic_ids = list(ind_task.topic_ids)

    return {
        "slice_version": SLICE_VERSION,
        "contract_path": contract_path,
        "contract_version": CS.CONTRACT_VERSION,
        "contract_sha256": contract_sha,
        "report_plan_id": plan.plan_id,
        "planner_version": plan.planner_version,
        "previews": {
            "company_business_topic_preview": {
                "section_id": "company",
                "section_task_id": comp_task.task_id,
                "task_canonical_fingerprint": comp_id["task_canonical_fingerprint"],
                "artifact_scope": comp_id["artifact_scope"],
                "selected_topic_ids": comp_id["selected_topic_ids"],
                "selected_question_ids": comp_id["selected_question_ids"],
                "covered_questions": comp_id["covered_questions"],
                "total_contract_questions": comp_id["total_contract_questions"],
                "covered_topics": comp_id["covered_topics"],
                "total_contract_topics": comp_id["total_contract_topics"],
                "chapter_complete": comp_id["chapter_complete"],
                "drift_validated": True,
            },
            "industry_section_preview": {
                "section_id": "industry",
                "section_task_id": ind_task.task_id,
                "task_canonical_fingerprint": ind_id["task_canonical_fingerprint"],
                "artifact_scope": ind_id["artifact_scope"],
                "selected_topic_ids": ind_id["selected_topic_ids"],
                "selected_question_ids": ind_id["selected_question_ids"],
                "covered_questions": ind_id["covered_questions"],
                "total_contract_questions": ind_id["total_contract_questions"],
                "covered_topics": ind_id["covered_topics"],
                "total_contract_topics": ind_id["total_contract_topics"],
                "chapter_complete": ind_id["chapter_complete"],
                "drift_validated": True,
            },
        },
        # 供架构审查：正式问题 / 主题清单（非手工重建）。
        "formal_company_business_questions": comp_question_ids,
        "formal_industry_topics": ind_topic_ids,
    }


def dry_run(*, company_id: str, company_name: str, industry_names: tuple[str, ...],
            credit_type: str = "other", report_as_of: str = "2026-03-31",
            contract_path: str = CONTRACT_PATH) -> dict:
    """正式链路 dry-run（无 DB/LLM/博查）：契约加载 → 计划 → 选择 → 漂移校验 → manifest。"""
    contracts, contract_sha = load_formal(contract_path)
    plan = plan_dry(
        job_id=f"job_dry_{uuid.uuid4().hex[:8]}", company_id=company_id,
        company_name=company_name, credit_type=credit_type, report_as_of=report_as_of,
        enabled_sections=("company", "industry"), contracts=contracts,
        contract_sha=contract_sha)
    manifest = _manifest_slices(plan, contracts, contract_sha, contract_path)

    # derive_topic_queries 正式派生（证明研究层已接入正式 SectionTask）。
    ctx = TopicResearchContext(company_id=company_id, company_name=company_name,
                               industry_names=industry_names, report_as_of=report_as_of)
    comp_task = CSL.find_section_task(plan, "company")
    ind_task = CSL.find_section_task(plan, "industry")
    comp_plan = derive_topic_queries(comp_task, "company_business", ctx)
    ind_plans = {t: derive_topic_queries(ind_task, t, ctx) for t in ind_task.topic_ids}

    manifest["query_plan"] = {
        "company_business_aspect_count": len(comp_plan.aspects),
        "company_business_question_count": len(comp_plan.question_ids)
        if hasattr(comp_plan, "question_ids") else len(set(a.question_id for a in comp_plan.aspects)),
        "industry_topic_query_plans": {
            t: len(p.aspects) for t, p in ind_plans.items()
        },
    }
    return manifest


# ---------------------------------------------------------------------------
# 自检（纯组装：契约加载 + 计划 + 选择 + 漂移校验 + 期间/一致性 + 合成渲染）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    class _Verdict:
        valid = True

    class _Auth:
        def validate(self, ref):
            return _Verdict()

    from planning.topic_research import AspectQuery
    from sections.chapter_writer import ParagraphDraft, SentenceDraft
    from sections.topic_research import aspect_fact_categories

    contracts, contract_sha = load_formal()
    plan = plan_dry(
        job_id="j_sc", company_id="C", company_name="测试", credit_type="other",
        report_as_of="2026-03-31", enabled_sections=("company", "industry"),
        contracts=contracts, contract_sha=contract_sha)

    # 漂移校验：正式 Planner 派生任务 → 无漂移；篡改 required_aspects → 漂移。
    comp_task = CSL.find_section_task(plan, "company")
    ind_task = CSL.find_section_task(plan, "industry")
    by_sec = contract_by_section(contracts)
    CSL.validate_task_against_contract(comp_task, by_sec["company"], "other")
    CSL.validate_task_against_contract(ind_task, by_sec["industry"], "other")
    drift_clean = True

    tampered = PS.SectionTask(
        task_id=comp_task.task_id, plan_id=comp_task.plan_id,
        section_id=comp_task.section_id, title=comp_task.title,
        purpose=comp_task.purpose, research_policy=comp_task.research_policy,
        topic_ids=comp_task.topic_ids,
        questions=tuple(
            PS.PlannedQuestion(**{**q.__dict__, "required_aspects": ("被篡改",)})
            for q in comp_task.questions
        ),
        output_requirements=comp_task.output_requirements,
        evaluation_rule_ids=comp_task.evaluation_rule_ids,
        allowed_capabilities=comp_task.allowed_capabilities,
        blocking_rules=comp_task.blocking_rules,
        dependency_versions=comp_task.dependency_versions,
    )
    drift_raises = False
    try:
        CSL.validate_task_against_contract(tampered, by_sec["company"], "other")
    except CSL.ContractDriftError as e:
        drift_raises = e.reason == CSL.CONTRACT_DRIFT

    # 合成 business 事实 → 表格 + 矩阵（正式 company_business_main 5 aspect）。
    class _Fact:
        def __init__(self, fid, eid, cat, seg, period, val):
            self.evidence_fact_id = fid
            self.evidence_id = eid
            self.revenue_cost_category = cat
            self.business_segment = seg
            self.period = period
            self.value = val
            self.page_number = 12

        def to_dict(self):
            return {"evidence_fact_id": self.evidence_fact_id,
                    "evidence_id": self.evidence_id,
                    "revenue_cost_category": self.revenue_cost_category,
                    "business_segment": self.business_segment,
                    "period": self.period, "value": self.value,
                    "page_number": self.page_number}

    facts = (_Fact("ef-r1", "e1", "revenue", "动力电池系统", "2025-12-31",
                   "316506369000.0"),
             _Fact("ef-c1", "e2", "cost", "动力电池系统", "2025-12-31",
                   "241064397000.0"))
    good_para = ParagraphDraft(
        topic_id="company_business", question_id="company_business_main",
        sentences=(SentenceDraft(sentence_type="fact",
                                 text="{{fact:ef-r1}}为主要收入来源，毛利率 {{calc:margin_0}}。",
                                 fact_ids=("ef-r1",)),
                   SentenceDraft(sentence_type="inference", text="动力电池为核心主业。",
                                 fact_ids=("ef-r1",))))
    comp_ctx = TopicResearchContext(company_id="C", company_name="测试",
                                    industry_names=("测试行业",), report_as_of="2026-03-31")
    comp_plan = derive_topic_queries(comp_task, "company_business", comp_ctx)
    matrix = _business_matrix(comp_plan.aspects, facts, _Auth())
    adopted_ids = _adopted_fact_ids(matrix)
    ef_dicts = _filter_evidence_to_adopted(facts, adopted_ids)
    chapter = _render_business_chapter(ef_dicts, _Auth(), (good_para,), adopted_ids)

    # 反虚假覆盖：产业链位置（supply_chain）不因收入/成本事实 obtained。
    chain_cat = aspect_fact_categories("产业链位置")
    chain_obtained = any(c.aspect_text == "产业链位置" and c.obtained for c in matrix)

    # 期间渲染 + 表文一致性（contract_slice 纯函数）。
    flow_annual = CSL.period_label("2025-12-31", semantics="flow")
    point_quarter = CSL.period_label("2026-03-31", semantics="point")
    trend = CSL.trend_label(["2023-12-31", "2024-12-31", "2025-12-31"])
    forbidden = CSL.find_forbidden_period_words("本报告期与本期期末")
    consistency = CSL.table_text_consistency(
        {f["evidence_fact_id"] for f in ef_dicts},
        {f["evidence_fact_id"] for f in ef_dicts})

    return {
        "slice_version": SLICE_VERSION,
        "contract_sha256": contract_sha,
        "company_business_question_ids": [
            q.question_id for q in comp_task.questions if q.topic_id == "company_business"],
        "industry_topic_ids": list(ind_task.topic_ids),
        "drift_clean": drift_clean,
        "drift_tamper_raises": drift_raises,
        "company_business_aspect_count": len(comp_plan.aspects),
        "business_chapter_ok": chapter.chapter_ok,
        "supply_chain_sentinel": chain_cat == ("supply_chain",),
        "supply_chain_not_fake_obtained": (not chain_obtained),
        "period_flow_annual": flow_annual,
        "period_point_quarter": point_quarter,
        "trend_label": trend,
        "forbidden_period_words": list(forbidden),
        "table_text_consistent": consistency == [],
        "shadow_contract_removed": all(
            n not in globals() for n in (
                "BUSINESS_ASPECTS", "build_sample_tasks", "_question", "SAMPLE_TOPIC")),
    }


# ---------------------------------------------------------------------------
# CLI（仅评测脚手架；无真实 LLM/博查研究分支）
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run_phase4_vertical_slice",
        description="Phase 4 纵向研究切片 Runner（EXPERIMENTAL / 非正式运行链）")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--company", default="300750")
    parser.add_argument("--company-name", default="宁德时代")
    parser.add_argument("--industry", default="动力电池")
    parser.add_argument("--credit-type", default="other")
    parser.add_argument("--report-as-of", default="2026-03-31")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        industry = tuple(s.strip() for s in args.industry.split(",") if s.strip())
        manifest = dry_run(company_id=args.company, company_name=args.company_name,
                           industry_names=industry, credit_type=args.credit_type,
                           report_as_of=args.report_as_of)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
