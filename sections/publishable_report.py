"""Phase 4 发布态报告收口（只读、确定性投影层）。

把既有 Phase 4 审计产物（``SectionResult`` 的 claims / unresolved / evaluation）**只读投影**
成可读、可截图、Phase-5-ready 的「发布态研究报告」。严格边界（任务书 Phase 4 收口 + 10 项定点修订）：

- **不重跑** Worker / Router / Retriever / Web Search / Financial Pipeline，不联网，不写上游 DB，
  不改写原始 SectionResult。
- 发布正文**只由「合格 Claim」组装**（正向支持证明，见 :func:`classify_claim`），不复刻 ``section.md``。
- ``publication_id`` 内容寻址绑定全部输出依赖；同 id 且字段一致 → ``reused=true``；不一致 →
  fail-closed 冲突；**绝不覆盖已有目录**；临时目录 + 原子发布。
- 确定性编辑器为默认（按 topic 合并流式段落，逐 Claim 带 ``<!-- claim:id -->`` marker）；
  ``--use-editor`` 启用受限 LLM 编辑（每章 ≤1 次，仅合并/重排/衔接，非法输出回退确定性）。
- 年度/季度可比性按**公式语义**（``financial_v2.formulas`` 的 ``period_requirement``），
  不用 OPER_/PROF_/GROWTH_/CASH_ 前缀启发式。

CLI:
    python -m sections.publishable_report --run-id run_20260911T_jsonfix \
        --results-root evaluation/results --out-root evaluation/results
    python -m sections.publishable_report --self-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from contracts import loader as CL
from contracts import schema as CS
from financial_v2 import formulas as FV
from harness import schema as HS
from routing import schema as RS
from sections import artifact_loader as AL
from sections import citation_authority as CA
from sections import schema as SS
from sections import service as SV

logger = logging.getLogger("sections.publishable_report")

# ---------------------------------------------------------------------------
# 版本常量（进入 publication_id，任一变化 → 新 id）
# ---------------------------------------------------------------------------

PUBLICATION_SCHEMA_VERSION = "p4-pub-1"
CITATION_DISPLAY_VERSION = "p4-citation-display-1"
RENDERER_VERSION = "p4-renderer-deterministic-1"
EDITOR_VERSION = "p4-editor-deterministic-1"
LLM_EDITOR_PROMPT_VERSION = "publication_editor-1"
PERIOD_POLICY_VERSION = "p4-period-comparability-v1"
EDITOR_PROMPT_NAME = "publication_editor"

DEFAULT_CONTRACTS_PATH = "templates/contracts/standard_v2.yaml"
DEFAULT_EV_DB = "data/evidence.db"
DEFAULT_FIN_DB = "data/financial_v2.db"
DEFAULT_EXT_DB = "data/external_sources.db"

# 正式发布状态（README 语义）。
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED_FOR_PUBLICATION"
STATUS_LABELS = {
    STATUS_READY: "可正式出具",
    STATUS_BLOCKED: "草稿／待人工确认，不可正式出具",
}

# 核心影响范围（缺口优先级第 2 档）。
CORE_IMPACT = {"subject", "solvency", "key_financial", "credit_scheme"}

# 人类可读文档类型标签（真实 source_type 元数据 → 中文；仅已知值，绝不猜测）。
SOURCE_TYPE_LABELS = {
    "annual_report": "年度报告",
    "debt_circular": "债项说明书",
    "other": "",          # "other" 不附加标签（不猜测为年报）
}

# 期间可比性类别（formula_period_comparability 返回值）。
COMP_INCOMPARABLE = {"ytd_flow_amount", "flow_stock_ratio", "annual_growth"}


class PublicationError(RuntimeError):
    """发布 fail-closed 错误（身份冲突 / 目录已存在 / 缺依赖）。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitationCheck:
    """单条引用的正向证明核验结果（只读）。"""

    ref_type: str
    valid: bool
    reason: str | None = None


@dataclass(frozen=True)
class ClaimDisposition:
    """单条 Claim 的入选/排除判定。"""

    claim_id: str
    claim_type: str
    adopted: bool
    reason: str | None = None        # 排除原因码（adopted=True 时 None）
    support: str | None = None       # 正向证明出处（adopted=True 时非空）
    citation_checks: tuple[CitationCheck, ...] = ()


@dataclass(frozen=True)
class ExactConflict:
    """跨章节精确冲突：同 (item_code|formula_id, period, scope, currency) 多快照。"""

    key: str
    snapshot_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class SectionPublication:
    """单章节发布态投影。"""

    section_id: str
    title: str
    publication_status: str
    blocking_reasons: tuple[str, ...]
    adopted_claims: tuple[SS.SectionClaim, ...]
    excluded: tuple[ClaimDisposition, ...]
    key_gaps: tuple[SS.SectionUnresolved, ...]
    remaining_gaps: tuple[SS.SectionUnresolved, ...]
    body: str


@dataclass(frozen=True)
class Publication:
    """完整发布态产物（不可变，内容寻址）。"""

    publication_id: str
    schema_version: str
    run_id: str
    plan_id: str
    manifest_id: str
    job_id: str
    company_id: str
    company_name: str
    report_as_of: str
    sections: tuple[SectionPublication, ...]
    exact_conflicts: tuple[ExactConflict, ...]
    potential_conflict_note: str
    editor_audit: dict
    fingerprint: dict
    reused: bool
    created_at: str


# ---------------------------------------------------------------------------
# 期间/可比性（公式语义，版本化政策 p4-period-comparability-v1）
# ---------------------------------------------------------------------------

def _period_kind(period: str | None) -> str:
    """把期间字符串归为 annual / interim / unknown（确定性，不猜口径）。"""
    if not period:
        return "unknown"
    if period.endswith("-12-31"):
        return "annual"
    return "interim"


def formula_period_comparability(formula_id: str) -> str:
    """按公式 ``period_requirement`` + 舍入单位返回可比性类别（确定性）。

    - ``end`` → ``point_in_time``（时点，各期间可比）
    - ``flow`` + yuan → ``ytd_flow_amount``（年累计流量，年度 vs 季度不可比）
    - ``flow`` + ratio/percent → ``same_basis_flow_ratio``（同口径流量比率）
    - ``flow/end`` / ``flow/avg`` → ``flow_stock_ratio``（流量/存量，年度 vs 季度不可比）
    - ``yoy_*`` → ``annual_growth``（年度同比，季报 NOT_APPLICABLE）
    """
    fd = FV.get_formula(formula_id)
    req = fd.period_requirement
    unit = fd.rounding_rule.split(":", 1)[0]
    if req == "end":
        return "point_in_time"
    if req == "flow":
        return "ytd_flow_amount" if unit == "yuan" else "same_basis_flow_ratio"
    if req in ("flow/end", "flow/avg"):
        return "flow_stock_ratio"
    if req in ("yoy_flow", "yoy_end"):
        return "annual_growth"
    return "unknown"


