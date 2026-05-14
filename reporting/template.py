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

    提取 <!-- agent: xxx --> 和 <!-- guidance: xxx --> 注释，
    关联到最近的标题，生成 SectionSpec 列表。

    Returns:
        sections: 按顺序排列的 SectionSpec 列表
        full_template: 原始模板全文（用于后续组装时保留结构）
    """
    import re
    from pathlib import Path

    text = Path(template_path).read_text(encoding="utf-8")
    sections: list[SectionSpec] = []
    current_heading: str | None = None
    current_level = 0
    seen_section_ids: dict[str, int] = {}  # section_id → counter for dedup

    agent_re = re.compile(r"<!--\s*agent:\s*(\S+)\s*-->")
    guidance_re = re.compile(r"<!--\s*guidance:\s*(.+?)\s*-->")
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

    lines = text.split("\n")
    for line in lines:
        # 检测标题
        h_match = heading_re.match(line)
        if h_match:
            current_level = len(h_match.group(1))
            current_heading = h_match.group(2).strip()
            continue

        # 检测 agent 注释
        a_match = agent_re.search(line)
        if a_match and current_heading:
            agent_id = a_match.group(1)
            # 生成稳定的 section_id
            base_id = current_heading.replace(" ", "_").replace("（", "").replace("）", "")
            counter = seen_section_ids.get(base_id, 0)
            section_id = f"{agent_id}.{base_id}" if counter == 0 else f"{agent_id}.{base_id}_{counter}"
            seen_section_ids[base_id] = counter + 1

            # 检查下一行是否有 guidance
            guidance: str | None = None
            g_match = guidance_re.search(line)
            if g_match:
                guidance = g_match.group(1).strip()

            sections.append(SectionSpec(
                section_id=section_id,
                title=current_heading,
                agent_id=agent_id,
                guidance=guidance,
            ))
            continue

        # 单独 guidance 行（不和 agent 同行）
        g_match = guidance_re.search(line)
        if g_match and sections:
            sections[-1].guidance = g_match.group(1).strip()

    return sections, text
