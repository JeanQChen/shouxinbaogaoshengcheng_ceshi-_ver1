"""Eval: Phase 4 Batch A — Section Store（不可变 + current 指针 + 原子提交）。

不调用 LLM / Embedding / Chroma / 互联网。使用临时 SQLite 文件（非 :memory:，因 Store
per-call connect 语义下 :memory: 每次连接都是空库）。

覆盖：
1. init_db 建齐全部表 + migration 记账。
2. commit_plan 原子写 plan + task + current 指针 + progress。
3. 幂等复用（同 plan 重复 commit 不重写、不切指针）。
4. 冲突 fail-closed（同 plan_id 不同指纹抛错）。
5. current 指针切换（同 job 换新 plan）。
6. migration append-only（重复 init 不追加、不破坏）。
7. 库隔离（不同文件不互染）。

用法: python -m evals.test_section_store
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planning import schema as PS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _task(task_id, plan_id, section_id):
    return PS.SectionTask(
        task_id=task_id, plan_id=plan_id, section_id=section_id,
        title=f"{section_id} 标题", purpose="purpose",
        research_policy="harness", topic_ids=("t1",), questions=(),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions={},
    )


def _plan(plan_id, job_id, input_fp, contract_fp="fp_c", company_id="300750"):
    return PS.ReportPlan(
        plan_id=plan_id, job_id=job_id, company_id=company_id,
        company_name="宁德时代", credit_type="other", report_as_of="2026-03-31",
        template_id="standard_v2", input_fingerprint=input_fp,
        contract_fingerprint=contract_fp, planner_version=PS.PLANNER_VERSION,
        section_tasks=(
            _task(PS.derive_task_id(plan_id, "company"), plan_id, "company"),
            _task(PS.derive_task_id(plan_id, "financial"), plan_id, "financial"),
            _task(PS.derive_task_id(plan_id, "industry"), plan_id, "industry"),
        ),
        created_at="2026-01-01T00:00:00Z",
    )


def main():
    tmpdir = Path(tempfile.mkdtemp(prefix="sections_store_test_"))
    db = tmpdir / "sections.db"

    # 1. init_db 建齐全部表 + migration 记账
    ST.init_db(db)
    conn = ST._get_conn()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    missing = [t for t in ST.EXPECTED_TABLES if t not in tables]
    check(not missing, f"init_db 应建齐所有表，缺失: {missing}")
    check("schema_migrations" in tables, "应有 schema_migrations 表")

    # 2. commit_plan 原子写 plan + tasks + current 指针
    pA = _plan("plan_A", "job_1", "fp_a")
    r1 = ST.commit_plan(pA, run_id="run_1")
    check(r1.reused is False and r1.current_switched is True,
          "首次 commit 不 reuse 且切 current")
    check(ST.get_current_plan("job_1") == "plan_A", "current 指针指向 plan_A")
    got = ST.get_plan("plan_A")
    check(got is not None and got.plan_id == "plan_A", "get_plan 往返")
    check(len(ST.list_tasks("plan_A")) == 3, "list_tasks 返回 3 任务")
    check(ST.get_task(PS.derive_task_id("plan_A", "company")).section_id == "company",
          "get_task 往返")
    check(ST.list_plans("300750") == ["plan_A"], "list_plans 按 company 过滤")

    # 3. 幂等复用
    r2 = ST.commit_plan(pA, run_id="run_2")
    check(r2.reused is True and r2.current_switched is False,
          "同 plan 重复 commit 应 reuse 且不切 current")
    conn = ST._get_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM section_plan WHERE plan_id='plan_A'").fetchone()["c"]
    finally:
        conn.close()
    check(n == 1, "重复 commit 不产生重复 plan 行")

    # 4. 冲突 fail-closed：同 plan_id 不同指纹
    pA_conflict = _plan("plan_A", "job_1", "fp_different")
    try:
        ST.commit_plan(pA_conflict)
        check(False, "同 plan_id 不同指纹应抛错")
    except ValueError:
        check(True, "同 plan_id 不同指纹抛错（fail-closed）")
    check(ST.get_current_plan("job_1") == "plan_A", "冲突回滚后 current 仍指向 plan_A")

    # 5. current 指针切换：同 job 换新 plan
    pB = _plan("plan_B", "job_1", "fp_b")
    r3 = ST.commit_plan(pB, run_id="run_3")
    check(r3.current_switched is True, "换 plan_B 切 current")
    check(ST.get_current_plan("job_1") == "plan_B", "current 指针切到 plan_B")
    check(ST.get_plan("plan_A") is not None, "旧 plan_A 仍可读（不可变历史）")

    # 6. migration append-only：重复 init 不追加、不破坏
    ST.init_db(db)
    conn = ST._get_conn()
    try:
        mig = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
    finally:
        conn.close()
    check(mig == 1, "重复 init 不追加 migration（迁移仍为 1）")

    # 7. 隔离：新库不互染
    db2 = tmpdir / "sections2.db"
    ST.init_db(db2)
    check(ST.get_current_plan("job_1") is None, "新库无 job_1 current（隔离）")
    check(ST.get_plan("plan_A") is None, "新库无 plan_A（隔离）")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
