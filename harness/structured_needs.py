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
- 无法精确表达的数值方面 → semantic_mismatches_rejected（不冒充 DB 结果）。

本模块只做「纯派生」（无 I/O、无 Router、无 Registry）；执行编排在 runtime.py。

CLI: python -m harness.structured_needs --self-check
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from financial_v2 import formulas, mapping
from harness.state import is_numeric_aspect
from routing import db_targets


@dataclass(frozen=True)
class StructuredSubNeed:
    """一个可精确表达的结构化子 need（db_targets.resolve_db_target 命中）。"""

    sub_need_id: str
    source: str             # "aspect" | "question"
    aspect_id: str | None   # 来自 required_aspects 时非空；question 来源为 None
    text: str               # 派生来源文本（aspect 文本或原始问题）
    target_type: str        # "field" | "metric"
    standard_item_code: str | None
    formula_id: str | None
    formula_version: str | None


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


# 业务分块 / 境内外等「子范围」限定词：问题一旦出现，说明提问的是子范围而非公司口径
# 聚合值（如「动力电池业务毛利率」≠ 公司整体毛利率）。聚合快照无法精确表达 → 语义不匹配。
# 与 db_targets._SUB_ITEM_BLOCKERS（科目子类：受限资金≠货币资金）互补：后者挡科目子类，
# 这里挡「业务分块/境内外/分产品分地区」维度的子范围。
_SEGMENT_SCOPE_QUALIFIERS = (
    "动力电池业务", "储能业务", "储能", "海外", "境外",
    "分业务", "分产品", "分地区", "分行业", "各业务", "各产品", "业务板块",
)


def _has_segment_scope_qualifier(text: str) -> bool:
    return any(q in (text or "") for q in _SEGMENT_SCOPE_QUALIFIERS)


def derive_structured_subneeds(question: str, required_aspects: list) -> Derivation:
    """从 required aspects + 原始问题确定性派生子 need。

    - 数值/指标方面（is_numeric_aspect）：resolve_db_target 命中 → 子 need；
      否则 → RejectedAspect（语义不匹配，诚实记录）。
    - 原始问题：直接点名 field/metric 时派生子 need（hybrid 题的数值部分）。
    - 按 (target_type, standard_item_code, formula_id) 去重；aspect 优先于 question。
    - 问题限定「业务分块/境内外」子范围 → 公司口径 field/metric 不精确表达 → 拒绝
      （不派生，reason=segment_scope_qualifier，不冒充 DB 结果）。
    """
    subneeds: list[StructuredSubNeed] = []
    rejected: list[RejectedAspect] = []
    seen: set[tuple] = set()
    q = (question or "").strip()
    segment_scope = _has_segment_scope_qualifier(q)

    def _add(text: str, source: str, aspect_id: str | None) -> str | None:
        """返回 None=成功派生；否则返回拒绝原因（no_deterministic_db_target /
        segment_scope_qualifier）。"""
        if segment_scope:
            return "segment_scope_qualifier"
        target = db_targets.resolve_db_target(text)
        if target is None:
            return "no_deterministic_db_target"
        key = (target.target_type, target.standard_item_code, target.formula_id)
        if key in seen:
            return None
        seen.add(key)
        subneeds.append(StructuredSubNeed(
            sub_need_id=f"sn{len(subneeds)}", source=source, aspect_id=aspect_id,
            text=text, target_type=target.target_type,
            standard_item_code=target.standard_item_code,
            formula_id=target.formula_id, formula_version=target.formula_version))
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

    return Derivation(subneeds=subneeds, rejected_aspects=rejected)


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
    """由 Router 产出的 DB filters 组装工具名 + 参数（字段 vs 指标）。"""
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
        # 纯指标：方面「净利率」→ PROF_NET_MARGIN。
        met = derive_structured_subneeds(
            "2025年合并口径下，销售利润率（净利率）如何变化？",
            [{"aspect_id": "a1", "text": "净利率", "source": "DATASET_MAPPING"}])
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
                     "expression": target_expression(s)}
                    for s in d.subneeds
                ],
                "rejected": [{"aspect_id": r.aspect_id, "text": r.text,
                              "reason": r.reason} for r in d.rejected_aspects],
            }

        print(json.dumps({
            "hybrid_field": _dump(hyb),
            "pure_metric": _dump(met),
            "semantic_mismatch": _dump(rej),
            "target_period_2025": target_period("2025年经营活动现金流净额是多少？"),
            "target_period_none": target_period("经营活动现金流净额是多少？"),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
