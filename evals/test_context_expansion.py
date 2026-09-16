"""Eval: 扩读编排 ContextExpansion（R2_IMPLEMENTATION_PLAN §11）。

用法: python -m evals.test_context_expansion

覆盖：经 ToolRegistry 的相邻块/续表/交叉引用、章节边界停止、连续性断裂（跨页）、
seed mismatch fail-closed、预算各轴（per_seed_cap/max_bytes）、trace 每步含 ToolCall、
trace 无 material_id、adopted 稳定序（seed 在前）。

§九：fixture 的 evidence_id/content_hash 一律经 ``evidence.ids.content_hash()`` +
``make_evidence_id()`` 计算权威身份；seed 身份复验经独立 ``resolve_seed_identity`` 工具
（正式链第 0 步）fail-closed。

全部离线：临时 SQLite 文件模拟 evidence.db + 真实 ToolRegistry，不调 LLM/网络。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids as evidence_ids
from harness.context_expansion import (
    DISPOSITION_CONTEXT_CANDIDATE,
    DISPOSITION_INSIDE_BOUNDARY,
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION,
    DISPOSITION_UNREAD_INSIDE_BOUNDARY,
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    _detect_reference_targets,
    expand,
)
from harness.evidence_reader import (
    BoundedEvidenceInspectionAdapter,
    INSPECT_EVIDENCE_BOUNDED_SPEC,
    TOOL_NAME,
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.set_enumeration import _trace_fingerprint, derive_enumeration_boundary_proof
from tools import contracts as C
from tools.registry import ToolRegistry


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"


def _eid_of(page: int, blk: int, text: str, payload=None) -> str:
    ch = evidence_ids.content_hash(text, payload)
    return evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)


def _make_db(dirpath: Path, blocks) -> Path:
    """建临时 evidence.db；blocks = [(page, block, section_path, etype, text), ...]"""
    db = dirpath / "evidence.db"
    conn = sqlite3.connect(str(db))
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
            UNIQUE (company_id, document_id, document_version, evidence_set_version, page_number, block_index)
        );
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "宁德时代", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current",
         len(blocks), "2026-01-01"))
    for row in blocks:
        page, blk, sp, etype, text = row[:5]
        payload = row[5] if len(row) > 5 else None
        ch = evidence_ids.content_hash(text, payload)
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
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None,
             "2025-12-31", "2026-04-01", None, None, ch,
             "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _add_foreign_block(db: Path, *, company_id: str = _COMPANY, document_id: str = _DOC,
                       document_version: str = _DOCV, evidence_set_version: str = _SETV,
                       page: int, block: int, section: tuple, etype: str,
                       text: str) -> str:
    """在已有临时 DB 追加一个其它身份（company/document/version/set）的块，返回 evidence_id。"""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (company_id, document_id, document_version, "年报", None, "annual_report",
             "annual", "f" * 64, 100, 20, "公司", None, "p1", "current", None, "2026-01-01"))
        conn.execute(
            "INSERT OR IGNORE INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
            (company_id, document_id, document_version, evidence_set_version,
             json.dumps({}), "current", 1, "2026-01-01"))
        ch = evidence_ids.content_hash(text, None)
        eid = evidence_ids.make_evidence_id(
            company_id, document_id, document_version, evidence_set_version, page, block, ch)
        conn.execute(
            "INSERT OR IGNORE INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "1", company_id, document_id, document_version, evidence_set_version,
             "年报", "annual_report", None, page, block, _sp(section), etype, text,
             None, "2025-12-31", "2026-04-01", None, None, ch, "b1", "2026-01-01"))
        conn.commit()
        return eid
    finally:
        conn.close()


def _seed(page: int = 5, block: int = 1, section: tuple = ("主营业务分析",),
          text: str = "公司主要从事动力电池研发与制造。",
          etype: str = "paragraph", payload=None) -> ExpansionSeed:
    ch = evidence_ids.content_hash(text, payload)
    eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, block, ch)
    return ExpansionSeed(
        evidence_id=eid, page_number=page, block_index=block,
        section_path=section, evidence_type=etype, text=text,
        content_hash=ch)


def _request(seed: ExpansionSeed, directions, budget: ExpansionBudget | None = None) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, seed=seed, directions=directions,
        budget=budget or ExpansionBudget(), dependency_fingerprint="dep-fp")


MAIN_BLOCKS = [
    (5, 0, ["主营业务分析"], "heading", "主营业务分析", None),
    (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。", None),
    (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）",
     {"table_title": "营业收入构成", "unit": "万元"}),
    (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000",
     {"table_title": "营业收入构成", "row_index": 0}),
    (6, 0, ["主营业务分析"], "paragraph", "（续）其中海外收入……", None),
    (7, 0, ["风险因素"], "heading", "风险因素", None),
]

MAIN_IDS = {(p, b): _eid_of(p, b, t, pl) for (p, b, _s, _et, t, pl) in MAIN_BLOCKS}


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

    def build_registry(td) -> tuple[ToolRegistry, Path]:
        db = _make_db(Path(td), MAIN_BLOCKS)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        return registry, db

    # ------------------------------------------------------------------
    # 1. seed mismatch → fail-closed（不读任何扩读）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()  # 存在，但用错误 content_hash 复验
        seed = ExpansionSeed(evidence_id=seed.evidence_id, page_number=seed.page_number,
                             block_index=seed.block_index, section_path=seed.section_path,
                             evidence_type=seed.evidence_type, text=seed.text,
                             content_hash="WRONG-HASH")
        req = _request(seed, ("adjacent_blocks",))
        bad = expand(req, registry, run_id="t1")
        check(bad.adopted == (), "seed mismatch → adopted 为空")
        check(bad.stop_reason == "authority", "seed mismatch → stop_reason=authority")
        check(bad.unread_scope.reason == "authority", "seed mismatch → unread reason=authority")
        check(len(bad.trace.steps) == 1 and bad.trace.steps[0].action == "resolve_seed",
              "seed mismatch → 仅 resolve_seed 一步，无扩读")

    # ------------------------------------------------------------------
    # 2. 相邻扩读 + 章节边界停止
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()  # content_hash 为权威 identity，正确
        req = _request(seed, ("adjacent_blocks",))
        res = expand(req, registry, run_id="t2")

        ids = [b.evidence_id for b in res.adopted]
        check(ids[0] == MAIN_IDS[(5, 1)], "adopted 首个为 seed")
        check(set(ids) >= {MAIN_IDS[(5, 0)], MAIN_IDS[(5, 2)], MAIN_IDS[(5, 3)], MAIN_IDS[(6, 0)]},
              "相邻扩读采纳同 section 前/后块")
        check(MAIN_IDS[(7, 0)] not in ids, "章节边界块不被采纳")
        check(any(d.evidence_id == MAIN_IDS[(7, 0)] for d in res.outside_boundary_sentinels),
              "章节边界块进入 outside_boundary_sentinels（证明边界已探索）")
        check(not any(c.evidence_id == MAIN_IDS[(7, 0)] for c in res.candidates_unread),
              "章节边界块不进 candidates_unread（非未读）")
        check(res.stop_reason == "unrelated section boundary",
              "章节边界 → stop_reason=unrelated section boundary")
        check(res.unread_scope.reason is None, "章节边界哨兵 → unread reason=None（非未读）")

        # trace：resolve_seed + 两次 inspect_bounded；每步含 ToolCall。
        actions = [s.action for s in res.trace.steps]
        check(actions[0] == "resolve_seed" and actions[1] == "inspect_bounded",
              "trace 首步 resolve_seed、次步 inspect_bounded")
        check(all(s.tool_call is not None for s in res.trace.steps),
              "每个 ExpansionStep 含 ToolCall")
        # trace 无 material_id；outputs 为 32-hex evidence_id（evidence.ids 权威身份）。
        check(not any("mat-" in o for s in res.trace.steps for o in s.outputs),
              "trace outputs 只含 evidence_id、无 material_id")
        check(all(o and len(o) == 32 and all(c in "0123456789abcdef" for c in o)
                  for s in res.trace.steps for o in s.outputs),
              "trace outputs 为 32-hex evidence_id（evidence.ids 权威身份）")

        # budget_consumed 已填充。
        check(res.budget_consumed.get("per_seed_cap", 0) >= 1,
              "budget_consumed.per_seed_cap 计入 seed")

    # ------------------------------------------------------------------
    # 3. per_seed_cap 预算
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        req = _request(_seed(), ("adjacent_blocks",), ExpansionBudget(per_seed_cap=2))
        res = expand(req, registry, run_id="t3")
        check(len(res.adopted) == 2, "per_seed_cap=2 → 只采纳 seed + 1 块")
        check(res.stop_reason.startswith("hard budget (per_seed_cap)"),
              "per_seed_cap 到顶 → hard budget stop_reason")
        check(res.unread_scope.budget_axis == "per_seed_cap", "budget_axis=per_seed_cap")

    # ------------------------------------------------------------------
    # 4. max_bytes 预算
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        req = _request(_seed(), ("adjacent_blocks",), ExpansionBudget(max_bytes=1))
        res = expand(req, registry, run_id="t4")
        check(len(res.adopted) == 1, "max_bytes=1 → 只采纳 seed")
        check(res.stop_reason.startswith("hard budget (max_bytes)"),
              "max_bytes 到顶 → hard budget stop_reason")

    # ------------------------------------------------------------------
    # 5. 页距预算（同 section 跨页 > adjacent_pages）→ unread，非哨兵
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        far_blocks = [
            (5, 0, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (9, 0, ["主营业务分析"], "paragraph", "（远处同 section 块，跨 4 页）"),
        ]
        db = _make_db(Path(td), far_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        req = _request(_seed(page=5, block=0), ("adjacent_blocks",))
        res = expand(req, registry, run_id="t5")
        far_id = _eid_of(9, 0, "（远处同 section 块，跨 4 页）")
        check(res.stop_reason.startswith("hard budget (adjacent_pages)"),
              "同 section 但跨页 > adjacent_pages → 页距预算 unread（非 continuity break）")
        check(res.unread_scope.budget_axis == "adjacent_pages",
              "页距预算轴 = adjacent_pages")
        check(not any(d.evidence_id == far_id for d in res.outside_boundary_sentinels),
              "跨页块不进 outside_boundary_sentinels（页距非结构边界）")
        check(any(c.evidence_id == far_id for c in res.candidates_unread),
              "跨页块进入 candidates_unread（边界内未读）")

    # ------------------------------------------------------------------
    # 6. table_continuation 方向：续页身份**由锚点块内未闭合表结构派生**
    #    （真实语料 769/769 全是 paragraph 块、structured_payload 为空 ⇒ 绝不按
    #    evidence_type/table_title 过滤）。续页 = 块首**重排本表物理表头**的块。
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as td2:
        hdr = ("项目    本期金额    上期金额", "产品    收入    收入")
        flat = "\n".join(("营业收入构成表", "单位：万元") + hdr +
                         ("动力电池系统    1,000    900",))
        cont = "\n".join(hdr + ("储能电池系统    800    700",))
        db = _make_db(Path(td), [
            (5, 1, ["主营业务分析"], "paragraph", flat),
            (5, 2, ["主营业务分析"], "paragraph", cont),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text=flat)
        res = expand(_request(seed, ("table_continuation",)), registry, run_id="t6")
        check(_eid_of(5, 2, cont) in {b.evidence_id for b in res.adopted},
              "table_continuation 采纳**重排本表物理表头**的续页块"
              "（身份由结构派生，非 evidence_type 过滤）")
        check(all(b.section_path == ("主营业务分析",) for b in res.adopted),
              "续表块在同一 section")

        # 6b. 只声明表题、块内无未闭合表结构 → 诚实 EMPTY（绝不按块类型猜续读）
        _title_only = "营业收入构成（分产品）"
        db2 = _make_db(Path(td2), [
            (5, 0, ["主营业务分析"], "paragraph", _title_only),
            (5, 1, ["主营业务分析"], "paragraph", "动力电池系统    1,000    900"),
        ])
        registry2 = ToolRegistry(audit_dir=Path(td2) / "audit")
        register_bounded_evidence_tool(registry2, db_path=db2)
        register_resolve_seed_identity_tool(registry2, db_path=db2)
        seed_only = _seed(page=5, block=0, text=_title_only)
        res2 = expand(_request(seed_only, ("table_continuation",)),
                      registry2, run_id="t6b")
        check(len(res2.adopted) == 1
              and res2.adopted[0].evidence_id == seed_only.evidence_id,
              "锚点块内无未闭合表结构 → 诚实 EMPTY，只采纳 seed 自身"
              "（绝不按 evidence_type 猜续表）")

    # ------------------------------------------------------------------
    # 7. explicit_reference 方向（seed 文本含交叉引用标记，指向锚点之后真实表结构）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        _hdr7 = ("项目    本期金额    上期金额", "产品    收入    收入")
        _table7 = "\n".join(("营业收入构成表", "单位：万元") + _hdr7 +
                            ("动力电池系统    1,000    900",))
        ref_blocks = [
            (5, 0, ["主营业务分析"], "paragraph", "营业收入构成详见下表。"),
            (5, 1, ["主营业务分析"], "paragraph", _table7),
        ]
        db = _make_db(Path(td), ref_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, text="营业收入构成详见下表。")
        req = _request(seed, ("explicit_reference",))
        res = expand(req, registry, run_id="t7")
        check(_eid_of(5, 1, _table7) in {b.evidence_id for b in res.adopted},
              "explicit_reference 真正解析到锚点之后首个真实表结构并采纳"
              "（结构派生，非 evidence_type 过滤）")
        check(res.budget_consumed.get("explicit_references", 0) >= 1,
              "explicit_references 预算轴计入")

    # ------------------------------------------------------------------
    # 7b. 命名跨章节引用：_detect_reference_targets 提取「详见 N、标题」目标（非仅标记词），
    #     经 expand → executor → find_by_section_reference 确定性解析到真实块。
    # ------------------------------------------------------------------
    check(_detect_reference_targets("详见 24、所有权或使用权受到限制的资产。") ==
          ("24、所有权或使用权受到限制的资产",),
          "修复 D：_detect_reference_targets 提取命名引用目标（非仅「详见」标记）")
    check(_detect_reference_targets("营业收入构成详见下表。") and
          "下表" in _detect_reference_targets("营业收入构成详见下表。"),
          "修复 D：_detect_reference_targets 仍识别结构性表引用标记")
    with tempfile.TemporaryDirectory() as td:
        named_blocks = [
            (5, 0, ["合并财务报表项目注释"], "paragraph",
             "说明：期末用于质押或担保的应收票据详见 24、所有权或使用权受到限制的资产。"),
            (5, 1, ["24、所有权或使用权受到限制的资产"], "heading",
             "24、所有权或使用权受到限制的资产"),
        ]
        db = _make_db(Path(td), named_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("合并财务报表项目注释",),
                     text="说明：期末用于质押或担保的应收票据详见 24、所有权或使用权受到限制的资产。")
        req = _request(seed, ("explicit_reference",))
        res = expand(req, registry, run_id="t7b")
        target_id = _eid_of(5, 1, "24、所有权或使用权受到限制的资产")
        check(target_id in {b.evidence_id for b in res.adopted},
              "修复 D：命名跨章节引用经 expand 解析到目标块（详见 24、标题）")
        check("cross reference target dangling" not in res.stop_reason,
              "修复 D：命名引用可解析时不误判 dangling")

    # ------------------------------------------------------------------
    # 8. 反例#1：adjacent_before 命中章节边界不阻塞 adjacent_after（§三.1 独立停止键）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        blocks = [
            (4, 0, ["风险因素"], "heading", "风险因素"),
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）"),
            (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000"),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t8")
        adopted = {b.evidence_id for b in res.adopted}
        check(_eid_of(5, 2, "营业收入构成（分产品）") in adopted
              and _eid_of(5, 3, "动力电池系统 1,000") in adopted,
              "反例#1：before 命中边界不阻塞 after（after 块仍被采纳）")
        check(any(d.evidence_id == _eid_of(4, 0, "风险因素") for d in res.outside_boundary_sentinels),
              "反例#1：before 边界块进入 outside_boundary_sentinels（独立记录，非中止全链）")

    # ------------------------------------------------------------------
    # 9. 反例#2：adjacent_before 工具失败不阻塞 adjacent_after
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), MAIN_BLOCKS)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_resolve_seed_identity_tool(registry, db_path=db)
        adapter = BoundedEvidenceInspectionAdapter(db_path=db)

        def _before_fails(args):
            if args.get("mode") == "adjacent_before":
                return C.ToolResult(
                    call_id="", tool_name=TOOL_NAME, tool_version="v1",
                    status="FATAL_ERROR", data={}, error_code="INTERNAL_ERROR",
                    message="simulated before failure", retryable=False, trace_id="t")
            return adapter.execute(args)

        registry.register(INSPECT_EVIDENCE_BOUNDED_SPEC, _before_fails)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t9")
        adopted = {b.evidence_id for b in res.adopted}
        check(MAIN_IDS[(5, 2)] in adopted and MAIN_IDS[(5, 3)] in adopted
              and MAIN_IDS[(6, 0)] in adopted,
              "反例#2：before 工具失败不阻塞 after（after 块仍被采纳）")
        check("tool error" in res.stop_reason,
              "反例#2：before 工具失败显式记录到 stop_reason（不伪装完成）")

    # ------------------------------------------------------------------
    # 10. 反例#3+#4：干净完成无未读 → reason=None（绝不写 error）；
    #     per_request_cap 为真实消耗计数
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        blocks = [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）"),
            (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000"),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t10")
        check(res.candidates_unread == (), "反例#3：干净完成无未读候选")
        check(res.unread_scope.reason is None,
              "反例#3：无未读干净完成 → unread.reason=None（绝不写 error）")
        check(res.budget_consumed.get("per_request_cap", 0) == len(res.adopted),
              "反例#4：per_request_cap 为真实消耗计数（== len(adopted)）")

    # ------------------------------------------------------------------
    # 11. 反例#5：跨公司 explicit_reference 不越界选他公司表（不任意正读）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [(5, 0, ["主营业务分析"], "paragraph", "营业收入构成详见下表。")])
        other_eid = _add_foreign_block(
            db, company_id="600000", page=5, block=1,
            section=("主营业务分析",), etype="table", text="营业收入构成（分产品）")
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, text="营业收入构成详见下表。")
        res = expand(_request(seed, ("explicit_reference",)), registry, run_id="t11")
        check(all(b.evidence_id != other_eid for b in res.adopted),
              "反例#5：跨公司表不被采纳（不任意正读）")
        check(res.stop_reason == "cross reference target dangling",
              "反例#5：跨公司引用不可解析 → dangling（非任意选他公司表）")

    # ------------------------------------------------------------------
    # 12. 反例#6：跨 document_version explicit_reference 不越界选他版本表
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [(5, 0, ["主营业务分析"], "paragraph", "营业收入构成详见下表。")])
        other_eid = _add_foreign_block(
            db, document_version="v2", page=5, block=1,
            section=("主营业务分析",), etype="table", text="营业收入构成（分产品）")
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, text="营业收入构成详见下表。")
        res = expand(_request(seed, ("explicit_reference",)), registry, run_id="t12")
        check(all(b.evidence_id != other_eid for b in res.adopted),
              "反例#6：跨 document_version 表不被采纳（不任意正读）")
        check(res.stop_reason == "cross reference target dangling",
              "反例#6：跨版本引用不可解析 → dangling")

    # ------------------------------------------------------------------
    # 13. §三：trace 指纹确定性（不同 run_id/call_id → 同指纹；输出变化 → 指纹变化）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()
        req = _request(seed, ("adjacent_blocks",))
        res_a = expand(req, registry, run_id="t13a")
        res_b = expand(req, registry, run_id="t13b")
        fp_a = _trace_fingerprint((res_a,))
        fp_b = _trace_fingerprint((res_b,))
        check(fp_a == fp_b,
              "§三：不同 run_id 同逻辑 → trace 指纹相同（不含随机 run_id/trace_id/call_id）")
        check(len(fp_a) == 64 and all(c in "0123456789abcdef" for c in fp_a),
              "§三：trace 指纹为 64-hex sha256")

        seed_tbl = _seed(page=5, block=2, text="营业收入构成（分产品）", etype="table",
                         payload={"table_title": "营业收入构成", "unit": "万元"})
        res_c = expand(_request(seed_tbl, ("adjacent_blocks",)), registry, run_id="t13c")
        fp_c = _trace_fingerprint((res_c,))
        check(fp_c != fp_a, "§三：输出变化 → trace 指纹变化")

    # ------------------------------------------------------------------
    # 14. §四：table_continuation 锚点块内**无未闭合表结构** → 不续读（绝不猜）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()  # paragraph，块内无表结构
        res = expand(_request(seed, ("table_continuation",)), registry, run_id="t14")
        check(len(res.adopted) == 1 and res.adopted[0].evidence_id == seed.evidence_id,
              "§四：锚点块内无未闭合表结构 → 只采纳 seed 自身"
              "（绝不按 evidence_type 猜续表）")
        check(any(t.get("mode") == "table_continuation"
                  and t.get("anchor_evidence_id") == seed.evidence_id
                  and "no open table structure" in str(t.get("stop_reason"))
                  for t in res.target_outcomes),
              "§四：诚实 EMPTY 的**真实原因**落进 trace（可复核锚点与判据）")

    # ------------------------------------------------------------------
    # 15. §四：同表（重排本表物理表头）被采纳；另一张表立即停止（不误并独立表）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        hdr = ("项目    本期金额    上期金额", "产品    收入    收入")
        other_hdr = ("项目    数量    单价", "类别    台    元")
        flat = "\n".join(("营业收入构成表", "单位：万元") + hdr +
                         ("动力电池系统    1,000    900",))
        same_table = "\n".join(hdr + ("储能电池系统    800    700",))
        other_title = "表 5-2 营业成本构成表"
        other_table = "\n".join((other_title, "单位：万元") + other_hdr +
                                ("原材料    2    400",))
        blocks = [
            (5, 1, ["主营业务分析"], "paragraph", flat),
            (5, 2, ["主营业务分析"], "paragraph", same_table),
            (5, 3, ["主营业务分析"], "paragraph", other_table),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text=flat)
        res = expand(_request(seed, ("table_continuation",)), registry, run_id="t15")
        adopted_ids = {b.evidence_id for b in res.adopted}
        check(_eid_of(5, 2, same_table) in adopted_ids,
              "§四：重排本表物理表头的续页块被采纳")
        check(_eid_of(5, 3, other_table) not in adopted_ids,
              "§四：另一张表（显式异表题 + 不同物理表头）不被采纳")

    # ------------------------------------------------------------------
    # 16. §四：同表但续页跨多页（> adjacent_pages）→ 不采纳（页距预算 unread）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        hdr = ("项目    本期金额    上期金额", "产品    收入    收入")
        flat = "\n".join(("营业收入构成表", "单位：万元") + hdr +
                         ("动力电池系统    1,000    900",))
        far_cont = "\n".join(hdr + ("海外收入    2,000    1,800",))
        blocks = [
            (5, 1, ["主营业务分析"], "paragraph", flat),
            (9, 0, ["主营业务分析"], "paragraph", far_cont),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text=flat)
        res = expand(_request(seed, ("table_continuation",)), registry, run_id="t16")
        far_id = _eid_of(9, 0, far_cont)
        check(all(b.evidence_id != far_id for b in res.adopted),
              "§四：跨多页续页块不被采纳")
        check(not any(d.evidence_id == far_id for d in res.outside_boundary_sentinels),
              "§四：跨多页续页块不进 outside_boundary_sentinels（页距非结构边界）")
        check(any(c.evidence_id == far_id for c in res.candidates_unread),
              "§四：跨多页续页块进入 candidates_unread（边界内未读）")

    # ------------------------------------------------------------------
    # 17. §三：正常真实扩读 → derive_enumeration_boundary_proof 无 violation（合法路径不破坏）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        # 干净扩读：同 section 内相邻块，无跨 section 边界 → 无未读候选。
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）"),
            (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t17")
        proof = derive_enumeration_boundary_proof(
            "company_business_main.main_business", (res,),
            document_id=_DOC, document_version=_DOCV, evidence_set_version=_SETV,
            source_boundary_identity="主营业务分析", component_material_ids=("m-a1",),
            dependency_fingerprint="0" * 64)
        check(proof.violation() is None,
              "§三：真实扩读（非空 seed/adopted/trace）→ boundary proof 无 violation")
        check(proof.seed_evidence_ids == (seed.evidence_id,),
              "§三：proof.seed_evidence_ids == 真实 seed 身份")

    # ------------------------------------------------------------------
    # 18. §三：same evidence_id 不同 content_hash → trace 指纹变化（身份绑定内容，防伪造）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t18")
        fp_a = _trace_fingerprint((res,))
        first = res.adopted[0]
        tampered_first = dataclasses.replace(first, content_hash="0" * 64)
        res2 = dataclasses.replace(res, adopted=(tampered_first,) + res.adopted[1:])
        fp_b = _trace_fingerprint((res2,))
        check(fp_a != fp_b,
              "§三：same evidence_id 不同 content_hash → trace 指纹变化（绑定内容身份）")

    # ------------------------------------------------------------------
    # 19. §三.1 修复一：rolling frontier 滚至真实结构边界（非 fixed-seed-radius one-read）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        blocks = [(5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。")]
        for i in range(2, 9):
            blocks.append((5, i, ["主营业务分析"], "paragraph", f"主营业务扩展块{i}。"))
        blocks.append((5, 9, ["风险因素"], "heading", "风险因素"))
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",),
                              ExpansionBudget(adjacent_blocks_after=10, per_seed_cap=20)),
                     registry, run_id="t19")
        inside = [d for d in res.boundary_decisions
                  if d.disposition == DISPOSITION_INSIDE_BOUNDARY]
        check(len(inside) == 7,
              "修复一：rolling 采纳全部 7 个同 section 块（非 one-read 固定半径）")
        check(any(d.evidence_id == _eid_of(5, 9, "风险因素")
                  for d in res.outside_boundary_sentinels),
              "修复一：滚动至真实结构边界（兄弟章节）→ sentinel")
        check(len(res.unread_inside_boundary) == 0,
              "修复一：块预算充足 → 无 unread（同 section 块全部采纳）")

    # ------------------------------------------------------------------
    # 20. §三.2 修复一：方向块数预算（adjacent_blocks_after）→ unread（非哨兵）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (5, 3, ["主营业务分析"], "paragraph", "储能业务快速增长。"),
            (5, 4, ["主营业务分析"], "paragraph", "材料业务稳步发展。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1)
        res = expand(_request(seed, ("adjacent_blocks",),
                              ExpansionBudget(adjacent_blocks_after=2)),
                     registry, run_id="t20")
        far_id = _eid_of(5, 4, "材料业务稳步发展。")
        check(res.stop_reason.startswith("hard budget (adjacent_blocks_after)"),
              "修复一：块预算到顶 → stop_reason=hard budget (adjacent_blocks_after)")
        check(any(d.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY
                  and d.reason_code == "budget_adjacent_blocks_after"
                  for d in res.boundary_decisions),
              "修复一：探针块 → unread_inside_boundary（budget_adjacent_blocks_after）")
        check(not any(d.evidence_id == far_id for d in res.outside_boundary_sentinels),
              "修复一：块预算耗尽不进 outside_boundary_sentinels（预算非结构边界）")

    # ------------------------------------------------------------------
    # 21. §三.3 / P1-1：混合块 → context_candidate（不再停止）；主题内部子标题继续扩读，
    #     直到真正无关的结构边界（兄弟章节 → sentinel）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 1, ["主营业务情况"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务情况"], "paragraph",
             "海外收入占比提升。（二）储能业务情况：快速增长。"),
            (5, 3, ["主营业务情况"], "paragraph", "（二）储能业务情况：产能持续扩张。"),
            (5, 4, ["风险因素"], "heading", "风险因素"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, section=("主营业务情况",),
                     text="公司主要从事动力电池研发与制造。")
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t21")
        mixed_id = _eid_of(5, 2, "海外收入占比提升。（二）储能业务情况：快速增长。")
        seg_id = _eid_of(5, 3, "（二）储能业务情况：产能持续扩张。")
        sibling_id = _eid_of(5, 4, "风险因素")
        mixed_dec = next(d for d in res.boundary_decisions if d.evidence_id == mixed_id)
        check(mixed_dec.disposition == DISPOSITION_CONTEXT_CANDIDATE
              and mixed_dec.reason_code == "mixed_block_context_candidate",
              "P1-1：混合块 → context_candidate + mixed_block_context_candidate（非边界停止）")
        check("mixed_block" in mixed_dec.structural_signals,
              "P1-1：混合块 structural_signals 含 mixed_block")
        check(any(b.evidence_id == seg_id for b in res.adopted),
              "P1-1：混合块后主题内部子标题块继续被采纳（不再提前停止）")
        check(any(d.evidence_id == sibling_id
                  and d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
                  for d in res.boundary_decisions),
              "P1-1：扩读至真正无关的兄弟章节 → sentinel（结构边界仍然生效）")
        check("boundary after mixed block" not in res.stop_reason,
              "P1-1：stop_reason 不再记录 boundary after mixed block")

    # ------------------------------------------------------------------
    # 22. §三.1 修复一：before/after 独立块预算轴（一个耗尽不阻塞另一个）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (5, 3, ["主营业务分析"], "paragraph", "储能业务快速增长。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=2, text="海外收入占比提升。")
        res = expand(_request(seed, ("adjacent_blocks",),
                              ExpansionBudget(adjacent_blocks_before=1,
                                              adjacent_blocks_after=10)),
                     registry, run_id="t22")
        adopted_texts = {b.text for b in res.adopted}
        check("储能业务快速增长。" in adopted_texts,
              "修复一：before 块预算耗尽不阻塞 after（after 后续块仍被采纳）")
        check(any(d.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY
                  and d.reason_code == "budget_adjacent_blocks_before"
                  for d in res.boundary_decisions),
              "修复一：before 独立预算轴 → unread（budget_adjacent_blocks_before）")

    # ------------------------------------------------------------------
    # 23. 修复 D：摊平表行 section_path（unreliable）不判兄弟章节边界 → 跨页续表/附注续被采纳
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (166, 0, ["合并财务报表项目注释"], "paragraph",
             "七、合并财务报表项目注释 1、货币资金 单位：千元"),
            (167, 0, ["续表  64,578,043  64,337,084"], "paragraph",
             "续表 期末余额 64,578,043"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=166, block=0, section=("合并财务报表项目注释",),
                     text="七、合并财务报表项目注释 1、货币资金 单位：千元")
        res = expand(_request(seed, ("adjacent_blocks",)), registry, run_id="t23")
        cont_id = _eid_of(167, 0, "续表 期末余额 64,578,043")
        check(any(b.evidence_id == cont_id for b in res.adopted),
              "修复 D：unreliable section_path 续表块被采纳（不误判兄弟章节边界）")
        check(not any(d.evidence_id == cont_id
                      and d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
                      for d in res.boundary_decisions),
              "修复 D：unreliable section_path 续表块不进 outside_boundary_sentinel")

    # ------------------------------------------------------------------
    # 24. 修复 A.2：向后扩读发现上一章节标题 → 撤回已暂存块（只留 boundary trace）
    #     P1-A.4：撤回前提是**结构性证明**——主题小节标题层级（3）由文档自身标题层级给出
    #     （本 seed 的 section_path 叶子与文本标题不同名，故层级由 aspect/文档版本级
    #     topic_level_hint 提供，见 heading_structure.topic_level_from_seed_set），向后遇到的
    #     （六）董事…（层级 3）是同级兄弟标题 → 结构证明越界才撤回；
    #     纯 ambiguous 且层级未证明的标题绝不撤回同 Topic 材料。
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["公司基本情况"], "heading", "（六）董事、监事、高级管理人员情况"),
            (4, 1, ["公司基本情况"], "paragraph", "高管1：张三。"),
            (4, 2, ["公司基本情况"], "paragraph", "高管2：李四。"),
            (5, 0, ["公司基本情况"], "paragraph", "（二）主营业务情况\n公司主营业务为动力电池。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="（二）主营业务情况\n公司主营业务为动力电池。")
        req = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=ExpansionBudget(), dependency_fingerprint="dep-fp",
            aspect_id="company_business_main.main_business",
            topic_level_hint=3)
        res = expand(req, registry, run_id="t24")
        exec1 = _eid_of(4, 1, "高管1：张三。")
        exec2 = _eid_of(4, 2, "高管2：李四。")
        exec_head = _eid_of(4, 0, "（六）董事、监事、高级管理人员情况")
        check(exec1 not in [b.evidence_id for b in res.adopted]
              and exec2 not in [b.evidence_id for b in res.adopted],
              "修复 A.2：向后发现上一章节 → 撤回高管内容块（不进 adopted）")
        check(any(d.evidence_id == exec1
                  and d.disposition == DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION
                  for d in res.boundary_decisions)
              and any(d.evidence_id == exec2
                      and d.disposition == DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION
                      for d in res.boundary_decisions),
              "修复 A.2：撤回块只留 boundary trace（rolled_back_previous_section）")
        check(exec_head not in [b.evidence_id for b in res.adopted],
              "修复 A.2：上一章节标题不被采纳")
        check(any(d.evidence_id == exec_head
                  and d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
                  for d in res.boundary_decisions),
              "修复 A.2：上一章节标题记为 outside_boundary_sentinel")

    # ------------------------------------------------------------------
    # 25. 修复 A.2 补齐：块内（非块首）上一章节标题 → 向后回滚（真实 v5 高管材料落点）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["公司基本情况"], "paragraph",
             "姓名 职务 任期\n曾毓群 董事长\n（二）董事及高级管理人员的主要工作经历\n"
             "1、董事会 公司现任董事会成员如下。"),
            (4, 1, ["公司基本情况"], "paragraph", "高管1：张三。"),
            (4, 2, ["公司基本情况"], "paragraph", "高管2：李四。"),
            (5, 0, ["公司基本情况"], "paragraph", "（二）主营业务情况\n公司主营业务为动力电池。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="（二）主营业务情况\n公司主营业务为动力电池。")
        req = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=ExpansionBudget(), dependency_fingerprint="dep-fp",
            aspect_id="company_business_main.main_business",
            topic_level_hint=3)
        res = expand(req, registry, run_id="t25")
        exec1 = _eid_of(4, 1, "高管1：张三。")
        exec2 = _eid_of(4, 2, "高管2：李四。")
        exec_head = _eid_of(
            4, 0, "姓名 职务 任期\n曾毓群 董事长\n"
            "（二）董事及高级管理人员的主要工作经历\n1、董事会 公司现任董事会成员如下。")
        check(exec1 not in [b.evidence_id for b in res.adopted]
              and exec2 not in [b.evidence_id for b in res.adopted],
              "修复 A.2 补齐：块内上一章节标题 → 撤回高管内容块（不进 adopted）")
        check(any(d.evidence_id == exec1
                  and d.disposition == DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION
                  for d in res.boundary_decisions)
              and any(d.evidence_id == exec2
                      and d.disposition == DISPOSITION_ROLLED_BACK_PREVIOUS_SECTION
                      for d in res.boundary_decisions),
              "修复 A.2 补齐：撤回块只留 rolled_back_previous_section trace")
        check(any(d.evidence_id == exec_head
                  and d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
                  for d in res.boundary_decisions),
              "修复 A.2 补齐：含块内上一章节标题的块记为 outside_boundary_sentinel")

    # ------------------------------------------------------------------
    # 26. P1-C.6 / 反例 #18：向后回滚边界 + 向前 adjacent_pages 预算 →
    #     unread reason=budget（因果链按方向保留，绝不因向后边界而伪标 boundary）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["公司基本情况"], "paragraph",
             "（二）董事及高级管理人员的主要工作经历\n1、董事会 公司现任董事会成员如下。"),
            (4, 1, ["公司基本情况"], "paragraph", "高管1：张三。"),
            (5, 0, ["公司基本情况"], "paragraph", "（二）主营业务情况\n公司主营业务为动力电池。"),
            (6, 0, ["公司基本情况"], "paragraph", "主营业务收入构成见下表。"),
            (8, 0, ["公司基本情况"], "paragraph", "更远处的风险因素说明。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="（二）主营业务情况\n公司主营业务为动力电池。")
        req = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=ExpansionBudget(), dependency_fingerprint="dep-fp",
            aspect_id="company_business_main.main_business",
            topic_level_hint=3)
        res = expand(req, registry, run_id="t26")
        far = _eid_of(8, 0, "更远处的风险因素说明。")
        check(any(c.evidence_id == far for c in res.candidates_unread),
              "P1-C.6：P8 块进入 candidates_unread（adjacent_pages 预算）")
        check(res.stop_reason == "previous section heading (backward rollback)",
              "P1-C.6：最终 stop_reason 为向后回滚边界（聚合 summary）")
        check(res.unread_scope.reason == "budget",
              "P1-C.6：unread reason=budget（向前 adjacent_pages 预算因果链，绝不改写为 boundary）")
        check(res.unread_scope.budget_axis == "adjacent_pages",
              "P1-C.6：unread budget_axis=adjacent_pages（保留预算轴）")
        check(res.unread_scope.stop_reason == "hard budget (adjacent_pages)",
              "P1-C.6：unread stop_reason=hard budget (adjacent_pages)（因果停止，非聚合 summary）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
