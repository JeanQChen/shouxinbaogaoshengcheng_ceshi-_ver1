"""Phase 4 Batch D — 真实 300750 Demo 验收入口（任务书 §20 / §24）。

把「规划 → 三章节 Worker → 评估/返工 → RunManifest」一次性跑通，并把**完整产物**
（非扁平 Preview DTO）落盘到 `evaluation/results/phase4_demo_<run_id>/`：

    run_manifest.json / report_plan.json
    company|financial|industry/{section_result.json, section.md}
    evaluation.json / trace_summary.json / human_review.md

纯业务粘合层（任务书 §15 / 约束 #12）：只调 `planning.report_planner` + `sections.service`，
不写业务逻辑、不硬编码 300750 / 宁德时代，公司 / 库路径 / 口径全部 CLI 传入。真实 LLM 由
`sections.service.run_phase4` 默认注入（DeepSeek-V4-Pro），也可经注入缝离线替换以便测试。

CLI:
  python -m scripts.run_phase4_demo \
    --company 300750 --company-name 宁德时代 --credit-type other \
    --report-as-of 2026-03-31 --contracts templates/contracts/standard_v2.yaml \
    [--no-external] [--run-id <id>] [--out-dir <dir>] [--validate-only]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import report_planner as planner  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import service as SV  # noqa: E402

logger = logging.getLogger("scripts.run_phase4_demo")

_DEFAULT_OUT_ROOT = Path("evaluation/results")
_SECTION_IDS = ("company", "financial", "industry")


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 完整产物序列化（结构化 SectionResult + Markdown，非扁平 Preview）
# ---------------------------------------------------------------------------

def _report_plan_payload(plan: PS.ReportPlan) -> dict:
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
        "section_tasks": [PS.section_task_to_dict(t) for t in plan.section_tasks],
    }


def _section_payload(outcome: SV.SectionOutcome) -> dict:
    """单章节完整产物：结构化 SectionResult + Evaluation + ReworkRun（None 安全）。"""
    r = outcome.section_result
    if r is None:
        return {"section_id": outcome.section_id, "title": outcome.title, "error": outcome.error}
    return {
        "section_id": r.section_id,
        "title": outcome.title,
        "section_result": SS.section_result_to_dict(r),
        "evaluation": (SS.evaluation_to_dict(outcome.evaluation)
                       if outcome.evaluation is not None else None),
        "rework_run": (SS.rework_run_to_dict(outcome.rework_run)
                       if outcome.rework_run is not None else None),
        "final_rules_passed": outcome.final_rules_passed,
    }


def _trace_summary(result: SV.Phase4RunResult) -> dict:
    """章节级可回放摘要（状态/decision/claim/unresolved/issue/llm_evaluator_calls）。"""
    sections = []
    for o in result.sections:
        pv = SV.outcome_preview(o)
        sections.append({
            "section_id": o.section_id,
            "title": o.title,
            "task_id": o.task_id,
            "status": pv.status,
            "decision": pv.decision,
            "llm_evaluator_calls": pv.llm_evaluator_calls,
            "claim_count": pv.claim_count,
            "unresolved_count": pv.unresolved_count,
            "rework_attempted": pv.rework_attempted,
            "final_check_passed": pv.final_check_passed,
            "issue_count": pv.issue_count,
            "error": pv.error,
        })
    return {
        "run_id": result.run_id,
        "job_id": result.job_id,
        "plan_id": result.plan_id,
        "manifest_id": result.manifest_id,
        "success": result.success,
        "model_id": result.manifest.frozen.get("model_id"),
        "external_research_enabled": result.manifest.frozen.get("external_research_enabled"),
        "sections": sections,
    }


def _human_review_markdown(result: SV.Phase4RunResult) -> str:
    """§20 人工验收清单（预填各章节计数，供人工逐项勾选）。"""
    frozen = result.manifest.frozen
    lines = [
        "# Phase 4 真实 Demo 人工验收清单", "",
        f"- company_id: `{frozen.get('company_id', '')}`",
        f"- company_name: `{frozen.get('company_name', '')}`",
        f"- run_id: `{result.run_id}`",
        f"- manifest_id: `{result.manifest_id}`",
        f"- plan_id: `{result.plan_id}`", "",
        "| 章节 | 状态 | decision | claims | unresolved | issues | llm_evaluator_calls | rework |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for o in result.sections:
        pv = SV.outcome_preview(o)
        lines.append(
            f"| {o.section_id} | {pv.status} | {pv.decision} | {pv.claim_count} "
            f"| {pv.unresolved_count} | {pv.issue_count} | {pv.llm_evaluator_calls} "
            f"| {'是' if pv.rework_attempted else '否'} |")
    lines += [
        "",
        "## 逐项检查（§20）", "",
        "1. [ ] 三章结构像授信报告而不是 41 问答案拼接。",
        "2. [ ] 引用能支撑对应句子（可展开查看来源与定位）。",
        "3. [ ] 无来源数字为 0。",
        "4. [ ] 财务抽样数值、期间、单位与 Snapshot 一致。",
        "5. [ ] 「未找到」没有被写成「不存在」。",
        "6. [ ] proxy、缺失、过期和冲突如实表达。",
        "7. [ ] 分析判断能回指事实，而非泛泛表扬。",
        "8. [ ] 行业结论落到公司收入、成本、资本开支或现金流。",
        "9. [ ] Evaluator issue 具体、可定位、可执行。",
        "10. [ ] 返工没有重做无关章节。",
        "11. [ ] 页面可以在合理等待时间内展示已完成真实产物。",
        "12. [ ] 未生成授信额度、评级或增信建议。",
    ]
    return "\n".join(lines)


def write_products(result: SV.Phase4RunResult, plan: PS.ReportPlan, out_dir: str) -> dict:
    """把完整产物写入 out_dir（幂等覆盖；返回写入清单）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    (out / "run_manifest.json").write_text(
        json.dumps(SS.manifest_to_dict(result.manifest), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    written.append("run_manifest.json")
    (out / "report_plan.json").write_text(
        json.dumps(_report_plan_payload(plan), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    written.append("report_plan.json")

    for o in result.sections:
        sid = o.section_id
        sub = out / sid
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "section_result.json").write_text(
            json.dumps(_section_payload(o), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        written.append(f"{sid}/section_result.json")
        md = (o.section_result.markdown if o.section_result is not None else
              f"# {o.title}\n\n生成失败：{o.error}")
        (sub / "section.md").write_text(md + "\n", encoding="utf-8")
        written.append(f"{sid}/section.md")

    (out / "evaluation.json").write_text(
        json.dumps({o.section_id: _section_payload(o).get("evaluation")
                    for o in result.sections}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    written.append("evaluation.json")
    (out / "trace_summary.json").write_text(
        json.dumps(_trace_summary(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    written.append("trace_summary.json")
    (out / "human_review.md").write_text(_human_review_markdown(result) + "\n",
                                         encoding="utf-8")
    written.append("human_review.md")
    return {"out_dir": str(out), "written": written}


# ---------------------------------------------------------------------------
# 编排入口（可注入 fake LLM 以便离线测试）
# ---------------------------------------------------------------------------

def run_demo(*, company_id: str, company_name: str, credit_type: str, report_as_of: str,
             contracts_path: str, fin_db: str, ev_db: str, ext_db: str, harness_db: str,
             section_db: str, scope: str = "consolidated", currency: str = "CNY",
             purpose: str = "credit_analysis", template_id: str = "standard_v2",
             enabled_sections: tuple[str, ...] = _SECTION_IDS, no_external: bool = False,
             run_id: str = "", llm_generate=None, llm_evaluator_generate=None,
             audit_llm_extract=None) -> dict:
    """执行一次真实（或注入 fake）Phase 4 章节生成并落盘完整产物。"""
    from contracts.loader import load_contracts

    job_id = f"job_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
    contracts = load_contracts(contracts_path)
    contract_fp = planner.contract_file_fingerprint(contracts_path)
    job = planner.build_job_input(
        job_id, company_id, company_name, credit_type, report_as_of, template_id,
        enabled_sections, scope=scope, currency=currency, purpose=purpose,
        fin_db=fin_db, ev_db=ev_db)
    plan = planner.plan(job, contracts, contract_fp)

    cfg = SV.ServiceConfig(
        contracts_path=contracts_path, fin_db=fin_db, ev_db=ev_db, ext_db=ext_db,
        harness_db=harness_db, section_db=section_db, scope=scope, currency=currency,
        purpose=purpose, external_research_enabled=(False if no_external else None))

    result = SV.run_phase4(job, service_cfg=cfg, run_id=run_id, llm_generate=llm_generate,
                           llm_evaluator_generate=llm_evaluator_generate,
                           audit_llm_extract=audit_llm_extract)

    out_dir = str(_DEFAULT_OUT_ROOT / f"phase4_demo_{result.run_id}")
    products = write_products(result, plan, out_dir)
    return {"run_id": result.run_id, "job_id": job_id, "plan_id": plan.plan_id,
            "manifest_id": result.manifest_id, "success": result.success,
            "out_dir": out_dir, "products": products["written"],
            "trace": _trace_summary(result)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_phase4_demo",
        description="Phase 4 真实 Demo 验收（规划 + 三章节 + 评估/返工 + 完整产物落盘）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--company-name", required=True, dest="company_name")
    parser.add_argument("--credit-type", required=True)
    parser.add_argument("--report-as-of", required=True, dest="report_as_of")
    parser.add_argument("--contracts", required=True)
    parser.add_argument("--template-id", default="standard_v2")
    parser.add_argument("--enabled-sections", default="company,financial,industry")
    parser.add_argument("--fin-db", default="data/financial_v2.db")
    parser.add_argument("--ev-db", default="data/evidence.db")
    parser.add_argument("--ext-db", default="data/external_sources.db")
    parser.add_argument("--harness-db", default="data/harness.db")
    parser.add_argument("--section-db", default="data/sections.db")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--no-external", action="store_true",
                        help="关闭外部检索（离线/降级）")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--validate-only", action="store_true",
                        help="只规划 + 解析指纹/快照（只读，不调 LLM、不写 Store、不落盘）")
    args = parser.parse_args(argv)

    enabled = tuple(s.strip() for s in args.enabled_sections.split(",") if s.strip())

    if args.validate_only:
        from contracts.loader import load_contracts
        try:
            job_id = f"job_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
            contracts = load_contracts(args.contracts)
            contract_fp = planner.contract_file_fingerprint(args.contracts)
            job = planner.build_job_input(
                job_id, args.company_id, args.company_name, args.credit_type,
                args.report_as_of, args.template_id, enabled, scope=args.scope,
                currency=args.currency, purpose=args.purpose,
                fin_db=args.fin_db, ev_db=args.ev_db)
            plan = planner.plan(job, contracts, contract_fp)
        except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
            logger.exception("规划失败")
            print(f"规划失败: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        print(json.dumps({
            "plan_id": plan.plan_id,
            "job_id": job.job_id,
            "company_id": job.company_id,
            "evidence_inventory_fingerprint": job.evidence_inventory_fingerprint,
            "financial_snapshot_id": job.financial_snapshot_id,
            "tasks": [{"section_id": t.section_id, "question_count": len(t.questions)}
                      for t in plan.section_tasks],
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        summary = run_demo(
            company_id=args.company_id, company_name=args.company_name,
            credit_type=args.credit_type, report_as_of=args.report_as_of,
            contracts_path=args.contracts, fin_db=args.fin_db, ev_db=args.ev_db,
            ext_db=args.ext_db, harness_db=args.harness_db, section_db=args.section_db,
            scope=args.scope, currency=args.currency, purpose=args.purpose,
            template_id=args.template_id, enabled_sections=enabled,
            no_external=args.no_external, run_id=args.run_id)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("Phase 4 Demo 失败")
        print(f"Phase 4 Demo 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))
