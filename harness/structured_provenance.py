"""Phase 3 Batch B 修订④：结构化结果权威判定（确定性，无 LLM）。

针对「仅结构化引用」的 fact claim，用 Store 权威 + canonical Decimal 数值等价，在 LLM
entailment 之前确定性地判定 SUPPORTED / PARTIAL / UNSUPPORTED，避免纯结构化 claim 被
LLM 因「无正文」误判 UNSUPPORTED（FIN-PM1）。

判定原则（fail-closed）：
- 权威性 = 复合判定（exists + is_current + validity==valid + not report_blocked +
  not quarantined），由 Store 决定；ref.snapshot_status 仅展示/审计，不自证。
- 数值事实任一实质不匹配 → UNSUPPORTED（非 PARTIAL）；唯一 PARTIAL 是「单期答变化」。
- ≥3 期「近三年趋势」序列无代码算得方向 → 本模块不判（回退 LLM 解读序列）。

本模块只做「纯判定」（读 state/answer + 注入的 snapshot_authority，不写库）；
`query_snapshot_authority` 为唯一 I/O 边界（Store 只读），运行时以
`partial(query_snapshot_authority, current_snapshot_id=state.active_snapshot_id)`
注入，使判定函数本身可离线测试。

CLI: python -m harness.structured_provenance --self-check
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from functools import partial

from financial_v2 import store as fstore
from harness import entailment as E
from harness import schema as H
from routing import schema as RS


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# ref.status 中「有真实值」的状态（financial_field 恒用 "available"；financial_metric
# 用 CALCULATED_EXACT / CALCULATED_PROXY，与 routing.context._AVAILABLE_METRIC_STATUSES 对齐）。
_AVAILABLE_STATUSES = ("available", "CALCULATED_EXACT", "CALCULATED_PROXY")

# ≥3 期趋势序列标记：代码无算得方向 → 本模块不判（回退 LLM 解读序列）。
_MULTI_PERIOD_TREND = ("近三年", "近3年", "连续三年")

# 唯一年份抽取（"2025年" → "2025-12-31"；≥2 个或 0 个年份 → None）。
_YEAR_RE = re.compile(r"(20\d{2})\s*年")

# 结构化引用解析「code 不符但同 snapshot+period 存在其它 ref」的哨兵。
_CODE_MISMATCH = object()


# ---------------------------------------------------------------------------
# 权威性复合判定（Store 权威）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotAuthority:
    """快照权威性复合判定（Store 权威；ref.snapshot_status 不自证）。"""

    exists: bool
    is_current: bool            # snapshot_id == run 锁定的 current_snapshot_id
    validity: str | None        # latest_snapshot_validity → valid|stale|superseded|None
    report_blocked: bool        # financial_snapshot.report_blocked 布尔列（独立于 validity）
    quarantined: bool           # quarantine 表 is_quarantined("financial_snapshot", id)

    def authoritative(self) -> bool:
        return (self.exists and self.is_current and self.validity == "valid"
                and not self.report_blocked and not self.quarantined)


@dataclass(frozen=True)
class StructuredProvenanceVerdict:
    """对单个 fact claim 的结构化权威判定。"""

    claim_id: str
    verdict: str = "UNSUPPORTED"          # SUPPORTED|PARTIAL|UNSUPPORTED
    evaluator: str = "structured_provenance"
    matched_refs: list = field(default_factory=list)
    reason: str = ""
    snapshot_valid: bool = False
    company_match: bool = False
    period_match: bool = False
    scope_currency_purpose_match: bool = False
    item_formula_match: bool = False
    value_match: bool = False


def query_snapshot_authority(snapshot_id: str,
                             current_snapshot_id: str | None) -> SnapshotAuthority:
    """Store 只读权威查询（get_snapshot + latest_snapshot_validity + is_quarantined）。"""
    snap = fstore.get_snapshot(snapshot_id)
    exists = snap is not None
    validity = fstore.latest_snapshot_validity(snapshot_id) if exists else None
    return SnapshotAuthority(
        exists=exists,
        is_current=(snapshot_id == current_snapshot_id),
        validity=validity,
        report_blocked=(snap.report_blocked if snap is not None else False),
        quarantined=fstore.is_quarantined("financial_snapshot", snapshot_id),
    )


# ---------------------------------------------------------------------------
# 纯判定辅助
# ---------------------------------------------------------------------------

def _claim_period(text: str) -> str | None:
    """claim 唯一年份 → "YYYY-12-31"；≥2 个或 0 个年份 → None（跳过期间匹配）。"""
    years = sorted({y for y in _YEAR_RE.findall(text or "")})
    return f"{years[0]}-12-31" if len(years) == 1 else None


def _ref_amount(ref: RS.StructuredResultRef) -> E.Amount | None:
    """把结构化 ref 的 (display_value|raw_value, unit) 归一化为 canonical Amount。"""
    v = ref.display_value if ref.display_value is not None else ref.raw_value
    if v is None:
        return None
    unit = (ref.unit or "").strip()
    amts = E.extract_amounts(f"{v}{unit}")
    return amts[0] if amts else None


def _value_matches(ref_amount: E.Amount | None,
                   claim_amounts: list[E.Amount]) -> bool:
    """ref 数值与 claim 数值中任一 canonical Decimal 等价 + 单位同类。"""
    if ref_amount is None:
        return False
    for ca in claim_amounts:
        if E.units_compatible(ref_amount, ca) and ca.value == ref_amount.value:
            return True
    return False


def _structured_citations(claim: H.Claim, answer: H.ResearchAnswer
                          ) -> list[H.CitationRef]:
    return [answer.citations[i] for i in claim.citation_refs
            if 0 <= i < len(answer.citations)
            and answer.citations[i].ref_type == "structured"]


def _has_evidence_citation(claim: H.Claim, answer: H.ResearchAnswer) -> bool:
    return any(0 <= i < len(answer.citations)
               and answer.citations[i].ref_type == "evidence"
               for i in claim.citation_refs)


def _resolve_ref(cit: H.CitationRef, state: H.ResearchState):
    """按 period + code 匹配结构化 ref。

    snapshot_id 是机器生成的 opaque 长 token，LLM 在写引用时可能转写错（真实复跑
    FIN-CF1 中 "…eadbde…" 被写成 "…deadbe…"），故 snapshot_id 不作为匹配硬键；
    ref 自身 snapshot_id 才是权威来源，由 `_check_ref` 做 is_current 校验。

    返回 StructuredResultRef（命中）/ None（无同 period ref，unresolvable）/
    _CODE_MISMATCH（同 period 有 ref 但 code 不符，item_formula_mismatch）。
    """
    same_period = [r for r in state.structured_refs if r.period == cit.period]
    if not same_period:
        return None

    def _code_match(r) -> bool:
        if cit.formula_id:
            return r.formula_id == cit.formula_id
        if cit.item_code:
            return r.item_code == cit.item_code
        return False

    # 1) 精确 snapshot + code 命中优先（LLM 正确转写时）。
    for r in same_period:
        if r.snapshot_id == cit.snapshot_id and _code_match(r):
            return r
    # 2) snapshot 转写错 → 回退 period+code；多个候选优先 current snapshot。
    code_matches = [r for r in same_period if _code_match(r)]
    if not code_matches:
        return _CODE_MISMATCH
    for r in code_matches:
        if r.snapshot_id == state.active_snapshot_id:
            return r
    return code_matches[0]


def _verdict(claim_id: str, verdict: str, reason: str, *,
             matched_refs: list = (), snapshot_valid: bool = False,
             company_match: bool = False, scope_currency_purpose_match: bool = False,
             item_formula_match: bool = False, value_match: bool = False,
             period_match: bool = False) -> StructuredProvenanceVerdict:
    return StructuredProvenanceVerdict(
        claim_id=claim_id, verdict=verdict, reason=reason,
        matched_refs=list(matched_refs), snapshot_valid=snapshot_valid,
        company_match=company_match,
        scope_currency_purpose_match=scope_currency_purpose_match,
        item_formula_match=item_formula_match, value_match=value_match,
        period_match=period_match)


def _check_ref(ref: RS.StructuredResultRef, claim: H.Claim, state: H.ResearchState,
               snapshot_authority, scope: str, currency: str, purpose: str,
               ) -> StructuredProvenanceVerdict | None:
    """单条结构化 ref 的 fail-closed 判定（返回 None=通过；否则失败 verdict）。"""
    # 1. 权威性（Store 权威；ref.snapshot_status 不自证）。
    if snapshot_authority is None:
        return _verdict(claim.claim_id, "UNSUPPORTED", "validity_query_unavailable")
    auth = snapshot_authority(ref.snapshot_id)
    if auth is None or not auth.exists:
        return _verdict(claim.claim_id, "UNSUPPORTED", "snapshot_not_found")
    if not auth.is_current:
        return _verdict(claim.claim_id, "UNSUPPORTED", "snapshot_not_current")
    if auth.validity != "valid":
        return _verdict(claim.claim_id, "UNSUPPORTED",
                        f"snapshot_{auth.validity or 'validity_missing'}")
    if auth.report_blocked:
        return _verdict(claim.claim_id, "UNSUPPORTED", "snapshot_report_blocked")
    if auth.quarantined:
        return _verdict(claim.claim_id, "UNSUPPORTED", "snapshot_quarantined")
    # 2. company。
    if ref.company_id != state.company_id:
        return _verdict(claim.claim_id, "UNSUPPORTED", "company_mismatch",
                        snapshot_valid=True, company_match=False)
    # 3. scope/currency/purpose。
    if (ref.scope != scope or ref.currency != currency or ref.purpose != purpose):
        dim = ("scope" if ref.scope != scope
               else ("currency" if ref.currency != currency else "purpose"))
        return _verdict(claim.claim_id, "UNSUPPORTED", f"{dim}_mismatch",
                        snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=False)
    # 4. 指标计算态（覆盖 blocked / not_applicable / missing_input 等）。
    if ref.status not in _AVAILABLE_STATUSES:
        return _verdict(claim.claim_id, "UNSUPPORTED", f"metric_status_{ref.status}",
                        snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=False)
    # 5. 数值等价（canonical Decimal + 单位同类）。
    ref_amt = _ref_amount(ref)
    claim_amounts = E.extract_amounts(claim.text or "")
    if not _value_matches(ref_amt, claim_amounts):
        return _verdict(claim.claim_id, "UNSUPPORTED", "value_mismatch",
                        snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=False)
    return None


def _evaluate_claim(claim: H.Claim, scits: list[H.CitationRef], state: H.ResearchState,
                    snapshot_authority, scope: str, currency: str, purpose: str
                    ) -> StructuredProvenanceVerdict:
    refs: list[RS.StructuredResultRef] = []
    for c in scits:
        r = _resolve_ref(c, state)
        if r is _CODE_MISMATCH:
            return _verdict(claim.claim_id, "UNSUPPORTED", "item_formula_mismatch")
        if r is None:
            return _verdict(claim.claim_id, "UNSUPPORTED", "unresolvable_ref")
        refs.append(r)

    matched: list[str] = []
    for r in refs:
        fail = _check_ref(r, claim, state, snapshot_authority, scope, currency, purpose)
        if fail is not None:
            return fail
        matched.append(f"{r.snapshot_id}:{r.formula_id or r.item_code}:{r.period}")

    # 6. 单期 period 匹配（claim 唯一年份 → ref.period == "YYYY-12-31"）。
    cp = _claim_period(claim.text)
    period_match = cp is None or all(r.period == cp for r in refs)
    if not period_match:
        return _verdict(claim.claim_id, "UNSUPPORTED", "period_mismatch",
                        matched_refs=matched, snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=False)

    # 7. 趋势守卫：claim 含趋势词但 ref 无 period_a/period_b（比较所需期间缺失）→ PARTIAL。
    if E.has_trend_semantics(claim.text) and not any(getattr(r, "period_a", None)
                                                     for r in refs):
        return _verdict(claim.claim_id, "PARTIAL", "single_period_for_trend",
                        matched_refs=matched, snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=True)

    return _verdict(claim.claim_id, "SUPPORTED", "structured_authoritative",
                    matched_refs=matched, snapshot_valid=True, company_match=True,
                    scope_currency_purpose_match=True, item_formula_match=True,
                    value_match=True, period_match=True)


# ---------------------------------------------------------------------------
# 主判定
# ---------------------------------------------------------------------------

def evaluate_structured_provenance(state: H.ResearchState, answer: H.ResearchAnswer | None,
                                   *, snapshot_authority=None,
                                   scope: str = "consolidated", currency: str = "CNY",
                                   purpose: str = "credit_analysis",
                                   ) -> dict[str, StructuredProvenanceVerdict]:
    """逐「仅结构化引用」fact claim 判定（无 LLM）。

    snapshot_authority: Callable[[snapshot_id], SnapshotAuthority|None]，运行时以
    partial(query_snapshot_authority, current_snapshot_id=state.active_snapshot_id) 注入；
    传入 None → 命中 claim 判 validity_query_unavailable（fail-closed）。

    返回 {claim_id: StructuredProvenanceVerdict}。仅结构化引用（无 evidence 引用）且非
    ≥3 期趋势序列的 fact claim 才被判定；混合引用/≥3 期趋势/无结构化引用的 claim 不在此
    层（回退 LLM entailment）。
    """
    out: dict[str, StructuredProvenanceVerdict] = {}
    if answer is None:
        return out
    for claim in answer.claims:
        if claim.kind != "fact":
            continue
        scits = _structured_citations(claim, answer)
        if not scits:
            continue
        if _has_evidence_citation(claim, answer):
            continue  # 混合引用 → 交 LLM entailment
        if any(t in (claim.text or "") for t in _MULTI_PERIOD_TREND):
            continue  # ≥3 期趋势序列 → 本模块不判（回退 LLM 解读序列）
        out[claim.claim_id] = _evaluate_claim(claim, scits, state, snapshot_authority,
                                              scope, currency, purpose)
    return out


def entailment_summary(state: H.ResearchState, prechecks: dict) -> list[dict]:
    """合并三 evaluator 汇总（evidence_deterministic / structured_provenance /
    llm_entailment），每项 {claim_id, evaluator, verdict, reason}。"""
    summary: list[dict] = []
    for cid, pc in prechecks.items():
        if pc.get("not_inspected"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "UNSUPPORTED", "reason": "not_inspected"})
        elif pc.get("value_missing"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "UNSUPPORTED",
                            "reason": "value_missing:"
                                      + ",".join(pc.get("value_missing_tokens", []))})
        elif pc.get("high_risk_scope"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "PARTIAL", "reason": "high_risk_scope"})
    for cid, v in state.structured_provenance.items():
        summary.append({"claim_id": cid, "evaluator": "structured_provenance",
                        "verdict": v.verdict, "reason": v.reason})
    for v in state.entailment_verdicts:
        summary.append({"claim_id": v.claim_id, "evaluator": "llm_entailment",
                        "verdict": v.verdict, "reason": v.reason})
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.structured_provenance",
        description="结构化结果权威判定自检（纯函数，注入假 authority，不读库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        need = RS.InformationNeed(
            need_id="q", section_id="fin", question="2025年净利率是多少？",
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="P0", depends_on=[])
        state = H.ResearchState(
            run_id="r", case_id="c", question_id="q", company_id="ACME",
            section_id="fin", original_question="2025年净利率是多少？", need=need)
        state.active_snapshot_id = "S1"

        def _ref(sid, formula_id, period, display="18.12", unit="%",
                 status="CALCULATED_EXACT", company="ACME"):
            return RS.StructuredResultRef(
                result_type="financial_metric", snapshot_id=sid, item_code=None,
                formula_id=formula_id, formula_version="v1", period=period,
                raw_value=display, display_value=display, unit=unit, status=status,
                reason_code=None, input_record_refs=[], input_snapshot_item_refs=[],
                company_id=company, scope="consolidated", currency="CNY",
                purpose="credit_analysis", snapshot_status="valid")

        state.structured_refs = [_ref("S1", "NET_MARGIN", "2025-12-31"),
                                 _ref("S2", "NET_MARGIN", "2025-12-31")]

        def _authority(sid: str) -> SnapshotAuthority | None:
            return SnapshotAuthority(exists=True, is_current=(sid == "S1"),
                                     validity="valid", report_blocked=False,
                                     quarantined=False)

        def _answer(claim_text: str, sid: str = "S1") -> H.ResearchAnswer:
            return H.ResearchAnswer(
                question_id="q", answer_text=claim_text,
                claims=[H.Claim(claim_id="c1", text=claim_text, kind="fact",
                                citation_refs=[0])],
                citations=[H.CitationRef(ref_type="structured", snapshot_id=sid,
                                          formula_id="NET_MARGIN",
                                          formula_version="v1",
                                          period="2025-12-31")])

        supported = evaluate_structured_provenance(
            state, _answer("2025年净利率为18.12%"), snapshot_authority=_authority)
        partial = evaluate_structured_provenance(
            state, _answer("2025年净利率同比如何变化？18.12%"),
            snapshot_authority=_authority)
        not_current = evaluate_structured_provenance(
            state, _answer("2025年净利率为18.12%", sid="S2"),
            snapshot_authority=_authority)

        print(json.dumps({
            "claim_period_single": _claim_period("2025年净利率18.12%"),
            "claim_period_multi": _claim_period("2024年较2025年如何变化？"),
            "ref_amount": E.dataclasses_asdict(_ref_amount(state.structured_refs[0]))
                         if _ref_amount(state.structured_refs[0]) else None,
            "supported": {k: {"verdict": v.verdict, "reason": v.reason}
                          for k, v in supported.items()},
            "partial_trend": {k: {"verdict": v.verdict, "reason": v.reason}
                              for k, v in partial.items()},
            "not_current": {k: {"verdict": v.verdict, "reason": v.reason}
                            for k, v in not_current.items()},
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
