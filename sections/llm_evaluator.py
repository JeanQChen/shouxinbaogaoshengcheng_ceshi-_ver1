"""Phase 4 Batch D — 章节 LLM Evaluator（每章至多一次，独立 prompt）。

任务书 §13.2：在 Rules Evaluator 通过（或仅 warning）后，对章节做一次 LLM 语义复核，
判断可交付性。硬约束：
- **每章至多调用一次**：``llm_calls`` 恒为 1（本函数被调用即一次 LLM 调用）；调用方
  负责在规则阻断/返工分支下**不调用**本函数（``llm_evaluator_calls`` 最终 0 或 1）。
  本模块无重试——输出非法直接 fail-closed 返回 FAILED，绝不二次调用。
- **不覆盖规则结论**：本模块仅在规则通过后运行，只能把 PASS 降级为
  PASS_WITH_GAPS / REWORK / BLOCKED / FAILED，不能升级规则失败。
- 引用复用现有 Structured Citation / FinancialFact / marker 安全链，不算数字。

CLI: python -m sections.llm_evaluator --self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from typing import Callable

from llm import client as llm
from planning import schema as PS
from sections import schema as SS

logger = logging.getLogger("sections.llm_evaluator")

# 版本常量（变更必须递增；进入 evaluation_id 派生）。
PROMPT_VERSION = "section_evaluator_v1"
EVALUATOR_VERSION = "p4-llm-evaluator-v1"

# 语义复核可给出的决策（与 SS.EVALUATION_DECISIONS 一致）。
_DECISIONS = SS.EVALUATION_DECISIONS
_ISSUE_SEVERITIES = ("blocking", "rework", "warning")
_TARGET_KINDS = ("question", "topic", "claim")


class LlmEvaluatorError(RuntimeError):
    """LLM Evaluator fail-closed 错误。"""


@dataclass(frozen=True)
class LlmEvaluation:
    """一次 LLM 语义复核的结论（不可变）。"""

    decision: str            # PASS | PASS_WITH_GAPS | REWORK | BLOCKED | FAILED
    passed: bool             # decision ∈ {PASS, PASS_WITH_GAPS}
    issues: tuple[SS.SectionIssue, ...]
    rework_targets: tuple[SS.ReworkTarget, ...]
    llm_calls: int           # 恒 1
    raw: str                 # LLM 原始输出（审计）


def final_decision(rules_verdict, llm_eval: "LlmEvaluation | None") -> str:
    """合并规则结论与 LLM 结论得到最终 decision。

    规则阻断/返工优先于 LLM；规则通过后 LLM 结论为最终 decision。
    """
    if rules_verdict.blocking:
        return "BLOCKED"
    if rules_verdict.rework_targets:
        return "REWORK"
    if llm_eval is None:
        return "PASS"
    return llm_eval.decision


# ---------------------------------------------------------------------------
# 渲染 / 解析（无 I/O，注入 LLM 输出）
# ---------------------------------------------------------------------------

def _result_json(result: SS.SectionResult) -> str:
    return json.dumps({
        "section_id": result.section_id,
        "status": result.status,
        "markdown": result.markdown,
        "claims": [
            {"claim_id": c.claim_id, "topic_id": c.topic_id,
             "question_ids": list(c.question_ids), "claim_type": c.claim_type,
             "text": c.text, "confidence": c.confidence,
             "impact_scope": list(c.impact_scope)}
            for c in result.claims
        ],
        "unresolved": [
            {"unresolved_id": u.unresolved_id, "topic_id": u.topic_id,
             "question_id": u.question_id, "state": u.state, "detail": u.detail,
             "blocking_effects": list(u.blocking_effects)}
            for u in result.unresolved
        ],
    }, ensure_ascii=False, indent=2)


def _questions_json(task: PS.SectionTask) -> str:
    return json.dumps([
        {"question_id": q.question_id, "question": q.question,
         "topic_id": q.topic_id, "priority": q.priority,
         "required_aspects": list(q.required_aspects),
         "impact_scope": list(q.impact_scope)}
        for q in task.questions
    ], ensure_ascii=False, indent=2)


def _rules_json(rules_verdict) -> str:
    if rules_verdict is None:
        return json.dumps({"rules_passed": True, "blocking": False, "issues": []},
                          ensure_ascii=False)
    return json.dumps({
        "rules_passed": rules_verdict.rules_passed,
        "blocking": rules_verdict.blocking,
        "issues": [{"rule_id": i.rule_id, "severity": i.severity, "detail": i.detail}
                   for i in rules_verdict.issues],
    }, ensure_ascii=False, indent=2)


def build_prompt(result: SS.SectionResult, task: PS.SectionTask,
                 rules_verdict=None) -> tuple[str, str]:
    """渲染 (system, user)。prompt 模板独立落盘于 llm/prompts/section_evaluator.txt。"""
    template = llm.load_prompt("section_evaluator")
    system = ("你是授信报告章节语义复核员，严格遵守 prompt 中的铁律，"
              "只输出 JSON，绝不计算数字。")
    user = (template.replace("<<RESULT>>", _result_json(result))
                    .replace("<<QUESTIONS>>", _questions_json(task))
                    .replace("<<RULES>>", _rules_json(rules_verdict)))
    return system, user


def _parse_json(text: str) -> dict:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LlmEvaluatorError("LLM 输出不含 JSON 对象（fail-closed）")
    try:
        payload = json.loads(s[start:end + 1])
    except json.JSONDecodeError as e:
        raise LlmEvaluatorError(f"LLM 输出 JSON 解析失败: {e}") from e
    if not isinstance(payload, dict):
        raise LlmEvaluatorError("LLM 输出顶层必须是 JSON 对象")
    return payload


def _construct(payload: dict, result: SS.SectionResult, task: PS.SectionTask) -> LlmEvaluation:
    """校验 LLM 输出并构造结构化结论（不二次调用；非法 → fail-closed FAILED）。"""
    decision = payload.get("decision")
    if decision not in _DECISIONS:
        decision = "FAILED"

    valid_question_ids = set(task.question_ids())
    valid_topic_ids = set(task.topic_ids)
    valid_claim_ids = {c.claim_id for c in result.claims}

    issues: list[SS.SectionIssue] = []
    for raw in (payload.get("issues") or []):
        if not isinstance(raw, dict):
            continue
        rule_id = str(raw.get("rule_id") or "").strip()
        severity = raw.get("severity")
        detail = str(raw.get("detail") or "").strip()
        location = str(raw.get("location") or "").strip()
        if not rule_id or severity not in _ISSUE_SEVERITIES or not detail:
            continue
        issues.append(SS.SectionIssue(
            issue_id=SS.derive_issue_id(rule_id, location, detail, severity),
            rule_id=rule_id, severity=severity, location=location, detail=detail,
            suggested_action=str(raw.get("suggested_action") or "")))

    rework_targets: list[SS.ReworkTarget] = []
    for raw in (payload.get("rework_targets") or []):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("target_kind")
        ref = str(raw.get("target_ref") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if kind not in _TARGET_KINDS or not ref or not reason:
            continue
        if kind == "question" and ref not in valid_question_ids:
            continue
        if kind == "topic" and ref not in valid_topic_ids:
            continue
        if kind == "claim" and ref not in valid_claim_ids:
            continue
        rework_targets.append(SS.ReworkTarget(
            target_id=SS.derive_rework_target_id(kind, ref, reason),
            target_kind=kind, target_ref=ref, reason=reason))

    # REWORK 必须给出有效 target，否则 fail-closed 降级 FAILED（无法执行返工）。
    if decision == "REWORK" and not rework_targets:
        decision = "FAILED"
        issues.append(SS.SectionIssue(
            issue_id=SS.derive_issue_id("llm_evaluator_invalid_output", "section",
                                        "REWORK 无有效 rework_target", "blocking"),
            rule_id="llm_evaluator_invalid_output", severity="blocking",
            location="section", detail="REWORK 决策未给出任何有效 rework_target（fail-closed）"))

    return LlmEvaluation(
        decision=decision,
        passed=decision in ("PASS", "PASS_WITH_GAPS"),
        issues=tuple(issues),
        rework_targets=tuple(rework_targets),
        llm_calls=1,
        raw=json.dumps(payload, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _default_llm_generate(messages: list[dict], system: str) -> str:
    resp = llm.chat_with_usage(messages, system=system, prompt_version=PROMPT_VERSION,
                               thinking={"type": "disabled"})
    return resp.text


def evaluate(result: SS.SectionResult, task: PS.SectionTask, *,
             rules_verdict=None,
             llm_generate: Callable[[list[dict], str], str] | None = None) -> LlmEvaluation:
    """对单个 SectionResult 做一次 LLM 语义复核（恒一次调用）。

    llm_generate 注入用于离线测试（默认走真实 LLM，自动落盘 logs/llm/）。
    """
    generate = llm_generate or _default_llm_generate
    system, user = build_prompt(result, task, rules_verdict)
    raw_text = generate([{"role": "user", "content": user}], system)
    try:
        payload = _parse_json(raw_text)
        return _construct(payload, result, task)
    except LlmEvaluatorError as e:
        logger.error("LLM Evaluator 输出非法（fail-closed FAILED）: %s", e)
        return LlmEvaluation(
            decision="FAILED", passed=False, issues=(), rework_targets=(),
            llm_calls=1, raw=raw_text)


# ---------------------------------------------------------------------------
# CLI 自检（注入 fake LLM，不读库不联网）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    out: dict = {}

    def _task():
        return PS.SectionTask(
            task_id="task_le", plan_id="plan_le", section_id="financial",
            title="财务分析", purpose="p", research_policy="workflow",
            topic_ids=("t1",),
            questions=(PS.PlannedQuestion(question_id="q1", question="Q1",
                                          priority="P0", topic_id="t1",
                                          required_aspects=("流动比率",),
                                          blocking_policy=(), impact_scope=()),
                       ),
            evaluation_rule_ids=(), allowed_capabilities=(),
            output_requirements=(), blocking_rules=(), dependency_versions={},
        )

    ref = SS.CitationRef(ref_type="structured", snapshot_id="S1",
                         item_code="TOTAL_ASSETS", period="2025-12-31")
    claim = SS.SectionClaim(claim_id="c1", section_id="financial", topic_id="t1",
                            question_ids=("q1",), text="流动比率 1.5", claim_type="fact",
                            citation_refs=(ref,))
    result = SS.SectionResult(section_result_id="sr_le", section_version="secver_le",
                              task_id="task_le", section_id="financial",
                              status="COMPLETED", claims=(claim,), markdown="# 财务分析")

    def fake_pass(messages, system):  # noqa: ARG001
        return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})

    def fake_rework(messages, system):  # noqa: ARG001
        return json.dumps({"decision": "REWORK", "issues": [
            {"rule_id": "aspect_gap", "severity": "rework", "location": "q1",
             "detail": "aspect 未覆盖"}],
            "rework_targets": [{"target_kind": "question", "target_ref": "q1",
                                "reason": "补 aspect"}]})

    def fake_rework_bad_target(messages, system):  # noqa: ARG001
        return json.dumps({"decision": "REWORK", "issues": [],
                           "rework_targets": [{"target_kind": "question",
                                               "target_ref": "q_ghost", "reason": "x"}]})

    def fake_blocked(messages, system):  # noqa: ARG001
        return json.dumps({"decision": "BLOCKED", "issues": [
            {"rule_id": "subject_error", "severity": "blocking", "location": "section",
             "detail": "主体错误"}], "rework_targets": []})

    def fake_garbage(messages, system):  # noqa: ARG001
        return "这不是 JSON"

    e = evaluate(result, _task(), llm_generate=fake_pass)
    out["pass_decision"] = e.decision == "PASS" and e.passed and e.llm_calls == 1

    e = evaluate(result, _task(), llm_generate=fake_rework)
    out["rework_with_target"] = (e.decision == "REWORK" and not e.passed
                                 and len(e.rework_targets) == 1
                                 and e.rework_targets[0].target_ref == "q1")

    e = evaluate(result, _task(), llm_generate=fake_rework_bad_target)
    out["rework_bad_target_fails"] = e.decision == "FAILED"

    e = evaluate(result, _task(), llm_generate=fake_blocked)
    out["blocked_decision"] = e.decision == "BLOCKED" and not e.passed

    e = evaluate(result, _task(), llm_generate=fake_garbage)
    out["garbage_fails"] = e.decision == "FAILED" and e.llm_calls == 1

    # final_decision 组合：规则阻断/返工优先
    class _Rules:
        def __init__(self, blocking, targets):
            self.blocking = blocking
            self.rework_targets = targets
            self.rules_passed = (not blocking) and (not targets)
    e_pass = evaluate(result, _task(), llm_generate=fake_pass)
    out["final_pass"] = final_decision(_Rules(False, ()), e_pass) == "PASS"
    out["final_blocked"] = final_decision(_Rules(True, ()), None) == "BLOCKED"
    out["final_rework"] = final_decision(_Rules(False, (1,)), None) == "REWORK"

    return out


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.llm_evaluator",
        description="Phase 4 章节 LLM Evaluator（每章至多一次）自检")
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
