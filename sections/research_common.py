"""Phase 4 Batch C — 公司/行业研究 Worker 共享助手（确定性转换 + 渲染，无 LLM 直调）。

公司信用研究 Worker 与行业研究 Worker 都复用 Phase 3 Harness 公共入口
（``harness.runtime.run_question``），本模块只承载「ResearchOutcome → 章节
Claim/Unresolved」的确定性转换、状态派生、依赖指纹与确定性 Markdown 渲染。
不复制/重写 Harness loop。

硬约束（任务书 §11）：
- 不把 ``retrieval_observation``（运行时诊断）写成事实 claim；
- 不把搜索 snippet / URL 当作正式引用（外部引用必须是 ``source_snapshot_id``）；
- 不把「未检索到」写成「不存在」（NOT_FOUND_AFTER_SEARCH ≠ 事实不存在）；
- 不针对任何公司 / 行业 / case_id 写专用业务分支。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable

from external_v2 import schema as XS
from harness import checkpoint as C
from harness import policies as P
from harness import runtime as RT
from harness import schema as HS
from harness import state as HState
from planning import schema as PS
from routing import router as router_mod
from routing import schema as RS
from sections import citation_authority as CA
from sections import schema as SS
from tools import adapters as adapters
from tools import registry as R

logger = logging.getLogger("sections.research_common")


class ResearchWorkerError(RuntimeError):
    """研究 Worker fail-closed 错误（不产出章节、不切换 current）。"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# missing_policy → QUESTION_STATES（覆盖缺口诚实状态）。与财务 Worker 同口径，
# 但 valid_no_controller / transfer_human 由 gap_state 单独处理为 WAITING_HUMAN。
_MISSING_POLICY_STATE: dict[str, str] = {
    "write_not_found": "NOT_FOUND_AFTER_SEARCH",
    "write_not_found_disclose": "NOT_FOUND_AFTER_SEARCH",
    "missing_material": "NOT_PROVIDED",
    "conflict_pause": "CONFLICT",
    "not_applicable_no_plan": "NOT_APPLICABLE",
    "proxy_allowed": "NOT_PROVIDED",
}

# 这两类 missing_policy 在「无法确认」时需转人工（区别于「无实际控制人=SATISFIED」）。
_GAP_STATE_FORCE_HUMAN = ("transfer_human", "valid_no_controller")


@dataclass(frozen=True)
class ResearchWorkerResult:
    """研究 Worker 的返回（不可变）。"""

    section_result: SS.SectionResult
    question_outcomes: tuple[dict, ...]
    claim_count: int
    unresolved_count: int
    reused: bool
    committed: bool
    verification: dict


# ---------------------------------------------------------------------------
# need 构建 / 引用解析
# ---------------------------------------------------------------------------

def _flatten_evidence_requirements(q: PS.PlannedQuestion) -> tuple[list[str], list[str], str | None, list[str]]:
    """从 q.evidence_requirements 派生 (evidence_types, source_types, freshness, required_fields)。

    去重保序；freshness_policy 取首个非空值；required_fields 跨 requirement 合并去重。
    """
    evidence_types: list[str] = []
    source_types: list[str] = []
    required_fields: list[str] = []
    freshness: str | None = None
    for er in q.evidence_requirements:
        ek = er.get("evidence_kind")
        if ek and ek not in evidence_types:
            evidence_types.append(ek)
        for sc in er.get("source_classes", []) or []:
            if sc and sc not in source_types:
                source_types.append(sc)
        for f in er.get("required_fields", []) or []:
            if f and f not in required_fields:
                required_fields.append(f)
        fp = er.get("freshness_policy")
        if fp and freshness is None:
            freshness = fp
    return evidence_types, source_types, freshness, required_fields


def derive_time_scope(q: PS.PlannedQuestion, report_as_of: str | None) -> str | None:
    """time_scope 派生：仅当问题有时效/外部来源信号时携带 report_as_of（parseable 截止）。

    外部来源（external）或显式 freshness_policy 视为时效敏感 → time_scope=report_as_of；
    无要求的问题保持 None。report_as_of 为 None 时返回 None。
    """
    if not report_as_of:
        return None
    _, source_types, freshness, _ = _flatten_evidence_requirements(q)
    if freshness or "external" in source_types:
        return report_as_of
    return None


def build_need(task: PS.SectionTask, q: PS.PlannedQuestion,
               *, report_as_of: str | None = None) -> RS.InformationNeed:
    """SectionTask 的必答问题 → InformationNeed（承接 Contract 的 evidence_requirements）。

    - required_evidence_types ← evidence_kind；
    - required_source_types ← source_classes；
    - time_scope ← report_as_of（仅时效/外部来源问题）；
    - metadata 携带 freshness_policy / required_fields（最小向后兼容扩展）。
    无 evidence_requirements 的问题保持空（不构造伪 Need）。
    """
    evidence_types, source_types, freshness, required_fields = _flatten_evidence_requirements(q)
    return RS.InformationNeed(
        need_id=q.question_id,
        section_id=task.section_id,
        question=q.question,
        required_evidence_types=evidence_types,
        required_source_types=source_types,
        time_scope=derive_time_scope(q, report_as_of),
        priority=q.priority,
        depends_on=[],
        metadata={
            "freshness_policy": freshness,
            "required_fields": required_fields,
        },
    )


