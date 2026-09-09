"""Eval: 结构化结果权威判定 —— Phase 3 Batch B 修订⑤（Change 2 + 定点修复）。

用法: python -m evals.test_harness_structured_provenance

断言（纯逻辑，无 LLM / Store I/O——authority 以假函数注入）：
- SnapshotAuthority.authoritative() 复合门（exists+is_current+valid+not_blocked+not_quarantined）；
- 有效 ref → SUPPORTED；单期答变化 → PARTIAL（唯一单期 PARTIAL 情形）；
- company / scope / period / value / item-formula 任一不一致 → UNSUPPORTED；
- unresolvable ref → UNSUPPORTED；快照 not_current / stale / blocked / quarantined /
  not_found → UNSUPPORTED；authority 不可用 → validity_query_unavailable；
- ref.snapshot_status 自称 valid 但 Store 权威为 stale → UNSUPPORTED（不自证）；
- 比较方向验证：claim 方向与 ref.direction 不一致 → direction_mismatch；missing_period →
  comparison_period_missing；只陈述两正确数值不表达方向 → 继续数值/期间校验；
- Citation Repair：snapshot_id 写错 → 唯一 active+period+code 候选才显式改写引用 + 记录
  CITATION_REF_REPAIRED，reason=structured_authoritative_after_citation_repair；0/多候选/
  候选不通过权威 → unresolvable_ref；原错误 ID 不得残留；
- 非业务数值过滤：年份/日期/页码/引用序号不计入业务数值（`_extract_claim_business_amounts`），
  避免「仅述方向无数值」被误判 value_mismatch；
- Citation Repair 两阶段原子化：阶段 A 只提出 pending repair（无副作用），阶段 B 仅在整条
  claim SUPPORTED 时一次性改写全部引用 + 落审计；PARTIAL/UNSUPPORTED 引用不变、无审计；
- 三期趋势确定性计算（Decimal）：increased/decreased/unchanged/mixed；期间不足/数值缺失/
  单位不一致 → PARTIAL；claim 趋势相反 → trend_direction_mismatch；方向无法识别 → PARTIAL；
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


def _compare_refs(sid="S1", formula_id="NET_MARGIN", a="1.61", b="1.60",
                  direction="decreased", unit="%", **kw) -> list[RS.StructuredResultRef]:
    """两期比较 ref（period_a=2024, period_b=2025，同 direction）。"""
    common = dict(snapshot_id=sid, formula_id=formula_id, item_code=None,
                  formula_version="v1", unit=unit, status="CALCULATED_EXACT",
                  reason_code=None, input_record_refs=[], input_snapshot_item_refs=[],
                  company_id="ACME", scope="consolidated", currency="CNY",
                  purpose="credit_analysis", snapshot_status="valid",
                  period_a="2024-12-31", period_b="2025-12-31",
                  value_a=a, value_b=b, direction=direction)
    common.update(kw)
    return [
        RS.StructuredResultRef(result_type="financial_metric", period="2024-12-31",
                               raw_value=a, display_value=a, **common),
        RS.StructuredResultRef(result_type="financial_metric", period="2025-12-31",
                               raw_value=b, display_value=b, **common),
    ]


def _compare_answer(text="2024年1.61，2025年1.60，下降", sid="S1",
                    formula_id="NET_MARGIN", periods=("2024-12-31", "2025-12-31")
                    ) -> H.ResearchAnswer:
    return H.ResearchAnswer(
        question_id="q", answer_text=text,
        claims=[H.Claim(claim_id="c1", text=text, kind="fact",
                        citation_refs=[0, 1])],
        citations=[H.CitationRef(ref_type="structured", snapshot_id=sid,
                                  formula_id=formula_id, formula_version="v1",
                                  period=p) for p in periods])


def _trend_refs(sid="S1", formula_id="NET_MARGIN", displays=("15.0", "16.0", "17.0"),
                unit="%", periods=("2022-12-31", "2023-12-31", "2024-12-31"),
                **kw) -> list[RS.StructuredResultRef]:
    out = []
    for p, d in zip(periods, displays):
        base = dict(snapshot_id=sid, item_code=None, formula_id=formula_id,
                    formula_version="v1", period=p, raw_value=d, display_value=d,
                    unit=unit, status="CALCULATED_EXACT", reason_code=None,
                    input_record_refs=[], input_snapshot_item_refs=[],
                    company_id="ACME", scope="consolidated", currency="CNY",
                    purpose="credit_analysis", snapshot_status="valid")
        base.update(kw)
        out.append(RS.StructuredResultRef(result_type="financial_metric", **base))
    return out


def _trend_answer(text="近三年净利率持续上升", sid="S1", formula_id="NET_MARGIN",
                  periods=("2022-12-31", "2023-12-31", "2024-12-31")
                  ) -> H.ResearchAnswer:
    return H.ResearchAnswer(
        question_id="q", answer_text=text,
        claims=[H.Claim(claim_id="c1", text=text, kind="fact",
                        citation_refs=list(range(len(periods))))],
        citations=[H.CitationRef(ref_type="structured", snapshot_id=sid,
                                  formula_id=formula_id, formula_version="v1",
                                  period=p) for p in periods])


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

    def verdict_obj(state, answer, authority):
        out = SP.evaluate_structured_provenance(state, answer,
                                                snapshot_authority=authority)
        return out["c1"]

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

    # ---- 单期：有效 ref → SUPPORTED ----
    state = _state(refs=[_ref()])
    v, r = verdict_of(state, _answer(), _valid_auth())
    check(v == "SUPPORTED" and r == "structured_authoritative",
          f"有效 ref → SUPPORTED（实际 {v}/{r}）")

    # ---- 单期：无年份 claim → 跳过期间匹配仍 SUPPORTED ----
    v, r = verdict_of(state, _answer(text="净利率为18.12%"), _valid_auth())
    check(v == "SUPPORTED", f"无年份 claim → SUPPORTED（实际 {v}）")

    # ---- 单期：company 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(company_id="OTHER")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "company_mismatch",
          f"company_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期：scope 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(scope="parent")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "scope_mismatch",
          f"scope_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期：period 不一致（claim 唯一年份 ≠ ref.period）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(text="2024年净利率为18.12%"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "period_mismatch",
          f"period_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期：value 不一致 → UNSUPPORTED ----
    state_bad = _state(refs=[_ref(display="20.00")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "value_mismatch",
          f"value_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期：item_formula 不一致（同 snapshot+period 但 code 不符）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref(formula_id="PROF_GROSS_MARGIN")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth())
    check(v == "UNSUPPORTED" and r == "item_formula_mismatch",
          f"item_formula_mismatch → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 单期：unresolvable ref（period 不在 state.refs）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(period="2024-12-31"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "unresolvable_ref",
          f"unresolvable_ref → UNSUPPORTED（实际 {v}/{r}）")

    # ---- 快照 not_current → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(), _valid_auth(is_current=False))
    check(v == "UNSUPPORTED" and r == "snapshot_not_current",
          f"snapshot_not_current → UNSUPPORTED（实际 {v}/{r}）")

    # ---- Store 权威 stale（ref.snapshot_status 自称 valid）→ UNSUPPORTED ----
    state_bad = _state(refs=[_ref(snapshot_status="valid")])
    v, r = verdict_of(state_bad, _answer(), _valid_auth(validity="stale"))
    check(v == "UNSUPPORTED" and r == "snapshot_stale",
          f"ref 自称 valid 但 Store stale → UNSUPPORTED（实际 {v}/{r}）")

    # ---- validity=valid 但 report_blocked → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(), _valid_auth(report_blocked=True))
    check(v == "UNSUPPORTED" and r == "snapshot_report_blocked",
          f"report_blocked → UNSUPPORTED（实际 {v}/{r}）")

    # ---- validity=valid 但 quarantine → UNSUPPORTED ----
    state_bad = _state(refs=[_ref()])
    v, r = verdict_of(state_bad, _answer(), _valid_auth(quarantined=True))
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

    # ---- 单期答变化 → PARTIAL（唯一单期 PARTIAL 情形）----
    state_trend = _state(refs=[_ref()])
    v, r = verdict_of(state_trend,
                      _answer(text="2025年净利率同比如何变化？18.12%"),
                      _valid_auth())
    check(v == "PARTIAL" and r == "single_period_for_trend",
          f"单期答变化 → PARTIAL（实际 {v}/{r}）")

    # ===================== 比较方向验证（定点修复①） =====================

    # 1.61→1.60，claim「下降」→ SUPPORTED
    state_cmp = _state(refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    v, r = verdict_of(state_cmp,
                      _compare_answer(text="2024年1.61%，2025年1.60%，下降"),
                      _valid_auth())
    check(v == "SUPPORTED", f"1.61→1.60 下降 → SUPPORTED（实际 {v}/{r}）")

    # 1.61→1.60，claim「上升」→ UNSUPPORTED direction_mismatch
    v, r = verdict_of(state_cmp,
                      _compare_answer(text="2024年1.61%，2025年1.60%，上升"),
                      _valid_auth())
    check(v == "UNSUPPORTED" and r == "direction_mismatch",
          f"1.61→1.60 上升（写反）→ direction_mismatch（实际 {v}/{r}）")

    # direction=missing_period → PARTIAL comparison_period_missing（单 present ref）
    ref_missing = _ref(period="2025-12-31", display="1.60", unit="%",
                       period_a="2024-12-31", period_b="2025-12-31",
                       value_a=None, value_b="1.60", direction="missing_period")
    state_cmp = _state(refs=[ref_missing])
    v, r = verdict_of(state_cmp,
                      _answer(text="2024年数据缺失，2025年1.60%"),
                      _valid_auth())
    check(v == "PARTIAL" and r == "comparison_period_missing",
          f"missing_period → PARTIAL comparison_period_missing（实际 {v}/{r}）")

    # 正确数字但方向写反 → 不因数字匹配而通过（direction_mismatch）
    state_cmp = _state(refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    v, r = verdict_of(state_cmp,
                      _compare_answer(text="2024年1.61%，2025年1.60%，上升"),
                      _valid_auth())
    check(v == "UNSUPPORTED" and r == "direction_mismatch",
          f"数字正确方向写反 → 不通过（实际 {v}/{r}）")

    # claim 不表达方向、只陈述两个正确数值 → 按数值/期间校验 → SUPPORTED
    v, r = verdict_of(state_cmp, _compare_answer(text="2024年1.61%，2025年1.60%"),
                      _valid_auth())
    check(v == "SUPPORTED", f"仅陈述两正确数值（无方向词）→ SUPPORTED（实际 {v}/{r}）")

    # ===================== 非业务数值过滤（定点修复①） =====================

    def biz_tokens(text):
        return [a.token for a in E.extract_claim_business_amounts(text)]

    # 「2024年至2025年净利率下降」——年份不当指标值，方向正确 → SUPPORTED
    state_cmp = _state(refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    v, r = verdict_of(state_cmp, _compare_answer(text="2024年至2025年净利率下降"),
                      _valid_auth())
    check(v == "SUPPORTED", f"年份不当指标值（下降）→ SUPPORTED（实际 {v}/{r}）")

    # 同一句写「上升」→ direction_mismatch（方向仍由工具结果核对）
    v, r = verdict_of(state_cmp, _compare_answer(text="2024年至2025年净利率上升"),
                      _valid_auth())
    check(v == "UNSUPPORTED" and r == "direction_mismatch",
          f"年份不当指标值（上升写反）→ direction_mismatch（实际 {v}/{r}）")

    # 只提取两个百分比，剔除年份
    check(biz_tokens("2024年至2025年净利率由18.1%下降至17.3%") == ["18.1%", "17.3%"],
          f"只提取两个百分比（实际 {biz_tokens('2024年至2025年净利率由18.1%下降至17.3%')}）")

    # 完整日期：保留 4亿元，剔除 2025/12/31
    check(biz_tokens("截至2025年12月31日，资产为4亿元") == ["4亿元"],
          f"保留4亿元、剔除日期数字（实际 {biz_tokens('截至2025年12月31日，资产为4亿元')}）")

    # 金额/比例带单位后缀 → 保留（2025 是业务数值）
    check(biz_tokens("金额为2025万元") == ["2025万元"],
          f"保留2025万元（实际 {biz_tokens('金额为2025万元')}）")
    check(biz_tokens("2025%") == ["2025%"], "保留 2025%")
    check(biz_tokens("2025元") == ["2025元"], "保留 2025元")

    # 页码/引用序号不进业务数值匹配
    check(biz_tokens("参见第5页，净利率为18.12%[3]") == ["18.12%"],
          f"页码/引用序号不进业务数值（实际 {biz_tokens('参见第5页，净利率为18.12%[3]')}）")

    # 单期只陈述方向无数值（年份被剔除）→ 不误判 value_mismatch，按方向/期间语义判 PARTIAL
    state_single = _state(refs=[_ref()])
    v, r = verdict_of(state_single, _answer(text="2025年净利率上升"), _valid_auth())
    check(v == "PARTIAL" and r == "single_period_for_trend",
          f"单期只述方向无数值 → PARTIAL（实际 {v}/{r}）")

    # ===================== Citation Repair（定点修复②） =====================

    # snapshot_id 正确 → 不产生 repair，reason=structured_authoritative
    state_r = _state(active="S1", refs=[_ref()])
    ans_ok = _answer(sid="S1")
    vo = verdict_obj(state_r, ans_ok, _valid_auth())
    check(vo.verdict == "SUPPORTED" and vo.reason == "structured_authoritative"
          and vo.citation_repairs == [] and state_r.citation_repairs == [],
          f"snapshot_id 正确 → 无 repair（实际 {vo.verdict}/{vo.reason}）")

    # snapshot_id 写错 + 唯一候选 → 显式 repair，答案引用被改写，state 有记录
    state_r = _state(active="S1", refs=[_ref()])
    ans_typo = _answer(sid="S1_typo")
    vo = verdict_obj(state_r, ans_typo, _valid_auth())
    repaired = (vo.verdict == "SUPPORTED"
                and vo.reason == "structured_authoritative_after_citation_repair"
                and ans_typo.citations[0].snapshot_id == "S1"
                and len(state_r.citation_repairs) == 1
                and state_r.citation_repairs[0]["original_snapshot_id"] == "S1_typo"
                and state_r.citation_repairs[0]["repaired_snapshot_id"] == "S1")
    check(repaired,
          f"唯一候选 → 显式 repair + 改写引用 + 审计（实际 {vo.verdict}/{vo.reason}）")

    # 原错误 ID 不得继续出现在最终持久化 CitationRef 中
    check("S1_typo" not in [c.snapshot_id for c in ans_typo.citations],
          "原错误 snapshot_id 不残留于最终 CitationRef")

    # 两个候选 → UNSUPPORTED unresolvable_ref
    state_r = _state(active="S1", refs=[_ref(), _ref()])
    v, r = verdict_of(state_r, _answer(sid="S1_typo"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "unresolvable_ref",
          f"两候选 → unresolvable_ref（实际 {v}/{r}）")

    # 候选不是 current（repair 候选不通过权威）→ UNSUPPORTED unresolvable_ref
    state_r = _state(active="S1", refs=[_ref()])
    v, r = verdict_of(state_r, _answer(sid="S1_typo"),
                      _valid_auth(is_current=False))
    check(v == "UNSUPPORTED" and r == "unresolvable_ref",
          f"候选不 current → unresolvable_ref（实际 {v}/{r}）")

    # 候选是 active 但 period 无匹配 → unresolvable_ref（无同 period ref）
    state_r = _state(active="S1", refs=[_ref()])
    v, r = verdict_of(state_r, _answer(sid="S1_typo", period="2024-12-31"),
                      _valid_auth())
    check(v == "UNSUPPORTED" and r == "unresolvable_ref",
          f"无同 period 候选 → unresolvable_ref（实际 {v}/{r}）")

    # ===================== Citation Repair 两阶段原子化（定点修复②） =====================

    # repair 候选身份有效但 value mismatch → UNSUPPORTED，引用不变、无 repair 审计
    state_r = _state(active="S1", refs=[_ref(display="20.00")])
    ans_vm = _answer(sid="S1_typo", text="2025年净利率为18.12%")
    v, r = verdict_of(state_r, ans_vm, _valid_auth())
    check(v == "UNSUPPORTED" and r == "value_mismatch"
          and ans_vm.citations[0].snapshot_id == "S1_typo"
          and state_r.citation_repairs == [],
          f"repair 候选 value mismatch → 引用不变、无审计（实际 {v}/{r}）")

    # repair 候选身份有效但 period mismatch → 引用不变
    state_r = _state(active="S1", refs=[_ref()])
    ans_pm = _answer(sid="S1_typo", text="2024年净利率为18.12%")
    v, r = verdict_of(state_r, ans_pm, _valid_auth())
    check(v == "UNSUPPORTED" and r == "period_mismatch"
          and ans_pm.citations[0].snapshot_id == "S1_typo"
          and state_r.citation_repairs == [],
          f"repair 候选 period mismatch → 引用不变（实际 {v}/{r}）")

    # repair 候选身份有效但 direction mismatch → 引用不变
    state_r = _state(active="S1",
                     refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    ans_dm = _compare_answer(text="2024年1.61%，2025年1.60%，上升", sid="S1_typo")
    v, r = verdict_of(state_r, ans_dm, _valid_auth())
    check(v == "UNSUPPORTED" and r == "direction_mismatch"
          and all(c.snapshot_id == "S1_typo" for c in ans_dm.citations)
          and state_r.citation_repairs == [],
          f"repair 候选 direction mismatch → 引用不变（实际 {v}/{r}）")

    # 多引用全部成功 → 一次性全部改写 + 2 条审计
    state_r = _state(active="S1",
                     refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    ans_ok2 = _compare_answer(text="2024年1.61%，2025年1.60%，下降", sid="S1_typo")
    v, r = verdict_of(state_r, ans_ok2, _valid_auth())
    check(v == "SUPPORTED"
          and all(c.snapshot_id == "S1" for c in ans_ok2.citations)
          and len(state_r.citation_repairs) == 2,
          f"多引用全部成功 → 一次性改写 + 2 审计（实际 {v}/{r}）")

    # 多引用中一个失败（period 无匹配）→ 所有引用保持原值，禁止部分提交
    state_r = _state(active="S1",
                     refs=_compare_refs(a="1.61", b="1.60", direction="decreased"))
    ans_part = H.ResearchAnswer(
        question_id="q", answer_text="2024年1.61%，2025年1.60%，下降",
        claims=[H.Claim(claim_id="c1", text="2024年1.61%，2025年1.60%，下降",
                        kind="fact", citation_refs=[0, 1])],
        citations=[
            H.CitationRef(ref_type="structured", snapshot_id="S1_typo",
                          formula_id="NET_MARGIN", formula_version="v1",
                          period="2024-12-31"),
            H.CitationRef(ref_type="structured", snapshot_id="S1_typo",
                          formula_id="NET_MARGIN", formula_version="v1",
                          period="2020-12-31"),  # 无匹配 ref → unresolvable
        ])
    v, r = verdict_of(state_r, ans_part, _valid_auth())
    check(v == "UNSUPPORTED" and r == "unresolvable_ref"
          and all(c.snapshot_id == "S1_typo" for c in ans_part.citations)
          and state_r.citation_repairs == [],
          f"多引用一个失败 → 全部不变（实际 {v}/{r}）")

    # ===================== 三期趋势（定点修复③） =====================

    # 连续上升 → increased，claim「持续上升」→ SUPPORTED
    state_t = _state(refs=_trend_refs(displays=("15.0", "16.0", "17.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续上升"), _valid_auth())
    check(v == "SUPPORTED", f"三期连续上升 → SUPPORTED（实际 {v}/{r}）")

    # 连续下降 → decreased，claim「持续下降」→ SUPPORTED
    state_t = _state(refs=_trend_refs(displays=("17.0", "16.0", "15.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续下降"), _valid_auth())
    check(v == "SUPPORTED", f"三期连续下降 → SUPPORTED（实际 {v}/{r}）")

    # 全部相等 → unchanged，claim「基本稳定」→ SUPPORTED
    state_t = _state(refs=_trend_refs(displays=("15.0", "15.0", "15.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率基本稳定"), _valid_auth())
    check(v == "SUPPORTED", f"三期全部相等 → SUPPORTED（实际 {v}/{r}）")

    # 有升有降 → mixed，claim「有升有降」→ SUPPORTED
    state_t = _state(refs=_trend_refs(displays=("15.0", "16.0", "15.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率有升有降"), _valid_auth())
    check(v == "SUPPORTED", f"三期有升有降（mixed）→ SUPPORTED（实际 {v}/{r}）")

    # mixed 却写成持续上升 → UNSUPPORTED trend_direction_mismatch
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续上升"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "trend_direction_mismatch",
          f"mixed 写成持续上升 → trend_direction_mismatch（实际 {v}/{r}）")

    # 连续上升但 claim 写「持续下降」→ UNSUPPORTED trend_direction_mismatch
    state_t = _state(refs=_trend_refs(displays=("15.0", "16.0", "17.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续下降"), _valid_auth())
    check(v == "UNSUPPORTED" and r == "trend_direction_mismatch",
          f"连续上升写成持续下降 → trend_direction_mismatch（实际 {v}/{r}）")

    # 数值缺失 → PARTIAL
    state_t = _state(refs=_trend_refs(displays=("15.0", None, "17.0")))
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续上升"), _valid_auth())
    check(v == "PARTIAL" and r == "trend_value_missing",
          f"数值缺失 → PARTIAL trend_value_missing（实际 {v}/{r}）")

    # 单位不一致（% vs 元）→ PARTIAL
    refs_mixed_unit = [_ref(period="2022-12-31", display="15.0", unit="%"),
                       _ref(period="2023-12-31", display="16.0", unit="%"),
                       _ref(period="2024-12-31", display="17.0", unit="元")]
    state_t = _state(refs=refs_mixed_unit)
    v, r = verdict_of(state_t, _trend_answer(text="近三年净利率持续上升"), _valid_auth())
    check(v == "PARTIAL" and r == "trend_unit_scope_mismatch",
          f"单位不一致 → PARTIAL trend_unit_scope_mismatch（实际 {v}/{r}）")

    # 方向无法可靠识别（无方向词 + 无显式趋势词）→ 单期 PARTIAL（single_period_for_trend）
    # （claim 只有数值无方向 → 不算趋势语义；单期直接 SUPPORTED，见下）
    state_t = _state(refs=[_ref()])
    v, r = verdict_of(state_t, _answer(text="2025年净利率为18.12%"), _valid_auth())
    check(v == "SUPPORTED", f"无趋势语义单期 → SUPPORTED（实际 {v}/{r}）")

    # 三期但 claim 无法识别方向 → PARTIAL trend_direction_ambiguous
    state_t = _state(refs=_trend_refs(displays=("15.0", "16.0", "17.0")))
    v, r = verdict_of(state_t,
                      _trend_answer(text="近三年净利率走势如下：15%、16%、17%"),
                      _valid_auth())
    check(v == "PARTIAL" and r == "trend_direction_ambiguous",
          f"三期方向无法识别 → PARTIAL trend_direction_ambiguous（实际 {v}/{r}）")

    # ---- _trend_relation 直接单测：期间不足 / 方向 ----
    check(SP._trend_relation([_ref(period="2022-12-31", display="15.0")]) ==
          (None, "trend_insufficient_periods"),
          "_trend_relation：单期 → insufficient_periods")
    rel, _ = SP._trend_relation(_trend_refs(displays=("15.0", "16.0", "17.0")))
    check(rel == "increased", f"_trend_relation：连续上升 → increased（实际 {rel}）")
    rel, _ = SP._trend_relation(_trend_refs(displays=("15.0", "16.0", "15.0")))
    check(rel == "mixed", f"_trend_relation：有升有降 → mixed（实际 {rel}）")

    # ---- _claim_direction 直接单测 ----
    check(SP._claim_direction("净利率同比上升") == "increased",
          "_claim_direction：上升 → increased")
    check(SP._claim_direction("净利率下降") == "decreased",
          "_claim_direction：下降 → decreased")
    check(SP._claim_direction("净利率基本稳定") == "unchanged",
          "_claim_direction：基本稳定 → unchanged")
    check(SP._claim_direction("净利率有升有降") == "mixed",
          "_claim_direction：有升有降 → mixed")
    check(SP._claim_direction("净利率为18.12%") is None,
          "_claim_direction：无数值方向 → None")
    check(SP._claim_direction("营收增长率为15%") is None,
          "_claim_direction：增长率名词不误判为方向 → None")

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
