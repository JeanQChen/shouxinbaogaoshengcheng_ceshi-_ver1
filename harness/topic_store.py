"""R1-B：唯一 TopicResearchPack 追加式 SQLite Pack Store。

- 复用 ``data/harness.db``（与 harness/checkpoint.py 共用文件，但本模块的表名全部
  ``topic_*`` 前缀 + 独立 ``topic_schema_migrations``，不与 checkpoint.py 独占的
  ``run_manifest`` / ``question_outcome`` / ``schema_migrations`` 冲突）。
- 不可变历史事实表（topic_pack / topic_aspect_result / topic_material / topic_fact /
  topic_conflict / topic_not_found_audit / topic_gap / topic_event）由
  BEFORE UPDATE/DELETE 触发器保护，不依赖代码约定。
- current 指针业务键 = (task_id, company_id, report_as_of, contract_fingerprint,
  source_policy_version, section_id, topic_id)，可原子切换（非历史事实）。
- 追加式迁移：独立 topic_schema_migrations 记录已应用版本；新建库一次到位，旧库原地
  升级；迁移失败完整回滚；结构与 migration 记录不一致失败关闭。
- commit_pack 单事务原子：校验 → 复用前完整性 → 严格内容一致 → 写 Pack + 全部子行
  → 写后复核 → 原子切换 current → commit。任一步失败全部回滚。
- pack_id 是「内容身份」（content_fingerprint + dependency_fingerprint，不含 run_id /
  timestamp / call_id / 日志路径）。同 pack_id 再次提交是幂等复用；content_fingerprint
  与 pack_id 不符 → StorageCorruptionError；同身份但内容不同（哈希碰撞）→ 拒绝。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness import topic_schema as TS

DEFAULT_DB_PATH = Path("data/harness.db")

# 模块级，由 init_topic_store() 设置，后续操作复用本路径。
_db_path: Path | None = None


class StorageConflictError(Exception):
    """存储冲突：同一内容身份被重复提交但内容不一致，显式报错（不静默覆盖）。"""


class StorageCorruptionError(Exception):
    """存储损坏：复用前完整性校验失败（pack_id/content_fingerprint 与内容不符）。"""


class TopicStoreValidationError(ValueError):
    """Pack / requirement / 身份校验失败（写入前拒绝）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("topic store not initialized. Call init_topic_store() first.")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class CommitPackResult:
    pack_id: str
    reused: bool
    current_switched: bool
    aspect_count: int
    material_count: int
    fact_count: int


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

def _immutable_trigger_sqls(table: str) -> list[str]:
    return [
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update\n"
        f"    BEFORE UPDATE ON {table}\n"
        f"BEGIN\n    SELECT RAISE(ABORT, '{table} is immutable (UPDATE forbidden)');\nEND;",
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete\n"
        f"    BEFORE DELETE ON {table}\n"
        f"BEGIN\n    SELECT RAISE(ABORT, '{table} is immutable (DELETE forbidden)');\nEND;",
    ]


def _immutable_triggers(table: str) -> str:
    return "\n" + "\n".join(_immutable_trigger_sqls(table)) + "\n"


_IMMUTABLE_TABLES = (
    "topic_pack",
    "topic_aspect_result",
    "topic_material",
    "topic_fact",
    "topic_conflict",
    "topic_not_found_audit",
    "topic_gap",
    "topic_event",
)

# 追加事件类型（§6：stale|invalidated|quarantined 标记失效，不进入 writer 消费）。
EVENT_TYPES = (
    "committed",
    "switched_current",
    "reused",
    "stale",
    "invalidated",
    "quarantined",
)


def _ddl_statements() -> list[str]:
    """返回最新（v1）建表语句列表（逐条拆分，供单事务原子 init）。

    不使用 executescript（其隐式 COMMIT 会破坏首次初始化的原子性）；每条语句独立
    ``conn.execute``，全部落在同一个事务里，任一失败整体回滚。
    """
    stmts: list[str] = []

    stmts.append(
        "CREATE TABLE IF NOT EXISTS topic_schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")

    stmts.append(
        "CREATE TABLE IF NOT EXISTS topic_pack ("
        "pack_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, run_id TEXT NOT NULL, "
        "task_id TEXT NOT NULL, company_id TEXT NOT NULL, report_as_of TEXT, "
        "contract_version TEXT NOT NULL, contract_fingerprint TEXT NOT NULL, "
        "source_policy_version TEXT NOT NULL, section_id TEXT NOT NULL, topic_id TEXT NOT NULL, "
        "question_ids TEXT NOT NULL, outcome_refs TEXT NOT NULL, external_funnel TEXT, "
        "usage TEXT NOT NULL, uncertain_calls TEXT NOT NULL, process_status TEXT NOT NULL, "
        "coverage_status TEXT NOT NULL, status_derivation TEXT NOT NULL, "
        "dependency_fingerprint TEXT NOT NULL, content_fingerprint TEXT NOT NULL, "
        "created_at TEXT NOT NULL)")
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_topic_pack_identity ON topic_pack("
        "task_id, company_id, report_as_of, contract_fingerprint, "
        "source_policy_version, section_id, topic_id)")
    stmts.extend(_immutable_trigger_sqls("topic_pack"))

    for table, child_id in (
        ("topic_aspect_result", "aspect_id"),
        ("topic_material", "material_id"),
        ("topic_fact", "fact_id"),
        ("topic_conflict", "conflict_id"),
        ("topic_not_found_audit", "audit_id"),
        ("topic_gap", "unresolved_id"),
    ):
        stmts.append(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            f"pack_id TEXT NOT NULL REFERENCES topic_pack(pack_id), "
            f"{child_id} TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL, "
            f"PRIMARY KEY (pack_id, {child_id}))")
        stmts.extend(_immutable_trigger_sqls(table))

    stmts.append(
        "CREATE TABLE IF NOT EXISTS topic_current ("
        "task_id TEXT NOT NULL, company_id TEXT NOT NULL, report_as_of TEXT NOT NULL, "
        "contract_fingerprint TEXT NOT NULL, source_policy_version TEXT NOT NULL, "
        "section_id TEXT NOT NULL, topic_id TEXT NOT NULL, "
        "pack_id TEXT NOT NULL REFERENCES topic_pack(pack_id), switched_at TEXT NOT NULL, "
        "PRIMARY KEY (task_id, company_id, report_as_of, contract_fingerprint, "
        "source_policy_version, section_id, topic_id))")

    stmts.append(
        "CREATE TABLE IF NOT EXISTS topic_event ("
        "event_id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES topic_pack(pack_id), "
        "event_type TEXT NOT NULL, old_pack_id TEXT, new_pack_id TEXT, reason TEXT, "
        "event_at TEXT NOT NULL)")
    stmts.extend(_immutable_trigger_sqls("topic_event"))

    return stmts


