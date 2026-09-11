"""Phase 4 Batch D — 服务入口（编排三章节 + 评估 + 定向返工 + RunManifest + Preview DTO）。

职责（任务书 §14）：
- 一次性编排 company → financial → industry 三章节 Worker；
- 每章：Worker → 评估（Rules 先、LLM Evaluator 至多一次）→ 必要时定向返工（至多
  一批，返工后只做确定性最终检查，无二次 LLM Evaluator）→ 关联存储；
- 财务章节：审计意见 enrichment（多 Evidence 派生文本事实，不写入 FinancialSnapshot）；
- 构建 SectionRunManifest（代码指纹 + Phase 3 关闭指纹 + 批次版本 + 冻结输入），
  manifest 历史 append-only、current_manifest 独立指针；
- 产出三章节 Preview DTO 供 Streamlit 薄展示（Streamlit 只展示，不承担业务判断）。

硬约束（任务书三/实现时必须坚持）：
- llm_evaluator_calls 每章 ∈ {0,1}，超过即 fail-closed；
- Rules 失败不被 LLM 覆盖（blocking/rework 分支不调用 LLM Evaluator）；
- 返工最多一批，未受影响 Claim 身份不变；
- Evaluation 是 SectionResult 关联对象，不回写 SectionResult 身份；
- 不为 300750 / 宁德时代 / 任何 case_id 写专用分支；
- 不提交数据库/日志/API Key/个人配置/临时目录/截图缓存。

CLI:
    python -m sections.service --company 300750 --company-name 宁德时代 \
        --credit-type other --report-as-of 2026-03-31 \
        --contracts templates/contracts/standard_v2.yaml \
        --fin-db data/financial_v2.db --ev-db data/evidence.db \
        --ext-db data/external_sources.db --section-db data/sections.db \
        --validate-only
    python -m sections.service --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable

from contracts import schema as CS
from contracts.loader import load_contracts
from planning import report_planner as planner
from planning import schema as PS
from sections import audit_opinion as AO
from sections import citation_authority as CA
from sections import common as SC
from sections import company_worker as CW
from sections import financial_worker as FW
from sections import industry_worker as IW
from sections import llm_evaluator as LE
from sections import rework as RW
from sections import rules_evaluator as RE
from sections import schema as SS
from sections import store as sstore

logger = logging.getLogger("sections.service")

# 版本常量（变更必须递增；进入 manifest batch_versions / code_fingerprint）。
SERVICE_VERSION = "p4-service-v1"

# 代码指纹覆盖的 Phase 4 源码包（本批拥有/编写的代码）。
_PHASE4_CODE_DIRS = ("sections", "planning", "contracts")
# Phase 3 冻结关闭包（Phase 4 依赖的已冻结代码，不含本批新增）。
_PHASE3_CLOSURE_DIRS = ("harness", "routing", "tools", "financial_v2",
                        "evidence", "external_v2", "llm")


class ServiceError(RuntimeError):
    """服务编排 fail-closed 错误。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 配置与 DTO
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceConfig:
    """服务运行配置（库路径 / 口径 / 外部检索开关）。"""

    contracts_path: str = "templates/contracts/standard_v2.yaml"
    fin_db: str = "data/financial_v2.db"
    ev_db: str = "data/evidence.db"
    ext_db: str = "data/external_sources.db"
    harness_db: str = "data/harness.db"
    section_db: str = "data/sections.db"
    scope: str = "consolidated"
    currency: str = "CNY"
    purpose: str = "credit_analysis"
    model: str | None = None
    external_research_enabled: bool | None = None
    audit_dir: str | None = None
    checkpoint: bool = True


@dataclass(frozen=True)
class SectionOutcome:
    """单章节编排结果（Worker 产物 + 评估 + 可选返工；不改变 SectionResult 身份）。"""

    section_id: str
    task_id: str
    title: str
    section_result: SS.SectionResult | None
    evaluation: SS.SectionEvaluation | None
    rework_run: SS.SectionReworkRun | None
    final_rules_passed: bool | None
    error: str | None = None


@dataclass(frozen=True)
class SectionPreview:
    """章节预览 DTO（Streamlit 薄展示；纯展示，不做业务判断）。"""

    section_id: str
    task_id: str
    title: str
    section_result_id: str
    status: str
    decision: str | None
    evaluation_id: str | None
    llm_evaluator_calls: int
    claim_count: int
    unresolved_count: int
    rework_attempted: bool
    final_check_passed: bool | None
    issue_count: int
    markdown: str
    error: str | None


