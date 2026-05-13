"""SQLite 连接 + CRUD。"""

from parsers.schema_mapper import NormalizedRow


def init_db(db_path: str) -> None:
    """初始化 SQLite 数据库，创建全部表。"""
    raise NotImplementedError


def insert_report(meta: dict, rows: list[NormalizedRow]) -> str:
    """插入一份报告的元信息和全部标准化行，返回 report_id。"""
    raise NotImplementedError


def query_metric(company_id: str, item_code: str, period: str) -> float | None:
    """查询某公司某报告期某科目的金额。"""
    raise NotImplementedError


def list_periods(company_id: str) -> list[str]:
    """列出某公司已入库的所有报告期，按时间排序。"""
    raise NotImplementedError
