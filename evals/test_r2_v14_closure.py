"""Eval: R2 v14 最终机制收口的聚焦反例（§三 P1-1 / P1-2 / P1-3 / P1-4 + §四 P2）。

用法: python -m evals.test_r2_v14_closure

覆盖 v14 四项机制与一项 P2 的**反例面**（每条都钉死「旧实现会通过、新实现必须拒绝」的形状）：

* §三 P1-1（保列内容身份）：表对象身份必须在**有序单元格数组**上计算 —— 列边界移动
  （``A|1|23`` vs ``A|12|3``）、列序、单元格内容任一变化必须改变身份；仅列间距宽度/单元格
  内部排版空白不改变身份；旧 schema 版本（"1"/"2"）记录不得被按新语义解读。
* §三 P1-2（资格单一原语）：治理散文/法规折行文本/只有表头的形状**不是**表对象；结构充分性
  按**独立物理行 + 列结构**判定（同一行不得被算成两条证据），且该判据是结构侧与恢复侧共用的
  唯一原语。治理散文被拒绝时不得产生伪 ``target_not_obtained``（理由码是 ``no_candidate``）。
* §三 P1-3（typed occurrence 不得被绕过）：正式分支**只**由 typed ``reference_kind`` 决定；
  未知/缺失 kind、偏移/序号被篡改、自报目标 ≠ 重算目标、「kind=table 而目标是命名引用文本」
  一律 fail-closed，绝不退回「按 reference_target 文本猜种类」。
* §三 P1-4（目标真的进入材料库 → 逐层复核）：``verify_reference_table_object`` 的六层
  （独立性：绑定 → assembly 投影 → payload/身份 → component evidence/material → aspect link
  → 源对象清单）**逐层**做反例：任一层缺失或矛盾都必须 fail-closed。
* §四 P2（表头/表体角色）：表头 + 两行数据必须保留两行表体；第二层表头只由**标签 vs 数字**
  加「结构在下方继续」裁定，绝不按「最多吞两行」的位置规则贪心吞并首个数据行。

全部离线：纯函数 + 临时 evidence.db（只读）+ 生产 dataclass 投影；零 LLM / 网络 / DB 写入。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import set_enumeration as SE  # noqa: E402
from harness import table_structure as TBL  # noqa: E402
from harness.evidence_reader import (  # noqa: E402
    REFERENCE_KIND_NAMED,
    REFERENCE_KIND_TABLE,
    BoundedEvidenceInspectionAdapter,
    ReferenceOccurrence,
)
from harness.material_slice_runner import _assembly_to_dict  # noqa: E402
from harness.six_category_acceptance import (  # noqa: E402
    _explicit_reference_audit,
    verify_reference_binding,
    verify_reference_table_object,
)
from harness.topic_materials import (  # noqa: E402
    REFERENCE_TABLE_OBJECT_RELATION,
    ReferenceTableObjectAssembly,
    reference_table_object_link,
)

from evals.test_r2_reference_binding import (  # noqa: E402
    _CHAPTER_HEADING,
    _COMPANY,
    _DOC,
    _DOCV,
    _SAME_BLOCK,
    _SETV,
    _block_id,
    _binding,
    _make_db,
    _occ,
    _read,
)

_ASPECT = "financial_notes.restricted_assets"

# 真实语料形状（逐行取自真实 evidence.db 的 p41/b0 锚点块，只替换公司名与数字，不新增
# 任何本语料不存在的形态）：块内「表题 + 折行表头 + 两条真实表体行」的同一张表。
_NUMERIC_TEXT = (
    "截至2025年12月末，发行人主要参股及联营、合营企业情况如下表：\n"
    "\n"
    "表5-5截至2025年12月末发行人主要参股及联营、合营企业情况\n"
    "  序号  重要的合营企业或联营企业  注册地  持股比例  对合营企业或联营企业\n"
    "  投资的会计处理方法\n"
    "  1  洛阳栾川钼业集团股份有限公司  洛阳市  24.9%  权益法\n"
    "  2  某某新材料股份有限公司  宜宾市  10.0%  权益法\n"
    "  截至本报告日，上述企业情况未发生重大变化。\n"
)

# 真实 表5-10 形状（结构侧与恢复侧**口径不同**的诚实钉点，见 §四 P2 的反例断言）：
# 行内「金额 占比」之间只有**单空格**（PDF 摊平后的粘连形态），结构侧按 ≥2 空格切列
# 时它是**一个**单元格，于是子列层（6）不比下方任何结构行更窄，不被承认为第二层表头，
# 表体行（6）> 最宽表头层（4）⇒ 资格 4′ 拒绝 ⇒ 结构侧**无候选**。这是 fail-closed。
_FLAT_510_BLOCK = (
    "收入构成如下表：\n"
    "\n"
    "表5-10主营业务收入构成表\n"
    "单位：万元，%\n"
    "  项目  2025年  2024年  2023年\n"
    "  金额  占比  金额  占比  金额  占比\n"
    "  动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n"
    "  储能电池系统  6,243,982.0 14.7  5,729,046.0  15.8  5,990,052.2  14.9\n"
    "  合计  42,370,183.3 100.0  36,201,255.3 100.0  40,091,704.5 100.0\n"
)


def _oid(title: str, *, header=(), body=(), closure="", rows=0, unit="") -> str:
    return TBL.table_object_id(title=title, unit=unit, header_rows=list(header),
                               body_row_texts=list(body), closure_row=closure,
                               structure_rows=rows)


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

    # ======================================================================
    # §三 P1-1：表对象内容身份**保列**（列边界/列序/列数/单元格内容）
    # ======================================================================
    gap1 = _oid("T", header=["序号  名称  金额"], body=["1  A公司  100"], rows=2)
    gap2 = _oid("T", header=["序号    名称    金额"], body=["1      A公司      100"], rows=2)
    check(gap1 == gap2,
          "§三 P1-1：仅列间距宽度/单元格排版空白不同 → 同一对象身份")

    # 旧语义（整行去空白）下 `A|1|23` 与 `A|12|3` 都是 "A123" → 撞同一身份。
    shift1 = _oid("T", header=["序号  名称  金额"], body=["1  A  1  23"], rows=2)
    shift2 = _oid("T", header=["序号  名称  金额"], body=["1  A  12  3"], rows=2)
    check(shift1 != shift2,
          "§三 P1-1：列边界移动（A|1|23 vs A|12|3）→ 身份必须不同（列语义被保留）")

    order1 = _oid("T", header=["序号  名称  金额"], body=["1  A  B"], rows=2)
    order2 = _oid("T", header=["序号  名称  金额"], body=["1  B  A"], rows=2)
    check(order1 != order2, "§三 P1-1：列序变化 → 身份必须不同")

    hdr1 = _oid("T", header=["序号  名称  金额"], body=["1  A  100"], rows=2)
    hdr2 = _oid("T", header=["序号  名称  余额"], body=["1  A  100"], rows=2)
    check(hdr1 != hdr2, "§三 P1-1：表头单元格内容变化 → 身份必须不同")

    check(isinstance(TBL.table_object_payload(title="T", header_rows=["a  b"],
                                              body_row_texts=["1  2"])["header_rows"][0], list),
          "§三 P1-1：canonical payload 的 header_rows/body_rows 是**有序单元格数组**")
    check(TBL.canonical_cells("A    1    23") == TBL.canonical_cells("A  1  23") == ["A", "1", "23"],
          "§三 P1-1：canonical_cells 只归一列间距，不跨列边界合并")

    # 表体摘要与对象身份**同口径**（列边界变化必须同时改变两者）。
    d1 = TBL.table_object_body_digest(["1  A  1  23"])
    d2 = TBL.table_object_body_digest(["1  A  12  3"])
    d3 = TBL.table_object_body_digest(["1      A      1      23"])
    check(d1 != d2 and d1 == d3,
          "§三 P1-1：表体摘要同口径 —— 列边界变化改变摘要，仅间距变化不改变")

    payload = TBL.table_object_payload(title="T", header_rows=["序号  名称"], body_row_texts=["1  A"])
    check(payload["schema_version"] == TBL.REFERENCE_TARGET_OBJECT_SCHEMA_VERSION
          and TBL.REFERENCE_TARGET_OBJECT_SCHEMA_VERSION == "3"
          and TBL.REFERENCE_TARGET_BINDING_VERSION == "3",
          "§三 P1-1：对象 schema / 绑定版本已升到 v3（旧版语义必须 fail-closed）")
    legacy = dict(payload, schema_version="2")
    check(TBL._object_digest(legacy) != TBL._object_digest(payload),
          "§三 P1-1：旧 schema 记录（version=2）与当前语义**不可能**同身份")

    check(TBL.table_object_payload_from_binding({"target_table_title": "T"}) is None
          and TBL.table_object_payload_from_binding(
              {"target_table_title": "T", "target_unit": "", "target_header_rows": ["a  b"],
               "target_body_row_texts": [], "target_closure_row": "",
               "target_structure_rows": 1}) is None,
          "§三 P1-1：残缺绑定记录 → payload 不可重建（None，绝不用默认值填充）")

    # ======================================================================
    # §三 P1-2：表对象资格（单一原语）+ 治理散文不得成为伪目标
    # ======================================================================
    check(TBL.reference_target_table_objects(_CHAPTER_HEADING, 0) == [],
          "§三 P1-2：治理散文（折行 + 多空格 + 句读）不是表对象候选")
    check(TBL.reference_target_table_object(_CHAPTER_HEADING, 0) is None,
          "§三 P1-2：治理散文的引用解析必须是**无候选**（不得伪造出目标）")
    obj_none, reason = TBL.select_reference_target_object(
        TBL.reference_target_table_objects(_CHAPTER_HEADING, 0))
    check(obj_none is None and reason == "no_candidate",
          f"§三 P1-2：无候选的理由码为 no_candidate（实得 {reason!r}）"
          "—— 伪 target_not_obtained 不得由散文资格误判产生")

    # 结构充分性 = 独立物理行 + 列结构（同一行不得被算两次证据）。
    ev_one = TBL.structural_evidence(["公司严格按照《公司法》的要求规范运作。"])
    check(ev_one["independent_rows"] <= 1,
          f"§三 P1-2：单物理行只贡献一次行级证据（实得 {ev_one['independent_rows']}）")
    check(not TBL.generic_structure_sufficient(
              {"strong_signal": False, "independent_rows": 1, "max_columns": 3}),
          "§三 P1-2：单行多列不足以证明结构充分（旧实现的摊平计数会误判为充分）")
    check(TBL.generic_structure_sufficient(
              {"strong_signal": False, "independent_rows": 2, "max_columns": 3}),
          "§三 P1-2：≥2 独立结构行 + ≥3 列 → 结构充分（通用表题）")
    check(TBL.generic_structure_sufficient({"strong_signal": True, "independent_rows": 1,
                                            "max_columns": 1}),
          "§三 P1-2：强结构信号（单位/合计/续表）→ 充分")

    check(TBL.reference_target_table_objects("表5-5只有表头的表\n  序号  名称  金额\n", 0) == [],
          "§三 P1-2：只有表头、无真实表体行 → 不是表对象")
    objs_ok = TBL.reference_target_table_objects(_NUMERIC_TEXT, 0)
    check(len(objs_ok) == 1 and objs_ok[0]["target_body_rows"] == 2
          and objs_ok[0]["target_header_decision"] == TBL.HEADER_DECISION_FIRST_DATA,
          f"§三 P1-2：真实表（表题 + 折行表头 + 两条真实表体行）仍是表对象，且折行的"
          f"表头续行不被算成表体行（实得 {len(objs_ok)} 个候选，表体行 "
          f"{objs_ok[0]['target_body_rows'] if objs_ok else 0}，裁定 "
          f"{objs_ok[0]['target_header_decision'] if objs_ok else ''}）")
    check(TBL.reference_target_table_objects(_FLAT_510_BLOCK, 0) == [],
          "§三 P1-2 诚实钉点：块内「金额 占比」单空格粘连的真实 表5-10 形状在**结构侧**"
          "无候选（表体 6 列 > 最宽表头层 4 列，资格 4′ fail-closed）—— 恢复侧口径不同"
          "（见 §四 P2 的摊平表恢复断言），两套口径的差异只朝**更严**方向")
    # 单一原语：结构侧与恢复侧共用同一 numeric-cell 口径（绝不各写一套）。
    check(SE._is_numeric_cell is TBL.is_numeric_cell
          and SE._is_numeric_token is TBL.is_numeric_token,
          "§三 P1-2：恢复侧 numeric-cell 原语就是结构侧同一函数（无第二套口径）")

    # ======================================================================
    # §三 P1-3：typed occurrence 身份不可绕过
    # ======================================================================
    text = "公司受限资产情况详见风险因素。"
    occ = _occ(text, 0)
    check(occ.reference_kind == REFERENCE_KIND_NAMED
          and text[occ.start:occ.end] == occ.marker == "详见",
          "§三 P1-3 前置：命名引用 occurrence 种类/偏移与正文一致")
    good_args = {"mode": "explicit_reference", "reference_target": occ.declared_target,
                 **occ.request_args()}
    check(ReferenceOccurrence.from_request_args(good_args) is not None,
          "§三 P1-3：完整 typed 身份 → 可重建 occurrence")
    bad_cases = {
        "缺 kind": {k: v for k, v in good_args.items() if k != "reference_kind"},
        "未知 kind": dict(good_args, reference_kind="guess"),
        "缺序号": {k: v for k, v in good_args.items() if k != "reference_occurrence_index"},
        "序号非整数": dict(good_args, reference_occurrence_index="0"),
        "偏移非法（end ≤ start）": dict(good_args, marker_start=occ.end, marker_end=occ.start),
        "偏移长度 ≠ 标记长度": dict(good_args, marker_end=occ.end + 1),
        "布尔偏移": dict(good_args, marker_start=True),
        "空标记": dict(good_args, reference_marker=""),
    }
    check(all(ReferenceOccurrence.from_request_args(a) is None for a in bad_cases.values()),
          f"§三 P1-3：缺/非法 typed 身份一律 None（实得失败项 "
          f"{[k for k, a in bad_cases.items() if ReferenceOccurrence.from_request_args(a)]}）")

    seed = {"evidence_id": "ev-seed-1", "company_id": _COMPANY, "document_id": _DOC,
            "document_version": _DOCV, "evidence_set_version": _SETV,
            "page_number": 1, "block_index": 0, "text": text}

    def _audit(step_args, *, stop="cross reference target dangling", anchor="ev-seed-1"):
        step = {"step_index": 0, "action": "inspect_bounded", "arguments": step_args,
                "stop_reason": stop}
        if anchor:
            step["anchor_evidence_id"] = anchor
        return _explicit_reference_audit(
            trace=[step], rolling={}, seed=seed, material_index=[], payload_previews=[],
            aspect_id=_ASPECT, assemblies=[], aspect_links=[], source_object_inventory={})

    a_untyped = _audit({"mode": "explicit_reference", "reference_target": occ.declared_target})
    check(a_untyped["state"] == "contradictory" and a_untyped["untyped_ref_attempt_count"] == 1,
          "§三 P1-3：legacy 请求（缺 typed kind）→ contradictory（绝不按文本推断种类）")
    a_unknown = _audit({"mode": "explicit_reference", "reference_target": occ.declared_target,
                        **dict(occ.request_args(), reference_kind="guess")})
    check(a_unknown["state"] == "contradictory" and a_unknown["untyped_ref_attempt_count"] == 1,
          "§三 P1-3：未知 kind → contradictory（未知种类不得猜分支）")
    a_named_ok = _audit(good_args)
    check(a_named_ok["state"] == "dangling" and a_named_ok["named_ref_attempt_count"] == 1
          and not a_named_ok["named_ref_problems"],
          "§三 P1-3：真实 typed 命名引用尝试 → 走命名分支且身份同源（dangling 由 stop 原因裁决）")
    a_bad_index = _audit(dict(good_args, reference_occurrence_index=7))
    check(a_bad_index["state"] == "contradictory" and a_bad_index["named_ref_problems"],
          "§三 P1-3：命名引用序号被篡改 → contradictory（绝不退回 occurrences[0]）")
    a_bad_off = _audit(dict(good_args, marker_start=0, marker_end=2))
    check(a_bad_off["state"] == "contradictory" and a_bad_off["named_ref_problems"],
          "§三 P1-3：命名引用偏移被篡改 → contradictory")
    a_bad_target = _audit(dict(good_args, reference_target="24、别的目标"))
    check(a_bad_target["state"] == "contradictory" and a_bad_target["named_ref_problems"],
          "§三 P1-3：请求自报目标 ≠ 由 occurrence 重算目标 → contradictory")
    a_kind_split = _audit({"mode": "explicit_reference", "reference_kind": REFERENCE_KIND_TABLE,
                           "reference_marker": "详见", "marker_start": occ.start,
                           "marker_end": occ.end, "reference_occurrence_index": 0,
                           "reference_target": f"详见{occ.declared_target}"})
    check(a_kind_split["table_ref_attempt_count"] == 1
          and a_kind_split["named_ref_attempt_count"] == 0
          and a_kind_split["untyped_ref_attempt_count"] == 0,
          "§三 P1-3：分支只由 kind 决定 —— kind=table 而目标是命名引用文本时仍走表分支")
    a_no_anchor = _audit(good_args, anchor="")
    check(a_no_anchor["state"] == "contradictory" and a_no_anchor["named_ref_problems"],
          "§三 P1-3：命名引用发起块身份缺失 → 身份不可复核 → contradictory")

    # ======================================================================
    # §三 P1-4：目标真的进入材料库（六层逐层反例）
    # ======================================================================
    with tempfile.TemporaryDirectory() as td:
        blocks = [(1, 0, ["主营业务分析"], "paragraph", _SAME_BLOCK)]
        db = _make_db(Path(td), blocks)
        adapter = BoundedEvidenceInspectionAdapter(db)
        target_eid = _block_id(blocks, 1, 0)
        anchor_occ = _occ(_SAME_BLOCK, 0)
        res = _read(adapter, 1, 0, anchor_occ)
        binding = _binding(res)
        check(res.status == "SUCCESS" and binding.get("target_object_id"),
              "§三 P1-4 前置：同块表引用真实解析并产出内容寻址绑定记录")
        req_args = {"mode": "explicit_reference", "company_id": _COMPANY, "document_id": _DOC,
                    "document_version": _DOCV, "evidence_set_version": _SETV,
                    "page_number": 1, "block_index": 0,
                    "reference_target": anchor_occ.request_target, **anchor_occ.request_args()}
        recomputed: dict = {}
        probs = verify_reference_binding(
            binding, anchor_text=_SAME_BLOCK, anchor_position=(1, 0),
            blocks_by_evidence={target_eid: {"text": _SAME_BLOCK, "page_number": 1,
                                             "block_index": 0}},
            adopted_evidence_ids={target_eid}, expected_identity=(_COMPANY, _DOC, _DOCV, _SETV),
            request_args=req_args, recomputed_out=recomputed)
        check(probs == [] and recomputed.get("object") is not None,
              f"§三 P1-4 前置：绑定独立复算通过并交出重算对象（实得 {probs}）")
        obj = recomputed.get("object") or {}
        payload = TBL.table_object_payload_from_binding(binding)
        asm_mat, anchor_mat = "mat-target", "mat-anchor"
        asm = ReferenceTableObjectAssembly(
            assembly_id="asm-ref-1", context_parent_id=asm_mat,
            component_material_ids=(asm_mat,), relation=REFERENCE_TABLE_OBJECT_RELATION,
            boundary_desc="p1", table_object_id=obj["target_object_id"],
            object_schema_version=binding["object_schema_version"],
            binding_version=binding["binding_version"], table_payload=payload,
            table_title=binding["target_table_title"], unit=binding["target_unit"],
            header_rows=tuple(tuple(h) for h in payload["header_rows"]),
            header_decision=binding["target_header_decision"],
            column_count=binding["target_column_count"],
            body_rows=tuple(tuple(r) for r in payload["body_rows"]),
            closure_row=tuple(payload["closure_row"]),
            body_digest=binding["target_body_digest"],
            structure_rows=binding["target_structure_rows"],
            anchor_evidence_id=binding["anchor_evidence_id"], anchor_material_id="",
            target_evidence_id=binding["target_evidence_id"],
            reference_kind=binding["reference_kind"],
            reference_marker=binding["reference_marker"],
            marker_start=binding["marker_start"], marker_end=binding["marker_end"],
            reference_occurrence_index=binding["reference_occurrence_index"],
            target_start=binding["target_start"], target_end=binding["target_end"],
            target_end_boundary=binding["target_end_boundary"],
            target_closed=binding["target_closed"],
            component_evidence_ids=(binding["target_evidence_id"],),
            dependency_fingerprint="dep-1")
        asm_dict = _assembly_to_dict(asm)
        # 投影侧身份字段名必须**恰好**是内容寻址的 ``table_object_id``（绑定记录侧对应
        # 字段名是 ``target_object_id``）：验收若按绑定侧的名字读投影，该层会恒不通过
        # （把「目标真的进入材料库」永久判死）。此处把两个名字的差别钉死。
        check(asm_dict.get("table_object_id") == asm.table_object_id == obj["target_object_id"]
              and "target_object_id" not in asm_dict,
              "§三 P1-4：投影的身份键是内容寻址的 table_object_id（绑定侧名字 "
              "target_object_id 在投影里**不存在**，验收不得按绑定侧名字读投影）")
        link_decl = reference_table_object_link(asm, _ASPECT)
        links = [{"material_id": asm_mat, "aspect_id": _ASPECT, "role": "source",
                  "reference_table_objects": [link_decl]}]
        oid = obj["target_object_id"]

        def _inventory(result="recovered_ok", assembly_id="asm-ref-1", extra=()):
            return {"recovery_results": [
                {"object_id": f"title:{binding['target_table_title']}", "result": result,
                 "matched_table": binding["target_table_title"], "issue": "",
                 "assembly_id": assembly_id, "table_object_ids": [oid]},
                *extra]}

        def _run(*, asms=None, links_=None, inv=None):
            return verify_reference_table_object(
                binding, obj, aspect_id=_ASPECT,
                assemblies=list(asms if asms is not None else [asm_dict]),
                aspect_links=list(links_ if links_ is not None else links),
                adopted_evidence_ids={target_eid}, material_ids={asm_mat, anchor_mat},
                source_object_inventory=(inv if inv is not None else _inventory()))

        check(_run() == [],
              f"§三 P1-4 正向：六层齐备（绑定/assembly/payload/component/aspect/清单）→ 零问题"
              f"（实得 {_run()}）")
        check(verify_reference_table_object(binding, None, aspect_id=_ASPECT, assemblies=[asm_dict],
                                            aspect_links=links, adopted_evidence_ids={target_eid},
                                            material_ids={asm_mat, anchor_mat},
                                            source_object_inventory=_inventory()) != [],
              "§三 P1-4 反例：目标对象不可由真实正文重算 → fail-closed")

        no_asm = _run(asms=[dict(asm_dict, relation="flattened_table_recovery")])
        check(no_asm and "未进入材料库" in no_asm[0],
              f"§三 P1-4 反例：assembly 关系不对 → 目标未进入材料库（实得 {no_asm}）")
        dup = _run(asms=[asm_dict, dict(asm_dict, assembly_id="asm-ref-2")])
        check(dup and "条 assembly" in dup[0],
              f"§三 P1-4 反例：同一对象两条投影 → 重复/错误合并（实得 {dup}）")
        tamper_payload = _run(asms=[dict(asm_dict, table_payload=dict(
            payload, body_rows=payload["body_rows"] + [["1", "X", "9"]],
        ))])
        check(any("payload" in p for p in tamper_payload),
              f"§三 P1-4 反例：持久化 payload 被改写 → fail-closed（实得 {tamper_payload}）")
        tamper_digest = _run(asms=[dict(asm_dict, body_digest="0" * 64)])
        check(any("表体" in p for p in tamper_digest),
              f"§三 P1-4 反例：表体摘要被篡改 → fail-closed（实得 {tamper_digest}）")
        tamper_pos = _run(asms=[dict(asm_dict, target_start=obj["target_start"] + 1)])
        check(any("target_start" in p for p in tamper_pos),
              f"§三 P1-4 反例：目标位置被篡改 → fail-closed（实得 {tamper_pos}）")
        old_ver = _run(asms=[dict(asm_dict, object_schema_version="2")])
        check(any("schema 版本" in p for p in old_ver),
              f"§三 P1-4 反例：旧 schema 版本投影 → fail-closed（实得 {old_ver}）")
        tamper_comp = _run(asms=[dict(asm_dict, component_evidence_ids=["ev-x"])])
        check(any("component evidence" in p for p in tamper_comp),
              f"§三 P1-4 反例：component evidence 身份不符 → fail-closed（实得 {tamper_comp}）")
        dangling_mat = _run(asms=[dict(asm_dict, component_material_ids=["mat-missing"])])
        check(any("悬空" in p for p in dangling_mat),
              f"§三 P1-4 反例：component material 外键悬空 → fail-closed（实得 {dangling_mat}）")
        no_fp = _run(asms=[dict(asm_dict, dependency_fingerprint="")])
        check(any("dependency" in p for p in no_fp),
              f"§三 P1-4 反例：缺依赖指纹 → fail-closed（实得 {no_fp}）")
        wrong_owner = _run(links_=[dict(links[0], aspect_id="other.aspect")])
        check(any("缺正式 aspect 绑定" in p for p in wrong_owner),
              f"§三 P1-4 反例：归属行属于别的 aspect → fail-closed（实得 {wrong_owner}）")
        bad_asm_id = _run(links_=[{"material_id": asm_mat, "aspect_id": _ASPECT, "role": "source",
                                   "reference_table_objects": [
                                       dict(link_decl, assembly_id="asm-other")]}])
        check(any("assembly_id" in p for p in bad_asm_id),
              f"§三 P1-4 反例：归属行声明的 assembly_id 不符 → fail-closed（实得 {bad_asm_id}）")
        not_obtained = _run(inv=_inventory(result="target_not_obtained"))
        check(any("recovered_ok" in p for p in not_obtained),
              f"§三 P1-4 反例：清单以非 ok 结果见证 → fail-closed（实得 {not_obtained}）")
        contradict = _run(inv=_inventory(extra=[{
            "object_id": "title:x", "result": "target_not_obtained",
            "matched_table": binding["target_table_title"], "issue": "x",
            "assembly_id": "", "table_object_ids": []}]))
        check(any("自相矛盾" in p for p in contradict),
              f"§三 P1-4 反例：清单同名条目自相矛盾 → fail-closed（实得 {contradict}）")
        bad_inv_asm = _run(inv=_inventory(assembly_id="asm-other"))
        check(any("assembly_id" in p for p in bad_inv_asm),
              f"§三 P1-4 反例：清单见证的 assembly_id 不符 → fail-closed（实得 {bad_inv_asm}）")

    # ======================================================================
    # §四 P2：表头 / 首个数据行的角色裁定（单一原语，不按「最多两行」）
    # ======================================================================
    h1, d1_, dec1 = TBL.assign_table_row_roles(
        ["项目  金额", "动力电池  100", "储能电池  200"])
    check(len(h1) == 1 and len(d1_) == 2
          and dec1 == TBL.HEADER_DECISION_FIRST_DATA,
          f"§四 P2：表头 + 两行数据 → 保留两行表体（实得 表头 {len(h1)} / 表体 {len(d1_)} / "
          f"{dec1}）")
    h2, d2_, dec2 = TBL.assign_table_row_roles(
        ["项目  2025年  2024年", "金额  占比  金额  占比", "1  2  3  4  5  6"])
    check(len(h2) == 2 and len(d2_) == 1 and dec2 == TBL.HEADER_DECISION_SUBCOLUMN,
          f"§四 P2：全标签子列层 + 下方更宽结构行 → 第二层表头（实得 表头 {len(h2)} / "
          f"{dec2}）")
    h3, _d3, dec3 = TBL.assign_table_row_roles(
        ["项目  2025年  2024年", "10.5  3.2  8.1  2.0", "1  2  3  4"])
    check(len(h3) == 1 and dec3 == TBL.HEADER_DECISION_FIRST_DATA,
          f"§四 P2 反例：子列行含数字（真实数据行）→ 绝不吞成表头（实得 {dec3}）")
    h4, d4_, dec4 = TBL.assign_table_row_roles(
        ["项目  2025年  2024年", "金额  占比  金额  占比", "1  2  3  4"])
    check(len(h4) == 1 and dec4 == TBL.HEADER_DECISION_FIRST_DATA,
          f"§四 P2 反例：下方无更宽结构行 → 不是第二层表头（实得 {dec4}）")
    check(not TBL.second_header_layer_cells(["a", "b"], ["c"], [["1", "2", "3"]]),
          "§四 P2 反例：子列行非多列 → 不是第二层表头")
    h5, d5_, dec5 = TBL.assign_table_row_roles(["动力电池", "储能电池  200"])
    check(h5 == [] and d5_ == ["动力电池", "储能电池  200"]
          and dec5 == TBL.HEADER_DECISION_NONE,
          f"§四 P2：首行非多列 → 无物理表头、该行仍留在表体（实得 表头 {h5} / "
          f"{dec5}）")

    # 真实 表5-10 形状：恢复侧（粘连金额二次拆列）必须保住 2 行表体 + 7 列表头。
    flat = ("表 5-10发行人主营业务收入构成表\n"
            "\n"
            "单位：万元，%\n"
            "项目  2025年  2024年  2023年\n"
            "金额  占比  金额  占比  金额  占比\n"
            "\n"
            "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n"
            "\n"
            "储能电池系统  6,243,982.0  14.7  5,729,046.0  15.8  5,990,052.2  14.9\n"
            "\n"
            "合计  42,370,183.3100.0  36,201,255.3 100.0  40,091,704.5 100.0\n")
    rec = next((t for t in SE.recover_flattened_tables([flat])
                if t.get("headers") and t.get("rows")), None)
    check(rec is not None and len(rec["headers"]) == 7 and len(rec["rows"]) == 2
          and bool(rec.get("total_row")),
          f"§四 P2：真实摊平表 表5-10 → 7 列表头 + 2 行表体 + 1 行合计（实得 "
          f"{len((rec or {}).get('headers') or ())} 列 / "
          f"{len((rec or {}).get('rows') or ())} 行 / 合计="
          f"{(rec or {}).get('total_row')!r}）—— 子列层未被吞、首个数据行未被吞")

    # 同一真实形状的两侧口径差异（诚实钉点，**不得**被读成「结构侧也能过」）：
    # 结构侧（引用表对象资格）对同一份粘连形状无候选，恢复侧（摊平表恢复）恢复完整。
    # 差异方向只可能是「结构侧更严」（fail-closed），绝不允许反过来。
    check(TBL.reference_target_table_objects(_FLAT_510_BLOCK, 0) == []
          and rec is not None and len(rec["rows"]) == 2,
          "§四 P2：同一粘连形状 —— 结构侧无候选（更严）/ 恢复侧 2 行表体（完整），"
          "差异方向单向（不得出现「结构侧放行、恢复侧恢复不出」的反向）")

    return {"module": "test_r2_v14_closure", "passed": passed, "failed": failed,
            "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
