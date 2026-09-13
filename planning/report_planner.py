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


def _require_readonly_fin_db(fin_db: str) -> None:
    """只读校验 financial_v2 库：文件存在 + schema 版本 + 必需表，不符 fail-closed。

    复用 scripts.demo_preflight 的只读探测（mode=ro + schema 前缀校验 + 缺表探测），
    不重复实现 schema 版本判断；恢复提示引导重建财务主链。
    """
    from scripts import demo_preflight as dp

    conn = dp._ro_conn(fin_db)
    try:
        ok, detail = dp._check_schema_version(conn)
        if not ok:
            raise RuntimeError(
                f"financial_v2 库不可用：{detail}（恢复：重建财务主链 / 执行迁移）")
        missing = dp._missing_tables(conn, dp._FIN_TABLES)
        if missing:
            raise RuntimeError(
                f"financial_v2 库缺表：{missing}（恢复：重建财务主链）")
    finally:
        conn.close()


def _require_readonly_ev_db(ev_db: str) -> None:
    """只读校验 evidence 库：文件存在 + documents/evidence_sets 表，不符 fail-closed。"""
    from scripts import demo_preflight as dp

    conn = dp._ro_conn(ev_db)
    try:
        missing = dp._missing_tables(conn, ("documents", "evidence_sets"))
        if missing:
            raise RuntimeError(
                f"evidence 库缺表：{missing}（恢复：重建 Evidence current set）")
    finally:
        conn.close()


def resolve_fingerprints(company_id: str, fin_db: str, ev_db: str, *,
                         scope: str = "consolidated", currency: str = "CNY",
                         as_of_date: str | None = None,
                         purpose: str = "credit_analysis") -> tuple[str, str | None]:
    """解析 evidence inventory 指纹 + current snapshot_id（严格只读，复用 Phase 3 固化逻辑）。

    只读保证（任务书 §9.2 / Phase 4 Batch A 收口）：
    - 不调 init_db()，不建库 / 不建表 / 不迁移；
    - 缺失文件 / 版本不符 / 缺表 / 损坏 → fail-closed 抛错（带恢复提示）；
    - 健康但空（无证据 / 无快照）→ 空指纹 / None（合法空态，不抛错）。

    evidence 指纹镜像 run_actual_path_41._evidence_fingerprint：
    sha256(sorted([[document_id, current_document_version]]))，只计 status=current 文档。
    snapshot_id 来自 build_route_context（健康门已 fail-closed）。无证据/无快照是合法空态；
    真实 DB 错误 fail-closed 抛错。
    """
    from evidence import store as estore
    from financial_v2 import store as fstore
    from routing import context as rctx

    # 1) 只读校验（打开现有库，不创建、不迁移）。
    _require_readonly_fin_db(fin_db)
    _require_readonly_ev_db(ev_db)

    # 2) 校验通过后，把 Store 的模块 _db_path 指向已验证路径（不触发 init/migration），
    #    复用 Store 只读查询 + build_route_context 健康门（不重复实现健康判断）。
    fstore._db_path = Path(fin_db)
    estore._db_path = Path(ev_db)

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


# ---------------------------------------------------------------------------
# 规范形 + 指纹 + 任务构建 + 来源校验（公开接口，planner.plan 与 Contract 漂移校验共用）
# ---------------------------------------------------------------------------

def _blocking_rule_dicts(task: PS.SectionTask) -> list[dict]:
    """ResolvedBlockingRule → 规范形 dict（进入 canonical_section_task）。"""
    return [{"rule_id": b.rule_id, "scope_id": b.scope_id,
             "outcome": b.outcome, "applies": b.applies}
            for b in task.blocking_rules]


def canonical_question(q: PS.PlannedQuestion) -> dict:
    """PlannedQuestion → 规范形 dict（键排序稳定，供身份指纹与漂移比对）。

    这是「问题」的单一规范形实现：契约派生（planner）与漂移校验（evaluation）共用，
    不得各自复制。
    """
    return {
        "question_id": q.question_id,
        "question": q.question,
        "priority": q.priority,
        "topic_id": q.topic_id,
        "required_aspects": list(q.required_aspects),
        "evidence_requirements": [
            dict(sorted(
                {k: (list(v) if isinstance(v, tuple) else v) for k, v in er.items()}
                .items()))
            for er in q.evidence_requirements
        ],
        "calculation_requirements": list(q.calculation_requirements),
        "analysis_requirements": list(q.analysis_requirements),
        "missing_policy": q.missing_policy,
        "blocking_policy": list(q.blocking_policy),
        "impact_scope": list(q.impact_scope),
    }


def canonical_section_task(task: PS.SectionTask) -> dict:
    """SectionTask → 规范形 dict（task_id/plan_id/created 等派生字段按值比对，
    不进入身份）。
    """
    return {
        "section_id": task.section_id,
        "title": task.title,
        "purpose": task.purpose,
        "research_policy": task.research_policy,
        "topic_ids": list(task.topic_ids),
        "questions": [canonical_question(q) for q in task.questions],
        "output_requirements": [dict(sorted(o.items())) for o in task.output_requirements],
        "evaluation_rule_ids": list(task.evaluation_rule_ids),
        "allowed_capabilities": list(task.allowed_capabilities),
        "blocking_rules": [dict(sorted(b.items())) for b in _blocking_rule_dicts(task)],
        "dependency_versions": dict(sorted(task.dependency_versions.items())),
    }


def section_task_fingerprint(task: PS.SectionTask) -> str:
    """任务规范形身份指纹（进入 RunManifest / 漂移比对）。"""
    return PS.sha256_json(canonical_section_task(task))


def question_fingerprint(q: PS.PlannedQuestion) -> str:
    """问题规范形身份指纹。"""
    return PS.sha256_json(canonical_question(q))