def resolve_claim_refs(claim: HS.Claim, answer: HS.ResearchAnswer) -> tuple[HS.CitationRef, ...] | None:
    """把 claim.citation_refs（下标）解析为 CitationRef；任一下标越界 → None（丢弃该 claim）。"""
    refs: list[HS.CitationRef] = []
    for idx in claim.citation_refs:
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 \
                or idx >= len(answer.citations):
            return None
        refs.append(answer.citations[idx])
    return tuple(refs)


def canonicalize_citation_refs(refs: tuple[HS.CitationRef, ...]
                               ) -> tuple[tuple[HS.CitationRef, ...], int]:
    """折叠重复 CitationRef（ResearchAnswer → SectionClaim 边界，§11.2）。

    同一引用（按 ``SS.citation_identity`` 内容身份）多次出现只保留首次，保持首次出现
    顺序；不同身份全部保留。返回 ``(去重后引用, 折叠掉的重复条数)``。

    - 不修改 External 引用语义（缺 ``source_snapshot_id`` 仍由 CitationAuthority 判非法）；
    - 不引入位置/序号信息，纯内容身份折叠，幂等；
    - 同一引用重复十次仍只算一个来源。
    """
    seen: dict[str, HS.CitationRef] = {}
    duplicate_count = 0
    for ref in refs:
        key = SS.citation_identity(ref)
        if key in seen:
            duplicate_count += 1
        else:
            seen[key] = ref
    return tuple(seen.values()), duplicate_count


# ---------------------------------------------------------------------------
# 状态派生
# ---------------------------------------------------------------------------

def missing_policy_state(policy: str) -> str:
    return _MISSING_POLICY_STATE.get(policy, "NOT_FOUND_AFTER_SEARCH")


def gap_state(q: PS.PlannedQuestion, outcome: HS.ResearchOutcome) -> str:
    """问题缺口状态：transfer_human / valid_no_controller（无法确认）→ WAITING_HUMAN；
    其余按 missing_policy 派生。"""
    if outcome.stop_reason == "WAITING_USER" or q.missing_policy in _GAP_STATE_FORCE_HUMAN:
        return "WAITING_HUMAN"
    return missing_policy_state(q.missing_policy)


def derive_status(unresolved: tuple[SS.SectionUnresolved, ...]) -> str:
    """章节状态：WAITING_HUMAN 优先 → 任一阻断级（SECTION/REPORT/JOB）→ COMPLETED_WITH_GAPS。

    JOB_BLOCKED / REPORT_BLOCKED / SECTION_BLOCKED 都是阻断级：任一带阻断的问题未解决，
    本章节即无法完成（章节状态无独立 JOB/REPORT 阻断值，统一映射为 SECTION_BLOCKED）。
    """
    if not unresolved:
        return "COMPLETED"
    if any(u.state == "WAITING_HUMAN" for u in unresolved):
        return "WAITING_HUMAN"
    for u in unresolved:
        if any(level in u.blocking_effects
               for level in ("SECTION_BLOCKED", "REPORT_BLOCKED", "JOB_BLOCKED")):
            return "SECTION_BLOCKED"
    return "COMPLETED_WITH_GAPS"


# ---------------------------------------------------------------------------
# Outcome → 章节 Claim / Unresolved
# ---------------------------------------------------------------------------

def _harness_gaps(outcome: HS.ResearchOutcome) -> str:
    gaps: list[str] = []
    if outcome.answer is not None:
        gaps.extend(outcome.answer.unresolved_items or [])
    gaps.extend(outcome.state.unresolved_items or [])
    return "；".join(dict.fromkeys(x for x in gaps if x))


def _unresolved_detail(q: PS.PlannedQuestion, outcome: HS.ResearchOutcome) -> str:
    cs = outcome.completion_status
    sr = outcome.stop_reason or ""
    if cs == "UNRESOLVED":
        base = f"问题「{q.question}」未解决（{sr or '未达充分答案'}）"
    elif cs == "NOT_IMPLEMENTED":
        base = f"问题「{q.question}」研究路径未实现（{sr or 'PATH_NOT_IMPLEMENTED'}）"
    elif cs == "FAILED":
        base = f"问题「{q.question}」研究失败（{sr or 'FAILED'}）"
    else:
        base = f"问题「{q.question}」存在未解决缺口（{sr or cs}）"
    gaps = _harness_gaps(outcome)
    if gaps:
        base += "；未解决项：" + gaps
    return base


