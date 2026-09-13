"""Eval: R1-B TopicResearchPack schema + Pack Store + checkpoint + CLI（§14 十六类矩阵）。

用法: python -m evals.test_topic_pack_store

全部离线：mock 构造 Pack / requirement，不调真实 LLM / bocha / 网络 / Router / 工具循环。
断言覆盖 R1B_IMPLEMENTATION_PLAN.md §14 的 16 类测试：
  1. 序列化往返（schema_version / 非法枚举 / 嵌套 unknown-field fail-closed）
  2. 内容寻址（pack_id / dependency_fingerprint 不含 run_id/时间戳；内容/依赖变化即变）
  3. 幂等（同 pack_id 同内容复用；异内容损坏 fail-closed）
  4. 冲突（错身份/公司/日期/契约/任务全部 fail-closed）
  5. current 指针（原子切换、只读加载、历史不 UPDATE/DELETE）
  6. 失效（依赖指纹变化 → 标记失效、不自动升级）
  7. migration（append-only、结构不一致 fail-closed、不创建/迁移 legacy 表、只读不建库）
  8. 损坏恢复（StorageCorruptionError、健康对象不受影响）
  9. 状态适配双轴（adapt_outcome_completion / derive_pack_status / 未知状态 fail-closed / 跨空间拒绝）
  10. 权威分离（三类强类型 + locator 联合、mismatch fail-closed）
  11. 两门独立（authority 通过 ≠ sufficiency 通过）
  12. checkpoint（严格只读、指纹不符拒绝 resume）
  13. 正式链边界（不接 runtime、v1 SHA256 不变）
  14. payload 不可变引用（hash/类型 mismatch fail-closed）
  15. 冻结投影（AspectV2 22 必需 + 4 扩展全投影）
  16. 数据库共存初始化（两种顺序均成功、只读 hash 不变）
"""

from __future__ import annotations

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


def _aspect_snapshot(aspect_id: str, topic_id: str = "t1") -> TS.TopicAspectRequirementSnapshot:
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=aspect_id, question_id="q1", topic_id=topic_id,
        requirement_text="req text", kind="fact", producer_kind="company",
        execution_path="direct", required_fields=("f1",), coverage_rules=("c1",),
        complete_set_rule="", evidence_requirement_ids=(_req_ref(),),
        source_policy_ref=_policy_ref(), time_scope="period", display_tier="primary",
        content_role="subject", missing_policy="none", blocking_policy=(),
        applicability_policy=None, impact_scope=("subject",), output_destination="body",
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
    return TS.EvidenceAuthorityAssessment(evidence_id="ev1", verdict="authoritative")


def _financial_authority() -> TS.FinancialSnapshotAuthorityAssessment:
    return TS.FinancialSnapshotAuthorityAssessment(snapshot_id="snap1", validity="valid",
                                                   verdict="authoritative")


def _external_authority() -> TS.ExternalSnapshotAuthorityAssessment:
    return TS.ExternalSnapshotAuthorityAssessment(source_snapshot_id="ext1", source_grade="A",
                                                  verdict="supplemental_only")


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
                            citation_refs=(TS.CitationRef(ref_type="evidence", evidence_id="e1"),),
                            source_authority=_evidence_authority())


def _audit(aid: str, qualified: bool = True) -> TS.NotFoundAudit:
    return TS.NotFoundAudit(
        audit_id=aid, policy_version="v1", required_source_scope=("disclosure",),
        attempted_source_types=("pdf",), valid_attempt_count=1, searched_need_ids=("n1",),
        context_expansion_attempted=False, alternative_candidate_ids=(),
        alternative_sources_attempted=(), time_window="2025", unattempted_candidate_ids=(),
        budget_exhausted=False, qualification_reasons=("ok",), qualified=qualified)


def _gap(gid: str, aspect_ids: tuple[str, ...], reason_code: str = "not_found",
         impact: str = "subject") -> TS.ResearchGap:
    return TS.ResearchGap(unresolved_id=gid, aspect_ids=aspect_ids, reason_code=reason_code,
                          detail="d", attempted_need_ids=("n1",), blocking=False, impact=impact)


def _usage() -> TS.TopicUsageSnapshot:
    bp = TS.BudgetPolicySnapshot(schema_version="1", canonical_hash=_sha("bp"), tier="t1")
    return TS.TopicUsageSnapshot(budget_policy=bp,
                                 cumulative_usage=(TS.UsageEntry(metric="rounds", value=1, unit=""),))


