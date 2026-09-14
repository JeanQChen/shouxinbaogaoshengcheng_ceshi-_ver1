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

from harness import topic_schema as TS
from harness.set_enumeration import (
    FormalSetEnumerationVerifier,
    build_formal_set_enumeration_verifier,
    normalize_name,
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


def _rp(payload_bytes: bytes) -> TS.ResolvedPayload:
    return TS.ResolvedPayload(object_type="evidence_span", authority_identity="evidence:ev-1",
                              version="1", locator=_locator(),
                              content_hash=_sha_bytes(payload_bytes), payload_bytes=payload_bytes)


def _assessment(aspect_id: str, expected: tuple[str, ...] = ("m",),
                document_version: str = "v1", source_boundary: str = "s1") -> TS.SetCompletenessAssessment:
    return TS.SetCompletenessAssessment(
        aspect_id=aspect_id, rule_version=TS.SET_COMPLETENESS_RULE_VERSION,
        source_material_ids=("m-a1",), document_version=document_version,
        source_boundary=source_boundary, expected_member_ids=expected,
        observed_member_ids=expected, excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=("m-a1",), supporting_fact_ids=("f-a1",),
        scope_complete=True, assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=_sha("contract"),
        dependency_fingerprint=TS.compute_dependency_fingerprint(_sha("contract"), "v1", {}))


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

    def enum(aspect_id, payloads, expected=("m",), doc_ver="v1", boundary="s1"):
        return verifier.enumerate(
            _assessment(aspect_id, expected=expected, document_version=doc_ver,
                        source_boundary=boundary),
            (), tuple(payloads), dep)

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
    # 散文来源 → fail-closed（不冒名归入 main_business）。
    r = enum("company_business_main.main_business",
             [_rp(_env("paragraph", text="公司主营动力电池业务。"))])
    check(r is not None and r.material_type_supported is False,
          "main_business 散文来源无法确定维度 → fail-closed")
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
             [_rp(_env("paragraph", text="公司拥有技术优势、规模优势、成本优势等多项核心竞争力。"))],
             expected=("公司拥有技术优势、规模优势、成本优势等多项核心竞争力",))
    check(r is not None and r.material_type_supported is True,
          "「等」普通连接词不单独判 incomplete")
    r = enum("company_competitiveness.core_competitiveness",
             [_rp(_env("paragraph", text="规模优势。此外公司在供应链具备协同能力。同时布局海外。"))],
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
            ["公司名称", "持股比例"], [["时代锂电有限公司", "51%"]]))),
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

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