@dataclass(frozen=True)
class EvaluatedSection:
    """评估 + 返工后的最终产物（核心状态机输出，可离线测试）。"""

    section_result: SS.SectionResult
    evaluation: SS.SectionEvaluation
    rework_run: SS.SectionReworkRun | None
    final_rules_passed: bool | None
    llm_evaluator_calls: int


@dataclass(frozen=True)
class Phase4RunResult:
    """一次 Phase 4 服务的完整结果（不可变）。"""

    job_id: str
    run_id: str
    plan_id: str
    manifest_id: str
    manifest: SS.SectionRunManifest
    sections: tuple[SectionOutcome, ...]
    success: bool

    def previews(self) -> tuple[SectionPreview, ...]:
        return tuple(outcome_preview(o) for o in self.sections)


def outcome_preview(outcome: SectionOutcome) -> SectionPreview:
    """把 SectionOutcome 折叠成薄展示 DTO（None 安全）。"""
    r = outcome.section_result
    if r is None:
        return SectionPreview(
            section_id=outcome.section_id, task_id=outcome.task_id, title=outcome.title,
            section_result_id="", status="FAILED", decision=None, evaluation_id=None,
            llm_evaluator_calls=0, claim_count=0, unresolved_count=0,
            rework_attempted=False, final_check_passed=None, issue_count=0,
            markdown="", error=outcome.error)
    ev = outcome.evaluation
    return SectionPreview(
        section_id=r.section_id, task_id=r.task_id, title=outcome.title,
        section_result_id=r.section_result_id, status=r.status,
        decision=(ev.decision if ev is not None else None),
        evaluation_id=(ev.evaluation_id if ev is not None else None),
        llm_evaluator_calls=(ev.llm_evaluator_calls if ev is not None else 0),
        claim_count=len(r.claims), unresolved_count=len(r.unresolved),
        rework_attempted=(outcome.rework_run is not None),
        final_check_passed=outcome.final_rules_passed,
        issue_count=(len(ev.issues) if ev is not None else 0),
        markdown=r.markdown, error=outcome.error)


def preview_to_dict(p: SectionPreview) -> dict:
    return {
        "section_id": p.section_id, "task_id": p.task_id, "title": p.title,
        "section_result_id": p.section_result_id, "status": p.status,
        "decision": p.decision, "evaluation_id": p.evaluation_id,
        "llm_evaluator_calls": p.llm_evaluator_calls, "claim_count": p.claim_count,
        "unresolved_count": p.unresolved_count, "rework_attempted": p.rework_attempted,
        "final_check_passed": p.final_check_passed, "issue_count": p.issue_count,
        "markdown": p.markdown, "error": p.error,
    }


def preview_from_dict(d: dict) -> SectionPreview:
    return SectionPreview(
        section_id=d["section_id"], task_id=d["task_id"], title=d.get("title") or "",
        section_result_id=d.get("section_result_id") or "", status=d.get("status") or "FAILED",
        decision=d.get("decision"), evaluation_id=d.get("evaluation_id"),
        llm_evaluator_calls=int(d.get("llm_evaluator_calls") or 0),
        claim_count=int(d.get("claim_count") or 0),
        unresolved_count=int(d.get("unresolved_count") or 0),
        rework_attempted=bool(d.get("rework_attempted")),
        final_check_passed=d.get("final_check_passed"),
        issue_count=int(d.get("issue_count") or 0),
        markdown=d.get("markdown") or "", error=d.get("error"))


def phase4_result_to_dict(res: Phase4RunResult) -> dict:
    return {
        "job_id": res.job_id, "run_id": res.run_id, "plan_id": res.plan_id,
        "manifest_id": res.manifest_id, "success": res.success,
        "manifest": SS.manifest_to_dict(res.manifest),
        "sections": [preview_to_dict(outcome_preview(o)) for o in res.sections],
    }


# ---------------------------------------------------------------------------
# 指纹 / 版本 / 冻结 / manifest（纯函数，可离线测试）
# ---------------------------------------------------------------------------

