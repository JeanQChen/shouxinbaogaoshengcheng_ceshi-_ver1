"""Contract-derived slice/task 选择 + 漂移校验（纯函数，除契约加载外无 I/O）。

把纵向研究能力重新接入冻结的 Section Contract 与正式 Planner：

    standard_v2.yaml → contracts.loader → ReportJobInput → planning.report_planner
    → ReportPlan → 原始 SectionTask → 选择 topic/question → TopicQueryPlan
    → TopicResearchPack → ChapterWriter/TopicWriter

本模块只做三件事（纯函数，不联网、不读库、不调 LLM）：

1. **选择**：从正式 ``ReportPlan.section_tasks`` 里按 ``section_id`` / ``topic_id`` /
   ``question_id`` 选出契约定义的任务对象，绝不手工重建 PlannedQuestion / SectionTask。
2. **漂移校验**：把选中的 ``SectionTask`` 与契约重新派生的期望任务做规范形比对，
   任何字段漂移 → fail-closed ``CONTRACT_TASK_DRIFT``。
3. **确定性渲染**：期间文字（flow/point-in-time/trend）与表格-正文一致性校验。

CLI: python -m evaluation.contract_slice --self-check
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from contracts import schema as CS
from contracts.loader import load_contracts
from planning import report_planner as planner
from planning import schema as PS

CONTRACT_DRIFT = "CONTRACT_TASK_DRIFT"

# 本 Demo 统一口径说明（期间渲染由 Python 生成，LLM 不得自行使用「报告期」等模糊词）。
# 动态生成见 scope_note()（下方「期间渲染」节），不在此写死任何年份。

# 流量类事实（收入/成本/利润/现金流），按「年度 / 季度区间」渲染。
FLOW_CATEGORIES = ("revenue", "cost", "profit", "cashflow")
# 时点类事实（资产/负债/存货等），按「截至…末」渲染。
POINT_CATEGORIES = ("asset", "liability", "inventory", "equity")

# 契约明确要求禁止在正文渲染中使用的模糊期间词。
FORBIDDEN_PERIOD_WORDS = ("报告期", "本期", "期末", "最新一期", "近年来")


@dataclass(frozen=True)
class ContractDriftError(Exception):
    """契约任务漂移（fail-closed）。"""

    reason: str = CONTRACT_DRIFT
    diffs: tuple[str, ...] = ()

    def __str__(self) -> str:  # pragma: no cover - 异常展示
        return (f"{self.reason}: 任务与正式 Section Contract 不一致 → "
                + "；".join(self.diffs))


# ---------------------------------------------------------------------------
# 规范形 + 指纹（单一实现：复用 planner 公开接口，不复制 Planner 逻辑）
# ---------------------------------------------------------------------------

# 直接引用 planner 的规范形实现（planner.plan 与漂移校验共用同一实现）。
canonical_question_dict = planner.canonical_question
canonical_task_dict = planner.canonical_section_task
task_canonical_fingerprint = planner.section_task_fingerprint
question_canonical_fingerprint = planner.question_fingerprint


# ---------------------------------------------------------------------------
# 正式 Planner 派生（委托 planner.build_section_task，不复制 planner.plan 内层逻辑）
# ---------------------------------------------------------------------------

def derive_expected_task(sec: CS.SectionContract, credit_type: str, *,
                         task_id: str, plan_id: str,
                         contract_version: str,
                         contract_sha256: str) -> PS.SectionTask:
    """按契约重新派生「期望任务」——委托 planner.build_section_task（唯一实现）。

    ``task_id`` 由 ``(plan_id, section_id)`` 确定性派生；调用方传入的 task_id 与
    派生值不一致属来源异常 → fail-closed。期望 Contract 身份（``contract_version`` /
    ``contract_sha256``）由调用方独立提供，不得原样取自待校验 task。
    """
    expected = planner.build_section_task(
        sec, credit_type, plan_id=plan_id,
        dependency_versions={
            "contract_version": contract_version,
            "contract_sha256": contract_sha256,
        })
    if expected.task_id != task_id:
        raise ContractDriftError(
            reason=CONTRACT_DRIFT,
            diffs=(f"task_id 不一致: 派生 {expected.task_id!r} 期望 {task_id!r}",))
    return expected


def validate_task_against_contract(task: PS.SectionTask, sec: CS.SectionContract,
                                   credit_type: str, *,
                                   contract_version: str,
                                   contract_sha256: str,
                                   plan_id: str | None = None) -> None:
    """漂移校验：委托 planner.validate_section_task_provenance，fail-closed。

    篡改 required_aspects / evidence_requirements / question 文本 / 任一正式字段 →
    ContractDriftError(reason=CONTRACT_TASK_DRIFT)。

    期望 Contract 身份（``contract_version`` / ``contract_sha256`` / ``plan_id``）必须
    来自独立来源（独立加载的 Contract 文件、ReportPlan 或调用方显式输入），不得从待校验
    task 自身读取（否则形成自我证明）。SHA 严格相等，缺失/空/篡改均 fail-closed。
    """
    ok, diffs = planner.validate_section_task_provenance(
        task, sec, credit_type,
        contract_version=contract_version,
        contract_sha256=contract_sha256,
        plan_id=plan_id)
    if not ok:
        raise ContractDriftError(reason=CONTRACT_DRIFT, diffs=diffs)


# ---------------------------------------------------------------------------
# 从正式 ReportPlan 选择 topic / question
# ---------------------------------------------------------------------------

def find_section_task(plan: PS.ReportPlan, section_id: str) -> PS.SectionTask:
    """从正式计划选章节任务；不存在即 fail-closed。"""
    for t in plan.section_tasks:
        if t.section_id == section_id:
            return t
    raise KeyError(f"ReportPlan 无章节任务: {section_id!r}，"
                   f"现有 {plan.section_ids()}")


def select_question(task: PS.SectionTask, question_id: str) -> PS.PlannedQuestion:
    """从正式章节任务选必答问题；不存在即 fail-closed。"""
    for q in task.questions:
        if q.question_id == question_id:
            return q
    raise KeyError(f"SectionTask[{task.section_id}] 无问题: {question_id!r}，"
                   f"现有 {task.question_ids()}")


def select_topic_questions(task: PS.SectionTask,
                           topic_id: str) -> tuple[PS.PlannedQuestion, ...]:
    """选某 topic 下全部正式问题（保持契约顺序）；topic 不在任务 → fail-closed。"""
    if topic_id not in task.topic_ids:
        raise KeyError(f"SectionTask[{task.section_id}] 无 topic: {topic_id!r}，"
                       f"现有 {list(task.topic_ids)}")
    return tuple(q for q in task.questions if q.topic_id == topic_id)


def slice_identity(task: PS.SectionTask, *, scope: str,
                   topic_id: str | None = None,
                   question_id: str | None = None) -> dict:
    """从正式 SectionTask 派生抽面身份（三种产物 scope + 覆盖/总量 + 规范形指纹）。

    不完整（question_slice / topic_preview，或 section_preview 未覆盖全部）→
    ``chapter_complete=False``，绝不标记完整章节。
    """
    if scope == "question_slice":
        if not question_id:
            raise ValueError("question_slice 必须指定 question_id")
        q = select_question(task, question_id)
        qids = [q.question_id]
        tids = [q.topic_id]
    elif scope == "topic_preview":
        if not topic_id:
            raise ValueError("topic_preview 必须指定 topic_id")
        qs = select_topic_questions(task, topic_id)
        qids = [q.question_id for q in qs]
        tids = [topic_id]
    elif scope == "section_preview":
        qids = task.question_ids()
        tids = list(task.topic_ids)
    else:
        raise ValueError(f"非法 artifact_scope: {scope!r}，允许 {ARTIFACT_SCOPES}")

    # 完整 = 覆盖问题/主题与正式契约精确一致（集合等价，非数量比较）。
    complete = (set(qids) == set(task.question_ids())
                and set(tids) == set(task.topic_ids))
    return {
        "artifact_scope": scope,
        "section_id": task.section_id,
        "section_task_id": task.task_id,
        "selected_topic_ids": tids,
        "selected_question_ids": qids,
        "covered_questions": qids,
        "total_contract_questions": len(task.questions),
        "covered_topics": tids,
        "total_contract_topics": len(task.topic_ids),
        "chapter_complete": complete,
        "task_canonical_fingerprint": task_canonical_fingerprint(task),
    }


# ---------------------------------------------------------------------------
# 期间渲染（Python 生成，禁止模糊期间词）
# ---------------------------------------------------------------------------

_ANNUAL_RE = re.compile(r"^(\d{4})(?:-12-31)?$")
_QUARTER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_QTR_RANGE = {3: "1—3", 6: "1—6", 9: "1—9", 12: "1—12"}


def period_semantics(category: str | None) -> str:
    """事实类别 → 语义（flow=流量 / point=时点 / unknown）。"""
    if category in FLOW_CATEGORIES:
        return "flow"
    if category in POINT_CATEGORIES:
        return "point"
    # 未识别类别不得默认按流量处理 → 显式 unknown（期间口径未确认）。
    return "unknown"


def scope_note(report_as_of: str,
               available_annual_periods: list[str] | tuple[str, ...] = (),
               available_interim_periods: list[str] | tuple[str, ...] = ()) -> str:
    """动态生成统一口径说明（替代硬编码 UNIFIED_SCOPE_NOTE，不写死年份）。

    - 年度期间 → 年度流量 ``2025年度``；
    - 季度期间 → 季度时点 ``截至2026年3月末``（``report_as_of`` 为时点锚，用于
      判读可用期间，不进正文拼装）；
    - 无任何可用期间 → ``期间口径未确认``。
    """
    annual = [period_label(p, semantics="flow")
              for p in available_annual_periods]
    interim = [period_label(p, semantics="point")
               for p in available_interim_periods]
    if not annual and not interim:
        return "期间口径未确认"
    note = f"本报告以{'、'.join(annual)}经营及财务数据为主要分析基础"
    if interim:
        note += f"，并以{'、'.join(interim)}的数据作为补充"
    return note + "。"


def period_label(period: str | None, *, semantics: str = "flow") -> str:
    """把 period 字符串确定性渲染为期间文字。

    - flow：年度 ``2025年度``；季度区间 ``2026年1—3月``；
    - point：年度 ``截至2025年末``；季度 ``截至2026年3月末``；
    - 未知/空 → ``期间未明确``（不是「报告期」等模糊词）。
    """
    if not period:
        return "期间未明确"
    p = str(period).strip()
    m = _ANNUAL_RE.match(p)
    if m:
        y = m.group(1)
        return f"截至{y}年末" if semantics == "point" else f"{y}年度"
    m = _QUARTER_RE.match(p)
    if m:
        y, mo = m.group(1), int(m.group(2))
        if semantics == "point":
            return f"截至{y}年{mo}月末"
        rng = _QTR_RANGE.get(mo)
        return f"{y}年{rng}月" if rng else f"{y}年{mo}月"
    return p


def trend_label(periods: list[str]) -> str:
    """三年趋势 → ``2023—2025年度``（按年份升序，缺值回退）。"""
    years: list[str] = []
    for p in periods:
        m = _ANNUAL_RE.match(str(p or "").strip()) or _QUARTER_RE.match(str(p or "").strip())
        if m:
            years.append(m.group(1))
    years = sorted(set(years))
    if not years:
        return "期间未明确"
    return f"{years[0]}—{years[-1]}年度" if len(years) > 1 else f"{years[0]}年度"


def find_forbidden_period_words(text: str) -> tuple[str, ...]:
    """在正文中查找被禁止的模糊期间词（报告期/本期/期末/最新一期/近年来）。"""
    return tuple(w for w in FORBIDDEN_PERIOD_WORDS if w in text)


# ---------------------------------------------------------------------------
# 表格-正文一致性（跨渲染 Validator）
# ---------------------------------------------------------------------------

def extract_text_fact_ids(sentences) -> set[str]:
    """从段落句提取正文引用的 fact_id（sentence.fact_ids + {{fact:...}} 标记）。"""
    ids: set[str] = set()
    for s in sentences:
        ids.update(s.fact_ids)
        from sections.chapter_writer import parse_markers
        for mk in parse_markers(s.text):
            if mk["kind"] == "fact":
                ids.add(mk["id"])
    return ids


def table_text_consistency(tabular_numeric_fact_ids: set[str],
                           text_numeric_fact_ids: set[str]) -> list[str]:
    """只校验「表格数值事实」的正文-表格一致性。

    仅数值事实必须出现在确定性数值表格中；正文引用的数值事实若不在表格 →
    不一致。定性证据（产品/经营模式/产业链等）**不进入数值表格**，不参与本校验
    （其资格由 SectionClaim + CitationRef 绑定约束，见 chapter_writer）。

    调用方只传数值 fact_ids；返回不一致问题列表；空列表 = 一致通过。
    """
    issues: list[str] = []
    missing = sorted(text_numeric_fact_ids - tabular_numeric_fact_ids)
    if missing:
        issues.append(
            f"正文引用数值事实未在确定性表格中（表格没有的数字正文不得出现）: {missing}")
    return issues


# ---------------------------------------------------------------------------
# artifact scope（三种产物）
# ---------------------------------------------------------------------------

ARTIFACT_SCOPES = ("question_slice", "topic_preview", "section_preview")


def artifact_scope_info(*, scope: str, covered_questions: list[str],
                        total_contract_questions: int,
                        covered_topics: list[str],
                        total_contract_topics: int) -> dict:
    """三种产物统一命名与状态；不完整时不得标记 chapter_complete=true。"""
    if scope not in ARTIFACT_SCOPES:
        raise ValueError(f"非法 artifact_scope: {scope!r}，允许 {ARTIFACT_SCOPES}")
    complete = (len(covered_questions) >= total_contract_questions
                and len(covered_topics) >= total_contract_topics)
    return {
        "artifact_scope": scope,
        "covered_questions": list(covered_questions),
        "total_contract_questions": total_contract_questions,
        "covered_topics": list(covered_topics),
        "total_contract_topics": total_contract_topics,
        "chapter_complete": complete,
    }


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    out: dict = {}

    # period 渲染
    out["flow_annual"] = period_label("2025-12-31", semantics="flow")
    out["flow_quarter"] = period_label("2026-03-31", semantics="flow")
    out["point_annual"] = period_label("2025-12-31", semantics="point")
    out["point_quarter"] = period_label("2026-03-31", semantics="point")
    out["trend"] = trend_label(["2023-12-31", "2024-12-31", "2025-12-31"])

    # 禁止模糊词
    out["forbidden_detected"] = list(find_forbidden_period_words("本报告期与本期期末"))

    # 表文一致性
    out["consistency_ok"] = table_text_consistency({"ef1", "ef2"}, {"ef1"}) == []
    out["consistency_missing"] = table_text_consistency({"ef1"}, {"ef1", "ef9"})

    # 漂移校验：真实契约 + 正式 Planner 派生任务 → 无漂移。
    contracts = load_contracts("templates/contracts/standard_v2.yaml")
    job = PS.ReportJobInput(job_id="j_sc", company_id="C", company_name="测试",
                            credit_type="other", report_as_of="2026-03-31",
                            enabled_sections=("company", "industry"))
    fp = planner.contract_file_fingerprint("templates/contracts/standard_v2.yaml")
    plan = planner.plan(job, contracts, fp)
    company_task = find_section_task(plan, "company")
    by_sec = {s.section_id: s for s in contracts}
    validate_task_against_contract(company_task, by_sec["company"], "other",
                                   contract_version=CS.CONTRACT_VERSION,
                                   contract_sha256=fp, plan_id=plan.plan_id)
    out["drift_company_clean"] = True

    # 篡改 required_aspects → 漂移
    tampered = PS.SectionTask(
        task_id=company_task.task_id, plan_id=company_task.plan_id,
        section_id=company_task.section_id, title=company_task.title,
        purpose=company_task.purpose, research_policy=company_task.research_policy,
        topic_ids=company_task.topic_ids,
        questions=tuple(
            PS.PlannedQuestion(**{**q.__dict__, "required_aspects": ("被篡改",)})
            for q in company_task.questions
        ),
        output_requirements=company_task.output_requirements,
        evaluation_rule_ids=company_task.evaluation_rule_ids,
        allowed_capabilities=company_task.allowed_capabilities,
        blocking_rules=company_task.blocking_rules,
        dependency_versions=company_task.dependency_versions,
    )
    drift_raised = False
    try:
        validate_task_against_contract(tampered, by_sec["company"], "other",
                                       contract_version=CS.CONTRACT_VERSION,
                                       contract_sha256=fp, plan_id=plan.plan_id)
    except ContractDriftError as e:
        drift_raised = e.reason == CONTRACT_DRIFT
    out["drift_tamper_raises"] = drift_raised

    # topic 选择：company_business 4 问题 / industry 9 topics
    out["company_business_question_count"] = len(
        select_topic_questions(company_task, "company_business"))
    industry_task = find_section_task(plan, "industry")
    out["industry_topic_count"] = len(industry_task.topic_ids)

    return out


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m evaluation.contract_slice",
        description="契约派生切片/任务选择 + 漂移校验（纯函数自检）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())
