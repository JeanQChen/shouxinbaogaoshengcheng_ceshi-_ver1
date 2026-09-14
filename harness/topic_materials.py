"""R2：atomic ResearchMaterial 构建 + MaterialAssembly/TableAssembly 投影（§4.6/§4.7/§4.10/§9）。

一个正式 ``ResearchMaterial`` 对应一个真实 ``EvidenceBlock``（atomic），绝不把多个 Block
合成一份 material/payload；相邻/跨页/表题-单位-表头-表体-续表/交叉引用的组合关系只经
``MaterialAssembly`` / ``TableAssembly`` 表达（过程侧投影，不产生新的材料类型/权威来源，
不进入第四套材料类型）。

双哈希两层身份（§6.2）：
- ``source_content_hash = EvidenceBlock.content_hash``（来源层，写入
  ``EvidenceAuthorityAssessment.content_hash``，证明对应哪一条真实 EvidenceBlock）；
- ``payload_hash = sha256(payload_bytes)``（载体层，``payload_id == payload_hash``，写入
  ``MaterialPayloadRef.content_hash`` 与 ``ResearchMaterial.content_hash``）。
- payload 信封**不含自身最终 payload_hash**（否则自引用）；``source_content_hash`` 与
  ``payload_hash`` 是两个不同身份层，不相等。

本模块是纯转换（扩读结果 → 材料 + payload batch），不直接调用 ``ReadonlyEvidenceReader``、
不建 ToolRegistry/Reader 循环；``is_current_document/is_current_set`` 由调用方（验收 runner）
经 bounded Evidence 工具的 verify_seed ToolResult 决定后传入。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from harness import topic_schema as TS
from harness.context_expansion import (
    ContextExpansionTrace,
    ExpansionResult,
    UnreadScope,
)
from harness.evidence_reader import EvidenceReadResult
from harness.topic_store import MaterialPayloadRecord

# material payload 信封版本（用于 payload envelope 的 material_payload_version 与
# MaterialPayloadRef.version / MaterialPayloadRecord.version 的同一常量）。
MATERIAL_PAYLOAD_VERSION = "1"


# ---------------------------------------------------------------------------
# 过程侧类型（§4）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaterialAssembly:
    """过程侧组合投影（§4.10）：只引用 component_material_ids，本身不是新权威/新材料类型。"""

    assembly_id: str
    context_parent_id: str            # 组合根（通常为 seed material 的 material_id）
    component_material_ids: tuple[str, ...]
    relation: str                     # adjacent | cross_page | table_chain | reference
    boundary_desc: str


@dataclass(frozen=True)
class TableAssembly(MaterialAssembly):
    table_title: str
    unit: str | None
    header_evidence_id: str | None
    body_evidence_ids: tuple[str, ...]
    continuation_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class AspectMaterialLink:
    """aspect ↔ material 绑定（§4.7）。"""

    aspect_id: str
    material_ids: tuple[str, ...]
    role: str                         # source | supporting


@dataclass(frozen=True)
class BuiltMaterial:
    """一次 atomic material 构建产物：material + 待持久化的 payload record（双哈希闭环）。"""

    material: TS.ResearchMaterial
    payload_record: MaterialPayloadRecord


@dataclass(frozen=True)
class MaterialBuildResult:
    """R2 最终返回（§4.6）：材料 + assembly + aspect 绑定 + 扩读 trace/未读范围/预算。

    ``payload_records`` 是本次构建新增的 atomic payload 批（§6.4：同一次 MaterialBuildResult
    的新增 payload 经 ``commit_payload_batch`` 单事务原子提交，任一失败整体回滚）。
    """

    materials: tuple[TS.ResearchMaterial, ...]
    assemblies: tuple[MaterialAssembly, ...]
    aspect_links: tuple[AspectMaterialLink, ...]
    trace: ContextExpansionTrace
    unread_scope: UnreadScope
    stop_reason: str
    budget_consumed: dict
    payload_records: tuple[MaterialPayloadRecord, ...]


# ---------------------------------------------------------------------------
# 身份规范形（§9.1）
# ---------------------------------------------------------------------------

def section_path_joined(section_path: tuple[str, ...]) -> str:
    """读取侧 ``tuple[str, ...]`` → EvidenceLocator.section_path 的稳定字符串。"""
    return " / ".join(section_path)


def _canonical_locator_key(block: EvidenceReadResult, material_type: str,
                           table_title: str | None) -> str:
    return "|".join([
        block.document_id, block.document_version, block.evidence_set_version,
        section_path_joined(block.section_path),
        str(block.page_number), str(block.block_index),
        table_title or "",
    ])


def compute_material_id(material_type: str, evidence_id: str, source_identity: str,
                        document_version: str, evidence_set_version: str,
                        canonical_locator_key: str, payload_hash: str) -> str:
    """材料身份规范形 → ``material_id``（§9.1，不含 run_id/时间戳/call_id）。"""
    identity = [
        material_type, evidence_id, source_identity, document_version,
        evidence_set_version, canonical_locator_key, payload_hash,
    ]
    return "mat-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# atomic material 构建（§6.2/§9.2）
# ---------------------------------------------------------------------------

def _build_locator(block: EvidenceReadResult, material_type: str,
                   table_title: str | None) -> TS.EvidenceLocator:
    return TS.EvidenceLocator(
        document_id=block.document_id,
        document_version=block.document_version,
        section_path=section_path_joined(block.section_path),
        page=block.page_number,
        table_title=table_title,
        block_range=(block.block_index, block.block_index),
    )


def _build_envelope(block: EvidenceReadResult, material_type: str,
                    source_identity: str, locator: TS.EvidenceLocator,
                    dependency_fingerprint: str) -> dict:
    """payload 规范 JSON 信封（§6.2）；不含自身 payload_hash。"""
    return {
        "material_payload_version": 1,
        "object_type": material_type,
        "authority_identity": source_identity,
        "document_identity": {
            "document_id": block.document_id,
            "document_version": block.document_version,
            "evidence_set_version": block.evidence_set_version,
        },
        "locator": locator.to_dict(),
        "evidence_id": block.evidence_id,
        "source_content_hash": block.content_hash,
        "content": {
            "text": block.text,
            "structured_payload": block.structured_payload,
            "evidence_type": block.evidence_type,
        },
        "created_dependency_fingerprint": dependency_fingerprint,
    }


def build_atomic_material(block: EvidenceReadResult, *,
                          material_type: str,
                          is_current_document: bool,
                          is_current_set: bool,
                          dependency_fingerprint: str,
                          table_title: str | None = None,
                          context_parent_id: str | None = None) -> BuiltMaterial:
    """把单个 EvidenceBlock 构建为一个 atomic ResearchMaterial + payload record。

    - 双哈希：source_content_hash == block.content_hash（来源层）；payload_hash ==
      sha256(payload_bytes)（载体层，== payload_id == material.content_hash == payload_ref.content_hash）。
    - authority 由 ``recompute_authority_verdict`` 确定性重算（不信任自填）。
    """
    if material_type not in ("evidence_span", "table_context"):
        raise ValueError(f"R2 只构建 evidence_span/table_context，得到 {material_type!r}")

    locator = _build_locator(block, material_type, table_title)
    source_identity = f"evidence:{block.evidence_id}"

    # authority（§9.2）：evidence_id/document/company/page/block 边界精确取自该 Block。
    authority = TS.EvidenceAuthorityAssessment(
        evidence_id=block.evidence_id,
        document_id=block.document_id,
        document_version=block.document_version,
        company_id=block.company_id,
        is_current_document=is_current_document,
        is_current_set=is_current_set,
        page=block.page_number,
        block_range=(block.block_index, block.block_index),
        fetched_inspected_nonempty=True,
        content_hash=block.content_hash,
        verdict="rejected",  # 占位；下面由 recompute_authority_verdict 确定性重算
        reason="",
        validator_version="",
    )
    authority = TS.EvidenceAuthorityAssessment(
        evidence_id=authority.evidence_id,
        document_id=authority.document_id,
        document_version=authority.document_version,
        company_id=authority.company_id,
        is_current_document=authority.is_current_document,
        is_current_set=authority.is_current_set,
        page=authority.page,
        block_range=authority.block_range,
        fetched_inspected_nonempty=authority.fetched_inspected_nonempty,
        content_hash=authority.content_hash,
        verdict=TS.recompute_authority_verdict(authority),
        reason="" if authority.is_current_document and authority.is_current_set
        else "非 current document/set",
        validator_version="",
    )

    envelope = _build_envelope(block, material_type, source_identity, locator,
                               dependency_fingerprint)
    payload_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"),
                               sort_keys=True).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    canonical_locator_key = _canonical_locator_key(block, material_type, table_title)
    material_id = compute_material_id(
        material_type, block.evidence_id, source_identity,
        block.document_version, block.evidence_set_version,
        canonical_locator_key, payload_hash)

    payload_ref = TS.MaterialPayloadRef(
        object_type=material_type,
        authority_identity=source_identity,
        version=MATERIAL_PAYLOAD_VERSION,
        content_hash=payload_hash,
        locator=locator,
        created_dependency_fingerprint=dependency_fingerprint,
    )

    material = TS.ResearchMaterial(
        material_id=material_id,
        material_type=material_type,
        source_identity=source_identity,
        locator=locator,
        payload_ref=payload_ref,
        content_hash=payload_hash,
        authority_assessment=authority,
        context_parent_id=context_parent_id,
    )

    payload_record = MaterialPayloadRecord(
        payload_id=payload_hash,
        object_type=material_type,
        authority_identity=source_identity,
        version=MATERIAL_PAYLOAD_VERSION,
        locator_json=json.dumps(locator.to_dict(), ensure_ascii=False,
                                separators=(",", ":"), sort_keys=True),
        source_content_hash=block.content_hash,
        payload_hash=payload_hash,
        payload_bytes=payload_bytes,
        created_dependency_fingerprint=dependency_fingerprint,
    )

    return BuiltMaterial(material=material, payload_record=payload_record)


# ---------------------------------------------------------------------------
# 扩读结果 → MaterialBuildResult（去重 + 稳定排序 + assembly + aspect link）
# ---------------------------------------------------------------------------

def _material_sort_key(material: TS.ResearchMaterial) -> tuple:
    loc = material.locator
    page = loc.page if isinstance(loc, TS.EvidenceLocator) else None
    block_index = (loc.block_range[0] if isinstance(loc, TS.EvidenceLocator)
                   and loc.block_range else None)
    return (
        material.material_type,
        material.authority_assessment.document_id,
        material.authority_assessment.document_version,
        page if page is not None else -1,
        block_index if block_index is not None else -1,
        getattr(loc, "section_path", "") if isinstance(loc, TS.EvidenceLocator) else "",
        material.material_id,
    )


def _table_title_for(block: EvidenceReadResult) -> str | None:
    if block.evidence_type == "table":
        # 表题：优先取 table 块正文；正文为空则回退 structured_payload 无标题。
        if block.text.strip():
            return block.text.strip()
        return None
    return None


def build_material_result(expansion: ExpansionResult, *,
                          dependency_fingerprint: str,
                          is_current_document: bool,
                          is_current_set: bool,
                          aspect_id: str | None = None) -> MaterialBuildResult:
    """把一次扩读结果转换为 MaterialBuildResult（去重 + 稳定排序 + assembly + aspect link）。

    去重：以 material_id（§9.1 完整 tuple 规范形）为键；同一 Block 只产一个 atomic material；
    相同文本但不同来源/版本/位置 → 不同 material（不合并）。
    """
    adopted = expansion.adopted
    # 去重（按 evidence_id，因一个 Block 一个 material）；保持首次出现的稳定序。
    blocks_by_id: dict[str, EvidenceReadResult] = {}
    for b in adopted:
        blocks_by_id.setdefault(b.evidence_id, b)

    built: dict[str, BuiltMaterial] = {}
    for block in blocks_by_id.values():
        if block.evidence_type in ("table", "table_row"):
            material_type = "table_context"
        else:
            material_type = "evidence_span"
        bm = build_atomic_material(
            block, material_type=material_type,
            is_current_document=is_current_document,
            is_current_set=is_current_set,
            dependency_fingerprint=dependency_fingerprint,
            table_title=_table_title_for(block))
        built[bm.material.material_id] = bm

    materials = tuple(sorted(
        (bm.material for bm in built.values()), key=_material_sort_key))

    assemblies = build_assemblies(materials, blocks_by_id)

    aspect_links: tuple[AspectMaterialLink, ...] = ()
    if aspect_id is not None:
        seed_material_id = next(
            (m.material_id for m in materials
             if m.authority_assessment.evidence_id == expansion.seed.evidence_id),
            None)
        supporting = tuple(
            m.material_id for m in materials
            if m.authority_assessment.evidence_id != expansion.seed.evidence_id)
        links: list[AspectMaterialLink] = []
        if seed_material_id is not None:
            links.append(AspectMaterialLink(
                aspect_id=aspect_id, material_ids=(seed_material_id,), role="source"))
        if supporting:
            links.append(AspectMaterialLink(
                aspect_id=aspect_id, material_ids=supporting, role="supporting"))
        aspect_links = tuple(links)

    return MaterialBuildResult(
        materials=materials,
        assemblies=assemblies,
        aspect_links=aspect_links,
        trace=expansion.trace,
        unread_scope=expansion.unread_scope,
        stop_reason=expansion.stop_reason,
        budget_consumed=expansion.budget_consumed,
        payload_records=tuple(bm.payload_record for bm in built.values()),
    )


# ---------------------------------------------------------------------------
# assembly 投影（§4.10）
# ---------------------------------------------------------------------------

def _assembly_id(component_material_ids: tuple[str, ...], relation: str) -> str:
    raw = json.dumps([relation, list(component_material_ids)], ensure_ascii=False,
                     separators=(",", ":"), sort_keys=True)
    return "asm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _materials_by_evidence_id(materials: tuple[TS.ResearchMaterial, ...]) -> dict[str, TS.ResearchMaterial]:
    return {m.authority_assessment.evidence_id: m for m in materials}


def build_assemblies(materials: tuple[TS.ResearchMaterial, ...],
                     blocks_by_evidence_id: dict[str, EvidenceReadResult],
                     ) -> tuple[MaterialAssembly, ...]:
    """从一组 atomic materials + 其 Block 派生组合投影（表链 / 相邻链）。

    assembly 只引用 ``component_material_ids``（真实 atomic material），本身不产生 payload、
    不产生新权威；``context_parent_id`` 为组合根 material_id。
    """
    mat_by_ev = _materials_by_evidence_id(materials)
    assemblies: list[MaterialAssembly] = []

    # ---- TableAssembly：table_context materials 的连续表链 ----
    table_mats = [m for m in materials if m.material_type == "table_context"]
    table_mats_sorted = sorted(
        table_mats, key=lambda m: (m.authority_assessment.page or -1,
                                   (m.locator.block_range[0]
                                    if isinstance(m.locator, TS.EvidenceLocator)
                                    and m.locator.block_range else -1)))
    runs: list[list[TS.ResearchMaterial]] = []
    for m in table_mats_sorted:
        if runs and _same_table_run(runs[-1][-1], m):
            runs[-1].append(m)
        else:
            runs.append([m])
    for run in runs:
        assemblies.append(_build_table_assembly(run, blocks_by_evidence_id))

    # ---- MaterialAssembly：evidence_span materials 的相邻/跨页链 ----
    span_mats = [m for m in materials if m.material_type == "evidence_span"]
    span_mats_sorted = sorted(
        span_mats, key=lambda m: (m.authority_assessment.document_id,
                                  m.authority_assessment.document_version,
                                  getattr(m.locator, "section_path", ""),
                                  m.authority_assessment.page or -1,
                                  (m.locator.block_range[0]
                                   if isinstance(m.locator, TS.EvidenceLocator)
                                   and m.locator.block_range else -1)))
    span_runs: list[list[TS.ResearchMaterial]] = []
    for m in span_mats_sorted:
        if span_runs and _same_span_run(span_runs[-1][-1], m):
            span_runs[-1].append(m)
        else:
            span_runs.append([m])
    for run in span_runs:
        if len(run) >= 2:
            assemblies.append(_build_span_assembly(run))

    return tuple(sorted(assemblies, key=lambda a: a.assembly_id))


def _same_table_run(prev: TS.ResearchMaterial, cur: TS.ResearchMaterial) -> bool:
    pp = prev.authority_assessment.page or -1
    cp = cur.authority_assessment.page or -1
    same_section = (getattr(prev.locator, "section_path", "")
                    == getattr(cur.locator, "section_path", ""))
    return same_section and cp - pp <= 1


def _same_span_run(prev: TS.ResearchMaterial, cur: TS.ResearchMaterial) -> bool:
    pp = prev.authority_assessment.page or -1
    cp = cur.authority_assessment.page or -1
    pi = (prev.locator.block_range[0]
          if isinstance(prev.locator, TS.EvidenceLocator) and prev.locator.block_range else -1)
    ci = (cur.locator.block_range[0]
          if isinstance(cur.locator, TS.EvidenceLocator) and cur.locator.block_range else -1)
    same_section = (getattr(prev.locator, "section_path", "")
                    == getattr(cur.locator, "section_path", ""))
    adjacent = (cp == pp and ci == pi + 1) or (cp == pp + 1)
    return same_section and adjacent


def _build_table_assembly(run: list[TS.ResearchMaterial],
                          blocks_by_evidence_id: dict[str, EvidenceReadResult]) -> TableAssembly:
    ordered = sorted(run, key=lambda m: (m.authority_assessment.page or -1,
                                         (m.locator.block_range[0]
                                          if isinstance(m.locator, TS.EvidenceLocator)
                                          and m.locator.block_range else -1)))
    component = tuple(m.material_id for m in ordered)

    header_m = None
    for m in ordered:
        b = blocks_by_evidence_id.get(m.authority_assessment.evidence_id)
        if b is not None and b.evidence_type == "table":
            header_m = m
            break
    header_ev = header_m.authority_assessment.evidence_id if header_m else None
    header_page = header_m.authority_assessment.page if header_m else None

    table_title = ""
    unit = None
    if header_m is not None:
        b = blocks_by_evidence_id.get(header_m.authority_assessment.evidence_id)
        if b is not None:
            table_title = b.text.strip() or ""
            if b.structured_payload and isinstance(b.structured_payload, dict):
                unit = b.structured_payload.get("unit")

    body_ids: list[str] = []
    continuation_ids: list[str] = []
    for m in ordered:
        b = blocks_by_evidence_id.get(m.authority_assessment.evidence_id)
        if b is None or b.evidence_type != "table_row":
            continue
        if header_page is not None and m.authority_assessment.page == header_page:
            body_ids.append(m.authority_assessment.evidence_id)
        else:
            continuation_ids.append(m.authority_assessment.evidence_id)

    root = header_m.material_id if header_m else ordered[0].material_id
    return TableAssembly(
        assembly_id=_assembly_id(component, "table_chain"),
        context_parent_id=root,
        component_material_ids=component,
        relation="table_chain",
        boundary_desc=_span_boundary_desc(ordered),
        table_title=table_title,
        unit=unit,
        header_evidence_id=header_ev,
        body_evidence_ids=tuple(body_ids),
        continuation_evidence_ids=tuple(continuation_ids),
    )


def _build_span_assembly(run: list[TS.ResearchMaterial]) -> MaterialAssembly:
    ordered = sorted(run, key=lambda m: (m.authority_assessment.page or -1,
                                         (m.locator.block_range[0]
                                          if isinstance(m.locator, TS.EvidenceLocator)
                                          and m.locator.block_range else -1)))
    component = tuple(m.material_id for m in ordered)
    pages = {m.authority_assessment.page for m in ordered}
    relation = "cross_page" if len(pages) > 1 else "adjacent"
    return MaterialAssembly(
        assembly_id=_assembly_id(component, relation),
        context_parent_id=ordered[0].material_id,
        component_material_ids=component,
        relation=relation,
        boundary_desc=_span_boundary_desc(ordered),
    )


def _span_boundary_desc(ordered: list[TS.ResearchMaterial]) -> str:
    pages = sorted({m.authority_assessment.page for m in ordered if m.authority_assessment.page})
    if not pages:
        return "无页面信息"
    if len(pages) == 1:
        return f"P{pages[0]}"
    return f"P{pages[0]}–P{pages[-1]}"
