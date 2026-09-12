"""Evidence 背书的结构化事实（纯确定性派生，无 I/O / LLM / RAG / OCR）。

任务书 §16 B / §17.1：财务数字必须先结构化再发布；「表头在前块、成本数字在后块」要
恢复表题单位列，成本不能被采纳为收入。本模块把已 inspect 的真实 Evidence 文本确定性
派生为 ``EvidenceStructuredFact``（Evidence 背书的结构化事实），是 harness claim 求值层
做收入/成本语义校验的输入载体。

派生链（全部确定性，无 LLM 参与）：
    inspected Evidence 文本 → table_context.extract_note_tables（恢复表题/单位/多级表头）
    → note_detail.parse_note_table（金额归一化 + 数值身份绑定）
    → EvidenceStructuredFact（content-addressed evidence_fact_id + 现有 evidence_id）

fail-closed（§17.1 不猜列/期间/收入成本）：
- 表题无法归类为明确收入/成本 → 不产 fact；
- 期间/单位/金额任一无法从表格上下文确定 → 不产 fact；
- 行标签为空或「合计」→ 不产 fact（合计是汇总行，非业务板块事实）；
- 本轮只提升「金额」列 fact（占比列已由 note_detail 解析，待后续轮次提升）。

明确不做：把 PDF 附注事实混入 Financial Snapshot（快照权威与 evidence 事实分属两条链）；
不扩展 financial_worker 直接读 Evidence；不伪造 snapshot_id；不绕过 CitationAuthority。

放在 financial_v2 而非 harness：本模块依赖 table_context/note_detail/number_identity
（同层），且 harness 需 import 本模块；放 harness 会形成 harness→financial_v2→harness 环。

CLI: python -m financial_v2.evidence_facts --self-check
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Protocol

from financial_v2 import note_detail as ND
from financial_v2 import table_context as TC
from financial_v2.number_identity import (
    FinancialNumberIdentity,
    item_code_from_title,
)

PARSER_VERSION = "evidence-facts-v1"

# 本轮提升的 fact 类型白名单（构成表结构化事实）。
FACT_TYPE_FINANCIAL_NOTE = "financial_note"


# ---------------------------------------------------------------------------
# 输入协议（harness.InspectedMaterial 结构化满足，避免 financial_v2 → harness 依赖）
# ---------------------------------------------------------------------------

class EvidenceBlock(Protocol):
    """build_evidence_facts 所需的最小 evidence 块接口。"""

    evidence_id: str
    document_id: str
    section_path: str
    page_number: int | None
    text: str


# ---------------------------------------------------------------------------
# 结构化事实
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceStructuredFact:
    """Evidence 背书的结构化事实（非财务快照结果，无 SnapshotAuthority）。

    - evidence_fact_id 是内部确定性坐标（content-addressed），非结构化 ref；
    - evidence_id 是已有 Evidence 的身份（CitationAuthority 复验原始证据的入口）；
    - 顶层字段是 financial_number_identity 的便捷投影（单一事实来源）；
    - value 为归一化元（金额列）；unit 为源单位（wan_yuan 等，占比列为 None）。
    """

    evidence_fact_id: str
    fact_type: str                                  # financial_note（本轮）
    evidence_id: str
    item_code: str | None
    revenue_cost_category: str | None
    period: str | None
    period_type: str | None
    scope: str | None
    currency: str | None
    unit: str | None
    value: Decimal | None
    business_segment: str | None
    amount_column: str | None
    row_column_source: str | None
    financial_number_identity: FinancialNumberIdentity
    parser_version: str

    def to_dict(self) -> dict:
        return {
            "evidence_fact_id": self.evidence_fact_id,
            "fact_type": self.fact_type,
            "evidence_id": self.evidence_id,
            "item_code": self.item_code,
            "revenue_cost_category": self.revenue_cost_category,
            "period": self.period,
            "period_type": self.period_type,
            "scope": self.scope,
            "currency": self.currency,
            "unit": self.unit,
            "value": str(self.value) if self.value is not None else None,
            "business_segment": self.business_segment,
            "amount_column": self.amount_column,
            "row_column_source": self.row_column_source,
            "financial_number_identity": self.financial_number_identity.to_dict(),
            "parser_version": self.parser_version,
        }


# ---------------------------------------------------------------------------
# 纯派生
# ---------------------------------------------------------------------------

def _evidence_fact_id(evidence_id: str, item: ND.StructuredNoteItem) -> str:
    raw = json.dumps({
        "evidence_id": evidence_id,
        "note_id": item.note_id,
        "identity": item.identity.to_dict(),
        "value": str(item.value),
        "raw_unit": item.raw_unit,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "ef-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _fact_qualified(item: ND.StructuredNoteItem) -> bool:
    """fail-closed 门槛：类别明确收入/成本 + 期间确定 + 板块非空非合计 + 金额确定。"""
    ident = item.identity
    seg = (ident.business_segment or "").strip()
    return (
        ident.revenue_cost_category in ("revenue", "cost")
        and ident.period is not None
        and seg not in ("", "合计", "总计", "小计")
        and item.value is not None
    )


def _to_fact(item: ND.StructuredNoteItem, table: TC.TableContext, evidence_id: str) -> EvidenceStructuredFact:
    source = f"{table.row_column_source} · {item.row_label}/{item.column_label}"
    ident = dataclasses.replace(item.identity, row_column_source=source)
    return EvidenceStructuredFact(
        evidence_fact_id=_evidence_fact_id(evidence_id, item),
        fact_type=FACT_TYPE_FINANCIAL_NOTE,
        evidence_id=evidence_id,
        item_code=ident.item_code,
        revenue_cost_category=ident.revenue_cost_category,
        period=ident.period,
        period_type=ident.period_type,
        scope=ident.scope,
        currency=ident.currency,
        unit=item.raw_unit,
        value=item.value,
        business_segment=ident.business_segment,
        amount_column=ident.amount_column,
        row_column_source=source,
        financial_number_identity=ident,
        parser_version=PARSER_VERSION,
    )


def build_evidence_facts(
    evidence_blocks: Iterable[EvidenceBlock],
    *,
    subject: str,
    scope: str = "consolidated",
    currency: str = "CNY",
    period_type: str = "annual",
) -> list[EvidenceStructuredFact]:
    """把已 inspect 的 Evidence 文本确定性派生为 EvidenceStructuredFact。

    - 按 (document_id, section_path) 分组并按物理页排序，组内文本顺序扫描恢复构成表；
    - 每组的事实绑定该组首个 evidence_id（构成表表题所在块的证据身份，跨块表以表题块为准）；
    - 表题 → item_code（item_code_from_title）→ 收入/成本类别（note_detail 派生）；
    - fail-closed：见模块 docstring 门槛，解析不足不产 fact。
    """
    facts: list[EvidenceStructuredFact] = []
    groups: dict[tuple[str, str], list] = {}
    for b in evidence_blocks:
        key = (b.document_id or "", b.section_path or "")
        groups.setdefault(key, []).append(b)
    for blocks in groups.values():
        ordered = sorted(blocks, key=lambda b: (b.page_number or 0))
        tables = TC.extract_note_tables([(b.page_number or 0, b.text or "") for b in ordered])
        fallback_eid = ordered[0].evidence_id if ordered else ""
        for t, bidx in tables:
            eid = ordered[bidx].evidence_id if 0 <= bidx < len(ordered) else fallback_eid
            item_code = item_code_from_title(t.table_title)
            res = ND.parse_note_table(
                t, subject=subject, scope=scope, currency=currency,
                item_code=item_code, period_type=period_type)
            for item in res.items:
                if item.amount_column != "金额":
                    continue  # 本轮只提升金额列 fact
                if not _fact_qualified(item):
                    continue
                facts.append(_to_fact(item, t, eid))
    return facts


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.evidence_facts",
        description="Evidence 背书的结构化事实派生自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        class _B:
            def __init__(self, eid, doc, sec, page, text):
                self.evidence_id, self.document_id = eid, doc
                self.section_path, self.page_number, self.text = sec, page, text

        blocks = [
            _B("ev-t5-10", "d1", "主营收入", 50,
               "表 5-10发行人主营业务收入构成表\n\n单位：万元，%\n"
               "  项目  2025年  2024年  2023年\n  金额  占比  金额  占比  金额  占比\n\n"
               "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n"),
            _B("ev-t5-11-a", "d1", "主营成本", 50,
               "表 5-11发行人主营业务成本构成表\n\n单位：万元，%\n"),
            _B("ev-t5-11-b", "d1", "主营成本", 50,
               "项目  2025年  2024年度  2023年度\n  金额  占比  金额  占比  金额  占比\n"
               "动力电池系统  24,106,439.7  77.2  19,246,128.2  70.4  22,171,419.3  71.7\n"),
        ]
        facts = build_evidence_facts(blocks, subject="宁德时代")
        out = []
        for f in facts:
            out.append({
                "evidence_fact_id": f.evidence_fact_id,
                "evidence_id": f.evidence_id,
                "item_code": f.item_code,
                "revenue_cost_category": f.revenue_cost_category,
                "period": f.period,
                "business_segment": f.business_segment,
                "value_yuan": str(f.value),
                "unit": f.unit,
                "row_column_source": f.row_column_source,
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
