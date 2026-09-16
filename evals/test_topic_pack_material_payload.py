"""Eval: Store migration 3 + topic_material_payload 持久化/resolver（R2_IMPLEMENTATION_PLAN §6.1/§9/§11）。

用法: python -m evals.test_topic_pack_material_payload

覆盖：
- Store schema v3（STORE_SCHEMA_VERSION=3 == MIGRATIONS[-1][0]；fresh init → applied 3；
  migration 2→3 追加升级；不可变触发器 UPDATE/DELETE 被拒）。
- TopicMaterialPayloadResolver.resolve：dangling → None；字段/哈希不符 → StorageCorruptionError
  （损坏 ≠ 未找到，不把损坏伪装成缺失）；object_type 门控（R2 只构建 evidence_span/table_context）。
- commit_payload_batch：单事务原子（批量含坏记录 → 整体回滚无残留）；幂等复用（同 ID 同内容）；
  同 ID 异内容 → StorageCorruptionError（哈希碰撞/损坏）；自洽校验 payload_id==payload_hash==sha256(bytes)。
- 双哈希两层身份：payload_id==payload_hash==sha256(payload_bytes)（载体层）；
  source_content_hash 独立（来源层），不进入 payload 内容哈希。
- Pack schema v2→v3 fail-closed：读 schema_version='2' 旧 Pack → SchemaVersionIncompatibleError。
- TS.verify_material_payload_ref 集成（成功返回 ResolvedPayload；dangling → SchemaValidationError）。

全部离线：临时 SQLite，不调 LLM/网络。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from harness import topic_store as Store
from evidence import ids as evidence_ids


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# 信封身份坐标（§五.1）：document_identity + locator(page/block_range) 需与
# evidence.ids.make_evidence_id 的七元组严格一致，跨公司/跨文档串读 fail-closed。
_COMPANY_ID = "300750"
_DOC_ID = "doc1"
_DOC_VERSION = "v1"
_EVIDENCE_SET_VERSION = "v1"
_BLOCK_INDEX = 0
_PAGE = 5


def _locator(page: int = _PAGE) -> TS.EvidenceLocator:
    return TS.EvidenceLocator(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        section_path="主营业务分析", page=page, block_range=(_BLOCK_INDEX, _BLOCK_INDEX + 1))


def _locator_json(loc: TS.MaterialLocator) -> str:
    return json.dumps(loc.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _make_payload(text: str = "动力电池", structured_payload: dict | None = None,
                  object_type: str = "evidence_span",
                  company_id: str = _COMPANY_ID,
                  document_id: str = _DOC_ID,
                  document_version: str = _DOC_VERSION,
                  evidence_set_version: str = _EVIDENCE_SET_VERSION,
                  page: int = _PAGE,
                  source_content_hash: str | None = None,
                  created_dependency_fingerprint: str | None = None) -> bytes:
    """构造 §五.1 合法 payload 信封（material_payload_version=1 + 关键字段闭合）。

    evidence_id 由 evidence.ids.make_evidence_id(company_id, document_id,
    document_version, evidence_set_version, page, block_index, source_content_hash)
    确定性派生，authority_identity == f"evidence:{evidence_id}"。
    """
    ch = source_content_hash or evidence_ids.content_hash(text, structured_payload)
    eid = evidence_ids.make_evidence_id(
        company_id, document_id, document_version, evidence_set_version,
        page, _BLOCK_INDEX, ch)
    env = {
        "material_payload_version": 1,
        "object_type": object_type,
        "authority_identity": f"evidence:{eid}",
        "evidence_id": eid,
        "source_content_hash": ch,
        "created_dependency_fingerprint": created_dependency_fingerprint or _sha("dep"),
        "content": {"text": text, "structured_payload": structured_payload},
        "locator": _locator(page=page).to_dict(),
        "document_identity": {
            "company_id": company_id,
            "document_id": document_id,
            "document_version": document_version,
            "evidence_set_version": evidence_set_version,
        },
    }
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


def _record(payload_bytes: bytes, object_type: str = "evidence_span",
            source_content_hash: str | None = None,
            authority_identity: str | None = None, version: str = "v1",
            created_dependency_fingerprint: str | None = None) -> Store.MaterialPayloadRecord:
    payload_hash = _sha_bytes(payload_bytes)
    env = json.loads(payload_bytes.decode("utf-8"))
    if authority_identity is None:
        authority_identity = env["authority_identity"]
    if source_content_hash is None:
        content = env["content"]
        source_content_hash = evidence_ids.content_hash(
            str(content.get("text", "")), content.get("structured_payload"))
    return Store.MaterialPayloadRecord(
        payload_id=payload_hash,
        object_type=object_type,
        authority_identity=authority_identity,
        version=version,
        locator_json=json.dumps(env["locator"], ensure_ascii=False, separators=(",", ":")),
        source_content_hash=source_content_hash,
        payload_hash=payload_hash,
        payload_bytes=payload_bytes,
        created_dependency_fingerprint=created_dependency_fingerprint or _sha("dep"))


def _ref(payload_bytes: bytes, object_type: str = "evidence_span",
         authority_identity: str | None = None, version: str = "v1",
         created_dependency_fingerprint: str | None = None) -> TS.MaterialPayloadRef:
    if authority_identity is None:
        try:
            env = json.loads(payload_bytes.decode("utf-8"))
            authority_identity = env.get("authority_identity") or "evidence:ev-1"
        except (UnicodeDecodeError, json.JSONDecodeError):
            authority_identity = "evidence:ev-1"
    return TS.MaterialPayloadRef(
        object_type=object_type,
        authority_identity=authority_identity,
        version=version,
        content_hash=_sha_bytes(payload_bytes),
        locator=_locator(),
        created_dependency_fingerprint=created_dependency_fingerprint or _sha("dep"))


def _row_count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM topic_material_payload").fetchone()[0]
    finally:
        conn.close()


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

    # ------------------------------------------------------------------
    # 1. Store schema v3 常量一致性 + fresh init 结构
    # ------------------------------------------------------------------
    check(TS.STORE_SCHEMA_VERSION == "3", "STORE_SCHEMA_VERSION == 3")
    check(TS.TOPIC_PACK_SCHEMA_VERSION == "4", "TOPIC_PACK_SCHEMA_VERSION == 4")
    check(Store.MIGRATIONS[-1][0] == "3", "MIGRATIONS[-1][0] == 3")
    check(Store.MIGRATIONS[-1][0] == TS.STORE_SCHEMA_VERSION,
          "Store schema v3 == Pack 迁移表最新版本（独立维度断言成立）")

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        check(Store.applied_schema_version() == "3", "fresh init → applied_schema_version 3")
        conn = sqlite3.connect(str(db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        trigs = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        conn.close()
        check("topic_material_payload" in tables, "topic_material_payload 表存在")
        check("trg_topic_material_payload_no_update" in trigs
              and "trg_topic_material_payload_no_delete" in trigs,
              "topic_material_payload 不可变触发器存在")

    # ------------------------------------------------------------------
    # 2. migration 2→3 追加升级（DROP v3 表 + 删 migration 记录 → 重建）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "m.db"
        Store.init_topic_store(db)
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE topic_material_payload")
        conn.execute("DELETE FROM topic_schema_migrations WHERE version='3'")
        conn.commit()
        conn.close()
        Store.init_topic_store(db)
        check(Store.applied_schema_version() == "3", "migration 2→3 升级 → schema 3")
        conn = sqlite3.connect(str(db))
        vers = [r[0] for r in conn.execute(
            "SELECT version FROM topic_schema_migrations ORDER BY rowid")]
        conn.close()
        check(vers == ["1", "2", "3"], "migration 序列 1,2,3（迁移 1/2 未被改写）")

    # ------------------------------------------------------------------
    # 3. commit_payload_batch 基本提交 + resolve 精确往返（双哈希）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)

        payload = _make_payload("动力电池")
        ph = _sha_bytes(payload)
        rec = _record(payload)
        # 双哈希：source_content_hash 独立于 payload_hash。
        check(rec.payload_id == rec.payload_hash == ph, "payload_id == payload_hash == sha256(bytes)")
        check(rec.source_content_hash != rec.payload_hash, "source_content_hash 独立于 payload_hash")
        resolver.commit_payload_batch((rec,))

        ref = _ref(payload)
        resolved = resolver.resolve(ref)
        check(resolved is not None, "resolve 命中返回 ResolvedPayload")
        check(resolved is not None and resolved.payload_bytes == payload,
              "resolve 返回 payload_bytes 精确一致")
        check(resolved is not None and resolved.content_hash == ph,
              "resolve content_hash == payload_hash（载体层身份）")
        check(resolved is not None and resolved.locator.to_dict() == _locator().to_dict(),
              "resolve locator 与 ref 一致")
        check(_row_count(db) == 1, "提交后恰好一行")

        # 不可变表：UPDATE/DELETE 被触发器拒绝。
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE topic_material_payload SET version='x' WHERE payload_id=?",
                         (ph,))
            check(False, "UPDATE topic_material_payload 应被拒绝")
        except sqlite3.IntegrityError:
            check(True, "UPDATE topic_material_payload → 不可变触发器拒绝")
        try:
            conn.execute("DELETE FROM topic_material_payload WHERE payload_id=?", (ph,))
            check(False, "DELETE topic_material_payload 应被拒绝")
        except sqlite3.IntegrityError:
            check(True, "DELETE topic_material_payload → 不可变触发器拒绝")
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 4. commit_payload_batch 幂等复用（同 ID 同内容） + 碰撞（同 ID 异内容）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)

        payload = _make_payload("idem")
        rec = _record(payload)
        resolver.commit_payload_batch((rec,))
        resolver.commit_payload_batch((rec,))  # 幂等复用，不新增行
        check(_row_count(db) == 1, "同 ID 同内容 → 幂等复用，仅一行")

        # 同 ID 异内容：payload_id 复用但 payload_hash/bytes/source 不同 → 拒绝。
        ph = _sha_bytes(payload)
        collided = Store.MaterialPayloadRecord(
            payload_id=ph, object_type="evidence_span",
            authority_identity="evidence:ev-1", version="v1",
            locator_json=_locator_json(_locator()),
            source_content_hash=_sha("other-src"),
            payload_hash=_sha_bytes(b"DIFFERENT"),
            payload_bytes=b"DIFFERENT",
            created_dependency_fingerprint=_sha("dep"))
        try:
            resolver.commit_payload_batch((collided,))
            check(False, "同 ID 异内容应拒绝（哈希碰撞/损坏）")
        except Store.StorageCorruptionError:
            check(True, "同 ID 异内容 → StorageCorruptionError")

    # ------------------------------------------------------------------
    # 5. commit_payload_batch 单事务原子（批量含坏记录 → 整体回滚无残留）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)

        good = _record(_make_payload("ok"))
        bad = Store.MaterialPayloadRecord(
            payload_id=_sha("bad-id"), object_type="evidence_span",
            authority_identity="evidence:ev-2", version="v1",
            locator_json=_locator_json(_locator(page=9)),
            source_content_hash=_sha("src2"),
            payload_hash=_sha_bytes(b"INTENDED"),
            payload_bytes=b"CORRUPTED",  # sha256(bytes) != payload_hash → 自洽校验失败
            created_dependency_fingerprint=_sha("dep2"))
        try:
            resolver.commit_payload_batch((good, bad))
            check(False, "批量含坏记录应整体失败")
        except Store.StorageCorruptionError:
            check(True, "批量含坏记录 → StorageCorruptionError")
        check(_row_count(db) == 0, "原子回滚：无任何残留行")

        # 空批无副作用。
        resolver.commit_payload_batch(())
        check(_row_count(db) == 0, "空批无副作用")

    # ------------------------------------------------------------------
    # 6. resolve：dangling vs 损坏（不把损坏伪装成缺失）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)

        payload = _make_payload("x")
        rec = _record(payload)
        resolver.commit_payload_batch((rec,))

        # dangling：不存在 → None。
        missing = _ref(b'{"never":"committed"}')
        check(resolver.resolve(missing) is None, "dangling payload_id → None（非损坏）")

        # 损坏：手工 INSERT 一行 payload_bytes 与 payload_hash 不符，resolve 应报损坏而非 None。
        ph = _sha_bytes(payload)
        corrupted_hash = _sha("corrupt-payload")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO topic_material_payload (payload_id, object_type, authority_identity, "
            "version, locator_json, source_content_hash, payload_hash, payload_bytes, "
            "created_dependency_fingerprint, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (corrupted_hash, "evidence_span", "evidence:ev-1", "v1",
             _locator_json(_locator()), _sha("src"),
             corrupted_hash, b"CORRUPTED-BYTES", _sha("dep"), "2026-01-01T00:00:00Z"))
        conn.commit()
        conn.close()
        corrupt_ref = TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
            content_hash=corrupted_hash, locator=_locator(),
            created_dependency_fingerprint=_sha("dep"))
        try:
            resolver.resolve(corrupt_ref)
            check(False, "payload_bytes 重算哈希不符 → 应报 StorageCorruptionError")
        except Store.StorageCorruptionError:
            check(True, "payload_bytes 重算哈希不符 → StorageCorruptionError（非 None）")

    # ------------------------------------------------------------------
    # 7. resolve 字段不符（object_type/version 不一致）→ StorageCorruptionError
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)

        payload = _make_payload("y")
        resolver.commit_payload_batch((_record(payload, version="v1"),))
        ref_wrong_version = TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity="evidence:ev-1", version="v9",
            content_hash=_sha_bytes(payload), locator=_locator(),
            created_dependency_fingerprint=_sha("dep"))
        try:
            resolver.resolve(ref_wrong_version)
            check(False, "version 不符 → 应报 StorageCorruptionError")
        except Store.StorageCorruptionError:
            check(True, "version 不符 → StorageCorruptionError")

        ref_wrong_type = TS.MaterialPayloadRef(
            object_type="table_context", authority_identity="evidence:ev-1", version="v1",
            content_hash=_sha_bytes(payload), locator=_locator(),
            created_dependency_fingerprint=_sha("dep"))
        try:
            resolver.resolve(ref_wrong_type)
            check(False, "object_type 不符 → 应报 StorageCorruptionError")
        except Store.StorageCorruptionError:
            check(True, "object_type 不符 → StorageCorruptionError")

    # ------------------------------------------------------------------
    # 8. resolve object_type 门控：R2 只构建 evidence_span/table_context
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)
        payload = _make_payload("z")
        resolver.commit_payload_batch((_record(payload),))
        for ot in ("structured", "external_snapshot"):
            ref = TS.MaterialPayloadRef(
                object_type=ot, authority_identity="evidence:ev-1", version="v1",
                content_hash=_sha_bytes(payload), locator=_locator(),
                created_dependency_fingerprint=_sha("dep"))
            check(resolver.resolve(ref) is None,
                  f"object_type={ot} → resolve None（R2 不构建，不永久 fail-closed）")

    # ------------------------------------------------------------------
    # 9. Pack schema v2→v3 fail-closed：读 v2 旧 Pack 不静默消费
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "v2.db"
        Store.init_topic_store(db)
        contract_fp = _sha("contract")
        pack_id = "v2-" + _sha("v2-pack")
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO topic_pack (pack_id, schema_version, run_id, task_id, company_id, "
            "report_as_of, contract_version, contract_fingerprint, source_policy_version, "
            "section_id, topic_id, question_ids, outcome_refs, external_funnel, usage, "
            "uncertain_calls, process_status, coverage_status, status_derivation, "
            "dependency_fingerprint, content_fingerprint, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pack_id, "2", "run-v2", "task1", "300750", None, "v1", contract_fp, "v1",
             "company", "t1", '["q1"]', "[]", None, "{}", "[]", "{}", "{}", "{}",
             _sha("dep-v2"), _sha("cfp-v2"), "2025-01-01T00:00:00Z"))
        conn.execute(
            "INSERT INTO topic_current (task_id, company_id, report_as_of, contract_fingerprint, "
            "source_policy_version, section_id, topic_id, pack_id, switched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("task1", "300750", "", contract_fp, "v1", "company", "t1", pack_id,
             "2025-01-01T00:00:00Z"))
        conn.commit()
        conn.close()
        identity = TS.PackIdentity(task_id="task1", company_id="300750", report_as_of=None,
                                   contract_fingerprint=contract_fp, source_policy_version="v1",
                                   section_id="company", topic_id="t1")
        try:
            Store.load_current_pack(identity)
            check(False, "读 v2 Pack 应 fail-closed")
        except Store.SchemaVersionIncompatibleError:
            check(True, "读 v2 Pack → SchemaVersionIncompatibleError（v2→v3 不静默消费）")
        try:
            Store.get_pack(pack_id)
            check(False, "get_pack 读 v2 应 fail-closed")
        except Store.SchemaVersionIncompatibleError:
            check(True, "get_pack 读 v2 → SchemaVersionIncompatibleError")

    # ------------------------------------------------------------------
    # 10. TS.verify_material_payload_ref 集成
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)
        payload = _make_payload("v")
        resolver.commit_payload_batch((_record(payload),))

        ok = TS.verify_material_payload_ref(_ref(payload), resolver)
        check(ok.payload_bytes == payload, "verify_material_payload_ref 成功返回 ResolvedPayload")

        dangling = _ref(b'{"never":1}')
        try:
            TS.verify_material_payload_ref(dangling, resolver)
            check(False, "verify_material_payload_ref dangling 应抛 SchemaValidationError")
        except TS.SchemaValidationError:
            check(True, "verify_material_payload_ref dangling → SchemaValidationError")

    # ------------------------------------------------------------------
    # 11. §五.1 反例#12：信封 evidence_id 与正式重算不一致 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)
        env = json.loads(_make_payload("动力电池").decode("utf-8"))
        env["evidence_id"] = "forged-evidence-id"
        env["authority_identity"] = "evidence:forged-evidence-id"
        tampered = json.dumps(env, ensure_ascii=False).encode("utf-8")
        try:
            resolver.commit_payload_batch((_record(tampered),))
            check(False, "信封 evidence_id 与正式重算不一致应 fail-closed")
        except Store.StorageCorruptionError:
            check(True, "信封 evidence_id 与 make_evidence_id 正式重算不一致 → StorageCorruptionError")

    # ------------------------------------------------------------------
    # 12. §五.1 反例#13：信封 source_content_hash 与 content 重算不一致 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)
        env = json.loads(_make_payload("动力电池").decode("utf-8"))
        forged_ch = _sha("forged-source")
        env["source_content_hash"] = forged_ch
        tampered = json.dumps(env, ensure_ascii=False).encode("utf-8")
        try:
            resolver.commit_payload_batch((_record(tampered, source_content_hash=forged_ch),))
            check(False, "信封 source_content_hash 与 content 重算不一致应 fail-closed")
        except Store.StorageCorruptionError:
            check(True, "信封 source_content_hash 与 content_hash 重算不一致 → StorageCorruptionError")

    # ------------------------------------------------------------------
    # 13. §五.1 反例#14：Resolver 读回损坏信封（缺 document_identity）→ fail-closed（非 dangling）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        resolver = Store.TopicMaterialPayloadResolver(db)
        env = json.loads(_make_payload("w").decode("utf-8"))
        env.pop("document_identity")
        bad_bytes = json.dumps(env, ensure_ascii=False).encode("utf-8")
        bad_hash = _sha_bytes(bad_bytes)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO topic_material_payload (payload_id, object_type, authority_identity, "
            "version, locator_json, source_content_hash, payload_hash, payload_bytes, "
            "created_dependency_fingerprint, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bad_hash, "evidence_span", env["authority_identity"], "v1",
             json.dumps(env["locator"], ensure_ascii=False, separators=(",", ":")),
             env["source_content_hash"], bad_hash, bad_bytes, _sha("dep"),
             "2026-01-01T00:00:00Z"))
        conn.commit()
        conn.close()
        ref = TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity=env["authority_identity"], version="v1",
            content_hash=bad_hash, locator=_locator(), created_dependency_fingerprint=_sha("dep"))
        try:
            resolver.resolve(ref)
            check(False, "Resolver 读回损坏信封应 fail-closed")
        except Store.StorageCorruptionError:
            check(True, "Resolver 读回损坏信封（缺 document_identity）→ StorageCorruptionError（非 None）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
