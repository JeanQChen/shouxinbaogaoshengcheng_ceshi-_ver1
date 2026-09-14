"""Eval: bounded Evidence inspection ToolSpec/adapter + 只读 reader（R2_IMPLEMENTATION_PLAN §11）。

用法: python -m evals.test_evidence_reader

覆盖：缺库返回 None/空、只读（写抛 OperationalError）、不建库、有界读取（before/after/limit/
section 过滤）、current 校验、seed mismatch fail-closed、ToolSpec 注册进 ToolRegistry、
参数校验（未知字段/缺必需字段拒绝）。

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

from harness._readonly_sqlite import open_readonly_conn
from harness.evidence_reader import (
    BoundedEvidenceInspectionAdapter,
    INSPECT_EVIDENCE_BOUNDED_SPEC,
    ReadonlyEvidenceReader,
    TOOL_NAME,
    register_bounded_evidence_tool,
)
from tools import contracts as C
from tools.registry import ToolRegistry


def _sp(seq) -> str:
    """与 evidence/store.py 一致的 section_path 紧凑 JSON 序列化。"""
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


def _make_evidence_db(dirpath: Path) -> Path:
    """建临时 evidence.db，插入 documents/evidence_sets/evidence_blocks。"""
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
        ("300750", "doc1", "v1", "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "宁德时代", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        ("300750", "doc1", "v1", "set1", json.dumps({}), "current", 6, "2026-01-01"))

    blocks = [
        # (page, block, section_path, evidence_type, text, structured_payload)
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
    ]
    for i, (page, blk, sp, etype, text, payload) in enumerate(blocks):
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ev-{page}-{blk}", "1", "300750", "doc1", "v1", "set1",
             "年报", "annual_report", None, page, blk, _sp(sp), etype, text,
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None,
             "2025-12-31", "2026-04-01", None, None, f"hash-{page}-{blk}",
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

        blk = reader.get_block("ev-5-1")
        check(blk is not None and blk.text.startswith("公司主要从事"),
              "get_block 按 evidence_id 读到正确块")
        check(blk.section_path == ("主营业务分析",),
              "读取侧 section_path 为 tuple")
        check(blk.content_hash == "hash-5-1", "content_hash 正确")

        at = reader.get_block_at("300750", "doc1", "v1", "set1", 5, 2)
        check(at is not None and at.evidence_type == "table",
              "get_block_at 定位到 table 块")

        after = reader.bounded_blocks("300750", "doc1", "v1", "set1",
                                      after=(5, 1), limit=2)
        check([b.evidence_id for b in after] == ["ev-5-2", "ev-5-3"],
              "bounded after 按 (page,block) 正序 + limit")

        before = reader.bounded_blocks("300750", "doc1", "v1", "set1",
                                       before=(5, 3), limit=2)
        check([b.evidence_id for b in before] == ["ev-5-2", "ev-5-1"],
              "bounded before 按倒序 + limit")

        sec = reader.bounded_blocks("300750", "doc1", "v1", "set1",
                                    after=(4, 0), section_path=("主营业务分析",), limit=10)
        check(all(b.section_path == ("主营业务分析",) for b in sec),
              "section_path 过滤只返回同 section 块")
        check(len(sec) == 6, "同 section 块数量正确（6 个）")

        check(reader.current_document_version("300750", "doc1") == "v1",
              "current_document_version 正确")
        check(reader.current_evidence_set("300750", "doc1", "v1") == "set1",
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
    # 3. adapter executor：verify_seed / adjacent / empty
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        adapter = BoundedEvidenceInspectionAdapter(db)

        # verify_seed 正确。
        good = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "verify_seed",
            "evidence_id": "ev-5-1", "page_number": 5, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": "hash-5-1"})
        check(good.status == "SUCCESS", "verify_seed 正确 → SUCCESS")
        check(good.data.get("is_current_document") is True, "verify_seed is_current_document=True")
        check(good.data.get("is_current_set") is True, "verify_seed is_current_set=True")

        # content_hash mismatch → fail-closed。
        bad_hash = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "verify_seed",
            "evidence_id": "ev-5-1", "page_number": 5, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": "WRONG"})
        check(bad_hash.status == "FATAL_ERROR" and "content_hash" in (bad_hash.message or ""),
              "content_hash mismatch → FATAL_ERROR fail-closed")

        # locator mismatch（page 不符）→ fail-closed。
        bad_loc = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "verify_seed",
            "evidence_id": "ev-5-1", "page_number": 99, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": "hash-5-1"})
        check(bad_loc.status == "FATAL_ERROR" and "page_number" in (bad_loc.message or ""),
              "page_number locator mismatch → fail-closed")

        # 公司不符 → fail-closed。
        bad_co = adapter.execute({
            "company_id": "OTHER", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "verify_seed",
            "evidence_id": "ev-5-1", "page_number": 5, "block_index": 1,
            "section_path": ["主营业务分析"], "content_hash": "hash-5-1"})
        check(bad_co.status == "FATAL_ERROR" and "company_id" in (bad_co.message or ""),
              "company_id mismatch → fail-closed")

        # adjacent_after。
        adj = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "adjacent_after",
            "page_number": 5, "block_index": 1, "limit": 3})
        check(adj.status == "SUCCESS", "adjacent_after → SUCCESS")
        check([b["evidence_id"] for b in adj.data["blocks"]] == ["ev-5-2", "ev-5-3", "ev-5-4"],
              "adjacent_after 返回有界块")

        # table_continuation 只返回 table/table_row。
        tc = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "table_continuation",
            "page_number": 5, "block_index": 1, "limit": 10})
        etypes = {b["evidence_type"] for b in tc.data["blocks"]}
        check(etypes <= {"table", "table_row"}, "table_continuation 只返回 table/table_row")

        # 空结果 → EMPTY。
        empty = adapter.execute({
            "company_id": "300750", "document_id": "doc1", "document_version": "v1",
            "evidence_set_version": "set1", "mode": "adjacent_after",
            "page_number": 99, "block_index": 0, "limit": 3})
        check(empty.status == "EMPTY", "无有界读取结果 → EMPTY")

    # ------------------------------------------------------------------
    # 4. ToolRegistry 注册 + 参数校验
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_evidence_db(Path(td))
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        check(registry.get(TOOL_NAME) is not None, "bounded 工具已注册进 ToolRegistry")
        check(registry.get(TOOL_NAME).name == INSPECT_EVIDENCE_BOUNDED_SPEC.name,
              "注册的 spec 与常量一致")

        # 重复注册 → 拒绝。
        try:
            register_bounded_evidence_tool(registry, db_path=db)
            check(False, "重复注册应抛 ToolValidationError")
        except C.ToolValidationError:
            check(True, "重复注册 → ToolValidationError")

        # 经 ToolRegistry.execute 正式执行。
        call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME,
            arguments={"company_id": "300750", "document_id": "doc1",
                       "document_version": "v1", "evidence_set_version": "set1",
                       "mode": "adjacent_after", "page_number": 5, "block_index": 1,
                       "limit": 2},
            idempotency_key="k1", need_id="", batch_id="")
        res = registry.execute(call, route="DIRECT_EVIDENCE", run_id="t1")
        check(res.status == "SUCCESS", "经 ToolRegistry.execute → SUCCESS")
        check(res.tool_version == "v1", "ToolResult 被 registry 回填 tool_version")

        # 未知参数 → INVALID_ARGUMENTS。
        bad_call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=TOOL_NAME,
            arguments={"company_id": "300750", "document_id": "doc1",
                       "document_version": "v1", "evidence_set_version": "set1",
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

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
