"""Eval: 本地工具适配器（tools/adapters.py）—— Phase 3 Batch A。

用法: python -m evals.test_tool_adapters

断言：
- build_default_registry 注册 6 个本地工具；参数越权 → INVALID_ARGUMENTS；
  路由越权（DB 工具在非 DB 路由）→ TOOL_NOT_ALLOWED（经 Registry，不触后端）；
- lookup_company_field 限定（修订 3）：无快照/无 Record Set → DB_FIELD_UNAVAILABLE，
  不返回假阳性、不把「不存在」写成「无风险」；
- compare_evidence 纯 Python 结构化比较（修订 4）：同文档/同期间/值一致/冲突/缺失，
  不做价值判断；
- search_evidence 的 EvidencePack → ToolResult 状态翻译（SUCCESS/PARTIAL/EMPTY/
  RETRYABLE_ERROR/INTERNAL_ERROR）；
- search_tables 能力门控：无 table/table_row → EMPTY/UNSUPPORTED_FOR_DOCUMENT。

后端依赖通过临时 DB / monkeypatch 注入，不触碰真实 Evidence / Financial 数据。
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import store as estore
from financial_v2 import store as fstore
from tools import contracts as C
from tools import adapters as A


def _call(name, args, route="STANDARD_RAG"):
    return C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=name, arguments=args,
        idempotency_key=uuid.uuid4().hex, need_id="n1", batch_id="b1")


def _blk(doc_id, ver, period, etype, payload):
    return SimpleNamespace(
        document_id=doc_id, document_version=ver, report_period=period,
        evidence_type=etype, structured_payload=payload)


def _ref(eid, etype="paragraph", text="text", page=1):
    return SimpleNamespace(
        evidence_id=eid, source_name="src", page_number=page, evidence_type=etype,
        score=0.5, rank=1, text=text)


def _pack(status, evidence, failure_code=None, missing=None):
    return SimpleNamespace(
        status=status, evidence=evidence, failure_code=failure_code,
        retrieval_trace_id=uuid.uuid4().hex, missing_requirements=missing or [])


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

    # ---- Registry 集成（不触后端）----
    audit_dir = tempfile.mkdtemp(prefix="eval_adapters_")
    reg = A.build_default_registry(audit_dir=Path(audit_dir))
    names = {s.name for s in reg.list()}
    check(names == {"search_evidence", "inspect_evidence", "lookup_company_field",
                    "lookup_financial_metric", "compare_evidence", "search_tables"},
          "build_default_registry 注册 6 个本地工具")

    res = reg.execute(_call("lookup_company_field",
                            {"company_id": "x", "standard_item_code": "A", "evil": 1}),
                      route="DB_LOOKUP")
    check(res.error_code == "INVALID_ARGUMENTS", "DB 工具未知参数 → INVALID_ARGUMENTS")

    res = reg.execute(_call("lookup_company_field",
                            {"company_id": "x", "standard_item_code": "A"}),
                      route="STANDARD_RAG")
    check(res.error_code == "TOOL_NOT_ALLOWED", "DB 工具在非 DB 路由 → TOOL_NOT_ALLOWED")

    res = reg.execute(_call("compare_evidence", {"evidence_ids": ["a"]}),
                      route="DIRECT_EVIDENCE")
    check(res.error_code == "INVALID_ARGUMENTS", "compare_evidence <2 条 → INVALID_ARGUMENTS")

    # ---- lookup_company_field：无快照 → DB_FIELD_UNAVAILABLE（修订 3）----
    fin_tmp = Path(tempfile.mkdtemp(prefix="eval_adapters_fin_")) / "fin.db"
    fstore.init_db(fin_tmp)
    res = A._lookup_company_field_executor(
        {"company_id": "NOCOMPANY", "standard_item_code": "TOTAL_ASSETS"})
    check(res.status == "EMPTY" and res.error_code == "DB_FIELD_UNAVAILABLE",
          "无 Record Set/快照 → DB_FIELD_UNAVAILABLE（修订 3）")
    check(res.message not in ("", None), "DB_FIELD_UNAVAILABLE 携带原因说明")

    # ---- compare_evidence 结构化比较（修订 4）----
    fake_blocks = {
        "e1": _blk("d1", "v1", "2024-12-31", "paragraph", {"amount": "100"}),
        "e2": _blk("d1", "v1", "2024-12-31", "paragraph", {"amount": "200"}),
        "e3": _blk("d2", "v2", "2023-12-31", "table", {"amount": "100"}),
    }
    orig_get_evidence = estore.get_evidence
    estore.get_evidence = lambda eid: fake_blocks.get(eid)
    try:
        res = A._compare_evidence_executor(
            {"evidence_ids": ["e1", "e2", "e3", "missing"]})
    finally:
        estore.get_evidence = orig_get_evidence

    check(res.status == "PARTIAL" and res.evidence_ids == ["e1", "e2", "e3"],
          "compare_evidence 排除缺失 evidence_id 并标记 PARTIAL")
    check(res.data["missing_ids"] == ["missing"], "缺失 evidence_id 显式列出")
    pairs = {frozenset((p["left"], p["right"])): p for p in res.data["pairs"]}
    p12 = pairs[frozenset(("e1", "e2"))]
    check(p12["same_document"] and p12["same_period"] and p12["value_relation"] == "conflict",
          "同文档同期间不同值 → conflict（结构化，非 LLM 价值判断）")
    p13 = pairs[frozenset(("e1", "e3"))]
    check((not p13["same_document"]) and (not p13["same_period"]),
          "跨文档/异期间 → same_document=False / same_period=False")

    # ---- search_evidence 状态翻译 ----
    orig_search = A._search_local
    A._search_local = lambda c, q, k: _pack("COMPLETED", [_ref("ev1"), _ref("ev2")])
    try:
        res = A._search_evidence_executor({"company_id": "c", "query": "q", "k": 2})
    finally:
        A._search_local = orig_search
    check(res.status == "SUCCESS" and res.evidence_ids == ["ev1", "ev2"],
          "search_evidence COMPLETED → SUCCESS + evidence_ids")

    A._search_local = lambda c, q, k: _pack("EMPTY", [])
    try:
        res = A._search_evidence_executor({"company_id": "c", "query": "q"})
    finally:
        A._search_local = orig_search
    check(res.status == "EMPTY" and res.error_code == "RETRIEVAL_EMPTY",
          "search_evidence 空结果 → EMPTY + RETRIEVAL_EMPTY")

    A._search_local = lambda c, q, k: _pack("FAILED", [], failure_code="TIMEOUT")
    try:
        res = A._search_evidence_executor({"company_id": "c", "query": "q"})
    finally:
        A._search_local = orig_search
    check(res.status == "RETRYABLE_ERROR" and res.error_code == "TOOL_TIMEOUT",
          "search_evidence 超时 → RETRYABLE_ERROR + TOOL_TIMEOUT")

    A._search_local = lambda c, q, k: _pack("FAILED", [], failure_code="INDEX_NOT_FOUND")
    try:
        res = A._search_evidence_executor({"company_id": "c", "query": "q"})
    finally:
        A._search_local = orig_search
    check(res.status == "FATAL_ERROR" and res.error_code == "INTERNAL_ERROR",
          "search_evidence 索引缺失 → FATAL_ERROR + INTERNAL_ERROR")

    # ---- search_tables 能力门控 ----
    A._search_local = lambda c, q, k: _pack("COMPLETED", [_ref("p1", "paragraph")])
    try:
        res = A._search_tables_executor({"company_id": "c", "query": "q"})
    finally:
        A._search_local = orig_search
    check(res.status == "EMPTY" and res.error_code == "UNSUPPORTED_FOR_DOCUMENT",
          "search_tables 无表格结构 → EMPTY + UNSUPPORTED_FOR_DOCUMENT")

    A._search_local = lambda c, q, k: _pack(
        "COMPLETED", [_ref("p1", "paragraph"), _ref("t1", "table_row")])
    try:
        res = A._search_tables_executor({"company_id": "c", "query": "q"})
    finally:
        A._search_local = orig_search
    check(res.status == "SUCCESS" and res.evidence_ids == ["t1"],
          "search_tables 只保留 table/table_row Evidence")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
