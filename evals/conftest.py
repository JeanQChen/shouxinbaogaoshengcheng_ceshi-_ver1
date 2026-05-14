"""共享 fixtures: 内存 DB 种子、mock LLM。"""

import uuid
from pathlib import Path
from unittest.mock import patch


def mock_chat_factory(responses: dict[str, str] | None = None):
    """返回一个 mock 版本的 llm.client.chat。

    responses: {substring_in_prompt: response_text}
    匹配时用 substring 检测，未匹配返回默认文字。
    """
    responses = responses or {}

    def _mock_chat(messages, system=None, model=None, max_tokens=4096):
        prompt = messages[0]["content"] if messages else ""
        for key, value in responses.items():
            if key in prompt:
                return value
        return "（Mock LLM 未匹配到预设回复）"

    return _mock_chat


def seed_in_memory_db(company_id: str = "300750"):
    """解析样本 Excel 文件并全部写入内存 SQLite 库，返回 db_path。

    返回 Path(":memory:") 的语义无法跨连接共享，因此改为临时文件方案：
    写入一个临时 .db 文件，返回路径。
    """
    import os
    import tempfile

    from financial.db import init_db
    from parsers.excel_parser import parse as excel_parse
    from parsers.schema_mapper import map_to_schema

    sample_dir = Path("data/samples") / company_id / "financial"

    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="eval_")
    os.close(fd)

    init_db(tmp_path)

    files = {
        "NDSD_BALANCESHEET_2023-2026Q1.xlsx": False,
        "NDSD_EFFORT_2023-2026Q1.xlsx": False,
        "NDSD_CASH_2023-2026Q1.xlsx": False,
    }
    all_rows = []
    for fname in files:
        fpath = sample_dir / fname
        if not fpath.exists():
            continue
        parsed = excel_parse(str(fpath))
        result = map_to_schema(parsed, company_id)
        all_rows.extend(result.rows)
        files[fname] = True

    # 按期间分批插入（模拟 insert_report 的分组逻辑）
    import sqlite3 as _sqlite3
    from financial.db import table_for_row
    import math as _math

    by_period: dict[str, list] = {}
    for row in all_rows:
        by_period.setdefault(row.period, []).append(row)

    insert_row_sql = (
        "INSERT INTO {table} (report_id, item_code, item_name_cn, amount, category, activity_type) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )

    conn = _sqlite3.connect(tmp_path)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for period, period_rows in by_period.items():
            report_id = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO report_meta (id, company_id, company_name, stock_code, "
                "report_period, report_type, statement_scope, source_file) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, company_id, "宁德时代", company_id, period, "annual", "consolidated", ""),
            )
            for row in period_rows:
                if _math.isnan(row.amount) or _math.isinf(row.amount):
                    continue
                table = table_for_row(row.category, row.activity_type)
                conn.execute(
                    insert_row_sql.format(table=table),
                    (report_id, row.item_code, row.item_name_cn, row.amount, row.category, row.activity_type),
                )
        conn.commit()
    finally:
        conn.close()

    return tmp_path, sum(files.values())


def setup_mock_llm():
    """在 os.environ 打标，告知测试模块用 mock LLM。"""
    import os
    os.environ["EVAL_MOCK_LLM"] = "true"


def get_api_key() -> str:
    import os
    return os.getenv("DEEPSEEK_API_KEY", "")
