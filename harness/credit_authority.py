"""R2 P1-E：授信权威链（credit authority chain）。

授信事实的输入必须走正式权威链，不得信任 ``material_index.json + aspect_links.json`` 的
自报字段（Codex 独立审计 E.1–E.4/E.9）：

- **E.9/E.3**：run 必须带一份可校验的 ``run_manifest.json``（``harness.run_manifest``）；
  manifest 绑定当前冻结 Contract v2 / Source Policy v1 / R2 六版本依赖指纹、逐产物内容指纹、
  seed 清单指纹与 DB 身份。manifest 缺失/被改写/依赖指纹非当前实现 → 整体 fail-closed。
- **E.1 material_id 不得自报**：材料身份从**权威 payload 信封**内容寻址重算
  （``recompute_material_id``，与 ``topic_materials.compute_material_id`` 同一规范形），
  index 自报的 material_id 仅作导航，不等即排除。
- **E.1/§五.2 aspect association 必须可验证**：``aspect_links.json`` 的内容指纹来自 manifest，
  但**自报 role 一律不采信**：条目必须被**真实边界决策记录**（``boundary_decisions.json``）
  逐条佐证（aspect_id / evidence_id / disposition / reason_code 逐项相符，且决策记录
  ``content_hash`` == 材料库 ``source_content_hash``）；否则 → 排除。
- **E.2/§五.3 context_candidate 不得直接形成正式事实**：只有 ``role ∈ {source, supporting}``
  且 disposal 落在该 role 的可提升白名单（seed→source；inside_boundary/fragment_projection→
  supporting）时才进入事实提取；``context_candidate`` / sentinel / 空 / 未知一律排除。
- **E.2/E.3**：``ReadonlyEvidenceReader``（evidence.db，严格只读）验证 current document /
  current set；重算 EvidenceBlock 身份（content_hash）。
- **E.4**：fragment 截断点必须严格位于父块内部（``0 < offset < len(parent)``）且正文与
  ``parent[:offset]`` 一致、后缀非空；非片段材料的正文必须与父块正文逐字一致。任何一处不闭合
  → 排除（不再「offset 为 None 就跳过校验」）。
- **E.4 不可变 typed 材料**：解析结果是 frozen dataclass ``ResolvedMaterial``，正文由
  ``payload_bytes`` 派生（property），事后替换可变 dict 的 ``text`` 不可能影响事实提取。

与 ``harness.credit_fact_extraction`` 分工：本模块只做「材料产物 → 权威材料记录」；
「材料 → 事实」仍在 ``credit_fact_extraction``。二者皆零 LLM / 零网络 / 零 DB 写入（只读）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from harness import run_manifest as RM
from harness._readonly_sqlite import open_readonly_conn
from harness.evidence_reader import ReadonlyEvidenceReader
from harness.topic_schema import MaterialPayloadRef, locator_from_dict
from harness.topic_store import TopicMaterialPayloadResolver

CREDIT_AUTHORITY_VERSION = "2"

# 显式提升规则（E.2）：**正式 role ← 允许的边界处置**（白名单，绝不反向由处置推断 role）。
# ``context_candidate``（以及 sentinel/unread/空/未知）**不在任何白名单内** —— 它不得直接
# 形成正式事实，必须由后续（R3 语义事实充分性）重新判定后才可能提升。
#
# 「边界处置」与「aspect role」是两个正交轴（生产侧同理：非 seed 扩读材料一律
# ``context_candidate``，处置另计）：处置说明块落在边界内/外，role 说明该材料是否被显式
# 提升为正式材料。一条关联只有在**自报 role 被某个可提升处置允许**时才可能成为正式材料。
PROMOTION_RULE_VERSION = "1"
ROLE_DISPOSITIONS = {
    "source": frozenset({"seed"}),
    "supporting": frozenset({"inside_boundary", "fragment_projection"}),
}

# 可进入事实提取的正式 role（E.2）。
FORMAL_ROLES = frozenset({"source", "supporting"})

# 需经 manifest 内容指纹校验的产物（E.1）。``boundary_decisions.json`` 是关联资产的
# **独立佐证来源**（§五.2 / §四.D）：关联资产自报的 role 一律不采信，必须由真实边界决策
# 记录逐条佐证后才重算 —— 它不是自报字段，因此其内容指纹必须同样被 manifest 绑定。
RUN_ARTIFACTS = ("material_index.json", "aspect_links.json", "boundary_decisions.json")


@dataclass(frozen=True)
class ResolvedMaterial:
    """不可变 typed 权威材料（E.4）。

    ``text`` **不是**可写字段：它由 ``payload_bytes``（已验字节）派生，任何事后改写
    ``material_index``/可变 dict 都无法替换正文。``payload_bytes`` 与 ``__hash__`` 排除，
    避免无谓的大对象哈希。
    """

    material_id: str
    material_type: str
    role: str
    disposition: str
    promotion_rule_version: str
    promotion_reason: str
    evidence_id: str
    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    source_content_hash: str
    payload_hash: str
    authority_verdict: str
    locator: dict
    structured_payload: object
    fragment_offset: int | None
    fragment_text_verified: bool
    payload_bytes: bytes = field(repr=False, compare=False, hash=False)
    _body: str = field(default="", repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        # 正文只在构造时从已验 payload 字节解出一次，之后不可变。
        object.__setattr__(self, "_body", _envelope_text(self.payload_bytes))

    @property
    def text(self) -> str:
        """材料正文（来自权威 payload 信封；不可事后替换）。"""
        return self._body

    def to_dict(self) -> dict:
        """兼容旧 call-site 的纯 dict 视图（正文仍取不可变 property）。"""
        return {
            "material_id": self.material_id,
            "material_type": self.material_type,
            "role": self.role,
            "disposition": self.disposition,
            "promotion_rule_version": self.promotion_rule_version,
            "promotion_reason": self.promotion_reason,
            "evidence_id": self.evidence_id,
            "company_id": self.company_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "source_content_hash": self.source_content_hash,
            "payload_hash": self.payload_hash,
            "authority_verdict": self.authority_verdict,
            "locator": self.locator,
            "text": self.text,
            "structured_payload": self.structured_payload,
            "payload_bytes": self.payload_bytes,
        }


def _envelope_text(payload_bytes: bytes | None) -> str:
    """从权威 payload 字节解出 ``content.text``（缺字节/非合法信封 → ""，由上游 fail-closed）。"""
    if not payload_bytes:
        return ""
    try:
        env = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(env, dict):
        return ""
    content = env.get("content") or {}
    return str(content.get("text", "") or "")


def _canonical_locator_key(env: dict) -> str:
    """材料身份规范形的 locator 部分（与 ``topic_materials._canonical_locator_key`` 同构）。

    document_id/version/evidence_set_version 取自信封 ``document_identity``（构建侧用的是
    EvidenceBlock 的这三个字段，不在 locator 里）；section_path/page/block_index/table_title/
    offset 取自信封 ``locator``（section_path 已是 joined 字符串）。
    """
    locator = env.get("locator") or {}
    doc = env.get("document_identity") or {}
    block_range = locator.get("block_range") or [None]
    offset = locator.get("offset")
    page = locator.get("page")
    return "|".join([
        str(doc.get("document_id", "") or ""),
        str(doc.get("document_version", "") or ""),
        str(doc.get("evidence_set_version", "") or ""),
        str(locator.get("section_path", "") or ""),
        str(page) if page is not None else "",
        str(block_range[0] if block_range and block_range[0] is not None else ""),
        str(locator.get("table_title", "") or ""),
        str(offset) if offset is not None else "",
    ])


def recompute_material_id(env: dict, payload_bytes: bytes) -> str:
    """从**权威 payload 信封**重算 material_id（E.1：内容寻址，绝不取自 index 自报）。

    与 ``topic_materials.compute_material_id`` 同一规范形：digest of
    ``[material_type, evidence_id, source_identity, document_version, evidence_set_version,
    canonical_locator_key, payload_hash]``。任一字段不自洽 → 抛 ValueError（调用方 fail-closed）。
    """
    if not isinstance(env, dict):
        raise ValueError("信封非对象")
    locator = env.get("locator") or {}
    if not isinstance(locator, dict):
        raise ValueError("信封 locator 非对象")
    doc_identity = env.get("document_identity") or {}
    material_type = str(env.get("object_type", "") or "")
    evidence_id = str(env.get("evidence_id", "") or "")
    source_identity = str(env.get("authority_identity", "") or "")
    if not material_type or not evidence_id or not source_identity:
        raise ValueError("信封缺 object_type/evidence_id/authority_identity")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    identity = [
        material_type, evidence_id, source_identity,
        str(doc_identity.get("document_version", "") or ""),
        str(doc_identity.get("evidence_set_version", "") or ""),
        _canonical_locator_key(env),
        payload_hash,
    ]
    return "mat-" + hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, separators=(",", ":"),
        sort_keys=True).encode("utf-8")).hexdigest()[:32]


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


def _verify_body(reader: ReadonlyEvidenceReader, evidence_id: str, offset,
                 content_text: str, disposition: str) -> tuple[bool, str, bool]:
    """正文与截断点校验（E.4）。返回 (ok, reason, fragment_verified)。

    - 非片段（``offset is None``）：正文必须与父块正文**逐字一致**（不再只看 hash 推断）。
    - 片段（``offset`` 非 None）：``fragment_projection`` 必须有严格内部截断点
      ``0 < offset < len(parent)``，正文 == ``parent[:offset]``（去空白比较），且后缀非空；
      其余 disposition 带 offset 同样按严格片段校验（不因 disposition 不同而放行）。
    """
    block = reader.get_block(evidence_id)
    if block is None:
        return False, "父块缺失", False
    parent_text = block.text or ""
    if offset is None:
        if disposition == "fragment_projection":
            return False, "fragment_projection 缺 offset（截断点不可验证）", False
        if content_text != parent_text:
            return False, "非片段正文与父块正文不一致（正文被替换）", False
        return True, "", False
    if not isinstance(offset, int) or isinstance(offset, bool):
        return False, "offset 非整数", False
    if not (0 < offset < len(parent_text)):
        return False, f"offset={offset} 非严格内部截断点（0<offset<len(父块)={len(parent_text)}）", False
    if not (parent_text[offset:]).strip():
        return False, "截断点之后无内容（不是内部截断点）", False
    if parent_text[:offset].strip() != (content_text or "").strip():
        return False, "片段正文与父块 text[:offset] 重算不符（offset 与来源身份不一致）", False
    return True, "", True


def _resolve_one(resolver: TopicMaterialPayloadResolver, reader: ReadonlyEvidenceReader,
                 entry: dict, company_id: str, *, dependency_fingerprint: str,
                 role: str, disposition: str) -> ResolvedMaterial | None:
    """解析单个材料到不可变权威记录；任一环节不闭合 → None（per-material fail-closed）。"""
    payload_hash = entry.get("payload_hash", "")
    if not payload_hash:
        return None

    # 1. 权威 payload 行（candidate navigation 只提供 payload_hash；身份/字节从 store 取）。
    row = _read_payload_row(resolver._db_path, payload_hash)
    if row is None:
        return None  # dangling：权威 store 无该 payload → 不产出事实
    # 1b. E.3：payload 行必须由**同一依赖束**产出（Contract/SourcePolicy/R2 dependency 绑定）。
    if row["created_dependency_fingerprint"] != dependency_fingerprint:
        return None
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

    # 3. E.1：material_id 必须等于从**权威信封**内容寻址重算的身份（自报一律不采信）。
    try:
        recomputed_mid = recompute_material_id(env, resolved.payload_bytes)
    except ValueError:
        return None
    if entry.get("material_id", "") != recomputed_mid:
        return None

    # 4. 跨公司串读 fail-closed。
    if material_company_id != company_id:
        return None

    # 5. current document / current set（E.2）。
    if not document_id or not document_version or not evidence_set_version:
        return None
    cur_doc_ver = reader.current_document_version(company_id, document_id)
    cur_set = reader.current_evidence_set(company_id, document_id, document_version)
    if cur_doc_ver is None or cur_doc_ver != document_version:
        return None
    if cur_set is None or cur_set != evidence_set_version:
        return None

    # 6. EvidenceBlock 身份重算（E.2）。
    if not _verify_block_identity(reader, evidence_id, source_content_hash):
        return None

    # 7. 正文/截断点校验（E.4）。
    offset = locator.get("offset")
    ok_body, _reason, frag_verified = _verify_body(
        reader, evidence_id, offset, str(content.get("text", "") or ""), disposition)
    if not ok_body:
        return None

    return ResolvedMaterial(
        material_id=recomputed_mid,
        material_type=str(env.get("object_type", "") or ""),
        role=role,
        disposition=disposition,
        promotion_rule_version=PROMOTION_RULE_VERSION,
        promotion_reason=("seed：真实来源（source）" if role == "source"
                          else "边界已验证的正式材料，经显式提升规则提升为 supporting"),
        evidence_id=evidence_id,
        company_id=material_company_id,
        document_id=document_id,
        document_version=document_version,
        evidence_set_version=evidence_set_version,
        source_content_hash=source_content_hash,
        payload_hash=payload_hash,
        authority_verdict="authoritative",
        locator=locator,
        structured_payload=content.get("structured_payload"),
        fragment_offset=offset,
        fragment_text_verified=frag_verified,
        payload_bytes=resolved.payload_bytes,
    )


def _read_json_asset(path: Path):
    """读已验指纹的 JSON 资产；缺失/不可读/非合法 JSON → ``None``（调用方 fail-closed）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _linked_roles(run_dir: Path, aspect_id: str, manifest: dict,
                  index_by_material_id: dict[str, dict]) -> dict[str, dict]:
    """取目标 aspect 的**经独立佐证**的正式关联（§五.2 / §四.D：绝不采信关联资产自报）。

    ``aspect_links.json`` 只是关联资产（其内容指纹由 run manifest 绑定，不可被事后改写），
    但其自报的 ``role`` **一律不采信**：role 必须由**运行自身的真实边界决策记录**
    （``boundary_decisions.json``）逐条佐证后才成立。

    逐条目接受条件（任一不满足 → 该关联排除）：
    1. ``aspect_id`` 命中目标 aspect，``material_id`` 非空；
    2. 自报 ``role`` ∈ 正式 role（``source`` / ``supporting``）—— ``context_candidate``
       一律排除（§五.3：context_candidate 不得直接产生正式事实）；
    3. ``disposition`` 必须落在该 role 的**可提升处置白名单**内（``seed`` → source；
       ``inside_boundary`` / ``fragment_projection`` → supporting）：非提升处置
       （context_candidate / sentinel / 空 / 未知）不得产生任何正式 role；
    4. ``material_index`` 中该材料的 ``boundary_disposition`` 必须与之一致（材料库不许与
       关联资产互相矛盾）；
    5. ``boundary_disposition_identity`` 必须给出 (aspect_id, evidence_id) 生产身份，且在
       真实 ``decisions`` 中存在逐项相符的决策记录（aspect_id / evidence_id / disposition /
       reason_code 非空），且该记录的 ``content_hash`` == 材料库自报 ``source_content_hash``
       —— 把「关联资产」钉到材料**真实块内容 hash**（该 hash 稍后在 ``_resolve_one`` 中还会
       对 evidence.db 重算复核）；
    6. 片段规则例外：link ``disposition == fragment_projection``（材料级处置）时，允许决策
       记录给出其边界有效处置（``inside_boundary`` 等），但该处置本身必须仍在提升白名单内
       （绝不接受 context_candidate/sentinel/unknown）。

    提升规则身份由**本模块权威常量**给出（``PROMOTION_RULE_VERSION``），不依赖生产者自报的
    版本字段 —— 生产者不产出该字段，且自报版本本身也不构成独立佐证。
    """
    links = _read_json_asset(run_dir / "aspect_links.json")
    decisions_asset = _read_json_asset(run_dir / "boundary_decisions.json")
    if not isinstance(links, list) or not isinstance(decisions_asset, dict):
        return {}
    raw_decisions = decisions_asset.get("decisions")
    if not isinstance(raw_decisions, list):
        return {}
    real_decisions = [d for d in raw_decisions if isinstance(d, dict)]

    out: dict[str, dict] = {}
    for link in links:
        if not isinstance(link, dict) or link.get("aspect_id") != aspect_id:
            continue
        material_id = str(link.get("material_id") or "")
        if not material_id:
            continue
        role = str(link.get("role") or "")
        allowed = ROLE_DISPOSITIONS.get(role)
        if not allowed:
            continue
        disposition = str(link.get("disposition") or "")
        if disposition not in allowed:
            continue
        index_entry = index_by_material_id.get(material_id)
        if not isinstance(index_entry, dict):
            continue
        if str(index_entry.get("boundary_disposition") or "") != disposition:
            continue
        source_content_hash = str(index_entry.get("source_content_hash") or "")
        if not source_content_hash:
            continue
        identity = link.get("boundary_disposition_identity")
        if not isinstance(identity, list) or not identity:
            continue
        if not _corroborated_by_decisions(
                real_decisions, aspect_id=aspect_id, identity=identity,
                link_disposition=disposition, source_content_hash=source_content_hash):
            continue
        out[material_id] = {**link, "promotion_rule_version": PROMOTION_RULE_VERSION}
    return out


