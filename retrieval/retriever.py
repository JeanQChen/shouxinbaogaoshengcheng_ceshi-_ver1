"""query → top-k chunks，含强制日志落盘。"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from retrieval.embedding import get_embedding_model

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs/retrieval")


@dataclass
class RetrievedChunk:
    text: str
    page_number: int
    chunk_index: int
    source_file: str
    score: float


def retrieve(
    company_id: str,
    collection: str,   # "company_docs" | "industry_docs"
    query: str,
    k: int = 5,
    db_path: str = "data/chroma",
) -> list[RetrievedChunk]:
    """从 ChromaDB 检索 top-k chunks。

    强制行为：每次调用必须把 {query, retrieved chunks, scores, timestamp}
    落盘到 logs/retrieval/<timestamp>__<query_hash>.jsonl。
    """
    import chromadb

    if not query or not query.strip():
        raise ValueError("Query must be a non-empty string")

    embedding = get_embedding_model()
    client = chromadb.PersistentClient(path=db_path)
    coll_name = f"{collection}__{company_id}"

    try:
        coll = client.get_collection(name=coll_name)
    except Exception as e:
        raise ValueError(f"Collection not found: {coll_name}") from e

    query_embedding = embedding.encode([query])[0]

    results = coll.query(query_embeddings=[query_embedding], n_results=k)

    chunks: list[RetrievedChunk] = []
    ids_list = results.get("ids", [[]])[0]
    docs_list = results.get("documents", [[]])[0]
    metas_list = results.get("metadatas", [[]])[0]
    dists_list = results.get("distances", [[]])[0]

    for i in range(len(ids_list)):
        meta = metas_list[i] if i < len(metas_list) else {}
        distance = dists_list[i] if i < len(dists_list) else 0.0
        chunks.append(RetrievedChunk(
            text=docs_list[i] if i < len(docs_list) else "",
            page_number=meta.get("page_number", 0),
            chunk_index=meta.get("chunk_index", 0),
            source_file=meta.get("source_file", ""),
            score=round(1.0 - distance, 6),  # cosine distance → similarity
        ))

    _log_retrieval(coll_name, query, chunks, k)

    logger.info("Retrieved %d chunks from '%s' for query: %.60s...",
                len(chunks), coll_name, query)

    return chunks


def _log_retrieval(
    collection_name: str,
    query: str,
    chunks: list[RetrievedChunk],
    k: int,
) -> None:
    """落盘检索日志到 logs/retrieval/。每次调用一条 JSONL。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:12]
        record = {
            "timestamp": ts,
            "collection": collection_name,
            "query": query,
            "k_requested": k,
            "results": [
                {
                    "text": c.text[:200],  # 截断，日志用
                    "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "source_file": c.source_file,
                    "score": c.score,
                }
                for c in chunks
            ],
        }
        filepath = LOGS_DIR / f"{ts}__{query_hash}.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write retrieval log", exc_info=True)


# ── CLI ──

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 4:
        print("Usage: python -m retrieval.retriever <company_id> <collection> <query> [k]",
              file=sys.stderr)
        sys.exit(1)

    company_id = sys.argv[1]
    collection = sys.argv[2]
    query = sys.argv[3]
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    results = retrieve(company_id, collection, query, k=k)
    for i, chunk in enumerate(results):
        print(f"\n--- Result {i+1} (score={chunk.score:.4f}, "
              f"page={chunk.page_number}, chunk={chunk.chunk_index}) ---")
        print(chunk.text[:500])
