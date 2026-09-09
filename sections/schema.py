"""Phase 4 章节产物数据模型（Claim / Citation / Unresolved / Result / Evaluation）。

只定义声明式数据结构、状态枚举与确定性身份/版本派生，不含 I/O、不含业务计算。
- CitationRef 直接复用 Phase 3 harness.schema.CitationRef（任务书 §7.5），不造第二套引用。
- claim_type 第一阶段仅 fact | calculation | inference；不生成 recommendation（授信建议属 Phase 5）。
- COMPLETED_WITH_GAPS ≠ 严格通过；SECTION_BLOCKED 不阻止其他章节。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

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
                           renderer_version: str, rules_version: str) -> str:
    """section_version 派生（任务书 §8.4）：task + Claims + Unresolved + Renderer/Prompt/规则版本。"""
    claim_hashes = sorted(c.claim_id for c in claims)
    unresolved_hashes = sorted(u.unresolved_id for u in unresolved)
    digest = sha256_json([task_id, claim_hashes, unresolved_hashes,
                          renderer_version, rules_version])
    return f"secver_{digest[:24]}"


@dataclass(frozen=True)
class CommitResult:
    """commit_plan 的返回（不可变）。"""

    plan_id: str
    reused: bool
    current_switched: bool
    task_count: int


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
