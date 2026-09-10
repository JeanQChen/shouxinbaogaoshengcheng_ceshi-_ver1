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
8. 跨 job 隔离（不同 job 同输入不冲突、各自 current、get_plan 返回正确 job_id、切换不互扰）。

用法: python -m evals.test_section_store
"""

from __future__ import annotations

import sqlite3
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
    check(mig == len(ST.MIGRATIONS), f"重复 init 不追加 migration（迁移仍为 {len(ST.MIGRATIONS)}）")

    # 7. 隔离：新库不互染
    db2 = tmpdir / "sections2.db"
    ST.init_db(db2)
    check(ST.get_current_plan("job_1") is None, "新库无 job_1 current（隔离）")
    check(ST.get_plan("plan_A") is None, "新库无 plan_A（隔离）")

    # 8. 跨 job 隔离：不同 job 同业务输入 → 各自 plan，互不冲突
    p_c1 = _plan("plan_c1", "job_c1", "fp_same")
    p_c2 = _plan("plan_c2", "job_c2", "fp_same")
    r1 = ST.commit_plan(p_c1, run_id="run_c1")
    r2 = ST.commit_plan(p_c2, run_id="run_c2")
    check(r1.reused is False and r2.reused is False,
          "不同 job 同输入不冲突（各自首次提交）")
    check(ST.get_current_plan("job_c1") == "plan_c1", "job_c1 current → plan_c1")
    check(ST.get_current_plan("job_c2") == "plan_c2", "job_c2 current → plan_c2")
    check(ST.get_plan("plan_c1").job_id == "job_c1", "get_plan(plan_c1).job_id == job_c1")
    check(ST.get_plan("plan_c2").job_id == "job_c2", "get_plan(plan_c2).job_id == job_c2")

    # 9. 同 job 同输入严格复用
    r3 = ST.commit_plan(p_c1, run_id="run_c1_again")
    check(r3.reused is True and r3.current_switched is False,
          "同 job 同输入重复提交 reuse")

    # 10. 切换 job_c1 的 plan 不影响 job_c2
    p_c1_v2 = _plan("plan_c1_v2", "job_c1", "fp_v2")
    r4 = ST.commit_plan(p_c1_v2, run_id="run_c1_v2")
    check(r4.current_switched is True, "job_c1 切换新 plan")
    check(ST.get_current_plan("job_c1") == "plan_c1_v2", "job_c1 current 切到新 plan")
    check(ST.get_current_plan("job_c2") == "plan_c2", "job_c2 current 不受影响")
    check(ST.get_plan("plan_c1").job_id == "job_c1", "旧 plan_c1 仍可读且 job_id 正确")

    # 11. 历史表不可变：UPDATE / DELETE 被触发器拒绝（current 指针不在其中）
    conn = ST._get_conn()
    try:
        for table, where in [
            ("section_plan", "plan_id='plan_c1'"),
            ("section_task", "plan_id='plan_c1'"),
            ("progress", "job_id='job_c1'"),
        ]:
            try:
                conn.execute(f"UPDATE {table} SET created_at='tampered' WHERE {where}")
                check(False, f"{table} 应拒绝 UPDATE（不可变）")
            except sqlite3.Error:
                check(True, f"{table} 拒绝 UPDATE（不可变）")
            try:
                conn.execute(f"DELETE FROM {table} WHERE {where}")
                check(False, f"{table} 应拒绝 DELETE（不可变）")
            except sqlite3.Error:
                check(True, f"{table} 拒绝 DELETE（不可变）")
    finally:
        conn.close()

    # 12. current 指针仍可原子切换（已由 #5/#10 覆盖；这里验证 UPSERT 不被触发器拦）
    p_c2_v2 = _plan("plan_c2_v2", "job_c2", "fp_v2")
    r5 = ST.commit_plan(p_c2_v2, run_id="run_c2_v2")
    check(r5.current_switched is True, "job_c2 也可切换 plan（current 无不可变触发器）")

    # 13. 外键补齐：三张表在 schema 中带目标外键
    conn = ST._get_conn()
    try:
        def _fks(table):
            return {(r["from"], r["table"])
                    for r in conn.execute(f"PRAGMA foreign_key_list({table})")}
        check(("plan_id", "section_plan") in _fks("current_plan"),
              "current_plan 有 plan_id → section_plan 外键")
        check(("section_result_id", "section_result") in _fks("current_section"),
              "current_section 有 section_result_id → section_result 外键")
        check(("section_result_id", "section_result") in _fks("section_citation"),
              "section_citation 有 section_result_id → section_result 外键")
    finally:
        conn.close()

    # 14. 外键强制：current_plan 引用不存在的 plan 被拒（fail-closed）
    conn = ST._get_conn()
    try:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO current_plan (job_id, company_id, plan_id, switched_at) "
                "VALUES ('job_ghost', '300750', 'plan_does_not_exist', '2026-01-01T00:00:00Z')")
            conn.commit()
            check(False, "current_plan 引用不存在的 plan 应被外键拒绝")
        except sqlite3.IntegrityError:
            conn.rollback()
            check(True, "current_plan 外键拒绝非法 plan_id")
    finally:
        conn.close()

    # 15. migration 历史是合法前缀 [1..N]
    conn = ST._get_conn()
    try:
        ids = [r["migration_id"] for r in conn.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
        check(ids == list(range(1, len(ST.MIGRATIONS) + 1)),
              f"schema_migrations 是合法前缀 [1..{len(ST.MIGRATIONS)}]")
    finally:
        conn.close()

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
