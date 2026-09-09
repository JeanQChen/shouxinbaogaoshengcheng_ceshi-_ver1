"""Phase 4 Batch A — 确定性章节 Planner。

职责（任务书 §9.2）：
1. 加载并校验 Section Contract（fail-closed）。
2. 按 credit_type 解析 applies_when 条件，过滤不适用主题。
3. 只产出 company / financial / industry 三章节任务（synthesizer 属 Phase 5，
   project 属产品第二阶段，均不进入本批）。
4. 派生 plan_id / task_id（确定性），不调 LLM、不执行检索、不写任何正文。

只规划、不研究、不写 prose、不碰研究工具（任务书 §23 硬约束）。

CLI：
    python -m planning.report_planner --company 300750 --company-name 宁德时代 \
        --credit-type other --report-as-of 2026-03-31 \
        --contracts templates/contracts/standard_v2.yaml \
        --fin-db data/financial_v2.db --ev-db data/evidence.db \
        --section-db data/sections.db --validate-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from contracts import schema as CS
from contracts.loader import load_contracts
from contracts.validator import validate_contracts
from planning import schema as PS

logger = logging.getLogger("planning.report_planner")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def contract_file_fingerprint(contracts_path: str) -> str:
    """契约指纹 = 契约 YAML 文件字节的 sha256（任务书 §8.1）。"""
    return sha256_file(contracts_path)


def resolve_fingerprints(company_id: str, fin_db: str, ev_db: str, *,
                         scope: str = "consolidated", currency: str = "CNY",
                         as_of_date: str | None = None,
                         purpose: str = "credit_analysis") -> tuple[str, str | None]:
    """解析 evidence inventory 指纹 + current snapshot_id（复用 Phase 3 固化逻辑）。

    evidence 指纹镜像 run_actual_path_41._evidence_fingerprint：
    sha256(sorted([[document_id, current_document_version]]))，只计 status=current 文档。
    snapshot_id 来自 build_route_context（健康门已 fail-closed）。无证据/无快照是合法空态；
    真实 DB 错误 fail-closed 抛错。
    """
    from evidence import store as estore
    from financial_v2 import store as fstore
    from routing import context as rctx

    fstore.init_db(fin_db)
    estore.init_db(ev_db)
    ctx = rctx.build_route_context(company_id, scope=scope, currency=currency,
                                   as_of_date=as_of_date, purpose=purpose)

    records: list[list[str]] = []
    for d in estore.list_documents(company_id):
        if d.status != "current":
            continue
        v = estore.current_document_version(company_id, d.document_id)
        if v is None:
            continue
        records.append([d.document_id, v])
    records.sort()
    ev_fp = PS.sha256_json(records)
    return ev_fp, ctx.snapshot_id


def build_job_input(job_id: str, company_id: str, company_name: str, credit_type: str,
                    report_as_of: str, template_id: str,
                    enabled_sections: tuple[str, ...], *,
                    scope: str = "consolidated", currency: str = "CNY",
                    purpose: str = "credit_analysis",
                    fin_db: str, ev_db: str) -> PS.ReportJobInput:
    """构造 ReportJobInput，解析 evidence 指纹 + 当前快照（只读 I/O）。"""
    ev_fp, snapshot_id = resolve_fingerprints(
        company_id, fin_db, ev_db, scope=scope, currency=currency,
        as_of_date=report_as_of, purpose=purpose)
    return PS.ReportJobInput(
        job_id=job_id,
        company_id=company_id,
        company_name=company_name,
        credit_type=credit_type,
        report_as_of=report_as_of,
        template_id=template_id,
        enabled_sections=tuple(enabled_sections),
        evidence_inventory_fingerprint=ev_fp,
        financial_snapshot_id=snapshot_id,
    )


def _evidence_requirement_dicts(q: CS.KeyQuestion) -> tuple[dict, ...]:
    return tuple(
        {
            "requirement_id": er.requirement_id,
            "evidence_kind": er.evidence_kind,
            "source_classes": list(er.source_classes),
            "minimum_sources": er.minimum_sources,
            "freshness_policy": er.freshness_policy,
            "required_fields": list(er.required_fields),
        }
        for er in q.evidence_requirements
    )


def _output_requirement_dicts(sec: CS.SectionContract) -> tuple[dict, ...]:
    return tuple(
        {"requirement_id": o.requirement_id, "kind": o.kind, "description": o.description}
        for o in sec.output_requirements
    )


def _planned_questions(topic: CS.TopicContract,
                       questions: list[CS.KeyQuestion]) -> tuple[PS.PlannedQuestion, ...]:
    out = []
    for q in questions:
        out.append(PS.PlannedQuestion(
            question_id=q.question_id,
            question=q.question,
            priority=q.priority,
            topic_id=topic.topic_id,
            required_aspects=tuple(q.required_aspects),
            evidence_requirements=_evidence_requirement_dicts(q),
            calculation_requirements=tuple(q.calculation_requirements),
            analysis_requirements=tuple(q.analysis_requirements),
            missing_policy=q.missing_policy,
            blocking_policy=tuple(q.blocking_policy),
            impact_scope=tuple(q.impact_scope),
        ))
    return tuple(out)


def _resolved_blocking_rules(sec: CS.SectionContract,
                             credit_type: str) -> tuple[PS.ResolvedBlockingRule, ...]:
    return tuple(
        PS.ResolvedBlockingRule(
            rule_id=cr.rule_id,
            scope_id=cr.scope_id,
            outcome=cr.outcome,
            applies=cr.condition.matches(credit_type),
        )
        for cr in sec.completion_rules
    )


def plan(job: PS.ReportJobInput, contracts: list[CS.SectionContract],
         contract_fingerprint: str, *, now: str | None = None) -> PS.ReportPlan:
    """确定性规划（纯函数，无 I/O、无 LLM、无检索）。"""
    # 1. 授信类型白名单（fail-closed）
    if job.credit_type not in CS.CREDIT_TYPES:
        raise ValueError(f"未知授信类型: {job.credit_type!r}，允许 {CS.CREDIT_TYPES}")

    # 2. 契约结构校验（fail-closed）
    result = validate_contracts(contracts)
    if not result.valid:
        raise ValueError("Section Contract 校验失败:\n"
                         + "\n".join(f"  - {e}" for e in result.errors))

    # 3. 章节映射与重复检测
    by_section = {sec.section_id: sec for sec in contracts}
    if len(by_section) != len(contracts):
        raise ValueError(f"Section Contract 存在重复 section_id: "
                         f"{[sec.section_id for sec in contracts]}")

    # 4. 契约版本一致性（fail-closed）
    if job.contract_version != CS.CONTRACT_VERSION:
        raise ValueError(f"契约版本不匹配: 期望 {CS.CONTRACT_VERSION}，"
                         f"实际 {job.contract_version!r}")

    # 5. enabled_sections 必须是第一阶段子集且存在（fail-closed）
    for sid in job.enabled_sections:
        if sid not in PS.PHASE4_SECTION_ORDER:
            raise ValueError(f"enabled_sections 含第一阶段之外章节: {sid!r}，"
                             f"允许 {PS.PHASE4_SECTION_ORDER}")
        if sid not in by_section:
            raise ValueError(f"enabled_sections 引用了契约不存在的章节: {sid!r}")

    # 6. 逐个章节建任务（按 PHASE4_SECTION_ORDER 固定顺序，与 enabled_sections 顺序无关）
    input_fingerprint = job.input_fingerprint()
    plan_id = PS.derive_plan_id(input_fingerprint, contract_fingerprint)

    tasks: list[PS.SectionTask] = []
    for sid in PS.PHASE4_SECTION_ORDER:
        if sid not in job.enabled_sections:
            continue
        sec = by_section[sid]

        applied_topic_ids: list[str] = []
        questions: list[PS.PlannedQuestion] = []
        for t in sec.required_topics:
            applies = t.applies_when is None or t.applies_when.matches(job.credit_type)
            if not applies:
                continue
            applied_topic_ids.append(t.topic_id)
            questions.extend(_planned_questions(t, t.key_questions))

        task_id = PS.derive_task_id(plan_id, sid)
        tasks.append(PS.SectionTask(
            task_id=task_id,
            plan_id=plan_id,
            section_id=sid,
            title=sec.title,
            purpose=sec.purpose,
            research_policy=sec.research_policy,
            topic_ids=tuple(applied_topic_ids),
            questions=tuple(questions),
            output_requirements=_output_requirement_dicts(sec),
            evaluation_rule_ids=tuple(er.rule_id for er in sec.evaluation_rules),
            allowed_capabilities=tuple(sec.allowed_capabilities),
            blocking_rules=_resolved_blocking_rules(sec, job.credit_type),
            dependency_versions={
                "contract_version": job.contract_version,
                "planner_version": PS.PLANNER_VERSION,
                "task_schema_version": PS.TASK_SCHEMA_VERSION,
                "evidence_inventory_fingerprint": job.evidence_inventory_fingerprint,
                "financial_snapshot_id": job.financial_snapshot_id or "",
                "credit_type": job.credit_type,
            },
        ))

    if not tasks:
        raise ValueError(f"enabled_sections 为空，未规划任何章节任务: {job.enabled_sections}")

    return PS.ReportPlan(
        plan_id=plan_id,
        job_id=job.job_id,
        company_id=job.company_id,
        company_name=job.company_name,
        credit_type=job.credit_type,
        report_as_of=job.report_as_of,
        template_id=job.template_id,
        input_fingerprint=input_fingerprint,
        contract_fingerprint=contract_fingerprint,
        planner_version=PS.PLANNER_VERSION,
        section_tasks=tuple(tasks),
        created_at=now or _utcnow(),
    )


def _plan_summary(plan: PS.ReportPlan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "job_id": plan.job_id,
        "company_id": plan.company_id,
        "company_name": plan.company_name,
        "credit_type": plan.credit_type,
        "report_as_of": plan.report_as_of,
        "template_id": plan.template_id,
        "input_fingerprint": plan.input_fingerprint,
        "contract_fingerprint": plan.contract_fingerprint,
        "planner_version": plan.planner_version,
        "created_at": plan.created_at,
        "tasks": [
            {
                "task_id": t.task_id,
                "section_id": t.section_id,
                "title": t.title,
                "research_policy": t.research_policy,
                "topic_count": len(t.topic_ids),
                "question_count": len(t.questions),
                "topic_ids": list(t.topic_ids),
                "question_ids": t.question_ids(),
            }
            for t in plan.section_tasks
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m planning.report_planner",
        description="Phase 4 确定性章节规划（只规划，不研究）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--company-name", required=True, dest="company_name")
    parser.add_argument("--credit-type", required=True, choices=CS.CREDIT_TYPES)
    parser.add_argument("--report-as-of", required=True, dest="report_as_of")
    parser.add_argument("--template-id", default="standard_v2")
    parser.add_argument("--enabled-sections", default="company,financial,industry",
                        help="逗号分隔；第一阶段子集")
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--fin-db", default="data/financial_v2.db")
    parser.add_argument("--ev-db", default="data/evidence.db")
    parser.add_argument("--section-db", default="data/sections.db")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--validate-only", action="store_true",
                        help="只规划并打印，不写 Store")
    parser.add_argument("--store", action="store_true",
                        help="规划后原子写入 section Store")
    parser.add_argument("--out", default=None, help="JSON 摘要输出路径（可选）")
    args = parser.parse_args(argv)

    enabled = tuple(s.strip() for s in args.enabled_sections.split(",") if s.strip())
    job_id = args.job_id or f"job_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"

    try:
        contracts = load_contracts(args.contracts)
        contract_fp = contract_file_fingerprint(args.contracts)
        job = build_job_input(
            job_id, args.company_id, args.company_name, args.credit_type,
            args.report_as_of, args.template_id, enabled,
            scope=args.scope, currency=args.currency, purpose=args.purpose,
            fin_db=args.fin_db, ev_db=args.ev_db,
        )
        report_plan = plan(job, contracts, contract_fp)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("规划失败")
        print(f"规划失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    summary = _plan_summary(report_plan)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.validate_only:
        print("\n[validate-only] 未写入 Store。")
        return 0

    if args.store:
        from sections import store as sstore
        sstore.init_db(args.section_db)
        run_id = f"run_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
        sstore.record_progress(job_id, run_id, "planning", "PLANNING",
                               detail=f"planner={PS.PLANNER_VERSION}")
        commit = sstore.commit_plan(report_plan, run_id=run_id)
        commit_info = {
            "plan_id": commit.plan_id,
            "reused": commit.reused,
            "current_switched": commit.current_switched,
            "task_count": commit.task_count,
        }
        print(f"\n[store] {json.dumps(commit_info, ensure_ascii=False)}")

    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n摘要已写入: {args.out}")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
