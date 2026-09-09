"""Phase 3 Batch B 修订⑤：结构化结果权威判定（确定性，无 LLM）。

针对「仅结构化引用」的 fact claim，用 Store 权威 + canonical Decimal 数值等价，在 LLM
entailment 之前确定性地判定 SUPPORTED / PARTIAL / UNSUPPORTED，避免纯结构化 claim 被
LLM 因「无正文」误判 UNSUPPORTED。

判定原则（fail-closed）：
- 权威性 = 复合判定（exists + is_current + validity==valid + not report_blocked +
  not quarantined），由 Store 决定；ref.snapshot_status 仅展示/审计，不自证。
- 数值事实任一实质不匹配 → UNSUPPORTED（非 PARTIAL）。
- 比较方向（上升/下降/持平）与趋势（近三年连续上升/下降/持平/有升有降）由本模块
  确定性核对：方向来自 Python 工具结果（ref.direction / 多期序列），LLM 不计算方向。

本模块只做「纯判定」（读 state/answer + 注入的 snapshot_authority，不写库）；
`query_snapshot_authority` 为唯一 I/O 边界（Store 只读），运行时以
`partial(query_snapshot_authority, current_snapshot_id=state.active_snapshot_id)`
注入，使判定函数本身可离线测试。

本轮（冻结前定点修复）改动：
- 比较方向验证：claim 明确表达的方向与 ref.direction 不一致 → direction_mismatch；
  ref.direction=missing_period → comparison_period_missing。
- Citation Repair：snapshot_id 写错时不再静默回退 period+code；改为显式 repair
  （唯一 active+period+code 候选且通过权威 + 维度校验）→ 改写 answer 引用 + 记录
  CITATION_REF_REPAIRED；0/多候选/不通过 → unresolvable_ref。
- 三期趋势确定性计算：删除「≥3 期回退 LLM」逻辑；用 Decimal 对年度序列算
  increased/decreased/unchanged/mixed；期间不足/数值缺失/单位口径不一致 → PARTIAL。

冻结前最后一轮（两项定点修复）改动：
- 排除非业务数值：`_extract_claim_business_amounts` 剔除年份（YYYY年）、完整日期
  （YYYY-MM-DD / 2025年12月31日）、月/日、页码/条款、括号引用序号，避免年份被当
  成指标值触发误判 value_mismatch；金额/比例带单位后缀不受影响。
- Citation Repair 两阶段原子化：`_resolve_ref`（阶段 A）只「提出」pending repair，
  不改写引用、不写审计、不发 trace；整条 claim 最终 SUPPORTED 时才经 `_commit_repairs`
  （阶段 B）一次性改写全部引用并落审计。PARTIAL/UNSUPPORTED 一律不提交，禁止部分提交。

CLI: python -m harness.structured_provenance --self-check
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial

from financial_v2 import store as fstore
from harness import entailment as E
from harness import schema as H
from routing import schema as RS


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# ref.status 中「有真实值」的状态（financial_field 恒用 "available"；financial_metric
# 用 CALCULATED_EXACT / CALCULATED_PROXY，与 routing.context._AVAILABLE_METRIC_STATUSES 对齐）。
_AVAILABLE_STATUSES = ("available", "CALCULATED_EXACT", "CALCULATED_PROXY")

# 唯一年份抽取（"2025年" → "2025-12-31"；≥2 个或 0 个年份 → None）。
_YEAR_RE = re.compile(r"(20\d{2})\s*年")

# 结构化引用解析「code 不符但同 snapshot+period 存在其它 ref」的哨兵。
_CODE_MISMATCH = object()

# 方向词 → 方向（increased/decreased/unchanged/mixed）。
# 「增长」用负向 lookahead 排除「增长率/增长速度」名词；「减少/降低/下降」同理排除「率」。
_DIRECTION_WORDS: dict[str, tuple[str, ...]] = {
    "increased": ("上升", "增加", "提高", "提升", "上涨", "走高", "攀升", "上扬",
                  "向好", "走强"),
    "decreased": ("下降", "减少", "降低", "回落", "走低", "下滑", "恶化", "收窄",
                  "走弱"),
    "unchanged": ("持平", "不变", "基本稳定", "保持稳定", "维持不变", "基本持平",
                  "无变化", "稳定不变"),
    "mixed": ("有升有降", "先升后降", "先降后升", "涨跌互现", "起伏", "震荡", "波动"),
}
# 「增长」「减少」的动词形式（排除名词「增长率/增长速度/减少率」）。
_INCREASED_VERB_RE = re.compile(r"增长(?!率|速度)")
_DECREASED_VERB_RE = re.compile(r"减少(?!率)|降低(?!率)")


# ---------------------------------------------------------------------------
# 权威性复合判定（Store 权威）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotAuthority:
    """快照权威性复合判定（Store 权威；ref.snapshot_status 不自证）。"""

    exists: bool
    is_current: bool            # snapshot_id == run 锁定的 current_snapshot_id
    validity: str | None        # latest_snapshot_validity → valid|stale|superseded|None
    report_blocked: bool        # financial_snapshot.report_blocked 布尔列（独立于 validity）
    quarantined: bool           # quarantine 表 is_quarantined("financial_snapshot", id)

    def authoritative(self) -> bool:
        return (self.exists and self.is_current and self.validity == "valid"
                and not self.report_blocked and not self.quarantined)


@dataclass(frozen=True)
class StructuredProvenanceVerdict:
    """对单个 fact claim 的结构化权威判定。"""

    claim_id: str
    verdict: str = "UNSUPPORTED"          # SUPPORTED|PARTIAL|UNSUPPORTED
    evaluator: str = "structured_provenance"
    matched_refs: list = field(default_factory=list)
    reason: str = ""
    mode: str = "single"                  # single | comparison | trend
    trend: str | None = None              # trend 模式下的 Python 算得趋势
    citation_repairs: list = field(default_factory=list)  # CITATION_REF_REPAIRED 审计
    snapshot_valid: bool = False
    company_match: bool = False
    period_match: bool = False
    scope_currency_purpose_match: bool = False
    item_formula_match: bool = False
    value_match: bool = False


def query_snapshot_authority(snapshot_id: str,
                             current_snapshot_id: str | None) -> SnapshotAuthority:
    """Store 只读权威查询（get_snapshot + latest_snapshot_validity + is_quarantined）。"""
    snap = fstore.get_snapshot(snapshot_id)
    exists = snap is not None
    validity = fstore.latest_snapshot_validity(snapshot_id) if exists else None
    return SnapshotAuthority(
        exists=exists,
        is_current=(snapshot_id == current_snapshot_id),
        validity=validity,
        report_blocked=(snap.report_blocked if snap is not None else False),
        quarantined=fstore.is_quarantined("financial_snapshot", snapshot_id),
    )


# ---------------------------------------------------------------------------
# 纯判定辅助
# ---------------------------------------------------------------------------

def _claim_period(text: str) -> str | None:
    """claim 唯一年份 → "YYYY-12-31"；≥2 个或 0 个年份 → None（跳过期间匹配）。"""
    years = sorted({y for y in _YEAR_RE.findall(text or "")})
    return f"{years[0]}-12-31" if len(years) == 1 else None


def _ref_amount(ref: RS.StructuredResultRef) -> E.Amount | None:
    """把结构化 ref 的 (display_value|raw_value, unit) 归一化为 canonical Amount。"""
    v = ref.display_value if ref.display_value is not None else ref.raw_value
    if v is None:
        return None
    unit = (ref.unit or "").strip()
    amts = E.extract_amounts(f"{v}{unit}")
    return amts[0] if amts else None


def _ref_decimal(ref: RS.StructuredResultRef) -> Decimal | None:
    """结构化 ref 的 canonical Decimal 值（金额→元、比例→百分数数值）。"""
    amt = _ref_amount(ref)
    return amt.value if amt is not None else None


def _value_matches(ref_amount: E.Amount | None,
                   claim_amounts: list[E.Amount]) -> bool:
    """ref 数值与 claim 数值中任一 canonical Decimal 等价 + 单位同类。"""
    if ref_amount is None:
        return False
    for ca in claim_amounts:
        if E.units_compatible(ref_amount, ca) and ca.value == ref_amount.value:
            return True
    return False


def _claim_direction(text: str) -> str | None:
    """识别 claim 明确表达的方向 → increased|decreased|unchanged|mixed；无法可靠识别 → None。

    方向必须来自 Python 工具结果，本函数只做「识别」不做「计算」；多个不同方向词同时出现
    （冲突）→ None（无法可靠识别，上层记 PARTIAL，不让 LLM 法官替代数值计算）。
    """
    t = text or ""
    # 先中性化名词短语，避免「增长率/增长速度/增速」误判为「增长」方向。
    t = t.replace("增长率", "").replace("增长速度", "").replace("增速", "")
    found: set[str] = set()
    for word in _DIRECTION_WORDS["mixed"]:
        if word in t:
            found.add("mixed")
    for word in _DIRECTION_WORDS["increased"]:
        if word in t:
            found.add("increased")
    for word in _DIRECTION_WORDS["decreased"]:
        if word in t:
            found.add("decreased")
    for word in _DIRECTION_WORDS["unchanged"]:
        if word in t:
            found.add("unchanged")
    if _INCREASED_VERB_RE.search(t):
        found.add("increased")
    if _DECREASED_VERB_RE.search(t):
        found.add("decreased")
    if len(found) != 1:
        return None
    return found.pop()


def _trend_relation(refs: list[RS.StructuredResultRef],
                    ) -> tuple[str | None, str | None]:
    """对同一 item/formula、相同单位的年度序列确定性计算趋势（Decimal，无 float）。

    返回 (relation, reason)：relation ∈ increased|decreased|unchanged|mixed；期间不足/
    数值缺失/单位口径不一致 → (None, reason)（上层记 PARTIAL，不得 FULL）。
    """
    by_period: dict[str, RS.StructuredResultRef] = {}
    for r in refs:
        if r.period is not None:
            by_period.setdefault(r.period, r)
    ordered = [by_period[p] for p in sorted(by_period)]
    if len(ordered) < 2:
        return None, "trend_insufficient_periods"

    # 单位/口径一致性：同一 item/formula、相同单位与 canonical 类别才可比。
    units = {r.unit for r in ordered if r.unit}
    kinds = {_ref_amount(r).canonical_kind for r in ordered if _ref_amount(r) is not None}
    if len(units) > 1 or len(kinds) > 1:
        return None, "trend_unit_scope_mismatch"

    vals: list[Decimal] = []
    for r in ordered:
        v = _ref_decimal(r)
        if v is None:
            return None, "trend_value_missing"
        vals.append(v)

    deltas = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    if all(d > 0 for d in deltas):
        return "increased", None
    if all(d < 0 for d in deltas):
        return "decreased", None
    if all(d == 0 for d in deltas):
        return "unchanged", None
    return "mixed", None


def _structured_citations(claim: H.Claim, answer: H.ResearchAnswer
                          ) -> list[H.CitationRef]:
    return [answer.citations[i] for i in claim.citation_refs
            if 0 <= i < len(answer.citations)
            and answer.citations[i].ref_type == "structured"]


def _has_evidence_citation(claim: H.Claim, answer: H.ResearchAnswer) -> bool:
    return any(0 <= i < len(answer.citations)
               and answer.citations[i].ref_type == "evidence"
               for i in claim.citation_refs)


def _code_match(cit: H.CitationRef, r: RS.StructuredResultRef) -> bool:
    if cit.formula_id:
        return r.formula_id == cit.formula_id
    if cit.item_code:
        return r.item_code == cit.item_code
    return False


def _resolve_ref(cit: H.CitationRef, state: H.ResearchState,
                 snapshot_authority, scope: str, currency: str, purpose: str):
    """解析结构化引用：严格匹配 → ref；否则显式 Citation Repair → (ref, pending_repair)。

    返回 (ref, pending_repair|None) / _CODE_MISMATCH / None（unresolvable）。
    snapshot_id 是机器生成的 opaque 长 token，LLM 写引用时可能转写错；禁止静默回退到
    「active 或第一个 code match」——只有唯一 active+period+code 候选且通过权威 + 维度
    校验时才提出 repair，否则 fail-closed。

    **阶段 A（无副作用）**：本函数只「提出」pending repair，不改写 CitationRef、
    不写 state.citation_repairs、不发 trace。pending repair 携带 `_citation`（对象引用，
    仅内部用于阶段 B 提交）与审计字段；是否真正改写由 `_evaluate_claim` 在整条 claim
    最终 SUPPORTED 时经 `_commit_repairs` 一次性原子提交。
    """
    same_period = [r for r in state.structured_refs if r.period == cit.period]

    # 1) 严格匹配：snapshot_id + period + item/formula。
    for r in same_period:
        if r.snapshot_id == cit.snapshot_id and _code_match(cit, r):
            return r, None

    # 2) 显式 repair：仅当 citation 有 snapshot_id（可被改写）且 run 锁定了 current。
    if not cit.snapshot_id or not state.active_snapshot_id:
        return None
    active = state.active_snapshot_id
    candidates = [r for r in same_period
                  if r.snapshot_id == active and _code_match(cit, r)]
    if len(candidates) != 1:
        # 0 或多候选 → 区分 code 不符（item_formula_mismatch）与 unresolvable。
        if not same_period:
            return None  # 无同 period ref → unresolvable
        if not any(_code_match(cit, r) for r in same_period):
            return _CODE_MISMATCH  # 同 period 有 ref 但 code 不符
        return None  # 多候选（非唯一 active 候选）→ unresolvable
    cand = candidates[0]
    # 候选必须通过权威 + 维度校验（company/scope/currency/purpose/status），否则不 repair。
    fail = _validate_ref_identity(cand, state, snapshot_authority, scope, currency, purpose)
    if fail is not None:
        return None
    # pending repair：仅提出，不应用（阶段 B 经 _commit_repairs 原子提交）。
    return cand, {
        "_citation": cit,
        "original_snapshot_id": cit.snapshot_id,
        "repaired_snapshot_id": cand.snapshot_id,
        "period": cit.period,
        "formula_id": cit.formula_id,
        "item_code": cit.item_code,
    }


def _commit_repairs(pending: list[dict]) -> list[dict]:
    """阶段 B：原子应用所有 pending repairs，返回审计记录（不含内部 `_citation`）。

    仅在整条 claim 最终 SUPPORTED 时调用（PARTIAL/UNSUPPORTED 不调用），故不会部分提交：
    要么全部改写引用并落审计，要么一条都不改。审计记录为可序列化 dict
    {original_snapshot_id, repaired_snapshot_id, period, item_code, formula_id}。
    """
    audit: list[dict] = []
    for pr in pending:
        pr["_citation"].snapshot_id = pr["repaired_snapshot_id"]
        audit.append({
            "original_snapshot_id": pr["original_snapshot_id"],
            "repaired_snapshot_id": pr["repaired_snapshot_id"],
            "period": pr["period"],
            "formula_id": pr["formula_id"],
            "item_code": pr["item_code"],
        })
    return audit


def _verdict(claim_id: str, verdict: str, reason: str, *,
             matched_refs: list = (), mode: str = "single", trend: str | None = None,
             citation_repairs: list = (), snapshot_valid: bool = False,
             company_match: bool = False, scope_currency_purpose_match: bool = False,
             item_formula_match: bool = False, value_match: bool = False,
             period_match: bool = False) -> StructuredProvenanceVerdict:
    return StructuredProvenanceVerdict(
        claim_id=claim_id, verdict=verdict, reason=reason,
        matched_refs=list(matched_refs), mode=mode, trend=trend,
        citation_repairs=list(citation_repairs), snapshot_valid=snapshot_valid,
        company_match=company_match,
        scope_currency_purpose_match=scope_currency_purpose_match,
        item_formula_match=item_formula_match, value_match=value_match,
        period_match=period_match)


def _validate_ref_identity(ref: RS.StructuredResultRef, state: H.ResearchState,
                           snapshot_authority, scope: str, currency: str, purpose: str,
                           ) -> str | None:
    """权威性 + company + scope/currency/purpose + status 校验（不含数值匹配）。

    返回 None=通过；否则失败 reason 字符串。数值匹配单独在 _check_ref 里做，使 repair
    候选可在改写前先做身份校验。
    """
    if snapshot_authority is None:
        return "validity_query_unavailable"
    auth = snapshot_authority(ref.snapshot_id)
    if auth is None or not auth.exists:
        return "snapshot_not_found"
    if not auth.is_current:
        return "snapshot_not_current"
    if auth.validity != "valid":
        return f"snapshot_{auth.validity or 'validity_missing'}"
    if auth.report_blocked:
        return "snapshot_report_blocked"
    if auth.quarantined:
        return "snapshot_quarantined"
    if ref.company_id != state.company_id:
        return "company_mismatch"
    if (ref.scope != scope or ref.currency != currency or ref.purpose != purpose):
        return ("scope_mismatch" if ref.scope != scope
                else ("currency_mismatch" if ref.currency != currency
                      else "purpose_mismatch"))
    if ref.status not in _AVAILABLE_STATUSES:
        return f"metric_status_{ref.status}"
    return None


def _check_ref(ref: RS.StructuredResultRef, claim: H.Claim, state: H.ResearchState,
               snapshot_authority, scope: str, currency: str, purpose: str,
               claim_amounts: list[E.Amount], require_value: bool,
               ) -> StructuredProvenanceVerdict | None:
    """单条结构化 ref 的 fail-closed 判定（返回 None=通过；否则失败 verdict）。"""
    reason = _validate_ref_identity(ref, state, snapshot_authority, scope, currency, purpose)
    if reason is not None:
        return _verdict(claim.claim_id, "UNSUPPORTED", reason)
    if require_value:
        ref_amt = _ref_amount(ref)
        if not _value_matches(ref_amt, claim_amounts):
            return _verdict(claim.claim_id, "UNSUPPORTED", "value_mismatch")
    return None


def _evaluate_claim(claim: H.Claim, scits: list[H.CitationRef], state: H.ResearchState,
                    snapshot_authority, scope: str, currency: str, purpose: str
                    ) -> StructuredProvenanceVerdict:
    refs: list[RS.StructuredResultRef] = []
    repairs: list[dict] = []
    for c in scits:
        r = _resolve_ref(c, state, snapshot_authority, scope, currency, purpose)
        if r is _CODE_MISMATCH:
            return _verdict(claim.claim_id, "UNSUPPORTED", "item_formula_mismatch")
        if r is None:
            return _verdict(claim.claim_id, "UNSUPPORTED", "unresolvable_ref")
        ref, repair = r
        refs.append(ref)
        if repair is not None:
            repairs.append(repair)

    claim_amounts = E.extract_claim_business_amounts(claim.text or "")
    claim_dir = _claim_direction(claim.text)
    has_direction = any(getattr(r, "direction", None) for r in refs)
    # 趋势语义 = 显式趋势词 或 claim 明确表达方向（含 mixed）。方向识别缺位时不算趋势。
    trend_semantics = E.has_trend_semantics(claim.text) or claim_dir is not None
    mode = "comparison" if has_direction else ("trend" if (
        trend_semantics and len({r.period for r in refs}) >= 2) else "single")

    # 逐值匹配触发条件：
    # - 纯单期数值 claim（无方向/趋势语义）必须有数值 → 强制逐值；
    # - 比较/趋势、或单期但含方向语义 → 方向是主判据，仅在 claim 实际陈述业务数值时
    #   才逐值匹配（年份/日期等非业务数字已由 _extract_claim_business_amounts 剔除，
    #   不再让「仅陈述方向无数值」的 claim 因年份存在被误判 value_mismatch）。
    require_value = (mode == "single" and not trend_semantics) or bool(claim_amounts)

    matched: list[str] = []
    for r in refs:
        fail = _check_ref(r, claim, state, snapshot_authority, scope, currency, purpose,
                          claim_amounts, require_value)
        if fail is not None:
            return fail
        matched.append(f"{r.snapshot_id}:{r.formula_id or r.item_code}:{r.period}")

    # 单期 period 匹配（claim 唯一年份 → ref.period == "YYYY-12-31"）。
    cp = _claim_period(claim.text)
    period_match = cp is None or all(r.period == cp for r in refs)
    if not period_match:
        return _verdict(claim.claim_id, "UNSUPPORTED", "period_mismatch",
                        matched_refs=matched, snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=False)

    # 比较方向验证（方向来自 Python 工具结果，LLM 不算方向）。
    if has_direction:
        direction = next(getattr(r, "direction", None) for r in refs
                         if getattr(r, "direction", None))
        if direction == "missing_period":
            return _verdict(claim.claim_id, "PARTIAL", "comparison_period_missing",
                            matched_refs=matched, mode="comparison", snapshot_valid=True,
                            company_match=True, scope_currency_purpose_match=True,
                            item_formula_match=True, value_match=True, period_match=True)
        if claim_dir is not None and claim_dir != direction:
            return _verdict(claim.claim_id, "UNSUPPORTED", "direction_mismatch",
                            matched_refs=matched, mode="comparison", snapshot_valid=True,
                            company_match=True, scope_currency_purpose_match=True,
                            item_formula_match=True, value_match=True, period_match=True)
        reason = ("structured_authoritative_after_citation_repair" if repairs
                  else "structured_authoritative")
        return _verdict(claim.claim_id, "SUPPORTED", reason,
                        matched_refs=matched, mode="comparison",
                        citation_repairs=_commit_repairs(repairs), snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=True)

    # 趋势（≥2 期，无 direction 字段）确定性计算 + 核对 claim 趋势表达。
    if mode == "trend":
        relation, reason = _trend_relation(refs)
        if relation is None:
            return _verdict(claim.claim_id, "PARTIAL", reason,
                            matched_refs=matched, mode="trend", snapshot_valid=True,
                            company_match=True, scope_currency_purpose_match=True,
                            item_formula_match=True, value_match=True, period_match=True)
        if claim_dir is None:
            return _verdict(claim.claim_id, "PARTIAL", "trend_direction_ambiguous",
                            matched_refs=matched, mode="trend", trend=relation,
                            snapshot_valid=True, company_match=True,
                            scope_currency_purpose_match=True, item_formula_match=True,
                            value_match=True, period_match=True)
        if claim_dir != relation:
            return _verdict(claim.claim_id, "UNSUPPORTED", "trend_direction_mismatch",
                            matched_refs=matched, mode="trend", trend=relation,
                            snapshot_valid=True, company_match=True,
                            scope_currency_purpose_match=True, item_formula_match=True,
                            value_match=True, period_match=True)
        reason = ("structured_authoritative_after_citation_repair" if repairs
                  else "structured_authoritative")
        return _verdict(claim.claim_id, "SUPPORTED", reason,
                        matched_refs=matched, mode="trend", trend=relation,
                        citation_repairs=_commit_repairs(repairs), snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=True)

    # 单期：含趋势语义但无比较期间 → PARTIAL（引用有效，仅比较所需期间缺失）。
    if trend_semantics:
        return _verdict(claim.claim_id, "PARTIAL", "single_period_for_trend",
                        matched_refs=matched, snapshot_valid=True, company_match=True,
                        scope_currency_purpose_match=True, item_formula_match=True,
                        value_match=True, period_match=True)

    reason = ("structured_authoritative_after_citation_repair" if repairs
              else "structured_authoritative")
    return _verdict(claim.claim_id, "SUPPORTED", reason,
                    matched_refs=matched, citation_repairs=_commit_repairs(repairs),
                    snapshot_valid=True, company_match=True,
                    scope_currency_purpose_match=True, item_formula_match=True,
                    value_match=True, period_match=True)


# ---------------------------------------------------------------------------
# 主判定
# ---------------------------------------------------------------------------

def evaluate_structured_provenance(state: H.ResearchState, answer: H.ResearchAnswer | None,
                                   *, snapshot_authority=None,
                                   scope: str = "consolidated", currency: str = "CNY",
                                   purpose: str = "credit_analysis",
                                   ) -> dict[str, StructuredProvenanceVerdict]:
    """逐「仅结构化引用」fact claim 判定（无 LLM）。

    snapshot_authority: Callable[[snapshot_id], SnapshotAuthority|None]，运行时以
    partial(query_snapshot_authority, current_snapshot_id=state.active_snapshot_id) 注入；
    传入 None → 命中 claim 判 validity_query_unavailable（fail-closed）。

    副作用（repair 所需，属答案改写而非写库）：
    - Citation Repair 会就地改写 answer.citations 中该引用的 snapshot_id；
    - 审计记录写入 state.citation_repairs（本 ANSWER 版本，供 trace/报告）。

    返回 {claim_id: StructuredProvenanceVerdict}。仅结构化引用（无 evidence 引用）的
    fact claim 才被判定；混合引用/无结构化引用的 claim 不在此层（回退 LLM entailment）。
    """
    out: dict[str, StructuredProvenanceVerdict] = {}
    if hasattr(state, "citation_repairs"):
        state.citation_repairs = []
    if answer is None:
        return out
    for claim in answer.claims:
        if claim.kind != "fact":
            continue
        scits = _structured_citations(claim, answer)
        if not scits:
            continue
        if _has_evidence_citation(claim, answer):
            continue  # 混合引用 → 交 LLM entailment
        v = _evaluate_claim(claim, scits, state, snapshot_authority, scope, currency, purpose)
        out[claim.claim_id] = v
        if v.citation_repairs and hasattr(state, "citation_repairs"):
            state.citation_repairs.extend(v.citation_repairs)
    return out


def entailment_summary(state: H.ResearchState, prechecks: dict) -> list[dict]:
    """合并三 evaluator 汇总（evidence_deterministic / structured_provenance /
    llm_entailment），每项 {claim_id, evaluator, verdict, reason}。"""
    summary: list[dict] = []
    for cid, pc in prechecks.items():
        if pc.get("not_inspected"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "UNSUPPORTED", "reason": "not_inspected"})
        elif pc.get("value_missing"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "UNSUPPORTED",
                            "reason": "value_missing:"
                                      + ",".join(pc.get("value_missing_tokens", []))})
        elif pc.get("high_risk_scope"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": "PARTIAL", "reason": "high_risk_scope"})
        csg = pc.get("closed_set")
        if csg and csg.get("triggered") and csg.get("verdict") in ("PARTIAL", "UNSUPPORTED"):
            summary.append({"claim_id": cid, "evaluator": "evidence_deterministic",
                            "verdict": csg["verdict"],
                            "reason": "closed_set:" + csg["reason"]})
    for cid, v in state.structured_provenance.items():
        summary.append({"claim_id": cid, "evaluator": "structured_provenance",
                        "verdict": v.verdict, "reason": v.reason})
    for v in state.entailment_verdicts:
        summary.append({"claim_id": v.claim_id, "evaluator": "llm_entailment",
                        "verdict": v.verdict, "reason": v.reason})
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.structured_provenance",
        description="结构化结果权威判定自检（纯函数，注入假 authority，不读库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        need = RS.InformationNeed(
            need_id="q", section_id="fin", question="2025年净利率是多少？",
            required_evidence_types=[], required_source_types=[], time_scope=None,
            priority="P0", depends_on=[])
        state = H.ResearchState(
            run_id="r", case_id="c", question_id="q", company_id="ACME",
            section_id="fin", original_question="2025年净利率是多少？", need=need)
        state.active_snapshot_id = "S1"

        def _ref(sid, formula_id, period, display="18.12", unit="%",
                 status="CALCULATED_EXACT", company="ACME"):
            return RS.StructuredResultRef(
                result_type="financial_metric", snapshot_id=sid, item_code=None,
                formula_id=formula_id, formula_version="v1", period=period,
                raw_value=display, display_value=display, unit=unit, status=status,
                reason_code=None, input_record_refs=[], input_snapshot_item_refs=[],
                company_id=company, scope="consolidated", currency="CNY",
                purpose="credit_analysis", snapshot_status="valid")

        state.structured_refs = [_ref("S1", "NET_MARGIN", "2025-12-31"),
                                 _ref("S2", "NET_MARGIN", "2025-12-31")]

        def _authority(sid: str) -> SnapshotAuthority | None:
            return SnapshotAuthority(exists=True, is_current=(sid == "S1"),
                                     validity="valid", report_blocked=False,
                                     quarantined=False)

        def _answer(claim_text: str, sid: str = "S1") -> H.ResearchAnswer:
            return H.ResearchAnswer(
                question_id="q", answer_text=claim_text,
                claims=[H.Claim(claim_id="c1", text=claim_text, kind="fact",
                                citation_refs=[0])],
                citations=[H.CitationRef(ref_type="structured", snapshot_id=sid,
                                          formula_id="NET_MARGIN",
                                          formula_version="v1",
                                          period="2025-12-31")])

        supported = evaluate_structured_provenance(
            state, _answer("2025年净利率为18.12%"), snapshot_authority=_authority)
        partial = evaluate_structured_provenance(
            state, _answer("2025年净利率同比如何变化？18.12%"),
            snapshot_authority=_authority)
        not_current = evaluate_structured_provenance(
            state, _answer("2025年净利率为18.12%", sid="S2"),
            snapshot_authority=_authority)

        print(json.dumps({
            "claim_period_single": _claim_period("2025年净利率18.12%"),
            "claim_period_multi": _claim_period("2024年较2025年如何变化？"),
            "direction_up": _claim_direction("净利率同比上升"),
            "direction_down": _claim_direction("净利率下降"),
            "direction_unchanged": _claim_direction("净利率持平"),
            "direction_none": _claim_direction("净利率为18.12%"),
            "ref_amount": E.dataclasses_asdict(_ref_amount(state.structured_refs[0]))
                         if _ref_amount(state.structured_refs[0]) else None,
            "supported": {k: {"verdict": v.verdict, "reason": v.reason}
                          for k, v in supported.items()},
            "partial_trend": {k: {"verdict": v.verdict, "reason": v.reason}
                              for k, v in partial.items()},
            "not_current": {k: {"verdict": v.verdict, "reason": v.reason}
                            for k, v in not_current.items()},
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
