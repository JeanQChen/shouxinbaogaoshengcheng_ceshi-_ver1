"""WritingSpec v1 的只读 schema / loader / validator。

载体：templates/writing_specs/credit_report_v1.yaml。

职责：加载唯一版本化写作规范（逐字 8/5/9 H2 目录 + 逐 aspect 唯一 primary 正文槽位），
做纯声明式校验，并可对 Contract v2 做交叉核对（每 aspect 恰一个 primary、synth 仅 phase5）。

边界（R1-A §十）：本模块不接正式 Writer / SectionTask 写作运行时，不生成正文；
一个 aspect 只能有一个 primary，其余位置仅 secondary_reference。

CLI：
    python -m sections.writing_spec templates/writing_specs/credit_report_v1.yaml \
        [--contract templates/contracts/standard_v3.yaml]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from contracts import schema_v2 as S

logger = logging.getLogger("sections.writing_spec")

WRITING_SPEC_ID = "credit_report_v1"
SCHEMA_VERSION = "writing-spec-v1"
CONTRACT_ASSET = "templates/contracts/standard_v3.yaml"

# 已确认 8/5/9 H2 目录（逐字，禁止重新设计）
VERBATIM_TOC = {
    "company": [
        ("co-h1", "企业基本信息与历史沿革"),
        ("co-h2", "股权结构、控股股东及实际控制人"),
        ("co-h3", "集团结构与主要子公司"),
        ("co-h4", "主营业务、经营模式与产业链"),
        ("co-h5", "核心竞争力与发展计划"),
        ("co-h6", "公司治理、内控与管理层稳定性"),
        ("co-h7", "偿债、授信、发债与对外担保"),
        ("co-h8", "财务与信用综合判断"),
    ],
    "financial": [
        ("fin-h1", "财务数据质量与审计意见"),
        ("fin-h2", "资产负债结构分析"),
        ("fin-h3", "盈利能力与利润质量"),
        ("fin-h4", "现金流结构与现金保障"),
        ("fin-h5", "整体分析"),
    ],
    "industry": [
        ("ind-h1", "行业定义与细分领域"),
        ("ind-h2", "行业规模、增速与周期位置"),
        ("ind-h3", "供需关系、价格与成本驱动"),
        ("ind-h4", "竞争格局与集中度"),
        ("ind-h5", "政策、监管、技术与外部冲击"),
        ("ind-h6", "公司行业地位与相对竞争力"),
        ("ind-h7", "可比公司选择与相对比较"),
        ("ind-h8", "风险向授信主体的传导路径"),
        ("ind-h9", "行业结论有效期与监测指标"),
    ],
}
_MAPPING_FIELDS = (
    "subsection_id", "aspect_id", "role", "content_role", "table_schema",
    "citation_granularity", "gap_display_policy", "display_tier",
    "period_language_policy",
)
_ROLES = ("primary", "secondary_reference")
_CITATION_GRANULARITIES = ("claim", "paragraph", "table_row")

# 主槽位归属前缀（R1-A §七）：公司→co-*，财务→fin-*，行业→ind-*，synth→phase5。
_SECTION_PREFIX = {
    "company": "co-",
    "financial": "fin-",
    "industry": "ind-",
}

# producer_kind → 目标 Writer Pack/FactPack 章节前缀（R1-A §三：显式声明，不靠 section 反推）。
# topic_harness → 公司/行业 TopicResearchPack；financial_workflow → 财务 FinancialFactPack；
# phase4_section_derived → 章节派生总结 H2（本章）；phase5_synthesizer → Phase 5 综合。
_PRODUCER_KIND_PREFIXES = {
    "topic_harness": ("co-", "ind-"),
    "financial_workflow": ("fin-",),
    "phase4_section_derived": ("co-", "fin-", "ind-"),
    "phase5_synthesizer": ("phase5",),
}


@dataclass(frozen=True)
class WritingSpec:
    writing_spec_id: str
    schema_version: str
    status: str
    created_at: str
    approved_at: str | None
    frozen_at: str | None
    contract_ref: dict
    source_policy_ref: str
    toc: dict
    mappings: list
    corrections: list
    raw: dict = field(repr=False)


@dataclass(frozen=True)
class WritingSpecValidationResult:
    valid: bool
    errors: list
    warnings: list
    stats: dict


def load_writing_spec(path: str) -> WritingSpec:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"写作规范文件不存在: {path}")
    return parse_writing_spec(p.read_text(encoding="utf-8"))


def parse_writing_spec(text: str) -> WritingSpec:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("WritingSpec YAML 顶层必须是 dict")
    return WritingSpec(
        writing_spec_id=doc.get("writing_spec_id", ""),
        schema_version=doc.get("schema_version", ""),
        status=doc.get("status", "candidate"),
        created_at=doc.get("created_at", ""),
        approved_at=doc.get("approved_at"),
        frozen_at=doc.get("frozen_at"),
        contract_ref=doc.get("contract_ref", {}),
        source_policy_ref=doc.get("source_policy_ref", ""),
        toc=doc.get("toc", {}),
        mappings=list(doc.get("mappings", [])),
        corrections=list(doc.get("corrections", [])),
        raw=dict(doc),
    )


def _check_toc(ws: WritingSpec, errors: list[str]) -> None:
    toc = ws.toc
    if set(toc.keys()) != set(VERBATIM_TOC.keys()):
        errors.append(f"toc 应含章节 {list(VERBATIM_TOC.keys())}")
        return
    for sec_id, expect in VERBATIM_TOC.items():
        got = toc.get(sec_id)
        if not isinstance(got, list) or len(got) != len(expect):
            errors.append(f"toc.{sec_id} 应 {len(expect)} 项，实际 {len(got) if isinstance(got, list) else '非列表'}")
            continue
        for i, (sid, h2) in enumerate(expect):
            item = got[i]
            if not isinstance(item, dict):
                errors.append(f"toc.{sec_id}[{i}] 应为 dict")
                continue
            if item.get("subsection_id") != sid or item.get("h2") != h2:
                errors.append(f"toc.{sec_id}[{i}] 应为 {sid}/{h2}，实际 "
                              f"{item.get('subsection_id')}/{item.get('h2')}")


def _valid_subsection_ids(ws: WritingSpec) -> set[str]:
    """TOC 全部 subsection_id 并集，外加合法 phase5（R1-A §三：subsection 必须可解析）。"""
    ids: set[str] = set()
    for items in ws.toc.values():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("subsection_id"):
                    ids.add(item["subsection_id"])
    ids.add("phase5")
    return ids


def validate_writing_spec(ws: WritingSpec, contract: S.ContractV2 | None = None,
                          ) -> WritingSpecValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if ws.writing_spec_id != WRITING_SPEC_ID:
        errors.append(f"writing_spec_id 应为 {WRITING_SPEC_ID!r}，实际 {ws.writing_spec_id!r}")
    if ws.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION!r}")

    # 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二）
    errors.extend(S.frozen_asset_errors(ws.raw))
    if ws.contract_ref.get("contract_version") != S.CONTRACT_VERSION_V2:
        errors.append("contract_ref.contract_version 应为 v2")
    if ws.contract_ref.get("asset") != CONTRACT_ASSET:
        errors.append(f"contract_ref.asset 应为 {CONTRACT_ASSET}")
    if ws.source_policy_ref != "source_policy_v1":
        errors.append(f"source_policy_ref 应为 source_policy_v1，实际 {ws.source_policy_ref!r}")

    _check_toc(ws, errors)

    # 逐映射 9 字段
    prim_by_aspect: Counter = Counter()
    n_secondary = 0
    for i, m in enumerate(ws.mappings):
        if not isinstance(m, dict):
            errors.append(f"mappings[{i}] 应为 dict")
            continue
        missing = [f for f in _MAPPING_FIELDS if f not in m]
        if missing:
            errors.append(f"mappings[{i}] 缺少字段 {missing}")
            continue
        if m["role"] not in _ROLES:
            errors.append(f"mappings[{i}] 未知 role {m['role']!r}")
        if m["citation_granularity"] not in _CITATION_GRANULARITIES:
            errors.append(f"mappings[{i}] 未知 citation_granularity {m['citation_granularity']!r}")
        if m["content_role"] not in S.CONTENT_ROLES:
            errors.append(f"mappings[{i}] 未知 content_role {m['content_role']!r}")
        if m["display_tier"] not in S.DISPLAY_TIERS:
            errors.append(f"mappings[{i}] 未知 display_tier {m['display_tier']!r}")
        if m["period_language_policy"] not in S.TIME_POLICIES:
            errors.append(f"mappings[{i}] 未知 period_language_policy {m['period_language_policy']!r}")
        if m["role"] == "primary":
            prim_by_aspect[m["aspect_id"]] += 1
        else:
            n_secondary += 1

    # 一个 aspect 恰一个 primary
    multi = {k: v for k, v in prim_by_aspect.items() if v > 1}
    if multi:
        errors.append(f"以下 aspect 有多个 primary: {multi}")

    if not ws.corrections:
        errors.append("corrections 不能为空（应记录 §十 修正事项）")

    # 交叉核对 Contract v2
    n_synth_phase5 = 0
    if contract is not None:
        contract_aspect_ids = {a.aspect_id for a in contract.all_aspects()}
        sec_of = {a.aspect_id: sec.section_id
                  for sec in contract.sections for a in sec.all_aspects()}
        producer_of = {a.aspect_id: a.producer_kind for a in contract.all_aspects()}
        valid_subsection_ids = _valid_subsection_ids(ws)
        # 每个契约 aspect 恰一个 primary
        zero_primary = sorted(contract_aspect_ids - set(prim_by_aspect.keys()))
        if zero_primary:
            errors.append(f"契约 aspect 缺少 primary 映射: {zero_primary}")
        extra_primary = sorted(set(prim_by_aspect.keys()) - contract_aspect_ids)
        if extra_primary:
            errors.append(f"存在契约外 aspect 的 primary 映射: {extra_primary}")
        # 逐映射：primary + secondary 的 aspect_id 均须存在于契约；
        # subsection_id 均须存在于 TOC 或为合法 phase5（R1-A §三）。
        for i, m in enumerate(ws.mappings):
            if not isinstance(m, dict):
                continue
            aid = m.get("aspect_id")
            sub = m.get("subsection_id")
            if aid is not None and aid not in contract_aspect_ids:
                errors.append(f"mappings[{i}] 引用了契约外 aspect_id {aid!r}"
                              f"（role={m.get('role')}）")
            if sub is not None and sub not in valid_subsection_ids:
                errors.append(f"mappings[{i}] subsection_id {sub!r} 不存在于 TOC 且非 phase5")
        # synth 仅 phase5
        synth_ids = {a.aspect_id for a in contract.all_aspects()
                     if a.producer_kind == "phase5_synthesizer"}
        for m in ws.mappings:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "primary" and m.get("aspect_id") in synth_ids:
                if m.get("subsection_id") != "phase5":
                    errors.append(f"synth {m.get('aspect_id')} primary 应指向 phase5，"
                                  f"实际 {m.get('subsection_id')}")
                n_synth_phase5 += 1
        if n_synth_phase5 != len(synth_ids):
            errors.append(f"synth 落 phase5 数应为 {len(synth_ids)}，实际 {n_synth_phase5}")
        # 主槽位归属（R1-A §七）：公司→co-*，财务→fin-*，行业→ind-*（synth→phase5 已在上方校验）
        # + producer_kind → 目标 Writer Pack/FactPack 显式一致（R1-A §三）。
        for m in ws.mappings:
            if not isinstance(m, dict):
                continue
            if m.get("role") != "primary":
                continue
            aid = m.get("aspect_id")
            sub = m.get("subsection_id")
            sec = sec_of.get(aid)
            prefix = _SECTION_PREFIX.get(sec) if sec else None
            if prefix and sub and not sub.startswith(prefix):
                errors.append(f"{aid} 主槽位 {sub} 不属于 {sec} 章节"
                              f"（应为 {prefix}*）")
            pk = producer_of.get(aid)
            pk_prefixes = _PRODUCER_KIND_PREFIXES.get(pk)
            if pk_prefixes and sub and not any(sub.startswith(p) for p in pk_prefixes):
                errors.append(f"{aid} 主槽位 {sub} 与 producer_kind={pk} "
                              f"目标章节不一致（应为 {pk_prefixes} 前缀）")

    stats = {
        "writing_spec_id": ws.writing_spec_id,
        "mappings": len(ws.mappings),
        "primary": sum(prim_by_aspect.values()),
        "secondary": n_secondary,
        "synth_phase5": n_synth_phase5,
    }
    return WritingSpecValidationResult(
        valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 WritingSpec v1")
    parser.add_argument("path", help="credit_report_v1.yaml 路径")
    parser.add_argument("--contract", default=None,
                        help="standard_v3.yaml 路径（可选，启用契约交叉核对）")
    args = parser.parse_args(argv)

    try:
        ws = load_writing_spec(args.path)
        contract = None
        if args.contract:
            from contracts.loader_v2 import load_contract_v2
            contract = load_contract_v2(args.contract)
        r = validate_writing_spec(ws, contract)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验 WritingSpec 失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if not r.valid:
        print(f"校验失败（{len(r.errors)} 处）:")
        for err in r.errors:
            print(f"  - {err}")
        return 1
    print(f"WritingSpec 校验通过: {r.stats['mappings']} 映射 "
          f"({r.stats['primary']} primary / {r.stats['secondary']} secondary) "
          f"synth→phase5={r.stats['synth_phase5']}")
    for w in r.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
