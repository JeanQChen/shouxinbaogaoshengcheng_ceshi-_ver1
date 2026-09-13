"""Eval: R1-B TopicResearchPack schema + Pack Store + checkpoint + CLI（§14 十六类矩阵 + §四 反例回归）。

用法: python -m evals.test_topic_pack_store

全部离线：mock 构造 Pack / requirement / resolver，不调真实 LLM / bocha / 网络 / Router / 工具循环。
断言覆盖 R1B_IMPLEMENTATION_PLAN.md §14 的 16 类测试，以及 R1-B 最后一次架构门禁返修的
4 个已确认缺陷 + 7 项定点修复的反例回归（§四）。

已确认缺陷（Codex 独立复现）→ 修复：
  1. 省略 requirement 仍可提交 → commit_pack(requirement) 必填
  2. 同 pack_id 改 coverage_status 仍幂等复用 → content_fingerprint 含 process/coverage/
     status_derivation/usage/uncertain_calls/outcome_refs + 深规范形比较
  3. invalidated 后 get_current_pack 仍返回 current → 失效 current fail-closed
  4. 只读连接允许 CREATE TABLE → mode=ro + PRAGMA query_only=ON
  + aspect 语义/双轴状态独立重算、损坏读 fail-closed、migration 单事务原子、
    MaterialPayloadRef 最小 typed resolver 可验证。

R1-B 最后四个残余门禁定点收口（§四 N1–N16 反例）：
  A. PayloadResolver 必填（material 非空 → fail-closed）+ material↔payload_ref typed 身份一致
     + created_dependency_fingerprint 有效；
  B. authority 确定性重算（不信任自称 verdict）+ material↔fact↔citation 来源身份一致 +
     coverage_rules 确定性评估（不可表达 → coverage_rule_not_evaluable）+ 关键结论 sufficiency 独立门；
  C. invalidated Pack 终态失效（stale|invalidated|quarantined 为 terminal 事件，普通 recommit 拒绝）；
  D. migration 最终结构复核纳入同一原子事务（失败 → ROLLBACK 无残留 schema）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import checkpoint as LegacyCheckpoint
from harness import topic_checkpoint as Checkpoint
from harness import topic_schema as TS
from harness import topic_store as Store
from harness import topic_store_cli as CLI
from harness._readonly_sqlite import open_readonly_conn

ROOT = Path(__file__).resolve().parent.parent
V1_FIXED_SHA256 = "23e1735e3b77e94dacae70be03712ca93c98d8f545cc087f8d8b092ad841ae45"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _policy_ref() -> TS.SourcePolicyRef:
    return TS.SourcePolicyRef(policy_id="sp1", policy_version="v1",
                              content_fingerprint=_sha("policy"))


def _req_ref(rid: str = "er1") -> TS.EvidenceRequirementRef:
    return TS.EvidenceRequirementRef(requirement_id=rid, contract_sha256=_sha("contract"),
                                     requirement_fingerprint=_sha("req:" + rid), schema_version="1")


def _aspect_snapshot(aspect_id: str, topic_id: str = "t1",
                     applicability_policy: str | None = None) -> TS.TopicAspectRequirementSnapshot:
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=aspect_id, question_id="q1", topic_id=topic_id,
        requirement_text="req text", kind="fact", producer_kind="company",
        execution_path="direct", required_fields=("f1",),
        coverage_rules=("required_fields_complete", "direct_support", "minimum_sources"),
        complete_set_rule="", evidence_requirement_ids=(_req_ref(),),
        source_policy_ref=_policy_ref(), time_scope="period", display_tier="primary",
        content_role="subject", missing_policy="none", blocking_policy=(),
        applicability_policy=applicability_policy, impact_scope=("subject",), output_destination="body",
        derived_from=(), business_review_status="none",
        contract_version="v1", contract_sha256=_sha("contract"),
        canonical_fingerprint=_sha("canonical:" + aspect_id),
        dependency_fingerprint=_sha("dep"))


def _evidence_locator() -> TS.EvidenceLocator:
    return TS.EvidenceLocator(document_id="doc1", section_path="s1", page=1)


def _financial_locator() -> TS.FinancialLocator:
    return TS.FinancialLocator(snapshot_id="snap1", item_code="ic1")


def _external_locator() -> TS.ExternalLocator:
    return TS.ExternalLocator(source_snapshot_id="ext1", canonical_url="https://x/1",
                              domain="x.com")


def _evidence_authority() -> TS.EvidenceAuthorityAssessment:
    """确定性可重算为 authoritative 的 evidence 权威（current + inspected + 身份 + 位置 + hash）。"""
    return TS.EvidenceAuthorityAssessment(
        evidence_id="ev1", document_id="doc1", document_version="v1", company_id="300750",
        is_current_document=True, is_current_set=True, page=1,
        fetched_inspected_nonempty=True, content_hash=_sha("evidence:ev1"),
        verdict="authoritative", reason="", validator_version="vv1")


def _financial_authority() -> TS.FinancialSnapshotAuthorityAssessment:
    return TS.FinancialSnapshotAuthorityAssessment(
        snapshot_id="snap1", company_id="300750", scope="consolidated", currency="CNY",
        purpose="annual_report", report_as_of="2025-12-31", is_current=True, validity="valid",
        report_blocked=False, quarantine=False, item_code="ic1", formula_id="f1", period="2025",
        verdict="authoritative", reason="", validator_version="vv1")


def _external_authority() -> TS.ExternalSnapshotAuthorityAssessment:
    """external 权威：即使字段全有效也只能 supplemental_only（external 仅 supplemental）。"""
    return TS.ExternalSnapshotAuthorityAssessment(
        source_snapshot_id="ext1", canonical_url="https://x/1", domain="x.com",
        fetched_nonempty=True, content_hash=_sha("external:ext1"), published_at="2025-01-01",
        time_qualified=True, source_grade="A", min_grade_met=True,
        independence_domain="independent.example", verdict="supplemental_only",
        reason="", validator_version="vv1")


def _payload_ref(material_type: str, locator: TS.MaterialLocator) -> TS.MaterialPayloadRef:
    return TS.MaterialPayloadRef(object_type=material_type, authority_identity="aid",
                                 version="v1", content_hash=_sha("payload"),
                                 locator=locator, created_dependency_fingerprint=_sha("cdep"))


def _material(material_type: str, mid: str) -> TS.ResearchMaterial:
    if material_type == "evidence_span":
        loc, auth = _evidence_locator(), _evidence_authority()
    elif material_type == "table_context":
        loc, auth = _evidence_locator(), _evidence_authority()
    elif material_type == "structured":
        loc, auth = _financial_locator(), _financial_authority()
    elif material_type == "external_snapshot":
        loc, auth = _external_locator(), _external_authority()
    else:
        raise ValueError(material_type)
    return TS.ResearchMaterial(material_id=mid, material_type=material_type,
                               source_identity="src", locator=loc,
                               payload_ref=_payload_ref(material_type, loc),
                               content_hash=_sha("mat:" + mid), authority_assessment=auth)


def _fact(fid: str, aspect_ids: tuple[str, ...], text: str = "fact text") -> TS.SupportedFact:
    return TS.SupportedFact(fact_id=fid, text=text, fact_type="fact", aspect_ids=aspect_ids,
                            citation_refs=(TS.CitationRef(ref_type="evidence", evidence_id="ev1"),),
                            source_authority=_evidence_authority(), obtained_fields=("f1",))


def _audit(aid: str, qualified: bool = True) -> TS.NotFoundAudit:
    return TS.NotFoundAudit(
        audit_id=aid, policy_version="v1", required_source_scope=("disclosure",),
        attempted_source_types=("pdf",), valid_attempt_count=1, searched_need_ids=("n1",),
        context_expansion_attempted=False, alternative_candidate_ids=(),
        alternative_sources_attempted=(), time_window="2025", unattempted_candidate_ids=(),
        budget_exhausted=False, qualification_reasons=("ok",), qualified=qualified)


def _gap(gid: str, aspect_ids: tuple[str, ...], reason_code: str = "not_found",
         impact: str = "subject", blocking: bool = False) -> TS.ResearchGap:
    return TS.ResearchGap(unresolved_id=gid, aspect_ids=aspect_ids, reason_code=reason_code,
                          detail="d", attempted_need_ids=("n1",), blocking=blocking, impact=impact)


def _usage(stop_reason: str | None = None) -> TS.TopicUsageSnapshot:
    bp = TS.BudgetPolicySnapshot(schema_version="1", canonical_hash=_sha("bp"), tier="t1")
    return TS.TopicUsageSnapshot(budget_policy=bp,
                                 cumulative_usage=(TS.UsageEntry(metric="rounds", value=1, unit=""),),
                                 stop_reason=stop_reason)


def _closed_fact(fid: str, aspect_ids: tuple[str, ...], verdict: str = "authoritative",
                 citation: bool = True) -> TS.SupportedFact:
    auth = TS.EvidenceAuthorityAssessment(
        evidence_id="ev1", document_id="doc1", document_version="v1", company_id="300750",
        is_current_document=True, is_current_set=True, page=1,
        fetched_inspected_nonempty=True, content_hash=_sha("evidence:ev1"),
        verdict=verdict, reason="", validator_version="vv1")
    refs = (TS.CitationRef(ref_type="evidence", evidence_id="ev1"),) if citation else ()
    return TS.SupportedFact(fact_id=fid, text="fact text", fact_type="fact", aspect_ids=aspect_ids,
                            citation_refs=refs, source_authority=auth, obtained_fields=("f1",))


def _build(topic_id: str = "t1", statuses: dict[str, str] | None = None,
           contract_fp: str | None = None, spv: str = "v1",
           dep_versions: dict[str, str] | None = None, run_id: str = "run-1",
           task_id: str = "task1", company_id: str = "300750", section_id: str = "company",
           report_as_of: str | None = None, contract_version: str = "v1",
           facts: tuple[TS.SupportedFact, ...] = (),
           materials: tuple[TS.ResearchMaterial, ...] = (),
           usage: TS.TopicUsageSnapshot | None = None,
           uncertain_calls: tuple[TS.UncertainToolCallRecord, ...] = (),
           closed_chain: bool = True):
    """构造一个 finalized Pack + 对应 aspect 冻结投影 tuple。

    covered aspect 默认走「最小真实闭合链」（≥1 material + ≥1 fact + citation + authoritative
    authority + 回指 aspect），否则 commit 会被 _validate_aspect_semantics 拒绝（空壳 covered
    是 R1-B 修复的缺陷）。closed_chain=False 仅用于反例测试。
    """
    contract_fp = contract_fp or _sha("contract")
    statuses = statuses or {"a1": "covered"}
    aspects: list[TS.TopicAspectRequirementSnapshot] = []
    results: list[TS.AspectResearchResult] = []
    audits: list[TS.NotFoundAudit] = []
    gaps: list[TS.ResearchGap] = []
    all_facts = list(facts)
    all_materials = list(materials)
    for aid, status in statuses.items():
        snap = _aspect_snapshot(aid, topic_id,
                                applicability_policy=("n/a-by-scope"
                                                      if status == "not_applicable" else None))
        aspects.append(snap)
        nfa_id = None
        material_ids: tuple[str, ...] = ()
        fact_ids: tuple[str, ...] = ()
        unresolved_ids: tuple[str, ...] = ()
        if status == "covered" and closed_chain:
            mid, fid = "m-" + aid, "f-" + aid
            all_materials.append(_material("evidence_span", mid))
            all_facts.append(_closed_fact(fid, (aid,)))
            material_ids, fact_ids = (mid,), (fid,)
        elif status == "not_found":
            nfa_id = "audit-" + aid
            audits.append(_audit(nfa_id, qualified=True))
        elif status == "partial":
            gid = "gap-" + aid
            gaps.append(_gap(gid, (aid,), blocking=False))
            unresolved_ids = (gid,)
        elif status == "blocked":
            gid = "gap-" + aid
            gaps.append(_gap(gid, (aid,), blocking=True))
            unresolved_ids = (gid,)
        results.append(TS.AspectResearchResult(
            aspect_id=aid, question_ids=("q1",), requirement_snapshot=snap, status=status,
            supported_fact_ids=fact_ids, material_ids=material_ids, attempted_need_ids=(),
            unresolved_ids=unresolved_ids, not_found_audit_id=nfa_id))
    required = tuple(a.aspect_id for a in aspects)
    u = usage or _usage()
    process, coverage, derivation = TS.derive_pack_status(required, tuple(results),
                                                          stop_reason=u.stop_reason)
    dep = TS.compute_dependency_fingerprint(contract_fp, spv, dep_versions or {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id=run_id,
        task_id=task_id, company_id=company_id, report_as_of=report_as_of,
        contract_version=contract_version, contract_fingerprint=contract_fp,
        source_policy_version=spv, section_id=section_id, topic_id=topic_id,
        question_ids=("q1",), aspect_results=tuple(results), materials=tuple(all_materials),
        facts=tuple(all_facts), outcome_refs=(), external_funnel=None, conflicts=(),
        not_found_audits=tuple(audits), unresolved=tuple(gaps), usage=u,
        uncertain_calls=uncertain_calls,
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), tuple(aspects)


def _build_raw(statuses: dict[str, str], topic_id: str = "t1",
               covered_material: bool = True, covered_fact: bool = True,
               covered_authority_verdict: str = "authoritative", covered_citation: bool = True,
               not_found_qualified: bool = True, partial_gap: bool = True,
               blocked_gap: bool = True, not_applicable_policy: bool = True,
               stop_reason: str | None = None):
    """构造任意状态组合的 finalized Pack（供反例测试；可有意制造语义门禁违规）。"""
    contract_fp = _sha("contract")
    aspects: list[TS.TopicAspectRequirementSnapshot] = []
    results: list[TS.AspectResearchResult] = []
    audits: list[TS.NotFoundAudit] = []
    gaps: list[TS.ResearchGap] = []
    materials: list[TS.ResearchMaterial] = []
    facts: list[TS.SupportedFact] = []
    for aid, status in statuses.items():
        snap = _aspect_snapshot(aid, topic_id,
                                applicability_policy=("n/a-by-scope"
                                                      if status == "not_applicable"
                                                      and not_applicable_policy else None))
        aspects.append(snap)
        nfa_id = None
        material_ids, fact_ids, unresolved_ids = (), (), ()
        if status == "covered":
            if covered_material:
                mid = "m-" + aid
                materials.append(_material("evidence_span", mid))
                material_ids = (mid,)
            if covered_fact:
                fid = "f-" + aid
                facts.append(_closed_fact(fid, (aid,), verdict=covered_authority_verdict,
                                          citation=covered_citation))
                fact_ids = (fid,)
        elif status == "not_found":
            nfa_id = "audit-" + aid
            audits.append(_audit(nfa_id, qualified=not_found_qualified))
        elif status == "partial":
            if partial_gap:
                gid = "gap-" + aid
                gaps.append(_gap(gid, (aid,), blocking=False))
                unresolved_ids = (gid,)
        elif status == "blocked":
            if blocked_gap:
                gid = "gap-" + aid
                gaps.append(_gap(gid, (aid,), blocking=True))
                unresolved_ids = (gid,)
        results.append(TS.AspectResearchResult(
            aspect_id=aid, question_ids=("q1",), requirement_snapshot=snap, status=status,
            supported_fact_ids=fact_ids, material_ids=material_ids, attempted_need_ids=(),
            unresolved_ids=unresolved_ids, not_found_audit_id=nfa_id))
    required = tuple(a.aspect_id for a in aspects)
    u = _usage(stop_reason=stop_reason)
    process, coverage, derivation = TS.derive_pack_status(required, tuple(results),
                                                          stop_reason=stop_reason)
    dep = TS.compute_dependency_fingerprint(contract_fp, "v1", {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id="run-1",
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=contract_fp, source_policy_version="v1",
        section_id="company", topic_id=topic_id, question_ids=("q1",),
        aspect_results=tuple(results), materials=tuple(materials), facts=tuple(facts),
        outcome_refs=(), external_funnel=None, conflicts=(), not_found_audits=tuple(audits),
        unresolved=tuple(gaps), usage=u, uncertain_calls=(),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), tuple(aspects)


def _forge_blocked_unjustified():
    """伪造：aspect status=blocked 但无 blocking gap 且无 stop_reason（绕过 derive 手工构造）。

    blocked 在 derive 层本身要求 stop_reason（PackProcessStatus 硬约束），故正常路径无法产生
    「无依据 blocked」；此处手工填一个不一致的 process_status（pending）以单独命中
    _validate_aspect_semantics 的 blocked 门禁。
    """
    contract_fp = _sha("contract")
    snap = _aspect_snapshot("a1", "t1")
    result = TS.AspectResearchResult(
        aspect_id="a1", question_ids=("q1",), requirement_snapshot=snap, status="blocked",
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=())
    process = TS.PackProcessStatus("pending")
    coverage = TS.PackCoverageStatus("insufficient", (), ("a1",), ())
    derivation = TS.StatusDerivation(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, rule_version="forged",
        derivation_fingerprint=_sha("d"), per_aspect=(TS.AspectStatusEntry("a1", "blocked"),))
    dep = TS.compute_dependency_fingerprint(contract_fp, "v1", {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id="run-1",
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=contract_fp, source_policy_version="v1", section_id="company",
        topic_id="t1", question_ids=("q1",), aspect_results=(result,), materials=(),
        facts=(), outcome_refs=(), external_funnel=None, conflicts=(), not_found_audits=(),
        unresolved=(), usage=_usage(), uncertain_calls=(),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), (snap,)


def _tamper_and_refinalize(good: TS.TopicResearchPack, **repl):
    """篡改状态字段后清空 pack_id 再 finalize（保持 verify_pack_id 通过，仅触发重算门禁）。"""
    return TS.finalize_pack(dataclasses.replace(good, pack_id="", **repl))


def _requirement(aspects: tuple[TS.TopicAspectRequirementSnapshot, ...],
                 topic_id: str = "t1", contract_fp: str | None = None, spv: str = "v1",
                 **overrides) -> TS.TopicResearchRequirement:
    kw = dict(
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=contract_fp or _sha("contract"), source_policy_version=spv,
        section_id="company", topic_id=topic_id, question_ids=("q1",),
        aspects=aspects, allowed_capabilities=("evidence",), dependency_versions={})
    kw.update(overrides)
    return TS.TopicResearchRequirement(**kw)


def _build_covered_override(snap: TS.TopicAspectRequirementSnapshot | None = None,
                            topic_id: str = "t1",
                            fact_authority: TS.AuthorityAssessment | None = None,
                            material_authority: TS.AuthorityAssessment | None = None,
                            citation_evidence_id: str = "ev1",
                            obtained_fields: tuple[str, ...] = ("f1",),
                            coverage_rules: tuple[str, ...] | None = None,
                            required_fields: tuple[str, ...] | None = None,
                            sufficiency: TS.SufficiencyAssessment | None = None):
    """构造单个 covered aspect 的 finalized Pack + 对应冻结投影（供 §四 残余门禁反例覆盖）。

    默认产出一个可通过全部门禁的最小真实闭合链（material + fact + citation + 确定性
    authoritative authority + coverage 证明）。各参数可定点覆盖 authority/citation/
    obtained_fields/coverage_rules/sufficiency 以制造指定门禁违规。
    """
    snap = snap or _aspect_snapshot("a1", topic_id)
    if coverage_rules is not None or required_fields is not None:
        snap = dataclasses.replace(
            snap, coverage_rules=coverage_rules or snap.coverage_rules,
            required_fields=required_fields or snap.required_fields)
    m_auth = material_authority or _evidence_authority()
    material = TS.ResearchMaterial(
        material_id="m-a1", material_type="evidence_span", source_identity="src",
        locator=_evidence_locator(), payload_ref=_payload_ref("evidence_span", _evidence_locator()),
        content_hash=_sha("mat:m-a1"), authority_assessment=m_auth)
    f_auth = fact_authority or _evidence_authority()
    fact = TS.SupportedFact(
        fact_id="f-a1", text="fact text", fact_type="fact", aspect_ids=("a1",),
        citation_refs=(TS.CitationRef(ref_type="evidence", evidence_id=citation_evidence_id),),
        source_authority=f_auth, obtained_fields=obtained_fields)
    result = TS.AspectResearchResult(
        aspect_id="a1", question_ids=("q1",), requirement_snapshot=snap, status="covered",
        supported_fact_ids=("f-a1",), material_ids=("m-a1",), attempted_need_ids=(),
        unresolved_ids=(), sufficiency_assessment=sufficiency)
    process, coverage, derivation = TS.derive_pack_status(("a1",), (result,), stop_reason=None)
    dep = TS.compute_dependency_fingerprint(_sha("contract"), "v1", {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id="run-1",
        task_id="task1", company_id="300750", report_as_of=None, contract_version="v1",
        contract_fingerprint=_sha("contract"), source_policy_version="v1", section_id="company",
        topic_id=topic_id, question_ids=("q1",), aspect_results=(result,),
        materials=(material,), facts=(fact,), outcome_refs=(), external_funnel=None,
        conflicts=(), not_found_audits=(), unresolved=(), usage=_usage(), uncertain_calls=(),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), (snap,)


# -- 最小 typed resolver（R1-B 依赖注入边界；离线 fake） --

class _GoodResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        return TS.ResolvedPayload(object_type=pr.object_type, authority_identity=pr.authority_identity,
                                  version=pr.version, locator=pr.locator,
                                  content_hash=pr.content_hash, payload_bytes=None)


class _DanglingResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload | None:
        return None


class _WrongTypeResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        wrong = "external_snapshot" if pr.object_type != "external_snapshot" else "evidence_span"
        return TS.ResolvedPayload(object_type=wrong, authority_identity=pr.authority_identity,
                                  version=pr.version, locator=pr.locator,
                                  content_hash=pr.content_hash, payload_bytes=None)


class _WrongVersionResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        return TS.ResolvedPayload(object_type=pr.object_type, authority_identity=pr.authority_identity,
                                  version="v-wrong", locator=pr.locator,
                                  content_hash=pr.content_hash, payload_bytes=None)


class _WrongHashResolver:
    def resolve(self, pr: TS.MaterialPayloadRef) -> TS.ResolvedPayload:
        return TS.ResolvedPayload(object_type=pr.object_type, authority_identity=pr.authority_identity,
                                  version=pr.version, locator=pr.locator,
                                  content_hash=_sha("wrong-hash"), payload_bytes=None)


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

    # =========================================================================
    # 1. 序列化往返
    # =========================================================================
    pack, aspects = _build()
    d = pack.to_dict()
    back = TS.TopicResearchPack.from_dict(d)
    check(back == pack, "TopicResearchPack to_dict ↔ from_dict 往返相等")
    check(back.pack_id == pack.pack_id, "往返后 pack_id 保持")

    try:
        TS.TopicResearchPack.from_dict({**d, "schema_version": "999"})
        check(False, "非法 schema_version 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "非法 schema_version → SchemaValidationError")

    try:
        TS.PackProcessStatus(status="bogus")
        check(False, "非法 PackProcessStatus 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "非法 PackProcessStatus → SchemaValidationError")
    try:
        TS.SupportedFact(fact_id="f", text="t", fact_type="bogus", aspect_ids=("a1",),
                         citation_refs=(), source_authority=_evidence_authority())
        check(False, "非法 fact_type 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "非法 fact_type → SchemaValidationError")

    nested = json.loads(json.dumps(d))
    nested["usage"]["extra_field"] = "x"
    try:
        TS.TopicResearchPack.from_dict(nested)
        check(False, "嵌套 unknown-field 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "嵌套 usage.extra_field → SchemaValidationError")

    mat = _material("evidence_span", "m1")
    check(mat.locator.to_dict()["locator_type"] == "evidence",
          "EvidenceLocator discriminator 序列化 = evidence")
    check(mat.authority_assessment.to_dict()["authority_type"] == "evidence",
          "EvidenceAuthorityAssessment discriminator 序列化 = evidence")

    # =========================================================================
    # 2. 内容寻址
    # =========================================================================
    p1, _ = _build(run_id="run-1")
    p2, _ = _build(run_id="run-2")
    check(p1.pack_id == p2.pack_id, "run_id 不同 → pack_id 相同（run_id 不入身份）")
    check(p1.content_fingerprint() == p2.content_fingerprint(),
          "run_id 不入 content_fingerprint")
    check("run-1" not in p1.pack_id, "pack_id 不含 run_id 字符串")
    check("run-1" not in p1.dependency_fingerprint, "dependency_fingerprint 不含 run_id")

    p_fact_a, _ = _build(facts=(_fact("f1", ("a1",), "text A"),))
    p_fact_b, _ = _build(facts=(_fact("f1", ("a1",), "text B"),))
    check(p_fact_a.content_fingerprint() != p_fact_b.content_fingerprint(),
          "内容变化 → content_fingerprint 变化")
    check(p_fact_a.pack_id != p_fact_b.pack_id, "内容变化 → pack_id 变化")

    p_dep_a, _ = _build(contract_fp=_sha("contract-A"))
    p_dep_b, _ = _build(contract_fp=_sha("contract-B"))
    check(p_dep_a.dependency_fingerprint != p_dep_b.dependency_fingerprint,
          "依赖变化 → dependency_fingerprint 变化")
    check(p_dep_a.pack_id != p_dep_b.pack_id, "依赖变化 → pack_id 变化")

    # =========================================================================
    # 3/4/5/6/8/12/16 需要临时 DB；下面分块
    # =========================================================================

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "harness.db"
        Store.init_topic_store(db)

        # -- 5. current 指针 + 落盘/读回 --
        p, aspects = _build()
        req = _requirement(aspects)
        res = Store.commit_pack(p, req, _GoodResolver())
        check(res.reused is False and res.current_switched is True, "首次 commit 非复用且切换 current")
        got = Store.get_pack(p.pack_id)
        check(got is not None and got.pack_id == p.pack_id, "get_pack(pack_id) 读回")
        cur = Store.get_current_pack(p.identity())
        check(cur is not None and cur.pack_id == p.pack_id, "get_current_pack(identity) 读当前")
        hist = Store.list_pack_history(p.identity())
        check(len(hist) == 1 and hist[0].pack_id == p.pack_id, "list_pack_history 含已提交 Pack")

        # -- 3. 幂等复用 --
        res2 = Store.commit_pack(p, req, _GoodResolver())
        check(res2.reused is True and res2.current_switched is False, "同 pack_id 同内容 → 幂等复用")
        check(Store.get_pack(p.pack_id) is not None, "复用后 Pack 仍可读")

        # -- 4. 冲突：错身份/公司/日期/契约/任务 fail-closed --
        cases = [
            ("错任务 task_id", {"task_id": "task-X"}),
            ("错公司 company_id", {"company_id": "600000"}),
            ("错日期 report_as_of", {"report_as_of": "2025-12-31"}),
            ("错契约 contract_fingerprint", {"contract_fp": _sha("other-contract")}),
            ("错政策 source_policy_version", {"spv": "v99"}),
        ]
        for label, ov in cases:
            bad_req = _requirement(aspects, **ov)
            try:
                Store.commit_pack(p, bad_req)
                check(False, f"{label} 应 fail-closed")
            except (TS.SchemaValidationError, Store.TopicStoreValidationError):
                check(True, f"{label} → fail-closed")

        # -- 12. 指纹不符拒绝 resume --
        dep = p.dependency_fingerprint
        check(Checkpoint.verify_dependency_fingerprint(p, dep) is True, "指纹一致 → 可 resume")
        check(Checkpoint.verify_dependency_fingerprint(p, _sha("other-env")) is False,
              "指纹不符 → 拒绝 resume")
        try:
            Checkpoint.verify_dependency_fingerprint(p, "")
            check(False, "空期望指纹应被拒绝")
        except ValueError:
            check(True, "空期望指纹 → ValueError")

        # -- 12. checkpoint 只读加载（失效前） --
        cp = Checkpoint.load_checkpoint(p.identity(), db)
        check(cp is not None and cp.pack.pack_id == p.pack_id, "load_checkpoint 只读加载 current")
        cp_by_id = Checkpoint.load_checkpoint_by_pack_id(p.pack_id, db)
        check(cp_by_id is not None and cp_by_id.pack.pack_id == p.pack_id,
              "load_checkpoint_by_pack_id 只读加载")
        cps = Checkpoint.list_checkpoints(db)
        check(len(cps) >= 1 and any(c.pack.pack_id == p.pack_id for c in cps),
              "list_checkpoints 只读列出 current")
        hist_cp = Checkpoint.load_history(p.identity(), db)
        check(len(hist_cp) >= 1 and all(c.pack.topic_id == "t1" for c in hist_cp),
              "load_history 只读列出历史")

        # 只读不建库
        missing = Path(td) / "nope.db"
        check(Checkpoint.load_checkpoint(p.identity(), missing) is None,
              "库不存在 → load_checkpoint 返回 None")
        check(not missing.exists(), "只读路径不创建库文件")

        # -- 7. migration：结构不一致 fail-closed、不创建 legacy 表 --
        check(Store.applied_schema_version() == "1", "applied_schema_version == 1")
        c = sqlite3.connect(str(db))
        legacy_tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        check("run_manifest" not in legacy_tables, "topic_store 不创建 run_manifest")
        check("question_outcome" not in legacy_tables, "topic_store 不创建 question_outcome")
        check("schema_migrations" not in legacy_tables, "topic_store 不创建 legacy schema_migrations")

        # -- CLI --
        cli_ck = CLI.run(["--self-check", "--db", str(db)])
        check(cli_ck.get("ok") is True, "CLI --self-check 通过")
        cli_pack = CLI.run(["--pack", p.pack_id, "--db", str(db)])
        check(cli_pack.get("ok") is True and cli_pack["pack"]["pack_id"] == p.pack_id,
              "CLI --pack 读回")
        cli_cur = CLI.run(["--current", "--task-id", "task1", "--company-id", "300750",
                           "--contract-fingerprint", p.contract_fingerprint,
                           "--source-policy-version", "v1", "--section-id", "company",
                           "--topic-id", "t1", "--db", str(db)])
        check(cli_cur.get("ok") is True and cli_cur["pack"]["pack_id"] == p.pack_id,
              "CLI --current 读回")

        # 破坏结构（DROP 一张 topic 表），self_check / _verify 应 fail-closed
        c = sqlite3.connect(str(db))
        c.execute("DROP TABLE topic_fact")
        c.commit()
        c.close()
        rc = sqlite3.connect(str(db))
        rc.row_factory = sqlite3.Row
        try:
            Store._verify_structure_matches_latest(rc)
            check(False, "缺表应结构校验失败")
        except RuntimeError:
            check(True, "缺 topic_fact → 结构校验 fail-closed")
        finally:
            rc.close()
        try:
            Store.self_check()
            check(False, "Store.self_check 缺表应抛异常")
        except RuntimeError:
            check(True, "Store.self_check 缺表 → RuntimeError（低层只读校验传播）")
        cli_bad = CLI.run(["--self-check", "--db", str(db)])
        check(cli_bad.get("ok") is False, "CLI --self-check 结构损坏 → ok=False")

        # -- 16. 只读打开前后文件 hash 不变（另一份干净 DB） --
        db2 = Path(td) / "clean.db"
        Store.init_topic_store(db2)
        Store.commit_pack(p, req, _GoodResolver())
        h_before = hashlib.sha256(db2.read_bytes()).hexdigest()
        Checkpoint.load_checkpoint(p.identity(), db2)
        Checkpoint.list_checkpoints(db2)
        CLI.run(["--self-check", "--db", str(db2)])
        h_after = hashlib.sha256(db2.read_bytes()).hexdigest()
        check(h_before == h_after, "只读路径（checkpoint/CLI self-check）前后文件 hash 不变")

    # =========================================================================
    # 9. 状态适配（双轴）
    # =========================================================================
    oc = SimpleNamespace(completion_status="COMPLETED", state=SimpleNamespace(question_id="q1"))
    elig = TS.adapt_outcome_completion(oc)
    check(isinstance(elig, TS.AtomicOutcomeEligibility) and elig.eligible
          and elig.reason_code == "ATOMIC_COMPLETED",
          "adapt_outcome_completion 产出原子资格（非 Pack 状态）")
    oc_gap = SimpleNamespace(completion_status="COMPLETED_WITH_GAPS",
                             state=SimpleNamespace(question_id="q1"))
    check(TS.adapt_outcome_completion(oc_gap).eligible, "COMPLETED_WITH_GAPS → 原子资格 eligible")
    oc_research = SimpleNamespace(completion_status="UNRESOLVED",
                                  state=SimpleNamespace(question_id="q1"))
    check(TS.adapt_outcome_completion(oc_research).eligible is False,
          "UNRESOLVED → 原子资格 not eligible")
    try:
        TS.adapt_outcome_completion(SimpleNamespace(completion_status="BOGUS",
                                                    state=SimpleNamespace(question_id="q1")))
        check(False, "未知 completion_status 应 fail-closed")
    except TS.StateAdaptationError:
        check(True, "未知 completion_status → StateAdaptationError")

    r5 = tuple(TS.AspectResearchResult(
        aspect_id=a, question_ids=("q1",), requirement_snapshot=_aspect_snapshot(a),
        status=("covered" if a == "a1" else "partial"),
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=())
        for a in ("a1", "a2", "a3", "a4", "a5"))
    proc, cov, der = TS.derive_pack_status(("a1", "a2", "a3", "a4", "a5"), r5)
    check(proc.status != "finished" and cov.status == "insufficient",
          "5 required 仅 1 covered → process≠finished + coverage=insufficient")

    mixed = tuple(TS.AspectResearchResult(
        aspect_id=a, question_ids=("q1",), requirement_snapshot=_aspect_snapshot(a),
        status={"a1": "covered", "a2": "covered", "a3": "covered",
                "a4": "not_found", "a5": "not_applicable"}[a],
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=(),
        not_found_audit_id=("audit-a4" if a == "a4" else None))
        for a in ("a1", "a2", "a3", "a4", "a5"))
    proc2, cov2, der2 = TS.derive_pack_status(("a1", "a2", "a3", "a4", "a5"), mixed)
    check(proc2.status == "finished" and cov2.status == "complete_with_gaps",
          "3 covered + 1 not_found + 1 not_applicable → finished + complete_with_gaps")
    check(cov2.covered_aspect_ids == ("a1", "a2", "a3") and "a4" in cov2.gap_aspect_ids,
          "coverage 保留 covered / gap 明细")

    blocked = tuple(TS.AspectResearchResult(
        aspect_id=a, question_ids=("q1",), requirement_snapshot=_aspect_snapshot(a),
        status=("covered" if a == "a1" else "blocked"),
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=())
        for a in ("a1", "a2"))
    proc3, cov3, der3 = TS.derive_pack_status(("a1", "a2"), blocked, stop_reason="REPORT_BLOCKED")
    check(proc3.status == "blocked" and proc3.hard_stop_reason == "REPORT_BLOCKED",
          "hard block → process=blocked + hard_stop_reason")
    check(cov3.covered_aspect_ids == ("a1",), "block 不清空已覆盖 aspect 明细")

    proc4, cov4, der4 = TS.derive_pack_status(("a1", "a2"), blocked, stop_reason="BUDGET_EXHAUSTED")
    check(proc4.status == "stopped_by_budget" and cov4.covered_aspect_ids == ("a1",),
          "预算耗尽 → stopped_by_budget 且保留 covered")

    try:
        TS.AspectResearchResult(aspect_id="a1", question_ids=("q1",),
                                requirement_snapshot=_aspect_snapshot("a1"), status="COMPLETED",
                                supported_fact_ids=(), material_ids=(), attempted_need_ids=(),
                                unresolved_ids=())
        check(False, "跨空间状态字符串 COMPLETED 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "跨空间状态字符串 COMPLETED → SchemaValidationError")

    try:
        TS.derive_pack_status(("a1", "a2"), (TS.AspectResearchResult(
            aspect_id="a1", question_ids=("q1",), requirement_snapshot=_aspect_snapshot("a1"),
            status="covered", supported_fact_ids=(), material_ids=(), attempted_need_ids=(),
            unresolved_ids=()),), stop_reason=None)
        check(False, "缺 required aspect 应 fail-closed")
    except TS.StateAdaptationError:
        check(True, "derive_pack_status 缺 required aspect → StateAdaptationError")

    u = _usage()
    check("completion_status" not in u.to_dict(), "TopicUsageSnapshot 无 completion_status 字段")
    check("completion_status" not in TS.PackProcessStatus("finished").to_dict(),
          "PackProcessStatus 无 completion_status 字段")

    # =========================================================================
    # 10. 权威分离（三类强类型 + locator 联合）
    # =========================================================================
    for auth in (_evidence_authority(), _financial_authority(), _external_authority()):
        back_auth = TS.authority_from_dict(auth.to_dict())
        check(type(back_auth) is type(auth), f"authority 往返保型: {type(auth).__name__}")

    check(TS.locator_from_dict(_evidence_locator().to_dict()).locator_type == "evidence",
          "evidence locator 往返")
    check(TS.locator_from_dict(_financial_locator().to_dict()).locator_type == "financial_snapshot",
          "financial locator 往返")
    check(TS.locator_from_dict(_external_locator().to_dict()).locator_type == "external_snapshot",
          "external locator 往返")

    try:
        TS.ResearchMaterial(material_id="m", material_type="evidence_span", source_identity="s",
                            locator=_financial_locator(), payload_ref=_payload_ref("evidence_span", _financial_locator()),
                            content_hash=_sha("x"), authority_assessment=_evidence_authority())
        check(False, "material_type/locator mismatch 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "material_type=evidence_span + financial locator → SchemaValidationError")

    try:
        TS.ResearchMaterial(material_id="m", material_type="structured", source_identity="s",
                            locator=_financial_locator(), payload_ref=_payload_ref("structured", _financial_locator()),
                            content_hash=_sha("x"), authority_assessment=_evidence_authority())
        check(False, "material_type/authority mismatch 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "material_type=structured + evidence authority → SchemaValidationError")

    v1 = TS.ValueIdentity(value_kind="k", metric="m", unit="u", period="p", scope="s",
                          amount_canonical="100")
    v2 = TS.ValueIdentity(value_kind="k", metric="m", unit="u", period="p", scope="s",
                          amount_canonical="200")
    check(v1.amount_canonical != v2.amount_canonical and v1.to_dict() != v2.to_dict(),
          "ValueIdentity 金额不同 → 不同语义身份")

    # =========================================================================
    # 11. 两门独立（authority 通过 ≠ sufficiency 通过）
    # =========================================================================
    suff = TS.SufficiencyAssessment(aspect_id="a1", conclusion_id=None,
                                    supporting_fact_ids=("f1",), supporting_source_ids=("s1",),
                                    rule="r", rule_version="1", threshold_met=False,
                                    independent_c_count=0, assessor_version="av")
    back_suff = TS.SufficiencyAssessment.from_dict(suff.to_dict())
    check(back_suff == suff, "SufficiencyAssessment 往返")
    check(suff.threshold_met is False, "authority 通过不代表 sufficiency threshold_met=True")
    try:
        TS.SufficiencyAssessment(aspect_id=None, conclusion_id=None, supporting_fact_ids=(),
                                 supporting_source_ids=(), rule="r", rule_version="1",
                                 threshold_met=True, independent_c_count=0, assessor_version="av")
        check(False, "SufficiencyAssessment 缺 aspect_id/conclusion_id 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "SufficiencyAssessment 缺 aspect_id+conclusion_id → SchemaValidationError")

    # =========================================================================
    # 14. payload 不可变引用（hash/类型 mismatch fail-closed）
    # =========================================================================
    try:
        TS.MaterialPayloadRef(object_type="evidence_span", authority_identity="a", version="v",
                              content_hash="not-hex", locator=_evidence_locator(),
                              created_dependency_fingerprint=_sha("c"))
        check(False, "MaterialPayloadRef 非法 content_hash 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "MaterialPayloadRef 非法 content_hash → SchemaValidationError")

    payload = b"hello funnel"
    ef = TS.ExternalFunnelSnapshot(schema_version="1", canonical_hash=hashlib.sha256(payload).hexdigest(),
                                   producer_version="pv1")
    check(TS.verify_external_funnel_payload(ef, payload) is True, "外部漏斗 payload hash 匹配")
    check(TS.verify_external_funnel_payload(ef, b"tampered") is False, "外部漏斗 payload hash 不符")
    try:
        TS.ExternalFunnelSnapshot(schema_version="1", canonical_hash="not-hex", producer_version="pv")
        check(False, "ExternalFunnelSnapshot 非法 canonical_hash 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "ExternalFunnelSnapshot 非法 canonical_hash → SchemaValidationError")

    try:
        TS.EvidenceLocator()
        check(False, "EvidenceLocator 无 page/block/section 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "EvidenceLocator 缺定位 → SchemaValidationError")
    try:
        TS.EvidenceLocator(section_path="s1")
        check(False, "EvidenceLocator 无 document 身份应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "EvidenceLocator 缺 document_id/version → SchemaValidationError")

    # =========================================================================
    # 15. 冻结投影（AspectV2 22 必需 + 4 扩展全投影）
    # =========================================================================
    snap = _aspect_snapshot("a1")
    snap_d = snap.to_dict()
    missing = [f for f in __import__("contracts.schema_v2", fromlist=["REQUIRED_ASPECT_FIELDS"])
               .REQUIRED_ASPECT_FIELDS if f not in snap_d]
    check(len(missing) == 0, f"冻结投影覆盖 AspectV2 全部 22 必需字段（缺失 {missing}）")
    for ext in ("business_review_reason", "derived_from_scope", "transmission_layers",
                "transmission_channel"):
        check(ext in snap_d, f"冻结投影含扩展字段 {ext}")
    er = _req_ref()
    check(er.requirement_id and len(er.contract_sha256) == 64
          and len(er.requirement_fingerprint) == 64 and er.schema_version,
          "EvidenceRequirementRef 绑定 4 项身份")
    sp = _policy_ref()
    check(sp.policy_version and len(sp.content_fingerprint) == 64,
          "SourcePolicyRef 绑定 policy version + content fingerprint")
    try:
        TS.EvidenceRequirementRef(requirement_id="", contract_sha256=_sha("c"),
                                  requirement_fingerprint=_sha("r"), schema_version="1")
        check(False, "EvidenceRequirementRef 空 requirement_id 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "EvidenceRequirementRef 空 requirement_id → SchemaValidationError")

    # =========================================================================
    # 16. 数据库共存初始化（两种顺序）
    # =========================================================================
    with tempfile.TemporaryDirectory() as td2:
        dbA = Path(td2) / "a.db"
        LegacyCheckpoint.init_db(dbA)
        Store.init_topic_store(dbA)
        LegacyCheckpoint.write_run_manifest(LegacyCheckpoint.RunManifest(
            run_id="r1", dataset_sha256="d", company_id="300750", report_as_of=None,
            contract_version="v1", router_fingerprint="rf", prompt_versions={"a": "1"},
            model="deepseek-v4-pro", budget={"max_tokens": 8000}, evidence_fingerprint="ef",
            snapshot_id=None, external_policy_version="v1", harness_fingerprint="hf"), dbA)
        check(LegacyCheckpoint.get_run_manifest("r1", dbA) is not None,
              "顺序① legacy run_manifest 可用")
        check(Store.applied_schema_version() == "1", "顺序① topic_store 可用")
        dbB = Path(td2) / "b.db"
        Store.init_topic_store(dbB)
        LegacyCheckpoint.init_db(dbB)
        LegacyCheckpoint.write_run_manifest(LegacyCheckpoint.RunManifest(
            run_id="r1", dataset_sha256="d", company_id="300750", report_as_of=None,
            contract_version="v1", router_fingerprint="rf", prompt_versions={"a": "1"},
            model="deepseek-v4-pro", budget={"max_tokens": 8000}, evidence_fingerprint="ef",
            snapshot_id=None, external_policy_version="v1", harness_fingerprint="hf"), dbB)
        check(LegacyCheckpoint.get_run_manifest("r1", dbB) is not None,
              "顺序② legacy run_manifest 可用")
        check(Store.applied_schema_version() == "1", "顺序② topic_store 可用")
        Store.init_topic_store(dbB)
        LegacyCheckpoint.init_db(dbB)
        check(Store.applied_schema_version() == "1", "重复初始化幂等（topic_store）")
        check(LegacyCheckpoint.get_run_manifest("r1", dbB) is not None,
              "重复初始化幂等（legacy）")

    # =========================================================================
    # 13. 正式链边界（静态）
    # =========================================================================
    src_files = ["harness/topic_schema.py", "harness/topic_store.py",
                 "harness/topic_checkpoint.py", "harness/topic_store_cli.py"]
    combined = "\n".join((ROOT / f).read_text(encoding="utf-8") for f in src_files)
    import_lines = [ln for ln in combined.splitlines()
                    if ln.strip().startswith(("import ", "from "))]
    check(not any("topic_research" in ln or "run_topic" in ln for ln in import_lines),
          "R1-B 模块不 import sections.topic_research / run_topic")
    check(not any("harness.runtime" in ln or "ToolRegistry" in ln or "tool_registry" in ln
                  for ln in import_lines),
          "R1-B 模块不 import harness.runtime / ToolRegistry")
    v1_bytes = (ROOT / "templates" / "contracts" / "standard_v2.yaml").read_bytes()
    check(hashlib.sha256(v1_bytes).hexdigest() == V1_FIXED_SHA256,
          "standard_v2.yaml 固定字节 SHA256 不变（v1 未被覆盖）")

    # =========================================================================
    # §四 反例回归（4 缺陷 + 7 修复的定点反例）
    # =========================================================================

    # --- 缺陷 1：省略 requirement 仍可提交 → requirement 必填 ---
    p0, aspects0 = _build()
    try:
        Store.commit_pack(p0)  # type: ignore[call-arg]
        check(False, "T1 省略 requirement 应拒绝提交")
    except TypeError:
        check(True, "T1 commit_pack 缺 requirement → TypeError（必填，无绕过入口）")

    # --- requirement 身份 / aspect / question / dependency 全量一致 ---
    try:
        Store.commit_pack(p0, _requirement(aspects0, task_id="task-X"))
        check(False, "T2 错 task_id 应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T2 错 task_id → TopicStoreValidationError")

    try:
        Store.commit_pack(p0, _requirement(tuple(_aspect_snapshot(a) for a in ("a1", "aX"))))
        check(False, "T3 aspect 集合错应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T3 aspect 集合不一致 → TopicStoreValidationError")

    try:
        Store.commit_pack(p0, _requirement(aspects0, question_ids=("q1", "qX")))
        check(False, "T4 question 集合错应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T4 question 集合不一致 → TopicStoreValidationError")

    try:
        Store.commit_pack(p0, _requirement(aspects0, dependency_versions={"assessor": "v9"}))
        check(False, "T5 dependency_fingerprint 错应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T5 dependency_fingerprint 不一致 → TopicStoreValidationError")

    # --- 缺陷 2 + 修复 2：aspect 语义 + 双轴状态独立重算 ---
    for label, kwargs in [
        ("T6 空壳 covered（无 material/fact）", {"covered_material": False, "covered_fact": False}),
        ("T7 covered 由 rejected authority 支撑", {"covered_authority_verdict": "rejected"}),
        ("T8 covered fact 空 citation", {"covered_citation": False}),
    ]:
        bad, bad_aspects = _build_raw({"a1": "covered"}, **kwargs)
        try:
            Store.commit_pack(bad, _requirement(bad_aspects))
            check(False, f"{label} 应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, f"{label} → TopicStoreValidationError")

    bad_nf, nf_aspects = _build_raw({"a1": "not_found"}, not_found_qualified=False)
    try:
        Store.commit_pack(bad_nf, _requirement(nf_aspects))
        check(False, "T9 not_found 未 qualified 应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T9 not_found 未 qualified audit → TopicStoreValidationError")

    bad_partial, partial_aspects = _build_raw({"a1": "partial"}, partial_gap=False)
    try:
        Store.commit_pack(bad_partial, _requirement(partial_aspects))
        check(False, "T10 partial 无 gap/not_found 应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T10 partial 无 gap/not_found → TopicStoreValidationError")

    bad_block, block_aspects = _forge_blocked_unjustified()
    try:
        Store.commit_pack(bad_block, _requirement(block_aspects))
        check(False, "T11 blocked 无 blocking gap/stop_reason 应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T11 blocked 无依据 → TopicStoreValidationError")

    bad_na, na_aspects = _build_raw({"a1": "not_applicable"}, not_applicable_policy=False)
    try:
        Store.commit_pack(bad_na, _requirement(na_aspects))
        check(False, "T12 not_applicable 无 applicability_policy 应 fail-closed")
    except Store.TopicStoreValidationError:
        check(True, "T12 not_applicable 无依据 → TopicStoreValidationError")

    # 双轴状态篡改（重算后 fail-closed，不信任调用方填写的 status）
    good, good_aspects = _build()
    for label, repl in [
        ("T13 process_status 篡改", {"process_status": TS.PackProcessStatus("pending")}),
        ("T14 coverage_status 篡改", {"coverage_status": TS.PackCoverageStatus(
            status="insufficient", covered_aspect_ids=(), gap_aspect_ids=("a1",),
            not_applicable_aspect_ids=())}),
        ("T15 status_derivation 篡改", {"status_derivation": TS.StatusDerivation(
            schema_version="1", rule_version="forged", derivation_fingerprint=_sha("x"),
            per_aspect=())}),
    ]:
        tampered = _tamper_and_refinalize(good, **repl)
        try:
            Store.commit_pack(tampered, _requirement(good_aspects))
            check(False, f"{label} 应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, f"{label} → TopicStoreValidationError（重算不一致）")

    # --- 修复 3：content 身份含 coverage/status/usage，改 content 即改 pack_id ---
    p_cov_a, _ = _build(statuses={"a1": "covered"})
    p_cov_b, _ = _build(statuses={"a1": "not_found"})
    check(p_cov_a.pack_id != p_cov_b.pack_id, "T16 coverage 变化 → pack_id 变化（不再幂等复用）")
    check(p_cov_a.content_fingerprint() != p_cov_b.content_fingerprint(),
          "T16 coverage 变化 → content_fingerprint 变化")
    p_usage_a, _ = _build(usage=_usage(stop_reason=None))
    p_usage_b, _ = _build(usage=_usage(stop_reason="BUDGET_EXHAUSTED"))
    check(p_usage_a.pack_id != p_usage_b.pack_id, "T16 usage/stop_reason 变化 → pack_id 变化")
    forged = dataclasses.replace(p_cov_b, pack_id=p_cov_a.pack_id)
    try:
        forged.verify_pack_id()
        check(False, "T17 同 pack_id 不同内容应被 verify_pack_id 拒绝")
    except TS.SchemaValidationError:
        check(True, "T17 同 pack_id 不同内容 → verify_pack_id SchemaValidationError")

    # --- 缺陷 3 + 修复 4：失效 current / 损坏读 fail-closed ---
    with tempfile.TemporaryDirectory() as td3:
        db3 = Path(td3) / "life.db"
        Store.init_topic_store(db3)
        p_life, aspects_life = _build()
        req_life = _requirement(aspects_life)
        Store.commit_pack(p_life, req_life, _GoodResolver())
        check(Store.get_current_pack(p_life.identity()) is not None,
              "失效前 current 可用（前置）")

        Store.mark_invalidated(p_life.pack_id, "invalidated", reason="contract changed")
        check(Store.get_current_pack(p_life.identity()) is None,
              "T18 invalidated 后 get_current_pack → None（不再返回失效 current）")
        cl = Store.load_current_pack(p_life.identity())
        check(cl.pack is None and cl.available is False and cl.reason == "invalidated",
              "T18 load_current_pack available=False + reason=invalidated")
        check(Store.list_current_packs() == [], "T19 list_current_packs 排除失效 current")
        check(Checkpoint.load_checkpoint(p_life.identity(), db3) is None,
              "T19 invalidated → load_checkpoint 不返回（不可 resume）")
        check(Checkpoint.load_checkpoint_by_pack_id(p_life.pack_id, db3) is not None,
              "T19 load_checkpoint_by_pack_id 显式历史读仍可返回失效 Pack")
        check(len(Store.list_pack_history(p_life.identity())) == 1,
              "T19 list_pack_history 保留失效历史（不 DELETE）")

        # 损坏：篡改 content_fingerprint → 读/提交 fail-closed
        # （用另一份非失效 Pack 隔离终态失效语义，确保命中 StorageCorruptionError 而非终态拒绝）
        p_life2, aspects_life2 = _build(topic_id="t2")
        Store.commit_pack(p_life2, _requirement(aspects_life2, topic_id="t2"), _GoodResolver())
        c3 = sqlite3.connect(str(db3))
        c3.execute("DROP TRIGGER IF EXISTS trg_topic_pack_no_update")
        c3.execute("UPDATE topic_pack SET content_fingerprint=? WHERE pack_id=?",
                   (_sha("garbage"), p_life2.pack_id))
        c3.commit()
        c3.close()
        try:
            Store.get_pack(p_life2.pack_id)
            check(False, "T20 损坏 content_fingerprint 读回应 fail-closed")
        except Store.StorageCorruptionError:
            check(True, "T20 损坏 content_fingerprint → get_pack StorageCorruptionError")
        try:
            Store.commit_pack(p_life2, _requirement(aspects_life2, topic_id="t2"), _GoodResolver())
            check(False, "T20 损坏 pack 复用应 fail-closed")
        except Store.StorageCorruptionError:
            check(True, "T20 损坏 pack 复用 → StorageCorruptionError")

    # --- 修复 5：严格只读（mode=ro + query_only=ON）写必失败 ---
    with tempfile.TemporaryDirectory() as td4:
        db4 = Path(td4) / "ro.db"
        Store.init_topic_store(db4)
        Store.commit_pack(p0, _requirement(aspects0), _GoodResolver())
        ro = open_readonly_conn(db4)
        check(ro is not None, "只读连接可打开已存在库")
        try:
            ro.execute("CREATE TABLE should_fail (x INTEGER)")
            check(False, "T21 只读连接 CREATE TABLE 应失败")
        except sqlite3.OperationalError as e:
            check("readonly" in str(e).lower(),
                  "T21 只读连接 CREATE TABLE → OperationalError(readonly)")
        finally:
            ro.close()
        try:
            ro2 = open_readonly_conn(db4)
            ro2.execute("INSERT INTO topic_event (event_id, pack_id, event_type, event_at) "
                        "VALUES ('x','y','z','')")
            check(False, "T21 只读连接 INSERT 应失败")
        except sqlite3.OperationalError:
            check(True, "T21 只读连接 INSERT → OperationalError")
        finally:
            ro2.close()
        # 只读连接不创建库文件
        missing2 = Path(td4) / "missing.db"
        check(open_readonly_conn(missing2) is None, "T21 缺库只读连接返回 None")
        check(not missing2.exists(), "T21 只读连接不创建库文件")

    # --- 修复 6：migration 单事务原子 + 禁 OR IGNORE/REPLACE + 前缀校验 ---
    check(all("INSERT OR IGNORE" not in s.upper() and "INSERT OR REPLACE" not in s.upper()
              for s in Store._ddl_statements()),
          "T22 DDL 不含 INSERT OR IGNORE / INSERT OR REPLACE")
    try:
        Store._validate_ddl_prefixes()
        check(True, "T22 DDL 前缀校验通过（仅 topic_/idx_topic_/trg_topic_）")
    except RuntimeError:
        check(False, "T22 DDL 前缀校验不应失败")

    orig_ddl = Store._ddl_statements
    orig_validate = Store._validate_ddl_prefixes

    def bad_ddl():
        stmts = orig_ddl()
        stmts.insert(3, "CREATE TABLE topic_bad_rollback (")
        return stmts

    with tempfile.TemporaryDirectory() as td5:
        fault_db = Path(td5) / "fault.db"
        Store._ddl_statements = bad_ddl
        Store._validate_ddl_prefixes = lambda: None
        try:
            Store.init_topic_store(fault_db)
            check(False, "T22 故障注入的 init 应失败")
        except sqlite3.OperationalError:
            check(True, "T22 故障注入 → init 失败")
        finally:
            Store._ddl_statements = orig_ddl
            Store._validate_ddl_prefixes = orig_validate
        c5 = sqlite3.connect(str(fault_db))
        tables5 = {r[0] for r in c5.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c5.close()
        check("topic_pack" not in tables5 and "topic_schema_migrations" not in tables5,
              "T22 单事务：故障回滚后无残留表（无半初始化）")
        # 前缀校验拒绝非 topic_ 对象
        Store._ddl_statements = lambda: ["CREATE TABLE evil (x INTEGER)"]
        try:
            Store._validate_ddl_prefixes()
            check(False, "T22 非 topic_ 前缀 DDL 应被拒绝")
        except RuntimeError:
            check(True, "T22 非 topic_ 前缀 DDL → RuntimeError")
        finally:
            Store._ddl_statements = orig_ddl

    # --- 修复 7：MaterialPayloadRef 最小 typed resolver 可验证 ---
    p_payload, aspects_payload = _build()
    check(TS.verify_pack_payloads(p_payload, _GoodResolver()) is None,
          "T23 good resolver → verify_pack_payloads 通过")
    for label, resolver in [
        ("T23 dangling", _DanglingResolver()),
        ("T24 object_type mismatch", _WrongTypeResolver()),
        ("T24 version mismatch", _WrongVersionResolver()),
        ("T24 content_hash mismatch", _WrongHashResolver()),
    ]:
        try:
            TS.verify_pack_payloads(p_payload, resolver)
            check(False, f"{label} 应 fail-closed")
        except TS.SchemaValidationError:
            check(True, f"{label} → SchemaValidationError（fail-closed）")
    with tempfile.TemporaryDirectory() as td6:
        db6 = Path(td6) / "res.db"
        Store.init_topic_store(db6)
        res6 = Store.commit_pack(p_payload, _requirement(aspects_payload), _GoodResolver())
        check(res6.reused is False, "T24 commit_pack + good resolver 成功落盘")
        try:
            Store.commit_pack(p_payload, _requirement(aspects_payload), _DanglingResolver())
            check(False, "T23 commit_pack + dangling resolver 应 fail-closed")
        except TS.SchemaValidationError:
            check(True, "T23 commit_pack + dangling resolver → SchemaValidationError")

    # =========================================================================
    # §四 最后四个残余门禁的定点反例（N1–N16）
    # =========================================================================

    # --- 门禁 1：PayloadResolver 必填 + material↔payload_ref typed 身份一致 ---
    with tempfile.TemporaryDirectory() as tdG1:
        Store.init_topic_store(Path(tdG1) / "g1.db")
        p_mat, asp_mat = _build()
        try:
            Store.commit_pack(p_mat, _requirement(asp_mat))
            check(False, "N1 有 material 但不提供 resolver 应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N1 有 material 无 resolver → TopicStoreValidationError（fail-closed）")
        p_nomat, asp_nomat = _build(statuses={"a1": "not_found"})
        res_n2 = Store.commit_pack(p_nomat, _requirement(asp_nomat))
        check(res_n2.reused is False, "N2 无 material → resolver 不必填，仍可提交")

    # material↔payload_ref typed 身份（构造期即拒绝）
    try:
        TS.ResearchMaterial(material_id="m", material_type="evidence_span", source_identity="s",
                            locator=_evidence_locator(),
                            payload_ref=_payload_ref("structured", _financial_locator()),
                            content_hash=_sha("x"), authority_assessment=_evidence_authority())
        check(False, "N3 material_type 与 payload_ref.object_type 不一致应拒绝")
    except TS.SchemaValidationError:
        check(True, "N3 material_type≠payload_ref.object_type → SchemaValidationError")

    try:
        TS.ResearchMaterial(material_id="m", material_type="evidence_span", source_identity="s",
                            locator=_evidence_locator(),
                            payload_ref=_payload_ref("evidence_span", _financial_locator()),
                            content_hash=_sha("x"), authority_assessment=_evidence_authority())
        check(False, "N4 material.locator 与 payload_ref.locator 不一致应拒绝")
    except TS.SchemaValidationError:
        check(True, "N4 material.locator≠payload_ref.locator → SchemaValidationError")

    for bad_fp in ("", "not-hex"):
        try:
            TS.MaterialPayloadRef(object_type="evidence_span", authority_identity="a", version="v",
                                  content_hash=_sha("p"), locator=_evidence_locator(),
                                  created_dependency_fingerprint=bad_fp)
            check(False, "N5 空/非法 created_dependency_fingerprint 应拒绝")
        except TS.SchemaValidationError:
            check(True, f"N5 created_dependency_fingerprint={bad_fp!r} → SchemaValidationError")

    # --- 门禁 2：authority 确定性重算 + coverage 确定性 + sufficiency 独立门 ---
    with tempfile.TemporaryDirectory() as tdG2:
        Store.init_topic_store(Path(tdG2) / "g2.db")

        bad_auth = TS.EvidenceAuthorityAssessment(evidence_id="ev1", verdict="authoritative")
        p_n6, asp_n6 = _build_covered_override(fact_authority=bad_auth)
        try:
            Store.commit_pack(p_n6, _requirement(asp_n6), _GoodResolver())
            check(False, "N6 自称 authoritative 但 current/inspected 全 False 应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N6 自称 authoritative 但字段不足 → TopicStoreValidationError")

        ev2_auth = TS.EvidenceAuthorityAssessment(
            evidence_id="ev2", document_id="doc1", document_version="v1", company_id="300750",
            is_current_document=True, is_current_set=True, page=1, fetched_inspected_nonempty=True,
            content_hash=_sha("evidence:ev2"), verdict="authoritative", reason="",
            validator_version="vv1")
        p_n7, asp_n7 = _build_covered_override(fact_authority=ev2_auth, citation_evidence_id="ev2")
        try:
            Store.commit_pack(p_n7, _requirement(asp_n7), _GoodResolver())
            check(False, "N7 material 与 fact 来源身份不一致应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N7 material↔fact 来源身份不一致 → TopicStoreValidationError")

        p_n8, asp_n8 = _build_covered_override(citation_evidence_id="wrong")
        try:
            Store.commit_pack(p_n8, _requirement(asp_n8), _GoodResolver())
            check(False, "N8 fact citation 与 source_authority 身份不一致应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N8 citation↔authority 身份不一致 → TopicStoreValidationError")

        p_n9, asp_n9 = _build_covered_override(coverage_rules=("required_fields_complete",
                                                              "bogus_rule"))
        try:
            Store.commit_pack(p_n9, _requirement(asp_n9), _GoodResolver())
            check(False, "N9 未知 coverage_rule 应 fail-closed")
        except Store.TopicStoreValidationError:
            check(True, "N9 未知 coverage_rule → coverage_rule_not_evaluable")

        p_n10, asp_n10 = _build_covered_override(obtained_fields=())
        try:
            Store.commit_pack(p_n10, _requirement(asp_n10), _GoodResolver())
            check(False, "N10 required_fields_complete 未满足应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N10 required_fields_complete 未满足 → TopicStoreValidationError")

        p_n11, asp_n11 = _build_covered_override(topic_id="industry_scale_cycle")
        try:
            Store.commit_pack(p_n11, _requirement(asp_n11, topic_id="industry_scale_cycle"),
                              _GoodResolver())
            check(False, "N11 关键结论缺 sufficiency 应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N11 关键结论缺 sufficiency → TopicStoreValidationError")

        p_n12, asp_n12 = _build_covered_override(topic_id="t1")
        res_n12 = Store.commit_pack(p_n12, _requirement(asp_n12), _GoodResolver())
        check(res_n12.reused is False, "N12 普通非关键事实无 sufficiency 仍可 covered")

        bad_er = TS.EvidenceRequirementRef(requirement_id="er1",
                                           contract_sha256=_sha("other-contract"),
                                           requirement_fingerprint=_sha("req:er1"),
                                           schema_version="1")
        bad_snap13 = dataclasses.replace(_aspect_snapshot("a1"),
                                         evidence_requirement_ids=(bad_er,))
        p_n13, asp_n13 = _build_covered_override(snap=bad_snap13)
        try:
            Store.commit_pack(p_n13, _requirement(asp_n13), _GoodResolver())
            check(False, "N13 requirement 内嵌 EvidenceRequirementRef contract 身份不一致应拒绝")
        except Store.TopicStoreValidationError:
            check(True, "N13 EvidenceRequirementRef.contract_sha256≠顶层 → TopicStoreValidationError")

    # --- 门禁 3：invalidated Pack 终态失效，禁止普通 recommit 复活 ---
    with tempfile.TemporaryDirectory() as tdG3:
        db7 = Path(tdG3) / "term.db"
        Store.init_topic_store(db7)
        p_life7, asp_life7 = _build()
        req_life7 = _requirement(asp_life7)
        Store.commit_pack(p_life7, req_life7, _GoodResolver())
        Store.mark_invalidated(p_life7.pack_id, "invalidated", reason="contract changed")
        try:
            Store.commit_pack(p_life7, req_life7, _GoodResolver())
            check(False, "N14 invalidated 同 pack_id 普通 recommit 应拒绝（不复活）")
        except Store.TopicStoreValidationError:
            check(True, "N14 invalidated recommit → TopicStoreValidationError")
        # 终态语义：失效后追加 switched_current 事件也不复活（不按「最新事件」推断生命周期）
        c7 = sqlite3.connect(str(db7))
        c7.execute("INSERT INTO topic_event (event_id, pack_id, event_type, event_at) "
                   "VALUES (?,?,?,?)",
                   ("e-manual", p_life7.pack_id, "switched_current", "2026-01-01T00:00:00Z"))
        c7.commit()
        c7.close()
        check(Store.get_current_pack(p_life7.identity()) is None,
              "N15 失效后追加 switched_current 事件仍终态失效（terminal 语义，不复活）")

    # --- 门禁 4：migration 最终复核纳入同一原子事务，复核失败 → ROLLBACK 无残留 schema ---
    orig_verify = Store._verify_structure_matches_latest

    def _fail_verify(conn):
        raise RuntimeError("injected final structure review failure")

    with tempfile.TemporaryDirectory() as tdG4:
        struct_db = Path(tdG4) / "struct.db"
        Store._verify_structure_matches_latest = _fail_verify
        try:
            Store.init_topic_store(struct_db)
            check(False, "N16 最终结构复核失败应使 init 失败")
        except RuntimeError:
            check(True, "N16 最终结构复核失败 → RuntimeError")
        finally:
            Store._verify_structure_matches_latest = orig_verify
        c8 = sqlite3.connect(str(struct_db))
        tables8 = {r[0] for r in c8.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c8.close()
        check("topic_pack" not in tables8 and "topic_schema_migrations" not in tables8,
              "N16 复核失败回滚后无残留 schema（复核纳入同一事务）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
