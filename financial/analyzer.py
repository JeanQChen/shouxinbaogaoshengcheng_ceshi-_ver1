"""Agent 2: 财务分析。

流程：SQL 取数 → Python 算指标 → LLM 写解读文字。
LLM 不计算任何数字，所有指标提前算好喂入 prompt。
"""

from reporting.template import SectionSpec, ReportSection


def run(company_id: str, section_spec: SectionSpec) -> ReportSection:
    """执行财务分析 agent。

    1. 调用 financial.metrics.compute_all 拿到指标表
    2. 把指标表 + guidance 喂给 LLM
    3. LLM 只写解读文字，不算新指标
    """
    raise NotImplementedError
