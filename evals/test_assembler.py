"""Eval: 报告组装器。

用法: python -m evals.test_assembler
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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

    from reporting.template import ReportSection
    from reporting.assembler import assemble

    # Create a minimal template with variables and agent comments
    template_content = """# 授信报告：{{ company_name }}

## 公司概况
<!-- agent: financial -->
<!-- guidance: 分析公司概况 -->

这是公司概况的模板文字。
"""

    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="eval_tpl_")
    os.close(fd)
    Path(tmp_path).write_text(template_content, encoding="utf-8")

    try:
        sections = [
            ReportSection(
                section_id="financial.公司概况",
                title="公司概况",
                content="这是生成的公司概况内容。资产总计 1000 亿元。",
                citations=[],
                generated_by="financial_analyzer",
            ),
        ]

        result = assemble(sections, tmp_path, company_name="宁德时代")

        check(len(result) > 0,
              f"assemble returns non-empty string (length={len(result)})")

        check("宁德时代" in result,
              f"Variable {{ company_name }} replaced: '宁德时代' found")

        check("{{ company_name }}" not in result,
              "No leftover template placeholder")

        check("这是生成的公司概况内容" in result,
              "Section content inserted into template")

        check("这是公司概况的模板文字" in result,
              "Original template text preserved")

        # ── No duplicate insertion ──
        # Count occurrences of the section content
        count = result.count("这是生成的公司概况内容。资产总计 1000 亿元。")
        check(count == 1,
              f"Section content appears exactly once (got {count})")

        # ── assemble with missing sections ──
        result_missing = assemble([], tmp_path, company_name="测试")
        check(len(result_missing) > 0,
              "assemble with no sections still returns template")

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # ── Test with real template ──
    for tpl_name in ["standard", "simple"]:
        tpl_path = f"templates/{tpl_name}.md"
        if not Path(tpl_path).exists():
            skipped += 1
            details.append(f"SKIP: Template '{tpl_path}' not found")
            continue

        sections = [
            ReportSection(
                section_id=f"financial.{tpl_name}_test",
                title="测试章节",
                content=f"这是 {tpl_name} 模板的测试生成内容。",
                citations=[],
                generated_by="financial_analyzer",
            ),
        ]

        result = assemble(sections, tpl_path, company_name="宁德时代", stock_code="300750",
                          report_period="2025-12-31", generated_at="2026-05-14")

        check(len(result) > 0,
              f"[{tpl_name}] assemble returns non-empty string")

        check("宁德时代" in result,
              f"[{tpl_name}] company_name substituted")

        check("300750" in result,
              f"[{tpl_name}] stock_code substituted")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
