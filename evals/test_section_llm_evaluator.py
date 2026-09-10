"""Phase 4 Batch D — 章节 LLM Evaluator 专项评测（每章至多一次调用）。

纯离线：注入确定性 fake llm_generate，不联网、不落盘。覆盖决策映射、目标校验、
非法输出 fail-closed（不二次调用）、与 RulesVerdict 的 final_decision 组合。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import llm_evaluator as LE  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _task():
    return PS.SectionTask(
        task_id="task_le", plan_id="plan_le", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=("t1",),
        questions=(PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                                      topic_id="t1", required_aspects=("流动比率",),
                                      blocking_policy=(), impact_scope=()),
                   ),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(), dependency_versions={},
    )


def _result():
    ref = CitationRef(ref_type="structured", snapshot_id="S1",
                      item_code="TOTAL_ASSETS", period="2025-12-31")
    claim = SS.SectionClaim(claim_id="c1", section_id="financial", topic_id="t1",
                            question_ids=("q1",), text="流动比率 1.5", claim_type="fact",
                            citation_refs=(ref,))
    return SS.SectionResult(section_result_id="sr_le", section_version="secver_le",
                            task_id="task_le", section_id="financial",
                            status="COMPLETED", claims=(claim,), markdown="# 财务分析")


def _fake(payload):
    def fn(messages, system):  # noqa: ARG001
        return json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
    return fn


class _Rules:
    def __init__(self, blocking=False, targets=()):
        self.blocking = blocking
        self.rework_targets = targets
        self.rules_passed = (not blocking) and (not targets)
        self.issues = ()


def main():
    task = _task()
    result = _result()

    # --- 决策映射 ---
    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "PASS"}))
    check(e.decision == "PASS" and e.passed and e.llm_calls == 1, "PASS → passed=True, calls=1")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "PASS_WITH_GAPS"}))
    check(e.decision == "PASS_WITH_GAPS" and e.passed, "PASS_WITH_GAPS → passed=True")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "BLOCKED", "issues": [
        {"rule_id": "subject_error", "severity": "blocking", "location": "section",
         "detail": "主体错误"}]}))
    check(e.decision == "BLOCKED" and not e.passed, "BLOCKED → passed=False")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "FAILED"}))
    check(e.decision == "FAILED" and not e.passed, "FAILED 直通")

    # --- 非法输出 fail-closed，不二次调用 ---
    calls = {"n": 0}

    def counting_garbage(messages, system):  # noqa: ARG001
        calls["n"] += 1
        return "这不是 JSON"

    e = LE.evaluate(result, task, llm_generate=counting_garbage)
    check(e.decision == "FAILED" and e.llm_calls == 1, "垃圾输出 → FAILED")
    check(calls["n"] == 1, f"垃圾输出不重试（调用 {calls['n']} 次）")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "UNKNOWN_DECISION"}))
    check(e.decision == "FAILED", "非法 decision → FAILED")

    # --- REWORK 目标校验 ---
    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "REWORK", "issues": [
        {"rule_id": "aspect_gap", "severity": "rework", "location": "q1", "detail": "缺 aspect"}],
        "rework_targets": [{"target_kind": "question", "target_ref": "q1", "reason": "补"}]}))
    check(e.decision == "REWORK" and len(e.rework_targets) == 1
          and e.rework_targets[0].target_ref == "q1", "REWORK 有效 target 通过")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "REWORK", "issues": [],
                                                      "rework_targets": [
                                                          {"target_kind": "question",
                                                           "target_ref": "q_ghost",
                                                           "reason": "x"}]}))
    check(e.decision == "FAILED", "REWORK 但 target 幻觉 → FAILED")

    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "REWORK",
                                                      "rework_targets": [
                                                          {"target_kind": "claim",
                                                           "target_ref": "c1", "reason": "改"}]}))
    check(e.decision == "REWORK" and e.rework_targets[0].target_kind == "claim",
          "REWORK claim 目标通过")

    # --- issue 严重度过滤 + 幂等 id ---
    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "PASS_WITH_GAPS", "issues": [
        {"rule_id": "soft_gap", "severity": "warning", "location": "q1", "detail": "轻微"},
        {"rule_id": "bad", "severity": "INVALID", "location": "q1", "detail": "应被丢弃"}]}))
    check(len(e.issues) == 1 and e.issues[0].rule_id == "soft_gap",
          "非法 severity 的 issue 被丢弃")

    e1 = LE.evaluate(result, task, llm_generate=_fake({"decision": "REWORK", "issues": [
        {"rule_id": "x", "severity": "rework", "location": "q1", "detail": "d"}],
        "rework_targets": [{"target_kind": "topic", "target_ref": "t1", "reason": "r"}]}))
    e2 = LE.evaluate(result, task, llm_generate=_fake({"decision": "REWORK", "issues": [
        {"rule_id": "x", "severity": "rework", "location": "q1", "detail": "d"}],
        "rework_targets": [{"target_kind": "topic", "target_ref": "t1", "reason": "r"}]}))
    check({i.issue_id for i in e1.issues} == {i.issue_id for i in e2.issues}
          and {t.target_id for t in e1.rework_targets} == {t.target_id for t in e2.rework_targets},
          "相同 LLM 输出 → issue_id / target_id 幂等")

    # --- final_decision 组合（规则优先） ---
    e_pass = LE.evaluate(result, task, llm_generate=_fake({"decision": "PASS"}))
    check(LE.final_decision(_Rules(False, ()), e_pass) == "PASS", "规则过 + LLM PASS → PASS")
    check(LE.final_decision(_Rules(True, ()), None) == "BLOCKED", "规则阻断 → BLOCKED")
    check(LE.final_decision(_Rules(False, ("t",)), None) == "REWORK", "规则返工 → REWORK")

    # --- 每章至多一次：evaluate 恒 llm_calls==1 ---
    e = LE.evaluate(result, task, llm_generate=_fake({"decision": "PASS"}))
    check(e.llm_calls == 1, "llm_calls 恒为 1（无二次调用）")

    # --- prompt 渲染：占位符已替换，不残留 << >> ---
    system, user = LE.build_prompt(result, task, _Rules(False, ()))
    check("<<RESULT>>" not in user and "<<QUESTIONS>>" not in user and "<<RULES>>" not in user,
          "prompt 占位符全部替换")
    check("task_le" not in user or "c1" in user, "prompt 含 claim 内容")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
