"""PDF chunks → ChromaDB 向量化入库，含 embedding 缓存加速重复索引。"""

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
) -> str:
    """将 PDF 切分后的 text chunks 向量化，存入 ChromaDB 对应 collection。

    Embedding 缓存在 data/cache/embeddings/{source_file}.json，
    重复索引时只编码新文本，大幅加速。

    Collection 命名规则: {collection}__{company_id}

    Returns:
        collection 名称。
    """
    import chromadb

    if not chunks:
        logger.warning("index_pdf: empty chunks list, nothing to index")
        return f"{collection}__{company_id}"

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
        }
        for c in chunks
    ]

    # ── Embedding cache: hash(text) → vector ──
    cache = _load_cache(source_file) if source_file else {}
    text_hashes = [hashlib.sha256(t.encode()).hexdigest() for t in texts]

    cached_count = 0
    new_indices: list[int] = []
    for i, t in enumerate(texts):
        if text_hashes[i] in cache:
            cached_count += 1
        else:
            new_indices.append(i)

    # 按原索引构建完整 embeddings 列表
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

    embeddings: list[list[float]] = [e for e in final_embeddings]  # type narrowing

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

    logger.info("Indexed %d chunks → collection '%s'", len(chunks), coll_name)
    return coll_name


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 3:
        print("Usage: python -m retrieval.indexer <pdf_path> <company_id> [collection]",
              file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    company_id = sys.argv[2]
    coll = sys.argv[3] if len(sys.argv) > 3 else "company_docs"

    from parsers.pdf_parser import parse as pdf_parse

    result = pdf_parse(pdf_path)
    source = result.metadata.get("source_file", "")
    name = index_pdf(result.chunks, company_id, coll, source_file=source)
    summary = {
        "collection": name,
        "chunks_indexed": len(result.chunks),
        "page_count": result.page_count,
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