def build_section_task(sec: CS.SectionContract, credit_type: str, *,
                       plan_id: str,
                       dependency_versions: dict | None = None,
                       contract_sha256: str = "") -> PS.SectionTask:
    """按 SectionContract 确定性构建一个章节任务（唯一实现，planner.plan 与漂移校验共用）。

    - 只按契约 + credit_type 派生：``required_topics`` 过滤 ``applies_when``，questions
      原样携带契约字段，output/eval/capability/blocking 由契约解析；
    - ``task_id`` 由 ``plan_id + section_id`` 确定性派生（同 plan 同 section → 同 task）；
    - ``dependency_versions`` 由调用方提供；``contract_sha256`` 非空时写入
      ``dependency_versions["contract_sha256"]``（来源可验证，见
      :func:`validate_section_task_provenance`）。
    """
    applied_topic_ids: list[str] = []
    questions: list[PS.PlannedQuestion] = []
    for t in sec.required_topics:
        applies = t.applies_when is None or t.applies_when.matches(credit_type)
        if not applies:
            continue
        applied_topic_ids.append(t.topic_id)
        questions.extend(_planned_questions(t, t.key_questions))

    deps = dict(dependency_versions or {})
    if contract_sha256:
        deps.setdefault("contract_sha256", contract_sha256)

    return PS.SectionTask(
        task_id=PS.derive_task_id(plan_id, sec.section_id),
        plan_id=plan_id,
        section_id=sec.section_id,
        title=sec.title,
        purpose=sec.purpose,
        research_policy=sec.research_policy,
        topic_ids=tuple(applied_topic_ids),
        questions=tuple(questions),
        output_requirements=_output_requirement_dicts(sec),
        evaluation_rule_ids=tuple(er.rule_id for er in sec.evaluation_rules),
        allowed_capabilities=tuple(sec.allowed_capabilities),
        blocking_rules=_resolved_blocking_rules(sec, credit_type),
        dependency_versions=deps,
    )


def validate_section_task_provenance(
        task: PS.SectionTask, sec: CS.SectionContract, credit_type: str, *,
        contract_version: str | None = None,
        contract_sha256: str | None = None,
        plan_id: str | None = None) -> tuple[bool, tuple[str, ...]]:
    """校验任务来源（provenance）与内容契约一致性，fail-closed。

    返回 ``(ok, diffs)``；``ok=False`` 时 ``diffs`` 为差异描述。以下任一即 fail：

    - 篡改 question / required_aspects / evidence_requirements / blocking_rules /
      output_requirements / 任一正式字段 → 内容漂移；
    - ``plan_id`` / ``contract_version`` / ``contract_sha256`` 来源不一致。其中
      ``contract_sha256`` 为严格相等：一旦调用方提供期望值，task 中的 SHA 缺失、
      ``None``、空字符串或错误值全部 fail-closed（无 legacy 静默兼容）。

    与 ``planner.plan`` 共用 :func:`build_section_task` + :func:`canonical_section_task`
    同一实现，不复制 Planner 逻辑。evaluation 层只消费本接口，不得调用任何
    ``_planned_questions`` 等私有函数。
    """
    diffs: list[str] = []

    # 来源一致性。
    if plan_id is not None and task.plan_id != plan_id:
        diffs.append(f"plan_id 不一致: 期望 {plan_id!r} 实际 {task.plan_id!r}")
    if contract_version is not None:
        actual_cv = task.dependency_versions.get("contract_version")
        if actual_cv != contract_version:
            diffs.append(
                f"contract_version 不一致: 期望 {contract_version!r} 实际 {actual_cv!r}")
    if contract_sha256 is not None:
        actual_sha = task.dependency_versions.get("contract_sha256")
        # 严格相等：缺失 / None / 空字符串 / 错误 SHA 全部 fail-closed。
        if actual_sha != contract_sha256:
            diffs.append(
                f"contract_sha256 不一致: 期望 {contract_sha256!r} 实际 {actual_sha!r}")

    # 内容一致性：以契约 + 独立期望 Contract 身份重新派生期望任务，规范形逐字段比对。
    # 期望身份（contract_version / contract_sha256）由调用方独立提供，不得把 task 自身
    # dependency_versions 原样当作权威来源（否则形成自我证明）。
    expected_deps = dict(task.dependency_versions)
    if contract_version is not None:
        expected_deps["contract_version"] = contract_version
    if contract_sha256 is not None:
        expected_deps["contract_sha256"] = contract_sha256
    expected = build_section_task(sec, credit_type, plan_id=task.plan_id,
                                  dependency_versions=expected_deps)
    exp = canonical_section_task(expected)
    act = canonical_section_task(task)
    for key in exp:
        if exp[key] != act.get(key):
            diffs.append(f"字段 {key} 漂移：期望 {exp[key]!r} 实际 {act.get(key)!r}")
    for key in act:
        if key not in exp:
            diffs.append(f"任务含契约未定义字段 {key}: {act[key]!r}")

    return (not diffs, tuple(diffs))


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

    dependency_versions = {
        "contract_version": job.contract_version,
        "planner_version": PS.PLANNER_VERSION,
        "task_schema_version": PS.TASK_SCHEMA_VERSION,
        "evidence_inventory_fingerprint": job.evidence_inventory_fingerprint,
        "financial_snapshot_id": job.financial_snapshot_id or "",
        "credit_type": job.credit_type,
    }

    tasks: list[PS.SectionTask] = []
    for sid in PS.PHASE4_SECTION_ORDER:
        if sid not in job.enabled_sections:
            continue
        sec = by_section[sid]
        tasks.append(build_section_task(
            sec, job.credit_type, plan_id=plan_id,
            dependency_versions=dependency_versions,
            contract_sha256=contract_fingerprint,
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
