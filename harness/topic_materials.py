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
import re
import unicodedata
from dataclasses import dataclass, replace

from evidence import ids
from harness import topic_schema as TS
from harness.context_expansion import (
    ContextExpansionTrace,
    ExpansionResult,
    UnreadScope,
)
from harness.evidence_reader import (
    EvidenceReadResult,
    recompute_evidence_identity,
    table_title_of,
)
from harness import source_object_inventory as SOI
from harness.set_enumeration import recover_flattened_tables
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
    # §五修复三：摊平表恢复出的列结构（headers/rows/total_row），仅 flattened_table_recovery
    # 投影填充；原生 table_chain 保持默认空。这些字段让真实 artifact 可展示 recovered
    # title/unit/header/rows/total，不产生新权威来源。
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    total_row: tuple[str, ...] | None = None
    # P1-2：摊平表严格结构校验结果（ok | partial | failed）+ 失败/部分原因；仅
    # flattened_table_recovery 投影填充，原生 table_chain 保持默认（ok/None）。
    recovery_status: str = "ok"
    recovery_issue: str | None = None
    # §修复 C：跨页续表「同一张表」证明（TableContinuationProof）。由 _build_table_assembly /
    # _build_flattened_table_assembly 构造后填充；纯 table_chain/flattened 投影默认 None（无续表
    # 或未构造证明时不伪造接受）。
    continuation_proof: "TableContinuationProof | None" = None


TABLE_CONTINUATION_PROOF_VERSION = "1"


@dataclass(frozen=True)
class TableContinuationProof:
    """跨页续表「同一张表」证明（§修复 C）。

    不是「两页上各有一张独立表」，而是**同一张表**的续表。``valid()`` 只有 7 个条件全真
    才成立：
      1. 有真实续表（continuation_evidence_ids 非空）；
      2. 同表身份：header 与每个续块归一化表题一致（title_compatible）；
      3. 单位兼容（unit_compatible）；
      4. 列兼容：续块列 == 表头列（column_compatible）；
      5. 行列连续：续块每行宽 == 表头列数（row_column_continuity，确定性结构校验）；
      6. 续块来自不同块/页：续块 evidence_id 互异且不同于表头，且跨页；
      7. 恢复 ok（final_recovery_status == "ok"）。
    无真实续表 → ``sample_not_obtained``（绝不伪造接受）。
    """

    normalized_title: str
    header_evidence_id: str | None
    continuation_evidence_ids: tuple[str, ...]
    header_page: int | None
    continuation_pages: tuple[int | None, ...]
    title_compatible: bool
    unit_compatible: bool
    column_compatible: bool
    row_column_continuity: bool
    final_recovery_status: str
    proof_version: str = TABLE_CONTINUATION_PROOF_VERSION
    issue: str = ""

    @property
    def has_continuation(self) -> bool:
        return bool(self.continuation_evidence_ids)

    @property
    def sample_not_obtained(self) -> bool:
        """无真实续表 → sample_not_obtained（不伪造「同一张表」接受）。"""
        return not self.has_continuation

    def _distinct_blocks(self) -> bool:
        return (
            bool(self.header_evidence_id)
            and bool(self.continuation_evidence_ids)
            and all(e != self.header_evidence_id for e in self.continuation_evidence_ids)
            and len(set(self.continuation_evidence_ids)) == len(self.continuation_evidence_ids)
        )

    def _cross_page(self) -> bool:
        return bool(self.continuation_pages) and any(
            p != self.header_page for p in self.continuation_pages)

    @property
    def valid(self) -> bool:
        """7 条件全真才是同一张表的续表；任一不满足 → False（fail-closed）。"""
        return (
            self.has_continuation
            and bool(self.header_evidence_id)
            and bool(self.normalized_title)
            and self.title_compatible
            and self.unit_compatible
            and self.column_compatible
            and self.row_column_continuity
            and self._distinct_blocks()
            and self._cross_page()
            and self.final_recovery_status == "ok"
        )


