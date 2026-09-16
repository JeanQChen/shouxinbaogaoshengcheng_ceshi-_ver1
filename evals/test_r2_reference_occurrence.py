"""Eval: R2 v13 三个生产 P1 的聚焦反例（occurrence 身份 / 表对象资格 / 对象身份绑定表体）。

用法: python -m evals.test_r2_reference_occurrence

修复前的真实失败现场（``_probe_v13_before.py`` 逐条留证）：

* P1-1：``_detect_reference_targets`` 对同一处标记同时匹配「如下表」与它的子串「下表」→
  同一次引用产生**两条请求**；请求又不携带 occurrence 身份，``evidence_reader`` 恒取
  ``occurrences[0]`` → 同一块内的**第二个**标记永远解析到**第一个**标记的表
  （实测：两个「如下表」分别 @6 与 @62，两次请求的 binding marker_start 都是 6）。
* P1-2：``reference_target_table_objects`` 把折行散文/治理文字判为表对象候选
  （实测：body_rows=0、structure_rows=0 的「公司严格按照《公司法》…」仍被返回）。
* P1-3：``table_object_id`` 的 canonical payload 只有表题/单位/表头/行数，**没有表体内容**
  → 表体企业名、金额、比例、期间变化都不改变对象身份。

本模块按任务书 §五 逐条钉死（13 条 + 版本兼容），全部纯结构、公司/页码/表号/evidence_id
无关；零 LLM / 零网络 / 零 DB 写入（临时 evidence.db 只读 + 临时 run 目录）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.context_expansion import (  # noqa: E402
    detect_reference_occurrences,
    detect_reference_targets,
)
from harness.evidence_reader import (  # noqa: E402
    BoundedEvidenceInspectionAdapter,
    REFERENCE_KIND_NAMED,
    ReferenceOccurrence,
)
from harness.table_structure import (  # noqa: E402
    REFERENCE_TARGET_BINDING_VERSION,
    REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
    reference_target_table_objects,
    select_reference_target_object,
)
from harness.six_category_acceptance import verify_reference_binding  # noqa: E402

from evals.test_r2_reference_binding import (  # noqa: E402
    _AMBIGUOUS_NAMED_BLOCKS,
    _NAMED_REF_BLOCKS,
    _NO_SAME_BLOCK,
    _TWO_CANDIDATES,
    _binding,
    _block_id,
    _make_db,
    _read,
)

_COMPANY = "100001"
_DOC = "doc_ref"
_DOCV = "v1"
_SETV = "set1"

# §五.1：真实语料形状（同块内同一位置的重叠标记「如下表」⊃「下表」）。
_REAL_SHAPE = "截至 2025年12月末，发行人主要参股及联营、合营企业情况如下表："
# §五.2/3：同块内**两个非重叠**的「如下表」，各自后随自己的表。
_TWO_MARKERS = (
    "参股企业情况如下表：\n"
    "\n"
    "表5-5参股企业情况\n"
    "  序号  企业名称  持股比例\n"
    "  1  A公司  24.9%\n"
    "\n"
    "各板块构成如下表：\n"
    "\n"
    "表5-6板块构成表\n"
    "  序号  板块  金额\n"
    "  1  电池  1000\n"
)
# §五.5：折行散文 / 多空格法规文本（真实语料里被误判为表对象的那种形状）。
_PROSE_WRAP = (
    "（一）公司治理结构\n"
    "公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业\n"
    "\n"
    "板股票上市规则》《深圳证券交易所上市公司自律监管指引第  2号——创业板上市公司\n"
    "规范运作》等法律、法规及规范性文件的要求，不断完善公司法人治理机构。\n"
)
# §五.5：章节标题 + 治理描述（多空格外观）。
_HEADING_AND_PROSE = (
    "六、公司治理及内控制度\n"
    "\n"
    "（一）公司治理结构\n"
    "公司严格按照《公司法》《证券法》的要求规范运作，不断完善公司法人治理结构。\n"
)
# §五.6：有表题、有表头但**没有表体数据行** → 非表对象。
_TITLE_HEADER_NO_BODY = "表5-7参股企业情况\n  序号  企业名称  持股比例\n"
# §五.8/9/10：表体内容变化 / 仅空白排版差异。
_TABLE_TMPL = ("表5-5参股企业情况\n"
               "  序号  企业名称  持股比例\n"
               "  1  {name}  {pct}\n")


def _only_object(text: str) -> dict | None:
    objs = reference_target_table_objects(text, 0)
    return objs[0] if len(objs) == 1 else None


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
    # §五.1：「如下表」只产生一次 occurrence，不再额外产生子串「下表」
    # ======================================================================
    occs = detect_reference_occurrences(_REAL_SHAPE)
    check(len(occs) == 1 and occs[0].marker == "如下表",
          f"§五.1：「如下表」只产生 1 个 occurrence（实得 {[o.marker for o in occs]}）")
    check(all(o.reference_kind == "table" for o in occs),
          "§五.1：结构性表引用的 reference_kind = table")
    check(detect_reference_targets(_REAL_SHAPE) == ("如下表",),
          f"§五.1：目标投影不再含子串重复项（实得 "
          f"{detect_reference_targets(_REAL_SHAPE)}）")
    check(bool(occs) and _REAL_SHAPE[occs[0].start:occs[0].end] == occs[0].marker
          and occs[0].occurrence_index == 0,
          "§五.1：occurrence 偏移可在原文上独立复算，index 自 0 起")

    two = detect_reference_occurrences(_TWO_MARKERS)
    check(len(two) == 2 and [o.occurrence_index for o in two] == [0, 1],
          f"§五.2：同块两个非重叠「如下表」→ 2 个 occurrence（实得 "
          f"{[(o.marker, o.start, o.occurrence_index) for o in two]}）")
    check(len(two) == 2 and two[0].end <= two[1].start
          and all(_TWO_MARKERS[o.start:o.end] == o.marker for o in two),
          "§五.2：occurrence 互不重叠且切片等于标记本身")
    check(any(o.reference_kind == REFERENCE_KIND_NAMED
              for o in detect_reference_occurrences("详见 24、所有权或使用权受到限制的资产")),
          "§五.13：命名引用也走同一 typed occurrence（kind=named）")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # ==================================================================
        # §五.2/§五.3：每个 occurrence 各自解析**自己的**目标；请求指向第二个
        #              marker 时 reader 不得解析第一个。
        # ==================================================================
        blocks = [(1, 0, ["参股企业"], "paragraph", _TWO_MARKERS)]
        db = _make_db(base / "occ", blocks)
        ad = BoundedEvidenceInspectionAdapter(db)
        anchor_eid = _block_id(blocks, 1, 0)
        r0 = _read(ad, 1, 0, occ=two[0])
        b0 = _binding(r0)
        r1 = _read(ad, 1, 0, occ=two[1])
        b1 = _binding(r1)
        check(r0.status == "SUCCESS" and b0.get("resolution_scope") == "same_block"
              and "表5-5" in str(b0.get("target_table_title")),
              f"§五.2：occurrence#0 解析到自己的目标（实得 {r0.status}/"
              f"{b0.get('target_table_title')!r}）")
        check(r1.status == "SUCCESS" and "表5-6" in str(b1.get("target_table_title")),
              f"§五.3：occurrence#1 解析到**第二个**目标而非第一个（实得 "
              f"{r1.status}/{b1.get('target_table_title')!r}）")
        check(int(b1.get("marker_start", -1)) == two[1].start
              and int(b1.get("reference_occurrence_index", -1)) == 1,
              f"§五.3：binding 的 marker_start/index 与请求的 occurrence 同身份"
              f"（实得 start={b1.get('marker_start')} "
              f"index={b1.get('reference_occurrence_index')}）")
        check(b0.get("target_object_id") and b1.get("target_object_id")
              and b0["target_object_id"] != b1["target_object_id"],
              "§五.2：两个 marker 各自的目标对象身份互不相同")
        check(b1.get("anchor_evidence_id") == anchor_eid
              and b1.get("target_evidence_id") == anchor_eid,
              "§五.2：同块绑定仍满足 target_evidence_id == 发起块")

        # 请求内部不一致（偏移取 #0、index 取 #1）→ fail-closed
        r_bad = _read(ad, 1, 0, reference_target="如下表", reference_kind="table",
                      reference_marker="如下表", marker_start=two[0].start,
                      marker_end=two[0].end, reference_occurrence_index=1)
        check(r_bad.status == "EMPTY"
              and "occurrence" in (r_bad.message or ""),
              f"§五.4：请求 occurrence 身份内部不一致 → fail-closed（实得 "
              f"{r_bad.status}/{r_bad.message!r}）")
        # 请求不带 occurrence 身份 → fail-closed（不得退回 occurrences[0]）
        r_legacy = _read(ad, 1, 0, reference_target="如下表")
        check(r_legacy.status == "EMPTY",
              f"§五.4：无 occurrence 身份的请求不得按 occurrences[0] 解析（实得 "
              f"{r_legacy.status}/{r_legacy.message!r}）")

        # ==================================================================
        # §五.4：request / binding 的 marker/start/end/index 任一篡改即拒绝
        # ==================================================================
        anchor_text = _TWO_MARKERS
        blocks_by_evidence = {anchor_eid: {"text": anchor_text, "page_number": 1,
                                           "block_index": 0}}
        identity = (_COMPANY, _DOC, _DOCV, _SETV)
        good_binding = dict(b1)
        good_request = two[1].request_args()
        problems = verify_reference_binding(
            good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids={anchor_eid}, expected_identity=identity,
            request_args=good_request)
        check(problems == [],
              f"§五.4：正确绑定 + 正确请求 occurrence 独立复核通过（实得 {problems}）")

        for label, mutate in (
            ("request reference_marker", lambda d: d.update(reference_marker="下表")),
            ("request marker_start", lambda d: d.update(marker_start=two[1].start + 1)),
            ("request marker_end", lambda d: d.update(marker_end=two[1].end + 1)),
            ("request occurrence_index", lambda d: d.update(reference_occurrence_index=0)),
            ("request reference_kind", lambda d: d.update(reference_kind="named")),
        ):
            req = dict(good_request)
            mutate(req)
            probs = verify_reference_binding(
                good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
                blocks_by_evidence=blocks_by_evidence,
                adopted_evidence_ids={anchor_eid}, expected_identity=identity,
                request_args=req)
            check(bool(probs), f"§五.4：{label} 被篡改 → 验收 fail-closed（实得 {probs}）")

        probs_no_req = verify_reference_binding(
            good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids={anchor_eid}, expected_identity=identity,
            request_args=None)
        check(bool(probs_no_req),
              f"§五.4：请求未携带 occurrence 身份 → fail-closed（实得 {probs_no_req}）")

        for label, mutate in (
            ("binding reference_marker", lambda d: d.update(reference_marker="见下表")),
            ("binding marker_start", lambda d: d.update(marker_start=two[0].start)),
            ("binding marker_end", lambda d: d.update(marker_end=two[0].end)),
            ("binding occurrence_index", lambda d: d.update(reference_occurrence_index=0)),
        ):
            bad = dict(good_binding)
            mutate(bad)
            probs = verify_reference_binding(
                bad, anchor_text=anchor_text, anchor_position=(1, 0),
                blocks_by_evidence=blocks_by_evidence,
                adopted_evidence_ids={anchor_eid}, expected_identity=identity,
                request_args=good_request)
            check(bool(probs),
                  f"§五.4：binding {label} 被篡改 → 验收 fail-closed（实得 {probs}）")

        # ==================================================================
        # §五.5/§五.6：散文/章节标题/多空格文本不得成为表对象；无表体行的对象拒绝
        # ==================================================================
        check(reference_target_table_objects(_PROSE_WRAP, 0) == [],
              "§五.5：折行散文 + 多空格法规文本不产生任何表对象候选")
        check(reference_target_table_objects(_HEADING_AND_PROSE, 0) == [],
              "§五.5：章节标题与治理描述不产生表对象候选")
        check(reference_target_table_objects(_TITLE_HEADER_NO_BODY, 0) == [],
              "§五.6：有表题/表头但无表体数据行的候选被拒绝")
        ok_obj = _only_object(_TABLE_TMPL.format(name="A公司", pct="24.9%"))
        check(ok_obj is not None and ok_obj["target_body_rows"] >= 1,
              f"§五.5：真实表（表题 + 表头 + 表体行）仍然是表对象（实得 {ok_obj}）")
        check(ok_obj is not None and bool(ok_obj.get("target_body_row_texts"))
              and bool(ok_obj.get("target_end_boundary")),
              "§五.5：表对象带可复核的表体行内容与终止边界记录")

        # ==================================================================
        # §五.7：多候选无法唯一绑定 → ambiguous / fail-closed（不放宽既有后续块门）
        # ==================================================================
        blocks7 = [(1, 0, ["参股企业"], "paragraph", _NO_SAME_BLOCK),
                   (2, 0, ["参股企业"], "paragraph", _TWO_CANDIDATES)]
        db7 = _make_db(base / "amb", blocks7)
        ad7 = BoundedEvidenceInspectionAdapter(db7)
        occ7 = detect_reference_occurrences(_NO_SAME_BLOCK)[0]
        r7 = _read(ad7, 1, 0, occ=occ7)
        check(r7.status == "EMPTY",
              f"§五.7：后续块内多个等价候选 → fail-closed（实得 {r7.status}）")
        # 并列最近候选（同一 start）→ 选中器必须判 ambiguous，不任意选
        sel, reason = select_reference_target_object(
            [{"target_start": 100, "target_object_id": "a"},
             {"target_start": 100, "target_object_id": "b"}])
        check(sel is None and reason == "ambiguous_tie",
              f"§五.7：最近候选并列 → ambiguous_tie（实得 {reason}/{sel}）")
        sel2, reason2 = select_reference_target_object(
            [{"target_start": 100, "target_object_id": "a"},
             {"target_start": 200, "target_object_id": "b"}])
        check(sel2 is not None and sel2["target_object_id"] == "a"
              and reason2 == "nearest_of_many",
              f"§五.7：最近目标唯一可判定 → 取最近而非任意（实得 {reason2}）")

        # ==================================================================
        # §五.8/§五.9：表体内容变化（企业名 / 比例 / 金额 / 期间）必须改变对象身份
        # ==================================================================
        base_obj = _only_object(_TABLE_TMPL.format(name="A公司", pct="24.9%"))
        name_obj = _only_object(_TABLE_TMPL.format(name="B公司", pct="24.9%"))
        pct_obj = _only_object(_TABLE_TMPL.format(name="A公司", pct="25.0%"))
        check(base_obj and name_obj
              and base_obj["target_object_id"] != name_obj["target_object_id"],
              "§五.8：表体企业名称变化 → target_object_id 变化")
        check(base_obj and pct_obj
              and base_obj["target_object_id"] != pct_obj["target_object_id"],
              "§五.9：表体比例变化 → target_object_id 变化")
        period_obj = _only_object(
            "表5-5参股企业情况\n  序号  企业名称  持股比例\n  1  A公司  24.9%\n"
            "  期间  2025年度\n")
        check(base_obj and period_obj
              and base_obj["target_object_id"] != period_obj["target_object_id"],
              "§五.9：表体新增期间/口径行 → target_object_id 变化")

        # §五.10：仅空白与排版差异不改变身份
        ws_obj = _only_object(
            "表5-5参股企业情况   \n"
            "  序号    企业名称    持股比例\n"
            "\n"
            "  1    A公司    24.9%   \n")
        check(base_obj and ws_obj
              and base_obj["target_object_id"] == ws_obj["target_object_id"],
              f"§五.10：仅空白/排版差异不改变 object_id（实得 "
              f"{base_obj and base_obj['target_object_id'][:12]} vs "
              f"{ws_obj and ws_obj['target_object_id'][:12]}）")

        # ==================================================================
        # §五.11：自报 object_id 不变但真实表体被篡改 → 验收器必须拒绝
        # ==================================================================
        real_text = _TABLE_TMPL.format(name="A公司", pct="24.9%")
        hmm_text = "情况如下表：\n" + real_text
        obj_r = _only_object(hmm_text)
        occ_r = ReferenceOccurrence(marker="如下表", start=0, end=3, occurrence_index=0,
                                    reference_kind="table")
        occ_t = detect_reference_occurrences(hmm_text)[0]
        binding_r = {
            "binding_version": REFERENCE_TARGET_BINDING_VERSION,
            "object_schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
            "anchor_evidence_id": anchor_eid,
            "reference_kind": "table", "reference_marker": "如下表",
            "marker_start": occ_t.start, "marker_end": occ_t.end,
            "reference_occurrence_index": 0, "marker_occurrence_count": 1,
            "resolution_scope": "same_block", "target_evidence_id": anchor_eid,
            "target_object_id": obj_r["target_object_id"],
            "target_body_digest": obj_r["target_body_digest"],
            "target_table_title": obj_r["target_table_title"],
            "target_start": obj_r["target_start"], "target_end": obj_r["target_end"],
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV,
            "reason": "same_block_first_verifiable_table_object_after_marker",
        }
        blocks_t = {anchor_eid: {"text": hmm_text, "page_number": 1, "block_index": 0}}
        probs_ok = verify_reference_binding(
            binding_r, anchor_text=hmm_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_t, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(probs_ok == [], f"§五.11：基线绑定独立复核通过（实得 {probs_ok}）")
        tampered_text = hmm_text.replace("A公司", "B公司")
        blocks_tampered = {anchor_eid: {"text": tampered_text, "page_number": 1,
                                       "block_index": 0}}
        probs_t = verify_reference_binding(
            binding_r, anchor_text=tampered_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_tampered, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(any("object_id" in p or "表体" in p for p in probs_t),
              f"§五.11：表体被篡改而自报 object_id 未变 → 验收器拒绝（实得 {probs_t}）")
        check(obj_r is not None,
              "§五.11：表体摘要字段可由真实正文独立重算（target_body_digest 存在）")

        # ==================================================================
        # §五.12：错误目标（把引用绑到后续块的另一张表 / 目标块不含该表）继续被拒绝
        # ==================================================================
        wrong_binding = dict(binding_r)
        wrong_binding.update({"target_evidence_id": _block_id(blocks, 1, 0) + "x"})
        probs_w = verify_reference_binding(
            wrong_binding, anchor_text=hmm_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_t, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(any("不是已采纳的真实材料" in p for p in probs_w),
              f"§五.12：目标块不是真实已采纳材料 → 拒绝（实得 {probs_w}）")
        # 后续块声明：同块本已有目标却跳到后续块 → 拒绝
        jump_binding = dict(binding_r)
        jump_binding.update({"resolution_scope": "subsequent_block",
                             "target_evidence_id": anchor_eid})
        probs_j = verify_reference_binding(
            jump_binding, anchor_text=hmm_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_t, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(any("跳过同块目标" in p for p in probs_j),
              f"§五.12：同块已有目标却声明后续块 → 拒绝（实得 {probs_j}）")
        _ = occ_r

        # ==================================================================
        # §四.6：旧版本（binding/object schema = 1）必须显式 fail-closed，不得静默兼容
        # ==================================================================
        old_binding = dict(binding_r)
        old_binding["binding_version"] = "1"
        probs_v = verify_reference_binding(
            old_binding, anchor_text=hmm_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_t, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(any("版本" in p for p in probs_v),
              f"§四.6：旧 binding_version 绑定记录 fail-closed（实得 {probs_v}）")
        old_schema = dict(binding_r)
        old_schema["object_schema_version"] = "1"
        probs_s = verify_reference_binding(
            old_schema, anchor_text=hmm_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_t, adopted_evidence_ids={anchor_eid},
            expected_identity=identity, request_args=occ_t.request_args())
        check(any("版本" in p for p in probs_s),
              f"§四.6：旧 object_schema_version 绑定记录 fail-closed（实得 {probs_s}）")

        # ==================================================================
        # §五.13：命名跨章节引用与既有行为不得回归
        # ==================================================================
        db13 = _make_db(base / "named", _NAMED_REF_BLOCKS)
        ad13 = BoundedEvidenceInspectionAdapter(db13)
        occ13 = detect_reference_occurrences("详见 24、所有权或使用权受到限制的资产")[0]
        r13 = _read(ad13, 1, 0, occ=occ13)
        check(r13.status == "SUCCESS"
              and r13.evidence_ids == [_block_id(_NAMED_REF_BLOCKS, 2, 0)],
              f"§五.13：命名跨章节引用仍可解析且目标唯一（实得 "
              f"{r13.status}/{r13.evidence_ids}）")
        check(not _binding(r13),
              "§五.13：命名引用不产生结构性表绑定记录（既有语义）")
        bad_named = _read(ad13, 1, 0, reference_kind="named",
                          reference_marker="详见", marker_start=occ13.start,
                          marker_end=occ13.end, reference_occurrence_index=0,
                          reference_target="详见 25、存货")
        check(bad_named.status == "EMPTY",
              f"§五.13：命名引用的声明目标与 occurrence 不一致 → fail-closed（实得 "
              f"{bad_named.status}/{bad_named.message!r}）")
        db13b = _make_db(base / "named_amb", _AMBIGUOUS_NAMED_BLOCKS)
        ad13b = BoundedEvidenceInspectionAdapter(db13b)
        r13b = _read(ad13b, 1, 0, occ=occ13)
        check(r13b.status == "EMPTY" and "歧义" in (r13b.message or ""),
              f"§五.13：命名引用同编号多匹配仍 fail-closed（实得 "
              f"{r13b.status}/{r13b.message!r}）")

        # ==================================================================
        # §二.3 补充：命名标记与表引用标记**相邻/重叠**（「详见下表」）——
        #   一处真实标记只产生**一个** occurrence，且必须是表引用（不是目标为「下表」的
        #   命名请求：那永远不可解析，还会把真正的表引用吞掉）。
        # ==================================================================
        mixed = "营业收入构成详见下表。"
        m_occs = detect_reference_occurrences(mixed)
        check(len(m_occs) == 1 and m_occs[0].reference_kind == "table"
              and m_occs[0].marker == "下表"
              and mixed[m_occs[0].start:m_occs[0].end] == "下表",
              f"§二.3：『详见下表』只产生一个表引用 occurrence（实得 "
              f"{[o.to_dict() for o in m_occs]}）")
        check(all(o.reference_kind != "named" for o in m_occs),
              "§二.3：『详见下表』不产生目标为表引用短语的命名 occurrence"
              "（否则命名请求永远不可解析）")
        check(detect_reference_targets(mixed) == ("下表",),
              f"§二.3：目标投影恰为表引用短语（实得 {detect_reference_targets(mixed)!r}）")
        named_plus_table = "详见 24、所有权或使用权受到限制的资产；构成情况详见下表。"
        np_occs = detect_reference_occurrences(named_plus_table)
        check(len(np_occs) == 2
              and [o.reference_kind for o in np_occs] == ["named", "table"]
              and np_occs[0].declared_target == "24、所有权或使用权受到限制的资产"
              and np_occs[1].marker == "下表",
              f"§五.13 补充：命名引用 + 表引用各一，互不重叠、各带身份（实得 "
              f"{[o.to_dict() for o in np_occs]}）")
        check(all(named_plus_table[o.start:o.end] == o.marker for o in np_occs)
              and np_occs[0].end <= np_occs[1].start,
              "§五.13 补充：两个 occurrence 的切片可在原文独立复算且不重叠")

        # 端到端：『详见下表』经读取侧解析到标记之后的真实表对象（不是 dangling 命名请求）
        db14 = _make_db(base / "mixed", [(1, 0, ["主营业务分析"], "paragraph", mixed),
                                        (1, 1, ["主营业务分析"], "paragraph", real_text)])
        ad14 = BoundedEvidenceInspectionAdapter(db14)
        r14 = _read(ad14, 1, 0, occ=m_occs[0])
        b14 = _binding(r14)
        check(r14.status == "SUCCESS" and b14.get("resolution_scope") == "subsequent_block"
              and "表5-5" in str(b14.get("target_table_title")),
              f"§二.3：『详见下表』解析到标记之后的真实表对象（实得 {r14.status}/"
              f"{b14.get('resolution_scope')!r}/{b14.get('target_table_title')!r}）")
        check(b14.get("reference_kind") == "table"
              and b14.get("reference_marker") == "下表"
              and b14.get("reference_occurrence_index") == 0,
              f"§二.3：绑定记录与请求 occurrence 同身份（实得 "
              f"{b14.get('reference_kind')!r}/{b14.get('reference_marker')!r}/"
              f"{b14.get('reference_occurrence_index')!r}）")

    result = {"module": "test_r2_reference_occurrence", "passed": passed,
              "failed": failed, "skipped": skipped, "details": details}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    r = main()
    sys.exit(1 if r["failed"] else 0)
