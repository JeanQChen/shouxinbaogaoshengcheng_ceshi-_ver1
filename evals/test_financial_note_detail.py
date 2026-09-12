"""Eval: financial_v2 财务附注明细结构化接口（B2 失败回归）。

用法: python -m evals.test_financial_note_detail

回归背景（任务书 §13.4 / §16 B / §17.1）：
- NDSD_KCZ_2026.pdf 物理第50页 表5-10 收入构成表 2024/2025 动力电池
  25,304,133.7 / 31,650,636.9 万元；表5-11 成本构成表 19,246,128.2 / 24,106,439.7 万元。
- 旧错误把表5-11 成本金额当收入采纳。本模块验证「表题→收入成本类别 + 表头→期间 +
  行标签→业务板块 + 金额 Decimal 归一化」的结构化绑定，以及「成本不能被采纳为收入」的
  确定性判定（note_item_check_label → mismatch）。
- B1+B2 集成：表头在前块、成本数字在后块 → expand_table_context 合并后 parse_note_table
  恢复表题/单位/列。

全部纯函数（无 I/O / LLM / DB），合成 fixture（宁德时代等名只作 case fixture）。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2.note_detail import (
    NoteTableParseResult,
    StructuredNoteItem,
    note_item_check_label,
    note_item_relation,
    parse_note_table,
)
from sections.material_bundle import TableContext, expand_table_context


def _ctx(title: str, headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...], *,
         unit: str = "万元", period: str = "2025-12-31", truncated: bool = False,
         table_id: str = "t", row_column_source: str = "P50") -> TableContext:
    return TableContext(
        table_id=table_id, table_title=title, headers=headers, rows=rows,
        period=period, currency="CNY", unit=unit, footnotes=(),
        row_column_source=row_column_source, continued_from=None, truncated=truncated)


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

    # ---- 表5-10 收入构成表 ----
    rev = parse_note_table(
        _ctx("表5-10 主营业务收入构成表", ("项目", "2024年", "2025年"),
             (("动力电池  25,304,133.7  31,650,636.9",),)),
        subject="宁德时代", item_code="OPERATING_REVENUE")
    check(rev.revenue_cost_category == "revenue",
          "B2：表题+OPERATING_REVENUE → revenue")
    check(len(rev.items) == 2, "B2：1 行 × 2 金额列 → 2 条明细")
    d25 = next(it for it in rev.items if it.period == "2025-12-31")
    check(d25.value == Decimal("316506369000"),
          "B2：收入 31,650,636.9 万元 → 316,506,369,000 元（Decimal 归一化）")
    check(d25.identity.business_segment == "动力电池"
          and d25.amount_column == "金额",
          "B2：行标签 → 业务板块，金额列 → amount_column=金额")
    check(note_item_check_label(d25, "revenue") == "match",
          "B2：收入明细 + claim 标收入 → match")

    # ---- 表5-11 成本构成表（核心回归：成本不能被采纳为收入）----
    cost = parse_note_table(
        _ctx("表5-11 主营业务成本构成表", ("项目", "2024年", "2025年"),
             (("动力电池  19,246,128.2  24,106,439.7",),)),
        subject="宁德时代", item_code="OPERATING_COST")
    check(cost.revenue_cost_category == "cost",
          "B2：表题+OPERATING_COST → cost")
    c25 = next(it for it in cost.items if it.period == "2025-12-31")
    check(c25.value == Decimal("241064397000"),
          "B2：成本 24,106,439.7 万元 → 241,064,397,000 元")
    check(note_item_check_label(c25, "revenue") == "mismatch",
          "B2：成本明细 + claim 标「收入」→ mismatch（阻断采纳）")
    check(note_item_check_label(c25, "cost") == "match",
          "B2：成本明细 + claim 标「成本」→ match")

    # ---- 行标签含空格 ----
    spaced = parse_note_table(
        _ctx("表5-10 主营业务收入构成表", ("项目", "2024年", "2025年"),
             (("动力电池 系统  1,000.0  2,000.0",),)),
        subject="宁德时代", item_code="OPERATING_REVENUE")
    check(spaced.items[0].identity.business_segment == "动力电池 系统",
          "B2：行标签含空格 → 完整业务板块（不因空格截断）")

    # ---- 占比列 ----
    pct = parse_note_table(
        _ctx("表5-10 主营业务收入构成表", ("项目", "2025年", "占比"),
             (("动力电池  31,650,636.9  56.7%",),)),
        subject="宁德时代", item_code="OPERATING_REVENUE")
    pct_item = next(it for it in pct.items if it.amount_column == "占比")
    check(pct_item.value == Decimal("56.7") and pct_item.raw_unit is None,
          "B2：占比列存占比数值（不乘单位），amount_column=占比")

    # ---- 空值 token → value None + parse_issue ----
    empty = parse_note_table(
        _ctx("表5-11 主营业务成本构成表", ("项目", "2024年", "2025年"),
             (("动力电池  19,246,128.2  —",),)),
        subject="宁德时代", item_code="OPERATING_COST")
    e25 = next(it for it in empty.items if it.period == "2025-12-31")
    check(e25.value is None, "B2：空值 token「—」→ value None（不编数）")
    check(len(empty.parse_issues) == 0, "B2：显式空值不误报 parse_issue")

    # ---- 节标题行（无足够数值列）→ skipped ----
    hdr_row = parse_note_table(
        _ctx("表5-11 主营业务成本构成表", ("项目", "2024年", "2025年"),
             (("（一）分产品",), ("动力电池  19,246,128.2  24,106,439.7",))),
        subject="宁德时代", item_code="OPERATING_COST")
    check(hdr_row.skipped_rows == 1 and len(hdr_row.items) == 2,
          "B2：节标题行跳过，不计入明细，不报错")

    # ---- 截断透传 ----
    trunc = parse_note_table(
        _ctx("表5-11 主营业务成本构成表", ("项目", "2024年", "2025年"),
             (("动力电池  19,246,128.2  24,106,439.7",),), truncated=True),
        subject="宁德时代", item_code="OPERATING_COST")
    check(trunc.truncated is True, "B2：truncated 由表透传（诚实声明）")

    # ---- note_item_relation：成本 vs 收入 → DIFFERENT_ITEM；同项 → SAME ----
    check(note_item_relation(c25, d25) == "DIFFERENT_ITEM",
          "B2：成本 vs 收入（同期间同板块）→ DIFFERENT_ITEM")
    check(note_item_relation(d25, d25) == "SAME",
          "B2：同明细自比 → SAME")

    # ---- B1+B2 集成：表头在前块、成本数字在后块 ----
    seed = {
        "table_id": "t5-11", "table_title": "表5-11 主营业务成本构成表",
        "headers": ("项目", "2024年", "2025年"), "unit": "万元",
        "period": "2025-12-31", "currency": "CNY",
        "row_column_source": "P50 表5-11", "document_version": "dv1",
        "section_path": "营业成本", "order": 1, "page": 50, "text": "",
    }
    cont = {"document_version": "dv1", "section_path": "营业成本", "order": 2,
            "page": 51, "text": "动力电池  19,246,128.2  24,106,439.7"}
    merged = expand_table_context(seed, [cont])
    res = parse_note_table(merged, subject="宁德时代", item_code="OPERATING_COST")
    check(res.table_title == "表5-11 主营业务成本构成表" and res.unit == "wan_yuan",
          "B2 集成：表头在前块 → 恢复表题+单位")
    check(res.revenue_cost_category == "cost",
          "B2 集成：恢复成本类别")
    m25 = next(it for it in res.items if it.period == "2025-12-31")
    check(m25.value == Decimal("241064397000")
          and note_item_check_label(m25, "revenue") == "mismatch",
          "B2 集成：后块成本数字恢复列 + 成本不能被采纳为收入")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