def build_ddl() -> str:
    """返回最新（v1）建表语句（单字符串；仅诊断/文档展示用，init 走 _ddl_statements）。"""
    return "\n".join(_ddl_statements())


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _read_applied_versions(conn: sqlite3.Connection) -> list[str]:
    return [r["version"] for r in conn.execute(
        "SELECT version FROM topic_schema_migrations ORDER BY rowid")]


def _latest_applied_version(conn: sqlite3.Connection) -> str | None:
    applied = _read_applied_versions(conn)
    if not applied:
        return None
    known = [v for v, _ in MIGRATIONS]
    if applied != known[:len(applied)]:
        raise RuntimeError(
            f"topic_schema_migrations 应用序列非合法前缀: {applied}（期望前缀 {known[:len(applied)]}）")
    return known[len(applied) - 1]


def _run_migration(conn: sqlite3.Connection, version: str, fn: Callable[[sqlite3.Connection], None]) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        try:
            fn(conn)
            conn.execute(
                "INSERT INTO topic_schema_migrations (version, applied_at) VALUES (?,?)",
                (version, _utcnow()),
            )
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"迁移 {version} 后外键校验失败: {bad[:5]}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _apply_pending_migrations(conn: sqlite3.Connection) -> None:
    applied = _read_applied_versions(conn)
    known = [v for v, _ in MIGRATIONS]
    if list(applied) != known[:len(applied)]:
        raise RuntimeError(
            f"topic_schema_migrations 版本序列不合法: {applied}（期望前缀 {known[:len(applied)]}）")
    pending = [(v, fn) for v, fn in MIGRATIONS if v not in set(applied)]
    for version, fn in pending:
        if fn is None:
            raise RuntimeError(f"迁移 {version} 无实现但结构已存在，拒绝")
        _run_migration(conn, version, fn)
    _verify_structure_matches_latest(conn)


def _verify_structure_matches_latest(conn: sqlite3.Connection) -> None:
    latest = _latest_applied_version(conn)
    if latest != MIGRATIONS[-1][0]:
        raise RuntimeError(f"topic_schema_migrations 最新版本 {latest} != 期望 {MIGRATIONS[-1][0]}")
    for table in ("topic_pack", "topic_aspect_result", "topic_material", "topic_fact",
                  "topic_conflict", "topic_not_found_audit", "topic_gap", "topic_current",
                  "topic_event", "topic_schema_migrations"):
        if not _table_exists(conn, table):
            raise RuntimeError(f"结构校验失败：缺表 {table}")
    for table in _IMMUTABLE_TABLES:
        for trig in (f"trg_{table}_no_update", f"trg_{table}_no_delete"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (trig,)
            ).fetchone() is not None
            if not exists:
                raise RuntimeError(f"结构校验失败：缺不可变触发器 {trig}")


# 迁移列表（追加式；已应用版本记录在 topic_schema_migrations 表）。
MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None] | None]] = [
    ("1", None),  # v1 初始 DDL
]


def _validate_ddl_prefixes() -> None:
    """DDL 前缀/危险子句校验：仅允许本模块 topic_ 表 / idx_topic_ 索引 / trg_topic_ 触发器，
    禁用 INSERT OR IGNORE / INSERT OR REPLACE（幂等/覆盖必须显式冲突，不静默吞冲突）。"""
    for stmt in _ddl_statements():
        upper = stmt.upper()
        if "INSERT OR IGNORE" in upper or "INSERT OR REPLACE" in upper:
            raise RuntimeError("topic store DDL 禁用 INSERT OR IGNORE / INSERT OR REPLACE")
        for kind, expect in (("TABLE", "TOPIC_"), ("INDEX", "IDX_TOPIC_"),
                             ("TRIGGER", "TRG_TOPIC_")):
            for m in re.finditer(rf"CREATE\s+{kind}\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", upper):
                if not m.group(1).startswith(expect):
                    raise RuntimeError(
                        f"topic store DDL 含非法 {kind} 前缀对象: {m.group(1)}")


