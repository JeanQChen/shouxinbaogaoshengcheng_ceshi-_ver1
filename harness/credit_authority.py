"""R2 P1-E：授信权威链（credit authority chain）。

授信事实的输入必须走正式权威链，不得信任 ``material_index.json + payload_preview/*.json``
的复制字段（Codex 独立审计 E.1）：

- 预览/提取入口经正式 ``TopicMaterialPayloadResolver``（harness.db，严格只读）解析权威
  payload 字节——绝不读 ``payload_preview/*.json`` 的复制副本；
- 经 ``ReadonlyEvidenceReader``（evidence.db，严格只读）验证 current document / current set；
- 逐材料重算 EvidenceBlock 身份（content_hash / evidence_id），fragment 校验实际 fragment
  bytes + offset + 来源身份（禁止跳过正文来源重算，E.3）；
- 消费正式 aspect association（``aspect_links.json``），证明材料属于授信 Topic/aspect（E.4）；
- 未知/空/不合格 disposition 一律 fail-closed（E.4）。

与 ``harness.credit_fact_extraction`` 分工：本模块只做「材料产物 → 权威材料记录」；
「材料 → 事实」仍在 ``credit_fact_extraction``。二者皆零 LLM / 零网络 / 零 DB 写入（只读）。
"""

from __future__ import annotations

import json
from pathlib import Path

from evidence import ids
from harness._readonly_sqlite import open_readonly_conn
from harness.topic_schema import MaterialPayloadRef, locator_from_dict
from harness.topic_store import TopicMaterialPayloadResolver
from harness.evidence_reader import ReadonlyEvidenceReader

CREDIT_AUTHORITY_VERSION = "1"

# 正式（可采纳）边界处置；其余（sentinel/unread/rejected/rolled_back/duplicate/空/未知）
# 一律 fail-closed（E.4）。
FORMAL_DISPOSITIONS = frozenset({
    "seed",
    "inside_boundary",
    "context_candidate",
    "fragment_projection",
})


def _read_payload_row(harness_db_path: str | Path, payload_id: str):
    """严格只读读取 topic_material_payload 行（缺库/缺行 → None，绝不建库/写库）。"""
    conn = open_readonly_conn(harness_db_path)
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT * FROM topic_material_payload WHERE payload_id=?", (payload_id,)
        ).fetchone()
    finally:
        conn.close()


def _build_payload_ref(row) -> MaterialPayloadRef:
    """从权威 payload 行重建不可变解析引用（locator/fingerprint 取自行，不取自预览复制）。"""
    return MaterialPayloadRef(
        object_type=row["object_type"],
        authority_identity=row["authority_identity"],
        version=row["version"],
        content_hash=row["payload_hash"],
        locator=locator_from_dict(json.loads(row["locator_json"])),
        created_dependency_fingerprint=row["created_dependency_fingerprint"],
    )


def _verify_block_identity(reader: ReadonlyEvidenceReader, evidence_id: str,
                           source_content_hash: str) -> bool:
    """EvidenceBlock 身份重算：块存在且 content_hash == source_content_hash。"""
    block = reader.get_block(evidence_id)
    if block is None:
        return False
    return block.content_hash == source_content_hash


def _verify_fragment(reader: ReadonlyEvidenceReader, evidence_id: str,
                     offset: int | None, fragment_text: str) -> tuple[bool, str]:
    """fragment 实际 bytes / offset / 来源身份校验（E.3）。

    - 非片段（offset is None）：无额外片段校验（块身份由 _verify_block_identity 覆盖）。
    - 片段（offset 非 None）：fragment 正文必须是父块 ``text[:offset]`` 的主题内前缀。
    """
    if offset is None:
        return True, ""
    block = reader.get_block(evidence_id)
    if block is None:
        return False, "fragment 父块缺失"
    prefix = (block.text or "")[:offset].strip()
    frag = (fragment_text or "").strip()
    if prefix != frag:
        return False, "fragment 正文与父块 text[:offset] 重算不符（offset 与来源身份不一致）"
    return True, ""


