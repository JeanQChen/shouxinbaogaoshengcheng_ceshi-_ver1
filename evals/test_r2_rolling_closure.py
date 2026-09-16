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
    #
    # 续页身份**由结构确定性派生**（与执行器/验收侧共用 ``table_structure`` 同一 primitive）：
    # 锚点块内最后一个**仍是未闭合结构**的表 = 待续的表；续页块必须**重排本表物理表头**
    # 才承接。语料形状照真实产物：全部 ``paragraph`` 块、``structured_payload`` 为空
    # （769/769）—— 旧 fixture 按 ``evidence_type='table'/'table_row'`` + ``structured_payload``
    # 造续页，正是被废弃的按类型过滤形态。
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        _title = "主营业务收入构成表"
        _physical_header = ("项目    本期金额    上期金额", "产品    收入    收入")

        def _flattened(*rows: str) -> str:
            """真实摊平表块：表题 + 单位行 + 物理表头（2 行）+ 数据行。"""
            return "\n".join((_title, "单位：万元") + _physical_header + tuple(rows))

        def _continuation(*rows: str) -> str:
            """跨页续页块：块首直接**重排本表物理表头**（真实跨页续表形状，无重复表题）。

            块首是通用表题行（非显式「表 N」、非续表标记）时**不承接** —— 那种行是另一张表的
            表题，不是续页；续页的结构签名就是「表头重排 + 继续的数据行」。
            """
            return "\n".join(_physical_header + tuple(rows))

        seed_text = _flattened("动力电池系统    1,000    900",
                               "储能电池系统    800    700")
        # 第 3 块含合计行 → 该表在此闭合，故它不再是「未闭合表结构」，不会成为后续滚动锚点。
        cont_texts = [
            _continuation("其他业务    200    180"),
            _continuation("分部间抵销    -50    -40"),
            _continuation("合计    2,000    1,780"),
            _continuation("少数股东权益    30    25"),
        ]
        blocks = [(5, 1, ["主营业务分析"], "paragraph", seed_text)] + [
            (5, i + 2, ["主营业务分析"], "paragraph", t)
            for i, t in enumerate(cont_texts)]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text=seed_text)
        res = expand(_request(seed, ("table_continuation",),
                              ExpansionBudget(table_continuation=3)),
                     registry, run_id="r2c1")
        adopted_ids = {b.evidence_id for b in res.adopted}
        check(all(_eid_of(5, i + 2, t) in adopted_ids
                  for i, t in enumerate(cont_texts[:3])),
              "C.1：续表第 1/2/3 块被采纳（预算内，锚点=seed 的未闭合表结构）")
        check(all(any(boundary.evidence_id == _eid_of(5, i + 2, t)
                      and boundary.relation == "continuation"
                      for boundary in res.boundary_decisions)
                  for i, t in enumerate(cont_texts[:3])),
              "C.1：采纳块以 continuation 关系落盘（非 adjacent，可复核续表来源）")
        unread_id = _eid_of(5, 5, cont_texts[3])
        check(any(c.evidence_id == unread_id for c in res.candidates_unread),
              "C.1：第 4 块续表进入 candidates_unread（budget table_continuation，"
              "非静默丢弃）")
        check(res.unread_scope.budget_axis == "table_continuation",
              "C.1：unread budget_axis=table_continuation")
        check(any(t.get("mode") == "table_continuation"
                  and t.get("anchor_evidence_id") == seed.evidence_id
                  and t.get("has_more") is True
                  for t in res.target_outcomes),
              "C.1：trace 记录续表锚点真实 evidence_id + limit+1 探针 has_more")
        _cont_steps = [s for s in res.trace.steps
                       if s.inputs.get("mode") == "table_continuation"]
        check(_cont_steps
              and _cont_steps[0].inputs.get("evidence_id") == seed.evidence_id
              and _cont_steps[0].outputs
              == tuple(_eid_of(5, i + 2, t)
                       for i, t in enumerate(cont_texts[:3]))
              and _cont_steps[0].stop_reason == "hard budget (table_continuation)"
              and _cont_steps[0].budget_remaining.get("table_continuation") == 0,
              "C.1：trace 步骤逐项可复核（锚点 evidence_id / 真实 outputs / stop_reason / "
              "预算消耗），不靠 obs 之外自报")

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
        # 主题小节层级由**同一 aspect / 文档版本的 seed 文本集**给出（本 fixture 只有一条
        # seed，其 section_path 叶子不在文本内 → 由正式 runner 的同源层级显式提供；见
        # harness.material_slice_runner 的 topic_level_by_scope 与
        # heading_structure.topic_level_from_seed_set）。层级未知则绝不猜、不做结构性关闭。
        req = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=ExpansionBudget(), dependency_fingerprint="dep-fp",
            aspect_id="company_business_main.main_business", topic_level_hint=3)
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
