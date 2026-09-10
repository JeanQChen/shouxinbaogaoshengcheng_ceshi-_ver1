"""Phase 4 Batch D — Phase 4 服务入口 + RunManifest + Preview DTO 专项评测。

纯离线：注入 fake LLM Evaluator / fake worker_fn，不读真实库、不联网、不调真实 LLM。
覆盖：代码/Phase3 关闭指纹确定性、batch_versions / frozen / manifest 身份、评估+返工
状态机（Rules 阻断/返工优先、LLM Evaluator 至多一次、返工后只确定性最终检查）、
Preview DTO 折叠与往返、run_phase4 fail-closed（缺库不建库不跑 Worker）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import service as SV  # noqa: E402
from sections import schema as SS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _ref(item="TOTAL_ASSETS", period="2025-12-31"):
    return CitationRef(ref_type="structured", snapshot_id="S1", item_code=item, period=period)


def _claim(cid, topic_id, qids, text, ctype="fact", refs=()):
    return SS.SectionClaim(claim_id=cid, section_id="financial", topic_id=topic_id,
                           question_ids=qids, text=text, claim_type=ctype, citation_refs=refs)


def _task(questions, topic_ids):
    return PS.SectionTask(
        task_id="task_svc", plan_id="plan_svc", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=tuple(topic_ids), questions=tuple(questions),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(),
        dependency_versions={"financial_snapshot_id": "S1"})


def _q(qid, topic_id, blocking=()):
    return PS.PlannedQuestion(question_id=qid, question=f"Q-{qid}", priority="P0",
                              topic_id=topic_id, blocking_policy=blocking, impact_scope=())


def _result(task, claims, unresolved=(), status="COMPLETED"):
    return SS.SectionResult(
        section_result_id="sr_svc", section_version="secver_svc", task_id=task.task_id,
        section_id="financial", status=status, claims=tuple(claims),
        unresolved=tuple(unresolved), markdown="# 财务分析")


def main():
    # ------------------------------------------------------------------
    # 1) 代码指纹 / Phase 3 关闭指纹：确定性 + 非空 + 两者不同
    # ------------------------------------------------------------------
    fp1 = SV.compute_code_fingerprint()
    fp2 = SV.compute_code_fingerprint()
    p3_1 = SV.compute_phase3_closure_fingerprint()
    p3_2 = SV.compute_phase3_closure_fingerprint()
    check(bool(fp1) and fp1 == fp2, "代码指纹确定性（两次一致）")
    check(bool(p3_1) and p3_1 == p3_2, "Phase3 关闭指纹确定性（两次一致）")
    check(fp1 != p3_1, "代码指纹与 Phase3 关闭指纹独立")

    # ------------------------------------------------------------------
    # 2) batch_versions 关键键齐备（各层版本常量进入冻结清单）
    # ------------------------------------------------------------------
    bv = SV.build_batch_versions()
    check(bv.get("service") == SV.SERVICE_VERSION, "batch_versions 含 service 版本")
    check(bv.get("rules_evaluator") and bv.get("llm_evaluator") and bv.get("rework")
          and bv.get("audit"), "batch_versions 含 evaluator/rework/audit 版本")
    check(set(bv.get("financial", {})) >= {"renderer", "rules", "prompt", "worker"}
          and set(bv.get("company", {})) >= {"renderer", "rules", "prompt", "worker"}
          and set(bv.get("industry", {})) >= {"renderer", "rules", "prompt", "worker"},
          "batch_versions 三章节各含 renderer/rules/prompt/worker")

    # ------------------------------------------------------------------
    # 3) build_frozen / build_manifest：身份内容寻址 + 冻结输入完整
    # ------------------------------------------------------------------
    job = PS.ReportJobInput(job_id="job_svc", company_id="C", company_name="测试公司",
                            credit_type="other", report_as_of="2026-03-31",
                            enabled_sections=("company", "financial", "industry"),
                            evidence_inventory_fingerprint="ev_fp",
                            financial_snapshot_id="S1")
    from contracts.loader import load_contracts
    from planning import report_planner as planner
    contracts = load_contracts("templates/contracts/standard_v2.yaml")
    contract_fp = planner.contract_file_fingerprint("templates/contracts/standard_v2.yaml")
    plan = planner.plan(job, contracts, contract_fp)

    frozen = SV.build_frozen(job, plan, model_id="m", scope="consolidated", currency="CNY",
                             purpose="credit_analysis", external_research_enabled=True,
                             audit_dir=None)
    check(frozen.get("financial_snapshot_id") == "S1"
          and frozen.get("evidence_inventory_fingerprint") == "ev_fp",
          "frozen 含快照锁 + evidence 清单指纹")
    check(frozen.get("scope") == "consolidated" and frozen.get("currency") == "CNY"
          and frozen.get("purpose") == "credit_analysis",
          "frozen 含口径维度 scope/currency/purpose")
    check(frozen.get("model_id") == "m" and frozen.get("contract_fingerprint") == contract_fp,
          "frozen 含 model_id + contract 指纹")

    manifest = SV.build_manifest(plan, job, run_id="run1", code_fingerprint="cf",
                                 phase3_closure_fingerprint="p3", batch_versions=bv,
                                 frozen=frozen, now="2026-09-10T00:00:00Z")
    check(manifest.manifest_id == SS.derive_manifest_id(
        job.job_id, "run1", "cf", "p3", bv, frozen), "manifest_id 内容寻址自洽")
    check(manifest.run_id == "run1" and manifest.code_fingerprint == "cf"
          and manifest.phase3_closure_fingerprint == "p3"
          and manifest.batch_versions == dict(bv) and manifest.frozen == dict(frozen),
          "manifest 字段与输入一致（历史不可变，字段冻结）")

    # ------------------------------------------------------------------
    # 4) 状态机：Rules 阻断 → BLOCKED，LLM Evaluator 不调用（calls=0）
    # ------------------------------------------------------------------
    task1 = _task([_q("q1", "t1")], ["t1"])
    u_conflict = SS.SectionUnresolved(
        unresolved_id="ur_c", section_id="financial", topic_id="t1", question_id="q1",
        state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")
    res_block = _result(task1, [_claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),))],
                        unresolved=[u_conflict], status="SECTION_BLOCKED")

    llm_log: list = []

    def fake_llm(messages, system):  # noqa: ARG001
        llm_log.append(1)
        return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})

    ev = SV.evaluate_section_and_rework(res_block, task1, company_id="C",
                                        renderer_version="r", rules_version="r",
                                        llm_evaluator_generate=fake_llm)
    check(ev.evaluation.decision == "BLOCKED", "Rules 阻断 → BLOCKED")
    check(ev.llm_evaluator_calls == 0 and not llm_log,
          "Rules 阻断不调用 LLM Evaluator（calls=0）")

    # ------------------------------------------------------------------
    # 5) 状态机：Rules 返工目标 → REWORK，不调 LLM，返工后只确定性最终检查
    # ------------------------------------------------------------------
    task2 = _task([_q("q1", "t1"), _q("q2", "t2")], ["t1", "t2"])
    res_missing = _result(task2, [_claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),))])

    def worker_fill_q2(rt):
        new_c2 = _claim("c2_new", "t2", ("q2",), "整体财务稳健", "inference", ())
        return type("W", (), {"section_result": SS.SectionResult(
            section_result_id="sr_new", section_version="sv_new", task_id=rt.task_id,
            section_id="financial", status="COMPLETED", claims=(new_c2,),
            markdown="# 财务")})()

    llm_log.clear()
    ev2 = SV.evaluate_section_and_rework(res_missing, task2, company_id="C",
                                         renderer_version="r", rules_version="r",
                                         llm_evaluator_generate=fake_llm,
                                         worker_fn=worker_fill_q2)
    check(ev2.evaluation.decision == "REWORK", "Rules 返工目标 → REWORK")
    check(ev2.llm_evaluator_calls == 0 and not llm_log,
          "Rules 返工分支不调用 LLM Evaluator（calls=0）")
    check(ev2.rework_run is not None and ev2.rework_run.batch_no == 0,
          "返工产生 batch_no=0 的 rework_run")
    check(any(c.claim_id == "c1" for c in ev2.section_result.claims)
          and any(c.claim_id == "c2_new" for c in ev2.section_result.claims),
          "未触及 claim c1 身份保持，新增 c2_new")
    check(ev2.final_rules_passed is True,
          "返工后确定性最终检查通过（无二次 LLM Evaluator）")

    # ------------------------------------------------------------------
    # 6) 状态机：规则通过 → LLM Evaluator 一次（calls=1）
    # ------------------------------------------------------------------
    task1_ok = _task([_q("q1", "t1")], ["t1"])
    res_ok = _result(task1_ok, [_claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),))])
    llm_log.clear()
    ev3 = SV.evaluate_section_and_rework(res_ok, task1_ok, company_id="C",
                                         renderer_version="r", rules_version="r",
                                         llm_evaluator_generate=fake_llm)
    check(ev3.evaluation.decision == "PASS" and ev3.llm_evaluator_calls == 1,
          "规则通过 → LLM Evaluator 一次（calls=1）")
    check(len(llm_log) == 1, "LLM Evaluator 恰好调用一次")

    # ------------------------------------------------------------------
    # 7) 状态机：LLM 返回 REWORK → 返工 + 确定性最终检查，无二次 LLM
    # ------------------------------------------------------------------
    def fake_llm_rework(messages, system):  # noqa: ARG001
        llm_log.append(1)
        return json.dumps({"decision": "REWORK", "issues": [
            {"rule_id": "aspect_gap", "severity": "rework", "location": "q1", "detail": "补 aspect"}],
            "rework_targets": [{"target_kind": "question", "target_ref": "q1", "reason": "补 aspect"}]})

    def worker_replace_q1(rt):
        new_c1 = _claim("c1_new", "t1", ("q1",), "总资产为 1,234.56万元，同比增长", "fact", (_ref(),))
        return type("W", (), {"section_result": SS.SectionResult(
            section_result_id="sr_new", section_version="sv_new", task_id=rt.task_id,
            section_id="financial", status="COMPLETED", claims=(new_c1,),
            markdown="# 财务")})()

    llm_log.clear()
    ev4 = SV.evaluate_section_and_rework(res_ok, task1_ok, company_id="C",
                                         renderer_version="r", rules_version="r",
                                         llm_evaluator_generate=fake_llm_rework,
                                         worker_fn=worker_replace_q1)
    check(ev4.evaluation.decision == "REWORK" and ev4.llm_evaluator_calls == 1,
          "LLM REWORK → calls=1（至多一次）")
    check(len(llm_log) == 1, "返工后无二次 LLM Evaluator（累计一次）")
    check(ev4.rework_run is not None and any(c.claim_id == "c1_new" for c in ev4.section_result.claims),
          "LLM REWORK 触发返工，产出新 claim")

    # ------------------------------------------------------------------
    # 8) 状态机：LLM 返回 BLOCKED → BLOCKED（calls=1，不返工）
    # ------------------------------------------------------------------
    def fake_llm_blocked(messages, system):  # noqa: ARG001
        return json.dumps({"decision": "BLOCKED", "issues": [
            {"rule_id": "subject_error", "severity": "blocking", "location": "section",
             "detail": "主体错误"}], "rework_targets": []})

    ev5 = SV.evaluate_section_and_rework(res_ok, task1_ok, company_id="C",
                                         renderer_version="r", rules_version="r",
                                         llm_evaluator_generate=fake_llm_blocked)
    check(ev5.evaluation.decision == "BLOCKED" and ev5.llm_evaluator_calls == 1
          and ev5.rework_run is None, "LLM BLOCKED → BLOCKED，不返工")

    # ------------------------------------------------------------------
    # 9) Preview DTO：None 安全 + 字段折叠 + 往返
    # ------------------------------------------------------------------
    err_out = SV.SectionOutcome(section_id="financial", task_id="t", title="财务",
                                section_result=None, evaluation=None, rework_run=None,
                                final_rules_passed=None, error="boom")
    pv_err = SV.outcome_preview(err_out)
    check(pv_err.status == "FAILED" and pv_err.error == "boom"
          and pv_err.section_result_id == "" and pv_err.claim_count == 0,
          "Preview DTO 对 error 结果 None 安全")

    ok_out = SV.SectionOutcome(section_id="financial", task_id="t", title="财务",
                               section_result=ev3.section_result, evaluation=ev3.evaluation,
                               rework_run=ev3.rework_run, final_rules_passed=ev3.final_rules_passed)
    pv_ok = SV.outcome_preview(ok_out)
    check(pv_ok.status == ev3.section_result.status
          and pv_ok.decision == ev3.evaluation.decision
          and pv_ok.llm_evaluator_calls == 1
          and pv_ok.claim_count == 1 and pv_ok.rework_attempted is False,
          "Preview DTO 折叠评估/返工字段")

    check(SV.preview_from_dict(SV.preview_to_dict(pv_ok)) == pv_ok,
          "Preview DTO dict 往返一致")

    # ------------------------------------------------------------------
    # 10) phase4_result_to_dict：整体往返（含 manifest 序列化）
    # ------------------------------------------------------------------
    res_run = SV.Phase4RunResult(
        job_id="job_svc", run_id="run1", plan_id=plan.plan_id,
        manifest_id=manifest.manifest_id, manifest=manifest,
        sections=(ok_out, err_out), success=False)
    payload = SV.phase4_result_to_dict(res_run)
    check(set(payload) >= {"job_id", "run_id", "plan_id", "manifest_id", "manifest",
                           "sections", "success"}, "phase4_result_to_dict 顶层键齐备")
    check(payload["manifest"]["manifest_id"] == manifest.manifest_id
          and len(payload["sections"]) == 2,
          "phase4_result_to_dict manifest + sections 序列化正确")
    check(json.dumps(payload, ensure_ascii=False), "phase4_result_to_dict 可 JSON 序列化")

    # ------------------------------------------------------------------
    # 11) run_phase4 fail-closed：缺库（fin/ev）不建库、不跑 Worker、抛错
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        cfg = SV.ServiceConfig(
            section_db=str(Path(tmp) / "sections.db"),
            fin_db=str(Path(tmp) / "nope_fin.db"),
            ev_db=str(Path(tmp) / "nope_ev.db"),
        )
        try:
            SV.run_phase4(job, service_cfg=cfg, llm_generate=lambda m, s: "",
                          llm_evaluator_generate=lambda m, s: "")
            check(False, "缺库应 fail-closed 抛错")
        except FileNotFoundError:
            check(True, "缺库 fail-closed 抛 FileNotFoundError")
        # 不应创建 section 库之外的东西，也不该在缺库时跑 Worker（抛错前已停）
        check(not (Path(tmp) / "nope_fin.db").exists()
              and not (Path(tmp) / "nope_ev.db").exists(),
              "缺库不创建证据/财务库")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
