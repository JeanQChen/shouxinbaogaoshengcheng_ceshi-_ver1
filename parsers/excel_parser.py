"""Excel → pandas DataFrame，检测报告期和合并范围。"""

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class ExcelParseResult:
    sheets: dict[str, pd.DataFrame]
    detected_period: str | None = None       # 推断的报告期，如 "2024-12-31"
    detected_scope: str | None = None        # "consolidated" | "parent"
    metadata: dict = field(default_factory=dict)


def parse(file_path: str) -> ExcelParseResult:
    """解析财务 Excel 文件，返回所有 sheet 的 DataFrame 及元信息。"""
    raise NotImplementedError
