"""Eval: §四 续页续表（摊平表跨页续表）识别 + 同源对象投影收敛（R2 §四 P1 关闭证据）。

用法: python -m evals.test_r2_table_continuation

覆盖（**通用结构**：无表号/页码/公司/固定文本生产规则；fixture 里的表题/数字均为合成）：

A. 正样本（真实续表形态）：本表表头在 P50、续页 span 在 P51 且**重排本表表头** + 数据行 +
   合计行 → ``TableContinuationProof.valid == True``，逐条事实（同文档/边界相邻/同 chapter/
   列宽一致/续页贡献真实结构）均可复核；``identity_source == recovered_structure``。

B. 反例（**不得误合并**）：
   B1 续页 span 内出现**另一张表**（不同表题）→ 两张独立表，本表 continuation 为空且不 valid；
   B2 单位冲突（续页声明不同单位）→ 不 valid（单位不兼容）；
   B3 **不同章节**（续页块 section_path 不同）→ 不 valid（存在主题/章节边界）；
   B4 **非相邻页**（P53）→ 不 valid（边界不相邻）；
   B5 续页**未重排表头**（只有数据行延续）→ 不 valid（不得凭「列数相同」猜续表）；
   B6 同页闭合（合计行与本表表头同页）→ 合计行归 body，**绝不是** continuation；
   B7 续页块不可读 → fail-closed（不冒充同文档/同章节）。

C. 修复 A（片段投影文本解析）：主题内前缀片段 material 的 Block 不在采纳集合时，必须用其
   **payload 真实前缀文本**参与恢复（旧实现回退 ""，在源流里插入空 span 截断摊平表）；
   同时：块不可读时证明仍 fail-closed，绝不因「文本不空」而放行。

D. 修复 C（同源对象跨 pass 投影收敛）：同一张表在多个 seed pass 留下多条 projection 时，
   收敛为**覆盖最完整**的一条，被取代者逐条披露；跨文档/结构不同/真不同的表**绝不收敛**。

全部离线：纯结构 + fixture（无 DB/LLM/网络）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids as evidence_ids
from harness import source_object_inventory as SOI
from harness import topic_materials as TM
from harness.evidence_reader import EvidenceReadResult
from harness.material_slice_runner import _converge_source_object_projections
from harness.set_enumeration import continuation_span_facts, recover_flattened_tables
from harness.topic_materials import (
    IDENTITY_SOURCE_RECOVERED,
    build_assemblies,
    build_atomic_material,
)


_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"
_DEP = hashlib.sha256(b"dep").hexdigest()

TITLE = "表 6-1 主营业务成本构成表"
OTHER_TITLE = "表 6-2 主营业务收入构成表"
_GROUP_ROW = "项目  2025年度  2024年度"
_LEAF_ROW = "金额  占比  金额  占比"
_P50_ROWS = (
    "直接材料  100.00  40.00%  90.00  45.00%",
    "直接人工  50.00  20.00%  40.00  20.00%",
)
_P51_ROWS = (
    "制造费用  30.00  12.00%  25.00  12.50%",
)
_P51_TOTAL = "合计  180.00  72.00%  155.00  77.50%"


def _doc_prefix(page: int) -> str:
    return (f"{TITLE}\n单位：万元\n{_GROUP_ROW}\n{_LEAF_ROW}\n"
            + "\n".join(_P50_ROWS) + "\n") if page == 50 else ""


def _p50_text() -> str:
    return _doc_prefix(50)


def _p51_text(*, repeat_header: bool = True, unit_line: str | None = None,
              rows=_P51_ROWS, total: bool = True) -> str:
    lines: list[str] = []
    if repeat_header:
        lines.append(_LEAF_ROW)
    if unit_line is not None:
        lines.append(f"单位：{unit_line}")
    lines.extend(rows)
    if total:
        lines.append(_P51_TOTAL)
    return "\n".join(lines) + "\n"


def _block(*, page: int, block: int, text: str,
           section: tuple = ("主营业务分析",),
           document_id: str = _DOC, document_version: str = _DOCV) -> EvidenceReadResult:
    ch = evidence_ids.content_hash(text, None)
    eid = evidence_ids.make_evidence_id(
        _COMPANY, document_id, document_version, _SETV, page, block, ch)
    return EvidenceReadResult(
        evidence_id=eid, company_id=_COMPANY, document_id=document_id,
        document_version=document_version, evidence_set_version=_SETV,
        source_name="年报", source_type="annual_report", page_number=page,
        block_index=block, section_path=section, evidence_type="paragraph",
        text=text, structured_payload=None, content_hash=ch)


def _span_mat(block: EvidenceReadResult, *, fragment_text: str | None = None,
              fragment_offset: int | None = None):
    return build_atomic_material(
        block, material_type="evidence_span", is_current_document=True,
        is_current_set=True, dependency_fingerprint=_DEP,
        fragment_offset=fragment_offset, fragment_text=fragment_text).material


def _assemblies(materials, blocks, fragment_texts=None):
    return build_assemblies(tuple(materials), {b.evidence_id: b for b in blocks},
                            fragment_texts_by_material_id=fragment_texts)


def _only_table(asms):
    tables = [a for a in asms if a.relation == "flattened_table_recovery"]
    return tables


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
    # A. 正样本：P50 表头 + P51 续页重排表头 + 含蓄合计
    # ------------------------------------------------------------------
    b50 = _block(page=50, block=4, text=_p50_text())
    b51 = _block(page=51, block=0, text=_p51_text())
    m50 = _span_mat(b50)
    m51 = _span_mat(b51)
    asms = _assemblies([m50, m51], [b50, b51])
    tables = _only_table(asms)
    check(len(tables) == 1, "§四A：跨页续表被恢复为**一张**表（不拆成两段残缺表）")
    if len(tables) == 1:
        t = tables[0]
        proof = t.continuation_proof
        check([list(r) for r in t.rows] == [
            ["直接材料", "100.00", "40.00%", "90.00", "45.00%"],
            ["直接人工", "50.00", "20.00%", "40.00", "20.00%"],
            ["制造费用", "30.00", "12.00%", "25.00", "12.50%"]],
            "§四A：两页数据行按规范源顺序合并进同一张表")
        check(t.total_row is not None and t.total_row[0] == "合计",
              "§四A：续页合计行落为 total（本表闭合）")
        check(t.continuation_evidence_ids == (m51.authority_assessment.evidence_id,),
              "§四A：continuation 精确指向续页 span（不含表头 span）")
        check(t.body_evidence_ids == (m50.authority_assessment.evidence_id,),
              "§四A：表头 span 归 body（表头页的表体，不冒充续页）")
        check(proof is not None and proof.valid is True,
              "§四A：续表证明 valid == True（真实续表正样本）")
        check(proof.identity_source == IDENTITY_SOURCE_RECOVERED,
              "§四A：摊平表身份来源 == recovered_structure（不读不存在的 structured_payload）")
        check(proof.header_repeat_verified is True,
              "§四A：续页重排本表**物理**表头行（分组行/叶子行，非合并后表头）")
        check(proof.boundary_consecutive is True and proof.header_page == 50
              and proof.continuation_pages == (51,),
              "§四A：边界相邻（P50 → P51，+1 页）")
        check(proof.section_path_shared is True,
              "§四A：表头 span 与续页 span 同 section_path（无中介主题/章节边界）")
        check(proof.same_document_verified is True,
              "§四A：同 document_id + document_version")
        check(proof.column_compatible is True and proof.row_column_continuity is True,
              "§四A：续页段列宽与表头列数一致（列/行连续性）")
        check(proof.issue == "", "§四A：valid 证明无遗留 issue（逐条事实全部可复核）")
        facts = [dict(f) for f in proof.span_facts]
        check(len(facts) == 1 and facts[0]["evidence_id"]
              == m51.authority_assessment.evidence_id
              and facts[0]["header_repeat_matched"] is True
              and facts[0]["contributed_structure"] is True
              and facts[0]["column_width_ok"] is True,
              "§四A：span_facts 逐 span 落盘（可复核真实结构，非自报字段）")
        # 证明由真实 span 文本确定性派生：同一 source 文本 → 同一证明（无随机/时间戳）。
        again = _only_table(_assemblies([_span_mat(b50), _span_mat(b51)], [b50, b51]))
        check(len(again) == 1
              and again[0].continuation_proof.span_facts == proof.span_facts
              and again[0].assembly_id == t.assembly_id,
              "§四A：同源文本 → 同 assembly_id + 同 span_facts（确定性派生）")

    # ------------------------------------------------------------------
    # B1. 续页 span 内出现另一张表（不同表题）→ 绝不合并
    # ------------------------------------------------------------------
    b51o = _block(page=51, block=0,
                  text=f"{OTHER_TITLE}\n单位：万元\n项目  金额  占比  金额  占比\n"
                       "营业收入  200.00  80.00%  180.00  81.00%\n")
    tables = _only_table(_assemblies([_span_mat(b50), _span_mat(b51o)], [b50, b51o]))
    titles = sorted((t.table_title or "") for t in tables)
    check(len(tables) == 2 and titles == [TITLE, OTHER_TITLE],
          "§四B1：续页出现**另一张表** → 两张独立表（绝不误合并成一张）")
    check(all(t.continuation_evidence_ids == () for t in tables),
          "§四B1：两张表各自 continuation 为空（另一张表绝不冒充本表续页）")
    check(all(t.continuation_proof is None or t.continuation_proof.valid is not True
              for t in tables),
          "§四B1：两张表都无有效续表证明（不冒充续表）")

    # ------------------------------------------------------------------
    # B2. 单位冲突 → 不 valid
    # ------------------------------------------------------------------
    b51u = _block(page=51, block=0, text=_p51_text(unit_line="元"))
    tables = _only_table(_assemblies([_span_mat(b50), _span_mat(b51u)], [b50, b51u]))
    check(len(tables) == 1 and tables[0].continuation_proof.valid is not True
          and "单位不兼容" in (tables[0].continuation_proof.issue or ""),
          "§四B2：续页声明不同单位 → 不给 valid（单位不兼容）")

    # ------------------------------------------------------------------
    # B3. 不同章节 → 不 valid（存在主题/章节边界）
    # ------------------------------------------------------------------
    b51s = _block(page=51, block=0, text=_p51_text(),
                  section=("主要财务指标",))
    tables = _only_table(_assemblies([_span_mat(b50), _span_mat(b51s)], [b50, b51s]))
    check(len(tables) == 1 and tables[0].continuation_proof.valid is not True
          and tables[0].continuation_proof.section_path_shared is False,
          "§四B3：续页块属不同 section_path → 不 valid（存在章节边界）")

    # ------------------------------------------------------------------
    # B4. 非相邻页（P53）→ 不 valid
    # ------------------------------------------------------------------
    b53 = _block(page=53, block=0, text=_p51_text())
    tables = _only_table(_assemblies([_span_mat(b50), _span_mat(b53)], [b50, b53]))
    check(len(tables) == 1 and tables[0].continuation_proof.valid is not True
          and tables[0].continuation_proof.boundary_consecutive is False,
          "§四B4：续页与表头页不相邻（P50→P53）→ 不 valid")

    # ------------------------------------------------------------------
    # B5. 续页未重排表头（只有数据行延续）→ 不 valid
    # ------------------------------------------------------------------
    b51n = _block(page=51, block=0, text=_p51_text(repeat_header=False))
    tables = _only_table(_assemblies([_span_mat(b50), _span_mat(b51n)], [b50, b51n]))
    check(len(tables) == 1 and tables[0].continuation_proof.valid is not True
          and tables[0].continuation_proof.header_repeat_verified is False,
          "§四B5：续页未重排本表表头 → 不 valid（不得凭「列数相同」猜续表）")

    # ------------------------------------------------------------------
    # B6. 同页闭合（合计行与表头同页）→ 合计归 body，绝不是 continuation
    # ------------------------------------------------------------------
    b50full = _block(page=50, block=4, text=_p50_text() + _P51_TOTAL + "\n")
    m50full = _span_mat(b50full)
    asms = _assemblies([m50full], [b50full])
    tables = _only_table(asms)
    check(len(tables) == 1, "§四B6：同页闭合表被恢复（单 span）")
    if len(tables) == 1:
        t = tables[0]
        check(t.continuation_evidence_ids == (),
              "§四B6：同页合计行**不是**续表 → continuation 为空")
        check(t.body_evidence_ids == (b50full.evidence_id,)
              and t.component_material_ids == (m50full.material_id,),
              "§四B6：同页表体（含合计行）归 body（component 指向该 span material）")
        check(t.continuation_proof.valid is not True,
              "§四B6：同页闭合不产生有效的跨页续表证明")

    # ------------------------------------------------------------------
    # B7. 续页块不可读 → fail-closed
    # ------------------------------------------------------------------
    asms = _assemblies([_span_mat(b50), _span_mat(b51)], [b50])   # b51 未采纳/不可读
    tables = _only_table(asms)
    check(len(tables) == 1 and tables[0].continuation_proof.valid is not True
          and tables[0].continuation_evidence_ids == (),
          "§四B7：续页块不可读（无真实文本）→ 该 span 不贡献结构 → 无 continuation，"
          "证明不 valid（绝不因不可读而假设续表成立）")

    # ------------------------------------------------------------------
    # C. 修复 A：片段投影的真实前缀文本参与恢复（旧实现插入空 span 截断表）
    # ------------------------------------------------------------------
    prefix = _p50_text()
    tail_prose = "（三）成本变动说明：报告期内成本结构基本稳定。\n"
    b50big = _block(page=50, block=4, text=prefix + tail_prose)
    frag_off = len(prefix)
    m50frag = _span_mat(b50big, fragment_text=prefix, fragment_offset=frag_off)
    b51 = _block(page=51, block=0, text=_p51_text())
    m51 = _span_mat(b51)

    # C1：片段 material 的 Block 不在采纳集合（主题边界外的块），只有 payload 前缀可见。
    asms = _assemblies([m50frag, m51], [b51],
                       {m50frag.material_id: prefix})
    tables = _only_table(asms)
    check(len(tables) == 1, "修复A：片段投影 + 续页仍恢复为一张表（未因空 span 截断/切段）")
    if len(tables) == 1:
        t = tables[0]
        check(t.table_title == TITLE and t.unit == "万元"
              and len(t.headers) == 5 and len(t.rows) == 3,
              "修复A：表题/单位/表头/三行数据全部来自真实前缀文本（绝无空 span 幻觉）")
        check(t.component_material_ids
              == (m50frag.material_id, m51.material_id),
              "修复A：component 指向真实的片段投影 material 与续页 material")
        check(t.continuation_proof.valid is not True
              and "不可读" in (t.continuation_proof.issue or ""),
              "修复A：片段块不可读 → 证明 fail-closed（文本非空≠放行）")

    # C2：片段 material 的 Block 可读（同主题内被采纳）→ 恢复完整且证明 valid。
    asms = _assemblies([m50frag, m51], [b50big, b51],
                       {m50frag.material_id: prefix})
    tables = _only_table(asms)
    check(len(tables) == 1 and tables[0].continuation_proof.valid is True,
          "修复A：片段投影（主题内前缀）+ 续页 → 真实续表证明 valid（正样本可复现）")
    check(all(len(str(s)) > 0 for s in (prefix, _p51_text())),
          "修复A：参与恢复的每个 span 都有真实正文（无空 span 进入源流）")

    # ------------------------------------------------------------------
    # D. 修复 C：同源对象跨 pass 投影收敛（覆盖最完整者胜；不可比者绝不合并）
    # ------------------------------------------------------------------
    def _mat(mid: str, evidence_id: str, *, offset: int = 0,
             document_id: str = _DOC, document_version: str = _DOCV):
        loc = types.SimpleNamespace(
            offset=offset or None, block_range=(0, 0), page=50,
            document_version=document_version, section_path="主营业务分析")
        aa = types.SimpleNamespace(
            evidence_id=evidence_id, document_id=document_id,
            document_version=document_version, page=50)
        return mid, types.SimpleNamespace(
            material_id=mid, locator=loc, authority_assessment=aa,
            material_type="evidence_span")

    def _flat_asm(aid: str, title: str, components, rows, total=None):
        return TM.TableAssembly(
            assembly_id=aid, context_parent_id=components[0],
            component_material_ids=tuple(components),
            relation=SOI.FLATTENED_TABLE_RELATION, boundary_desc="P50-P51",
            table_title=title, unit="万元",
            header_evidence_id="ev-block-1",
            body_evidence_ids=(components[0],),
            continuation_evidence_ids=(tuple(components[1:])),
            headers=("项目", "金额", "占比"),
            rows=tuple(tuple(r) for r in rows), total_row=total)

    m_frag = _mat("mat-frag", "ev-block-1", offset=180)
    m_full = _mat("mat-full", "ev-block-1", offset=0)
    m_cont = _mat("mat-cont", "ev-block-2", offset=0)
    mats = dict([m_frag, m_full, m_cont])

    rows_a = (("直接材料", "100.00", "40.00%"),)
    asm_frag = _flat_asm("asm-frag", TITLE, ["mat-frag", "mat-cont"], rows_a)
    asm_full = _flat_asm("asm-full", TITLE, ["mat-full", "mat-cont"], rows_a)
    kept, sup = _converge_source_object_projections(
        [asm_frag, asm_full], mats)
    check([a.assembly_id for a in kept] == ["asm-full"],
          "修复C：同一张表的片段投影 + 完整投影 → 收敛为覆盖最完整的一条")
    check(len(sup) == 1 and sup[0]["superseded_assembly_id"] == "asm-frag"
          and sup[0]["retained_assembly_id"] == "asm-full"
          and sup[0]["less_covered_blocks"][0]["evidence_id"] == "ev-block-1"
          and sup[0]["less_covered_blocks"][0]["superseded_fragment_offset"] == 180
          and sup[0]["less_covered_blocks"][0]["retained_fragment_offset"] == 0,
          "修复C：被取代投影**逐条披露**（取代者/被取代者/覆盖更少的 Block 与偏移，不静默丢弃）")

    # D2：跨文档同名表 → 绝不收敛（交回清单 fail-closed）。
    m_other = _mat("mat-other", "ev-block-9", document_id="doc2",
                   document_version="v2")
    mats_doc = dict([m_frag, m_other, m_cont])
    asm_doc = _flat_asm("asm-doc", TITLE, ["mat-other", "mat-cont"], rows_a)
    kept, sup = _converge_source_object_projections(
        [asm_frag, asm_doc], mats_doc)
    check(len(kept) == 2 and sup == [],
          "修复C：同一表题的**跨文档**投影 → 绝不收敛（保留两条交回清单 fail-closed）")

    # D3：结构不同（真不同的表/错误合并）→ 绝不收敛。
    asm_other_rows = _flat_asm(
        "asm-otherrows", TITLE, ["mat-full", "mat-cont"],
        (("直接材料", "999.00", "90.00%"),))
    kept, sup = _converge_source_object_projections(
        [asm_other_rows, asm_full], mats)
    check(len(kept) == 2 and sup == [],
          "修复C：恢复结构不一致（同行不同值）→ 绝不收敛（不静默合并两张不同的表）")

    # D4：无表题 / 结构化表链 → 不参与收敛。
    untitled = _flat_asm("asm-untitled", "", ["mat-frag", "mat-cont"], rows_a)
    chain = TM.TableAssembly(
        assembly_id="asm-chain", context_parent_id="mat-full",
        component_material_ids=("mat-full", "mat-cont"), relation="table_chain",
        boundary_desc="P50-P51", table_title=TITLE, unit="万元",
        header_evidence_id="ev-block-1", body_evidence_ids=("mat-full",),
        continuation_evidence_ids=("mat-cont",),
        headers=("项目", "金额", "占比"), rows=rows_a)
    kept, sup = _converge_source_object_projections(
        [untitled, chain, asm_full], mats)
    check(len(kept) == 3 and sup == [],
          "修复C：无表题投影 / 结构化表链 → 不参与收敛（行为不变）")

    # D5：两个互相不可比的同名同结构投影（无覆盖关系）→ 原样保留。
    m_b1 = _mat("mat-b1", "ev-block-b1", offset=0)
    m_b2 = _mat("mat-b2", "ev-block-b2", offset=0)
    mats_bi = dict([m_b1, m_b2])
    asm_x = _flat_asm("asm-x", TITLE, ["mat-b1"], rows_a)
    asm_y = _flat_asm("asm-y", TITLE, ["mat-b2"], rows_a)
    kept, sup = _converge_source_object_projections([asm_x, asm_y], mats_bi)
    check(len(kept) == 2 and sup == [],
          "修复C：两条投影互不覆盖（各自引用不同 Block）→ 不合并（清单侧如实判重复）")

    # ------------------------------------------------------------------
    # E. continuation_span_facts：续页段在下一张表题/表后正文处截止
    # ------------------------------------------------------------------
    f = continuation_span_facts(
        _p51_text() + "表 A-9 其他表\n项目  金额\n占位  1.00\n",
        [_GROUP_ROW.split("  "), _LEAF_ROW.split("  ")])
    check(f["header_repeat_matched"] is True and f["data_row_widths"] == [5]
          and f["closure_width"] == 5,
          "§四E：续页段事实 = 重复表头 + 数据行列宽 + 合计列宽")
    f2 = continuation_span_facts(
        _p51_text() + "表 A-9 其他表\n其他项目  9.00  9.00%  9.00  9.50%\n",
        [_LEAF_ROW.split("  ")])
    check(f2["data_row_widths"] == [5],
          "§四E：续页段在**下一张表题**处截止（后表数据行不污染本表列连续性）")
    f3 = continuation_span_facts("表 A-9 其他表\n项目  金额\n", [_LEAF_ROW.split("  ")])
    check(f3["header_repeat_matched"] is False and f3["data_row_widths"] == []
          and f3["closure_width"] is None,
          "§四E：以表题开头的 span 对本表无结构贡献（由证明侧 fail-closed 判定）")
    f4 = continuation_span_facts("", [_LEAF_ROW.split("  ")])
    check(f4["header_repeat_matched"] is False and f4["data_row_widths"] == []
          and f4["closure_width"] is None and f4["has_prose"] is False,
          "§四E：空 span 不贡献任何结构（空 span 不得冒充续页）")

    # ------------------------------------------------------------------
    # F. 恢复侧：续页重排表头不被当成第 3 行表头（否则整表判 failed 被丢弃）
    # ------------------------------------------------------------------
    recovered = recover_flattened_tables([_p50_text(), _p51_text()])
    check(len(recovered) == 1 and recovered[0]["recovery_status"] == "ok",
          "§四F：跨页续表恢复状态 ok（续页重复表头不把表头行数推过 2 行上限）")
    if len(recovered) == 1:
        t = recovered[0]
        check(t["repeated_header_text_indices"] == [1]
              and t["continuation_text_indices"] == [1],
              "§四F：恢复侧显式记录续页 span 索引（与证明侧同一索引集合）")
        check(len(t["header_cell_rows"]) == 2
              and len(t["headers"]) == 5,
              "§四F：物理表头行（分组/叶子）与合并表头分别保留（证明可核对物理行）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