@dataclass(frozen=True)
class AspectMaterialLink:
    """aspect ↔ material 绑定（§4.7）。

    role 语义（§三/§四：权威 / 边界成员 / aspect 资格 / supporting 四者分离）：
    - ``source``：seed（真实来源，唯一「确定支撑」入口）；
    - ``context_candidate``：边界内/语义不确定的扩读材料，仅作上下文候选，
      是否正式 ``supporting`` 由 R3 做语义事实充分性判定（R2 绝不代做）。
    """

    aspect_id: str
    material_ids: tuple[str, ...]
    role: str                         # source | context_candidate（supporting 留给 R3）


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
                           table_title: str | None, offset: int | None = None) -> str:
    return "|".join([
        block.document_id, block.document_version, block.evidence_set_version,
        section_path_joined(block.section_path),
        str(block.page_number), str(block.block_index),
        table_title or "", str(offset) if offset is not None else "",
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
                   table_title: str | None, offset: int | None = None) -> TS.EvidenceLocator:
    return TS.EvidenceLocator(
        document_id=block.document_id,
        document_version=block.document_version,
        section_path=section_path_joined(block.section_path),
        page=block.page_number,
        table_title=table_title,
        block_range=(block.block_index, block.block_index),
        offset=offset,
    )


def _build_envelope(block: EvidenceReadResult, material_type: str,
                    source_identity: str, locator: TS.EvidenceLocator,
                    dependency_fingerprint: str,
                    text_override: str | None = None) -> dict:
    """payload 规范 JSON 信封（§6.2）；不含自身 payload_hash。

    ``text_override``：片段投影（修复 A）时覆盖 content.text 为主题内前缀；完整原子材料
    保持 ``block.text`` 原样，绝不修改原文/来源 content_hash。
    """
    return {
        "material_payload_version": 1,
        "object_type": material_type,
        "authority_identity": source_identity,
        "document_identity": {
            "company_id": block.company_id,
            "document_id": block.document_id,
            "document_version": block.document_version,
            "evidence_set_version": block.evidence_set_version,
        },
        "locator": locator.to_dict(),
        "evidence_id": block.evidence_id,
        "source_content_hash": block.content_hash,
        "content": {
            "text": block.text if text_override is None else text_override,
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
                          context_parent_id: str | None = None,
                          fragment_offset: int | None = None,
                          fragment_text: str | None = None) -> BuiltMaterial:
    """把单个 EvidenceBlock 构建为一个 atomic ResearchMaterial + payload record。

    - 双哈希：source_content_hash == block.content_hash（来源层）；payload_hash ==
      sha256(payload_bytes)（载体层，== payload_id == material.content_hash == payload_ref.content_hash）。
    - authority 由 ``recompute_authority_verdict`` 确定性重算（不信任自填）。
    - 片段投影（修复 A）：``fragment_offset``/``fragment_text`` 提供时，locator.offset 记录
      主题内前缀的字符界，payload text 为主题内前缀；source_content_hash 仍为完整块 hash
      （保留完整原子材料身份，绝不修改原文/来源 content_hash）。
    """
    if material_type not in ("evidence_span", "table_context"):
        raise ValueError(f"R2 只构建 evidence_span/table_context，得到 {material_type!r}")

    # 来源身份必须用 formal 算法（evidence.ids）确定性重算，不信任读取侧自报 evidence_id/content_hash。
    recomputed_eid, recomputed_ch = recompute_evidence_identity(block)
    if block.evidence_id != recomputed_eid:
        raise ValueError(
            f"EvidenceBlock.evidence_id 与重算不一致: {block.evidence_id} != {recomputed_eid}")
    if block.content_hash != recomputed_ch:
        raise ValueError(
            f"EvidenceBlock.content_hash 与重算不一致: {block.content_hash} != {recomputed_ch}")

    locator = _build_locator(block, material_type, table_title, offset=fragment_offset)
    source_identity = f"evidence:{recomputed_eid}"

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
                               dependency_fingerprint, text_override=fragment_text)
    payload_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"),
                               sort_keys=True).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    canonical_locator_key = _canonical_locator_key(block, material_type, table_title,
                                                   offset=fragment_offset)
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
    # 表身份：优先 structured_payload.table_title，其次 table 块正文（与读取侧一致）。
    return table_title_of(block)