def _make_unresolved(q: PS.PlannedQuestion, *, section_id: str, state: str,
                     reason_code: str, detail: str,
                     attempted: tuple[str, ...] = ()) -> SS.SectionUnresolved:
    qid = q.question_id
    # unresolved_id 必须绑定缺口内容 + 尝试来源 + 阻断/影响范围：缺口内容或检索来源变化
    # → 新 unresolved_id → 新 section_version（Store 复用不得复用旧版本）。
    uid = "ur_" + SS.sha256_json([
        section_id, q.topic_id, qid, state, reason_code, detail,
        tuple(attempted), tuple(q.blocking_policy), tuple(q.impact_scope)])[:24]
    return SS.SectionUnresolved(
        unresolved_id=uid, section_id=section_id, topic_id=q.topic_id, question_id=qid,
        state=state, reason_code=reason_code, detail=detail,
        impact_scope=tuple(q.impact_scope), blocking_effects=tuple(q.blocking_policy),
        attempted_sources=attempted)


def convert_question_outcome(task: PS.SectionTask, q: PS.PlannedQuestion,
                             outcome: HS.ResearchOutcome, *, section_id: str,
                             authority: CA.CitationAuthority | None = None
                             ) -> tuple[list[SS.SectionClaim], list[SS.SectionUnresolved], list[str]]:
    """单题 Outcome → (claims, unresolved, notes)。遵循 §11.2 + 关闭前定点修复一/二：
    COMPLETED / COMPLETED_WITH_GAPS → 引用支持的 claim（+缺口）；
    UNRESOLVED / NOT_IMPLEMENTED / FAILED → 不写肯定事实，仅 SectionUnresolved。

    一：所有正式 Claim（fact/calculation/inference）都必须有 ≥1 可回查 CitationRef
      （derived 链条本批未构建 → 一律要求引用）。
    二：正式引用必须经过权威性校验（authority），校验失败 fail-closed 丢弃该 Claim。
    """
    claims: list[SS.SectionClaim] = []
    unresolved: list[SS.SectionUnresolved] = []
    notes: list[str] = []
    qid = q.question_id
    topic_id = q.topic_id
    impact_scope = tuple(q.impact_scope)
    answer = outcome.answer
    ans_conf = (answer.confidence if answer is not None
                and answer.confidence in SS.CONFIDENCE_LEVELS else "low")

    valid_claims: list[SS.SectionClaim] = []
    # 权威失败（正式引用校验）：(ref_type, reason)。区分「引用本身未通过权威校验」
    # （citation_authority_failed）与「权威查询异常」（authority_query_failed）。
    authority_failures: list[tuple[str, str]] = []
    query_failed = False

    if answer is not None:
        for c in answer.claims:
            if c.kind == "retrieval_observation":
                notes.append(f"{qid}: 过滤 retrieval_observation claim（运行时诊断，非事实）")
                continue
            refs = resolve_claim_refs(c, answer)
            if refs is None:
                notes.append(f"{qid}: claim {c.claim_id} 引用下标越界，丢弃")
                continue
            refs, duplicate_count = canonicalize_citation_refs(refs)
            if duplicate_count:
                notes.append(
                    f"{qid}: claim {c.claim_id} 折叠 {duplicate_count} 条重复引用 "
                    f"(duplicate_citation_collapsed)")
            claim_type = "fact" if c.kind == "fact" else "inference"
            text = (c.text or "").strip()
            if not text:
                notes.append(f"{qid}: claim {c.claim_id} 空文本，丢弃")
                continue
            if not refs:
                notes.append(f"{qid}: {claim_type} claim {c.claim_id} 无引用，丢弃")
                continue
            if authority is not None:
                invalid: str | None = None
                failed_ref_type = "?"
                try:
                    for ref in refs:
                        failed_ref_type = ref.ref_type
                        verdict = authority.validate(ref)
                        if verdict.warnings:
                            notes.append(
                                f"{qid}: claim {c.claim_id} 引用 {ref.ref_type} soft 降级: "
                                f"{','.join(verdict.warnings)}")
                        if not verdict.valid:
                            invalid = verdict.reason
                            break
                except Exception as e:  # noqa: BLE001 — 权威查询失败不得 fail-open
                    logger.warning("权威校验查询失败，claim %s 丢弃: %s", c.claim_id, e)
                    query_failed = True
                    invalid = "authority_query_failed"
                if invalid is not None:
                    if invalid == "authority_query_failed":
                        authority_failures.append((failed_ref_type, "authority_query_failed"))
                    else:
                        authority_failures.append((failed_ref_type, invalid))
                    notes.append(
                        f"{qid}: claim {c.claim_id} 引用未通过权威校验（{invalid}），丢弃")
                    continue
            claim_id = SS.derive_claim_id(claim_type, topic_id, (qid,), text, refs)
            valid_claims.append(SS.SectionClaim(
                claim_id=claim_id, section_id=section_id, topic_id=topic_id,
                question_ids=(qid,), text=text, claim_type=claim_type,
                citation_refs=tuple(refs), confidence=ans_conf,
                impact_scope=impact_scope))

    cs = outcome.completion_status
    covered = bool(valid_claims)
    claims_allowed = cs in ("COMPLETED", "COMPLETED_WITH_GAPS")
    attempted = tuple(dict.fromkeys(
        list(outcome.state.evidence_ids) + list(outcome.state.external_snapshot_ids)))

    # 权威失败 → 问题绑定 unresolved，持久化到 SectionResult / 身份 / Store / Markdown
    # （不落 body/敏感信息，detail 只记 ref_type + 失败原因）。
    if claims_allowed and authority_failures:
        reason_code = "authority_query_failed" if query_failed else "citation_authority_failed"
        detail = "正式引用未通过权威校验：" + "；".join(dict.fromkeys(
            f"引用类型={rt} 失败原因={r}" for rt, r in authority_failures))
        unresolved.append(_make_unresolved(
            q, section_id=section_id, state=gap_state(q, outcome),
            reason_code=reason_code, detail=detail, attempted=attempted))

    if claims_allowed and covered:
        claims.extend(valid_claims)
        if cs == "COMPLETED_WITH_GAPS":
            unresolved.append(_make_unresolved(
                q, section_id=section_id, state=gap_state(q, outcome),
                reason_code=outcome.stop_reason or "COMPLETED_WITH_GAPS",
                detail=_unresolved_detail(q, outcome), attempted=attempted))
    elif claims_allowed and not covered and not authority_failures:
        detail = f"问题「{q.question}」研究完成但未形成有效可引用 claim"
        gaps = _harness_gaps(outcome)
        if gaps:
            detail += "；未解决项：" + gaps
        unresolved.append(_make_unresolved(
            q, section_id=section_id, state=gap_state(q, outcome),
            reason_code="no_valid_claim", detail=detail, attempted=attempted))
    elif not claims_allowed:
        # UNRESOLVED / NOT_IMPLEMENTED / FAILED → 不写肯定事实，仅缺口。
        reason = outcome.stop_reason or cs or q.missing_policy
        unresolved.append(_make_unresolved(
            q, section_id=section_id, state=gap_state(q, outcome),
            reason_code=reason, detail=_unresolved_detail(q, outcome),
            attempted=attempted))

    return claims, unresolved, notes


