"""Eval: Phase 4 纵向研究 Runner 契约派生接线（影子契约已移除）。

用法: python -m evals.test_phase4_vertical_slice

纯组装（无 DB / LLM / 博查）。覆盖：
- 正式契约加载（load_formal）→ contract_sha256；
- plan_dry 派生正式 ReportPlan（company/industry 顺序 + 字段完整）；
- select_slice 漂移校验 + 三种 scope 身份；
- SectionTask 保留全部正式字段（purpose/output_requirements/evaluation_rule_ids/
  allowed_capabilities/blocking_rules/dependency_versions）；
- _self_check 纯组装产出；
- dry-run manifest 记录契约 hash + 任务身份。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.run_phase4_vertical_slice import (
    _self_check, dry_run, load_formal, plan_dry, select_slice,
)
from evaluation import contract_slice as CSL


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

    # 正式契约加载 + 计划
    contracts, sha = load_formal()
    check(len(sha) == 64 and sha, "load_formal 派生 contract_sha256")
    check(len(contracts) >= 3, "load_formal 加载 ≥3 正式章节契约")

    plan = plan_dry(job_id="j", company_id="C", company_name="测试", credit_type="other",
                    report_as_of="2026-03-31", enabled_sections=("company", "industry"),
                    contracts=contracts, contract_sha=sha)
    check(plan.section_ids() == ["company", "industry"], "正式 plan 章节顺序")
    check(plan.planner_version == "p4-planner-v1", "plan 记录 planner_version")

    # 漂移校验 + 选择
    comp_task, comp_id = select_slice(plan, contracts, scope="topic_preview",
                                      section_id="company", topic_id="company_business")
    check(comp_id["artifact_scope"] == "topic_preview"
          and comp_id["chapter_complete"] is False,
          "company_business topic_preview 不 chapter_complete")
    check(comp_id["total_contract_questions"] == len(comp_task.questions)
          and comp_id["total_contract_topics"] == len(comp_task.topic_ids),
          "topic_preview 总量 = 章节契约总量")

    ind_task, ind_id = select_slice(plan, contracts, scope="section_preview",
                                    section_id="industry")
    check(ind_id["chapter_complete"] is True
          and ind_id["covered_questions"] == ind_task.question_ids(),
          "industry section_preview 全量 chapter_complete")

    q_task, q_id = select_slice(plan, contracts, scope="question_slice",
                                section_id="company", question_id="company_business_main")
    check(q_id["selected_question_ids"] == ["company_business_main"]
          and q_id["chapter_complete"] is False,
          "question_slice 单问题不 chapter_complete")

    # SectionTask 保留全部正式字段（不再空）
    for t in (comp_task, ind_task):
        check(bool(t.title) and bool(t.purpose) and bool(t.research_policy)
              and t.output_requirements and t.evaluation_rule_ids
              and t.allowed_capabilities and t.blocking_rules
              and t.dependency_versions,
              f"SectionTask[{t.section_id}] 保留全部正式字段（非空）")

    # 自检
    sc = _self_check()
    check(sc["shadow_contract_removed"], "_self_check 确认影子契约已删除")
    check(sc["drift_clean"] and sc["drift_tamper_raises"], "_self_check 漂移校验")
    check(sc["business_chapter_ok"], "_self_check business 渲染")
    check(sc["supply_chain_not_fake_obtained"], "_self_check 反虚假覆盖")
    check(sc["table_text_consistent"], "_self_check 表文一致")
    check(sc["company_business_question_ids"] == [
        "company_business_main", "company_business_model",
        "company_customer_concentration", "company_supplier_concentration"],
        "_self_check company_business 4 正式问题")

    # dry-run manifest
    m = dry_run(company_id="C", company_name="测试", industry_names=("测试行业",))
    check(m["contract_sha256"] == sha and m["report_plan_id"]
          and m["contract_version"] == "v1"
          and m["previews"]["company_business_topic_preview"]["task_canonical_fingerprint"],
          "dry-run manifest 记录契约 hash/version/plan_id/任务指纹")
    check(m["query_plan"]["industry_topic_query_plans"].keys()
          == set(ind_task.topic_ids),
          "dry-run 为 industry 全部 9 主题派生 TopicQueryPlan")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
