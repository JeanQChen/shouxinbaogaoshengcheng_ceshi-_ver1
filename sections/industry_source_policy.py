"""Phase 4 Batch C — 行业 A/B/C/D 来源规则（确定性 policy，无 LLM、无 I/O）。

任务书关闭前定点修复三：落实行业来源分级规则（复用 P3-B02 的 A/B/C/D 定义）。

规则（通用，不按 case_id / 公司硬编码）：
- A/B 各自独立支撑普通事实；
- 关键行业结论（规模 / 份额 / 重大风险 / 重大变化）需 ≥1 直接 A/B，或 ≥2 个
  相互独立且一致的 C；
- 单一 C 只能形成有限非关键陈述；
- D 不得作为关键结论唯一依据；
- published_at 未知不得支撑强时点结论（即使 A/B）；
- 来源不足（单一 C / 仅 D / 无独立来源）→ 从正式研究结论**移除**该 Claim +
  生成 unresolved（不重写事实文本）；强时点且日期未知 → 同样移除 + 显式 unresolved。

「独立」按 canonical domain / 来源身份判定：同一网站两篇文章 ≠ 两个独立 C。

CLI: python -m sections.industry_source_policy --self-check
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from external_v2.schema import _extract_host

logger = logging.getLogger("sections.industry_source_policy")

# 关键行业结论主题（规模/份额/重大风险/重大变化）—— 通用契约 topic_id，非公司特定。
KEY_INDUSTRY_TOPICS = (
    "industry_scale_cycle",        # 规模 / 增速 / 周期
    "industry_position",           # 份额 / 行业地位
    "industry_competition",        # 集中度 / 份额
    "industry_risk_transmission",  # 重大风险传导
    "industry_supply_demand",      # 供需/价格重大变化
)

# freshness_policy → 时效窗口天数（强时点 eligible 过滤用；未知 policy 不过度过滤）。
_FRESHNESS_WINDOW_DAYS = {
    "near_3m": 90,
    "near_6m": 180,
    "near_1y": 365,
    "near_2y": 730,
    "near_3y": 1095,
}


def canonical_domain(url: str) -> str:
    """来源规范身份 = canonical domain（小写 host，去端口/协议）。"""
    return _extract_host(url or "").lower()


@dataclass(frozen=True)
class CitedSource:
    """一条被正式引用的外部来源（用于纯政策判定）。"""

    source_grade: str
    canonical_url: str
    published_at: str | None


@dataclass(frozen=True)
class IndustrySourceAssessment:
    """行业来源充分性判定结果。"""

    grades: dict[str, int]                 # {grade: count}（未知级别记为 "unknown"）
    independent_domains: tuple[str, ...]   # 去重后的 canonical domain（升序）
    independent_c_count: int               # 相互独立的 C 级来源数（按 domain 去重）
    has_ab: bool                           # 是否 ≥1 个 A 或 B
    d_only: bool                           # 全部来源均为 D/unknown
    single_c_only: bool                    # 仅单一 C（无 A/B、无 D）
    unknown_date_count: int                # published_at 未知的来源数
    key_conclusion_supported: bool         # ≥1 A/B 或 ≥2 独立 C
    key_conclusion_reason: str

    def supports_strong_timepoint(self) -> bool:
        """是否存在 published_at 未知的来源（未知日期不得支撑强时点结论）。"""
        return self.unknown_date_count == 0


def assess_industry_sources(sources) -> IndustrySourceAssessment:
    """对一批被引用的外部来源做充分性判定（纯函数）。

    ``sources`` 为 iterable，元素需暴露 ``source_grade`` / ``canonical_url`` /
    ``published_at``（或为 ``CitedSource``）。
    """
    grades: dict[str, int] = {}
    domains_by_grade: dict[str, set[str]] = {}
    unknown_date = 0
    for s in sources:
        grade = (getattr(s, "source_grade", None) or "unknown")
        grades[grade] = grades.get(grade, 0) + 1
        dom = canonical_domain(getattr(s, "canonical_url", "") or "")
        if dom:
            domains_by_grade.setdefault(grade, set()).add(dom)
        if getattr(s, "published_at", None) is None:
            unknown_date += 1

    independent_domains = tuple(sorted(
        {d for gd in domains_by_grade.values() for d in gd}))
    independent_c = len(domains_by_grade.get("C", ()))
    has_ab = bool(grades.get("A", 0) or grades.get("B", 0))

    non_d = sum(v for k, v in grades.items() if k not in ("D", "unknown"))
    d_only = bool(grades) and non_d == 0
    single_c_only = (
        not has_ab and independent_c == 1
        and grades.get("C", 0) > 0
        and not grades.get("D", 0) and not grades.get("unknown", 0)
    )

    key_supported = has_ab or independent_c >= 2
    if has_ab:
        reason = "has_ab"
    elif independent_c >= 2:
        reason = "two_independent_c"
    elif single_c_only:
        reason = "single_c_only"
    elif d_only:
        reason = "d_only"
    else:
        reason = "insufficient_independent_sources"

    return IndustrySourceAssessment(
        grades=grades, independent_domains=independent_domains,
        independent_c_count=independent_c, has_ab=has_ab, d_only=d_only,
        single_c_only=single_c_only, unknown_date_count=unknown_date,
        key_conclusion_supported=key_supported, key_conclusion_reason=reason)


def _cited_sources_from_labels(claims, external_labels) -> list[CitedSource]:
    """从章节 claims + 外部快照标签还原被引用来源（含 canonical_url / grade / 日期）。"""
    seen: dict[str, CitedSource] = {}
    for c in claims:
        for ref in c.citation_refs:
            if ref.ref_type != "external" or not ref.source_snapshot_id:
                continue
            sid = ref.source_snapshot_id
            if sid in seen:
                continue
            lab = external_labels.get(sid, {})
            seen[sid] = CitedSource(
                source_grade=lab.get("source_grade") or "unknown",
                canonical_url=lab.get("canonical_url") or "",
                published_at=lab.get("published_at"),
            )
    return list(seen.values())


def _claim_sources(c, external_labels) -> list[CitedSource]:
    """单条 claim 实际引用的外部来源（含 canonical_url / grade / 发布日期）。"""
    out: list[CitedSource] = []
    seen: set[str] = set()
    for ref in c.citation_refs:
        if ref.ref_type != "external" or not ref.source_snapshot_id:
            continue
        sid = ref.source_snapshot_id
        if sid in seen:
            continue
        seen.add(sid)
        lab = external_labels.get(sid, {})
        out.append(CitedSource(
            source_grade=lab.get("source_grade") or "unknown",
            canonical_url=lab.get("canonical_url") or "",
            published_at=lab.get("published_at"),
        ))
    return out


def _is_strong_timepoint_question(q) -> bool:
    """强时点问题：显式 freshness_policy 或 Contract 要求 published_at 字段。

    对应「发布日期未知不得支撑强时点结论」—— 强时点信号来自
    InformationNeed.metadata.freshness_policy / time_scope(report_as_of) /
    Contract evidence requirement（required_fields 含 published_at）。
    """
    for er in q.evidence_requirements:
        if er.get("freshness_policy"):
            return True
        if "published_at" in (er.get("required_fields") or []):
            return True
    return False


def _claim_is_strong_timepoint(c, question_map: dict) -> bool:
    return any(_is_strong_timepoint_question(question_map[qid])
               for qid in c.question_ids if qid in question_map)


def _iso_date(s: str | None) -> date | None:
    """解析 YYYY-MM-DD 前缀为 date；无法解析返回 None。"""
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def _strong_timepoint_scope(c, question_map: dict, context) -> tuple[str | None, str | None]:
    """返回 claim 的 (freshness_policy, report_as_of)（强时点 eligible 过滤参数）。"""
    freshness: str | None = None
    for qid in c.question_ids:
        q = question_map.get(qid)
        if q is None:
            continue
        for er in q.evidence_requirements:
            fp = er.get("freshness_policy")
            if fp:
                freshness = fp
                break
        if freshness:
            break
    report_as_of = getattr(context, "report_as_of", None) if context is not None else None
    return freshness, report_as_of


def _strong_timepoint_eligible_sources(cited, *, freshness_policy: str | None,
                                       report_as_of: str | None) -> list[CitedSource]:
    """强时点 claim 的 eligible 来源：published_at 已知 + 不晚于 report_as_of + 满足时效窗口。

    日期未知来源不得为强时点结论贡献 A/B、C 数量或独立来源门槛。
    """
    eligible: list[CitedSource] = []
    rd = _iso_date(report_as_of) if report_as_of else None
    days = _FRESHNESS_WINDOW_DAYS.get(freshness_policy) if freshness_policy else None
    for s in cited:
        if s.published_at is None:
            continue
        pd = _iso_date(s.published_at)
        if pd is None:
            continue  # 日期不可解析 → 保守视为不 eligible
        if rd is not None:
            if pd > rd:
                continue  # 未来日期不可支撑时点结论
            if days is not None and (rd - pd).days > days:
                continue  # 超出时效窗口
        eligible.append(s)
    return eligible


def _removal_detail(c, ca: IndustrySourceAssessment, reason: str) -> str:
    if reason == "strong_timepoint_published_at_unknown":
        return ("强时点结论被移除：所有被引外部来源发布日期未知"
                f"（来源分级={ca.grades}），即使 A/B 级也不得支撑强时点结论")
    if reason == "strong_timepoint_source_insufficient":
        return ("强时点结论被移除：已知日期来源经时效过滤后等级仍不足"
                "（需 ≥1 直接 A/B 级或 ≥2 相互独立且一致的 C 级；日期未知来源不计数）；"
                f"当前判定={ca.key_conclusion_reason}，来源分级={ca.grades}")
    return ("关键行业结论（规模/份额/重大风险/重大变化）来源不足被移除："
            "需 ≥1 直接 A/B 级来源或 ≥2 相互独立且一致的 C 级来源；"
            f"当前判定={ca.key_conclusion_reason}，来源分级={ca.grades}")


def apply_industry_source_policy(claims, external_labels, task, context,
                                 *, section_id: str = "industry"):
    """行业来源 policy 落地：返回 (processed_claims, extra_unresolved, assessment_dict)。

    逐 claim 按实际引用判定（非章节整体），真正约束正式 Claim：
    - 强时点 claim（freshness_policy / required published_at）先按 eligible 来源
      （published_at 已知且满足 report_as_of / 时效窗口）重新计算充分性：
      全部来源日期未知 → 移除 + strong_timepoint_published_at_unknown（即使 A/B）；
      存在已知日期来源但 eligible 等级不足 → 移除 + strong_timepoint_source_insufficient。
      日期未知来源不得为强时点结论贡献 A/B、C 数量或独立来源门槛。
    - 关键行业结论 claim（KEY_INDUSTRY_TOPICS）来源不足（无 A/B、<2 独立 C、单一 C、
      仅 D）→ 移除 + insufficient_industry_sources（单一 C 不升级为无限正式事实）。
    - 其余 claim（非关键主题 / 非强时点 / 来源充分 / 无外部引用）→ 保留。
    移除的 claim 不再进入研究结论，并生成显式 unresolved（不重写事实文本）。
    """
    from sections import schema as SS

    question_map = {q.question_id: q for q in (task.questions or ())}
    kept: list = []
    extra: list[SS.SectionUnresolved] = []

    # 章节级汇总诊断（供 verification / 报告，非判定依据）。
    all_sources = _cited_sources_from_labels(claims, external_labels)
    assessment = assess_industry_sources(all_sources)
    removed_key = 0
    removed_strong = 0

    for c in claims:
        external_refs = [r for r in c.citation_refs
                         if r.ref_type == "external" and r.source_snapshot_id]
        if not external_refs:
            kept.append(c)  # 无外部来源的 claim 不属本 policy 约束
            continue

        cited = _claim_sources(c, external_labels)
        ca = assess_industry_sources(cited)
        is_key = c.topic_id in KEY_INDUSTRY_TOPICS
        is_strong = _claim_is_strong_timepoint(c, question_map)

        remove_reason: str | None = None
        detail_ca = ca
        if is_strong:
            # 强时点：仅用 eligible（日期已知且满足时效窗口）来源重新计算充分性。
            freshness, report_as_of = _strong_timepoint_scope(c, question_map, context)
            eligible = _strong_timepoint_eligible_sources(
                cited, freshness_policy=freshness, report_as_of=report_as_of)
            ca_eligible = assess_industry_sources(eligible)
            if all(s.published_at is None for s in cited):
                remove_reason = "strong_timepoint_published_at_unknown"
            elif not ca_eligible.key_conclusion_supported:
                remove_reason = "strong_timepoint_source_insufficient"
                detail_ca = ca_eligible
            if remove_reason is not None:
                removed_strong += 1
        elif is_key and not ca.key_conclusion_supported:
            remove_reason = "insufficient_industry_sources"
            removed_key += 1

        if remove_reason is None:
            kept.append(c)
            continue

        qid = c.question_ids[0] if c.question_ids else None
        uid = "ur_" + SS.sha256_json([
            section_id, c.topic_id, qid, remove_reason, c.claim_id,
            sorted(detail_ca.grades.items())])[:24]
        extra.append(SS.SectionUnresolved(
            unresolved_id=uid, section_id=section_id, topic_id=c.topic_id,
            question_id=qid, state="NOT_FOUND_AFTER_SEARCH",
            reason_code=remove_reason, detail=_removal_detail(c, detail_ca, remove_reason),
            impact_scope=tuple(c.impact_scope), blocking_effects=(),
            attempted_sources=tuple(sorted(
                {s.canonical_url for s in cited if s.canonical_url}))))

    assessment_dict = {
        "grades": assessment.grades,
        "independent_c_count": assessment.independent_c_count,
        "has_ab": assessment.has_ab,
        "d_only": assessment.d_only,
        "single_c_only": assessment.single_c_only,
        "unknown_date_count": assessment.unknown_date_count,
        "key_conclusion_supported": assessment.key_conclusion_supported,
        "key_conclusion_reason": assessment.key_conclusion_reason,
        "removed_key_claims": removed_key,
        "removed_strong_timepoint_claims": removed_strong,
    }
    return tuple(kept), extra, assessment_dict


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    ab = assess_industry_sources([CitedSource("A", "https://stats.gov.cn/a", "2025-01-01")])
    two_c = assess_industry_sources([
        CitedSource("C", "https://eastmoney.com/a", "2025-01-01"),
        CitedSource("C", "https://thepaper.cn/b", "2025-02-01"),
    ])
    same_domain_c = assess_industry_sources([
        CitedSource("C", "https://eastmoney.com/a", "2025-01-01"),
        CitedSource("C", "https://eastmoney.com/b", "2025-02-01"),
    ])
    single_c = assess_industry_sources([CitedSource("C", "https://eastmoney.com/a", "2025-01-01")])
    d_only = assess_industry_sources([CitedSource("D", "https://unknown.example/a", None)])
    unknown_date = assess_industry_sources([CitedSource("A", "https://stats.gov.cn/a", None)])

    # 强时点判定 + 逐 claim policy 落地（移除/保留）冒烟。
    from sections import schema as SS
    from harness import schema as HS

    class _Q:
        def __init__(self, qid, freshness=None, required_fields=()):
            self.question_id = qid
            self.evidence_requirements = [
                {"freshness_policy": freshness, "required_fields": list(required_fields)}]

    class _Task:
        def __init__(self, questions):
            self.questions = questions

    def _mk_claim(claim_id, topic_id, qid, sids):
        return SS.SectionClaim(
            claim_id=claim_id, section_id="industry", topic_id=topic_id,
            question_ids=(qid,), text="行业规模约 X", claim_type="fact",
            citation_refs=tuple(HS.CitationRef(ref_type="external", source_snapshot_id=s)
                                for s in sids))

    labels = {
        "s_a": {"source_grade": "A", "canonical_url": "https://stats.gov.cn/a",
                "published_at": "2025-01-01"},
        "s_b": {"source_grade": "B", "canonical_url": "https://ndrc.gov.cn/b",
                "published_at": "2025-01-01"},
        "s_c1": {"source_grade": "C", "canonical_url": "https://eastmoney.com/a",
                 "published_at": "2025-01-01"},
        "s_c2": {"source_grade": "C", "canonical_url": "https://thepaper.cn/b",
                 "published_at": "2025-01-01"},
        "s_d": {"source_grade": "D", "canonical_url": "https://unknown.example/a",
                "published_at": "2025-01-01"},
        "s_a_nodate": {"source_grade": "A", "canonical_url": "https://stats.gov.cn/a",
                       "published_at": None},
        "s_c_nodate": {"source_grade": "C", "canonical_url": "https://eastmoney.com/c",
                       "published_at": None},
    }
    task_key = _Task([_Q("q_key")])
    task_strong = _Task([_Q("q_strong", freshness="near_1y")])

    kept_single, extra_single, _ = apply_industry_source_policy(
        [_mk_claim("c1", "industry_scale_cycle", "q_key", ["s_c1"])], labels, task_key, None)
    kept_ab, extra_ab, _ = apply_industry_source_policy(
        [_mk_claim("c2", "industry_scale_cycle", "q_key", ["s_a"])], labels, task_key, None)
    kept_strong, extra_strong, _ = apply_industry_source_policy(
        [_mk_claim("c3", "industry_scale_cycle", "q_strong", ["s_a_nodate"])],
        labels, task_strong, None)

    # 强时点混合日期来源：未知日期来源不得贡献 A/B/C 数量或独立来源门槛。
    kept_na_d, extra_na_d, _ = apply_industry_source_policy(
        [_mk_claim("m1", "industry_scale_cycle", "q_strong", ["s_a_nodate", "s_d"])],
        labels, task_strong, None)
    kept_na_c, extra_na_c, _ = apply_industry_source_policy(
        [_mk_claim("m2", "industry_scale_cycle", "q_strong", ["s_a_nodate", "s_c1"])],
        labels, task_strong, None)
    kept_na_b, extra_na_b, _ = apply_industry_source_policy(
        [_mk_claim("m3", "industry_scale_cycle", "q_strong", ["s_a_nodate", "s_b"])],
        labels, task_strong, None)
    kept_cc, extra_cc, _ = apply_industry_source_policy(
        [_mk_claim("m4", "industry_scale_cycle", "q_strong", ["s_c1", "s_c2"])],
        labels, task_strong, None)
    kept_cnc, extra_cnc, _ = apply_industry_source_policy(
        [_mk_claim("m5", "industry_scale_cycle", "q_strong", ["s_c_nodate", "s_c1"])],
        labels, task_strong, None)

    return {
        "ab_key_supported": ab.key_conclusion_supported,
        "two_independent_c_key_supported": two_c.key_conclusion_supported,
        "two_independent_c_count": two_c.independent_c_count,
        "same_domain_two_c_independent_count": same_domain_c.independent_c_count,
        "same_domain_two_c_key_supported": same_domain_c.key_conclusion_supported,
        "single_c_key_supported": single_c.key_conclusion_supported,
        "single_c_only_flag": single_c.single_c_only,
        "d_only_key_supported": d_only.key_conclusion_supported,
        "d_only_flag": d_only.d_only,
        "unknown_date_supports_strong_timepoint": unknown_date.supports_strong_timepoint(),
        "strong_timepoint_freshness": _is_strong_timepoint_question(
            _Q("x", freshness="near_1y")),
        "strong_timepoint_published_field": _is_strong_timepoint_question(
            _Q("x", required_fields=["published_at"])),
        "not_strong_timepoint": not _is_strong_timepoint_question(_Q("x")),
        "policy_single_c_removed": len(kept_single) == 0 and len(extra_single) == 1,
        "policy_ab_kept": len(kept_ab) == 1 and len(extra_ab) == 0,
        "policy_strong_unknown_date_removed": (
            len(kept_strong) == 0 and len(extra_strong) == 1
            and extra_strong[0].reason_code == "strong_timepoint_published_at_unknown"),
        "policy_mixed_unknown_a_known_d_removed": (
            len(kept_na_d) == 0 and extra_na_d
            and extra_na_d[0].reason_code == "strong_timepoint_source_insufficient"),
        "policy_mixed_unknown_a_known_single_c_removed": (
            len(kept_na_c) == 0 and extra_na_c
            and extra_na_c[0].reason_code == "strong_timepoint_source_insufficient"),
        "policy_mixed_unknown_a_known_b_kept": (
            len(kept_na_b) == 1 and len(extra_na_b) == 0),
        "policy_mixed_two_known_c_kept": (
            len(kept_cc) == 1 and len(extra_cc) == 0),
        "policy_mixed_unknown_c_known_c_not_two_c": (
            len(kept_cnc) == 0 and extra_cnc
            and extra_cnc[0].reason_code == "strong_timepoint_source_insufficient"),
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.industry_source_policy",
        description="行业 A/B/C/D 来源规则自检（纯函数，不读库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
