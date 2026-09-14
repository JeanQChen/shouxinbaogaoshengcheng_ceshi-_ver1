"""Eval: R2 依赖束 build_r2_material_dependencies（R2_IMPLEMENTATION_PLAN §10/§11）。

用法: python -m evals.test_r2_dependencies

覆盖：
- ``build_r2_material_dependencies(db_path)`` 返回 4-tuple
  ``(resolver, source_policy_resolver, set_completeness_verifier, set_enumeration_verifier)``；
  resolver == TopicMaterialPayloadResolver，set_enumeration_verifier == FormalSetEnumerationVerifier。
- 复验 R1-B 硬门：注入 R2 依赖束（缺 source_policy_resolver / set_completeness_verifier）提交
  set_complete Pack → fail-closed（TopicStoreValidationError），即使已注入正式枚举器也不得把
  set_complete 升为 covered。
- 不称完整正式 runtime：source_policy_resolver / set_completeness_verifier 均为 None（R3 职责），
  R2 模块不导出 topic_runtime 组合入口。

全部离线：临时 SQLite topic store + 合成 Pack，不调 LLM/网络。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from harness import topic_store as Store
from harness import r2_dependencies
from harness.set_enumeration import FormalSetEnumerationVerifier


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# 最小合成工厂（镜像 R1-B 测试，仅覆盖 set_complete 提交所需）
# ---------------------------------------------------------------------------

def _policy_ref() -> TS.SourcePolicyRef:
    return TS.SourcePolicyRef(policy_id="sp1", policy_version="v1",
                              content_fingerprint=_sha("policy"))


def _source_policy() -> TS.FrozenSourcePolicySnapshot:
    return TS.FrozenSourcePolicySnapshot(
        policy_id="sp1", policy_version="v1", content_fingerprint=_sha("policy"),
        key_industry_topics=("industry_position",))


def _req_ref() -> TS.EvidenceRequirementRef:
    return TS.EvidenceRequirementRef(requirement_id="er1", contract_sha256=_sha("contract"),
                                     requirement_fingerprint=_sha("req:er1"), schema_version="1")


def _aspect_snapshot(aspect_id: str, topic_id: str = "t1") -> TS.TopicAspectRequirementSnapshot:
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=aspect_id, question_id="q1", topic_id=topic_id,
        requirement_text="req text", kind="fact", producer_kind="company",
        execution_path="direct", required_fields=("f1",),
        coverage_rules=("required_fields_complete", "direct_support", "minimum_sources"),
        complete_set_rule="", evidence_requirement_ids=(_req_ref(),),
        source_policy_ref=_policy_ref(), time_scope="period", display_tier="primary",
        content_role="subject", missing_policy="none", blocking_policy=(),
        applicability_policy=None, impact_scope=("subject",), output_destination="body",
        derived_from=(), business_review_status="none",
        contract_version="v1", contract_sha256=_sha("contract"),
        canonical_fingerprint=_sha("canonical:" + aspect_id),
        dependency_fingerprint=_sha("dep"))


def _evidence_locator() -> TS.EvidenceLocator:
    return TS.EvidenceLocator(document_id="doc1", document_version="v1", section_path="s1", page=1)


def _evidence_authority() -> TS.EvidenceAuthorityAssessment:
    return TS.EvidenceAuthorityAssessment(
        evidence_id="ev1", document_id="doc1", document_version="v1", company_id="300750",
        is_current_document=True, is_current_set=True, page=1,
        fetched_inspected_nonempty=True, content_hash=_sha("evidence:ev1"),
        verdict="authoritative", reason="", validator_version="vv1")


def _closed_fact(fid: str, aspect_ids: tuple[str, ...]) -> TS.SupportedFact:
    return TS.SupportedFact(
        fact_id=fid, text="fact text", fact_type="fact", aspect_ids=aspect_ids,
        citation_refs=(TS.CitationRef(ref_type="evidence", evidence_id="ev1"),),
        source_authority=_evidence_authority(), obtained_fields=("f1",))


def _usage() -> TS.TopicUsageSnapshot:
    bp = TS.BudgetPolicySnapshot(schema_version="1", canonical_hash=_sha("bp"), tier="t1")
    return TS.TopicUsageSnapshot(budget_policy=bp,
                                 cumulative_usage=(TS.UsageEntry(metric="rounds", value=1, unit=""),),
                                 stop_reason=None)


def _set_complete(expected: tuple[str, ...] = ("sub1", "sub2")) -> TS.SetCompletenessAssessment:
    return TS.SetCompletenessAssessment(
        aspect_id="a1", rule_version=TS.SET_COMPLETENESS_RULE_VERSION,
        source_material_ids=("m-a1",), document_version="v1", source_boundary="s1",
        expected_member_ids=expected, observed_member_ids=expected,
        excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=("m-a1",), supporting_fact_ids=("f-a1",),
        scope_complete=True, assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=_sha("contract"),
        dependency_fingerprint=TS.compute_dependency_fingerprint(_sha("contract"), "v1", {}))


def _build_set_complete_covered():
    """构造 set_complete covered Pack + 冻结投影（合成 payload，不落库）。"""
    payload_bytes = b'{"content":{"text":"synthetic"}}'
    payload_ref = TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity="aid", version="v1",
        content_hash=_sha_bytes(payload_bytes), locator=_evidence_locator(),
        created_dependency_fingerprint=_sha("cdep"))
    material = TS.ResearchMaterial(
        material_id="m-a1", material_type="evidence_span", source_identity="src",
        locator=_evidence_locator(), payload_ref=payload_ref,
        content_hash=_sha("mat:m-a1"), authority_assessment=_evidence_authority())
    fact = _closed_fact("f-a1", ("a1",))
    snap = dataclasses.replace(_aspect_snapshot("a1", "t1"), coverage_rules=("set_complete",))
    result = TS.AspectResearchResult(
        aspect_id="a1", question_ids=("q1",), requirement_snapshot=snap, status="covered",
        supported_fact_ids=("f-a1",), material_ids=("m-a1",), attempted_need_ids=(),
        unresolved_ids=(), set_completeness=_set_complete())
    process, coverage, derivation = TS.derive_pack_status(("a1",), (result,), stop_reason=None)
    dep = TS.compute_dependency_fingerprint(_sha("contract"), "v1", {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id="run-1",
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=_sha("contract"), source_policy_version="v1", section_id="company",
        topic_id="t1", question_ids=("q1",), aspect_results=(result,), materials=(material,),
        facts=(fact,), outcome_refs=(), external_funnel=None, conflicts=(),
        not_found_audits=(), unresolved=(), usage=_usage(), uncertain_calls=(),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), (snap,)


def _requirement(aspects: tuple[TS.TopicAspectRequirementSnapshot, ...]) -> TS.TopicResearchRequirement:
    return TS.TopicResearchRequirement(
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=_sha("contract"), source_policy_version="v1",
        section_id="company", topic_id="t1", question_ids=("q1",),
        aspects=aspects, allowed_capabilities=("evidence",), dependency_versions={})


class _GoodSourcePolicyResolver:
    """R1-B 独立冻结 SourcePolicy 解析（复验 set_complete 硬门用）。"""

    def resolve(self, ref: TS.SourcePolicyRef) -> TS.FrozenSourcePolicySnapshot | None:
        return _source_policy()


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

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "h.db"
        Store.init_topic_store(db)
        bundle = r2_dependencies.build_r2_material_dependencies(db)
        check(isinstance(bundle, tuple) and len(bundle) == 4,
              "build_r2_material_dependencies 返回 4-tuple")
        resolver, source_policy_resolver, set_completeness_verifier, set_enumeration_verifier = bundle
        check(isinstance(resolver, Store.TopicMaterialPayloadResolver),
              "resolver == TopicMaterialPayloadResolver")
        check(isinstance(set_enumeration_verifier, FormalSetEnumerationVerifier),
              "set_enumeration_verifier == FormalSetEnumerationVerifier")
        check(source_policy_resolver is None, "source_policy_resolver == None（R3 职责，R2 不构建）")
        check(set_completeness_verifier is None,
              "set_completeness_verifier == None（R3 职责，R2 不构建）")
        check(not hasattr(r2_dependencies, "build_topic_runtime")
              and not hasattr(r2_dependencies, "build_trusted_runtime"),
              "R2 模块不称完整 runtime（不导出 topic_runtime/trusted_runtime 组合入口）")

        pack, aspects = _build_set_complete_covered()
        req = _requirement(aspects)

        # 复验 R1-B 硬门 1：注入 R2 依赖束（缺 source_policy_resolver）→ SourcePolicy gate fail-closed。
        try:
            Store.commit_pack(pack, req, resolver=resolver,
                              source_policy_resolver=source_policy_resolver,
                              set_completeness_verifier=set_completeness_verifier,
                              set_enumeration_verifier=set_enumeration_verifier)
            check(False, "R2 依赖束（缺 SourcePolicyResolver）提交 set_complete 应 fail-closed")
        except Store.TopicStoreValidationError as e:
            check("SourcePolicyResolver" in str(e),
                  "R2 依赖束缺 SourcePolicyResolver → SourcePolicy gate fail-closed")

        # 复验 R1-B 硬门 2：补 SourcePolicyResolver 但缺 SetCompletenessVerifier → set_complete gate fail-closed。
        try:
            Store.commit_pack(pack, req, resolver=resolver,
                              source_policy_resolver=_GoodSourcePolicyResolver(),
                              set_completeness_verifier=None,
                              set_enumeration_verifier=set_enumeration_verifier)
            check(False, "缺 SetCompletenessVerifier 提交 set_complete 应 fail-closed")
        except Store.TopicStoreValidationError as e:
            check("SetCompletenessVerifier" in str(e),
                  "缺 SetCompletenessVerifier（即使注入正式枚举器）→ set_complete gate fail-closed")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
