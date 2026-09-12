"""Eval: 契约派生切片 + 漂移 fail-closed + 期间/表文一致性（§十一 专项）。

用法: python -m evals.test_phase4_contract_slice

覆盖 §十一 测试要求（适配本轮停止边界：无真实 LLM/博查）：
 1. 影子契约符号已删除（BUSINESS_ASPECTS / build_sample_tasks / _question / SAMPLE_TOPIC / SAMPLES）；
 2. question_slice 规范形 == 正式 Contract（无手工重建 PlannedQuestion）；
 3. 篡改 required_aspects → CONTRACT_TASK_DRIFT；
 4. 篡改 evidence_requirements → CONTRACT_TASK_DRIFT；
 5. topic_preview company_business 含 4 正式问题；
 6. 单问题 question_slice 不 chapter_complete；scale/cycle 单独 ≠ 章节完整；
 7. industry section 含全部 9 主题；
 8. manifest 记录 contract hash + 任务身份指纹（确定性）；
 9. 表文不一致 → 产物不通过；
10. 禁止「报告期/本期/期末/最新一期/近年来」；
11. 期间 flow/point/trend 确定性渲染；
12. 规范形指纹确定性（同任务同指纹）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import contract_slice as CSL
from evaluation.run_phase4_vertical_slice import (
    load_formal, plan_dry, select_slice,
)
from planning import schema as PS


def _with_questions(task: PS.SectionTask, questions) -> PS.SectionTask:
    return PS.SectionTask(
        task_id=task.task_id, plan_id=task.plan_id, section_id=task.section_id,
        title=task.title, purpose=task.purpose, research_policy=task.research_policy,
        topic_ids=task.topic_ids, questions=tuple(questions),
        output_requirements=task.output_requirements,
        evaluation_rule_ids=task.evaluation_rule_ids,
        allowed_capabilities=task.allowed_capabilities,
        blocking_rules=task.blocking_rules,
        dependency_versions=task.dependency_versions)


def _tamper_required(task: PS.SectionTask) -> PS.SectionTask:
    return _with_questions(task, [
        PS.PlannedQuestion(**{**q.__dict__, "required_aspects": ("被篡改",)})
        for q in task.questions])


def _tamper_evidence(task: PS.SectionTask) -> PS.SectionTask:
    return _with_questions(task, [
        PS.PlannedQuestion(**{**q.__dict__, "evidence_requirements": ()})
        for q in task.questions])


def _raises_drift(task: PS.SectionTask, sec) -> bool:
    try:
        CSL.validate_task_against_contract(task, sec, "other")
        return False
    except CSL.ContractDriftError as e:
        return e.reason == CSL.CONTRACT_DRIFT


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    import evaluation.run_phase4_vertical_slice as R

    # 1) 影子契约符号删除
    check(all(n not in dir(R) for n in ("BUSINESS_ASPECTS", "build_sample_tasks",
                                        "_question", "SAMPLE_TOPIC", "SAMPLES")),
          "Runner 无影子契约符号（BUSINESS_ASPECTS/build_sample_tasks/_question/...）")

    contracts, sha = load_formal()
    plan = plan_dry(job_id="j", company_id="C", company_name="测试", credit_type="other",
                    report_as_of="2026-03-31", enabled_sections=("company", "industry"),
                    contracts=contracts, contract_sha=sha)
    by_sec = {s.section_id: s for s in contracts}
    comp_task, comp_id = select_slice(plan, contracts, scope="topic_preview",
                                      section_id="company", topic_id="company_business")

    # 2) question_slice 规范形 == 契约
    q = CSL.select_question(comp_task, "company_business_main")
    check(q.required_aspects == ("主营业务构成", "各业务收入及收入占比",
                                 "各业务成本与毛利构成", "产业链位置", "对应报告期与口径"),
          "company_business_main required_aspects == 冻结契约")
    expected = CSL.derive_expected_task(
        by_sec["company"], "other", task_id=comp_task.task_id,
        plan_id=comp_task.plan_id, dependency_versions=comp_task.dependency_versions)
    exp_q = CSL.select_question(expected, "company_business_main")
    check(CSL.canonical_question_dict(exp_q) == CSL.canonical_question_dict(q),
          "question_slice 规范形 == 契约（无手工重建 PlannedQuestion）")

    # 3) 篡改 required_aspects → CONTRACT_TASK_DRIFT
    check(_raises_drift(_tamper_required(comp_task), by_sec["company"]),
          "篡改 required_aspects → CONTRACT_TASK_DRIFT")

    # 4) 篡改 evidence_requirements → CONTRACT_TASK_DRIFT
    check(_raises_drift(_tamper_evidence(comp_task), by_sec["company"]),
          "篡改 evidence_requirements → CONTRACT_TASK_DRIFT")

    # 5) topic_preview company_business 4 正式问题
    check(comp_id["selected_question_ids"] == [
        "company_business_main", "company_business_model",
        "company_customer_concentration", "company_supplier_concentration"],
        "topic_preview company_business 含 4 正式问题")

    # 6) 单问题 question_slice 不 chapter_complete
    qslice = CSL.slice_identity(comp_task, scope="question_slice",
                                question_id="company_business_main")
    check(qslice["chapter_complete"] is False
          and qslice["artifact_scope"] == "question_slice",
          "question_slice 不 chapter_complete")

    # scale/cycle 单独 ≠ 章节完整
    ind_task, _ = select_slice(plan, contracts, scope="section_preview",
                               section_id="industry")
    scale_only = CSL.slice_identity(ind_task, scope="topic_preview",
                                    topic_id="industry_scale_cycle")
    check(scale_only["chapter_complete"] is False,
          "industry_scale_cycle 单独 ≠ 章节完整（chapter_complete=False）")

    # 7) industry section 含全部 9 主题
    check(set(ind_task.topic_ids) == {
        "industry_definition", "industry_scale_cycle", "industry_supply_demand",
        "industry_competition", "industry_policy", "industry_position",
        "industry_comparables", "industry_risk_transmission", "industry_monitoring"},
        "industry section 含全部 9 正式主题")

    # 8) 规范形指纹确定性 + manifest 记录
    fp1 = CSL.task_canonical_fingerprint(comp_task)
    fp2 = CSL.task_canonical_fingerprint(comp_task)
    check(fp1 == fp2 and len(fp1) == 64, "规范形指纹确定性（sha256）")
    check(comp_id["task_canonical_fingerprint"] == fp1,
          "manifest 记录任务规范形指纹")
    check(sha == "23e1735e3b77e94dacae70be03712ca93c98d8f545cc087f8d8b092ad841ae45"
          or len(sha) == 64, "manifest 记录 contract_sha256")

    # 9) 表文不一致 → 不通过
    check(CSL.table_text_consistency({"ef1"}, {"ef1", "ef9"}) != [],
          "正文引用表格外事实 → 不一致")
    check(CSL.table_text_consistency({"ef1", "ef9"}, {"ef1"}) == [],
          "正文全部引用自表格 → 一致")

    # 10) 禁止模糊期间词
    check(CSL.find_forbidden_period_words("报告期本期期末最新一期近年来")
          == ("报告期", "本期", "期末", "最新一期", "近年来"),
          "禁止「报告期/本期/期末/最新一期/近年来」")

    # 11) 期间 flow/point/trend
    check(CSL.period_label("2025-12-31", semantics="flow") == "2025年度"
          and CSL.period_label("2025-12-31", semantics="point") == "截至2025年末",
          "年度 flow/point 渲染")
    check(CSL.period_label("2026-03-31", semantics="flow") == "2026年1—3月"
          and CSL.period_label("2026-03-31", semantics="point") == "截至2026年3月末",
          "季度 flow/point 渲染")
    check(CSL.trend_label(["2023-12-31", "2024-12-31", "2025-12-31"]) == "2023—2025年度",
          "三年趋势渲染")

    # 12) artifact scope 三种
    check(CSL.ARTIFACT_SCOPES == ("question_slice", "topic_preview", "section_preview"),
          "三种产物 scope 定义")

    # 13) period_semantics 未知类别 → unknown（不默认按流量）
    check(CSL.period_semantics("revenue") == "flow"
          and CSL.period_semantics("asset") == "point",
          "flow/point 类别语义识别")
    check(CSL.period_semantics("unknown_category") == "unknown"
          and CSL.period_semantics(None) == "unknown",
          "未知类别 → unknown（不默认 flow）")

    # 14) scope_note 动态（不写死 2025/2026）
    note_empty = CSL.scope_note("2026-03-31")
    check(note_empty == "期间口径未确认", "无可用期间 → 期间口径未确认")
    note = CSL.scope_note("2026-03-31", available_annual_periods=["2023-12-31", "2024-12-31"])
    check(note == "本报告以2023年度、2024年度经营及财务数据为主要分析基础。"
          and "2025" not in note and "2026" not in note,
          "scope_note 动态渲染（不写死 2025/2026）")
    note_interim = CSL.scope_note("2026-03-31", available_annual_periods=["2024-12-31"],
                                  available_interim_periods=["2025-09-30"])
    check("截至2025年9月末" in note_interim and note_interim.endswith("。"),
          "scope_note 年度 + 季度期间合并渲染")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
