"""Phase 4 只读产物加载器专项评测（sections/artifact_loader.py）。

纯离线：不调 LLM、不联网、不写 Store、不重跑报告。构造合成产物目录（镜像
`scripts.run_phase4_demo.write_products` 的落盘形状），覆盖 13 项：

1. 能列出完整 run；2. 能加载全部产物；3. 还原后三章 status/decision/count 与原 JSON
一致；4. 只读加载不调用 run_phase4；5. 不调用 LLM/网络/Retriever/init_db；6. 加载前后
hash 不变；7. 缺文件 fail-closed；8. 非法 JSON fail-closed；9. run_id/manifest 不一致
fail-closed；10. 路径穿越拒绝；11. 不完整旧失败 run 不进入默认可加载列表；12. Streamlit
加载模式不触发现场生成；13. 现有现场生成入口不回归。

用法: python -m evals.test_section_artifact_loader
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from sections import artifact_loader as AL  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import service as SV  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


JOB_ID = "job_test1"
PLAN_ID = "plan_" + "a" * 24
MANIFEST_ID = "manifest_" + "b" * 24
RUN_ID = "run_test001"

_SECTIONS = (
    ("company", "公司信用研究", "task_company", "WAITING_HUMAN", "BLOCKED", False),
    ("financial", "财务分析", "task_financial", "COMPLETED_WITH_GAPS", "PASS_WITH_GAPS", False),
    ("industry", "行业研究", "task_industry", "COMPLETED_WITH_GAPS", "REWORK", True),
)


def _make_claim(section_id: str) -> SS.SectionClaim:
    return SS.SectionClaim(
        claim_id=f"claim_{section_id}_0", section_id=section_id, topic_id="t1",
        question_ids=("q1",), text="测试断言文本", claim_type="fact",
        citation_refs=(
            CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=3),
            CitationRef(ref_type="structured", snapshot_id="snap_1",
                        item_code="TOTAL_ASSETS", period="2025-12-31"),
            CitationRef(ref_type="external", source_snapshot_id="ext_1"),
        ),
    )


def _make_unresolved(section_id: str) -> SS.SectionUnresolved:
    return SS.SectionUnresolved(
        unresolved_id=f"ur_{section_id}", section_id=section_id, topic_id="t1",
        question_id="q1", state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")


def _make_issue() -> SS.SectionIssue:
    return SS.SectionIssue(issue_id="iss_1", rule_id="r1", severity="yellow",
                           location="l1", detail="数值存疑")


def _section_payload(section_id: str, title: str, task_id: str, status: str,
                     decision: str, rework: bool) -> dict:
    sr = SS.SectionResult(
        section_result_id=f"sr_{section_id}", section_version=f"sv_{section_id}",
        task_id=task_id, section_id=section_id, status=status,
        claims=(_make_claim(section_id),), unresolved=(_make_unresolved(section_id),),
        markdown=f"# {title}\n\n正文")
    ev = SS.SectionEvaluation(
        evaluation_id=f"eval_{section_id}", section_result_id=f"sr_{section_id}",
        rules_version="rv1", evaluator_prompt_version="pv1", rules_passed=True,
        llm_passed=True, decision=decision, issues=(_make_issue(),), llm_evaluator_calls=1)
    rr = None
    if rework:
        rr = SS.SectionReworkRun(
            rework_run_id=f"rr_{section_id}", job_id=JOB_ID,
            section_result_id=f"sr_{section_id}", from_section_result_id=f"sr_{section_id}_old",
            evaluation_id=f"eval_{section_id}", batch_no=0, llm_evaluator_calls=0)
    return {
        "section_id": section_id, "title": title,
        "section_result": SS.section_result_to_dict(sr),
        "evaluation": SS.evaluation_to_dict(ev),
        "rework_run": SS.rework_run_to_dict(rr) if rr else None,
        "final_rules_passed": (True if rework else None),
    }


def _write_run(root: Path, run_id: str = RUN_ID, *, manifest_run_id: str | None = None,
               manifest_manifest_id: str = MANIFEST_ID,
               trace_manifest_id: str | None = None) -> Path:
    d = root / f"phase4_demo_{run_id}"
    d.mkdir(parents=True, exist_ok=True)

    manifest = SS.SectionRunManifest(
        manifest_id=manifest_manifest_id, job_id=JOB_ID,
        run_id=(manifest_run_id or run_id), code_fingerprint="c" * 64,
        phase3_closure_fingerprint="p" * 64,
        batch_versions={"service": "p4-service-v1",
                        "financial": {"worker": "p4-fin-worker-v2", "prompt": "section_financial_v3"}},
        frozen={"company_id": "300750", "company_name": "宁德时代", "report_as_of": "2026-03-31",
                "financial_snapshot_id": "snap_1", "evidence_inventory_fingerprint": "eif_1",
                "model_id": "deepseek-v4-pro"},
        created_at="2026-09-11T00:00:00Z")
    (d / "run_manifest.json").write_text(
        json.dumps(SS.manifest_to_dict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")

    plan = {"plan_id": PLAN_ID, "job_id": JOB_ID, "company_id": "300750",
            "company_name": "宁德时代", "credit_type": "other", "report_as_of": "2026-03-31",
            "template_id": "standard_v2", "input_fingerprint": "ifp", "contract_fingerprint": "cfp",
            "planner_version": "p4-planner-v1", "created_at": "2026-09-11T00:00:00Z",
            "section_tasks": []}
    (d / "report_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    trace_sections = []
    eval_map = {}
    for sid, title, task_id, status, decision, rework in _SECTIONS:
        payload = _section_payload(sid, title, task_id, status, decision, rework)
        sub = d / sid
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "section_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (sub / "section.md").write_text(payload["section_result"]["markdown"] + "\n",
                                        encoding="utf-8")
        eval_map[sid] = payload["evaluation"]
        trace_sections.append({
            "section_id": sid, "title": title, "task_id": task_id, "status": status,
            "decision": decision, "llm_evaluator_calls": 1, "claim_count": 1,
            "unresolved_count": 1, "rework_attempted": rework,
            "final_check_passed": (True if rework else None), "issue_count": 1, "error": None,
        })
    (d / "evaluation.json").write_text(json.dumps(eval_map, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    trace = {"run_id": run_id, "job_id": JOB_ID, "plan_id": PLAN_ID,
             "manifest_id": (trace_manifest_id or manifest_manifest_id), "success": True,
             "model_id": "deepseek-v4-pro", "external_research_enabled": None,
             "sections": trace_sections}
    (d / "trace_summary.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _dir_hashes(d: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _loader_import_roots() -> set[str]:
    src = Path(__file__).resolve().parent.parent / "sections" / "artifact_loader.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def main():
    # ── 1. 列出完整 run ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_run(root)
        runs = AL.list_phase4_runs(root)
        check(len(runs) == 1, "list 返回恰好一个 run")
        check(runs[0].complete and runs[0].run_id == RUN_ID, "完整 run 标记 complete")
        check(runs[0].company_name == "宁德时代" and runs[0].company_id == "300750",
              "summary 含公司信息")
        check(runs[0].section_ids == ("company", "financial", "industry"),
              "summary 含三章节 id")

        # ── 2. 加载全部产物 ──
        res = AL.load_phase4_run(RUN_ID, root=root)
        check(isinstance(res, SV.Phase4RunResult), "load 返回 Phase4RunResult")
        check(res.manifest.manifest_id == MANIFEST_ID and res.plan_id == PLAN_ID,
              "manifest_id / plan_id 还原")
        check(res.success is True and len(res.sections) == 3, "success + 三章节还原")
        check(res.manifest.batch_versions["financial"]["worker"] == "p4-fin-worker-v2",
              "batch_versions 还原（worker 版本）")

        # ── 3. 还原后三章 status/decision/count 与原 JSON 一致 ──
        trace = json.loads((root / f"phase4_demo_{RUN_ID}" / "trace_summary.json")
                           .read_text(encoding="utf-8"))
        for ts, outcome in zip(trace["sections"], res.sections):
            pv = SV.outcome_preview(outcome)
            check(pv.status == ts["status"], f"{ts['section_id']} status 一致")
            check(pv.decision == ts["decision"], f"{ts['section_id']} decision 一致")
            check(pv.claim_count == ts["claim_count"], f"{ts['section_id']} claim_count 一致")
            check(pv.unresolved_count == ts["unresolved_count"],
                  f"{ts['section_id']} unresolved_count 一致")
            check(pv.issue_count == ts["issue_count"], f"{ts['section_id']} issue_count 一致")
            check(pv.rework_attempted == ts["rework_attempted"],
                  f"{ts['section_id']} rework_attempted 一致")
            check(pv.final_check_passed == ts["final_check_passed"],
                  f"{ts['section_id']} final_check_passed 一致")
        ind = next(o for o in res.sections if o.section_id == "industry")
        check(ind.rework_run is not None and ind.final_rules_passed is True,
              "industry rework_run 还原")

        # ── 4. 只读加载不调用 run_phase4 ──
        def _boom_run_phase4(*a, **k):
            raise AssertionError("run_phase4 不应被调用")
        orig_rp = SV.run_phase4
        SV.run_phase4 = _boom_run_phase4
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(True, "只读加载不调用 run_phase4")
        finally:
            SV.run_phase4 = orig_rp

        # ── 5. 不调用 LLM / 网络 / Retriever / init_db ──
        roots = _loader_import_roots()
        banned = {"llm", "retrieval", "harness", "routing", "tools", "evidence",
                  "external", "external_v2", "financial_v2"}
        check(not (roots & banned),
              f"loader 不 import 业务包（命中 {sorted(roots & banned)}）")
        check(roots <= {"sections", "__future__", "argparse", "hashlib", "json",
                        "logging", "re", "sys", "dataclasses", "pathlib"},
              "loader 仅 import stdlib + sections")
        import sections.store as sstore
        def _boom_init_db(*a, **k):
            raise AssertionError("init_db 不应被调用")
        orig_init = sstore.init_db
        sstore.init_db = _boom_init_db
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(True, "只读加载不调用 init_db")
        finally:
            sstore.init_db = orig_init

        # ── 6. 加载前后产物 hash 不变 ──
        d = root / f"phase4_demo_{RUN_ID}"
        before = _dir_hashes(d)
        AL.load_phase4_run(RUN_ID, root=root)
        after = _dir_hashes(d)
        check(before == after, "加载前后所有产物 hash 不变")

    # ── 7. 缺文件 fail-closed ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        (d / "financial" / "section_result.json").unlink()
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "缺文件应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "缺文件 fail-closed")

    # ── 8. 非法 JSON fail-closed ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        (d / "run_manifest.json").write_text("{not json", encoding="utf-8")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "非法 JSON 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "非法 JSON fail-closed")

    # ── 9. run_id / manifest 不一致 fail-closed ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_run(root, manifest_run_id="run_OTHER")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "run_id/manifest 不一致应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "run_id/manifest 不一致 fail-closed")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_run(root, trace_manifest_id="manifest_" + "c" * 24)
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "manifest_id 与 trace 不一致应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "manifest_id 与 trace 不一致 fail-closed")

    # ── 10. 路径穿越拒绝 ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for bad in ("../etc/passwd", "..", "run_x/../../y", "run_..", "a/b",
                    "run_x\\..\\..", "/abs/path"):
            try:
                AL.load_phase4_run(bad, root=root)
                check(False, f"路径穿越应拒绝: {bad!r}")
            except AL.ArtifactLoaderError:
                check(True, f"路径穿越拒绝: {bad!r}")

    # ── 11. 不完整旧失败 run 不进入默认可加载列表 ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_run(root)
        inc = root / "phase4_demo_run_incomplete"
        inc.mkdir()
        (inc / "run_manifest.json").write_text(
            json.dumps({"manifest_id": "manifest_" + "d" * 24, "job_id": "j",
                        "run_id": "run_incomplete"}), encoding="utf-8")
        runs = AL.list_phase4_runs(root)
        by_id = {r.run_id: r for r in runs}
        check(not by_id["run_incomplete"].complete, "旧失败 run 标记不完整")
        check([r.run_id for r in runs if r.complete] == [RUN_ID],
              "默认可加载列表排除不完整 run")
        try:
            AL.load_phase4_run("run_incomplete", root=root)
            check(False, "不完整 run 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "不完整 run fail-closed")

    # ── 11b. 缺章节 evaluation：不 complete 且不 load ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        ep = d / "evaluation.json"
        em = json.loads(ep.read_text(encoding="utf-8"))
        del em["financial"]
        ep.write_text(json.dumps(em, ensure_ascii=False, indent=2), encoding="utf-8")
        runs = AL.list_phase4_runs(root)
        check(not runs[0].complete, "缺章节 evaluation → 不标记 complete")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "缺章节 evaluation 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "缺章节 evaluation fail-closed")

    # ── 11c. 非法 evaluation.json：不 complete 且不 load ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        (d / "evaluation.json").write_text("{not json", encoding="utf-8")
        runs = AL.list_phase4_runs(root)
        check(not runs[0].complete, "非法 evaluation.json → 不标记 complete")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "非法 evaluation.json 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "非法 evaluation.json fail-closed")

    # ── 11d. 空 sections：不 complete 且不 load ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        tp = d / "trace_summary.json"
        t = json.loads(tp.read_text(encoding="utf-8"))
        t["sections"] = []
        tp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        runs = AL.list_phase4_runs(root)
        check(not runs[0].complete, "空 sections → 不标记 complete")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "空 sections 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "空 sections fail-closed")

    # ── 11e. 重复 section_id：不 complete 且不 load ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _write_run(root)
        tp = d / "trace_summary.json"
        t = json.loads(tp.read_text(encoding="utf-8"))
        t["sections"] = [t["sections"][0], dict(t["sections"][0])]
        tp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        runs = AL.list_phase4_runs(root)
        check(not runs[0].complete, "重复 section_id → 不标记 complete")
        try:
            AL.load_phase4_run(RUN_ID, root=root)
            check(False, "重复 section_id 应 fail-closed")
        except AL.ArtifactLoaderError:
            check(True, "重复 section_id fail-closed")

    # ── 11f. 真实 run_20260911T_jsonfix 无损加载（结果目录未提交，缺失时 skip） ──
    real_root = Path(__file__).resolve().parent.parent / "evaluation" / "results"
    if (real_root / "phase4_demo_run_20260911T_jsonfix").is_dir():
        try:
            rr = AL.load_phase4_run("run_20260911T_jsonfix", root=real_root)
            check(isinstance(rr, SV.Phase4RunResult), "真实 run 加载为 Phase4RunResult")
            check(len(rr.sections) == 3 and rr.success is True, "真实 run 三章 + success")
            check(rr.manifest.batch_versions["financial"]["worker"] == "p4-fin-worker-v2",
                  "真实 run worker 版本无损还原")
        except Exception as e:  # noqa: BLE001
            check(False, f"真实 run 无损加载失败: {type(e).__name__}: {e}")
    else:
        _results["skipped"] += 1

    # ── 12/13. Streamlit 加载模式不触发现场生成；现场生成入口不回归 ──
    app = Path(__file__).resolve().parent.parent / "streamlit_app.py"
    src = app.read_text(encoding="utf-8")
    check("def _render_view_mode" in src, "存在 _render_view_mode（查看模式）")
    check("artifact_loader" in src and "load_phase4_run" in src,
          "查看模式引用 artifact_loader.load_phase4_run")
    tree = ast.parse(src)
    view_mode = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == "_render_view_mode")
    view_names = {n.id for n in ast.walk(view_mode) if isinstance(n, ast.Name)}
    check("run_phase4" not in view_names and "_generate_report" not in view_names,
          "_render_view_mode 不触发 run_phase4 / _generate_report")
    check("def _generate_report" in src and "run_phase4" in src
          and ".previews()" in src and "现场重新生成报告" in src,
          "现场生成入口（_generate_report / run_phase4 / previews）不回归")

    return _results


if __name__ == "__main__":
    import sys as _sys
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    _sys.exit(0 if r["failed"] == 0 else 1)
