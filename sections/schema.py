"""Phase 4 章节产物数据模型（Claim / Citation / Unresolved / Result / Evaluation）。

只定义声明式数据结构、状态枚举与确定性身份/版本派生，不含 I/O、不含业务计算。
- CitationRef 直接复用 Phase 3 harness.schema.CitationRef（任务书 §7.5），不造第二套引用。
- claim_type 第一阶段仅 fact | calculation | inference；不生成 recommendation（授信建议属 Phase 5）。
- COMPLETED_WITH_GAPS ≠ 严格通过；SECTION_BLOCKED 不阻止其他章节。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from harness.schema import CitationRef  # 复用，禁止重定义

# 章节工作流状态（任务书 §7.7）
SECTION_STATUSES = (
    "PLANNED",
    "RESEARCHING",
    "DRAFT_READY",
    "EVALUATING",
    "REWORK_REQUIRED",
    "COMPLETED",
    "COMPLETED_WITH_GAPS",
    "SECTION_BLOCKED",
    "WAITING_HUMAN",
    "FAILED",
)

# 第一阶段 claim 类型（fact 事实 / calculation 计算值 / inference 研判；
# 不含 recommendation —— 授信建议属 Phase 5）
CLAIM_TYPES = ("fact", "calculation", "inference")

# 评估决策
EVALUATION_DECISIONS = ("PASS", "PASS_WITH_GAPS", "REWORK", "BLOCKED", "FAILED")

# 置信度（与 harness 对齐）
CONFIDENCE_LEVELS = ("high", "low")

# 引用类别（与 harness.schema.CITATION_TYPES 对齐，不重复定义）
CITATION_TYPES = ("evidence", "structured", "external")


def sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def citation_identity(ref: CitationRef) -> str:
    """一条引用的身份字符串（供 claim_id 派生与去重）。"""
    if ref.ref_type == "evidence":
        return f"evidence:{ref.evidence_id}:{ref.page_number or ''}"
    if ref.ref_type == "structured":
        return (f"structured:{ref.snapshot_id}:{ref.item_code or ''}:"
                f"{ref.formula_id or ''}:{ref.formula_version or ''}:{ref.period or ''}")
    if ref.ref_type == "external":
        return f"external:{ref.source_snapshot_id}"
    return f"{ref.ref_type}:?"


def derive_claim_id(claim_type: str, topic_id: str, question_ids, text: str,
                    citation_refs) -> str:
    """claim_id 派生（任务书 §8.3）：claim 类型 + 主题/问题 + 规范正文 + 引用身份。"""
    qids = sorted(list(question_ids))
    cids = sorted(citation_identity(r) for r in citation_refs)
    digest = sha256_json([claim_type, topic_id, qids, text, cids])
    return f"claim_{digest[:24]}"


def derive_section_version(task_id: str, claims, unresolved, *,
                           renderer_version: str, rules_version: str,
                           dependency_fingerprint: str = "") -> str:
    """section_version 派生（任务书 §8.4）：task + Claims + Unresolved + 版本 + 依赖指纹。

    dependency_fingerprint 编码 snapshot_id / required_formula_versions / 契约与任务依赖 /
    prompt/renderer/rules/worker 版本，必须进入派生 —— 同内容但任一依赖版本变化 → 新版本；
    完全相同输入 → 严格复用。缺省空串保持对旧调用方向后兼容。
    """
    claim_hashes = sorted(c.claim_id for c in claims)
    unresolved_hashes = sorted(u.unresolved_id for u in unresolved)
    digest = sha256_json([task_id, claim_hashes, unresolved_hashes,
                          renderer_version, rules_version, dependency_fingerprint])
    return f"secver_{digest[:24]}"


def derive_section_result_id(section_version: str) -> str:
    """section_result_id 由 section_version 稳定派生（内容寻址，幂等复用）。

    section_version 已编码 task + claims + unresolved + renderer/rules 版本，故
    同内容恒得同 section_result_id，可复用；任何依赖变化 → 新版本 → 新 result id。
    """
    return f"sr_{section_version}"


@dataclass(frozen=True)
class CommitResult:
    """commit_plan 的返回（不可变）。"""

    plan_id: str
    reused: bool
    current_switched: bool
    task_count: int


@dataclass(frozen=True)
class SectionCommitResult:
    """commit_section_result 的返回（不可变）。"""

    section_result_id: str
    reused: bool
    current_switched: bool
    claim_count: int
    unresolved_count: int


@dataclass(frozen=True)
class SectionClaim:
    """章节中的一个断言（任务书 §7.4；citation_refs 复用 harness CitationRef）。"""

    claim_id: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    text: str
    claim_type: str
    citation_refs: tuple[CitationRef, ...]
    derived_from_claim_ids: tuple[str, ...] = ()
    confidence: str = "low"
    as_of_date: str | None = None
    impact_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionUnresolved:
    """章节中一个未解决的问题缺口（任务书 §7.6）。"""

    unresolved_id: str
    section_id: str
    topic_id: str
    question_id: str | None
    state: str
    reason_code: str
    detail: str
    impact_scope: tuple[str, ...] = ()
    blocking_effects: tuple[str, ...] = ()
    attempted_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionIssue:
    """评估发现的问题（任务书 §7.8 局部）。"""

    issue_id: str
    rule_id: str
    severity: str
    location: str
    detail: str
    suggested_action: str = ""


@dataclass(frozen=True)
class ReworkTarget:
    """有界返工目标（任务书 §7.8 局部）。"""

    target_id: str
    target_kind: str     # question | topic | claim
    target_ref: str
    reason: str


@dataclass(frozen=True)
class SectionEvaluation:
    """章节评估结论（任务书 §7.8）。"""

    evaluation_id: str
    section_result_id: str
    rules_version: str
    evaluator_prompt_version: str
    rules_passed: bool
    llm_passed: bool | None
    decision: str
    issues: tuple[SectionIssue, ...] = ()
    rework_targets: tuple[ReworkTarget, ...] = ()
    evaluated_at: str = ""


@dataclass(frozen=True)
class SectionResult:
    """一个章节的最终产物（任务书 §7.5/§7.7）。"""

    section_result_id: str
    section_version: str
    task_id: str
    section_id: str
    status: str
    claims: tuple[SectionClaim, ...] = ()
    unresolved: tuple[SectionUnresolved, ...] = ()
    markdown: str = ""
    evaluation: SectionEvaluation | None = None
    source_run_ids: tuple[str, ...] = ()
    source_question_ids: tuple[str, ...] = ()
    dependency_fingerprint: str = ""
    created_at: str = ""


# ---------------------------------------------------------------------------
# 序列化（Store 落盘用；显式 tuple/list 边界转换，asdict 仅用于扁平 CitationRef）
# ---------------------------------------------------------------------------

def citation_to_dict(ref: CitationRef) -> dict:
    """CitationRef（扁平 dataclass）→ dict（asdict 全字段，None 保留）。"""
    return asdict(ref)


def citation_from_dict(d: dict) -> CitationRef:
    """dict → CitationRef（字段全可选，缺省 None）。"""
    return CitationRef(**{k: v for k, v in d.items()
                          if k in CitationRef.__dataclass_fields__})


def claim_to_dict(c: SectionClaim) -> dict:
    return {
        "claim_id": c.claim_id,
        "section_id": c.section_id,
        "topic_id": c.topic_id,
        "question_ids": list(c.question_ids),
        "text": c.text,
        "claim_type": c.claim_type,
        "citation_refs": [citation_to_dict(r) for r in c.citation_refs],
        "derived_from_claim_ids": list(c.derived_from_claim_ids),
        "confidence": c.confidence,
        "as_of_date": c.as_of_date,
        "impact_scope": list(c.impact_scope),
    }


def claim_from_dict(d: dict) -> SectionClaim:
    return SectionClaim(
        claim_id=d["claim_id"],
        section_id=d["section_id"],
        topic_id=d["topic_id"],
        question_ids=tuple(d.get("question_ids") or []),
        text=d.get("text") or "",
        claim_type=d["claim_type"],
        citation_refs=tuple(citation_from_dict(r)
                            for r in (d.get("citation_refs") or [])),
        derived_from_claim_ids=tuple(d.get("derived_from_claim_ids") or []),
        confidence=d.get("confidence") or "low",
        as_of_date=d.get("as_of_date"),
        impact_scope=tuple(d.get("impact_scope") or []),
    )


def unresolved_to_dict(u: SectionUnresolved) -> dict:
    return {
        "unresolved_id": u.unresolved_id,
        "section_id": u.section_id,
        "topic_id": u.topic_id,
        "question_id": u.question_id,
        "state": u.state,
        "reason_code": u.reason_code,
        "detail": u.detail,
        "impact_scope": list(u.impact_scope),
        "blocking_effects": list(u.blocking_effects),
        "attempted_sources": list(u.attempted_sources),
    }


def unresolved_from_dict(d: dict) -> SectionUnresolved:
    return SectionUnresolved(
        unresolved_id=d["unresolved_id"],
        section_id=d["section_id"],
        topic_id=d["topic_id"],
        question_id=d.get("question_id"),
        state=d["state"],
        reason_code=d["reason_code"],
        detail=d.get("detail") or "",
        impact_scope=tuple(d.get("impact_scope") or []),
        blocking_effects=tuple(d.get("blocking_effects") or []),
        attempted_sources=tuple(d.get("attempted_sources") or []),
    )


def issue_to_dict(i: SectionIssue) -> dict:
    return {
        "issue_id": i.issue_id,
        "rule_id": i.rule_id,
        "severity": i.severity,
        "location": i.location,
        "detail": i.detail,
        "suggested_action": i.suggested_action,
    }


def issue_from_dict(d: dict) -> SectionIssue:
    return SectionIssue(
        issue_id=d["issue_id"],
        rule_id=d["rule_id"],
        severity=d["severity"],
        location=d["location"],
        detail=d.get("detail") or "",
        suggested_action=d.get("suggested_action") or "",
    )


def rework_target_to_dict(t: ReworkTarget) -> dict:
    return {"target_id": t.target_id, "target_kind": t.target_kind,
            "target_ref": t.target_ref, "reason": t.reason}


def rework_target_from_dict(d: dict) -> ReworkTarget:
    return ReworkTarget(target_id=d["target_id"], target_kind=d["target_kind"],
                        target_ref=d["target_ref"], reason=d.get("reason") or "")


def evaluation_to_dict(e: SectionEvaluation) -> dict:
    return {
        "evaluation_id": e.evaluation_id,
        "section_result_id": e.section_result_id,
        "rules_version": e.rules_version,
        "evaluator_prompt_version": e.evaluator_prompt_version,
        "rules_passed": e.rules_passed,
        "llm_passed": e.llm_passed,
        "decision": e.decision,
        "issues": [issue_to_dict(i) for i in e.issues],
        "rework_targets": [rework_target_to_dict(t) for t in e.rework_targets],
        "evaluated_at": e.evaluated_at,
    }


def evaluation_from_dict(d: dict) -> SectionEvaluation:
    return SectionEvaluation(
        evaluation_id=d["evaluation_id"],
        section_result_id=d["section_result_id"],
        rules_version=d["rules_version"],
        evaluator_prompt_version=d["evaluator_prompt_version"],
        rules_passed=bool(d["rules_passed"]),
        llm_passed=d.get("llm_passed"),
        decision=d["decision"],
        issues=tuple(issue_from_dict(i) for i in (d.get("issues") or [])),
        rework_targets=tuple(rework_target_from_dict(t)
                             for t in (d.get("rework_targets") or [])),
        evaluated_at=d.get("evaluated_at") or "",
    )


def section_result_to_dict(r: SectionResult) -> dict:
    return {
        "section_result_id": r.section_result_id,
        "section_version": r.section_version,
        "task_id": r.task_id,
        "section_id": r.section_id,
        "status": r.status,
        "claims": [claim_to_dict(c) for c in r.claims],
        "unresolved": [unresolved_to_dict(u) for u in r.unresolved],
        "markdown": r.markdown,
        "evaluation": evaluation_to_dict(r.evaluation) if r.evaluation is not None else None,
        "source_run_ids": list(r.source_run_ids),
        "source_question_ids": list(r.source_question_ids),
        "dependency_fingerprint": r.dependency_fingerprint,
        "created_at": r.created_at,
    }


def section_result_from_dict(d: dict) -> SectionResult:
    ev = d.get("evaluation")
    return SectionResult(
        section_result_id=d["section_result_id"],
        section_version=d["section_version"],
        task_id=d["task_id"],
        section_id=d["section_id"],
        status=d["status"],
        claims=tuple(claim_from_dict(c) for c in (d.get("claims") or [])),
        unresolved=tuple(unresolved_from_dict(u) for u in (d.get("unresolved") or [])),
        markdown=d.get("markdown") or "",
        evaluation=evaluation_from_dict(ev) if ev is not None else None,
        source_run_ids=tuple(d.get("source_run_ids") or []),
        source_question_ids=tuple(d.get("source_question_ids") or []),
        dependency_fingerprint=d.get("dependency_fingerprint") or "",
        created_at=d.get("created_at") or "",
    )
