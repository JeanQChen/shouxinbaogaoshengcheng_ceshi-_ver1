"""财务指标计算函数（纯 Python，不含 LLM）。"""

from dataclasses import dataclass, field


@dataclass
class MetricsTable:
    by_period: dict[str, dict[str, float]]        # period → {metric_name: value}
    yoy_changes: dict[str, dict[str, float]]      # period → {metric_name: yoy_pct}


def compute_all(company_id: str, periods: list[str]) -> MetricsTable:
    """计算全部核心指标，返回各期值和同比增长率。

    必须包含：流动比率、速动比率、资产负债率、利息保障倍数、
             毛利率、净利率、ROE、ROA、营收增长率、净利增长率、
             经营现金流/净利润。
    """
    raise NotImplementedError