# ---------------------------------------------------------------------------
# 依赖指纹
# ---------------------------------------------------------------------------

def outcome_content_identity(q: PS.PlannedQuestion, outcome: HS.ResearchOutcome) -> str:
    """单题 ResearchOutcome 的内容身份（不含 run_id / 时间戳 / call_id）。

    编码 completion_status / stop_reason / success / answer 的 claim 文本 + 类型 +
    解析后引用身份 + confidence + unresolved_items —— 内容变化 → 新身份。
    """
    answer = outcome.answer
    claims_part: list[dict] = []
    if answer is not None:
        for c in answer.claims:
            refs = resolve_claim_refs(c, answer) or ()
            claims_part.append({
                "claim_id": c.claim_id,
                "kind": c.kind,
                "text": c.text,
                "refs": [SS.citation_identity(r) for r in refs],
            })
        unresolved_items = sorted(answer.unresolved_items or [])
        confidence = answer.confidence
    else:
        unresolved_items = []
        confidence = None
    return SS.sha256_json([
        q.question_id,
        outcome.completion_status,
        outcome.stop_reason,
        outcome.success,
        confidence,
        unresolved_items,
        claims_part,
    ])


def dependency_fingerprint(task: PS.SectionTask, context: RS.RouteContext | None, *,
                           renderer_version: str, rules_version: str,
                           prompt_version: str, worker_version: str,
                           model_id: str = "", budget=None,
                           outcome_identities: tuple[str, ...] = (),
                           harness_version: str = "") -> str:
    """依赖指纹：任务依赖 + 证据清单 + 快照 + 版本常量 + 模型/预算 + 内容身份。

    进入 section_version 派生 —— 任一依赖变化 → 新版本；完全相同输入 → 严格复用。
    不包含 run_id / 时间戳 / call_id（保幂等）。outcome_identities 为每题
    ResearchOutcome 内容身份（内容变化 → 新指纹，即使最终 claims 相同也换版本）。
    """
    return SS.sha256_json([
        sorted((task.dependency_versions or {}).items()),
        sorted(context.available_document_ids) if context is not None else [],
        (context.snapshot_id or "") if context is not None else "",
        RS.RULE_VERSION,
        harness_version,
        XS.GRADE_RULE_VERSION,
        model_id,
        (budget.as_dict() if budget is not None else None),
        renderer_version, rules_version, prompt_version, worker_version,
        sorted(outcome_identities),
    ])


# ---------------------------------------------------------------------------
# 引用展示标签（§12.2）
# ---------------------------------------------------------------------------

