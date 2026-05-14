"""PDF chunks → ChromaDB 向量化入库。"""

import logging

from parsers.pdf_parser import TextChunk
from retrieval.embedding import get_embedding_model

logger = logging.getLogger(__name__)

# Max batch size for embedding.encode to avoid OOM
_BATCH_SIZE = 256


def index_pdf(
    chunks: list[TextChunk],
    company_id: str,
    collection: str,   # "company_docs" | "industry_docs"
    db_path: str = "data/chroma",
    source_file: str = "",
) -> str:
    """将 PDF 切分后的 text chunks 向量化，存入 ChromaDB 对应 collection。

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

    # 分批编码，避免长文本 OOM
    for start in range(0, len(texts), _BATCH_SIZE):
        end = min(start + _BATCH_SIZE, len(texts))
        batch_texts = texts[start:end]
        batch_embeddings = embedding.encode(batch_texts)
        coll.add(
            embeddings=batch_embeddings,
            documents=batch_texts,
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )
        logger.debug("Indexed batch %d-%d (%d chunks)", start, end - 1, len(batch_texts))

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