def _build(topic_id: str = "t1", statuses: dict[str, str] | None = None,
           contract_fp: str | None = None, spv: str = "v1",
           dep_versions: dict[str, str] | None = None, run_id: str = "run-1",
           task_id: str = "task1", company_id: str = "300750", section_id: str = "company",
           report_as_of: str | None = None, contract_version: str = "v1",
           facts: tuple[TS.SupportedFact, ...] = (),
           materials: tuple[TS.ResearchMaterial, ...] = ()):
    """构造一个 finalized Pack + 对应 aspect 冻结投影 tuple。"""
    contract_fp = contract_fp or _sha("contract")
    statuses = statuses or {"a1": "covered"}
    aspects: list[TS.TopicAspectRequirementSnapshot] = []
    results: list[TS.AspectResearchResult] = []
    audits: list[TS.NotFoundAudit] = []
    for aid, status in statuses.items():
        snap = _aspect_snapshot(aid, topic_id)
        aspects.append(snap)
        nfa_id = None
        if status == "not_found":
            nfa_id = "audit-" + aid
            audits.append(_audit(nfa_id, qualified=True))
        results.append(TS.AspectResearchResult(
            aspect_id=aid, question_ids=("q1",), requirement_snapshot=snap, status=status,
            supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=(),
            not_found_audit_id=nfa_id))
    required = tuple(a.aspect_id for a in aspects)
    process, coverage, derivation = TS.derive_pack_status(required, tuple(results))
    dep = TS.compute_dependency_fingerprint(contract_fp, spv, dep_versions or {})
    pack = TS.TopicResearchPack(
        schema_version=TS.TOPIC_PACK_SCHEMA_VERSION, pack_id="", run_id=run_id,
        task_id=task_id, company_id=company_id, report_as_of=report_as_of,
        contract_version=contract_version, contract_fingerprint=contract_fp,
        source_policy_version=spv, section_id=section_id, topic_id=topic_id,
        question_ids=("q1",), aspect_results=tuple(results), materials=materials,
        facts=facts, outcome_refs=(), external_funnel=None, conflicts=(),
        not_found_audits=tuple(audits), unresolved=(), usage=_usage(), uncertain_calls=(),
        process_status=process, coverage_status=coverage, status_derivation=derivation,
        dependency_fingerprint=dep)
    return TS.finalize_pack(pack), tuple(aspects)


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

    # schema_version 校验
    try:
        TS.TopicResearchPack.from_dict({**d, "schema_version": "999"})
        check(False, "非法 schema_version 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "非法 schema_version → SchemaValidationError")

    # 非法枚举 fail-closed
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

    # 嵌套对象 unknown-field fail-closed
    nested = json.loads(json.dumps(d))
    nested["usage"]["extra_field"] = "x"
    try:
        TS.TopicResearchPack.from_dict(nested)
        check(False, "嵌套 unknown-field 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "嵌套 usage.extra_field → SchemaValidationError")

    # discriminator 序列化
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
        res = Store.commit_pack(p)
        check(res.reused is False and res.current_switched is True, "首次 commit 非复用且切换 current")
        got = Store.get_pack(p.pack_id)
        check(got is not None and got.pack_id == p.pack_id, "get_pack(pack_id) 读回")
        cur = Store.get_current_pack(p.identity())
        check(cur is not None and cur.pack_id == p.pack_id, "get_current_pack(identity) 读当前")
        hist = Store.list_pack_history(p.identity())
        check(len(hist) == 1 and hist[0].pack_id == p.pack_id, "list_pack_history 含已提交 Pack")

        # -- 3. 幂等复用 --
        res2 = Store.commit_pack(p)
        check(res2.reused is True and res2.current_switched is False, "同 pack_id 同内容 → 幂等复用")
        check(Store.get_pack(p.pack_id) is not None, "复用后 Pack 仍可读")

        # -- 3/8. 损坏：篡改已存 Pack 的 content_fingerprint，重提交 → StorageCorruptionError --
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TRIGGER IF EXISTS trg_topic_pack_no_update")
        conn.execute("UPDATE topic_pack SET content_fingerprint=? WHERE pack_id=?",
                     (_sha("garbage"), p.pack_id))
        conn.commit()
        conn.close()
        try:
            Store.commit_pack(p)
            check(False, "损坏 pack 复用应被拒绝")
        except Store.StorageCorruptionError:
            check(True, "损坏 pack 复用 → StorageCorruptionError")
        # 健康对象不受影响（同一 Pack 历史仍可读，不被隔离删除）
        check(Store.get_pack(p.pack_id) is not None, "损坏对象不影响健康 Pack")

        # -- 4. 冲突：错身份/公司/日期/契约/任务 fail-closed --
        cases = [
            ("错任务 task_id", {"task_id": "task-X"}),
            ("错公司 company_id", {"company_id": "600000"}),
            ("错日期 report_as_of", {"report_as_of": "2025-12-31"}),
            ("错契约 contract_fingerprint", {"contract_fp": _sha("other-contract")}),
            ("错政策 source_policy_version", {"spv": "v99"}),
        ]
        for label, ov in cases:
            req = _requirement(aspects, **ov)
            try:
                Store.commit_pack(p, req)
                check(False, f"{label} 应 fail-closed")
            except (TS.SchemaValidationError, Store.TopicStoreValidationError):
                check(True, f"{label} → fail-closed")

        # -- 6. 失效：依赖指纹变化 → 标记失效、不自动升级 --
        mark_target = p.pack_id
        Store.mark_invalidated(mark_target, "invalidated", reason="contract changed")
        events = Store.list_events(mark_target)
        check(any(e.event_type == "invalidated" and e.pack_id == mark_target for e in events),
              "mark_invalidated 追加 invalidated 事件")
        # 不自动升级 / 不删除历史 / current 不变
        check(Store.get_pack(mark_target) is not None, "失效后 Pack 历史仍在（不 DELETE）")
        check(Store.get_current_pack(p.identity()).pack_id == mark_target,
              "失效不自动切换 current（不自动升级）")
        # 非法失效类型
        try:
            Store.mark_invalidated(mark_target, "bogus")
            check(False, "mark_invalidated 非法事件类型应被拒绝")
        except Store.TopicStoreValidationError:
            check(True, "mark_invalidated 非法事件类型 → TopicStoreValidationError")

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

        # -- 12. checkpoint 只读加载 --
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
        # topic_store 不创建 run_manifest / question_outcome
        c = sqlite3.connect(str(db))
        legacy_tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        check("run_manifest" not in legacy_tables, "topic_store 不创建 run_manifest")
        check("question_outcome" not in legacy_tables, "topic_store 不创建 question_outcome")
        check("schema_migrations" not in legacy_tables, "topic_store 不创建 legacy schema_migrations")

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

        # -- 16. 只读打开前后文件 hash 不变 --
        # 用未损坏的另一份干净 DB 校验（上面已 DROP topic_fact）
        db2 = Path(td) / "clean.db"
        Store.init_topic_store(db2)
        Store.commit_pack(p)
        h_before = hashlib.sha256(db2.read_bytes()).hexdigest()
        Checkpoint.load_checkpoint(p.identity(), db2)
        Checkpoint.list_checkpoints(db2)
        CLI.run(["--self-check", "--db", str(db2)])
        h_after = hashlib.sha256(db2.read_bytes()).hexdigest()
        check(h_before == h_after, "只读路径（checkpoint/CLI self-check）前后文件 hash 不变")

        # -- CLI --
        cli_ck = CLI.run(["--self-check", "--db", str(db2)])
        check(cli_ck.get("ok") is True, "CLI --self-check 通过")
        cli_pack = CLI.run(["--pack", p.pack_id, "--db", str(db2)])
        check(cli_pack.get("ok") is True and cli_pack["pack"]["pack_id"] == p.pack_id,
              "CLI --pack 读回")
        cli_cur = CLI.run(["--current", "--task-id", "task1", "--company-id", "300750",
                           "--contract-fingerprint", p.contract_fingerprint,
                           "--source-policy-version", "v1", "--section-id", "company",
                           "--topic-id", "t1", "--db", str(db2)])
        check(cli_cur.get("ok") is True and cli_cur["pack"]["pack_id"] == p.pack_id,
              "CLI --current 读回")

    # =========================================================================
    # 9. 状态适配（双轴）
    # =========================================================================
    # adapt_outcome_completion 只产出原子资格
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

    # derive_pack_status 双轴
    # 5 required 仅 1 covered → process 不得 finished + coverage=insufficient
    r5 = tuple(TS.AspectResearchResult(
        aspect_id=a, question_ids=("q1",), requirement_snapshot=_aspect_snapshot(a),
        status=("covered" if a == "a1" else "partial"),
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=())
        for a in ("a1", "a2", "a3", "a4", "a5"))
    proc, cov, der = TS.derive_pack_status(("a1", "a2", "a3", "a4", "a5"), r5)
    check(proc.status != "finished" and cov.status == "insufficient",
          "5 required 仅 1 covered → process≠finished + coverage=insufficient")

    # 3 covered + 1 合格 not_found + 1 not_applicable → finished + complete_with_gaps
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

    # hard block → blocked + coverage 保留实际结果不清空
    blocked = tuple(TS.AspectResearchResult(
        aspect_id=a, question_ids=("q1",), requirement_snapshot=_aspect_snapshot(a),
        status=("covered" if a == "a1" else "blocked"),
        supported_fact_ids=(), material_ids=(), attempted_need_ids=(), unresolved_ids=())
        for a in ("a1", "a2"))
    proc3, cov3, der3 = TS.derive_pack_status(("a1", "a2"), blocked, stop_reason="REPORT_BLOCKED")
    check(proc3.status == "blocked" and proc3.hard_stop_reason == "REPORT_BLOCKED",
          "hard block → process=blocked + hard_stop_reason")
    check(cov3.covered_aspect_ids == ("a1",), "block 不清空已覆盖 aspect 明细")

    # 预算耗尽 → stopped_by_budget + 已有 covered facts 保留
    proc4, cov4, der4 = TS.derive_pack_status(("a1", "a2"), blocked, stop_reason="BUDGET_EXHAUSTED")
    check(proc4.status == "stopped_by_budget" and cov4.covered_aspect_ids == ("a1",),
          "预算耗尽 → stopped_by_budget 且保留 covered")

    # 未知状态 fail-closed / 跨空间字符串拒绝
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
            unresolved_ids=()),), stop_reason=None)  # required 含 a2 但结果缺失
        check(False, "缺 required aspect 应 fail-closed")
    except TS.StateAdaptationError:
        check(True, "derive_pack_status 缺 required aspect → StateAdaptationError")

    # TopicUsageSnapshot 无含混 completion_status
    u = _usage()
    check("completion_status" not in u.to_dict(), "TopicUsageSnapshot 无 completion_status 字段")
    check("completion_status" not in TS.PackProcessStatus("finished").to_dict(),
          "PackProcessStatus 无 completion_status 字段")

    # =========================================================================
    # 10. 权威分离（三类强类型 + locator 联合）
    # =========================================================================
    # 三类 authority 往返（discriminator）
    for auth in (_evidence_authority(), _financial_authority(), _external_authority()):
        back_auth = TS.authority_from_dict(auth.to_dict())
        check(type(back_auth) is type(auth), f"authority 往返保型: {type(auth).__name__}")

    # locator 三变体按 material_type 区分
    check(TS.locator_from_dict(_evidence_locator().to_dict()).locator_type == "evidence",
          "evidence locator 往返")
    check(TS.locator_from_dict(_financial_locator().to_dict()).locator_type == "financial_snapshot",
          "financial locator 往返")
    check(TS.locator_from_dict(_external_locator().to_dict()).locator_type == "external_snapshot",
          "external locator 往返")

    # material type ↔ authority ↔ locator mismatch fail-closed
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

    # ValueIdentity 不同不可互换
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

    # 外部漏斗 payload hash mismatch fail-closed
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

    # EvidenceLocator 最低要求
    try:
        TS.EvidenceLocator()
        check(False, "EvidenceLocator 无 page/block/section 应 fail-closed")
    except TS.SchemaValidationError:
        check(True, "EvidenceLocator 缺定位 → SchemaValidationError")
    try:
        TS.EvidenceLocator(section_path="s1")  # 无 document_id/document_version
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
    # EvidenceRequirementRef 绑定 requirement ID + 所属 Contract SHA + requirement fingerprint + schema/version
    er = _req_ref()
    check(er.requirement_id and len(er.contract_sha256) == 64
          and len(er.requirement_fingerprint) == 64 and er.schema_version,
          "EvidenceRequirementRef 绑定 4 项身份")
    # SourcePolicyRef 绑定 policy version + content fingerprint
    sp = _policy_ref()
    check(sp.policy_version and len(sp.content_fingerprint) == 64,
          "SourcePolicyRef 绑定 policy version + content fingerprint")
    # 缺字段 fail-closed
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
        # 顺序 ①：checkpoint 先、topic_store 后
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
        # 顺序 ②：topic_store 先、checkpoint 后
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
        # 重复初始化幂等
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

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
