"""Eval: V2 Dense 索引器（retrieval/indexer_v2.py）—— Phase 2 Commit 4。

用法: python -m evals.test_indexer_v2

覆盖（任务书 §6 / 契约修正 3）：
- index_version 确定性派生且排除 built_at（同输入重建同版本）；
- 库存指纹稳定且对内容/数量变化敏感；
- 只索引 current、健康 Evidence Set（retired/superseded 排除）；
- manifest 冻结文档/证据集版本、组件版本、记录数、指纹与构建时间。

全部临时目录 + mock embedding，不加载 BGE-M3、不污染生产库。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import builder, ids, store
from evidence import schema as ES
from parsers.pdf_parser import PdfParseResult, TextChunk
from retrieval import indexer_v2 as iv2


def _tmp_dir(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


class _MockEmbedding:
    """确定性 mock embedding（不加载 BGE-M3）。"""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.5, 2.0] for t in texts]


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

    # ---- 纯派生：index_version 确定性 + 排除 built_at ----
    docs = [{"document_id": "d1", "document_version": "v1", "evidence_set_version": "s1"}]
    versions = {"indexer": "1", "embedding": "bge-m3"}
    v1 = iv2.derive_index_version("ACME", docs, 2, "fp", versions)
    v2 = iv2.derive_index_version("ACME", docs, 2, "fp", versions)
    check(v1 == v2 and len(v1) == 32, "index_version 确定性（同输入同输出）")
    v3 = iv2.derive_index_version("ACME", docs, 3, "fp", versions)
    check(v1 != v3, "记录数变化 → index_version 变化")
    v4 = iv2.derive_index_version("ACME", docs, 2, "fp2", versions)
    check(v1 != v4, "库存指纹变化 → index_version 变化")
    # 结构上 built_at 不参与版本：签名不含 built_at 参数，manifest 才记录 built_at
    check("built_at" not in iv2.derive_index_version.__code__.co_varnames,
          "derive_index_version 签名不含 built_at（契约修正 3）")

    # ---- 纯派生：库存指纹 ----
    class _B:
        def __init__(self, eid, ch):
            self.evidence_id = eid
            self.content_hash = ch
    f1 = iv2._inventory_fingerprint([_B("e1", "h1"), _B("e2", "h2")])
    f2 = iv2._inventory_fingerprint([_B("e2", "h2"), _B("e1", "h1")])
    check(f1 == f2, "库存指纹与顺序无关")
    f3 = iv2._inventory_fingerprint([_B("e1", "h1"), _B("e2", "hX")])
    check(f1 != f3, "库存指纹对内容哈希敏感")

    # ---- 集成：只索引 current，同输入重建同版本 ----
    ev_db = _tmp_dir("eval_iv2_ev_")
    chroma_dir = _tmp_dir("eval_iv2_chroma_")
    manifest_dir = _tmp_dir("eval_iv2_manifest_")
    store.init_db(str(Path(ev_db) / "evidence.db"))
    model = _MockEmbedding()

    d1 = _seed_doc(ev_db, "ACME", "doc-1", b"v1",
                   [_chunk("第一段", 1, 0, "概述"), _chunk("第二段", 1, 1, "概述")])
    m1 = iv2.build_index("ACME", db_path=Path(chroma_dir),
                         manifest_dir=Path(manifest_dir), model=model)
    check(m1.record_count == 2, "current 证据块全部索引（record_count=2）")
    check(m1.collection == f"v2_dense__ACME__{m1.index_version}",
          "collection 命名含 company + index_version")
    check(m1.documents == [{"document_id": "doc-1",
                            "document_version": d1.document_version,
                            "evidence_set_version": builder.current_evidence_set_version()}],
          "manifest documents 冻结文档+证据集版本")
    check(m1.versions["embedding"] == "bge-m3" and m1.versions["tokenizer"] == "1",
          "manifest 冻结组件版本")
    check(m1.built_at != "", "manifest 记录 built_at")

    # 同输入重建 → 同 index_version（built_at 不同不影响版本）
    m2 = iv2.build_index("ACME", db_path=Path(chroma_dir),
                         manifest_dir=Path(manifest_dir), model=model)
    check(m1.index_version == m2.index_version,
          "同输入重建 index_version 不变（built_at 不参与版本）")

    # 新版本文档替换 → 只索引 current，版本变化
    _seed_doc(ev_db, "ACME", "doc-1", b"v2", [_chunk("新版内容", 1, 0)])
    m3 = iv2.build_index("ACME", db_path=Path(chroma_dir),
                         manifest_dir=Path(manifest_dir), model=model)
    check(m3.record_count == 1, "新版本替换后只索引 current（record_count=1）")
    check(m3.index_version != m1.index_version, "输入变化 → 新 index_version")

    # 无证据公司 → 空索引不崩溃
    m4 = iv2.build_index("NO-CO", db_path=Path(chroma_dir),
                         manifest_dir=Path(manifest_dir), model=model)
    check(m4.record_count == 0, "无证据公司空索引（record_count=0）")
    check(iv2.load_manifest("NO-CO", Path(manifest_dir)).index_version == m4.index_version,
          "空索引也落盘 manifest")

    # ---- manifest 读回 + inspect ----
    back = iv2.load_manifest("ACME", Path(manifest_dir))
    check(back.index_version == m3.index_version, "load_manifest 读回一致")
    ins = iv2.inspect("ACME", db_path=Path(chroma_dir), manifest_dir=Path(manifest_dir))
    check(ins["indexed"] is True and ins["collection_block_count"] == 1,
          "inspect 报告 collection 块数")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
