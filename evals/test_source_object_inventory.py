"""Eval: 修复 B 源对象清单 → 恢复结果 闭环（ExpectedSourceObjectInventory v2）。

用法: python -m evals.test_source_object_inventory

v2 口径（P1-B）：清单按**规范源顺序**建立，恢复真相**只**来自已持久化 assembly
（``relation == flattened_table_recovery``）；无 assembly ⇒ 绝不 ``recovered_ok``。

覆盖（counter-example 先行，全部离线/确定性）：
- 真实 main_business 样本 表5-10/5-11/5-12/5-13 每张表都进入清单并各有唯一恢复结果
  （表号只出现在测试与验收，绝不进生产正文）。
- 缺失表号 → target_not_obtained → source_object_gate 失败。
- 重复恢复（同表号两张）→ recovery_failed（重复/错误合并）。
- 错误合并（恢复出清单外带表题的表）→ unmatched_recovered_tables → gate 失败。
- 标题不一致（同表号、不同表题）→ recovery_failed（标题不一致）。
- dangling 显式引用 → 按**本对象自己的**目标表号裁决（不回到整行重解析）。
- 清单**绝不**自行恢复：无持久化 assembly ⇒ inventory 侧不得报 ok（P1-B.5/B.6）。
- 确定性：同 (aspect_id, texts, assemblies) 恒得同结果。
- 四态分布 reconciliation_summary 逐对象唯一归属。

纯确定性：零 LLM/网络。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import source_object_inventory as SOI
from harness.set_enumeration import recover_flattened_tables

_ASPECT = "company_business_main.main_business"

# 真实形态四张表（表 5-10/5-11/5-12/5-13），各自有表题+单位+表头+数据行+合计。
_FOUR_TABLES = (
    "表 5-10 发行人主营业务收入构成表\n单位：万元，%\n"
    "项目  金额  占比\n"
    "动力电池系统  31,650  74.7\n"
    "合计  42,370  100.0\n"
    "表 5-11 发行人主营业务成本构成表\n单位：万元，%\n"
    "项目  金额  占比\n"
    "原材料  8,000  70.0\n"
    "合计  11,000  100.0\n"
    "表 5-12 发行人毛利率构成表\n单位：万元\n"
    "项目  金额\n"
    "毛利  5,000\n"
    "合计  5,000\n"
    "表 5-13 发行人研发投入构成表\n单位：万元\n"
    "项目  金额\n"
    "研发  3,000\n"
    "合计  3,000\n"
)


def _src(text: str, page: int = 1, block: int = 0, mat: str = "mat-a",
         dv: str = "v1", offset: int = 0) -> SOI.SourceText:
    return SOI.SourceText(SOI.SourcePosition(dv, page, block, offset, mat), text)


def _asm(title: str, status: str = "ok", issue: str | None = None,
         comps: tuple = ("mat-a",)) -> dict:
    """合成一条**已持久化**的摊平表 assembly（含 relation 与内容寻址 assembly_id）。"""
    aid = "asm-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:32]
    return {"assembly_id": aid, "relation": SOI.FLATTENED_TABLE_RELATION,
            "component_material_ids": list(comps), "table_title": title,
            "recovery_status": status, "recovery_issue": issue}


def _persist(texts: list[str], comps: tuple = ("mat-a",)) -> list[dict]:
    """按真实落盘口径把 recover_flattened_tables 结果投影为已持久化 assembly。"""
    out: list[dict] = []
    for t in recover_flattened_tables(texts):
        if not t.get("headers") or not t.get("rows"):
            continue  # 真实实现同样不产出无表头/无数据行的投影
        disc = SOI.flattened_table_structure_identity(t)
        out.append({
            "assembly_id": "asm-" + hashlib.sha256(disc.encode("utf-8")).hexdigest()[:32],
            "relation": SOI.FLATTENED_TABLE_RELATION,
            "component_material_ids": list(comps),
            "table_title": t.get("title") or "",
            "recovery_status": t.get("recovery_status", "ok"),
            "recovery_issue": t.get("recovery_issue"),
        })
    return out


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
    # 1. 真实四表样本：表 5-10..5-13 全部进入清单，各获 recovered_ok
    # ------------------------------------------------------------------
    sources = [_src(_FOUR_TABLES)]
    inv = SOI.derive_expected_source_object_inventory(_ASPECT, sources)
    nums = {o.object_id for o in inv.expected_objects
            if o.kind == SOI.KIND_TABLE_NUMBER}
    check(nums == {"table:5-10", "table:5-11", "table:5-12", "table:5-13"},
          "四表样本：表 5-10/5-11/5-12/5-13 全部进入清单（表号仅测试/验收）")
    asms = _persist([_FOUR_TABLES])
    check(len(asms) == 4, "四表样本：四张表各产出唯一持久化 assembly")
    inv = SOI.reconcile_source_object_inventory(inv, asms)
    results_by_id = {r.object_id: r for r in inv.recovery_results}
    check(all(results_by_id[f"table:{n}"].result == SOI.RECOVERED_OK
              for n in ("5-10", "5-11", "5-12", "5-13")),
          "四表样本：每张表逐项 recovered_ok（逐项对账，非「至少一张成功」）")
    check(SOI.source_object_gate(inv, asms) is None,
          "四表样本：逐项闭合 → source_object_gate 通过（None）")
    check(inv.unmatched_recovered_tables == [],
          "四表样本：无未匹配恢复表（无错误合并）")

    # ------------------------------------------------------------------
    # 1b. P1-B.5/B.6：清单**绝不**自行恢复 —— 无持久化 assembly ⇒ 绝不 ok
    # ------------------------------------------------------------------
    inv_none = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, sources), [])
    r_none = {r.object_id: r.result for r in inv_none.recovery_results
              if r.object_id.startswith("table:")}
    check(r_none and all(v == SOI.TARGET_NOT_OBTAINED for v in r_none.values()),
          "无持久化 assembly：清单侧四个表号全部 target_not_obtained（绝不自报 ok）")
    check(SOI.source_object_gate(inv_none, []) is not None,
          "无持久化 assembly：source_object_gate 失败（fail-closed）")

    # ------------------------------------------------------------------
    # 2. 缺失表号 → target_not_obtained → gate 失败
    # ------------------------------------------------------------------
    # 只恢复 5-10（5-11/5-12/5-13 缺失）。
    partial = _persist([
        "表 5-10 发行人主营业务收入构成表\n单位：万元，%\n"
        "项目  金额  占比\n动力电池系统  31,650  74.7\n合计  42,370  100.0\n"])
    inv2 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, sources), partial)
    r11 = next(r for r in inv2.recovery_results if r.object_id == "table:5-11")
    check(r11.result == SOI.TARGET_NOT_OBTAINED,
          "缺失表：表 5-11 → target_not_obtained（不逃逸验收）")
    gate2 = SOI.source_object_gate(inv2, partial)
    check(gate2 is not None and "target_not_obtained" in gate2,
          "缺失表：source_object_gate 失败（含 target_not_obtained）")

    # ------------------------------------------------------------------
    # 3. 重复恢复（同表号两张）→ recovery_failed（重复/错误合并）
    # ------------------------------------------------------------------
    dup = [_asm("表 5-10 发行人主营业务收入构成表", comps=("mat-a",)),
           _asm("表 5-10 发行人主营业务收入构成表", comps=("mat-b",))]
    dup[1]["assembly_id"] = dup[1]["assembly_id"] + "-b"  # 同表题两张不同 assembly
    inv3 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(
            _ASPECT, [_src("表 5-10 发行人主营业务收入构成表\n")]), dup)
    r10 = next(r for r in inv3.recovery_results if r.object_id == "table:5-10")
    check(r10.result == SOI.RECOVERY_FAILED and "重复" in (r10.issue or ""),
          "重复：同表号两张 → recovery_failed（重复/错误合并）")
    check(SOI.source_object_gate(inv3, dup) is not None,
          "重复：source_object_gate 失败")

    # ------------------------------------------------------------------
    # 4. 错误合并（恢复出清单外带表题的表）→ unmatched_recovered_tables → gate 失败
    # ------------------------------------------------------------------
    merged = [_asm("表 5-10 发行人主营业务收入构成表"),
              _asm("表 5-99 其他无关表")]  # 清单外 → 错误合并
    inv4 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(
            _ASPECT, [_src("表 5-10 发行人主营业务收入构成表\n")]), merged)
    check(len(inv4.unmatched_recovered_tables) == 1
          and "表 5-99" in inv4.unmatched_recovered_tables[0],
          "错误合并：恢复出清单外带表题表 → unmatched_recovered_tables")
    gate4 = SOI.source_object_gate(inv4, merged)
    check(gate4 is not None and "unmatched_recovered_tables" in gate4,
          "错误合并：source_object_gate 失败（含 unmatched_recovered_tables）")

    # ------------------------------------------------------------------
    # 5. 标题不一致（同表号、不同表题）→ recovery_failed（标题不一致）
    # ------------------------------------------------------------------
    wrong_title = [_asm("表 5-10 发行人主营业务成本构成表")]  # 同号不同题
    inv5 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(
            _ASPECT, [_src("表 5-10 发行人主营业务收入构成表\n")]), wrong_title)
    r10b = next(r for r in inv5.recovery_results if r.object_id == "table:5-10")
    check(r10b.result == SOI.RECOVERY_FAILED and "标题不一致" in (r10b.issue or ""),
          "标题不一致：同表号不同表题 → recovery_failed（标题不一致）")
    check(SOI.source_object_gate(inv5, wrong_title) is not None,
          "标题不一致：source_object_gate 失败")

    # ------------------------------------------------------------------
    # 6. 无表题退化片段（仅表头/仅数据行）不进 unmatched（由枚举侧诚实 fail-closed）
    # ------------------------------------------------------------------
    degen = [dict(_asm(""), table_title="", recovery_status="ok")]
    inv6 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, []), degen)
    check(inv6.unmatched_recovered_tables == [],
          "退化片段（无表题）不进 unmatched_recovered_tables")

    # ------------------------------------------------------------------
    # 7. 确定性：同输入 → 同输出
    # ------------------------------------------------------------------
    inv_a = SOI.derive_expected_source_object_inventory(_ASPECT, sources)
    inv_b = SOI.derive_expected_source_object_inventory(_ASPECT, sources)
    check(inv_a.to_dict() == inv_b.to_dict(),
          "确定性：derive 同输入 → 同清单（to_dict 相等）")
    rec_a = SOI.reconcile_source_object_inventory(inv_a, asms).to_dict()
    rec_b = SOI.reconcile_source_object_inventory(
        inv_b, _persist([_FOUR_TABLES])).to_dict()
    check(rec_a == rec_b, "确定性：reconcile 同输入 → 同恢复结果")

    # ------------------------------------------------------------------
    # 8. 四态分布：每个源对象唯一归属
    # ------------------------------------------------------------------
    counts = SOI.reconciliation_summary(inv)
    check(counts[SOI.RECOVERED_OK] == 4
          and counts[SOI.RECOVERED_PARTIAL] == 0
          and counts[SOI.RECOVERY_FAILED] == 0
          and counts[SOI.TARGET_NOT_OBTAINED] == 0,
          "四态分布：四表全 recovered_ok，其余为 0")
    check(sum(counts.values()) == len(inv.expected_objects),
          "四态分布：计数总和 == 期望源对象数（逐对象唯一归属）")

    # ------------------------------------------------------------------
    # 9. 续表「表 5-10（续）」不得误作普通主表号（counter-example #5）
    # ------------------------------------------------------------------
    cont_text = (
        "表 5-10 发行人主营业务收入构成表\n"
        "单位：万元\n项目  金额\n动力电池系统  31,650\n合计  42,370\n"
        "表 5-10（续）发行人主营业务收入构成表\n单位：万元\n项目  金额\n其他  1,000\n")
    inv_c = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(cont_text)])
    cont_objs = {o.object_id: o.kind for o in inv_c.expected_objects}
    check(cont_objs.get("continuation:5-10") == SOI.KIND_CONTINUATION,
          "续表：表 5-10（续）→ continuation（非 table_number）")
    check("table:5-10" in cont_objs,
          "续表：主表 5-10 仍作为 table_number 入清单（主表与续表分离）")

    # ------------------------------------------------------------------
    # 10. dangling 跨页引用「详见表 5-12」不得无条件 recovered_ok（counter-example #6）
    # ------------------------------------------------------------------
    cross_src = [_src("表 5-10 发行人主营业务收入构成表\n详见表 5-12 发行人毛利率构成表")]
    inv_x = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, cross_src),
        [_asm("表 5-10 发行人主营业务收入构成表")])
    xr = next((r for r in inv_x.recovery_results
               if r.object_id.startswith("cross_ref:")), None)
    check(xr is not None and xr.result == SOI.TARGET_NOT_OBTAINED,
          "dangling 详见表 5-12 → target_not_obtained（非无条件 recovered_ok）")
    # 目标表 recovered_ok → cross_ref recovered_ok。
    inv_y = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, cross_src),
        [_asm("表 5-10 发行人主营业务收入构成表"),
         _asm("表 5-12 发行人毛利率构成表")])
    yr = next((r for r in inv_y.recovery_results
               if r.object_id.startswith("cross_ref:")), None)
    check(yr is not None and yr.result == SOI.RECOVERED_OK,
          "cross_ref 目标表 5-12 recovered_ok → recovered_ok")
    check(SOI.inventory_assembly_closure(inv_y, [
        _asm("表 5-10 发行人主营业务收入构成表"),
        _asm("表 5-12 发行人毛利率构成表")]) is None,
          "cross_ref recovered_ok：清单 ↔ assembly 闭合一致（closure 通过）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
