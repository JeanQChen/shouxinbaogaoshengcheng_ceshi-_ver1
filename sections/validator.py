"""Phase 4 章节产物结构校验器（Batch A 结构层；业务覆盖由 P4-D Rules Evaluator 承担）。

不调 LLM / Embedding / Chroma / 网络。只做硬结构校验：类型白名单、必填字段、引用身份。
- fact/calculation claim 必须有 citation 或 derived_from_claim_ids（可回查）。
- claim_type 白名单拒绝 recommendation（授信建议属 Phase 5）。

CLI：python -m sections.validator --self-check
"""

from __future__ import annotations

import argparse
import sys

from contracts import schema as CS
from sections import schema as SS


def validate_citation_ref(ref) -> list[str]:
    errors: list[str] = []
    if ref.ref_type not in SS.CITATION_TYPES:
        errors.append(f"citation ref_type 非法: {ref.ref_type!r}")
        return errors
    if ref.ref_type == "evidence" and not ref.evidence_id:
        errors.append("evidence 引用缺少 evidence_id")
    if ref.ref_type == "structured":
        if not ref.snapshot_id:
            errors.append("structured 引用缺少 snapshot_id")
        if not ref.item_code and not ref.formula_id:
            errors.append("structured 引用既无 item_code 也无 formula_id")
    if ref.ref_type == "external" and not ref.source_snapshot_id:
        errors.append("external 引用缺少 source_snapshot_id")
    return errors


def validate_claim(claim: SS.SectionClaim) -> list[str]:
    errors: list[str] = []
    if claim.claim_type not in SS.CLAIM_TYPES:
        errors.append(f"claim_type 非法: {claim.claim_type!r}，允许 {SS.CLAIM_TYPES}"
                      f"（第一阶段不含 recommendation）")
    if not (claim.text or "").strip():
        errors.append("claim.text 为空")
    if not claim.topic_id:
        errors.append("claim.topic_id 为空")
    if not claim.question_ids:
        errors.append("claim.question_ids 为空")
    if claim.confidence not in SS.CONFIDENCE_LEVELS:
        errors.append(f"claim.confidence 非法: {claim.confidence!r}")
    for sc in claim.impact_scope:
        if sc not in CS.IMPACT_SCOPES:
            errors.append(f"claim.impact_scope 非法: {sc!r}")
    for ref in claim.citation_refs:
        errors.extend(validate_citation_ref(ref))
    # fact/calculation 必须有可回查依据（citation 或 derived_from_claim_ids）
    if (claim.claim_type in ("fact", "calculation")
            and not claim.citation_refs and not claim.derived_from_claim_ids):
        errors.append(f"{claim.claim_type} claim 既无 citation 也无 derived_from_claim_ids")
    return errors


def validate_unresolved(u: SS.SectionUnresolved) -> list[str]:
    errors: list[str] = []
    if u.state not in CS.QUESTION_STATES:
        errors.append(f"unresolved.state 非法: {u.state!r}，允许 {CS.QUESTION_STATES}")
    if not u.reason_code:
        errors.append("unresolved.reason_code 为空")
    if not u.topic_id:
        errors.append("unresolved.topic_id 为空")
    for sc in u.impact_scope:
        if sc not in CS.IMPACT_SCOPES:
            errors.append(f"unresolved.impact_scope 非法: {sc!r}")
    for b in u.blocking_effects:
        if b not in CS.BLOCKING_LEVELS or b == "NONE":
            errors.append(f"unresolved.blocking_effects 非法: {b!r}")
    return errors


def validate_section_result(result: SS.SectionResult) -> list[str]:
    errors: list[str] = []
    if result.status not in SS.SECTION_STATUSES:
        errors.append(f"section status 非法: {result.status!r}，允许 {SS.SECTION_STATUSES}")
    if not result.section_version:
        errors.append("section_version 为空")
    if not result.task_id:
        errors.append("task_id 为空")
    if not result.section_id:
        errors.append("section_id 为空")

    seen_claim_ids: set[str] = set()
    seen_citation_ids: set[str] = set()
    for c in result.claims:
        if c.claim_id in seen_claim_ids:
            errors.append(f"claim_id 重复: {c.claim_id}")
        seen_claim_ids.add(c.claim_id)
        if c.section_id != result.section_id:
            errors.append(f"claim {c.claim_id} 的 section_id 与 result 不一致")
        # 同一结果内 citation_id 重复检测（内容身份；跨 SectionResult 合法，由复合归属键承载）。
        for ref in c.citation_refs:
            cid = SS.derive_citation_id(c.claim_id, ref)
            if cid in seen_citation_ids:
                errors.append(f"citation_id 重复: {cid}")
            seen_citation_ids.add(cid)
        errors.extend(validate_claim(c))

    seen_unresolved_ids: set[str] = set()
    for u in result.unresolved:
        if u.unresolved_id in seen_unresolved_ids:
            errors.append(f"unresolved_id 重复: {u.unresolved_id}")
        seen_unresolved_ids.add(u.unresolved_id)
        errors.extend(validate_unresolved(u))

    if result.evaluation is not None:
        ev = result.evaluation
        if ev.decision not in SS.EVALUATION_DECISIONS:
            errors.append(f"evaluation.decision 非法: {ev.decision!r}")
        if ev.section_result_id != result.section_result_id:
            errors.append("evaluation.section_result_id 与 result 不一致")
        for i in ev.issues:
            if not i.issue_id:
                errors.append("issue 缺 issue_id")
            if not i.rule_id:
                errors.append("issue 缺 rule_id")
        for rt in ev.rework_targets:
            if rt.target_kind not in ("question", "topic", "claim"):
                errors.append(f"rework_target.target_kind 非法: {rt.target_kind!r}")
    return errors


def _self_check() -> dict:
    """构造合法/非法样例，验证校验器行为（无 I/O）。"""
    ok_ref = SS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)
    bad_rec = SS.CitationRef(ref_type="recommendation")

    valid_claim = SS.SectionClaim(
        claim_id="claim_1", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="主体一致",
        claim_type="fact", citation_refs=(ok_ref,))

    bad_type = SS.SectionClaim(
        claim_id="claim_2", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="建议授信",
        claim_type="recommendation", citation_refs=(ok_ref,))

    bad_rec_claim = SS.SectionClaim(
        claim_id="claim_3", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="某事实",
        claim_type="fact", citation_refs=(bad_rec,))

    fact_no_cite = SS.SectionClaim(
        claim_id="claim_4", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="无依据事实",
        claim_type="fact", citation_refs=())

    valid_unresolved = SS.SectionUnresolved(
        unresolved_id="ur_1", section_id="company", topic_id="company_identity",
        question_id="company_subject_match", state="NOT_FOUND_AFTER_SEARCH",
        reason_code="no_evidence", detail="未检索到")

    ok = validate_claim(valid_claim) == []
    got_bad_type = any("recommendation" in e for e in validate_claim(bad_type))
    got_bad_rec = any("ref_type" in e for e in validate_claim(bad_rec_claim))
    got_no_cite = any("无 citation" in e for e in validate_claim(fact_no_cite))
    ok_unresolved = validate_unresolved(valid_unresolved) == []

    return {
        "valid_claim_passes": ok,
        "recommendation_rejected": got_bad_type,
        "bad_ref_type_rejected": got_bad_rec,
        "fact_without_citation_rejected": got_no_cite,
        "valid_unresolved_passes": ok_unresolved,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.validator",
        description="章节产物结构校验器 self-check")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_check:
        parser.print_help()
        return 0
    import json
    result = _self_check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    all_ok = all(result.values())
    print("\nself-check:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
