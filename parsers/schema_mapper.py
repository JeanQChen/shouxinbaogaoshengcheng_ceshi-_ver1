"""DataFrame + LLM → 标准 schema 映射。"""

from dataclasses import dataclass, field
from parsers.excel_parser import ExcelParseResult


@dataclass
class NormalizedRow:
    """标准化后的单行财务数据。"""
    item_code: str         # 标准化科目代码，如 TOTAL_ASSETS
    item_name_cn: str      # 原始中文科目名
    amount: float          # 金额（元）
    category: str | None = None       # asset / liability / equity / revenue / cost / profit
    activity_type: str | None = None  # operating / investing / financing（现金流量表专用）


@dataclass
class SchemaMapResult:
    report_meta: dict                # 对应 report_meta 表字段
    rows: list[NormalizedRow]
    unmapped_items: list[str]        # 未能映射的科目，需人工 review


def map_to_schema(parsed: ExcelParseResult, company_id: str) -> SchemaMapResult:
    """将 Excel 解析结果映射到标准化 schema，包括 LLM 辅助的科目匹配。"""
    raise NotImplementedError
