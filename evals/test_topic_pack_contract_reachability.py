"""Eval: R1-B §六 真实 Contract 可达性测试（加载真实冻结资产，schema/Store 合法路径证明）。

用法: python -m evals.test_topic_pack_contract_reachability

全部离线：加载 4 份 R1-A 冻结资产中的 Contract v2（standard_v3.yaml）与 SourcePolicy v1
（source_policy_v1.yaml），把真实 AspectV2 投影为 TopicAspectRequirementSnapshot，再用
确定性构造的最小真实闭合链（material + fact + citation + 确定性 authority + coverage +
usage-scope + sufficiency + set_complete 证明）证明每个 topic_harness aspect 都有一条合法
coverage 路径（或合法的 not_applicable 终态）。不调真实 LLM / bocha / 网络 / Router /
工具循环 / 生产 DB；不生成任何业务结论。

§六 十项逐项落地：
  1. 187 aspects 按 producer 分类（115/49/7/16；52 问 28/13/3/8）；
  2. financial/derived/synth 不得被误路由进 Topic Pack Store（各含 ≥1 条非 topic_harness
     覆盖规则 → coverage_rule_not_evaluable fail-closed）；
  3. 全部 coverage rules 已类型化（每条 aspect 的规则 ⊆ 其 producer 的 PRODUCER_COVERAGE_RULES）；
  4. 3 个 set_complete aspect 合法 covered 路径 + 失败反例；
  5. 22 个 external-only aspect 可达（usage-scope + sufficiency 均放行）；
  6. company_exposure / actual_company_impact 不得由 external-only 覆盖（external 仅 supplemental）；
  7. item-only / formula-only financial 权威门可达 + 经 full Store gate 可达；
  8. 无 required_body aspect 永久不可达（required source class 非空）；
  9. 未知 coverage rule fail-closed；
 10. 冻结资产未漂移（content_fingerprint 与声明的 content_sha256 一致）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader_v2
from contracts import schema_v2 as S
from contracts import source_policy as SP
from harness import topic_schema as TS
from harness import topic_store as Store

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_ASSET = str(ROOT / S.CONTRACT_V2_ASSET)
SOURCE_POLICY_ASSET = str(ROOT / "templates/policies/source_policy_v1.yaml")

# topic_harness 六条可被 typed schema 确定性评估的 coverage rules（与 topic_store 一致）。
TOPIC_HARNESS_EVALUABLE_RULES = {
    "set_complete", "required_fields_complete", "minimum_sources",
    "direct_support", "search_audit", "applicability",
}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# §五.7 / item 7：set_complete 依赖束必须携带 set_enumerator 版本（依赖指纹锁步一致）。
_SET_ENUM_DV = {"set_enumerator": TS.SET_ENUMERATION_VERIFIER_VERSION}


# ---------------------------------------------------------------------------
# 冻结资产投影
# ---------------------------------------------------------------------------

def _load_frozen_assets():
    contract = loader_v2.load_contract_v2(CONTRACT_ASSET)
    sp = SP.load_source_policy(SOURCE_POLICY_ASSET)
    contract_sha256 = S.content_fingerprint(contract.raw)
    sp_fp = S.content_fingerprint(sp.raw)
    ers_by_id = contract.raw.get("evidence_requirements", {})
    topic_section = {}
    for sec in contract.sections:
        for t in sec.topics:
            topic_section[t.topic_id] = sec.section_id
    return contract, sp, contract_sha256, sp_fp, ers_by_id, topic_section


def _snapshot_from_aspect(a: S.AspectV2, contract_sha256: str, sp: SP.SourcePolicy,
                          sp_fp: str, ers_by_id: dict) -> TS.TopicAspectRequirementSnapshot:
    """真实 AspectV2 → 冻结 TopicAspectRequirementSnapshot 投影（不发明近义字段）。"""
    er_refs = []
    for rid in a.evidence_requirement_ids:
        er = ers_by_id[rid]
        authority = None
        raw_auth = er.get("authority")
        if raw_auth:
            groups = tuple(
                TS.SourceClassGroup(
                    source_classes=tuple(g.get("source_classes", [])),
                    min_grade=g.get("min_grade"),
                    kind=g.get("kind"),
                )
                for g in raw_auth.get("required_any_of", [])
            )
            il = raw_auth.get("inference_lineage") or {}
            authority = TS.EvidenceAuthorityPolicy(
                required_any_of=groups,
                supplemental_only=tuple(raw_auth.get("supplemental_only", [])),
                inference_lineage_required=bool(il.get("required", False)),
            )
        er_refs.append(TS.EvidenceRequirementRef(
            requirement_id=rid,
            contract_sha256=contract_sha256,
            requirement_fingerprint=S.sha256_json(er),
            schema_version="1",
            source_classes=tuple(er.get("source_classes", [])),
            authority=authority,
        ))

    dfs = None
    if a.derived_from_scope:
        dfs = TS.DerivedFromScope(
            include_sections=tuple(a.derived_from_scope.get("include_sections", [])),
            exclude_producer_kinds=tuple(a.derived_from_scope.get("exclude_producer_kinds", [])),
            exclude_display_tiers=tuple(a.derived_from_scope.get("exclude_display_tiers", ["diagnostic_only"])),
            exclude_aspect_ids=tuple(a.derived_from_scope.get("exclude_aspect_ids", [])),
            exclude_terminal_states=tuple(a.derived_from_scope.get(
                "exclude_terminal_states", ["NOT_APPLICABLE", "UNRESOLVED", "BLOCKED", "UNSUPPORTED"])),
        )

    return TS.TopicAspectRequirementSnapshot(
        aspect_id=a.aspect_id,
        question_id=a.question_id,
        topic_id=a.topic_id,
        requirement_text=a.requirement_text,
        kind=a.kind,
        producer_kind=a.producer_kind,
        execution_path=a.execution_path,
        required_fields=tuple(a.required_fields),
        coverage_rules=tuple(a.coverage_rules),
        complete_set_rule=a.complete_set_rule,
        evidence_requirement_ids=tuple(er_refs),
        source_policy_ref=TS.SourcePolicyRef(
            policy_id=sp.policy_id, policy_version=sp.policy_version, content_fingerprint=sp_fp),
        time_scope=a.time_scope,
        display_tier=a.display_tier,
        content_role=a.content_role,
        missing_policy=a.missing_policy,
        blocking_policy=tuple(a.blocking_policy),
        applicability_policy=a.applicability_policy,
        impact_scope=tuple(a.impact_scope),
        output_destination=a.output_destination,
        derived_from=tuple(a.derived_from),
        business_review_status=a.business_review_status,
        business_review_reason=a.business_review_reason,
        derived_from_scope=dfs,
        transmission_layers=tuple(a.transmission_layers),
        transmission_channel=a.transmission_channel,
        contract_version="v2",
        contract_sha256=contract_sha256,
        canonical_fingerprint=S.sha256_json(a.raw),
    )


def _sp_snapshot(sp: SP.SourcePolicy, sp_fp: str) -> TS.FrozenSourcePolicySnapshot:
    return TS.FrozenSourcePolicySnapshot(
        policy_id=sp.policy_id, policy_version=sp.policy_version, content_fingerprint=sp_fp,
        key_industry_topics=tuple(sp.key_industry_topics))


# ---------------------------------------------------------------------------
# 离线最小真实闭合链工厂（不生成业务结论，只证明 schema/Store 合法路径）
# ---------------------------------------------------------------------------

def _payload_ref(material_type: str, locator: TS.MaterialLocator,
                 authority_identity: str = "aid") -> TS.MaterialPayloadRef:
    return TS.MaterialPayloadRef(object_type=material_type, authority_identity=authority_identity,
                                 version="v1", content_hash=_sha("payload"),
                                 locator=locator, created_dependency_fingerprint=_sha("cdep"))


def _covering_source_class(snap: TS.TopicAspectRequirementSnapshot) -> str:
    required = set(TS.derive_support_eligibility(snap).required_source_classes)
    if "company_industry" in required:
        return "evidence"
    if "external" in required:
        return "external"
    if "structured_db" in required or "financial" in required:
        return "structured"
    raise AssertionError(f"aspect {snap.aspect_id!r} 无可用 required 来源类: {sorted(required)}")


def _chain(snap: TS.TopicAspectRequirementSnapshot, source: str):
    """按覆盖来源类构造最小真实闭合链（material + fact，共享同一 authority 身份）。"""
    aid = snap.aspect_id
    if source == "evidence":
        auth = TS.EvidenceAuthorityAssessment(
            evidence_id="ev-" + aid, document_id="doc-" + aid, document_version="v1",
            company_id="300750", is_current_document=True, is_current_set=True, page=1,
            fetched_inspected_nonempty=True, content_hash=_sha("evidence:" + aid),
            verdict="authoritative", reason="", validator_version="vv1")
        loc = TS.EvidenceLocator(document_id="doc-" + aid, document_version="v1",
                                 section_path="s1", page=1)
        material_type = "evidence_span"
        citation = TS.CitationRef(ref_type="evidence", evidence_id="ev-" + aid)
    elif source == "external":
        sid = "ext-" + aid
        auth = TS.ExternalSnapshotAuthorityAssessment(
            source_snapshot_id=sid, canonical_url="https://example.com/" + aid,
            domain="example.com", fetched_nonempty=True, content_hash=_sha("external:" + aid),
            published_at="2025-01-01", time_qualified=True, source_grade="A",
            min_grade_met=True, independence_domain="independent-" + aid,
            verdict="authoritative", reason="", validator_version="vv1")
        loc = TS.ExternalLocator(source_snapshot_id=sid, canonical_url="https://example.com/" + aid,
                                 domain="example.com")
        material_type = "external_snapshot"
        citation = TS.CitationRef(ref_type="external", source_snapshot_id=sid)
    elif source == "structured":
        sid = "snap-" + aid
        auth = TS.FinancialSnapshotAuthorityAssessment(
            snapshot_id=sid, company_id="300750", scope="consolidated", currency="CNY",
            purpose="annual_report", report_as_of="2025-12-31", is_current=True,
            validity="valid", report_blocked=False, quarantine=False, item_code="ic-" + aid,
            formula_id=None, period="2025", verdict="authoritative", reason="",
            validator_version="vv1")
        loc = TS.FinancialLocator(snapshot_id=sid, item_code="ic-" + aid, period="2025")
        material_type = "structured"
        citation = TS.CitationRef(ref_type="structured", snapshot_id=sid, item_code="ic-" + aid,
                                  period="2025")
    else:
        raise AssertionError(f"未知来源类 {source!r}")

    auth_id = TS.authority_source_identity(auth)
    pref = _payload_ref(material_type, loc, auth_id)
    material = TS.ResearchMaterial(
        material_id="m-" + aid, material_type=material_type, source_identity=auth_id,
        locator=loc, payload_ref=pref,
        content_hash=pref.content_hash, authority_assessment=auth)
    fact = TS.SupportedFact(
        fact_id="f-" + aid, text="fact text", fact_type="fact", aspect_ids=(aid,),
        citation_refs=(citation,), source_authority=auth, obtained_fields=snap.required_fields)
    return material, fact


def _build_conditional_chain(snap: TS.TopicAspectRequirementSnapshot, source: str):
    """conditional_transmission：base fact（普通 fact）+ inference fact（类型化 lineage 指向 base）。"""
    aid = snap.aspect_id
    base_material, base_fact = _chain(snap, source)
    inf_fact = TS.SupportedFact(
        fact_id="f-inf-" + aid, text="inference fact", fact_type="inference",
        aspect_ids=(aid,), citation_refs=base_fact.citation_refs,
        source_authority=base_fact.source_authority, obtained_fields=snap.required_fields,
        inference_lineage=TS.InferenceLineage(
            inference_policy_ref=TS.CONDITIONAL_INFERENCE_POLICY_ID,
            rule_version=TS.CONDITIONAL_INFERENCE_POLICY_VERSION,
            channel=snap.transmission_channel, direction="industry_to_company",
            conditions=("c1",), limitation=("l1",), derived_from_fact_ids=(base_fact.fact_id,)))
    return base_material, base_fact, inf_fact


def _set_complete(aid: str, material: TS.ResearchMaterial, fact_id: str, contract_sha256: str,
                  scope_complete: bool = True) -> TS.SetCompletenessAssessment:
    """set_complete 类型化证明：绑定 material 真实 document_id/document_version/source_boundary
    + 当前 Pack 依赖指纹；seed_evidence_ids 取自 material 的真实 Evidence identity（非 fact id）。"""
    mid = material.material_id
    loc = material.locator
    sid = material.source_identity or ""
    evidence_id = sid[len("evidence:"):] if sid.startswith("evidence:") else "ev-" + aid
    if isinstance(loc, TS.EvidenceLocator):
        doc_id = loc.document_id
        doc_version = loc.document_version
        boundary = loc.section_path or loc.table_title or ""
    elif isinstance(loc, TS.FinancialLocator):
        doc_id, doc_version = "doc1", loc.snapshot_id
        boundary = loc.scope or ""
    elif isinstance(loc, TS.ExternalLocator):
        doc_id, doc_version = "doc1", loc.source_snapshot_id
        boundary = loc.canonical_url or ""
    else:
        doc_id, doc_version, boundary = "doc1", "", ""
    dep_fp = TS.compute_dependency_fingerprint(contract_sha256, "v1", _SET_ENUM_DV)
    boundary_proof = TS.EnumerationBoundaryProof(
        aspect_id=aid, seed_evidence_ids=(evidence_id,), document_id=doc_id,
        document_version=doc_version, evidence_set_version="set1",
        source_boundary_identity=boundary, component_material_ids=(mid,),
        trace_fingerprint=_sha("trace"), direction_stop_reasons=(),
        unread_candidate_refs=(), unresolved_explicit_refs=(), unclosed_continuations=(),
        tool_errors=(), budget_exhausted=False, dependency_fingerprint=dep_fp)
    return TS.SetCompletenessAssessment(
        aspect_id=aid, rule_version=TS.SET_COMPLETENESS_RULE_VERSION, source_material_ids=(mid,),
        document_version=doc_version, source_boundary=boundary,
        expected_member_ids=("m1",), observed_member_ids=("m1",),
        excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=(mid,), supporting_fact_ids=(fact_id,),
        scope_complete=scope_complete, assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=contract_sha256, boundary_proof=boundary_proof,
        dependency_fingerprint=dep_fp)


def _usage() -> TS.TopicUsageSnapshot:
    bp = TS.BudgetPolicySnapshot(schema_version="1", canonical_hash=_sha("bp"), tier="t1")
    return TS.TopicUsageSnapshot(budget_policy=bp,
                                 cumulative_usage=(TS.UsageEntry(metric="rounds", value=1, unit=""),),
                                 stop_reason=None)


def _build_pack(snap: TS.TopicAspectRequirementSnapshot, contract_sha256: str,
                sp_snap: TS.FrozenSourcePolicySnapshot, result: TS.AspectResearchResult,
                materials: tuple[TS.ResearchMaterial, ...],
                facts: tuple[TS.SupportedFact, ...]) -> TS.TopicResearchPack:
    aid = snap.aspect_id
    process, coverage, derivation = TS.derive_pack_status((aid,), (result,), stop_reason=None)
    dep = TS.compute_dependency_fingerprint(contract_sha256, "v1", _SET_ENUM_DV)
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id="reach-test",
        task_id="reach-test", company_id="300750", report_as_of=None, contract_version="v2",
        contract_fingerprint=contract_sha256, source_policy_version="v1",
        section_id=snap.topic_id, topic_id=snap.topic_id, question_ids=(snap.question_id,),
        aspect_results=(result,), materials=materials, facts=facts, outcome_refs=(),
        external_funnel=None, conflicts=(), not_found_audits=(), unresolved=(), usage=_usage(),
        uncertain_calls=(), process_status=process, coverage_status=coverage,
        status_derivation=derivation, dependency_fingerprint=dep)
    return TS.finalize_pack(pack)


def _build_covered(snap: TS.TopicAspectRequirementSnapshot, contract_sha256: str,
                   sp_snap: TS.FrozenSourcePolicySnapshot, force_source: str | None = None):
    """构造单 covered aspect 的 finalized Pack（force_source 供反例强制外部来源）。"""
    aid = snap.aspect_id
    source = force_source or _covering_source_class(snap)
    if "conditional_transmission" in set(snap.transmission_layers):
        material, base_fact, inf_fact = _build_conditional_chain(snap, source)
        facts = (base_fact, inf_fact)
        result0 = TS.AspectResearchResult(
            aspect_id=aid, question_ids=(snap.question_id,), requirement_snapshot=snap,
            status="covered", supported_fact_ids=(inf_fact.fact_id,),
            material_ids=(material.material_id,), attempted_need_ids=(), unresolved_ids=())
        sa = TS.recompute_sufficiency(result0, (inf_fact,), sp_snap)
        sc = None
    else:
        material, fact = _chain(snap, source)
        facts = (fact,)
        result0 = TS.AspectResearchResult(
            aspect_id=aid, question_ids=(snap.question_id,), requirement_snapshot=snap,
            status="covered", supported_fact_ids=(fact.fact_id,),
            material_ids=(material.material_id,), attempted_need_ids=(), unresolved_ids=())
        sa = TS.recompute_sufficiency(result0, (fact,), sp_snap)
        if "set_complete" in set(snap.coverage_rules):
            # Fix 2：set_complete 的 source material 绑定合成 payload（成员 m1），独立枚举器
            # 据此从真实 payload 字节枚举成员，杜绝仅靠自填 expected/observed 自证完整。
            pb = _set_members_payload(("m1",))
            material = dataclasses.replace(
                material,
                payload_ref=TS.MaterialPayloadRef(
                    object_type=material.payload_ref.object_type,
                    authority_identity=material.payload_ref.authority_identity,
                    version=material.payload_ref.version,
                    content_hash=_sha_bytes(pb),
                    locator=material.payload_ref.locator,
                    created_dependency_fingerprint=material.payload_ref.created_dependency_fingerprint),
                content_hash=_sha_bytes(pb))
        sc = (_set_complete(aid, material, fact.fact_id, contract_sha256)
              if "set_complete" in set(snap.coverage_rules) else None)
    result = dataclasses.replace(result0, sufficiency_assessment=sa, set_completeness=sc)
    pack = _build_pack(snap, contract_sha256, sp_snap, result, (material,), facts)
    return pack, (snap,)


def _build_not_applicable(snap: TS.TopicAspectRequirementSnapshot, contract_sha256: str,
                          sp_snap: TS.FrozenSourcePolicySnapshot):
    aid = snap.aspect_id
    result = TS.AspectResearchResult(
        aspect_id=aid, question_ids=(snap.question_id,), requirement_snapshot=snap,
        status="not_applicable", supported_fact_ids=(), material_ids=(),
        attempted_need_ids=(), unresolved_ids=())
    pack = _build_pack(snap, contract_sha256, sp_snap, result, (), ())
    return pack, (snap,)


class _GoodResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        return TS.ResolvedPayload(object_type=pr.object_type, authority_identity=pr.authority_identity,
                                  version=pr.version, locator=pr.locator,
                                  content_hash=pr.content_hash, payload_bytes=None)


class _GoodSourcePolicyResolver:
    """从冻结 sp_snap 解析 SourcePolicy（不直接信任调用方构造投影）。"""

    def __init__(self, snap: TS.FrozenSourcePolicySnapshot):
        self._snap = snap

    def resolve(self, ref: TS.SourcePolicyRef) -> TS.FrozenSourcePolicySnapshot | None:
        return self._snap


class _GoodSetCompletenessVerifier:
    def verify(self, assessment: TS.SetCompletenessAssessment,
               dependency_fingerprint: str) -> TS.SetCompletenessVerdict | None:
        return TS.compute_set_completeness_verdict(assessment, dependency_fingerprint)


# -- Fix 2：set_complete 独立枚举（合成 payload；成员 m1 与 _set_complete 一致） --
def _set_members_payload(members: tuple[str, ...]) -> bytes:
    return json.dumps({"members": sorted(set(members))}, ensure_ascii=False).encode("utf-8")


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _parse_set_members(payload_bytes: bytes) -> tuple[str, ...]:
    obj = json.loads(payload_bytes.decode("utf-8"))
    members = obj.get("members")
    if not isinstance(members, list) or not all(isinstance(m, str) and m for m in members):
        raise ValueError("bad synthetic set payload")
    return tuple(members)


class _SetBytesResolver:
    def __init__(self, payload_bytes: bytes):
        self._by_hash = {_sha_bytes(payload_bytes): payload_bytes}

    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        return TS.ResolvedPayload(object_type=pr.object_type, authority_identity=pr.authority_identity,
                                  version=pr.version, locator=pr.locator, content_hash=pr.content_hash,
                                  payload_bytes=self._by_hash.get(pr.content_hash))


class _GoodSetEnumerationVerifier:
    """受信任的测试枚举器：从合成 payload 确定性枚举成员（只证明接口与 Store 绑定，不代表
    正式文档枚举已实现；正式枚举器由 R2 唯一正式组合入口注入）。"""

    verifier_version = TS.SET_ENUMERATION_VERIFIER_VERSION

    def enumerate(self, assessment: TS.SetCompletenessAssessment,
                  materials: tuple[TS.ResearchMaterial, ...],
                  resolved_payloads: tuple[TS.ResolvedPayload, ...],
                  dependency_fingerprint: str) -> TS.SetEnumerationResult | None:
        if len(resolved_payloads) != 1:
            return TS.SetEnumerationResult(material_type_supported=False,
                                           verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                                           reason=f"需恰好 1 个 source payload，得到 {len(resolved_payloads)}")
        rp = resolved_payloads[0]
        if rp.payload_bytes is None:
            return TS.SetEnumerationResult(material_type_supported=False,
                                           verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                                           reason="payload bytes 不可用")
        try:
            members = _parse_set_members(rp.payload_bytes)
        except Exception as e:  # noqa: BLE001 - 非法 payload fail-closed
            return TS.SetEnumerationResult(material_type_supported=False,
                                           verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                                           reason=f"payload 解析失败: {e}")
        return TS.SetEnumerationResult(
            material_type_supported=True,
            enumerated_member_ids=members,
            payload_hash=TS.compute_source_payload_hash(tuple(resolved_payloads)),
            boundary_identity=TS.compute_boundary_identity(assessment.document_version,
                                                           assessment.source_boundary),
            verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
            reason="enumerated from synthetic payload")


def _requirement(snaps: tuple[TS.TopicAspectRequirementSnapshot, ...], topic_id: str,
                 contract_sha256: str) -> TS.TopicResearchRequirement:
    return TS.TopicResearchRequirement(
        task_id="reach-test", company_id="300750", report_as_of=None, contract_version="v2",
        contract_fingerprint=contract_sha256, source_policy_version="v1",
        section_id=topic_id, topic_id=topic_id, question_ids=tuple(s.question_id for s in snaps),
        aspects=snaps, allowed_capabilities=("evidence",), dependency_versions=_SET_ENUM_DV)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

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

    contract, sp, contract_sha256, sp_fp, ers_by_id, topic_section = _load_frozen_assets()
    sp_snap = _sp_snapshot(sp, sp_fp)
    sp_resolver = _GoodSourcePolicyResolver(sp_snap)
    sc_verifier = _GoodSetCompletenessVerifier()
    sc_enum_verifier = _GoodSetEnumerationVerifier()
    set_bytes_resolver = _SetBytesResolver(_set_members_payload(("m1",)))

    # -- 10. 冻结资产未漂移 --
    declared = contract.raw.get("content_sha256")
    if declared:
        check(declared == contract_sha256, "Contract v2 冻结指纹与当前内容一致（未漂移）")
    sp_declared = sp.raw.get("content_sha256")
    if sp_declared:
        check(sp_declared == sp_fp, "SourcePolicy v1 冻结指纹与当前内容一致（未漂移）")

    # 展平全部 aspect
    aspects = []
    for sec in contract.sections:
        for t in sec.topics:
            for q in t.questions:
                for a in q.aspects:
                    aspects.append(a)

    # -- 1. 分类计数 --
    by_producer = {}
    for a in aspects:
        by_producer[a.producer_kind] = by_producer.get(a.producer_kind, 0) + 1
    check(len(aspects) == 187, "真实 Contract 共 187 aspects")
    check(by_producer.get("topic_harness") == 115, "topic_harness = 115")
    check(by_producer.get("financial_workflow") == 49, "financial_workflow = 49")
    check(by_producer.get("phase4_section_derived") == 7, "phase4_section_derived = 7")
    check(by_producer.get("phase5_synthesizer") == 16, "phase5_synthesizer = 16")

    q_by_producer = {}
    for sec in contract.sections:
        for t in sec.topics:
            for q in t.questions:
                q_by_producer[q.producer_kind] = q_by_producer.get(q.producer_kind, 0) + 1
    check(q_by_producer.get("topic_harness") == 28, "52 问中 topic_harness = 28")
    check(q_by_producer.get("financial_workflow") == 13, "financial_workflow = 13")
    check(q_by_producer.get("phase4_section_derived") == 3, "phase4_section_derived = 3")
    check(q_by_producer.get("phase5_synthesizer") == 8, "phase5_synthesizer = 8")

    # -- 3. 全部 coverage rules 类型化 + -- 2. financial/derived/synth 不得误路由 --
    th_aspects = [a for a in aspects if a.producer_kind == "topic_harness"]
    non_th = [a for a in aspects if a.producer_kind != "topic_harness"]
    typed_ok = True
    for a in aspects:
        allowed = set(S.PRODUCER_COVERAGE_RULES.get(a.producer_kind, ()))
        if not set(a.coverage_rules) <= allowed:
            typed_ok = False
            details.append(f"FAIL: aspect {a.aspect_id} 的 coverage_rules 含未类型化规则")
    check(typed_ok, "全部 aspect 的 coverage_rules ⊆ 其 producer 允许规则（类型化）")
    check(len(non_th) == 72, "非 topic_harness aspect = 72（financial 49 + derived 7 + synth 16）")

    # 误路由防线：producer_kind 唯一决定路由（topic_harness=115 已单独断言）；此外，任何携带
    # 非 topic_harness 规则的 aspect 若被送入 Topic Pack Store，_evaluate_coverage_rules 会
    # coverage_rule_not_evaluable fail-closed。逐 producer 统计携带专有规则的 aspect 数。
    producer_specific = {"financial_workflow": 0, "phase4_section_derived": 0, "phase5_synthesizer": 0}
    for a in non_th:
        if set(a.coverage_rules) - TOPIC_HARNESS_EVALUABLE_RULES:
            producer_specific[a.producer_kind] += 1
    check(producer_specific["phase4_section_derived"] == 7,
          "7 derived aspect 全部携带非 topic_harness 规则 → Topic Store fail-closed")
    check(producer_specific["phase5_synthesizer"] == 16,
          "16 synth aspect 全部携带非 topic_harness 规则 → Topic Store fail-closed")
    check(producer_specific["financial_workflow"] == 46,
          "46/49 financial aspect 携带 financial 专有规则 → Topic Store fail-closed")

    # -- 8. 无 required_body aspect 永久不可达 --
    required_body = [a for a in th_aspects if a.display_tier == "required_body"]
    uncoverable = []
    for a in th_aspects:
        elig = TS.derive_support_eligibility(_snapshot_from_aspect(
            a, contract_sha256, sp, sp_fp, ers_by_id))
        if not elig.required_source_classes:
            uncoverable.append(a.aspect_id)
    check(len(required_body) == 113, "required_body topic_harness aspect = 113")
    check(len(uncoverable) == 0, "无 topic_harness aspect 的 required source class 为空（无永久不可达）")

    # -- 4/5/6/7/9：Store 合法路径证明 --
    ext_only = []
    set_complete_ids = []
    transmission_ids = []
    for a in th_aspects:
        snap = _snapshot_from_aspect(a, contract_sha256, sp, sp_fp, ers_by_id)
        elig = TS.derive_support_eligibility(snap)
        if set(elig.required_source_classes) == {"external"}:
            ext_only.append(a.aspect_id)
        if "set_complete" in set(a.coverage_rules):
            set_complete_ids.append(a.aspect_id)
        if snap.transmission_layers:
            transmission_ids.append(a.aspect_id)
    check(len(ext_only) == 22, "external-only topic_harness aspect = 22")
    check(len(set_complete_ids) == 3, "set_complete aspect = 3")
    check(len(transmission_ids) == 16, "传导 aspect = 16（4 通道 × 4 层）")

    committed_covered = set()
    committed_na = set()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "harness.db"
        Store.init_topic_store(db)

        # 4/5/6/7：逐 aspect 证明合法路径（covered 或 not_applicable）。
        for a in th_aspects:
            snap = _snapshot_from_aspect(a, contract_sha256, sp, sp_fp, ers_by_id)
            if snap.applicability_policy is None:
                pack, snaps = _build_covered(snap, contract_sha256, sp_snap)
                Store.commit_pack(pack, _requirement(snaps, snap.topic_id, contract_sha256),
                                  set_bytes_resolver, source_policy_resolver=sp_resolver,
                                  set_completeness_verifier=sc_verifier,
                                  set_enumeration_verifier=sc_enum_verifier)
                committed_covered.add(a.aspect_id)
            else:
                pack, snaps = _build_not_applicable(snap, contract_sha256, sp_snap)
                Store.commit_pack(pack, _requirement(snaps, snap.topic_id, contract_sha256),
                                  _GoodResolver(), source_policy_resolver=sp_resolver)
                committed_na.add(a.aspect_id)
        check(len(committed_covered) == 108, "108 covered topic_harness aspect 合法可达")
        check(len(committed_na) == 7, "7 not_applicable topic_harness aspect 合法可达")
        check(len(committed_covered | committed_na) == 115, "115 topic_harness aspect 全部合法可达")
        check(set(ext_only) <= committed_covered, "22 external-only aspect 全部合法可达")

        # 3 个 set_complete 合法 covered 路径（已含在 covered 循环中）。
        check(set(set_complete_ids) <= committed_covered, "3 set_complete aspect 合法 covered 可达")

        # 6. company_exposure / actual_company_impact 不得 external-only 覆盖。
        exp_impact = [a for a in th_aspects
                      if set(a.transmission_layers) & {"company_exposure", "actual_company_impact"}]
        check(len(exp_impact) == 8, "company_exposure/actual_company_impact 共 8 aspect")
        rejected_external_only = 0
        for a in exp_impact:
            snap = _snapshot_from_aspect(a, contract_sha256, sp, sp_fp, ers_by_id)
            pack, snaps = _build_covered(snap, contract_sha256, sp_snap, force_source="external")
            try:
                Store.commit_pack(pack, _requirement(snaps, snap.topic_id, contract_sha256),
                                  _GoodResolver(), source_policy_resolver=sp_resolver)
                details.append(f"FAIL: {a.aspect_id} external-only 不应被覆盖")
                failed += 1
            except Store.TopicStoreValidationError:
                rejected_external_only += 1
        check(rejected_external_only == 8,
              "8 company_exposure/impact external-only 全部被 usage-scope/sufficiency fail-closed 拒绝")

        # 7. item-only financial 经 full Store gate 可达（structured_db 为 required 来源类）。
        structured_candidate = next(
            (a for a in th_aspects
             if "structured_db" in set(TS.derive_support_eligibility(
                 _snapshot_from_aspect(a, contract_sha256, sp, sp_fp, ers_by_id))
                 .required_source_classes)), None)
        check(structured_candidate is not None, "存在 required 含 structured_db 的 topic_harness aspect")
        if structured_candidate is not None:
            snap = _snapshot_from_aspect(structured_candidate, contract_sha256, sp, sp_fp, ers_by_id)
            pack, snaps = _build_covered(snap, contract_sha256, sp_snap, force_source="structured")
            Store.commit_pack(pack, _requirement(snaps, snap.topic_id, contract_sha256),
                              _GoodResolver(), source_policy_resolver=sp_resolver)
            check(True, "item-only financial fact 经 full Store gate 可达（structured material）")

        # 4. set_complete 失败反例：scope_complete=False → fail-closed。
        sc_na = next(iter(set_complete_ids))
        snap = _snapshot_from_aspect(
            next(a for a in th_aspects if a.aspect_id == sc_na),
            contract_sha256, sp, sp_fp, ers_by_id)
        aid = snap.aspect_id
        material, fact = _chain(snap, _covering_source_class(snap))
        result0 = TS.AspectResearchResult(
            aspect_id=aid, question_ids=(snap.question_id,), requirement_snapshot=snap,
            status="covered", supported_fact_ids=(fact.fact_id,), material_ids=(material.material_id,),
            attempted_need_ids=(), unresolved_ids=())
        sa = TS.recompute_sufficiency(result0, (fact,), sp_snap)
        result_bad = dataclasses.replace(
            result0, sufficiency_assessment=sa,
            set_completeness=_set_complete(aid, material, fact.fact_id,
                                           contract_sha256, scope_complete=False))
        pack_bad = _build_pack(snap, contract_sha256, sp_snap, result_bad, (material,), (fact,))
        try:
            Store.commit_pack(pack_bad, _requirement((snap,), snap.topic_id, contract_sha256),
                              _GoodResolver(), source_policy_resolver=sp_resolver)
            check(False, "set_complete scope_complete=False 应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, "set_complete scope_complete=False → fail-closed（反例）")

        # 4b. set_complete 无独立枚举证明 → 不得完成（接口理论可达，但缺枚举器 fail-closed）。
        sc_na2 = next(iter(set_complete_ids))
        snap2 = _snapshot_from_aspect(
            next(a for a in th_aspects if a.aspect_id == sc_na2),
            contract_sha256, sp, sp_fp, ers_by_id)
        pack_enum, snaps_enum = _build_covered(snap2, contract_sha256, sp_snap)
        try:
            Store.commit_pack(pack_enum, _requirement(snaps_enum, snap2.topic_id, contract_sha256),
                              set_bytes_resolver, source_policy_resolver=sp_resolver,
                              set_completeness_verifier=sc_verifier)
            check(False, "set_complete 无独立枚举证明应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, "set_complete 无 SetEnumerationVerifier → fail-closed（不得靠自填集合升 covered）")

        # 9. 未知 coverage rule → fail-closed。
        a0 = th_aspects[0]
        snap0 = _snapshot_from_aspect(a0, contract_sha256, sp, sp_fp, ers_by_id)
        snap_unknown = dataclasses.replace(snap0, coverage_rules=snap0.coverage_rules + ("bogus_rule",))
        material, fact = _chain(snap_unknown, _covering_source_class(snap_unknown))
        result_u = TS.AspectResearchResult(
            aspect_id=snap_unknown.aspect_id, question_ids=(snap_unknown.question_id,),
            requirement_snapshot=snap_unknown, status="covered",
            supported_fact_ids=(fact.fact_id,), material_ids=(material.material_id,),
            attempted_need_ids=(), unresolved_ids=())
        result_u = dataclasses.replace(result_u, sufficiency_assessment=TS.recompute_sufficiency(
            result_u, (fact,), sp_snap))
        pack_u = _build_pack(snap_unknown, contract_sha256, sp_snap, result_u, (material,), (fact,))
        try:
            Store.commit_pack(pack_u, _requirement((snap_unknown,), snap_unknown.topic_id, contract_sha256),
                              _GoodResolver(), source_policy_resolver=sp_resolver)
            check(False, "未知 coverage rule 应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, "未知 coverage rule → coverage_rule_not_evaluable fail-closed")

    # -- 7. Fix 2：item-only / formula-only financial 权威门 --
    item_only = TS.FinancialSnapshotAuthorityAssessment(
        snapshot_id="snap-x", company_id="300750", scope="consolidated", currency="CNY",
        purpose="annual_report", report_as_of="2025-12-31", is_current=True, validity="valid",
        report_blocked=False, quarantine=False, item_code="ic1", formula_id=None, period="2025",
        verdict="authoritative", reason="", validator_version="vv1")
    formula_only = dataclasses.replace(item_only, item_code=None, formula_id="f1",
                         formula_version="fv1")
    neither = dataclasses.replace(item_only, item_code=None, formula_id=None)
    check(TS.recompute_authority_verdict(item_only) == "authoritative", "item-only financial → authoritative")
    check(TS.recompute_authority_verdict(formula_only) == "authoritative", "formula-only financial → authoritative")
    check(TS.recompute_authority_verdict(neither) == "rejected", "item+formula 皆缺 → rejected")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
