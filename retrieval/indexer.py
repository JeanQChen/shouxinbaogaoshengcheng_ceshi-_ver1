"""PDF chunks → ChromaDB 向量化入库，含 embedding 缓存加速重复索引。

支持文档类型标记（年报/债券募集说明书/其他），写入 ChromaDB metadata
供检索时按优先级加权。
"""

import hashlib
import json
import logging
import os
from pathlib import Path

from parsers.pdf_parser import TextChunk
from retrieval.embedding import get_embedding_model

logger = logging.getLogger(__name__)

_BATCH_SIZE = 256
_CACHE_DIR = Path("data/cache/embeddings")

# ── 文档类型推断 ──

def _infer_doc_type(filename: str) -> tuple[str, int]:
    """从文件名推断文档类型和优先级。

    Returns:
        (source_type, priority)
        - debt_circular: priority=2（债券募集说明书，最详细）
        - annual_report:  priority=1（年报/公告）
        - other:          priority=0（其他）
    """
    name_lower = filename.lower()
    # 债券募集说明书
    debt_keywords = ["kcz", "债", "募集", "募集说明书", "发行公告", "bond", "offering"]
    if any(kw in name_lower for kw in debt_keywords):
        return ("debt_circular", 2)
    # 年报
    annual_keywords = ["year", "年报", "年度报告", "annual", "20", "19"]
    if any(kw in name_lower for kw in annual_keywords):
        return ("annual_report", 1)
    return ("other", 0)


# ── Embedding cache ──

def _load_cache(source_file: str) -> dict[str, list[float]]:
    """加载 embedding 缓存文件。"""
    cache_file = _CACHE_DIR / f"{source_file}.json"
    if not cache_file.exists():
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items()}
    except Exception:
        logger.warning("Failed to load embedding cache, ignoring", exc_info=True)
        return {}


def _save_cache(source_file: str, cache: dict[str, list[float]]) -> None:
    """保存 embedding 缓存文件。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"{source_file}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        logger.warning("Failed to save embedding cache", exc_info=True)


def index_pdf(
    chunks: list[TextChunk],
    company_id: str,
    collection: str,   # "company_docs" | "industry_docs"
    db_path: str = "data/chroma",
    source_file: str = "",
    doc_type: str = "",    # "debt_circular" | "annual_report" | "other"，空则自动推断
) -> str:
    """将 PDF 切分后的 text chunks 向量化，存入 ChromaDB 对应 collection。

    新增：
      - 从文件名或 doc_type 参数推断文档类型，写入 metadata
      - section_title / section_level 从 TextChunk 获取，写入 metadata
      - 检索时可利用 priority 字段加权

    Collection 命名规则: {collection}__{company_id}

    Returns:
        collection 名称。
    """
    import chromadb

    if not chunks:
        logger.warning("index_pdf: empty chunks list, nothing to index")
        return f"{collection}__{company_id}"

    # 推断文档类型
    fname = source_file or ""
    inferred_type, priority = _infer_doc_type(fname)
    if doc_type:
        inferred_type = doc_type
        # 根据显式指定的类型设置优先级
        priority_map = {"debt_circular": 2, "annual_report": 1, "other": 0}
        priority = priority_map.get(doc_type, 0)

    logger.info("Document type: %s (priority=%d) for '%s'", inferred_type, priority, fname)

    embedding = get_embedding_model()
    client = chromadb.PersistentClient(path=db_path)
    coll_name = f"{collection}__{company_id}"
    coll = client.get_or_create_collection(
        name=coll_name,
        metadata={"hnsw:space": "cosine"},
    )

    texts = [c.text for c in chunks]
    ids = [
        f"{company_id}__{source_file or 'unknown'}__p{c.page_number}__c{c.chunk_index}"
        for c in chunks
    ]
    metadatas = [
        {
            "page_number": c.page_number,
            "chunk_index": c.chunk_index,
            "source_file": source_file,
            "company_id": company_id,
            "source_type": inferred_type,
            "priority": priority,
            "section_title": c.section_title or "",
            "section_level": c.section_level,
        }
        for c in chunks
    ]

    # ── Embedding cache ──
    cache = _load_cache(source_file) if source_file else {}
    text_hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]

    cached_count = 0
    new_indices: list[int] = []
    for i, t in enumerate(texts):
        if text_hashes[i] in cache:
            cached_count += 1
        else:
            new_indices.append(i)

    final_embeddings: list[list[float] | None] = [None] * len(texts)
    for i, t in enumerate(texts):
        h = text_hashes[i]
        if h in cache:
            final_embeddings[i] = cache[h]

    if new_indices:
        logger.info("Encoding %d new chunks (%d cached, %.0f%% reuse)",
                     len(new_indices), cached_count,
                     cached_count / len(texts) * 100 if texts else 0)

        new_texts = [texts[i] for i in new_indices]
        new_embeddings = embedding.encode(new_texts)

        for idx, emb in zip(new_indices, new_embeddings):
            final_embeddings[idx] = emb
            cache[text_hashes[idx]] = emb
        if source_file:
            _save_cache(source_file, cache)
    else:
        logger.info("All %d chunks served from embedding cache", len(texts))

    embeddings: list[list[float]] = [e for e in final_embeddings]

    # 分批写入 ChromaDB
    for start in range(0, len(texts), _BATCH_SIZE):
        end = min(start + _BATCH_SIZE, len(texts))
        coll.add(
            embeddings=embeddings[start:end],
            documents=texts[start:end],
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )
        logger.debug("Indexed batch %d-%d (%d chunks)", start, end - 1, end - start)

    # 统计 section 分布
    from collections import Counter
    section_counts = Counter(m["section_title"] for m in metadatas if m["section_title"])
    if section_counts:
        logger.info("Top sections indexed: %s",
                    ", ".join(f"{t}({c})" for t, c in section_counts.most_common(5)))

    logger.info("Indexed %d chunks → collection '%s' (type=%s, priority=%d)",
                len(chunks), coll_name, inferred_type, priority)
    return coll_name


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 3:
        print("Usage: python -m retrieval.indexer <pdf_path> <company_id> [collection] [--doc-type TYPE]",
              file=sys.stderr)
        print("  doc-type: debt_circular | annual_report | other (default: auto-detect)",
              file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    company_id = sys.argv[2]
    coll = "company_docs"
    doc_type = ""

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--doc-type" and i + 1 < len(sys.argv):
            doc_type = sys.argv[i + 1]
            i += 2
        elif not sys.argv[i].startswith("--"):
            coll = sys.argv[i]
            i += 1
        else:
            i += 1

    from parsers.pdf_parser import parse as pdf_parse

    result = pdf_parse(pdf_path)
    source = result.metadata.get("source_file", "")
    name = index_pdf(result.chunks, company_id, coll, source_file=source, doc_type=doc_type)
    summary = {
        "collection": name,
        "chunks_indexed": len(result.chunks),
        "page_count": result.page_count,
        "sections_detected": result.metadata.get("sections_detected", 0),
        "top_sections": result.metadata.get("top_sections", []),
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
