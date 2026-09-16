"""Phase 4 发布态报告收口专项评测（sections/publishable_report.py）。

纯离线：不调 LLM（llm_editor 仅注入假 client）、不联网、不写真实 Store / 上游 DB、
不重跑研究。覆盖核心纯函数 + 10 项定点修订的验收场景。

用法: python -m evals.test_section_publishable_report
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import schema as CS  # noqa: E402
from harness.schema import CitationRef  # noqa: E402
from sections import citation_authority as CA  # noqa: E402
from sections import publishable_report as PR  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import service as SV  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


FROZEN_SNAP = "snap-AAA"


class _FakeAuth:
    """注入式假权威校验器（不读库）。"""

    def __init__(self, *, structured=True, evidence=True, external=True):
        self.structured = structured
        self.evidence = evidence
        self.external = external

    def validate(self, ref):
        if ref.ref_type == "structured":
            return CA.CitationVerdict("structured", self.structured,
                                      None if self.structured else "snapshot_not_found")
        if ref.ref_type == "evidence":
            return CA.CitationVerdict("evidence", self.evidence,
                                      None if self.evidence else "evidence_not_found")
        if ref.ref_type == "external":
            return CA.CitationVerdict("external", self.external,
                                      None if self.external else "external_snapshot_not_found")
        return CA.CitationVerdict(ref.ref_type or "?", False, "unknown_ref_type")


ALL_KINDS = frozenset({"structured", "evidence", "external"})


def _claim(cid, ref_type, *, snapshot=None, formula=None, item=None, period=None,
           evidence=None, page=None, claim_type="fact", derived=()):
    refs = []
    if ref_type == "structured":
        refs.append(CitationRef(ref_type="structured", snapshot_id=snapshot,
                                formula_id=formula, item_code=item,
                                formula_version=("1.0" if formula else None), period=period))
    elif ref_type == "evidence":
        refs.append(CitationRef(ref_type="evidence", evidence_id=evidence, page_number=page))
    elif ref_type == "none":
        refs = []
    return SS.SectionClaim(
        claim_id=cid, section_id="financial", topic_id="t1", question_ids=("q1",),
        text="断言正文", claim_type=claim_type, citation_refs=tuple(refs),
        derived_from_claim_ids=derived)


def _unresolved(uid, state="NOT_PROVIDED", impact=(), blocking=()):
    return SS.SectionUnresolved(
        unresolved_id=uid, section_id="financial", topic_id="t1", question_id="q1",
        state=state, reason_code="x", detail=uid, impact_scope=impact,
        blocking_effects=blocking)


def _outcome(status="COMPLETED", decision="PASS", rules_passed=True, *,
             claims=(), unresolved=(), issues=()):
    sr = SS.SectionResult(section_result_id="sr1", section_version="sv1", task_id="t1",
                          section_id="financial", status=status, claims=claims,
                          unresolved=unresolved)
    ev = SS.SectionEvaluation(evaluation_id="e1", section_result_id="sr1", rules_version="rv",
                              evaluator_prompt_version="pv", rules_passed=rules_passed,
                              llm_passed=True, decision=decision, issues=issues)
    return SV.SectionOutcome(section_id="financial", task_id="t1", title="财务分析",
                             section_result=sr, evaluation=ev, rework_run=None,
                             final_rules_passed=rules_passed, error=None)


def _make_run(company_id="300750", company_name="测试公司", snap=FROZEN_SNAP,
              sections=()):
    manifest = SS.SectionRunManifest(
        manifest_id="manifest_" + "1" * 24, job_id="job1", run_id="run_test",
        code_fingerprint="c" * 64, phase3_closure_fingerprint="p" * 64,
        batch_versions={"service": "p4-service-v1"},
        frozen={"company_id": company_id, "company_name": company_name,
                "report_as_of": "2026-03-31", "financial_snapshot_id": snap,
                "scope": "consolidated", "currency": "CNY", "purpose": "credit_analysis",
                "model_id": "deepseek-v4-pro"},
        created_at="2026-09-11T00:00:00Z")
    return SV.Phase4RunResult(job_id="job1", run_id="run_test", plan_id="plan_1",
                              manifest_id=manifest.manifest_id, manifest=manifest,
                              sections=sections, success=True)


def _contracts():
    return [CS.SectionContract(
        contract_version="v1", section_id="financial", title="财务分析", purpose="",
        required_topics=[CS.TopicContract(topic_id="t1", title="偿债能力", required=True,
                                          key_questions=[])])]


_PLAN_META = {"plan_id": "plan_1", "company_id": "300750", "company_name": "测试公司",
              "report_as_of": "2026-03-31"}


def main():
    # ── 1. structured 快照锁 + 权威通过 → 入选（正向证明） ──
    d = PR.classify_claim(
        _claim("c1", "structured", snapshot=FROZEN_SNAP, formula="SOLV_CURRENT_RATIO",
               period="2025-12-31", claim_type="calculation"),
        reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
        authority=_FakeAuth(), authority_kinds=ALL_KINDS)
    check(d.adopted and d.support == "structured_snapshot_lock",
          "structured 快照锁 + 权威通过 → 入选")

    # ── 2. 快照未锁定 → 排除 snapshot_not_locked ──
    d = PR.classify_claim(
        _claim("c2", "structured", snapshot="snap-OTHER", formula="SOLV_CURRENT_RATIO",
               period="2025-12-31"),
        reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
        authority=_FakeAuth(), authority_kinds=ALL_KINDS)
    check((not d.adopted) and d.reason == "snapshot_not_locked", "snapshot_not_locked 排除")

    # ── 3. 无引用 → 排除 no_citation ──
    d = PR.classify_claim(_claim("c3", "none"), reject_rule_ids={},
                          frozen_snapshot_id=FROZEN_SNAP, authority=_FakeAuth(),
                          authority_kinds=ALL_KINDS)
    check((not d.adopted) and d.reason == "no_citation", "no_citation 排除")

    # ── 4. 评估器 claim 级拒绝 → 排除（正向拒绝信号） ──
    d = PR.classify_claim(_claim("c4", "structured", snapshot=FROZEN_SNAP,
                                 formula="SOLV_CURRENT_RATIO", period="2025-12-31"),
                          reject_rule_ids={"c4": "absence_contradicts_gap"},
                          frozen_snapshot_id=FROZEN_SNAP, authority=_FakeAuth(),
                          authority_kinds=ALL_KINDS)
    check((not d.adopted) and d.reason == "absence_contradicts_gap",
          "评估器 claim 级拒绝 → 排除（rule_id）")

    # ── 5. evidence 无权威 → support_status_unavailable（不猜） ──
    d = PR.classify_claim(_claim("c5", "evidence", evidence="e1", page=3),
                          reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
                          authority=None, authority_kinds=frozenset())
    check((not d.adopted) and d.reason == "support_status_unavailable",
          "evidence 无权威 → support_status_unavailable 排除")

    # ── 6. evidence 权威失败 → 排除 ──
    d = PR.classify_claim(_claim("c6", "evidence", evidence="e1", page=3),
                          reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
                          authority=_FakeAuth(evidence=False), authority_kinds=ALL_KINDS)
    check((not d.adopted) and d.reason == "evidence_not_found",
          "evidence 权威失败 → 排除（reason 透传）")

    # ── 7. evidence 权威通过 → 入选 ──
    d = PR.classify_claim(_claim("c7", "evidence", evidence="e1", page=3),
                          reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
                          authority=_FakeAuth(evidence=True), authority_kinds=ALL_KINDS)
    check(d.adopted and d.support == "evidence_authority_verified",
          "evidence 权威通过 → 入选")

    # ── 8. 派生链：inference 无直接引用，派生链落到已入选基础 Claim → 入选 ──
    base = _claim("base1", "structured", snapshot=FROZEN_SNAP, formula="SOLV_DEBT_RATIO",
                  period="2025-12-31", claim_type="calculation")
    dbase = PR.classify_claim(base, reject_rule_ids={}, frozen_snapshot_id=FROZEN_SNAP,
                              authority=_FakeAuth(), authority_kinds=ALL_KINDS)
    check(dbase.adopted, "基础 Claim 入选")
    ok, reason = PR._derived_chain_ok(("base1",), {"base1"}, {"base1": base})
    check(ok and reason == "", "派生链终止于已入选基础 Claim")

    # ── 9. derive_publication_status：干净 → READY ──
    st, rs = PR.derive_publication_status(_outcome(), unresolved=(), excluded=(),
                                          has_potential_conflict=False)
    check(st == PR.STATUS_READY and rs == (), "干净章节 → READY")

    # ── 10. WAITING_HUMAN 缺口 → BLOCKED ──
    st, rs = PR.derive_publication_status(
        _outcome(status="WAITING_HUMAN"), unresolved=(), excluded=(),
        has_potential_conflict=False)
    check(st == PR.STATUS_BLOCKED and any("WAITING_HUMAN" in r for r in rs),
          "WAITING_HUMAN → BLOCKED")

    # ── 11. 核心影响缺口（solvency）→ BLOCKED ──
    st, rs = PR.derive_publication_status(
        _outcome(), unresolved=(_unresolved("u1", impact=("solvency",)),), excluded=(),
        has_potential_conflict=False)
    check(st == PR.STATUS_BLOCKED and any("solvency" in r or "core_impact" in r for r in rs),
          "solvency 核心影响缺口 → BLOCKED")

    # ── 12. split_unresolved 优先级 + ≤5 限制 ──
    g = [
        _unresolved("other1", state="NOT_FOUND_AFTER_SEARCH"),
        _unresolved("core1", impact=("solvency",)),
        _unresolved("wait1", state="WAITING_HUMAN"),
        _unresolved("block1", blocking=("JOB_BLOCKED",)),
        _unresolved("other2", state="NOT_PROVIDED"),
        _unresolved("other3", state="NOT_PROVIDED"),
    ]
    key, rest = PR.split_unresolved(tuple(g))
    check([u.unresolved_id for u in key] == ["wait1", "block1", "core1", "other1", "other2"],
          "缺口优先级：WAITING_HUMAN > blocking > core > 其他")
    check(len(key) == 5 and len(rest) == 1, "关键缺口 ≤5，其余进审计附录")

    # ── 13. 缺口不因部分 claim 整体删除（修订#3） ──
    key2, _ = PR.split_unresolved((_unresolved("gap1", state="NOT_FOUND_AFTER_SEARCH"),))
    check(len(key2) == 1, "NOT_FOUND_AFTER_SEARCH 缺口不被整体删除（保守保留）")

    # ── 14. formula_period_comparability 公式语义映射（修订#8） ──
    check(PR.formula_period_comparability("SOLV_CURRENT_RATIO") == "point_in_time",
          "end → 时点可比")
    check(PR.formula_period_comparability("EBITDA") == "ytd_flow_amount",
          "flow+yuan → 年累计流量（不可直接比）")
    check(PR.formula_period_comparability("PROF_NET_MARGIN") == "same_basis_flow_ratio",
          "flow+percent → 同口径流量比率")
    check(PR.formula_period_comparability("PROF_ROE") == "flow_stock_ratio",
          "flow/end → 流量/存量（不可直接比）")
    check(PR.formula_period_comparability("GROWTH_REVENUE") == "annual_growth",
          "yoy_flow → 年度增长")

    # ── 15. claim_period_note 跨年度/季度不可比提示 ──
    cross = SS.SectionClaim(
        claim_id="cross1", section_id="financial", topic_id="t1", question_ids=("q1",),
        text="x", claim_type="calculation",
        citation_refs=(CitationRef(ref_type="structured", snapshot_id=FROZEN_SNAP,
                                   formula_id="PROF_ROE", formula_version="1.0",
                                   period="2025-12-31"),
                       CitationRef(ref_type="structured", snapshot_id=FROZEN_SNAP,
                                   formula_id="PROF_ROE", formula_version="1.0",
                                   period="2026-03-31")))
    note = PR.claim_period_note(cross)
    check(note is not None and "PROF_ROE" in note and "口径不可直接比较" in note,
          "跨年度/季度 flow/stock 指标 → 口径不可直接比较提示")

    # ── 16. render_citation 结构化 ──
    cite = PR.render_citation(
        CitationRef(ref_type="structured", snapshot_id="snap-1234567890ab",
                    formula_id="SOLV_CURRENT_RATIO", period="2025-12-31"), doc_names={})
    check("snap-1234567" in cite and "SOLV_CURRENT_RATIO" in cite and "2025-12-31" in cite,
          "结构化引用渲染")

    # ── 17. render_citation evidence 命名 + 中性回退（修订#9，不猜年报） ──
    named = PR.render_citation(CitationRef(ref_type="evidence", evidence_id="e9", page_number=3),
                               doc_names={"e9": "NDSD_2024_year.pdf（年度报告）"})
    check(named == "NDSD_2024_year.pdf（年度报告） · 第3页", "evidence 命名引用")
    neutral = PR.render_citation(CitationRef(ref_type="evidence", evidence_id="missing",
                                             page_number=7), doc_names={})
    check(neutral == "本地披露材料 · PDF物理页 7", "evidence 缺失 → 中性回退（不猜年报）")
    other_only = PR.render_citation(CitationRef(ref_type="evidence", evidence_id="eX",
                                                page_number=2),
                                    doc_names={"eX": "NDSD_2025_year.pdf"})
    check("年度报告" not in other_only, "source_type=other 不附加「年度报告」标签")

    # ── 18. detect_cross_section_conflicts 精确冲突（修订#7a） ──
    a = SS.SectionClaim(claim_id="a1", section_id="financial", topic_id="t1",
                        question_ids=("q1",), text="x", claim_type="calculation",
                        citation_refs=(CitationRef(ref_type="structured", snapshot_id="snap-A",
                                                   formula_id="SOLV_DEBT_RATIO",
                                                   formula_version="1.0", period="2025-12-31"),))
    b = SS.SectionClaim(claim_id="b1", section_id="company", topic_id="t1",
                        question_ids=("q1",), text="y", claim_type="fact",
                        citation_refs=(CitationRef(ref_type="structured", snapshot_id="snap-B",
                                                   formula_id="SOLV_DEBT_RATIO",
                                                   formula_version="1.0", period="2025-12-31"),))
    conflicts, note = PR.detect_cross_section_conflicts({"financial": (a,), "company": (b,)})
    check(len(conflicts) == 1 and conflicts[0].claim_ids == ("a1", "b1"),
          "同 formula+period 跨章不同快照 → 精确冲突")
    check("潜在冲突" in note and "需人工复核" in note,
          "潜在冲突诚实标注（不宣称完整覆盖）")

    # ── 19. deterministic_editor 按 topic 合并 + marker（修订#5） ──
    body, markers = PR.deterministic_editor(
        (_claim("c1", "structured", snapshot=FROZEN_SNAP, formula="SOLV_CURRENT_RATIO",
                period="2025-12-31", claim_type="calculation"),),
        [("t1", "偿债能力")], lambda r: "财务快照 · SOLV_CURRENT_RATIO")
    check("<!-- claim:c1 -->" in body, "确定性编辑器输出 claim marker")
    check(markers == ("c1",), "marker 顺序与入选一致")
    check("### 偿债能力" in body and "引用来源：" in body, "按 topic 合并 + 引用来源")
    check("[事实]" not in body, "非 [事实] 一行一条的 dev 列表")

    # ── 20. llm_editor marker 不一致 → 回退确定性（修订#5） ──
    import llm.client as LLC
    orig_cwu, orig_lp = LLC.chat_with_usage, LLC.load_prompt
    try:
        class _Resp:
            text = "被改坏的正文，marker 丢失"
            model = "fake"
            call_id = "call1"
            input_tokens = 1
            output_tokens = 1
        LLC.load_prompt = lambda name: "system prompt"
        LLC.chat_with_usage = lambda *a, **k: _Resp()
        out, audit = PR.llm_editor(
            (_claim("c1", "structured", snapshot=FROZEN_SNAP, formula="SOLV_CURRENT_RATIO",
                    period="2025-12-31", claim_type="calculation"),),
            [("t1", "偿债能力")], render_fn=lambda r: "src", company_id="300750")
        check(audit["used"] is True and audit.get("fallback_reason") is not None
              and "marker_mismatch" in audit["fallback_reason"],
              "LLM marker 不一致 → 审计回退原因")
        check("<!-- claim:c1 -->" in out, "回退后仍含确定性 marker")
    finally:
        LLC.chat_with_usage, LLC.load_prompt = orig_cwu, orig_lp

    # ── 21. build_publication 端到端 + publication_id 确定性（修订#4） ──
    out_sec = _outcome(claims=(_claim("c1", "structured", snapshot=FROZEN_SNAP,
                                      formula="SOLV_CURRENT_RATIO", period="2025-12-31",
                                      claim_type="calculation"),),
                       unresolved=(_unresolved("u1", impact=("solvency",)),))
    run = _make_run(sections=(out_sec,))
    pub = PR.build_publication(run, plan_meta=_PLAN_META, authority=_FakeAuth(),
                               authority_kinds=ALL_KINDS, contracts=_contracts(),
                               use_editor=False, ev_db=None)
    check(pub.publication_id.startswith("pub_"), "publication_id 内容寻址")
    check(len(pub.sections) == 1 and pub.sections[0].adopted_claims
          and pub.sections[0].publication_status == PR.STATUS_BLOCKED,
          "端到端：入选 + solvency 缺口 → BLOCKED")
    pub2 = PR.build_publication(run, plan_meta=_PLAN_META, authority=_FakeAuth(),
                                authority_kinds=ALL_KINDS, contracts=_contracts(),
                                use_editor=False, ev_db=None)
    check(pub.publication_id == pub2.publication_id, "同输入 → 同 publication_id（幂等）")
    pub3 = PR.build_publication(run, plan_meta={**_PLAN_META, "company_name": "另一公司"},
                                authority=_FakeAuth(), authority_kinds=ALL_KINDS,
                                contracts=_contracts(), use_editor=False, ev_db=None)
    check(pub.publication_id != pub3.publication_id,
          "依赖（company_name）变化 → 新 publication_id")

    # ── 22. compute_publication_id 确定性 ──
    check(PR.compute_publication_id({"a": 1, "b": [1, 2]})
          == PR.compute_publication_id({"b": [1, 2], "a": 1}),
          "compute_publication_id 键序无关（sort_keys）")

    # ── 23. 原子发布：不覆盖已有目录 + reused + 冲突 fail-closed（修订#4） ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = PR.write_publication(pub, out_root=root)
        check((d / "report.md").is_file() and (d / "publication.json").is_file(),
              "原子发布写入 report.md + publication.json")
        check(not list(root.glob(".*.tmp-*")), "发布成功后无残留临时目录")
        try:
            PR.write_publication(pub, out_root=root)
            check(False, "覆盖已有目录应 fail-closed")
        except PR.PublicationError:
            check(True, "覆盖已有目录 fail-closed")
        reused = PR._publish(pub, out_root=root)
        check(reused.reused is True, "同 id 同字段 → reused=true")
        try:
            PR._publish(pub3, out_root=root)
            check(False, "同目录不同 id 应 fail-closed 冲突")
        except PR.PublicationError:
            check(True, "同目录不同 id → fail-closed 冲突")
        loaded = PR.load_publication(root, pub.run_id)
        check(loaded.publication_id == pub.publication_id, "load_publication 还原身份")

    # ── 24. 原子发布失败不留半成品（修订#10） ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        import sections.publishable_report as _PR
        orig_replace = _PR.os.replace
        def _boom_replace(src, dst):
            raise OSError("simulated atomic-move failure")
        _PR.os.replace = _boom_replace
        try:
            try:
                PR.write_publication(pub, out_root=root)
                check(False, "os.replace 失败应抛出")
            except OSError:
                check(True, "os.replace 失败抛出")
            check(not list(root.iterdir()), "失败后不留半成品（无 out_dir、无临时目录）")
        finally:
            _PR.os.replace = orig_replace

    # ── 25. 渲染 markdown：BLOCKED 章显著标注 + 关键缺口 + 另有 N 项 ──
    md = PR.render_report_markdown(pub)
    check("不可正式出具" in md and "草稿" in md, "BLOCKED 章显著标注「草稿／不可正式出具」")
    check("报告编号" in md and pub.publication_id in md, "报告编号进入 markdown")
    check("审计附录" in md and "潜在冲突" in md, "审计附录含跨章节与潜在冲突说明")

    # ── 26. 确定性编辑器：editor_audit 按章节持久化，model 不绑定（修订#4/#5） ──
    check(pub.editor_audit.get("financial") == {"editor": "deterministic", "used": False},
          "确定性编辑器审计按章节持久化")
    check(pub.fingerprint.get("llm_editor_model") is None,
          "未启用编辑器 → fingerprint 不绑定 model")

    # ── 27. 受限 LLM 编辑：model 绑定指纹 + 审计持久化（修订#4/#5） ──
    import llm.client as LLC
    orig_cwu, orig_lp = LLC.chat_with_usage, LLC.load_prompt
    try:
        class _Resp2:
            text = "正文<!-- claim:c1 -->"
            model = "fake-editor-model"
            call_id = "call_editor"
            input_tokens = 7
            output_tokens = 8
        LLC.load_prompt = lambda name: "system prompt"
        LLC.chat_with_usage = lambda *a, **k: _Resp2()
        pub_e = PR.build_publication(run, plan_meta=_PLAN_META, authority=_FakeAuth(),
                                     authority_kinds=ALL_KINDS, contracts=_contracts(),
                                     use_editor=True, ev_db=None)
        check(pub_e.fingerprint.get("llm_editor_model") == ["fake-editor-model"],
              "编辑 model 绑定进 fingerprint（修订#4）")
        check(pub_e.editor_audit.get("financial", {}).get("used") is True
              and pub_e.editor_audit["financial"].get("model") == "fake-editor-model"
              and pub_e.editor_audit["financial"].get("call_id") == "call_editor",
              "编辑审计 call_id/model 持久化（修订#5）")
        check(pub_e.publication_id != pub.publication_id, "启用编辑器 → 新 publication_id")
    finally:
        LLC.chat_with_usage, LLC.load_prompt = orig_cwu, orig_lp

    # ── 28. 跨运行幂等：created_at 不同但内容相同 → reused=true（修订#4） ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        PR.write_publication(pub, out_root=root)
        orig_utcnow = PR._utcnow
        PR._utcnow = lambda: "2099-01-01T00:00:00Z"
        try:
            later = PR.build_publication(run, plan_meta=_PLAN_META, authority=_FakeAuth(),
                                         authority_kinds=ALL_KINDS, contracts=_contracts(),
                                         use_editor=False, ev_db=None)
        finally:
            PR._utcnow = orig_utcnow
        check(later.publication_id == pub.publication_id
              and later.created_at != pub.created_at,
              "同内容不同 created_at → publication_id 不变")
        check(PR._publish(later, out_root=root).reused is True,
              "跨运行（created_at 不同）→ reused=true，不误报冲突")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