def citation_label(ref: HS.CitationRef, external_labels: dict[str, dict] | None = None) -> str:
    """§12.2 引用展示：Evidence=[来源文件，PDF第N页]；Structured=[FinancialSnapshot，
    期间，科目/公式]；External=[来源名称，发布日期，抓取日期]（来源名称经外部快照查询）。"""
    external_labels = external_labels or {}
    if ref.ref_type == "evidence":
        page = ref.page_number
        page_s = f"PDF第{page}页" if page is not None else "PDF页码未标注"
        return f"[来源文件 {ref.evidence_id}，{page_s}]"
    if ref.ref_type == "structured":
        code = ref.formula_id or ref.item_code or "?"
        return f"[FinancialSnapshot，{ref.period or '?'}，{code}]"
    if ref.ref_type == "external":
        ex = external_labels.get(ref.source_snapshot_id or "", {})
        name = ex.get("name") or ref.source_snapshot_id
        published = ex.get("published_at") or "发布日期未标注"
        fetched = ex.get("fetched_at") or "抓取日期未标注"
        grade = ex.get("source_grade")
        name_s = f"[{grade}] {name}" if grade else name
        return f"[{name_s}，{published}，{fetched}]"
    return f"[{ref.ref_type}]"


def label_fn_for(external_labels: dict[str, dict] | None = None) -> Callable[[HS.CitationRef], str]:
    def _label(ref: HS.CitationRef) -> str:
        return citation_label(ref, external_labels=external_labels)
    return _label


# ---------------------------------------------------------------------------
# 渲染（确定性 Markdown）
# ---------------------------------------------------------------------------

def render_markdown(task: PS.SectionTask, claims: tuple[SS.SectionClaim, ...],
                    unresolved: tuple[SS.SectionUnresolved, ...], *,
                    topic_labels: dict[str, str], company_id: str, company_name: str,
                    context: RS.RouteContext | None,
                    label_fn: Callable[[HS.CitationRef], str]) -> str:
    lines: list[str] = []
    lines.append(f"# {task.title or '章节'}")
    lines.append("")
    lines.append("## 数据来源与范围")
    lines.append("")
    lines.append(f"- 公司：{company_name or '—'}（{company_id}）")
    if context is not None:
        lines.append(f"- 报告时点：{context.report_as_of or '—'}")
        lines.append(f"- 可检索文档：{len(context.available_document_ids)} 份")
        lines.append(f"- 财务快照：{context.snapshot_id or '（无健康快照）'}")
        lines.append(f"- 外部检索：{'开启' if context.external_research_enabled else '关闭'}")
    lines.append("")

    by_topic: dict[str, list[SS.SectionClaim]] = {}
    for c in claims:
        by_topic.setdefault(c.topic_id, []).append(c)

    lines.append("## 研究结论")
    lines.append("")
    for tid in task.topic_ids:
        if tid not in by_topic:
            continue
        lines.append(f"### {topic_labels.get(tid, tid)}")
        lines.append("")
        for c in by_topic[tid]:
            tag = "事实" if c.claim_type == "fact" else "研判"
            lines.append(f"- [{tag}] {c.text}")
        lines.append("")

    if unresolved:
        lines.append("## 未解决 / 未取得项")
        lines.append("")
        for u in unresolved:
            lines.append(f"- [{u.state}] {u.detail}")
        lines.append("")

    lines.append("## 引用来源")
    lines.append("")
    distinct: dict[str, HS.CitationRef] = {}
    for c in claims:
        for ref in c.citation_refs:
            distinct[SS.citation_identity(ref)] = ref
    if distinct:
        for idx, ref in enumerate(sorted(distinct.values(), key=SS.citation_identity), start=1):
            lines.append(f"{idx}. {label_fn(ref)}")
    else:
        lines.append("（无引用）")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 外部快照标签（I/O：只读查询，用于 §12.2 展示，失败降级为空不抛错）
# ---------------------------------------------------------------------------

