"""Phase 4 纵向切片 — 主题研究编排（Evidence Matrix + 外部漏斗 + 预算 + Pack）。

.. warning::
    **EXPERIMENTAL / NOT_A_FORMAL_RUNTIME_PATH**：本模块是纵向预览的并行研究循环，
    **不是**正式 Phase 4 运行链。正式唯一运行链是
    ``sections.service.run_phase4 → company/industry worker → research_common
    → Router → harness.run_question → ToolRegistry``；正式 service/worker/新 Runner
    **不得依赖本模块**。本模块保留用于架构对照，不删除历史，不重写 git 历史（§四/§九）。

把 ``planning.topic_research`` 的查询计划在**有界预算**下落地的并行实现。

架构边界（用户最终约束，绝不违反）：
- **不调 ``harness.runtime.run_question``** 作为 aspect 补检动作（避免嵌套研究循环 +
  双重预算）。Harness 仅作为纯库复用 Citation / entailment / A5 / 来源规则。
- 本地 Evidence 检索走 ``ToolRegistry``（search_tables / search_evidence / inspect_evidence），
  读 evidence.db 只读（设置 ``estore._db_path``，不 init_db / 不迁移 / 不写）。
- 外部研究走 ``ToolRegistry``（search_external_sources / fetch_external_content /
  snapshot_external_source），snapshot 落 external_sources.db（研究缓存，允许写）。
- ``obtained`` 语义：aspect 必须有「经 CitationAuthority + 相关性/蕴含 + A5 + 适用来源
  政策（P3-B02 行业来源分级）验证的事实」，仅 search hits / fetch / snapshot /
  EvidenceStructuredFact 本身 ≠ obtained。

CLI: python -m sections.topic_research --self-check
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from harness import schema as HS
from planning.topic_research import AspectQuery, TopicQueryPlan, TopicResearchContext

log = logging.getLogger("sections.topic_research")

# 实验性并行研究循环（非正式运行链，§四/§九）。
EXPERIMENTAL_TOPIC_RESEARCH = True
NOT_A_FORMAL_RUNTIME_PATH = True

# 版本（进入 pack_id 内容寻址；预算/矩阵/漏斗/来源规则变化需递增）。
TOPIC_RESEARCH_VERSION = "topic-research-v1"

# 关键行业结论主题（复用 industry_source_policy 的 P3-B02 定义，不重定义）。
from sections.industry_source_policy import KEY_INDUSTRY_TOPICS  # noqa: E402


def _sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# TopicBudget（冻结默认值）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicBudget:
    """单个主题研究切片的有界预算（用户最终约束 §三 冻结默认值）。"""

    max_tool_calls: int
    max_tokens: int
    max_llm_calls: int
    max_elapsed_ms: int
    max_external_queries: int = 3
    max_candidate_fetches: int = 4
    max_snapshots: int = 4
    max_rounds_per_gap: int = 1        # 每个核心缺口最多补检 1 轮

    def as_dict(self) -> dict:
        return asdict(self)


# 三样本冻结预算（按 question_id 键控；industry_scale_cycle 额外限定外部漏斗）。
TOPIC_BUDGETS: dict[str, TopicBudget] = {
    "company_business_main": TopicBudget(
        max_tool_calls=12, max_tokens=24_000, max_llm_calls=2, max_elapsed_ms=120_000),
    "industry_scale_cycle": TopicBudget(
        max_tool_calls=12, max_tokens=32_000, max_llm_calls=3, max_elapsed_ms=180_000,
        max_external_queries=3, max_candidate_fetches=4, max_snapshots=4),
    "industry_risk_transmission": TopicBudget(
        max_tool_calls=16, max_tokens=48_000, max_llm_calls=4, max_elapsed_ms=180_000),
}

DEFAULT_TOPIC_BUDGET = TopicBudget(
    max_tool_calls=8, max_tokens=16_000, max_llm_calls=2, max_elapsed_ms=120_000)


def budget_for(question_id: str) -> TopicBudget:
    """按样本 question_id 取冻结预算；未知样本回退默认（保守）。"""
    return TOPIC_BUDGETS.get(question_id, DEFAULT_TOPIC_BUDGET)


# ---------------------------------------------------------------------------
# ExternalFunnel（外部转化漏斗 7 字段 + 分级 loss）
# ---------------------------------------------------------------------------

FUNNEL_LEVELS = (
    "external_queries",    # 1. 外部搜索次数
    "candidate_urls",      # 2. 候选 URL 数
    "fetched",             # 3. 成功抓取正文数
    "snapshotted",         # 4. 固化快照数
    "extracted_facts",     # 5. 从快照提取的事实数
    "validated_facts",     # 6. 通过权威校验的事实数
    "adopted_facts",       # 7. 采纳为 obtained 的事实数
)


@dataclass(frozen=True)
class ExternalFunnel:
    external_queries: int = 0
    candidate_urls: int = 0
    fetched: int = 0
    snapshotted: int = 0
    extracted_facts: int = 0
    validated_facts: int = 0
    adopted_facts: int = 0
    loss_reasons: tuple[tuple[str, str], ...] = ()  # (level, reason)

    def record_loss(self, level: str, reason: str) -> "ExternalFunnel":
        return ExternalFunnel(
            **{**asdict(self), "loss_reasons": (*self.loss_reasons, (level, reason))})

    def with_counts(self, **kw) -> "ExternalFunnel":
        return ExternalFunnel(**{**asdict(self), **kw})

    def as_dict(self) -> dict:
        d = asdict(self)
        d["loss_reasons"] = list(self.loss_reasons)
        return d


# ---------------------------------------------------------------------------
# Evidence Matrix
# ---------------------------------------------------------------------------

MATRIX_VALIDATION_STATUSES = ("PASS", "PARTIAL", "FAIL", "NOT_FOUND")


@dataclass(frozen=True)
class MatrixCell:
    """Evidence Matrix 一格（aspect 级，支持多事实 / 多来源）。8 字段。"""

    aspect_id: str
    aspect_text: str
    obtained: bool
    fact_ids: tuple[str, ...]                  # evidence_fact_id / external source_snapshot_id
    citation_refs: tuple[HS.CitationRef, ...]  # 多来源
    validation_status: str                     # PASS | PARTIAL | FAIL | NOT_FOUND
    loss_reasons: tuple[str, ...]
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "aspect_text": self.aspect_text,
            "obtained": self.obtained,
            "fact_ids": list(self.fact_ids),
            "citation_refs": [
                {k: v for k, v in asdict(r).items()} for r in self.citation_refs],
            "validation_status": self.validation_status,
            "loss_reasons": list(self.loss_reasons),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class VerifiedFact:
    """一条通过权威性校验、可被章节写入器引用的可引用事实/来源。"""

    fact_id: str
    fact_type: str                          # evidence_structured | external_source
    text: str
    citation_refs: tuple[HS.CitationRef, ...]
    source_grade: str | None = None         # external A/B/C/D
    published_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "text": self.text,
            "citation_refs": [asdict(r) for r in self.citation_refs],
            "source_grade": self.source_grade,
            "published_at": self.published_at,
        }


# ---------------------------------------------------------------------------
# 使用账本（本切片内）
# ---------------------------------------------------------------------------

@dataclass
class _Usage:
    tool_calls: int = 0
    external_queries: int = 0
    fetches: int = 0
    snapshots: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# TopicResearchPack
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicResearchPack:
    pack_id: str
    topic_id: str
    question_id: str
    context: TopicResearchContext
    budget: TopicBudget
    matrix: tuple[MatrixCell, ...]
    funnel: ExternalFunnel
    verified_facts: tuple[VerifiedFact, ...]
    evidence_facts: tuple[dict, ...]        # 本地 EvidenceStructuredFact（含金额/期间/板块）
    external_sources: tuple[dict, ...]      # 已采纳外部快照（标题/URL/等级/日期/正文摘录）
    usage: dict
    stop_reason: str
    created_at: str
    rejected_sources: tuple[dict, ...] = ()  # 权威通过但来源政策拒绝的外部快照（含 rejection_reason）
    funnel_trace: tuple[dict, ...] = ()      # 每候选 URL 的漏斗轨迹（query→fetch→snapshot→adopted/rejected）

    def obtained_aspect_ids(self) -> list[str]:
        return [c.aspect_id for c in self.matrix if c.obtained]

    def adopted_fact_ids(self) -> list[str]:
        """采纳事实 id = 全部 obtained MatrixCell 的 fact_ids 去重。

        这是 adopted-facts 防火墙的事实白名单：只有这些 id 能进入 ChapterWriter
        （写作 prompt、确定性表格都只能消费这些 id）。
        """
        ids: list[str] = []
        for c in self.matrix:
            if c.obtained:
                ids.extend(c.fact_ids)
        return list(dict.fromkeys(ids))

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "topic_id": self.topic_id,
            "question_id": self.question_id,
            "context": {
                "company_id": self.context.company_id,
                "company_name": self.context.company_name,
                "industry_names": list(self.context.industry_names),
                "report_as_of": self.context.report_as_of,
            },
            "budget": self.budget.as_dict(),
            "matrix": [c.as_dict() for c in self.matrix],
            "funnel": self.funnel.as_dict(),
            "verified_facts": [f.as_dict() for f in self.verified_facts],
            "evidence_facts": list(self.evidence_facts),
            "external_sources": list(self.external_sources),
            "rejected_sources": list(self.rejected_sources),
            "funnel_trace": list(self.funnel_trace),
            "usage": dict(self.usage),
            "stop_reason": self.stop_reason,
            "created_at": self.created_at,
        }


def derive_pack_id(*, topic_id: str, question_id: str, context: TopicResearchContext,
                   budget: TopicBudget, matrix: tuple[MatrixCell, ...],
                   funnel: ExternalFunnel, verified_facts: tuple[VerifiedFact, ...],
                   evidence_facts: tuple[dict, ...],
                   rejected_sources: tuple[dict, ...] = (),
                   funnel_trace: tuple[dict, ...] = ()) -> str:
    """pack_id 内容寻址（同输入 → 同 id，可幂等复用 / 冲突检测）。"""
    digest = _sha256_json({
        "version": TOPIC_RESEARCH_VERSION,
        "topic_id": topic_id,
        "question_id": question_id,
        "context": {
            "company_id": context.company_id,
            "company_name": context.company_name,
            "industry_names": list(context.industry_names),
            "report_as_of": context.report_as_of,
        },
        "budget": budget.as_dict(),
        "matrix": [c.as_dict() for c in matrix],
        "funnel": funnel.as_dict(),
        "verified_facts": [f.as_dict() for f in verified_facts],
        "evidence_facts": list(evidence_facts),
        "rejected_sources": list(rejected_sources),
        "funnel_trace": list(funnel_trace),
    })
    return f"pack_{digest[:32]}"


# ---------------------------------------------------------------------------
# 纯校验逻辑（可离线测试，无 I/O）
# ---------------------------------------------------------------------------

def aspect_fact_categories(aspect_text: str) -> tuple[str, ...]:
    """由 aspect 文本确定性派生期望的 fact 类别（revenue/cost/定性类别，通用词汇非公司特定）。

    A5 收入/成本语义：标「收入」的 aspect 必须由 revenue 事实背书，标「成本/毛利」的
    必须由 cost 事实背书；两者都标则都需。

    反虚假覆盖（Phase 4 纵向切片定点修复）：标「产品/服务/应用/下游」或
    「产业链/上下游/供应链/定位」的 aspect 必须由对应类别的定性 Evidence 背书——
    收入/成本结构化事实（``revenue_cost_category`` 只有 revenue/cost）不含这些类别，
    因此不会被误判 obtained（表5-10/5-11 不能单独支撑「产品应用」「产业链位置」）。
    均无标 → 不限。
    """
    t = aspect_text
    # supply_chain 必须先判（「上下游」含「下游」子串，先判产品/应用会把
    # 「上下游关系及产业链位置」误判为 product_application）。
    if any(k in t for k in ("产业链", "上下游", "供应链", "定位")):
        return ("supply_chain",)
    if any(k in t for k in ("产品", "服务", "应用", "下游", "功能", "场景")):
        return ("product_application",)
    has_rev = ("收入" in t) or ("营收" in t)
    has_cost = ("成本" in t) or ("毛利" in t) or ("盈利" in t)
    if has_rev and has_cost:
        return ("revenue", "cost")
    if has_cost:
        return ("cost",)
    if has_rev:
        return ("revenue",)
    return ()


def _valid_ref_count(refs: tuple[HS.CitationRef, ...], authority) -> int:
    """经 CitationAuthority 校验通过的引用数（软 warning 仍计通过）。"""
    n = 0
    for r in refs:
        try:
            v = authority.validate(r)
        except Exception:  # 查询失败 → fail-closed，不计数
            continue
        if v.valid:
            n += 1
    return n


def assess_local_cell(*, aspect: AspectQuery, facts, authority) -> MatrixCell:
    """本地 aspect 的 MatrixCell 判定（A5 + CitationAuthority）。

    ``facts`` 为 EvidenceStructuredFact 列表（已 build_evidence_facts）。obtained =
    至少 minimum_sources 个「类别匹配 + evidence 引用权威有效」的事实。
    """
    want = aspect_fact_categories(aspect.aspect_text)
    matched: list = []
    for f in facts:
        if want and f.revenue_cost_category not in want:
            continue  # A5 类别不匹配（如「收入」被成本事实背书 → 不采纳）
        ref = HS.CitationRef(ref_type="evidence", evidence_id=f.evidence_id,
                             evidence_fact_id=f.evidence_fact_id,
                             page_number=getattr(f, "page_number", None))
        try:
            verdict = authority.validate(ref)
        except Exception:
            verdict = None
        if verdict is not None and verdict.valid:
            matched.append((f, ref))
    obtained = len(matched) >= aspect.minimum_sources
    fact_ids = tuple(f.evidence_fact_id for f, _ in matched)
    refs = tuple(r for _, r in matched)
    loss: list[str] = []
    if not obtained:
        if not facts:
            loss.append("no_facts_extracted")
        elif want and not matched:
            if want == ("product_application",):
                loss.append("product_application_evidence_missing")
            elif want == ("supply_chain",):
                loss.append("supply_chain_evidence_missing")
            else:
                loss.append("a5_revenue_cost_mismatch")
        elif len(matched) < aspect.minimum_sources:
            loss.append("insufficient_validated_facts")
    status = "PASS" if obtained else ("PARTIAL" if matched else "NOT_FOUND")
    return MatrixCell(
        aspect_id=aspect.aspect_id, aspect_text=aspect.aspect_text, obtained=obtained,
        fact_ids=fact_ids, citation_refs=refs,
        validation_status=status, loss_reasons=tuple(loss),
        detail=f"matched={len(matched)} want_categories={want or 'any'}")


def _partition_external(sources, authority, *, topic_id: str):
    """外部来源 → (valid_sources, valid_refs, adopted_sources, rejected_sources)。

    分区规则（规则五「D 级不入正文」+ P3-B02 关键行业结论来源充分性）：
    - valid：CitationAuthority.validate 通过；
    - adopted：valid 且可入正文 —— D/unknown 级一律拒绝；关键行业主题还需来源充分
      （≥1 A/B 或 ≥2 独立 C），单一 C / 仅 D 时全部 valid 来源都不 adopted；
    - rejected_sources：[(source, reason)] 权威通过但来源政策拒绝（含拒绝理由）。
    """
    from sections.industry_source_policy import assess_industry_sources

    valid_sources: list = []
    valid_refs: list[HS.CitationRef] = []
    for s in sources:
        ref = HS.CitationRef(ref_type="external", source_snapshot_id=s.source_snapshot_id)
        try:
            verdict = authority.validate(ref)
        except Exception:
            verdict = None
        if verdict is not None and verdict.valid:
            valid_sources.append(s)
            valid_refs.append(ref)

    adopted: list = []
    rejected: list = []  # (source, reason)
    if topic_id in KEY_INDUSTRY_TOPICS:
        assess = assess_industry_sources(valid_sources)
        if not assess.key_conclusion_supported:
            reason = f"insufficient_industry_sources:{assess.key_conclusion_reason}"
            rejected = [(s, reason) for s in valid_sources]
        else:
            for s in valid_sources:
                g = (s.source_grade or "unknown")
                if g in ("D", "unknown"):
                    rejected.append((s, "d_grade_not_in_body"))
                else:
                    adopted.append(s)
    else:
        for s in valid_sources:
            g = (s.source_grade or "unknown")
            if g in ("D", "unknown"):
                rejected.append((s, "d_grade_not_in_body"))
            else:
                adopted.append(s)

    return valid_sources, tuple(valid_refs), adopted, rejected


def _cell_from_partition(*, aspect: AspectQuery, valid_sources, valid_refs,
                         adopted_sources, rejected_sources,
                         topic_id: str) -> MatrixCell:
    """由分区结果构建 MatrixCell（fact_ids 只含 adopted 来源 = 防火墙白名单）。"""
    loss: list[str] = []
    if not valid_sources:
        obtained = False
        status = "NOT_FOUND"
        loss.append("no_validated_external_source")
    elif not adopted_sources:
        obtained = False
        status = "FAIL"
        for reason in dict.fromkeys(r for _, r in rejected_sources):
            loss.append(reason)
    elif topic_id in KEY_INDUSTRY_TOPICS:
        obtained = True
        status = "PASS"
    else:
        obtained = len(adopted_sources) >= aspect.minimum_sources
        status = "PASS" if obtained else "FAIL"
        if not obtained:
            loss.append("insufficient_adopted_sources")

    fact_ids = tuple(s.source_snapshot_id for s in adopted_sources) if obtained else ()
    adopted_ids = {s.source_snapshot_id for s in adopted_sources}
    adopted_refs = tuple(r for s, r in zip(valid_sources, valid_refs)
                         if s.source_snapshot_id in adopted_ids)
    return MatrixCell(
        aspect_id=aspect.aspect_id, aspect_text=aspect.aspect_text, obtained=obtained,
        fact_ids=fact_ids, citation_refs=adopted_refs,
        validation_status=status, loss_reasons=tuple(loss),
        detail=(f"valid_sources={len(valid_sources)} adopted={len(fact_ids)} "
                f"rejected_policy={len(rejected_sources)}"))


def assess_external_cell(*, aspect: AspectQuery, sources, authority,
                         topic_id: str) -> MatrixCell:
    """外部 aspect 的 MatrixCell 判定（P3-B02 行业来源分级 + CitationAuthority）。

    ``sources`` 为已采纳候选（元素暴露 source_grade / canonical_url / published_at /
    source_snapshot_id）。obtained = 权威校验通过 + 关键行业结论来源充分（≥1 A/B 或
    ≥2 独立 C）；非关键主题需 ≥1 通过校验的 body-eligible 来源。D/unknown 级来源
    不入 fact_ids（规则五「D 级不入正文」）。
    """
    valid_sources, valid_refs, adopted_sources, rejected_sources = _partition_external(
        sources, authority, topic_id=topic_id)
    return _cell_from_partition(
        aspect=aspect, valid_sources=valid_sources, valid_refs=valid_refs,
        adopted_sources=adopted_sources, rejected_sources=rejected_sources,
        topic_id=topic_id)


# ---------------------------------------------------------------------------
# 本地证据派生（只读，复用 B1/B2 生产接线）
# ---------------------------------------------------------------------------

def _build_local_facts(materials, subject: str | None) -> list:
    """inspected evidence → EvidenceStructuredFact（金额列 revenue/cost）。"""
    from financial_v2 import evidence_facts as EF
    return EF.build_evidence_facts(materials, subject=subject or "")


# ---------------------------------------------------------------------------
# Store 路径准备（只读上游 + 写缓存）
# ---------------------------------------------------------------------------

def prepare_stores(ev_db: str | Path, fin_db: str | Path,
                   ext_db: str | Path) -> None:
    """为 ``run_topic`` 的工具调用准备 Store 路径。

    - evidence.db / financial_v2.db 只读：仅 set ``_db_path``，**不 init_db / 不建表 /
      不写**（读操作不落任何 DDL / DML，保证上游库 hash/mtime 不变）；
    - external_sources.db 是研究缓存（快照落库），允许写：用 ``init_db`` 保证表存在。
    """
    from evidence import store as estore
    from external_v2 import store as extstore
    from financial_v2 import store as fstore

    estore._db_path = Path(ev_db)
    fstore._db_path = Path(fin_db)
    extstore.init_db(ext_db)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def run_topic(
    plan: TopicQueryPlan,
    *,
    question_id: str,
    budget: TopicBudget,
    company_id: str,
    authority,
    registry,
    subject: str | None = None,
    run_id: str | None = None,
    source_intent: dict | None = None,
) -> TopicResearchPack:
    """把查询计划在有界预算下落为材料并校验，产出 TopicResearchPack。

    缺口驱动（每 aspect 至多补检 1 轮，受 max_tool_calls / max_external_queries /
    max_candidate_fetches / max_snapshots 硬上限约束）：
    - 本地 aspect → search_tables（空则 search_evidence）→ inspect → build_evidence_facts
      → assess_local_cell；
    - 外部 aspect → search_external_sources → fetch_external_content → snapshot_external_source
      → assess_external_cell（更新 ExternalFunnel + 分级 loss）。
    """
    usage = _Usage()
    t0 = time.monotonic()
    aspect_list = [a for a in plan.aspects if a.question_id == question_id] or list(plan.aspects)

    matrix: list[MatrixCell] = []
    verified: list[VerifiedFact] = []
    evidence_fact_dicts: list[dict] = []
    external_source_dicts: list[dict] = []
    rejected_source_dicts: list[dict] = []
    funnel_trace_entries: list[dict] = []
    funnel = ExternalFunnel()

    def _budget_exhausted() -> bool:
        return (usage.tool_calls >= budget.max_tool_calls
                or usage.external_queries >= budget.max_external_queries)

    def _record_tool(n: int = 1) -> None:
        usage.tool_calls += n

    for aspect in aspect_list:
        if _budget_exhausted():
            break

        if aspect.external_query is not None:
            # ---- 外部路径 ----
            cell, f, srcs, rejs, trace = _run_external_aspect(
                aspect, plan, budget, authority, registry, company_id, usage, funnel,
                source_intent=source_intent)
            funnel = f
            matrix.append(cell)
            funnel_trace_entries.extend(trace)
            if srcs:
                external_source_dicts.extend(srcs)
                for s in srcs:
                    verified.append(VerifiedFact(
                        fact_id=s["source_snapshot_id"], fact_type="external_source",
                        text=s.get("title") or s.get("canonical_url") or "",
                        citation_refs=(HS.CitationRef(
                            ref_type="external", source_snapshot_id=s["source_snapshot_id"]),),
                        source_grade=s.get("source_grade"),
                        published_at=s.get("published_at")))
            rejected_source_dicts.extend(rejs)
        else:
            # ---- 本地路径 ----
            cell, facts, fdicts = _run_local_aspect(
                aspect, budget, authority, registry, company_id, usage, subject)
            matrix.append(cell)
            if facts:
                evidence_fact_dicts.extend(fdicts)
                for fact in facts:
                    verified.append(VerifiedFact(
                        fact_id=fact.evidence_fact_id, fact_type="evidence_structured",
                        text=fact_text(fact),
                        citation_refs=(HS.CitationRef(
                            ref_type="evidence", evidence_id=fact.evidence_id,
                            evidence_fact_id=fact.evidence_fact_id),)))

    usage.elapsed_ms = int((time.monotonic() - t0) * 1000)
    stop_reason = ("budget_exhausted" if _budget_exhausted() else "completed")

    pack_id = derive_pack_id(
        topic_id=plan.topic_id, question_id=question_id, context=plan.context,
        budget=budget, matrix=tuple(matrix), funnel=funnel,
        verified_facts=tuple(verified), evidence_facts=tuple(evidence_fact_dicts),
        rejected_sources=tuple(rejected_source_dicts),
        funnel_trace=tuple(funnel_trace_entries))

    return TopicResearchPack(
        pack_id=pack_id, topic_id=plan.topic_id, question_id=question_id,
        context=plan.context, budget=budget, matrix=tuple(matrix), funnel=funnel,
        verified_facts=tuple(verified), evidence_facts=tuple(evidence_fact_dicts),
        external_sources=tuple(external_source_dicts),
        rejected_sources=tuple(rejected_source_dicts),
        funnel_trace=tuple(funnel_trace_entries),
        usage=usage.as_dict(), stop_reason=stop_reason, created_at=_utcnow())


def fact_text(fact) -> str:
    """EvidenceStructuredFact → 规范正文（金额/板块/期间/类别）。"""
    seg = getattr(fact, "business_segment", "") or ""
    period = getattr(fact, "period", "") or ""
    cat = getattr(fact, "revenue_cost_category", "") or ""
    val = getattr(fact, "value", None)
    label = {"revenue": "营业收入", "cost": "营业成本"}.get(cat, cat)
    return f"{period} {seg} {label} {val}元".replace("None", "—")


def _run_local_aspect(aspect, budget, authority, registry, company_id, usage,
                      subject) -> tuple[MatrixCell, list, list]:
    from tools import contracts as TC

    query = aspect.local_query or aspect.aspect_text
    evidence_ids: list[str] = []
    for tool_name in ("search_tables", "search_evidence"):
        call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name=tool_name,
                           arguments={"company_id": company_id, "query": query, "k": 8},
                           idempotency_key=uuid.uuid4().hex, need_id="topic", batch_id="topic")
        res = registry.execute(call, route="STANDARD_RAG", run_id="topic_research")
        usage.tool_calls += 1
        if res.status in ("SUCCESS", "PARTIAL") and res.evidence_ids:
            evidence_ids = res.evidence_ids[:8]
            break

    materials = []
    for eid in evidence_ids:
        if usage.tool_calls >= budget.max_tool_calls:
            break
        call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name="inspect_evidence",
                           arguments={"evidence_id": eid},
                           idempotency_key=uuid.uuid4().hex, need_id="topic", batch_id="topic")
        res = registry.execute(call, route="DIRECT_EVIDENCE", run_id="topic_research")
        usage.tool_calls += 1
        if res.status == "SUCCESS":
            d = res.data or {}
            materials.append(HS.InspectedMaterial(
                evidence_id=d.get("evidence_id", eid),
                document_id=d.get("document_id", ""),
                source_name=d.get("source_name", ""),
                source_type=d.get("source_type", ""),
                page_number=d.get("page_number"),
                section_path=d.get("section_path", ""),
                evidence_type=d.get("evidence_type", ""),
                report_period=d.get("report_period"),
                text=d.get("text", "") or "",
                is_snippet=False))

    facts = _build_local_facts(materials, subject) if materials else []
    fdicts = [f.to_dict() for f in facts]
    cell = assess_local_cell(aspect=aspect, facts=facts, authority=authority)
    return cell, facts, fdicts


def _run_external_aspect(aspect, plan, budget, authority, registry, company_id,
                         usage, funnel, source_intent=None
                         ) -> tuple[MatrixCell, ExternalFunnel, list, list, list]:
    """外部 aspect 的完整漏斗：query → fetch → snapshot → 权威/来源政策分区 → cell。

    返回 (cell, funnel, adopted_srcs, rejected_srcs, funnel_trace)：
    - adopted_srcs：仅 cell.obtained 时非空（D/unknown 级不入正文，规则五）；
    - rejected_srcs：权威通过但来源政策拒绝的快照（含 rejection_reason）；
    - funnel_trace：每候选 URL 一条轨迹（query / fetch_failed / snapshot_failed /
      adopted / rejected_policy / rejected_authority）。

    ``source_intent``（可选）：{include, exclude} 博查站点限定（source-intent 配置，
    政府/监管、交易所/法定披露等），透传 search_external_sources。
    """
    from tools import contracts as TC

    query = aspect.external_query or aspect.aspect_text
    candidate_urls: list[dict] = []
    trace: list[dict] = []

    def _entry(stage, *, url="", source_grade=None, source_snapshot_id="",
               reason="", title="") -> dict:
        return {"stage": stage, "url": url, "source_grade": source_grade,
                "source_snapshot_id": source_snapshot_id, "reason": reason,
                "query": query, "title": title}

    if usage.external_queries < budget.max_external_queries:
        search_args: dict = {"query": query, "limit": 5}
        if source_intent:
            if source_intent.get("include"):
                search_args["include"] = source_intent["include"]
            if source_intent.get("exclude"):
                search_args["exclude"] = source_intent["exclude"]
        call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name="search_external_sources",
                           arguments=search_args,
                           idempotency_key=uuid.uuid4().hex, need_id="topic", batch_id="topic")
        res = registry.execute(call, route="EXTERNAL_RESEARCH", run_id="topic_research")
        usage.tool_calls += 1
        usage.external_queries += 1
        funnel = funnel.with_counts(external_queries=funnel.external_queries + 1)
        if res.status in ("SUCCESS", "PARTIAL"):
            for r in (res.data or {}).get("results", []):
                candidate_urls.append(r)
        if not candidate_urls:
            funnel = funnel.record_loss("candidate_urls", "search_empty")
            trace.append(_entry("query", reason="search_empty"))
    else:
        funnel = funnel.record_loss("external_queries", "budget_exhausted")
        trace.append(_entry("query", reason="budget_exhausted"))

    funnel = funnel.with_counts(candidate_urls=funnel.candidate_urls + len(candidate_urls))

    # 采集来源对象（供权威/来源政策分区使用）。
    gathered: list = []

    for cand in candidate_urls:
        url = cand.get("url")
        title = cand.get("title") or ""
        grade = cand.get("source_grade")
        if not url:
            funnel = funnel.record_loss("fetched", "no_url")
            trace.append(_entry("fetch_failed", reason="no_url", title=title))
            continue
        if usage.tool_calls >= budget.max_tool_calls:
            funnel = funnel.record_loss("fetched", "budget_exhausted")
            trace.append(_entry("fetch_failed", url=url, source_grade=grade,
                                reason="budget_exhausted", title=title))
            break
        if usage.fetches >= budget.max_candidate_fetches:
            funnel = funnel.record_loss("fetched", "budget_fetch_cap")
            trace.append(_entry("fetch_failed", url=url, source_grade=grade,
                                reason="budget_fetch_cap", title=title))
            break

        call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name="fetch_external_content",
                           arguments={"url": url},
                           idempotency_key=uuid.uuid4().hex, need_id="topic", batch_id="topic")
        res = registry.execute(call, route="EXTERNAL_RESEARCH", run_id="topic_research")
        usage.tool_calls += 1
        usage.fetches += 1
        if res.status != "SUCCESS":
            reason = res.error_code or "fetch_failed"
            funnel = funnel.record_loss("fetched", reason)
            trace.append(_entry("fetch_failed", url=url, source_grade=grade,
                                reason=reason, title=title))
            continue
        funnel = funnel.with_counts(fetched=funnel.fetched + 1)

        content_text = (res.data or {}).get("content_text") or ""
        if not content_text.strip():
            funnel = funnel.record_loss("snapshotted", "empty_content")
            trace.append(_entry("snapshot_failed", url=url, source_grade=grade,
                                reason="empty_content", title=title))
            continue
        if usage.snapshots >= budget.max_snapshots:
            funnel = funnel.record_loss("snapshotted", "budget_snapshot_cap")
            trace.append(_entry("snapshot_failed", url=url, source_grade=grade,
                                reason="budget_snapshot_cap", title=title))
            break

        snap_args = {
            "company_id": company_id,
            "canonical_url": (res.data or {}).get("canonical_url") or url,
            "content_text": content_text,
            "title": title,
            "snippet": cand.get("snippet") or "",
            "published_at": cand.get("published_at"),
            "provider": (res.data or {}).get("provider") or "bocha",
            "content_type": (res.data or {}).get("content_type"),
            "http_status": (res.data or {}).get("http_status"),
            "content_hash": (res.data or {}).get("content_hash"),
            "source_grade": grade,
        }
        call = TC.ToolCall(call_id=uuid.uuid4().hex, tool_name="snapshot_external_source",
                           arguments=snap_args,
                           idempotency_key=uuid.uuid4().hex, need_id="topic", batch_id="topic")
        res = registry.execute(call, route="EXTERNAL_RESEARCH", run_id="topic_research")
        usage.tool_calls += 1
        usage.snapshots += 1
        if res.status != "SUCCESS":
            reason = res.error_code or "snapshot_failed"
            funnel = funnel.record_loss("snapshotted", reason)
            trace.append(_entry("snapshot_failed", url=url, source_grade=grade,
                                reason=reason, title=title))
            continue
        funnel = funnel.with_counts(snapshotted=funnel.snapshotted + 1)
        sid = (res.data or {}).get("source_snapshot_id")
        if not sid:
            funnel = funnel.record_loss("extracted_facts", "no_snapshot_id")
            trace.append(_entry("snapshot_failed", url=url, source_grade=grade,
                                reason="no_snapshot_id", title=title))
            continue
        funnel = funnel.with_counts(extracted_facts=funnel.extracted_facts + 1)
        gathered.append(_SourceObj(
            source_snapshot_id=sid, source_grade=grade,
            canonical_url=url, published_at=cand.get("published_at"),
            content_excerpt=content_text[:2000], title=title,
            snippet=cand.get("snippet") or ""))

    # 权威 + 来源政策分区（一次 validate，不重复）。
    valid_sources, valid_refs, adopted_sources, rejected_sources = _partition_external(
        gathered, authority, topic_id=plan.topic_id)
    cell = _cell_from_partition(
        aspect=aspect, valid_sources=valid_sources, valid_refs=valid_refs,
        adopted_sources=adopted_sources, rejected_sources=rejected_sources,
        topic_id=plan.topic_id)

    # 漏斗：validated = 权威通过数；adopted = obtained 时采纳数，否则逐原因记录 loss。
    funnel = funnel.with_counts(validated_facts=funnel.validated_facts + len(valid_sources))
    if cell.obtained:
        funnel = funnel.with_counts(adopted_facts=funnel.adopted_facts + len(cell.fact_ids))
    else:
        for reason in cell.loss_reasons:
            funnel = funnel.record_loss("adopted", reason)

    # 轨迹终态：snapshotted 来源按分区结果标注 adopted / rejected_policy / rejected_authority。
    adopted_ids = {s.source_snapshot_id for s in adopted_sources}
    rejected_by_sid = {s.source_snapshot_id: reason for s, reason in rejected_sources}
    for s in gathered:
        sid = s.source_snapshot_id
        if sid in adopted_ids:
            trace.append(_entry("adopted", url=s.canonical_url, source_grade=s.source_grade,
                                source_snapshot_id=sid, title=s.title))
        elif sid in rejected_by_sid:
            trace.append(_entry("rejected_policy", url=s.canonical_url,
                                source_grade=s.source_grade, source_snapshot_id=sid,
                                reason=rejected_by_sid[sid], title=s.title))
        else:
            trace.append(_entry("rejected_authority", url=s.canonical_url,
                                source_grade=s.source_grade, source_snapshot_id=sid,
                                reason="authority_validation_failed", title=s.title))

    def _src_dict(s):
        return {
            "source_snapshot_id": s.source_snapshot_id,
            "source_grade": s.source_grade,
            "canonical_url": s.canonical_url,
            "published_at": s.published_at,
            "title": s.title,
            "snippet": s.snippet,
            "content_excerpt": s.content_excerpt,
        }

    adopted_srcs = [_src_dict(s) for s in adopted_sources] if cell.obtained else []
    rejected_srcs = [
        {**_src_dict(s), "rejection_reason": reason} for s, reason in rejected_sources]

    return cell, funnel, adopted_srcs, rejected_srcs, trace


@dataclass(frozen=True)
class _SourceObj:
    source_snapshot_id: str
    source_grade: str | None
    canonical_url: str
    published_at: str | None
    content_excerpt: str = ""
    title: str = ""
    snippet: str = ""


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，无 I/O）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    # 用注入的假 authority 证明纯校验逻辑（A5 / 来源分级 / 预算默认值）。
    class _Auth:
        def validate(self, ref):
            class V:
                valid = True
            return V()

    class _Fact:
        def __init__(self, fid, eid, cat, seg, period, val):
            self.evidence_fact_id = fid
            self.evidence_id = eid
            self.revenue_cost_category = cat
            self.business_segment = seg
            self.period = period
            self.value = val
            self.page_number = 1

        def to_dict(self):
            return {"evidence_fact_id": self.evidence_fact_id,
                    "revenue_cost_category": self.revenue_cost_category}

    from planning.topic_research import derive_topic_queries
    from planning import schema as PS

    ctx = TopicResearchContext(company_id="300750", company_name="宁德时代",
                               industry_names=("动力电池",), report_as_of="2026-03-31")
    q = PS.PlannedQuestion(
        question_id="company_business_main", question="主营", priority="P0",
        topic_id="company_business",
        required_aspects=("各业务收入及收入占比", "各业务成本与毛利构成"),
        evidence_requirements=({"evidence_kind": "table",
                                "source_classes": ["company_industry"],
                                "minimum_sources": 1, "required_fields": []},))
    task = PS.SectionTask(
        task_id="t", plan_id="p", section_id="company", title="公司", purpose="",
        research_policy="harness", topic_ids=("company_business",), questions=(q,),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=())
    plan = derive_topic_queries(task, "company_business", ctx)

    auth = _Auth()
    rev_fact = _Fact("ef-rev", "e1", "revenue", "动力电池系统", "2025-12-31", 100)
    cst_fact = _Fact("ef-cst", "e2", "cost", "动力电池系统", "2025-12-31", 80)

    rev_cell = assess_local_cell(aspect=plan.aspects[0], facts=[rev_fact], authority=auth)
    # 收入 aspect 用成本事实背书 → A5 mismatch（不应 obtained）。
    mismatch_cell = assess_local_cell(aspect=plan.aspects[0], facts=[cst_fact], authority=auth)

    from sections.industry_source_policy import CitedSource

    class _Src:
        def __init__(self, sid, grade, url, pub):
            self.source_snapshot_id = sid
            self.source_grade = grade
            self.canonical_url = url
            self.published_at = pub

    scale_q = PS.PlannedQuestion(
        question_id="industry_scale_cycle", question="规模", priority="P0",
        topic_id="industry_scale_cycle", required_aspects=("行业规模",),
        evidence_requirements=({"evidence_kind": "web", "source_classes": ["external"],
                                "minimum_sources": 1, "required_fields": []},))
    scale_task = PS.SectionTask(
        task_id="t2", plan_id="p2", section_id="industry", title="行业", purpose="",
        research_policy="harness", topic_ids=("industry_scale_cycle",), questions=(scale_q,),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=())
    scale_plan = derive_topic_queries(scale_task, "industry_scale_cycle", ctx)
    single_c = assess_external_cell(
        aspect=scale_plan.aspects[0],
        sources=[_Src("s1", "C", "https://eastmoney.com/a", "2025-01-01")],
        authority=auth, topic_id="industry_scale_cycle")
    two_c = assess_external_cell(
        aspect=scale_plan.aspects[0],
        sources=[_Src("s1", "C", "https://eastmoney.com/a", "2025-01-01"),
                 _Src("s2", "C", "https://thepaper.cn/b", "2025-02-01")],
        authority=auth, topic_id="industry_scale_cycle")

    return {
        "version": TOPIC_RESEARCH_VERSION,
        "budget_company_business_main": budget_for("company_business_main").as_dict(),
        "budget_industry_scale_cycle": budget_for("industry_scale_cycle").as_dict(),
        "budget_industry_risk_transmission": budget_for("industry_risk_transmission").as_dict(),
        "aspect_revenue_categories": aspect_fact_categories("各业务收入及收入占比"),
        "aspect_cost_categories": aspect_fact_categories("各业务成本与毛利构成"),
        "revenue_aspect_obtained_with_revenue_fact": rev_cell.obtained,
        "revenue_aspect_rejects_cost_fact": (not mismatch_cell.obtained
                                             and "a5_revenue_cost_mismatch"
                                             in mismatch_cell.loss_reasons),
        "single_c_key_conclusion_not_obtained": not single_c.obtained,
        "two_independent_c_key_conclusion_obtained": two_c.obtained,
        "funnel_levels": list(FUNNEL_LEVELS),
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.topic_research",
        description="主题研究编排自检（纯函数，不读库、不调 LLM、不联网）")
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