def claim_period_note(claim: SS.SectionClaim) -> str | None:
    """若某 Claim 的 structured 引用跨「年度 vs 季度」且口径不可比 → 返回提示串；否则 None。"""
    by_formula: dict[str, set[str]] = {}
    for ref in claim.citation_refs:
        if ref.ref_type != "structured":
            continue
        ident = ref.formula_id or ref.item_code
        if not ident:
            continue
        by_formula.setdefault(ident, set()).add(_period_kind(ref.period))
    notes: list[str] = []
    for ident, kinds in by_formula.items():
        if "annual" in kinds and "interim" in kinds:
            comp = formula_period_comparability(ident) if ref_formula_exists(ident) else "unknown"
            if comp in COMP_INCOMPARABLE:
                notes.append(f"{ident}（年度/季度口径不可直接比较）")
    return "；".join(notes) if notes else None


def ref_formula_exists(ident: str) -> bool:
    """ident 是否命中 Formula Registry（item_code 不在 registry 时安全回退）。"""
    try:
        FV.get_formula(ident)
        return True
    except KeyError:
        return False


# ---------------------------------------------------------------------------
# Claim 正向支持判定
# ---------------------------------------------------------------------------

def collect_claim_reject_rule_ids(outcome: SV.SectionOutcome) -> dict[str, str]:
    """从 evaluation.issues 收集 claim 级（location 形如 ``claim:<id>``）blocking/rework 拒绝。

    这是**正向拒绝信号**（评估器明确点名），与「无 evaluator issue ≠ 合格」互补；warning
    级 claim issue（如 external_missing_date）不构成拒绝。
    """
    ev = outcome.evaluation
    if ev is None:
        return {}
    out: dict[str, str] = {}
    for issue in ev.issues:
        if issue.severity not in ("blocking", "rework"):
            continue
        loc = issue.location or ""
        if not loc.startswith("claim:"):
            continue
        cid = loc[len("claim:"):]
        out.setdefault(cid, issue.rule_id)
    return out


def classify_claim(
    claim: SS.SectionClaim,
    *,
    reject_rule_ids: dict[str, str],
    frozen_snapshot_id: str | None,
    authority: CA.CitationAuthority | None,
    authority_kinds: frozenset[str],
) -> ClaimDisposition:
    """正向支持判定（确定性，不猜中文错误文本）。

    - 评估器 claim 级 blocking/rework 拒绝 → 排除（reason=rule_id）。
    - 无引用 → ``no_citation``。
    - structured：``snapshot_id == frozen_snapshot_id``（结构化 provenance，确定性）；
      fin_db 可用时再跑只读 authority 复验，失败 → 排除。
    - evidence/external：无既存 provenance，**必须**只读 authority 复验；DB 缺失 →
      ``support_status_unavailable`` 排除（绝不猜）。
    """
    def _excluded(reason: str, checks: tuple[CitationCheck, ...] = ()) -> ClaimDisposition:
        return ClaimDisposition(claim.claim_id, claim.claim_type, False, reason, None, checks)

    if claim.claim_id in reject_rule_ids:
        return _excluded(reject_rule_ids[claim.claim_id])

    refs = claim.citation_refs
    if not refs:
        return _excluded("no_citation")

    checks: list[CitationCheck] = []
    all_ok = True
    for ref in refs:
        t = ref.ref_type
        if t == "structured":
            if ref.snapshot_id != frozen_snapshot_id:
                checks.append(CitationCheck("structured", False, "snapshot_not_locked"))
                all_ok = False
                continue
            if "structured" in authority_kinds and authority is not None:
                v = authority.validate(ref)
                checks.append(CitationCheck("structured", v.valid, v.reason))
                if not v.valid:
                    all_ok = False
            else:
                checks.append(CitationCheck("structured", True, None))
        elif t == "evidence":
            if "evidence" not in authority_kinds or authority is None:
                checks.append(CitationCheck("evidence", False, "support_status_unavailable"))
                all_ok = False
            else:
                v = authority.validate(ref)
                checks.append(CitationCheck("evidence", v.valid, v.reason))
                if not v.valid:
                    all_ok = False
        elif t == "external":
            if "external" not in authority_kinds or authority is None:
                checks.append(CitationCheck("external", False, "support_status_unavailable"))
                all_ok = False
            else:
                v = authority.validate(ref)
                checks.append(CitationCheck("external", v.valid, v.reason))
                if not v.valid:
                    all_ok = False
        else:
            checks.append(CitationCheck(t or "?", False, "unknown_ref_type"))
            all_ok = False

    checks_t = tuple(checks)
    if not all_ok:
        first = next((c.reason for c in checks_t if not c.valid), "authority_failed")
        return _excluded(first, checks_t)

    # 正向证明出处。
    types = {c.ref_type for c in checks_t if c.valid}
    tags = []
    if "structured" in types:
        tags.append("structured_snapshot_lock")
    if "evidence" in types:
        tags.append("evidence_authority_verified")
    if "external" in types:
        tags.append("external_authority_verified")
    return ClaimDisposition(claim.claim_id, claim.claim_type, True, None,
                            "+".join(tags) or "citations_verified", checks_t)


def _derived_chain_ok(derived_ids: tuple[str, ...], adopted_ids: set[str],
                      claims_by_id: dict[str, SS.SectionClaim]) -> tuple[bool, str]:
    """派生链必须非空且递归终止于「已入选、有直接引用的基础 Claim」。"""
    if not derived_ids:
        return False, "derived_chain_broken"
    seen: set[str] = set()
    stack = list(derived_ids)
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        if cid in adopted_ids:
            continue  # 基础 Claim 已入选 → 终止
        base = claims_by_id.get(cid)
        if base is None:
            return False, "derived_chain_broken"
        if not base.derived_from_claim_ids:
            # 非入选且无派生 → 断链（基础 Claim 未入选或缺失）。
            return False, "derived_chain_broken"
        stack.extend(base.derived_from_claim_ids)
    return True, ""


# ---------------------------------------------------------------------------
# 缺口与状态
# ---------------------------------------------------------------------------

def _gap_priority(u: SS.SectionUnresolved) -> int:
    if u.state in ("WAITING_HUMAN", "CONFLICT"):
        return 0
    if u.blocking_effects:
        return 1
    if any(s in CORE_IMPACT for s in u.impact_scope):
        return 2
    return 3


