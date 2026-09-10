"""Section Store：Phase 4 章节产物不可变存储 + current 指针 + 原子提交。

- 独立库 data/sections.db，与 credit.db / evidence.db / financial_v2.db 分离。
- 不可变历史：plan / task / section_result / claim / citation / unresolved / evaluation /
  rework 全部 append-only，不覆盖、不物理删除。
- current 指针（current_plan / current_section）只在完整验证并原子提交后切换。
- 原子提交：commit_plan 把 plan + task + current_plan 指针 + progress 放在同一事务，
  失败回滚，不留半个 current plan。
- SQLite migration 只追加（schema_migrations 记账），禁止重写历史 migration。
- 连接模式与 evidence/store.py 一致：per-call connect/close，PRAGMA foreign_keys=ON。

Batch A 只实现 plan / task / current_plan / progress 的写入与读取；section_result / claim /
citation / unresolved / evaluation / rework / current_section 的 DDL 一并建齐（任务书 §8.3
要求 Store 至少包含这些表），写入器在 P4-B/C/D 接入，后续只增不改。

CLI：python -m sections.store --db data/sections.db --init | --summary
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from planning import schema as PS
from sections import schema as SS

DEFAULT_DB_PATH = Path("data/sections.db")

_db_path: Path = DEFAULT_DB_PATH


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id() -> str:
    return f"p4evt_{uuid.uuid4().hex[:12]}"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# DDL（migration 1 历史 DDL，内容与 v1 完全一致，仅按语句边界拆分，不改语义）
# ---------------------------------------------------------------------------

_MIGRATION_1_DDL = """
    CREATE TABLE IF NOT EXISTS section_plan (
        plan_id             TEXT PRIMARY KEY,
        job_id              TEXT NOT NULL,
        company_id          TEXT NOT NULL,
        company_name        TEXT NOT NULL,
        credit_type         TEXT NOT NULL,
        report_as_of        TEXT NOT NULL,
        template_id         TEXT NOT NULL,
        input_fingerprint   TEXT NOT NULL,
        contract_fingerprint TEXT NOT NULL,
        planner_version     TEXT NOT NULL,
        created_at          TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS section_task (
        task_id             TEXT PRIMARY KEY,
        plan_id             TEXT NOT NULL REFERENCES section_plan(plan_id),
        section_id          TEXT NOT NULL,
        ordinal             INTEGER NOT NULL,
        title               TEXT NOT NULL,
        purpose             TEXT NOT NULL,
        research_policy     TEXT NOT NULL,
        task_schema_version TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_task_plan ON section_task(plan_id, ordinal);

    CREATE TABLE IF NOT EXISTS current_plan (
        job_id              TEXT PRIMARY KEY,
        company_id          TEXT NOT NULL,
        plan_id             TEXT NOT NULL,
        switched_at         TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS section_result (
        section_result_id   TEXT PRIMARY KEY,
        section_version     TEXT NOT NULL,
        task_id             TEXT NOT NULL REFERENCES section_task(task_id),
        section_id          TEXT NOT NULL,
        status              TEXT NOT NULL,
        section_ordinal     INTEGER NOT NULL,
        payload_json        TEXT NOT NULL,
        created_at          TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_result_task ON section_result(task_id);

    CREATE TABLE IF NOT EXISTS section_claim (
        claim_id            TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        section_id          TEXT NOT NULL,
        topic_id            TEXT NOT NULL,
        question_ids_json   TEXT NOT NULL,
        text                TEXT NOT NULL,
        claim_type          TEXT NOT NULL,
        citation_refs_json  TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_claim_result ON section_claim(section_result_id);

    CREATE TABLE IF NOT EXISTS section_citation (
        citation_id         TEXT PRIMARY KEY,
        claim_id            TEXT NOT NULL REFERENCES section_claim(claim_id),
        section_result_id   TEXT NOT NULL,
        ref_type            TEXT NOT NULL,
        evidence_id         TEXT,
        snapshot_id         TEXT,
        item_code           TEXT,
        formula_id          TEXT,
        formula_version     TEXT,
        period              TEXT,
        source_snapshot_id  TEXT,
        page_number         INTEGER,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_citation_claim ON section_citation(claim_id);

    CREATE TABLE IF NOT EXISTS section_unresolved (
        unresolved_id       TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        section_id          TEXT NOT NULL,
        topic_id            TEXT NOT NULL,
        question_id         TEXT,
        state               TEXT NOT NULL,
        reason_code         TEXT NOT NULL,
        detail              TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_unresolved_result ON section_unresolved(section_result_id);

    CREATE TABLE IF NOT EXISTS section_evaluation (
        evaluation_id       TEXT PRIMARY KEY,
        section_result_id   TEXT NOT NULL REFERENCES section_result(section_result_id),
        rules_version       TEXT NOT NULL,
        evaluator_prompt_version TEXT NOT NULL,
        rules_passed        INTEGER NOT NULL,
        llm_passed          INTEGER,
        decision            TEXT NOT NULL,
        payload_json        TEXT NOT NULL,
        evaluated_at        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_eval_result ON section_evaluation(section_result_id);

    CREATE TABLE IF NOT EXISTS section_rework (
        rework_id           TEXT PRIMARY KEY,
        evaluation_id       TEXT NOT NULL REFERENCES section_evaluation(evaluation_id),
        target_kind         TEXT NOT NULL,
        target_ref          TEXT NOT NULL,
        reason              TEXT NOT NULL,
        payload_json        TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_section_rework_eval ON section_rework(evaluation_id);

    CREATE TABLE IF NOT EXISTS current_section (
        task_id             TEXT PRIMARY KEY,
        section_id          TEXT NOT NULL,
        section_result_id   TEXT NOT NULL,
        switched_at         TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS progress (
        event_id            TEXT PRIMARY KEY,
        job_id              TEXT NOT NULL,
        run_id              TEXT NOT NULL,
        stage               TEXT NOT NULL,
        status              TEXT NOT NULL,
        detail              TEXT,
        created_at          TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_progress_job ON progress(job_id, created_at);
    """


def _split_statements(script: str) -> list[str]:
    """按 ';' 把 DDL 脚本拆为单条语句（migration 1 的 DDL 无字符串内分号，拆分安全）。"""
    return [s.strip() for s in script.split(";") if s.strip()]


# 历史事实表（append-only，禁止 UPDATE/DELETE）；current_plan/current_section 是可切换
# 指针，不加不可变触发器。
_IMMUTABLE_TABLES = (
    "section_plan", "section_task", "section_result", "section_claim",
    "section_citation", "section_unresolved", "section_evaluation",
    "section_rework", "progress",
)


def _no_update_trigger(table: str) -> str:
    return (f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} 是不可变历史表，禁止 UPDATE'); END")


def _no_delete_trigger(table: str) -> str:
    return (f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} 是不可变历史表，禁止 DELETE'); END")


def _migration_2_statements() -> list[str]:
    """migration 2：不可变触发器 + 缺失外键（真正的追加迁移，表交换保留行）。

    - 9 个历史事实表加 BEFORE UPDATE / BEFORE DELETE 触发器；
    - current_plan / current_section / section_citation 用「建新表 → 拷贝 → 删旧 →
      改名」补外键（SQLite 不支持 ALTER ADD CONSTRAINT，只能表交换）；
    - 迁移后 foreign_key_check 由 _apply_migration 统一校验。
    """
    stmts: list[str] = []
    # 1) 非重建历史表的不可变触发器（section_citation 在表交换后单独建）
    for t in _IMMUTABLE_TABLES:
        if t == "section_citation":
            continue
        stmts.append(_no_update_trigger(t))
        stmts.append(_no_delete_trigger(t))
    # 2) current_plan 补外键 plan_id → section_plan（可切换指针，不加不可变触发器）
    stmts.extend([
        "CREATE TABLE current_plan_new (job_id TEXT PRIMARY KEY, company_id TEXT NOT NULL, "
        "plan_id TEXT NOT NULL REFERENCES section_plan(plan_id), switched_at TEXT NOT NULL)",
        "INSERT INTO current_plan_new (job_id, company_id, plan_id, switched_at) "
        "SELECT job_id, company_id, plan_id, switched_at FROM current_plan",
        "DROP TABLE current_plan",
        "ALTER TABLE current_plan_new RENAME TO current_plan",
    ])
    # 3) current_section 补外键 section_result_id → section_result
    stmts.extend([
        "CREATE TABLE current_section_new (task_id TEXT PRIMARY KEY, section_id TEXT NOT NULL, "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "switched_at TEXT NOT NULL)",
        "INSERT INTO current_section_new (task_id, section_id, section_result_id, switched_at) "
        "SELECT task_id, section_id, section_result_id, switched_at FROM current_section",
        "DROP TABLE current_section",
        "ALTER TABLE current_section_new RENAME TO current_section",
    ])
    # 4) section_citation 补外键 section_result_id → section_result（表交换 + 索引 + 触发器）
    stmts.extend([
        "CREATE TABLE section_citation_new (citation_id TEXT PRIMARY KEY, "
        "claim_id TEXT NOT NULL REFERENCES section_claim(claim_id), "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "ref_type TEXT NOT NULL, evidence_id TEXT, snapshot_id TEXT, item_code TEXT, "
        "formula_id TEXT, formula_version TEXT, period TEXT, source_snapshot_id TEXT, "
        "page_number INTEGER, payload_json TEXT NOT NULL)",
        "INSERT INTO section_citation_new (citation_id, claim_id, section_result_id, ref_type, "
        "evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT citation_id, claim_id, section_result_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_new RENAME TO section_citation",
        "CREATE INDEX idx_section_citation_claim ON section_citation(claim_id)",
        _no_update_trigger("section_citation"),
        _no_delete_trigger("section_citation"),
    ])
    return stmts


# 每个 migration 是「有序 SQL 语句列表」，在单事务内逐条执行；失败整体回滚（不留半迁移）。
MIGRATIONS: list[list[str]] = [
    _split_statements(_MIGRATION_1_DDL),
    _migration_2_statements(),
]

# Store 应有表（§8.3），供 self-check / 测试断言
EXPECTED_TABLES = (
    "section_plan", "section_task", "current_plan",
    "section_result", "section_claim", "section_citation", "section_unresolved",
    "section_evaluation", "section_rework", "current_section", "progress",
)


def _valid_prefix(applied: list[int]) -> bool:
    """已应用 migration 必须是 [1..N] 连续前缀，否则拒绝追加（防乱序/半迁移）。"""
    return applied == list(range(1, len(applied) + 1))


def _apply_migration(conn: sqlite3.Connection, migration_id: int,
                     statements: list[str]) -> None:
    """单事务执行一个 migration 的全部语句，foreign_key_check 通过才 COMMIT，失败整体回滚。"""
    try:
        conn.execute("BEGIN")
        for stmt in statements:
            conn.execute(stmt)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"migration {migration_id} 外键校验失败：{len(violations)} 处违例，已回滚")
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (migration_id, _utcnow()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate(conn: sqlite3.Connection) -> None:
    """追加式 migration：先校验历史是合法前缀，再逐条执行缺失 migration。

    - 已应用历史不是 [1..N] 连续前缀 → 抛错拒绝（防半迁移/乱序）。
    - 每个 migration 单事务执行，失败回滚；重复调用幂等（已应用则跳过）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(migration_id INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = [row["migration_id"]
               for row in conn.execute(
                   "SELECT migration_id FROM schema_migrations ORDER BY migration_id")]
    if not _valid_prefix(applied):
        raise sqlite3.IntegrityError(
            f"schema_migrations 历史不是合法前缀: {applied}（拒绝追加，防半迁移）")
    for i, statements in enumerate(MIGRATIONS, start=1):
        if i in applied:
            continue
        _apply_migration(conn, i, statements)


def init_db(db_path: str | Path | None = None) -> None:
    global _db_path
    if db_path is not None:
        _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    try:
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 写入：progress + 原子 commit_plan
# ---------------------------------------------------------------------------

def record_progress(job_id: str, run_id: str, stage: str, status: str,
                    detail: str = "") -> str:
    """追加一条 progress 事件（append-only）。"""
    event_id = _event_id()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, job_id, run_id, stage, status, detail, _utcnow()))
        conn.commit()
    finally:
        conn.close()
    return event_id


def commit_plan(plan: PS.ReportPlan, *, run_id: str = "") -> SS.CommitResult:
    """原子提交 plan + 全部 task + current_plan 指针 + PLANNED progress。

    - 幂等复用：同 plan_id + 同输入/契约指纹 → 返回 reused=True，不重写、不切指针。
    - 冲突 fail-closed：同 plan_id 但指纹不同 → 抛错，回滚。
    - 任意异常 → 回滚，不留半个 current plan。
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_id, input_fingerprint, contract_fingerprint FROM section_plan "
            "WHERE plan_id = ?", (plan.plan_id,)).fetchone()
        if row is not None:
            conn.rollback()
            if (row["job_id"] == plan.job_id
                    and row["input_fingerprint"] == plan.input_fingerprint
                    and row["contract_fingerprint"] == plan.contract_fingerprint):
                return SS.CommitResult(plan_id=plan.plan_id, reused=True,
                                       current_switched=False,
                                       task_count=len(plan.section_tasks))
            raise ValueError(
                f"plan_id 冲突: {plan.plan_id} 已存在但 job/输入/契约指纹不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_plan (plan_id, job_id, company_id, company_name, credit_type, "
            "report_as_of, template_id, input_fingerprint, contract_fingerprint, planner_version, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (plan.plan_id, plan.job_id, plan.company_id, plan.company_name, plan.credit_type,
             plan.report_as_of, plan.template_id, plan.input_fingerprint,
             plan.contract_fingerprint, plan.planner_version, plan.created_at))

        for ordinal, task in enumerate(plan.section_tasks):
            conn.execute(
                "INSERT INTO section_task (task_id, plan_id, section_id, ordinal, title, purpose, "
                "research_policy, task_schema_version, payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (task.task_id, task.plan_id, task.section_id, ordinal, task.title, task.purpose,
                 task.research_policy, PS.TASK_SCHEMA_VERSION,
                 json.dumps(PS.section_task_to_dict(task), ensure_ascii=False)))

        before = conn.execute("SELECT plan_id FROM current_plan WHERE job_id = ?",
                              (plan.job_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_plan (job_id, company_id, plan_id, switched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "company_id=excluded.company_id, plan_id=excluded.plan_id, "
            "switched_at=excluded.switched_at",
            (plan.job_id, plan.company_id, plan.plan_id, plan.created_at))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), plan.job_id, run_id, "planning", "PLANNED",
             f"plan_id={plan.plan_id}", plan.created_at))

        conn.commit()
        switched = (before is None or before["plan_id"] != plan.plan_id)
        return SS.CommitResult(plan_id=plan.plan_id, reused=False,
                               current_switched=switched,
                               task_count=len(plan.section_tasks))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _load_tasks(conn: sqlite3.Connection, plan_id: str) -> list[PS.SectionTask]:
    rows = conn.execute(
        "SELECT payload_json FROM section_task WHERE plan_id = ? ORDER BY ordinal",
        (plan_id,)).fetchall()
    return [PS.section_task_from_dict(json.loads(r["payload_json"])) for r in rows]


def get_plan(plan_id: str) -> PS.ReportPlan | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM section_plan WHERE plan_id = ?",
                           (plan_id,)).fetchone()
        if row is None:
            return None
        tasks = _load_tasks(conn, plan_id)
        return PS.ReportPlan(
            plan_id=row["plan_id"],
            job_id=row["job_id"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            credit_type=row["credit_type"],
            report_as_of=row["report_as_of"],
            template_id=row["template_id"],
            input_fingerprint=row["input_fingerprint"],
            contract_fingerprint=row["contract_fingerprint"],
            planner_version=row["planner_version"],
            section_tasks=tuple(tasks),
            created_at=row["created_at"],
        )
    finally:
        conn.close()


def list_tasks(plan_id: str) -> list[PS.SectionTask]:
    conn = _get_conn()
    try:
        return _load_tasks(conn, plan_id)
    finally:
        conn.close()


def get_task(task_id: str) -> PS.SectionTask | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT payload_json FROM section_task WHERE task_id = ?",
                           (task_id,)).fetchone()
        if row is None:
            return None
        return PS.section_task_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def get_current_plan(job_id: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT plan_id FROM current_plan WHERE job_id = ?",
                           (job_id,)).fetchone()
        return None if row is None else row["plan_id"]
    finally:
        conn.close()


def list_plans(company_id: str) -> list[str]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT plan_id FROM section_plan WHERE company_id = ? ORDER BY created_at",
            (company_id,)).fetchall()
        return [r["plan_id"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# self-check / summary
# ---------------------------------------------------------------------------

def _summary() -> dict:
    conn = _get_conn()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for t in ("section_plan", "section_task", "current_plan", "progress"):
            counts[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        return {
            "expected_tables_present": [t for t in EXPECTED_TABLES if t in tables],
            "missing_tables": [t for t in EXPECTED_TABLES if t not in tables],
            "migration_count": conn.execute(
                "SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"],
            "row_counts": counts,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.store",
        description="Section Store 初始化 / 摘要")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    init_db(args.db)
    print(f"已初始化: {args.db}")
    if args.init or args.summary:
        print(json.dumps(_summary(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
