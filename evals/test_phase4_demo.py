"""Phase 4 Batch D — 真实 Demo 验收入口专项评测（任务书 §20 / §24，纯离线）。

覆盖：
1. ``scripts.run_phase4_demo.write_products`` 完整产物落盘布局（§20 的 11 个文件：
   run_manifest / report_plan / 三章节 section_result.json + section.md / evaluation /
   trace_summary / human_review），JSON 可解析且关键键齐备。
2. 无 300750 / 宁德时代 硬编码分支（约束 #13）：剥离模块 docstring 后源码不含这两串。
3. 缺失库 fail-closed：``run_demo`` 在 financial_v2/evidence 库不存在时抛错（不静默降级）。
4. ``--validate-only``：有真实库时只读规划成功返回 0 并出 3 任务；缺库时返回 1（不崩溃）。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_phase4_demo as RD  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_CONTRACTS = "templates/contracts/standard_v2.yaml"


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _make_plan():
    from contracts.loader import load_contracts
    from planning import report_planner as planner
    from planning import schema as PS

    contracts = load_contracts(_CONTRACTS)
    fp = planner.contract_file_fingerprint(_CONTRACTS)
    job = PS.ReportJobInput(
        job_id="job_test", company_id="SYNTH", company_name="测试公司",
        credit_type="other", report_as_of="2026-03-31",
        enabled_sections=("company", "financial", "industry"))
    return planner.plan(job, contracts, fp), job


def _make_result(plan, job):
    from sections import schema as SS
    from sections import service as SV

    outcomes = []
    for task in plan.section_tasks:
        claim = SS.SectionClaim(
            claim_id=f"c_{task.section_id}", section_id=task.section_id, topic_id="t1",
            question_ids=(task.questions[0].question_id,),
            text="总资产为 1,234.56 万元", claim_type="fact", citation_refs=())
        sr = SS.SectionResult(
            section_result_id=f"sr_{task.section_id}", section_version=f"sv_{task.section_id}",
            task_id=task.task_id, section_id=task.section_id, status="COMPLETED",
            claims=(claim,), markdown=f"# {task.title}\n\n正文段落")
        ev = SS.SectionEvaluation(
            evaluation_id=f"e_{task.section_id}", section_result_id=sr.section_result_id,
            rules_version="rv", evaluator_prompt_version="epv", rules_passed=True,
            llm_passed=True, decision="PASS", llm_evaluator_calls=1)
        outcomes.append(SV.SectionOutcome(
            section_id=task.section_id, task_id=task.task_id, title=task.title,
            section_result=sr, evaluation=ev, rework_run=None, final_rules_passed=None))

    manifest = SV.build_manifest(
        plan, job, run_id="run_test", code_fingerprint="cf",
        phase3_closure_fingerprint="p3", batch_versions=SV.build_batch_versions(),
        frozen=SV.build_frozen(job, plan, model_id="m", scope="consolidated",
                               currency="CNY", purpose="credit_analysis",
                               external_research_enabled=False, audit_dir=None))
    return SV.Phase4RunResult(
        job_id=job.job_id, run_id="run_test", plan_id=plan.plan_id,
        manifest_id=manifest.manifest_id, manifest=manifest,
        sections=tuple(outcomes), success=True)


def _check_write_products() -> None:
    plan, job = _make_plan()
    result = _make_result(plan, job)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "demo"
        meta = RD.write_products(result, plan, str(out))

        expected = ["run_manifest.json", "report_plan.json", "evaluation.json",
                    "trace_summary.json", "human_review.md"]
        for sid in ("company", "financial", "industry"):
            expected += [f"{sid}/section_result.json", f"{sid}/section.md"]
        missing = [f for f in expected if not (out / f).is_file()]
        check(not missing, f"§20 产物 11 文件齐备（缺 {missing}）")
        check(set(meta["written"]) == set(expected), "write_products 返回清单与落盘一致")

        man = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
        check(set(man) >= {"manifest_id", "job_id", "run_id", "code_fingerprint",
                           "phase3_closure_fingerprint", "batch_versions", "frozen",
                           "created_at"}, "run_manifest.json 关键键齐备")

        plan_d = json.loads((out / "report_plan.json").read_text(encoding="utf-8"))
        check(set(plan_d) >= {"plan_id", "job_id", "company_id", "company_name",
                              "credit_type", "report_as_of", "template_id",
                              "input_fingerprint", "contract_fingerprint",
                              "planner_version", "section_tasks"},
              "report_plan.json 关键键齐备")
        check(len(plan_d["section_tasks"]) == 3, "report_plan 含 3 章节任务")

        for sid in ("company", "financial", "industry"):
            sr = json.loads((out / sid / "section_result.json").read_text(encoding="utf-8"))
            check(sr["section_id"] == sid, f"{sid}/section_result.json section_id 正确")
            check("claims" in sr["section_result"]
                  and "markdown" in sr["section_result"],
                  f"{sid} section_result 含 claims/markdown")
            check(sr["evaluation"]["decision"] == "PASS"
                  and sr["evaluation"]["llm_evaluator_calls"] == 1,
                  f"{sid} evaluation 含 decision/llm_evaluator_calls")
            md = (out / sid / "section.md").read_text(encoding="utf-8")
            check(md.startswith("#"), f"{sid}/section.md 非空 Markdown")

        ev = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
        check(set(ev) == {"company", "financial", "industry"},
              "evaluation.json 覆盖三章节")

        tr = json.loads((out / "trace_summary.json").read_text(encoding="utf-8"))
        check(set(tr) >= {"run_id", "job_id", "plan_id", "manifest_id", "success",
                          "model_id", "external_research_enabled", "sections"},
              "trace_summary.json 关键键齐备")
        check(len(tr["sections"]) == 3 and all(s["decision"] == "PASS" for s in tr["sections"]),
              "trace_summary 三章节且 decision=PASS")

        hr = (out / "human_review.md").read_text(encoding="utf-8")
        check("人工验收清单" in hr and "未生成授信额度" in hr,
              "human_review.md 含 12 项验收清单")


def _check_no_hardcoded_company() -> None:
    src = Path(RD.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    doc_node = tree.body[0] if tree.body and isinstance(tree.body[0], ast.Expr) else None
    doc_start = doc_node.lineno if doc_node is not None else 0
    doc_end = (doc_node.end_lineno if (doc_node is not None and doc_node.end_lineno)
               else doc_start)
    bad = [i for i, line in enumerate(lines, start=1)
           if ("300750" in line or "宁德时代" in line)
           and not (doc_start <= i <= doc_end)]
    check(not bad, f"300750/宁德时代 仅限模块 docstring（违规行 {bad}）")


def _check_fail_closed_missing_db() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        try:
            RD.run_demo(
                company_id="X", company_name="X公司", credit_type="other",
                report_as_of="2026-03-31", contracts_path=_CONTRACTS,
                fin_db=str(base / "fin.db"), ev_db=str(base / "ev.db"),
                ext_db=str(base / "ext.db"), harness_db=str(base / "harness.db"),
                section_db=str(base / "section.db"))
            check(False, "缺失库 run_demo 应 fail-closed 抛错")
        except Exception as e:  # noqa: BLE001 - 任何库缺失/损坏均应抛错
            check(not isinstance(e, SystemExit), f"缺失库 fail-closed 抛错（{type(e).__name__}）")


def _check_validate_only() -> None:
    fin = Path("data/financial_v2.db")
    ev = Path("data/evidence.db")
    if not (fin.is_file() and ev.is_file()):
        _results["skipped"] += 1
        _results["details"].append("SKIP: 真实库不存在，跳过 --validate-only 成功路径")
    else:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = RD._main([
                "--validate-only", "--company", "X", "--company-name", "X公司",
                "--credit-type", "other", "--report-as-of", "2026-03-31",
                "--contracts", _CONTRACTS])
        check(rc == 0, "--validate-only 有库时返回 0")
        out = json.loads(buf.getvalue())
        check(set(out) >= {"plan_id", "job_id", "company_id",
                           "evidence_inventory_fingerprint", "financial_snapshot_id",
                           "tasks"} and len(out["tasks"]) == 3,
              "--validate-only 输出 plan_id/job_id/三任务")

    # 缺库：返回 1 不崩溃。
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = RD._main([
                "--validate-only", "--company", "X", "--company-name", "X公司",
                "--credit-type", "other", "--report-as-of", "2026-03-31",
                "--contracts", _CONTRACTS,
                "--fin-db", str(base / "fin.db"), "--ev-db", str(base / "ev.db")])
        check(rc == 1, "--validate-only 缺库时返回 1（fail-closed，不崩溃）")


def main():
    _check_write_products()
    _check_no_hardcoded_company()
    _check_fail_closed_missing_db()
    _check_validate_only()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
