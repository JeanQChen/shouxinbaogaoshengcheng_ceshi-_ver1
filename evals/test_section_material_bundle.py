"""Eval: sections 通用主题材料包（B1 TopicEvidenceBundle）。

用法: python -m evals.test_section_material_bundle

覆盖（§13.1 / §16 B）：
- expand_table_context：同 document_version + section_path 的相邻后续块接续；
  新表题/不同文档/不同 section/顺序在 seed 之前 → 不并入（不盲拼不同表）；
  截断标志由 seed 透传（诚实声明，纯函数不自推断）。
- assemble_topic_bundle + coverage_summary：核心/补充覆盖矩阵；主题名出现 ≠ 覆盖完成
  （有未覆盖核心字段 → core_complete=False）。

全部纯函数（无 I/O / LLM / DB），合成 fixture（宁德时代等名只作 case fixture）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import schema as PS
from sections.material_bundle import (
    CoverageRequirement,
    MaterialEntry,
    assemble_topic_bundle,
    coverage_summary,
    expand_table_context,
)


def _seed() -> dict:
    return {
        "table_id": "t5-11", "table_title": "表5-11 主营业务成本构成表",
        "headers": ("项目", "2024年", "2025年"), "unit": "万元",
        "period": "2025-12-31", "currency": "CNY",
        "row_column_source": "P50 表5-11", "document_version": "dv1",
        "section_path": "营业成本", "order": 1, "page": 50,
        "text": "动力电池  19,246,128.2  24,106,439.7",
    }


def _question() -> PS.PlannedQuestion:
    return PS.PlannedQuestion(
        question_id="q1", question="主营业务成本构成？", priority="P0",
        topic_id="business", required_aspects=("分板块成本", "期间"),
        evidence_requirements=(), calculation_requirements=(),
        analysis_requirements=(), missing_policy="write_not_found",
        blocking_policy=(), impact_scope=())


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- expand_table_context：跨页接续 ----
    seed = _seed()
    cont_same = {"document_version": "dv1", "section_path": "营业成本",
                 "order": 2, "page": 51, "text": "储能电池  8,000,000.0  9,500,000.0"}
    new_table = {"document_version": "dv1", "section_path": "营业成本",
                 "order": 3, "page": 52, "text": "表5-12 期间费用构成表\n销售费用  1,000,000"}
    diff_doc = {"document_version": "dv2", "section_path": "营业成本",
                "order": 4, "page": 53, "text": "储能电池  8,000,000.0  9,500,000.0"}
    diff_section = {"document_version": "dv1", "section_path": "营业收入",
                    "order": 5, "page": 54, "text": "储能电池  8,000,000.0  9,500,000.0"}
    before_seed = {"document_version": "dv1", "section_path": "营业成本",
                   "order": 0, "page": 49, "text": "上一表残行  1,000"}

    merged = expand_table_context(
        seed, [cont_same, new_table, diff_doc, diff_section, before_seed])
    check(len(merged.rows) == 2, "B1：同表相邻块接续（seed + 1 行 = 2 行）")
    check("储能电池" in "".join(r[0] for r in merged.rows),
          "B1：接续块内容并入表体")
    check("期间费用构成表" not in "".join(r[0] for r in merged.rows),
          "B1：新表题块不并入（不盲拼不同表）")
    check(not merged.truncated, "B1：正确排除不同表 ≠ 截断（truncated=False）")
    check(merged.continued_from == "t5-11", "B1：跨块接续记录 continued_from 来源")
    check(merged.table_title == "表5-11 主营业务成本构成表"
          and merged.unit == "万元" and merged.period == "2025-12-31",
          "B1：接续保留表题/单位/期间")

    single = expand_table_context(seed, [new_table])
    check(len(single.rows) == 1, "B1：仅新表候选 → 不接续（仅 seed 1 行）")

    # 截断透传。
    seed_trunc = dict(seed, truncated=True)
    check(expand_table_context(seed_trunc, [cont_same]).truncated is True,
          "B1：seed 截断标志透传到结果（诚实声明，不自推断）")

    # ---- assemble_topic_bundle + coverage_summary ----
    q = _question()
    bundle = assemble_topic_bundle(
        "business", "主营业务", [q],
        [CoverageRequirement(field_id="seg_cost", field_label="分板块成本",
                             kind="core", covered=True, source_ref="claim_x"),
         CoverageRequirement(field_id="seg_revenue", field_label="分板块收入",
                             kind="core", covered=False),
         CoverageRequirement(field_id="seg_margin", field_label="分板块毛利",
                             kind="supplementary", covered=False)],
        subject="宁德时代", report_period="2025-12-31",
        materials=[MaterialEntry(
            entry_id="m1", kind="evidence", citation_key="evidence:e1",
            content_text="动力电池成本 19,246,128.2", content_hash="h1",
            document_version="dv1", page=50, section_path="营业成本",
            table=merged, unread_range=None)],
        verified_facts=["claim_x"], stop_reason="COMPLETED")

    check(bundle.topic_id == "business" and len(bundle.sub_questions) == 1,
          "B1：主题材料包携带 topic + 子问题契约")
    check(bundle.subject == "宁德时代" and bundle.report_period == "2025-12-31",
          "B1：主题材料包携带主体/报告期")
    check(len(bundle.materials) == 1 and bundle.materials[0].table is merged,
          "B1：材料条目携带表格上下文")

    summary = coverage_summary(bundle)
    check(summary["core_total"] == 2 and summary["core_covered"] == 1
          and summary["core_complete"] is False,
          "B1：有未覆盖核心字段 → core_complete=False（主题名出现 ≠ 覆盖完成）")
    check(summary["supplementary_total"] == 1 and summary["supplementary_covered"] == 0,
          "B1：补充字段单独计数，不影响核心完整性判定")

    # 核心字段全部覆盖 → core_complete=True。
    full = assemble_topic_bundle(
        "business", "主营业务", [q],
        [CoverageRequirement(field_id="seg_cost", field_label="分板块成本",
                             kind="core", covered=True),
         CoverageRequirement(field_id="seg_revenue", field_label="分板块收入",
                             kind="core", covered=True)])
    check(coverage_summary(full)["core_complete"] is True,
          "B1：核心字段全部覆盖 → core_complete=True")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