def build_external_labels(snapshot_ids, external_db: str | None
                          ) -> tuple[dict[str, dict], dict]:
    """外部快照展示标签（§12.2，严格只读）。

    复用 ``citation_authority._ro_external_get``（SQLite URI ``mode=ro``，缺库
    fail-closed），不修改 ``external_v2.store._db_path``、不 init_db、不创建、不迁移。
    返回 ``(labels, diagnostic)``：缺库/缺表/损坏 → 安全降级为 ``{}`` + 明确 diagnostic
    （上层据此把来源等级按 ``unknown`` fail-closed，不因此放行关键 Claim）。
    """
    diag: dict = {
        "queried": len(snapshot_ids or ()), "resolved": 0,
        "status": "skipped", "errors": [],
    }
    if not snapshot_ids or not external_db:
        return {}, diag
    diag["status"] = "ok"
    labels: dict[str, dict] = {}
    for sid in snapshot_ids:
        try:
            snap = CA._ro_external_get(external_db, sid)
        except FileNotFoundError as e:
            diag["status"] = "missing_db"
            diag["errors"].append(str(e))
            break
        except sqlite3.OperationalError as e:
            diag["status"] = "missing_table_or_corrupt"
            diag["errors"].append(str(e))
            break
        except sqlite3.DatabaseError as e:
            diag["status"] = "corrupt"
            diag["errors"].append(str(e))
            break
        except Exception as e:  # noqa: BLE001 — 反序列化等其它失败同样安全降级
            diag["status"] = "query_failed"
            diag["errors"].append(str(e))
            break
        if snap is None:
            continue
        labels[sid] = {
            "name": snap.title or snap.provider or snap.canonical_url or sid,
            "published_at": snap.published_at,
            "fetched_at": snap.fetched_at,
            "source_grade": snap.source_grade,
            "canonical_url": snap.canonical_url,
        }
        diag["resolved"] += 1
    return labels, diag


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _make_research_question(registry: R.ToolRegistry, llm, budget: P.ResearchBudget,
                            run_id: str, company_id: str, section_id: str):
    """构造注入缝对应的生产 harness 入口（复用 Phase 3 公共 run_question）。"""
    def _run(need: RS.InformationNeed, route_result: RS.RouterResult,
             context: RS.RouteContext | None) -> HS.ResearchOutcome:
        return RT.run_question(
            need=need, route_result=route_result, registry=registry, llm=llm,
            budget=budget, run_id=run_id, case_id=f"{section_id}:{need.need_id}",
            company_id=company_id, section_id=section_id,
            trace_enabled=True, context=context)
    return _run


