"""Phase 3 Batch B · 用户 A3：混合需求本地子 need 派生（确定性，Rules 内部，非 LLM）。

混合需求 = 显式外部来源要求（web/external）+ 本地检索要求（本地来源类或深信号）。
父 need 路由 EXTERNAL_RESEARCH（外部优先，见 routing.router._route 顺序）；本地部分不能靠
「把全部工具对全部路由开放」补齐——那会破坏来源权限门控。此处把本地部分拆成有界本地
子 need：子 need 仅保留本地来源类（剥离 external），经 Router 单独路由到本地通道
（DIRECT/STANDARD/DEEP/DB），由 runtime 经 ToolRegistry 执行本地检索，结果汇入父 state
（evidence_ids / inspected_evidence），保留父子来源链、权限、预算与合并规则。

本模块只做纯派生（无 I/O、无 Router 执行、无 Registry）；执行编排在 runtime.py。

CLI: python -m harness.mixed_needs --self-check
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from routing import router as router_mod
from routing import schema as RS


@dataclass(frozen=True)
class LocalSubNeed:
    """混合需求拆出的有界本地子 need（父 need 的本地部分）。

    local_source_classes 只含 router.LOCAL_SOURCE_CLASSES 白名单内的本地来源类；
    trigger ∈ {"explicit_local_source", "deep_signal"} 描述为何判定为混合。
    """

    sub_need_id: str
    parent_need_id: str
    question: str
    local_source_classes: tuple[str, ...]
    trigger: str
    budget_note: str


def derive_local_subneed(need: RS.InformationNeed,
                         context: RS.RouteContext) -> LocalSubNeed | None:
    """混合需求 → 派生本地子 need；非混合返回 None。

    本地子 need 的 need_id 稳定（父 need_id + "__local"），便于去重与父子链审计。
    本地来源类 = 父 required_source_types 与 LOCAL_SOURCE_CLASSES 的交集；若交集为空
    （本地要求来自深信号，如「比较」），trigger=deep_signal，仍派生本地子 need 走 DEEP。
    """
    if not router_mod.is_mixed_need(need, context):
        return None
    local_classes = tuple(
        sc for sc in (need.required_source_types or [])
        if sc in router_mod.LOCAL_SOURCE_CLASSES
    )
    trigger = "explicit_local_source" if local_classes else "deep_signal"
    return LocalSubNeed(
        sub_need_id=f"{need.need_id}__local",
        parent_need_id=need.need_id,
        question=need.question,
        local_source_classes=local_classes,
        trigger=trigger,
        budget_note="bounded: 1 local search + up to 3 inspect, merged into parent state",
    )


def build_local_need(sub: LocalSubNeed,
                     parent: RS.InformationNeed) -> RS.InformationNeed:
    """由本地子 need 构造可路由的本地 InformationNeed（剥离 external 来源要求）。

    保留父 required_evidence_types 中的本地证据类（剥离 web）；required_source_types 只用
    本地来源类（external 移除），避免再次触发外部路由。depends_on 指向父 need_id 保留来源链。
    """
    evidence_types = [
        et for et in (parent.required_evidence_types or []) if et != "web"
    ] or ["paragraph"]
    return RS.InformationNeed(
        need_id=sub.sub_need_id,
        section_id=parent.section_id,
        question=sub.question,
        required_evidence_types=evidence_types,
        required_source_types=list(sub.local_source_classes),
        time_scope=parent.time_scope,
        priority=parent.priority,
        depends_on=[sub.parent_need_id],
    )


def _main(argv: list[str]) -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.mixed_needs", description="混合需求本地子 need 派生自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        ctx = RS.RouteContext(
            company_id="300750", report_as_of="2025-06-30",
            available_document_ids=["NDSD_2025_year"],
            available_source_types=["annual_report", "company_industry"],
            supported_db_fields=["TOTAL_ASSETS"], supported_metric_ids=["PROF_ROE"],
            available_db_fields=["TOTAL_ASSETS"], available_metric_ids=["PROF_ROE"],
            external_research_enabled=True)

        # 显式本地来源类 + 外部 → 混合（explicit_local_source）。
        mixed_src = RS.InformationNeed(
            need_id="industry_risk_transmission", section_id="industry",
            question="行业风险向借款人收入、成本、现金流的传导",
            required_evidence_types=["paragraph"],
            required_source_types=["company_industry", "external"],
            time_scope=None, priority="P0", depends_on=[])
        # web/external + 深信号「比较」→ 混合（deep_signal）。
        mixed_deep = RS.InformationNeed(
            need_id="industry_comparables", section_id="industry",
            question="选择 3～5 家可比公司并做相对比较",
            required_evidence_types=["web"],
            required_source_types=["external"],
            time_scope=None, priority="P0", depends_on=[])
        # 纯外部 → 非混合。
        pure_ext = RS.InformationNeed(
            need_id="mcap", section_id="company", question="公司当前市值是多少？",
            required_evidence_types=["web"], required_source_types=["external"],
            time_scope=None, priority="P0", depends_on=[])

        def _dump(need) -> dict | None:
            sub = derive_local_subneed(need, ctx)
            if sub is None:
                return None
            local = build_local_need(sub, need)
            return {
                "sub_need_id": sub.sub_need_id,
                "parent_need_id": sub.parent_need_id,
                "trigger": sub.trigger,
                "local_source_classes": list(sub.local_source_classes),
                "local_need_source_types": local.required_source_types,
                "local_need_evidence_types": local.required_evidence_types,
                "local_need_depends_on": local.depends_on,
            }

        print(json.dumps({
            "mixed_explicit_local_source": _dump(mixed_src),
            "mixed_deep_signal": _dump(mixed_deep),
            "pure_external_is_none": _dump(pure_ext) is None,
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
