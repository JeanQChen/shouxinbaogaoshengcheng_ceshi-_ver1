"""Eval: P1-C 扩读/续表/显式引用滚动闭合 + 未读因果链（反例先行）。

用法: python -m evals.test_r2_rolling_closure

覆盖（P1-C）：
- C.1：``table_continuation`` 不再 single read_once —— 用 limit+1 探针 + has_more
  滚动，预算耗尽时把首个未读续表块**显式记 unread_inside_boundary**（旧实现静默丢弃）。
- C.2：``explicit_reference`` 每个引用目标独立解析（旧实现把多目标 join 成单一
  reference_target，只解析最后一个编号、漏掉其余目标）。
- C.3：后续扩读材料（非 seed）中新发现的显式引用被 follow（旧实现只扫描 seed 文本，
  漏掉相邻块/续表块中的交叉引用目标）。
- C.6：向后边界 + 向前 adjacent_pages 预算 → unread reason=budget + budget_axis=
  adjacent_pages + 因果 stop_reason，**绝不**因另一方向命中边界而改写为 boundary。

全部离线：零 LLM/网络；复用 test_context_expansion 的临时 DB fixture 助手。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.context_expansion import (
    ContextExpansionRequest,
    ExpansionBudget,
    expand,
)
from harness.evidence_reader import (
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from tools.registry import ToolRegistry

from evals.test_context_expansion import (
    _COMPANY,
    _DOC,
    _DOCV,
    _SETV,
    _eid_of,
    _make_db,
    _request,
    _seed,
)


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

    # ------------------------------------------------------------------
    # C.1：table_continuation 滚动闭合 —— 预算耗尽时显式记 unread（非静默丢弃）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        blocks = [
            (5, 1, ["主营业务分析"], "table", "营业收入构成（分产品）",
             {"table_title": "营业收入构成"}),
            (5, 2, ["主营业务分析"], "table_row", "动力电池系统 1,000",
             {"table_title": "营业收入构成"}),
            (5, 3, ["主营业务分析"], "table_row", "储能电池系统 800",
             {"table_title": "营业收入构成"}),
            (5, 4, ["主营业务分析"], "table_row", "其他业务 200",
             {"table_title": "营业收入构成"}),
            (5, 5, ["主营业务分析"], "table_row", "合计 2,000",
             {"table_title": "营业收入构成"}),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text="营业收入构成（分产品）", etype="table",
                     payload={"table_title": "营业收入构成"})
        res = expand(_request(seed, ("table_continuation",),
                              ExpansionBudget(table_continuation=3)),
                     registry, run_id="r2c1")
        adopted_ids = {b.evidence_id for b in res.adopted}
        last_id = _eid_of(5, 5, "合计 2,000", {"table_title": "营业收入构成"})
        check(_eid_of(5, 2, "动力电池系统 1,000", {"table_title": "营业收入构成"})
              in adopted_ids, "C.1：续表第 1 块被采纳")
        check(_eid_of(5, 3, "储能电池系统 800", {"table_title": "营业收入构成"})
              in adopted_ids, "C.1：续表第 2 块被采纳")
        check(_eid_of(5, 4, "其他业务 200", {"table_title": "营业收入构成"})
              in adopted_ids, "C.1：续表第 3 块被采纳（预算内）")
        check(any(c.evidence_id == last_id for c in res.candidates_unread),
              "C.1：第 4 块续表进入 candidates_unread（budget table_continuation，"
              "非静默丢弃）")
        check(res.unread_scope.budget_axis == "table_continuation",
              "C.1：unread budget_axis=table_continuation")

    # ------------------------------------------------------------------
    # C.2：explicit_reference 每个目标独立解析（不 join 成单一 reference_target）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        blocks = [
            (5, 0, ["公司基本情况"], "paragraph",
             "公司主营情况详见 24、资产情况；公司负债情况详见 25、负债情况。"),
            (6, 0, ["资产情况"], "paragraph", "24、资产情况 公司资产规模稳健。"),
            (7, 0, ["负债情况"], "paragraph", "25、负债情况 公司负债结构合理。"),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="公司主营情况详见 24、资产情况；公司负债情况详见 25、负债情况。")
        res = expand(_request(seed, ("explicit_reference",)), registry, run_id="r2c2")
        adopted_ids = {b.evidence_id for b in res.adopted}
        a_id = _eid_of(6, 0, "24、资产情况 公司资产规模稳健。")
        b_id = _eid_of(7, 0, "25、负债情况 公司负债结构合理。")
        check(a_id in adopted_ids,
              "C.2：第一个显式引用目标（24、资产情况）被独立解析采纳")
        check(b_id in adopted_ids,
              "C.2：第二个显式引用目标（25、负债情况）被独立解析采纳")

    # ------------------------------------------------------------------
    # C.3：后续扩读材料中新发现的显式引用被 follow（不只扫描 seed 文本）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["公司基本情况"], "paragraph", "公司主营情况概述。"),
            (5, 1, ["公司基本情况"], "paragraph", "公司资产详见 24、资产情况。"),
            (5, 2, ["在建工程"], "heading", "十、在建工程项目"),
            (6, 0, ["资产情况"], "paragraph", "24、资产情况 公司资产规模稳健。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="公司主营情况概述。")
        res = expand(_request(seed, ("adjacent_blocks", "explicit_reference")),
                     registry, run_id="r2c3")
        adopted_ids = {b.evidence_id for b in res.adopted}
        target_id = _eid_of(6, 0, "24、资产情况 公司资产规模稳健。")
        check(target_id in adopted_ids,
              "C.3：后续扩读材料（非 seed）中新发现的显式引用被 follow 并采纳")
        check(all(c.evidence_id != target_id for c in res.candidates_unread),
              "C.3：被 follow 采纳的目标非未读（已读即不在 candidates_unread）")
        dec = [d for d in res.boundary_decisions if d.evidence_id == target_id]
        check(any(d.relation == "reference" for d in dec),
              "C.3：目标块以 reference 关系采纳（非 adjacent）")

    # ------------------------------------------------------------------
    # C.6：向后回滚边界 + 向前 adjacent_pages 预算 → unread 因果链按预算保留
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["公司基本情况"], "paragraph",
             "（二）董事及高级管理人员的主要工作经历\n1、董事会 公司现任董事会成员如下。"),
            (4, 1, ["公司基本情况"], "paragraph", "高管1：张三。"),
            (5, 0, ["公司基本情况"], "paragraph", "（二）主营业务情况\n公司主营业务为动力电池。"),
            (6, 0, ["公司基本情况"], "paragraph", "主营业务收入构成见下表。"),
            (8, 0, ["公司基本情况"], "paragraph", "更远处的风险因素说明。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, section=("公司基本情况",),
                     text="（二）主营业务情况\n公司主营业务为动力电池。")
        req = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=ExpansionBudget(), dependency_fingerprint="dep-fp",
            aspect_id="company_business_main.main_business")
        res = expand(req, registry, run_id="r2c6")
        far = _eid_of(8, 0, "更远处的风险因素说明。")
        check(any(c.evidence_id == far for c in res.candidates_unread),
              "C.6：P8 块进入 candidates_unread（adjacent_pages 预算）")
        check(res.stop_reason == "previous section heading (backward rollback)",
              "C.6：最终 stop_reason 为向后回滚边界（聚合 summary）")
        check(res.unread_scope.reason == "budget",
              "C.6：unread reason=budget（向前 adjacent_pages 预算因果链，绝不改写为 boundary）")
        check(res.unread_scope.budget_axis == "adjacent_pages",
              "C.6：unread budget_axis=adjacent_pages（保留预算轴）")
        check(getattr(res.unread_scope, "stop_reason", None) == "hard budget (adjacent_pages)",
              "C.6：unread stop_reason=hard budget (adjacent_pages)（因果停止，非聚合 summary）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
