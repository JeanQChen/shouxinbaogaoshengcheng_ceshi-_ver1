"""模板解析 — Markdown 模板 → SectionSpec 列表。

模板中的 HTML 注释语法：
  <!-- agent: xxx -->    声明该 section 由哪个 agent 负责填充
  <!-- guidance: xxx --> 注入到 agent prompt 的约束文字
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SectionSpec:
    section_id: str         # 如 "financial.solvency"
    title: str              # 如 "偿债能力分析"
    agent_id: str           # 如 "financial"
    guidance: str | None = None  # 从模板注释里提取的约束文字


@dataclass
class Citation:
    source_type: str   # "sqlite" | "chromadb" | "web_search" | "akshare"
    source_ref: str    # "balance_sheet.id=42" 或 "chunk_id=xxx"
    snippet: str       # 原文片段（用于可解释性展示）


@dataclass
class ReportSection:
    section_id: str
    title: str
    content: str                  # Markdown 文本
    citations: list[Citation]     # 文中数字/论断的出处
    generated_by: str             # agent 标识
    generated_at: datetime = None  # type: ignore

    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now()


def parse_template(template_path: str) -> tuple[list[SectionSpec], str]:
    """解析 Markdown 模板文件。

    Returns:
        sections: 按顺序排列的 SectionSpec 列表
        full_template: 原始模板全文（用于后续组装时保留结构）
    """
    raise NotImplementedError
