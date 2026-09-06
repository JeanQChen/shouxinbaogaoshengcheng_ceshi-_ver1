"""gold 页码解析与只读语料盘点。

- resolve_gold_pages：把 case 的 gold_evidence_groups 依据 manifest 重新校验，
  产出逐 target 的 mapping_status（verified / missing）。
- inspect_corpus_readonly：只读盘点 ChromaDB collection 的实际索引库存，产出
  库存指纹与 document 级差异，不执行向量查询、不改写索引、不自动重建。
"""

from __future__ import annotations

import hashlib
import json
import logging

from evaluation.schema import (
    CorpusManifest,
    CorpusState,
    GoldEvidenceGroup,
    GoldEvidenceTarget,
    ResolvedGoldCase,
    RetrievalEvalCase,
)

logger = logging.getLogger(__name__)


def _recheck_target(
    target: GoldEvidenceTarget,
    manifest: CorpusManifest,
) -> GoldEvidenceTarget:
    """依据 manifest 重新校验单个 target 的映射状态（不修改入参）。"""
    if target.document_id is None:
        return GoldEvidenceTarget(
            document_id=None, pdf_page=target.pdf_page, mapping_status="missing",
            source_note=target.source_note, mapping_note="缺 document_id",
        )
    doc = manifest.by_id().get(target.document_id)
    if doc is None:
        return GoldEvidenceTarget(
            document_id=target.document_id, pdf_page=target.pdf_page, mapping_status="missing",
            source_note=target.source_note, mapping_note=f"document_id 未在 manifest 注册: {target.document_id}",
        )
    if target.pdf_page is None or not (1 <= target.pdf_page <= doc.page_count):
        return GoldEvidenceTarget(
            document_id=target.document_id, pdf_page=target.pdf_page, mapping_status="missing",
            source_note=target.source_note,
            mapping_note=f"页码越界或缺失（{doc.document_id} 共 {doc.page_count} 页）",
        )
    return GoldEvidenceTarget(
        document_id=target.document_id, pdf_page=target.pdf_page, mapping_status="verified",
        source_note=target.source_note,
        mapping_note=target.mapping_note or f"PDF 1-based 页序 {target.pdf_page}",
    )


def resolve_gold_pages(
    case: RetrievalEvalCase,
    manifest: CorpusManifest,
) -> ResolvedGoldCase:
    """重新解析 case 的 gold 页码，产出逐 target mapping_status。"""
    groups: list[GoldEvidenceGroup] = []
    for g in case.gold_evidence_groups:
        if g.is_local:
            new_targets = [_recheck_target(t, manifest) for t in g.targets]
        else:
            # external / structured_db 组无本地页，原样保留
            new_targets = list(g.targets)
        groups.append(GoldEvidenceGroup(
            group_id=g.group_id, requirement=g.requirement,
            channel=g.channel, targets=new_targets,
        ))
    return ResolvedGoldCase(case_id=case.case_id, groups=groups)


def inspect_corpus_readonly(
    manifest: CorpusManifest,
    db_path: str,
    collection: str,
    company_id: str,
) -> CorpusState:
    """只读盘点 ChromaDB collection 库存。

    Returns:
        CorpusState：库存指纹、document 级 page_range/chunk_count/文本哈希，
        以及与 manifest 的差异警告。不存在时 exists=False。
    """
    import chromadb

    coll_name = f"{collection}__{company_id}"
    client = chromadb.PersistentClient(path=db_path)

    try:
        coll = client.get_collection(name=coll_name)
    except Exception as e:
        return CorpusState(
            collection_name=coll_name, exists=False, chunk_count=0,
            documents_indexed={}, fingerprint="",
            warnings=[f"collection 不存在: {coll_name}（{e}）"],
        )

    n = coll.count()
    if n == 0:
        return CorpusState(
            collection_name=coll_name, exists=True, chunk_count=0,
            documents_indexed={}, fingerprint="", warnings=["collection 为空"],
        )

    data = coll.get(limit=n, include=["metadatas", "documents"])
    ids = data["ids"]
    metas = data["metadatas"]
    docs = data["documents"]

    documents_indexed: dict[str, dict] = {}
    indexed_pages: dict[str, set[int]] = {}
    records: list[tuple[str, str, int, int, str]] = []

    for i in range(n):
        meta = metas[i] if i < len(metas) else {}
        text = docs[i] if i < len(docs) else ""
        sf = meta.get("source_file", "")
        page = meta.get("page_number", 0)
        chunk = meta.get("chunk_index", 0)
        text_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        records.append((ids[i], sf, page, chunk, text_hash))

        entry = documents_indexed.setdefault(sf, {
            "page_min": page, "page_max": page, "chunk_count": 0,
            "source_type": meta.get("source_type", ""),
        })
        entry["page_min"] = min(entry["page_min"], page)
        entry["page_max"] = max(entry["page_max"], page)
        entry["chunk_count"] += 1
        if entry.get("source_type") == "" and meta.get("source_type"):
            entry["source_type"] = meta.get("source_type")

        if page > 0:
            indexed_pages.setdefault(sf, set()).add(page)

    # 稳定库存指纹：按 id 排序后哈希记录摘要
    records.sort(key=lambda r: r[0])
    fp_in = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    fingerprint = hashlib.sha256(fp_in).hexdigest()

    warnings: list[str] = []
    indexed_files = set(documents_indexed.keys())
    manifest_files = {d.source_file for d in manifest.documents}

    for d in manifest.documents:
        if d.source_file not in indexed_files:
            warnings.append(f"manifest 文档未入索引: {d.source_file}")
    for sf in sorted(indexed_files - manifest_files):
        warnings.append(f"索引中存在 manifest 未登记的文档: {sf}")

    return CorpusState(
        collection_name=coll_name, exists=True, chunk_count=n,
        documents_indexed=documents_indexed, fingerprint=fingerprint,
        indexed_pages=indexed_pages, warnings=warnings,
    )
