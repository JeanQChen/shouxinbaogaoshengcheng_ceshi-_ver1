"""PDF chunks → ChromaDB 向量化入库。"""

from parsers.pdf_parser import TextChunk


def index_pdf(
    chunks: list[TextChunk],
    company_id: str,
    collection: str,  # "company_docs" | "industry_docs"
    db_path: str,
) -> str:
    """将 PDF 切分后的 text chunks 向量化，存入 ChromaDB 对应 collection。

    Collection 命名规则: {collection}__{company_id}
    """
    raise NotImplementedError
