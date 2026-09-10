"""Phase 4 Batch D — 章节 Rules Evaluator（12 项确定性规则，无 LLM）。

任务书 §13.1 的 12 项规则，在 LLM Evaluator 之前做确定性预检。原则（修订四/十一）：
- Rules 失败不能被 LLM 覆盖：任一 blocking/rework 级 issue → rules_passed=False，LLM 不得
  将其改写为 PASS。
- 财务数字复核复用现有安全链（Structured Citation → FinancialFactPack → display 精确等价），
  **不新建正则数字解析器**；marker/裸数字安全链在 Worker 侧已强制，这里只做 display 回查。
- 自创授信方案联合判定（不是关键词封杀）：recommendation 动词 + 方案维度词共现，且
  排除「有引用的存量事实」措辞；「额度/期限/评级/担保/增信」作为存量事实出现不封杀。

严重度三档（决定后续状态机分支）：
- ``blocking``：不可返工 → BLOCKED（CONFLICT / WAITING_HUMAN / 阻断后果不一致 / 空壳）。
- ``rework``：  可定向返工 → REWORK（附 ReworkTarget）。
- ``warning``： 非阻断缺口 → 交由 LLM Evaluator 语义复核 / PASS_WITH_GAPS。

本模块纯确定性、可注入、无 I/O：citation_authority（``.validate(ref)`` + 可选
``.external_get``）与 fact_pack（``.facts`` 元素含 kind/code/period/display/status）均为
注入对象，便于离线合成测试；不注入时相应规则跳过并在 summary 记 ``*_available: False``。

CLI: python -m sections.rules_evaluator --self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from sections import schema as SS
from sections.citation_authority import CitationVerdict

logger = logging.getLogger("sections.rules_evaluator")

# 版本常量（变更必须递增；进入 evaluation_id 派生）。
RULES_VERSION = "p4-rules-v1"

# 严重度三档。
SEVERITY_BLOCKING = "blocking"
SEVERITY_REWORK = "rework"
SEVERITY_WARNING = "warning"

# 规则 8：「未检索到」不得写成「不存在」。
_NOT_FOUND_PHRASES = ("未发现", "未检索到", "未找到", "未查到", "查无")
_ABSENCE_PHRASES = ("不存在", "未发生")

# 规则 9：fact claim 不应含研判措辞（→ 应降 inference）。
_HEDGE_PHRASES = ("预计", "推测", "预测", "估计", "大概率", "或可", "倾向认为", "判断为")

# 规则 12：自创授信方案联合判定（动词 + 维度词共现；存量事实豁免）。
_RECOMMENDATION_VERBS = ("建议", "拟", "应予", "应授", "应给予")
_SCHEME_DIMENSIONS = ("授信额度", "授信", "额度", "期限", "担保", "增信", "评级", "利率", "抵押率")
_EXISTING_FRAMING = ("现有", "目前", "存量", "截至", "当前", "已获批", "已取得")


@dataclass(frozen=True)
class RulesVerdict:
    """Rules 预检结论（不可变）。rules_passed = 无 blocking 且无 rework target。"""

    rules_passed: bool
    blocking: bool
    issues: tuple[SS.SectionIssue, ...]
    rework_targets: tuple[SS.ReworkTarget, ...]
    summary: dict


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _rework_target(kind: str, ref: str, reason: str) -> SS.ReworkTarget:
    return SS.ReworkTarget(
        target_id=SS.derive_rework_target_id(kind, ref, reason),
        target_kind=kind, target_ref=ref, reason=reason)


def _add(acc: list, rule_id: str, severity: str, location: str, detail: str,
         action: str = "", target: SS.ReworkTarget | None = None) -> None:
    """追加一条 issue（及可选 rework target），acc 元素为 (issue, target|None, is_blocking)。"""
    issue = SS.SectionIssue(
        issue_id=SS.derive_issue_id(rule_id, location, detail, severity),
        rule_id=rule_id, severity=severity, location=location,
        detail=detail, suggested_action=action)
    acc.append((issue, target, severity == SEVERITY_BLOCKING))


# ---------------------------------------------------------------------------
# 规则 3 — 阻断后果一致性（blocking）
# ---------------------------------------------------------------------------

def _rule_blocking(result: SS.SectionResult, task, acc: list) -> None:
    qmap = {q.question_id: q for q in task.questions}
    has_section_blocking = False
    for u in result.unresolved:
        q = qmap.get(u.question_id) if u.question_id else None
        expected = tuple(q.blocking_policy) if q else ()
        if tuple(u.blocking_effects) != expected:
            _add(acc, "blocking_policy_mismatch", SEVERITY_BLOCKING,
                 f"unresolved:{u.unresolved_id}",
                 f"unresolved blocking_effects {tuple(u.blocking_effects)} 与问题 "
                 f"blocking_policy {expected} 不一致")
        if u.state == "CONFLICT":
            _add(acc, "conflict_unresolved", SEVERITY_BLOCKING,
                 f"unresolved:{u.unresolved_id}",
                 f"存在 CONFLICT 未解决项（多来源/口径冲突）：{u.detail}")
        if u.state == "WAITING_HUMAN":
            _add(acc, "waiting_human", SEVERITY_BLOCKING,
                 f"unresolved:{u.unresolved_id}",
                 f"问题需客户经理确认（WAITING_HUMAN），章节不能自动完成：{u.detail}")
        if "SECTION_BLOCKED" in u.blocking_effects or "JOB_BLOCKED" in u.blocking_effects:
            has_section_blocking = True
    if has_section_blocking and result.status != "SECTION_BLOCKED":
        _add(acc, "status_not_blocked", SEVERITY_BLOCKING, "section",
             f"存在 SECTION_BLOCKED/JOB_BLOCKED 未解决项但章节 status={result.status!r}，"
             f"应为 SECTION_BLOCKED")


# ---------------------------------------------------------------------------
# 规则 1 — 契约覆盖（topic/question，rework）
# ---------------------------------------------------------------------------

def _rule_coverage(result: SS.SectionResult, task, acc: list) -> None:
    covered_topics = {c.topic_id for c in result.claims}
    unresolved_topics = {u.topic_id for u in result.unresolved}
    for tid in task.topic_ids:
        if tid not in covered_topics and tid not in unresolved_topics:
            _add(acc, "coverage_missing_topic", SEVERITY_REWORK, f"topic:{tid}",
                 f"主题 {tid} 无任何 claim 且无 unresolved",
                 target=_rework_target("topic", tid, "missing_topic"))
    covered_questions = {qid for c in result.claims for qid in c.question_ids}
    unresolved_questions = {u.question_id for u in result.unresolved if u.question_id}
    for q in task.questions:
        if q.question_id in covered_questions or q.question_id in unresolved_questions:
            continue
        _add(acc, "coverage_missing_question", SEVERITY_REWORK,
             f"question:{q.question_id}",
             f"问题 {q.question_id} 未被 claim 覆盖且无 unresolved",
             target=_rework_target("question", q.question_id, "missing_question"))


# ---------------------------------------------------------------------------
# 规则 2 — required aspect 覆盖（warning：字面可追溯，语义由 LLM Evaluator）
# ---------------------------------------------------------------------------

def _rule_aspects(result: SS.SectionResult, task, acc: list) -> None:
    by_question: dict[str, list[str]] = {}
    for c in result.claims:
        for qid in c.question_ids:
            by_question.setdefault(qid, []).append(c.text)
    for q in task.questions:
        if not q.required_aspects or q.question_id not in by_question:
            continue
        blob = " ".join(by_question[q.question_id])
        for aspect in q.required_aspects:
            if aspect and aspect not in blob:
                _add(acc, "aspect_uncovered", SEVERITY_WARNING,
                     f"question:{q.question_id}",
                     f"必需方面「{aspect}」未在覆盖 claim 正文中字面出现（语义覆盖由 LLM Evaluator 复核）")


# ---------------------------------------------------------------------------
# 规则 4 — 关键 claim 有 citation（rework；结构校验器已兜底，此处纵深）
# ---------------------------------------------------------------------------

def _rule_citation(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        if (c.claim_type in ("fact", "calculation")
                and not c.citation_refs and not c.derived_from_claim_ids):
            _add(acc, "claim_missing_citation", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"{c.claim_type} claim 无 citation 且无 derived_from_claim_ids",
                 target=_rework_target("claim", c.claim_id, "missing_citation"))


# ---------------------------------------------------------------------------
# 规则 5 — 引用可解析 + 锁定版本（rework；复用 citation_authority）
# ---------------------------------------------------------------------------

def _rule_resolvability(result: SS.SectionResult, acc: list, verdict_of: Callable) -> None:
    warned_types: set[str] = set()
    for c in result.claims:
        for ref in c.citation_refs:
            v = verdict_of(ref)
            if v.valid:
                continue
            reason = v.reason or "unknown"
            if reason == "authority_unavailable":
                if ref.ref_type not in warned_types:
                    warned_types.add(ref.ref_type)
                    _add(acc, "citation_authority_unavailable", SEVERITY_WARNING,
                         f"ref_type:{ref.ref_type}",
                         f"引用类型 {ref.ref_type} 的权威校验不可用（未提供对应库），"
                         f"由 LLM Evaluator 语义复核")
                continue
            _add(acc, "citation_unresolvable", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"引用不可解析（{reason}）：{SS.citation_identity(ref)}",
                 target=_rework_target("claim", c.claim_id, f"citation:{reason}"))


# ---------------------------------------------------------------------------
# 规则 6 — 财务数值 == 权威值（rework；复用 fact_pack 安全链，不新建解析器）
# ---------------------------------------------------------------------------

def _rule_financial(result: SS.SectionResult, acc: list, fact_pack) -> None:
    if fact_pack is None:
        return
    by_item: dict = {}
    by_metric: dict = {}
    for f in fact_pack.facts:
        if getattr(f, "kind", None) == "fact":
            by_item[(f.code, f.period)] = f
        else:
            by_metric[(f.code, f.period)] = f
    for c in result.claims:
        for ref in c.citation_refs:
            if ref.ref_type != "structured":
                continue
            fact = None
            if ref.formula_id:
                fact = by_metric.get((ref.formula_id, ref.period))
            elif ref.item_code:
                fact = by_item.get((ref.item_code, ref.period))
            if fact is None:
                _add(acc, "financial_unresolvable_value", SEVERITY_REWORK,
                     f"claim:{c.claim_id}",
                     f"structured 引用无法解析到权威值：{SS.citation_identity(ref)}",
                     target=_rework_target("claim", c.claim_id, "unresolvable_value"))
                continue
            if fact.display and fact.display not in c.text:
                _add(acc, "financial_value_mismatch", SEVERITY_REWORK,
                     f"claim:{c.claim_id}",
                     f"claim 正文缺少权威 display「{fact.display}」（{fact.code}@{fact.period}）",
                     target=_rework_target("claim", c.claim_id, "value_mismatch"))
            if getattr(fact, "status", None) == "CALCULATED_PROXY" and _has_exactness(c.text):
                _add(acc, "proxy_claimed_exact", SEVERITY_REWORK, f"claim:{c.claim_id}",
                     f"代理口径指标 {fact.code} 被写成精确值",
                     target=_rework_target("claim", c.claim_id, "proxy_as_exact"))


_EXACTNESS_PHRASES = ("精确", "确切", "精确值", "准确", "权威精确")


def _has_exactness(text: str) -> bool:
    return any(p in text for p in _EXACTNESS_PHRASES)


# ---------------------------------------------------------------------------
# 规则 7 — external 引用完整性（warning：日期未知；rework：D 级唯一依据）
# ---------------------------------------------------------------------------

def _rule_external(result: SS.SectionResult, acc: list, verdict_of: Callable,
                   external_get: Callable | None) -> None:
    for c in result.claims:
        ext_refs = [r for r in c.citation_refs if r.ref_type == "external"]
        if not ext_refs:
            continue
        grades: list[str] = []
        for ref in ext_refs:
            v = verdict_of(ref)
            if "published_at_unknown" in v.warnings:
                _add(acc, "external_missing_date", SEVERITY_WARNING, f"claim:{c.claim_id}",
                     f"external 引用缺发布日期，不能支撑强时点结论")
            if external_get is not None:
                snap = external_get(ref.source_snapshot_id)
                g = getattr(snap, "source_grade", None)
                if g:
                    grades.append(g)
        if not grades:
            continue
        if c.impact_scope and all(g == "D" for g in grades):
            _add(acc, "d_source_sole_basis", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"核心结论（impact_scope={list(c.impact_scope)}）仅由 D 级来源支撑，"
                 f"不得作为关键结论唯一依据",
                 target=_rework_target("claim", c.claim_id, "d_source_sole_basis"))
        elif c.impact_scope and all(g == "C" for g in grades):
            _add(acc, "single_c_source", SEVERITY_WARNING, f"claim:{c.claim_id}",
                 f"核心结论仅由 C 级来源支撑，建议补充 A/B 级来源")


# ---------------------------------------------------------------------------
# 规则 8 — 「未发现/不存在」措辞（rework；缺证据 ≠ 事实不存在）
# ---------------------------------------------------------------------------

def _rule_absence(result: SS.SectionResult, acc: list) -> None:
    not_found_by_q: dict[str, set[str]] = {}
    for u in result.unresolved:
        if u.question_id:
            not_found_by_q.setdefault(u.question_id, set()).add(u.state)
    for c in result.claims:
        has_not_found = any(p in c.text for p in _NOT_FOUND_PHRASES)
        has_absence = any(p in c.text for p in _ABSENCE_PHRASES)
        if has_not_found and c.claim_type == "fact":
            _add(acc, "unfound_claimed_absent", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"fact claim 以事实语气断言「未发现/未检索到」，应写诚实缺口或降 inference",
                 target=_rework_target("claim", c.claim_id, "unfound_as_absent"))
        if has_absence:
            for qid in c.question_ids:
                if "NOT_FOUND_AFTER_SEARCH" in not_found_by_q.get(qid, set()):
                    _add(acc, "absence_contradicts_gap", SEVERITY_REWORK,
                         f"claim:{c.claim_id}",
                         f"claim 断言「不存在/未发生」但问题 {qid} 状态为 NOT_FOUND_AFTER_SEARCH"
                         f"（缺证据 ≠ 事实不存在）",
                         target=_rework_target("claim", c.claim_id, "absence_contradicts_gap"))
                    break


# ---------------------------------------------------------------------------
# 规则 9 — claim 类型与内容相符（rework）
# ---------------------------------------------------------------------------

def _rule_claim_type(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        if c.claim_type == "calculation" and not any(
                r.ref_type == "structured" for r in c.citation_refs):
            _add(acc, "calculation_missing_structured", SEVERITY_REWORK,
                 f"claim:{c.claim_id}",
                 f"calculation claim 无 structured 引用（计算值应来自结构化数据）",
                 target=_rework_target("claim", c.claim_id, "calculation_missing_structured"))
        if c.claim_type == "fact":
            for p in _HEDGE_PHRASES:
                if p in c.text:
                    _add(acc, "fact_hedged", SEVERITY_REWORK, f"claim:{c.claim_id}",
                         f"fact claim 含研判措辞「{p}」（应降 inference）",
                         target=_rework_target("claim", c.claim_id, "fact_hedged"))
                    break


# ---------------------------------------------------------------------------
# 规则 10 — 期间/scope/currency/单位一致（rework；scope/currency 由规则 5 权威校验）
# ---------------------------------------------------------------------------

def _rule_dimensions(result: SS.SectionResult, acc: list) -> None:
    for c in result.claims:
        snapshots = {r.snapshot_id for r in c.citation_refs
                     if r.ref_type == "structured" and r.snapshot_id}
        if len(snapshots) > 1:
            _add(acc, "dimension_mismatch", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 混用多个快照 {sorted(snapshots)}（scope/currency/purpose 可能不一致）",
                 target=_rework_target("claim", c.claim_id, "dimension_mismatch"))


# ---------------------------------------------------------------------------
# 规则 11 — 非空标题/内容（blocking：空壳章节；rework：空壳 claim）
# ---------------------------------------------------------------------------

def _rule_emptiness(result: SS.SectionResult, acc: list) -> None:
    if not result.claims and not result.unresolved and not (result.markdown or "").strip():
        _add(acc, "empty_section", SEVERITY_BLOCKING, "section",
             "章节无 claim、无 unresolved、无 markdown（空壳）")
    for c in result.claims:
        if len((c.text or "").strip()) < 2:
            _add(acc, "empty_shell_claim", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 内容为空壳（长度 {len((c.text or '').strip())}）",
                 target=_rework_target("claim", c.claim_id, "empty_shell_claim"))


# ---------------------------------------------------------------------------
# 规则 12 — 自创授信方案（rework；联合判定，非关键词封杀）
# ---------------------------------------------------------------------------

def _is_self_invented_scheme(text: str, has_citation: bool) -> bool:
    if not any(v in text for v in _RECOMMENDATION_VERBS):
        return False
    if not any(d in text for d in _SCHEME_DIMENSIONS):
        return False
    # 存量事实豁免：有引用且措辞为「现有/目前/存量」等既有事实，非新建议。
    if has_citation and any(f in text for f in _EXISTING_FRAMING):
        return False
    return True


def _rule_scheme(result: SS.SectionResult, acc: list, proposed_scheme) -> None:
    for c in result.claims:
        has_citation = bool(c.citation_refs)
        if _is_self_invented_scheme(c.text, has_citation):
            _add(acc, "self_invented_scheme", SEVERITY_REWORK, f"claim:{c.claim_id}",
                 f"claim 含用户未提供的授信建议/评级（Phase 4 不产出 recommendation）",
                 target=_rework_target("claim", c.claim_id, "self_invented_scheme"))


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _cached_validator(citation_authority) -> Callable:
    cache: dict = {}

    def verdict_of(ref) -> CitationVerdict:
        key = SS.citation_identity(ref)
        if key not in cache:
            try:
                cache[key] = citation_authority.validate(ref)
            except Exception as e:  # noqa: BLE001 - 权威查询异常 → fail-closed
                logger.exception("引用权威查询异常（fail-closed）")
                cache[key] = CitationVerdict(ref.ref_type, False,
                                             f"authority_query_failed:{type(e).__name__}")
        return cache[key]

    return verdict_of


def evaluate_section(result: SS.SectionResult, task, *, company_id: str = "",
                     citation_authority=None, fact_pack=None,
                     proposed_scheme=None) -> RulesVerdict:
    """对单个 SectionResult 执行 12 项确定性规则，返回 RulesVerdict。

    - citation_authority：``.validate(ref) -> CitationVerdict``（可选 ``.external_get``）。
    - fact_pack：``.facts`` 元素含 kind/code/period/display/status（财务章节注入）。
    - proposed_scheme：保留上下文（Phase 4 不产出 recommendation，规则 12 不做用户输入豁免）。
    """
    acc: list = []

    _rule_blocking(result, task, acc)
    _rule_coverage(result, task, acc)
    _rule_aspects(result, task, acc)
    _rule_citation(result, acc)
    _rule_financial(result, acc, fact_pack)
    _rule_absence(result, acc)
    _rule_claim_type(result, acc)
    _rule_dimensions(result, acc)
    _rule_emptiness(result, acc)
    _rule_scheme(result, acc, proposed_scheme)

    if citation_authority is not None:
        verdict_of = _cached_validator(citation_authority)
        external_get = getattr(citation_authority, "external_get", None)
        _rule_resolvability(result, acc, verdict_of)
        _rule_external(result, acc, verdict_of, external_get)

    issues = tuple(i for i, _, _ in acc)
    rework_targets = tuple(t for _, t, _ in acc if t is not None)
    blocking = any(b for _, _, b in acc)

    covered_topics = {c.topic_id for c in result.claims}
    covered_questions = {qid for c in result.claims for qid in c.question_ids}
    rule_hits = dict(Counter(i.rule_id for i in issues))

    summary = {
        "rules_version": RULES_VERSION,
        "rules_passed": (not blocking) and (not rework_targets),
        "blocking": blocking,
        "issue_count": len(issues),
        "rework_target_count": len(rework_targets),
        "blocking_issue_count": sum(1 for i in issues if i.severity == SEVERITY_BLOCKING),
        "rework_issue_count": sum(1 for i in issues if i.severity == SEVERITY_REWORK),
        "warning_issue_count": sum(1 for i in issues if i.severity == SEVERITY_WARNING),
        "rule_hits": rule_hits,
        "covered_topics": sorted(covered_topics),
        "uncovered_topics": sorted(set(task.topic_ids) - covered_topics),
        "covered_questions": sorted(covered_questions),
        "uncovered_questions": sorted(q.question_id for q in task.questions
                                      if q.question_id not in covered_questions),
        "citation_authority_available": citation_authority is not None,
        "fact_pack_available": fact_pack is not None,
    }

    return RulesVerdict(
        rules_passed=summary["rules_passed"],
        blocking=blocking,
        issues=issues,
        rework_targets=rework_targets,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，注入假对象，不读库）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    from planning import schema as PS

    def _task(aspects=(), blocking=(), impact=()):
        return PS.SectionTask(
            task_id="task_sc", plan_id="plan_sc", section_id="financial",
            title="财务分析", purpose="p", research_policy="workflow",
            topic_ids=("t1", "t2"),
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

    def _claim(cid, topic_id, qids, text, ctype="fact", refs=()):
        return SS.SectionClaim(claim_id=cid, section_id="financial", topic_id=topic_id,
                               question_ids=qids, text=text, claim_type=ctype,
                               citation_refs=refs)

    def _struct_ref(sid="S1", item="TOTAL_ASSETS", period="2025-12-31"):
        return SS.CitationRef(ref_type="structured", snapshot_id=sid,
                              item_code=item, period=period)

    def _result(task, claims, unresolved=(), status="COMPLETED", markdown="# 财务分析"):
        return SS.SectionResult(
            section_result_id="sr_sc", section_version="secver_sc", task_id=task.task_id,
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
        """可配置假权威：fail 集合 → 不可解析；unknown 集合 → 缺日期警告。"""

        def __init__(self, fail=(), unknown=(), grades=None):
            self._fail = set(fail)
            self._unknown = set(unknown)
            self._grades = grades or {}

        def validate(self, ref):
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

    out: dict = {}
    checks: list[tuple[str, bool, str]] = []

    def check(name, cond, msg=""):
        checks.append((name, bool(cond), msg))
        out[name] = bool(cond)

    # 1) 合法章节通过。
    fact = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "1,234.56万元")
    task_ok = _task()
    ref_ok = _struct_ref(item="TOTAL_ASSETS")
    c_ok = _claim("c1", "t1", ("q1",), "总资产为 1,234.56万元", "fact", (ref_ok,))
    c_q2 = _claim("c2", "t2", ("q2",), "整体财务稳健", "inference", ())
    res_ok = _result(task_ok, [c_ok, c_q2])
    v = evaluate_section(res_ok, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact]))
    check("happy_path_passes", v.rules_passed and not v.blocking,
          str([i.rule_id for i in v.issues]))

    # 2) 缺失 topic → coverage_missing_topic。
    c_q2 = _claim("c2", "t2", ("q2",), "某内容", "fact", (ref_ok,))
    res = _result(task_ok, [c_q2])
    v = evaluate_section(res, task_ok)
    check("missing_topic_detected", any(i.rule_id == "coverage_missing_topic" for i in v.issues))

    # 3) 缺失 question → coverage_missing_question。
    res = _result(task_ok, [c_ok])
    v = evaluate_section(res, task_ok)
    check("missing_question_detected",
          any(i.rule_id == "coverage_missing_question" for i in v.issues))

    # 4) aspect 未字面出现 → aspect_uncovered（warning，不阻断）。
    task_aspect = _task(aspects=("流动比率",))
    res = _result(task_aspect, [c_ok])
    v = evaluate_section(res, task_aspect)
    check("aspect_uncovered_warning",
          any(i.rule_id == "aspect_uncovered" and i.severity == "warning" for i in v.issues))

    # 5) CONFLICT 未解决 → conflict_unresolved（blocking）。
    u_conflict = SS.SectionUnresolved(
        unresolved_id="ur_conf", section_id="financial", topic_id="t1",
        question_id="q1", state="CONFLICT", reason_code="conflict_pause", detail="口径冲突")
    res = _result(task_ok, [c_ok], unresolved=[u_conflict], status="COMPLETED_WITH_GAPS")
    v = evaluate_section(res, task_ok)
    check("conflict_blocks", v.blocking and any(
        i.rule_id == "conflict_unresolved" for i in v.issues))

    # 6) fact 无 citation → claim_missing_citation。
    c_nocite = _claim("c3", "t1", ("q1",), "某事实", "fact", ())
    res = _result(task_ok, [c_nocite])
    v = evaluate_section(res, task_ok)
    check("missing_citation_detected",
          any(i.rule_id == "claim_missing_citation" for i in v.issues))

    # 7) 引用不可解析 → citation_unresolvable。
    res = _result(task_ok, [c_ok])
    v = evaluate_section(res, task_ok,
                         citation_authority=_Authority(fail={SS.citation_identity(ref_ok)}))
    check("unresolvable_citation_detected",
          any(i.rule_id == "citation_unresolvable" for i in v.issues))

    # 8) 财务值不等 → financial_value_mismatch。
    res = _result(task_ok, [c_ok])  # 正文 "1,234.56万元" 与 fact display 不符时用另一 fact
    fact2 = _Fact("fact", "TOTAL_ASSETS", "2025-12-31", "9,999万元")
    v = evaluate_section(res, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact2]))
    check("financial_value_mismatch_detected",
          any(i.rule_id == "financial_value_mismatch" for i in v.issues))

    # 9) proxy 写成精确 → proxy_claimed_exact。
    fact_proxy = _Fact("calculation", "SOLV_CURRENT_RATIO", "2025-12-31", "1.50",
                       status="CALCULATED_PROXY")
    ref_metric = SS.CitationRef(ref_type="structured", snapshot_id="S1",
                                formula_id="SOLV_CURRENT_RATIO", formula_version="v1",
                                period="2025-12-31")
    c_proxy = _claim("c4", "t1", ("q1",), "流动比率精确值为 1.50", "calculation", (ref_metric,))
    res = _result(task_ok, [c_proxy])
    v = evaluate_section(res, task_ok, citation_authority=_Authority(),
                         fact_pack=_Pack([fact_proxy]))
    check("proxy_claimed_exact_detected",
          any(i.rule_id == "proxy_claimed_exact" for i in v.issues))

    # 10) external 缺日期 → external_missing_date（warning）。
    ref_ext = SS.CitationRef(ref_type="external", source_snapshot_id="x1")
    c_ext = _claim("c5", "t1", ("q1",), "行业可比数据", "fact", (ref_ext,))
    res = _result(task_ok, [c_ext])
    v = evaluate_section(res, task_ok,
                         citation_authority=_Authority(unknown={SS.citation_identity(ref_ext)}))
    check("external_missing_date_warning",
          any(i.rule_id == "external_missing_date" and i.severity == "warning"
              for i in v.issues))

    # 11) 「未发现」写成事实 → unfound_claimed_absent。
    c_unfound = _claim("c6", "t1", ("q1",), "未发现重大诉讼", "fact", (ref_ok,))
    res = _result(task_ok, [c_unfound])
    v = evaluate_section(res, task_ok)
    check("unfound_claimed_absent_detected",
          any(i.rule_id == "unfound_claimed_absent" for i in v.issues))

    # 12) calculation 无 structured → calculation_missing_structured。
    c_calc = _claim("c7", "t1", ("q1",), "流动比率良好", "calculation", (ref_ext,))
    res = _result(task_ok, [c_calc])
    v = evaluate_section(res, task_ok)
    check("calculation_missing_structured_detected",
          any(i.rule_id == "calculation_missing_structured" for i in v.issues))

    # 13) fact 含研判 → fact_hedged。
    c_hedged = _claim("c8", "t1", ("q1",), "预计未来营收增长", "fact", (ref_ok,))
    res = _result(task_ok, [c_hedged])
    v = evaluate_section(res, task_ok)
    check("fact_hedged_detected", any(i.rule_id == "fact_hedged" for i in v.issues))

    # 14) 混用快照 → dimension_mismatch。
    ref_s2 = _struct_ref(sid="S2", item="TOTAL_ASSETS")
    c_mix = _claim("c9", "t1", ("q1",), "总资产对比", "fact", (ref_ok, ref_s2))
    res = _result(task_ok, [c_mix])
    v = evaluate_section(res, task_ok)
    check("dimension_mismatch_detected",
          any(i.rule_id == "dimension_mismatch" for i in v.issues))

    # 15) 空壳章节 → empty_section（blocking）。
    res_empty = _result(task_ok, [], markdown="")
    v = evaluate_section(res_empty, task_ok)
    check("empty_section_blocks", v.blocking and any(
        i.rule_id == "empty_section" for i in v.issues))

    # 16) 自创授信方案 → self_invented_scheme；存量事实豁免。
    c_scheme = _claim("c10", "t1", ("q1",), "建议授信额度 3 亿元，期限 3 年", "fact", ())
    res = _result(task_ok, [c_scheme])
    v = evaluate_section(res, task_ok)
    check("self_invented_scheme_detected",
          any(i.rule_id == "self_invented_scheme" for i in v.issues))
    c_existing = _claim("c11", "t1", ("q1",), "公司现有授信额度 20 亿元", "fact", (ref_ok,))
    res = _result(task_ok, [c_existing])
    v = evaluate_section(res, task_ok)
    check("existing_scheme_exempted",
          not any(i.rule_id == "self_invented_scheme" for i in v.issues))

    # 17) WAITING_HUMAN → blocking。
    u_wh = SS.SectionUnresolved(
        unresolved_id="ur_wh", section_id="financial", topic_id="t1",
        question_id="q1", state="WAITING_HUMAN", reason_code="transfer_human",
        detail="需确认", blocking_effects=())
    res = _result(task_ok, [c_ok], unresolved=[u_wh], status="COMPLETED_WITH_GAPS")
    v = evaluate_section(res, task_ok)
    check("waiting_human_blocks", v.blocking and any(
        i.rule_id == "waiting_human" for i in v.issues))

    return out


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sections.rules_evaluator",
        description="Phase 4 章节 Rules Evaluator（12 项确定性规则）自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        result = _self_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        all_ok = all(result.values())
        print("\nself-check:", "PASS" if all_ok else "FAIL")
        return 0 if all_ok else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
