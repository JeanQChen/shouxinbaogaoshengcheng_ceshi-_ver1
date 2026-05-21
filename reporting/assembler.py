"""章节 → 完整 Markdown 报告。"""

import re
from reporting.template import ReportSection
from reporting.template import parse_template as _parse_template


def assemble(sections: list[ReportSection], template_path: str, **variables: str) -> str:
    """将各 agent 生成的 ReportSection 按模板填入，返回完整 Markdown 字符串。

    替换模板中的 section 标题行（在标题后插入生成内容，保留原模板结构）。
    未匹配的 section 保留原标题（用于 agent 未实现的占位）。

    variables 用于替换模板中的 {{ key }} 占位符。
    """
    _, template = _parse_template(template_path)

    # 先替换模板变量
    for key, val in variables.items():
        template = template.replace("{{ " + key + " }}", str(val))
        template = template.replace("{{" + key + "}}", str(val))

    lines = template.split("\n")
    result: list[str] = []
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$")
    agent_re = re.compile(r"<!--\s*agent:\s*(\S+)\s*-->")
    comment_re = re.compile(r"<!--\s*(agent|guidance):\s*.*?-->")

    inserted: set[str] = set()

    for line in lines:
        # Skip HTML comment lines (agent/guidance markers) — they're metadata, not content
        if comment_re.search(line):
            # Still trigger content insertion for agent markers
            a_match = agent_re.search(line)
            if a_match:
                heading = None
                for j in range(len(result) - 1, -1, -1):
                    h_match = heading_re.match(result[j])
                    if h_match:
                        heading = h_match.group(2).strip()
                        break

                if heading:
                    content: str | None = None
                    for section in sections:
                        clean_title = re.sub(r"^[一二三四五六七八九十\d]+[.、．]\s*", "", section.title).strip()
                        if clean_title == re.sub(r"^[一二三四五六七八九十\d]+[.、．]\s*", "", heading).strip():
                            if section.section_id not in inserted:
                                content = section.content
                                inserted.add(section.section_id)
                                break

                    if content:
                        result.append("")
                        result.append(content)
                        result.append("")
            continue

        result.append(line)

    return "\n".join(result)


# ── CLI ──

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m reporting.assembler <template_path>", file=sys.stderr)
        sys.exit(1)

    # Demo: parse template and show sections
    sections, full = _parse_template(sys.argv[1])
    print(f"Template: {sys.argv[1]}")
    print(f"Found {len(sections)} sections:\n")
    for s in sections:
        print(f"  [{s.agent_id}] {s.title}")
        if s.guidance:
            print(f"    guidance: {s.guidance}")
