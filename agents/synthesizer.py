"""Agent 5: 综合 — 按模板组装三个章节为全文 Markdown。

LLM 调用应尽量轻——主要是衔接、过渡、保持口径一致，不重写各章节内容。
"""

from reporting.template import ReportSection


def run(sections: list[ReportSection], template_path: str) -> str:
    """组装完整报告。

    1. 读模板，按结构填空
    2. LLM 轻量衔接：标题过渡、口径统一、前后不一致的标注
    3. 返回完整 Markdown
    """
    raise NotImplementedError
