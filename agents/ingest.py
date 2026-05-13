"""Agent 1: 解析入库 — 编排 parsers + indexer。

Excel → SQLite（结构化财务数据）
PDF  → ChromaDB（向量化文本）
"""


def run(
    company_id: str,
    excel_paths: list[str],
    pdf_paths: list[str],
    company_name: str = "",
) -> str:
    """执行解析入库流程，返回 report_id。

    1. Excel: excel_parser → schema_mapper → db.insert_report
    2. PDF:  pdf_parser → indexer.index_pdf
    """
    raise NotImplementedError
