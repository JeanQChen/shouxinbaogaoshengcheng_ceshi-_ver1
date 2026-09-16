"""Eval: 修复 A 主题边界策略（版本化 / 确定性 / 从冻结契约派生 / 公司无关）。

用法: python -m evals.test_topic_boundary

覆盖：
- ``classify_heading_topic``：在建工程/公司治理/公司行业地位 → out_of_topic（从其他 topic 标题派生）；
  主营业务/经营模式/产业链 → in_topic（从自身 topic 标题/requirement/question/H2 派生）；
  整体情况 → ambiguous（不据此停）；
  **公司专属词不硬编码**：储能电池系统板块/销售情况/安全生产/高级管理人员 → ambiguous。
- ``TopicBoundaryPolicy`` 派生：internal/external 关键词来自 Contract v2 + WritingSpec，
  不含任何手工公司词典；policy_version 确定性。
- ``topic_boundary_coverage``：115 个 topic_harness aspects 全覆盖审计（executable / unavailable）。
- fail-closed：未知 aspect → available=False + boundary_policy_unavailable。
- ``domain_segments``：连接词 及/和/与 仅两侧 ≥2 CJK 才切（「主要参与者」「涉及主体」不被拆散）。
- ``find_topic_boundary`` / ``block_start_topic_class``：mixed block 定位 + 块首分类。
- 集成：``expand`` 遇主题外标题块 → outside_boundary_sentinel + 片段投影。

全部离线：零 LLM/网络；派生仅读冻结 Contract v2 + WritingSpec。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.context_expansion import (
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    ContextExpansionRequest,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import (
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.topic_boundary import (
    BOUNDARY_POLICY_UNAVAILABLE,
    POLICY_SELF_CONSISTENT,
    TOPIC_AMBIGUOUS,
    TOPIC_IN_TOPIC,
    TOPIC_OUT_OF_TOPIC,
    block_start_topic_class,
    classify_heading_topic,
    domain_segments,
    find_topic_boundary,
    topic_boundary_coverage,
    topic_boundary_policy,
)
from harness.topic_materials import build_material_result
from tools.registry import ToolRegistry

# 复用 test_context_expansion 的临时 DB 构造（同 project 内共享 fixture 助手）。
from evals.test_context_expansion import (
    _DOC,
    _DOCV,
    _SETV,
    _COMPANY,
    _eid_of,
    _make_db,
    _seed,
)

_MAIN_BUSINESS = "company_business_main.main_business"

_DEP = hashlib.sha256(b"dep").hexdigest()


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

    # ------------------------------------------------------------------
    # 1. classify_heading_topic：主题外/主题内/ambiguous（全部从冻结契约派生）
    # ------------------------------------------------------------------
    check(classify_heading_topic("九、在建工程项目", _MAIN_BUSINESS) == TOPIC_OUT_OF_TOPIC,
          "在建工程 → out_of_topic（从 company_competitiveness 标题派生）")
    check(classify_heading_topic("（六）公司治理情况", _MAIN_BUSINESS) == TOPIC_OUT_OF_TOPIC,
          "公司治理 → out_of_topic（从 company_governance 标题派生）")
    check(classify_heading_topic("（三）公司行业地位", _MAIN_BUSINESS) == TOPIC_OUT_OF_TOPIC,
          "公司行业地位 → out_of_topic（从 industry_position 标题派生）")
    check(classify_heading_topic("1、主营业务收入分析", _MAIN_BUSINESS) == TOPIC_IN_TOPIC,
          "主营业务收入分析 → in_topic（自身标题「主营业务」）")
    check(classify_heading_topic("（二）经营模式与产业链", _MAIN_BUSINESS) == TOPIC_IN_TOPIC,
          "经营模式与产业链 → in_topic（自身标题）")
    check(classify_heading_topic("（1）整体情况", _MAIN_BUSINESS) == TOPIC_AMBIGUOUS,
          "整体情况 → ambiguous（不据此停，也不据此判定主题内）")

    # 公司专属词不硬编码（反例：证明策略从契约派生，非三样本手工词典）。
    check(classify_heading_topic("2、储能电池系统板块", _MAIN_BUSINESS) == TOPIC_AMBIGUOUS,
          "储能电池系统板块 → ambiguous（公司专属词，不硬编码）")
    check(classify_heading_topic("（2）销售情况", _MAIN_BUSINESS) == TOPIC_AMBIGUOUS,
          "销售情况 → ambiguous（公司专属词，不硬编码）")
    check(classify_heading_topic("（四）安全生产情况", _MAIN_BUSINESS) == TOPIC_AMBIGUOUS,
          "安全生产情况 → ambiguous（契约无此词，不硬编码）")
    check(classify_heading_topic("高级管理人员履历", _MAIN_BUSINESS) == TOPIC_AMBIGUOUS,
          "高级管理人员 → ambiguous（关键词级不硬编码；越界由章节边界裁决）")

    # ------------------------------------------------------------------
    # 2. TopicBoundaryPolicy 派生：零手工词典 + 确定性
    # ------------------------------------------------------------------
    pol = topic_boundary_policy(_MAIN_BUSINESS)
    check(pol.available, "main_business 边界策略可执行")
    for kw in ("主营业务", "经营模式", "产业链", "客户", "供应商集中度", "分板块", "分部"):
        check(kw in pol.internal_keywords, f"internal 含派生词「{kw}」")
    for kw in ("在建工程", "公司治理", "关联交易", "对外担保", "核心竞争力", "发展计划"):
        check(kw in pol.external_keywords, f"external 含派生词「{kw}」")
    for kw in ("储能", "电池", "销售", "安全生产", "高级管理人员"):
        check(kw not in pol.internal_keywords and kw not in pol.external_keywords,
              f"不含公司专属词「{kw}」")
    pol2 = topic_boundary_policy(_MAIN_BUSINESS)
    check(pol.policy_version == pol2.policy_version
          and pol.internal_keywords == pol2.internal_keywords
          and pol.external_keywords == pol2.external_keywords,
          "策略确定性：重复派生 policy_version 与关键词一致")

    # ------------------------------------------------------------------
    # 3. 覆盖审计：115 个 topic_harness aspects 全覆盖（P1-A.1 诚实四态）
    # ------------------------------------------------------------------
    cov = topic_boundary_coverage()
    check(cov["total"] == 115, f"覆盖审计共 115 个 aspect（实际 {cov['total']}）")
    check(cov["boundary_policy_unavailable"] == 0
          and cov["policy_generated"] == 0
          and cov["policy_self_consistent"] == cov["total"]
          and cov["boundary_semantics_verified"] == 0,
          "115 个 aspect 四态审计：全部 policy_self_consistent、**0** 个"
          " boundary_semantics_verified（P1-A.1：策略内部自洽 ≠ 真实边界已验证；"
          f"unavailable={cov['boundary_policy_unavailable']} "
          f"generated={cov['policy_generated']} "
          f"self_consistent={cov['policy_self_consistent']} "
          f"verified={cov['boundary_semantics_verified']}）")
    check(all(a["status"] == POLICY_SELF_CONSISTENT
              for a in cov["aspects"].values()),
          "每个 aspect 状态均为 policy_self_consistent（非「已验证」）")
    check(all(a["policy_version"] for a in cov["aspects"].values()),
          "每个 aspect 均带非空 policy_version（版本化确定性）")

    # ------------------------------------------------------------------
    # 4. fail-closed：未知 aspect → boundary_policy_unavailable
    # ------------------------------------------------------------------
    unknown = topic_boundary_policy("no.such.aspect")
    check(not unknown.available, "未知 aspect → available=False")
    check(unknown.reason.startswith(BOUNDARY_POLICY_UNAVAILABLE),
          "未知 aspect → reason 带 boundary_policy_unavailable")
    check(classify_heading_topic("xx", "no.such.aspect") == TOPIC_AMBIGUOUS,
          "未知 aspect 分类 → ambiguous（是否 fail-closed 由上层裁决）")

    # ------------------------------------------------------------------
    # 5. domain_segments：连接词两侧 ≥2 CJK 才切，不拆词内字
    # ------------------------------------------------------------------
    seg = domain_segments("客户与供应商集中度")
    check("客户" in seg and "供应商集中度" in seg,
          "「客户与供应商集中度」→ 客户 / 供应商集中度（连接词切分）")
    seg2 = domain_segments("竞争格局、集中度和主要参与者")
    check("主要参与者" in seg2 and "主要参" not in seg2,
          "「主要参与者」不被拆成「主要参」（与 为词内字）")
    seg3 = domain_segments("涉及主体")
    check(seg3 == ("涉及主体",), "「涉及主体」不被拆（及 为词内字）")
    seg4 = domain_segments("主营业务构成（分板块/分部）")
    check("主营业务构成" in seg4 and "分板块" in seg4 and "分部" in seg4,
          "括号内分板块/分部被正确切分（无空格残留）")

    # ------------------------------------------------------------------
    # 6. find_topic_boundary：mixed block 定位首个主题外标题 + 前缀
    # ------------------------------------------------------------------
    mixed = ("公司主营业务收入分析实现增长。（二）经营模式情况\n"
             "2023年收入占比提升。（四）在建工程情况\n"
             "公司在建工程项目持续推进。")
    tb = find_topic_boundary(mixed, _MAIN_BUSINESS)
    check(tb.has_out_of_topic and tb.out_of_topic_heading == "（四）在建工程情况",
          "find_topic_boundary 命中首个主题外标题（四）在建工程情况")
    check("在建工程" not in tb.relevant_prefix and "经营模式" in tb.relevant_prefix,
          "find_topic_boundary 前缀含主题内内容、不含主题外标题")
    check(tb.stop_direction, "find_topic_boundary 标记停止该方向扩读")

    tb2 = find_topic_boundary("（二）主营业务情况（1）整体情况\n公司提供产品。", _MAIN_BUSINESS)
    check(not tb2.has_out_of_topic, "纯主题内 mixed block 不触发主题边界")

    # ------------------------------------------------------------------
    # 7. block_start_topic_class：块首主题外/主题内/无标题
    # ------------------------------------------------------------------
    check(block_start_topic_class("十、在建工程项目\n公司持续推进……",
                                  _MAIN_BUSINESS) == TOPIC_OUT_OF_TOPIC,
          "块首主题外标题 → out_of_topic")
    check(block_start_topic_class("（二）主营业务情况：快速增长。",
                                  _MAIN_BUSINESS) == TOPIC_IN_TOPIC,
          "块首主题内标题 → in_topic")
    check(block_start_topic_class("公司主要从事研发制造。",
                                  _MAIN_BUSINESS) is None,
          "块首无标题 → None")

    # ------------------------------------------------------------------
    # 8. 集成：expand 遇主题外标题块 → sentinel + 片段投影
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 1, ["主营业务情况"], "paragraph", "公司主要从事研发与制造。"),
            (5, 2, ["主营业务情况"], "paragraph", mixed),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, section=("主营业务情况",),
                     text="公司主要从事研发与制造。")
        request = ContextExpansionRequest(
            company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, seed=seed, directions=("adjacent_blocks",),
            budget=__import__("harness.context_expansion", fromlist=["ExpansionBudget"])
            .ExpansionBudget(),
            dependency_fingerprint=_DEP, aspect_id=_MAIN_BUSINESS)
        res = expand(request, registry, run_id="topic-boundary")
        mixed_id = _eid_of(5, 2, mixed)
        dec = next(d for d in res.boundary_decisions if d.evidence_id == mixed_id)
        check(dec.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
              and dec.reason_code == "topic_boundary_out_of_topic",
              "集成：主题外标题块 → outside_boundary_sentinel + topic_boundary_out_of_topic")
        check(len(res.fragment_projections) == 1
              and res.fragment_projections[0].evidence_id == mixed_id,
              "集成：产生 1 个片段投影（指向主题外标题块）")
        check("在建工程" not in res.fragment_projections[0].prefix_text
              and "经营模式" in res.fragment_projections[0].prefix_text,
              "集成：片段投影前缀含主题内内容、不含在建工程")

        # 片段 material：locator.offset 记录主题外标题界，payload 为主题内前缀。
        mres = build_material_result(res, dependency_fingerprint=_DEP,
                                     aspect_id=_MAIN_BUSINESS)
        frag_mats = [m for m in mres.materials
                     if getattr(m.locator, "offset", None) is not None]
        check(len(frag_mats) == 1,
              "集成：片段 material 携带 locator.offset（有界关联）")
        if frag_mats:
            fm = frag_mats[0]
            check(fm.authority_assessment.evidence_id == mixed_id,
                  "集成：片段 material 来源 identity 仍指向完整 atomic block")
            check(fm.locator.offset == res.fragment_projections[0].char_offset,
                  "集成：片段 material locator.offset == 主题外标题字符界")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
