"""章节 → 完整 Markdown 报告。"""

from reporting.template import ReportSection


def assemble(sections: list[ReportSection], template_path: str) -> str:
    """将各 agent 生成的 ReportSection 按模板填入，返回完整 Markdown 字符串。"""
    raise NotImplementedError
