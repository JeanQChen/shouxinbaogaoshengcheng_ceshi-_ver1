"""Eval: §二/§三/§五 结构性表引用（见下表/如下表/下表/续表/接上表）**确定性目标绑定**反例。

用法: python -m evals.test_r2_reference_binding

缺陷现场（真实 v11 产物
``r2_material_slice_r2_sixcat_v11_major_subsidiaries_20260916/``）：

seed ``c783f227…``（p41）同一块内「如下表」之后**紧邻**真正目标「表5-5 截至 2025 年 12 月末
发行人主要参股及联营、合营企业情况」，但生产实现**忽略同块内、标记之后的目标**，直接从 seed
**后续块**里取「第一张表」，于是错误命中 p43 的「表5-6 发行人组织结构图」；验收器又只因
「该输出属于已采纳材料」就判 ``resolved`` —— 错误正例。``_build_six_category_manifest_v11.py``
再按「优先选择解析成功的 run」重新绑定验收样本，构成**结果驱动选样**。

本模块逐条钉死通用机制（纯结构、公司/页码/表号无关）：

1. 同块「如下表」后存在唯一表对象 → ``same_block`` 正确解析；
2. 标记前有表、标记后有另一张表 → **只能**选标记后的表；
3. 同块已有目标、后续块还有另一张表 → **不得**跳到后续表；
4. 同块无目标、下一块为同章节唯一目标 → ``subsequent_block`` 正确解析；
5. 后续目标跨越明确章节边界（层级 ≤2 标题）→ dangling（不任意选）；
6. 首个候选块内存在多个等价候选 → 歧义 fail-closed；
7. 绑定记录被篡改（marker offset / target offset / target object identity / document 身份）
   → 验收 fail-closed；
8. 步骤输出是真实已采纳材料、但与引用对象不匹配 → **不得**判 ``resolved``；
9. 无引用标记 → ``not_exercised``，不得伪造尝试；
10. 既有命名引用「详见 N、标题」不得回归。

零 LLM / 零网络 / 零 DB 写入：临时 evidence.db 只读 + 临时 run 目录。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids as evidence_ids  # noqa: E402
from harness.evidence_reader import (  # noqa: E402
    BoundedEvidenceInspectionAdapter,
    iter_reference_marker_occurrences,
    iter_reference_occurrences,
)
from harness.table_structure import (  # noqa: E402
    REFERENCE_REASON_SAME_BLOCK,
    REFERENCE_TARGET_BINDING_VERSION,
    REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
    reference_target_table_object,
    table_object_body_digest,
    table_object_id,
)
from harness.six_category_acceptance import (  # noqa: E402
    CAPABILITY_NOT_TESTED,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    verify_category,
    verify_reference_binding,
)

_COMPANY = "100001"
_DOC = "doc_ref"
_DOCV = "v1"
_SETV = "set1"

# ---- §五.1/2/3 fixture：同块目标 + 标记前有表 + 后续块另有表 ----
_SAME_BLOCK = (
    "截至 2025年12月末，发行人主要参股及联营、合营企业情况如下表：\n"
    "\n"
    "表5-5主要参股及联营、合营企业情况\n"
    "  序号  企业名称  注册地  持股比例\n"
    "  1  A公司  洛阳市  24.9%\n"
)
_LATER_BLOCK = (
    "表5-9无关事项表\n"
    "  序号  项目  金额\n"
    "  1  其他  1\n"
)
_MARKER_BEFORE_AFTER = (
    "表5-1人员构成表\n"
    "  序号  项目  人数\n"
    "  1  生产  100\n"
    "\n"
    "各业务板块情况如下表：\n"
    "\n"
    "表5-2收入构成表\n"
    "  序号  项目  金额\n"
    "  1  电池  1000\n"
)
# ---- §五.4 fixture：同块无目标、下一块为唯一目标 ----
_NO_SAME_BLOCK = "主要参股及联营、合营企业情况如下表：\n"
_NEXT_BLOCK_TABLE = (
    "表5-5主要参股及联营、合营企业情况\n"
    "  序号  企业名称  注册地  持股比例\n"
    "  1  A公司  洛阳市  24.9%\n"
)
# ---- §五.5 fixture：中间跨章节边界（层级 2 标题）----
_CHAPTER_HEADING = "二、公司治理\n公司严格按照《公司法》的要求规范运作。\n"
_BEYOND_BOUNDARY_TABLE = (
    "表5-6发行人组织结构图\n"
    "  序号  名称\n"
    "  1  董事会\n"
)
# ---- §五.6 fixture：首个候选块内两个等价候选 ----
_TWO_CANDIDATES = (
    "表5-5参股企业情况\n"
    "  序号  名称  持股比例\n"
    "  1  A公司  24.9%\n"
    "\n"
    "表5-6组织结构图\n"
    "  序号  名称\n"
    "  1  董事会\n"
)
# ---- §五.10 fixture：命名跨章节引用（不得回归）----
_NAMED_REF_BLOCKS = [
    (1, 0, ["货币资金"], "paragraph", "详见 24、所有权或使用权受到限制的资产"),
    (2, 0, ["所有权或使用权受到限制的资产"], "heading", "24、所有权或使用权受到限制的资产"),
    (3, 0, ["存货"], "heading", "25、存货"),
]
# 命名引用歧义 fixture：同编号出现两处 ⇒ 生产侧必须 fail-closed（不任意选）。
_AMBIGUOUS_NAMED_BLOCKS = [
    (1, 0, ["货币资金"], "paragraph", "详见 24、所有权或使用权受到限制的资产"),
    (2, 0, ["所有权或使用权受到限制的资产"], "heading", "24、所有权或使用权受到限制的资产"),
    (3, 0, ["其它"], "heading", "24、其它事项"),
]


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


def _make_db(dirpath: Path, blocks) -> Path:
    """临时 evidence.db（与生产 store 同 schema；身份经 evidence.ids 重算）。"""
    dirpath.mkdir(parents=True, exist_ok=True)
    db = dirpath / "evidence.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE documents (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, source_name TEXT NOT NULL,
            source_path TEXT, source_type TEXT NOT NULL, material_group TEXT NOT NULL,
            file_sha256 TEXT NOT NULL, file_size INTEGER NOT NULL, page_count INTEGER,
            declared_company_name TEXT, detected_company_names TEXT,
            parser_version TEXT NOT NULL, status TEXT NOT NULL, quality_flags TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version)
        );
        CREATE TABLE evidence_sets (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            dependency_versions TEXT NOT NULL, status TEXT NOT NULL,
            block_count INTEGER, created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version, evidence_set_version)
        );
        CREATE TABLE evidence_blocks (
            evidence_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            source_name TEXT NOT NULL, source_type TEXT NOT NULL, source_uri TEXT,
            page_number INTEGER NOT NULL, block_index INTEGER NOT NULL,
            section_path TEXT, evidence_type TEXT NOT NULL, text TEXT NOT NULL,
            structured_payload TEXT, report_period TEXT, published_at TEXT,
            entities TEXT, quality_flags TEXT, content_hash TEXT NOT NULL,
            builder_version TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (company_id, document_id, document_version, evidence_set_version,
                    page_number, block_index)
        );
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "示例公司", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current", len(blocks),
         "2026-01-01"))
    for (page, blk, sp, etype, text) in blocks:
        ch = evidence_ids.content_hash(text, None)
        eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "1", _COMPANY, _DOC, _DOCV, _SETV,
             "年报", "annual_report", None, page, blk, _sp(sp), etype, text,
             None, "2025-12-31", "2026-04-01", None, None, ch, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _block_id(blocks, page, blk) -> str:
    for (p, b, _s, _et, text) in blocks:
        if p == page and b == blk:
            ch = evidence_ids.content_hash(text, None)
            return evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)
    raise KeyError((page, blk))


def _occ(text: str, index: int = 0):
    """发起块正文中第 ``index`` 个引用 occurrence（生产**同源**提取，不复制第二套规则）。"""
    return iter_reference_occurrences(text)[index]


def _read(adapter, page, blk, occ=None, **extra):
    """生产形状的 explicit_reference 调用：锚点 (page, block) + reference_target + occurrence 身份。

    生产 ``do_rolling_read`` 传 ``extra={"reference_target": occ.request_target,
    **occ.request_args()}``（§二.1/§二.4），**不**传 ``evidence_id``（那会走「具名原子块目标」
    分支，不是结构性表引用）。测试必须用同一形状，否则测的是另一条分支。
    """
    args = {"company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "explicit_reference",
            "page_number": page, "block_index": blk, "limit": 5}
    if occ is not None:
        args.update(occ.request_args())
        args["reference_target"] = occ.request_target
    args.update(extra)
    return adapter.execute(args)


def _binding(res):
    return (res.data or {}).get("reference_binding") or {}


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

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # ==================================================================
        # §五.1/2/3：同块目标唯一 → same_block；标记前的表不得作目标；
        #            同块已有目标 → 不得跳到后续块的另一张表。
        # ==================================================================
        blocks = [
            (1, 0, ["参股企业"], "paragraph", _SAME_BLOCK),
            (2, 0, ["参股企业"], "paragraph", _LATER_BLOCK),
        ]
        db = _make_db(base / "s1", blocks)
        adapter = BoundedEvidenceInspectionAdapter(db)
        anchor_eid = _block_id(blocks, 1, 0)
        res = _read(adapter, 1, 0, occ=_occ(_SAME_BLOCK))
        b = _binding(res)
        check(res.status == "SUCCESS",
              "§五.1：同块「如下表」后有唯一表对象 → 解析成功（非 dangling）")
        check(b.get("resolution_scope") == "same_block",
              f"§五.1：解析作用域 = same_block（实得 {b.get('resolution_scope')!r}）")
        check("表5-5" in str(b.get("target_table_title")),
              f"§五.1：目标表题 = 标记之后同块内的表（实得 {b.get('target_table_title')!r}）")
        check(b.get("target_evidence_id") == anchor_eid,
              "§五.1：同块目标 target_evidence_id == 发起块（同块语义）")
        check(bool(b.get("target_object_id")),
              "§五.1：目标带内容寻址的表对象身份 target_object_id")
        check(int(b.get("marker_start", -1)) >= 0
              and int(b.get("target_start", -1)) >= int(b.get("marker_start", 0)),
              "§五.1：目标位置在 marker 之后（marker_start ≤ target_start）")

        # §五.2：标记**之前**的表（表5-1）绝不作目标
        res2 = _read(adapter, 1, 0, occ=_occ(_SAME_BLOCK))
        check("表5-5" in str(_binding(res2).get("target_table_title")),
              "§五.3：同块已有目标 → 不上溯、不跳到其它表")

        blocks_mb = [(1, 0, ["业务"], "paragraph", _MARKER_BEFORE_AFTER),
                     (2, 0, ["业务"], "paragraph", _LATER_BLOCK)]
        db_mb = _make_db(base / "s2", blocks_mb)
        ad_mb = BoundedEvidenceInspectionAdapter(db_mb)
        res_mb = _read(ad_mb, 1, 0, occ=_occ(_MARKER_BEFORE_AFTER))
        t_mb = str(_binding(res_mb).get("target_table_title"))
        check("表5-2" in t_mb,
              f"§五.2：只能选标记**之后**的表（实得 {t_mb!r}）")
        check("表5-1" not in t_mb,
              "§五.2：标记之前的表绝不作目标")

        # ==================================================================
        # §五.4：同块无目标，下一块是同章节唯一目标 → subsequent_block
        # ==================================================================
        blocks4 = [
            (1, 0, ["参股企业"], "paragraph", _NO_SAME_BLOCK),
            (2, 0, ["参股企业"], "paragraph", _NEXT_BLOCK_TABLE),
        ]
        db4 = _make_db(base / "s4", blocks4)
        ad4 = BoundedEvidenceInspectionAdapter(db4)
        res4 = _read(ad4, 1, 0, occ=_occ(_NO_SAME_BLOCK))
        b4 = _binding(res4)
        check(res4.status == "SUCCESS" and b4.get("resolution_scope") == "subsequent_block",
              f"§五.4：同块无目标 → subsequent_block 正确解析"
              f"（实得 {res4.status}/{b4.get('resolution_scope')!r}）")
        check(b4.get("target_evidence_id") == _block_id(blocks4, 2, 0),
              "§五.4：目标 = 后续块中同章节的唯一表对象")
        check("表5-5" in str(b4.get("target_table_title")),
              "§五.4：目标表题正确")

        # ==================================================================
        # §五.5：后续目标跨越明确章节边界 → dangling
        # ==================================================================
        blocks5 = [
            (1, 0, ["参股企业"], "paragraph", _NO_SAME_BLOCK),
            (2, 0, ["公司治理"], "heading", _CHAPTER_HEADING),
            (3, 0, ["公司治理"], "paragraph", _BEYOND_BOUNDARY_TABLE),
        ]
        db5 = _make_db(base / "s5", blocks5)
        ad5 = BoundedEvidenceInspectionAdapter(db5)
        res5 = _read(ad5, 1, 0, occ=_occ(_NO_SAME_BLOCK))
        check(res5.status == "EMPTY",
              f"§五.5：后续目标跨越章节边界 → 如实 dangling（实得 {res5.status}）")
        check("dangling" in (res5.message or ""),
              "§五.5：dangling 理由如实记录（不任意选下一张表）")

        # ==================================================================
        # §五.6：首个候选块内多个等价候选 → 歧义 fail-closed
        # ==================================================================
        blocks6 = [
            (1, 0, ["参股企业"], "paragraph", _NO_SAME_BLOCK),
            (2, 0, ["参股企业"], "paragraph", _TWO_CANDIDATES),
        ]
        db6 = _make_db(base / "s6", blocks6)
        ad6 = BoundedEvidenceInspectionAdapter(db6)
        res6 = _read(ad6, 1, 0, occ=_occ(_NO_SAME_BLOCK))
        check(res6.status == "EMPTY",
              f"§五.6：多个等价候选 → fail-closed（实得 {res6.status}）")
        check("歧义" in (res6.message or "") or "多个" in (res6.message or ""),
              f"§五.6：理由如实为歧义（实得 {res6.message!r}）")

        # ==================================================================
        # §五.10：命名跨章节引用「详见 N、标题」不得回归
        # ==================================================================
        db10 = _make_db(base / "s10", _NAMED_REF_BLOCKS)
        ad10 = BoundedEvidenceInspectionAdapter(db10)
        res10 = _read(ad10, 1, 0, occ=_occ(_NAMED_REF_BLOCKS[0][4]))
        check(res10.status == "SUCCESS"
              and res10.evidence_ids == [_block_id(_NAMED_REF_BLOCKS, 2, 0)],
              f"§五.10：命名跨章节引用仍可解析且目标唯一（实得 "
              f"{res10.status}/{res10.evidence_ids}）")
        check(_binding(res10).get("reference_marker") is None
              or _binding(res10).get("resolution_scope") is None,
              "§五.10：命名引用不产生结构性表绑定的 same/subsequent 作用域")

        # 同编号出现两处 → 生产侧必须 fail-closed（歧义不任意选），本修复不得放宽。
        db10b = _make_db(base / "s10b", _AMBIGUOUS_NAMED_BLOCKS)
        ad10b = BoundedEvidenceInspectionAdapter(db10b)
        res10b = _read(ad10b, 1, 0, occ=_occ(_AMBIGUOUS_NAMED_BLOCKS[0][4]))
        check(res10b.status == "EMPTY" and "歧义" in (res10b.message or ""),
              f"§五.10：命名引用同编号多匹配 → 歧义 fail-closed（实得 "
              f"{res10b.status}/{res10b.message!r}）")

        # ==================================================================
        # §五.7：绑定记录被篡改 → 验收 fail-closed
        # ==================================================================
        anchor_text = _SAME_BLOCK
        occ = iter_reference_marker_occurrences(anchor_text)
        check(len(occ) == 1 and occ[0][0] == "如下表",
              f"§二.4：逐 occurrence 且同起点取最长标记（实得 {occ!r}）")
        marker, ms, me = occ[0]
        obj = reference_target_table_object(anchor_text, me)
        check(obj is not None and "表5-5" in str(obj.get("target_table_title")),
              "§二.2：标记之后的同块表对象可确定性解析")
        anchor_occ = _occ(anchor_text)
        req = anchor_occ.request_args()
        good_binding = {
            "binding_version": REFERENCE_TARGET_BINDING_VERSION,
            "object_schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
            "anchor_evidence_id": anchor_eid,
            "reference_kind": anchor_occ.reference_kind,
            "reference_marker": marker, "marker_start": ms, "marker_end": me,
            "reference_occurrence_index": anchor_occ.occurrence_index,
            "resolution_scope": "same_block", "target_evidence_id": anchor_eid,
            "target_object_id": obj["target_object_id"],
            "target_table_title": obj["target_table_title"],
            "target_start": obj["target_start"], "target_end": obj["target_end"],
            "target_body_digest": obj["target_body_digest"],
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "reason": REFERENCE_REASON_SAME_BLOCK,
            "marker_occurrence_count": len(occ),
        }
        blocks_by_evidence = {
            anchor_eid: {"text": anchor_text, "page_number": 1, "block_index": 0}}
        identity = (_COMPANY, _DOC, _DOCV, _SETV)

        problems = verify_reference_binding(
            good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids={anchor_eid}, expected_identity=identity,
            request_args=req)
        check(problems == [],
              f"§五.7：正确绑定独立复核通过（实得 {problems}）")

        for label, mutate in (
            ("marker offset", lambda d: d.update(marker_start=ms + 1)),
            ("marker 身份（换标记）", lambda d: d.update(reference_marker="下表")),
            ("occurrence 序号", lambda d: d.update(reference_occurrence_index=3)),
            ("target start offset", lambda d: d.update(target_start=obj["target_start"] + 1)),
            ("target object identity",
             lambda d: d.update(target_object_id="0" * 64)),
            ("target table title", lambda d: d.update(target_table_title="表5-9无关事项表")),
            ("target body digest", lambda d: d.update(target_body_digest="0" * 64)),
            ("绑定版本（旧版本）", lambda d: d.update(binding_version="1")),
            ("对象 schema 版本（旧版本）", lambda d: d.update(object_schema_version="1")),
            ("document identity", lambda d: d.update(document_version="v2")),
            ("resolution scope", lambda d: d.update(resolution_scope="subsequent_block")),
        ):
            tampered = dict(good_binding)
            mutate(tampered)
            probs = verify_reference_binding(
                tampered, anchor_text=anchor_text, anchor_position=(1, 0),
                blocks_by_evidence=blocks_by_evidence,
                adopted_evidence_ids={anchor_eid}, expected_identity=identity,
                request_args=req)
            check(bool(probs), f"§五.7：{label} 被篡改 → 验收 fail-closed（实得 {probs}）")

        # §五.7：请求端 occurrence 身份被篡改（绑定未变）→ fail-closed
        for label, mutate in (
            ("请求 marker_start", lambda d: d.update(marker_start=ms + 1)),
            ("请求 occurrence 序号", lambda d: d.update(reference_occurrence_index=2)),
            ("请求 reference_kind", lambda d: d.update(reference_kind="named")),
            ("请求 occurrence 身份缺失", lambda d: d.pop("marker_start")),
        ):
            bad_req = dict(req)
            mutate(bad_req)
            probs = verify_reference_binding(
                good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
                blocks_by_evidence=blocks_by_evidence,
                adopted_evidence_ids={anchor_eid}, expected_identity=identity,
                request_args=bad_req)
            check(bool(probs), f"§五.7：{label} 被篡改 → 验收 fail-closed（实得 {probs}）")

        # 目标未进已采纳材料 → fail-closed
        probs_unadopted = verify_reference_binding(
            good_binding, anchor_text=anchor_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids=set(), expected_identity=identity)
        check(bool(probs_unadopted),
              "§五.7：目标不在已采纳材料集合内 → fail-closed")

        # 缺绑定记录（解析成功但无可复核绑定）→ fail-closed
        probs_missing = verify_reference_binding(
            {}, anchor_text=anchor_text, anchor_position=(1, 0),
            blocks_by_evidence=blocks_by_evidence,
            adopted_evidence_ids={anchor_eid}, expected_identity=identity)
        check(bool(probs_missing),
              "§三：解析成功但无绑定记录 → fail-closed（绝不只凭「输出属于已采纳材料」）")

        # ==================================================================
        # §五.8：step 输出是真实材料但与引用对象不匹配 → 不得判 resolved
        # ==================================================================
        from evals.test_six_category_acceptance import (  # noqa: E402
            _canonical_json,
            _seed,
            _write_run_dir,
        )
        import evals.test_six_category_acceptance as _T6  # noqa: E402

        ASPECT = "company_subsidiaries.major_subsidiaries"
        mismatched_trace = [
            {"step_index": 0, "action": "inspect_bounded",
             "arguments": {"mode": "explicit_reference", "reference_target": "如下表"},
             "outputs": ["ev-other"], "stop_reason": "target exhausted",
             "reference_binding": {
                 "binding_version": REFERENCE_TARGET_BINDING_VERSION,
                 "object_schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
                 "anchor_evidence_id": _seed(ASPECT)["evidence_id"],
                 "reference_kind": "table",
                 "reference_marker": "如下表", "marker_start": 10, "marker_end": 13,
                 "reference_occurrence_index": 0,
                 "resolution_scope": "same_block",
                 "target_evidence_id": _seed(ASPECT)["evidence_id"],
                 "target_object_id": "0" * 64,
                 "target_table_title": "表5-5参股企业情况",
                 "target_start": 20, "target_end": 40,
                 "target_body_digest": table_object_body_digest(("1  A公司  24.9%",)),
                 "company_id": "300750", "document_id": "NDSD_KCZ_2026",
                 "document_version": _T6.DOC_VERSION,
                 "evidence_set_version": _T6.SET_VERSION,
                 "reason": "same_block_first_verifiable_table_object_after_marker",
                 "marker_occurrence_count": 1}},
        ]
        _write_run_dir(base, "xr_mismatch", aspect_id=ASPECT,
                       materials=("m1", "m2"), trace=mismatched_trace)
        v8 = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, base / "xr_mismatch")
        a8 = v8.facts["explicit_reference_audit"]
        check(a8["state"] != "resolved",
              f"§五.8：真实材料的输出与引用对象不匹配 → 不得判 resolved（实得 {a8['state']}）")
        check(a8["state"] == "contradictory",
              f"§五.8：不一致 → contradictory（fail-closed，实得 {a8['state']}）")
        check(a8["target_resolved"] is False,
              "§五.8：target_resolved 必须为假（错误正例不得通过）")

        # ==================================================================
        # §五.9：无引用标记 → not_exercised，不得伪造尝试
        # ==================================================================
        _write_run_dir(base, "xr_none", aspect_id=ASPECT, materials=("m1", "m2"),
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "arguments": {"mode": "adjacent_after", "limit": 5},
                               "outputs": [], "stop_reason": "target exhausted"}])
        v9 = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, base / "xr_none")
        a9 = v9.facts["explicit_reference_audit"]
        check(a9["state"] == "not_exercised" and a9["not_exercised"] is True,
              f"§五.9：无标记无尝试 → not_exercised（实得 {a9['state']}）")
        check(v9.capability_verdict == CAPABILITY_NOT_TESTED,
              "§五.9：能力未测 → NOT_TESTED（绝不伪造尝试/通过）")

    result = {"module": "test_r2_reference_binding", "passed": passed,
              "failed": failed, "skipped": skipped, "details": details}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    r = main()
    sys.exit(1 if r["failed"] else 0)