def build_material_result(expansion: ExpansionResult, *,
                          dependency_fingerprint: str,
                          aspect_id: str | None = None) -> MaterialBuildResult:
    """把一次扩读结果转换为 MaterialBuildResult（去重 + 稳定排序 + assembly + aspect link）。

    current 判定不再由调用方硬编码：``is_current_document/is_current_set`` 取自已完成的
    verify_seed ToolResult（``expansion.seed_is_current_*``），与来源身份一起由重算闭环。
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
            is_current_document=expansion.seed_is_current_document,
            is_current_set=expansion.seed_is_current_set,
            dependency_fingerprint=dependency_fingerprint,
            table_title=_table_title_for(block))
        built[bm.material.material_id] = bm

    # 修复 A：mixed block 主题内前缀片段投影 → 独立 evidence_span material（locator.offset
    # 记录主题外标题字符界，payload text 为主题内前缀，source_content_hash 仍为完整块 hash）。
    # 完整原子材料保留（block 仍在 boundary_decisions 里作为主题外 sentinel 记录）。
    for frag in expansion.fragment_projections:
        frag_block = frag.block
        bm = build_atomic_material(
            frag_block, material_type="evidence_span",
            is_current_document=expansion.seed_is_current_document,
            is_current_set=expansion.seed_is_current_set,
            dependency_fingerprint=dependency_fingerprint,
            table_title=None, fragment_offset=frag.char_offset,
            fragment_text=frag.prefix_text)
        built.setdefault(bm.material.material_id, bm)

    materials = tuple(sorted(
        (bm.material for bm in built.values()), key=_material_sort_key))

    assemblies = build_assemblies(materials, blocks_by_id,
                                  relations=expansion.adopted_relations)

    aspect_links: tuple[AspectMaterialLink, ...] = ()
    if aspect_id is not None:
        seed_material_id = next(
            (m.material_id for m in materials
             if m.authority_assessment.evidence_id == expansion.seed.evidence_id),
            None)
        # §三/§四：非 seed 扩读材料最多 context_candidate，绝不自动 supporting
        # （「来源真实」≠「支撑该 aspect」，supporting 事实充分性留给 R3）。
        context = tuple(
            m.material_id for m in materials
            if m.authority_assessment.evidence_id != expansion.seed.evidence_id)
        links: list[AspectMaterialLink] = []
        if seed_material_id is not None:
            links.append(AspectMaterialLink(
                aspect_id=aspect_id, material_ids=(seed_material_id,), role="source"))
        if context:
            links.append(AspectMaterialLink(
                aspect_id=aspect_id, material_ids=context, role="context_candidate"))
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

def _assembly_id(component_material_ids: tuple[str, ...], relation: str,
                 discriminator: str = "") -> str:
    raw = json.dumps([relation, list(component_material_ids), discriminator],
                     ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "asm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _materials_by_evidence_id(materials: tuple[TS.ResearchMaterial, ...]) -> dict[str, TS.ResearchMaterial]:
    return {m.authority_assessment.evidence_id: m for m in materials}


def build_assemblies(materials: tuple[TS.ResearchMaterial, ...],
                     blocks_by_evidence_id: dict[str, EvidenceReadResult],
                     relations: dict[str, str] | None = None,
                     ) -> tuple[MaterialAssembly, ...]:
    """从一组 atomic materials + 其 Block 派生组合投影（表链 / 相邻链 / 交叉引用）。

    assembly 只引用 ``component_material_ids``（真实 atomic material），本身不产生 payload、
    不产生新权威；``context_parent_id`` 为组合根 material_id。
    """
    mat_by_ev = _materials_by_evidence_id(materials)
    assemblies: list[MaterialAssembly] = []

    # ---- TableAssembly：table_context materials 的连续表链（真实表身份，非仅页面相邻） ----
    table_mats = [m for m in materials if m.material_type == "table_context"]
    table_mats_sorted = sorted(
        table_mats, key=lambda m: (m.authority_assessment.page or -1,
                                   (m.locator.block_range[0]
                                    if isinstance(m.locator, TS.EvidenceLocator)
                                    and m.locator.block_range else -1)))
    runs: list[list[TS.ResearchMaterial]] = []
    for m in table_mats_sorted:
        if runs and _same_table_run(runs[-1][-1], m, blocks_by_evidence_id):
            runs[-1].append(m)
        else:
            runs.append([m])
    for run in runs:
        assemblies.append(_build_table_assembly(run, blocks_by_evidence_id))

    # ---- TableAssembly：PDF 摊平段落恢复（§五修复三，过程侧投影，非第 4 权威） ----
    assemblies.extend(_build_flattened_table_assemblies(materials, blocks_by_evidence_id))

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

    # ---- MaterialAssembly：交叉引用（explicit_reference 采纳的 reference 目标） ----
    assemblies.extend(_build_reference_assemblies(
        materials, mat_by_ev, relations or {}))

    return tuple(sorted(assemblies, key=lambda a: a.assembly_id))


def _same_table_run(prev: TS.ResearchMaterial, cur: TS.ResearchMaterial,
                    blocks_by_evidence_id: dict[str, EvidenceReadResult]) -> bool:
    """真实表身份（§四.2）：同 section 且同 table_title 且结构连续才视为同表链。

    - 无表身份（title 缺失）→ 不合并：即使页面相邻也是两张独立表（§四.2 反例）；
    - 同表身份但相距多页（跨页 >1，如不同年度/不同披露位置）→ 不合并；
    - 同表身份且跨页 ≤1（续表跨页）→ 合并为连续表链。
    营业收入表与营业成本表即使相邻页也是两张独立表（title 不同 → 不合并）。
    """
    same_section = (getattr(prev.locator, "section_path", "")
                    == getattr(cur.locator, "section_path", ""))
    if not same_section:
        return False
    pb = blocks_by_evidence_id.get(prev.authority_assessment.evidence_id)
    cb = blocks_by_evidence_id.get(cur.authority_assessment.evidence_id)
    # 新表头块（evidence_type == "table"）= 新独立表，不是续表：续表只有数据行，无独立表头。
    # 两张同表题但各自带独立表头的表（即使相邻页）必须拆成两张独立 TableAssembly，
    # 绝不合并为一张并伪造跨表续表 valid（§修复 C.6 / 反例 #7）。
    if cb is not None and cb.evidence_type == "table":
        return False
    pt = _table_title_for(pb) if pb is not None else None
    ct = _table_title_for(cb) if cb is not None else None
    if pt is None or ct is None:
        return False  # 无表身份 → 不合并（不按页面相邻猜表）。
    if pt != ct:
        return False  # 不同表身份（如营业收入 vs 营业成本）→ 独立表。
    # 同表身份 + 同 section：结构连续性上限（跨页 ≤1）；相距多页/跨年度 → 独立表。
    pp = prev.authority_assessment.page or -1
    cp = cur.authority_assessment.page or -1
    return cp - pp <= 1


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
            table_title = table_title_of(b) or ""
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
    asm = TableAssembly(
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
    # §修复 C：附「同一张表」续表证明（无真实续表 → sample_not_obtained，不伪造接受）。
    return replace(asm, continuation_proof=build_table_continuation_proof(
        asm, blocks_by_evidence_id))


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


def _build_reference_assemblies(materials: tuple[TS.ResearchMaterial, ...],
                                mat_by_ev: dict[str, TS.ResearchMaterial],
                                relations: dict[str, str]) -> list[MaterialAssembly]:
    """交叉引用组合（§四.4）：seed material 与其 explicit_reference 采纳目标的 reference 链。"""
    if not relations:
        return []
    seed_material = next(
        (mat_by_ev[eid] for eid, rel in relations.items()
         if rel == "seed" and eid in mat_by_ev), None)
    if seed_material is None:
        return []
    ref_targets = [mat_by_ev[eid] for eid, rel in relations.items()
                   if rel == "reference" and eid in mat_by_ev
                   and eid != seed_material.authority_assessment.evidence_id]
    assemblies: list[MaterialAssembly] = []
    for target in ref_targets:
        component = (seed_material.material_id, target.material_id)
        assemblies.append(MaterialAssembly(
            assembly_id=_assembly_id(component, "reference"),
            context_parent_id=seed_material.material_id,
            component_material_ids=component,
            relation="reference",
            boundary_desc=_span_boundary_desc([seed_material, target]),
        ))
    return assemblies


def _build_flattened_table_assemblies(
        materials: tuple[TS.ResearchMaterial, ...],
        blocks_by_evidence_id: dict[str, EvidenceReadResult]) -> list[TableAssembly]:
    """§五修复三：从 PDF 摊平段落（evidence_span 但文本像表格行摊平）恢复 TableAssembly 投影。

    纯结构（无 LLM/公司/页码规则）：把单个 block 文本按行拆分（真实 PDF 把整张表塞进一个
    超长段落），识别 title/unit/header/rows/total 边界；显式「表 N」/不同表题开新表（收入 vs
    成本 vs 毛利独立）；表后正文（句读/单列无数字）结束当前表，绝不误读为数据行。恢复出的
    TableAssembly 是过程侧投影（relation=flattened_table_recovery），只引用 component material，
    不产生新权威来源。
    """
    # P1-B.1：摊平表恢复必须按**规范源顺序**（document_version → page → block_index →
    # fragment_offset）拼接文本，与源对象清单使用同一顺序键；否则同一份源文本会因两侧
    # 排序不同而派生两套互相矛盾的「恢复真相」（清单说 recovered_ok，assembly 却缺失）。
    def _canonical_key(m):
        loc = m.locator
        br = getattr(loc, "block_range", None)
        off = getattr(loc, "offset", None)
        return (
            str(getattr(loc, "document_version", "")
                or getattr(m.authority_assessment, "document_version", "") or ""),
            m.authority_assessment.page if m.authority_assessment.page is not None else -1,
            br[0] if isinstance(br, (list, tuple)) and br else -1,
            off if isinstance(off, int) else 0,
            m.material_id,
        )

    spans = sorted(
        (m for m in materials if m.material_type == "evidence_span"),
        key=_canonical_key)
    ordered = list(spans)
    texts = [
        unicodedata.normalize(
            "NFC",
            (blocks_by_evidence_id.get(m.authority_assessment.evidence_id).text
             if blocks_by_evidence_id.get(m.authority_assessment.evidence_id) is not None
             else "") or "")
        for m in ordered
    ]
    tables = recover_flattened_tables(texts)

    assemblies: list[TableAssembly] = []
    for t in tables:
        a = _build_flattened_table_assembly(t, ordered, blocks_by_evidence_id)
        if a is not None:
            assemblies.append(a)
    return assemblies


def _build_flattened_table_assembly(
        table: dict, ordered: list[TS.ResearchMaterial],
        blocks_by_evidence_id: dict[str, EvidenceReadResult]) -> TableAssembly | None:
    """由 ``recover_flattened_tables`` 的恢复结果 + 有序材料投影 flattened_table_recovery。

    只有「有表头且有数据行」才产出（诚实缺口：仅表头/仅数据行不伪造投影）。component 只
    引用贡献表结构（unit/header/rows/total）的 material，表题块不计入 component。
    """
    headers = tuple(table.get("headers") or ())
    rows = tuple(tuple(r) for r in (table.get("rows") or []))
    if not headers or not rows:
        return None
    structure_indices = sorted(table.get("structure_text_indices") or [])
    component_materials = [ordered[i] for i in structure_indices
                           if 0 <= i < len(ordered)]
    component = tuple(m.material_id for m in component_materials)
    if not component:
        component = tuple(m.material_id for m in ordered)

    def _ev(i) -> str | None:
        if i is None or not (0 <= i < len(ordered)):
            return None
        return ordered[i].authority_assessment.evidence_id

    header_ev = _ev(table.get("header_text_index"))
    header_page = (ordered[table["header_text_index"]].authority_assessment.page
                   if table.get("header_text_index") is not None
                   and 0 <= table["header_text_index"] < len(ordered) else None)
    body_ids: list[str] = []
    continuation_ids: list[str] = []
    for ri in table.get("row_text_indices") or []:
        ev = _ev(ri)
        if ev is None:
            continue
        m = ordered[ri]
        if header_page is not None and m.authority_assessment.page == header_page:
            body_ids.append(ev)
        else:
            continuation_ids.append(ev)
    total_ev = _ev(table.get("total_text_index"))
    if total_ev is not None:
        continuation_ids.append(total_ev)

    root = component[0]
    # P1-3：单个摊平 block 内可恢复出**多张不同表**（如研发投入表 + 现金流表同段摊平），
    # 其 component_material_ids 相同（同一 block material），仅按 component 生成 assembly_id
    # 会冲突。必须把恢复出的表结构（title/unit/headers/rows/total）并入身份，使同一 block
    # 内的不同表各得唯一、内容寻址的 assembly_id（同表内容 → 同 ID 去重，异表 → 异 ID 共存）。
    discriminator = SOI.flattened_table_structure_identity(table)
    asm = TableAssembly(
        assembly_id=_assembly_id(component, "flattened_table_recovery", discriminator),
        context_parent_id=root,
        component_material_ids=component,
        relation="flattened_table_recovery",
        boundary_desc=_span_boundary_desc(component_materials),
        table_title=table.get("title") or "",
        unit=table.get("unit"),
        header_evidence_id=header_ev,
        body_evidence_ids=tuple(dict.fromkeys(body_ids)),
        continuation_evidence_ids=tuple(dict.fromkeys(continuation_ids)),
        headers=headers,
        rows=rows,
        total_row=tuple(table["total_row"]) if table.get("total_row") else None,
        recovery_status=table.get("recovery_status", "ok"),
        recovery_issue=table.get("recovery_issue"),
    )
    # §修复 C：附「同一张表」续表证明（无真实续表 → sample_not_obtained，不伪造接受）。
    return replace(asm, continuation_proof=build_table_continuation_proof(
        asm, blocks_by_evidence_id))


def _normalize_table_identity(title: str | None) -> str:
    """归一化表身份：NFC + 折叠空白 + 去首尾（用于「同一张表」判定）。"""
    if not title:
        return ""
    return " ".join(unicodedata.normalize("NFC", str(title)).split()).strip()


def _normalize_unit(unit) -> str:
    if unit is None:
        return ""
    return " ".join(unicodedata.normalize("NFC", str(unit)).split()).strip()


def _structured_columns(b: EvidenceReadResult | None) -> tuple[str, ...] | None:
    """取块的结构化列名（structured_payload.headers）；非结构化/缺列 → None。"""
    if b is None or not isinstance(b.structured_payload, dict):
        return None
    headers = b.structured_payload.get("headers")
    if isinstance(headers, list):
        return tuple(str(h) for h in headers)
    return None


def _structured_row_widths(b: EvidenceReadResult | None) -> tuple[int, ...]:
    """取块每行的列宽（structured_payload.cells 各行长度）；用于行列连续性校验。"""
    if b is None or not isinstance(b.structured_payload, dict):
        return ()
    cells = b.structured_payload.get("cells")
    if isinstance(cells, list):
        return tuple(len(c) if isinstance(c, list) else 0 for c in cells)
    return ()


def build_table_continuation_proof(
        assembly: TableAssembly,
        blocks_by_evidence_id: dict[str, EvidenceReadResult]) -> TableContinuationProof:
    """从 TableAssembly + 其 Block 确定性构造跨页续表「同一张表」证明（§修复 C）。

    纯结构（无 LLM/公司/页码规则）：归一化表题、单位、列名、行宽、续块身份/页面全部来自
    Block 真实结构。不满足任一条件 → proof.valid == False；无真实续表 → sample_not_obtained。
    """
    title = _normalize_table_identity(assembly.table_title)
    header_b = blocks_by_evidence_id.get(assembly.header_evidence_id or "")
    header_page = header_b.page_number if header_b is not None else None
    header_cols = _structured_columns(header_b)
    unit = _normalize_unit(assembly.unit)

    cont_evs = tuple(dict.fromkeys(assembly.continuation_evidence_ids))
    cont_blocks = [blocks_by_evidence_id.get(eid) for eid in cont_evs]
    cont_blocks = [b for b in cont_blocks if b is not None]
    cont_pages = tuple(b.page_number for b in cont_blocks)

    # 1. 同表身份：每个续块归一化表题 == header 表题。
    title_compatible = bool(title) and all(
        _normalize_table_identity(table_title_of(b)) == title for b in cont_blocks)
    # 2. 单位兼容。
    unit_compatible = all(
        _normalize_unit(b.structured_payload.get("unit")
                       if isinstance(b.structured_payload, dict) else None) == unit
        for b in cont_blocks)
    # 3. 列兼容。
    column_compatible = True
    if header_cols is not None:
        for b in cont_blocks:
            c = _structured_columns(b)
            if c is not None and c != header_cols:
                column_compatible = False
                break
    # 4. 行列连续：续块每行宽 == 表头列数。
    row_column_continuity = True
    if header_cols is not None:
        for b in cont_blocks:
            if any(w != len(header_cols) for w in _structured_row_widths(b)):
                row_column_continuity = False
                break
    # 5. 续块来自不同块。
    distinct = (
        bool(cont_evs)
        and bool(assembly.header_evidence_id)
        and all(e != assembly.header_evidence_id for e in cont_evs)
        and len(set(cont_evs)) == len(cont_evs))
    # 6. 跨页。
    cross_page = any(p != header_page for p in cont_pages)

    issues: list[str] = []
    if not title_compatible:
        issues.append("表题不一致")
    if not unit_compatible:
        issues.append("单位不兼容")
    if not column_compatible:
        issues.append("列不兼容")
    if not row_column_continuity:
        issues.append("行列不连续")
    if not distinct:
        issues.append("续块身份重复或同表头")
    if not cross_page:
        issues.append("非跨页续表")
    if assembly.recovery_status != "ok":
        issues.append(f"恢复状态 {assembly.recovery_status}")

    return TableContinuationProof(
        normalized_title=title,
        header_evidence_id=assembly.header_evidence_id,
        continuation_evidence_ids=cont_evs,
        header_page=header_page,
        continuation_pages=cont_pages,
        title_compatible=title_compatible,
        unit_compatible=unit_compatible,
        column_compatible=column_compatible,
        row_column_continuity=row_column_continuity,
        final_recovery_status=assembly.recovery_status,
        issue="; ".join(issues),
    )


def _span_boundary_desc(ordered: list[TS.ResearchMaterial]) -> str:
    pages = sorted({m.authority_assessment.page for m in ordered if m.authority_assessment.page})
    if not pages:
        return "无页面信息"
    if len(pages) == 1:
        return f"P{pages[0]}"
    return f"P{pages[0]}–P{pages[-1]}"
