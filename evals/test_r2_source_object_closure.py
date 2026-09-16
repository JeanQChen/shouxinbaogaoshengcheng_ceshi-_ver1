"""Eval: P1-B 源对象清单 ↔ 持久化 assembly 机制级闭环反例（先写反例，确认旧实现失败）。

用法: python -m evals.test_r2_source_object_closure

覆盖（§三.B 反例清单，全部离线/确定性/零 LLM）：
1. 普通「主营业务情况」等标题**不是**表（复合结构信号，关键词单独命中无效）；
2. 折行数据「有限公司」「名称」等不是表题；
3. 真表题（「（1） 应收票据分类列示」）**必须**被识别；
4. 显式「表 N」介入时，其前的正文标题不得成为表题（中介表题）；
5. **inventory ok 但 assembly 缺失必须失败**；
6. **inventory 结果与持久化 assembly 状态矛盾必须失败**（不得失败/成功互相打架）；
7. **assembly component 外键不存在必须失败**；
8. 同块多表必须生成不同且稳定的 object/assembly identity；
9. 「详见表 5-10、表 5-11」必须生成两个**相互独立**的源对象；
10. 清单按**规范源顺序**（document_version→page→block_index→offset）建立，
    绝不受 content-addressed material_id 顺序影响；
11. 多行表头（3 行）由「与数据行同宽的叶子表头」确定性合并，不再一刀切 fail。
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


def _src(text: str, page: int = 1, block: int = 0, mat: str = "mat-a",
         dv: str = "v1", offset: int = 0) -> SOI.SourceText:
    return SOI.SourceText(
        SOI.SourcePosition(dv, page, block, offset, mat), text)


def _asm(title: str, assembly_id: str, status: str = "ok",
         comps: tuple = ("mat-a",), issue: str | None = None) -> dict:
    return {"assembly_id": assembly_id, "relation": "flattened_table_recovery",
            "component_material_ids": list(comps), "table_title": title,
            "recovery_status": status, "recovery_issue": issue}


def _persist(texts: list[str], comps: tuple = ("mat-a",)) -> list[dict]:
    """按真实落盘口径把 recover_flattened_tables 结果投影为 assembly dict（内容寻址身份）。"""
    out: list[dict] = []
    for t in recover_flattened_tables(texts):
        if not t.get("headers") or not t.get("rows"):
            continue  # 真实实现同样不产出无表头/无数据行的投影
        disc = json.dumps([t.get("title") or "", t.get("unit"),
                           list(t["headers"]), [list(r) for r in t["rows"]],
                           t.get("total_row")],
                          ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        out.append({
            "assembly_id": "asm-" + hashlib.sha256(disc.encode("utf-8")).hexdigest()[:32],
            "relation": "flattened_table_recovery",
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
    # 1. 普通标题不是表（关键词单独命中绝不构成表格起点）
    # ------------------------------------------------------------------
    heading_text = (
        "（二）主营业务情况\n\n"
        "公司主营业务为动力电池系统，产品覆盖乘用车与商用车领域。\n\n"
        "（三）各业务板块经营情况\n"
        "1、动力电池板块\n\n"
        "（1）整体情况\n"
        "公司动力电池产品包括电芯、模组及电池包。\n\n"
        "（四）安全生产情况\n"
        "公司高度重视安全生产工作。近三年及一期未发生重大安全生产事故。\n")
    inv_h = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(heading_text)])
    starts = [o.label for o in inv_h.expected_objects
              if o.kind == SOI.KIND_TABLE_START]
    check(starts == [],
          "普通标题不是表：（二）主营业务情况/（三）各业务板块经营情况/（1）整体情况/"
          "（四）安全生产情况 全部不进清单（复合结构信号：无结构跟随）")

    # ------------------------------------------------------------------
    # 2. 折行数据「有限公司」不是表题（前驱是「单位：」行/数据行）
    # ------------------------------------------------------------------
    wrapped = (
        "单位：千元\n"
        "  项目  期末余额  期初余额\n"
        " 宁普时代电池科技有  202,998  -2,098  -200,900\n"
        " 限公司\n"
        " 福建时代泽远股权投  700,000  700,000\n"
        " 资基金合伙企业（有\n"
        " 限合伙）\n")
    inv_w = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(wrapped)])
    check(not any(o.kind == SOI.KIND_TABLE_START for o in inv_w.expected_objects),
          "折行数据不是表题：单位行/数据行之下的「限公司」「限合伙）」不进清单")

    # ------------------------------------------------------------------
    # 3. 真表题必须被识别（复合结构信号成立）
    # ------------------------------------------------------------------
    real_title = (
        "（1） 应收票据分类列示\n\n"
        "单位：千元\n"
        "  项目  期末余额  期初余额\n"
        " 银行承兑票据  1,000  2,000\n"
        " 合计  1,000  2,000\n")
    inv_r = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(real_title)])
    check(any(o.kind == SOI.KIND_TABLE_START and "应收票据分类列示" in o.label
              for o in inv_r.expected_objects),
          "真表题被识别：无「表 N」但有单位行/表头/数据行/合计行 → table_start_signal")

    # ------------------------------------------------------------------
    # 4. 中介表题：显式「表 N」介入 → 其前的正文标题不得成为表题
    # ------------------------------------------------------------------
    intervening = (
        "（二）主营业务情况\n"
        "表 1 主营业务收入构成表\n"
        "单位：万元\n"
        "项目  金额  占比\n"
        "动力电池  100  100.0\n"
        "合计  100  100.0\n")
    inv_i = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(intervening)])
    kinds = {o.object_id: o.kind for o in inv_i.expected_objects}
    check(kinds.get("table:1") == SOI.KIND_TABLE_NUMBER,
          "中介表题：显式「表 1」仍进清单")
    check(not any(o.kind == SOI.KIND_TABLE_START for o in inv_i.expected_objects),
          "中介表题：正文标题「（二）主营业务情况」不成为表题（真正的表题在后面）")

    # ------------------------------------------------------------------
    # 5. inventory ok 但 assembly 缺失必须失败
    # ------------------------------------------------------------------
    four = (
        "表 5-10 发行人主营业务收入构成表\n单位：万元，%\n"
        "项目  金额  占比\n动力电池系统  31,650  74.7\n合计  42,370  100.0\n"
        "表 5-11 发行人主营业务成本构成表\n单位：万元，%\n"
        "项目  金额  占比\n原材料  8,000  70.0\n合计  11,000  100.0\n")
    inv5 = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(four)])
    rec5 = SOI.reconcile_source_object_inventory(inv5, [])
    r510 = next(r for r in rec5.recovery_results if r.object_id == "table:5-10")
    check(r510.result == SOI.TARGET_NOT_OBTAINED and not r510.assembly_id,
          "inventory ok 但 assembly 缺失：无持久化 assembly ⇒ target_not_obtained（绝不 ok）")
    gate5 = SOI.source_object_gate(rec5, [])
    check(gate5 is not None, "inventory ok 但 assembly 缺失：source_object_gate 失败")

    # 篡改：结果声明 recovered_ok 却不绑定 assembly_id → 必须失败（矛盾声明）。
    tampered = SOI.ExpectedSourceObjectInventory(
        aspect_id=_ASPECT, version=SOI.SOURCE_OBJECT_INVENTORY_VERSION,
        expected_objects=list(rec5.expected_objects),
        recovery_results=[SOI.SourceObjectRecoveryResult(
            r.object_id, SOI.RECOVERED_OK if r.object_id == "table:5-10" else r.result,
            r.matched_table, r.issue, r.assembly_id, r.component_material_ids)
            for r in rec5.recovery_results])
    check(SOI.inventory_assembly_closure(tampered, []) is not None,
          "篡改：recovered_ok 但未绑定 assembly_id → inventory_assembly_closure 失败")

    # ------------------------------------------------------------------
    # 6. inventory 结果与持久化 assembly 状态矛盾必须失败
    # ------------------------------------------------------------------
    asms6 = _persist([four], comps=("mat-a",))
    rec6 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(four)]), asms6)
    r6 = {r.object_id: r for r in rec6.recovery_results}
    check(r6["table:5-10"].result == SOI.RECOVERED_OK
          and r6["table:5-10"].assembly_id,
          "状态一致：assembly recovery_status=ok ⇒ 源对象恰好 recovered_ok（不得报 failed）")
    check(r6["table:5-10"].assembly_id == next(
        a["assembly_id"] for a in asms6 if "5-10" in a["table_title"]),
        "身份绑定：recovered_ok 绑定到真实持久化 assembly_id")
    check(SOI.inventory_assembly_closure(rec6, asms6) is None,
          "清单 ↔ assembly 双向闭合：绑定一致 → closure 通过")
    # 反向：assembly 标记 failed ⇒ 源对象必须 recovery_failed（不得自行报 ok）。
    asms6f = [dict(a, recovery_status="failed", recovery_issue="列数不一致")
              for a in asms6]
    rec6f = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(four)]), asms6f)
    check(all(r.result == SOI.RECOVERY_FAILED for r in rec6f.recovery_results
              if r.object_id.startswith("table:")),
          "状态一致：assembly failed ⇒ 源对象 recovery_failed（绝不自报 ok）")
    check(SOI.source_object_gate(rec6f, asms6f) is not None,
          "状态一致：failed assembly ⇒ gate 失败")
    # 篡改：把 ok 的 assembly 从清单里摘掉（assembly 侧仍在）→ closure 必须失败。
    stripped = SOI.ExpectedSourceObjectInventory(
        aspect_id=_ASPECT, version=SOI.SOURCE_OBJECT_INVENTORY_VERSION,
        expected_objects=list(rec6.expected_objects),
        recovery_results=[SOI.SourceObjectRecoveryResult(
            r.object_id, SOI.RECOVERED_OK, r.matched_table, r.issue,
            "" if r.object_id == "table:5-10" else r.assembly_id,
            r.component_material_ids) for r in rec6.recovery_results])
    check(SOI.inventory_assembly_closure(stripped, asms6) is not None,
          "篡改：inventory 声明 ok 但 assembly 绑定被删 → closure 失败")

    # ------------------------------------------------------------------
    # 7. assembly component 外键不存在必须失败
    # ------------------------------------------------------------------
    asms7 = _persist([four], comps=("mat-does-not-exist",))
    rec7 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(four)]), asms7,
        known_material_ids={"mat-a"})
    bad = [r for r in rec7.recovery_results if r.result == SOI.RECOVERY_FAILED]
    check(bad and all("外键不存在" in (r.issue or "") for r in bad),
          "component 外键不存在：assembly component material 不存在 → recovery_failed")
    check(SOI.source_object_gate(rec7, asms7) is not None,
          "component 外键不存在：source_object_gate 失败（fail-closed）")

    # ------------------------------------------------------------------
    # 8. 同块多表：不同且稳定的 object/assembly identity
    # ------------------------------------------------------------------
    two_in_one = (
        "表 5-10 发行人主营业务收入构成表\n单位：万元\n"
        "项目  金额\n动力电池系统  31,650\n合计  42,370\n"
        "表 5-11 发行人主营业务成本构成表\n单位：万元\n"
        "项目  金额\n原材料  8,000\n合计  11,000\n")
    asms8 = _persist([two_in_one], comps=("mat-block0",))
    check(len(asms8) == 2 and len({a["assembly_id"] for a in asms8}) == 2,
          "同块多表：同一 block 内两张表 → 两个**不同** assembly_id")
    rec8 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(two_in_one)]), asms8)
    ids8 = [r.assembly_id for r in rec8.recovery_results]
    check(len(ids8) == 2 and len(set(ids8)) == 2 and all(ids8),
          "同块多表：两个源对象各绑定不同 assembly_id（无身份合并）")
    check(SOI.inventory_assembly_closure(rec8, asms8) is None,
          "同块多表：清单 ↔ assembly 双向闭合")
    rec8b = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(two_in_one)]),
        _persist([two_in_one], comps=("mat-block0",)))
    check(rec8.to_dict() == rec8b.to_dict(),
          "同块多表：身份稳定（同输入 → 同 assembly_id 与同清单）")

    # ------------------------------------------------------------------
    # 9. 「详见表 5-10、表 5-11」→ 两个相互独立的源对象
    # ------------------------------------------------------------------
    multi_ref = ("表 5-10 发行人主营业务收入构成表\n单位：万元\n"
                 "项目  金额\n动力电池系统  31,650\n合计  42,370\n"
                 "详见表 5-10、表 5-11 的有关披露。\n")
    inv9 = SOI.derive_expected_source_object_inventory(_ASPECT, [_src(multi_ref)])
    xrefs = [o for o in inv9.expected_objects if o.kind == SOI.KIND_CROSS_REFERENCE]
    check(len(xrefs) == 2 and len({o.object_id for o in xrefs}) == 2,
          "多重引用：详见表 5-10、表 5-11 → 两个独立源对象（绝不合并目标）")
    # 只恢复 5-10 → 5-11 的引用独立地 target_not_obtained，5-10 的引用 recovered_ok。
    asms9 = _persist([multi_ref], comps=("mat-a",))
    rec9 = SOI.reconcile_source_object_inventory(inv9, asms9)
    x = {r.object_id: r.result for r in rec9.recovery_results
         if r.object_id.startswith("cross_ref:")}
    check(sorted(x.values()) == [SOI.RECOVERED_OK, SOI.TARGET_NOT_OBTAINED],
          "多重引用：两个引用各自独立裁决（一 ok 一 not_obtained，互不污染）")

    # ------------------------------------------------------------------
    # 10. 规范源顺序（document_version → page → block_index → offset）
    # ------------------------------------------------------------------
    tA = "表 5-10 A 表\n单位：万元\n项目  金额\n甲  1\n合计  1\n"
    tB = "表 5-11 B 表\n单位：万元\n项目  金额\n乙  2\n合计  2\n"
    inv_ord = SOI.derive_expected_source_object_inventory(_ASPECT, [
        _src(tB, page=9, block=0, mat="mat-0000000000000000000000000000000a"),
        _src(tA, page=2, block=0, mat="mat-ffffffffffffffffffffffffffffffff"),
    ])
    order = [o.material_id for o in inv_ord.expected_objects]
    check(order == ["mat-ffffffffffffffffffffffffffffffff",
                    "mat-0000000000000000000000000000000a"],
          "规范源顺序：清单按页码升序（非 content-addressed material_id 升序）")
    inv_ord_b = SOI.derive_expected_source_object_inventory(_ASPECT, [
        _src(tA, page=2, block=0, mat="mat-ffffffffffffffffffffffffffffffff"),
        _src(tB, page=9, block=0, mat="mat-0000000000000000000000000000000a"),
    ])
    check(inv_ord.to_dict() == inv_ord_b.to_dict(),
          "规范源顺序：入参顺序不同 → 同一清单（顺序稳定）")

    # ------------------------------------------------------------------
    # 11. 多行表头（3 行）由「与数据行同宽的叶子表头」确定性合并
    # ------------------------------------------------------------------
    three_header = (
        "表5-12发行人主营业务毛利润及毛利率构成表\n\n"
        "单位：万元，%\n"
        "  2025年  2024年度  2023年度\n"
        "  项目  金额  占比  毛利  金额  占比  毛利  金额  占比  毛利\n"
        "  率  率  率\n"
        "动力电池系统  7,544,197.267.8 23.8 6,058,005.568.523.9  6,353,872.469.222.3\n"
        "  合计  11,131,853.6100.026.38,849,359.4100.024.4 9,184,661.1100.022.9\n")
    tables11 = recover_flattened_tables([three_header])
    check(len(tables11) == 1 and tables11[0].get("recovery_status") == "ok",
          "多行表头：3 行表头 → 取与数据行同宽的叶子表头 → recovery_status=ok")
    t11 = tables11[0]
    check(t11.get("headers") is not None
          and len(t11["headers"]) == len(t11["rows"][0]),
          "多行表头：表头列数 == 数据行列数（列结构可靠）")
    check(t11.get("dropped_header_lines") == 2,
          "多行表头：被丢弃的分组表头行数显式落盘（审计可见，不静默）")
    asms11 = _persist([three_header], comps=("mat-a",))
    rec11 = SOI.reconcile_source_object_inventory(
        SOI.derive_expected_source_object_inventory(_ASPECT, [_src(three_header)]),
        asms11)
    check(SOI.source_object_gate(rec11, asms11) is None,
          "多行表头：表 5-12 逐项闭合 → source_object_gate 通过（None）")

    # ------------------------------------------------------------------
    # 12. 四态分布：每个源对象唯一归属
    # ------------------------------------------------------------------
    counts = SOI.reconciliation_summary(rec6)
    check(sum(counts.values()) == len(rec6.expected_objects),
          "四态分布：计数总和 == 期望源对象数（逐对象唯一归属）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
