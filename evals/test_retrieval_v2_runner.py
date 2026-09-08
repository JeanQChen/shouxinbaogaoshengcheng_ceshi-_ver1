"""Eval: Track A Runner（evaluation/run_retrieval_v2.py）—— Phase 2 Commit 9。

用法: python -m evals.test_retrieval_v2_runner

覆盖（任务书 §9 Track A）：
- inspect_evidence_corpus：Evidence Store current 块 → V1 对齐 CorpusState
  （source_file 经 manifest document_id 映射）；
- _evidence_to_snapshot：EvidenceRef → RetrievedChunkSnapshot（document_id+页码）；
- run_case_v2：固定本地 Hybrid 决策（de-Router，不调 Router）→ 快照映射 + route_info；
- run_retrieval_v2 端到端：冻结分母、eligible 判定、命中计分（复用 V1 口径）、
  DB/External 排除题不计本地召回。

全临时目录 + mock embedding（不加载 BGE-M3、不污染生产库）；真实验收
（真实 BGE-M3 + 真实 300750）走 `python -m evaluation.run_retrieval_v2`。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import builder, ids, store as estore
from evidence import schema as ES
from parsers.pdf_parser import PdfParseResult, TextChunk
from financial_v2 import store as fstore
from retrieval import indexer_v2 as iv2
from retrieval import retriever_v2, sparse
from routing import schema as S

from evaluation import run_retrieval_v2 as runner


# ---------------------------------------------------------------------------
# 证据 seeding（同 test_retriever_v2）
# ---------------------------------------------------------------------------

def _tmp_dir(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


def _tmp_file(prefix: str, suffix: str) -> str:
    fd, p = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    return p


def _ctx(company: str = "ACME", document_id: str | None = None) -> ES.DocumentContext:
    return ES.DocumentContext(
        company_id=company, source_name="DOC1.pdf", source_type="annual_report",
        material_group="company_industry", document_id=document_id)


def _chunk(text: str, page: int, idx: int, section: str = "") -> TextChunk:
    return TextChunk(text=text, page_number=page, chunk_index=idx,
                     section_title=section, section_level=0)


def _seed_doc(tmpdir: str, company: str, doc_id: str, content: bytes,
              chunks: list[TextChunk]) -> ES.DocumentRecord:
    setv = builder.current_evidence_set_version()
    f = str(Path(tmpdir) / f"{doc_id}.pdf")
    Path(f).write_bytes(content)
    d = estore.register_document(f, _ctx(company, document_id=doc_id))
    parsed = PdfParseResult(chunks=chunks, page_count=1, metadata={})
    blocks = builder.build(parsed, d, setv)
    d.page_count = 1
    estore.commit_document(d, blocks, setv, ids.new_run_id(),
                           {"file_sha256": d.file_sha256},
                           builder.current_dependency_versions())
    return d


class _BagEmbedding:
    """确定性 bag-of-tokens 稠密向量（不加载 BGE-M3）。"""

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        import hashlib
        v = [0.0] * self.dim
        for t in sparse.tokenize(text):
            h = int(hashlib.sha256(t.encode("utf-8")).hexdigest(), 16) % self.dim
            v[h] += 1.0
        return v

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


# ---------------------------------------------------------------------------
# manifest / dataset 构造
# ---------------------------------------------------------------------------

def _write_manifest(path: str, company: str = "ACME") -> None:
    Path(path).write_text(json.dumps({
        "company_id": company, "page_system": "pdf_1based", "page_system_note": "",
        "documents": [{
            "document_id": "DOC1", "aliases": ["doc1"],
            "file_path": "data/xx/DOC1.pdf", "source_type": "annual_report",
            "sha256": "x", "page_count": 5, "page_system": "pdf", "page_offset": 0,
            "mapping_status": "verified", "verification_cases": [],
        }],
    }, ensure_ascii=False), encoding="utf-8")


def _write_dataset(path: str, company: str = "ACME") -> None:
    cases = [
        {"case_id": "A", "company_id": company, "section_id": "s1",
         "question": "实际控制人是谁", "expected_route_raw": "STRUCTURED",
         "expected_route_v2": "DIRECT_EVIDENCE", "priority": "P0",
         "time_scope": None, "gold_evidence_raw": {}, "notes": "", "gold_answer": None,
         "gold_evidence_groups": [{"group_id": "local__DOC1", "requirement": "all",
                                   "channel": "local",
                                   "targets": [{"document_id": "DOC1", "pdf_page": 1,
                                                "mapping_status": "verified",
                                                "source_note": "", "mapping_note": ""}]}]},
        {"case_id": "B", "company_id": company, "section_id": "s2",
         "question": "最新股价是多少", "expected_route_raw": "EXTERNAL",
         "expected_route_v2": "EXTERNAL_RESEARCH", "priority": "P0",
         "time_scope": None, "gold_evidence_raw": {}, "notes": "", "gold_answer": None,
         "gold_evidence_groups": [{"group_id": "external", "requirement": "all",
                                   "channel": "external", "targets": []}]},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 主测试
# ---------------------------------------------------------------------------

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

    # ---- 场景搭建（临时目录） ----
    ev_db = _tmp_dir("eval_r2_ev_")
    chroma_dir = _tmp_dir("eval_r2_chroma_")
    sparse_dir = _tmp_dir("eval_r2_sparse_")
    manifest_dir = _tmp_dir("eval_r2_manifest_")
    out_dir = _tmp_dir("eval_r2_out_")
    fin_db = _tmp_file("eval_r2_fin_", ".db")
    ev_db_path = str(Path(ev_db) / "evidence.db")

    estore.init_db(ev_db_path)
    model = _BagEmbedding()

    _seed_doc(ev_db, "ACME", "DOC1", b"v1", [
        _chunk("宁德时代实际控制人为曾毓群", 1, 0, "公司概况"),
        _chunk("主营业务为动力电池研发制造", 2, 1, "主营业务"),
    ])
    iv2.build_index("ACME", db_path=Path(chroma_dir),
                    manifest_dir=Path(manifest_dir), model=model)
    sparse.build_index("ACME", data_dir=Path(sparse_dir))
    session = retriever_v2.RetrievalSession(
        "ACME", chroma_dir=Path(chroma_dir), sparse_dir=Path(sparse_dir),
        manifest_dir=Path(manifest_dir), model=model)

    # manifest + dataset 落盘
    manifest_path = str(Path(ev_db) / "corpus_manifest.json")
    dataset_path = str(Path(ev_db) / "dataset.jsonl")
    _write_manifest(manifest_path)
    _write_dataset(dataset_path)

    # ---- 纯函数：inspect_evidence_corpus ----
    from evaluation.dataset import load_corpus_manifest
    manifest = load_corpus_manifest(manifest_path)
    cs = runner.inspect_evidence_corpus(manifest, "ACME")
    check(cs.exists and cs.chunk_count == 2,
          f"Evidence 语料盘点 exists=True, chunk_count=2（{cs.chunk_count}）")
    check("DOC1.pdf" in cs.documents_indexed,
          "documents_indexed 按 manifest source_file 组织（DOC1.pdf）")
    check(cs.indexed_pages.get("DOC1.pdf") == {1, 2},
          f"indexed_pages 记录页码 {{1,2}}（{cs.indexed_pages.get('DOC1.pdf')}）")
    check(cs.fingerprint, "语料指纹非空")

    # ---- 纯函数：_evidence_to_snapshot ----
    sf_by_id = {d.document_id: d.source_file for d in manifest.documents}
    ref = S.EvidenceRef(
        evidence_id="e1", document_id="DOC1", evidence_set_version="esv",
        source_name="whatever.pdf", source_type="annual_report", page_number=3,
        evidence_type="paragraph", text="正文", structured_payload=None,
        score=0.5, rank=1, retrieval_channels=["sparse", "dense"], channel_ranks={})
    snap = runner._evidence_to_snapshot(ref, sf_by_id)
    check(snap.source_file == "DOC1.pdf" and snap.page_number == 3,
          "EvidenceRef → snapshot：document_id→source_file + 页码透传")

    # ---- 端到端：run_retrieval_v2 ----
    result = runner.run_retrieval_v2(
        dataset_path=dataset_path, corpus_manifest_path=manifest_path,
        company_id="ACME", ks=[1, 5, 10], ev_db_path=ev_db_path, fin_db_path=fin_db,
        chroma_dir=chroma_dir, sparse_dir=sparse_dir, manifest_dir=manifest_dir,
        output_root=out_dir, session=session)

    agg = result.aggregate
    check(result.status == "completed",
          f"run_retrieval_v2 完成（{result.status}）")
    check(agg.n_total == 2 and agg.n_eligible == 1,
          f"冻结分母：n_total=2, n_eligible=1（{agg.n_total}/{agg.n_eligible}）")
    check(agg.required_page_coverage[10] == 1.0,
          f"RequiredPageCoverage@10 == 1.0（{agg.required_page_coverage[10]}）")
    check(agg.page_hit[10] == 1.0, f"PageHit@10 == 1.0（{agg.page_hit[10]}）")

    # 逐题：eligible 题命中 page1 + 固定本地决策（de-Router）
    cr_a = next(r for r in result.case_results if r.case_id == "A")
    check(len(cr_a.retrieved) >= 1
          and any(c.source_file == "DOC1.pdf" and c.page_number == 1 for c in cr_a.retrieved),
          "eligible 题返回 DOC1.pdf 第 1 页命中")
    check(cr_a.error is None, "eligible 题无错误")

    # de-Router：固定本地决策（不调 Router，reason_code=TRACK_A_FIXED_LOCAL）
    fixed = runner._fixed_local_decision(S.InformationNeed(
        need_id="X", section_id="s", question="q", required_evidence_types=[],
        required_source_types=[], time_scope=None, priority="P0", depends_on=[]))
    check(fixed.route == "STANDARD_RAG" and fixed.reason_code == "TRACK_A_FIXED_LOCAL"
          and fixed.decided_by == "rule",
          "固定本地决策：STANDARD_RAG + TRACK_A_FIXED_LOCAL + decided_by=rule")
    check(fixed.budget.candidate_k_sparse == 20 and fixed.budget.candidate_k_dense == 20
          and fixed.budget.context_k == 10,
          "固定预算：candidate_k=20、context_k=10（公平对照口径）")

    # route_info 落盘（case_results.jsonl）：eligible 题 route 为固定决策
    with open(Path(result.output_dir) / "case_results.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    row_a = next(r for r in rows if r["case_id"] == "A")
    check(row_a["route"]["route"] == "STANDARD_RAG"
          and row_a["route"]["reason_code"] == "TRACK_A_FIXED_LOCAL",
          "case_results.jsonl 记录固定决策（TRACK_A_FIXED_LOCAL）")

    # 产物落盘
    check(Path(result.output_dir).exists()
          and (Path(result.output_dir) / "metrics.json").exists(),
          "产物目录 + metrics.json 落盘")

    # ---- trace_meta 接线：mock run 的 trace 记录 run_id/case_id/dataset/corpus ----
    v2_logs = Path("logs/retrieval_v2")
    run_traces: list[dict] = []
    if v2_logs.exists():
        for p in sorted(v2_logs.glob("*.jsonl")):
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if obj.get("run_id") == result.run_id:
                run_traces.append(obj)
    check(len(run_traces) == 1 and run_traces[0].get("case_id") == "A",
          f"trace_meta 接线：mock run 落 1 条 trace 且 case_id=A（{len(run_traces)}）")
    if run_traces:
        t = run_traces[0]
        check(t.get("run_id") == result.run_id, "trace 记录 run_id")
        check(t.get("dataset_sha256") and t.get("corpus_manifest_sha256")
              and t.get("evidence_inventory_fingerprint")
              and t.get("code_config_fingerprint"),
              "trace 记录 dataset/corpus/inventory/code-config 指纹（非空）")

    # ---- fail-closed 完整性校验（verify_trace_integrity，纯函数 + 临时目录）----
    import tempfile as _tf
    from evaluation.run_retrieval_v2 import verify_trace_integrity

    tl_dir = Path(_tf.mkdtemp(prefix="eval_trace_"))
    rl_dir = Path(_tf.mkdtemp(prefix="eval_router_"))

    def _w_trace(d, rid, cid, ds, cm, emb="BAAI/bge-m3", dev="cpu", status="COMPLETED"):
        (d / f"{rid}__{cid}.jsonl").write_text(json.dumps({
            "run_id": rid, "case_id": cid, "status": status,
            "dataset_sha256": ds, "corpus_manifest_sha256": cm,
            "embedding_model": emb, "embedding_device": dev,
            "trace_id": f"t-{cid}",
        }, ensure_ascii=False) + "\n", encoding="utf-8")

    cids37 = [f"C{i:02d}" for i in range(37)]
    for cid in cids37:
        _w_trace(tl_dir, "RUN1", cid, "DS", "CM")
    rep = verify_trace_integrity("RUN1", cids37, "DS", "CM",
                                 router_audits_before=set(), logs_dir=tl_dir,
                                 router_logs_dir=rl_dir)
    check(rep.ok and rep.n_traces == 37,
          f"37 条 trace 完整性通过（n={rep.n_traces}）")
    check("BAAI/bge-m3" in rep.embedding_model_seen and "cpu" in rep.devices_seen,
          "embedding_model/device 被记录")

    rep_missing = verify_trace_integrity("RUN1", cids37 + ["C99"], "DS", "CM",
                                         router_audits_before=set(), logs_dir=tl_dir,
                                         router_logs_dir=rl_dir)
    check(not rep_missing.ok and any("不一致" in e or "非 eligible" in e
                                     for e in rep_missing.errors),
          "case 集合不一致 → fail-closed")

    tl2 = Path(_tf.mkdtemp(prefix="eval_trace2_"))
    for cid in cids37:
        _w_trace(tl2, "RUN1", cid, "DS_DRIFT", "CM")
    rep_drift = verify_trace_integrity("RUN1", cids37, "DS", "CM",
                                       router_audits_before=set(), logs_dir=tl2,
                                       router_logs_dir=rl_dir)
    check(not rep_drift.ok and any("dataset_sha256" in e for e in rep_drift.errors),
          "dataset_sha256 漂移 → fail-closed")

    tl3 = Path(_tf.mkdtemp(prefix="eval_trace3_"))
    for cid in cids37:
        _w_trace(tl3, "RUN1", cid, "DS", "CM", dev=None)
    rep_dev = verify_trace_integrity("RUN1", cids37, "DS", "CM",
                                     router_audits_before=set(), logs_dir=tl3,
                                     router_logs_dir=rl_dir)
    check(not rep_dev.ok and any("embedding_device" in e for e in rep_dev.errors),
          "embedding_device 为空 → fail-closed")

    rl2 = Path(_tf.mkdtemp(prefix="eval_router2_"))
    (rl2 / "audit1.jsonl").write_text("{}\n", encoding="utf-8")
    rep_router = verify_trace_integrity("RUN1", cids37, "DS", "CM",
                                        router_audits_before=set(), logs_dir=tl_dir,
                                        router_logs_dir=rl2)
    check(not rep_router.ok and any("Router audit" in e for e in rep_router.errors),
          "新增 Router audit → fail-closed")

    # ---- 冻结分母校验（真实 41 问，纯资格判定，不加载 BGE-M3） ----
    from evaluation.dataset import load_dataset
    from evaluation.failure_classifier import classify_eligibility
    _repo = Path(__file__).resolve().parent.parent
    real_cases = load_dataset(_repo / "evaluation/datasets/v1_baseline.jsonl")
    real_manifest = load_corpus_manifest(_repo / "evaluation/datasets/corpus_manifest.json")
    real_excl: dict[str, int] = {}
    for case in real_cases:
        elig = classify_eligibility(case, real_manifest, None)
        real_excl[elig.status] = real_excl.get(elig.status, 0) + 1
    check(real_excl == runner.FROZEN_ELIGIBILITY_BREAKDOWN,
          f"冻结分母 == {runner.FROZEN_ELIGIBILITY_BREAKDOWN}（实际 {real_excl}）")
    try:
        runner.verify_frozen_denominator(
            {"ELIGIBLE_LOCAL": 36, "EXTERNAL_ONLY": 3, "INVALID_GOLD_MAPPING": 1})
        check(False, "冻结分母不符应抛 ValueError")
    except ValueError:
        check(True, "冻结分母不符 → fail-closed 抛 ValueError")

    # 清理
    for p in (fin_db,):
        for suf in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(p + suf)
            except FileNotFoundError:
                pass

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