def _corroborated_by_decisions(real_decisions: list[dict], *, aspect_id: str,
                               identity: list, link_disposition: str,
                               source_content_hash: str) -> bool:
    """关联资产条目是否被真实边界决策记录逐条佐证（见 ``_linked_roles`` 条件 5/6）。"""
    for rec in identity:
        if not isinstance(rec, dict) or rec.get("aspect_id") != aspect_id:
            continue
        evidence_id = str(rec.get("evidence_id") or "")
        if not evidence_id:
            continue
        rec_disposition = str(rec.get("disposition") or "")
        rec_reason = str(rec.get("reason_code") or "")
        # 条件 6：材料级 disposition 与决策处置的允许差异**仅限**片段投影规则白名单内。
        if rec_disposition != link_disposition:
            if not (link_disposition == "fragment_projection"
                    and rec_disposition in ROLE_DISPOSITIONS["supporting"]):
                continue
        if not rec_reason:
            continue
        for d in real_decisions:
            if (str(d.get("aspect_id") or "") == aspect_id
                    and str(d.get("evidence_id") or "") == evidence_id
                    and str(d.get("disposition") or "") == rec_disposition
                    and str(d.get("reason_code") or "") == rec_reason
                    and str(d.get("content_hash") or "") == source_content_hash):
                return True
    return False


