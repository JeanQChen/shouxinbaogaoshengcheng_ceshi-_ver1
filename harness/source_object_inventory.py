"""R2 修复 B：源对象清单 → 恢复结果 闭环（ExpectedSourceObjectInventory）。

在材料边界内先确定性生成「应出现的源对象」清单（表题/表号、显式表格起始信号、续表信号、
显式跨页/跨章引用），再把每个源对象对账到**唯一**持久化恢复结果（assembly）：
``recovered_ok`` / ``recovered_partial`` / ``recovery_failed`` / ``target_not_obtained``。

不变量（P1-B）：
- **规范源顺序**：清单按 ``document_version → page_number → block_index → fragment_offset``
  建立，绝不按 content-addressed material_id 顺序（后者与文档真实阅读顺序无关）。
- **复合结构信号**：表题绝不由关键词单独裁决，统一走 ``harness.table_structure``
  （表题形态 + 前驱合法 + 结构跟随 + 无中介表题）。
- **单一恢复真相**：``recovered_ok`` 只能由**已持久化并通过结构校验的 assembly** 派生；
  无 assembly ⇒ 绝不 ok。清单侧绝不另跑一遍摊平表恢复。
- **身份绑定**：每个源对象绑定 ``assembly_id`` + ``component_material_ids`` + 文档/版本 +
  源边界 + 恢复状态/原因。
- **多重显式引用**：``详见表5-10、表5-11`` 必须生成两个相互独立的源对象，绝不合并目标。

纯确定性函数：同 (aspect_id, sources, assemblies) 恒得同结果。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from harness import table_structure as TBL

SOURCE_OBJECT_INVENTORY_VERSION = "3"

# 恢复结果四态（每个源对象必须且只能对应一个）。
RECOVERED_OK = "recovered_ok"
RECOVERED_PARTIAL = "recovered_partial"
RECOVERY_FAILED = "recovery_failed"
TARGET_NOT_OBTAINED = "target_not_obtained"
RECOVERY_RESULTS = (RECOVERED_OK, RECOVERED_PARTIAL, RECOVERY_FAILED, TARGET_NOT_OBTAINED)

# 源对象种类（四类，覆盖任务书「表题/表号、显式表格起始信号、续表信号、显式跨页/跨章引用」）。
KIND_TABLE_NUMBER = "table_number"        # 表号/表题：「表 5-10 主营业务收入构成」
KIND_TABLE_START = "table_start_signal"   # 显式表格起始信号：无「表 N」的通用表题行
KIND_CONTINUATION = "continuation"        # 续表信号：「续表 5-10」「表 5-10（续）」
KIND_CROSS_REFERENCE = "cross_reference"  # 显式跨页/跨章引用：「见表 5-12」「如附表」

_SOURCE_OBJECT_KINDS = (
    KIND_TABLE_NUMBER, KIND_TABLE_START, KIND_CONTINUATION, KIND_CROSS_REFERENCE,
)

# 摊平表恢复产物的 assembly relation（摊平表这一条恢复路径的唯一来源）。
FLATTENED_TABLE_RELATION = "flattened_table_recovery"

# §三 P1-4：显式引用成功解析出的目标表对象投影的 assembly relation。它是**第二条**被本清单
# 承认的「已获得」来源：引用目标表有自己的内容寻址身份（``table_object_id``），且已经作为
# 过程侧投影真实落盘 —— 因此源对象必须如实记为已获得并绑定该 assembly，而**不是**
# ``target_not_obtained``（v13 的缺陷：目标表已被解析并采纳，清单却仍报未获得）。
REFERENCE_TABLE_OBJECT_RELATION = "reference_table_object"

# 无表号引用（「见第 X 章」）的源对象目标哨兵。
_PAGE_CHAPTER_SENTINEL = "_pg"

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 显式「表 N …」行（表号，可能后接表题）。
_TABLE_NUMBER_LINE = re.compile(r"^\s*表\s*([0-9０-９][0-9０-９\-\–—~～]*)\s*(.*)$")

# 显式跨页/跨章引用标记（见/参见/详见/如/参照）。
_REFERENCE_MARKER_RE = re.compile(r"(?:见|参见|详见|如|参照)")
# 引用行内的表号（「表 5-12」「附表 3」）。
_REFERENCED_TABLE_RE = re.compile(r"(?:附表|表)\s*([0-9０-９][0-9０-９\-\–—~～]*)")
# 「第 X 页/章」引用（无表号目标）。
_PAGE_CHAPTER_REF_RE = re.compile(
    r"第\s*[0-9０-９一二三四五六七八九十]+\s*[页章]")


# ---------------------------------------------------------------------------
# 源位置与源文本（P1-B.1 规范源顺序）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourcePosition:
    """源对象在权威文档中的规范位置（规范顺序键，绝不用 content-addressed material_id）。

    P1-B.1/B.3：位置**必须**绑定 document_id + document_version（跨文档同名页不可混淆）
    与 source_boundary_identity（同一文档内不同主题边界的同名对象不可混淆）。
    """

    document_version: str
    page_number: int
    block_index: int
    fragment_offset: int
    material_id: str
    document_id: str = ""
    source_boundary_identity: str = ""

    def sort_key(self) -> tuple:
        return (self.document_id, self.document_version, self.page_number,
                self.block_index, self.fragment_offset, self.material_id)


@dataclass(frozen=True)
class SourceText:
    """一段边界内源文本 + 其规范位置。"""

    position: SourcePosition
    text: str

    @staticmethod
    def from_texts(texts: Sequence[str], *, document_id: str = "",
                   document_version: str = "",
                   source_boundary_identity: str = "") -> tuple["SourceText", ...]:
        """测试/内部便捷：按给定顺序合成递增规范位置（block_index = i）。"""
        return tuple(
            SourceText(
                SourcePosition(document_version=document_version, page_number=0,
                               block_index=i, fragment_offset=0,
                               material_id=f"synthetic-{i}",
                               document_id=document_id,
                               source_boundary_identity=source_boundary_identity),
                t or "")
            for i, t in enumerate(texts))


def _canonical_order(sources: Iterable[SourceText]) -> list[SourceText]:
    """规范源顺序：document_version → page_number → block_index → fragment_offset。"""
    return sorted(sources, key=lambda s: s.position.sort_key())


# ---------------------------------------------------------------------------
# 表号/续表的文本原语
# ---------------------------------------------------------------------------

def _normalize_table_number(s: str) -> str:
    """规范化表号：全角数字→半角、连接符（－–—~～）→ '-'、去空白。「5－10」→「5-10」。"""
    t = unicodedata.normalize("NFKC", s or "")
    t = t.translate(_FULLWIDTH_DIGITS)
    t = re.sub(r"[－–—~～]", "-", t)
    t = re.sub(r"\s+", "", t)
    return t


def table_number_of(line: str) -> str:
    """从「表 5-10 主营业务收入构成」提取规范化表号「5-10」；非「表 N」行 → ''。"""
    m = _TABLE_NUMBER_LINE.match(unicodedata.normalize("NFC", line or ""))
    if not m:
        return ""
    return _normalize_table_number(m.group(1))


def _title_content(line: str) -> str:
    """提取「表 N」行中表号后的表题内容（去「表 5-10」前缀），用于标题一致性对账。"""
    m = _TABLE_NUMBER_LINE.match(unicodedata.normalize("NFC", line or ""))
    if not m:
        return unicodedata.normalize("NFC", line or "").strip()
    return (m.group(2) or "").strip()


def _slug(s: str) -> str:
    """确定性短身份（去空白/标点，截断）。"""
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))
    return t[:24]


def _continuation_target_number(s: str) -> str:
    """从续表行提取目标表号（「续表 5-10」→「5-10」；无表号 → ''）。"""
    m = re.search(r"[0-9０-９][0-9０-９\-\–—~～]*", unicodedata.normalize("NFKC", s or ""))
    return _normalize_table_number(m.group(0)) if m else ""


def cross_reference_target_numbers(s: str) -> list[str]:
    """提取一行中**全部**显式引用的目标表号（P1-B.7：绝不合并「详见表5-10、表5-11」）。

    只在出现引用标记（见/参见/详见/如/参照）之后扫描，且目标必须是「表 N」/「附表 N」，
    避免把正文里的任意数字当作引用目标。
    """
    t = unicodedata.normalize("NFKC", s or "")
    m = _REFERENCE_MARKER_RE.search(t)
    if not m:
        return []
    out: list[str] = []
    for tm in _REFERENCED_TABLE_RE.finditer(t[m.start():]):
        n = _normalize_table_number(tm.group(1))
        if n and n not in out:
            out.append(n)
    return out


def _has_page_chapter_reference(s: str) -> bool:
    return bool(_PAGE_CHAPTER_REF_RE.search(unicodedata.normalize("NFKC", s or "")))


def flattened_table_structure_identity(table: dict) -> str:
    """摊平表恢复结果的结构身份判别串（内容寻址，唯一真相）。

    生产侧（``topic_materials._build_flattened_table_assembly``）与校验侧必须使用**同一个**
    判别串：同一 block 内可恢复出多张不同表，component material 相同，只有把恢复出的表结构
    （title/unit/headers/rows/total）并入身份，才能让同块多表各得唯一且稳定的 assembly_id。
    """
    return json.dumps(
        [table.get("title") or "", table.get("unit"),
         list(table.get("headers") or []),
         [list(r) for r in (table.get("rows") or [])],
         (list(table["total_row"]) if table.get("total_row") else None)],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def table_object_key(title: str) -> str:
    """表题 → 本清单使用的**表对象键**（表号优先，否则通用表题 slug；无表题 → ""）。

    与 ``derive_expected_source_object_inventory`` 的 ``table:`` / ``title:`` 对象身份**同源**：
    任何需要按「同一张表」归并持久化 assembly 的地方都必须复用本函数，绝不复制第二套表身份规则。
    """
    num = table_number_of(title or "")
    if num:
        return f"table:{num}"
    slug = _slug(title or "")
    return f"title:{slug}" if slug else ""


def cross_reference_object_target(object_id: str) -> str:
    """从引用源对象身份「cross_ref:{目标}:{text_index}:{line_index}」取回**本对象**的目标。

    目标可能包含 '-'（「5-10」）但不含 ':'；因此按 ':' 切分后第 2 段即目标（无表号时为 ``_pg``）。
    非引用对象身份 → ''（绝不猜目标）。
    """
    parts = (object_id or "").split(":")
    if len(parts) != 4 or parts[0] != "cross_ref":
        return ""
    return parts[1]


# ---------------------------------------------------------------------------
# 清单对象
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpectedSourceObject:
    """期望源对象（P1-B.4）：身份**必须**绑定 document_id/version、source_boundary_identity、
    source_object_id + 规范位置，绝不只靠块内标签。
    """

    object_id: str
    kind: str
    label: str
    source_text_index: int
    line_index: int
    document_version: str = ""
    page_number: int = 0
    block_index: int = 0
    fragment_offset: int = 0
    material_id: str = ""
    document_id: str = ""
    source_boundary_identity: str = ""
    source_object_id: str = ""


@dataclass(frozen=True)
class SourceObjectRecoveryResult:
    """逐对象恢复结果（P1-B.5）：必须绑定 assembly_id + component_material_ids +
    recovery_status（持久化 assembly 的真实状态）+ 原因，绝不只报一个结论词。
    """

    object_id: str
    result: str
    matched_table: str = ""
    issue: str = ""
    assembly_id: str = ""
    component_material_ids: tuple[str, ...] = ()
    recovery_status: str = ""
    recovery_reason: str = ""
    # §三 P1-4：见证本对象「已获得」的目标表对象**内容寻址身份**（引用表对象投影的
    # ``table_object_id``）。使「清单说已获得」与「材料库中确有该目标对象」用同一个身份对账，
    # 绝不靠表号字符串凑合。
    table_object_ids: tuple[str, ...] = ()


@dataclass
class ExpectedSourceObjectInventory:
    aspect_id: str
    version: str
    expected_objects: list[ExpectedSourceObject] = field(default_factory=list)
    recovery_results: list[SourceObjectRecoveryResult] = field(default_factory=list)
    unmatched_recovered_tables: list[str] = field(default_factory=list)
    orphan_assemblies: list[str] = field(default_factory=list)
    # §四.B.6/B.7：无表题/无表号的恢复表 —— 无对象身份可对账，必须**显式披露**，
    # 与 orphan_assemblies 互斥且共同穷尽全部持久化摊平表 assembly（绝不静默丢弃）。
    untitled_recovered_tables: list[str] = field(default_factory=list)
    # §三 P1-4：已落盘的引用表对象投影中，**未被任何源对象认领**者（``table_object_id``）。
    # 非空即 fail-closed：目标表已被解析并进入材料库，清单却没有对应「已获得」——这正是
    # 「材料库与清单两套真相」的入口，绝不静默。
    unclaimed_reference_objects: list[str] = field(default_factory=list)
    # P1-B.6：恢复事实的**唯一**来源（清单侧绝不自行恢复）。
    recovery_source: str = "persisted_assemblies"
    document_id: str = ""
    source_boundary_identity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "aspect_id": self.aspect_id,
            "recovery_source": self.recovery_source,
            "document_id": self.document_id,
            "source_boundary_identity": self.source_boundary_identity,
            "expected_source_objects": [
                {"object_id": o.object_id, "kind": o.kind, "label": o.label,
                 "source_text_index": o.source_text_index, "line_index": o.line_index,
                 "document_id": o.document_id,
                 "document_version": o.document_version, "page_number": o.page_number,
                 "block_index": o.block_index, "fragment_offset": o.fragment_offset,
                 "material_id": o.material_id,
                 "source_boundary_identity": o.source_boundary_identity,
                 "source_object_id": o.source_object_id or o.object_id}
                for o in self.expected_objects],
            "recovery_results": [
                {"object_id": r.object_id, "result": r.result,
                 "matched_table": r.matched_table, "issue": r.issue,
                 "assembly_id": r.assembly_id,
                 "component_material_ids": list(r.component_material_ids),
                 "recovery_status": r.recovery_status,
                 "recovery_reason": r.recovery_reason,
                 "table_object_ids": list(r.table_object_ids)}
                for r in self.recovery_results],
            "unmatched_recovered_tables": list(self.unmatched_recovered_tables),
            "orphan_assemblies": list(self.orphan_assemblies),
            "untitled_recovered_tables": list(self.untitled_recovered_tables),
            "unclaimed_reference_objects": list(self.unclaimed_reference_objects),
        }


# ---------------------------------------------------------------------------
# 清单生成（材料边界内，先于恢复）
# ---------------------------------------------------------------------------

def _source_object_id(document_id: str, document_version: str,
                      source_boundary_identity: str, object_id: str) -> str:
    """源对象**全局身份**：绑定 document + version + 源边界 + 清单内对象键。

    同一文档内不同主题边界下的同名对象（如两个章节各有一张「主营业务收入构成」）
    因此互不相同；跨 document_version 的同名对象同样互不相同。
    """
    return "|".join((document_id or "", document_version or "",
                     source_boundary_identity or "", object_id or ""))


def derive_expected_source_object_inventory(
        aspect_id: str, sources: Sequence[SourceText]) -> ExpectedSourceObjectInventory:
    """从源 payload 文本确定性生成「应出现的源对象」清单（纯结构，零 LLM）。

    入参 ``sources`` 先按规范源顺序排序（document_version → page_number → block_index →
    fragment_offset），清单顺序与身份因此与 content-addressed material_id 顺序无关。
    """
    inv = ExpectedSourceObjectInventory(
        aspect_id=aspect_id, version=SOURCE_OBJECT_INVENTORY_VERSION)
    seen: set[str] = set()
    ordered = _canonical_order(sources)
    if ordered:
        inv.document_id = ordered[0].position.document_id
        inv.source_boundary_identity = ordered[0].position.source_boundary_identity
    # 通用表题判定在**跨块单一源流**上进行（与摊平表恢复共用同一函数）：真实表题常单独成块，
    # 其单位/表头/数据行在相邻下一块，块内判定会漏掉真表题。
    line_groups = [(s.text or "").splitlines() for s in ordered]
    flags_by_text = TBL.detect_table_start_flags_across(line_groups)
    for ti, src in enumerate(ordered):
        pos = src.position
        lines = line_groups[ti]
        start_flags = flags_by_text[ti]
        for li, raw in enumerate(lines):
            s = TBL.normalize_line(raw)
            if not s:
                continue

            def _emit(object_id: str, kind: str, label: str) -> None:
                if object_id in seen:
                    return
                seen.add(object_id)
                inv.expected_objects.append(ExpectedSourceObject(
                    object_id=object_id, kind=kind, label=label,
                    source_text_index=ti, line_index=li,
                    document_id=pos.document_id,
                    document_version=pos.document_version,
                    page_number=pos.page_number, block_index=pos.block_index,
                    fragment_offset=pos.fragment_offset, material_id=pos.material_id,
                    source_boundary_identity=pos.source_boundary_identity,
                    source_object_id=_source_object_id(
                        pos.document_id, pos.document_version,
                        pos.source_boundary_identity, object_id)))

            # 续表信号必须先于表号识别（「表 5-10（续）」是续表，不是普通主表号）。
            if TBL.is_continuation_marker(s):
                cont_num = _continuation_target_number(s)
                _emit(f"continuation:{cont_num or _slug(s)}", KIND_CONTINUATION, s)
                continue
            num = table_number_of(s)
            if num:
                _emit(f"table:{num}", KIND_TABLE_NUMBER, s)
                continue
            targets = cross_reference_target_numbers(s)
            if targets:
                # P1-B.7：每个引用目标各自构成独立源对象（绝不合并成一个）。
                for t in targets:
                    _emit(f"cross_ref:{t}:{ti}:{li}", KIND_CROSS_REFERENCE, s)
                continue
            if _has_page_chapter_reference(s):
                _emit(f"cross_ref:{_PAGE_CHAPTER_SENTINEL}:{ti}:{li}",
                      KIND_CROSS_REFERENCE, s)
                continue
            if start_flags[li]:
                _emit(f"title:{_slug(s)}", KIND_TABLE_START, s)
    return inv


# ---------------------------------------------------------------------------
# 对账（恢复后，逐对象唯一结果）
# ---------------------------------------------------------------------------

def _map_status(status: str) -> str:
    """持久化 assembly 的 ``recovery_status`` → 源对象恢复结果（未知一律按 failed）。"""
    return {"ok": RECOVERED_OK, "partial": RECOVERED_PARTIAL,
            "failed": RECOVERY_FAILED}.get(str(status or ""), RECOVERY_FAILED)


def _flattened_assemblies(assemblies: Iterable[dict]) -> list[dict]:
    """摊平表恢复产物（孤儿/无题表穷尽披露只覆盖这一条恢复路径）。"""
    return _by_relation(assemblies, FLATTENED_TABLE_RELATION)


def _reference_table_object_assemblies(assemblies: Iterable[dict]) -> list[dict]:
    """§三 P1-4：显式引用成功解析出的目标表对象投影（第二条「已获得」来源）。

    只承认真正内容寻址的投影（``table_object_id`` 非空）；缺身份者不可作为「已获得」见证。
    """
    return [a for a in _by_relation(assemblies, REFERENCE_TABLE_OBJECT_RELATION)
            if a.get("table_object_id")]


def _by_relation(assemblies: Iterable[dict], relation: str) -> list[dict]:
    out = []
    for a in assemblies or ():
        if not isinstance(a, dict):
            continue
        if a.get("relation") != relation:
            continue
        if not a.get("assembly_id"):
            continue
        out.append(a)
    return out


def reconcile_source_object_inventory(
        inv: ExpectedSourceObjectInventory,
        assemblies: Sequence[dict],
        known_material_ids: Iterable[str] | None = None) -> ExpectedSourceObjectInventory:
    """把每个期望源对象对账到唯一**持久化 assembly**；未匹配的 assembly 显式列为孤儿。

    ``assemblies`` 是已持久化的 ``TableAssembly`` dict（``relation == flattened_table_recovery``），
    每项至少含 ``assembly_id`` / ``component_material_ids`` / ``table_title`` /
    ``recovery_status``。``recovered_ok`` 绝不由清单侧另行恢复派生：无 assembly ⇒ 绝不 ok。

    ``known_material_ids`` 非空时校验 assembly component 外键真实存在（悬空 component →
    该源对象 recovery_failed，绝不静默通过）。
    """
    results: list[SourceObjectRecoveryResult] = []
    asm_flat = _flattened_assemblies(assemblies)
    asm_ref = _reference_table_object_assemblies(assemblies)
    # §三 P1-4：「已获得」由**两条**已落盘恢复路径共同见证（摊平表恢复 / 引用目标表对象），
    # 两者都是真实持久化投影，绝不在清单侧另行恢复。
    asm = asm_flat + asm_ref
    known = set(known_material_ids) if known_material_ids is not None else None

    tables_by_num: dict[str, list[dict]] = {}
    tables_by_title: dict[str, list[dict]] = {}
    for t in asm:
        title = t.get("table_title", "") or ""
        tables_by_num.setdefault(table_number_of(title), []).append(t)
        tables_by_title.setdefault(_slug(title), []).append(t)

    claimed: set[str] = set()
    claimed_object_ids: set[str] = set()

    def _object_ids(t: dict) -> tuple[str, ...]:
        oid = str(t.get("table_object_id") or "")
        return (oid,) if oid else ()

    def _bind(obj_id: str, t: dict, result: str, issue: str = "",
              matched: str | None = None) -> SourceObjectRecoveryResult:
        aid = t.get("assembly_id", "") or ""
        comps = tuple(t.get("component_material_ids") or ())
        raw_status = str(t.get("recovery_status", "") or "")
        matched_table = (matched if matched is not None
                         else str(t.get("table_title", "") or ""))
        if known is not None:
            missing = [c for c in comps if c not in known]
            if missing:
                return SourceObjectRecoveryResult(
                    obj_id, RECOVERY_FAILED, matched_table,
                    f"assembly {aid} component 外键不存在: {'|'.join(missing)}", aid, comps,
                    raw_status, f"dangling_component:{'|'.join(missing)}")
        return SourceObjectRecoveryResult(obj_id, result, matched_table,
                                          issue, aid, comps, raw_status, issue,
                                          _object_ids(t))

    def _claim(r: SourceObjectRecoveryResult) -> SourceObjectRecoveryResult:
        if r.assembly_id:
            claimed.add(r.assembly_id)
        claimed_object_ids.update(r.table_object_ids)
        return r

    recovered_ok_assemblies: set[str] = {
        t.get("assembly_id", "")
        for t in asm if _map_status(t.get("recovery_status", "ok")) == RECOVERED_OK}

    for obj in inv.expected_objects:
        if obj.kind in (KIND_TABLE_NUMBER, KIND_TABLE_START):
            if obj.kind == KIND_TABLE_NUMBER:
                num = obj.object_id[len("table:"):]
                hits = tables_by_num.get(num, [])
                missing_msg = f"表号 {num} 在持久化 assemblies 中缺失"
            else:
                key = obj.object_id[len("title:"):]
                hits = tables_by_title.get(key, [])
                missing_msg = f"通用表题 {obj.label!r} 在持久化 assemblies 中缺失"
            if not hits:
                results.append(SourceObjectRecoveryResult(
                    obj.object_id, TARGET_NOT_OBTAINED, "", missing_msg))
                continue
            if len(hits) >= 2:
                results.append(SourceObjectRecoveryResult(
                    obj.object_id, RECOVERY_FAILED, "",
                    f"{obj.label!r} 对账到 {len(hits)} 个 assembly（重复/错误合并）"))
                continue
            t = hits[0]
            r = _bind(obj.object_id, t, _map_status(t.get("recovery_status", "ok")),
                      t.get("recovery_issue") or "")
            # 表号一致性：同号但表题内容不符 → recovery_failed（标题不一致）。
            if r.result == RECOVERED_OK and obj.kind == KIND_TABLE_NUMBER:
                expected_tc = _title_content(obj.label)
                actual_tc = _title_content(t.get("table_title", "") or "")
                if expected_tc and actual_tc and expected_tc != actual_tc:
                    r = replace(r, result=RECOVERY_FAILED,
                                issue=(f"表号 {obj.object_id[len('table:'):]} 标题不一致"
                                       f"（期望 {obj.label!r}，实得 "
                                       f"{t.get('table_title')!r}）"))
            results.append(_claim(r))
        elif obj.kind == KIND_CONTINUATION:
            target = obj.object_id[len("continuation:"):]
            hits = tables_by_num.get(target, [])
            ok = bool(hits) and hits[0].get("assembly_id", "") in recovered_ok_assemblies
            if ok:
                results.append(_claim(
                    _bind(obj.object_id, hits[0], RECOVERED_OK, matched=target)))
            else:
                results.append(SourceObjectRecoveryResult(
                    obj.object_id, TARGET_NOT_OBTAINED, target,
                    f"续表目标表号 {target} 未获得 recovered_ok"))
        elif obj.kind == KIND_CROSS_REFERENCE:
            # 引用本身不是一张要恢复的表；其 result 由「**本对象自己的**引用目标表号是否实际
            # recovered_ok」裁决（dangling → target_not_obtained，绝不无条件 recovered_ok）。
            # P1-B.7：裁决对象身份必须来自源对象身份（cross_ref:{目标}:{ti}:{li}），
            # 绝不回到整行重新解析全部目标 —— 否则同一行内一个目标缺失会污染另一个目标。
            target = cross_reference_object_target(obj.object_id)
            if not target or target == _PAGE_CHAPTER_SENTINEL:
                results.append(SourceObjectRecoveryResult(
                    obj.object_id, TARGET_NOT_OBTAINED, "",
                    "显式引用未指明目标表号，无法裁决"))
                continue
            hit = _target_assembly(target, tables_by_num, recovered_ok_assemblies)
            if hit is not None:
                # 引用的「获得」必须由目标表**真实持久化 assembly**见证：绑定该 assembly_id，
                # 使 closure 能核实引用所指对象确实存在（不得凭空声明引用已满足）。
                results.append(_claim(
                    _bind(obj.object_id, hit, RECOVERED_OK, matched=target)))
            else:
                results.append(SourceObjectRecoveryResult(
                    obj.object_id, TARGET_NOT_OBTAINED, target,
                    f"显式引用目标表号 {target} 未获得 recovered_ok"))
        else:  # pragma: no cover - 防御
            results.append(SourceObjectRecoveryResult(
                obj.object_id, RECOVERY_FAILED, "", f"未知源对象种类 {obj.kind!r}"))

    # 未被任何源对象认领的恢复表必须**穷尽披露**，且两个桶互斥：
    # - 带表题 → 本应能被某个源对象认领却没认领 ⇒ 错误合并/清单缺口（orphan，硬缺陷）；
    # - 无表题/无表号 → 没有任何对象身份可对账 ⇒ untitled（诚实披露，非静默跳过）。
    # 两个桶只覆盖**摊平表恢复**这条路径（其恢复结果必然带表题/表号）；引用表对象由
    # ``unclaimed_reference_objects`` 独立穷尽披露，两者互不掩盖。
    titled_ids = {t.get("assembly_id", "") for t in asm_flat if t.get("table_title")}
    untitled_ids = {t.get("assembly_id", "") for t in asm_flat
                    if t.get("assembly_id") and not t.get("table_title")}
    inv.orphan_assemblies = sorted(titled_ids - claimed)
    inv.untitled_recovered_tables = sorted(untitled_ids - claimed)
    inv.unmatched_recovered_tables = sorted(
        {t.get("table_title", "") for t in asm_flat
         if t.get("table_title") and t.get("assembly_id", "") not in claimed})
    inv.unclaimed_reference_objects = sorted(
        {oid for t in asm_ref for oid in _object_ids(t)} - claimed_object_ids)
    inv.recovery_results = results
    return inv


def _target_assembly(target: str, tables_by_num: dict[str, list[dict]],
                     recovered_ok_assemblies: set[str]) -> dict | None:
    """目标表号的**已 recovered_ok 的持久化 assembly**；不存在 → None（引用不得无条件 ok）。"""
    hits = tables_by_num.get(target, [])
    if not hits:
        return None
    if hits[0].get("assembly_id", "") not in recovered_ok_assemblies:
        return None
    return hits[0]


# ---------------------------------------------------------------------------
# 逐项对账门（main_business accepted 的前置：绝不「至少一张表成功」）
# ---------------------------------------------------------------------------

_STATUS_FOR_RESULT = {RECOVERED_OK: "ok", RECOVERED_PARTIAL: "partial"}


def inventory_assembly_closure(
        inv: ExpectedSourceObjectInventory,
        assemblies: Sequence[dict],
        known_material_ids: Iterable[str] | None = None) -> str | None:
    """清单 ↔ 持久化 assembly 双向闭合校验（P1-B.5/B.6/B.7），返回失败原因或 None。

    逐项检查（任一不成立即 fail-closed）：
    - 结果声明 recovered_ok/recovered_partial 的源对象必须绑定**真实存在**的 assembly_id；
    - 其 component_material_ids 必须与持久化 assembly 逐一一致；
    - 结果必须与持久化 assembly 的 ``recovery_status`` 一致（ok↔recovered_ok，
      partial↔recovered_partial）—— 结果词绝不与恢复事实互相矛盾；
    - component 外键必须真实存在（给出 ``known_material_ids`` 时）；
    - 结果声明为「未获得/失败」的源对象不得绑定 assembly；
    - **反向矛盾**：声明失败/未获得、但持久化 assemblies 中确有 recovered_ok 的同名对象
      → 矛盾（清单=failed 而 assembly=ok 绝不允许）。
    """
    asm = _flattened_assemblies(assemblies)
    asm_ref = _reference_table_object_assemblies(assemblies)
    by_id = {a.get("assembly_id", ""): a for a in asm + asm_ref}
    known = set(known_material_ids) if known_material_ids is not None else None
    recovered_ok_ids = {
        a.get("assembly_id", "") for a in asm + asm_ref
        if _map_status(a.get("recovery_status", "ok")) == RECOVERED_OK}
    tables_by_num: dict[str, list[dict]] = {}
    tables_by_title: dict[str, list[dict]] = {}
    for a in asm + asm_ref:
        title = a.get("table_title", "") or ""
        tables_by_num.setdefault(table_number_of(title), []).append(a)
        tables_by_title.setdefault(_slug(title), []).append(a)

    problems: list[str] = []
    for r in inv.recovery_results:
        if r.result in (RECOVERED_OK, RECOVERED_PARTIAL):
            if not r.assembly_id:
                problems.append(f"{r.object_id}: 结果为 {r.result} 但未绑定 assembly_id")
                continue
            a = by_id.get(r.assembly_id)
            if a is None:
                problems.append(
                    f"{r.object_id}: assembly_id {r.assembly_id} 不存在于持久化 assemblies")
                continue
            if tuple(a.get("component_material_ids") or ()) != tuple(r.component_material_ids):
                problems.append(f"{r.object_id}: component_material_ids 与持久化 assembly 不一致")
            want = _STATUS_FOR_RESULT.get(r.result, "")
            got = str(a.get("recovery_status", "") or "")
            if want and got and want != got:
                problems.append(
                    f"{r.object_id}: 结果为 {r.result} 但持久化 assembly recovery_status={got}")
            # §三 P1-4：由**引用表对象**见证的「已获得」必须绑定该对象的内容寻址身份，且该身份
            # 必须与持久化投影里的 ``table_object_id`` 逐一相同（绝不用表号字符串凑合）。
            if a.get("relation") == REFERENCE_TABLE_OBJECT_RELATION:
                oid = str(a.get("table_object_id") or "")
                if not oid or oid not in tuple(r.table_object_ids or ()):
                    problems.append(
                        f"{r.object_id}: 由引用表对象见证为已获得，但未绑定其内容寻址身份"
                        f"（assembly {r.assembly_id} table_object_id={oid!r}，"
                        f"清单记录={list(r.table_object_ids or ())}）")
            if known is not None:
                dangling = [c for c in r.component_material_ids if c not in known]
                if dangling:
                    problems.append(
                        f"{r.object_id}: component 外键悬空: {'|'.join(dangling)}")
        else:
            if r.assembly_id:
                problems.append(
                    f"{r.object_id}: 结果为 {r.result} 却绑定 assembly_id {r.assembly_id}（矛盾声明）")
            # 反向矛盾：清单说不存在，但持久化里确有 recovered_ok 的同名恢复表。
            if r.object_id.startswith("table:"):
                hits = tables_by_num.get(r.object_id[len("table:"):], [])
            elif r.object_id.startswith("title:"):
                hits = tables_by_title.get(r.object_id[len("title:"):], [])
            elif r.object_id.startswith("continuation:"):
                hits = tables_by_num.get(r.object_id[len("continuation:"):], [])
            elif r.object_id.startswith("cross_ref:"):
                hits = tables_by_num.get(cross_reference_object_target(r.object_id), [])
            else:
                hits = []
            live = [h for h in hits if h.get("assembly_id", "") in recovered_ok_ids]
            if live:
                problems.append(
                    f"{r.object_id}: 结果为 {r.result} 但持久化 assemblies 存在 recovered_ok "
                    f"的同名恢复表 {live[0].get('assembly_id')}（清单与恢复事实矛盾）")
    return "; ".join(problems) or None


def source_object_gate(inv: ExpectedSourceObjectInventory,
                       assemblies: Sequence[dict] | None = None,
                       known_material_ids: Iterable[str] | None = None) -> str | None:
    """逐项对账门：返回失败原因（非空即 fail-closed），全 ok → None。

    §四.D.9/D.10：本门只判**归因缺陷**与**能力/完整性门失败**，绝不把诚实、可复核的
    负面材料状态本身当成代码失败：

    - ``recovery_failed``（识别出的表格对象无法恢复，或身份矛盾/重复合并/外键悬空）→ 失败；
    - ``target_not_obtained``（该源对象在本轮材料中确实不存在）**不是**失败：它由
      ``recovery_results`` 逐对象如实落盘，并由调用方形成诚实的 material_state
      （boundary_incomplete / not_obtained）；但**必须带原因**，无原因即不可归因 → 失败；
    - ``recovered_partial``（真实部分恢复）→ 失败（恢复事实不完整，不得当完整集合用）；
    - 存在未匹配恢复表（错误合并/清单缺口）→ 失败；
    - 给出 assemblies 时追加清单↔assembly 双向闭合校验（含 result↔recovery_status
      一致性、component 外键存在性、反向矛盾）。
    """
    problems: list[str] = []
    for r in inv.recovery_results:
        if r.result == RECOVERED_OK:
            continue
        if r.result == TARGET_NOT_OBTAINED:
            if not (r.issue or r.recovery_reason):
                problems.append(f"{r.object_id}:{r.result}(缺原因，不可归因)")
            continue
        problems.append(f"{r.object_id}:{r.result}" + (f"({r.issue})" if r.issue else ""))
    if inv.unmatched_recovered_tables:
        problems.append(
            "unmatched_recovered_tables:" + "|".join(inv.unmatched_recovered_tables))
    if inv.orphan_assemblies:
        problems.append("orphan_assemblies:" + "|".join(inv.orphan_assemblies))
    if inv.unclaimed_reference_objects:
        # §三 P1-4：目标表已被解析并进入材料库，清单却没有对应「已获得」源对象 ⇒ 两套真相。
        problems.append(
            "unclaimed_reference_objects:" + "|".join(inv.unclaimed_reference_objects))
    if assemblies is not None:
        closure = inventory_assembly_closure(inv, assemblies, known_material_ids)
        if closure:
            problems.append(closure)
    if not problems:
        return None
    return "source_object_inventory 未逐项闭合: " + "; ".join(problems)


def reconciliation_summary(inv: ExpectedSourceObjectInventory) -> dict[str, int]:
    """四态分布统计（ok/partial/failed/not_obtained），供审计与报告。"""
    counts = {r: 0 for r in RECOVERY_RESULTS}
    for r in inv.recovery_results:
        counts[r.result] = counts.get(r.result, 0) + 1
    return counts
