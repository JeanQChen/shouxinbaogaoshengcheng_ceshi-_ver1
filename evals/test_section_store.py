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
from harness.schema import CitationRef  # noqa: E402
from sections import schema as SS  # noqa: E402
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


def _result(task_id, section_id, status="DRAFT_READY"):
    secver = SS.derive_section_version(task_id, (), (), renderer_version="rv", rules_version="rv")
    srid = SS.derive_section_result_id(secver)
    return SS.SectionResult(section_result_id=srid, section_version=secver,
                            task_id=task_id, section_id=section_id, status=status,
                            created_at="2026-01-01T00:00:00Z")


def _result_with_claim(task_id, section_id):
    ref = CitationRef(ref_type="evidence", evidence_id="ev1")
    claim = SS.SectionClaim(claim_id="claim_x", section_id=section_id, topic_id="t1",
                            question_ids=("q1",), text="text", claim_type="fact",
                            citation_refs=(ref,))
    secver = SS.derive_section_version(task_id, (claim,), (), renderer_version="rv",
                                       rules_version="rv")
    srid = SS.derive_section_result_id(secver)
    return SS.SectionResult(section_result_id=srid, section_version=secver,
                            task_id=task_id, section_id=section_id,
                            status="COMPLETED_WITH_GAPS", claims=(claim,),
                            created_at="2026-01-01T00:00:00Z")


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

    # ---- Phase 4 Batch D：evaluation / rework / manifest 写读路径 ----

    # 16. commit_section_result（financial）+ commit_evaluation + get_evaluation
    # 注意：step 7 之后 _db_path 已切到 db2，故用 db2 上已提交的 plan_c2_v2 的任务。
    fin_task = PS.derive_task_id("plan_c2_v2", "financial")
    fin_result = _result(fin_task, "financial")
    ST.commit_section_result(fin_result, run_id="run_eval")
    iid = SS.derive_issue_id("rule_missing_citation", "claim_x", "关键 claim 缺引用", "yellow")
    issue = SS.SectionIssue(issue_id=iid, rule_id="rule_missing_citation", severity="yellow",
                            location="claim_x", detail="关键 claim 缺引用")
    tid = SS.derive_rework_target_id("claim", "claim_x", "补引用")
    target = SS.ReworkTarget(target_id=tid, target_kind="claim", target_ref="claim_x",
                             reason="补引用")
    eid = SS.derive_evaluation_id(fin_result.section_result_id, "REWORK", "rules_v1", "eval_v1",
                                  (issue,), (target,), 0)
    ev = SS.SectionEvaluation(evaluation_id=eid, section_result_id=fin_result.section_result_id,
                              rules_version="rules_v1", evaluator_prompt_version="eval_v1",
                              rules_passed=False, llm_passed=None, decision="REWORK",
                              issues=(issue,), rework_targets=(target,),
                              evaluated_at="2026-01-01T00:00:00Z", llm_evaluator_calls=0)
    r_ev = ST.commit_evaluation(ev, run_id="run_eval")
    check(r_ev.reused is False and r_ev.rework_target_count == 1, "commit_evaluation 首次写入")
    got_ev = ST.get_evaluation(fin_result.section_result_id)
    check(got_ev is not None and got_ev.decision == "REWORK" and len(got_ev.issues) == 1,
          "get_evaluation 往返")
    check(len(ST.list_evaluations(fin_result.section_result_id)) == 1, "list_evaluations 返回 1")

    # 17. evaluation 幂等复用
    check(ST.commit_evaluation(ev, run_id="run_eval2").reused is True,
          "同 evaluation 重复提交 reuse")

    # 18. evaluation_id 与内容派生不一致 → fail-closed（内容寻址自洽守卫）
    ev_conflict = SS.SectionEvaluation(
        evaluation_id=eid, section_result_id=fin_result.section_result_id,
        rules_version="rules_v1", evaluator_prompt_version="eval_v1", rules_passed=True,
        llm_passed=True, decision="PASS")
    try:
        ST.commit_evaluation(ev_conflict)
        check(False, "evaluation_id 与内容派生不一致应抛错")
    except ST.SectionStorageConflictError:
        check(True, "evaluation_id 与内容派生不一致 fail-closed")

    # 19. llm_evaluator_calls 上限：>1 fail-closed
    ev_bad = SS.SectionEvaluation(
        evaluation_id="eval_bad", section_result_id=fin_result.section_result_id,
        rules_version="r", evaluator_prompt_version="e", rules_passed=True, llm_passed=True,
        decision="PASS", llm_evaluator_calls=2)
    try:
        ST.commit_evaluation(ev_bad)
        check(False, "llm_evaluator_calls>1 应抛错")
    except ST.SectionStorageConflictError:
        check(True, "llm_evaluator_calls>1 fail-closed")

    # 20. Evaluation 是关联对象：get_section_result 不隐式 join，evaluation 仍为 None
    got_result = ST.get_section_result(fin_result.section_result_id)
    check(got_result is not None and got_result.evaluation is None,
          "get_section_result 不隐式 join evaluation（内容身份不变）")

    # 21. commit_rework_run：新的 section_result（有 claim，不同版本）作为返工后产物
    new_result = _result_with_claim(fin_task, "financial")
    ST.commit_section_result(new_result, run_id="run_rework")
    rrid = SS.derive_rework_run_id(fin_result.section_result_id, new_result.section_result_id, 0)
    rr = SS.SectionReworkRun(rework_run_id=rrid, job_id="job_1",
                             section_result_id=new_result.section_result_id,
                             from_section_result_id=fin_result.section_result_id,
                             evaluation_id=eid, batch_no=0, llm_evaluator_calls=0,
                             targets=(target,), created_at="2026-01-01T00:00:00Z")
    r_rr = ST.commit_rework_run(rr, run_id="run_rework")
    check(r_rr.reused is False, "commit_rework_run 首次写入")
    runs = ST.get_rework_runs(fin_result.section_result_id)
    check(len(runs) == 1 and runs[0].rework_run_id == rrid, "get_rework_runs 往返")

    # 22. rework_run 幂等复用 + batch_no 守卫
    check(ST.commit_rework_run(rr).reused is True, "同 rework_run 重复提交 reuse")
    rr_bad = SS.SectionReworkRun(
        rework_run_id="rr_bad", job_id="job_1", section_result_id=new_result.section_result_id,
        from_section_result_id=fin_result.section_result_id, evaluation_id=eid,
        batch_no=1, llm_evaluator_calls=0)
    try:
        ST.commit_rework_run(rr_bad)
        check(False, "batch_no!=0 应抛错")
    except ST.SectionStorageConflictError:
        check(True, "batch_no!=0 fail-closed")

    # 23. commit_manifest + current_manifest 指针 + 幂等 + 切换
    mid = SS.derive_manifest_id("job_1", "run_eval", "cf", "p3cf", {"planner": "v1"},
                                {"model": "x"})
    mf = SS.SectionRunManifest(manifest_id=mid, job_id="job_1", run_id="run_eval",
                               code_fingerprint="cf", phase3_closure_fingerprint="p3cf",
                               batch_versions={"planner": "v1"}, frozen={"model": "x"},
                               created_at="2026-01-01T00:00:00Z")
    r_mf = ST.commit_manifest(mf)
    check(r_mf.reused is False and r_mf.current_switched is True,
          "commit_manifest 首次写入并切指针")
    check(ST.current_manifest("job_1") == mid, "current_manifest 指向 mid")
    check(ST.get_manifest(mid).run_id == "run_eval", "get_manifest 往返")
    check(ST.commit_manifest(mf).reused is True, "同 manifest 重复提交 reuse")

    mid2 = SS.derive_manifest_id("job_1", "run_eval2", "cf", "p3cf", {"planner": "v1"},
                                 {"model": "x"})
    mf2 = SS.SectionRunManifest(manifest_id=mid2, job_id="job_1", run_id="run_eval2",
                                code_fingerprint="cf", phase3_closure_fingerprint="p3cf",
                                batch_versions={"planner": "v1"}, frozen={"model": "x"},
                                created_at="2026-01-01T00:00:00Z")
    r_mf2 = ST.commit_manifest(mf2)
    check(r_mf2.current_switched is True, "切换 manifest 切 current_manifest")
    check(ST.current_manifest("job_1") == mid2, "current_manifest 切到 mid2")
    check(ST.get_manifest(mid) is not None, "旧 manifest 仍可读（不可变历史）")

    # 24. 新历史表不可变：section_evaluation / section_rework_run / section_run_manifest
    conn = ST._get_conn()
    try:
        for table, where in [
            ("section_evaluation", f"evaluation_id='{eid}'"),
            ("section_rework_run", f"rework_run_id='{rrid}'"),
            ("section_run_manifest", f"manifest_id='{mid}'"),
        ]:
            try:
                conn.execute(f"UPDATE {table} SET payload_json='tampered' WHERE {where}")
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

    # 25. current_manifest 外键：引用不存在的 manifest 被拒（fail-closed）
    conn = ST._get_conn()
    try:
        conn.execute("BEGIN")
        try:
            conn.execute("INSERT INTO current_manifest (job_id, manifest_id, switched_at) "
                         "VALUES ('job_ghost', 'manifest_ghost', '2026-01-01T00:00:00Z')")
            conn.commit()
            check(False, "current_manifest 引用不存在的 manifest 应被外键拒绝")
        except sqlite3.IntegrityError:
            conn.rollback()
            check(True, "current_manifest 外键拒绝非法 manifest_id")
    finally:
        conn.close()

    # ---- Phase 4 修复：migration 4 复合归属键 + 跨版本内容复用 ----

    def _pre_mig4(db_path: Path) -> None:
        """建一个只应用 migration 1-3 的历史库（模拟 migration 4 之前的旧 schema）。"""
        ST._db_path = db_path
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("CREATE TABLE schema_migrations "
                  "(migration_id INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        c.commit()
        for i in (1, 2, 3):
            ST._apply_migration(c, i, ST.MIGRATIONS[i - 1])
        c.close()

    def _counts(db_path: Path) -> dict:
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        out = {t: c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
               for t in ("section_result", "section_claim", "section_citation",
                         "section_unresolved")}
        c.close()
        return out

    # 26. migration 3→4 保留全部历史数据（旧单列主键 → 复合归属键）
    mig_db = tmpdir / "mig4.db"
    _pre_mig4(mig_db)
    p_mig = _plan("plan_mig", "job_mig", "fp_mig")
    ST.commit_plan(p_mig, run_id="run_mig")
    mig_task = PS.derive_task_id("plan_mig", "financial")
    ref_mig = CitationRef(ref_type="evidence", evidence_id="ev_mig", page_number=3)
    claim_mig = SS.SectionClaim(claim_id="claim_mig", section_id="financial", topic_id="t1",
                                question_ids=("q1",), text="text", claim_type="fact",
                                citation_refs=(ref_mig,))
    ur_mig = SS.SectionUnresolved(unresolved_id="ur_mig", section_id="financial", topic_id="t1",
                                  question_id="q1", state="NOT_FOUND_AFTER_SEARCH",
                                  reason_code="r", detail="d")
    secver_mig = SS.derive_section_version(mig_task, (claim_mig,), (ur_mig,),
                                           renderer_version="rv", rules_version="rv")
    res_mig = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_mig),
                               section_version=secver_mig, task_id=mig_task,
                               section_id="financial", status="COMPLETED_WITH_GAPS",
                               claims=(claim_mig,), unresolved=(ur_mig,),
                               created_at="2026-01-01T00:00:00Z")
    ST.commit_section_result(res_mig, run_id="run_mig")
    before = _counts(mig_db)
    ST.init_db(mig_db)  # 触发 migration 4（表交换）
    after = _counts(mig_db)
    check(before["section_claim"] == 1 and before["section_citation"] == 1
          and before["section_unresolved"] == 1, "迁移前各子对象表各有 1 行")
    check(after == before, f"migration 3→4 保留全部历史行（before={before}, after={after}）")

    # 26b. migration 4 复合主键 / 复合外键结构 + foreign_key_check 为空
    conn = ST._get_conn()
    try:
        def _pk(table):
            return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["pk"] > 0}
        def _fks(table):
            return {(r["from"], r["table"]) for r in conn.execute(
                f"PRAGMA foreign_key_list({table})")}
        check(_pk("section_claim") == {"section_result_id", "claim_id"},
              "section_claim 复合主键 (section_result_id, claim_id)")
        check(_pk("section_citation") == {"section_result_id", "citation_id"},
              "section_citation 复合主键 (section_result_id, citation_id)")
        check(_pk("section_unresolved") == {"section_result_id", "unresolved_id"},
              "section_unresolved 复合主键 (section_result_id, unresolved_id)")
        cit_fks = _fks("section_citation")
        check(("claim_id", "section_claim") in cit_fks
              and ("section_result_id", "section_claim") in cit_fks,
              "section_citation 复合外键 (section_result_id, claim_id) → section_claim")
        check(len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 0,
              "migration 4 后 foreign_key_check 为空")
    finally:
        conn.close()

    # 27. 跨版本内容复用：两个不同 section_result 复用同一 claim/citation 内容均能提交
    reuse_db = tmpdir / "mig4_reuse.db"
    ST.init_db(reuse_db)
    p_ru = _plan("plan_reuse", "job_reuse", "fp_reuse")
    ST.commit_plan(p_ru, run_id="run_reuse")
    reuse_task = PS.derive_task_id("plan_reuse", "financial")
    ref_shared = CitationRef(ref_type="evidence", evidence_id="ev_shared", page_number=1)
    shared_claim = SS.SectionClaim(claim_id="claim_shared", section_id="financial",
                                   topic_id="t1", question_ids=("q1",), text="same text",
                                   claim_type="fact", citation_refs=(ref_shared,))
    secver_a = SS.derive_section_version(reuse_task, (shared_claim,), (),
                                         renderer_version="rv", rules_version="rv")
    res_a = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_a),
                             section_version=secver_a, task_id=reuse_task,
                             section_id="financial", status="COMPLETED",
                             claims=(shared_claim,), created_at="2026-01-01T00:00:00Z")
    check(ST.commit_section_result(res_a, run_id="run_reuse").reused is False, "结果 A 提交成功")
    new_claim = SS.SectionClaim(claim_id="claim_new", section_id="financial", topic_id="t1",
                                question_ids=("q2",), text="new text", claim_type="inference",
                                citation_refs=())
    secver_b = SS.derive_section_version(reuse_task, (shared_claim, new_claim), (),
                                         renderer_version="rv", rules_version="rv")
    res_b = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_b),
                             section_version=secver_b, task_id=reuse_task,
                             section_id="financial", status="COMPLETED",
                             claims=(shared_claim, new_claim),
                             created_at="2026-01-01T00:00:00Z")
    r_b = ST.commit_section_result(res_b, run_id="run_reuse")
    check(r_b.reused is False, "结果 B 复用相同 claim 内容不冲突（复合归属键）")
    conn = ST._get_conn()
    try:
        n_claim = conn.execute("SELECT COUNT(*) AS n FROM section_claim "
                               "WHERE claim_id='claim_shared'").fetchone()["n"]
        n_cite = conn.execute("SELECT COUNT(*) AS n FROM section_citation "
                              "WHERE claim_id='claim_shared'").fetchone()["n"]
        vios = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    check(n_claim == 2, f"同一 claim_id 在两个 result 各存一份（got {n_claim}）")
    check(n_cite == 2, f"同一 citation 在两个 result 各存一份（got {n_cite}）")
    check(len(vios) == 0, "跨结果复用后 foreign_key_check 为空")
    check(ST.get_section_result(res_a.section_result_id) is not None, "结果 A 完整可读")
    check(ST.get_section_result(res_b.section_result_id) is not None, "结果 B 完整可读")

    # 28. 同一结果重复 claim_id → validator fail-closed（Store 不去重）
    dup1 = SS.SectionClaim(claim_id="claim_dup", section_id="financial", topic_id="t1",
                           question_ids=("q1",), text="a", claim_type="inference", citation_refs=())
    dup2 = SS.SectionClaim(claim_id="claim_dup", section_id="financial", topic_id="t1",
                           question_ids=("q2",), text="b", claim_type="inference", citation_refs=())
    secver_dup = SS.derive_section_version(reuse_task, (dup1, dup2), (),
                                           renderer_version="rv", rules_version="rv")
    res_dup = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_dup),
                               section_version=secver_dup, task_id=reuse_task,
                               section_id="financial", status="COMPLETED",
                               claims=(dup1, dup2), created_at="2026-01-01T00:00:00Z")
    try:
        ST.commit_section_result(res_dup, run_id="run_dup")
        check(False, "同一结果重复 claim_id 应被 validator 拒绝")
    except ST.SectionStorageConflictError:
        check(True, "同一结果重复 claim_id fail-closed")

    # 28b. 同一结果重复 citation_id（同 claim 两条相同引用）→ validator fail-closed
    cite_dup_ref = CitationRef(ref_type="evidence", evidence_id="ev_dup", page_number=1)
    cite_dup_claim = SS.SectionClaim(claim_id="claim_cdup", section_id="financial", topic_id="t1",
                                     question_ids=("q1",), text="a", claim_type="fact",
                                     citation_refs=(cite_dup_ref, cite_dup_ref))
    secver_cdup = SS.derive_section_version(reuse_task, (cite_dup_claim,), (),
                                            renderer_version="rv", rules_version="rv")
    res_cdup = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_cdup),
                                section_version=secver_cdup, task_id=reuse_task,
                                section_id="financial", status="COMPLETED",
                                claims=(cite_dup_claim,),
                                created_at="2026-01-01T00:00:00Z")
    try:
        ST.commit_section_result(res_cdup, run_id="run_cdup")
        check(False, "同一结果重复 citation_id 应被 validator 拒绝")
    except ST.SectionStorageConflictError:
        check(True, "同一结果重复 citation_id fail-closed")

    # 29. migration 4 故障完整回滚（拷贝阶段复合外键违例 → 回滚，保留旧 schema/数据）
    rb_db = tmpdir / "mig4_rollback.db"
    _pre_mig4(rb_db)
    p_rb = _plan("plan_rb", "job_rb", "fp_rb")
    ST.commit_plan(p_rb, run_id="run_rb")
    rb_task = PS.derive_task_id("plan_rb", "financial")
    ref_a = CitationRef(ref_type="evidence", evidence_id="ev_a")
    claim_a = SS.SectionClaim(claim_id="claim_a", section_id="financial", topic_id="t1",
                              question_ids=("q1",), text="a", claim_type="fact",
                              citation_refs=(ref_a,))
    secver_rb = SS.derive_section_version(rb_task, (claim_a,), (), renderer_version="rv",
                                          rules_version="rv")
    res_rb = SS.SectionResult(section_result_id=SS.derive_section_result_id(secver_rb),
                              section_version=secver_rb, task_id=rb_task,
                              section_id="financial", status="COMPLETED", claims=(claim_a,),
                              created_at="2026-01-01T00:00:00Z")
    ST.commit_section_result(res_rb, run_id="run_rb")
    res_rb_empty = _result(rb_task, "financial", status="COMPLETED")
    ST.commit_section_result(res_rb_empty, run_id="run_rb")
    # 手动插入悬挂 citation：claim_id 指向 res_rb 的 claim_a，但 section_result_id 指向另一结果
    # （旧 schema 单列 claim_id 外键放行；新复合外键会违例 → migration 4 回滚）
    c = sqlite3.connect(str(rb_db))
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("INSERT INTO section_citation (citation_id, claim_id, section_result_id, "
              "ref_type, evidence_id, payload_json) VALUES (?,?,?,?,?,?)",
              ("cite_dangling", "claim_a", res_rb_empty.section_result_id,
               "evidence", "ev_x", "{}"))
    c.commit()
    c.close()
    try:
        ST.init_db(rb_db)
        check(False, "migration 4 遇复合外键违例应失败")
    except sqlite3.IntegrityError:
        check(True, "migration 4 复合外键违例失败")
    c = sqlite3.connect(str(rb_db))
    c.row_factory = sqlite3.Row
    migs = [r["migration_id"] for r in c.execute(
        "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
    claim_n = c.execute("SELECT COUNT(*) AS n FROM section_claim").fetchone()["n"]
    cite_n = c.execute("SELECT COUNT(*) AS n FROM section_citation").fetchone()["n"]
    c.close()
    check(migs == [1, 2, 3], f"migration 4 回滚后 schema_migrations 仍为 [1,2,3]（got {migs}）")
    check(claim_n == 1, "回滚后旧 section_claim 数据保留")
    check(cite_n == 2, f"回滚后旧 section_citation 数据保留（含悬挂行，got {cite_n}）")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
