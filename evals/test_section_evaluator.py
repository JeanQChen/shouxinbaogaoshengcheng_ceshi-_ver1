"""Phase 4 Batch D — 章节 Rules Evaluator 专项评测（12 项规则 + 返回契约）。

纯离线：注入假 CitationAuthority / FinancialFactPack，不读库、不调 LLM。
覆盖任务书 §13.1 的 12 项规则及 RulesVerdict 返回契约（幂等 id、阻断/返工/警告三档）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import rules_evaluator as RE  # noqa: E402
from sections.citation_authority import CitationVerdict  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _task(aspects=(), blocking=(), impact=(), topic_ids=("t1", "t2")):
    return PS.SectionTask(
        task_id="task_eval", plan_id="plan_eval", section_id="financial",
        title="财务分析", purpose="p", research_policy="workflow",
        topic_ids=topic_ids,
        questions=(
            PS.PlannedQuestion(question_id="q1", question="Q1", priority="P0",
                               topic_id="t1", required_aspects=aspects,
                               blocking_policy=blocking, impact_scope=impact),
            PS.PlannedQuestion(question_id="q2", question="Q2", priority="P1",
                               topic_id="t2", blocking_policy=(), impact_scope=()),
        ),
        evaluation_rule_ids=(), allowed_capabilities=(),
        output_requirements=(), blocking_rules=(), dependency_versions={},
    )


def _claim(cid, topic_id, qids, text, ctype="fact", refs=(), impact=()):
    return SS.SectionClaim(claim_id=cid, section_id="financial", topic_id=topic_id,
                           question_ids=qids, text=text, claim_type=ctype,
                           citation_refs=refs, impact_scope=impact)


def _struct_ref(sid="S1", item="TOTAL_ASSETS", period="2025-12-31"):
    return CitationRef(ref_type="structured", snapshot_id=sid, item_code=item, period=period)


def _result(task, claims, unresolved=(), status="COMPLETED", markdown="# 财务分析"):
    return SS.SectionResult(
        section_result_id="sr_eval", section_version="secver_eval", task_id=task.task_id,
        section_id="financial", status=status, claims=tuple(claims),
        unresolved=tuple(unresolved), markdown=markdown)


class _Fact:
    def __init__(self, kind, code, period, display, status="CALCULATED_EXACT"):
        self.kind, self.code, self.period, self.display, self.status = \
            kind, code, period, display, status


class _Pack:
    def __init__(self, facts):
        self.facts = facts


class _Authority:
    def __init__(self, fail=(), unknown=(), grades=None):
        self._fail = set(fail)
        self._unknown = set(unknown)
        self._grades = grades or {}
        self.calls = 0

    def validate(self, ref):
        self.calls += 1
        ident = SS.citation_identity(ref)
        if ident in self._fail:
            return CitationVerdict(ref.ref_type, False, "snapshot_not_current")
        if ref.ref_type == "external" and ident in self._unknown:
            return CitationVerdict("external", True, None, ("published_at_unknown",))
        return CitationVerdict(ref.ref_type, True)

    def external_get(self, sid):
        class _Ext:
            source_grade = self._grades.get(sid)
        return _Ext()


def _rule_ids(v: RE.RulesVerdict) -> set[str]:
    return {i.rule_id for i in v.issues}


def main():
    fact = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "1,234.56万元")
    task_ok = _task()
    ref_ok = _struct_ref(item="TOTAL_ASSETS")
    c_ok = _claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (ref_ok,))
    c_q2 = _claim("c2", "t2", ("q2",), "整体财务稳健", "inference", ())
    pack = _Pack([fact])

    # --- 规则 1：契约覆盖 ---
    v = RE.evaluate_section(_result(task_ok, [c_ok, c_q2]), task_ok,
                            citation_authority=_Authority(), fact_pack=pack)
    check(v.rules_passed and not v.blocking, "合法章节通过（规则 1/4/5/6 无告警）")

    res = _result(task_ok, [c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("coverage_missing_topic" in _rule_ids(v), "缺失 topic 检出 coverage_missing_topic")
    check("coverage_missing_question" in _rule_ids(v), "缺失 question 检出 coverage_missing_question")

    # unresolved 覆盖 question 时不重复报 coverage_missing_question
    u_cov = SS.SectionUnresolved(unresolved_id="ur_cov", section_id="financial",
                                 topic_id="t2", question_id="q2",
                                 state="NOT_FOUND_AFTER_SEARCH", reason_code="write_not_found",
                                 detail="未检索到")
    res = _result(task_ok, [c_ok], unresolved=[u_cov], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("coverage_missing_question" not in _rule_ids(v),
          "unresolved 覆盖问题后不再报覆盖缺口")

    # --- 规则 2：required aspect（warning，非阻断）---
    task_aspect = _task(aspects=("流动比率",))
    res = _result(task_aspect, [c_ok, c_q2])
    v = RE.evaluate_section(res, task_aspect)
    check(any(i.rule_id == "aspect_uncovered" and i.severity == "warning" for i in v.issues),
          "未字面出现的 aspect → warning 且不阻断")
    c_aspect = _claim("c3", "t1", ("q1",), "流动比率 1.23", "fact", (ref_ok,))
    res = _result(task_aspect, [c_aspect, c_q2])
    v = RE.evaluate_section(res, task_aspect)
    check("aspect_uncovered" not in _rule_ids(v), "字面出现的 aspect 不报")

    # --- 规则 3：阻断后果一致性 ---
    u_conf = SS.SectionUnresolved(unresolved_id="ur_conf", section_id="financial",
                                  topic_id="t1", question_id="q1", state="CONFLICT",
                                  reason_code="conflict_pause", detail="口径冲突")
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_conf], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "conflict_unresolved" in _rule_ids(v), "CONFLICT → blocking")

    u_wh = SS.SectionUnresolved(unresolved_id="ur_wh", section_id="financial",
                                topic_id="t1", question_id="q1", state="WAITING_HUMAN",
                                reason_code="transfer_human", detail="需确认")
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_wh], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "waiting_human" in _rule_ids(v), "WAITING_HUMAN → blocking")

    # blocking_effects 与 blocking_policy 不一致
    task_blk = _task(blocking=("SECTION_BLOCKED",), impact=("subject",))
    u_mismatch = SS.SectionUnresolved(unresolved_id="ur_mm", section_id="financial",
                                      topic_id="t1", question_id="q1",
                                      state="NOT_FOUND_AFTER_SEARCH", reason_code="x",
                                      detail="d", blocking_effects=())
    res = _result(task_blk, [c_ok, c_q2], unresolved=[u_mismatch], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_blk)
    check("blocking_policy_mismatch" in _rule_ids(v), "blocking_effects 不一致 → blocking_policy_mismatch")

    # SECTION_BLOCKED unresolved 但 status 非 SECTION_BLOCKED
    u_sb = SS.SectionUnresolved(unresolved_id="ur_sb", section_id="financial",
                                topic_id="t1", question_id="q1",
                                state="NOT_FOUND_AFTER_SEARCH", reason_code="x",
                                detail="d", blocking_effects=("SECTION_BLOCKED",))
    res = _result(task_ok, [c_ok, c_q2], unresolved=[u_sb], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("status_not_blocked" in _rule_ids(v), "SECTION_BLOCKED unresolved 但 status 未阻断 → status_not_blocked")

    # --- 规则 4：关键 claim 有 citation ---
    c_nocite = _claim("c4", "t1", ("q1",), "某事实", "fact", ())
    res = _result(task_ok, [c_nocite, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("claim_missing_citation" in _rule_ids(v), "fact 无 citation → claim_missing_citation")

    # --- 规则 5：引用可解析 ---
    res = _result(task_ok, [c_ok, c_q2])
    auth_fail = _Authority(fail={SS.citation_identity(ref_ok)})
    v = RE.evaluate_section(res, task_ok, citation_authority=auth_fail, fact_pack=pack)
    check("citation_unresolvable" in _rule_ids(v), "引用不可解析 → citation_unresolvable")

    # 缓存：同引用只 validate 一次
    auth = _Authority()
    v = RE.evaluate_section(res, task_ok, citation_authority=auth, fact_pack=pack)
    check(auth.calls == 1, f"相同引用只 validate 一次（实际 {auth.calls} 次）")

    # --- 规则 6：财务数值 == 权威值 ---
    res = _result(task_ok, [c_ok, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(),
                            fact_pack=_Pack([_Fact("fact", "TOTAL_ASSETS", "2025-12-31", "9,999万元")]))
    check("financial_value_mismatch" in _rule_ids(v), "display 不在正文 → financial_value_mismatch")

    ref_unknown = _struct_ref(item="REVENUE")
    c_unknown = _claim("c5", "t1", ("q1",), "营收增长", "fact", (ref_unknown,))
    res = _result(task_ok, [c_unknown, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(), fact_pack=pack)
    check("financial_unresolvable_value" in _rule_ids(v), "structured 引用解析不到 → financial_unresolvable_value")

    fact_proxy = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                       status="CALCULATED_PROXY")
    ref_metric = CitationRef(ref_type="structured", snapshot_id="S1",
                             formula_id="SOLV_CURRENT_RATIO", formula_version="v1",
                             period="2025-12-31")
    c_proxy = _claim("c6", "t1", ("q1",), "流动比率精确值为 1.50", "calculation", (ref_metric,))
    res = _result(task_ok, [c_proxy, c_q2])
    v = RE.evaluate_section(res, task_ok, citation_authority=_Authority(),
                            fact_pack=_Pack([fact_proxy]))
    check("proxy_claimed_exact" in _rule_ids(v), "proxy 写成精确 → proxy_claimed_exact")

    # --- 规则 7：external 完整性 + 来源分级 ---
    ref_ext = CitationRef(ref_type="external", source_snapshot_id="x1")
    c_ext = _claim("c7", "t1", ("q1",), "行业数据", "fact", (ref_ext,))
    res = _result(task_ok, [c_ext, c_q2])
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(unknown={SS.citation_identity(ref_ext)}))
    check("external_missing_date" in _rule_ids(v), "external 缺发布日期 → warning")

    c_ext_core = _claim("c8", "t1", ("q1",), "行业关键结论", "fact", (ref_ext,),
                        impact=("key_financial",))
    res = _result(task_ok, [c_ext_core, c_q2])
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(grades={"x1": "D"}))
    check("d_source_sole_basis" in _rule_ids(v), "D 级来源唯一支撑核心结论 → rework")
    v = RE.evaluate_section(res, task_ok,
                            citation_authority=_Authority(grades={"x1": "C"}))
    check("single_c_source" in _rule_ids(v), "C 级来源唯一支撑核心结论 → warning")

    # --- 规则 8：未发现/不存在措辞 ---
    c_unfound = _claim("c9", "t1", ("q1",), "未发现重大诉讼", "fact", (ref_ok,))
    res = _result(task_ok, [c_unfound, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("unfound_claimed_absent" in _rule_ids(v), "「未发现」写成事实 → unfound_claimed_absent")

    c_absent = _claim("c10", "t1", ("q1",), "不存在重大诉讼", "fact", (ref_ok,))
    u_nf = SS.SectionUnresolved(unresolved_id="ur_nf", section_id="financial",
                                topic_id="t1", question_id="q1",
                                state="NOT_FOUND_AFTER_SEARCH", reason_code="write_not_found",
                                detail="未检索到")
    res = _result(task_ok, [c_absent, c_q2], unresolved=[u_nf], status="COMPLETED_WITH_GAPS")
    v = RE.evaluate_section(res, task_ok)
    check("absence_contradicts_gap" in _rule_ids(v), "「不存在」与 NOT_FOUND 缺口矛盾 → absence_contradicts_gap")

    # --- 规则 9：claim 类型与内容 ---
    c_calc = _claim("c11", "t1", ("q1",), "流动比率良好", "calculation", (ref_ext,))
    res = _result(task_ok, [c_calc, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("calculation_missing_structured" in _rule_ids(v),
          "calculation 无 structured → calculation_missing_structured")

    c_hedged = _claim("c12", "t1", ("q1",), "预计未来营收增长", "fact", (ref_ok,))
    res = _result(task_ok, [c_hedged, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("fact_hedged" in _rule_ids(v), "fact 含研判措辞 → fact_hedged")

    # --- 规则 10：维度一致 ---
    ref_s2 = _struct_ref(sid="S2", item="TOTAL_ASSETS")
    c_mix = _claim("c13", "t1", ("q1",), "总资产对比", "fact", (ref_ok, ref_s2))
    res = _result(task_ok, [c_mix, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("dimension_mismatch" in _rule_ids(v), "混用快照 → dimension_mismatch")

    # --- 规则 11：空壳 ---
    res = _result(task_ok, [], markdown="")
    v = RE.evaluate_section(res, task_ok)
    check(v.blocking and "empty_section" in _rule_ids(v), "空壳章节 → blocking empty_section")

    # --- 规则 12：自创授信方案 ---
    c_scheme = _claim("c14", "t1", ("q1",), "建议授信额度 3 亿元，期限 3 年", "fact", ())
    res = _result(task_ok, [c_scheme, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("self_invented_scheme" in _rule_ids(v), "无引用建议 → self_invented_scheme")

    c_existing = _claim("c15", "t1", ("q1",), "公司现有授信额度 20 亿元", "fact", (ref_ok,))
    res = _result(task_ok, [c_existing, c_q2])
    v = RE.evaluate_section(res, task_ok)
    check("self_invented_scheme" not in _rule_ids(v), "存量事实豁免（有引用 + 现有措辞）")

    # --- RulesVerdict 契约：幂等 + 三档 ---
    v1 = RE.evaluate_section(_result(task_ok, [c_nocite, c_q2]), task_ok)
    v2 = RE.evaluate_section(_result(task_ok, [c_nocite, c_q2]), task_ok)
    check({i.issue_id for i in v1.issues} == {i.issue_id for i in v2.issues},
          "相同输入 issue_id 幂等")
    check(any(t.target_kind == "claim" and t.target_ref == "c4" for t in v1.rework_targets),
          "rework target 指向 claim c4")
    check(v1.summary["rework_target_count"] == len(v1.rework_targets),
          "summary rework_target_count 一致")

    # warning-only 不阻断、不返工
    task_w = _task(aspects=("流动比率",))
    v = RE.evaluate_section(_result(task_w, [c_ok, c_q2]), task_w)
    check(v.rules_passed and not v.blocking and not v.rework_targets,
          "仅 warning 时 rules_passed=True 且无 blocking/rework")

    return _results


if __name__ == "__main__":
    import json
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
