"""Eval: Phase 4 Batch A — 确定性章节 Planner。

不调用 LLM / Embedding / Chroma / 互联网 / 生产库。

覆盖：
1. other 授信类型 → 3 任务（company/financial/industry），顺序固定。
2. working_capital 追加 fin_working_capital_needs 主题；other 不追加。
3. 确定性：同输入两次 → 同 plan_id / task_ids / 顺序（与 created_at 无关）。
4. 不同授信类型 → 不同 plan_id（输入指纹变化）。
5. job_id 纳入 plan 身份：不同 job 同输入 → 不同 plan_id；同 job 同输入 → 相同。
6. task_id 派生确定性。
7. company 任务携带 company_subject_match 问题 / company_identity 主题。
8. 契约指纹确定性（同文件字节）。
9. fail-closed：未知授信类型 / 含 synthesizer / 空 enabled_sections。

用法: python -m evals.test_report_planner
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contracts.loader import load_contracts  # noqa: E402
from planning import schema as PS  # noqa: E402
from planning.report_planner import contract_file_fingerprint, plan  # noqa: E402

STD = ROOT / "templates" / "contracts" / "standard_v2.yaml"

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _load():
    return load_contracts(str(STD)), contract_file_fingerprint(str(STD))


def _job(credit_type="other", enabled=("company", "financial", "industry"),
         company_id="300750", company_name="宁德时代", report_as_of="2026-03-31",
         job_id="job_A", ev_fp="ev_fp_000", snap="snap_000"):
    return PS.ReportJobInput(
        job_id=job_id, company_id=company_id, company_name=company_name,
        credit_type=credit_type, report_as_of=report_as_of,
        enabled_sections=tuple(enabled),
        evidence_inventory_fingerprint=ev_fp,
        financial_snapshot_id=snap,
    )


def main():
    contracts, fp = _load()

    # 1. other → 3 任务，顺序 company/financial/industry
    p = plan(_job("other"), contracts, fp, now="2026-01-01T00:00:00Z")
    check([t.section_id for t in p.section_tasks] == ["company", "financial", "industry"],
          "other 授信类型任务顺序应为 company/financial/industry")
    check(all(t.section_id not in ("synthesizer", "project") for t in p.section_tasks),
          "不得规划 synthesizer/project 任务")

    # 2. working_capital 追加 fin_working_capital_needs 主题；other 不追加
    p_wc = plan(_job("working_capital"), contracts, fp, now="2026-01-01T00:00:00Z")
    fin_wc = next(t for t in p_wc.section_tasks if t.section_id == "financial")
    fin_other = next(t for t in p.section_tasks if t.section_id == "financial")
    check("fin_working_capital_needs" in set(fin_wc.topic_ids),
          "working_capital 应包含 fin_working_capital_needs 主题")
    check("fin_working_capital_needs" not in set(fin_other.topic_ids),
          "other 不应包含 fin_working_capital_needs 主题")

    # 3. 确定性：同输入两次（不同 created_at）→ 同 plan_id / task_ids
    p2 = plan(_job("other"), contracts, fp, now="2026-02-02T00:00:00Z")
    check(p.plan_id == p2.plan_id, "同输入（不同 created_at）应产生相同 plan_id")
    check(p.task_ids() == p2.task_ids(), "同输入应产生相同 task_ids")

    # 4. 不同授信类型 → 不同 plan_id
    p_wc2 = plan(_job("working_capital"), contracts, fp, now="2026-01-01T00:00:00Z")
    check(p.plan_id != p_wc2.plan_id, "不同 credit_type 应产生不同 plan_id")

    # 5. job_id 纳入 plan 身份：不同 job 同输入 → 不同 plan_id；同 job 同输入 → 相同
    p_jobA = plan(_job("other", job_id="job_A"), contracts, fp, now="2026-01-01T00:00:00Z")
    p_jobB = plan(_job("other", job_id="job_B"), contracts, fp, now="2026-01-01T00:00:00Z")
    check(p_jobA.plan_id != p_jobB.plan_id, "不同 job 同输入应产生不同 plan_id")
    check(p_jobA.task_ids() != p_jobB.task_ids(), "不同 job 同输入应产生不同 task_ids")
    p_jobA2 = plan(_job("other", job_id="job_A"), contracts, fp, now="2026-02-02T00:00:00Z")
    check(p_jobA.plan_id == p_jobA2.plan_id, "同 job 同输入应产生相同 plan_id（严格复用）")

    # 6. task_id 派生确定性
    t1 = next(t for t in p.section_tasks if t.section_id == "company")
    check(t1.task_id == PS.derive_task_id(p.plan_id, "company"), "task_id 派生确定性")

    # 7. company 任务携带问题/主题
    comp = next(t for t in p.section_tasks if t.section_id == "company")
    qids = set(q.question_id for q in comp.questions)
    check("company_subject_match" in qids, "company 任务应包含 company_subject_match 问题")
    check("company_identity" in set(comp.topic_ids), "company 任务应包含 company_identity 主题")

    # 8. 契约指纹确定性
    check(fp == contract_file_fingerprint(str(STD)), "契约指纹确定性")

    # 9. fail-closed：未知授信类型
    try:
        plan(_job("bogus"), contracts, fp)
        check(False, "未知授信类型应抛错")
    except ValueError:
        check(True, "未知授信类型抛错（fail-closed）")

    # 10. fail-closed：enabled_sections 含 synthesizer（Phase 5）
    try:
        plan(_job("other", enabled=("company", "synthesizer")), contracts, fp)
        check(False, "synthesizer 属于 Phase 5，第一阶段不应规划")
    except ValueError:
        check(True, "enabled_sections 含 synthesizer 抛错")

    # 11. fail-closed：空 enabled_sections
    try:
        plan(_job("other", enabled=()), contracts, fp)
        check(False, "空 enabled_sections 应抛错")
    except ValueError:
        check(True, "空 enabled_sections 抛错")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
