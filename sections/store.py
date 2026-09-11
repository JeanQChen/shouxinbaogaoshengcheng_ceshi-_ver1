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
from sections import validator as svalidator

DEFAULT_DB_PATH = Path("data/sections.db")

_db_path: Path = DEFAULT_DB_PATH


class SectionStorageConflictError(Exception):
    """章节产物参数冲突：同 section_result_id 但内容/任务/章节不一致，显式报错（不覆盖、不切指针）。"""


class SectionStorageCorruptionError(Exception):
    """章节产物存储损坏：复用前规范化 payload 与自身 section_version/result_id 不一致。"""


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


def _migration_3_statements() -> list[str]:
    """migration 3：返工批次 + 运行清单 + current_manifest 指针（真正追加迁移）。

    - section_rework_run / section_run_manifest 是 append-only 历史事实表（加不可变触发器）；
    - current_manifest 是可切换指针（不加不可变触发器，引用 section_run_manifest 外键）。
    """
    stmts: list[str] = [
        "CREATE TABLE section_rework_run ("
        "rework_run_id TEXT PRIMARY KEY, "
        "job_id TEXT NOT NULL, "
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "from_section_result_id TEXT NOT NULL, "
        "evaluation_id TEXT NOT NULL REFERENCES section_evaluation(evaluation_id), "
        "batch_no INTEGER NOT NULL, "
        "llm_evaluator_calls INTEGER NOT NULL, "
        "targets_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)",
        "CREATE INDEX idx_rework_run_from ON section_rework_run(from_section_result_id)",
        "CREATE TABLE section_run_manifest ("
        "manifest_id TEXT PRIMARY KEY, "
        "job_id TEXT NOT NULL, "
        "run_id TEXT NOT NULL, "
        "code_fingerprint TEXT NOT NULL, "
        "phase3_closure_fingerprint TEXT NOT NULL, "
        "batch_versions_json TEXT NOT NULL, "
        "frozen_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "created_at TEXT NOT NULL)",
        "CREATE INDEX idx_run_manifest_job ON section_run_manifest(job_id)",
        "CREATE TABLE current_manifest ("
        "job_id TEXT PRIMARY KEY, "
        "manifest_id TEXT NOT NULL REFERENCES section_run_manifest(manifest_id), "
        "switched_at TEXT NOT NULL)",
    ]
    stmts.append(_no_update_trigger("section_rework_run"))
    stmts.append(_no_delete_trigger("section_rework_run"))
    stmts.append(_no_update_trigger("section_run_manifest"))
    stmts.append(_no_delete_trigger("section_run_manifest"))
    return stmts


