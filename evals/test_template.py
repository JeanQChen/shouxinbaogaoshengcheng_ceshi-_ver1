"""Eval: 模板解析。

用法: python -m evals.test_template
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.cases.config import VALID_TEMPLATE_AGENTS
from reporting.template import parse_template


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    for template_name in ["standard", "simple"]:
        template_path = f"templates/{template_name}.md"
        tpl_file = Path(template_path)
        if not tpl_file.exists():
            skipped += 1
            details.append(f"SKIP: Template file '{template_path}' not found")
            continue

        sections, full_text = parse_template(template_path)

        check(len(sections) >= 4,
              f"[{template_name}] at least 4 sections parsed (got {len(sections)})")

        check(len(full_text) > 0,
              f"[{template_name}] full template text returned (length={len(full_text)})")

        agent_ids = {s.agent_id for s in sections}
        unknown = agent_ids - VALID_TEMPLATE_AGENTS
        check(len(unknown) == 0,
              f"[{template_name}] all agent_ids are valid (unknown: {unknown})")

        section_ids = [s.section_id for s in sections]
        dupes = [sid for sid in section_ids if section_ids.count(sid) > 1]
        check(len(dupes) == 0,
              f"[{template_name}] no duplicate section_ids (dupes: {set(dupes)})")

        has_financial = any(s.agent_id == "financial" for s in sections)
        check(has_financial,
              f"[{template_name}] has a 'financial' agent section")

        has_guidance = any(s.guidance for s in sections if s.guidance)
        check(has_guidance,
              f"[{template_name}] at least one section has guidance")

    # Test missing file
    try:
        parse_template("nonexistent_template_12345.md")
        check(False, "parse_template(nonexistent) should raise")
    except FileNotFoundError:
        check(True, "parse_template(nonexistent) raises FileNotFoundError")
    except Exception as e:
        check(True, f"parse_template(nonexistent) raises {type(e).__name__}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
