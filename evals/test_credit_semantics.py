"""Eval: 授信口径 P0 语义解耦双轴状态（P1-5）。

用法: python -m evals.test_credit_semantics

覆盖（3 个反例 + 语义归类 + 文本纠正 + 后继 changelist）：
- 反例#1：6,000亿 权威有效但对「实际获批授信总额/total_credit_line」是 semantic_mismatch（绝不
  authority_failed）。
- 反例#2：total_credit_line = 申请上限 obtained + 实际获批总额 not_obtained（绝不 authority_failed）。
- 反例#3：used_credit 与 unused_credit 绝不求和/求差（scope_not_reconciled）；unused 口径未闭合 →
  scope_qualified。
- classify_semantic_type 确定性归类 + 文本纠正逐字断言 + 后继 changelist 新增
  authorized_application_ceiling（supporting，非 REPORT_BLOCKED）。

全部离线：纯函数，零 LLM/网络/DB。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.credit_semantics import (
    AUTHORITY_VALID,
    NOT_OBTAINED_WORDING,
    SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
    SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
    SEMANTIC_TYPE_UNUSED_CREDIT,
    SEMANTIC_TYPE_USED_CREDIT,
    SUPPORT_MISMATCH,
    SUPPORT_NOT_OBTAINED,
    SUPPORT_SCOPE_QUALIFIED,
    SUPPORT_SUPPORTS,
    SUCCESSOR_AUTHORIZED_APPLICATION_CEILING,
    TEXT_CORRECTION_CORRECT,
    TEXT_CORRECTION_WRONG,
    classify_semantic_type,
    credit_aspect_dual_axis,
    credit_dependency_fingerprint,
    dual_axis_for_fact,
    reconcile_used_unused,
    successor_changelist,
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
    # 1. classify_semantic_type：确定性归类（无 LLM）
    # ------------------------------------------------------------------
    check(classify_semantic_type(
        "2025年度股东会批准不超过人民币 6,000亿元 的综合授信额度")
        == SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
        "归类：股东会「不超过…综合授信额度」→ authorized_application_ceiling")
    check(classify_semantic_type("授信额度已使用 2918.37 亿") == SEMANTIC_TYPE_USED_CREDIT,
          "归类：「已使用」→ used_credit")
    check(classify_semantic_type("尚未使用的银行借款额度 3,655亿元") == SEMANTIC_TYPE_UNUSED_CREDIT,
          "归类：「尚未使用」→ unused_credit")
    check(classify_semantic_type("实际获批授信总额 8,000亿") == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
          "归类：「实际获批」→ actual_granted_total_credit_line")
    check(classify_semantic_type("实际授信总额 8,000亿") == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
          "归类：「实际授信」→ actual_granted_total_credit_line（E.9 扩展识别）")
    check(classify_semantic_type("获批授信额度 8,000亿") == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
          "归类：「获批授信」→ actual_granted_total_credit_line（E.9 扩展识别）")

    # ------------------------------------------------------------------
    # 2. 反例#1：6,000亿 权威有效但语义类型 ≠ 目标 → semantic_mismatch（绝不 authority_failed）
    # ------------------------------------------------------------------
    ax = dual_axis_for_fact(
        "company_debt_credit.actual_granted_total_credit_line",
        semantic_type=SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
        authority_valid=True)
    check(ax.authority_status == AUTHORITY_VALID,
          "反例#1：6000亿 来源权威有效（authority_status=valid）")
    check(ax.semantic_status == SUPPORT_MISMATCH,
          "反例#1：6000亿 对实际获批总额是 semantic_mismatch（非 authority_failed）")
    check("authority_failed" not in ax.semantic_status and ax.authority_status != "invalid",
          "反例#1：语义误配绝不写成 authority_failed")

    # 对冻结 total_credit_line（总授信）同样：权威有效 + 语义误配，不降为权威失败。
    ax_tot = dual_axis_for_fact(
        "company_debt_credit.total_credit_line",
        semantic_type=SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
        authority_valid=True)
    check(ax_tot.authority_status == AUTHORITY_VALID
          and ax_tot.semantic_status == SUPPORT_MISMATCH,
          "反例#1b：6000亿 对 total_credit_line 权威有效 + semantic_mismatch（不标 authority_failed）")

    # ------------------------------------------------------------------
    # 3. 反例#2：total_credit_line = 申请上限 obtained + 实际获批总额 not_obtained
    #    （绝不 authority_failed）
    # ------------------------------------------------------------------
    st = credit_aspect_dual_axis(
        "company_debt_credit.total_credit_line",
        ({"semantic_type": SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
          "authority_valid": True, "scope_closed": True},))
    check(st["authority_status"] == AUTHORITY_VALID,
          "反例#2：total_credit_line 来源权威有效")
    check(st["semantic_status"] == SUPPORT_NOT_OBTAINED,
          "反例#2：total_credit_line = 申请上限 obtained + 实际获批总额 not_obtained")
    check(st["semantic_status"] != "authority_failed",
          "反例#2：实际获批总额缺口是 not_obtained，绝不标 authority_failed")
    check(NOT_OBTAINED_WORDING in st["semantic_reason"],
          "E.10：not_obtained 措辞含「在本轮已纳入材料及检索范围内未取得」")
    check("无披露" not in st["semantic_reason"],
          "E.10：not_obtained 措辞绝不写「无披露」")
    st_empty = credit_aspect_dual_axis("company_debt_credit.total_credit_line", ())
    check(st_empty["semantic_status"] == SUPPORT_NOT_OBTAINED
          and NOT_OBTAINED_WORDING in st_empty["semantic_reason"],
          "E.10：无权威有效事实 → not_obtained 也用「在本轮已纳入材料及检索范围内未取得」")

    # ------------------------------------------------------------------
    # 4. 反例#3：used/unused 绝不求和/求差 + unused 口径未闭合 → scope_qualified
    # ------------------------------------------------------------------
    used = {"entity_scope": "公司及控股子公司", "facility_scope": "综合授信额度",
            "document": "NDSD_KCZ_2026", "value": "2,918.37亿元"}
    unused = {"entity_scope": "公司", "facility_scope": "银行借款额度",
              "document": "NDSD_2025_year", "value": "3,655亿元"}
    check(reconcile_used_unused(used, unused) == "scope_not_reconciled",
          "反例#3：used 与 unused 口径不一致 → scope_not_reconciled（不求和/求差）")
    # 同口径 → reconciled（证明函数不是无条件拒绝）。
    check(reconcile_used_unused(used, dict(used)) == "reconciled",
          "反例#3：同口径 → reconciled")
    check(reconcile_used_unused({}, {}) == "scope_not_reconciled",
          "E.7：used/unused 双方皆空 → scope_not_reconciled（空==空 不算对账）")
    check(reconcile_used_unused({"entity_scope": "公司及控股子公司"}, {}) == "scope_not_reconciled",
          "E.7：一方空另一方非空 → scope_not_reconciled")
    # unused 口径未闭合 → scope_qualified（partial），非完整 obtained。
    ax_unused = dual_axis_for_fact(
        "company_debt_credit.unused_credit",
        semantic_type=SEMANTIC_TYPE_UNUSED_CREDIT, authority_valid=True,
        scope_closed=False)
    check(ax_unused.authority_status == AUTHORITY_VALID
          and ax_unused.semantic_status == SUPPORT_SCOPE_QUALIFIED,
          "反例#3：unused_credit 口径未闭合 → scope_qualified（partial）")
    # 口径闭合时 → supports（合法路径不破坏）。
    ax_used = dual_axis_for_fact(
        "company_debt_credit.used_credit",
        semantic_type=SEMANTIC_TYPE_USED_CREDIT, authority_valid=True, scope_closed=True)
    check(ax_used.semantic_status == SUPPORT_SUPPORTS,
          "反例#3b：used_credit 语义相符 + 口径闭合 → supports")

    # ------------------------------------------------------------------
    # 4b. 反例#15：完整口径对账（entity/facility/consolidation/currency/as_of/period）
    # ------------------------------------------------------------------
    used_2025 = {"entity_scope": "公司及控股子公司", "facility_scope": "综合授信额度",
                 "document": "NDSD_KCZ_2026", "consolidation": "consolidated",
                 "currency": "CNY", "as_of": "2025-12-31", "period": "2025",
                 "value": "2,918.37亿元"}
    unused_2024 = {"entity_scope": "公司及控股子公司", "facility_scope": "综合授信额度",
                   "document": "NDSD_KCZ_2026", "consolidation": "consolidated",
                   "currency": "USD", "as_of": "2024-12-31", "period": "2024",
                   "value": "3,655亿元"}
    check(reconcile_used_unused(used_2025, unused_2024) == "scope_not_reconciled",
          "反例#15：used=2025/CNY、unused=2024/USD → scope_not_reconciled"
          "（period/currency 不一致，绝不求和/求差）")
    check(reconcile_used_unused(used_2025, dict(used_2025)) == "reconciled",
          "反例#15b：同完整口径（含 consolidation/currency/as_of/period）→ reconciled")

    # ------------------------------------------------------------------
    # 5. 文本纠正 + 后继 changelist
    # ------------------------------------------------------------------
    check(TEXT_CORRECTION_CORRECT
          == "当前 Contract 缺少独立的申请额度上限 aspect，旧 evaluation 绑定发生语义误配",
          "文本纠正：正确表述逐字一致")
    check(TEXT_CORRECTION_WRONG != TEXT_CORRECTION_CORRECT,
          "文本纠正：错误表述（Contract 把 6000亿 定义成总授信）与正确表述区分")
    cl = successor_changelist()
    check(cl["text_correction"]["correct"] == TEXT_CORRECTION_CORRECT
          and cl["text_correction"]["wrong"] == TEXT_CORRECTION_WRONG,
          "changelist：text_correction 正确/错误表述逐字一致")
    check(cl["add_aspect"]["aspect_id"] == SUCCESSOR_AUTHORIZED_APPLICATION_CEILING,
          "changelist：新增 company_debt_credit.authorized_application_ceiling")
    check(cl["add_aspect"].get("blocking_policy") == "supporting",
          "changelist：authorized_application_ceiling blocking_policy = supporting")
    check("non_blocking" in (cl["add_aspect"].get("missing_policy", "") or ""),
          "changelist：authorized_application_ceiling 缺失不阻断（NOT REPORT_BLOCKED）")
    check(any("绝不求和/求差" in r for r in cl["hard_rules"]),
          "changelist：hard_rules 含 used/unused 绝不求和/求差")
    check(any("绝不标 authority_failed" in r for r in cl["hard_rules"]),
          "changelist：hard_rules 含实际获批总额缺口绝不标 authority_failed")

    # ------------------------------------------------------------------
    # 6. E.11：credit_dependency_fingerprint 绑定三版本（bump 版本 → 指纹变化）
    # ------------------------------------------------------------------
    fp = credit_dependency_fingerprint(extraction_version="2", semantics_version="2",
                                       authority_version="1")
    check(len(fp) == 64 and all(c in "0123456789abcdef" for c in fp),
          "E.11：依赖指纹为 64-hex")
    check(fp == credit_dependency_fingerprint(extraction_version="2", semantics_version="2",
                                              authority_version="1"),
          "E.11：依赖指纹确定性（同输入同输出）")
    check(fp != credit_dependency_fingerprint(extraction_version="1", semantics_version="2",
                                              authority_version="1"),
          "E.11：bump extractor 版本 → 依赖指纹变化（旧事实可区分）")
    check(fp != credit_dependency_fingerprint(extraction_version="2", semantics_version="1",
                                              authority_version="1"),
          "E.11：bump semantics 版本 → 依赖指纹变化")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