def _migration_4_statements() -> list[str]:
    """migration 4：子对象改复合归属键 (section_result_id, 内容 id)，支持跨版本内容复用。

    问题：section_claim.claim_id / section_citation.citation_id /
    section_unresolved.unresolved_id 曾是全局 PRIMARY KEY 且同时绑定 section_result_id。
    返工 merge() 复用父结果未触及的 claim（claim_id 不变）进新 section_result_id →
    全局主键冲突（IntegrityError: UNIQUE constraint failed: section_claim.claim_id）。

    修复（受控表交换）：
    - section_claim / section_unresolved → 复合主键 (section_result_id, 内容 id)；
    - section_citation → 复合主键 (section_result_id, citation_id) + 复合外键
      (section_result_id, claim_id) → section_claim(section_result_id, claim_id)。
    语义：同一 SectionResult 内重复 content_id → validator fail-closed（Store 不去重）；
    不同 SectionResult 相同内容 → 合法，各行均能完整查询。

    顺序（子表先删，父表后删，规避 SQLite DROP 父表时子表外键悬挂；不依赖 RENAME 外键
    自动改写）：先交换 section_citation（临时去掉 claim 外键）→ 交换 section_claim →
    交换 section_unresolved → 再交换 section_citation（加回复合外键）。任一步失败整体回滚。
    """
    stmts: list[str] = []

    # 1) section_citation 首轮交换：复合主键 (section_result_id, citation_id)，
    #    暂不含 claim 外键（先解除对 section_claim 的单列外键依赖）。
    stmts.extend([
        "CREATE TABLE section_citation_stage ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "citation_id TEXT NOT NULL, "
        "claim_id TEXT NOT NULL, "
        "ref_type TEXT NOT NULL, "
        "evidence_id TEXT, "
        "snapshot_id TEXT, "
        "item_code TEXT, "
        "formula_id TEXT, "
        "formula_version TEXT, "
        "period TEXT, "
        "source_snapshot_id TEXT, "
        "page_number INTEGER, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, citation_id))",
        "INSERT INTO section_citation_stage (section_result_id, citation_id, claim_id, "
        "ref_type, evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT section_result_id, citation_id, claim_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_stage RENAME TO section_citation",
    ])

    # 2) section_claim：复合主键 (section_result_id, claim_id)。
    stmts.extend([
        "CREATE TABLE section_claim_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "claim_id TEXT NOT NULL, "
        "section_id TEXT NOT NULL, "
        "topic_id TEXT NOT NULL, "
        "question_ids_json TEXT NOT NULL, "
        "text TEXT NOT NULL, "
        "claim_type TEXT NOT NULL, "
        "citation_refs_json TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, claim_id))",
        "INSERT INTO section_claim_new (section_result_id, claim_id, section_id, topic_id, "
        "question_ids_json, text, claim_type, citation_refs_json, payload_json) "
        "SELECT section_result_id, claim_id, section_id, topic_id, question_ids_json, text, "
        "claim_type, citation_refs_json, payload_json FROM section_claim",
        "DROP TABLE section_claim",
        "ALTER TABLE section_claim_new RENAME TO section_claim",
        "CREATE INDEX idx_section_claim_result ON section_claim(section_result_id)",
        _no_update_trigger("section_claim"),
        _no_delete_trigger("section_claim"),
    ])

    # 3) section_unresolved：复合主键 (section_result_id, unresolved_id)。
    stmts.extend([
        "CREATE TABLE section_unresolved_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "unresolved_id TEXT NOT NULL, "
        "section_id TEXT NOT NULL, "
        "topic_id TEXT NOT NULL, "
        "question_id TEXT, "
        "state TEXT NOT NULL, "
        "reason_code TEXT NOT NULL, "
        "detail TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, unresolved_id))",
        "INSERT INTO section_unresolved_new (section_result_id, unresolved_id, section_id, "
        "topic_id, question_id, state, reason_code, detail, payload_json) "
        "SELECT section_result_id, unresolved_id, section_id, topic_id, question_id, state, "
        "reason_code, detail, payload_json FROM section_unresolved",
        "DROP TABLE section_unresolved",
        "ALTER TABLE section_unresolved_new RENAME TO section_unresolved",
        "CREATE INDEX idx_section_unresolved_result ON section_unresolved(section_result_id)",
        _no_update_trigger("section_unresolved"),
        _no_delete_trigger("section_unresolved"),
    ])

    # 4) section_citation 二轮交换：加回复合外键
    #    (section_result_id, claim_id) → section_claim(section_result_id, claim_id)。
    stmts.extend([
        "CREATE TABLE section_citation_new ("
        "section_result_id TEXT NOT NULL REFERENCES section_result(section_result_id), "
        "citation_id TEXT NOT NULL, "
        "claim_id TEXT NOT NULL, "
        "ref_type TEXT NOT NULL, "
        "evidence_id TEXT, "
        "snapshot_id TEXT, "
        "item_code TEXT, "
        "formula_id TEXT, "
        "formula_version TEXT, "
        "period TEXT, "
        "source_snapshot_id TEXT, "
        "page_number INTEGER, "
        "payload_json TEXT NOT NULL, "
        "PRIMARY KEY (section_result_id, citation_id), "
        "FOREIGN KEY (section_result_id, claim_id) "
        "REFERENCES section_claim(section_result_id, claim_id))",
        "INSERT INTO section_citation_new (section_result_id, citation_id, claim_id, ref_type, "
        "evidence_id, snapshot_id, item_code, formula_id, formula_version, period, "
        "source_snapshot_id, page_number, payload_json) "
        "SELECT section_result_id, citation_id, claim_id, ref_type, evidence_id, snapshot_id, "
        "item_code, formula_id, formula_version, period, source_snapshot_id, page_number, "
        "payload_json FROM section_citation",
        "DROP TABLE section_citation",
        "ALTER TABLE section_citation_new RENAME TO section_citation",
        "CREATE INDEX idx_section_citation_claim ON section_citation(claim_id)",
        "CREATE INDEX idx_section_citation_result ON section_citation(section_result_id)",
        _no_update_trigger("section_citation"),
        _no_delete_trigger("section_citation"),
    ])

    return stmts