def init_topic_store(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化 topic Pack Store SQLite 数据库（append-only 追加迁移，首次初始化单事务原子）。

    - 首次初始化在单个事务内执行全部 DDL + 写入 migration 记录，任一失败整体回滚；
    - 禁用 INSERT OR IGNORE / INSERT OR REPLACE；
    - 前缀校验：仅 topic_ 表 / idx_topic_ 索引 / trg_topic_ 触发器，杜绝混入非本模块对象。
    """
    global _db_path
    _db_path = Path(db_path)
    if TS.TOPIC_PACK_SCHEMA_VERSION != MIGRATIONS[-1][0]:
        raise RuntimeError(
            f"TOPIC_PACK_SCHEMA_VERSION {TS.TOPIC_PACK_SCHEMA_VERSION} != 最新 migration {MIGRATIONS[-1][0]}")

    _validate_ddl_prefixes()

    conn = _get_conn()
    try:
        if not _table_exists(conn, "topic_schema_migrations"):
            conn.execute("BEGIN")
            try:
                for stmt in _ddl_statements():
                    conn.execute(stmt)
                now = _utcnow()
                for version, _ in MIGRATIONS:
                    conn.execute(
                        "INSERT INTO topic_schema_migrations (version, applied_at) VALUES (?,?)",
                        (version, now),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:
            _apply_pending_migrations(conn)
        _verify_structure_matches_latest(conn)
    finally:
        conn.close()


def applied_schema_version() -> str | None:
    conn = _get_conn()
    try:
        return _latest_applied_version(conn)
    finally:
        conn.close()


def self_check() -> dict:
    """只读结构/migration 一致性自检（供 --self-check）。"""
    conn = _get_conn()
    try:
        _verify_structure_matches_latest(conn)
        return {"ok": True, "schema_version": _latest_applied_version(conn),
                "detail": "topic store structure matches latest"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(s: str | None):
    if not s:
        return None
    return json.loads(s)


# ---------------------------------------------------------------------------
# Pack 一致性校验
# ---------------------------------------------------------------------------

def _validate_pack_references(pack: TS.TopicResearchPack) -> None:
    """Pack 级引用完整性：所有跨对象引用必须闭合，重复 id / 悬空 id / 未闭合引用一律拒绝。"""
    aspect_ids = {r.aspect_id for r in pack.aspect_results}
    if len(aspect_ids) != len(pack.aspect_results):
        raise TopicStoreValidationError("Pack 含重复 aspect_id")

    def _assert_unique(objects, attr: str, label: str) -> set[str]:
        ids = {getattr(o, attr) for o in objects}
        if len(ids) != len(objects):
            raise TopicStoreValidationError(f"Pack 含重复 {label} {attr}")
        return ids

    material_ids = _assert_unique(pack.materials, "material_id", "material")
    fact_ids = _assert_unique(pack.facts, "fact_id", "fact")
    audit_ids = _assert_unique(pack.not_found_audits, "audit_id", "not_found_audit")
    unresolved_ids = _assert_unique(pack.unresolved, "unresolved_id", "gap")
    _assert_unique(pack.conflicts, "conflict_id", "conflict")

    audit_by_id = {a.audit_id: a for a in pack.not_found_audits}

    for r in pack.aspect_results:
        for mid in r.material_ids:
            if mid not in material_ids:
                raise TopicStoreValidationError(f"aspect {r.aspect_id!r} 引用不存在 material {mid!r}")
        for fid in r.supported_fact_ids:
            if fid not in fact_ids:
                raise TopicStoreValidationError(f"aspect {r.aspect_id!r} 引用不存在 fact {fid!r}")
        for uid in r.unresolved_ids:
            if uid not in unresolved_ids:
                raise TopicStoreValidationError(f"aspect {r.aspect_id!r} 引用不存在 gap {uid!r}")
        if r.not_found_audit_id is not None:
            if r.not_found_audit_id not in audit_ids:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} 引用不存在 not_found_audit {r.not_found_audit_id!r}")
            if r.status == "not_found" and not audit_by_id[r.not_found_audit_id].qualified:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=not_found 但审计 {r.not_found_audit_id!r} "
                    f"qualified=False（未合格搜索不得投影 not_found）")

    for f in pack.facts:
        for aid in f.aspect_ids:
            if aid not in aspect_ids:
                raise TopicStoreValidationError(f"fact {f.fact_id!r} 引用不存在 aspect {aid!r}")
    for c in pack.conflicts:
        for fid in c.fact_ids:
            if fid not in fact_ids:
                raise TopicStoreValidationError(f"conflict {c.conflict_id!r} 引用不存在 fact {fid!r}")
    for g in pack.unresolved:
        for aid in g.aspect_ids:
            if aid not in aspect_ids:
                raise TopicStoreValidationError(f"gap {g.unresolved_id!r} 引用不存在 aspect {aid!r}")


def _validate_requirement_self_consistency(requirement: TS.TopicResearchRequirement) -> None:
    """requirement 不得自证：内嵌 Contract/SourcePolicy/EvidenceRequirement/question 身份必须闭合一致。

    - 顶层 contract_fingerprint 必须为有效 sha256；
    - 每条 aspect 的 contract_version / contract_sha256 与顶层一致；
    - 每条 EvidenceRequirementRef 的 contract_sha256 与顶层一致；
    - 每条 SourcePolicyRef 的 policy_version 与顶层 source_policy_version 一致；
    - question 集合与所有 aspect 的 question_id 闭合一致。
    """
    if not TS._is_sha256_hex(requirement.contract_fingerprint):
        raise TopicStoreValidationError(
            "requirement.contract_fingerprint 必须为 64 位 sha256 hex")
    qids = set(requirement.question_ids)
    aspect_qids = {a.question_id for a in requirement.aspects}
    if qids != aspect_qids:
        raise TopicStoreValidationError(
            f"requirement question 集合与 aspect question_id 不闭合："
            f"question_ids={sorted(qids)}，aspect question_id={sorted(aspect_qids)}")
    for a in requirement.aspects:
        if a.contract_version != requirement.contract_version:
            raise TopicStoreValidationError(
                f"aspect {a.aspect_id!r} contract_version={a.contract_version!r} 与 "
                f"requirement={requirement.contract_version!r} 不一致")
        if a.contract_sha256 != requirement.contract_fingerprint:
            raise TopicStoreValidationError(
                f"aspect {a.aspect_id!r} contract_sha256 与 requirement.contract_fingerprint 不一致")
        if a.source_policy_ref.policy_version != requirement.source_policy_version:
            raise TopicStoreValidationError(
                f"aspect {a.aspect_id!r} source_policy_ref.policy_version 与 "
                f"requirement.source_policy_version 不一致")
        for er in a.evidence_requirement_ids:
            if er.contract_sha256 != requirement.contract_fingerprint:
                raise TopicStoreValidationError(
                    f"aspect {a.aspect_id!r} EvidenceRequirementRef.contract_sha256 与 "
                    f"requirement.contract_fingerprint 不一致")


def _validate_requirement_matches(pack: TS.TopicResearchPack,
                                  requirement: TS.TopicResearchRequirement) -> None:
    """Store 提交时校验 requirement 身份 + aspect 集合完全一致 + 冻结投影一致（§六）。"""
    ident_checks = {
        "topic_id": (requirement.topic_id, pack.topic_id),
        "section_id": (requirement.section_id, pack.section_id),
        "task_id": (requirement.task_id, pack.task_id),
        "company_id": (requirement.company_id, pack.company_id),
        "report_as_of": (requirement.report_as_of, pack.report_as_of),
        "contract_version": (requirement.contract_version, pack.contract_version),
        "contract_fingerprint": (requirement.contract_fingerprint, pack.contract_fingerprint),
        "source_policy_version": (requirement.source_policy_version, pack.source_policy_version),
    }
    for field_name, (req_val, pack_val) in ident_checks.items():
        if req_val != pack_val:
            raise TopicStoreValidationError(
                f"requirement.{field_name}={req_val!r} 与 pack.{field_name}={pack_val!r} 不一致")

    req_ids = set(requirement.aspect_ids())
    pack_ids = {r.aspect_id for r in pack.aspect_results}
    if req_ids != pack_ids:
        missing = sorted(req_ids - pack_ids)
        extra = sorted(pack_ids - req_ids)
        raise TopicStoreValidationError(
            f"aspect 集合不一致：缺失 {missing}，混入 {extra}")

    frozen_by_id = {a.aspect_id: a for a in requirement.aspects}
    for r in pack.aspect_results:
        frozen = frozen_by_id[r.aspect_id]
        if r.requirement_snapshot.to_dict() != frozen.to_dict():
            raise TopicStoreValidationError(
                f"aspect {r.aspect_id!r} 的 requirement_snapshot 与冻结投影不一致")

    # question 集合一致（顺序无关，按集合比较）
    if set(requirement.question_ids) != set(pack.question_ids):
        raise TopicStoreValidationError(
            f"question 集合不一致：requirement={sorted(requirement.question_ids)}，"
            f"pack={sorted(pack.question_ids)}")

    # dependency fingerprint 必须与 requirement 重新计算结果一致
    expected_dep = requirement.dependency_fingerprint()
    if pack.dependency_fingerprint != expected_dep:
        raise TopicStoreValidationError(
            f"dependency_fingerprint 不一致：pack={pack.dependency_fingerprint!r}，"
            f"requirement 重算={expected_dep!r}")


# 关键行业结论 topic（§9 sufficiency gate 目标）；由 frozen Contract 派生，不修改业务语义。
_CRITICAL_CONCLUSION_TOPICS = (
    "industry_scale_cycle",
    "industry_position",
    "industry_competition",
    "industry_risk_transmission",
    "industry_supply_demand",
)


def _evaluate_coverage_rules(r: TS.AspectResearchResult,
                             facts: tuple[TS.SupportedFact, ...],
                             materials: tuple[TS.ResearchMaterial, ...]) -> None:
    """covered aspect 的 coverage_rules 确定性评估（fail-closed，绝不默认通过）。

    只有 topic_harness 的 6 条规则可被 typed schema 确定性表达；未知/非 topic_harness 规则，
    或需运行时集合枚举证明的 set_complete → coverage_rule_not_evaluable（拒绝）。
    direct_support 的闭环由 _validate_aspect_semantics 的 authority/closed-chain 门禁承担；
    search_audit 仅针对负面核验/not_found/集合/外部时效（由 not_found 分支单独门禁），
    covered 正面事实不触发；applicability 由 not_applicable 分支单独门禁。
    """
    snap = r.requirement_snapshot
    rules = set(snap.coverage_rules)
    known = {"set_complete", "required_fields_complete", "minimum_sources",
             "direct_support", "search_audit", "applicability"}
    unknown = rules - known
    if unknown:
        raise TopicStoreValidationError(
            f"aspect {r.aspect_id!r} 含不可评估 coverage_rule_not_evaluable: {sorted(unknown)}")
    if "set_complete" in rules:
        raise TopicStoreValidationError(
            f"aspect {r.aspect_id!r} coverage_rule_not_evaluable: set_complete 需权威集合枚举证明，"
            f"R1-B typed schema 无法表达（fail-closed）")
    if "required_fields_complete" in rules:
        obtained: set[str] = set()
        for f in facts:
            obtained |= set(f.obtained_fields)
        missing = set(snap.required_fields) - obtained
        if missing:
            raise TopicStoreValidationError(
                f"aspect {r.aspect_id!r} required_fields_complete 未满足，缺字段 {sorted(missing)}")
    if "minimum_sources" in rules:
        ids = {TS.authority_source_identity(f.source_authority) for f in facts}
        if len(ids) < 1:
            raise TopicStoreValidationError(
                f"aspect {r.aspect_id!r} minimum_sources 未满足（无有效来源）")


def _validate_sufficiency_required(r: TS.AspectResearchResult) -> None:
    """关键行业结论 topic 的 covered aspect 必须携带满足阈值的 SufficiencyAssessment（§9）。"""
    snap = r.requirement_snapshot
    if snap.topic_id not in _CRITICAL_CONCLUSION_TOPICS:
        return
    if r.status != "covered":
        return
    sa = r.sufficiency_assessment
    if sa is None:
        raise TopicStoreValidationError(
            f"关键结论 aspect {r.aspect_id!r}（topic={snap.topic_id}）缺 SufficiencyAssessment")
    if not sa.threshold_met:
        raise TopicStoreValidationError(
            f"关键结论 aspect {r.aspect_id!r} SufficiencyAssessment.threshold_met=False")


def _validate_aspect_semantics(pack: TS.TopicResearchPack) -> None:
    """aspect 层语义门禁（不信任调用方直接填写的 status，也不信任空壳 covered）。

    - covered 必须非空壳（≥1 material + ≥1 supported fact + 非 rejected authority + citation 非空）；
    - covered 的 authority 必须确定性重算为 authoritative（不信任自称 verdict），且 material /
      fact / citation 来源身份一致（material→payload→authority→fact→citation→aspect 闭环）；
    - covered 的 coverage_rules 必须确定性评估（不可表达 → fail-closed）；
    - 关键结论 topic 的 covered aspect 必须通过 sufficiency gate（authority 与 sufficiency 独立）；
    - not_found 必须绑定 qualified NotFoundAudit；
    - partial/blocked 必须与对应 Gap / 停止原因一致；
    - not_applicable 必须保留适用性判定依据；
    - aspect 引用的 facts/materials/gaps/audits 必须存在并回指该 aspect；
    - attached SufficiencyAssessment 的 aspect/事实/来源 ID 必须可解析且一致。
    """
    fact_by_id = {f.fact_id: f for f in pack.facts}
    material_by_id = {m.material_id: m for m in pack.materials}
    audit_by_id = {a.audit_id: a for a in pack.not_found_audits}
    gap_by_id = {g.unresolved_id: g for g in pack.unresolved}
    source_ids = ({m.source_identity for m in pack.materials}
                  | {m.payload_ref.authority_identity for m in pack.materials})

    for r in pack.aspect_results:
        for fid in r.supported_fact_ids:
            if fid not in fact_by_id:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} 引用不存在 fact {fid!r}")
            if r.aspect_id not in fact_by_id[fid].aspect_ids:
                raise TopicStoreValidationError(
                    f"fact {fid!r} 不回指 aspect {r.aspect_id!r}")
        for gid in r.unresolved_ids:
            if gid not in gap_by_id:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} 引用不存在 gap {gid!r}")
            if r.aspect_id not in gap_by_id[gid].aspect_ids:
                raise TopicStoreValidationError(
                    f"gap {gid!r} 不回指 aspect {r.aspect_id!r}")

        if r.status == "covered":
            if not r.material_ids:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=covered 但无 material（空壳）")
            if not r.supported_fact_ids:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=covered 但无 supported fact（空壳）")
            facts = tuple(fact_by_id[fid] for fid in r.supported_fact_ids)
            materials = tuple(material_by_id[mid] for mid in r.material_ids)
            for f in facts:
                if not f.citation_refs:
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 fact {f.fact_id!r} 无 CitationRef")
                # 权威确定性重算（不信任自称 verdict）。
                rv = TS.recompute_authority_verdict(f.source_authority)
                if f.source_authority.verdict != rv:
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 fact {f.fact_id!r} authority verdict "
                        f"自称 {f.source_authority.verdict!r} 与字段确定性重算 {rv!r} 不一致")
                if rv != "authoritative":
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 fact {f.fact_id!r} 由非 authoritative 来源支撑")
                # citation 与 source_authority 来源身份一致。
                aid = TS.authority_source_identity(f.source_authority)
                if not any(TS.citation_source_identity(c) == aid for c in f.citation_refs):
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 fact {f.fact_id!r} citation 与 "
                        f"source_authority 来源身份不一致")
                # fact 必须被同源 material 支撑（material→fact 身份一致）。
                if not any(TS.authority_source_identity(m.authority_assessment) == aid
                           for m in materials):
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 fact {f.fact_id!r} 无同源 material 支撑")
            for m in materials:
                rv = TS.recompute_authority_verdict(m.authority_assessment)
                if m.authority_assessment.verdict != rv:
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 material {m.material_id!r} authority verdict "
                        f"自称 {m.authority_assessment.verdict!r} 与字段重算 {rv!r} 不一致")
                if rv != "authoritative":
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 material {m.material_id!r} 由非 authoritative 来源支撑")
            # 最小覆盖证明（coverage_rules；不可表达 → fail-closed）。
            _evaluate_coverage_rules(r, facts, materials)
            # 关键结论 sufficiency gate（与 authority gate 独立）。
            _validate_sufficiency_required(r)
        elif r.status == "not_found":
            if r.not_found_audit_id is None:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=not_found 未绑定 not_found_audit_id")
            audit = audit_by_id[r.not_found_audit_id]
            if not audit.qualified:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=not_found 但 audit 未 qualified")
        elif r.status == "partial":
            if not r.unresolved_ids and r.not_found_audit_id is None:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=partial 但无 gap/not_found 依据")
        elif r.status == "blocked":
            blocking_gap = any(gap_by_id[gid].blocking for gid in r.unresolved_ids
                               if gid in gap_by_id)
            if not blocking_gap and not pack.usage.stop_reason:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=blocked 但无 blocking gap 或 stop_reason")
        elif r.status == "not_applicable":
            if not r.requirement_snapshot.applicability_policy:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} status=not_applicable 但无 applicability_policy 依据")

        if r.sufficiency_assessment is not None:
            sa = r.sufficiency_assessment
            if sa.aspect_id is not None and sa.aspect_id != r.aspect_id:
                raise TopicStoreValidationError(
                    f"aspect {r.aspect_id!r} 的 SufficiencyAssessment.aspect_id={sa.aspect_id!r} 不一致")
            for fid in sa.supporting_fact_ids:
                if fid not in fact_by_id:
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 SufficiencyAssessment 引用不存在 fact {fid!r}")
            for sid in sa.supporting_source_ids:
                if sid not in source_ids:
                    raise TopicStoreValidationError(
                        f"aspect {r.aspect_id!r} 的 SufficiencyAssessment 引用无法解析的 source {sid!r}")


def _recompute_status_consistency(pack: TS.TopicResearchPack,
                                  requirement: TS.TopicResearchRequirement) -> None:
    """双轴状态 + status_derivation 必须由完整 requirement + aspect_results 确定性重算。

    调用方提供的 process_status / coverage_status / status_derivation 与重算值不一致 →
    fail-closed（不信任伪造的 covered/complete）。
    """
    required = tuple(a.aspect_id for a in requirement.aspects)
    process2, coverage2, derivation2 = TS.derive_pack_status(
        required, pack.aspect_results, stop_reason=pack.usage.stop_reason)
    if process2 != pack.process_status:
        raise TopicStoreValidationError(
            f"process_status 与确定性重算不一致：调用方={pack.process_status.to_dict()}，"
            f"重算={process2.to_dict()}")
    if coverage2 != pack.coverage_status:
        raise TopicStoreValidationError(
            f"coverage_status 与确定性重算不一致：调用方={pack.coverage_status.to_dict()}，"
            f"重算={coverage2.to_dict()}")
    if derivation2 != pack.status_derivation:
        raise TopicStoreValidationError(
            f"status_derivation 与确定性重算不一致")


# ---------------------------------------------------------------------------
# 落盘 / 读回
# ---------------------------------------------------------------------------

def _identity_cols(pack: TS.TopicResearchPack) -> tuple:
    return (pack.task_id, pack.company_id, pack.report_as_of or "", pack.contract_fingerprint,
            pack.source_policy_version, pack.section_id, pack.topic_id)


def _insert_pack_conn(conn: sqlite3.Connection, pack: TS.TopicResearchPack) -> None:
    conn.execute(
        "INSERT INTO topic_pack (pack_id, schema_version, run_id, task_id, company_id, "
        "report_as_of, contract_version, contract_fingerprint, source_policy_version, "
        "section_id, topic_id, question_ids, outcome_refs, external_funnel, usage, "
        "uncertain_calls, process_status, coverage_status, status_derivation, "
        "dependency_fingerprint, content_fingerprint, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pack.pack_id, pack.schema_version, pack.run_id, pack.task_id, pack.company_id,
         pack.report_as_of, pack.contract_version, pack.contract_fingerprint,
         pack.source_policy_version, pack.section_id, pack.topic_id,
         _json_dumps(list(pack.question_ids)), _json_dumps(list(pack.outcome_refs)),
         _json_dumps(pack.external_funnel.to_dict()) if pack.external_funnel else None,
         _json_dumps(pack.usage.to_dict()),
         _json_dumps([u.to_dict() for u in pack.uncertain_calls]),
         _json_dumps(pack.process_status.to_dict()),
         _json_dumps(pack.coverage_status.to_dict()),
         _json_dumps(pack.status_derivation.to_dict()),
         pack.dependency_fingerprint, pack.content_fingerprint(), _utcnow()),
    )


def _insert_child_conn(conn: sqlite3.Connection, table: str, pack_id: str, child_id: str,
                       seq: int, obj) -> None:
    conn.execute(
        f"INSERT INTO {table} (pack_id, {_child_id_col(table)}, seq, payload) VALUES (?,?,?,?)",
        (pack_id, child_id, seq, _json_dumps(obj.to_dict())),
    )


_CHILD_TABLES = {
    "topic_aspect_result": ("aspect_id", "aspect_id"),
    "topic_material": ("material_id", "material_id"),
    "topic_fact": ("fact_id", "fact_id"),
    "topic_conflict": ("conflict_id", "conflict_id"),
    "topic_not_found_audit": ("audit_id", "audit_id"),
    "topic_gap": ("unresolved_id", "unresolved_id"),
}


def _child_id_col(table: str) -> str:
    return _CHILD_TABLES[table][0]


def _child_attr(table: str) -> str:
    return _CHILD_TABLES[table][1]


def _insert_all_children_conn(conn: sqlite3.Connection, pack: TS.TopicResearchPack) -> None:
    for seq, r in enumerate(pack.aspect_results):
        _insert_child_conn(conn, "topic_aspect_result", pack.pack_id, r.aspect_id, seq, r)
    for seq, m in enumerate(pack.materials):
        _insert_child_conn(conn, "topic_material", pack.pack_id, m.material_id, seq, m)
    for seq, f in enumerate(pack.facts):
        _insert_child_conn(conn, "topic_fact", pack.pack_id, f.fact_id, seq, f)
    for seq, c in enumerate(pack.conflicts):
        _insert_child_conn(conn, "topic_conflict", pack.pack_id, c.conflict_id, seq, c)
    for seq, a in enumerate(pack.not_found_audits):
        _insert_child_conn(conn, "topic_not_found_audit", pack.pack_id, a.audit_id, seq, a)
    for seq, g in enumerate(pack.unresolved):
        _insert_child_conn(conn, "topic_gap", pack.pack_id, g.unresolved_id, seq, g)


def _child_payloads(conn: sqlite3.Connection, table: str, pack_id: str) -> list:
    rows = conn.execute(
        f"SELECT payload FROM {table} WHERE pack_id=? ORDER BY seq", (pack_id,)).fetchall()
    return [_json_loads(r["payload"]) for r in rows]


def _row_to_pack(conn: sqlite3.Connection, row: sqlite3.Row) -> TS.TopicResearchPack:
    pack_id = row["pack_id"]
    d = {
        "schema_version": row["schema_version"],
        "pack_id": row["pack_id"],
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "company_id": row["company_id"],
        "report_as_of": row["report_as_of"],
        "contract_version": row["contract_version"],
        "contract_fingerprint": row["contract_fingerprint"],
        "source_policy_version": row["source_policy_version"],
        "section_id": row["section_id"],
        "topic_id": row["topic_id"],
        "question_ids": _json_loads(row["question_ids"]),
        "aspect_results": _child_payloads(conn, "topic_aspect_result", pack_id),
        "materials": _child_payloads(conn, "topic_material", pack_id),
        "facts": _child_payloads(conn, "topic_fact", pack_id),
        "outcome_refs": _json_loads(row["outcome_refs"]),
        "external_funnel": _json_loads(row["external_funnel"]),
        "conflicts": _child_payloads(conn, "topic_conflict", pack_id),
        "not_found_audits": _child_payloads(conn, "topic_not_found_audit", pack_id),
        "unresolved": _child_payloads(conn, "topic_gap", pack_id),
        "usage": _json_loads(row["usage"]),
        "uncertain_calls": _json_loads(row["uncertain_calls"]),
        "process_status": _json_loads(row["process_status"]),
        "coverage_status": _json_loads(row["coverage_status"]),
        "status_derivation": _json_loads(row["status_derivation"]),
        "dependency_fingerprint": row["dependency_fingerprint"],
    }
    return TS.TopicResearchPack.from_dict(d)


def _set_current_conn(conn: sqlite3.Connection, pack: TS.TopicResearchPack) -> None:
    ident = _identity_cols(pack)
    conn.execute(
        "INSERT INTO topic_current (task_id, company_id, report_as_of, contract_fingerprint, "
        "source_policy_version, section_id, topic_id, pack_id, switched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(task_id, company_id, report_as_of, contract_fingerprint, "
        "source_policy_version, section_id, topic_id) DO UPDATE SET "
        "pack_id=excluded.pack_id, switched_at=excluded.switched_at",
        (*ident, pack.pack_id, _utcnow()),
    )


def _append_event_conn(conn: sqlite3.Connection, pack_id: str, event_type: str,
                       old_pack_id: str | None = None, new_pack_id: str | None = None,
                       reason: str | None = None) -> None:
    conn.execute(
        "INSERT INTO topic_event (event_id, pack_id, event_type, old_pack_id, new_pack_id, "
        "reason, event_at) VALUES (?,?,?,?,?,?,?)",
        ("e-" + uuid.uuid4().hex[:16], pack_id, event_type, old_pack_id, new_pack_id,
         reason, _utcnow()),
    )


def _current_pack_id_conn(conn: sqlite3.Connection, pack: TS.TopicResearchPack) -> str | None:
    ident = _identity_cols(pack)
    row = conn.execute(
        "SELECT pack_id FROM topic_current WHERE task_id=? AND company_id=? AND report_as_of=? "
        "AND contract_fingerprint=? AND source_policy_version=? AND section_id=? AND topic_id=?",
        ident,
    ).fetchone()
    return row["pack_id"] if row else None


def _canonical_equal(a: TS.TopicResearchPack, b: TS.TopicResearchPack) -> bool:
    """深规范形比较：content_fingerprint + dependency_fingerprint 全等即同一份不可变内容。

    content_fingerprint 已含 process/coverage/status_derivation/usage/uncertain_calls/
    outcome_refs 等全部内容字段，故这是对「全部内容 + 依赖」的深比较；run_id / pack_id
    被有意排除（内容身份不绑定 run/timestamp，由 pack_id 自身承载身份）。
    """
    return (a.content_fingerprint() == b.content_fingerprint()
            and a.dependency_fingerprint == b.dependency_fingerprint)


def _verify_reconstructed(conn: sqlite3.Connection, pack_id: str) -> TS.TopicResearchPack:
    """读回复核（fail-closed）：行 + 全部子行 + content_fingerprint + pack_id 逐项一致。

    子行是 content_fingerprint 的一部分，因此重算 content_fingerprint 并与存库值、pack_id
    比对，即同时复核了子行缺失/篡改；任一不符 → StorageCorruptionError（不返回半成品）。
    """
    row = conn.execute("SELECT * FROM topic_pack WHERE pack_id=?", (pack_id,)).fetchone()
    if row is None:
        raise StorageCorruptionError(f"pack 行缺失: {pack_id}")
    back = _row_to_pack(conn, row)
    if back.content_fingerprint() != row["content_fingerprint"]:
        raise StorageCorruptionError(
            f"pack content_fingerprint 与内容重算不一致: {pack_id}")
    if back.compute_pack_id() != pack_id:
        raise StorageCorruptionError(
            f"pack_id 与内容/依赖指纹重算不一致: {pack_id}")
    return back


def _terminal_invalidation_conn(conn: sqlite3.Connection, pack_id: str) -> str | None:
    """返回 pack 的终态失效类型（stale|invalidated|quarantined），否则 None。

    失效是 Pack 身份上的终态事件：只要历史上存在任一失效事件即终态失效，不被后续
    committed/reused/switched_current 清除。绝不默认「最新事件非失效」即复活。
    """
    row = conn.execute(
        "SELECT event_type FROM topic_event WHERE pack_id=? AND event_type IN "
        "('stale','invalidated','quarantined') ORDER BY rowid DESC LIMIT 1",
        (pack_id,)).fetchone()
    return row["event_type"] if row is not None else None


def _existing_pack_integrity_ok(conn: sqlite3.Connection, pack_id: str) -> bool:
    """复用前完整性校验：既有 Pack 的 content_fingerprint 与 pack_id 与内容重算一致。"""
    try:
        _verify_reconstructed(conn, pack_id)
        return True
    except StorageCorruptionError:
        return False


# ---------------------------------------------------------------------------
# 提交（公开）
# ---------------------------------------------------------------------------

def commit_pack(pack: TS.TopicResearchPack,
                requirement: TS.TopicResearchRequirement,
                resolver: TS.PayloadResolver | None = None) -> CommitPackResult:
    """单事务原子提交一个 TopicResearchPack（append-only）。

    - requirement 必填：身份 / aspect 集合 / question 集合 / 冻结投影 / dependency fingerprint
      完全一致，否则拒绝（不存在绕过 requirement 的公开写入口）；
    - aspect 语义门禁 + 双轴状态独立重算（不信任调用方填写的 status，空壳 covered 拒绝）；
    - 注入 resolver 时逐 material payload_ref 可解析校验（dangling/类型/版本/locator/hash fail-closed）；
    - pack_id 为空 → 确定性回填；非空 → 校验与内容/依赖指纹一致；
    - 同 pack_id 已存在 → 复用前完整性校验 + 深规范形比较 → 幂等复用（不重写、不冲突）；
    - 全新 pack_id → 写 Pack + 全部子行 → 写后复核 → 原子切换 current → commit。
    """
    if not pack.pack_id:
        pack = TS.finalize_pack(pack)
    else:
        pack.verify_pack_id()
    _validate_pack_references(pack)
    _validate_requirement_self_consistency(requirement)
    _validate_requirement_matches(pack, requirement)
    _validate_aspect_semantics(pack)
    _recompute_status_consistency(pack, requirement)
    if pack.materials:
        if resolver is None:
            raise TopicStoreValidationError(
                "pack 含 material 但未注入 PayloadResolver（payload 必须可解析，fail-closed）")
        TS.verify_pack_payloads(pack, resolver)

    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM topic_pack WHERE pack_id=?", (pack.pack_id,)).fetchone()

        if existing is not None:
            if _terminal_invalidation_conn(conn, pack.pack_id) is not None:
                raise TopicStoreValidationError(
                    f"pack {pack.pack_id} 已终态失效（stale|invalidated|quarantined），"
                    f"禁止普通 recommit 复活")
            if not _existing_pack_integrity_ok(conn, pack.pack_id):
                raise StorageCorruptionError(
                    f"pack 复用前完整性校验失败: {pack.pack_id}")
            existing_pack = _verify_reconstructed(conn, pack.pack_id)
            if not _canonical_equal(existing_pack, pack):
                raise StorageConflictError(
                    f"pack_id 已存在但内容深规范形不一致（哈希碰撞/内容篡改）: {pack.pack_id}")
            cur = _current_pack_id_conn(conn, pack)
            current_switched = (cur != pack.pack_id)
            if current_switched:
                old = cur
                _set_current_conn(conn, pack)
                _append_event_conn(conn, pack.pack_id, "switched_current",
                                   old_pack_id=old, new_pack_id=pack.pack_id)
            conn.commit()
            return CommitPackResult(
                pack_id=pack.pack_id, reused=True, current_switched=current_switched,
                aspect_count=len(pack.aspect_results), material_count=len(pack.materials),
                fact_count=len(pack.facts))

        _insert_pack_conn(conn, pack)
        _insert_all_children_conn(conn, pack)

        # 写后复核：读回并深规范形比对，防止序列化/落盘丢失。
        back = _verify_reconstructed(conn, pack.pack_id)
        if not _canonical_equal(back, pack):
            raise StorageCorruptionError(
                f"pack 写后复核不一致: {pack.pack_id}")

        _set_current_conn(conn, pack)
        _append_event_conn(conn, pack.pack_id, "committed", new_pack_id=pack.pack_id)
        conn.commit()
        return CommitPackResult(
            pack_id=pack.pack_id, reused=False, current_switched=True,
            aspect_count=len(pack.aspect_results), material_count=len(pack.materials),
            fact_count=len(pack.facts))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 读查询（公开）
# ---------------------------------------------------------------------------

def get_pack(pack_id: str) -> TS.TopicResearchPack | None:
    """读单 Pack；行/子行/指纹/pack_id 任一不符 → StorageCorruptionError（fail-closed）。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT 1 FROM topic_pack WHERE pack_id=?", (pack_id,)).fetchone()
        if row is None:
            return None
        return _verify_reconstructed(conn, pack_id)
    finally:
        conn.close()


@dataclass(frozen=True)
class CurrentPackLoad:
    """current 指针解析结果（fail-closed 包装，区分「无 current」与「current 不可用」）。"""

    pack: TS.TopicResearchPack | None
    pack_id: str | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.pack is not None


def load_current_pack(identity: TS.PackIdentity) -> CurrentPackLoad:
    """读 current 指针并解析为「可用」Pack（fail-closed）。

    - 无 current 指针 → available=False（reason="no_current"）；
    - current 指向的行/子行/指纹不一致 → StorageCorruptionError（不返回半成品）；
    - 指向的 Pack 最新事件为 stale|invalidated|quarantined → available=False（reason=事件类型），
      绝不把失效 Pack 当可用 current 返回。
    """
    conn = _get_conn()
    try:
        k = identity.key()
        row = conn.execute(
            "SELECT pack_id FROM topic_current WHERE task_id=? AND company_id=? AND "
            "report_as_of=? AND contract_fingerprint=? AND source_policy_version=? "
            "AND section_id=? AND topic_id=?", k).fetchone()
        if row is None:
            return CurrentPackLoad(pack=None, reason="no_current")
        pack_id = row["pack_id"]
        inv = _terminal_invalidation_conn(conn, pack_id)
        if inv is not None:
            return CurrentPackLoad(pack=None, pack_id=pack_id, reason=inv)
        return CurrentPackLoad(pack=_verify_reconstructed(conn, pack_id), pack_id=pack_id)
    finally:
        conn.close()


def get_current_pack(identity: TS.PackIdentity) -> TS.TopicResearchPack | None:
    """fail-closed 包装：仅返回「可用」current；失效/损坏/无 current 一律 None（不暴露失效对象）。"""
    return load_current_pack(identity).pack


def list_current_packs() -> list[TS.TopicResearchPack]:
    """列出全部「可用」current Pack（跳过失效的 current；损坏仍 fail-closed 抛出）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT pack_id FROM topic_current ORDER BY task_id, company_id, topic_id").fetchall()
        out: list[TS.TopicResearchPack] = []
        for r in rows:
            pack_id = r["pack_id"]
            if _terminal_invalidation_conn(conn, pack_id) is not None:
                continue
            out.append(_verify_reconstructed(conn, pack_id))
        return out
    finally:
        conn.close()


def list_pack_history(identity: TS.PackIdentity) -> list[TS.TopicResearchPack]:
    """读该身份全部历史 Pack（显式历史读，含失效/旧版本；损坏仍 fail-closed 抛出）。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT pack_id FROM topic_pack WHERE task_id=? AND company_id=? AND "
            "COALESCE(report_as_of,'')=? AND contract_fingerprint=? AND "
            "source_policy_version=? AND section_id=? AND topic_id=? ORDER BY rowid",
            (identity.task_id, identity.company_id, identity.report_as_of or "",
             identity.contract_fingerprint, identity.source_policy_version,
             identity.section_id, identity.topic_id),
        ).fetchall()
        return [_verify_reconstructed(conn, r["pack_id"]) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 失效事件（append-only）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicEvent:
    """topic_event 表的一行追加事件（不可变审计事件）。"""

    event_id: str
    pack_id: str
    event_type: str
    old_pack_id: str | None = None
    new_pack_id: str | None = None
    reason: str | None = None
    event_at: str = ""

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise TopicStoreValidationError(
                f"非法事件类型 {self.event_type!r}（允许 {EVENT_TYPES}）")

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "pack_id": self.pack_id, "event_type": self.event_type,
            "old_pack_id": self.old_pack_id, "new_pack_id": self.new_pack_id,
            "reason": self.reason, "event_at": self.event_at,
        }