def _hash_package(root: Path, pkg_dirs) -> str:
    """确定性哈希某组包目录下的全部 .py 文件（相对路径 + 字节，排序稳定）。

    新增/删除/改动任一文件都会改变指纹（代码漂移 = 新指纹），这是期望语义。
    """
    h = hashlib.sha256()
    paths: list[Path] = []
    for d in pkg_dirs:
        p = root / d
        if p.is_dir():
            paths.extend(p.rglob("*.py"))
    for pth in sorted(paths, key=lambda p: str(p.relative_to(root))):
        rel = str(pth.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(pth.read_bytes() if pth.exists() else b"MISSING")
        h.update(b"\0")
    return h.hexdigest()


def compute_code_fingerprint(root: str | Path | None = None) -> str:
    """Phase 4 代码指纹（本批拥有的源码：sections/planning/contracts）。"""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return _hash_package(root, _PHASE4_CODE_DIRS)


def compute_phase3_closure_fingerprint(root: str | Path | None = None) -> str:
    """Phase 3 关闭指纹（Phase 4 依赖的已冻结源码）。"""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return _hash_package(root, _PHASE3_CLOSURE_DIRS)


def build_batch_versions() -> dict:
    """本批各层版本常量（进入 manifest.batch_versions，冻结批次身份）。"""
    return {
        "service": SERVICE_VERSION,
        "planner": PS.PLANNER_VERSION,
        "task_schema": PS.TASK_SCHEMA_VERSION,
        "contract": CS.CONTRACT_VERSION,
        "rules_evaluator": RE.RULES_VERSION,
        "llm_evaluator": LE.EVALUATOR_VERSION,
        "llm_evaluator_prompt": LE.PROMPT_VERSION,
        "rework": RW.REWORK_VERSION,
        "audit": AO.ENRICHMENT_VERSION,
        "audit_prompt": AO.AUDIT_PROMPT_VERSION,
        "financial": {
            "renderer": SC.RENDERER_VERSION, "rules": SC.RULES_VERSION,
            "prompt": SC.PROMPT_VERSION, "worker": SC.WORKER_VERSION,
        },
        "company": {
            "renderer": CW.RENDERER_VERSION, "rules": CW.RULES_VERSION,
            "prompt": CW.PROMPT_VERSION, "worker": CW.WORKER_VERSION,
        },
        "industry": {
            "renderer": IW.RENDERER_VERSION, "rules": IW.RULES_VERSION,
            "prompt": IW.PROMPT_VERSION, "worker": IW.WORKER_VERSION,
        },
    }


def build_frozen(job: PS.ReportJobInput, plan: PS.ReportPlan, *, model_id: str,
                 scope: str, currency: str, purpose: str,
                 external_research_enabled: bool | None, audit_dir: str | None) -> dict:
    """冻结输入（contract/prompt/renderer/rules 版本走 batch_versions，此处冻结业务输入）。"""
    return {
        "company_id": job.company_id,
        "company_name": job.company_name,
        "credit_type": job.credit_type,
        "report_as_of": job.report_as_of,
        "template_id": job.template_id,
        "enabled_sections": sorted(job.enabled_sections),
        "scope": scope,
        "currency": currency,
        "purpose": purpose,
        "contract_fingerprint": plan.contract_fingerprint,
        "input_fingerprint": plan.input_fingerprint,
        "evidence_inventory_fingerprint": job.evidence_inventory_fingerprint,
        "financial_snapshot_id": job.financial_snapshot_id or "",
        "model_id": model_id,
        "external_research_enabled": external_research_enabled,
        "audit_dir": audit_dir,
    }


def build_manifest(plan: PS.ReportPlan, job: PS.ReportJobInput, *, run_id: str,
                   code_fingerprint: str, phase3_closure_fingerprint: str,
                   batch_versions: dict, frozen: dict,
                   now: str | None = None) -> SS.SectionRunManifest:
    """构造 RunManifest（身份内容寻址派生；历史 append-only）。"""
    manifest_id = SS.derive_manifest_id(job.job_id, run_id, code_fingerprint,
                                        phase3_closure_fingerprint,
                                        batch_versions, frozen)
    return SS.SectionRunManifest(
        manifest_id=manifest_id, job_id=job.job_id, run_id=run_id,
        code_fingerprint=code_fingerprint,
        phase3_closure_fingerprint=phase3_closure_fingerprint,
        batch_versions=dict(batch_versions), frozen=dict(frozen),
        created_at=now or _utcnow())


# ---------------------------------------------------------------------------
# 核心状态机（可离线测试）：评估 + 定向返工
# ---------------------------------------------------------------------------

def evaluate_section_and_rework(result: SS.SectionResult, task: PS.SectionTask, *,
                                company_id: str, renderer_version: str,
                                rules_version: str, citation_authority=None,
                                fact_pack=None, proposed_scheme=None,
                                llm_evaluator_generate=None,
                                worker_fn: Callable | None = None,
                                job_id: str = "") -> EvaluatedSection:
    """对单章节执行「Rules → LLM Evaluator（至多一次）→ 定向返工（至多一批）」状态机。

    - blocking → BLOCKED（不调用 LLM Evaluator）；
    - rework_targets → REWORK（不调用 LLM Evaluator）；
    - 仅规则通过 → 调用一次 LLM Evaluator（llm_evaluator_calls=1）；
    - REWORK → 定向返工（worker_fn 注入），返工后只做确定性最终检查（无二次 LLM）。
    """
    rules_verdict = RE.evaluate_section(result, task, company_id=company_id,
                                        citation_authority=citation_authority,
                                        fact_pack=fact_pack,
                                        proposed_scheme=proposed_scheme)

    llm_eval = None
    llm_calls = 0
    if rules_verdict.blocking:
        decision = "BLOCKED"
    elif rules_verdict.rework_targets:
        decision = "REWORK"
    else:
        llm_eval = LE.evaluate(result, task, rules_verdict=rules_verdict,
                               llm_generate=llm_evaluator_generate)
        llm_calls = 1
        decision = LE.final_decision(rules_verdict, llm_eval)

    issues = tuple(rules_verdict.issues) + (llm_eval.issues if llm_eval is not None else ())
    if rules_verdict.rework_targets:
        raw_targets = tuple(rules_verdict.rework_targets)
    elif llm_eval is not None:
        raw_targets = tuple(llm_eval.rework_targets)
    else:
        raw_targets = ()
    # 汇总边界规范化：同一 claim 多条引用可能产生相同 (target_kind, target_ref, reason)
    # → 相同 target_id。稳定去重（保留首次顺序），issues 全量保留不经此折叠。
    # 去重后 target 同时流入 evaluation_id / SectionEvaluation / run_rework / commit_evaluation，
    # 保证 section_rework.rework_id 不撞主键。
    rework_targets, _duplicate_target_count = SS.canonicalize_rework_targets(raw_targets)

    evaluator_prompt_version = LE.PROMPT_VERSION
    evaluation_id = SS.derive_evaluation_id(
        result.section_result_id, decision, rules_version, evaluator_prompt_version,
        issues, rework_targets, llm_calls)
    evaluation = SS.SectionEvaluation(
        evaluation_id=evaluation_id, section_result_id=result.section_result_id,
        rules_version=rules_version, evaluator_prompt_version=evaluator_prompt_version,
        rules_passed=rules_verdict.rules_passed,
        llm_passed=(llm_eval.passed if llm_eval is not None else None),
        decision=decision, issues=issues, rework_targets=rework_targets,
        evaluated_at=_utcnow(), llm_evaluator_calls=llm_calls)

    final_result = result
    rework_run = None
    final_rules_passed = None
    if decision == "REWORK":
        def _final_check(m: SS.SectionResult) -> RE.RulesVerdict:
            return RE.evaluate_section(m, task, company_id=company_id,
                                       citation_authority=citation_authority,
                                       fact_pack=fact_pack,
                                       proposed_scheme=proposed_scheme)
        rr = RW.run_rework(result, task, rework_targets, evaluation=evaluation,
                           worker_fn=worker_fn, renderer_version=renderer_version,
                           rules_version=rules_version, job_id=job_id,
                           llm_evaluator_calls=llm_calls, final_check_fn=_final_check)
        final_result = rr.section_result
        rework_run = rr.rework_run
        final_rules_passed = rr.final_check_passed

    return EvaluatedSection(
        section_result=final_result, evaluation=evaluation, rework_run=rework_run,
        final_rules_passed=final_rules_passed, llm_evaluator_calls=llm_calls)


# ---------------------------------------------------------------------------
# 依赖库准备（只读校验 + 指向，不建库不迁移）
# ---------------------------------------------------------------------------

def _prepare_stores(cfg: ServiceConfig) -> None:
    """只读校验证据/财务库存在，并把 store 模块 _db_path 指向既有库（不 init_db 不迁移）。

    研究 Worker 的 build_route_context 经模块级 _db_path 读库；缺失/不可用 fail-closed。
    """
    from evidence import store as estore
    from financial_v2 import store as fstore

    if not Path(cfg.fin_db).is_file():
        raise FileNotFoundError(f"financial_v2 库不存在（fail-closed）: {cfg.fin_db}")
    if not Path(cfg.ev_db).is_file():
        raise FileNotFoundError(f"evidence 库不存在（fail-closed）: {cfg.ev_db}")
    fstore._db_path = Path(cfg.fin_db)
    estore._db_path = Path(cfg.ev_db)


# ---------------------------------------------------------------------------
# Worker 分派（章节 → result + fact_pack + rework worker_fn）
# ---------------------------------------------------------------------------

def _run_worker(task: PS.SectionTask, job: PS.ReportJobInput, cfg: ServiceConfig,
                run_id: str, model_id: str, llm_generate, audit_llm_extract,
                budget) -> tuple[SS.SectionResult, object, Callable, str, str]:
    """执行单个章节 Worker，返回 (result, fact_pack, worker_fn, renderer_version, rules_version)。"""
    section_id = task.section_id

    if section_id == "financial":
        wr = FW.run_task(task, company_id=job.company_id, company_name=job.company_name,
                         snapshot_id=job.financial_snapshot_id, fin_db=cfg.fin_db,
                         scope=cfg.scope, currency=cfg.currency, purpose=cfg.purpose,
                         as_of_date=job.report_as_of, llm_generate=llm_generate)
        result = wr.section_result
        fact_pack = FW.build_fact_pack_for_task(
            task, company_id=job.company_id, company_name=job.company_name,
            snapshot_id=job.financial_snapshot_id, fin_db=cfg.fin_db,
            scope=cfg.scope, currency=cfg.currency, purpose=cfg.purpose,
            as_of_date=job.report_as_of)
        result = _apply_audit_enrichment(result, task, job, cfg, audit_llm_extract,
                                         snapshot_period=fact_pack.as_of_date)
        worker_fn = partial(FW.run_task, company_id=job.company_id,
                            company_name=job.company_name,
                            snapshot_id=job.financial_snapshot_id, fin_db=cfg.fin_db,
                            scope=cfg.scope, currency=cfg.currency, purpose=cfg.purpose,
                            as_of_date=job.report_as_of, llm_generate=llm_generate)
        return result, fact_pack, worker_fn, SC.RENDERER_VERSION, SC.RULES_VERSION

    if section_id == "company":
        worker = CW.run_task
        renderer_version, rules_version = CW.RENDERER_VERSION, CW.RULES_VERSION
    elif section_id == "industry":
        worker = IW.run_task
        renderer_version, rules_version = IW.RENDERER_VERSION, IW.RULES_VERSION
    else:
        raise ServiceError(f"未知章节（第一阶段仅 company/financial/industry）: {section_id!r}")

    kwargs = dict(company_id=job.company_id, company_name=job.company_name, run_id=run_id,
                  scope=cfg.scope, currency=cfg.currency, purpose=cfg.purpose,
                  as_of_date=job.report_as_of, model=model_id,
                  external_research_enabled=cfg.external_research_enabled,
                  budget=budget, audit_dir=cfg.audit_dir, external_db=cfg.ext_db,
                  harness_db=cfg.harness_db, checkpoint=cfg.checkpoint,
                  evidence_db=cfg.ev_db, financial_db=cfg.fin_db)
    wr = worker(task, **kwargs)
    worker_fn = partial(worker, **kwargs)
    return wr.section_result, None, worker_fn, renderer_version, rules_version


def _apply_audit_enrichment(result: SS.SectionResult, task: PS.SectionTask,
                            job: PS.ReportJobInput, cfg: ServiceConfig,
                            audit_llm_extract, snapshot_period: str | None) -> SS.SectionResult:
    """财务章节：审计意见 enrichment（多 Evidence 派生文本事实，不写入快照）。"""
    if AO.find_audit_question(task) is None:
        return result
    blocks = AO.gather_candidate_blocks(job.company_id, ev_db=cfg.ev_db)
    llm_extract = audit_llm_extract
    if llm_extract is None:
        llm_extract = partial(AO.default_llm_extract, snapshot_scope=cfg.scope,
                              snapshot_period=snapshot_period)
    enrichment = AO.extract(job.company_id, evidence_blocks=blocks, llm_extract=llm_extract)
    claim, unresolved = AO.build_audit_claim(task, enrichment, section_id="financial")
    return AO.enrich_section_result(result, task, claim=claim, unresolved=unresolved,
                                    renderer_version=SC.RENDERER_VERSION,
                                    rules_version=SC.RULES_VERSION)


# ---------------------------------------------------------------------------
# 单章节编排
# ---------------------------------------------------------------------------

def _run_section(task: PS.SectionTask, job: PS.ReportJobInput, cfg: ServiceConfig,
                 run_id: str, citation_authority, model_id: str, llm_generate,
                 llm_evaluator_generate, audit_llm_extract, budget) -> SectionOutcome:
    section_id = task.section_id
    try:
        result, fact_pack, worker_fn, renderer_version, rules_version = _run_worker(
            task, job, cfg, run_id, model_id, llm_generate, audit_llm_extract, budget)
        sstore.commit_section_result(result, run_id=run_id)
        ev = evaluate_section_and_rework(
            result, task, company_id=job.company_id, renderer_version=renderer_version,
            rules_version=rules_version, citation_authority=citation_authority,
            fact_pack=fact_pack, proposed_scheme=job.proposed_scheme,
            llm_evaluator_generate=llm_evaluator_generate, worker_fn=worker_fn,
            job_id=job.job_id)
        sstore.commit_evaluation(ev.evaluation, run_id=run_id)
        if ev.rework_run is not None:
            sstore.commit_section_result(ev.section_result, run_id=run_id)
            sstore.commit_rework_run(ev.rework_run, run_id=run_id)
        return SectionOutcome(
            section_id=section_id, task_id=task.task_id, title=task.title,
            section_result=ev.section_result, evaluation=ev.evaluation,
            rework_run=ev.rework_run, final_rules_passed=ev.final_rules_passed)
    except Exception as e:  # noqa: BLE001 - 单章失败不阻断其余章节，记录并继续
        logger.exception("章节 %s 编排失败（fail-closed）", section_id)
        return SectionOutcome(
            section_id=section_id, task_id=task.task_id, title=task.title,
            section_result=None, evaluation=None, rework_run=None,
            final_rules_passed=None, error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 服务入口
# ---------------------------------------------------------------------------

def run_phase4(job: PS.ReportJobInput, *, service_cfg: ServiceConfig | None = None,
               run_id: str = "", llm_generate=None, llm_evaluator_generate=None,
               audit_llm_extract=None, budget=None) -> Phase4RunResult:
    """执行一次 Phase 4 报告章节生成（规划 → 三章节 → 评估/返工 → manifest）。

    注入缝：llm_generate（财务 Worker）/ llm_evaluator_generate（LLM Evaluator）/
    audit_llm_extract（审计字段抽取）均可用 fake 离线替换真实 LLM。
    """
    cfg = service_cfg or ServiceConfig()
    if not run_id:
        run_id = f"run_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"

    from config import LLM_MODEL
    model_id = cfg.model or LLM_MODEL

    # 1) 规划（确定性，无 I/O）
    contracts = load_contracts(cfg.contracts_path)
    contract_fp = planner.contract_file_fingerprint(cfg.contracts_path)
    plan = planner.plan(job, contracts, contract_fp)

    # 2) Store init + commit_plan
    sstore.init_db(cfg.section_db)
    sstore.commit_plan(plan, run_id=run_id)

    # 3) 依赖库指向（研究 Worker 的 build_route_context 经 _db_path 只读）
    _prepare_stores(cfg)

    # 4) RouteContext + citation_authority（一次，供规则评估复用）
    from routing import context as rctx
    context = rctx.build_route_context(job.company_id, scope=cfg.scope, currency=cfg.currency,
                                       as_of_date=job.report_as_of, purpose=cfg.purpose,
                                       external_research_enabled=cfg.external_research_enabled)
    citation_authority = CA.build_citation_authority(job.company_id, context,
                                                     ev_db=cfg.ev_db, fin_db=cfg.fin_db,
                                                     ext_db=cfg.ext_db)

    # 5) 逐章节
    outcomes: list[SectionOutcome] = []
    for task in plan.section_tasks:
        outcomes.append(_run_section(task, job, cfg, run_id, citation_authority,
                                     model_id, llm_generate, llm_evaluator_generate,
                                     audit_llm_extract, budget))

    # 6) manifest（append-only，current_manifest 指针独立切换）
    batch_versions = build_batch_versions()
    frozen = build_frozen(job, plan, model_id=model_id, scope=cfg.scope, currency=cfg.currency,
                          purpose=cfg.purpose,
                          external_research_enabled=cfg.external_research_enabled,
                          audit_dir=cfg.audit_dir)
    manifest = build_manifest(
        plan, job, run_id=run_id, code_fingerprint=compute_code_fingerprint(),
        phase3_closure_fingerprint=compute_phase3_closure_fingerprint(),
        batch_versions=batch_versions, frozen=frozen)
    sstore.commit_manifest(manifest)

    success = all(o.error is None
                  and o.section_result is not None
                  and o.section_result.status not in ("SECTION_BLOCKED", "FAILED")
                  for o in outcomes)
    return Phase4RunResult(
        job_id=job.job_id, run_id=run_id, plan_id=plan.plan_id,
        manifest_id=manifest.manifest_id, manifest=manifest,
        sections=tuple(outcomes), success=success)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m sections.service",
        description="Phase 4 服务入口（三章节编排 + 评估/返工 + RunManifest）")
    p.add_argument("--company", default=None, dest="company_id")
    p.add_argument("--company-name", default="")
    p.add_argument("--credit-type", default=None, choices=CS.CREDIT_TYPES)
    p.add_argument("--report-as-of", default=None, dest="report_as_of")
    p.add_argument("--template-id", default="standard_v2")
    p.add_argument("--enabled-sections", default="company,financial,industry")
    p.add_argument("--contracts", default="templates/contracts/standard_v2.yaml")
    p.add_argument("--fin-db", default="data/financial_v2.db")
    p.add_argument("--ev-db", default="data/evidence.db")
    p.add_argument("--ext-db", default="data/external_sources.db")
    p.add_argument("--harness-db", default="data/harness.db")
    p.add_argument("--section-db", default="data/sections.db")
    p.add_argument("--scope", default="consolidated")
    p.add_argument("--currency", default="CNY")
    p.add_argument("--purpose", default="credit_analysis")
    p.add_argument("--model", default=None)
    p.add_argument("--no-external", action="store_true", dest="no_external",
                   help="关闭外部检索")
    p.add_argument("--validate-only", action="store_true",
                   help="只规划 + 指纹/版本/冻结预演，不执行 Worker、不调 LLM")
    p.add_argument("--self-check", action="store_true",
                   help="纯函数离线自检（不读库、不调 LLM）")
    p.add_argument("--out", default=None, help="JSON 结果输出路径（可选）")
    return p


def _self_check() -> dict:
    out: dict = {}

    # 1) 指纹确定性（两次一致）。
    fp1 = compute_code_fingerprint()
    fp2 = compute_code_fingerprint()
    p3_1 = compute_phase3_closure_fingerprint()
    p3_2 = compute_phase3_closure_fingerprint()
    out["code_fingerprint_deterministic"] = bool(fp1) and fp1 == fp2
    out["phase3_closure_deterministic"] = bool(p3_1) and p3_1 == p3_2

    # 2) batch_versions 关键键齐备。
    bv = build_batch_versions()
    out["batch_versions_shape"] = (
        bv.get("service") == SERVICE_VERSION
        and bv.get("rules_evaluator") == RE.RULES_VERSION
        and bv.get("financial", {}).get("renderer") == SC.RENDERER_VERSION
        and bv.get("company", {}).get("renderer") == CW.RENDERER_VERSION
        and bv.get("industry", {}).get("renderer") == IW.RENDERER_VERSION)

    # 3) manifest 身份自洽（与 derive_manifest_id 一致）。
    job = PS.ReportJobInput(job_id="j_sc", company_id="C", company_name="测试",
                            credit_type="other", report_as_of="2026-03-31",
                            enabled_sections=("company", "financial", "industry"))
    plan = planner.plan(job, load_contracts("templates/contracts/standard_v2.yaml"), "fp")
    frozen = build_frozen(job, plan, model_id="m", scope="consolidated", currency="CNY",
                          purpose="credit_analysis", external_research_enabled=True,
                          audit_dir=None)
    manifest = build_manifest(plan, job, run_id="r1", code_fingerprint="cf",
                              phase3_closure_fingerprint="p3", batch_versions=bv,
                              frozen=frozen)
    out["manifest_identity"] = manifest.manifest_id == SS.derive_manifest_id(
        job.job_id, "r1", "cf", "p3", bv, frozen)

    # 4) preview DTO：None 安全 + 往返。
    err = SectionOutcome(section_id="financial", task_id="t", title="财务",
                         section_result=None, evaluation=None, rework_run=None,
                         final_rules_passed=None, error="boom")
    pv = outcome_preview(err)
    out["preview_error_safe"] = pv.status == "FAILED" and pv.error == "boom"
    out["preview_roundtrip"] = preview_from_dict(preview_to_dict(pv)) == pv

    # 5) 状态机：规则阻断 → BLOCKED，LLM 不调用（llm_evaluator_calls=0）。
    def _task():
        return PS.SectionTask(
            task_id="t_sc", plan_id="p_sc", section_id="financial", title="财务",
            purpose="p", research_policy="workflow", topic_ids=("t1",),
            questions=(PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                                          topic_id="t1", blocking_policy=(), impact_scope=()),),
            evaluation_rule_ids=(), allowed_capabilities=(), output_requirements=(),
            blocking_rules=(), dependency_versions={})

    u_conflict = SS.SectionUnresolved(
        unresolved_id="ur_c", section_id="financial", topic_id="t1", question_id="q1",
        state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")
    res_conflict = SS.SectionResult(
        section_result_id="sr_c", section_version="sv_c", task_id="t_sc",
        section_id="financial", status="SECTION_BLOCKED", claims=(), unresolved=(u_conflict,),
        markdown="# 财务")

    called: list[int] = []

    def llm_spy(messages, system):  # noqa: ARG001
        called.append(1)
        return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})

    ev = evaluate_section_and_rework(res_conflict, _task(), company_id="C",
                                     renderer_version="r", rules_version="r",
                                     llm_evaluator_generate=llm_spy)
    out["blocking_no_llm"] = ev.evaluation.decision == "BLOCKED" and not called
    out["blocking_llm_calls_zero"] = ev.llm_evaluator_calls == 0

    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _build_cli_parser().parse_args(argv)

    if args.self_check:
        result = _self_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\nself-check:", "PASS" if all(result.values()) else "FAIL")
        return 0 if all(result.values()) else 1

    if args.validate_only:
        enabled = tuple(s.strip() for s in args.enabled_sections.split(",") if s.strip())
        job_id = f"job_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
        try:
            contracts = load_contracts(args.contracts)
            cfp = planner.contract_file_fingerprint(args.contracts)
            job = planner.build_job_input(
                job_id, args.company_id, args.company_name, args.credit_type,
                args.report_as_of, args.template_id, enabled,
                scope=args.scope, currency=args.currency, purpose=args.purpose,
                fin_db=args.fin_db, ev_db=args.ev_db)
            plan = planner.plan(job, contracts, cfp)
        except Exception as e:  # noqa: BLE001
            logger.exception("规划失败")
            print(f"规划失败: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        summary = {
            "plan_id": plan.plan_id,
            "job_id": job.job_id,
            "code_fingerprint": compute_code_fingerprint(),
            "phase3_closure_fingerprint": compute_phase3_closure_fingerprint(),
            "batch_versions": build_batch_versions(),
            "frozen": build_frozen(job, plan, model_id=args.model or "deepseek-v4-pro",
                                   scope=args.scope, currency=args.currency,
                                   purpose=args.purpose,
                                   external_research_enabled=not args.no_external,
                                   audit_dir=None),
            "tasks": [{"section_id": t.section_id, "task_id": t.task_id,
                       "question_count": len(t.questions)} for t in plan.section_tasks],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if not args.company_id or not args.credit_type or not args.report_as_of:
        _build_cli_parser().print_help()
        return 1

    enabled = tuple(s.strip() for s in args.enabled_sections.split(",") if s.strip())
    job_id = f"job_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
    cfg = ServiceConfig(
        contracts_path=args.contracts, fin_db=args.fin_db, ev_db=args.ev_db,
        ext_db=args.ext_db, harness_db=args.harness_db, section_db=args.section_db,
        scope=args.scope, currency=args.currency, purpose=args.purpose,
        model=args.model, external_research_enabled=(False if args.no_external else None))

    try:
        job = planner.build_job_input(
            job_id, args.company_id, args.company_name, args.credit_type,
            args.report_as_of, args.template_id, enabled,
            scope=args.scope, currency=args.currency, purpose=args.purpose,
            fin_db=args.fin_db, ev_db=args.ev_db)
        result = run_phase4(job, service_cfg=cfg)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("Phase 4 服务失败")
        print(f"Phase 4 服务失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    payload = phase4_result_to_dict(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n结果已写入: {args.out}")
    return 0 if result.success else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
