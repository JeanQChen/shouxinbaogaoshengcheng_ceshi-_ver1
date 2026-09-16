"""Eval: 正式 SetEnumerationVerifier + 三策略确定性枚举（R2_IMPLEMENTATION_PLAN §4.11/§8/§11）。

用法: python -m evals.test_set_enumeration

覆盖：
- 三个 set_complete aspect 逐项枚举：subsidiaries（表名列）/ main_business（分维度）/ competitiveness（条目 head）。
- main_business ``dimension_type+name`` 身份：分行业/分产品同名成员不合并；散文来源/维度无法确定 → fail-closed。
- 普通连接词（「等」「此外」「同时」）不单独判 incomplete（仍 material_type_supported=True）。
- 结构信号 partial：「包括但不限于」明确非穷尽；「续表」无「合计/总计」未闭合；有「合计」→ 闭合。
- material_type_supported=False：不支持 aspect / 无 payload / payload bytes 不可用。
- 枚举结果绑定：payload_hash == compute_source_payload_hash、boundary_identity == compute_boundary_identity、
  verifier_version == SET_ENUMERATION_VERIFIER_VERSION。

全部离线：直接构造 ResolvedPayload（payload 信封），不调 Store/LLM/网络。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import source_object_inventory as SOI
from harness import topic_schema as TS
from harness.set_enumeration import (
    EnumerationBoundaryProof,
    FormalSetEnumerationVerifier,
    aggregate_per_version_enumeration,
    build_formal_set_enumeration_verifier,
    derive_enumeration_boundary_proof,
    group_materials_by_document_version,
    normalize_name,
    recover_flattened_table,
    recover_flattened_tables,
)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _locator() -> TS.EvidenceLocator:
    return TS.EvidenceLocator(document_id="doc1", document_version="v1",
                              section_path="s1", page=1)


def _env(etype: str, text: str = "", structured: dict | None = None) -> bytes:
    """构造 R2 payload 信封（envelope.content = {text, structured_payload, evidence_type}）。"""
    content = {"text": text, "structured_payload": structured, "evidence_type": etype}
    env = {
        "material_payload_version": "1", "object_type": "evidence_span",
        "authority_identity": "evidence:ev-1", "document_identity": {"document_id": "doc1"},
        "locator": {}, "evidence_id": "ev-1", "source_content_hash": _sha("src"),
        "content": content, "created_dependency_fingerprint": _sha("dep"),
    }
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


def _table_sp(headers: list[str], rows: list[list[str]], unit: str = "万元") -> dict:
    return {"unit": unit, "headers": headers, "cells": rows,
            "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": []}}


def _rp(payload_bytes: bytes, locator: TS.EvidenceLocator | None = None) -> TS.ResolvedPayload:
    return TS.ResolvedPayload(object_type="evidence_span", authority_identity="evidence:ev-1",
                              version="1", locator=locator or _locator(),
                              content_hash=_sha_bytes(payload_bytes), payload_bytes=payload_bytes)


class _Mat:
    """最小 material 依赖（枚举侧只消费 ``material_id`` 作为 assembly component 外键universe）。"""

    def __init__(self, material_id: str) -> None:
        self.material_id = material_id


def _persisted_assemblies(texts: list[str], comps: tuple[str, ...] = ("m-a1",)) -> tuple[dict, ...]:
    """把摊平表恢复结果投影为**已持久化** assembly（P1-B.5/B.6）。

    枚举侧绝不自行恢复：``recovered_ok`` 必须由真实持久化 assembly 见证；本投影与生产侧
    ``topic_materials._build_flattened_table_assembly`` 共用同一结构身份判别串。
    """
    out: list[dict] = []
    for t in recover_flattened_tables(list(texts)):
        if not t.get("headers") or not t.get("rows"):
            continue
        disc = SOI.flattened_table_structure_identity(t)
        out.append({
            "assembly_id": "asm-" + hashlib.sha256(disc.encode("utf-8")).hexdigest()[:32],
            "relation": SOI.FLATTENED_TABLE_RELATION,
            "component_material_ids": list(comps),
            "table_title": t.get("title") or "",
            "recovery_status": t.get("recovery_status", "ok"),
            "recovery_issue": t.get("recovery_issue"),
        })
    return tuple(out)


def _assessment(aspect_id: str, expected: tuple[str, ...] = ("m",),
                document_version: str = "v1", source_boundary: str = "s1",
                boundary_proof: EnumerationBoundaryProof | None = None) -> TS.SetCompletenessAssessment:
    return TS.SetCompletenessAssessment(
        aspect_id=aspect_id, rule_version=TS.SET_COMPLETENESS_RULE_VERSION,
        source_material_ids=("m-a1",), document_version=document_version,
        source_boundary=source_boundary, expected_member_ids=expected,
        observed_member_ids=expected, excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=("m-a1",), supporting_fact_ids=("f-a1",),
        scope_complete=True, assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=_sha("contract"),
        dependency_fingerprint=TS.compute_dependency_fingerprint(_sha("contract"), "v1", {}),
        boundary_proof=boundary_proof)


def _bp(**overrides) -> EnumerationBoundaryProof:
    """构造一个「边界闭合输入自洽」的 EnumerationBoundaryProof（默认无违规）。"""
    base = dict(
        aspect_id="company_subsidiaries.major_subsidiaries",
        seed_evidence_ids=("ev-1",),
        document_id="doc1",
        document_version="v1",
        evidence_set_version="v1",
        source_boundary_identity="s1",
        component_material_ids=("m-a1",),
        trace_fingerprint=_sha("trace"),
        direction_stop_reasons=(),
        unread_candidate_refs=(),
        unresolved_explicit_refs=(),
        unclosed_continuations=(),
        tool_errors=(),
        budget_exhausted=False,
        dependency_fingerprint=_sha("dep"),
    )
    base.update(overrides)
    return EnumerationBoundaryProof(**base)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    verifier = build_formal_set_enumeration_verifier()
    check(isinstance(verifier, FormalSetEnumerationVerifier),
          "factory 返回 FormalSetEnumerationVerifier")
    dep = _sha("dep")

    def enum(aspect_id, payloads, expected=("m",), doc_ver="v1", boundary="s1",
             boundary_proof=_bp(), assemblies=(), materials=()):
        return verifier.enumerate(
            _assessment(aspect_id, expected=expected, document_version=doc_ver,
                        source_boundary=boundary, boundary_proof=boundary_proof),
            tuple(materials), tuple(payloads), dep, tuple(assemblies))

    # ------------------------------------------------------------------
    # 1. major_subsidiaries：表名列枚举 + 规范化 + 去重
    # ------------------------------------------------------------------
    subs = [
        _rp(_env("table", text="主要控股参股公司")),
        _rp(_env("table_row", text="", structured=_table_sp(
            ["公司名称", "持股比例"], [["宁德时代新能源科技股份有限公司", "100%"]]))),
        _rp(_env("table_row", text="", structured=_table_sp(
            ["公司名称", "持股比例"], [["时代锂电有限公司", "51%"]]))),
    ]
    r = enum("company_subsidiaries.major_subsidiaries", subs,
             expected=("宁德时代新能源科技股份有限公司", "时代锂电有限公司"))
    check(r is not None and r.material_type_supported is True, "subsidiaries 表枚举 → supported")
    check(r is not None and r.enumerated_member_ids
          == ("宁德时代新能源科技股份有限公司", "时代锂电有限公司"),
          "subsidiaries 名称列逐行枚举成员")
    # 名称列判定：header 命中「公司名称」（含「公司」关键词）→ 第 0 列。
    dup = subs + [
        _rp(_env("table_row", text="", structured=_table_sp(
            ["公司名称", "持股比例"], [["宁德时代新能源科技股份有限公司", "100%"]]))),
    ]
    r2 = enum("company_subsidiaries.major_subsidiaries", dup,
              expected=("宁德时代新能源科技股份有限公司", "时代锂电有限公司"))
    check(r2 is not None and r2.enumerated_member_ids
          == ("宁德时代新能源科技股份有限公司", "时代锂电有限公司"),
          "subsidiaries 同 normalize 名去重（跨行重复仅一次）")

    # ------------------------------------------------------------------
    # 2. main_business：dimension_type+name 身份（分产品/分行业同名不合并）
    # ------------------------------------------------------------------
    biz = [
        _rp(_env("table", text="营业收入构成（分产品）")),
        _rp(_env("table_row", text="", structured=_table_sp(
            ["产品", "收入"], [["动力电池", "100"], ["储能", "50"]]))),
    ]
    r = enum("company_business_main.main_business", biz,
             expected=("product:动力电池", "product:储能"))
    check(r is not None and r.material_type_supported is True, "main_business 表枚举 → supported")
    check(r is not None and r.enumerated_member_ids == ("product:动力电池", "product:储能"),
          "main_business member_id = dimension_type:name（分产品）")
    # 分行业 + 分产品出现同名「汽车」→ 不同 member（不合并）。
    biz2 = [
        _rp(_env("table", text="营业收入构成（分产品）")),
        _rp(_env("table_row", text="", structured=_table_sp(["产品", "收入"], [["汽车", "100"]]))),
        _rp(_env("table", text="营业收入构成（分行业）")),
        _rp(_env("table_row", text="", structured=_table_sp(["行业", "收入"], [["汽车", "200"]]))),
    ]
    r = enum("company_business_main.main_business", biz2,
             expected=("product:汽车", "industry:汽车"))
    check(r is not None and r.enumerated_member_ids == ("product:汽车", "industry:汽车"),
          "main_business 分行业/分产品同名成员不合并（dimension_type 区分）")
    # 维度无法确定 → fail-closed。
    biz3 = [
        _rp(_env("table", text="收入构成明细")),
        _rp(_env("table_row", text="", structured=_table_sp(["项目", "金额"], [["汽车", "100"]]))),
    ]
    r = enum("company_business_main.main_business", biz3)
    check(r is not None and r.material_type_supported is False
          and "dimension_type" in (r.reason or ""),
          "main_business 维度无法确定 → material_type_supported=False")
    # 散文来源 → business_segment 维度（「主营业务分析」小节描述业务板块，不冒名归入分产品/分行业）。
    r = enum("company_business_main.main_business",
             [_rp(_env("paragraph", text="公司主营动力电池业务。"))])
    check(r is not None and r.material_type_supported is True
          and r.enumerated_member_ids == ("business_segment:公司主营动力电池业务",),
          "main_business 散文来源 → business_segment 维度")
    # 表行缺维度上下文（row 先于 table）→ fail-closed。
    r = enum("company_business_main.main_business",
             [_rp(_env("table_row", text="", structured=_table_sp(["产品", "收入"], [["汽车", "1"]])))])
    check(r is not None and r.material_type_supported is False,
          "main_business 表行缺维度上下文 → fail-closed")

    # ------------------------------------------------------------------
    # 3. core_competitiveness：条目 head 枚举（编号/（一）（二））
    # ------------------------------------------------------------------
    comp = [
        _rp(_env("paragraph", text="（一）技术研发优势。公司拥有多项核心技术。")),
        _rp(_env("paragraph", text="（二）规模优势。产能规模行业领先。")),
    ]
    r = enum("company_competitiveness.core_competitiveness", comp,
             expected=("技术研发优势", "规模优势"))
    check(r is not None and r.material_type_supported is True,
          "competitiveness 条目枚举 → supported")
    check(r is not None and r.enumerated_member_ids == ("技术研发优势", "规模优势"),
          "competitiveness member_id = normalize(条目 head)（去编号前缀）")

    # ------------------------------------------------------------------
    # 4. 普通连接词（等/此外/同时）不单独判 incomplete
    # ------------------------------------------------------------------
    r = enum("company_competitiveness.core_competitiveness",
             [_rp(_env("paragraph", text="（一）公司拥有技术优势、规模优势、成本优势等多项核心竞争力。"))],
             expected=("公司拥有技术优势、规模优势、成本优势等多项核心竞争力",))
    check(r is not None and r.material_type_supported is True,
          "「等」普通连接词不单独判 incomplete")
    r = enum("company_competitiveness.core_competitiveness",
             [_rp(_env("paragraph", text="（一）规模优势。此外公司在供应链具备协同能力。同时布局海外。"))],
             expected=("规模优势",))
    check(r is not None and r.material_type_supported is True,
          "「此外」「同时」普通连接词不单独判 incomplete（head 仍取首句）")

    # ------------------------------------------------------------------
    # 5. 结构信号 partial
    # ------------------------------------------------------------------
    r = enum("company_subsidiaries.major_subsidiaries",
             [_rp(_env("paragraph", text="主要子公司包括但不限于以下主体。"))])
    check(r is not None and r.material_type_supported is False
          and "非穷尽" in (r.reason or ""),
          "「包括但不限于」明确非穷尽 → material_type_supported=False")
    r = enum("company_business_main.main_business",
             [_rp(_env("table", text="营业收入构成（分产品）续表")),
              _rp(_env("table_row", text="", structured=_table_sp(["产品", "收入"], [["储能", "50"]])))],
             expected=("product:储能",))
    check(r is not None and r.material_type_supported is False
          and "截断" in (r.reason or ""),
          "「续表」无「合计」→ 截断未闭合 → fail-closed")
    # 有「合计」→ 闭合，不再判截断。
    r = enum("company_business_main.main_business",
             [_rp(_env("table", text="营业收入构成（分产品）")),
              _rp(_env("table_row", text="", structured=_table_sp(
                  ["产品", "收入"], [["储能", "50"]]))),
              _rp(_env("table_row", text="合计 150", structured=_table_sp(
                  ["产品", "收入"], [["合计", "150"]])))],
             expected=("product:储能", "product:合计"))
    check(r is not None and r.material_type_supported is True,
          "「合计」闭合行 → 截断信号被闭合（仍可枚举）")

    # ------------------------------------------------------------------
    # 6. material_type_supported=False：不支持 aspect / 空 payload / bytes 不可用
    # ------------------------------------------------------------------
    r = enum("company_finance.some_other_aspect", [_rp(_env("paragraph", text="x"))])
    check(r is not None and r.material_type_supported is False,
          "不支持 aspect → material_type_supported=False")
    r = enum("company_subsidiaries.major_subsidiaries", [])
    check(r is not None and r.material_type_supported is False
          and "无 source payload" in (r.reason or ""),
          "空 payload → material_type_supported=False")
    no_bytes = TS.ResolvedPayload(object_type="evidence_span", authority_identity="evidence:ev-1",
                                  version="1", locator=_locator(),
                                  content_hash=_sha("x"), payload_bytes=None)
    r = enum("company_subsidiaries.major_subsidiaries", [no_bytes])
    check(r is not None and r.material_type_supported is False
          and "不可用" in (r.reason or ""),
          "payload bytes 不可用 → material_type_supported=False")

    # ------------------------------------------------------------------
    # 7. 枚举结果绑定：payload_hash / boundary_identity / verifier_version
    # ------------------------------------------------------------------
    subs_ok = [
        _rp(_env("table_row", text="", structured=_table_sp(
            ["公司名称", "持股比例"], [["时代锂电有限公司", "51%"]])),
            locator=TS.EvidenceLocator(document_id="doc1", document_version="v9",
                                       section_path="b7", page=1)),
    ]
    r = enum("company_subsidiaries.major_subsidiaries", subs_ok,
             expected=("时代锂电有限公司",), doc_ver="v9", boundary="b7")
    check(r is not None and r.material_type_supported is True
          and r.payload_hash == TS.compute_source_payload_hash(tuple(subs_ok)),
          "payload_hash == compute_source_payload_hash(resolved_payloads)")
    check(r is not None and r.boundary_identity == TS.compute_boundary_identity("v9", "b7"),
          "boundary_identity == compute_boundary_identity(document_version, source_boundary)")
    check(r is not None and r.verifier_version == TS.SET_ENUMERATION_VERIFIER_VERSION,
          "verifier_version == SET_ENUMERATION_VERIFIER_VERSION")

    # ------------------------------------------------------------------
    # 8. 规范化确定性（normalize_name）
    # ------------------------------------------------------------------
    check(normalize_name("  宁德时代新能源科技股份有限公司  ") == "宁德时代新能源科技股份有限公司",
          "normalize_name 折叠空白 + 去首尾")
    check(normalize_name("时代锂电有限公司；") == "时代锂电有限公司",
          "normalize_name 去首尾标点")

    # ------------------------------------------------------------------
    # 9. §六/item 6：boundary_proof 闭合违规 → material_type_supported=False（反例#15-18）
    # ------------------------------------------------------------------
    subs_ok = [
        _rp(_env("table_row", text="", structured=_table_sp(
            ["公司名称", "持股比例"], [["时代锂电有限公司", "51%"]]))),
    ]

    # 自洽边界输入（无违规）→ 仍可枚举（证明 boundary_proof 机制不破坏合法路径）。
    r_ok = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",), boundary_proof=_bp()),
        (), tuple(subs_ok), dep)
    check(r_ok is not None and r_ok.material_type_supported is True,
          "边界闭合输入自洽（无违规）→ material_type_supported=True")

    # 反例#19：set_complete 缺 boundary_proof → 拒绝（不再保留自报兼容路径）。
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",)),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "boundary_proof" in (r.reason or ""),
          "反例#19：set_complete 缺 boundary_proof → material_type_supported=False")

    # 反例#15：边界内仍有未读候选 → 拒绝。
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",),
                    boundary_proof=_bp(unread_candidate_refs=("ev-unread",))),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "未读候选" in (r.reason or ""),
          "反例#15：unread 候选仍在边界 → material_type_supported=False")

    # 反例#16：预算耗尽提前停止 → 拒绝。
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",),
                    boundary_proof=_bp(budget_exhausted=True)),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "预算耗尽" in (r.reason or ""),
          "反例#16：预算耗尽 → material_type_supported=False")

    # 反例#17：扩读存在工具错误 → 拒绝。
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",),
                    boundary_proof=_bp(tool_errors=("tool error:boom",))),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "工具错误" in (r.reason or ""),
          "反例#17：工具错误 → material_type_supported=False")

    # 反例#18：dangling 显式引用 / 未闭合 table continuation → 拒绝。
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",),
                    boundary_proof=_bp(unresolved_explicit_refs=("ev-ref",))),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "dangling" in (r.reason or ""),
          "反例#18a：dangling explicit reference → material_type_supported=False")
    r = verifier.enumerate(
        _assessment("company_subsidiaries.major_subsidiaries",
                    expected=("时代锂电有限公司",),
                    boundary_proof=_bp(unclosed_continuations=("ev-cont",))),
        (), tuple(subs_ok), dep)
    check(r is not None and r.material_type_supported is False
          and "未闭合" in (r.reason or ""),
          "反例#18b：未闭合 table continuation → material_type_supported=False")

    # ------------------------------------------------------------------
    # 10. §六/§三：EnumerationBoundaryProof + SetCompletenessAssessment schema 往返
    #     （boundary_proof 进入正式 typed schema 与 Pack content identity）
    # ------------------------------------------------------------------
    bp = _bp(direction_stop_reasons=(("table_continuation", "stopped"),))
    d = bp.to_dict()
    bp_back = TS.EnumerationBoundaryProof.from_dict(d)
    check(bp_back == bp, "EnumerationBoundaryProof to_dict ↔ from_dict 往返相等")
    check(d["trace_fingerprint"] == bp.trace_fingerprint
          and d["dependency_fingerprint"] == bp.dependency_fingerprint,
          "EnumerationBoundaryProof 指纹字段进入序列化")

    assess_bp = _assessment("company_subsidiaries.major_subsidiaries",
                            expected=("时代锂电有限公司",), boundary_proof=bp)
    d2 = assess_bp.to_dict()
    back2 = TS.SetCompletenessAssessment.from_dict(d2)
    check(back2 == assess_bp, "SetCompletenessAssessment（含 boundary_proof）往返相等")
    check(back2.boundary_proof is not None and back2.boundary_proof == bp,
          "boundary_proof 经 SetCompletenessAssessment.to_dict/from_dict 保留")

    # 反例：非法 trace_fingerprint → SchemaValidationError。
    try:
        TS.EnumerationBoundaryProof.from_dict({**d, "trace_fingerprint": "not-sha"})
        check(False, "非法 trace_fingerprint 应被拒绝")
    except TS.SchemaValidationError:
        check(True, "非法 trace_fingerprint → SchemaValidationError")

    # ------------------------------------------------------------------
    # 11. §三：空证明缺口——violation() 拒绝空字段/空扩读指纹；正常自洽输入仍通过
    # ------------------------------------------------------------------
    check(_bp(document_id="").violation() is not None, "§三：空 document_id → violation")
    check(_bp(document_version="").violation() is not None, "§三：空 document_version → violation")
    check(_bp(evidence_set_version="").violation() is not None,
          "§三：空 evidence_set_version → violation")
    check(_bp(source_boundary_identity="").violation() is not None,
          "§三：空 source_boundary_identity → violation")
    check(_bp(seed_evidence_ids=()).violation() is not None, "§三：空 seed_evidence_ids → violation")
    check(_bp(component_material_ids=()).violation() is not None,
          "§三：空 component_material_ids → violation")
    check(_bp(trace_fingerprint=_sha("")).violation() is not None,
          "§三：空扩读指纹（sha256('')）→ violation")
    # 空扩读（expansions == ()）经 derive 产生的证明也必须被 violation() 拒绝。
    empty_bp = derive_enumeration_boundary_proof(
        "company_subsidiaries.major_subsidiaries", (),
        document_id="doc1", document_version="v1", evidence_set_version="v1",
        source_boundary_identity="s1", component_material_ids=("m-a1",),
        dependency_fingerprint=_sha("dep"))
    check(empty_bp.trace_fingerprint == _sha(""), "§三：空扩读 trace_fingerprint == sha256('')")
    check(empty_bp.violation() is not None,
          "§三：空扩读（无真实 expansion 输入）→ violation（不得视为闭合）")
    check(_bp().violation() is None, "§三：自洽边界输入 → violation() is None（合法路径不破坏）")

    # ------------------------------------------------------------------
    # 12. §五 修复三：PDF 摊平段落 → 确定性摊平表恢复 → 分产品枚举（不伪造、不冒充）
    # ------------------------------------------------------------------
    flat = [
        _rp(_env("paragraph", text="营业收入构成（分产品）")),
        _rp(_env("paragraph", text="单位：万元")),
        _rp(_env("paragraph", text="产品  2025年  2024年  2023年")),
        _rp(_env("paragraph", text="动力电池系统  31,650,636.9  25,304,133.7  20,000,000.0")),
        _rp(_env("paragraph", text="储能系统  5,000.0  4,000.0  3,000.0")),
        _rp(_env("paragraph", text="合计  36,650,636.9  29,304,133.7  23,000,000.0")),
    ]
    flat_texts = [json.loads(p.payload_bytes.decode("utf-8"))["content"]["text"]
                  for p in flat]
    flat_asm = _persisted_assemblies(flat_texts)
    flat_mats = (_Mat("m-a1"),)
    r = enum("company_business_main.main_business", flat,
             expected=("product:动力电池系统", "product:储能系统"),
             assemblies=flat_asm, materials=flat_mats)
    check(r is not None and r.material_type_supported is True,
          "修复三：摊平段落 → 确定性恢复 → supported（含真实持久化 assembly）")
    check(r is not None and r.enumerated_member_ids
          == ("product:动力电池系统", "product:储能系统"),
          "修复三：摊平表恢复后按 dimension_type:name 枚举（分产品）")
    # 反例（P1-B.5/B.6）：同一份摊平材料**无持久化 assembly** → 枚举侧绝不自报 supported。
    r_noasm = enum("company_business_main.main_business", flat,
                   expected=("product:动力电池系统", "product:储能系统"),
                   materials=flat_mats)
    check(r_noasm is not None and r_noasm.material_type_supported is False,
          "反例：无持久化 assembly → material_type_supported=False（清单绝不自行恢复）")
    check(r_noasm is not None and "target_not_obtained" in (r_noasm.reason or ""),
          "反例：无持久化 assembly → 失败原因含 target_not_obtained（逐项未闭合）")
    # 反例：assembly 存在但 component 外键不在材料 universe 内 → 悬空外键 → fail-closed。
    r_dangling = enum("company_business_main.main_business", flat,
                      expected=("product:动力电池系统", "product:储能系统"),
                      assemblies=flat_asm, materials=(_Mat("m-not-in-universe"),))
    check(r_dangling is not None and r_dangling.material_type_supported is False,
          "反例：assembly component 外键不存在 → material_type_supported=False")

    # ------------------------------------------------------------------
    # 13. §五 修复三：recover_flattened_table 结构恢复（title/unit/header/rows/total）
    # ------------------------------------------------------------------
    tbl, why = recover_flattened_table(tuple(flat))
    check(tbl is not None and why is None, "修复三：recover_flattened_table 返回表结构（非 None）")
    check(tbl is not None and tbl["title"] == "营业收入构成（分产品）" and tbl["unit"] == "万元",
          "修复三：恢复 title + unit")
    check(tbl is not None and tbl["headers"] == ["产品", "2025年", "2024年", "2023年"],
          "修复三：恢复 header 列（全非数字多列 → 表头）")
    check(tbl is not None and len(tbl["rows"]) == 2 and tbl["rows"][0][0] == "动力电池系统",
          "修复三：恢复数据行（≥1 数字列 → row）")
    check(tbl is not None and tbl["total_row"] is not None,
          "修复三：合计行 → total_row（闭合）")

    # ------------------------------------------------------------------
    # 14. §五 修复三：仅表头无数据行 → 摊平表恢复失败（缺数据行，诚实 fail-closed）
    # ------------------------------------------------------------------
    r = enum("company_business_main.main_business",
             [_rp(_env("paragraph", text="产品  2025年  2024年  2023年"))])
    check(r is not None and r.material_type_supported is False
          and "数据行" in (r.reason or ""),
          "修复三：仅表头无数据行 → 缺数据行（诚实 fail-closed，不伪造成员）")

    # ------------------------------------------------------------------
    # 15. §五 修复三：有数据行缺表头 → 摊平表恢复失败（缺表头，诚实 fail-closed）
    # ------------------------------------------------------------------
    r = enum("company_business_main.main_business",
             [_rp(_env("paragraph", text="动力电池系统  31,650  25,304  20,000"))])
    check(r is not None and r.material_type_supported is False
          and "表头" in (r.reason or ""),
          "修复三：有数据行缺表头 → 缺表头（诚实 fail-closed，不冒名归入 business_segment）")

    # ------------------------------------------------------------------
    # 16. §五 修复三：混入结构化 table/table_row → 不摊平恢复（走原生枚举）
    # ------------------------------------------------------------------
    tbl, why = recover_flattened_table((
        _rp(_env("table", text="营业收入构成（分产品）")),
        _rp(_env("paragraph", text="产品  2025年  2024年  2023年")),
    ))
    check(tbl is None and why == "存在结构化 table/table_row，走原生枚举，不摊平恢复",
          "修复三：混入结构化 table/table_row → 不摊平恢复（走原生枚举）")

    # ------------------------------------------------------------------
    # 17. §五 修复三：真实形态——单 block 摊平表（title/unit/header/rows/total/analysis 同块）
    # ------------------------------------------------------------------
    single_block_text = (
        "（二）主营业务情况\n"
        "1、主营业务收入分析\n\n"
        "表 5-10发行人主营业务收入构成表\n\n"
        "单位：万元，%\n"
        "项目  2025年  2024年  2023年\n"
        "金额  占比  金额  占比  金额  占比\n\n"
        "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n\n"
        "储能电池系统  6,243,982.0  14.7  5,729,046.0  15.8  5,990,052.2  14.9\n\n"
        "电池材料及回收  2,186,093.6  5.2  2,869,993.5  7.9  3,360,228.4  8.4\n\n"
        "电池矿产资源  597,809.6  1.4  549,300.3  1.5  773,415.1  1.9\n\n"
        "其他业务  1,691,661.2  4.0  1,748,781.8  4.8  1,442,717.1  3.6\n\n"
        "合计  42,370,183.3100.0  36,201,255.3 100.0  40,091,704.5 100.0\n\n"
        "发行人主要营业收入来自于动力电池系统、储能电池系统、电池材料及回收和电池\n"
        "矿产资源四大业务板块。2023-2025年，发行人营业收入分别为 40,091,704.5万元、\n\n"
        "36,201,255.3万元和 42,370,183.3万元。\n\n"
        "2、主营业务成本分析\n\n"
        "表 5-11发行人主营业务成本构成表\n\n"
        "单位：万元，%\n"
    )
    tbl, why = recover_flattened_table((_rp(_env("paragraph", text=single_block_text)),))
    check(tbl is not None and why is None,
          "修复三真实形态：单 block 摊平表恢复（非缺表头）")
    check(tbl is not None and "表 5-10" in tbl["title"],
          "修复三真实形态：恢复收入表题（非章节标题）")
    check(tbl is not None and tbl["unit"] == "万元，%", "修复三真实形态：恢复 unit")
    check(tbl is not None and tbl["headers"][0] == "项目", "修复三真实形态：恢复表头首列")
    check(tbl is not None and len(tbl["rows"]) == 5,
          "修复三真实形态：恢复 5 个数据行（表后正文不误读为数据行）")
    check(tbl is not None and tbl["rows"][0][0] == "动力电池系统",
          "修复三真实形态：恢复数据行首列")
    check(tbl is not None and tbl["total_row"] is not None,
          "修复三真实形态：恢复合计 total_row")
    tables = recover_flattened_tables([single_block_text])
    check(len(tables) >= 1 and any("表 5-10" in t["title"] for t in tables),
          "修复三真实形态：收入表被独立识别")

    # ------------------------------------------------------------------
    # 18. P1-2：粘连「金额+百分比」拆分（无空格/单空格）+ 表头合并 + 列数一致校验
    # ------------------------------------------------------------------
    glued_text = (
        "表 5-10 发行人主营业务收入构成表\n单位：万元，%\n"
        "项目  2025年  2024年\n金额  占比  金额  占比\n"
        "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9\n"
        "储能电池系统  6,243,982.0 14.7  5,729,046.0 15.8\n"
        "合计  42,370,183.3100.0  36,201,255.3 100.0\n"
    )
    gt, why = recover_flattened_table((_rp(_env("paragraph", text=glued_text)),))
    check(gt is not None and why is None,
          "P1-2：粘连表可确定性恢复（非缺表头/缺数据行）")
    check(gt is not None and gt["headers"] == ["项目", "金额", "占比", "金额", "占比"],
          "P1-2：分组+叶子表头合并为 [名称列]+叶子列")
    check(gt is not None and all(len(r) == 5 for r in gt["rows"]),
          "P1-2：数据行经粘连拆分后列数一致（5 列）")
    check(gt is not None and gt["total_row"]
          == ["合计", "42,370,183.3", "100.0", "36,201,255.3", "100.0"],
          "P1-2：合计行粘连「金额+百分比」拆分（无空格 + 单空格两种）")
    check(gt is not None and gt["recovery_status"] == "ok",
          "P1-2：列数一致且无残留粘连 → recovery_status=ok")

    # ------------------------------------------------------------------
    # 19. P1-2：孤立页码行剔除（绝不当数据行/表头）
    # ------------------------------------------------------------------
    page_text = (
        "表 5-11 发行人主营业务成本构成表\n单位：万元\n"
        "项目  金额\n"
        "原材料成本  8,000\n"
        "49\n"
        "人工成本  3,000\n"
        "合计  11,000\n"
    )
    pt, why = recover_flattened_table((_rp(_env("paragraph", text=page_text)),))
    check(pt is not None and why is None,
          "P1-2：含页码行的表可恢复（页码行被剔除）")
    check(pt is not None and pt["rows"] == [["原材料成本", "8,000"], ["人工成本", "3,000"]],
          "P1-2：孤立页码 '49' 不进入数据行")

    # ------------------------------------------------------------------
    # 20. P1-2：表头/数据行列数不一致 → recovery failed（诚实 fail-closed）
    # ------------------------------------------------------------------
    mismatch_text = (
        "表 5-12 发行人毛利率构成表\n单位：万元\n"
        "项目  金额\n"
        "动力电池系统  31,650  74.7\n"
        "合计  42,370\n"
    )
    mt, why = recover_flattened_table((_rp(_env("paragraph", text=mismatch_text)),))
    check(mt is None and why is not None and "不一致" in why,
          "P1-2：表头 2 列 vs 数据行 3 列 → recovery failed（不冒名恢复）")

    # ------------------------------------------------------------------
    # 21. P1-2：无法拆分的「金额+百分比」粘连 → recovery partial（诚实标记）
    # ------------------------------------------------------------------
    partial_text = (
        "表 5-13 发行人研发投入构成表\n单位：万元，%\n"
        "项目  金额  占比\n"
        "研发投入  31,650.0  74.7\n"
        "合计  42,370.3105.7\n"
    )
    ppt, why = recover_flattened_table((_rp(_env("paragraph", text=partial_text)),))
    check(ppt is not None and why is None,
          "P1-2：无法拆分粘连仍返回表结构（不整体丢弃）")
    check(ppt is not None and ppt["recovery_status"] == "partial",
          "P1-2：无法拆分的粘连 → recovery_status=partial（诚实标记）")
    check(ppt is not None and "粘连" in (ppt["recovery_issue"] or ""),
          "P1-2：partial 原因记录「粘连」")

    # ------------------------------------------------------------------
    # 22. P1-4：按单一 document_version 分组（多版本共存绝不合并）
    # ------------------------------------------------------------------
    from types import SimpleNamespace

    def _mat(mid: str, doc_id: str, doc_ver: str) -> SimpleNamespace:
        return SimpleNamespace(
            material_id=mid,
            authority_assessment=SimpleNamespace(
                document_id=doc_id, document_version=doc_ver),
            locator=SimpleNamespace(section_path="s1"))

    m_v1a = _mat("m1", "doc-a", "v1")
    m_v1b = _mat("m2", "doc-a", "v1")
    m_v2 = _mat("m3", "doc-a", "v2")
    groups = group_materials_by_document_version([m_v1a, m_v1b, m_v2])
    check([k for k, _ in groups] == [("doc-a", "v1"), ("doc-a", "v2")],
          "P1-4：按 (document_id, document_version) 分组（确定性顺序，同版本合并）")
    check(len(groups[0][1]) == 2 and len(groups[1][1]) == 1,
          "P1-4：同版本材料归一组，不同版本分列")

    # ------------------------------------------------------------------
    # 23. P1-4：多版本聚合 → material_type_supported=False + merged=False（不伪造单一完整集合）
    # ------------------------------------------------------------------
    ver = TS.SET_ENUMERATION_VERIFIER_VERSION
    single = aggregate_per_version_enumeration([
        {"document_id": "doc-a", "document_version": "v1", "source_boundary": "s1",
         "result": {"material_type_supported": True, "reason": "ok",
                    "enumerated_member_ids": ["a"], "payload_hash": _sha("p"),
                    "boundary_identity": _sha("b"), "verifier_version": ver}},
    ], ver)
    check(single["material_type_supported"] is True and single["merged"] is False
          and len(single["per_version"]) == 1,
          "P1-4：单版本 → aggregate == 该版本 result（merged=False）")
    multi = aggregate_per_version_enumeration([
        {"document_id": "doc-a", "document_version": "v1", "source_boundary": "s1",
         "result": {"material_type_supported": True, "reason": "ok",
                    "enumerated_member_ids": ["a"], "payload_hash": _sha("p"),
                    "boundary_identity": _sha("b"), "verifier_version": ver}},
        {"document_id": "doc-a", "document_version": "v2", "source_boundary": "s1",
         "result": {"material_type_supported": True, "reason": "ok",
                    "enumerated_member_ids": ["b"], "payload_hash": _sha("p2"),
                    "boundary_identity": _sha("b2"), "verifier_version": ver}},
    ], ver)
    check(multi["material_type_supported"] is False and multi["merged"] is False,
          "P1-4：多版本 → material_type_supported=False（不合并为单一完整集合）")
    check("多 document_version" in (multi["reason"] or ""),
          "P1-4：多版本 reason 显式「多 document_version 共存」")
    check(len(multi["per_version"]) == 2
          and {p["document_version"] for p in multi["per_version"]} == {"v1", "v2"},
          "P1-4：per_version 保留每版本独立结果（历史视图共存）")
    empty = aggregate_per_version_enumeration([], ver)
    check(empty["material_type_supported"] is False and empty["per_version"] == [],
          "P1-4：空 per_version → material_type_supported=False（无 source material）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
