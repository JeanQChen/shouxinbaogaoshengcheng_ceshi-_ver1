"""Phase 4 Batch D — Streamlit 薄预览 UI 专项评测（约束 #12 / 任务书 §15）。

纯离线：不启动 Streamlit、不调 LLM、不读库。分两层验证「薄」：

1. AST 静态薄度检查：streamlit_app.py 的 Phase 4 主流程不得 import 任何 V1 业务逻辑包
   （parsers/retrieval/evidence/financial/agents/reporting/external/external_v2/harness/
   routing/tools/llm），必须走 sections.service + planning.report_planner + contracts.schema；
   不得残留 V1 业务函数（_parse_and_save / _build_evidence_from_parsed / ...）；同时保留
   Phase 3 冻结的 V2 实验面板（_render_financial_v2_confirmation / _render_financial_v2_snapshot
   / _generate_report），否则会破坏 evals.test_financial_v2_* 冻结测试。

2. DTO 渲染冒烟：patch streamlit_app.st 后，用真实 SectionPreview / Phase4RunResult 调
   _render_preview / _render_result，确保渲染器对 Preview DTO 的字段访问与实际 DTO 一致
   （字段名拼错会在这里抛 AttributeError，而非运行时才爆）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import schema as SS  # noqa: E402
from sections import service as SV  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


_APP_PATH = Path(__file__).resolve().parent.parent / "streamlit_app.py"

# V1 业务逻辑包（Phase 4 主流程不得直接 import；一切经 sections.service 编排）。
# 注意：financial_v2 是 Phase 3 冻结 V2 实验面板的合法依赖，不在禁用清单。
_BANNED_ROOTS = {
    "parsers", "retrieval", "evidence", "financial",
    "agents", "reporting", "external", "external_v2",
    "harness", "routing", "tools", "llm",
}

# V1 业务函数（薄化后不得残留）。
_OLD_FUNCS = (
    "_parse_and_save", "_build_evidence_from_parsed",
    "_process_pdf", "_parse_and_index_pdf", "_clear_company",
    "_clear_chroma_collection", "_render_evidence_status",
    "_render_evidence_progress", "_classify_error", "_ensure_db",
)

# Phase 3 冻结 V2 面板（必须保留，否则破坏冻结测试）。
_FROZEN_V2_FUNCS = (
    "_render_batch_result", "_render_financial_v2_confirmation",
    "_render_financial_v2_snapshot", "_generate_report",
)


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _check_thinness() -> None:
    src = _APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots = _imported_roots(tree)

    check(not (roots & _BANNED_ROOTS),
          f"Phase 4 主流程不 import V1 业务逻辑包（命中 {sorted(roots & _BANNED_ROOTS)}）")
    check({"sections", "planning", "contracts", "config"} <= roots,
          "import sections / planning / contracts / config（薄粘合层）")
    check("run_phase4" in src, "调用 sections.service.run_phase4")
    check(".previews()" in src, "通过 result.previews() 展示 DTO")
    for name in _OLD_FUNCS:
        check(name not in src, f"不残留 V1 业务函数 {name}")
    for name in _FROZEN_V2_FUNCS:
        check(f"def {name}" in src, f"保留 Phase 3 冻结 V2 面板 {name}")
    # 冻结测试依赖源结构顺序：_generate_report 必须出现在 _render_financial_v2_snapshot 之后。
    check(src.index("def _generate_report") > src.index("def _render_financial_v2_snapshot"),
          "源结构顺序满足冻结测试（_generate_report 在 _render_financial_v2_snapshot 之后）")


def _check_render_dtos() -> None:
    from unittest.mock import MagicMock

    import streamlit_app as APP  # noqa: N813

    fake_st = MagicMock()
    fake_st.session_state.get.return_value = None
    old_st = APP.st
    APP.st = fake_st
    try:
        _render_dtos(APP)
    finally:
        APP.st = old_st


def _render_dtos(APP) -> None:  # noqa: N803
    # 渲染器对 Preview DTO 的字段访问必须与实际 DTO 字段一致（拼错 → AttributeError）。
    pv = SV.SectionPreview(
        section_id="financial", task_id="t", title="财务分析",
        section_result_id="sr", status="COMPLETED", decision="PASS",
        evaluation_id="e", llm_evaluator_calls=1, claim_count=2,
        unresolved_count=0, rework_attempted=False, final_check_passed=True,
        issue_count=0, markdown="# 财务\n\n正文", error=None)
    issue = SS.SectionIssue(issue_id="i", rule_id="r1", severity="yellow",
                            location="l", detail="数值存疑")
    unresolved = SS.SectionUnresolved(
        unresolved_id="u", section_id="financial", topic_id="t1", question_id="q",
        state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")
    outcome = SV.SectionOutcome(
        section_id="financial", task_id="t", title="财务分析",
        section_result=SS.SectionResult(
            section_result_id="sr", section_version="sv", task_id="t",
            section_id="financial", status="COMPLETED", claims=(),
            unresolved=(unresolved,), markdown="# 财务"),
        evaluation=SS.SectionEvaluation(
            evaluation_id="e", section_result_id="sr", rules_version="r",
            evaluator_prompt_version="p", rules_passed=True, llm_passed=True,
            decision="PASS", issues=(issue,), llm_evaluator_calls=1),
        rework_run=None, final_rules_passed=True)

    try:
        APP._render_preview(pv, outcome)
        check(True, "渲染完整 Preview DTO（含 issue / unresolved）不抛错")
    except Exception as e:  # noqa: BLE001
        check(False, f"渲染 Preview DTO 抛错: {type(e).__name__}: {e}")

    # 错误态 Preview（error 非空 → 提前返回，仅 st.error）。
    err_pv = SV.outcome_preview(SV.SectionOutcome(
        section_id="financial", task_id="t", title="财务",
        section_result=None, evaluation=None, rework_run=None,
        final_rules_passed=None, error="boom"))
    try:
        APP._render_preview(err_pv, None)
        check(True, "渲染错误态 Preview 不抛错")
    except Exception as e:  # noqa: BLE001
        check(False, f"渲染错误态 Preview 抛错: {type(e).__name__}: {e}")

    # 整体结果（manifest 摘要 + 三章节 zip）路径。
    manifest = SS.SectionRunManifest(
        manifest_id="m" * 32, job_id="j", run_id="r", code_fingerprint="c" * 32,
        phase3_closure_fingerprint="p" * 32, batch_versions={}, frozen={})
    res = SV.Phase4RunResult(
        job_id="j", run_id="r", plan_id="pl", manifest_id=manifest.manifest_id,
        manifest=manifest, sections=(outcome,), success=False)
    try:
        APP._render_result(res)
        check(True, "渲染 Phase4RunResult（manifest 摘要 + previews）不抛错")
    except Exception as e:  # noqa: BLE001
        check(False, f"渲染 Phase4RunResult 抛错: {type(e).__name__}: {e}")


def main():
    _check_thinness()
    _check_render_dtos()
    return _results


if __name__ == "__main__":
    import json
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