def run_task(task: PS.SectionTask, *, section_id: str, topic_labels: dict[str, str],
             renderer_version: str, rules_version: str, prompt_version: str,
             worker_version: str, company_id: str, company_name: str = "",
             run_id: str = "", scope: str = "consolidated", currency: str = "CNY",
             purpose: str = "credit_analysis", as_of_date: str | None = None,
             model: str | None = None, external_research_enabled: bool | None = None,
             budget: P.ResearchBudget | None = None, audit_dir: str | Path | None = None,
             registry: R.ToolRegistry | None = None, llm=None,
             build_context: Callable[[], RS.RouteContext] | None = None,
             route_fn: Callable | None = None,
             research_question: Callable | None = None,
             external_db: str | None = "data/external_sources.db",
             harness_db: str | None = "data/harness.db",
             checkpoint: bool = True,
             evidence_db: str | None = "data/evidence.db",
             financial_db: str | None = "data/financial_v2.db",
             authority: CA.CitationAuthority | None = None,
             source_policy: Callable | None = None) -> ResearchWorkerResult:
    """执行公司/行业研究 Worker（复用 Harness + 确定性转换 + 渲染）。

    - ``research_question`` / ``route_fn`` / ``build_context`` / ``registry`` / ``llm``
      均可注入（测试隔离真实 LLM / Embedding / 网络 / 路由审计副作用）。
    """
    if task.section_id != section_id:
        raise ResearchWorkerError(
            f"本 Worker 仅处理 {section_id} 章节，收到 {task.section_id!r}")
    budget = budget or P.DEFAULT_BUDGET

    if build_context is None:
        from routing import context as routing_context
        build_context = partial(
            routing_context.build_route_context, company_id, scope=scope, currency=currency,
            as_of_date=as_of_date, purpose=purpose,
            external_research_enabled=external_research_enabled)
    context = build_context()

    # 依赖指纹 / 内容身份所需的模型身份（显式传入优先，否则回退全局配置）。
    from config import LLM_MODEL
    model_id = model or LLM_MODEL

    # 正式引用权威性校验（只读查询既有库，不 init_db / 不创建迁移）。
    # 未提供库路径时 authority 保持 None（引用不进权威校验，仅用于测试注入）。
    if authority is None and (evidence_db or financial_db or external_db):
        authority = CA.build_citation_authority(
            company_id, context, ev_db=evidence_db, fin_db=financial_db,
            ext_db=external_db)

    route_fn = route_fn or router_mod.route
    if research_question is None:
        reg = registry or adapters.build_default_registry(
            audit_dir or R.DEFAULT_AUDIT_DIR)
        llm_obj = llm or RT.RealResearchLLM(model)
        research_question = _make_research_question(
            reg, llm_obj, budget, run_id, company_id, section_id)

    all_claims: list[SS.SectionClaim] = []
    all_unresolved: list[SS.SectionUnresolved] = []
    notes: list[str] = []
    question_outcomes: list[dict] = []
    outcome_identities: list[str] = []
    route_counts: dict[str, int] = {}

    for q in task.questions:
        need = build_need(task, q, report_as_of=(context.report_as_of if context else None))
        rr = route_fn(need, context)
        route_label = (rr.decision.route if rr.status == "DECIDED" and rr.decision
                       else rr.status)
        route_counts[route_label] = route_counts.get(route_label, 0) + 1
        outcome = research_question(need, rr, context)
        if checkpoint and run_id and harness_db:
            C.write_question_outcome(run_id, outcome, harness_db)
        claims_q, unresolved_q, notes_q = convert_question_outcome(
            task, q, outcome, section_id=section_id, authority=authority)
        all_claims.extend(claims_q)
        all_unresolved.extend(unresolved_q)
        notes.extend(notes_q)
        oid = outcome_content_identity(q, outcome)
        outcome_identities.append(oid)
        question_outcomes.append({
            "question_id": q.question_id, "topic_id": q.topic_id,
            "priority": q.priority, "route": route_label,
            "completion_status": outcome.completion_status,
            "stop_reason": outcome.stop_reason, "success": outcome.success,
            "claims": len(claims_q), "unresolved": len(unresolved_q),
            "evidence_ids": list(outcome.state.evidence_ids),
            "external_snapshot_ids": list(outcome.state.external_snapshot_ids),
            "content_fingerprint": oid,
            # 新能力在正式链的承载位置（§五）：只读投影自 Harness 状态。
            "aspect_coverage": HState.build_aspect_coverage(
                outcome.state, outcome.answer).to_dict(),
            "external_funnel": HState.project_external_funnel(
                outcome.state).to_dict(),
        })

    claims = tuple(all_claims)
    unresolved = tuple(all_unresolved)

    # 引用外部快照标签（§12.2 展示）+ 来源分级诊断（行业 §11.4；不阻断、不进状态）。
    ext_sids = {ref.source_snapshot_id for c in claims for ref in c.citation_refs
                if ref.ref_type == "external" and ref.source_snapshot_id}
    external_labels, external_label_diag = build_external_labels(ext_sids, external_db)
    grade_counts: dict[str, int] = {}
    for c in claims:
        for ref in c.citation_refs:
            if ref.ref_type == "external":
                grade = external_labels.get(ref.source_snapshot_id or {}, {}).get("source_grade") \
                    or "unknown"
                grade_counts[grade] = grade_counts.get(grade, 0) + 1

    # 行业 A/B/C/D 来源规则（通用 policy；仅行业 Worker 注入 source_policy）。
    # 返回 (processed_claims, extra_unresolved, assessment)：policy 可移除不合格
    # 的正式 Claim 并生成显式 unresolved，二者随后进入 status / 身份 / Markdown。
    source_assessment: dict = {}
    if source_policy is not None:
        claims, extra_unresolved, source_assessment = source_policy(
            claims, external_labels, task, context, section_id=section_id)
        if extra_unresolved:
            unresolved = tuple(list(unresolved) + list(extra_unresolved))

    status = derive_status(unresolved)
    fingerprint = dependency_fingerprint(
        task, context, renderer_version=renderer_version, rules_version=rules_version,
        prompt_version=prompt_version, worker_version=worker_version,
        model_id=model_id, budget=budget, outcome_identities=tuple(outcome_identities),
        harness_version=HS.HARNESS_VERSION)
    section_version = SS.derive_section_version(
        task.task_id, claims, unresolved, renderer_version=renderer_version,
        rules_version=rules_version, dependency_fingerprint=fingerprint)
    section_result_id = SS.derive_section_result_id(section_version)
    markdown = render_markdown(
        task, claims, unresolved, topic_labels=topic_labels, company_id=company_id,
        company_name=company_name, context=context,
        label_fn=label_fn_for(external_labels))

    result = SS.SectionResult(
        section_result_id=section_result_id, section_version=section_version,
        task_id=task.task_id, section_id=task.section_id, status=status,
        claims=claims, unresolved=unresolved, markdown=markdown, evaluation=None,
        source_run_ids=(run_id,) if run_id else (), source_question_ids=tuple(task.question_ids()),
        dependency_fingerprint=fingerprint, created_at=_utcnow())

    from sections import validator as svalidator
    errors = svalidator.validate_section_result(result)
    if errors:
        raise ResearchWorkerError(
            "章节结构校验失败:\n" + "\n".join(f"  - {e}" for e in errors))

    covered = sorted({qid for c in claims for qid in c.question_ids})
    verification = {
        "section_id": section_id,
        "company_id": company_id,
        "questions_total": len(task.questions),
        "questions_with_claims": len({qid for c in claims for qid in c.question_ids}),
        "questions_unresolved": len({u.question_id for u in unresolved if u.question_id}),
        "claims_total": len(claims),
        "unresolved_total": len(unresolved),
        "route_summary": route_counts,
        "source_grade_distribution": grade_counts,
        "authority_report": {
            "applied": authority is not None,
            "authority_failures": sum(1 for n in notes if "未通过权威校验" in n),
            "soft_degradations": sum(1 for n in notes if "soft 降级" in n),
            "no_citation_drops": sum(1 for n in notes if "无引用" in n),
        },
        "source_assessment": source_assessment,
        "external_label_query": external_label_diag,
        "notes": notes,
        "coverage": {
            "covered_questions": covered,
            "uncovered_questions": [q.question_id for q in task.questions
                                    if q.question_id not in covered
                                    and not any(u.question_id == q.question_id for u in unresolved)],
        },
    }

    return ResearchWorkerResult(
        section_result=result, question_outcomes=tuple(question_outcomes),
        claim_count=len(claims), unresolved_count=len(unresolved),
        reused=False, committed=False, verification=verification)


