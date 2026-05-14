"""SQLite 连接 + CRUD。"""

import math
import sqlite3
import uuid
from pathlib import Path
from parsers.schema_mapper import NormalizedRow
from financial.schema import build_all_ddl, table_for_row, ALL_STATEMENT_TABLES

# 模块级，由 init_db() 设置，后续操作复用本路径。
_db_path: Path | None = None
DEFAULT_DB_PATH = Path("data/credit.db")


def _get_conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """初始化 SQLite 数据库，创建全部表。"""
    global _db_path
    _db_path = Path(db_path)
    conn = _get_conn()
    try:
        ddl = build_all_ddl()
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()


def insert_report(meta: dict, rows: list[NormalizedRow]) -> str:
    """插入报告的元信息和全部标准化行。

    多期间数据：按 row.period 分组，每组生成一个 report_meta，
    返回主 report_id（第一个）。
    """
    # 按期间分组
    by_period: dict[str, list[NormalizedRow]] = {}
    for row in rows:
        by_period.setdefault(row.period, []).append(row)

    conn = _get_conn()
    try:
        primary_id = ""
        insert_row_sql = (
            "INSERT INTO {table} (report_id, item_code, item_name_cn, amount, category, activity_type) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )

        for period, period_rows in by_period.items():
            report_id = uuid.uuid4().hex[:12]
            if not primary_id:
                primary_id = report_id

            conn.execute(
                "INSERT INTO report_meta (id, company_id, company_name, stock_code, "
                "report_period, report_type, statement_scope, source_file) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    meta.get("company_id", ""),
                    meta.get("company_name", ""),
                    meta.get("stock_code", ""),
                    period,
                    meta.get("report_type", "annual"),
                    meta.get("statement_scope", "consolidated"),
                    meta.get("source_file", ""),
                ),
            )

            for row in period_rows:
                if math.isnan(row.amount) or math.isinf(row.amount):
                    continue
                table = table_for_row(row.category, row.activity_type)
                conn.execute(
                    insert_row_sql.format(table=table),
                    (report_id, row.item_code, row.item_name_cn, row.amount, row.category, row.activity_type),
                )

        conn.commit()
        return primary_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_metric(company_id: str, item_code: str, period: str) -> float | None:
    """查询某公司某报告期某科目的金额。

    item_code 可为英文代码（如 TOTAL_ASSETS）或中文名（如 资产总计），
    跨三张表搜索，匹配 item_code 和 item_name_cn 两列。
    多条匹配时取合计值。
    """
    conn = _get_conn()
    try:
        total = 0.0
        found = False
        for table in ALL_STATEMENT_TABLES:
            rows = conn.execute(
                f"SELECT fs.amount FROM {table} fs "
                "JOIN report_meta rm ON fs.report_id = rm.id "
                "WHERE rm.company_id = ? AND (fs.item_code = ? OR fs.item_name_cn = ?) AND rm.report_period = ?",
                (company_id, item_code, item_code, period),
            ).fetchall()
            for r in rows:
                total += r["amount"]
                found = True
        return total if found else None
    finally:
        conn.close()


def list_periods(company_id: str) -> list[str]:
    """列出某公司已入库的所有报告期，按日期排序。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT report_period FROM report_meta "
            "WHERE company_id = ? ORDER BY report_period",
            (company_id,),
        ).fetchall()
        return [r["report_period"] for r in rows]
    finally:
        conn.close()


def get_company_info(company_id: str) -> dict[str, str]:
    """获取公司基本信息（名称、代码、最新报告期）。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT company_name, stock_code, report_period FROM report_meta "
            "WHERE company_id = ? ORDER BY report_period DESC LIMIT 1",
            (company_id,),
        ).fetchone()
        if row:
            return {
                "company_name": row["company_name"] or "",
                "stock_code": row["stock_code"] or company_id,
                "report_period": row["report_period"] or "",
            }
        return {"company_name": "", "stock_code": company_id, "report_period": ""}
    finally:
        conn.close()