# 每个 migration 是「有序 SQL 语句列表」，在单事务内逐条执行；失败整体回滚（不留半迁移）。
MIGRATIONS: list[list[str]] = [
    _split_statements(_MIGRATION_1_DDL),
    _migration_2_statements(),
    _migration_3_statements(),
    _migration_4_statements(),
]

# Store 应有表（§8.3），供 self-check / 测试断言
EXPECTED_TABLES = (
    "section_plan", "section_task", "current_plan",
    "section_result", "section_claim", "section_citation", "section_unresolved",
    "section_evaluation", "section_rework", "current_section", "progress",
    "section_rework_run", "section_run_manifest", "current_manifest",
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
# 章节产物写入：commit_section_result（P4-B 接入，原子提交 + current_section 切换）
# ---------------------------------------------------------------------------

def _citation_id(claim_id: str, ref) -> str:
    """citation_id 稳定派生（委托 schema.derive_citation_id，单一来源，避免二次硬编码）。"""
    return SS.derive_citation_id(claim_id, ref)


def _job_id_for_task(conn: sqlite3.Connection, task_id: str) -> str:
    row = conn.execute(
        "SELECT p.job_id FROM section_task t "
        "JOIN section_plan p ON p.plan_id = t.plan_id WHERE t.task_id = ?",
        (task_id,)).fetchone()
    return row["job_id"] if row is not None else ""


def _normalized_payload(result: SS.SectionResult) -> dict:
    """规范化 payload（复用深度比较用）：只比内容身份，不含 created_at 等易变字段。"""
    return {
        "section_version": result.section_version,
        "task_id": result.task_id,
        "section_id": result.section_id,
        "status": result.status,
        "markdown": result.markdown,
        "dependency_fingerprint": result.dependency_fingerprint,
        "claims": [SS.claim_to_dict(c) for c in result.claims],
        "unresolved": [SS.unresolved_to_dict(u) for u in result.unresolved],
        "source_run_ids": list(result.source_run_ids),
        "source_question_ids": list(result.source_question_ids),
    }


def commit_section_result(result: SS.SectionResult, *, run_id: str = "") -> SS.SectionCommitResult:
    """原子提交一个章节产物：section_result + claims + citations + unresolved +
    current_section 指针 + progress，失败整体回滚。

    - 写入/复用前必经：结构校验 + section_result_id 由 section_version 派生自洽 +
      task 存在且 result.section_id == task.section_id。
    - 幂等复用：同 section_result_id（由 section_version 派生）→ 深度比较规范化
      payload + dependency_fingerprint（非只比 section_version）；不一致即报存储损坏。
    - 冲突/损坏 fail-closed：任何失败都不切换 current_section 指针。
    - current_section 指针只在完整校验并写入后原子切换（UPSERT）。
    """
    # 0. 结构校验（写入/复用前必经）。
    errors = svalidator.validate_section_result(result)
    if errors:
        raise SectionStorageConflictError(
            "章节结构校验失败:\n" + "\n".join(f"  - {e}" for e in errors))
    # 1. section_result_id 必须由 section_version 稳定派生（身份自洽）。
    if SS.derive_section_result_id(result.section_version) != result.section_result_id:
        raise SectionStorageConflictError(
            f"section_result_id 与 section_version 派生不一致: "
            f"{result.section_result_id} != {SS.derive_section_result_id(result.section_version)}")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 2. task 必须存在且 result.section_id == task.section_id（写入/复用前）。
        task_row = conn.execute(
            "SELECT section_id FROM section_task WHERE task_id = ?",
            (result.task_id,)).fetchone()
        if task_row is None:
            conn.rollback()
            raise SectionStorageConflictError(f"task 不存在: {result.task_id}（fail-closed）")
        if task_row["section_id"] != result.section_id:
            conn.rollback()
            raise SectionStorageConflictError(
                f"result.section_id 与 task.section_id 不一致: "
                f"{result.section_id} != {task_row['section_id']}（fail-closed）")

        row = conn.execute(
            "SELECT section_version, payload_json FROM section_result WHERE section_result_id = ?",
            (result.section_result_id,)).fetchone()
        if row is not None:
            conn.rollback()
            if row["section_version"] != result.section_version:
                raise SectionStorageConflictError(
                    f"section_result_id 冲突: {result.section_result_id} 已存在但版本不一致（fail-closed）")
            existing = SS.section_result_from_dict(json.loads(row["payload_json"]))
            if _normalized_payload(existing) != _normalized_payload(result):
                raise SectionStorageCorruptionError(
                    f"section_result 复用前规范化 payload 与自身身份不一致: {result.section_result_id}")
            return SS.SectionCommitResult(
                section_result_id=result.section_result_id, reused=True,
                current_switched=False, claim_count=len(result.claims),
                unresolved_count=len(result.unresolved))

        section_ordinal = PS.PHASE4_SECTION_ORDER.index(result.section_id) \
            if result.section_id in PS.PHASE4_SECTION_ORDER else 999

        conn.execute(
            "INSERT INTO section_result (section_result_id, section_version, task_id, "
            "section_id, status, section_ordinal, payload_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (result.section_result_id, result.section_version, result.task_id,
             result.section_id, result.status, section_ordinal,
             json.dumps(SS.section_result_to_dict(result), ensure_ascii=False),
             result.created_at or _utcnow()))

        for c in result.claims:
            conn.execute(
                "INSERT INTO section_claim (claim_id, section_result_id, section_id, "
                "topic_id, question_ids_json, text, claim_type, citation_refs_json, "
                "payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (c.claim_id, result.section_result_id, c.section_id, c.topic_id,
                 json.dumps(list(c.question_ids), ensure_ascii=False), c.text,
                 c.claim_type,
                 json.dumps([SS.citation_to_dict(r) for r in c.citation_refs],
                            ensure_ascii=False),
                 json.dumps(SS.claim_to_dict(c), ensure_ascii=False)))
            for ref in c.citation_refs:
                conn.execute(
                    "INSERT INTO section_citation (citation_id, claim_id, section_result_id, "
                    "ref_type, evidence_id, snapshot_id, item_code, formula_id, "
                    "formula_version, period, source_snapshot_id, page_number, payload_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_citation_id(c.claim_id, ref), c.claim_id, result.section_result_id,
                     ref.ref_type, ref.evidence_id, ref.snapshot_id, ref.item_code,
                     ref.formula_id, ref.formula_version, ref.period,
                     ref.source_snapshot_id, ref.page_number,
                     json.dumps(SS.citation_to_dict(ref), ensure_ascii=False)))

        for u in result.unresolved:
            conn.execute(
                "INSERT INTO section_unresolved (unresolved_id, section_result_id, "
                "section_id, topic_id, question_id, state, reason_code, detail, payload_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (u.unresolved_id, result.section_result_id, u.section_id, u.topic_id,
                 u.question_id, u.state, u.reason_code, u.detail,
                 json.dumps(SS.unresolved_to_dict(u), ensure_ascii=False)))

        before = conn.execute("SELECT section_result_id FROM current_section WHERE task_id = ?",
                              (result.task_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_section (task_id, section_id, section_result_id, switched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "section_id=excluded.section_id, section_result_id=excluded.section_result_id, "
            "switched_at=excluded.switched_at",
            (result.task_id, result.section_id, result.section_result_id,
             result.created_at or _utcnow()))

        job_id = _job_id_for_task(conn, result.task_id)
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), job_id, run_id, "writing", "SECTION_WRITTEN",
             f"section_result_id={result.section_result_id} section_id={result.section_id}",
             result.created_at or _utcnow()))

        conn.commit()
        switched = (before is None or before["section_result_id"] != result.section_result_id)
        return SS.SectionCommitResult(
            section_result_id=result.section_result_id, reused=False,
            current_switched=switched, claim_count=len(result.claims),
            unresolved_count=len(result.unresolved))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_section_result(section_result_id: str) -> SS.SectionResult | None:
    """按 section_result_id 读取完整章节产物（payload_json 往返）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_result WHERE section_result_id = ?",
            (section_result_id,)).fetchone()
        if row is None:
            return None
        return SS.section_result_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def get_current_section(task_id: str) -> str | None:
    """按 task_id 读取 current 章节产物指针（section_result_id）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT section_result_id FROM current_section WHERE task_id = ?",
                           (task_id,)).fetchone()
        return None if row is None else row["section_result_id"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 章节产物写入：commit_evaluation / commit_rework_run（P4-D 接入）
# ---------------------------------------------------------------------------

def _job_id_for_result(conn: sqlite3.Connection, section_result_id: str) -> str:
    row = conn.execute(
        "SELECT p.job_id FROM section_result r "
        "JOIN section_task t ON t.task_id = r.task_id "
        "JOIN section_plan p ON p.plan_id = t.plan_id "
        "WHERE r.section_result_id = ?", (section_result_id,)).fetchone()
    return row["job_id"] if row is not None else ""


def _rework_row_id(evaluation_id: str, target_id: str) -> str:
    return "rw_" + SS.sha256_json([evaluation_id, target_id])[:24]


def commit_evaluation(evaluation: SS.SectionEvaluation, *, run_id: str = "") -> SS.EvaluationCommitResult:
    """原子提交一次章节评估：section_evaluation + section_rework（rework_targets 逐条），
    失败整体回滚。

    - 写入前必经：decision 白名单 + llm_evaluator_calls ∈ {0,1} + evaluation_id 内容寻址自洽
      + 引用的 section_result 已存在（外键）。
    - 幂等复用：同 evaluation_id → 深度比较 payload，一致 reuse。
    - Evaluation 是 SectionResult 的关联对象：只写 section_evaluation/section_rework 表，
      绝不回写 section_result，绝不改变 SectionResult 内容身份。
    """
    if evaluation.decision not in SS.EVALUATION_DECISIONS:
        raise SectionStorageConflictError(
            f"evaluation.decision 非法: {evaluation.decision!r}，允许 {SS.EVALUATION_DECISIONS}")
    if evaluation.llm_evaluator_calls not in (0, 1):
        raise SectionStorageConflictError(
            f"llm_evaluator_calls 必须 ∈ {{0,1}}，收到 {evaluation.llm_evaluator_calls}（fail-closed）")
    if SS.derive_evaluation_id(
            evaluation.section_result_id, evaluation.decision, evaluation.rules_version,
            evaluation.evaluator_prompt_version, evaluation.issues, evaluation.rework_targets,
            evaluation.llm_evaluator_calls) != evaluation.evaluation_id:
        raise SectionStorageConflictError("evaluation_id 与内容派生不一致（fail-closed）")

    # 防线：同一 Evaluation 内 target_id 必须唯一（section_rework.rework_id 主键按
    # (evaluation_id, target_id) 派生）。重复应在汇总边界经 canonicalize_rework_targets
    # 去重；此处 fail-closed，绝不依赖 SQLite IntegrityError 兜底、绝不用 INSERT OR IGNORE 吞冲突。
    seen_target_ids: set[str] = set()
    for t in evaluation.rework_targets:
        if t.target_id in seen_target_ids:
            raise SectionStorageConflictError(
                f"evaluation 内存在重复 rework_target_id: {t.target_id}（fail-closed；"
                f"应在汇总边界经 canonicalize_rework_targets 去重）")
        seen_target_ids.add(t.target_id)

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM section_result WHERE section_result_id = ?",
            (evaluation.section_result_id,)).fetchone()
        if exists is None:
            conn.rollback()
            raise SectionStorageConflictError(
                f"evaluation 引用不存在的 section_result: {evaluation.section_result_id}（fail-closed）")

        row = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE evaluation_id = ?",
            (evaluation.evaluation_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.evaluation_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 evaluated_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.evaluation_to_dict(old)
            new_d = SS.evaluation_to_dict(evaluation)
            old_d.pop("evaluated_at", None)
            new_d.pop("evaluated_at", None)
            if old_d == new_d:
                return SS.EvaluationCommitResult(
                    evaluation_id=evaluation.evaluation_id, reused=True,
                    rework_target_count=len(evaluation.rework_targets))
            raise SectionStorageCorruptionError(
                f"evaluation_id 冲突: {evaluation.evaluation_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_evaluation (evaluation_id, section_result_id, rules_version, "
            "evaluator_prompt_version, rules_passed, llm_passed, decision, payload_json, "
            "evaluated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (evaluation.evaluation_id, evaluation.section_result_id, evaluation.rules_version,
             evaluation.evaluator_prompt_version, int(evaluation.rules_passed),
             None if evaluation.llm_passed is None else int(evaluation.llm_passed),
             evaluation.decision, json.dumps(SS.evaluation_to_dict(evaluation), ensure_ascii=False),
             evaluation.evaluated_at or _utcnow()))

        for t in evaluation.rework_targets:
            conn.execute(
                "INSERT INTO section_rework (rework_id, evaluation_id, target_kind, "
                "target_ref, reason, payload_json) VALUES (?,?,?,?,?,?)",
                (_rework_row_id(evaluation.evaluation_id, t.target_id),
                 evaluation.evaluation_id, t.target_kind, t.target_ref, t.reason,
                 json.dumps(SS.rework_target_to_dict(t), ensure_ascii=False)))

        job_id = _job_id_for_result(conn, evaluation.section_result_id)
        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), job_id, run_id, "evaluating", "EVALUATED",
             f"evaluation_id={evaluation.evaluation_id} decision={evaluation.decision}",
             evaluation.evaluated_at or _utcnow()))

        conn.commit()
        return SS.EvaluationCommitResult(
            evaluation_id=evaluation.evaluation_id, reused=False,
            rework_target_count=len(evaluation.rework_targets))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_evaluation(section_result_id: str) -> SS.SectionEvaluation | None:
    """读取某 section_result 的最新评估（独立关联对象，不 join 进 SectionResult）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE section_result_id = ? "
            "ORDER BY evaluated_at DESC, rowid DESC LIMIT 1",
            (section_result_id,)).fetchone()
        if row is None:
            return None
        return SS.evaluation_from_dict(json.loads(row["payload_json"]))
    finally:
        conn.close()


def list_evaluations(section_result_id: str) -> list[SS.SectionEvaluation]:
    """读取某 section_result 的全部评估（按时间升序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM section_evaluation WHERE section_result_id = ? "
            "ORDER BY evaluated_at ASC, rowid ASC",
            (section_result_id,)).fetchall()
        return [SS.evaluation_from_dict(json.loads(r["payload_json"])) for r in rows]
    finally:
        conn.close()


