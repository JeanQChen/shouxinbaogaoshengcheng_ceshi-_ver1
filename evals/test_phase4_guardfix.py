"""Eval: Phase 4 纵向研究 guardfix 回归 — adopted-facts 硬防火墙 / 反虚假覆盖 / 双支撑。

用法: python -m evals.test_phase4_guardfix

纯函数回归（无 I/O / LLM），覆盖用户最终约束 §二～§五 的失效形态（本轮回接正式契约后保留）：
 1. D 级来源全部拒绝 → 行业章 DATA_GAP（0 adopted 不产正文）；
 2. 0 adopted → build_chapter 不产伪完整章节（content_ok=False / chapter_ok=False）；
 3. 反虚假覆盖：收入/成本事实不得让「产业链位置」obtained（正式 company_business_main aspect）；
 4. aspect_fact_categories sentinel 正确（正式契约 aspect 文本）；
 5. support_verdict 防火墙：非 SUPPORTED / 白名单外 → rejected；
 6. D/unknown 级来源 → rejected（不入正文，d_grade_not_in_body）；
 7. transmission 双支撑：缺公司暴露事实 → DATA_GAP；
 8. transmission 双支撑：缺行业驱动事实 → DATA_GAP；
 9. transmission 双支撑：驱动+暴露齐备 → chapter_ok=True；
10. chapter_ok 内容资格 = verdict.ok AND content_ok（0 adopted + ok=True 被禁止）；
11. source-intent 配置：外部样本 include 权威域名，business 为 None。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import chapter_writer as CW
from sections.topic_research import assess_external_cell, assess_local_cell, aspect_fact_categories
from evaluation.run_phase4_vertical_slice import (
    _filter_evidence_to_adopted, _partition_extracted_facts,
    _render_business_chapter, _render_industry_chapter, _render_transmission_chapter,
    _source_intent_for, load_formal, plan_dry, select_slice,
)
from planning.topic_research import TopicResearchContext, derive_topic_queries


class _Verdict:
    valid = True


class _Auth:
    def validate(self, ref):
        return _Verdict()


class _Fact:
    def __init__(self, fid, eid, cat, seg, period, val):
        self.evidence_fact_id = fid
        self.evidence_id = eid
        self.revenue_cost_category = cat
        self.business_segment = seg
        self.period = period
        self.value = val
        self.page_number = 12

    def to_dict(self):
        return {"evidence_fact_id": self.evidence_fact_id,
                "evidence_id": self.evidence_id,
                "revenue_cost_category": self.revenue_cost_category,
                "business_segment": self.business_segment,
                "period": self.period, "value": self.value,
                "page_number": self.page_number}


class _Src:
    def __init__(self, sid, grade):
        self.source_snapshot_id = sid
        self.source_grade = grade
        self.canonical_url = f"https://x/{sid}"
        self.published_at = "2025-01-01"
        self.title = f"t{sid}"
        self.snippet = ""
        self.content_excerpt = "x"


def _ext_fact(fid, sid, verdict, fact_class="scale", value="1.2 万亿元"):
    return {"fact_id": fid, "source_snapshot_id": sid, "fact_class": fact_class,
            "value": value, "period": "2025", "statement": f"{fid} 陈述",
            "source_grade": "B", "published_at": "2025-01-01",
            "canonical_url": f"https://x/{sid}", "support_verdict": verdict}


def _company_business_main_aspects():
    """从正式契约派生 company_business_main 的 5 个 AspectQuery。"""
    contracts, sha = load_formal()
    plan = plan_dry(job_id="j", company_id="C", company_name="测试", credit_type="other",
                    report_as_of="2026-03-31", enabled_sections=("company", "industry"),
                    contracts=contracts, contract_sha=sha)
    comp_task, _ = select_slice(plan, contracts, scope="topic_preview",
                                section_id="company", topic_id="company_business")
    ctx = TopicResearchContext(company_id="C", company_name="测试",
                               industry_names=("测试行业",), report_as_of="2026-03-31")
    qp = derive_topic_queries(comp_task, "company_business", ctx)
    return tuple(a for a in qp.aspects if a.question_id == "company_business_main")


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

    auth = _Auth()

    # 1) D 级来源全部拒绝 → 行业章 DATA_GAP
    ch = _render_industry_chapter((), auth, (), ())
    check((not ch.chapter_ok) and (not ch.verdict.content_ok)
          and "DATA_GAP" in ch.markdown,
          "0 adopted → 行业章 DATA_GAP（chapter_ok=False）")

    # 2) 0 adopted → business 章不产伪完整章节
    chb = _render_business_chapter((), auth, (), ())
    check((not chb.chapter_ok) and "DATA_GAP" in chb.markdown,
          "0 adopted → business 章 DATA_GAP")

    # 3) 反虚假覆盖：收入/成本事实不得让「产业链位置」obtained（正式 aspect）
    main_aspects = _company_business_main_aspects()
    check({a.aspect_text for a in main_aspects} == {
        "主营业务构成", "各业务收入及收入占比", "各业务成本与毛利构成",
        "产业链位置", "对应报告期与口径"},
        "company_business_main 正式 5 aspect")
    facts = (_Fact("ef-r1", "e1", "revenue", "动力电池系统", "2025-12-31", "316506369000.0"),
             _Fact("ef-c1", "e2", "cost", "动力电池系统", "2025-12-31", "241064397000.0"))
    by_text = {c.aspect_text: assess_local_cell(aspect=c, facts=facts, authority=auth)
               for c in main_aspects}
    check(by_text["主营业务构成"].obtained and by_text["各业务收入及收入占比"].obtained
          and by_text["各业务成本与毛利构成"].obtained,
          "主营业务构成/收入占比/成本毛利 aspect 由收入/成本事实 obtained")
    check(not by_text["产业链位置"].obtained,
          "产业链位置 aspect 不因收入/成本事实 obtained（反虚假覆盖）")

    # 4) aspect_fact_categories sentinel（正式契约 aspect 文本）
    check(aspect_fact_categories("产业链位置") == ("supply_chain",),
          "「产业链位置」→ supply_chain（不误判 product_application）")
    check(aspect_fact_categories("各业务收入及收入占比") == ("revenue",),
          "「各业务收入及收入占比」→ revenue")
    check(aspect_fact_categories("各业务成本与毛利构成") == ("cost",),
          "「各业务成本与毛利构成」→ cost")

    # 5) support_verdict 防火墙：非 SUPPORTED / 白名单外 → rejected
    extracted = (_ext_fact("f1", "s1", "SUPPORTED"),
                 _ext_fact("f2", "s2", "PARTIAL"),
                 _ext_fact("f3", "s3", "SUPPORTED"),
                 _ext_fact("f4", "s1", "UNSUPPORTED"))
    adopted, rejected = _partition_extracted_facts(extracted, ("s1",))
    check([f["fact_id"] for f in adopted] == ["f1"],
          "只采纳 SUPPORTED 且 source_snapshot_id 在白名单内的事实")
    check({f["fact_id"] for f in rejected} == {"f2", "f3", "f4"},
          "PARTIAL / 白名单外 / UNSUPPORTED → rejected")

    # 6) D/unknown 级来源 → rejected（不入正文，d_grade_not_in_body）
    from planning.topic_research import AspectQuery
    asp = AspectQuery(aspect_id="a", aspect_text="行业规模", query_id="q",
                      topic_id="company_business", question_id="q",
                      priority="P0", evidence_kind="web", source_classes=("external",),
                      required_fields=(), freshness_policy=None, minimum_sources=1,
                      local_query=None, external_query="行业规模")
    dcell = assess_external_cell(aspect=asp, sources=(_Src("sD", "D"),),
                                 authority=auth, topic_id="company_business")
    check((not dcell.obtained) and "d_grade_not_in_body" in dcell.loss_reasons,
          "D 级来源 → 不 obtained + d_grade_not_in_body")

    # 7) transmission 双支撑：缺公司暴露事实 → DATA_GAP
    ch7 = _render_transmission_chapter((), (), auth, (), ("s1",), ())
    check((not ch7.chapter_ok) and "DATA_GAP" in ch7.markdown,
          "transmission 缺暴露事实 → DATA_GAP")

    # 8) transmission 双支撑：缺行业驱动事实 → DATA_GAP
    ch8 = _render_transmission_chapter((), (), auth, (), (), ("ef-r1",))
    check((not ch8.chapter_ok) and "DATA_GAP" in ch8.markdown,
          "transmission 缺驱动事实 → DATA_GAP")

    # 9) transmission 双支撑：驱动+暴露齐备 → chapter_ok=True
    exposure = ({"evidence_fact_id": "ef-r1", "evidence_id": "e1",
                 "revenue_cost_category": "revenue", "business_segment": "动力电池系统",
                 "period": "2025-12-31", "value": "316506369000.0", "page_number": 12},)
    extf = (_ext_fact("efext-1", "s1", "SUPPORTED", fact_class="risk", value="上游降价"),)
    ch9 = _render_transmission_chapter(extf, exposure, auth, (), ("s1",), ("ef-r1",))
    check(ch9.chapter_ok and not ch9.verdict.ok is False,
          "transmission 驱动+暴露齐备 → chapter_ok=True")

    # 10) chapter_ok 内容资格 = verdict.ok AND content_ok
    idx = CW.build_fact_index(exposure, ())
    _, cdisp, fdisp = CW.build_business_table(exposure)
    ch10_gap = CW.build_chapter(topic_id="company_business", question_id="q",
                                kind="business", paragraphs=(), tables=(),
                                fact_display=fdisp, calc_display=cdisp, authority=auth,
                                fact_index=idx, adopted_fact_ids=())
    check(ch10_gap.verdict.ok and (not ch10_gap.verdict.content_ok)
          and (not ch10_gap.chapter_ok),
          "0 adopted + verdict.ok=True → 仍 chapter_ok=False（内容资格硬防火墙）")
    ch10_ok = CW.build_chapter(topic_id="company_business", question_id="q",
                               kind="business", paragraphs=(), tables=(),
                               fact_display=fdisp, calc_display=cdisp, authority=auth,
                               fact_index=idx, adopted_fact_ids=("ef-r1",))
    check(ch10_ok.chapter_ok, "非空 adopted → chapter_ok=True")

    # 10b) 段落校验失败（裸数字）→ 正文剔除无效段落，只保留确定性内容
    from sections.chapter_writer import ParagraphDraft, SentenceDraft
    bare = SentenceDraft(sentence_type="fact", text="收入 3165 亿元。",
                         fact_ids=("ef-r1",))
    bad_para = ParagraphDraft(topic_id="t", question_id="q", sentences=(bare,))
    ch10b = CW.build_chapter(topic_id="company_business", question_id="q",
                             kind="business", paragraphs=(bad_para,), tables=(),
                             fact_display=fdisp, calc_display=cdisp, authority=auth,
                             fact_index=idx, adopted_fact_ids=("ef-r1",))
    check((not ch10b.verdict.ok) and ("3165 亿元" not in ch10b.markdown)
          and ("已从正文剔除" in ch10b.markdown),
          "段落校验失败 → 正文剔除无效段落（不产出裸数字正文）")

    # 11) source-intent 配置
    scale_intent = _source_intent_for("industry_scale_cycle")
    trans_intent = _source_intent_for("industry_risk_transmission")
    check(scale_intent and "gov.cn" in scale_intent["include"],
          "scale source-intent include 政府/监管域名")
    check(trans_intent and "cninfo.com.cn" in trans_intent["include"],
          "transmission source-intent include 交易所/法定披露域名")
    check(_source_intent_for("company_business") is None,
          "business 无 source-intent（本地确定性路径）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
