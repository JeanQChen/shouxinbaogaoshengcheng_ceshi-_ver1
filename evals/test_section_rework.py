"""Phase 4 Batch D — 定向返工 runtime 专项评测（至多一批 + 确定性最终检查）。

纯离线：注入 fake worker_fn / final_check_fn，不读库、不联网、不调 LLM。
覆盖 target 解析（question/topic/claim）、受限任务身份保持、merge 身份保持、
rework_run 身份派生、端到端 run_rework、fail-closed 边界、确定性最终检查（无二次 LLM）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import rules_evaluator as RE  # noqa: E402
from sections import rework as RW  # noqa: E402
from sections import schema as SS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _task():
    return PS.SectionTask(
        task_id="task_rw", plan_id="plan_rw", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=("t1", "t2"),
        questions=(
            PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                               topic_id="t1", blocking_policy=(), impact_scope=()),
            PS.PlannedQuestion(question_id="q2", question="Q2", priority="P1",
                               topic_id="t2", blocking_policy=(), impact_scope=()),
        ),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(), dependency_versions={"financial_snapshot_id": "S1"},
    )


def _ref(item="TOTAL_ASSETS", period="2025-12-31"):
    return CitationRef(ref_type="structured", snapshot_id="S1", item_code=item, period=period)


def _claim(cid, topic_id, qids, text, ctype="fact", refs=()):
    return SS.SectionClaim(claim_id=cid, section_id="financial", topic_id=topic_id,
                           question_ids=qids, text=text, claim_type=ctype, citation_refs=refs)


def _parent():
    c1 = _claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),))
    c2 = _claim("c2", "t2", ("q2",), "整体财务稳健", "inference", ())
    return SS.SectionResult(
        section_result_id="sr_parent", section_version="secver_parent", task_id="task_rw",
        section_id="financial", status="COMPLETED", claims=(c1, c2),
        markdown="# 财务分析", dependency_fingerprint="fp1")


def _evaluation(decision="REWORK"):
    return SS.SectionEvaluation(
        evaluation_id="eval_rw", section_result_id="sr_parent", rules_version="p4-rules-v1",
        evaluator_prompt_version="section_evaluator_v1", rules_passed=False, llm_passed=False,
        decision=decision, rework_targets=(), llm_evaluator_calls=1)


def _tgt(kind, ref, reason):
    return SS.ReworkTarget(target_id=SS.derive_rework_target_id(kind, ref, reason),
                           target_kind=kind, target_ref=ref, reason=reason)


def _new_result(claims, unresolved=()):
    return SS.SectionResult(
        section_result_id="sr_new", section_version="secver_new", task_id="task_rw",
        section_id="financial", status="COMPLETED", claims=tuple(claims),
        unresolved=tuple(unresolved), markdown="# 财务分析")


def main():
    task = _task()
    parent = _parent()
    eval_ = _evaluation()

    # --- resolve_targets：三类 target ---
    qids, tids, ins = RW.resolve_targets(parent, task, (_tgt("question", "q1", "缺 aspect"),))
    check(qids == {"q1"} and tids == {"t1"} and ins.get("q1") == ["缺 aspect"],
          "question target 解析")

    qids, tids, ins = RW.resolve_targets(parent, task, (_tgt("topic", "t2", "补 topic"),))
    check(qids == {"q2"} and tids == {"t2"} and ins.get("q2") == ["补 topic"],
          "topic target 解析")

    qids, tids, ins = RW.resolve_targets(parent, task, (_tgt("claim", "c1", "value mismatch"),))
    check(qids == {"q1"} and tids == {"t1"} and ins.get("q1") == ["value mismatch"],
          "claim target 解析")

    # ghost target 被跳过
    qids, tids, _ = RW.resolve_targets(parent, task, (_tgt("question", "q_ghost", "x"),))
    check(qids == set() and tids == set(), "ghost target 被跳过（不解析）")

    # --- restrict_task：身份保持 + 指令注入 ---
    restricted = RW.restrict_task(task, {"q1"}, {"t1"}, ins)
    check(restricted.task_id == task.task_id and restricted.plan_id == task.plan_id,
          "受限任务保留 task_id / plan_id")
    check(restricted.dependency_versions == task.dependency_versions,
          "受限任务保留 dependency_versions（快照锁）")
    check(restricted.topic_ids == ("t1",) and [q.question_id for q in restricted.questions] == ["q1"],
          "受限任务只含 target topic/question")
    check("定向返工" in restricted.questions[0].question and "value mismatch" in restricted.questions[0].question,
          "返工指令注入 question 文本")

    # --- merge：身份保持 ---
    new_c1 = _claim("c1_new", "t1", ("q1",), "总资产为 1,234.56万元，同比上升", "fact", (_ref(),))
    merged_identity, replaced, untouched, new = RW.merge(
        parent, _new_result([new_c1]), target_question_ids={"q1"}, target_topic_ids={"t1"},
        renderer_version="p4-fin-renderer-v1", rules_version="p4-fin-rules-v1", task=task)
    check(replaced == 1 and untouched == 1 and new == 1,
          f"merge 计数（replaced={replaced}, untouched={untouched}, new={new}）")

    # 未触及 claim c2 完整身份字节一致
    kept = next(c for c in merged_identity.claims if c.claim_id == "c2")
    orig_c2 = next(c for c in parent.claims if c.claim_id == "c2")
    check(SS.claim_to_dict(kept) == SS.claim_to_dict(orig_c2),
          "未触及 claim c2 内容身份完整一致")
    # 触及 claim c1 被替换为 c1_new
    check(any(c.claim_id == "c1_new" for c in merged_identity.claims)
          and all(c.claim_id != "c1" for c in merged_identity.claims), "触及 claim c1 被替换")
    # 新 section_result_id 内容寻址自洽
    check(merged_identity.section_result_id != parent.section_result_id
          and merged_identity.section_result_id == SS.derive_section_result_id(merged_identity.section_version),
          "merge 派生新 section_version / result_id")

    # --- merge：unresolved 合并 ---
    u_q1 = SS.SectionUnresolved(unresolved_id="ur_q1", section_id="financial", topic_id="t1",
                                question_id="q1", state="NOT_FOUND_AFTER_SEARCH",
                                reason_code="write_not_found", detail="未检索到")
    u_q2 = SS.SectionUnresolved(unresolved_id="ur_q2", section_id="financial", topic_id="t2",
                                question_id="q2", state="NOT_FOUND_AFTER_SEARCH",
                                reason_code="write_not_found", detail="未检索到")
    parent_gaps = SS.SectionResult(
        section_result_id="sr_pg", section_version="secver_pg", task_id="task_rw",
        section_id="financial", status="COMPLETED_WITH_GAPS",
        claims=(_claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),)),
                _claim("c2", "t2", ("q2",), "稳健", "inference", ())),
        unresolved=(u_q1, u_q2), markdown="# 财务分析")
    new_uq1 = SS.SectionUnresolved(unresolved_id="ur_q1_new", section_id="financial", topic_id="t1",
                                   question_id="q1", state="CONFLICT", reason_code="conflict_pause",
                                   detail="口径冲突")
    merged, _, _, _ = RW.merge(
        parent_gaps, _new_result([_claim("c1_new", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (_ref(),))],
                                  unresolved=[new_uq1]),
        target_question_ids={"q1"}, target_topic_ids={"t1"},
        renderer_version="r", rules_version="r", task=task)
    ur_ids = {u.unresolved_id for u in merged.unresolved}
    check("ur_q2" in ur_ids and "ur_q1_new" in ur_ids and "ur_q1" not in ur_ids,
          "未触及 unresolved 保留，触及 unresolved 替换")

    # --- build_rework_run：batch_no=0 + 身份派生 ---
    rr = RW.build_rework_run(parent, merged_identity, eval_, job_id="job1", llm_evaluator_calls=1,
                             targets=(_tgt("question", "q1", "缺 aspect"),))
    check(rr.batch_no == 0 and rr.from_section_result_id == "sr_parent"
          and rr.section_result_id == merged_identity.section_result_id
          and rr.rework_run_id == SS.derive_rework_run_id("sr_parent", merged_identity.section_result_id, 0),
          "rework_run batch_no=0 且身份内容寻址")

    # --- run_rework 端到端 ---
    def fake_worker(rt):
        return type("W", (), {"section_result": _new_result([new_c1])})()

    res = RW.run_rework(parent, task, (_tgt("question", "q1", "缺 aspect"),),
                        evaluation=eval_, worker_fn=fake_worker,
                        renderer_version="p4-fin-renderer-v1", rules_version="p4-fin-rules-v1",
                        job_id="job1")
    check(res.section_result.section_result_id == merged_identity.section_result_id
          and res.rework_run.batch_no == 0 and res.replaced_claim_count == 1,
          "run_rework 端到端产物一致")
    check(res.rework_run.llm_evaluator_calls == 1,
          "rework_run 携带 llm_evaluator_calls=1（无二次 LLM Evaluator）")

    # 确定性最终检查：默认 rules evaluator 返回 RulesVerdict（非 None，无 LLM）
    check(isinstance(res.final_rules, RE.RulesVerdict), "默认最终检查返回 RulesVerdict（无 LLM）")

    # 注入 final_check_fn 被使用
    def fake_final(r):
        return RE.RulesVerdict(rules_passed=True, blocking=False, issues=(),
                               rework_targets=(), summary={"injected": True})
    res2 = RW.run_rework(parent, task, (_tgt("question", "q1", "缺 aspect"),),
                         evaluation=eval_, worker_fn=fake_worker,
                         renderer_version="r", rules_version="r",
                         final_check_fn=fake_final)
    check(res2.final_rules.summary.get("injected") is True and res2.final_check_passed,
          "注入 final_check_fn 被使用")

    # --- fail-closed 边界 ---
    try:
        RW.run_rework(parent, task, (), evaluation=eval_, worker_fn=fake_worker,
                      renderer_version="r", rules_version="r")
        check(False, "空 rework_targets 应 fail-closed")
    except RW.ReworkError:
        check(True, "空 rework_targets fail-closed")

    try:
        RW.run_rework(parent, task, (_tgt("question", "q_ghost", "x"),),
                      evaluation=eval_, worker_fn=fake_worker,
                      renderer_version="r", rules_version="r")
        check(False, "ghost target 应 fail-closed")
    except RW.ReworkError:
        check(True, "ghost target fail-closed")

    try:
        RW.run_rework(parent, task, (_tgt("question", "q1", "x"),),
                      evaluation=_evaluation("PASS"), worker_fn=fake_worker,
                      renderer_version="r", rules_version="r")
        check(False, "非 REWORK 决策应 fail-closed")
    except RW.ReworkError:
        check(True, "非 REWORK 决策 fail-closed")

    def worker_none(rt):
        return None
    try:
        RW.run_rework(parent, task, (_tgt("question", "q1", "x"),),
                      evaluation=eval_, worker_fn=worker_none,
                      renderer_version="r", rules_version="r")
        check(False, "worker 无产物应 fail-closed")
    except RW.ReworkError:
        check(True, "worker 无产物 fail-closed")

    # --- 多 target 联合（topic + question）---
    qids, tids, _ = RW.resolve_targets(
        parent, task, (_tgt("topic", "t1", "r1"), _tgt("question", "q2", "r2")))
    check(qids == {"q1", "q2"} and tids == {"t1", "t2"}, "多 target 联合解析")

    # --- 父结果不可变（frozen dataclass，merge 不改变父 claim 集合）---
    check(len(parent.claims) == 2 and {c.claim_id for c in parent.claims} == {"c1", "c2"},
          "父结果 claims 未被修改")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
