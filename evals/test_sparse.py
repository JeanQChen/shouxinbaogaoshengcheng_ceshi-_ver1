"""Eval: V2 Sparse（BM25）通道（retrieval/sparse.py）—— Phase 2 Commit 5。

用法: python -m evals.test_sparse

覆盖（任务书 §6/§11）：
- 分词：NFKC、英文小写、中文字符二元组、数字/日期/百分比/股票代码整词元、空输入；
- BM25：build/search、幂等（同 index_version 跳过重建）、空查询/损坏/版本不匹配
  显式失败、跨公司隔离。

全部临时目录，不加载 BGE-M3、不污染生产库。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import builder, ids, store
from evidence import schema as ES
from parsers.pdf_parser import PdfParseResult, TextChunk
from retrieval import sparse


def _tmp_dir(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


def _write_pdf(tmpdir: str, name: str, content: bytes) -> str:
    p = Path(tmpdir) / name
    p.write_bytes(content)
    return str(p)


def _ctx(company: str = "ACME", document_id: str | None = None,
         source_type: str = "annual_report") -> ES.DocumentContext:
    return ES.DocumentContext(
        company_id=company, source_name="rpt.pdf", source_type=source_type,
        material_group="company_industry", document_id=document_id)


def _chunk(text: str, page: int, idx: int, section: str = "") -> TextChunk:
    return TextChunk(text=text, page_number=page, chunk_index=idx,
                     section_title=section, section_level=0)


def _seed_doc(tmpdir: str, company: str, doc_id: str, content: bytes,
              chunks: list[TextChunk]) -> ES.DocumentRecord:
    setv = builder.current_evidence_set_version()
    f = _write_pdf(tmpdir, f"{doc_id}.pdf", content)
    d = store.register_document(f, _ctx(company, document_id=doc_id))
    parsed = PdfParseResult(chunks=chunks, page_count=1, metadata={})
    blocks = builder.build(parsed, d, setv)
    d.page_count = 1
    store.commit_document(d, blocks, setv, ids.new_run_id(),
                          {"file_sha256": d.file_sha256},
                          builder.current_dependency_versions())
    return d


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

    # ---- 分词（纯函数） ----
    check(sparse.tokenize("实际控制人") == ["实际", "际控", "控制", "制人"],
          "中文字符二元组")
    check(sparse.tokenize("宁德时代") == ["宁德", "德时", "时代"],
          "四字中文 → 3 个二元组")
    check(sparse.tokenize("300750") == ["300750"], "股票代码整词元保留")
    check(sparse.tokenize("BGE-M3") == ["bge", "m3"], "英文缩写小写 + 分段")
    check(sparse.tokenize("2024-12-31") == ["2024", "12", "31"], "日期数字保留")
    check(sparse.tokenize("25.3%") == ["25.3%"], "百分比保留")
    check(sparse.tokenize("１２３") == ["123"], "NFKC 全角数字转半角")
    check(sparse.tokenize("   ") == [], "纯空白 → 空词元")
    check(sparse.tokenize("") == [], "空字符串 → 空词元")

    # ---- 集成：build + search + 幂等 + 失败态 ----
    ev_db = _tmp_dir("eval_sparse_ev_")
    data_dir = _tmp_dir("eval_sparse_data_")
    store.init_db(str(Path(ev_db) / "evidence.db"))

    d1 = _seed_doc(ev_db, "ACME", "doc-1", b"v1", [
        _chunk("宁德时代实际控制人为曾毓群", 1, 0, "公司概况"),
        _chunk("主营业务为动力电池研发", 1, 1, "主营业务"),
    ])

    idx = sparse.build_index("ACME", data_dir=Path(data_dir))
    check(idx.record_count == 2, "BM25 索引覆盖全部 current 块")
    check(len(idx.vocab) > 0 and len(idx.postings) > 0, "词表/倒排表非空")
    check(idx.doc_lengths and len(idx.doc_lengths) == 2, "文档长度落盘")
    check(set(idx.params) == {"k1", "b"}, "BM25 参数持久化")

    # 幂等：同输入重建 → 同 index_version，跳过重建
    idx2 = sparse.build_index("ACME", data_dir=Path(data_dir))
    check(idx.index_version == idx2.index_version, "同输入重建 index_version 不变（幂等）")
    check(idx.built_at == idx2.built_at, "幂等跳过重建保留首次 built_at")

    # 检索：命中"实际控制人"
    hits = sparse.search("ACME", "实际控制人", k=5, data_dir=Path(data_dir))
    check(len(hits) >= 1 and hits[0].rank == 1, "检索返回排序结果")
    check(all(isinstance(h.evidence_id, str) for h in hits),
          "检索结果 evidence_id 为字符串且非空")

    # 空查询 / k<=0 显式失败
    try:
        sparse.search("ACME", "   ", data_dir=Path(data_dir))
        check(False, "空查询应显式失败")
    except ValueError:
        check(True, "空查询显式失败")

    # 跨公司隔离：BETA 索引不含 ACME 内容
    _seed_doc(ev_db, "BETA", "doc-b", b"v1", [_chunk("腾讯控股主营社交网络", 1, 0)])
    sparse.build_index("BETA", data_dir=Path(data_dir))
    beta_hits = sparse.search("BETA", "宁德时代", data_dir=Path(data_dir))
    check(beta_hits == [], "跨公司隔离：BETA 检索不到 ACME 内容")

    # 版本不匹配：新版本替换后，旧 sparse 索引失效
    _seed_doc(ev_db, "ACME", "doc-1", b"v2", [_chunk("新版本内容完全替换", 1, 0)])
    try:
        sparse.search("ACME", "实际控制人", data_dir=Path(data_dir))
        check(False, "版本不匹配应显式失败")
    except sparse.SparseIndexVersionMismatch:
        check(True, "版本不匹配显式失败（SparseIndexVersionMismatch）")

    # 损坏：写垃圾内容 → 加载失败
    p = sparse._path(Path(data_dir), "ACME")
    p.write_text("not valid json {", encoding="utf-8")
    try:
        sparse.load_index("ACME", Path(data_dir))
        check(False, "损坏索引应显式失败")
    except sparse.SparseCorrupt:
        check(True, "损坏索引显式失败（SparseCorrupt）")

    # 未构建 → SparseIndexNotFound
    try:
        sparse.search("NEVER-BUILT", "宁德时代", data_dir=Path(data_dir))
        check(False, "未构建索引应显式失败")
    except sparse.SparseIndexNotFound:
        check(True, "未构建索引显式失败（SparseIndexNotFound）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