def split_unresolved(
    unresolved: tuple[SS.SectionUnresolved, ...], *, limit: int = 5
) -> tuple[tuple[SS.SectionUnresolved, ...], tuple[SS.SectionUnresolved, ...]]:
    """按优先级切分关键缺口（≤limit）与其余缺口。

    优先级：(1) WAITING_HUMAN/CONFLICT；(2) subject/solvency/key_financial/credit_scheme；
    (3) 影响核心结论（blocking_effects）；(4) 其他数据缺口。**不**因问题有部分 claim 而
    整条删除缺口（修订#3：确定性对应不可能时保留简化关键缺口）。
    """
    ordered = sorted(unresolved, key=lambda u: (_gap_priority(u), u.unresolved_id))
    return tuple(ordered[:limit]), tuple(ordered[limit:])


def derive_publication_status(
    outcome: SV.SectionOutcome,
    *,
    unresolved: tuple[SS.SectionUnresolved, ...],
    excluded: tuple[ClaimDisposition, ...],
    has_potential_conflict: bool,
) -> tuple[str, tuple[str, ...]]:
    """章节发布状态。READY 仅在「无可阻断缺口 + 状态/评估通过 + 无冲突」时成立。"""
    reasons: list[str] = []
    sr = outcome.section_result
    if sr is None:
        return STATUS_BLOCKED, ("section_result_missing",)

    if sr.status in ("WAITING_HUMAN", "SECTION_BLOCKED", "FAILED"):
        reasons.append(f"section_status={sr.status}")
    ev = outcome.evaluation
    if ev is not None:
        if ev.decision in ("BLOCKED", "FAILED"):
            reasons.append(f"evaluation_decision={ev.decision}")
        elif ev.decision == "REWORK" and ev.rules_passed is False:
            reasons.append("rules_not_passed")
        elif ev.decision == "PASS_WITH_GAPS":
            reasons.append("evaluation_decision=PASS_WITH_GAPS")

    for u in unresolved:
        if u.state in ("WAITING_HUMAN", "CONFLICT"):
            reasons.append(f"unresolved_{u.state}:{u.question_id or u.unresolved_id}")
        elif u.blocking_effects:
            reasons.append(f"unresolved_blocking:{u.question_id or u.unresolved_id}")
        elif any(s in CORE_IMPACT for s in u.impact_scope):
            reasons.append(f"unresolved_core_impact:{u.question_id or u.unresolved_id}")

    for d in excluded:
        if d.reason in ("conflict_unresolved", "financial_value_mismatch",
                        "self_invented_scheme", "cross_section_exact_conflict"):
            reasons.append(f"excluded_{d.reason}:{d.claim_id}")

    if has_potential_conflict:
        reasons.append("potential_cross_section_conflict")

    if reasons:
        return STATUS_BLOCKED, tuple(dict.fromkeys(reasons))
    return STATUS_READY, ()


# ---------------------------------------------------------------------------
# 引用渲染（人类可读，绝不伪造文档类型）
# ---------------------------------------------------------------------------

def _ro_evidence_doc_names(ev_db: str | Path, evidence_ids) -> dict[str, str]:
    """只读查询 evidence_blocks 的真实 source_name/source_type（不写、不迁移）。"""
    p = Path(ev_db).expanduser().resolve()
    if not p.is_file():
        return {}
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, str] = {}
    try:
        for eid in evidence_ids:
            row = conn.execute(
                "SELECT source_name, source_type FROM evidence_blocks WHERE evidence_id=?",
                (eid,),
            ).fetchone()
            if row is None:
                continue
            name = (row["source_name"] or "").strip()
            label = SOURCE_TYPE_LABELS.get((row["source_type"] or "").strip(), "")
            if name and label:
                out[eid] = f"{name}（{label}）"
            elif name:
                out[eid] = name
    finally:
        conn.close()
    return out


def render_citation(ref: HS.CitationRef, *, doc_names: dict[str, str]) -> str:
    """把 CitationRef 渲染为人类可读字符串。缺失/查不到 → 中性表达，绝不猜「年度报告」。"""
    t = ref.ref_type
    if t == "structured":
        ident = ref.formula_id or ref.item_code or "?"
        return f"财务快照 {ref.snapshot_id[:12]} · {ident} · {ref.period or '?'}"
    if t == "evidence":
        name = doc_names.get(ref.evidence_id or "")
        if name and ref.page_number is not None:
            return f"{name} · 第{ref.page_number}页"
        if name:
            return name
        if ref.page_number is not None:
            return f"本地披露材料 · PDF物理页 {ref.page_number}"
        return "本地披露材料"
    if t == "external":
        return f"外部来源 {ref.source_snapshot_id[:12]}"
    return f"{t or '?'}:?"


# ---------------------------------------------------------------------------
# 跨章节一致性
# ---------------------------------------------------------------------------

def detect_cross_section_conflicts(
    adopted_by_section: dict[str, tuple[SS.SectionClaim, ...]],
    *,
    scope: str = "consolidated",
    currency: str = "CNY",
) -> tuple[tuple[ExactConflict, ...], str]:
    """两层跨章节一致性。

    (a) 精确冲突：同 (formula_id|item_code, period, scope, currency) 跨章节出现不同
    snapshot_id → 精确冲突（相关 Claim 由调用方排除）。
    (b) 潜在冲突：Evidence Claim 无法可靠生成统一 metric_key → 诚实标注「需人工复核」，
    不宣称完整覆盖。
    """
    from collections import defaultdict
    key_snaps: dict[str, set[str]] = defaultdict(set)
    key_claims: dict[str, set[str]] = defaultdict(set)
    for sid, claims in adopted_by_section.items():
        for c in claims:
            for ref in c.citation_refs:
                if ref.ref_type != "structured":
                    continue
                ident = ref.formula_id or ref.item_code
                if not ident:
                    continue
                key = (ident, ref.period or "", scope, currency)
                key_snaps[key].add(ref.snapshot_id or "")
                key_claims[key].add(c.claim_id)

    conflicts: list[ExactConflict] = []
    for key, snaps in sorted(key_snaps.items(), key=lambda kv: kv[0]):
        if len(snaps) > 1:
            conflicts.append(ExactConflict(
                key="|".join(key),
                snapshot_ids=tuple(sorted(snaps)),
                claim_ids=tuple(sorted(key_claims[key])),
            ))

    note = ("跨章节 Evidence 级潜在冲突（同公司/年度/显式业务指标的金额或比率不一致）"
            "无法可靠自动生成统一 metric_key，需人工复核；本版不宣称已覆盖潜在冲突检测。")
    return tuple(conflicts), note


