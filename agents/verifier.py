"""Agent 6: 回检 — 三类规则比对。

规则：
  yellow (数值): 指标差异 > 5%，需复核
  red   (实体): 公司名/代码/人名等实体与公开数据源不一致
  orange(时效): 引用的数据或报告已过时
"""

from dataclasses import dataclass, field


@dataclass
class Issue:
    severity: str        # "yellow" | "red" | "orange"
    rule: str
    location: str        # 在原文中的位置
    detail: str
    evidence: list[str]  # 公开数据源的对比依据


@dataclass
class VerificationResult:
    annotated_markdown: str      # 加了标注的 markdown
    issues: list[Issue] = field(default_factory=list)


def run(report_markdown: str, company_id: str) -> VerificationResult:
    """对报告全文执行三类回检规则，返回带标注的 markdown 和问题列表。"""
    raise NotImplementedError