def resolve_credit_materials(run_dir: str | Path, *, harness_db_path: str | Path,
                             evidence_db_path: str | Path,
                             company_id: str, aspect_id: str) -> list[ResolvedMaterial]:
    """从真实 R2 材料 run 目录解析权威授信材料（走正式权威链，E.1–E.4/E.9）。

    - run manifest 必须存在且校验通过（依赖指纹 = 当前 Contract/SourcePolicy/R2 实现，
      逐产物内容指纹相符）；否则整体 fail-closed（返回空，绝不「尽力解析」）；
    - ``material_index.json`` 只作 candidate navigation（material_id/payload_hash/
      boundary_disposition），身份从权威信封内容寻址重算，正文从权威 payload 字节取；
    - 只接受 ``role ∈ {source, supporting}`` 且 role 与 disposition 提升规则一致的关联；
    - 未知/空 disposition（含 context_candidate）、dangling、依赖指纹不符、损坏信封、
      非 current doc/set、跨公司串读、正文/截断点不闭合 → 逐材料 fail-closed。
    """
    run_dir = Path(run_dir)
    ok, _reason, manifest = RM.verify_run_manifest(run_dir, artifacts=RUN_ARTIFACTS)
    if not ok:
        return []
    index_path = run_dir / "material_index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(index, list):
        return []

    index_by_material_id = {
        str(e.get("material_id") or ""): e for e in index if isinstance(e, dict)
        and e.get("material_id")}
    linked = _linked_roles(run_dir, aspect_id, manifest, index_by_material_id)
    if not linked:
        return []

    resolver = TopicMaterialPayloadResolver(harness_db_path)
    reader = ReadonlyEvidenceReader(evidence_db_path)
    dependency_fingerprint = manifest.get("dependency_fingerprint", "")

    materials: list[ResolvedMaterial] = []
    for entry in index:
        if not isinstance(entry, dict):
            continue
        material_id = entry.get("material_id", "")
        link = linked.get(material_id)
        if link is None:
            continue  # E.1/E.2：缺经独立佐证的正式 aspect association → 不产出
        disposition = str(link.get("disposition") or "")
        role = str(link.get("role") or "")
        # E.2/§五.3：role 与 disposition 必须落在提升白名单内（``_linked_roles`` 已核，此处复核）。
        if role not in FORMAL_ROLES or disposition not in ROLE_DISPOSITIONS.get(role, ()):
            continue
        if str(entry.get("boundary_disposition") or "") != disposition:
            continue
        resolved = _resolve_one(resolver, reader, entry, company_id,
                                dependency_fingerprint=dependency_fingerprint,
                                role=role, disposition=disposition)
        if resolved is None:
            continue
        materials.append(resolved)
    return materials