def _resolve_one(resolver: TopicMaterialPayloadResolver, reader: ReadonlyEvidenceReader,
                 entry: dict, company_id: str) -> dict | None:
    """解析单个材料到权威记录；任一环节不闭合 → 返回 None（per-material fail-closed）。

    - 权威 payload 经 ``TopicMaterialPayloadResolver.resolve``（含信封 + evidence_id 正式重算）；
    - current document/current set 经 ReadonlyEvidenceReader 验证；
    - 父块身份 content_hash 重算 + fragment 前缀校验；
    - 材料 company_id 必须等于请求 company_id（跨公司串读 fail-closed）。
    """
    payload_hash = entry.get("payload_hash", "")
    if not payload_hash:
        return None

    # 1. 权威 payload 行（candidate navigation 只提供 payload_hash；身份/字节从 store 取）。
    row = _read_payload_row(resolver._db_path, payload_hash)
    if row is None:
        return None  # dangling：权威 store 无该 payload → 不产出事实
    try:
        ref = _build_payload_ref(row)
        resolved = resolver.resolve(ref)
    except Exception:
        return None  # 信封/身份损坏 → fail-closed
    if resolved is None or resolved.payload_bytes is None:
        return None

    # 2. 信封解析（复用权威 payload 字节，绝不读预览复制）。
    try:
        env = json.loads(resolved.payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(env, dict):
        return None
    content = env.get("content") or {}
    locator = env.get("locator") or {}
    doc_identity = env.get("document_identity") or {}
    evidence_id = env.get("evidence_id", "")
    source_content_hash = env.get("source_content_hash", "")
    document_id = str(doc_identity.get("document_id", "") or "")
    document_version = str(doc_identity.get("document_version", "") or "")
    evidence_set_version = str(doc_identity.get("evidence_set_version", "") or "")
    material_company_id = str(doc_identity.get("company_id", "") or "")

    # 3. 跨公司串读 fail-closed。
    if material_company_id != company_id:
        return None

    # 4. current document / current set（E.2）。
    if not document_id or not document_version or not evidence_set_version:
        return None
    cur_doc_ver = reader.current_document_version(company_id, document_id)
    cur_set = reader.current_evidence_set(company_id, document_id, document_version)
    if cur_doc_ver is None or cur_doc_ver != document_version:
        return None
    if cur_set is None or cur_set != evidence_set_version:
        return None

    # 5. EvidenceBlock 身份重算（E.2/E.3）。
    if not _verify_block_identity(reader, evidence_id, source_content_hash):
        return None
    offset = locator.get("offset")
    ok_frag, _ = _verify_fragment(reader, evidence_id, offset, str(content.get("text", "")))
    if not ok_frag:
        return None

    return {
        "material_id": entry.get("material_id", ""),
        "evidence_id": evidence_id,
        "company_id": material_company_id,
        "document_id": document_id,
        "document_version": document_version,
        "evidence_set_version": evidence_set_version,
        "source_content_hash": source_content_hash,
        "payload_hash": payload_hash,
        "authority_verdict": "authoritative",
        "locator": locator,
        "text": content.get("text", ""),
        "structured_payload": content.get("structured_payload"),
        "disposition": entry.get("boundary_disposition", ""),
        "payload_bytes": resolved.payload_bytes,
    }


def resolve_credit_materials(run_dir: str | Path, *, harness_db_path: str | Path,
                             evidence_db_path: str | Path,
                             company_id: str, aspect_id: str) -> list[dict]:
    """从真实 R2 材料 run 目录解析权威授信材料（走正式权威链，E.1）。

    - ``material_index.json`` 仅作 candidate navigation（material_id / payload_hash /
      boundary_disposition），身份与正文从权威 store 取，绝不信任预览复制字段；
    - ``aspect_links.json`` 消费正式 aspect association：只返回属于 ``aspect_id`` 的材料；
    - 未知/空/不合格 disposition、缺 aspect 关联、dangling、损坏信封、非 current doc/set、
      跨公司串读 → 逐材料 fail-closed（不产出该材料）。
    """
    run_dir = Path(run_dir)
    index_path = run_dir / "material_index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"材料 run 目录缺 material_index.json: {run_dir}")
    index = json.loads(index_path.read_text(encoding="utf-8"))

    # 正式 aspect association（E.4）：证明材料属于授信 Topic/aspect。
    links_path = run_dir / "aspect_links.json"
    linked_material_ids: set[str] = set()
    if links_path.exists():
        links = json.loads(links_path.read_text(encoding="utf-8"))
        linked_material_ids = {
            l.get("material_id", "") for l in links
            if isinstance(l, dict) and l.get("aspect_id") == aspect_id
        }

    resolver = TopicMaterialPayloadResolver(harness_db_path)
    reader = ReadonlyEvidenceReader(evidence_db_path)

    materials: list[dict] = []
    for entry in index:
        if not isinstance(entry, dict):
            continue
        material_id = entry.get("material_id", "")
        # E.4：必须消费正式 aspect association。
        if material_id not in linked_material_ids:
            continue
        # E.4：未知/空/不合格 disposition 一律 fail-closed。
        if entry.get("boundary_disposition", "") not in FORMAL_DISPOSITIONS:
            continue
        resolved = _resolve_one(resolver, reader, entry, company_id)
        if resolved is None:
            continue
        materials.append(resolved)
    return materials