def _row_to_event(row: sqlite3.Row) -> TopicEvent:
    return TopicEvent(
        event_id=row["event_id"], pack_id=row["pack_id"], event_type=row["event_type"],
        old_pack_id=row["old_pack_id"], new_pack_id=row["new_pack_id"],
        reason=row["reason"], event_at=row["event_at"],
    )


def mark_invalidated(pack_id: str, event_type: str = "invalidated",
                     reason: str | None = None) -> None:
    """标记 Pack 失效（append-only 事件，不 UPDATE/DELETE、不自动升级、不切换 current）。

    event_type ∈ {stale, invalidated, quarantined}。仅追加一条 topic_event，Pack 历史不变，
    是否退出 current / 不进入 writer 消费由消费方（R3/R5）按事件判定。
    """
    if event_type not in ("stale", "invalidated", "quarantined"):
        raise TopicStoreValidationError(
            f"mark_invalidated 仅接受 stale|invalidated|quarantined，得到 {event_type!r}")
    conn = _get_conn()
    try:
        row = conn.execute("SELECT 1 FROM topic_pack WHERE pack_id=?", (pack_id,)).fetchone()
        if row is None:
            raise KeyError(f"pack 不存在: {pack_id}")
        _append_event_conn(conn, pack_id, event_type, reason=reason)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_events(pack_id: str | None = None) -> list[TopicEvent]:
    """读取追加事件（按 event_at / rowid 序）；pack_id=None 时返回全部。"""
    conn = _get_conn()
    try:
        if pack_id is None:
            rows = conn.execute("SELECT * FROM topic_event ORDER BY rowid").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM topic_event WHERE pack_id=? ORDER BY rowid", (pack_id,)
            ).fetchall()
        return [_row_to_event(r) for r in rows]
    finally:
        conn.close()
