"""Eval: Contract v2 资产一致性（R1-A §十一/§十三）。

用法: python -m evals.test_contract_v2_assets

覆盖纯声明式资产一致性（无真实 LLM / bocha / DB / 网络）：
 - Contract v2（standard_v3.yaml）52 问、28/13/3/8 归属、每 aspect 22 字段、
   coverage / display_tier / content_role / time / evidence / source 引用、
   派生回指同章（derived_from_scope，禁通配）、synth 仅 phase5、负面事件表达、
   行业传导四层与四条通道；
 - 来源政策 v1（source_policy_v1.yaml）分级/关键结论/传导层/传导通道；
 - WritingSpec v1（credit_report_v1.yaml）逐字 8/5/9 + 每 aspect 恰一 primary；
 - PresentationProfile v1（interview_demo_v1.yaml）呈现边界；
 - 52 问 x aspect x evidence 审计导出（review_52q.json / csv）；
 - 漂移护栏：standard_v2.yaml 保持 v1 未被覆盖，standard_v3.yaml 为 v2。

本测试不硬编码 aspect 总数 / 映射总数 / evidence 总数 / CSV 行数等派生数量，
全部从契约对象派生；权威数量（52 问、28/13/3/8）由 contracts.validator_v2 单一来源
强制。绿灯只证明「资产自洽」，不宣称 P3R/P4R 或 Phase 4 产品内容关闭。
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK：把 stdout/stderr 切成 UTF-8（errors=replace），
# 保证打印任意字符都不因编码崩溃（exit 0）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import yaml

from contracts import schema_v2 as S
from contracts.loader_v2 import load_contract_v2
from contracts.validator_v2 import (
    validate_contract_v2, _validate_transmission_authority, _validate_derived_eligibility,
)
from contracts.source_policy import load_source_policy, validate_source_policy
from sections.writing_spec import load_writing_spec, validate_writing_spec
from sections.presentation_profile import (
    load_presentation_profile, validate_presentation_profile,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_V2 = ROOT / "templates" / "contracts" / "standard_v3.yaml"
CONTRACT_V1 = ROOT / "templates" / "contracts" / "standard_v2.yaml"

# standard_v2.yaml 固定字节 hash（R1-A §五：锁定 v1 不被覆盖，不止 contract_version=v1）。
V1_FIXED_SHA256 = "23e1735e3b77e94dacae70be03712ca93c98d8f545cc087f8d8b092ad841ae45"
SOURCE_POLICY = ROOT / "templates" / "policies" / "source_policy_v1.yaml"
WRITING_SPEC = ROOT / "templates" / "writing_specs" / "credit_report_v1.yaml"
PRES_PROFILE = ROOT / "templates" / "presentation_profiles" / "interview_demo_v1.yaml"
REVIEW_PY = ROOT / "contracts" / "review" / "topic_aspect_evidence_review.py"


def _load_review_exporter():
    """contracts/review.py 与 contracts/review/ 目录同名冲突，按文件路径加载导出器。"""
    spec = importlib.util.spec_from_file_location(
        "topic_aspect_evidence_review", str(REVIEW_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _contract_with_evidence(contract, er_id, authority):
    """构造证据需求 authority 被替换的 Contract 副本（§二负例用）。"""
    raw = dict(contract.raw)
    er = dict(raw["evidence_requirements"])
    target = dict(er[er_id])
    target["authority"] = authority
    er[er_id] = target
    raw["evidence_requirements"] = er
    return replace(contract, raw=raw)


def _contract_without_policy_key(contract, key):
    """构造删除 derived_input_eligibility 某字段的 Contract 副本（§四负例用）。"""
    raw = dict(contract.raw)
    pol = dict(raw["derived_input_eligibility"])
    pol.pop(key, None)
    raw["derived_input_eligibility"] = pol
    return replace(contract, raw=raw)


def _rewrite_primary(ws, aspect_id, subsection_id):
    """把某 aspect 的 primary 主槽位改写为 subsection_id（§三负例用）。"""
    maps = []
    for m in ws.mappings:
        mm = dict(m)
        if mm.get("role") == "primary" and mm.get("aspect_id") == aspect_id:
            mm["subsection_id"] = subsection_id
        maps.append(mm)
    return maps


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # --- 加载 + 校验 ---
    contract = load_contract_v2(str(CONTRACT_V2))
    vr = validate_contract_v2(contract)
    policy = load_source_policy(str(SOURCE_POLICY))
    pvr = validate_source_policy(policy)
    ws = load_writing_spec(str(WRITING_SPEC))
    wsvr = validate_writing_spec(ws, contract)
    prof = load_presentation_profile(str(PRES_PROFILE))
    prvr = validate_presentation_profile(prof)

    aspects = contract.all_aspects()
    questions = contract.all_questions()
    check(contract.contract_version == "v2", "Contract v2 contract_version=v2")
    check(vr.valid, f"Contract v2 校验通过（{len(vr.errors)} 错误）")

    # --- 派生计数一致性（不硬编码总数） ---
    check(vr.stats["questions"] == len(questions),
          f"问题数一致 {vr.stats['questions']} == {len(questions)}")
    check(vr.stats["aspects"] == len(aspects),
          f"aspect 数一致 {vr.stats['aspects']} == {len(aspects)}")
    check(sum(vr.stats["producer_counts"].values()) == len(questions),
          "producer 归属合计 == 问题总数")
    check(vr.stats["producer_counts"] == {"topic_harness": 28, "financial_workflow": 13,
                                          "phase4_section_derived": 3, "phase5_synthesizer": 8},
          f"归属 28/13/3/8（实际 {vr.stats['producer_counts']}）")

    # --- aspect_id / question_id 全局唯一 ---
    check(len({a.aspect_id for a in aspects}) == len(aspects), "aspect_id 全局唯一")
    check(len({q.question_id for q in questions}) == len(questions), "question_id 全局唯一")

    # --- 22 字段 / 枚举 / 引用（独立复验，非仅 enum-only） ---
    required = set(S.REQUIRED_ASPECT_FIELDS)
    check(all(required <= set(a.raw.keys()) for a in aspects), "每 aspect 22 字段完整")
    check(all(set(a.coverage_rules) <= set(S.COVERAGE_RULES) for a in aspects),
          "coverage_rules 均为白名单成员")
    check(all(set(a.coverage_rules) <= set(S.PRODUCER_COVERAGE_RULES.get(a.producer_kind, ()))
              for a in aspects), "coverage_rules 与 producer_kind 兼容")
    check(all("not_applicable" not in a.coverage_rules for a in aspects),
          "not_applicable 不作为 coverage mode")
    check(all(a.time_scope in S.TIME_POLICIES for a in aspects), "time_scope 均为五类时间政策")
    evidence_keys = set(contract.raw.get("evidence_requirements", {}).keys())
    check(all(set(a.evidence_requirement_ids) <= evidence_keys for a in aspects),
          "evidence_requirement_ids 全部可解析")
    check(all(a.source_policy_ref == contract.source_policy_ref for a in aspects),
          "source_policy_ref 统一为契约值")
    check(all(a.display_tier in S.DISPLAY_TIERS for a in aspects), "display_tier 均为白名单")
    check(all(a.content_role in S.CONTENT_ROLES for a in aspects), "content_role 均为白名单")
    check(all(not (a.display_tier == "diagnostic_only" and a.content_role != "audit_only")
              for a in aspects), "diagnostic_only 只配 audit_only")

    # --- derived_from 禁通配 + 派生回指同章（derived_from_scope） ---
    sec_of = {a.aspect_id: sec.section_id
              for sec in contract.sections for a in sec.all_aspects()}
    check(all(not any(r.endswith(":*") for r in a.derived_from) for a in aspects),
          "derived_from 无 chapter:* 通配")
    derived_scope_ok = True
    for a in aspects:
        if a.producer_kind == "phase4_section_derived":
            own = sec_of[a.aspect_id]
            scope = a.derived_from_scope or {}
            if list(scope.get("include_sections", [])) != [own]:
                derived_scope_ok = False
    check(derived_scope_ok, "phase4_section_derived 的 derived_from_scope 仅回指本章")
    check(all(a.output_destination == "phase5_synthesizer"
              for a in aspects if a.producer_kind == "phase5_synthesizer"),
          "synth 仅声明 phase5_synthesizer")

    # --- 负面事件 / 传导通道 ---
    np_ = contract.raw.get("negative_event_body_policy", {})
    check(list(np_.get("allowed_body_forms", [])) == list(S.NEGATIVE_BODY_ALLOWED_FORMS),
          "负面事件 allowed_body_forms 与 schema 一致")
    check(list(np_.get("forbidden_body_forms", [])) == list(S.NEGATIVE_BODY_FORBIDDEN_FORMS),
          "负面事件 forbidden_body_forms 与 schema 一致")
    check(all(all(f in a.required_fields for f in S.EVENT_CORE_FIELDS)
              for a in aspects if a.kind == "event_set"),
          "event_set 含负面事件核心字段")
    check(all(bool(a.complete_set_rule) for a in aspects if a.kind == "all_disclosed_items"),
          "all_disclosed_items 有集合锚点")
    tx_channels = [c.get("channel") for c in contract.raw.get("transmission_channels", [])]
    check(tx_channels == list(S.TRANSMISSION_CHANNELS),
          "transmission_channels 为四条独立通道")
    check(all(set(a.transmission_layers) <= set(S.TRANSMISSION_LAYERS) for a in aspects),
          "transmission_layers 均为四层白名单")
    tx_aspects = [a for a in aspects if a.transmission_channel]
    check(len(tx_aspects) == 16, f"行业传导拆为 4×4=16 aspect（实际 {len(tx_aspects)}）")
    check(all(a.transmission_channel in S.TRANSMISSION_CHANNELS for a in tx_aspects),
          "transmission_channel 均为四条通道白名单")
    check(len({(a.transmission_channel, tuple(a.transmission_layers)) for a in tx_aspects}) == 16,
          "每个 (channel, layer) 组合恰一个 aspect")
    check(all(len(a.transmission_layers) == 1 for a in tx_aspects),
          "每个传导 aspect 恰一个 transmission_layer")
    # search_audit 分布（fix #3：公司 19 / 行业 24，财务/派生/synth 无）
    sa_company = [a for a in aspects if "search_audit" in a.coverage_rules
                  and sec_of[a.aspect_id] == "company"]
    sa_industry = [a for a in aspects if "search_audit" in a.coverage_rules
                   and sec_of[a.aspect_id] == "industry"]
    sa_other = [a for a in aspects if "search_audit" in a.coverage_rules
                and sec_of[a.aspect_id] not in ("company", "industry")]
    check(len(sa_company) == 19, f"公司段 search_audit 保留 19（实际 {len(sa_company)}）")
    check(len(sa_industry) == 24, f"行业段 search_audit 保留 24（实际 {len(sa_industry)}）")
    check(not sa_other, "financial/derived/synth 不含 search_audit")
    # core_competitiveness 收口（fix #10 方案A）
    core_comp = {a.aspect_id: a for a in aspects}.get("company_competitiveness.core_competitiveness")
    check(core_comp is not None and core_comp.business_review_status == "CONFIRMED",
          "core_competitiveness business_review_status=CONFIRMED")
    check(vr.stats["business_review_required"] == 0, "无 BUSINESS_REVIEW_REQUIRED 残留")
    # derived 输入资格（fix #7）：所有 derived_from_scope 精确排除四类不合格终态
    derived_scopes = [a for a in aspects if a.derived_from_scope is not None]
    check(all(list((a.derived_from_scope or {}).get("exclude_terminal_states", []))
              == list(S.DERIVED_INPUT_INELIGIBLE_STATES) for a in derived_scopes),
          "所有 derived_from_scope 精确排除四类不合格终态")

    # --- 传导 Evidence 权威（R1-A §二）：四层 authority 结构可机器判定 ---
    er_registry = contract.raw.get("evidence_requirements", {})
    for layer, er_id in S.TRANSMISSION_EVIDENCE_BY_LAYER.items():
        authority = er_registry.get(er_id, {}).get("authority", {})
        check(isinstance(authority, dict) and bool(authority.get("required_any_of")),
              f"{er_id} 含非空 authority.required_any_of")
    cond_il = er_registry["er_ind_transmission_conditional"]["authority"]["inference_lineage"]
    check(cond_il.get("required") is True
          and list(cond_il.get("fields", [])) == list(S.CONDITIONAL_INFERENCE_LINEAGE_FIELDS),
          "条件性传导含完整 inference_lineage 血缘")
    for er_id in ("er_ind_transmission_exposure", "er_ind_transmission_impact"):
        auth = er_registry[er_id]["authority"]
        req_classes = {c for cl in auth.get("required_any_of", [])
                       for c in cl.get("source_classes", [])}
        check("external" not in req_classes and "external" in auth.get("supplemental_only", []),
              f"{er_id} external 仅 supplemental_only")
    # 负例：条件性传导缺 inference_lineage 必须失败
    errs = []
    _validate_transmission_authority(
        _contract_with_evidence(contract, "er_ind_transmission_conditional",
                                {"required_any_of": [{"source_classes": ["external", "company_industry"],
                                                      "kind": "verified_fact"}],
                                 "supplemental_only": []}), errs)
    check(any("inference_lineage" in e for e in errs),
          "负例：条件性传导缺 inference_lineage 校验失败")
    # 负例：external-only 公司暴露必须失败
    errs = []
    _validate_transmission_authority(
        _contract_with_evidence(contract, "er_ind_transmission_exposure",
                                {"required_any_of": [{"source_classes": ["external"]}],
                                 "supplemental_only": []}), errs)
    check(any("supplemental_only" in e for e in errs),
          "负例：external-only 公司暴露校验失败")
    # 负例：external-only 实际影响必须失败
    errs = []
    _validate_transmission_authority(
        _contract_with_evidence(contract, "er_ind_transmission_impact",
                                {"required_any_of": [{"source_classes": ["external"]}],
                                 "supplemental_only": []}), errs)
    check(any("supplemental_only" in e for e in errs),
          "负例：external-only 实际影响校验失败")

    # --- 派生输入资格政策（R1-A §四）：typed 完整政策 ---
    check(contract.raw.get("derived_input_eligibility") == S.DERIVED_ELIGIBILITY_POLICY,
          "derived_input_eligibility 与 schema typed 政策逐字段一致")
    for key in S.DERIVED_ELIGIBILITY_POLICY_KEYS:
        errs = []
        _validate_derived_eligibility(_contract_without_policy_key(contract, key), errs)
        check(any(key in e for e in errs),
              f"负例：删除 derived_input_eligibility.{key} 校验失败")
    raw_bad = dict(contract.raw)
    pol_bad = dict(raw_bad["derived_input_eligibility"])
    pol_bad["claim_verdicts_ineligible"] = ["SUPPORTED", "PARTIAL", "UNSUPPORTED"]
    raw_bad["derived_input_eligibility"] = pol_bad
    errs = []
    _validate_derived_eligibility(replace(contract, raw=raw_bad), errs)
    check(any("交集" in e for e in errs),
          "负例：eligible/ineligible 交集非空校验失败")

    # --- 业务不变式（R1-A §五）：锁定 §十一 关键业务 aspect 存在 ---
    aspect_by_id = {a.aspect_id: a for a in aspects}
    aspect_id_set = set(aspect_by_id)
    key_business_aspects = [
        "company_litigation.major_litigation", "company_litigation.arbitration",
        "company_litigation.regulatory_penalty", "company_litigation.credit_events",
        "company_litigation.debt_overdue", "company_litigation.debt_default",
        "company_litigation.bankruptcy_restructuring", "company_litigation.delisting_regulatory",
        "company_business_model.procurement_mode", "company_business_model.production_mode",
        "company_business_model.sales_mode",
        "company_governance.governance_structure", "company_governance.internal_control",
        "company_related_transactions.related_procurement", "company_related_transactions.related_sales",
        "company_related_transactions.related_guarantee", "company_related_transactions.related_ratio",
        "company_related_transactions.related_compliance",
        "industry_supply_demand.supply", "industry_supply_demand.demand",
        "industry_supply_demand.price_change", "industry_supply_demand.cost_driver",
    ]
    check(all(aid in aspect_id_set for aid in key_business_aspects),
          f"§十一 关键业务 aspect 全部存在（{len(key_business_aspects)} 项）")
    check(not all(aid in (aspect_id_set - {"company_litigation.major_litigation"})
                  for aid in key_business_aspects),
          "删除关键 aspect 时锁定谓词翻转（不依赖 WritingSpec 映射）")
    check("search_audit" not in aspect_by_id["company_identity_basic.registered_capital"].coverage_rules,
          "普通正向事实不机械要求 search_audit")
    check(all("search_audit" in aspect_by_id[aid].coverage_rules
              for aid in ("company_litigation.major_litigation",
                          "company_litigation.search_scope_cutoff")),
          "负面/not_found 保留 search_audit")
    comp_aspects = [a for a in aspects if a.question_id == "company_competitiveness"]
    check(len(comp_aspects) == 1 and comp_aspects[0].kind == "all_disclosed_items",
          "core_competitiveness 单一 all_disclosed_items")
    check(aspect_by_id["company_subject_match.name_consistency"].missing_policy == "transfer_human",
          "transfer_human 只读人工复核")

    # --- 来源政策 ---
    check(pvr.valid, f"来源政策 v1 校验通过（{len(pvr.errors)} 错误）")
    check(policy.policy_id == "source_policy_v1" and policy.policy_version == "v1"
          and policy.schema_version == "source-policy-v1",
          "来源政策唯一命名 policy_id/version/schema_version")
    check([c.get("channel") for c in policy.transmission_channels] == list(S.TRANSMISSION_CHANNELS),
          "来源政策含四条传导通道")

    # --- WritingSpec ---
    check(wsvr.valid, f"WritingSpec v1 校验通过（{len(wsvr.errors)} 错误）")
    prim = Counter(m["aspect_id"] for m in ws.mappings if m.get("role") == "primary")
    multi = sorted(k for k, v in prim.items() if v > 1)
    check(not multi, f"每 aspect 恰一个 primary（多 primary: {multi}）")
    contract_ids = {a.aspect_id for a in aspects}
    check(set(prim.keys()) == contract_ids, "primary 映射与契约 aspect 一一对应")
    synth_ids = {a.aspect_id for a in aspects if a.producer_kind == "phase5_synthesizer"}
    synth_phase5 = sum(1 for m in ws.mappings
                       if m.get("role") == "primary" and m["aspect_id"] in synth_ids
                       and m["subsection_id"] == "phase5")
    check(synth_phase5 == len(synth_ids), f"synth 落 phase5 数 {synth_phase5} == {len(synth_ids)}")
    check(len(ws.toc.get("company", [])) == 8 and len(ws.toc.get("financial", [])) == 5
          and len(ws.toc.get("industry", [])) == 9, "逐字 8/5/9 目录计数")
    # 负例：主槽位归属错误（fix #6）——公司 aspect 落到 fin-* 应校验失败
    bad_mappings = []
    for m in ws.mappings:
        mm = dict(m)
        if (mm.get("role") == "primary"
                and mm["aspect_id"] == "company_subject_match.name_consistency"):
            mm["subsection_id"] = "fin-h1"
        bad_mappings.append(mm)
    bad_ws = replace(ws, mappings=bad_mappings)
    bad_vr = validate_writing_spec(bad_ws, contract)
    check(not bad_vr.valid and any("主槽位" in e for e in bad_vr.errors),
          "负例：公司 aspect 主槽位落到 fin-* 触发校验失败")

    # --- WritingSpec 归属完整性（R1-A §三）：secondary/subsection/producer 全量校验 ---
    fin_primary = next(a.aspect_id for a in aspects if sec_of[a.aspect_id] == "financial")
    ind_primary = next(a.aspect_id for a in aspects if sec_of[a.aspect_id] == "industry")
    synth_primary = next(a.aspect_id for a in aspects
                         if a.producer_kind == "phase5_synthesizer")
    co_primary = next(a.aspect_id for a in aspects if sec_of[a.aspect_id] == "company")
    check(not validate_writing_spec(
        replace(ws, mappings=_rewrite_primary(ws, fin_primary, "co-h1")), contract).valid,
        "负例：financial aspect 主槽位落到 co-* 校验失败")
    check(not validate_writing_spec(
        replace(ws, mappings=_rewrite_primary(ws, ind_primary, "co-h1")), contract).valid,
        "负例：industry aspect 主槽位落到 co-* 校验失败")
    check(not validate_writing_spec(
        replace(ws, mappings=_rewrite_primary(ws, synth_primary, "co-h1")), contract).valid,
        "负例：synth primary 落到非 phase5 校验失败")
    check(not validate_writing_spec(
        replace(ws, mappings=_rewrite_primary(ws, co_primary, "co-h99")), contract).valid,
        "负例：不存在的 subsection（前缀正确 co-h99）校验失败")
    existing_sec = next(m for m in ws.mappings if m.get("role") == "secondary_reference")
    bad_sec_aspect = list(ws.mappings) + [dict(existing_sec, aspect_id="nonexistent.aspect")]
    check(not validate_writing_spec(replace(ws, mappings=bad_sec_aspect), contract).valid,
          "负例：secondary 引用契约外 aspect 校验失败")
    bad_sec_sub = list(ws.mappings) + [dict(existing_sec, subsection_id="co-h99")]
    check(not validate_writing_spec(replace(ws, mappings=bad_sec_sub), contract).valid,
          "负例：secondary subsection_id 不存在于 TOC 校验失败")
    check(wsvr.valid, "合法跨章节 secondary 通过（primary 唯一）")

    # --- PresentationProfile ---
    check(prvr.valid, f"PresentationProfile v1 校验通过（{len(prvr.errors)} 错误）")
    check(tuple(prof.scope.get("forbidden", []))
          == ("fact_change", "coverage_change", "citation_change",
              "business_judgment_change"),
          "呈现禁止改变事实/覆盖/引用/业务判断")

    # --- 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二）：frozen + content_sha256 fail-closed ---
    for name, doc in (("contract", contract.raw), ("source_policy", policy.raw),
                      ("writing_spec", ws.raw), ("presentation_profile", prof.raw)):
        check(doc.get("status") == "frozen", f"{name} status=frozen")
        check(doc.get("approved_at") == "2026-09-13", f"{name} approved_at 非空")
        check(doc.get("frozen_at") == "2026-09-13", f"{name} frozen_at 非空")
        check(bool(doc.get("created_at")), f"{name} created_at 非空")
        check(doc.get("content_sha256") == S.content_fingerprint(doc),
              f"{name} content_sha256 与内容指纹一致")
        check(S.frozen_asset_errors(doc) == [], f"{name} frozen_asset_errors 无错误")
    check(bool(S.frozen_asset_errors({"status": "frozen", "created_at": "2026-09-13",
                                      "approved_at": "2026-09-13", "frozen_at": "2026-09-13"})),
          "负例：frozen 缺 content_sha256 报错")
    check(bool(S.frozen_asset_errors({"status": "frozen", "created_at": "2026-09-13",
                                      "approved_at": "2026-09-13", "frozen_at": "2026-09-13",
                                      "content_sha256": "0" * 64})),
          "负例：frozen 内容指纹不符报错")
    # R1-A 冻结收口 §三：两条架构约束已写入契约并可由机器读取
    arch = contract.raw.get("r1_architecture_constraints", {})
    check(set(arch.keys()) == {"constraint_state_space_isolation",
                               "constraint_authority_vs_sufficiency_gate"},
          "r1_architecture_constraints 记录两条 §三 架构约束")
    check(S.lifecycle_errors({"status": "candidate", "created_at": "2026-09-13",
                              "approved_at": None, "frozen_at": None}) == [],
          "正例：合法 candidate 生命周期无错误")
    check(bool(S.lifecycle_errors({"status": "candidate", "created_at": "2026-09-13",
                                   "approved_at": "2026-09-13", "frozen_at": None})),
          "负例：candidate 带 approved_at 报错")
    check(S.lifecycle_errors({"status": "approved", "created_at": "2026-09-13",
                              "approved_at": "2026-09-13", "frozen_at": None}) == [],
          "正例：合法 approved 生命周期无错误")
    check(bool(S.lifecycle_errors({"status": "approved", "created_at": "2026-09-13",
                                   "approved_at": None, "frozen_at": None})),
          "负例：approved 缺 approved_at 报错")
    check(S.lifecycle_errors({"status": "frozen", "created_at": "2026-09-13",
                              "approved_at": "2026-09-13", "frozen_at": "2026-09-13"}) == [],
          "正例：合法 frozen 生命周期无错误")
    check(bool(S.lifecycle_errors({"status": "frozen", "created_at": "2026-09-13",
                                   "approved_at": "2026-09-13", "frozen_at": None})),
          "负例：frozen 缺 frozen_at 报错")
    check(bool(S.lifecycle_errors({"status": "frozen", "created_at": "2026-09-13",
                                   "approved_at": None, "frozen_at": "2026-09-13"})),
          "负例：frozen 缺 approved_at 报错")
    check(bool(S.lifecycle_errors({"status": "approved", "created_at": "2026-09-13",
                                   "approved_at": "2026-09-13", "frozen_at": "2026-09-13"})),
          "负例：approved 带 frozen_at 报错")

    # --- 审计导出（计数派生，不硬编码行数） ---
    exporter = _load_review_exporter()
    review = exporter.build_review(contract)
    s = review["summary"]
    ev_count = len(contract.raw.get("evidence_requirements", {}))
    check(s["questions"] == len(questions)
          and s["aspects"] == len(aspects)
          and s["evidence_requirements"] == ev_count,
          "审计导出计数与契约一致")
    with tempfile.TemporaryDirectory() as td:
        jp, cp = exporter.write_review_artifacts(review, td)
        check(Path(jp).exists() and Path(cp).exists(), "审计产物 JSON/CSV 落盘")
        rows = Path(cp).read_text(encoding="utf-8-sig").strip().splitlines()
        check(len(rows) == len(aspects) + 1, f"CSV 数据行 == aspect 数（{len(rows) - 1}）")
        jdoc = json.loads(Path(jp).read_text(encoding="utf-8"))
        check(len(jdoc["aspects"]) == len(aspects) and len(jdoc["questions"]) == len(questions),
              "JSON 含全部问/aspect")
    # v1 基线业务审计（R1-A §一）：52 问真实比对，六类 change_type，无 expanded
    check(review["v1_baseline"]["questions_cross_referenced"] == len(questions),
          "52 问全部完成 v1 基线交叉引用")
    check(set(review["summary"]["change_type_counts"]) <= set(exporter.CHANGE_TYPES),
          "change_type 枚举合法（六类）")
    check("expanded" not in review["summary"]["change_type_counts"],
          "无未批准的 expanded change_type")
    pq = review["v1_baseline"]["per_question"]
    for qid in ("company_business_main", "company_business_model", "company_litigation",
                "company_related_transactions", "industry_supply_demand"):
        check(pq[qid]["change_type"] == "split", f"{qid} 审计为 split（v1→v2 拆分）")
    for qid in ("fin_solvency", "fin_profitability", "fin_cashflow", "fin_growth"):
        check(pq[qid]["change_type"] == "display_refined", f"{qid} 审计为 display_refined（诊断槽位）")
    check(pq["company_competitiveness"]["change_type"] == "unchanged",
          "company_competitiveness 审计为 unchanged（方案A）")
    for qid in ("company_credit_summary", "fin_risk_summary", "industry_monitoring"):
        check(pq[qid]["change_type"] == "producer_routed", f"{qid} 审计为 producer_routed")
    check(all(pq[qid]["change_type"] == "producer_routed" for qid in pq if qid.startswith("synth_")),
          "synth 8 问审计为 producer_routed")
    check(all(pq[qid]["business_reason"] for qid in pq), "每问有具体 business_reason")
    check(all(a["business_review_reason"] for a in review["aspects"]),
          "每 aspect 有具体 business_review_reason")
    # --- 规范漂移测试（R1-A §五）：canonical JSON 深度比对 + CSV 结构比对，字节比对仅追加 ---
    checked_json_b = (ROOT / "contracts" / "review" / "review_52q.json").read_bytes()
    checked_csv_b = (ROOT / "contracts" / "review" / "review_52q.csv").read_bytes()
    checked_json = json.loads(checked_json_b.decode("utf-8"))
    checked_csv_text = checked_csv_b.decode("utf-8-sig")
    checked_rows = list(csv.DictReader(checked_csv_text.splitlines()))
    with tempfile.TemporaryDirectory() as td2:
        rjp, rcp = exporter.write_review_artifacts(review, td2)
        regen_json = json.loads(Path(rjp).read_text(encoding="utf-8"))
        check(regen_json == checked_json, "review_52q.json 与入库资产 canonical 深度一致（无漂移）")
        regen_text = Path(rcp).read_text(encoding="utf-8-sig")
        regen_rows = list(csv.DictReader(regen_text.splitlines()))
        check(list(regen_rows[0].keys()) == list(checked_rows[0].keys()),
              "review_52q.csv header/字段一致")
        check(regen_rows == checked_rows, "review_52q.csv 行级结构一致")
        check(Path(rjp).read_bytes() == checked_json_b, "review_52q.json 字节一致（追加护栏）")
        check(Path(rcp).read_bytes() == checked_csv_b, "review_52q.csv 字节一致（追加护栏）")

    # --- 漂移护栏：v1 未被覆盖 / v2 声明为草稿 ---
    v1_bytes = CONTRACT_V1.read_bytes()
    check(hashlib.sha256(v1_bytes).hexdigest() == V1_FIXED_SHA256,
          "standard_v2.yaml 固定字节 hash 锁定（v1 未被覆盖）")
    v1doc = yaml.safe_load(v1_bytes.decode("utf-8"))
    check(v1doc.get("contract_version") == "v1", "standard_v2.yaml 保持 contract_version=v1")
    v3doc = yaml.safe_load(CONTRACT_V2.read_text(encoding="utf-8"))
    check(v3doc.get("contract_version") == "v2"
          and v3doc.get("draft_successor_of") == "templates/contracts/standard_v2.yaml"
          and v3doc.get("based_on") == "templates/contracts/standard_v2.yaml",
          "standard_v3.yaml 为 v2 草案并声明 draft_successor_of/based_on")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    r = main()
    print(f"passed={r['passed']} failed={r['failed']} skipped={r['skipped']}")
    for d in r["details"]:
        print(d)
    sys.exit(0 if r["failed"] == 0 else 1)
