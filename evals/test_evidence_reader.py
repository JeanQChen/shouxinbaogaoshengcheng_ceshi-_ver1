"""Eval: bounded Evidence inspection ToolSpec/adapter + 只读 reader（R2_IMPLEMENTATION_PLAN §11）。

用法: python -m evals.test_evidence_reader

覆盖：缺库返回 None/空、只读（写抛 OperationalError）、不建库、有界读取（before/after/limit/
section 过滤）、current 校验、seed 身份复验（formal evidence.ids 重算）fail-closed、
ToolSpec 注册进 ToolRegistry、参数校验（未知字段/缺必需字段拒绝）。

§九 反例回归：所有 seed 身份复验/current 校验逐轴 fail-closed，fixture 一律用
``evidence.ids.content_hash()`` + ``make_evidence_id()`` 计算权威身份（不再使用
``ev-5-1`` / ``hash-5-1`` / ``sha(text)`` 作为权威身份）。

全部离线：临时 SQLite 文件模拟 evidence.db，不调真实 LLM/网络，不 touch evidence/store.py。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids as evidence_ids
from harness._readonly_sqlite import open_readonly_conn
from harness.evidence_reader import (
    BoundedEvidenceInspectionAdapter,
    EvidenceReadResult,
    INSPECT_EVIDENCE_BOUNDED_SPEC,
    REFERENCE_KIND_NAMED,
    ReadonlyEvidenceReader,
    RESOLVE_SEED_IDENTITY_TOOL_NAME,
    TOOL_NAME,
    _parse_section_reference,
    iter_reference_occurrences,
    recompute_evidence_identity,
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from tools import contracts as C
from tools.registry import ToolRegistry


def _sp(seq) -> str:
    """与 evidence/store.py 一致的 section_path 紧凑 JSON 序列化。"""
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# fixture：权威身份一律经 evidence.ids 计算
# ---------------------------------------------------------------------------

_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"

# (page, block, section_path, evidence_type, text, structured_payload)
_BLOCKS = [
    (5, 0, ["主营业务分析"], "heading", "主营业务分析", None),
    (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。", None),
    (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）",
     {"table_title": "营业收入构成", "unit": "万元"}),
    (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000",
     {"table_title": "营业收入构成", "row_index": 0}),
    (5, 4, ["主营业务分析"], "table_row", "合计 1,500",
     {"table_title": "营业收入构成", "row_index": 1}),
    (6, 0, ["主营业务分析"], "paragraph", "（续）其中海外收入……", None),
    (7, 0, ["风险因素"], "heading", "风险因素", None),
    # 修复 D：命名跨章节引用 fixture（详见 N、标题）。目标块、引用发起块（seed）、同号异题块。
    (8, 0, ["所有权或使用权受到限制的资产"], "heading",
     "24、所有权或使用权受到限制的资产", None),
    (9, 0, ["货币资金"], "paragraph", "详见 24、所有权或使用权受到限制的资产", None),
    (10, 0, ["存货"], "heading", "24、存货", None),
    # v14 typed 契约 fixture：①唯一可解析的命名引用发起块（目标为无编号标题）；
    # ②悬空引用发起块（目标编号不存在）。二者都必须是**真实 occurrence**。
    (9, 1, ["货币资金"], "paragraph", "公司受限资产情况详见风险因素。", None),
    (9, 2, ["货币资金"], "paragraph", "详见 30、不存在的资产", None),
]


def _block_identity(page: int, blk: int,
                    company: str = _COMPANY, doc: str = _DOC,
                    docv: str = _DOCV, setv: str = _SETV) -> tuple[str, str]:
    """按 evidence.ids 重算某 fixture 块的 (evidence_id, content_hash)。"""
    for (p, b, _s, _et, text, payload) in _BLOCKS:
        if p == page and b == blk:
            ch = evidence_ids.content_hash(text, payload)
            eid = evidence_ids.make_evidence_id(company, doc, docv, setv, page, blk, ch)
            return eid, ch
    raise KeyError((page, blk))


def _named_req_args(page: int, block: int, **extra) -> dict:
    """按 fixture 真值重算某发起块的 **typed 命名 occurrence** 请求参数。

    v14 契约：命名引用请求必须携带 ``reference_kind == "named"`` 与
    (marker, start, end, occurrence_index) 身份，且 ``reference_target`` 必须等于该
    occurrence 从发起块正文重算出的目标文本。身份字段一律由真实正文重算（不手写常量），
    以免 fixture 与生产重算漂移。
    """
    text = next(t for (p, b, _s, _et, t, _pl) in _BLOCKS if p == page and b == block)
    occ = next(o for o in iter_reference_occurrences(text)
               if o.reference_kind == REFERENCE_KIND_NAMED)
    args = {"mode": "explicit_reference", "page_number": page, "block_index": block,
            "reference_target": occ.declared_target}
    args.update(occ.request_args())
    args.update(extra)
    return args


def _make_evidence_db(dirpath: Path, *, doc_status: str = "current",
                      set_status: str = "current") -> Path:
    """建临时 evidence.db，插入 documents/evidence_sets/evidence_blocks。

    所有 evidence_id / content_hash 均经 evidence.ids 确定性计算（§九）。
    """
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
            UNIQUE (company_id, document_id, document_version, evidence_set_version, page_number, block_index)
        );
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "宁德时代", None, "p1", doc_status, None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), set_status, len(_BLOCKS),
         "2026-01-01"))

    for (page, blk, sp, etype, text, payload) in _BLOCKS:
        eid, ch = _block_identity(page, blk)
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

    eid_5_0, ch_5_0 = _block_identity(5, 0)
    eid_5_1, ch_5_1 = _block_identity(5, 1)
    eid_5_2, ch_5_2 = _block_identity(5, 2)
    eid_5_3, ch_5_3 = _block_identity(5, 3)
    eid_5_4, ch_5_4 = _block_identity(5, 4)
    eid_6_0, _ = _block_identity(6, 0)
    eid_7_0, _ = _block_identity(7, 0)

    # ------------------------------------------------------------------
    # 1. 缺库 → None/空；不建库
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.db"
        check(open_readonly_conn(missing) is None, "缺库 open_readonly_conn 返回 None")
        reader = ReadonlyEvidenceReader(missing)
        check(reader.get_block("x") is None, "缺库 get_block 返回 None")
        check(reader.get_block_at("c", "d", "v", "s", 1, 0) is None,
              "缺库 get_block_at 返回 None")
        check(reader.bounded_blocks("c", "d", "v", "s", after=(1, 0), limit=3) == [],
              "缺库 bounded_blocks 返回 []")
        check(reader.current_document_version("c", "d") is None,
              "缺库 current_document_version 返回 None")
        check(not missing.exists(), "缺库读取后不创建文件（不建库）")

    # ------------------------------------------------------------------
    # 2. 只读 / 有界 / current
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        reader = ReadonlyEvidenceReader(db)

        blk = reader.get_block(eid_5_1)
        check(blk is not None and blk.text.startswith("公司主要从事"),
              "get_block 按 evidence_id 读到正确块")
        check(blk.section_path == ("主营业务分析",),
              "读取侧 section_path 为 tuple")
        check(blk.content_hash == ch_5_1, "content_hash == 权威 content_hash（evidence.ids）")

        at = reader.get_block_at(_COMPANY, _DOC, _DOCV, _SETV, 5, 2)
        check(at is not None and at.evidence_type == "table",
              "get_block_at 定位到 table 块")

        after = reader.bounded_blocks(_COMPANY, _DOC, _DOCV, _SETV,
                                      after=(5, 1), limit=2)
        check([b.evidence_id for b in after] == [eid_5_2, eid_5_3],
              "bounded after 按 (page,block) 正序 + limit")

        before = reader.bounded_blocks(_COMPANY, _DOC, _DOCV, _SETV,
                                       before=(5, 3), limit=2)
        check([b.evidence_id for b in before] == [eid_5_2, eid_5_1],
              "bounded before 按倒序 + limit")

        sec = reader.bounded_blocks(_COMPANY, _DOC, _DOCV, _SETV,
                                    after=(4, 0), section_path=("主营业务分析",), limit=10)
        check(all(b.section_path == ("主营业务分析",) for b in sec),
              "section_path 过滤只返回同 section 块")
        check(len(sec) == 6, "同 section 块数量正确（6 个）")

        check(reader.current_document_version(_COMPANY, _DOC) == _DOCV,
              "current_document_version 正确")
        check(reader.current_evidence_set(_COMPANY, _DOC, _DOCV) == _SETV,
              "current_evidence_set 正确")

        # 只读：写抛 OperationalError。
        conn = open_readonly_conn(db)
        try:
            conn.execute("INSERT INTO documents (company_id) VALUES ('x')")
            check(False, "只读连接写应抛 OperationalError")
        except sqlite3.OperationalError:
            check(True, "只读连接写 → OperationalError")
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 3. seed 身份复验（resolve_seed_identity / verify_seed）逐轴 fail-closed
    # ------------------------------------------------------------------
    def _verify(db: Path, **overrides):
        args = {
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "evidence_id": eid_5_1,
            "page_number": 5, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": ch_5_1,
        }
        args.update(overrides)
        return BoundedEvidenceInspectionAdapter(db).execute_resolve_seed_identity(args)

    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))

        # 正确身份 → SUCCESS。
        good = _verify(db)
        check(good.status == "SUCCESS", "seed 身份复验正确 → SUCCESS")
        check(good.data.get("is_current_document") is True, "is_current_document=True")
        check(good.data.get("is_current_set") is True, "is_current_set=True")

        # 反例#22：最小可信输入（仅 company_id + evidence_id）→ 由只读 reader 解析完整身份 → SUCCESS。
        minimal = BoundedEvidenceInspectionAdapter(db).execute_resolve_seed_identity(
            {"company_id": _COMPANY, "evidence_id": eid_5_1})
        check(minimal.status == "SUCCESS",
              "反例#22：最小输入（company_id+evidence_id）→ SUCCESS")
        check(minimal.data is not None and minimal.data.get("blocks")
              and minimal.data["blocks"][0].get("evidence_id") == eid_5_1,
              "反例#22：最小输入解析出完整身份 blocks（含 document_id/version/set）")
        check(minimal.data.get("is_current_document") is True
              and minimal.data.get("is_current_set") is True,
              "反例#22：最小输入解析后 current 校验通过")

        # verify_seed 模式仍映射到同一 fail-closed 复验（INSPECT_MODES 兼容）。
        vm = BoundedEvidenceInspectionAdapter(db).execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "verify_seed",
            "evidence_id": eid_5_1, "page_number": 5, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": ch_5_1})
        check(vm.status == "SUCCESS", "verify_seed 模式 → SUCCESS（映射到身份复验）")

        # 反例：args content_hash 与重算不一致 → fail-closed。
        bad_hash = _verify(db, content_hash="WRONG")
        check(bad_hash.status == "FATAL_ERROR" and "content_hash" in (bad_hash.message or ""),
              "反例：args content_hash 与重算不一致 → FATAL_ERROR")

        # 反例：page_number locator 不符 → fail-closed。
        bad_page = _verify(db, page_number=99)
        check(bad_page.status == "FATAL_ERROR" and "page_number" in (bad_page.message or ""),
              "反例：page_number locator 不符 → FATAL_ERROR")

        # 反例：block_index locator 不符 → fail-closed。
        bad_blk = _verify(db, block_index=99)
        check(bad_blk.status == "FATAL_ERROR" and "block_index" in (bad_blk.message or ""),
              "反例：block_index locator 不符 → FATAL_ERROR")

        # 反例：section_path 不符 → fail-closed。
        bad_sec = _verify(db, section_path=["风险因素"])
        check(bad_sec.status == "FATAL_ERROR" and "section_path" in (bad_sec.message or ""),
              "反例：section_path 不符 → FATAL_ERROR")

        # 反例：company_id 不符 → fail-closed。
        bad_co = _verify(db, company_id="OTHER")
        check(bad_co.status == "FATAL_ERROR" and "company_id" in (bad_co.message or ""),
              "反例：company_id 不符 → FATAL_ERROR")

        # 反例：document_id 不符 → fail-closed。
        bad_doc = _verify(db, document_id="other-doc")
        check(bad_doc.status == "FATAL_ERROR" and "document_id" in (bad_doc.message or ""),
              "反例：document_id 不符 → FATAL_ERROR")

        # 反例：document_version 不符 → fail-closed。
        bad_docv = _verify(db, document_version="v99")
        check(bad_docv.status == "FATAL_ERROR" and "document_version" in (bad_docv.message or ""),
              "反例：document_version 不符 → FATAL_ERROR")

        # 反例：evidence_set_version 不符 → fail-closed。
        bad_setv = _verify(db, evidence_set_version="set99")
        check(bad_setv.status == "FATAL_ERROR" and "evidence_set_version" in (bad_setv.message or ""),
              "反例：evidence_set_version 不符 → FATAL_ERROR")

        # 反例：seed 不存在 → EMPTY。
        missing_seed = _verify(db, evidence_id="nonexistent")
        check(missing_seed.status == "EMPTY",
              "反例：seed 不存在 → EMPTY")

        # 反例：缺 evidence_id → FATAL_ERROR。
        no_eid = _verify(db, evidence_id="")
        check(no_eid.status == "FATAL_ERROR",
              "反例：缺 evidence_id → FATAL_ERROR")

    # 反例：存储侧 content_hash 与重算不一致 → fail-closed。
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE evidence_blocks SET content_hash=? WHERE evidence_id=?",
                     ("0" * 64, eid_5_1))
        conn.commit()
        conn.close()
        bad_stored_ch = _verify(db)
        check(bad_stored_ch.status == "FATAL_ERROR"
              and "content_hash（重算）" in (bad_stored_ch.message or ""),
              "反例：存储 content_hash 与重算不一致 → FATAL_ERROR（content_hash（重算））")

    # 反例：存储侧 evidence_id 与重算不一致 → fail-closed。
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE evidence_blocks SET evidence_id=? WHERE evidence_id=?",
                     ("wrong-" + eid_5_1, eid_5_1))
        conn.commit()
        conn.close()
        bad_stored_eid = _verify(db, evidence_id="wrong-" + eid_5_1)
        check(bad_stored_eid.status == "FATAL_ERROR"
              and "evidence_id（重算）" in (bad_stored_eid.message or ""),
              "反例：存储 evidence_id 与重算不一致 → FATAL_ERROR（evidence_id（重算））")

    # 反例：非 current document → fail-closed。
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td), doc_status="superseded")
        stale_doc = _verify(db)
        check(stale_doc.status == "FATAL_ERROR" and "current" in (stale_doc.message or ""),
              "反例：非 current document → FATAL_ERROR（fail-closed）")

    # 反例：非 current evidence set → fail-closed。
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td), set_status="superseded")
        stale_set = _verify(db)
        check(stale_set.status == "FATAL_ERROR" and "current" in (stale_set.message or ""),
              "反例：非 current evidence set → FATAL_ERROR（fail-closed）")

    # adjacent_after / table_continuation / empty（有界读取仍受 current+身份复验保护）。
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        adapter = BoundedEvidenceInspectionAdapter(db)
        adj = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "adjacent_after",
            "page_number": 5, "block_index": 1, "limit": 3})
        check(adj.status == "SUCCESS", "adjacent_after → SUCCESS")
        check([b["evidence_id"] for b in adj.data["blocks"]] == [eid_5_2, eid_5_3, eid_5_4],
              "adjacent_after 返回有界块")

        # §二 P1-A：续表身份**由锚点块内未闭合表结构确定性派生**（不再按 evidence_type
        # 过滤、也不信调用方声明的 table_title）。本 fixture 的块文本是单行表格行/表题，
        # 不含可承接的结构（真实含结构表头的正样本由 evals.test_context_expansion 与
        # evals.test_r2_table_continuation 覆盖）。此处只断言本模块该保证的 fail-closed 面：
        # 缺锚点、锚点无结构、声明身份与锚点派生身份无从对齐——一律诚实 EMPTY，绝不猜。
        tc_no_anchor = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "table_continuation",
            "page_number": 5, "block_index": 1, "limit": 10,
            "table_title": "营业收入构成"})
        check(tc_no_anchor.status == "EMPTY"
              and "无锚点块" in (tc_no_anchor.message or ""),
              "table_continuation 无锚点块（evidence_id）→ EMPTY（身份不得按声明猜测）")
        check("blocks" not in (tc_no_anchor.data or {}),
              "EMPTY 结果不携带 blocks（缺证明不得被读成空材料）")

        # 反例（§二 P1-A）：锚点块内无未闭合表结构 → EMPTY，绝不按 evidence_type 猜续表。
        tc_no_struct = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "table_continuation",
            "page_number": 5, "block_index": 2, "limit": 10,
            "evidence_id": eid_5_2, "table_title": "营业收入构成"})
        check(tc_no_struct.status == "EMPTY"
              and "无未闭合表结构" in (tc_no_struct.message or ""),
              "table_continuation 锚点块无未闭合表结构 → EMPTY（不按 structured_payload 猜）")

        # 反例（§二 P1-A）：锚点块内有未闭合结构，但前向无承接块 → EMPTY（结构派生链止）。
        tc_chain_stop = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "table_continuation",
            "page_number": 6, "block_index": 0, "limit": 10,
            "evidence_id": eid_6_0})
        check(tc_chain_stop.status == "EMPTY"
              and "无有界读取结果" in (tc_chain_stop.message or ""),
              "table_continuation 锚点有结构但前向无承接块 → EMPTY（不伪造续表材料）")

        empty = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "adjacent_after",
            "page_number": 99, "block_index": 0, "limit": 3})
        check(empty.status == "EMPTY", "无有界读取结果 → EMPTY")

    # ------------------------------------------------------------------
    # 4. ToolRegistry 注册 + 参数校验
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        check(registry.get(TOOL_NAME) is not None, "bounded 工具已注册进 ToolRegistry")
        check(registry.get(TOOL_NAME).name == INSPECT_EVIDENCE_BOUNDED_SPEC.name,
              "注册的 spec 与常量一致")
        check(registry.get(RESOLVE_SEED_IDENTITY_TOOL_NAME) is not None,
              "resolve_seed_identity 工具已注册进 ToolRegistry")

        # 重复注册 → 拒绝。
        try:
            register_bounded_evidence_tool(registry, db_path=db)
            check(False, "重复注册应抛 ToolValidationError")
        except C.ToolValidationError:
            check(True, "重复注册 → ToolValidationError")

        # 经 ToolRegistry.execute 正式执行。
        call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME,
            arguments={"company_id": _COMPANY, "document_id": _DOC,
                       "document_version": _DOCV, "evidence_set_version": _SETV,
                       "mode": "adjacent_after", "page_number": 5, "block_index": 1,
                       "limit": 2},
            idempotency_key="k1", need_id="", batch_id="")
        res = registry.execute(call, route="DIRECT_EVIDENCE", run_id="t1")
        check(res.status == "SUCCESS", "经 ToolRegistry.execute → SUCCESS")
        check(res.tool_version == "v1", "ToolResult 被 registry 回填 tool_version")

        # 未知参数 → INVALID_ARGUMENTS。
        bad_call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME,
            arguments={"company_id": _COMPANY, "document_id": _DOC,
                       "document_version": _DOCV, "evidence_set_version": _SETV,
                       "mode": "adjacent_after", "unknown_field": 1},
            idempotency_key="k2", need_id="", batch_id="")
        res2 = registry.execute(bad_call, route="DIRECT_EVIDENCE", run_id="t1")
        check(res2.status == "FATAL_ERROR" and res2.error_code == "INVALID_ARGUMENTS",
              "未知参数 → INVALID_ARGUMENTS fail-closed")

        # 缺必需参数 → INVALID_ARGUMENTS。
        miss_call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME,
            arguments={"mode": "adjacent_after"},
            idempotency_key="k3", need_id="", batch_id="")
        res3 = registry.execute(miss_call, route="DIRECT_EVIDENCE", run_id="t1")
        check(res3.status == "FATAL_ERROR" and res3.error_code == "INVALID_ARGUMENTS",
              "缺必需参数 → INVALID_ARGUMENTS fail-closed")

    # ------------------------------------------------------------------
    # 4b. 修复 D：同文档命名跨章节引用解析（find_by_section_reference）
    # ------------------------------------------------------------------
    eid_7_0, _ = _block_identity(7, 0)
    eid_8_0, _ = _block_identity(8, 0)
    eid_10_0, _ = _block_identity(10, 0)

    # 解析：编号 + 标题。
    check(_parse_section_reference("详见 24、所有权或使用权受到限制的资产")
          == ("24", "所有权或使用权受到限制的资产"),
          "修复 D：解析「详见 N、标题」→ (编号, 标题)")
    check(_parse_section_reference("见第5节 存货") == ("5", "存货"),
          "修复 D：解析「第N节 标题」")
    check(_parse_section_reference("（三）应付账款") == ("三", "应付账款"),
          "修复 D：解析「（N）标题」")
    check(_parse_section_reference("") == (None, None), "修复 D：空引用 → (None, None)")

    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        reader = ReadonlyEvidenceReader(db)

        # 标题唯一解析：不把引用发起块（seed 正文含「详见」）误当目标。
        r_title = reader.find_by_section_reference(
            _COMPANY, _DOC, _DOCV, _SETV, "所有权或使用权受到限制的资产")
        check([b.evidence_id for b in r_title] == [eid_8_0],
              "修复 D：标题唯一解析到目标块，排除引用发起块（seed）")

        # 同号异题 → 多匹配（fail-closed 不任意选）。
        r_amb = reader.find_by_section_reference(
            _COMPANY, _DOC, _DOCV, _SETV, "详见 24、所有权或使用权受到限制的资产")
        check(len(r_amb) == 2 and eid_8_0 in [b.evidence_id for b in r_amb]
              and eid_10_0 in [b.evidence_id for b in r_amb],
              "修复 D：同号异题 → 多匹配返回（由执行器 fail-closed）")

        # 悬空引用 → 0 匹配。
        r_dangling = reader.find_by_section_reference(
            _COMPANY, _DOC, _DOCV, _SETV, "详见 30、不存在的资产")
        check(r_dangling == [], "修复 D：悬空引用 → 0 匹配")

        # 执行器级：typed 命名引用唯一解析 → SUCCESS（identity 经 evidence.ids 重算通过）。
        adapter = BoundedEvidenceInspectionAdapter(db)
        xr_ok = adapter.execute(dict(
            _named_req_args(9, 1, company_id=_COMPANY, document_id=_DOC,
                            document_version=_DOCV, evidence_set_version=_SETV)))
        check(xr_ok.status == "SUCCESS"
              and xr_ok.data["blocks"][0]["evidence_id"] == eid_7_0,
              "修复 D：typed explicit_reference 唯一命名解析 → SUCCESS 返回目标块")

        # 执行器级：同号异题 → 歧义 EMPTY（fail-closed）。
        xr_amb = adapter.execute(dict(
            _named_req_args(9, 0, company_id=_COMPANY, document_id=_DOC,
                            document_version=_DOCV, evidence_set_version=_SETV)))
        check(xr_amb.status == "EMPTY" and "歧义" in (xr_amb.message or ""),
              "修复 D：explicit_reference 多匹配 → EMPTY（歧义 fail-closed）")

        # 执行器级：悬空 → EMPTY（dangling）。
        xr_dangling = adapter.execute(dict(
            _named_req_args(9, 2, company_id=_COMPANY, document_id=_DOC,
                            document_version=_DOCV, evidence_set_version=_SETV)))
        check(xr_dangling.status == "EMPTY" and "dangling" in (xr_dangling.message or ""),
              "修复 D：explicit_reference 悬空 → EMPTY（dangling）")

        # 反例（v14 §三 P1-3）：typed 身份被篡改/缺失 → 一律 fail-closed，绝不退回
        # 「按 reference_target 文本猜种类」或「缺字段退回 occurrences[0]」。
        tampered_occ = adapter.execute(dict(
            _named_req_args(9, 1, company_id=_COMPANY, document_id=_DOC,
                            document_version=_DOCV, evidence_set_version=_SETV,
                            marker_start=3, marker_end=5)))
        check(tampered_occ.status == "EMPTY" and "occurrence" in (tampered_occ.message or ""),
              "反例：typed 身份偏移被篡改 → EMPTY（fail-closed，不按文本推断）")
        untyped = adapter.execute({
            "company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "explicit_reference",
            "reference_target": "风险因素", "page_number": 9, "block_index": 1})
        check(untyped.status == "EMPTY" and "reference_kind" in (untyped.message or ""),
              "反例：legacy 请求缺 typed reference_kind → EMPTY（fail-closed）")
        mismatched_target = adapter.execute(dict(
            _named_req_args(9, 1, company_id=_COMPANY, document_id=_DOC,
                            document_version=_DOCV, evidence_set_version=_SETV,
                            reference_target="所有权或使用权受到限制的资产")))
        check(mismatched_target.status == "EMPTY"
              and "不一致" in (mismatched_target.message or ""),
              "反例：请求自报目标 ≠ 重算目标 → EMPTY（fail-closed）")

    # ------------------------------------------------------------------
    # 5. evidence.ids 权威身份原语回归（§九）
    # ------------------------------------------------------------------
    check(evidence_ids.content_hash("a  b", None) == evidence_ids.content_hash("a b", None)
          and evidence_ids.content_hash("  a b  ", None) == evidence_ids.content_hash("a b", None),
          "content_hash 空白归一化：不同空白 → 相同 hash")
    check(evidence_ids.content_hash("x", {"a": 1}) != evidence_ids.content_hash("x", {"a": 2}),
          "content_hash 不同 structured_payload → 不同 hash")
    base_eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, 5, 1, ch_5_1)
    variants = [
        evidence_ids.make_evidence_id("other", _DOC, _DOCV, _SETV, 5, 1, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, "d2", _DOCV, _SETV, 5, 1, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, _DOC, "v2", _SETV, 5, 1, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, "s2", 5, 1, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, 6, 1, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, 5, 2, ch_5_1),
        evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, 5, 1, ch_5_0),
    ]
    check(all(v != base_eid for v in variants),
          "make_evidence_id 绑定全部 7 字段：改任一字段 → 不同 evidence_id")
    blk_5_1 = EvidenceReadResult(
        evidence_id=eid_5_1, company_id=_COMPANY, document_id=_DOC,
        document_version=_DOCV, evidence_set_version=_SETV, source_name="年报",
        source_type="annual_report", page_number=5, block_index=1,
        section_path=("主营业务分析",), evidence_type="paragraph",
        text="公司主要从事动力电池研发与制造。", structured_payload=None,
        content_hash=ch_5_1)
    r1 = recompute_evidence_identity(blk_5_1)
    r2 = recompute_evidence_identity(blk_5_1)
    check(r1 == r2 == (eid_5_1, ch_5_1),
          "recompute_evidence_identity 确定性：同块重算 == evidence.ids 权威身份")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
