"""Phase 4 Batch D — 定向返工 runtime（至多一批，确定性最终检查，无二次 LLM Evaluator）。

任务书 §13.3：评估结论为 REWORK 时，对明确 rework_target 做**至多一批**定向返工。
硬约束（任务书三/实现时必须坚持）：
- **只改明确 target**：返工重跑仅覆盖 target 指向的 topic/question/claim 所属问题；
  未被点名的 Claim/topic 内容身份（claim_id）必须保持不变。
- **不二次 LLM Evaluator**：返工只重跑 Worker（Worker 自身的 LLM 调用不计入
  ``llm_evaluator_calls``），返工后**只做确定性最终检查**（Rules Evaluator，无 LLM）；
  每章 ``llm_evaluator_calls`` 恒 ∈ {0,1}（本模块不调用 LLM Evaluator）。
- **Evaluation 是 SectionResult 的关联对象**：返工产生**新** SectionResult（新
  section_version / section_result_id）+ SectionReworkRun（batch_no=0），不回写、不改变
  父 SectionResult 的不可变内容身份。

返工指令经**受限任务**注入：把 rework_target 的 reason 渲染成 ``section_repair`` 指令，
追加到 target question 的 ``question`` 文本里（question 文本会流入 Worker prompt；财务
Worker 经 ``<<TOPICS>>``、研究 Worker 经 ``build_need.question``）。不改 Worker 内部。

Worker 通过注入的 ``worker_fn(restricted_task) -> WorkerResult`` 复用 —— 财务传
``financial_worker.run_task`` 的部分应用，研究传 ``company/industry_worker.run_task`` 的
部分应用。rework runtime 不感知 Worker 内部，只做 resolve / restrict / merge / 身份派生 /
rework_run 记录 / 确定性最终检查。

CLI: python -m sections.rework --self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from llm import client as llm
from planning import schema as PS
from sections import rules_evaluator as RE
from sections import schema as SS
from sections import validator as svalidator

logger = logging.getLogger("sections.rework")

# 版本常量（变更必须递增；进入 rework_run 审计，不进入身份派生——身份由内容寻址）。
REWORK_VERSION = "p4-rework-v1"


class ReworkError(RuntimeError):
    """定向返工 fail-closed 错误（不产出新结果、不记录返工）。"""


@dataclass(frozen=True)
class ReworkResult:
    """一次定向返工批次的产物（不可变）。"""

    section_result: SS.SectionResult          # 返工后新 SectionResult（merged）
    rework_run: SS.SectionReworkRun           # 父结果 → 新结果的返工事实（batch_no=0）
    final_rules: RE.RulesVerdict | None       # 确定性最终检查结论（无 LLM）
    target_question_ids: tuple[str, ...]
    target_topic_ids: tuple[str, ...]
    replaced_claim_count: int                 # 因触及 target 而被替换的父 claim 数
    untouched_claim_count: int                # 保持身份不变的父 claim 数
    new_claim_count: int                      # 返工重跑产生的 claim 数
    final_check_passed: bool                  # 确定性最终检查是否通过（无 blocking/rework）


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 返工指令渲染（section_repair prompt；无 I/O 副作用，可注入）
# ---------------------------------------------------------------------------

def render_rework_instruction(reasons: list[str]) -> str:
    """把返工原因渲染成 section_repair 指令块（追加到 target question 文本）。"""
    joined = "\n".join(f"- {r}" for r in dict.fromkeys(r for r in reasons if r))
    template = llm.load_prompt("section_repair")
    return template.replace("<<REWORK_REASONS>>", joined)


# ---------------------------------------------------------------------------
# target 解析
# ---------------------------------------------------------------------------

def resolve_targets(parent_result: SS.SectionResult, task: PS.SectionTask,
                    rework_targets) -> tuple[set[str], set[str], dict[str, list[str]]]:
    """rework_targets → (target_qids, target_tids, question_instructions)。

    - ``question`` → 该 question（+ 其 topic）；
    - ``topic``    → 该 topic 下全部 question；
    - ``claim``    → 该 claim 所属的 question（+ 其 topic）。
    返回的 ``question_instructions`` 记录每个 target question 应注入的返工原因。
    """
    qmap = {q.question_id: q for q in task.questions}
    claim_by_id = {c.claim_id: c for c in parent_result.claims}
    target_qids: set[str] = set()
    target_tids: set[str] = set()
    instructions: dict[str, list[str]] = {}

    for t in rework_targets:
        reason = (t.reason or "").strip() or "未明确原因"
        if t.target_kind == "question":
            if t.target_ref not in qmap:
                continue
            target_qids.add(t.target_ref)
            target_tids.add(qmap[t.target_ref].topic_id)
            instructions.setdefault(t.target_ref, []).append(reason)
        elif t.target_kind == "topic":
            if t.target_ref not in task.topic_ids:
                continue
            target_tids.add(t.target_ref)
            for q in task.questions:
                if q.topic_id == t.target_ref:
                    target_qids.add(q.question_id)
                    instructions.setdefault(q.question_id, []).append(reason)
        elif t.target_kind == "claim":
            c = claim_by_id.get(t.target_ref)
            if c is None:
                continue
            target_tids.add(c.topic_id)
            for qid in c.question_ids:
                if qid in qmap:
                    target_qids.add(qid)
                    instructions.setdefault(qid, []).append(reason)

    return target_qids, target_tids, instructions


# ---------------------------------------------------------------------------
# 受限任务构造
# ---------------------------------------------------------------------------

def restrict_task(task: PS.SectionTask, target_question_ids: set[str],
                  target_topic_ids: set[str],
                  instructions: dict[str, list[str]] | None = None) -> PS.SectionTask:
    """构造只含 target topic/question 的受限任务。

    - 保留原 ``task_id`` / ``plan_id`` / ``dependency_versions``（身份与快照锁不变）；
    - target question 的 ``question`` 文本追加 section_repair 指令（流入 Worker prompt）。
    """
    instructions = instructions or {}
    questions: list[PS.PlannedQuestion] = []
    for q in task.questions:
        if q.question_id not in target_question_ids:
            continue
        if q.question_id in instructions:
            extra = render_rework_instruction(instructions[q.question_id])
            new_q = PS.PlannedQuestion(
                question_id=q.question_id, question=f"{q.question}\n{extra}",
                priority=q.priority, topic_id=q.topic_id,
                required_aspects=q.required_aspects,
                evidence_requirements=q.evidence_requirements,
                calculation_requirements=q.calculation_requirements,
                analysis_requirements=q.analysis_requirements,
                missing_policy=q.missing_policy, blocking_policy=q.blocking_policy,
                impact_scope=q.impact_scope)
        else:
            new_q = q
        questions.append(new_q)

    return PS.SectionTask(
        task_id=task.task_id, plan_id=task.plan_id, section_id=task.section_id,
        title=task.title, purpose=task.purpose, research_policy=task.research_policy,
        topic_ids=tuple(t for t in task.topic_ids if t in target_topic_ids),
        questions=tuple(questions),
        output_requirements=task.output_requirements,
        evaluation_rule_ids=task.evaluation_rule_ids,
        allowed_capabilities=task.allowed_capabilities,
        blocking_rules=task.blocking_rules,
        dependency_versions=task.dependency_versions)


# ---------------------------------------------------------------------------
# 合并（身份保持）
# ---------------------------------------------------------------------------

def _derive_status(unresolved: tuple[SS.SectionUnresolved, ...]) -> str:
    """合并后状态派生（研究 Worker 口径的严格超集；财务 Worker 不含 WAITING_HUMAN，但
    此处保留 WAITING_HUMAN 优先更诚实，且 Rules 规则 3 会兜底阻断）。"""
    if not unresolved:
        return "COMPLETED"
    if any(u.state == "WAITING_HUMAN" for u in unresolved):
        return "WAITING_HUMAN"
    for u in unresolved:
        if any(level in u.blocking_effects
               for level in ("SECTION_BLOCKED", "REPORT_BLOCKED", "JOB_BLOCKED")):
            return "SECTION_BLOCKED"
    return "COMPLETED_WITH_GAPS"


def _render_merged_markdown(task: PS.SectionTask,
                            claims: tuple[SS.SectionClaim, ...],
                            unresolved: tuple[SS.SectionUnresolved, ...]) -> str:
    """合并结果的通用 Markdown（确定性命中 claim/unresolved；最终报告由 service 层按
    对应 Worker renderer 重渲染，此处为返工中间产物兜底）。"""
    lines = [f"# {task.title or '章节'}", "", "## 返工后章节", ""]
    by_topic: dict[str, list[SS.SectionClaim]] = {}
    for c in claims:
        by_topic.setdefault(c.topic_id, []).append(c)
    for tid in task.topic_ids:
        if tid not in by_topic:
            continue
        lines.append(f"### {tid}")
        for c in by_topic[tid]:
            tag = "事实" if c.claim_type == "fact" else "研判"
            lines.append(f"- [{tag}] {c.text}")
        lines.append("")
    if unresolved:
        lines.append("## 未解决 / 未取得项")
        for u in unresolved:
            lines.append(f"- [{u.state}] {u.detail}")
        lines.append("")
    return "\n".join(lines)


def merge(parent_result: SS.SectionResult, new_result: SS.SectionResult, *,
          target_question_ids: set[str], target_topic_ids: set[str],
          renderer_version: str, rules_version: str,
          task: PS.SectionTask) -> tuple[SS.SectionResult, int, int, int]:
    """合并父结果与返工重跑结果 → 新 SectionResult。

    - 未触及 target 的父 claim 保持原身份（claim_id 不变）；
    - 触及 target 的父 claim 被丢弃，由 new_result 的 claim 取代；
    - unresolved 同理：未触及 target 的保留，触及 target 的由 new_result 取代；
    - 新 section_version / section_result_id 由合并后 claim/unresolved 内容寻址派生；
    - dependency_fingerprint 复用父结果（快照/公式版本/版本常量在本 cycle 内不变）。
    返回 (merged, replaced_count, untouched_count, new_count)。
    """
    target_qids = set(target_question_ids)

    untouched_claims = tuple(c for c in parent_result.claims
                             if not (set(c.question_ids) & target_qids))
    replaced_count = len(parent_result.claims) - len(untouched_claims)
    new_claims = tuple(new_result.claims)
    merged_claims = untouched_claims + new_claims

    untouched_unresolved = tuple(
        u for u in parent_result.unresolved
        if not ((u.question_id is not None and u.question_id in target_qids)
                or (u.question_id is None and u.topic_id in target_topic_ids)))
    merged_unresolved = untouched_unresolved + tuple(new_result.unresolved)

    section_version = SS.derive_section_version(
        parent_result.task_id, merged_claims, merged_unresolved,
        renderer_version=renderer_version, rules_version=rules_version,
        dependency_fingerprint=parent_result.dependency_fingerprint)
    section_result_id = SS.derive_section_result_id(section_version)

    merged = SS.SectionResult(
        section_result_id=section_result_id, section_version=section_version,
        task_id=parent_result.task_id, section_id=parent_result.section_id,
        status=_derive_status(merged_unresolved),
        claims=merged_claims, unresolved=merged_unresolved,
        markdown=_render_merged_markdown(task, merged_claims, merged_unresolved),
        evaluation=None,
        source_run_ids=parent_result.source_run_ids,
        source_question_ids=parent_result.source_question_ids,
        dependency_fingerprint=parent_result.dependency_fingerprint,
        created_at=_utcnow())

    return merged, replaced_count, len(untouched_claims), len(new_claims)


# ---------------------------------------------------------------------------
# rework_run 构造
# ---------------------------------------------------------------------------

def build_rework_run(parent_result: SS.SectionResult, merged_result: SS.SectionResult,
                     evaluation: SS.SectionEvaluation, *,
                     job_id: str = "", llm_evaluator_calls: int = 0,
                     targets) -> SS.SectionReworkRun:
    """构造 SectionReworkRun（batch_no=0，至多一批；身份内容寻址派生）。"""
    from_sid = parent_result.section_result_id
    to_sid = merged_result.section_result_id
    rework_run_id = SS.derive_rework_run_id(from_sid, to_sid, 0)
    return SS.SectionReworkRun(
        rework_run_id=rework_run_id, job_id=job_id, section_result_id=to_sid,
        from_section_result_id=from_sid, evaluation_id=evaluation.evaluation_id,
        batch_no=0, llm_evaluator_calls=llm_evaluator_calls,
        targets=tuple(targets), created_at=_utcnow())


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def run_rework(parent_result: SS.SectionResult, task: PS.SectionTask,
               rework_targets, *, evaluation: SS.SectionEvaluation,
               worker_fn: Callable[[PS.SectionTask], "object"],
               renderer_version: str, rules_version: str,
               job_id: str = "", llm_evaluator_calls: int | None = None,
               final_check_fn: Callable[[SS.SectionResult], RE.RulesVerdict] | None = None
               ) -> ReworkResult:
    """执行一次定向返工（至多一批）。

    - ``worker_fn(restricted_task) -> WorkerResult``：Worker 部分应用（含 .section_result）。
    - ``final_check_fn``：确定性最终检查（默认 Rules Evaluator，无 authority/fact_pack）；
      财务章节请注入带 fact_pack/citation_authority 的闭包以复用财务安全链。
    - ``llm_evaluator_calls``：本 cycle 累计 LLM Evaluator 调用次数（默认取 evaluation）。
    - 无有效 target / worker 无产物 → ReworkError（fail-closed，不产出、不记录）。
    """
    if evaluation.decision != "REWORK":
        raise ReworkError(f"返工仅在 REWORK 决策下执行，收到 {evaluation.decision!r}")
    if not rework_targets:
        raise ReworkError("无 rework_target，无法定向返工（fail-closed）")

    target_qids, target_tids, instructions = resolve_targets(
        parent_result, task, rework_targets)
    if not target_qids and not target_tids:
        raise ReworkError("rework_target 未能解析到任何有效 question/topic（fail-closed）")

    restricted = restrict_task(task, target_qids, target_tids, instructions)
    worker_result = worker_fn(restricted)
    if worker_result is None or getattr(worker_result, "section_result", None) is None:
        raise ReworkError("worker_fn 未返回有效 WorkerResult（fail-closed）")
    new_result = worker_result.section_result

    merged, replaced, untouched, new = merge(
        parent_result, new_result,
        target_question_ids=target_qids, target_topic_ids=target_tids,
        renderer_version=renderer_version, rules_version=rules_version, task=task)

    errors = svalidator.validate_section_result(merged)
    if errors:
        raise ReworkError("返工合并结果结构校验失败:\n"
                          + "\n".join(f"  - {e}" for e in errors))

    calls = llm_evaluator_calls if llm_evaluator_calls is not None \
        else evaluation.llm_evaluator_calls

    rework_run = build_rework_run(
        parent_result, merged, evaluation, job_id=job_id,
        llm_evaluator_calls=calls, targets=rework_targets)

    final_rules = None
    if final_check_fn is not None:
        final_rules = final_check_fn(merged)
    elif task is not None:
        final_rules = RE.evaluate_section(merged, task)

    return ReworkResult(
        section_result=merged, rework_run=rework_run, final_rules=final_rules,
        target_question_ids=tuple(sorted(target_qids)),
        target_topic_ids=tuple(sorted(target_tids)),
        replaced_claim_count=replaced, untouched_claim_count=untouched,
        new_claim_count=new,
        final_check_passed=(final_rules.rules_passed if final_rules is not None else False))


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，注入 fake worker_fn，不读库不联网不调 LLM）
# ---------------------------------------------------------------------------

def _task() -> PS.SectionTask:
    return PS.SectionTask(
        task_id="task_rw", plan_id="plan_rw", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=("t1", "t2"),
        questions=(
            PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                               topic_id="t1", required_aspects=(), blocking_policy=(),
                               impact_scope=()),
            PS.PlannedQuestion(question_id="q2", question="Q2", priority="P1",
                               topic_id="t2", blocking_policy=(), impact_scope=()),
        ),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(), dependency_versions={},
    )


def _parent_result() -> SS.SectionResult:
    ref = SS.CitationRef(ref_type="structured", snapshot_id="S1",
                         item_code="TOTAL_ASSETS", period="2025-12-31")
    c1 = SS.SectionClaim(claim_id="c1", section_id="financial", topic_id="t1",
                         question_ids=("q1",), text="总资产为 1,234.56万元", claim_type="fact",
                         citation_refs=(ref,))
    c2 = SS.SectionClaim(claim_id="c2", section_id="financial", topic_id="t2",
                         question_ids=("q2",), text="整体财务稳健", claim_type="inference",
                         citation_refs=())
    return SS.SectionResult(
        section_result_id="sr_parent", section_version="secver_parent",
        task_id="task_rw", section_id="financial", status="COMPLETED",
        claims=(c1, c2), markdown="# 财务分析")


def _evaluation() -> SS.SectionEvaluation:
    return SS.SectionEvaluation(
        evaluation_id="eval_rw", section_result_id="sr_parent",
        rules_version="p4-rules-v1", evaluator_prompt_version="section_evaluator_v1",
        rules_passed=False, llm_passed=False, decision="REWORK",
        rework_targets=(), llm_evaluator_calls=1)


def _self_check() -> dict:
    out: dict = {}

    task = _task()
    parent = _parent_result()
    eval_ = _evaluation()

    # 1) resolve_targets：question / topic / claim 三类。
    tgt_q = SS.ReworkTarget(target_id="t1", target_kind="question", target_ref="q1", reason="缺 aspect")
    qids, tids, ins = resolve_targets(parent, task, (tgt_q,))
    out["resolve_question"] = qids == {"q1"} and tids == {"t1"} and ins.get("q1") == ["缺 aspect"]

    tgt_t = SS.ReworkTarget(target_id="t2", target_kind="topic", target_ref="t2", reason="补 topic")
    qids, tids, ins = resolve_targets(parent, task, (tgt_t,))
    out["resolve_topic"] = qids == {"q2"} and tids == {"t2"} and ins.get("q2") == ["补 topic"]

    tgt_c = SS.ReworkTarget(target_id="t3", target_kind="claim", target_ref="c1", reason="value mismatch")
    qids, tids, ins = resolve_targets(parent, task, (tgt_c,))
    out["resolve_claim"] = qids == {"q1"} and tids == {"t1"} and ins.get("q1") == ["value mismatch"]

    # 2) restrict_task：身份保持 + 指令注入。
    restricted = restrict_task(task, {"q1"}, {"t1"}, ins)
    out["restrict_identity"] = (restricted.task_id == task.task_id
                                and restricted.topic_ids == ("t1",)
                                and [q.question_id for q in restricted.questions] == ["q1"])
    out["restrict_hint_injected"] = "定向返工" in restricted.questions[0].question

    # 3) merge：未触及 claim 身份不变，触及 claim 被替换。
    ref2 = SS.CitationRef(ref_type="structured", snapshot_id="S1",
                          item_code="TOTAL_ASSETS", period="2025-12-31")
    new_c1 = SS.SectionClaim(claim_id="c1_new", section_id="financial", topic_id="t1",
                             question_ids=("q1",), text="总资产为 1,234.56万元，同比增长", claim_type="fact",
                             citation_refs=(ref2,))
    new_result = SS.SectionResult(
        section_result_id="sr_new", section_version="secver_new", task_id="task_rw",
        section_id="financial", status="COMPLETED", claims=(new_c1,), markdown="# 财务分析")
    merged, replaced, untouched, new = merge(
        parent, new_result, target_question_ids={"q1"}, target_topic_ids={"t1"},
        renderer_version="p4-fin-renderer-v1", rules_version="p4-fin-rules-v1", task=task)
    out["merge_untouched_identity"] = (
        untouched == 1 and new == 1
        and any(c.claim_id == "c2" for c in merged.claims)  # 未触及 c2 保留
        and any(c.claim_id == "c1_new" for c in merged.claims)  # 触及 c1 被替换
        and all(c.claim_id != "c1" for c in merged.claims))
    out["merge_new_identity"] = (merged.section_result_id != parent.section_result_id
                                 and merged.section_result_id == SS.derive_section_result_id(merged.section_version))

    # 4) build_rework_run：batch_no=0 + 身份派生。
    rr = build_rework_run(parent, merged, eval_, job_id="job1", llm_evaluator_calls=1,
                          targets=(tgt_q,))
    out["rework_run_shape"] = (rr.batch_no == 0 and rr.from_section_result_id == "sr_parent"
                               and rr.section_result_id == merged.section_result_id
                               and rr.rework_run_id == SS.derive_rework_run_id("sr_parent", merged.section_result_id, 0))

    # 5) run_rework 端到端：fake worker_fn 返回新 claim；确定性最终检查无二次 LLM。
    def fake_worker(rt):
        return type("W", (), {"section_result": new_result})()
    res = run_rework(parent, task, (tgt_q,), evaluation=eval_, worker_fn=fake_worker,
                     renderer_version="p4-fin-renderer-v1", rules_version="p4-fin-rules-v1",
                     job_id="job1", llm_evaluator_calls=1)
    out["run_rework_end_to_end"] = (
        res.section_result.section_result_id == merged.section_result_id
        and res.rework_run.batch_no == 0
        and res.replaced_claim_count == 1 and res.untouched_claim_count == 1
        and res.new_claim_count == 1)

    # 6) 无有效 target → fail-closed。
    ghost = SS.ReworkTarget(target_id="tg", target_kind="question", target_ref="q_ghost", reason="x")
    try:
        run_rework(parent, task, (ghost,), evaluation=eval_, worker_fn=fake_worker,
                   renderer_version="r", rules_version="r")
        out["no_target_fails"] = False
    except ReworkError:
        out["no_target_fails"] = True

    # 7) 非 REWORK 决策 → fail-closed。
    eval_pass = SS.SectionEvaluation(
        evaluation_id="eval_pass", section_result_id="sr_parent",
        rules_version="r", evaluator_prompt_version="v", rules_passed=True,
        llm_passed=True, decision="PASS", rework_targets=(), llm_evaluator_calls=1)
    try:
        run_rework(parent, task, (tgt_q,), evaluation=eval_pass, worker_fn=fake_worker,
                   renderer_version="r", rules_version="r")
        out["non_rework_fails"] = False
    except ReworkError:
        out["non_rework_fails"] = True

    return out


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.rework",
        description="Phase 4 定向返工 runtime（至多一批）自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        result = _self_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        all_ok = all(result.values())
        print("\nself-check:", "PASS" if all_ok else "FAIL")
        return 0 if all_ok else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