# ---------------------------------------------------------------------------
# CLI 共享逻辑
# ---------------------------------------------------------------------------

def add_cli_arguments(parser) -> None:
    parser.add_argument("--task", required=True, help="SectionTask JSON 文件（section_task_to_dict 产物）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--company-name", default="")
    parser.add_argument("--run-id", default=None, help="研究 run id（checkpoint 用；缺省按时间戳）")
    parser.add_argument("--scope", default="consolidated")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--purpose", default="credit_analysis")
    parser.add_argument("--as-of", default=None, dest="as_of_date")
    parser.add_argument("--ev-db", default=None, help="evidence SQLite 库路径（缺省用 store 默认）")
    parser.add_argument("--fin-db", default=None, help="financial_v2 SQLite 库路径（缺省用 store 默认）")
    parser.add_argument("--model", default=None)
    parser.add_argument("--external-db", default="data/external_sources.db")
    parser.add_argument("--harness-db", default="data/harness.db")
    parser.add_argument("--section-db", default="data/sections.db")
    parser.add_argument("--audit-dir", default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--max-tool-calls", type=int, default=None)
    parser.add_argument("--max-elapsed-ms", type=int, default=None)
    parser.add_argument("--validate-only", action="store_true", help="只生成不写 Store")
    parser.add_argument("--store", action="store_true", help="原子写入 section Store")
    parser.add_argument("--out", default=None, help="Markdown 输出路径（可选）")


def _make_budget(args) -> P.ResearchBudget:
    import dataclasses
    b = P.DEFAULT_BUDGET
    overrides: dict = {}
    if args.max_rounds is not None:
        overrides["max_rounds"] = args.max_rounds
    if args.max_tool_calls is not None:
        overrides["max_tool_calls"] = args.max_tool_calls
    if args.max_elapsed_ms is not None:
        overrides["max_elapsed_ms"] = args.max_elapsed_ms
    return dataclasses.replace(b, **overrides) if overrides else b


def run_cli(run_task_fn, *, module_name: str, argv: list[str] | None = None) -> int:
    """研究 Worker CLI 通用逻辑（company/industry 复用）。"""
    import argparse
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog=f"python -m {module_name}", description=f"Phase 4 研究 Worker（{module_name}）")
    add_cli_arguments(parser)
    args = parser.parse_args(argv)

    task_dict = json.loads(Path(args.task).read_text(encoding="utf-8"))
    task = PS.section_task_from_dict(task_dict)

    run_id = args.run_id or f"run_{_utcnow().replace('-', '').replace(':', '').replace('Z', '')}"
    budget = _make_budget(args)

    # 路由只读依赖的库：先初始化（build_route_context 内部用模块级 _db_path）。
    # 解析后的路径同时透传给 run_task 的 authority（权威校验只读同一份库）。
    from evidence import store as estore
    from financial_v2 import store as fstore
    ev_path = args.ev_db or str(estore.DEFAULT_DB_PATH)
    fin_path = args.fin_db or str(fstore.DEFAULT_DB_PATH)
    estore.init_db(ev_path)
    fstore.init_db(fin_path)

    try:
        wr = run_task_fn(task, company_id=args.company_id, company_name=args.company_name,
                         run_id=run_id, scope=args.scope, currency=args.currency,
                         purpose=args.purpose, as_of_date=args.as_of_date, model=args.model,
                         budget=budget, audit_dir=args.audit_dir,
                         external_db=args.external_db, harness_db=args.harness_db,
                         evidence_db=ev_path, financial_db=fin_path)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("研究 Worker 失败")
        print(f"研究 Worker 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    summary = {
        "run_id": run_id,
        "section_result_id": wr.section_result.section_result_id,
        "section_version": wr.section_result.section_version,
        "status": wr.section_result.status,
        "claims": wr.claim_count,
        "unresolved": wr.unresolved_count,
        "question_outcomes": [dict(o) for o in wr.question_outcomes],
        "verification": wr.verification,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.store:
        from sections import store as sstore
        sstore.init_db(args.section_db)
        commit = sstore.commit_section_result(wr.section_result, run_id=run_id)
        print(f"\n[store] {json.dumps({
            'reused': commit.reused, 'current_switched': commit.current_switched,
            'claim_count': commit.claim_count, 'unresolved_count': commit.unresolved_count},
            ensure_ascii=False)}")

    if args.out:
        Path(args.out).write_text(wr.section_result.markdown, encoding="utf-8")
        print(f"\nMarkdown 已写入: {args.out}")

    return 0
