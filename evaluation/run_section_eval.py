"""Phase 4 Batch D — 章节级合成数据集 runner（纯离线，注入 fake LLM / worker / authority）。

任务书 §18：把 declarative JSON 数据集（evaluation/datasets/section_cases_v1.json）逐题
重建为 SectionTask / SectionResult，注入：
- pass-through citation_authority（所有 structured 引用判 valid，规避真实库依赖）；
- 逐 case 的 fake LLM Evaluator（数据集声明 llm_response；缺省为 PASS）；
- 通用定向返工 worker_fn（对受限任务的每个 question 产出一条 neutral inference claim）。

然后跑 ``sections.service.evaluate_section_and_rework`` 状态机，断言数据集声明的
expected{decision, llm_evaluator_calls, rework_attempted, final_check_passed} 与实际一致。
不读库、不联网、不调真实 LLM。

CLI: python -m evaluation.run_section_eval [--dataset PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from harness.schema import CitationRef
from planning import schema as PS
from sections import schema as SS
from sections import service as SV
from sections.citation_authority import CitationVerdict

DEFAULT_DATASET = "evaluation/datasets/section_cases_v1.json"

# 期望断言的字段；final_check_passed 仅对返工分支有意义（其余为 null）。
_EXPECTED_KEYS = ("decision", "llm_evaluator_calls", "rework_attempted", "final_check_passed")


# ---------------------------------------------------------------------------
# 重建（JSON → 结构化对象）
# ---------------------------------------------------------------------------

def _build_question(d: dict) -> PS.PlannedQuestion:
    return PS.PlannedQuestion(
        question_id=d["question_id"],
        question=d["question"],
        priority=d.get("priority", "P0"),
        topic_id=d["topic_id"],
        required_aspects=tuple(d.get("required_aspects") or ()),
        blocking_policy=tuple(d.get("blocking_policy") or ()),
        impact_scope=tuple(d.get("impact_scope") or ()),
    )


def _build_ref(d: dict) -> CitationRef:
    return CitationRef(
        ref_type=d["ref_type"],
        evidence_id=d.get("evidence_id"),
        snapshot_id=d.get("snapshot_id"),
        item_code=d.get("item_code"),
        formula_id=d.get("formula_id"),
        formula_version=d.get("formula_version"),
        period=d.get("period"),
        source_snapshot_id=d.get("source_snapshot_id"),
        page_number=d.get("page_number"),
    )


def _build_claim(d: dict, section_id: str) -> SS.SectionClaim:
    return SS.SectionClaim(
        claim_id=d["claim_id"],
        section_id=section_id,
        topic_id=d["topic_id"],
        question_ids=tuple(d["question_ids"]),
        text=d["text"],
        claim_type=d["claim_type"],
        citation_refs=tuple(_build_ref(r) for r in (d.get("citation_refs") or [])),
        derived_from_claim_ids=tuple(d.get("derived_from_claim_ids") or ()),
        impact_scope=tuple(d.get("impact_scope") or ()),
    )


def _build_unresolved(d: dict, section_id: str) -> SS.SectionUnresolved:
    return SS.SectionUnresolved(
        unresolved_id=d["unresolved_id"],
        section_id=section_id,
        topic_id=d["topic_id"],
        question_id=d.get("question_id"),
        state=d["state"],
        reason_code=d.get("reason_code", ""),
        detail=d.get("detail", ""),
        impact_scope=tuple(d.get("impact_scope") or ()),
        blocking_effects=tuple(d.get("blocking_effects") or ()),
        attempted_sources=tuple(d.get("attempted_sources") or ()),
    )


def build_task(case: dict) -> PS.SectionTask:
    """从 case 重建 SectionTask（task_id/plan_id 由 case_id 确定性派生）。"""
    section_id = case["section_id"]
    title = case.get("title", section_id)
    task = case["task"]
    questions = tuple(_build_question(q) for q in task["questions"])
    return PS.SectionTask(
        task_id=f"task_{case['case_id']}",
        plan_id=f"plan_{case['case_id']}",
        section_id=section_id,
        title=title,
        purpose=case.get("scenario", ""),
        research_policy="workflow",
        topic_ids=tuple(task["topic_ids"]),
        questions=questions,
        output_requirements=(),
        evaluation_rule_ids=(),
        allowed_capabilities=(),
        blocking_rules=(),
        dependency_versions={},
    )


def build_result(case: dict, task: PS.SectionTask) -> SS.SectionResult:
    """从 case 重建 SectionResult（claims/unresolved/status/markdown）。"""
    section_id = case["section_id"]
    claims = tuple(_build_claim(c, section_id) for c in case.get("claims") or [])
    unresolved = tuple(_build_unresolved(u, section_id) for u in case.get("unresolved") or [])
    return SS.SectionResult(
        section_result_id=f"sr_{case['case_id']}",
        section_version=f"secver_{case['case_id']}",
        task_id=task.task_id,
        section_id=section_id,
        status=case.get("status", "COMPLETED"),
        claims=claims,
        unresolved=unresolved,
        markdown=case.get("markdown", f"# {case.get('title', section_id)}"),
    )


# ---------------------------------------------------------------------------
# 注入件（fake，无 I/O）
# ---------------------------------------------------------------------------

class _PassAuthority:
    """pass-through 权威：所有引用判 valid，规避真实证据/财务库依赖。"""

    def validate(self, ref: CitationRef) -> CitationVerdict:  # noqa: ARG002
        return CitationVerdict(ref.ref_type, True)


def _build_llm_generate(case: dict) -> tuple[Callable, dict]:
    """逐 case 的 fake LLM Evaluator。返回 (generate, counter)。

    counter 记录实际调用次数，用于与 evaluation.llm_evaluator_calls 交叉校验。
    """
    llm_response = case.get("llm_response")
    counter = {"n": 0}

    def generate(messages: list[dict], system: str) -> str:  # noqa: ARG001
        counter["n"] += 1
        if llm_response is None:
            return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})
        return json.dumps(llm_response, ensure_ascii=False)

    return generate, counter


def _build_repair_worker() -> Callable:
    """通用定向返工 worker：对受限任务每个 question 产出一条 neutral inference claim。

    inference claim 无 citation 不触发 claim_missing_citation，文本无 recommendation /
    「未发现」/ 研判措辞，保证返工后确定性最终检查通过。
    """

    def worker(restricted_task: PS.SectionTask) -> object:
        claims = tuple(
            SS.SectionClaim(
                claim_id=f"{q.question_id}_repaired",
                section_id=restricted_task.section_id,
                topic_id=q.topic_id,
                question_ids=(q.question_id,),
                text="经定向返工补充，该问题结论已更新",
                claim_type="inference",
                citation_refs=(),
            )
            for q in restricted_task.questions
        )
        result = SS.SectionResult(
            section_result_id="sr_repaired",
            section_version="secver_repaired",
            task_id=restricted_task.task_id,
            section_id=restricted_task.section_id,
            status="COMPLETED",
            claims=claims,
            markdown="# 返工后章节",
        )
        return type("W", (), {"section_result": result})()

    return worker


# ---------------------------------------------------------------------------
# 数据加载 + 校验
# ---------------------------------------------------------------------------

def load_dataset(dataset_path: str | None = None) -> list[dict]:
    """读 JSON 数据集，校验唯一 case_id 与必填字段（fail-closed）。"""
    path = Path(dataset_path or DEFAULT_DATASET)
    raw = path.read_text(encoding="utf-8")
    cases = json.loads(raw)
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"数据集 {path} 顶层必须是非空 JSON 数组")
    seen: set[str] = set()
    for c in cases:
        if not isinstance(c, dict):
            raise ValueError(f"数据集含非对象条目: {c!r}")
        case_id = c.get("case_id")
        if not case_id:
            raise ValueError(f"数据集条目缺 case_id: {c!r}")
        if case_id in seen:
            raise ValueError(f"case_id 重复: {case_id!r}")
        seen.add(case_id)
        for key in ("section_id", "task", "expected"):
            if key not in c:
                raise ValueError(f"case {case_id} 缺必填字段 {key!r}")
        missing_exp = [k for k in _EXPECTED_KEYS if k not in c["expected"]]
        if missing_exp:
            raise ValueError(f"case {case_id} expected 缺字段 {missing_exp}")
    return cases


# ---------------------------------------------------------------------------
# 单题运行 + 全量聚合
# ---------------------------------------------------------------------------

def run_case(case: dict) -> dict:
    """重建并运行一个 case，返回实际 outcome 与逐字段比对。"""
    task = build_task(case)
    result = build_result(case, task)
    generate, counter = _build_llm_generate(case)
    worker_fn = _build_repair_worker()
    authority = _PassAuthority()

    ev = SV.evaluate_section_and_rework(
        result, task, company_id="SYNTH", renderer_version="p4-renderer-v1",
        rules_version="p4-rules-v1", citation_authority=authority,
        fact_pack=None, proposed_scheme=None,
        llm_evaluator_generate=generate, worker_fn=worker_fn)

    actual = {
        "decision": ev.evaluation.decision,
        "llm_evaluator_calls": ev.llm_evaluator_calls,
        "rework_attempted": ev.rework_run is not None,
        "final_check_passed": ev.final_rules_passed,
    }
    expected = case["expected"]
    checks = {k: (actual[k] == expected[k]) for k in _EXPECTED_KEYS}
    checks["llm_invocation_consistent"] = (counter["n"] == ev.llm_evaluator_calls)
    return {"case_id": case["case_id"], "expected": expected, "actual": actual,
            "checks": checks, "ok": all(checks.values())}


def run_all(dataset_path: str | None = None) -> dict:
    """跑全量数据集，返回标准 eval dict（{passed,failed,skipped,details} + per_case）。"""
    cases = load_dataset(dataset_path)
    per_case: list[dict] = []
    passed = failed = 0
    details: list[str] = []
    for case in cases:
        r = run_case(case)
        per_case.append(r)
        if r["ok"]:
            passed += 1
        else:
            failed += 1
            for k, ok in r["checks"].items():
                if not ok:
                    details.append(
                        f"FAIL: {case['case_id']} {k} expected={r['expected'][k]!r} "
                        f"actual={r['actual'][k]!r}" if k in _EXPECTED_KEYS else
                        f"FAIL: {case['case_id']} {k}")
    return {"passed": passed, "failed": failed, "skipped": 0,
            "details": details, "per_case": per_case}


def _main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run_section_eval",
        description="章节级合成数据集 runner（状态机 expected-vs-actual，纯离线）")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    args = parser.parse_args(argv)

    result = run_all(args.dataset)
    for r in result["per_case"]:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['case_id']}  decision={r['actual']['decision']} "
              f"calls={r['actual']['llm_evaluator_calls']} "
              f"rework={r['actual']['rework_attempted']}")
    print(json.dumps({"passed": result["passed"], "failed": result["failed"],
                      "skipped": result["skipped"], "details": result["details"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
