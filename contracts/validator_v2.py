"""Contract v2 校验器：对 ContractV2 对象做纯声明式校验。

覆盖 R1-A 授权书 §十二 的可机器验收项：
- 52 问全部存在且执行归属 28/13/3/8；
- aspect_id / question_id 全局唯一；
- 每个 aspect 22 字段完整；
- coverage_rules 可组合且 NA 不作为 coverage mode；
- 五类时间政策 / evidence / source policy 引用均可解析；
- 负面事件核心/扩展字段规则正确；
- 三道 section-derived 仅回指同章，八道 synth 阶段归属正确。

本模块不 import 正式 service / runtime / Worker / Writer，不写库，不调用 LLM。

CLI：
    python -m contracts.validator_v2 templates/contracts/standard_v3.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from contracts import schema_v2 as S
from contracts.loader_v2 import load_contract_v2

logger = logging.getLogger("contracts.validator_v2")

# 52 问执行归属（权威，R1-A §四）
_EXPECTED_PRODUCER_COUNTS = {
    "topic_harness": 28,
    "financial_workflow": 13,
    "phase4_section_derived": 3,
    "phase5_synthesizer": 8,
}
_EXPECTED_QUESTION_COUNT = 52


def _section_id_of(contract: S.ContractV2, aspect_id: str) -> str | None:
    for sec in contract.sections:
        for a in sec.all_aspects():
            if a.aspect_id == aspect_id:
                return sec.section_id
    return None


def _validate_transmission_authority(contract: S.ContractV2, errors: list[str]) -> None:
    """传导 Evidence 权威约束（R1-A §二）：四层各自的 authority 结构必须可机器判定。

    - industry_background：qualified external（required_any_of 含 external，且有 grade 门槛）。
    - conditional_transmission：required_any_of 引用已验证事实 + 完整 inference_lineage 血缘。
    - company_exposure / actual_company_impact：external 只能 supplemental_only，
      不得作为 required_any_of（否则「纯外部来源的公司暴露/实际影响」会通过校验）。
    """
    evidence_registry = contract.raw.get("evidence_requirements", {})
    for layer, er_id in S.TRANSMISSION_EVIDENCE_BY_LAYER.items():
        er = evidence_registry.get(er_id)
        if er is None:
            errors.append(f"{er_id}: 缺失传导证据需求（层 {layer}）")
            continue
        authority = er.get("authority")
        if not isinstance(authority, dict):
            errors.append(f"{er_id}: 必须提供 authority（required_any_of/supplemental_only）")
            continue
        unknown = sorted(set(authority.keys()) - set(S.EVIDENCE_AUTHORITY_KEYS))
        if unknown:
            errors.append(f"{er_id}: authority 未知键 {unknown}")
        req = authority.get("required_any_of")
        supp = authority.get("supplemental_only", [])
        if not isinstance(req, list) or not req:
            errors.append(f"{er_id}: authority.required_any_of 必须非空")
            continue
        if not isinstance(supp, list):
            errors.append(f"{er_id}: authority.supplemental_only 必须为列表")
        req_classes = {
            c
            for clause in req
            if isinstance(clause, dict)
            for c in clause.get("source_classes", [])
        }
        # 三类权威身份分离：external 只可作补充（公司暴露/实际影响），不得作 required_any_of。
        if layer in ("company_exposure", "actual_company_impact") and "external" in req_classes:
            errors.append(f"{er_id}: external 只能 supplemental_only，不得作为 required_any_of")
        if layer == "industry_background":
            if "external" not in req_classes:
                errors.append(f"{er_id}: 行业背景必须由 qualified external 满足")
            # 背景须有 grade 门槛（D 级不得进入背景正文）。
            graded = [cl for cl in req if isinstance(cl, dict) and "min_grade" in cl]
            if not graded:
                errors.append(f"{er_id}: 行业背景 authority 须给出 min_grade 门槛")
        if layer == "conditional_transmission":
            il = authority.get("inference_lineage")
            if not isinstance(il, dict) or il.get("required") is not True:
                errors.append(f"{er_id}: 条件性传导必须提供 inference_lineage.required=true")
            else:
                if list(il.get("fields", [])) != list(S.CONDITIONAL_INFERENCE_LINEAGE_FIELDS):
                    errors.append(f"{er_id}: inference_lineage.fields 应完整为 "
                                  f"{list(S.CONDITIONAL_INFERENCE_LINEAGE_FIELDS)}")


def _validate_derived_eligibility(contract: S.ContractV2, errors: list[str]) -> None:
    """派生输入资格政策完整校验（R1-A §四）。

    契约顶层 derived_input_eligibility 必须与 schema 的 typed 政策逐字段一致。
    删任一必需字段、改任一枚举、或把 eligible/ineligible 混同，都会在此失败——
    不是只检查「列表非空」或「值在某个白名单里」。
    """
    block = contract.raw.get("derived_input_eligibility")
    if not isinstance(block, dict):
        errors.append("缺少 derived_input_eligibility（typed 派生输入资格政策）")
        return
    missing = sorted(set(S.DERIVED_ELIGIBILITY_POLICY_KEYS) - set(block.keys()))
    if missing:
        errors.append(f"derived_input_eligibility 缺字段 {missing}")
    extra = sorted(set(block.keys()) - set(S.DERIVED_ELIGIBILITY_POLICY_KEYS))
    if extra:
        errors.append(f"derived_input_eligibility 含未知字段 {extra}")
    for key in S.DERIVED_ELIGIBILITY_POLICY_KEYS:
        if key not in block:
            continue
        got = block[key]
        want = S.DERIVED_ELIGIBILITY_POLICY[key]
        # list 逐项 + 顺序无关比较；bool/str 直接比较。
        if isinstance(want, list):
            if not isinstance(got, list) or sorted(got) != sorted(want):
                errors.append(f"derived_input_eligibility.{key} 应为 {want}，实际 {got}")
        elif got != want:
            errors.append(f"derived_input_eligibility.{key} 应为 {want!r}，实际 {got!r}")
    # eligible/ineligible 必须两两不交（防「既合格又排除」的自相矛盾政策）。
    pairs = [
        ("question_states_eligible", "question_states_ineligible"),
        ("pack_section_states_eligible", "pack_section_states_ineligible"),
        ("claim_verdicts_eligible", "claim_verdicts_ineligible"),
    ]
    for e_key, i_key in pairs:
        if e_key in block and i_key in block:
            overlap = sorted(set(block[e_key]) & set(block[i_key]))
            if overlap:
                errors.append(f"derived_input_eligibility.{e_key} 与 {i_key} 交集非空 {overlap}")


def validate_contract_v2(contract: S.ContractV2) -> S.ContractV2ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    evidence_registry = contract.raw.get("evidence_requirements", {})
    missing_policies = contract.missing_policies or {}
    negative_policy = contract.raw.get("negative_event_body_policy", {})
    coverage_registry = contract.coverage_rules_registry or {}

    all_questions = contract.all_questions()
    all_aspects = contract.all_aspects()

    # --- 章节顺序 ---
    sec_ids = [s.section_id for s in contract.sections]
    if sec_ids != list(S.SECTION_ORDER):
        errors.append(f"章节顺序/集合应为 {list(S.SECTION_ORDER)}，实际 {sec_ids}")

    # --- 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二） ---
    errors.extend(S.frozen_asset_errors(contract.raw))

    # --- 52 问 + 归属 ---
    nq = len(all_questions)
    if nq != _EXPECTED_QUESTION_COUNT:
        errors.append(f"问题总数应为 {_EXPECTED_QUESTION_COUNT}，实际 {nq}")
    qids = [q.question_id for q in all_questions]
    dup_q = [k for k, v in Counter(qids).items() if v > 1]
    if dup_q:
        errors.append(f"question_id 重复: {dup_q}")
    prod_counts = Counter(q.producer_kind for q in all_questions)
    for pk, want in _EXPECTED_PRODUCER_COUNTS.items():
        got = prod_counts.get(pk, 0)
        if got != want:
            errors.append(f"producer_kind={pk} 问题数应为 {want}，实际 {got}")
    for pk in prod_counts:
        if pk not in S.PRODUCER_KINDS:
            errors.append(f"未知 producer_kind: {pk!r}")

    # --- aspect_id 全局唯一 ---
    aids = [a.aspect_id for a in all_aspects]
    dup_a = [k for k, v in Counter(aids).items() if v > 1]
    if dup_a:
        errors.append(f"aspect_id 重复: {dup_a}")

    # --- 派生题集合 ---
    derived_qids = sorted(
        q.question_id for q in all_questions
        if q.producer_kind == "phase4_section_derived")
    if derived_qids != sorted(S.DERIVED_QUESTION_IDS):
        errors.append(f"章节派生题应为 {sorted(S.DERIVED_QUESTION_IDS)}，实际 {derived_qids}")

    # --- 逐 aspect 校验 ---
    required_fields_set = set(S.REQUIRED_ASPECT_FIELDS)
    aspect_by_id = {a.aspect_id: a for a in all_aspects}
    question_by_id = {q.question_id: q for q in all_questions}
    time_policy_keys = set(contract.time_policies.keys())
    n_business_review = 0

    for a in all_aspects:
        aid = a.aspect_id
        # 22 字段完整
        missing = [f for f in required_fields_set if f not in a.raw]
        if missing:
            errors.append(f"{aid}: 缺少字段 {missing}")
        # 归属一致（关键：按 aspect.question_id 回查，而非 aspect_id；缺失显式报错）
        if a.question_id in question_by_id:
            q = question_by_id[a.question_id]
            if a.topic_id != q.topic_id:
                errors.append(f"{aid}: topic_id={a.topic_id} 与问题 {q.topic_id} 不一致")
            if a.producer_kind != q.producer_kind:
                errors.append(f"{aid}: producer_kind={a.producer_kind} 与问题不一致")
        else:
            errors.append(f"{aid}: question_id={a.question_id!r} 不在 52 问集合中")
        # 枚举白名单
        if a.kind not in S.ASPECT_KINDS:
            errors.append(f"{aid}: 未知 kind {a.kind!r}")
        if a.producer_kind not in S.PRODUCER_KINDS:
            errors.append(f"{aid}: 未知 producer_kind {a.producer_kind!r}")
        if a.time_scope not in S.TIME_POLICIES:
            errors.append(f"{aid}: 未知 time_scope {a.time_scope!r}")
        if a.output_destination not in S.OUTPUT_DESTINATIONS:
            errors.append(f"{aid}: 未知 output_destination {a.output_destination!r}")
        if a.display_tier not in S.DISPLAY_TIERS:
            errors.append(f"{aid}: 未知 display_tier {a.display_tier!r}")
        if a.content_role not in S.CONTENT_ROLES:
            errors.append(f"{aid}: 未知 content_role {a.content_role!r}")
        # 展示层级/内容角色配对约束（R1-A §六：diagnostic_only 只与 audit_only 配对）
        if a.display_tier == "diagnostic_only" and a.content_role != "audit_only":
            errors.append(f"{aid}: diagnostic_only 必须配 audit_only，实际 {a.content_role!r}")
        if a.business_review_status not in S.BUSINESS_REVIEW_STATUSES:
            errors.append(f"{aid}: 未知 business_review_status {a.business_review_status!r}")
        for bl in a.blocking_policy:
            if bl not in S.BLOCKING_LEVELS:
                errors.append(f"{aid}: 未知 blocking 级别 {bl!r}")
        for isc in a.impact_scope:
            if isc not in S.IMPACT_SCOPES:
                errors.append(f"{aid}: 未知 impact_scope {isc!r}")
        # 覆盖规则可组合 + NA 不作为 coverage mode + producer 兼容（R1-A §五）
        for cr in a.coverage_rules:
            if cr not in S.COVERAGE_RULES:
                errors.append(f"{aid}: 未知 coverage rule {cr!r}")
            elif cr not in S.PRODUCER_COVERAGE_RULES.get(a.producer_kind, ()):
                errors.append(f"{aid}: coverage rule {cr!r} 不适用于 producer_kind={a.producer_kind}")
        if "not_applicable" in a.coverage_rules:
            errors.append(f"{aid}: not_applicable 不得作为 coverage mode")
        # evidence / source / time policy 引用可解析
        for er in a.evidence_requirement_ids:
            if er not in evidence_registry:
                errors.append(f"{aid}: evidence requirement 不可解析 {er!r}")
        if a.source_policy_ref != contract.source_policy_ref:
            errors.append(f"{aid}: source_policy_ref={a.source_policy_ref!r} "
                          f"≠ 契约 {contract.source_policy_ref!r}")
        if a.missing_policy not in missing_policies:
            errors.append(f"{aid}: missing_policy 不可解析 {a.missing_policy!r}")
        if a.applicability_policy is not None and a.applicability_policy not in missing_policies:
            errors.append(f"{aid}: applicability_policy 不可解析 {a.applicability_policy!r}")
        # 事件核心字段
        if a.kind == "event_set":
            for f in S.EVENT_CORE_FIELDS:
                if f not in a.required_fields:
                    errors.append(f"{aid}: event_set 缺核心字段 {f!r}")
        # 全披露集合锚点
        if a.kind == "all_disclosed_items" and not a.complete_set_rule:
            errors.append(f"{aid}: all_disclosed_items 必须提供 complete_set_rule 集合锚点")
        # derived_from 禁通配（R1-A §十.4：不得 company:*/financial:*/industry:*，
        # 只能用显式 aspect_id 或 typed derived_from_scope）
        for ref in a.derived_from:
            if ref.endswith(":*"):
                errors.append(f"{aid}: derived_from 禁止通配 {ref!r}（用显式 id 或 derived_from_scope）")
            elif _section_id_of(contract, ref) is None:
                errors.append(f"{aid}: derived_from 不可解析 {ref!r}")
        # derived_from_scope 校验
        scope = a.derived_from_scope
        if scope is not None:
            if not isinstance(scope, dict):
                errors.append(f"{aid}: derived_from_scope 必须是 dict")
            else:
                for k in scope:
                    if k not in S.DERIVED_FROM_SCOPE_KEYS:
                        errors.append(f"{aid}: derived_from_scope 未知键 {k!r}")
                inc = scope.get("include_sections")
                if not inc:
                    errors.append(f"{aid}: derived_from_scope 缺 include_sections")
                elif not set(inc) <= set(S.SECTION_ORDER):
                    errors.append(f"{aid}: derived_from_scope.include_sections 含未知章节 {sorted(set(inc) - set(S.SECTION_ORDER))}")
                for et in scope.get("exclude_display_tiers", []):
                    if et not in S.DISPLAY_TIERS:
                        errors.append(f"{aid}: derived_from_scope.exclude_display_tiers 未知 {et!r}")
                for epk in scope.get("exclude_producer_kinds", []):
                    if epk not in S.PRODUCER_KINDS:
                        errors.append(f"{aid}: derived_from_scope.exclude_producer_kinds 未知 {epk!r}")
                excl_states = scope.get("exclude_terminal_states")
                if not excl_states:
                    errors.append(f"{aid}: derived_from_scope 必须提供 exclude_terminal_states "
                                  f"（排除 {list(S.DERIVED_INPUT_INELIGIBLE_STATES)} 等不合格终态）")
                else:
                    for st in excl_states:
                        if st not in S.DERIVED_INPUT_INELIGIBLE_STATES:
                            errors.append(f"{aid}: derived_from_scope.exclude_terminal_states 未知 {st!r}")
        # 派生回指同章（derived 用 derived_from_scope，且 include_sections 仅本章）
        if a.producer_kind == "phase4_section_derived":
            own_sec = _section_id_of(contract, aid)
            if scope is None:
                errors.append(f"{aid}: phase4_section_derived 必须提供 derived_from_scope")
            else:
                inc = scope.get("include_sections", [])
                if list(inc) != [own_sec]:
                    errors.append(f"{aid}: 派生回指应仅本章 [{own_sec}]，实际 include_sections={inc}")
        # transmission_layers / transmission_channel 校验（R1-A §二：每 aspect 恰一个通道 × 一个层）
        for tl in a.transmission_layers:
            if tl not in S.TRANSMISSION_LAYERS:
                errors.append(f"{aid}: 未知 transmission_layer {tl!r}")
        if a.transmission_channel:
            if a.transmission_channel not in S.TRANSMISSION_CHANNELS:
                errors.append(f"{aid}: 未知 transmission_channel {a.transmission_channel!r}")
            elif not a.transmission_layers:
                errors.append(f"{aid}: 有 transmission_channel 但缺 transmission_layers")
        # synth 仅 Phase 5
        if a.producer_kind == "phase5_synthesizer":
            if a.output_destination != "phase5_synthesizer":
                errors.append(f"{aid}: synth 应仅声明 phase5_synthesizer，"
                              f"实际 {a.output_destination}")
        if a.business_review_status == "BUSINESS_REVIEW_REQUIRED":
            if not a.business_review_reason:
                errors.append(f"{aid}: BUSINESS_REVIEW_REQUIRED 必须提供 business_review_reason")
            n_business_review += 1

    # --- 全局产物完整性 ---
    if set(coverage_registry.keys()) != set(S.COVERAGE_RULES):
        errors.append(f"coverage_rules_registry 应覆盖 {S.COVERAGE_RULES}")
    missing_tp = set(S.TIME_POLICIES) - set(time_policy_keys)
    if missing_tp:
        errors.append(f"time_policies 缺少五类政策 {sorted(missing_tp)}")
    if negative_policy:
        if list(negative_policy.get("event_core_fields", [])) != list(S.EVENT_CORE_FIELDS):
            errors.append("negative_event_body_policy.event_core_fields 与 schema 不一致")
        if list(negative_policy.get("event_extension_fields", [])) != list(S.EVENT_EXTENSION_FIELDS):
            errors.append("negative_event_body_policy.event_extension_fields 与 schema 不一致")
        if list(negative_policy.get("allowed_body_forms", [])) != list(S.NEGATIVE_BODY_ALLOWED_FORMS):
            errors.append("negative_event_body_policy.allowed_body_forms 与 schema 不一致")
        if list(negative_policy.get("forbidden_body_forms", [])) != list(S.NEGATIVE_BODY_FORBIDDEN_FORMS):
            errors.append("negative_event_body_policy.forbidden_body_forms 与 schema 不一致")
    else:
        errors.append("缺少 negative_event_body_policy")
    # 行业风险传导通道（R1-A §四：四条独立通道）
    tx_channels = [c.get("channel") for c in contract.raw.get("transmission_channels", [])]
    if tx_channels != list(S.TRANSMISSION_CHANNELS):
        errors.append(f"transmission_channels 应为 {list(S.TRANSMISSION_CHANNELS)}，实际 {tx_channels}")

    # 传导 Evidence 权威（R1-A §二）+ 派生输入资格政策完整校验（R1-A §四）
    _validate_transmission_authority(contract, errors)
    _validate_derived_eligibility(contract, errors)

    # --- 统计（供 stop report） ---
    sec_stats = {
        s.section_id: {
            "questions": len(s.all_questions()),
            "aspects": len(s.all_aspects()),
        }
        for s in contract.sections
    }
    stats = {
        "questions": nq,
        "aspects": len(all_aspects),
        "producer_counts": dict(prod_counts),
        "sections": sec_stats,
        "business_review_required": n_business_review,
    }

    valid = len(errors) == 0
    return S.ContractV2ValidationResult(
        valid=valid, errors=errors, warnings=warnings, stats=stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 Contract v2")
    parser.add_argument("path", help="standard_v3.yaml 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON 统计")
    args = parser.parse_args(argv)

    import json
    try:
        c = load_contract_v2(args.path)
        r = validate_contract_v2(c)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if args.json:
        print(json.dumps(r.stats, ensure_ascii=False, indent=2))
        return 0 if r.valid else 1

    if not r.valid:
        print(f"校验失败（{len(r.errors)} 处）:")
        for err in r.errors:
            print(f"  - {err}")
        return 1
    print(f"校验通过: {r.stats['questions']} 问 / {r.stats['aspects']} aspects "
          f"/ 归属 {r.stats['producer_counts']}")
    for wid in r.warnings:
        print(f"  ! {wid}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
