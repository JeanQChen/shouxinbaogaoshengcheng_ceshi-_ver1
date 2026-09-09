"""Eval: Phase 4 Batch A — 章节产物 Schema 与身份派生。

不调用 LLM / Embedding / Chroma / 互联网。

覆盖：
1. CitationRef 复用 harness.schema.CitationRef（非第二套引用类型）。
2. claim_type 白名单（fact/calculation/inference，不含 recommendation）。
3. derive_claim_id / derive_section_version 确定性且随内容变化。
4. citation_identity 三类引用身份。
5. 结构校验器：合法 claim 通过；recommendation 拒绝；fact 无 citation 拒绝。

用法: python -m evals.test_section_schema
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import schema as HS  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import validator as SV  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def main():
    # 1. CitationRef 复用 harness 身份（不是第二套类型）
    check(SS.CitationRef is HS.CitationRef,
          "SectionClaim 引用应复用 harness.schema.CitationRef")

    # 2. claim_type 白名单
    check("recommendation" not in SS.CLAIM_TYPES, "第一阶段不含 recommendation")
    check(SS.CLAIM_TYPES == ("fact", "calculation", "inference"), "claim 类型三态")

    # 3. derive_claim_id 确定性 + 随正文变化
    ref = SS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)
    cid1 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体一致", [ref])
    cid2 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体一致", [ref])
    check(cid1 == cid2, "derive_claim_id 确定性")
    cid3 = SS.derive_claim_id("fact", "company_identity", ["company_subject_match"],
                              "主体不一致", [ref])
    check(cid1 != cid3, "正文变化改变 claim_id")

    # 4. derive_section_version 确定性 + 随 claims 变化
    claim = SS.SectionClaim(claim_id=cid1, section_id="company", topic_id="company_identity",
                            question_ids=("company_subject_match",), text="主体一致",
                            claim_type="fact", citation_refs=(ref,))
    v1 = SS.derive_section_version("task_x", [claim], [],
                                   renderer_version="r1", rules_version="g1")
    v2 = SS.derive_section_version("task_x", [claim], [],
                                   renderer_version="r1", rules_version="g1")
    check(v1 == v2, "derive_section_version 确定性")
    claim2 = SS.SectionClaim(claim_id="claim_other", section_id="company",
                             topic_id="company_identity",
                             question_ids=("company_subject_match",), text="另一事实",
                             claim_type="fact", citation_refs=(ref,))
    v3 = SS.derive_section_version("task_x", [claim, claim2], [],
                                   renderer_version="r1", rules_version="g1")
    check(v1 != v3, "claims 变化改变 section_version")

    # 5. citation_identity 三类
    check(SS.citation_identity(SS.CitationRef(ref_type="evidence", evidence_id="e",
                                               page_number=3)) == "evidence:e:3",
          "evidence 引用身份")
    check(SS.citation_identity(SS.CitationRef(ref_type="structured", snapshot_id="s",
                                               formula_id="f", formula_version="v1",
                                               period="2025-12-31"))
          == "structured:s::f:v1:2025-12-31", "structured 引用身份")
    check(SS.citation_identity(SS.CitationRef(ref_type="external",
                                               source_snapshot_id="x")) == "external:x",
          "external 引用身份")

    # 6. 结构校验器
    ok_ref = SS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=12)
    valid = SS.SectionClaim(claim_id="c1", section_id="company", topic_id="company_identity",
                            question_ids=("company_subject_match",), text="主体一致",
                            claim_type="fact", citation_refs=(ok_ref,))
    check(SV.validate_claim(valid) == [], "合法 claim 通过校验")
    rec = SS.SectionClaim(claim_id="c2", section_id="company", topic_id="company_identity",
                          question_ids=("company_subject_match",), text="建议授信",
                          claim_type="recommendation", citation_refs=(ok_ref,))
    check(any("recommendation" in e for e in SV.validate_claim(rec)),
          "recommendation 被拒绝")
    nocite = SS.SectionClaim(claim_id="c3", section_id="company", topic_id="company_identity",
                             question_ids=("company_subject_match",), text="无依据",
                             claim_type="fact", citation_refs=())
    check(any("无 citation" in e for e in SV.validate_claim(nocite)),
          "fact 无 citation 被拒绝")

    return _results


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
