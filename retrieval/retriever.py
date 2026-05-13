"""query → top-k chunks，含强制日志落盘。"""

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    text: str
    page_number: int
    chunk_index: int
    source_file: str
    score: float


def retrieve(
    company_id: str,
    collection: str,  # "company_docs" | "industry_docs"
    query: str,
    k: int = 5,
) -> list[RetrievedChunk]:
    """从 ChromaDB 检索 top-k chunks。

    强制行为：每次调用必须把 {query, retrieved chunks, scores, timestamp}
    落盘到 logs/retrieval/<timestamp>__<query_hash>.jsonl。
    """
    raise NotImplementedError
