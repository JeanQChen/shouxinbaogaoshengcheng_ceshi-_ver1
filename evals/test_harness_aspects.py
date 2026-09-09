"""Eval: required-aspect 契约优先派生 —— Phase 3 Batch B 修订①。

用法: python -m evals.test_harness_aspects

断言（纯逻辑，无 LLM / I/O）：
- DATASET_MAPPING：case_id COMP-R1 / COMP-CR1 命中契约 question_ids 的 required_aspects；
- SECTION_CONTRACT：question_id 直接命中契约 required_aspects；
- 优先级：question_id 命中优先于 DATASET_MAPPING（同题两来源取 SECTION_CONTRACT）；
- TEXT_FALLBACK：多句按句末标点切、单句保 1 不猜、不按「和/及/、」切名词短语；
- 空问题 → 空列表；source 标记正确。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import aspects as A
from contracts import schema as S


def _texts(aspects: list[A.Aspect]) -> list[str]:
    return [a.text for a in aspects]


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

    # ---- DATASET_MAPPING：COMP-R1 → 契约「主营业务」3 方面 ----
    r1 = A.derive_required_aspects("占位", case_id="COMP-R1")
    check([a.source for a in r1] == ["DATASET_MAPPING"] * 3,
          "COMP-R1：3 个方面且 source=DATASET_MAPPING")
    check(_texts(r1) == ["主营业务构成", "各业务收入及收入占比", "对应报告期与口径"],
          "COMP-R1：方面文本 == 契约 required_aspects（非字符串切分）")

    # ---- DATASET_MAPPING：COMP-CR1 → 授信 + 担保 3 方面 ----
    cr1 = A.derive_required_aspects("占位", case_id="COMP-CR1")
    check(_texts(cr1) == ["公司整体授信额度及使用情况", "公司整体对外担保余额",
                          "担保范围口径与报告期"],
          "COMP-CR1：授信额度/对外担保/口径 三方面（不把「授信与担保」误拆）")

    # ---- DATASET_MAPPING：字段级范围（covered_aspects 非空只派生声明字段）----
    s1 = A.derive_required_aspects("占位", case_id="COMP-S1")
    check([a.source for a in s1] == ["DATASET_MAPPING"]
          and _texts(s1) == ["成立日期"],
          "COMP-S1：字段级 covered_aspects 只派生「成立日期」，不展开 6 方面")

    gm1 = A.derive_required_aspects("占位", case_id="FIN-GM1")
    check(_texts(gm1) == ["毛利率"],
          "FIN-GM1：partial + covered_aspects 只派生「毛利率」")

    # ---- DATASET_MAPPING：partial 且无 covered_aspects → fail-closed 走 TEXT_FALLBACK ----
    synth_partial = [S.BaselineContractMapping(
        case_id="X-P1", question_ids=["company_identity_basic"],
        coverage_role="partial", note="")]
    xp = A.derive_required_aspects(
        "成立时间", case_id="X-P1", mappings=synth_partial)
    check([a.source for a in xp] == ["TEXT_FALLBACK"] and len(xp) == 1,
          "partial 无 covered_aspects → 不展开，fail-closed 走 TEXT_FALLBACK")

    # ---- DATASET_MAPPING：full 且无 covered_aspects → 展开全部（契约粒度完全匹配）----
    synth_full = [S.BaselineContractMapping(
        case_id="X-F1", question_ids=["company_identity_basic"],
        coverage_role="full", note="")]
    xf = A.derive_required_aspects(
        "占位", case_id="X-F1", mappings=synth_full)
    check(_texts(xf) == ["成立日期", "办公地址", "法定代表人", "注册资本", "实缴资本", "经营范围"],
          "full 无 covered_aspects → 展开契约全部 6 方面")

    # ---- SECTION_CONTRACT：question_id 直接命中 ----
    sc = A.derive_required_aspects("占位", question_id="company_business_main")
    check([a.source for a in sc] == ["SECTION_CONTRACT"] * 5
          and _texts(sc) == ["主营业务构成", "各业务收入及收入占比",
                             "各业务成本与毛利构成", "产业链位置", "对应报告期与口径"],
          "SECTION_CONTRACT：question_id 命中契约 required_aspects（5 方面，含成本/毛利与产业链位置）")

    # ---- 优先级：question_id 命中优先于 DATASET_MAPPING ----
    both = A.derive_required_aspects("占位", question_id="company_business_main",
                                     case_id="COMP-CR1")
    check(both[0].source == "SECTION_CONTRACT"
          and _texts(both) == ["主营业务构成", "各业务收入及收入占比",
                               "各业务成本与毛利构成", "产业链位置", "对应报告期与口径"],
          "优先级：question_id 命中优先于 case_id 映射")

    # ---- TEXT_FALLBACK：多句按句末标点切 ----
    multi = A.derive_required_aspects("主营业务有哪些？各业务收入占比如何？")
    check([a.source for a in multi] == ["TEXT_FALLBACK"] * 2
          and _texts(multi) == ["主营业务有哪些", "各业务收入占比如何"],
          "TEXT_FALLBACK：多句按句末标点切为 2 方面")

    # ---- TEXT_FALLBACK：单句保 1 不猜 ----
    single = A.derive_required_aspects("核心竞争力是什么")
    check(len(single) == 1 and single[0].text == "核心竞争力是什么"
          and single[0].source == "TEXT_FALLBACK",
          "TEXT_FALLBACK：单句保 1 方面 = 整句")

    # ---- TEXT_FALLBACK：不按「和/及/、」切名词短语 ----
    and_q = A.derive_required_aspects("授信与对外担保情况")
    check(len(and_q) == 1 and and_q[0].text == "授信与对外担保情况",
          "TEXT_FALLBACK：不按「和/及/、」切名词短语（授信与担保不误拆）")

    # ---- 空问题 → 空 ----
    empty = A.derive_required_aspects("")
    check(empty == [], "空问题 → 空方面列表")

    # ---- 未命中契约/mapping 的 case 走 TEXT_FALLBACK（非空，保 1） ----
    out = A.derive_required_aspects("实际控制人是谁", case_id="COMP-MV1")
    check(len(out) == 1 and out[0].source == "TEXT_FALLBACK"
          and out[0].text == "实际控制人是谁",
          "未命中契约/mapping 的 case → TEXT_FALLBACK 保 1（非空）")

    # ---- aspect_source 汇总 ----
    check(A.aspect_source([]) == "" and A.aspect_source(r1) == "DATASET_MAPPING",
          "aspect_source：空列表 → 空串；命中列表 → 统一来源")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
