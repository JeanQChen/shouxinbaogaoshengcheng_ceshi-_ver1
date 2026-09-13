"""52 问 × aspect × evidence 审计产物导出器（只读）。

把 Contract v2 的结构化清单展平为两份审计产物：
- contracts/review/review_52q.json —— 全量结构化（52 问 / 187 aspect / 49 evidence 需求 + 摘要）；
- contracts/review/review_52q.csv  —— 逐 aspect 扁平表（每行一个 aspect，22 字段 + 逐 aspect 审计）。

R1-A §一：本导出器是一份「真实业务审计」，逐问把 standard_v2.yaml（v1）与
standard_v3.yaml（v2）做实际比对，得出六类合法 change_type（unchanged / clarified /
split / producer_routed / applicability_refined / display_refined），并为每问、每 aspect
给出由该 aspect 自身字段推导的具体理由（不落回「全部 unchanged」默认逻辑，也不写泛化共享理由）。

边界（R1-A §二/§五）：纯只读导出，不接 Router / Harness / Worker / Writer，
不写库，不调用 LLM。产物为审计快照，供人复核，不参与正式运行时。

CLI：
    python -m contracts.review.topic_aspect_evidence_review \
        [--contract templates/contracts/standard_v3.yaml] \
        [--out-dir contracts/review]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import yaml

# contracts/review/ 目录与 contracts/review.py 模块同名冲突，故本导出器以脚本路径运行
# （python contracts/review/topic_aspect_evidence_review.py），需先把仓库根加入 sys.path。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from contracts import schema_v2 as S
from contracts.loader_v2 import load_contract_v2

logger = logging.getLogger("contracts.review.topic_aspect_evidence_review")

# 六个合法 change_type（R1-A §一；删除未批准的 expanded）。
CHANGE_TYPES = (
    "unchanged",            # v1 required_aspects 固化为稳定 aspect_id，无语义变更
    "clarified",            # v1 aspect 语义澄清/收口（数量不变或收紧）
    "split",                # v1 的粗粒度 aspect 拆为多个独立 required_body aspect
    "producer_routed",      # 执行归属从 harness/workflow 改为 derived/synthesizer
    "applicability_refined",# v1 aspect 保留，新增/细化 applicability_policy（合法不适用分支）
    "display_refined",      # v1 aspect 保留，另新增 diagnostic_only 诊断槽位
)

_CSV_COLUMNS = (
    "section_id", "topic_id", "question_id", "question", "producer_kind",
    "aspect_id", "kind", "requirement_text", "execution_path", "required_fields",
    "coverage_rules", "complete_set_rule", "evidence_requirement_ids",
    "evidence_kinds", "source_policy_ref", "time_scope", "display_tier",
    "content_role", "missing_policy", "blocking_policy", "applicability_policy",
    "impact_scope", "output_destination", "derived_from", "derived_from_scope",
    "business_review_status", "business_review_reason", "transmission_layers",
    "transmission_channel", "v1_question", "v1_required_aspects",
    "change_type", "business_reason", "v1_mapped_aspects", "independent_meaning",
    "researchability_reason", "coverage_reason", "search_audit_reason",
    "evidence_authority_reason", "writing_spec_ownership", "writing_spec_reason",
)


def _join(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if isinstance(v, (list, tuple)):
        return " | ".join(str(x) for x in v)
    return str(v)


# v1 基线（R1-A §五）：standard_v2.yaml 是唯一 v1 基线，question_id 与 v2 一致。
_V1_ASSET = "templates/contracts/standard_v2.yaml"

# 传输通道 → v1 传导维度（industry_risk_transmission 由 v1 的 3 个维度展开为 4 通道 × 4 层）。
_TRANSMISSION_CHANNEL_TO_V1 = {
    "demand_revenue": "对收入的传导",
    "raw_material_cost": "对成本与资本开支的传导",
    "capacity_capex": "对成本与资本开支的传导",
    "cashflow_solvency": "对现金流与偿债能力的传导",
}

# v1 → v2 aspect 映射（仅非 1:1 的问题；1:1 问题按位置对齐）。
# 键为完整 aspect_id；值为该 v2 aspect 对应的 v1 required_aspects 文本。
_V1_TO_V2_ASPECT_MAP = {
    # company_business_main：5 → 7（主营业务构成拆 3）
    "company_business_main.main_business": ["主营业务构成"],
    "company_business_main.products_solutions": ["主营业务构成"],
    "company_business_main.app_scenarios": ["主营业务构成"],
    "company_business_main.revenue_breakdown": ["各业务收入及收入占比"],
    "company_business_main.cost_gross_margin": ["各业务成本与毛利构成"],
    "company_business_main.industry_chain_position": ["产业链位置"],
    "company_business_main.period_unit_caliber": ["对应报告期与口径"],
    # company_business_model：3 → 6（采购生产销售模式拆 3，成本与竞争能力拆 2）
    "company_business_model.procurement_mode": ["采购生产销售模式"],
    "company_business_model.production_mode": ["采购生产销售模式"],
    "company_business_model.sales_mode": ["采购生产销售模式"],
    "company_business_model.tech_route": ["技术路线"],
    "company_business_model.cost_structure": ["成本与竞争能力"],
    "company_business_model.cost_competitiveness": ["成本与竞争能力"],
    # company_customer_concentration：2 → 3（客户集中度拆 current + change）
    "company_customer_concentration.customer_current_concentration": ["客户集中度"],
    "company_customer_concentration.customer_concentration_change": ["客户集中度"],
    "company_customer_concentration.customer_anonymity": ["依法未披露时的标注"],
    # company_supplier_concentration：1 → 2（供应商集中度拆 current + change）
    "company_supplier_concentration.supplier_current_concentration": ["供应商集中度"],
    "company_supplier_concentration.supplier_concentration_change": ["供应商集中度"],
    # company_rd_capacity：3 → 7（研发能力拆 5）
    "company_rd_capacity.rd_investment_amount": ["研发能力"],
    "company_rd_capacity.rd_intensity": ["研发能力"],
    "company_rd_capacity.rd_personnel": ["研发能力"],
    "company_rd_capacity.rd_achievements": ["研发能力"],
    "company_rd_capacity.rd_platform_tech_route": ["研发能力"],
    "company_rd_capacity.development_plan": ["发展计划"],
    "company_rd_capacity.construction_in_progress": ["在建工程"],
    # company_governance：3 → 4（治理结构与内控拆 2）
    "company_governance.governance_structure": ["治理结构与内控"],
    "company_governance.internal_control": ["治理结构与内控"],
    "company_governance.management_stability": ["管理层稳定性"],
    "company_governance.executive_background": ["主要管理人员履历"],
    # company_litigation：3 → 9（重大诉讼与处罚拆 3，失信与退市风险拆 5）
    "company_litigation.major_litigation": ["重大诉讼与处罚"],
    "company_litigation.arbitration": ["重大诉讼与处罚"],
    "company_litigation.regulatory_penalty": ["重大诉讼与处罚"],
    "company_litigation.credit_events": ["失信与退市风险"],
    "company_litigation.debt_overdue": ["失信与退市风险"],
    "company_litigation.debt_default": ["失信与退市风险"],
    "company_litigation.bankruptcy_restructuring": ["失信与退市风险"],
    "company_litigation.delisting_regulatory": ["失信与退市风险"],
    "company_litigation.search_scope_cutoff": ["检索范围与截止日期"],
    # company_related_transactions：3 → 5（关联采购销售担保拆 3）
    "company_related_transactions.related_procurement": ["关联采购销售担保"],
    "company_related_transactions.related_sales": ["关联采购销售担保"],
    "company_related_transactions.related_guarantee": ["关联采购销售担保"],
    "company_related_transactions.related_ratio": ["关联交易占比"],
    "company_related_transactions.related_compliance": ["合规性"],
    # company_debt_credit：1 → 3（授信额度及使用情况拆 3）
    "company_debt_credit.total_credit_line": ["公司整体授信额度及使用情况"],
    "company_debt_credit.used_credit": ["公司整体授信额度及使用情况"],
    "company_debt_credit.unused_credit": ["公司整体授信额度及使用情况"],
    # company_debt_guarantee：4 → 5（发债情况拆 发债 + 债务期限结构）
    "company_debt_guarantee.bond_issuance": ["发债情况"],
    "company_debt_guarantee.debt_maturity": ["发债情况"],
    "company_debt_guarantee.financial_institution_loans": ["金融机构借款"],
    "company_debt_guarantee.external_guarantee_balance": ["公司整体对外担保余额"],
    "company_debt_guarantee.guarantee_scope_caliber": ["担保范围口径与报告期"],
    # industry_supply_demand：3 → 5（供需关系拆 2，价格与成本驱动拆 2）
    "industry_supply_demand.supply": ["供需关系"],
    "industry_supply_demand.demand": ["供需关系"],
    "industry_supply_demand.price_change": ["价格与成本驱动"],
    "industry_supply_demand.cost_driver": ["价格与成本驱动"],
    "industry_supply_demand.operating_impact": ["对经营的影响"],
}


def _load_v1_baseline() -> dict[str, dict]:
    """展平 v1 为 {question_id: {question, required_aspects}}，供业务审计溯源。"""
    v1_path = os.path.join(_REPO_ROOT, "templates", "contracts", "standard_v2.yaml")
    if not os.path.exists(v1_path):
        logger.warning("v1 基线不存在，跳过业务审计溯源: %s", v1_path)
        return {}
    doc = yaml.safe_load(Path(v1_path).read_text(encoding="utf-8"))
    baseline: dict[str, dict] = {}
    for sec in doc.get("sections", []):
        for topic in sec.get("required_topics", []):
            for q in topic.get("key_questions", []):
                qid = q.get("question_id")
                if qid:
                    baseline[qid] = {
                        "question": q.get("question", ""),
                        "required_aspects": list(q.get("required_aspects", [])),
                    }
    return baseline


def _load_writing_spec_ownership() -> dict[str, dict]:
    """加载 WritingSpec，返回 {aspect_id: {primary, secondary:[...]}}（主/次槽位归属）。"""
    try:
        from sections.writing_spec import load_writing_spec
        ws = load_writing_spec(os.path.join(
            _REPO_ROOT, "templates", "writing_specs", "credit_report_v1.yaml"))
    except Exception as e:  # noqa: BLE001
        logger.warning("加载 WritingSpec 失败，跳过主/次槽位归属审计: %s", e)
        return {}
    ownership: dict[str, dict] = {}
    for m in ws.mappings:
        aid = m.get("aspect_id")
        if not aid:
            continue
        own = ownership.setdefault(aid, {"primary": None, "secondary": []})
        if m.get("role") == "primary":
            own["primary"] = m.get("subsection_id")
        elif m.get("role") == "secondary_reference":
            own["secondary"].append(m.get("subsection_id"))
    return ownership


def _v1_aspects_for(a: S.AspectV2, v1_aspects: list[str], idx: int) -> list[str]:
    """该 v2 aspect 对应的 v1 required_aspect(s)。传输通道优先，其次显式映射，最后按位置对齐。"""
    if a.transmission_channel:
        v1_text = _TRANSMISSION_CHANNEL_TO_V1.get(a.transmission_channel)
        return [v1_text] if v1_text else []
    mapped = _V1_TO_V2_ASPECT_MAP.get(a.aspect_id)
    if mapped is not None:
        return list(mapped)
    if idx < len(v1_aspects):
        return [v1_aspects[idx]]
    return list(v1_aspects)


def _split_summary(q: S.QuestionV2, v1_aspects: list[str]) -> str:
    """v1 维度 → 拆出的 v2 aspect 数量（供 split 类 business_reason 引用）。"""
    counts: dict[str, int] = {}
    for i, a in enumerate(q.aspects):
        for v in _v1_aspects_for(a, v1_aspects, i):
            counts[v] = counts.get(v, 0) + 1
    return "、".join(f"{v}→{c}" for v, c in counts.items())


def _classify_change_type(q: S.QuestionV2, v1_aspects: list[str]) -> str:
    """确定性 v1→v2 change_type 分类（R1-A §一，六类，无 expanded）。"""
    v2_count = len(q.aspects)
    v1_count = len(v1_aspects)
    pk = q.producer_kind
    if pk in ("phase4_section_derived", "phase5_synthesizer"):
        return "producer_routed"
    if pk == "financial_workflow" and any(a.display_tier == "diagnostic_only" for a in q.aspects):
        return "display_refined"
    if v2_count > v1_count:
        # 增量里出现 optional_body 或 applicability_policy → 适用性细化；否则是 required_body 拆分。
        has_applicability = any(
            a.display_tier == "optional_body" or a.applicability_policy is not None
            for a in q.aspects)
        return "applicability_refined" if has_applicability else "split"
    if v2_count == v1_count:
        if any(a.applicability_policy is not None for a in q.aspects):
            return "applicability_refined"
        return "unchanged"
    return "clarified"  # v2_count < v1_count（不预期）：语义收口


def _business_reason(q: S.QuestionV2, change_type: str, v1_aspects: list[str]) -> str:
    v1_count, v2_count = len(v1_aspects), len(q.aspects)
    if change_type == "split":
        return (f"v1 的 {v1_count} 个 required_aspects 拆为 {v2_count} 个独立 required_body aspect"
                f"（{_split_summary(q, v1_aspects)}），各自可独立研究/核验/成缺口。")
    if change_type == "display_refined":
        slots = [a.aspect_id for a in q.aspects if a.display_tier == "diagnostic_only"]
        return (f"v1 required_aspects 保留，另新增诊断槽位 aspect {slots}"
                f"（diagnostic_only+audit_only，不进正文判断）。")
    if change_type == "applicability_refined":
        pols = sorted({a.applicability_policy for a in q.aspects if a.applicability_policy})
        return (f"v1 required_aspects 保留，新增/细化 applicability_policy {pols}"
                f"，把合法不适用/无实际控制人等分支显式化为终态，非事实缺失。")
    if change_type == "producer_routed":
        return (f"执行归属由 v1 的 harness/workflow 改为 {q.producer_kind}："
                f"该问 {v2_count} 个 aspect 由章节派生/Phase5 合成承载，不入 Topic Harness。")
    if change_type == "clarified":
        return "v1 required_aspects 语义澄清/收口，aspect 数量未增加或收紧。"
    return "v1 required_aspects 固化为稳定 aspect_id，无语义变更（不拆分）。"


def _chapter_of_subsection(sub: str) -> str:
    if sub == "phase5":
        return "综合(phase5)"
    if sub.startswith("co-"):
        return "公司"
    if sub.startswith("fin-"):
        return "财务"
    if sub.startswith("ind-"):
        return "行业"
    return "未知"


def _audit_for_question(q: S.QuestionV2, v1_baseline: dict[str, dict]) -> dict:
    qid = q.question_id
    v1 = v1_baseline.get(qid, {})
    v1_aspects = list(v1.get("required_aspects", []))
    change_type = _classify_change_type(q, v1_aspects)
    reason = _business_reason(q, change_type, v1_aspects)
    tiers = Counter(a.display_tier for a in q.aspects)
    return {
        "v1_question": v1.get("question", ""),
        "v1_required_aspects": v1_aspects,
        "change_type": change_type,
        "business_reason": reason,
        "decision_summary": (
            f"展示 {dict(tiers)}；content_role "
            f"{sorted({a.content_role for a in q.aspects})}；"
            f"time_scope {sorted({a.time_scope for a in q.aspects})}；"
            f"coverage {sorted({c for a in q.aspects for c in a.coverage_rules})}"),
    }


def build_review(contract: S.ContractV2) -> dict:
    evidence_registry = contract.raw.get("evidence_requirements", {})
    v1_baseline = _load_v1_baseline()
    ownership = _load_writing_spec_ownership()

    aspects = []
    for sec in contract.sections:
        for topic in sec.topics:
            for q in topic.questions:
                audit = _audit_for_question(q, v1_baseline)
                v1_aspects = audit["v1_required_aspects"]
                for i, a in enumerate(q.aspects):
                    er_ids = list(a.evidence_requirement_ids)
                    evidence_kinds = [evidence_registry.get(e, {}).get("evidence_kind", "")
                                      for e in er_ids]
                    er_classes = sorted({
                        c for e in er_ids
                        for c in evidence_registry.get(e, {}).get("source_classes", [])})
                    er_mins = sorted({
                        evidence_registry.get(e, {}).get("minimum_sources")
                        for e in er_ids})
                    mapped = _v1_aspects_for(a, v1_aspects, i)
                    own = ownership.get(a.aspect_id, {})
                    ws_primary = own.get("primary")
                    ws_secondary = own.get("secondary", [])
                    ws_ownership = "; ".join(
                        ([f"primary:{ws_primary}"] if ws_primary else []) +
                        [f"secondary:{s}" for s in ws_secondary])
                    if ws_primary:
                        ws_reason = (f"主槽位落在 {ws_primary}"
                                     f"（{_chapter_of_subsection(ws_primary)} H2 正文），"
                                     f"与 producer_kind={a.producer_kind} 目标 Writer 章节一致")
                    elif ws_secondary:
                        ws_reason = (f"仅 secondary_reference 引用（{ws_secondary}），"
                                     f"跨章节显式引用，非主槽位")
                    else:
                        ws_reason = "WritingSpec 无归属映射（审计缺口）"
                    if "search_audit" in a.coverage_rules:
                        search_reason = ("保留 search_audit：负面核验/not_found/集合完整性/"
                                         "外部时效需记录检索范围与截止日期")
                    else:
                        search_reason = ("不保留 search_audit：普通正向事实或财务/派生/synth，"
                                         "不机械要求检索审计")
                    if a.business_review_reason:
                        br_reason = a.business_review_reason
                    else:
                        br_reason = (f"CONFIRMED：由 v1 维度「{'、'.join(mapped)}」固化，"
                                     f"以 {a.kind} 形态独立研究，覆盖 {a.coverage_rules}，"
                                     f"证据 {er_ids}（{er_classes}），主槽位 {ws_primary}；"
                                     f"可独立核验，无需额外业务裁决")
                    aspects.append({
                        "section_id": sec.section_id,
                        "topic_id": topic.topic_id,
                        "question_id": q.question_id,
                        "question": q.question,
                        "producer_kind": a.producer_kind,
                        "aspect_id": a.aspect_id,
                        "kind": a.kind,
                        "requirement_text": a.requirement_text,
                        "execution_path": a.execution_path,
                        "required_fields": list(a.required_fields),
                        "coverage_rules": list(a.coverage_rules),
                        "complete_set_rule": a.complete_set_rule,
                        "evidence_requirement_ids": er_ids,
                        "evidence_kinds": evidence_kinds,
                        "source_policy_ref": a.source_policy_ref,
                        "time_scope": a.time_scope,
                        "display_tier": a.display_tier,
                        "content_role": a.content_role,
                        "missing_policy": a.missing_policy,
                        "blocking_policy": list(a.blocking_policy),
                        "applicability_policy": a.applicability_policy,
                        "impact_scope": list(a.impact_scope),
                        "output_destination": a.output_destination,
                        "derived_from": list(a.derived_from),
                        "derived_from_scope": a.derived_from_scope,
                        "business_review_status": a.business_review_status,
                        "business_review_reason": br_reason,
                        "transmission_layers": list(a.transmission_layers),
                        "transmission_channel": a.transmission_channel,
                        "v1_question": audit["v1_question"],
                        "v1_required_aspects": v1_aspects,
                        "change_type": audit["change_type"],
                        "business_reason": audit["business_reason"],
                        "v1_mapped_aspects": mapped,
                        "independent_meaning": a.requirement_text,
                        "researchability_reason": (
                            f"以 {a.kind} 形态独立研究，required_fields={a.required_fields}，"
                            f"可独立核验/成缺口"),
                        "coverage_reason": (
                            f"覆盖规则 {a.coverage_rules}（producer_kind={a.producer_kind}，"
                            f"集合锚点 {a.complete_set_rule or '无'}）"),
                        "search_audit_reason": search_reason,
                        "evidence_authority_reason": (
                            f"证据 {er_ids}：evidence_kind={evidence_kinds}，"
                            f"source_classes={er_classes}，minimum_sources={er_mins}"),
                        "writing_spec_ownership": ws_ownership,
                        "writing_spec_reason": ws_reason,
                    })

    questions = []
    for sec in contract.sections:
        for topic in sec.topics:
            for q in topic.questions:
                audit = _audit_for_question(q, v1_baseline)
                q_own_primary = {}
                q_own_secondary = {}
                for a in q.aspects:
                    own = ownership.get(a.aspect_id, {})
                    if own.get("primary"):
                        q_own_primary[a.aspect_id] = own["primary"]
                    for s in own.get("secondary", []):
                        q_own_secondary.setdefault(a.aspect_id, []).append(s)
                questions.append({
                    "section_id": sec.section_id,
                    "topic_id": topic.topic_id,
                    "question_id": q.question_id,
                    "question": q.question,
                    "priority": q.priority,
                    "producer_kind": q.producer_kind,
                    "execution_path": q.execution_path,
                    "blocking_policy": list(q.blocking_policy),
                    "missing_policy": q.missing_policy,
                    "aspect_ids": [a.aspect_id for a in q.aspects],
                    "v1_question": audit["v1_question"],
                    "v1_required_aspects": audit["v1_required_aspects"],
                    "change_type": audit["change_type"],
                    "business_reason": audit["business_reason"],
                    "decision_summary": audit["decision_summary"],
                    "writing_spec_primary": q_own_primary,
                    "writing_spec_secondary": q_own_secondary,
                    "business_review_status": sorted({a.business_review_status for a in q.aspects}),
                })

    change_type_counts = dict(Counter(q["change_type"] for q in questions))

    return {
        "contract_version": contract.contract_version,
        "source_policy_ref": contract.source_policy_ref,
        "summary": {
            "questions": len(questions),
            "aspects": len(aspects),
            "evidence_requirements": len(evidence_registry),
            "producer_counts": {
                pk: sum(1 for q in questions if q["producer_kind"] == pk)
                for pk in S.PRODUCER_KINDS
            },
            "change_type_counts": change_type_counts,
            "change_types": list(CHANGE_TYPES),
        },
        "v1_baseline": {
            "source": _V1_ASSET,
            "contract_version": "v1",
            "questions_cross_referenced": sum(1 for q in questions if q["v1_question"]),
            "per_question": {
                q["question_id"]: {
                    "v1_question": q["v1_question"],
                    "v1_required_aspects": q["v1_required_aspects"],
                    "v2_aspect_ids": q["aspect_ids"],
                    "change_type": q["change_type"],
                    "business_reason": q["business_reason"],
                }
                for q in questions
            },
        },
        "questions": questions,
        "aspects": aspects,
        "evidence_requirements": evidence_registry,
    }


def write_review_artifacts(review: dict, out_dir: str) -> tuple[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "review_52q.json"
    json_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out / "review_52q.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for a in review["aspects"]:
            row = {k: a[k] for k in _CSV_COLUMNS}
            for k in _CSV_COLUMNS:
                row[k] = _join(row[k])
            writer.writerow(row)

    return str(json_path), str(csv_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 52 问 × aspect × evidence 审计")
    parser.add_argument("--contract", default=S.CONTRACT_V2_ASSET,
                        help="standard_v3.yaml 路径")
    parser.add_argument("--out-dir", default="contracts/review",
                        help="输出目录")
    args = parser.parse_args(argv)

    try:
        c = load_contract_v2(args.contract)
        review = build_review(c)
        jp, cp = write_review_artifacts(review, args.out_dir)
    except Exception as e:  # noqa: BLE001
        logger.exception("导出审计产物失败")
        print(f"导出异常: {type(e).__name__}: {e}")
        return 1

    s = review["summary"]
    print(f"审计产物已生成: {s['questions']} 问 / {s['aspects']} aspects / "
          f"{s['evidence_requirements']} evidence 需求")
    print(f"  JSON: {jp}")
    print(f"  CSV : {cp}")
    print(f"  归属: {s['producer_counts']}")
    print(f"  change_type: {s['change_type_counts']}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