def commit_rework_run(run: SS.SectionReworkRun, *, run_id: str = "") -> SS.ReworkRunCommitResult:
    """原子提交一次返工批次（append-only 历史事实，不可变）。"""
    if run.batch_no != 0:
        raise SectionStorageConflictError(
            f"返工批次必须为 0（本阶段至多一批），收到 {run.batch_no}（fail-closed）")
    if SS.derive_rework_run_id(run.from_section_result_id, run.section_result_id,
                               run.batch_no) != run.rework_run_id:
        raise SectionStorageConflictError("rework_run_id 与内容派生不一致（fail-closed）")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM section_rework_run WHERE rework_run_id = ?",
            (run.rework_run_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.rework_run_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 created_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.rework_run_to_dict(old)
            new_d = SS.rework_run_to_dict(run)
            old_d.pop("created_at", None)
            new_d.pop("created_at", None)
            if old_d == new_d:
                return SS.ReworkRunCommitResult(rework_run_id=run.rework_run_id, reused=True)
            raise SectionStorageCorruptionError(
                f"rework_run_id 冲突: {run.rework_run_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_rework_run (rework_run_id, job_id, section_result_id, "
            "from_section_result_id, evaluation_id, batch_no, llm_evaluator_calls, "
            "targets_json, payload_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run.rework_run_id, run.job_id, run.section_result_id, run.from_section_result_id,
             run.evaluation_id, run.batch_no, run.llm_evaluator_calls,
             json.dumps([SS.rework_target_to_dict(t) for t in run.targets], ensure_ascii=False),
             json.dumps(SS.rework_run_to_dict(run), ensure_ascii=False),
             run.created_at or _utcnow()))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), run.job_id, run_id, "rework", "REWORKED",
             f"rework_run_id={run.rework_run_id} from={run.from_section_result_id} "
             f"to={run.section_result_id}", run.created_at or _utcnow()))

        conn.commit()
        return SS.ReworkRunCommitResult(rework_run_id=run.rework_run_id, reused=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_rework_runs(from_section_result_id: str) -> list[SS.SectionReworkRun]:
    """读取某父 SectionResult 触发过的全部返工批次（按时间升序）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM section_rework_run WHERE from_section_result_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (from_section_result_id,)).fetchall()
        return [SS.rework_run_from_dict(json.loads(r["payload_json"])) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 运行清单写入：commit_manifest + current_manifest 指针
# ---------------------------------------------------------------------------

def commit_manifest(manifest: SS.SectionRunManifest) -> SS.ManifestCommitResult:
    """原子提交运行清单 + current_manifest 指针切换。

    - manifest 历史 append-only（不可变）；current_manifest 是独立可切换指针。
    - 幂等复用：同 manifest_id → 深度比较 payload，一致 reuse 且不切指针。
    """
    if SS.derive_manifest_id(manifest.job_id, manifest.run_id, manifest.code_fingerprint,
                             manifest.phase3_closure_fingerprint, manifest.batch_versions,
                             manifest.frozen) != manifest.manifest_id:
        raise SectionStorageConflictError("manifest_id 与内容派生不一致（fail-closed）")

    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM section_run_manifest WHERE manifest_id = ?",
            (manifest.manifest_id,)).fetchone()
        if row is not None:
            conn.rollback()
            old = SS.manifest_from_dict(json.loads(row["payload_json"]))
            # 复用深度比较只比内容身份，剔除 created_at（时间戳非身份，重跑时必然变化）。
            old_d = SS.manifest_to_dict(old)
            new_d = SS.manifest_to_dict(manifest)
            old_d.pop("created_at", None)
            new_d.pop("created_at", None)
            if old_d == new_d:
                return SS.ManifestCommitResult(
                    manifest_id=manifest.manifest_id, reused=True, current_switched=False)
            raise SectionStorageCorruptionError(
                f"manifest_id 冲突: {manifest.manifest_id} 已存在但内容不一致（fail-closed）")

        conn.execute(
            "INSERT INTO section_run_manifest (manifest_id, job_id, run_id, code_fingerprint, "
            "phase3_closure_fingerprint, batch_versions_json, frozen_json, payload_json, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (manifest.manifest_id, manifest.job_id, manifest.run_id, manifest.code_fingerprint,
             manifest.phase3_closure_fingerprint,
             json.dumps(manifest.batch_versions, ensure_ascii=False),
             json.dumps(manifest.frozen, ensure_ascii=False),
             json.dumps(SS.manifest_to_dict(manifest), ensure_ascii=False),
             manifest.created_at or _utcnow()))

        before = conn.execute("SELECT manifest_id FROM current_manifest WHERE job_id = ?",
                              (manifest.job_id,)).fetchone()
        conn.execute(
            "INSERT INTO current_manifest (job_id, manifest_id, switched_at) "
            "VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "manifest_id=excluded.manifest_id, switched_at=excluded.switched_at",
            (manifest.job_id, manifest.manifest_id, manifest.created_at or _utcnow()))

        conn.execute(
            "INSERT INTO progress (event_id, job_id, run_id, stage, status, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (_event_id(), manifest.job_id, manifest.run_id, "manifest", "MANIFESTED",
             f"manifest_id={manifest.manifest_id}", manifest.created_at or _utcnow()))

        conn.commit()
        switched = (before is None or before["manifest_id"] != manifest.manifest_id)
        return SS.ManifestCommitResult(
            manifest_id=manifest.manifest_id, reused=False, current_switched=switched)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current_manifest(job_id: str) -> str | None:
    """按 job_id 读取 current 运行清单指针（manifest_id）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT manifest_id FROM current_manifest WHERE job_id = ?",
                           (job_id,)).fetchone()
        return None if row is None else row["manifest_id"]
    finally:
        conn.close()


def get_manifest(manifest_id: str) -> SS.SectionRunManifest | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM section_run_manifest WHERE manifest_id = ?",
            (manifest_id,)).fetchone()
        if row is None:
            return None
        return SS.manifest_from_dict(json.loads(row["payload_json"]))
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
        for t in ("section_plan", "section_task", "current_plan", "section_result",
                  "section_claim", "section_unresolved", "section_evaluation",
                  "section_rework", "section_rework_run", "section_run_manifest",
                  "current_manifest", "progress"):
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
