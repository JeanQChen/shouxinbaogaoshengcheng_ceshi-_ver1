"""Phase 3 Batch B · 用户 §三：结构化子 need 派生（确定性，Rules 内部，非 LLM）。

把原始财务问题接入 Financial Snapshot：原始问题 → required aspects → 数值/指标方面
确定性派生结构化子 InformationNeed → Router / DB resolver 路由 → ToolRegistry 执行 →
StructuredResultRef 汇入 state.structured_refs；原因/解释方面继续走 Evidence。

关键约束：
- 仅「可精确表达」的 field/metric 派生 DB 子 need（db_targets.resolve_db_target 非 None）；
- 子 need 不回退上层聚合科目（受限资金≠货币资金、机器设备净值≠固定资产，由
  db_targets._SUB_ITEM_BLOCKERS 阻断）；歧义/子类语义 → 不派生；
- 数字来自 StructuredResult（LLM 只解读，不重新算数）；
- 父路由保持 Phase 2 原始判定不变；子 need 路由单独记录（state.structured_subneeds），
  不篡改 Track B；
- 无法精确表达的数值方面 → semantic_mismatches_rejected（不冒充 DB 结果）；
- 趋势/变化语义（同比/如何变化/近三年）→ 跨期子 need（compare/trend），方向/差额由
  compare_financial_periods 工具算好，LLM 只解读不重算。

本模块只做「纯派生」（无 I/O、无 Router、无 Registry）；执行编排在 runtime.py。

CLI: python -m harness.structured_needs --self-check
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field

from financial_v2 import formulas, mapping
from harness import entailment as E
from harness.state import is_numeric_aspect
from routing import db_targets


@dataclass(frozen=True)
class StructuredSubNeed:
    """一个可精确表达的结构化子 need（db_targets.resolve_db_target 命中）。

    period_mode：single（单期，target_period 取原始问题年份）| compare（两期比较）|
    trend（≥3 期趋势序列）。periods：跨期子 need 的报告期（compare=2、trend≥3、
    single=()）。
    """

    sub_need_id: str
    source: str             # "aspect" | "question"
    aspect_id: str | None   # 来自 required_aspects 时非空；question 来源为 None
    text: str               # 派生来源文本（aspect 文本或原始问题）
    target_type: str        # "field" | "metric"
    standard_item_code: str | None
    formula_id: str | None
    formula_version: str | None
    period_mode: str = "single"        # single | compare | trend
    periods: tuple[str, ...] = ()      # 跨期子 need 的报告期（升序，-12-31 年报期）


@dataclass(frozen=True)
class RejectedAspect:
    """数值/指标方面无法精确表达 → 语义不匹配，不派生 DB 子 need（不冒充 DB 结果）。"""

    aspect_id: str
    text: str
    reason: str             # "no_deterministic_db_target"


@dataclass
class Derivation:
    subneeds: list[StructuredSubNeed] = field(default_factory=list)
    rejected_aspects: list[RejectedAspect] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)  # 趋势/跨期无法解析的缺口记录


# 业务分块 / 境内外等「子范围」限定词：问题一旦出现，说明提问的是子范围而非公司口径
# 聚合值（如「动力电池业务毛利率」≠ 公司整体毛利率）。聚合快照无法精确表达 → 语义不匹配。
# 与 db_targets._SUB_ITEM_BLOCKERS（科目子类：受限资金≠货币资金）互补：后者挡科目子类，
# 这里挡「业务分块/境内外/分产品分地区」维度的子范围。
_SEGMENT_SCOPE_QUALIFIERS = (
    "动力电池业务", "储能业务", "储能", "海外", "境外",
    "分业务", "分产品", "分地区", "分行业", "各业务", "各产品", "业务板块",
)

# ≥3 期趋势序列标记（近三年）：available 最近 3 个 -12-31 年报期升序；不足 3 → 不派生。
_MULTI_PERIOD_TREND = ("近三年", "近3年", "连续三年")

# 显式年份抽取（"2025年" → "2025"）。
_YEAR_RE = re.compile(r"(20\d{2})\s*年")


def _has_segment_scope_qualifier(text: str) -> bool:
    return any(q in (text or "") for q in _SEGMENT_SCOPE_QUALIFIERS)


# ---------------------------------------------------------------------------
# 跨期期间解析（纯函数，可独立测试）
# ---------------------------------------------------------------------------

def resolve_comparison_periods(question: str, available_periods: list[str]) -> list[str]:
    """由原始问题 + 可用报告期确定性解析跨期比较期间（fail-closed，不猜）。

    - 两个显式年份（2024至2025 / 2025较2024）→ [YYYY-12-31, YYYY-12-31]（须在 available）；
    - 单年份 + 趋势词（2025如何变化）→ available 中小于该年的最近 -12-31 + 该年；
    - "近三年" → available 最近 3 个 -12-31 升序；不足 3 → []；
    - 其它/不可靠 → []（由调用方记 gap）。
    """
    q = question or ""
    avail = [p for p in (available_periods or []) if isinstance(p, str)]
    annual = sorted({p for p in avail if p.endswith("-12-31")})

    if any(t in q for t in _MULTI_PERIOD_TREND):
        return annual[-3:] if len(annual) >= 3 else []

    years = sorted({int(y) for y in _YEAR_RE.findall(q)})
    if len(years) >= 2:
        targets = [f"{years[0]}-12-31", f"{years[1]}-12-31"]
        return targets if all(t in avail for t in targets) else []

    if len(years) == 1 and E.has_trend_semantics(q):
        y = f"{years[0]}-12-31"
        priors = [p for p in annual if p < y]
        if y in avail and priors:
            return [priors[-1], y]
        return []

    return []


# ---------------------------------------------------------------------------
# 主派生
# ---------------------------------------------------------------------------

def derive_structured_subneeds(question: str, required_aspects: list,
                               available_periods: list[str] | None = None) -> Derivation:
    """从 required aspects + 原始问题确定性派生子 need。

    - 数值/指标方面（is_numeric_aspect）：resolve_db_target 命中 → 子 need；
      否则 → RejectedAspect（语义不匹配，诚实记录）。
    - 原始问题：直接点名 field/metric 时派生子 need（hybrid 题的数值部分）。
    - 去重键 = (target_type, standard_item_code, formula_id, period_mode, periods)；
      aspect 优先于 question。
    - 问题限定「业务分块/境内外」子范围 → 公司口径 field/metric 不精确表达 → 拒绝。
    - 趋势语义（问题级）：目标与问题目标一致的 metric 派生 compare（2 期）/ trend（≥3 期），
      替代 single（避免 _same_ref 折叠丢比较字段）；field → 逐期 loop；<2 期 → 保留
      single + 记 gap。
    """
    subneeds: list[StructuredSubNeed] = []
    rejected: list[RejectedAspect] = []
    gaps: list[dict] = []
    seen: set[tuple] = set()
    q = (question or "").strip()
    segment_scope = _has_segment_scope_qualifier(q)

    # 问题级趋势（target 与问题一致的子 need 继承此趋势，避免 aspect 文本无趋势词时漏判）。
    qtrend = E.has_trend_semantics(q)
    qtarget = (db_targets.resolve_db_target(q) if q and not segment_scope else None)
    qperiods = resolve_comparison_periods(q, available_periods or []) if qtrend else []

    def _period_plan(target, text: str) -> tuple[str, tuple[str, ...], dict | None]:
        """按（target 类型 + 文本/问题趋势）返回 (period_mode, periods, gap|None)。"""
        trend = qtrend and qtarget is not None and (
            target.target_type == qtarget.target_type
            and target.standard_item_code == qtarget.standard_item_code
            and target.formula_id == qtarget.formula_id)
        if not trend and qtarget is None:
            trend = E.has_trend_semantics(text)
        cps = qperiods if (trend and qtarget is not None) else (
            resolve_comparison_periods(text, available_periods or [])
            if trend else [])
        gap = None
        if not trend:
            return "single", (), None
        if target.target_type == "metric":
            if len(cps) == 2:
                return "compare", tuple(cps), None
            if len(cps) >= 3:
                return "trend", tuple(cps), None
            gap = {"reason": "trend_prior_missing", "detail": list(available_periods or [])}
            return "single", (), gap
        # field → 逐期 loop（compare/trend 均回退为多期 periods，由 runtime 逐期 lookup）。
        if len(cps) >= 2:
            mode = "compare" if len(cps) == 2 else "trend"
            return mode, tuple(cps), None
        if len(cps) == 1:
            return "single", tuple(cps), None
        gap = {"reason": "trend_prior_missing", "detail": list(available_periods or [])}
        return "single", (), gap

    def _add(text: str, source: str, aspect_id: str | None) -> str | None:
        """返回 None=成功派生；否则返回拒绝原因（no_deterministic_db_target /
        segment_scope_qualifier）。"""
        if segment_scope:
            return "segment_scope_qualifier"
        target = db_targets.resolve_db_target(text)
        if target is None:
            return "no_deterministic_db_target"
        period_mode, periods, gap = _period_plan(target, text)
        key = (target.target_type, target.standard_item_code, target.formula_id,
               period_mode, periods)
        if key in seen:
            return None
        seen.add(key)
        if gap is not None:
            gaps.append({"aspect_id": aspect_id, "text": text, **gap})
        subneeds.append(StructuredSubNeed(
            sub_need_id=f"sn{len(subneeds)}", source=source, aspect_id=aspect_id,
            text=text, target_type=target.target_type,
            standard_item_code=target.standard_item_code,
            formula_id=target.formula_id, formula_version=target.formula_version,
            period_mode=period_mode, periods=periods))
        return None

    for asp in (required_aspects or []):
        text = (asp.get("text", "") or "").strip()
        if not text or not is_numeric_aspect(text):
            continue
        reason = _add(text, source="aspect", aspect_id=asp.get("aspect_id"))
        if reason is not None:
            rejected.append(RejectedAspect(
                aspect_id=asp.get("aspect_id", ""), text=text, reason=reason))

    if q:
        _add(q, source="question", aspect_id=None)

    return Derivation(subneeds=subneeds, rejected_aspects=rejected, gaps=gaps)


def target_expression(sn: StructuredSubNeed) -> str:
    """把子 need 目标还原为精确中文表达式（供 Router 路由，无 DEEP/EXTERNAL 信号）。"""
    if sn.target_type == "metric":
        fdef = formulas.build_registry().get(sn.formula_id)
        return fdef.name if fdef is not None else (sn.formula_id or "")
    for rule in mapping.build_builtin_rules():
        if rule.standard_item_code == sn.standard_item_code and rule.aliases:
            return rule.aliases[0]
    return sn.standard_item_code or ""


def target_period(question: str) -> str | None:
    """从原始问题提取目标报告期（快照内期间），与 snapshot_as_of_date 分离。"""
    return db_targets.resolve_target_period(question)


def db_tool_args(company_id: str, filters: dict) -> tuple[str, dict]:
    """由 Router 产出的 DB filters 组装工具名 + 参数（字段 vs 指标，单期）。"""
    dims = {k: filters[k] for k in ("snapshot_as_of_date", "target_period",
                                    "scope", "currency", "purpose")
            if filters.get(k)}
    if filters.get("db_target_type") == "field":
        args = {"company_id": company_id,
                "standard_item_code": filters.get("standard_item_code", "")}
        args.update(dims)
        return "lookup_company_field", args
    args = {"company_id": company_id, "formula_id": filters.get("formula_id", "")}
    if filters.get("formula_version"):
        args["formula_version"] = filters["formula_version"]
    args.update(dims)
    return "lookup_financial_metric", args


def compare_tool_args(company_id: str, filters: dict,
                      periods: list[str]) -> tuple[str, dict]:
    """compare_financial_periods 参数（period_a/period_b + dims，无 target_period）。"""
    dims = {k: filters[k] for k in ("snapshot_as_of_date", "scope", "currency", "purpose")
            if filters.get(k)}
    args = {"company_id": company_id, "formula_id": filters.get("formula_id", ""),
            "period_a": periods[0], "period_b": periods[1]}
    if filters.get("formula_version"):
        args["formula_version"] = filters["formula_version"]
    args.update(dims)
    return "compare_financial_periods", args


def classify_aspects(required_aspects: list, subneeds: list,
                     rejected: list) -> list[dict]:
    """逐 required-aspect 标注解决通道（报告用，不影响判分）。

    channel ∈ {"db", "rejected", "evidence"}：
    - db：有 aspect 来源且 RESOLVED 的结构化子 need；
    - rejected：数值方面无法精确表达（semantic mismatch）；
    - evidence：其余（原因/解释方面，走 Evidence）。
    """
    db_aspect_ids = {
        sn["aspect_id"] for sn in (subneeds or [])
        if sn.get("aspect_id") and sn.get("status") == "RESOLVED"
    }
    rejected_ids = {r["aspect_id"] for r in (rejected or [])}
    rows: list[dict] = []
    for asp in (required_aspects or []):
        aid = asp.get("aspect_id", "")
        if aid in db_aspect_ids:
            channel = "db"
        elif aid in rejected_ids:
            channel = "rejected"
        else:
            channel = "evidence"
        rows.append({"aspect_id": aid, "text": asp.get("text", ""),
                     "channel": channel})
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.structured_needs",
        description="结构化子 need 派生自检（纯函数，无 Router/LLM）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # hybrid：问题直接点名字段（经营活动现金流净额 → OPERATING_CASH_FLOW）。
        hyb = derive_structured_subneeds(
            "合并口径下，2025年经营活动现金流净额是多少？变化原因是什么？",
            [{"aspect_id": "a1", "text": "现金流结构", "source": "DATASET_MAPPING"}])
        # 纯指标趋势：问题「如何变化」+ 单年份 → compare（2 期）。
        avail = ["2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31"]
        met = derive_structured_subneeds(
            "2025年合并口径下，销售利润率（净利率）如何变化？",
            [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}],
            available_periods=avail)
        # 近三年趋势 → trend（3 期）。
        met3 = derive_structured_subneeds(
            "近三年净利率趋势如何？",
            [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}],
            available_periods=avail)
        # 语义不匹配：数值方面「各业务收入及收入占比」无法精确表达 → rejected。
        rej = derive_structured_subneeds(
            "主营业务有哪些？各业务收入占比如何？",
            [{"aspect_id": "a1", "text": "各业务收入及收入占比", "source": "DATASET_MAPPING"}])

        def _dump(d: Derivation) -> dict:
            return {
                "subneeds": [
                    {"sub_need_id": s.sub_need_id, "source": s.source,
                     "aspect_id": s.aspect_id, "target_type": s.target_type,
                     "standard_item_code": s.standard_item_code,
                     "formula_id": s.formula_id,
                     "formula_version": s.formula_version,
                     "period_mode": s.period_mode, "periods": list(s.periods),
                     "expression": target_expression(s)}
                    for s in d.subneeds
                ],
                "rejected": [{"aspect_id": r.aspect_id, "text": r.text,
                              "reason": r.reason} for r in d.rejected_aspects],
                "gaps": d.gaps,
            }

        print(json.dumps({
            "hybrid_field": _dump(hyb),
            "pure_metric_compare": _dump(met),
            "pure_metric_trend3": _dump(met3),
            "semantic_mismatch": _dump(rej),
            "target_period_2025": target_period("2025年经营活动现金流净额是多少？"),
            "target_period_none": target_period("经营活动现金流净额是多少？"),
            "resolve_compare_two_years": resolve_comparison_periods(
                "2024年较2025年净利率如何变化？", avail),
            "resolve_compare_single_year": resolve_comparison_periods(
                "2025年净利率如何变化？", avail),
            "resolve_compare_three_year": resolve_comparison_periods(
                "近三年净利率趋势如何？", avail),
            "resolve_compare_unreliable": resolve_comparison_periods(
                "净利率如何变化？", avail),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
