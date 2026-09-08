"""Eval: V2 Hybrid Retriever（retrieval/retriever_v2.py + trace_v2.py）—— Phase 2 Commit 7。

用法: python -m evals.test_retriever_v2

覆盖（任务书 §8 / 契约修正 B/C）：
- Hybrid（STANDARD/DIRECT/DEEP）：sparse+dense → RRF 按 evidence_id 融合 → EvidenceRef；
- DB_LOOKUP：字段/指标 → StructuredResultRef（不伪造成 EvidenceRef），
  可用 → DB_RESULT_AVAILABLE，不可用 → DB_FIELD_UNAVAILABLE；
- EXTERNAL_RESEARCH → EXTERNAL_RESEARCH_NOT_IMPLEMENTED；
- 索引缺失/版本不匹配 fail-closed（INDEX_NOT_FOUND / INDEX_VERSION_MISMATCH）；
- trace 落盘 logs/retrieval/（RAG 可观测性）。

全部临时目录 + mock embedding，不加载 BGE-M3、不污染生产库。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import builder, ids, store as estore
from evidence import schema as ES
from parsers.pdf_parser import PdfParseResult, TextChunk
from financial_v2 import progress
from financial_v2 import schema as FS
from financial_v2 import snapshots
from financial_v2 import store as fstore
from financial_v2 import validator as fvalidator
from retrieval import indexer_v2 as iv2
from retrieval import retriever_v2 as rv2
from retrieval import sparse, trace_v2
from routing import context as rcontext
from routing import schema as S


# ---------------------------------------------------------------------------
# 证据 seeding（同 test_indexer_v2）
# ---------------------------------------------------------------------------

def _tmp_dir(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


def _write_pdf(tmpdir: str, name: str, content: bytes) -> str:
    p = Path(tmpdir) / name
    p.write_bytes(content)
    return str(p)


def _ctx(company: str = "ACME", document_id: str | None = None) -> ES.DocumentContext:
    return ES.DocumentContext(
        company_id=company, source_name="rpt.pdf", source_type="annual_report",
        material_group="company_industry", document_id=document_id)


def _chunk(text: str, page: int, idx: int, section: str = "") -> TextChunk:
    return TextChunk(text=text, page_number=page, chunk_index=idx,
                     section_title=section, section_level=0)


def _seed_doc(tmpdir: str, company: str, doc_id: str, content: bytes,
              chunks: list[TextChunk]) -> ES.DocumentRecord:
    setv = builder.current_evidence_set_version()
    f = _write_pdf(tmpdir, f"{doc_id}.pdf", content)
    d = estore.register_document(f, _ctx(company, document_id=doc_id))
    parsed = PdfParseResult(chunks=chunks, page_count=1, metadata={})
    blocks = builder.build(parsed, d, setv)
    d.page_count = 1
    estore.commit_document(d, blocks, setv, ids.new_run_id(),
                           {"file_sha256": d.file_sha256},
                           builder.current_dependency_versions())
    return d


class _BagEmbedding:
    """确定性 bag-of-tokens 稠密向量（不加载 BGE-M3），余弦相似度随词元重叠增长。"""

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for t in sparse.tokenize(text):
            h = int(hashlib.sha256(t.encode("utf-8")).hexdigest(), 16) % self.dim
            v[h] += 1.0
        return v

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


# ---------------------------------------------------------------------------
# 财务 seeding（同 test_context）
# ---------------------------------------------------------------------------

def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_rv2_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> FS.SourceLocator:
    return FS.SourceLocator(kind="excel", excel=FS.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"

_STMT = {
    "CURRENT_ASSETS": "balance_sheet", "CURRENT_LIABILITIES": "balance_sheet",
    "TOTAL_ASSETS": "balance_sheet", "TOTAL_LIABILITIES": "balance_sheet",
    "TOTAL_EQUITY": "balance_sheet", "TOTAL_REVENUE": "income_statement",
    "NET_PROFIT": "income_statement",
}


def _seed_records(company: str, ext_id: str, specs: list[dict]) -> str:
    source_document_id = FS.scope_source_document_id(company, ext_id)
    source_version = FS.derive_source_version(
        source_document_id, hashlib.sha256(ext_id.encode()).hexdigest())
    fstore.register_source_atomic(
        FS.FinancialSourceDocument(
            source_document_id=source_document_id, company_id=company,
            source_name=f"{ext_id}.xlsx", source_class="financial_statement",
            declared_company_name=company, detected_company_name=company,
            subject_match_status="matched", created_at=_TS),
        FS.FinancialSourceVersion(
            source_version=source_version, source_document_id=source_document_id,
            file_sha256=hashlib.sha256(ext_id.encode()).hexdigest(), file_type="xlsx",
            file_size=100, document_id=None, document_version=None, created_at=_TS))

    rs = FS.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
    records = []
    for i, sp in enumerate(specs):
        r = FS.SourceFinancialRecord(
            record_id="", record_set_version=rs, company_id=company,
            standard_item_code=sp["item_code"],
            statement_type=sp.get("statement_type", "balance_sheet"),
            raw_item_text=sp["item_code"], raw_value=sp["value"], raw_unit="wan_yuan",
            raw_currency="CNY", std_value=sp["value"], std_unit="yuan",
            std_currency="CNY", conversion_rule_version="1.0",
            report_period=sp["period"], period_type="annual",
            statement_scope="consolidated", currency="CNY",
            restatement_version="0", locator=_locator(i + 2),
            mapping_mode="rule", confidence=1.0, record_hash="", quality_flags=[],
            created_at=_TS, candidate_id=None)
        r.record_hash = fvalidator._record_hash(r)
        r.record_id = FS.derive_record_id(rs, FS.record_identity_fields(r))
        records.append(r)

    record_set = FS.FinancialRecordSet(
        record_set_version=rs, source_version=source_version,
        extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
        normalization_rule_version="1.0", dependency_versions={},
        report_periods=sorted({r.report_period for r in records}),
        currency="CNY", unit="wan_yuan", statement_scope="consolidated",
        audit_status="audited", block_count=0, record_count=len(records),
        created_at=_TS, input_candidate_set_version=rs)
    fstore.commit_normalization_atomic(record_set, records, [], source_document_id)
    return rs


def _specs(values: dict[str, Decimal], period: str) -> list[dict]:
    return [{"item_code": code, "value": val, "period": period,
             "statement_type": _STMT.get(code, "balance_sheet")}
            for code, val in values.items()]


# ---------------------------------------------------------------------------
# 路由测试构件
# ---------------------------------------------------------------------------

_BUDGET = S.RetrievalBudget(candidate_k_sparse=20, candidate_k_dense=20,
                            fusion_k=20, context_k=10, timeout_ms=5000)
_BUDGET_NOOP = S.RetrievalBudget(candidate_k_sparse=1, candidate_k_dense=1,
                                 fusion_k=1, context_k=1, timeout_ms=1000)

_REASON = {
    "DB_LOOKUP": "REGISTERED_DB_FIELD",
    "EXTERNAL_RESEARCH": "EXPLICIT_EXTERNAL_RECENCY",
    "DIRECT_EVIDENCE": "EXACT_DOCUMENT_FIELD",
    "DEEP_RETRIEVAL": "CROSS_DOCUMENT_OR_CONFLICT",
    "STANDARD_RAG": "SECTION_TOPIC_SYNTHESIS",
}


def _need(nid: str, q: str) -> S.InformationNeed:
    return S.InformationNeed(need_id=nid, section_id="s", question=q,
                             required_evidence_types=[], required_source_types=[],
                             time_scope=None, priority="normal", depends_on=[])


def _decision(nid: str, route: str, filters: dict | None = None) -> S.RouteDecision:
    budget = _BUDGET_NOOP if route in ("DB_LOOKUP", "EXTERNAL_RESEARCH") else _BUDGET
    return S.RouteDecision(need_id=nid, route=route, reason_code=_REASON[route],
                           filters=filters or {}, budget=budget, fallback_routes=[],
                           decided_by="rule", rule_version=S.RULE_VERSION, confidence="high")


def _ctx_empty(company: str = "ACME") -> S.RouteContext:
    return S.RouteContext(company_id=company, report_as_of=None,
                          available_document_ids=[], available_source_types=[],
                          supported_db_fields=[], supported_metric_ids=[],
                          available_db_fields=[], available_metric_ids=[],
                          external_research_enabled=True)


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

    # ---- trace 单测 ----
    tdir = _tmp_dir("eval_rv2_trace_")
    t = trace_v2.RetrievalTrace(
        trace_id="abc123", timestamp="20260101T000000", company_id="ACME",
        need_id="n1", route="STANDARD_RAG", query="实际控制人",
        index_version="v", status="COMPLETED", failure_code=None)
    tpath = trace_v2.write_trace(t, Path(tdir))
    check(Path(tpath).exists() and Path(tpath).name.endswith(".jsonl"),
          "trace 落盘为 JSONL 文件")

    # ---- 契约修正 6：扩展字段序列化 + fail-closed ----
    t2 = trace_v2.RetrievalTrace(
        trace_id="abc456", timestamp="20260101T000001", company_id="ACME",
        need_id="n1", route="STANDARD_RAG", query="实际控制人",
        index_version="v", status="COMPLETED", failure_code=None,
        run_id="run-1", case_id="r001", dataset_sha256="dsha",
        corpus_manifest_sha256="msha", evidence_inventory_fingerprint="efp",
        code_config_fingerprint="ccfp",
        rrf_full_ranking=[{"evidence_id": "e1", "rrf_score": 0.5, "rank": 1}],
        embedding_model="mock", embedding_device="cpu", embedding_first_load_ms=1.0,
        filter_pre_count=20, filter_post_count=18,
        timings_ms={"sparse": 1, "dense": 2, "total": 3},
        rss_bytes=123, rss_note=None)
    t2path = trace_v2.write_trace(t2, Path(tdir))
    rec = json.loads(Path(t2path).read_text(encoding="utf-8"))
    check(rec["run_id"] == "run-1" and rec["case_id"] == "r001"
          and rec["rrf_full_ranking"][0]["evidence_id"] == "e1"
          and rec["timings_ms"]["total"] == 3,
          "trace 扩展字段（run/case/完整排名/timings）正确序列化")

    # fail-closed：logs_dir 被文件占据 → 抛 TraceWriteError
    blocker = Path(tdir) / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    try:
        trace_v2.write_trace(t, blocker)
        check(False, "不可写 logs_dir 应抛 TraceWriteError")
    except trace_v2.TraceWriteError:
        check(True, "trace 落盘失败抛 TraceWriteError（fail-closed）")

    # code_config_fingerprint 确定性
    fp_a = trace_v2.code_config_fingerprint({"tokenizer": "1", "indexer": "1"}, "v2-rule-1.0")
    fp_b = trace_v2.code_config_fingerprint({"indexer": "1", "tokenizer": "1"}, "v2-rule-1.0")
    check(fp_a == fp_b and len(fp_a) == 32, "code/config 指纹确定性且长度 32")

    # ---- Hybrid 检索 ----
    ev_db = _tmp_dir("eval_rv2_ev_")
    chroma_dir = _tmp_dir("eval_rv2_chroma_")
    sparse_dir = _tmp_dir("eval_rv2_sparse_")
    manifest_dir = _tmp_dir("eval_rv2_manifest_")
    estore.init_db(str(Path(ev_db) / "evidence.db"))
    model = _BagEmbedding()

    _seed_doc(ev_db, "ACME", "doc-1", b"v1", [
        _chunk("宁德时代实际控制人为曾毓群", 1, 0, "公司概况"),
        _chunk("主营业务为动力电池研发制造", 1, 1, "主营业务"),
    ])
    iv2.build_index("ACME", db_path=Path(chroma_dir),
                    manifest_dir=Path(manifest_dir), model=model)
    sparse.build_index("ACME", data_dir=Path(sparse_dir))

    session = rv2.RetrievalSession("ACME", chroma_dir=Path(chroma_dir),
                                   sparse_dir=Path(sparse_dir),
                                   manifest_dir=Path(manifest_dir), model=model)

    pack = session.retrieve(_need("n1", "实际控制人是谁"),
                            _decision("n1", "STANDARD_RAG"), _ctx_empty())
    check(pack.status == "COMPLETED", f"Hybrid 检索 COMPLETED（{pack.status}）")
    check(len(pack.evidence) >= 1, "Hybrid 返回 EvidenceRef")
    check(pack.evidence[0].rank == 1 and "实际控制人" in pack.evidence[0].text,
          "top-1 命中含「实际控制人」的块")
    check(set(pack.evidence[0].retrieval_channels) <= {"sparse", "dense"}
          and pack.evidence[0].retrieval_channels,
          "EvidenceRef 记录通道来源")
    check(len({r.evidence_id for r in pack.evidence}) == len(pack.evidence),
          "融合结果 evidence_id 去重")
    check(pack.retrieval_trace_id, "回填 retrieval_trace_id")

    # EXTERNAL_RESEARCH
    ext = session.retrieve(_need("n2", "最新股价是多少"),
                           _decision("n2", "EXTERNAL_RESEARCH"), _ctx_empty())
    check(ext.status == "EXTERNAL_RESEARCH_NOT_IMPLEMENTED",
          "EXTERNAL_RESEARCH 未实现状态")

    # 版本不匹配：新版本替换后旧索引失效
    _seed_doc(ev_db, "ACME", "doc-1", b"v2", [_chunk("新版内容完全替换", 1, 0)])
    session_stale = rv2.RetrievalSession("ACME", chroma_dir=Path(chroma_dir),
                                         sparse_dir=Path(sparse_dir),
                                         manifest_dir=Path(manifest_dir), model=model)
    stale = session_stale.retrieve(_need("n3", "实际控制人"),
                                   _decision("n3", "STANDARD_RAG"), _ctx_empty())
    check(stale.status == "FAILED" and stale.failure_code == "INDEX_VERSION_MISMATCH",
          "索引版本不匹配 fail-closed（INDEX_VERSION_MISMATCH）")

    # 索引缺失
    ev_db2 = _tmp_dir("eval_rv2_ev2_")
    estore.init_db(str(Path(ev_db2) / "evidence.db"))
    session_missing = rv2.RetrievalSession(
        "ACME", chroma_dir=Path(_tmp_dir("eval_rv2_chroma2_")),
        sparse_dir=Path(_tmp_dir("eval_rv2_sparse2_")),
        manifest_dir=Path(_tmp_dir("eval_rv2_manifest2_")), model=model)
    miss = session_missing.retrieve(_need("n4", "风险有哪些"),
                                    _decision("n4", "STANDARD_RAG"), _ctx_empty())
    check(miss.status == "FAILED" and miss.failure_code == "INDEX_NOT_FOUND",
          "索引缺失 fail-closed（INDEX_NOT_FOUND）")

    # ---- DB_LOOKUP ----
    fin_db = _tmp_db()
    ev_db3 = _tmp_db()
    try:
        fstore.init_db(fin_db)
        estore.init_db(ev_db3)

        rs = _seed_records("ACME", "ok", _specs({
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
            "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
            "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
            "NET_PROFIT": Decimal("120"),
        }, "2024-12-31"))
        req = snapshots.SnapshotBuildRequest(
            company_id="ACME", as_of_date="2024-12-31", scope="consolidated",
            currency="CNY", purpose="credit_analysis", record_set_ids=[rs],
            reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
            restatement_selection={}, policy_adjustments={}, run_id="run-rv2")
        res = progress.run_pipeline(req)
        check(res.final_state == "completed", "前置：快照构建成功")

        ctx_db = rcontext.build_route_context("ACME")
        session_db = rv2.RetrievalSession(
            "ACME", chroma_dir=Path(_tmp_dir("eval_rv2_chroma3_")),
            sparse_dir=Path(_tmp_dir("eval_rv2_sparse3_")),
            manifest_dir=Path(_tmp_dir("eval_rv2_manifest3_")), model=model)

        d_ok = _decision("db1", "DB_LOOKUP",
                         {"db_target_type": "field", "standard_item_code": "CURRENT_ASSETS",
                          "snapshot_as_of_date": "2024-12-31",
                          "target_period": "2024-12-31", "scope": "consolidated",
                          "currency": "CNY", "purpose": "credit_analysis"})
        p_ok = session_db.retrieve(_need("db1", "流动资产是多少"), d_ok, ctx_db)
        check(p_ok.status == "DB_RESULT_AVAILABLE",
              f"可用字段 → DB_RESULT_AVAILABLE（{p_ok.status}）")
        check(len(p_ok.structured_results) == 1 and p_ok.evidence == [],
              "DB 结果走 structured_results，不伪造成 EvidenceRef")
        check(p_ok.structured_results[0].item_code == "CURRENT_ASSETS"
              and p_ok.structured_results[0].raw_value == "200",
              "字段结构化结果 item_code + raw_value 正确")

        d_miss = _decision("db2", "DB_LOOKUP",
                           {"db_target_type": "field",
                            "standard_item_code": "OPERATING_CASH_FLOW",
                            "snapshot_as_of_date": "2024-12-31",
                            "target_period": "2024-12-31", "scope": "consolidated",
                            "currency": "CNY", "purpose": "credit_analysis"})
        p_miss = session_db.retrieve(_need("db2", "经营现金流是多少"), d_miss, ctx_db)
        check(p_miss.status == "DB_FIELD_UNAVAILABLE",
              f"不可用字段 → DB_FIELD_UNAVAILABLE（{p_miss.status}）")
        check(p_miss.evidence == [] and p_miss.structured_results == []
              and p_miss.missing_requirements,
              "不可用字段两类结果为空 + 写明缺失项")
    finally:
        _cleanup_db(fin_db)
        _cleanup_db(ev_db3)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
