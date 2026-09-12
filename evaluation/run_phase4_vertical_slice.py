"""Phase 4 纵向切片验收 Runner — 三样本一次性生成 + 确定性校验。

三样本（用户最终约束 §一）：
1. ``company_business_main`` — 公司主营（表 + 2-4 分析段）：本地确定性 B1/B2 链取表5-10/5-11
   收入/成本 → EvidenceStructuredFact → A5 校验 → 确定性业务表格 + LLM 段落。
2. ``industry_scale_cycle`` — 行业规模/增速/周期：外部漏斗 search→fetch→snapshot →
   CitationAuthority + P3-B02 来源分级 → LLM 事实提取 → 来源/口径表 + 周期判断 + 段落。
3. ``industry_risk_transmission`` — 行业风险传导（3 条完整链）：本地+外部 → 3 链段落。

停止边界（用户最终约束）：三样本生成 + 确定性校验通过即停。不跑完整 41 题、不扩章、
不产 2-3 万字报告、不进 Phase 5、每个样本至多 1 次真实 LLM。

CLI:
  python -m evaluation.run_phase4_vertical_slice --self-check   # 纯组装自检（无 I/O / LLM）
  python -m evaluation.run_phase4_vertical_slice [--real-llm]   # 真实 300750 三样本
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from planning import schema as PS
from planning.topic_research import TopicResearchContext, derive_topic_queries
from sections import chapter_writer as CW
from sections.topic_research import (
    ExternalFunnel, TopicBudget, assess_external_cell, assess_local_cell,
    budget_for, derive_pack_id, prepare_stores, run_topic,
)

log = logging.getLogger("run_phase4_vertical_slice")

FIXTURE_COMPANY_ID = "300750"
FIXTURE_SUBJECT = "宁德时代"
FIXTURE_INDUSTRY = ("动力电池",)
REPORT_AS_OF = "2026-03-31"

SLICE_VERSION = "phase4-vertical-slice-v1"

SAMPLES = ("company_business_main", "industry_scale_cycle", "industry_risk_transmission")

TITLE_BY_SAMPLE = {
    "company_business_main": "公司主营业务构成",
    "industry_scale_cycle": "行业规模、增速与当前周期位置",
    "industry_risk_transmission": "行业风险向授信主体的传导路径",
}


class SliceRunnerError(Exception):
    pass


# ---------------------------------------------------------------------------
# 契约（与 standard_v2.yaml 三样本一致，不写死公司/行业）
# ---------------------------------------------------------------------------

def _question(qid, question, topic_id, aspects, kind, sources, *, priority="P0",
              min_sources=1, req_fields=(), missing_policy="write_not_found",
              blocking_policy=(), impact_scope=()):
    return PS.PlannedQuestion(
        question_id=qid, question=question, priority=priority, topic_id=topic_id,
        required_aspects=tuple(aspects),
        evidence_requirements=(
            {"requirement_id": f"er_{qid}", "evidence_kind": kind,
             "source_classes": list(sources), "minimum_sources": min_sources,
             "required_fields": list(req_fields)},),
        missing_policy=missing_policy, blocking_policy=tuple(blocking_policy),
        impact_scope=tuple(impact_scope))


def build_sample_tasks() -> dict[str, PS.SectionTask]:
    business_q = _question(
        "company_business_main",
        "主营业务、收入/成本/毛利构成与产业链位置；主营业务完全无法确认时阻断章节",
        "company_business",
        ["主营业务构成", "各业务收入及收入占比", "各业务成本与毛利构成",
         "产业链位置", "对应报告期与口径"],
        "table", ["company_industry"], req_fields=["主营业务", "收入构成", "毛利构成"],
        blocking_policy=["SECTION_BLOCKED"], impact_scope=["subject"])
    scale_q = _question(
        "industry_scale_cycle", "行业规模、增速与当前周期位置（缺单一数字不阻断，可用代理指标）",
        "industry_scale_cycle",
        ["行业规模", "增速", "当前周期位置", "数据截止日期与统计口径"],
        "web", ["external"], req_fields=["行业规模", "增速", "数据截止日期", "统计口径"],
        missing_policy="proxy_allowed")
    transmission_q = _question(
        "industry_risk_transmission",
        "行业风险向借款人收入、成本、资本开支、现金流和偿债能力的传导",
        "industry_risk_transmission",
        ["对收入的传导", "对成本与资本开支的传导", "对现金流与偿债能力的传导"],
        "paragraph", ["company_industry", "external"], impact_scope=["solvency"])

    return {
        "company_business_main": PS.SectionTask(
            task_id="slice_business", plan_id="slice", section_id="company",
            title="公司主营业务构成", purpose="切片验收", research_policy="harness",
            topic_ids=("company_business",), questions=(business_q,),
            output_requirements=(), evaluation_rule_ids=(),
            allowed_capabilities=(), blocking_rules=()),
        "industry_scale_cycle": PS.SectionTask(
            task_id="slice_scale", plan_id="slice", section_id="industry",
            title="行业规模与周期", purpose="切片验收", research_policy="harness",
            topic_ids=("industry_scale_cycle",), questions=(scale_q,),
            output_requirements=(), evaluation_rule_ids=(),
            allowed_capabilities=(), blocking_rules=()),
        "industry_risk_transmission": PS.SectionTask(
            task_id="slice_transmission", plan_id="slice", section_id="industry",
            title="行业风险传导", purpose="切片验收", research_policy="harness",
            topic_ids=("industry_risk_transmission",), questions=(transmission_q,),
            output_requirements=(), evaluation_rule_ids=(),
            allowed_capabilities=(), blocking_rules=()),
    }


SAMPLE_TOPIC = {
    "company_business_main": "company_business",
    "industry_scale_cycle": "industry_scale_cycle",
    "industry_risk_transmission": "industry_risk_transmission",
}


# ---------------------------------------------------------------------------
# 本地确定性事实（复用 B1/B2 只读链：表5-10/5-11 → facts）
# ---------------------------------------------------------------------------

def _gather_business_facts(ev_db: str, fin_db: str, company_id: str):
    """表5-10/5-11 收入/成本 → EvidenceStructuredFact（只读，fail-closed）。"""
    from evaluation import build_intermediate_preview as BIP
    from financial_v2 import evidence_facts as EF

    document_id = BIP._ro_discover_document_id(ev_db, company_id)
    if not document_id:
        raise SliceRunnerError(f"{company_id} 未发现表5-10/5-11 表题文档（fail-closed）")
    subject = BIP._ro_subject(fin_db, company_id) or FIXTURE_SUBJECT
    blocks = BIP._note_table_blocks(
        BIP._ro_list_document_evidence(ev_db, company_id, document_id))
    if not blocks:
        raise SliceRunnerError(f"{company_id}/{document_id} 无表5-10/5-11 表块")
    materials = [BIP._to_inspected(b) for b in blocks]
    facts = EF.build_evidence_facts(materials, subject=subject)
    return {"subject": subject, "document_id": document_id, "facts": facts}


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


def _evidence_fact_dicts(facts) -> tuple[dict, ...]:
    return tuple(f.to_dict() for f in facts)


def _render_business_chapter(evidence_facts, authority, paragraphs) -> CW.ChapterDraft:
    table, cdisp, fdisp = CW.build_business_table(evidence_facts)
    idx = CW.build_fact_index(evidence_facts, ())
    return CW.build_chapter(
        topic_id="company_business", question_id="company_business_main",
        kind="business", paragraphs=paragraphs, tables=(table,),
        fact_display=fdisp, calc_display=cdisp, authority=authority, fact_index=idx)


def _render_industry_chapter(external_facts, authority, paragraphs) -> CW.ChapterDraft:
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
        fact_display=fdisp, calc_display={}, authority=authority, fact_index=idx)


def _render_transmission_chapter(external_facts, authority, paragraphs) -> CW.ChapterDraft:
    idx = CW.build_fact_index((), external_facts)
    fdisp = {f["fact_id"]: f.get("value") or "" for f in external_facts}
    return CW.build_chapter(
        topic_id="industry_risk_transmission",
        question_id="industry_risk_transmission", kind="transmission",
        paragraphs=paragraphs, tables=(),
        fact_display=fdisp, calc_display={}, authority=authority, fact_index=idx)


# ---------------------------------------------------------------------------
# LLM 路径（每样本至多 1 次；确定性回退）
# ---------------------------------------------------------------------------

def _llm_draft_paragraphs(kind: str, title: str, fact_index: dict, calc_display: dict,
                          *, model: str, paragraphs_hint: str = "2-4"):
    """LLM 写结构化段落；失败回退空段落（fail-closed，不产出假文本）。"""
    from llm import client

    prompt = CW.render_prompt("topic_chapter_writer_v1", {
        "kind": kind, "title": title, "paragraphs_hint": paragraphs_hint,
        "fact_context": CW.fact_context_lines(fact_index),
        "calc_context": CW.calc_context_lines(calc_display),
    })
    raw = client.chat_with_usage(
        [{"role": "user", "content": prompt}], model=model, max_tokens=4096,
        prompt_version=CW.PROMPT_VERSION).text
    payload = CW._parse_json(raw)
    return CW.paragraphs_from_payload(payload, topic_id=kind, question_id=kind)


def _llm_extract_facts(external_sources: tuple[dict, ...], *, model: str) -> tuple[dict, ...]:
    """LLM 从外部快照提取可核查事实；失败回退空（fail-closed）。"""
    from llm import client

    if not external_sources:
        return ()
    src_block = "\n\n".join(
        f"- id={s['source_snapshot_id']} 等级={s.get('source_grade')} "
        f"日期={s.get('published_at')}\n  {s.get('title') or s.get('canonical_url')}"
        for s in external_sources)
    prompt = CW.render_prompt("topic_fact_validation_v1", {"sources": src_block})
    raw = client.chat_with_usage(
        [{"role": "user", "content": prompt}], model=model, max_tokens=4096,
        prompt_version=CW.FACT_VALIDATION_PROMPT_VERSION).text
    payload = CW._parse_json(raw)
    return tuple(payload.get("facts") or [])


# ---------------------------------------------------------------------------
# 单样本执行
# ---------------------------------------------------------------------------

def _run_business_sample(*, plan, authority, ev_db, fin_db, company_id, model,
                         real_llm) -> dict:
    gathered = _gather_business_facts(ev_db, fin_db, company_id)
    facts = gathered["facts"]
    matrix = _business_matrix(plan.aspects, facts, authority)
    ef_dicts = _evidence_fact_dicts(facts)
    paragraphs = ()
    if real_llm:
        idx = CW.build_fact_index(ef_dicts, ())
        _, cdisp, _ = CW.build_business_table(ef_dicts)
        try:
            paragraphs = _llm_draft_paragraphs(
                "business", "主营业务构成", idx, cdisp, model=model)
        except Exception as e:  # noqa: BLE001 - LLM 失败回退确定性
            log.warning("business LLM 段落失败，回退确定性: %s", e)
    chapter = _render_business_chapter(ef_dicts, authority, paragraphs)

    funnel = ExternalFunnel()
    pack_id = derive_pack_id(
        topic_id="company_business", question_id="company_business_main",
        context=plan.context, budget=budget_for("company_business_main"),
        matrix=matrix, funnel=funnel, verified_facts=(), evidence_facts=ef_dicts)
    return {"sample": "company_business_main", "pack_id": pack_id,
            "matrix": [c.as_dict() for c in matrix], "funnel": funnel.as_dict(),
            "evidence_fact_count": len(facts),
            "subject": gathered["subject"], "document_id": gathered["document_id"],
            "chapter": chapter.as_dict()}


def _run_external_sample(*, sample, plan, authority, registry, company_id, model,
                         real_llm, budget) -> dict:
    pack = run_topic(plan, question_id=sample, budget=budget, company_id=company_id,
                     authority=authority, registry=registry, subject=FIXTURE_SUBJECT)
    external_facts = ()
    paragraphs = ()
    if real_llm and pack.external_sources:
        try:
            external_facts = _llm_extract_facts(pack.external_sources, model=model)
        except Exception as e:  # noqa: BLE001
            log.warning("%s LLM 事实提取失败: %s", sample, e)
    if real_llm and external_facts:
        idx = CW.build_fact_index((), external_facts)
        try:
            paragraphs = _llm_draft_paragraphs(
                "industry" if sample == "industry_scale_cycle" else "transmission",
                TITLE_BY_SAMPLE[sample], idx, {}, model=model,
                paragraphs_hint="2-4" if sample == "industry_scale_cycle" else "3")
        except Exception as e:  # noqa: BLE001
            log.warning("%s LLM 段落失败: %s", sample, e)

    if sample == "industry_scale_cycle":
        chapter = _render_industry_chapter(external_facts, authority, paragraphs)
    else:
        chapter = _render_transmission_chapter(external_facts, authority, paragraphs)

    return {"sample": sample, "pack_id": pack.pack_id,
            "matrix": [c.as_dict() for c in pack.matrix], "funnel": pack.funnel.as_dict(),
            "external_source_count": len(pack.external_sources),
            "external_fact_count": len(external_facts),
            "chapter": chapter.as_dict()}


# ---------------------------------------------------------------------------
# 主编排
# ---------------------------------------------------------------------------

def run(company_id: str, *, out_root: str, run_id: str | None = None,
        real_llm: bool = False, model: str | None = None,
        ev_db: str | None = None, fin_db: str | None = None,
        ext_db: str | None = None) -> dict:
    if company_id != FIXTURE_COMPANY_ID:
        raise SliceRunnerError(
            f"本 Runner 是 {FIXTURE_COMPANY_ID}（{FIXTURE_SUBJECT}）case-specific，"
            f"不支持其它 company（收到 {company_id}）")
    from config import LLM_MODEL
    from routing import context as routing_context
    from sections import citation_authority as CA
    from tools import adapters

    run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "_" + uuid.uuid4().hex[:8])
    model = model or LLM_MODEL
    root = Path(__file__).resolve().parent
    ev_db = ev_db or str(root.parent / "data" / "evidence.db")
    fin_db = fin_db or str(root.parent / "data" / "financial_v2.db")
    ext_db = ext_db or str(root.parent / "data" / "external_sources.db")

    prepare_stores(ev_db, fin_db, ext_db)
    context = routing_context.build_route_context(
        company_id, external_research_enabled=True)
    authority = CA.build_citation_authority(company_id, context, ev_db=ev_db,
                                            fin_db=fin_db, ext_db=ext_db)
    registry = adapters.build_default_registry(audit_dir=str(root.parent / "logs" / "tools"))

    ctx = TopicResearchContext(company_id=company_id, company_name=FIXTURE_SUBJECT,
                               industry_names=FIXTURE_INDUSTRY, report_as_of=REPORT_AS_OF)
    tasks = build_sample_tasks()

    results = []
    for sample in SAMPLES:
        task = tasks[sample]
        plan = derive_topic_queries(task, SAMPLE_TOPIC[sample], ctx)
        budget = budget_for(sample)
        if sample == "company_business_main":
            r = _run_business_sample(plan=plan, authority=authority, ev_db=ev_db,
                                     fin_db=fin_db, company_id=company_id,
                                     model=model, real_llm=real_llm)
        else:
            r = _run_external_sample(sample=sample, plan=plan, authority=authority,
                                     registry=registry, company_id=company_id,
                                     model=model, real_llm=real_llm, budget=budget)
        results.append(r)

    out_dir = Path(out_root) / f"phase4_vertical_slice_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=False)
    for r in results:
        sample_dir = out_dir / r["sample"]
        sample_dir.mkdir(parents=True, exist_ok=False)
        chapter = r["chapter"]
        (sample_dir / "chapter.md").write_text(chapter["markdown"], encoding="utf-8")
        (sample_dir / "chapter.json").write_text(
            json.dumps(chapter, ensure_ascii=False, indent=2), encoding="utf-8")
        (sample_dir / "summary.json").write_text(
            json.dumps({k: v for k, v in r.items() if k != "chapter"},
                       ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "slice_version": SLICE_VERSION,
        "run_id": run_id,
        "company_id": company_id,
        "real_llm": real_llm,
        "model": model,
        "samples": [
            {"sample": r["sample"], "pack_id": r["pack_id"],
             "obtained_aspects": sum(1 for c in r["matrix"] if c["obtained"]),
             "total_aspects": len(r["matrix"]),
             "chapter_ok": r["chapter"]["verdict"]["ok"],
             "external_source_count": r.get("external_source_count", 0),
             "external_fact_count": r.get("external_fact_count", 0),
             "evidence_fact_count": r.get("evidence_fact_count", 0),
            }
            for r in results
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("vertical slice written to %s", out_dir)
    return {"run_id": run_id, "output_dir": str(out_dir), "manifest": manifest}


# ---------------------------------------------------------------------------
# 自检（纯组装，无 I/O / LLM）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    class _Verdict:
        valid = True

    class _Auth:
        def validate(self, ref):
            return _Verdict()

    from planning.topic_research import AspectQuery

    ctx = TopicResearchContext(company_id=FIXTURE_COMPANY_ID,
                               company_name=FIXTURE_SUBJECT,
                               industry_names=FIXTURE_INDUSTRY,
                               report_as_of=REPORT_AS_OF)
    tasks = build_sample_tasks()
    plans = {s: derive_topic_queries(tasks[s], SAMPLE_TOPIC[s], ctx) for s in SAMPLES}
    auth = _Auth()

    # business：合成收入/成本事实 → 表格 + 矩阵 + 段落校验
    ef = (
        {"evidence_fact_id": "ef-r1", "evidence_id": "e1",
         "revenue_cost_category": "revenue", "business_segment": "动力电池系统",
         "period": "2025-12-31", "value": "316506369000.0", "page_number": 12},
        {"evidence_fact_id": "ef-c1", "evidence_id": "e2",
         "revenue_cost_category": "cost", "business_segment": "动力电池系统",
         "period": "2025-12-31", "value": "241064397000.0", "page_number": 13},
    )
    from sections.chapter_writer import ParagraphDraft, SentenceDraft

    good_para = ParagraphDraft(
        topic_id="company_business", question_id="company_business_main",
        sentences=(SentenceDraft(sentence_type="fact",
                                 text="{{fact:ef-r1}}为主要收入来源，毛利率 {{calc:margin_0}}。",
                                 fact_ids=("ef-r1",)),
                   SentenceDraft(sentence_type="inference", text="动力电池为核心主业。",
                                 fact_ids=("ef-r1",))))
    chapter = _render_business_chapter(ef, auth, (good_para,))
    matrix = _business_matrix(plans["company_business_main"].aspects,
                              [type("F", (), {
                                  "evidence_fact_id": f["evidence_fact_id"],
                                  "evidence_id": f["evidence_id"],
                                  "revenue_cost_category": f["revenue_cost_category"],
                                  "business_segment": f["business_segment"],
                                  "period": f["period"], "value": f["value"],
                                  "page_number": f["page_number"]})()
                               for f in ef],
                              auth)

    # industry：合成外部事实 → 来源表 + 周期判断
    ext_facts = (
        {"fact_id": "efext-1", "source_snapshot_id": "s1", "fact_class": "scale",
         "value": "1.2 万亿元", "period": "2025", "source_grade": "B",
         "published_at": "2025-01-01", "canonical_url": "https://gov.cn/a",
         "statement": "2025 年动力电池行业规模约 1.2 万亿元"},
        {"fact_id": "efext-2", "source_snapshot_id": "s2", "fact_class": "growth",
         "value": "15%", "period": "2025", "source_grade": "C",
         "published_at": "2025-02-01", "canonical_url": "https://eastmoney.com/b",
         "statement": "行业增速约 15%"},
    )
    ind_chapter = _render_industry_chapter(ext_facts, auth, ())

    return {
        "slice_version": SLICE_VERSION,
        "samples": list(SAMPLES),
        "aspect_counts": {s: len(plans[s].aspects) for s in SAMPLES},
        "business_chapter_ok": chapter.verdict.ok,
        "business_table_rows": len(chapter.tables[0].rows),
        "business_obtained_aspects": sum(1 for c in matrix if c.obtained),
        "industry_chapter_ok": ind_chapter.verdict.ok,
        "industry_source_table_rows": len(ind_chapter.tables[0].rows),
        "industry_cycle_judgment_present": any(
            p.sentences and p.sentences[0].sentence_type == "inference"
            for p in ind_chapter.paragraphs),
        "markdown_rendered": bool(chapter.markdown and ind_chapter.markdown),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _ev_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "evidence.db")


def _fin_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "financial_v2.db")


def _ext_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "data" / "external_sources.db")


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run_phase4_vertical_slice",
        description="Phase 4 纵向切片三样本验收 Runner")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--company", default=FIXTURE_COMPANY_ID)
    parser.add_argument("--model", default=None)
    parser.add_argument("--ev-db", default=None)
    parser.add_argument("--fin-db", default=None)
    parser.add_argument("--ext-db", default=None)
    parser.add_argument("--out-root", default=str(
        Path(__file__).resolve().parent / "results"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0

    result = run(args.company, out_root=args.out_root, run_id=args.run_id,
                 real_llm=args.real_llm, model=args.model,
                 ev_db=args.ev_db or _ev_db_path(),
                 fin_db=args.fin_db or _fin_db_path(),
                 ext_db=args.ext_db or _ext_db_path())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
