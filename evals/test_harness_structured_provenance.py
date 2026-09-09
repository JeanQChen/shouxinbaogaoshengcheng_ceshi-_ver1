"""Eval: 结构化结果权威判定 —— Phase 3 Batch B 修订④（Change 2）。

用法: python -m evals.test_harness_structured_provenance

断言（纯逻辑，无 LLM / Store I/O——authority 以假函数注入）：
- SnapshotAuthority.authoritative() 复合门（exists+is_current+valid+not_blocked+not_quarantined）；
- 有效 ref → SUPPORTED；单期答变化 → PARTIAL（唯一 PARTIAL 情形）；
- company / scope / period / value / item-formula 任一不一致 → UNSUPPORTED；
- unresolvable ref → UNSUPPORTED；快照 not_current / stale / blocked / quarantined /
  not_found → UNSUPPORTED；authority 不可用 → validity_query_unavailable；
- ref.snapshot_status 自称 valid 但 Store 权威为 stale → UNSUPPORTED（不自证）；
- 纯结构化 claim 经 exclude_claim_ids 不送 LLM entailment；
- entailment_summary 合并三 evaluator（evidence_deterministic/structured_provenance/
  llm_entailment）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import entailment as E
from harness import schema as H
from harness import structured_provenance as SP
from routing import schema as RS


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="q", section_id="fin", question="2025年净利率是多少？",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _state(company_id="ACME", active="S1", refs=None) -> H.ResearchState:
    state = H.ResearchState(
        run_id="r", case_id="c", question_id="q", company_id=company_id,
        section_id="fin", original_question="2025年净利率是多少？", need=_need())
    state.active_snapshot_id = active
    state.structured_refs = refs or []
    return state


def _ref(snapshot_id="S1", formula_id="NET_MARGIN", item_code=None,
         period="2025-12-31", display="18.12", unit="%",
         status="CALCULATED_EXACT", company_id="ACME", scope="consolidated",
         currency="CNY", purpose="credit_analysis", snapshot_status="valid",
         **extra) -> RS.StructuredResultRef:
    return RS.StructuredResultRef(
        result_type="financial_field" if item_code else "financial_metric",
        snapshot_id=snapshot_id, item_code=item_code, formula_id=formula_id,
        formula_version="v1", period=period, raw_value=display,
        display_value=display, unit=unit, status=status, reason_code=None,
        input_record_refs=[], input_snapshot_item_refs=[],
        company_id=company_id, scope=scope, currency=currency, purpose=purpose,
        snapshot_status=snapshot_status, **extra)


def _answer(claim_id="c1", text="2025年净利率为18.12%", sid="S1",
            formula_id="NET_MARGIN", item_code=None, period="2025-12-31",
            ref_type="structured") -> H.ResearchAnswer:
    return H.ResearchAnswer(
        question_id="q", answer_text=text,
        claims=[H.Claim(claim_id=claim_id, text=text, kind="fact",
                        citation_refs=[0])],
        citations=[H.CitationRef(ref_type=ref_type, snapshot_id=sid,
                                  formula_id=formula_id, item_code=item_code,
                                  formula_version="v1", period=period)])


def _auth(sid_map: dict) -> callable:
    return lambda sid: sid_map.get(sid)


def _valid_auth(sid="S1", **kw) -> callable:
    base = dict(exists=True, is_current=True, validity="valid",
                report_blocked=False, quarantined=False)
    base.update(kw)
    return _auth({sid: SP.SnapshotAuthority(**base)})


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

    def verdict_of(state, answer, authority):
        out = SP.evaluate_structured_provenance(state, answer,
                                                snapshot_authority=authority)
        return out["c1"].verdict, out["c1"].reason

    # ---- SnapshotAuthority.authoritative() 复合门 ----
    check(SP.SnapshotAuthority(True, True, "valid", False, False).authoritative(),
          "authoritative：全真 → True")
    check(not SP.SnapshotAuthority(True, False, "valid", False, False).authoritative(),
          "authoritative：is_current=False → False")
    check(not SP.SnapshotAuthority(True, True, "stale", False, False).authoritative(),
          "authoritative：validity=stale → False")
    check(not SP.SnapshotAuthority(True, True, "valid", True, False).authoritative(),
          "authoritative：report_blocked → False")
    check(not SP.SnapshotAuthority(True, True, "valid", False, True).authoritative(),
          "authoritative：quarantined → False")
    check(not SP.SnapshotAuthority(False, True, "valid", False, False).authoritative(),
          "authoritative：exists=False → False")

    # ---- 有效 ref → SUPPORTED ----
    state = _state(refs=[_ref()])
    v, r = verdict_of(state, _answer(), _valid_auth())
    check(v == "SUPPORTED" and r == "structured_authoritative",
          f"有效 ref → SUPPORTED（实际 {v}/{r}）")

    # ---- 无年份 claim → 跳过期间匹配仍 SUPPORTED ----
    v, r = verdict_of(state, _answer(text="净利率为18.12%"), _valid_auth())
    check(v == "SUPPORTED", f"无年份 claim → 跳过期间匹配 SUPPORTED（实际 {v}）")

    # ---- company 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(company_id="OTHER")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "company_mismatch",
          f"company_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- scope 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(scope="parent")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "scope_mismatch",
          f"scope_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- period 不一致（claim 唯一年份 ≠ ref.period）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(text="2024年净利率为18.12%"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "period_mismatch",
          f"period_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- value 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(display="20.00")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "value_mismatch",
          f"value_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- item_formula 不一致（同 snapshot+period 但 code 不符）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref(formula_id="PROF_GROSS_MARGIN")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "item_formula_mismatch",
          f"item_formula_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- unresolvable ref（period 不在 state.refs）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(period="2024-12-31"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "unresolvable_ref",
          f"unresolvable_ref → UNSUPPORTED（实际 {v}/{r}）")

    # ---- snapshot 转写错（period+code 命中，ref 自身 snapshot 为 current）→ SUPPORTED ----
    # 真实复跑 FIN-CF1：LLM 把 snapshot_id "…eadbde…" 写成 "…deadbe…"。
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(sid="S1_typo"), _valid_auth())
    check(v == "SUPPORTED" and r == "structured_authoritative",
          f"snapshot 转写错仍按 period+code 命中 → SUPPORTED（实际 {v}/{r}）")

    # ---- 快照 not_current → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(),
                      _valid_auth(is_current=False))
    check(v == "UNSUPPORTED" and r == "snapshot_not_current",
          f"snapshot_not_current → UNSUPPORTED（实际 {v}/{r}）")

    # ---- Store 权威 stale（ref.snapshot_status 自称 valid）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref(snapshot_status="valid")])
    v, r = verdict_of(state_bad, _answer(),
                      _valid_auth(validity="stale"))
    check(v == "UNSUPPORTED" and r == "snapshot_stale",
          f"ref 自称 valid 但 Store stale → UNSUPPORTED（实际 {v}/{r}）")

    # ---- validity=valid 但 report_blocked → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(),
                      _valid_auth(report_blocked=True))
    check(v == "UNSUPPORTED" and r == "snapshot_report_blocked",
          f"report_blocked → UNSUPPORTED（实际 {v}/{r}）")

    # ---- validity=valid 但 quarantine → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(),
                      _valid_auth(quarantined=True))
    check(v == "UNSUPPORTED" and r == "snapshot_quarantined",
          f"quarantined → UNSUPPORTED（实际 {v}/{r}）")

    # ---- snapshot 不存在 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(),
                      _auth({"S1": SP.SnapshotAuthority(False, True, None, False, False)}))
    check(v == "UNSUPPORTED" and r == "snapshot_not_found",
          f"snapshot_not_found → UNSUPPORTED（实际 {v}/{r}）")

    # ---- authority 不可用（snapshot_authority=None）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(), None)
    check(v == "UNSUPPORTED" and r == "validity_query_unavailable",
          f"validity_query_unavailable → UNSUPPORTED（实际 {v}/{r}）")

    # ---- metric status 不在可用态 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(status="blocked")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "metric_status_blocked",
          f"metric_status_blocked → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期答变化 → PARTIAL（唯一 PARTIAL 情形）----
    state_trend = _state(refs=[_ref()])
    v, r = verdict_of(state_trend,
                      _answer(text="2025年净利率同比如何变化？18.12%"),
                      _valid_auth())
    check(v == "PARTIAL" and r == "single_period_for_trend",
          f"单期答变化 → PARTIAL（实际 {v}/{r}）")

    # ---- 比较 ref（带 period_a/period_b/direction）答变化 → SUPPORTED ----
    state_cmp = _state(refs=[_ref(period_a="2024-12-31", period_b="2025-12-31",
                                  direction="increased", change_value="1.2")])
    v, r = verdict_of(state_cmp,
                      _answer(text="净利率同比上升1.2个百分点至18.12%"),
                      _valid_auth())
    check(v == "SUPPORTED",
          f"带比较字段的 ref 答变化 → SUPPORTED（实际 {v}/{r}）")

    # ---- ≥3 期趋势序列 → 本模块不判（回退 LLM）----
    state_seq = _state(refs=[_ref()])
    out = SP.evaluate_structured_provenance(
        state_seq, _answer(text="近三年净利率趋势如何？18.12%"),
        snapshot_authority=_valid_auth())
    check("c1" not in out, "近三年趋势序列 → 本模块不判（claim 不在结果中）")

    # ---- 纯结构化 claim 经 exclude_claim_ids 不送 LLM entailment ----
    state_ex = _state(refs=[_ref()])
    ans_ex = _answer()
    pv_in = E.entailment_prompt_vars(state_ex, ans_ex, {}, exclude_claim_ids=frozenset())
    pv_out = E.entailment_prompt_vars(state_ex, ans_ex, {},
                                      exclude_claim_ids=frozenset({"c1"}))
    check("c1" in pv_in["claims"] and "c1" not in pv_out["claims"],
          "exclude_claim_ids：纯结构化 claim 不进 LLM entailment")

    # ---- entailment_summary 合并三 evaluator ----
    state_sum = _state(refs=[_ref()])
    state_sum.structured_provenance = {"c1": SP.StructuredProvenanceVerdict(
        claim_id="c1", verdict="SUPPORTED", reason="structured_authoritative")}
    state_sum.entailment_verdicts = [H.EntailmentVerdict(
        claim_id="c2", verdict="UNSUPPORTED", reason="no_text")]
    summary = SP.entailment_summary(state_sum, {
        "c3": {"not_inspected": False, "value_missing": True,
               "value_missing_tokens": ["18.12"], "high_risk_scope": False},
    })
    evals = {(s["claim_id"], s["evaluator"]) for s in summary}
    check(("c1", "structured_provenance") in evals
          and ("c2", "llm_entailment") in evals
          and ("c3", "evidence_deterministic") in evals,
          "entailment_summary：合并三 evaluator")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
