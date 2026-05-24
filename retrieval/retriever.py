"""query → top-k chunks，含文档优先级加权 + 多查询合并 + 强制日志落盘。"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from retrieval.embedding import get_embedding_model

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs/retrieval")

# 文档类型优先级加权系数
_PRIORITY_BOOST: dict[str, float] = {
    "debt_circular": 1.25,   # 债券募集说明书最详细，加权最高
    "annual_report": 1.05,   # 年报轻微加权
    "other": 0.95,           # 其他稍减
}


@dataclass
class RetrievedChunk:
    text: str
    page_number: int
    chunk_index: int
    source_file: str
    source_type: str = ""     # debt_circular / annual_report / other
    section_title: str = ""   # 节段标题
    score: float = 0.0


def _apply_priority_boost(
    chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """按文档类型对检索结果加权重排。

    debt_circular × 1.25, annual_report × 1.05, other × 0.95。
    """
    for c in chunks:
        boost = _PRIORITY_BOOST.get(c.source_type, 1.0)
        c.score = round(c.score * boost, 6)
    chunks.sort(key=lambda x: x.score, reverse=True)
    return chunks


def retrieve(
    company_id: str,
    collection: str,   # "company_docs" | "industry_docs"
    query: str,
    k: int = 5,
    db_path: str = "data/chroma",
) -> list[RetrievedChunk]:
    """从 ChromaDB 检索 top-k chunks（含文档优先级加权）。

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

    # 多取一些，重排后可能有些被压低
    fetch_k = min(k * 2, 20)
    results = coll.query(query_embeddings=[query_embedding], n_results=fetch_k)

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
            source_type=meta.get("source_type", "other"),
            section_title=meta.get("section_title", ""),
            score=round(1.0 - distance, 6),
        ))

    # 按文档类型加权重排
    chunks = _apply_priority_boost(chunks)

    # 截取 top-k
    chunks = chunks[:k]

    _log_retrieval(coll_name, query, chunks, k)

    logger.info("Retrieved %d chunks from '%s' for query: %.60s...",
                len(chunks), coll_name, query)

    return chunks


def retrieve_multi(
    company_id: str,
    collection: str,
    queries: list[str],
    k_per_query: int = 5,
    max_total: int = 30,
    db_path: str = "data/chroma",
) -> list[RetrievedChunk]:
    """多角度查询合并检索。

    对同一信息需求从多个角度查询，合并去重，按加权分数排列。

    Args:
        queries: 多个查询角度（如 ["营收构成 分产品", "收入结构 分地区", "主营业务 毛利率"]）
        k_per_query: 每个 query 的检索数量
        max_total: 最终返回的最大 chunk 数

    Returns:
        去重后按加权分数排列的 chunk 列表。
    """
    all_chunks: list[RetrievedChunk] = []
    seen: set[tuple[int, int, str]] = set()

    for query in queries:
        try:
            chunks = retrieve(company_id, collection, query, k=k_per_query, db_path=db_path)
        except ValueError as e:
            logger.warning("retrieve_multi: query '%.40s' failed: %s", query, e)
            continue

        for c in chunks:
            key = (c.page_number, c.chunk_index, c.source_file)
            if key not in seen:
                seen.add(key)
                all_chunks.append(c)

    # 按加权分数排列
    all_chunks.sort(key=lambda x: x.score, reverse=True)

    result = all_chunks[:max_total]
    logger.info("retrieve_multi: %d queries → %d unique chunks (returning top %d)",
                len(queries), len(all_chunks), len(result))

    return result


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
                    "text": c.text[:200],
                    "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "source_file": c.source_file,
                    "source_type": c.source_type,
                    "section": c.section_title[:60] if c.section_title else "",
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
        print("       python -m retrieval.retriever <company_id> <collection> --multi <q1> <q2> ...",
              file=sys.stderr)
        sys.exit(1)

    company_id = sys.argv[1]
    collection = sys.argv[2]

    if "--multi" in sys.argv:
        multi_idx = sys.argv.index("--multi")
        queries = sys.argv[multi_idx + 1:]
        if not queries:
            print("Error: --multi requires at least one query", file=sys.stderr)
            sys.exit(1)
        results = retrieve_multi(company_id, collection, queries)
    else:
        query = sys.argv[3]
        k = int(sys.argv[4]) if len(sys.argv) > 4 else 5
        results = retrieve(company_id, collection, query, k=k)

    for i, chunk in enumerate(results):
        section_info = f", section='{chunk.section_title[:40]}'" if chunk.section_title else ""
        print(f"\n--- Result {i+1} (score={chunk.score:.4f}, "
              f"type={chunk.source_type}, page={chunk.page_number}{section_info}) ---")
        print(chunk.text[:500])
