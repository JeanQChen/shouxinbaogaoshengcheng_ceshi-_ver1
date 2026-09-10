"""Phase 4 Batch D — 章节级合成数据集 runner 专项评测（任务书 §18）。

纯离线：读 evaluation/datasets/section_cases_v1.json，重建 SectionTask/SectionResult，
注入 fake authority / fake LLM Evaluator / fake worker，跑 evaluate_section_and_rework
状态机，逐题断言 expected{decision, llm_evaluator_calls, rework_attempted,
final_check_passed} 与 actual 一致；并验证 runner 的数据加载 fail-closed（重复 case_id /
缺必填字段 / 非对象条目 → ValueError）、重建正确性、状态机分支覆盖完整性。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_section_eval as R  # noqa: E402
from sections import schema as SS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _load() -> list[dict]:
    return R.load_dataset()


def _check_dataset_loading() -> None:
    cases = _load()
    check(len(cases) == 9, f"数据集含 9 题（实际 {len(cases)}）")
    ids = [c["case_id"] for c in cases]
    check(len(set(ids)) == len(ids), "case_id 唯一")
    check(all(isinstance(c, dict) for c in cases), "数据集全为对象条目")
    for c in cases:
        check(set(c) >= {"case_id", "section_id", "task", "expected"},
              f"{c['case_id']} 含必填字段 case_id/section_id/task/expected")
        check(set(c["expected"]) >= {"decision", "llm_evaluator_calls",
                                     "rework_attempted", "final_check_passed"},
              f"{c['case_id']} expected 四键齐备")


def _check_reconstruction() -> None:
    # 用含结构化引用的 case 验证 claim → CitationRef 字段还原。
    case = next(c for c in _load() if c["case_id"] == "fin-pass-llm-pass")
    task = R.build_task(case)
    result = R.build_result(case, task)

    check(task.section_id == "financial" and task.task_id == "task_fin-pass-llm-pass"
          and len(task.questions) == 1 and task.topic_ids == ("t1",),
          "build_task 重建 section_id/task_id/questions/topic_ids")
    check(len(result.claims) == 1 and result.claims[0].claim_id == "c1",
          "build_result 重建 claim 数量与 claim_id")
    ref = result.claims[0].citation_refs[0]
    check(ref.ref_type == "structured" and ref.snapshot_id == "S1"
          and ref.item_code == "TOTAL_ASSETS" and ref.period == "2025-12-31",
          "CitationRef 字段还原（snapshot_id/item_code/period）")
    check(result.status == "COMPLETED" and result.markdown.startswith("#"),
          "build_result 还原 status/markdown")

    # unresolved 字段还原（含 WAITING_HUMAN + reason_code）。
    case_u = next(c for c in _load() if c["case_id"] == "co-block-waiting-human")
    res_u = R.build_result(case_u, R.build_task(case_u))
    u = res_u.unresolved[0]
    check(u.state == "WAITING_HUMAN" and u.question_id == "q1"
          and u.reason_code == "transfer_human",
          "SectionUnresolved 字段还原（state/question_id/reason_code）")


def _check_state_machine_branches() -> None:
    result = R.run_all()
    check(result["failed"] == 0 and result["passed"] == 9,
          f"run_all 9 题全通过（passed={result['passed']} failed={result['failed']}）")
    check(result["skipped"] == 0, "run_all 无跳过")

    decisions = {r["actual"]["decision"] for r in result["per_case"]}
    check(decisions == {"BLOCKED", "REWORK", "PASS"},
          f"状态机覆盖 BLOCKED/REWORK/PASS 三终态（实际 {sorted(decisions)}）")

    calls = [r["actual"]["llm_evaluator_calls"] for r in result["per_case"]]
    check(all(c in (0, 1) for c in calls), "llm_evaluator_calls 恒 ∈ {0,1}（至多一次）")
    check(any(c == 0 for c in calls) and any(c == 1 for c in calls),
          "数据集同时覆盖 calls=0 与 calls=1")

    # 返工题（REWORK）必须 final_check_passed=True；非返工题 final_check_passed=None。
    for r in result["per_case"]:
        if r["actual"]["decision"] == "REWORK":
            check(r["actual"]["rework_attempted"] is True,
                  f"{r['case_id']} REWORK 必返工")
            check(r["actual"]["final_check_passed"] is True,
                  f"{r['case_id']} 返工后确定性最终检查通过")
        else:
            check(r["actual"]["rework_attempted"] is False
                  and r["actual"]["final_check_passed"] is None,
                  f"{r['case_id']} 非返工不产生 rework_run / final_check_passed")

    # 每条 case 的 fake LLM 实际调用次数与 evaluation.llm_evaluator_calls 一致。
    check(all(r["checks"].get("llm_invocation_consistent") for r in result["per_case"]),
          "fake LLM 调用次数与 llm_evaluator_calls 交叉一致")


def _check_fail_closed() -> None:
    good = next(c for c in _load() if c["case_id"] == "fin-pass-llm-pass")

    def _loads(obj) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                         delete=False) as f:
            json.dump(obj, f, ensure_ascii=False)
            path = f.name
        try:
            R.load_dataset(path)
            check(False, "畸形数据集应 fail-closed")
        except (ValueError, json.JSONDecodeError):
            check(True, "畸形数据集 fail-closed（raise）")
        finally:
            Path(path).unlink()

    # 重复 case_id。
    dup = [dict(good), dict(good)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as f:
        json.dump(dup, f, ensure_ascii=False)
        path = f.name
    try:
        try:
            R.load_dataset(path)
            check(False, "重复 case_id 应 fail-closed")
        except ValueError as e:
            check("重复" in str(e), "重复 case_id → ValueError")
    finally:
        Path(path).unlink()

    # 缺 task 字段。
    no_task = dict(good)
    del no_task["task"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as f:
        json.dump([no_task], f, ensure_ascii=False)
        path = f.name
    try:
        try:
            R.load_dataset(path)
            check(False, "缺 task 应 fail-closed")
        except ValueError:
            check(True, "缺 task 字段 → ValueError")
    finally:
        Path(path).unlink()

    # 缺 expected 键。
    no_exp = dict(good)
    no_exp["expected"] = {"decision": "PASS"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as f:
        json.dump([no_exp], f, ensure_ascii=False)
        path = f.name
    try:
        try:
            R.load_dataset(path)
            check(False, "缺 expected 键应 fail-closed")
        except ValueError:
            check(True, "缺 expected 键 → ValueError")
    finally:
        Path(path).unlink()

    # 顶层非数组。
    _loads({"case_id": "x"})


def main():
    _check_dataset_loading()
    _check_reconstruction()
    _check_state_machine_branches()
    _check_fail_closed()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