# ---------------------------------------------------------------------------
# 编辑器（确定性默认 / 受限 LLM 可选）
# ---------------------------------------------------------------------------

def _norm_sentence(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    if t[-1] in "。；！？；，、：":
        return t
    return t + "。"


def deterministic_editor(
    adopted: tuple[SS.SectionClaim, ...],
    topics: list[tuple[str, str]],
    render_fn,
) -> tuple[str, tuple[str, ...]]:
    """确定性编辑器：按 topic 合并流式段落（非 ``[事实]`` 一行一条），逐 Claim 带 marker。"""
    by_topic: dict[str, list[SS.SectionClaim]] = {}
    for c in adopted:
        by_topic.setdefault(c.topic_id, []).append(c)

    order: list[str] = [tid for tid, _ in topics]
    for c in adopted:
        if c.topic_id not in order:
            order.append(c.topic_id)

    lines: list[str] = []
    markers: list[str] = []
    for tid in order:
        claims = by_topic.get(tid, [])
        if not claims:
            continue
        title = next((t for t_id, t in topics if t_id == tid), tid)
        lines.append(f"### {title}")
        sentences = [_norm_sentence(c.text) + f"<!-- claim:{c.claim_id} -->" for c in claims]
        lines.append("".join(sentences))
        lines.append("")
        lines.append("引用来源：")
        for c in claims:
            cites = [render_fn(r) for r in c.citation_refs]
            shown = "；".join(cites[:3]) + (f" 等 {len(cites)} 项" if len(cites) > 3 else "")
            lines.append(f"- `{c.claim_id}` → {shown}")
            note = claim_period_note(c)
            if note:
                lines.append(f"  - ⚠️ 口径提示：{note}")
        lines.append("")
        markers.extend(c.claim_id for c in claims)
    return "\n".join(lines).rstrip() + "\n", tuple(markers)


def _parse_llm_markers(text: str) -> set[str]:
    return set(re.findall(r"<!-- claim:([A-Za-z0-9_]+) -->", text))


def llm_editor(
    adopted: tuple[SS.SectionClaim, ...],
    topics: list[tuple[str, str]],
    *,
    render_fn,
    company_id: str,
) -> tuple[str, dict]:
    """受限 LLM 编辑（每章 ≤1 次）：仅合并/重排/加非事实衔接；输出必须带逐 claim marker。

    校验：marker 集合与 adopted 完全一致（无丢、无新增）；无新增数字/事实由 prompt 约束 +
    marker 一致性近似保证。任何失败 → 回退确定性编辑器（审计回退原因）。
    """
    import llm.client as LLC
    audit: dict = {
        "editor": "llm", "used": False, "prompt_version": LLM_EDITOR_PROMPT_VERSION,
        "model": None, "call_id": None, "input_tokens": None, "output_tokens": None,
        "fallback_reason": None,
    }
    try:
        prompt_tpl = LLC.load_prompt(EDITOR_PROMPT_NAME)
    except FileNotFoundError as e:
        audit["fallback_reason"] = f"prompt_missing:{e}"
        body, _ = deterministic_editor(adopted, topics, render_fn)
        return body, audit

    # 输入只放 adopted Claims（正文 + marker 指令），不含任何上游 markdown/中文错误文本。
    claim_lines = [f"{c.claim_id}\t{c.text}" for c in adopted]
    user_msg = (
        "请在下列「合格断言」上做最小编辑（仅合并重复句、重排顺序、加非事实衔接词），"
        "每条断言后必须原样保留 `<!-- claim:<claim_id> -->` 标记，不得新增/删除数字或事实。\n\n"
        + "\n".join(claim_lines)
    )
    try:
        resp = LLC.chat_with_usage(
            [{"role": "user", "content": user_msg}],
            system=prompt_tpl, max_tokens=8192,
            prompt_version=LLM_EDITOR_PROMPT_VERSION,
            thinking={"type": "disabled"},
        )
    except Exception as e:  # noqa: BLE001 - 编辑失败一律回退确定性
        audit["fallback_reason"] = f"llm_call_failed:{type(e).__name__}"
        body, _ = deterministic_editor(adopted, topics, render_fn)
        return body, audit

    audit.update({
        "used": True, "model": resp.model, "call_id": resp.call_id,
        "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
    })

    expected = {c.claim_id for c in adopted}
    got = _parse_llm_markers(resp.text)
    if got != expected:
        audit["fallback_reason"] = (
            f"marker_mismatch(missing={sorted(expected - got)}, extra={sorted(got - expected)})")
        body, _ = deterministic_editor(adopted, topics, render_fn)
        return body, audit

    return resp.text, audit


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def disposition_to_dict(d: ClaimDisposition) -> dict:
    return {
        "claim_id": d.claim_id, "claim_type": d.claim_type, "adopted": d.adopted,
        "reason": d.reason, "support": d.support,
        "citation_checks": [asdict(c) for c in d.citation_checks],
    }


def disposition_from_dict(d: dict) -> ClaimDisposition:
    return ClaimDisposition(
        claim_id=d["claim_id"], claim_type=d["claim_type"], adopted=bool(d["adopted"]),
        reason=d.get("reason"), support=d.get("support"),
        citation_checks=tuple(CitationCheck(**c) for c in (d.get("citation_checks") or [])),
    )


def exact_conflict_to_dict(c: ExactConflict) -> dict:
    return {"key": c.key, "snapshot_ids": list(c.snapshot_ids), "claim_ids": list(c.claim_ids)}


def exact_conflict_from_dict(d: dict) -> ExactConflict:
    return ExactConflict(key=d["key"], snapshot_ids=tuple(d.get("snapshot_ids") or []),
                         claim_ids=tuple(d.get("claim_ids") or []))


def section_publication_to_dict(s: SectionPublication) -> dict:
    return {
        "section_id": s.section_id, "title": s.title,
        "publication_status": s.publication_status,
        "blocking_reasons": list(s.blocking_reasons),
        "adopted_claims": [SS.claim_to_dict(c) for c in s.adopted_claims],
        "excluded": [disposition_to_dict(d) for d in s.excluded],
        "key_gaps": [SS.unresolved_to_dict(u) for u in s.key_gaps],
        "remaining_gaps": [SS.unresolved_to_dict(u) for u in s.remaining_gaps],
        "body": s.body,
    }


def section_publication_from_dict(d: dict) -> SectionPublication:
    return SectionPublication(
        section_id=d["section_id"], title=d.get("title") or "",
        publication_status=d["publication_status"],
        blocking_reasons=tuple(d.get("blocking_reasons") or []),
        adopted_claims=tuple(SS.claim_from_dict(c) for c in (d.get("adopted_claims") or [])),
        excluded=tuple(disposition_from_dict(x) for x in (d.get("excluded") or [])),
        key_gaps=tuple(SS.unresolved_from_dict(u) for u in (d.get("key_gaps") or [])),
        remaining_gaps=tuple(SS.unresolved_from_dict(u) for u in (d.get("remaining_gaps") or [])),
        body=d.get("body") or "",
    )


def publication_to_dict(p: Publication) -> dict:
    return {
        "publication_id": p.publication_id, "schema_version": p.schema_version,
        "run_id": p.run_id, "plan_id": p.plan_id, "manifest_id": p.manifest_id,
        "job_id": p.job_id, "company_id": p.company_id, "company_name": p.company_name,
        "report_as_of": p.report_as_of,
        "sections": [section_publication_to_dict(s) for s in p.sections],
        "exact_conflicts": [exact_conflict_to_dict(c) for c in p.exact_conflicts],
        "potential_conflict_note": p.potential_conflict_note,
        "editor_audit": dict(p.editor_audit),
        "fingerprint": dict(p.fingerprint),
        "reused": p.reused,
        "created_at": p.created_at,
    }


def publication_from_dict(d: dict) -> Publication:
    return Publication(
        publication_id=d["publication_id"], schema_version=d.get("schema_version") or "",
        run_id=d["run_id"], plan_id=d.get("plan_id") or "", manifest_id=d.get("manifest_id") or "",
        job_id=d.get("job_id") or "", company_id=d.get("company_id") or "",
        company_name=d.get("company_name") or "", report_as_of=d.get("report_as_of") or "",
        sections=tuple(section_publication_from_dict(s) for s in (d.get("sections") or [])),
        exact_conflicts=tuple(exact_conflict_from_dict(c) for c in (d.get("exact_conflicts") or [])),
        potential_conflict_note=d.get("potential_conflict_note") or "",
        editor_audit=dict(d.get("editor_audit") or {}),
        fingerprint=dict(d.get("fingerprint") or {}),
        reused=bool(d.get("reused")),
        created_at=d.get("created_at") or "",
    )


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _load_topics(contracts: list[CS.SectionContract] | None) -> dict[str, list[tuple[str, str]]]:
    if contracts is None:
        contracts = CL.load_contracts(DEFAULT_CONTRACTS_PATH)
    return {sec.section_id: [(t.topic_id, t.title) for t in sec.required_topics]
            for sec in contracts}


def compute_publication_id(fingerprint: dict) -> str:
    """publication_id 由完整依赖指纹内容寻址（确定性、幂等）。"""
    return "pub_" + SS.sha256_json(fingerprint)[:40]


def build_publication(
    run: SV.Phase4RunResult,
    *,
    plan_meta: dict,
    authority: CA.CitationAuthority | None = None,
    authority_kinds: frozenset[str] = frozenset(),
    contracts: list[CS.SectionContract] | None = None,
    use_editor: bool = False,
    ev_db: str | Path | None = None,
) -> Publication:
    """把 Phase 4 run 投影为发布态 Publication（纯只读，内容寻址）。"""
    frozen = run.manifest.frozen
    company_id = frozen.get("company_id") or plan_meta.get("company_id") or ""
    frozen_snapshot_id = frozen.get("financial_snapshot_id")
    scope = frozen.get("scope") or "consolidated"
    currency = frozen.get("currency") or "CNY"
    topics_by_section = _load_topics(contracts)

    # 只读文档名（渲染引用用）。
    ev_ids: list[str] = []
    for o in run.sections:
        sr = o.section_result
        if sr is None:
            continue
        for c in sr.claims:
            for r in c.citation_refs:
                if r.ref_type == "evidence" and r.evidence_id:
                    ev_ids.append(r.evidence_id)
    doc_names = _ro_evidence_doc_names(ev_db, list(dict.fromkeys(ev_ids))) if ev_db else {}

    def render_fn(ref):
        return render_citation(ref, doc_names=doc_names)

    # ── 逐章判定 ──
    pending: list[tuple[SV.SectionOutcome, tuple[SS.SectionClaim, ...],
                        tuple[ClaimDisposition, ...]]] = []
    missing_sections: list[SectionPublication] = []
    adopted_by_section: dict[str, tuple[SS.SectionClaim, ...]] = {}
    for o in run.sections:
        sr = o.section_result
        if sr is None:
            missing_sections.append(SectionPublication(
                section_id=o.section_id, title=o.title, publication_status=STATUS_BLOCKED,
                blocking_reasons=("section_result_missing",), adopted_claims=(),
                excluded=(), key_gaps=(), remaining_gaps=(), body=""))
            continue

        reject = collect_claim_reject_rule_ids(o)
        claims_by_id = {c.claim_id: c for c in sr.claims}

        dispositions: list[ClaimDisposition] = []
        direct_adopted_ids: set[str] = set()
        derived_only: list[SS.SectionClaim] = []
        for c in sr.claims:
            if c.claim_type == "inference" and not c.citation_refs and c.derived_from_claim_ids:
                derived_only.append(c)
                continue
            d = classify_claim(c, reject_rule_ids=reject, frozen_snapshot_id=frozen_snapshot_id,
                               authority=authority, authority_kinds=authority_kinds)
            dispositions.append(d)
            if d.adopted:
                direct_adopted_ids.add(c.claim_id)

        # 派生链（inference 无直接引用）。
        for c in derived_only:
            ok, reason = _derived_chain_ok(c.derived_from_claim_ids, direct_adopted_ids,
                                           claims_by_id)
            if ok:
                d = ClaimDisposition(c.claim_id, c.claim_type, True, None,
                                     "derived_chain", ())
                direct_adopted_ids.add(c.claim_id)
            else:
                d = ClaimDisposition(c.claim_id, c.claim_type, False, reason, None, ())
            dispositions.append(d)

        adopted = tuple(c for c in sr.claims if c.claim_id in direct_adopted_ids)
        excluded = tuple(sorted(
            (d for d in dispositions if not d.adopted), key=lambda d: d.claim_id))
        adopted_by_section[o.section_id] = adopted
        pending.append((o, adopted, excluded))

    # ── 跨章节精确冲突（把冲突 Claim 移出 adopted） ──
    exact_conflicts, potential_note = detect_cross_section_conflicts(
        adopted_by_section, scope=scope, currency=currency)
    conflict_claims: set[str] = {cid for cf in exact_conflicts for cid in cf.claim_ids}

    final_sections: list[SectionPublication] = list(missing_sections)
    adopted_by_section_final: dict[str, tuple[SS.SectionClaim, ...]] = {}
    editor_audits: dict[str, dict] = {}
    for o, adopted, excluded in pending:
        if conflict_claims:
            kept = tuple(c for c in adopted if c.claim_id not in conflict_claims)
            moved = tuple(d for d in excluded) + tuple(
                ClaimDisposition(c.claim_id, c.claim_type, False, "cross_section_exact_conflict",
                                 None, ())
                for c in adopted if c.claim_id in conflict_claims)
            adopted = kept
            excluded = tuple(sorted(moved, key=lambda d: d.claim_id))
        adopted_by_section_final[o.section_id] = adopted

        sr = o.section_result
        unresolved = sr.unresolved
        key_gaps, remaining = split_unresolved(unresolved)
        status, reasons = derive_publication_status(
            o, unresolved=unresolved, excluded=excluded,
            has_potential_conflict=False)  # 潜在冲突无法可靠自动检测（修订#7b），不设阻断

        topics = topics_by_section.get(o.section_id, [])
        if use_editor:
            body, editor_audit = llm_editor(adopted, topics, render_fn=render_fn,
                                            company_id=company_id)
        else:
            body, _ = deterministic_editor(adopted, topics, render_fn)
            editor_audit = {"editor": "deterministic", "used": False}
        editor_audits[o.section_id] = editor_audit

        final_sections.append(SectionPublication(
            section_id=o.section_id, title=o.title, publication_status=status,
            blocking_reasons=reasons, adopted_claims=adopted, excluded=excluded,
            key_gaps=key_gaps, remaining_gaps=remaining, body=body))

    # ── 指纹 + 身份 ──
    sections_fp = [{
        "section_id": s.section_id, "title": s.title, "publication_status": s.publication_status,
        "adopted": [c.claim_id for c in s.adopted_claims],
        "excluded": sorted(d.claim_id + ":" + (d.reason or "") for d in s.excluded),
    } for s in final_sections]
    topics_fp = sorted([sid, tid, title] for sid, ts in topics_by_section.items()
                       for tid, title in ts)
    editor_models = None
    if use_editor:
        editor_models = sorted({a.get("model") for a in editor_audits.values()
                                if a.get("model")})
    fingerprint = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "run_id": run.run_id, "plan_id": run.plan_id, "manifest_id": run.manifest_id,
        "job_id": run.job_id,
        "manifest": SS.manifest_to_dict(run.manifest),
        "plan_meta": dict(sorted(plan_meta.items())),
        "sections": sections_fp,
        "topic_titles": topics_fp,
        "exact_conflicts": [exact_conflict_to_dict(c) for c in exact_conflicts],
        "potential_conflict_note": potential_note,
        "citation_display_version": CITATION_DISPLAY_VERSION,
        "renderer_version": RENDERER_VERSION,
        "editor_version": EDITOR_VERSION,
        "llm_editor_prompt_version": LLM_EDITOR_PROMPT_VERSION if use_editor else None,
        "llm_editor_model": editor_models,
        "period_policy_version": PERIOD_POLICY_VERSION,
    }
    publication_id = compute_publication_id(fingerprint)

    return Publication(
        publication_id=publication_id,
        schema_version=PUBLICATION_SCHEMA_VERSION,
        run_id=run.run_id, plan_id=run.plan_id, manifest_id=run.manifest_id, job_id=run.job_id,
        company_id=company_id,
        company_name=(plan_meta.get("company_name") or frozen.get("company_name")
                      or company_id),
        report_as_of=(frozen.get("report_as_of") or plan_meta.get("report_as_of") or ""),
        sections=tuple(final_sections),
        exact_conflicts=exact_conflicts,
        potential_conflict_note=potential_note,
        editor_audit=dict(editor_audits),
        fingerprint=fingerprint,
        reused=False,
        created_at=_utcnow(),
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 发布态 Markdown 渲染
# ---------------------------------------------------------------------------

def render_report_markdown(pub: Publication) -> str:
    lines: list[str] = []
    lines.append("# 授信研究报告（发布态）")
    lines.append("")
    lines.append(f"> **报告编号**：`{pub.publication_id}`")
    lines.append(f"> **主体**：{pub.company_name}（{pub.company_id}）　**报告基准日**：{pub.report_as_of}")
    any_blocked = any(s.publication_status == STATUS_BLOCKED for s in pub.sections)
    if any_blocked:
        lines.append("> **整体状态**：⚠️ 本报告含待人工确认章节，为**草稿**，不可正式出具。")
    else:
        lines.append("> **整体状态**：全部章节可正式出具。")
    lines.append("")

    for i, s in enumerate(pub.sections, start=1):
        label = STATUS_LABELS.get(s.publication_status, s.publication_status)
        lines.append(f"## 第{i}章　{s.title}")
        lines.append("")
        lines.append(f"> 发布状态：**{label}**")
        for r in s.blocking_reasons:
            lines.append(f"> - {r}")
        lines.append("")
        lines.append(s.body.strip())
        lines.append("")
        if s.key_gaps:
            lines.append("**关键缺口**：")
            for u in s.key_gaps:
                lines.append(f"- `{u.state}` {u.detail}")
            if s.remaining_gaps:
                lines.append(f"- …另有 {len(s.remaining_gaps)} 项审计明细，见审计附录。")
            lines.append("")
        if s.excluded:
            lines.append(f"**已排除断言**：{len(s.excluded)} 条（原因见审计附录）。")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 审计附录")
    lines.append("")
    lines.append("> 完整审计明细见同目录 `publication.json`（excluded 原因分布、全部缺口、跨章节冲突、技术诊断）。")
    lines.append("")
    lines.append("### 跨章节一致性")
    if pub.exact_conflicts:
        for c in pub.exact_conflicts:
            lines.append(f"- **精确冲突**：`{c.key}` → 快照 {', '.join(c.snapshot_ids)}；"
                         f"涉及断言 {', '.join(c.claim_ids)}")
    else:
        lines.append("- 未发现结构化字段级精确冲突。")
    lines.append(f"- {pub.potential_conflict_note}")
    lines.append("")
    lines.append("### 生成说明")
    lines.append("- 本报告由 Phase 4 审计产物只读投影生成，未重跑任何检索/计算/LLM 研究。")
    lines.append("- 正文仅由「合格断言」组装；排除断言与全部缺口保留在审计附录。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 原子发布 / 加载
# ---------------------------------------------------------------------------

def publication_dir(out_root: str | Path, run_id: str) -> Path:
    return Path(out_root).resolve() / f"publication_{run_id}"


def write_publication(pub: Publication, *, out_root: str | Path) -> Path:
    """原子发布：临时目录写完再 os.replace。目录已存在 → fail-closed 拒绝覆盖。"""
    out_dir = publication_dir(out_root, pub.run_id)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    if out_dir.exists():
        raise PublicationError(f"发布目录已存在，拒绝覆盖（fail-closed）: {out_dir}")

    tmp = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=str(out_dir.parent)))
    try:
        (tmp / "report.md").write_text(render_report_markdown(pub), encoding="utf-8")
        (tmp / "publication.json").write_text(
            json.dumps(publication_to_dict(pub), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(tmp, out_dir)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out_dir


def load_publication(out_root: str | Path, run_id: str) -> Publication:
    out_dir = publication_dir(out_root, run_id)
    p = out_dir / "publication.json"
    if not p.is_file():
        raise PublicationError(f"发布产物不存在: {p}")
    return publication_from_dict(json.loads(p.read_text(encoding="utf-8")))


def _publish(pub: Publication, *, out_root: str | Path) -> Publication:
    """幂等/冲突判定：同 id 已存在且字段一致 → reused；否则 fail-closed。"""
    out_dir = publication_dir(out_root, pub.run_id)
    if out_dir.exists():
        existing = load_publication(out_root, pub.run_id)
        # 内容地址相同 + 指纹字段一致 → reused（不比较 created_at / 运行期 token 等非内容元数据）。
        if existing.publication_id == pub.publication_id and \
                existing.fingerprint == pub.fingerprint:
            return replace(pub, reused=True)
        raise PublicationError(
            f"发布目录已存在但身份不一致（fail-closed）：现有 {existing.publication_id} "
            f"vs 拟写 {pub.publication_id}")
    write_publication(pub, out_root=out_root)
    return pub


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_db(arg: str | None, default: str) -> Path | None:
    if arg:
        p = Path(arg).expanduser().resolve()
        if not p.is_file():
            raise PublicationError(f"显式指定的只读库不存在（fail-closed）: {p}")
        return p
    p = Path(default).expanduser().resolve()
    return p if p.is_file() else None


def _read_plan_meta(results_root: str | Path, run_id: str) -> dict:
    d = AL._safe_run_dir(results_root, run_id)
    p = d / "report_plan.json"
    if not p.is_file():
        raise PublicationError(f"report_plan.json 缺失（fail-closed）: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_authority(company_id: str, frozen: dict, *, ev_db, fin_db, ext_db):
    kinds = frozenset(k for k, p in (("structured", fin_db), ("evidence", ev_db),
                                     ("external", ext_db)) if p is not None)
    context = RS.RouteContext(
        company_id=company_id, report_as_of=frozen.get("report_as_of"),
        available_document_ids=[], available_source_types=[], supported_db_fields=[],
        supported_metric_ids=[], available_db_fields=[], available_metric_ids=[],
        external_research_enabled=bool(frozen.get("external_research_enabled")),
        scope=frozen.get("scope") or "consolidated", currency=frozen.get("currency") or "CNY",
        purpose=frozen.get("purpose") or "credit_analysis",
        snapshot_id=frozen.get("financial_snapshot_id"),
    )
    authority = CA.build_citation_authority(company_id, context, ev_db=ev_db,
                                            fin_db=fin_db, ext_db=ext_db) if kinds else None
    return authority, kinds


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        prog="python -m sections.publishable_report",
        description="Phase 4 发布态报告收口（只读投影，不重跑研究）")
    p.add_argument("--run-id", default=None)
    p.add_argument("--results-root", default="evaluation/results")
    p.add_argument("--out-root", default="evaluation/results")
    p.add_argument("--ev-db", default=None, help="evidence SQLite（缺省 data/evidence.db）")
    p.add_argument("--fin-db", default=None, help="financial_v2 SQLite（缺省 data/financial_v2.db）")
    p.add_argument("--ext-db", default=None, help="external_sources SQLite（缺省 data/external_sources.db）")
    p.add_argument("--use-editor", action="store_true", help="启用受限 LLM 编辑（默认确定性）")
    p.add_argument("--self-check", action="store_true", help="纯函数自检（不读库、不发布）")
    args = p.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0

    if not args.run_id:
        p.error("--run-id 必填（除非 --self-check）")

    try:
        run = AL.load_phase4_run(args.run_id, root=args.results_root)
        plan_meta = _read_plan_meta(args.results_root, args.run_id)
        ev_db = _resolve_db(args.ev_db, DEFAULT_EV_DB)
        fin_db = _resolve_db(args.fin_db, DEFAULT_FIN_DB)
        ext_db = _resolve_db(args.ext_db, DEFAULT_EXT_DB)
        frozen = run.manifest.frozen
        company_id = frozen.get("company_id") or plan_meta.get("company_id") or ""
        authority, kinds = _build_authority(company_id, frozen, ev_db=ev_db,
                                            fin_db=fin_db, ext_db=ext_db)
        pub = build_publication(run, plan_meta=plan_meta, authority=authority,
                                authority_kinds=kinds, use_editor=args.use_editor, ev_db=ev_db)
        result = _publish(pub, out_root=args.out_root)
    except (AL.ArtifactLoaderError, PublicationError) as e:
        print(f"发布失败: {e}", file=sys.stderr)
        return 1

    out_dir = publication_dir(args.out_root, args.run_id)
    summary = {
        "publication_id": result.publication_id,
        "reused": result.reused,
        "report_md": str(out_dir / "report.md"),
        "publication_json": str(out_dir / "publication.json"),
        "company": result.company_name,
        "report_as_of": result.report_as_of,
        "sections": [
            {"section_id": s.section_id, "title": s.title,
             "publication_status": s.publication_status,
             "adopted": len(s.adopted_claims), "excluded": len(s.excluded),
             "key_gaps": len(s.key_gaps), "remaining_gaps": len(s.remaining_gaps),
             "blocking_reasons": list(s.blocking_reasons)}
            for s in result.sections
        ],
        "exact_conflicts": [c.key for c in result.exact_conflicts],
        "potential_conflict_note": result.potential_conflict_note,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 自检（纯函数，注入合成对象，不读真实库）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    from dataclasses import dataclass as _dc

    # 合成 authority：结构化通过、evidence 通过、external 失败。
    @_dc
    class _Verdict:
        ref_type: str
        valid: bool
        reason: str | None = None

    class _Auth:
        def validate(self, ref):
            if ref.ref_type == "structured":
                return _Verdict("structured", True)
            if ref.ref_type == "evidence":
                return _Verdict("evidence", True)
            if ref.ref_type == "external":
                return _Verdict("external", False, "external_snapshot_not_found")
            return _Verdict(ref.ref_type or "?", False, "unknown_ref_type")

    frozen_snap = "snap-AAA"
    kinds = frozenset({"structured", "evidence", "external"})

    def ref(t, **kw):
        return HS.CitationRef(ref_type=t, **kw)

    # 1. adopted structured（snapshot lock + authority 通过）。
    c_struct = SS.SectionClaim(
        claim_id="c1", section_id="financial", topic_id="fin_solvency",
        question_ids=("fin_solvency",), text="流动比率 1.5 倍。", claim_type="calculation",
        citation_refs=(ref("structured", snapshot_id=frozen_snap, formula_id="SOLV_CURRENT_RATIO",
                           formula_version="1.0", period="2025-12-31"),))
    d1 = classify_claim(c_struct, reject_rule_ids={}, frozen_snapshot_id=frozen_snap,
                        authority=_Auth(), authority_kinds=kinds)

    # 2. snapshot_not_locked。
    c_badlock = SS.SectionClaim(
        claim_id="c2", section_id="financial", topic_id="fin_solvency",
        question_ids=("fin_solvency",), text="x", claim_type="fact",
        citation_refs=(ref("structured", snapshot_id="snap-OTHER", formula_id="SOLV_CURRENT_RATIO",
                           formula_version="1.0", period="2025-12-31"),))
    d2 = classify_claim(c_badlock, reject_rule_ids={}, frozen_snapshot_id=frozen_snap,
                        authority=_Auth(), authority_kinds=kinds)

    # 3. evidence 无 authority → support_status_unavailable。
    c_ev = SS.SectionClaim(
        claim_id="c3", section_id="company", topic_id="company_identity",
        question_ids=("company_identity_basic",), text="成立于 2011 年。", claim_type="fact",
        citation_refs=(ref("evidence", evidence_id="e1", page_number=3),))
    d3 = classify_claim(c_ev, reject_rule_ids={}, frozen_snapshot_id=frozen_snap,
                        authority=None, authority_kinds=frozenset())

    # 4. no_citation。
    c_noc = SS.SectionClaim(
        claim_id="c4", section_id="company", topic_id="company_identity",
        question_ids=("company_identity_basic",), text="无来源断言。", claim_type="fact",
        citation_refs=())
    d4 = classify_claim(c_noc, reject_rule_ids={}, frozen_snapshot_id=frozen_snap,
                        authority=_Auth(), authority_kinds=kinds)

    # 5. 评估器正向拒绝。
    c_rej = SS.SectionClaim(
        claim_id="c5", section_id="company", topic_id="company_identity",
        question_ids=("company_identity_basic",), text="被点名。", claim_type="fact",
        citation_refs=(ref("evidence", evidence_id="e1", page_number=3),))
    d5 = classify_claim(c_rej, reject_rule_ids={"c5": "absence_contradicts_gap"},
                        frozen_snapshot_id=frozen_snap, authority=_Auth(), authority_kinds=kinds)

    # 6. 可比性映射。
    comp_map = {
        "SOLV_CURRENT_RATIO": formula_period_comparability("SOLV_CURRENT_RATIO"),
        "PROF_ROE": formula_period_comparability("PROF_ROE"),
        "GROWTH_REVENUE": formula_period_comparability("GROWTH_REVENUE"),
        "EBITDA": formula_period_comparability("EBITDA"),
        "PROF_NET_MARGIN": formula_period_comparability("PROF_NET_MARGIN"),
    }

    # 7. 缺口优先级。
    u_wait = SS.SectionUnresolved(
        unresolved_id="u1", section_id="company", topic_id="company_control",
        question_id="company_subject_match", state="WAITING_HUMAN",
        reason_code="x", detail="等待人工", impact_scope=("subject",),
        blocking_effects=("JOB_BLOCKED",))
    u_core = SS.SectionUnresolved(
        unresolved_id="u2", section_id="financial", topic_id="fin_solvency",
        question_id="fin_solvency", state="NOT_PROVIDED", reason_code="MISSING_REQUIRED_ITEM",
        detail="EBITDA 缺失", impact_scope=("solvency",))
    u_other = SS.SectionUnresolved(
        unresolved_id="u3", section_id="industry", topic_id="industry_competition",
        question_id="industry_competition", state="NOT_FOUND_AFTER_SEARCH",
        reason_code="x", detail="竞争格局未检索到")
    key, rest = split_unresolved((u_other, u_core, u_wait))
    prio_ok = [u.unresolved_id for u in key] == ["u1", "u2", "u3"]

    # 8. 跨章节精确冲突（结构化同 key 两快照）。
    claim_a = SS.SectionClaim(
        claim_id="a1", section_id="financial", topic_id="fin_solvency",
        question_ids=("fin_solvency",), text="x", claim_type="calculation",
        citation_refs=(ref("structured", snapshot_id="snap-AAA", formula_id="SOLV_DEBT_RATIO",
                           formula_version="1.0", period="2025-12-31"),))
    claim_b = SS.SectionClaim(
        claim_id="b1", section_id="company", topic_id="company_debt",
        question_ids=("company_debt_credit",), text="y", claim_type="fact",
        citation_refs=(ref("structured", snapshot_id="snap-BBB", formula_id="SOLV_DEBT_RATIO",
                           formula_version="1.0", period="2025-12-31"),))
    conflicts, note = detect_cross_section_conflicts(
        {"financial": (claim_a,), "company": (claim_b,)})

    # 9. 确定性编辑器 marker 与合并。
    body, markers = deterministic_editor(
        (c_struct,),
        [("fin_solvency", "短期与长期偿债能力")],
        lambda r: "财务快照 · SOLV_CURRENT_RATIO")
    marker_ok = "<!-- claim:c1 -->" in body and markers == ("c1",)

    # 10. 引用中性回退。
    neutral = render_citation(ref("evidence", evidence_id="missing", page_number=7),
                              doc_names={})
    named = render_citation(ref("evidence", evidence_id="e9", page_number=3),
                            doc_names={"e9": "NDSD_2024_year.pdf（年度报告）"})

    return {
        "structured_adopted": d1.adopted and d1.support == "structured_snapshot_lock",
        "snapshot_not_locked": (not d2.adopted and d2.reason == "snapshot_not_locked"),
        "evidence_support_unavailable": (not d3.adopted
                                         and d3.reason == "support_status_unavailable"),
        "no_citation": (not d4.adopted and d4.reason == "no_citation"),
        "evaluator_reject": (not d5.adopted and d5.reason == "absence_contradicts_gap"),
        "comparability_map": comp_map,
        "gap_priority_order_ok": prio_ok,
        "exact_conflict_detected": len(conflicts) == 1
            and conflicts[0].claim_ids == ("a1", "b1"),
        "deterministic_marker_ok": marker_ok,
        "citation_neutral_fallback": neutral,
        "citation_named": named,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
