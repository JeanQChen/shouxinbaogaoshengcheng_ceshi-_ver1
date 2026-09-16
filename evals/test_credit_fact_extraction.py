"""Eval: R2 §12 修复 E —— 授信事实从真实材料派生（无手写 _FACTS）。

用法: python -m evals.test_credit_fact_extraction

覆盖：
- ``extract_credit_facts`` 从真实材料 text 确定性派生授信事实（拟申请上限/实际获批总额/已使用/
  尚未使用），每条携带 provenance（evidence_id/material_id/document_id+version/locator/双哈希）；
- ``recompute_authority`` 复用正式权威链（payload 字节 sha256、source_content_hash==content_hash、
  正式 evidence_id 算法、document/current-set/locator 一致）重算权威（绝不手写 True）；
- 伪造 64-hex 双哈希 + authority verdict / payload 文本篡改但 hash 未改 / 缺 payload 文件 → invalid；
- sentinel（outside_boundary_sentinel）含授信措辞也不得提取；
- 实际获批授信总额（actual_granted_total_credit_line）可识别；
- 完整报告期保留（2025-06-30 不压缩为 2025-12-31）；
- 标量不可靠提取（同最新报告期多值冲突）→ not_obtained（value=None，显式缺口）；
- 派生事实经 ``credit_aspect_dual_axis`` 仍得到正确双轴：total_credit_line=not_obtained、
  used_credit=not_obtained（E.7 裸「亿」）、unused_credit=scope_qualified、
  authorized_application_ceiling=scope_qualified（E.6 as_of 空）。
- 缺口/冲突事实与正常事实**同一字段集**（反例：缺口事实曾缺 ``scope_closed`` 字段，使
  ``f.get("scope_closed", True)`` 把显式缺口默认成「口径闭合」，把 used_credit 缺口判成
  supports；缺口一律不得被默认成闭合）。

全部离线：纯函数，零 LLM/网络/DB。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids
from harness.credit_authority import ResolvedMaterial as _RM
from harness.credit_fact_extraction import (
    extract_credit_facts,
    recompute_authority,
)
from harness.credit_semantics import (
    SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL,
    SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
    SEMANTIC_TYPE_UNUSED_CREDIT,
    SEMANTIC_TYPE_USED_CREDIT,
    SUPPORT_NOT_OBTAINED,
    SUPPORT_SCOPE_QUALIFIED,
    SUPPORT_SUPPORTS,
    credit_aspect_dual_axis,
)


def _mat(material_id, document_id, document_version, page, section_path, text, *,
         authority_verdict="authoritative", company_id="300750",
         evidence_set_version="evidence-set-v1", block_index=0,
         disposition=None, structured_payload=None,
         tamper_text=None, forged_hashes=False, omit_payload=False):
    """构造一条**正式权威链自洽**的材料（复用 evidence.ids 重算，绝不手写哈希）。

    - ``tamper_text``：仅篡改材料 text 字段（hash/envelope 未改）→ content_hash 重算不符（#10）。
    - ``forged_hashes``：src/payload hash 与 evidence_id 全部伪造（不随字节重算）→ #11。
    - ``omit_payload``：不附 payload 字节 → 缺 payload 文件（#12）。
    """
    src_hash = ids.content_hash(text, structured_payload)
    locator = {
        "document_id": document_id,
        "document_version": document_version,
        "page": page,
        "block_range": [block_index, block_index],
        "section_path": section_path,
    }
    document_identity = {
        "company_id": company_id,
        "document_id": document_id,
        "document_version": document_version,
        "evidence_set_version": evidence_set_version,
    }
    evidence_id = ids.make_evidence_id(
        company_id, document_id, document_version, evidence_set_version,
        page, block_index, src_hash)
    envelope = {
        "material_payload_version": 1,
        "object_type": "evidence_span",
        "authority_identity": f"evidence:{evidence_id}",
        "evidence_id": evidence_id,
        "source_content_hash": src_hash,
        "created_dependency_fingerprint": "d" * 64,
        "locator": locator,
        "document_identity": document_identity,
        "content": {"text": text, "structured_payload": structured_payload},
    }
    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    final_text = tamper_text if tamper_text is not None else text
    final_src = "c" * 64 if forged_hashes else src_hash
    final_payload_hash = "d" * 64 if forged_hashes else payload_hash
    final_evidence_id = "e" * 32 if forged_hashes else evidence_id

    mat = {
        "material_id": material_id,
        "evidence_id": final_evidence_id,
        "company_id": company_id,
        "document_id": document_id,
        "document_version": document_version,
        "evidence_set_version": evidence_set_version,
        "source_content_hash": final_src,
        "payload_hash": final_payload_hash,
        "authority_verdict": authority_verdict,
        "locator": locator,
        "text": final_text,
        "structured_payload": structured_payload,
        "disposition": disposition,
    }
    if not omit_payload:
        mat["payload_bytes"] = payload_bytes
    return mat


# 真实形态文本（来自 NDSD_KCZ_2026 p111 + NDSD_2025_year p210，含 2024/2025 双年度）。
_P111 = (
    "议案公告：2024年度公司及其控股子公司拟向相关金融机构申请不超过人民币5,600亿元的综合授信额度。"
    "截至2024年末，公司及控股子公司未使用银行授信额度3,441亿元。"
    "议案公告：2025年度公司及控股子公司拟向相关金融机构新增申请不超过人民币6,000亿元的综合授信额度。"
    "截至2025年末，公司及控股子公司授信额度已使用2918.37亿。")
_P210 = "于 2025 年 12 月 31 日，本公司尚未使用的银行借款额度为 3,655亿元（2024 年 12 月 31日 3,441亿元）。"


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
    # 1. 从真实材料派生 3 条事实（无手写 _FACTS）+ provenance
    # ------------------------------------------------------------------
    mat1 = _mat("mat-p111", "NDSD_KCZ_2026", "sha256-2b3a1fb3de97f23c",
                111, "发行人资信状况", _P111)
    mat2 = _mat("mat-p210", "NDSD_2025_year", "sha256-c15272977147dee7",
                210, "与金融工具相关的风险", _P210)
    facts = extract_credit_facts([mat1, mat2])

    check(len(facts) == 3, "从真实材料派生 3 条事实（拟申请上限/已使用/尚未使用）")
    by_type = {f["semantic_type"]: f for f in facts}

    ceiling = by_type.get(SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING)
    check(ceiling is not None and ceiling["value"] == "6,000亿元",
          "拟申请上限 = 6,000亿元（最新 2025 年度，非 2024 的 5,600亿元）")
    check(ceiling is not None and ceiling["scope_closed"] is False
          and ceiling["facility_scope"] == "综合授信额度"
          and ceiling["entity_scope"] == "公司及控股子公司"
          and ceiling["currency"] == "CNY",
          "拟申请上限口径未闭合（E.6：as_of 空 → 空字段不得闭合；facility/entity/currency 正确）")

    used = by_type.get(SEMANTIC_TYPE_USED_CREDIT)
    check(used is not None and used["value"] is None and used.get("not_obtained") is True,
          "已使用：真实原文为裸「亿」（无 人民币/元）→ 币种不明确 → not_obtained（E.7，不回填）")
    check(used is not None and "币种不明确" in (used.get("not_obtained_reason", "") or ""),
          "已使用：缺口原因是「币种不明确」（E.7）")

    unused = by_type.get(SEMANTIC_TYPE_UNUSED_CREDIT)
    check(unused is not None and unused["value"] == "3,655亿元",
          "尚未使用 = 3,655亿元（2025-12-31，非 2024 的 3,441亿元）")
    check(unused is not None and unused["scope_closed"] is False
          and unused["facility_scope"] == "银行借款额度"
          and unused["entity_scope"] == "公司"
          and unused["document"] == "NDSD_2025_year",
          "尚未使用口径未闭合（银行借款额度 × 公司 × NDSD_2025_year）")

    # 字段集一致（反例：缺口事实曾缺 scope_closed，导致下游 `f.get("scope_closed", True)`
    # 把显式缺口默认成「口径闭合」，把 used_credit 缺口聚合判成 supports/口径闭合）。
    _UNIFORM_KEYS = ("scope_closed", "currency", "as_of", "period", "entity_scope",
                     "facility_scope", "consolidation", "document")
    check(all(all(k in f and f[k] is not None for k in _UNIFORM_KEYS) for f in facts),
          "缺口/正常事实字段集一致且 scope_closed 非 None（不得被下游默认成闭合）")
    check(used is not None and used["scope_closed"] is False,
          "已使用（显式缺口）scope_closed=False → 缺口绝不被当成口径闭合")
    check(all(not (f["not_obtained"] and f["scope_closed"]) for f in facts),
          "任何缺口事实都不得同时是「口径闭合」")

    # provenance：每条事实携带 evidence_id/material_id/document_id+version/locator/双哈希。
    for f in facts:
        check(all(f.get(k) for k in (
            "evidence_id", "material_id", "document_id", "document_version",
            "locator", "source_content_hash", "payload_hash")),
            f"provenance 完整（{f['semantic_type']}）")
        check(len(f["source_content_hash"]) == 64 and len(f["payload_hash"]) == 64
              and f["source_content_hash"] != f["payload_hash"],
              f"双哈希 64-hex 且互异（{f['semantic_type']}）")

    # ------------------------------------------------------------------
    # 2. 派生事实 → 双轴（端到端语义仍正确）
    # ------------------------------------------------------------------
    fact_axes = tuple({"semantic_type": f["semantic_type"],
                       "authority_valid": f["authority_valid"],
                       "authority_reason": "",
                       "scope_closed": f.get("scope_closed", False),
                       "value": f.get("value"),
                       "not_obtained": bool(f.get("not_obtained")),
                       "conflict_status": f.get("conflict_status", "")} for f in facts)
    st_tot = credit_aspect_dual_axis("company_debt_credit.total_credit_line", fact_axes)
    check(st_tot["semantic_status"] == SUPPORT_NOT_OBTAINED
          and st_tot["authority_status"] == "valid",
          "派生事实 → total_credit_line = not_obtained（非 authority_failed）")
    st_used = credit_aspect_dual_axis("company_debt_credit.used_credit", fact_axes)
    check(st_used["semantic_status"] == SUPPORT_NOT_OBTAINED,
          "派生事实 → used_credit = not_obtained（E.7 裸「亿」币种不明确 → 显式缺口）")
    st_unused = credit_aspect_dual_axis("company_debt_credit.unused_credit", fact_axes)
    check(st_unused["semantic_status"] == SUPPORT_SCOPE_QUALIFIED,
          "派生事实 → unused_credit = scope_qualified")
    st_auth = credit_aspect_dual_axis(
        "company_debt_credit.authorized_application_ceiling", fact_axes)
    check(st_auth["semantic_status"] == SUPPORT_SCOPE_QUALIFIED,
          "派生事实 → authorized_application_ceiling = scope_qualified（E.6：as_of 空）")

    # ------------------------------------------------------------------
    # 3. recompute_authority 反例：绝不手写 True（复用正式权威链）
    # ------------------------------------------------------------------
    ok, reason = recompute_authority(mat1)
    check(ok and reason == "", "权威重算：正式权威链身份自洽 → valid")
    check(recompute_authority(
        _mat("m", "D", "v", 1, "s", "t", authority_verdict="rejected"))[0] is False,
        "权威重算：authority_verdict 非 authoritative → invalid")
    check(recompute_authority(
        _mat("m", "D", "v", 1, "s", "t", forged_hashes=True))[0] is False,
        "权威重算#11：伪造 64-hex 双哈希与 authority verdict → invalid（字节重算不符）")
    check(recompute_authority(
        _mat("m", "D", "v", 1, "s", "原始文本", tamper_text="被篡改的文本"))[0] is False,
        "权威重算#10：payload 文本被篡改但 hash 未改 → invalid（content_hash 重算不符）")
    check(recompute_authority(
        _mat("m", "D", "v", 1, "s", "t", omit_payload=True))[0] is False,
        "权威重算#12：缺 payload 文件 → invalid")

    # 权威无效的材料不参与事实提取。
    facts_bad = extract_credit_facts([
        _mat("m1", "NDSD_KCZ_2026", "v", 111, "发行人资信状况", _P111,
             authority_verdict="rejected")])
    check(facts_bad == [], "权威无效材料 → 不产出事实（fail-closed）")

    # ------------------------------------------------------------------
    # 4. 标量不可靠提取 → not_obtained
    # ------------------------------------------------------------------
    # 同最新报告期两个不同值 → 多值冲突 → not_obtained。
    ambiguous = _mat("m-a", "NDSD_KCZ_2026", "v", 1, "s",
                     "2025年度拟申请不超过人民币6,000亿元的综合授信额度。"
                     "2025年度拟申请不超过人民币7,000亿元的综合授信额度。")
    facts_amb = extract_credit_facts([ambiguous])
    amb = next((f for f in facts_amb if f.get("not_obtained")), None)
    check(amb is not None and amb["value"] is None
          and amb["semantic_type"] == SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
          "标量不可靠（同最新报告期多值冲突）→ not_obtained（value=None）")
    check(amb is not None and amb.get("conflict_status") == "multi_source_conflict",
          "E.8：多源值冲突 → 独立 conflict_status=multi_source_conflict")
    check(amb is not None and amb.get("authority_valid") is True,
          "E.8：多源值冲突不降为 authority invalid（authority_valid=True）")
    check(amb is not None and len(amb.get("competing_values", [])) == 2
          and {cv["value"] for cv in amb["competing_values"]} == {"6,000亿元", "7,000亿元"},
          "E.8：保留竞争值（6,000亿元 / 7,000亿元）及各自口径/provenance")

    # ------------------------------------------------------------------
    # 5. 确定性：同输入同输出
    # ------------------------------------------------------------------
    check(extract_credit_facts([mat1, mat2]) == facts,
          "确定性：同输入 → 同事实列表")

    # ------------------------------------------------------------------
    # 6. 反例#13：sentinel（outside_boundary_sentinel）含授信措辞也不得提取
    # ------------------------------------------------------------------
    sentinel = _mat("m-s", "NDSD_KCZ_2026", "v", 1, "s",
                    "不超过人民币6,000亿元的综合授信额度。",
                    disposition="outside_boundary_sentinel")
    check(extract_credit_facts([sentinel]) == [],
          "反例#13：sentinel 含授信措辞也不得提取（非正式材料不产出事实）")

    # ------------------------------------------------------------------
    # 7. 反例#14/#16：实际获批授信总额可识别 + 报告期保留完整
    # ------------------------------------------------------------------
    actual = _mat("m-g", "NDSD_KCZ_2026", "v", 1, "s",
                  "截至2025年6月30日，公司实际获批授信总额为8,000亿元。")
    facts_g = extract_credit_facts([actual])
    g = next((f for f in facts_g
              if f["semantic_type"] == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL), None)
    check(g is not None and g["value"] == "8,000亿元",
          "反例#14：实际获批授信总额可识别（actual_granted_total_credit_line）")
    check(g is not None and g["period"] == "2025-06-30",
          "反例#16：2025-06-30 保持 2025-06-30（不压缩为 2025-12-31）")

    # ------------------------------------------------------------------
    # 8. E.5：金额+币种联合解析（10亿美元 绝不写成 10亿元/CNY）
    # ------------------------------------------------------------------
    usd = _mat("m-usd", "NDSD_KCZ_2026", "v", 1, "s",
               "2025年度拟申请不超过10亿美元的综合授信额度。")
    facts_usd = extract_credit_facts([usd])
    usd_f = next((f for f in facts_usd
                  if f["semantic_type"] == SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING
                  and not f.get("not_obtained")), None)
    check(usd_f is not None and usd_f["value"] == "10亿美元"
          and usd_f["currency"] == "USD",
          "E.5：10亿美元 保持 USD（value=10亿美元，非 10亿元/CNY）")
    check(usd_f is not None and usd_f["scope_closed"] is False,
          "E.5：非 CNY 不自动改写为 CNY（USD 口径不闭合）")

    # 币种冲突（人民币 + 美元）→ 币种不明确 → 显式缺口。
    unclear = _mat("m-uc", "NDSD_KCZ_2026", "v", 1, "s",
                   "不超过人民币10亿美元的综合授信额度。")
    facts_uc = extract_credit_facts([unclear])
    uc_f = next((f for f in facts_uc if f.get("not_obtained")), None)
    check(uc_f is not None and "币种不明确" in (uc_f.get("not_obtained_reason", "") or ""),
          "E.5：币种不明确（人民币+美元冲突）→ 显式缺口（不回填）")

    # ------------------------------------------------------------------
    # 9. E.9：扩展实际总额识别，且不把拟申请上限回填为实际
    # ------------------------------------------------------------------
    actual2 = _mat("m-g2", "NDSD_KCZ_2026", "v", 1, "s",
                   "截至2025年6月30日，公司实际授信总额为8,000亿元。")
    facts_g2 = extract_credit_facts([actual2])
    g2 = next((f for f in facts_g2
               if f["semantic_type"] == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL), None)
    check(g2 is not None and g2["value"] == "8,000亿元",
          "E.9：实际授信总额（新增表述）可识别为 actual_granted_total_credit_line")

    ceiling_only = _mat("m-c", "NDSD_KCZ_2026", "v", 1, "s",
                        "2025年度拟申请不超过人民币6,000亿元的综合授信额度。")
    facts_c = extract_credit_facts([ceiling_only])
    check(not any(f["semantic_type"] == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL for f in facts_c),
          "E.9：拟申请上限绝不回填为实际获批总额（ceiling 不产出 actual 事实）")

    # ------------------------------------------------------------------
    # 10. E.4：fragment 事实提取不得接受可变 dict 中后来替换的 text
    #     （必须从已验证 payload envelope 重取正文）
    # ------------------------------------------------------------------
    swap = _mat("m-swap", "NDSD_KCZ_2026", "v", 1, "s",
                "2025年度拟申请不超过人民币6,000亿元的综合授信额度。")
    swap["text"] = "2025年度拟申请不超过人民币9,999亿元的综合授信额度。"  # 解析后替换
    facts_swap = extract_credit_facts([swap])
    swap_f = next((f for f in facts_swap
                   if f["semantic_type"] == SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING), None)
    check(swap_f is None or swap_f.get("value") == "6,000亿元",
          "E.4：材料 dict 的 text 被事后替换 → 事实仍取权威 payload envelope 正文（非 9,999亿）")

    # ------------------------------------------------------------------
    # 11. E.7：裸「亿」无可靠币种上下文 → 不得默认 CNY
    # ------------------------------------------------------------------
    bare = _mat("m-bare", "NDSD_KCZ_2026", "v", 1, "s",
                "截至2025年末，公司及控股子公司授信额度已使用2918.37亿。")
    facts_bare = extract_credit_facts([bare])
    bare_f = next((f for f in facts_bare
                   if f["semantic_type"] == SEMANTIC_TYPE_USED_CREDIT), None)
    check(bare_f is not None and bare_f["value"] is None and bare_f.get("not_obtained") is True,
          "E.7：裸「亿」（无 人民币/元/外币标记）→ 币种不明确 → not_obtained（绝不默认 CNY）")
    check(bare_f is not None and "币种不明确" in (bare_f.get("not_obtained_reason", "") or ""),
          "E.7：裸「亿」缺口原因是「币种不明确」（显式缺口，不回填）")
    bare_bad = next((f for f in facts_bare
                     if f["semantic_type"] == SEMANTIC_TYPE_USED_CREDIT
                     and f.get("currency") == "CNY"), None)
    check(bare_bad is None, "E.7：裸「亿」绝不产出 currency=CNY 的事实")
    # 可靠上下文（含「元」或「人民币」）仍判定为 CNY（不误伤合法路径）。
    ok_ctx = _mat("m-ok", "NDSD_KCZ_2026", "v", 1, "s",
                  "截至2025年末，公司及控股子公司授信额度已使用2918.37亿元。")
    ok_f = next((f for f in extract_credit_facts([ok_ctx])
                 if f["semantic_type"] == SEMANTIC_TYPE_USED_CREDIT), None)
    check(ok_f is not None and ok_f["value"] == "2918.37亿元" and ok_f["currency"] == "CNY",
          "E.7：含「元」的匹配域（可靠上下文）仍判定 CNY")

    # ------------------------------------------------------------------
    # 12. E.4：fragment offset 非严格内部 / 片段正文与 payload 不符 → 不可采纳
    # ------------------------------------------------------------------
    for bad_off in (-1, 0):
        fd = _mat("m-frag", "NDSD_KCZ_2026", "v", 1, "s", "片段正文。")
        fd["locator"] = dict(fd["locator"], offset=bad_off)
        check(extract_credit_facts([fd]) == [],
              f"E.4：offset={bad_off} 非严格内部截断点 → 材料不可采纳（不产出事实）")
    check(getattr(_RM, "__dataclass_params__").frozen is True,
          "E.4：ResolvedMaterial 为不可变（frozen）typed 材料，正文不可事后替换")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
