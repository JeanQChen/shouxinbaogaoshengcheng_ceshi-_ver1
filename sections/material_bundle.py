"""B1 通用主题材料包（TopicEvidenceBundle，纯确定性组装，无 I/O / LLM / RAG）。

任务书 §13.1 / §16 B：研究系统交付「完整材料」而非零散 ID。本模块把一次 topic 的
研究产物（PlannedQuestion 契约 + inspected evidence + external material + 结构化
financial fact + 已验证 claim + unresolved + 冲突）组装成写作层可直接消费的主题材料包，
恢复主题完整性（表题/期间/币种/单位/多级表头/表体/行列定位/未读范围），并做「名单/表格
跨页接续」的受控上下文扩展。

职责边界：
- 纯组装与派生，不发起检索、不改上游 SectionResult/Claim、不做 PDF 附注解析；
- 跨页接续「受控」：仅在 document_version + section_path 一致、block 顺序相邻、且无
  新表题/新主体/新年度信号时接续；不确定即标记 truncated，绝不盲拼；
- 覆盖矩阵区分核心/补充字段；「主题名出现 ≠ 覆盖完成」（§15.1）。

CLI: python -m sections.material_bundle --self-check
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from financial_v2.table_context import TableContext, expand_table_context
from planning import schema as PS


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 覆盖要求
# ---------------------------------------------------------------------------

# 覆盖要求类别（§13.1「区分核心/补充字段」）。
COVERAGE_KINDS = ("core", "supplementary")


@dataclass(frozen=True)
class CoverageRequirement:
    """一个字段/方面的覆盖要求（核心 vs 补充）。covered 表示该字段已取得有效材料。"""

    field_id: str
    field_label: str
    kind: str                      # core | supplementary
    covered: bool = False
    source_ref: str | None = None  # 覆盖来源（claim_id / fact_id / material entry_id）


# ---------------------------------------------------------------------------
# 材料条目
# ---------------------------------------------------------------------------
# TableContext / expand_table_context 由 financial_v2.table_context 提供（B1 通用
# 表格上下文已下沉至 financial_v2，供 harness claim 求值层复用；本模块 re-export）。

# 材料条目种类：evidence 本地正文 / external 外部快照 / structured 结构化事实 /
# claim 已验证事实 / computed 计算结果。
MATERIAL_KINDS = ("evidence", "external", "structured", "claim", "computed")


@dataclass(frozen=True)
class MaterialEntry:
    """主题材料包中的一条材料（可回查 + 可读正文 + 表格上下文 + 未读范围）。"""

    entry_id: str
    kind: str                      # MATERIAL_KINDS 之一
    citation_key: str | None       # 引用身份（复用 sections.schema.citation_identity 语义）
    content_text: str              # 实际供模型读取的连续正文（evidence/external）
    content_hash: str              # 原始内容 hash
    document_version: str | None
    page: int | None
    section_path: str | None
    table: TableContext | None
    unread_range: str | None       # 未读取/截断范围（如「第3段之后未读」）


# ---------------------------------------------------------------------------
# 主题材料包
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicEvidenceBundle:
    """一个主题（topic）的完整材料包（§13.1 字段集）。

    - 契约侧：sub_questions（PlannedQuestion）+ coverage（核心/补充覆盖矩阵）；
    - 时点侧：subject / report_period / disclosure_date / financial_cutoff /
      external_search_cutoff；
    - 材料侧：materials（连续正文 + 表格上下文 + 未读范围）；
    - 结论侧：verified_facts（claim_id）/ computed_results（fact_id）/
      inference_depends_on / unresolved / conflicts；
    - 过程侧：lookup_strategies / budget_used / stop_reason / material_additions。
    """

    bundle_id: str
    topic_id: str
    topic_label: str
    sub_questions: tuple[PS.PlannedQuestion, ...]
    coverage: tuple[CoverageRequirement, ...]
    subject: str | None
    report_period: str | None
    disclosure_date: str | None
    financial_cutoff: str | None
    external_search_cutoff: str | None
    materials: tuple[MaterialEntry, ...]
    verified_facts: tuple[str, ...] = ()          # claim_id 列表
    computed_results: tuple[str, ...] = ()        # fact_id 列表
    inference_depends_on: tuple[str, ...] = ()    # claim_id/fact_id 列表
    unresolved: tuple[dict, ...] = ()             # SectionUnresolved 序列化
    conflicts: tuple[dict, ...] = ()
    lookup_strategies: tuple[str, ...] = ()
    budget_used: dict = field(default_factory=dict)
    stop_reason: str | None = None
    material_additions: tuple[dict, ...] = ()
    created_at: str = ""

    def covered_core(self) -> int:
        return sum(1 for c in self.coverage if c.kind == "core" and c.covered)

    def total_core(self) -> int:
        return sum(1 for c in self.coverage if c.kind == "core")

    def covered_supplementary(self) -> int:
        return sum(1 for c in self.coverage if c.kind == "supplementary" and c.covered)

    def total_supplementary(self) -> int:
        return sum(1 for c in self.coverage if c.kind == "supplementary")


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def assemble_topic_bundle(
    topic_id: str,
    topic_label: str,
    questions: list[PS.PlannedQuestion],
    coverage: list[CoverageRequirement],
    *,
    subject: str | None = None,
    report_period: str | None = None,
    disclosure_date: str | None = None,
    financial_cutoff: str | None = None,
    external_search_cutoff: str | None = None,
    materials: list[MaterialEntry] | None = None,
    verified_facts: list[str] | None = None,
    computed_results: list[str] | None = None,
    inference_depends_on: list[str] | None = None,
    unresolved: list[dict] | None = None,
    conflicts: list[dict] | None = None,
    lookup_strategies: list[str] | None = None,
    budget_used: dict | None = None,
    stop_reason: str | None = None,
    material_additions: list[dict] | None = None,
) -> TopicEvidenceBundle:
    """组装一个主题材料包（纯函数，输入全显式，无 I/O）。"""
    from sections import schema as SS  # 局部导入避免环

    bundle_id = SS.sha256_json([
        "bundle", topic_id, topic_label,
        [q.question_id for q in questions],
        [c.field_id for c in coverage],
    ])[:24]
    return TopicEvidenceBundle(
        bundle_id=bundle_id,
        topic_id=topic_id,
        topic_label=topic_label,
        sub_questions=tuple(questions),
        coverage=tuple(coverage),
        subject=subject,
        report_period=report_period,
        disclosure_date=disclosure_date,
        financial_cutoff=financial_cutoff,
        external_search_cutoff=external_search_cutoff,
        materials=tuple(materials or []),
        verified_facts=tuple(verified_facts or []),
        computed_results=tuple(computed_results or []),
        inference_depends_on=tuple(inference_depends_on or []),
        unresolved=tuple(unresolved or []),
        conflicts=tuple(conflicts or []),
        lookup_strategies=tuple(lookup_strategies or []),
        budget_used=dict(budget_used or {}),
        stop_reason=stop_reason,
        material_additions=tuple(material_additions or []),
        created_at=_utcnow(),
    )


def coverage_summary(bundle: TopicEvidenceBundle) -> dict:
    """覆盖矩阵摘要（核心/补充的已取得/总数；主题名出现 ≠ 覆盖完成）。"""
    return {
        "topic_id": bundle.topic_id,
        "core_covered": bundle.covered_core(),
        "core_total": bundle.total_core(),
        "supplementary_covered": bundle.covered_supplementary(),
        "supplementary_total": bundle.total_supplementary(),
        "core_complete": bundle.total_core() > 0
        and bundle.covered_core() == bundle.total_core(),
    }


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.material_bundle",
        description="通用主题材料包自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        seed = {
            "table_id": "t5-11", "table_title": "表5-11 主营业务成本构成表",
            "headers": ("项目", "2024年", "2025年"), "unit": "万元",
            "period": "2025-12-31", "currency": "CNY",
            "row_column_source": "P50 表5-11", "document_version": "dv1",
            "section_path": "营业成本", "order": 1, "page": 50,
            "text": "动力电池  19,246,128.2  24,106,439.7",
        }
        cont_same_table = {
            "document_version": "dv1", "section_path": "营业成本", "order": 2,
            "page": 51, "text": "储能电池  8,000,000.0  9,500,000.0",
        }
        cont_new_table = {
            "document_version": "dv1", "section_path": "营业成本", "order": 3,
            "page": 52, "text": "表5-12 期间费用构成表\n销售费用  1,000,000",
        }

        merged = expand_table_context(seed, [cont_same_table, cont_new_table])
        single = expand_table_context(seed, [cont_new_table])
        # 截断透传：seed 标记截断（检索层只读了片段）→ 结果保留 truncated=True。
        seed_trunc = dict(seed, truncated=True)
        merged_trunc = expand_table_context(seed_trunc, [cont_same_table])

        q = PS.PlannedQuestion(
            question_id="q1", question="主营业务成本构成？", priority="P0",
            topic_id="business", required_aspects=("板块成本", "期间"),
            evidence_requirements=(), calculation_requirements=(),
            analysis_requirements=(), missing_policy="write_not_found",
            blocking_policy=(), impact_scope=())
        bundle = assemble_topic_bundle(
            "business", "主营业务", [q],
            [CoverageRequirement(field_id="seg_cost", field_label="分板块成本",
                                 kind="core", covered=True, source_ref="claim_x"),
             CoverageRequirement(field_id="seg_margin", field_label="分板块毛利",
                                 kind="supplementary", covered=False)],
            subject="宁德时代", report_period="2025-12-31",
            materials=[MaterialEntry(
                entry_id="m1", kind="evidence", citation_key="evidence:e1",
                content_text="动力电池成本 19,246,128.2", content_hash="h1",
                document_version="dv1", page=50, section_path="营业成本",
                table=merged, unread_range=None)],
            verified_facts=["claim_x"], stop_reason="COMPLETED")

        print(json.dumps({
            "merged_table_rows": len(merged.rows),
            "merged_table_truncated": merged.truncated,
            "merged_continued_from": merged.continued_from,
            "single_table_truncated": single.truncated,
            "single_table_rows": len(single.rows),
            "truncated_passthrough": merged_trunc.truncated,
            "coverage": coverage_summary(bundle),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
