"""Eval: Phase 4 纵向切片 — 章节写入器（结构化段落 + 数字标记替换 + 确定性渲染）。

用法: python -m evals.test_chapter_writer

纯函数 + 注入假 authority（不调 LLM、不读库）。真实 LLM 落地由
evaluation/run_phase4_vertical_slice.py 验收。覆盖：
- 数字格式化（元→亿元 / 百分比）；
- 标记解析 / 裸数字检测 / 占位符替换；
- SentenceDraft 校验：裸数字 / 未解析 fact / 权威失败 / fact 句必须绑定事实 /
  inference 绑定事实 / A5 类别；
- 确定性业务表格（分板块收入/占比/毛利率）与来源表格、周期位置推断；
- LLM JSON 结构化解析（非法类型 fail-closed）；
- build_chapter 端到端渲染 + 校验 fail-closed。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections.chapter_writer import (
    ParagraphDraft, SentenceDraft, TableDraft, bare_numbers, build_business_table,
    build_chapter, build_cycle_judgment, build_fact_index, build_source_table,
    chapter_draft_id, compute_business_calculations, fill_markers, format_percent,
    format_yuan_yi, paragraphs_from_payload, parse_markers, render_prompt,
    validate_draft, validate_sentence,
)


class _Verdict:
    def __init__(self, valid=True):
        self.valid = valid


class _Auth:
    def __init__(self, valid=True):
        self._valid = valid

    def validate(self, ref):
        return _Verdict(self._valid)


_EF = (
    {"evidence_fact_id": "ef-r1", "evidence_id": "e1",
     "revenue_cost_category": "revenue", "business_segment": "动力电池系统",
     "period": "2025-12-31", "value": "316506369000.0", "page_number": 12},
    {"evidence_fact_id": "ef-r2", "evidence_id": "e2",
     "revenue_cost_category": "revenue", "business_segment": "储能电池系统",
     "period": "2025-12-31", "value": "50000000000.0", "page_number": 12},
    {"evidence_fact_id": "ef-c1", "evidence_id": "e3",
     "revenue_cost_category": "cost", "business_segment": "动力电池系统",
     "period": "2025-12-31", "value": "241064397000.0", "page_number": 13},
)


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

    # 1) 数字格式化
    check(format_yuan_yi("316506369000.0") == "3,165.06 亿元",
          "元→亿元：316506369000 → 3,165.06 亿元")
    check(format_yuan_yi(None) == "—", "空值 → —")
    check(format_percent(__import__("decimal").Decimal("0.8636")) == "86.4%",
          "百分比格式化")

    # 2) 标记解析 / 裸数字 / 替换
    txt = "{{fact:ef-r1}}占比 {{calc:share_0}}"
    check(parse_markers(txt) == ({"kind": "fact", "id": "ef-r1"},
                                 {"kind": "calc", "id": "share_0"}),
          "parse_markers 解析 fact/calc")
    check(bare_numbers("收入 3165 亿元") == ("3165",),
          "裸数字检测")
    check(bare_numbers("收入 {{fact:ef-r1}} 亿元") == (),
          "占位符内数字不算裸数字")
    filled = fill_markers("收入 {{fact:ef-r1}}，占比 {{calc:share_0}}",
                          {"ef-r1": "3,165.06 亿元"}, {"share_0": "86.4%"})
    check(filled == "收入 3,165.06 亿元，占比 86.4%", "占位符替换为精确值")

    # 3) 事实索引
    idx = build_fact_index(_EF, ())
    check(idx["ef-r1"]["display"] == "3,165.06 亿元"
          and idx["ef-r1"]["refs"][0].ref_type == "evidence",
          "本地事实索引（display + evidence 引用）")
    ext_facts = ({"fact_id": "efext-1", "source_snapshot_id": "s1",
                  "fact_class": "scale", "value": "1.2 万亿元", "period": "2025",
                  "statement": "行业规模 1.2 万亿元"},)
    idx2 = build_fact_index(_EF, ext_facts)
    check(idx2["efext-1"]["refs"][0].ref_type == "external",
          "外部事实索引（external 引用）")

    # 4) 校验
    auth = _Auth(True)
    good = SentenceDraft(sentence_type="fact",
                         text="{{fact:ef-r1}}为主要收入来源，占比 {{calc:share_0}}。",
                         fact_ids=("ef-r1",))
    bare = SentenceDraft(sentence_type="fact", text="收入 3165 亿元。",
                         fact_ids=("ef-r1",))
    missing = SentenceDraft(sentence_type="fact", text="收入 {{fact:ef-missing}}。",
                            fact_ids=("ef-missing",))
    nofact = SentenceDraft(sentence_type="fact", text="占比很高。", fact_ids=())
    infer_ok = SentenceDraft(sentence_type="inference", text="动力电池为核心主业。",
                             fact_ids=("ef-r1",))
    infer_bad = SentenceDraft(sentence_type="inference", text="结论合理。", fact_ids=())
    check(len(validate_sentence(good, idx, ("share_0",), auth)) == 0,
          "合规句无 issue")
    check(any(i.code == "bare_number" for i in validate_sentence(bare, idx, ("share_0",), auth)),
          "裸数字 → bare_number")
    check(any(i.code == "unresolved_fact"
              for i in validate_sentence(missing, idx, ("share_0",), auth)),
          "未解析 fact → unresolved_fact")
    check(any(i.code == "type_requires_facts"
              for i in validate_sentence(nofact, idx, ("share_0",), auth)),
          "fact 句无 fact_ids → type_requires_facts")
    check(len(validate_sentence(infer_ok, idx, ("share_0",), auth)) == 0,
          "inference 绑定事实 → ok")
    check(any(i.code == "type_requires_facts"
              for i in validate_sentence(infer_bad, idx, ("share_0",), auth)),
          "inference 无事实绑定 → type_requires_facts")
    auth_bad = _Auth(False)
    check(any(i.code == "authority_failed"
              for i in validate_sentence(good, idx, ("share_0",), auth_bad)),
          "权威校验失败 → authority_failed")

    # 5) 业务表格 / 计算
    calc, rows = compute_business_calculations(_EF)
    check(len(rows) == 2 and rows[0]["segment"] == "动力电池系统",
          "分板块聚合（收入降序）")
    check(abs(float(rows[0]["share"]) - 0.8636) < 1e-3,
          "动力电池收入占比 ≈ 86.36%（Python 计算）")
    check(abs(float(rows[0]["margin"]) - 0.2384) < 1e-3,
          "动力电池毛利率 ≈ 23.84%（(rev-cost)/rev）")
    table, cdisp, fdisp = build_business_table(_EF)
    check(table.header == ("业务板块", "营业收入(亿元)", "收入占比", "营业成本(亿元)", "毛利率"),
          "业务表格表头")
    check(table.rows[0][0] == "动力电池系统" and table.rows[0][2] == "86.4%",
          "业务表格首行")

    # 6) 来源表格 / 周期推断
    src_table = build_source_table(ext_facts)
    check(src_table.header[0] == "来源等级" and len(src_table.rows) == 1,
          "来源表格")
    ext_two = ({"fact_id": "efext-1", "source_snapshot_id": "s1",
                "fact_class": "scale", "value": "1.2 万亿元"},
               {"fact_id": "efext-2", "source_snapshot_id": "s2",
                "fact_class": "growth", "value": "15%"})
    check(build_cycle_judgment(ext_two) is not None,
          "周期位置：≥2 类事实 → 产出推断")
    check(build_cycle_judgment(ext_two[:1]) is None,
          "周期位置：单类事实 → 阻断（无跨类交叉印证）")

    # 7) LLM JSON 解析
    payload = {"paragraphs": [{"sentences": [
        {"type": "fact", "text": "收入 {{fact:ef-r1}}。", "fact_ids": ["ef-r1"]}]}]}
    paras = paragraphs_from_payload(payload, topic_id="company_business",
                                    question_id="company_business_main")
    check(len(paras) == 1 and paras[0].sentences[0].fact_ids == ("ef-r1",),
          "LLM JSON → ParagraphDraft")
    try:
        paragraphs_from_payload({"paragraphs": [{"sentences": [
            {"type": "bogus", "text": "x", "fact_ids": []}]}]},
            topic_id="t", question_id="q")
        check(False, "非法句子类型应 fail-closed")
    except Exception:
        check(True, "非法句子类型 fail-closed")

    # 8) render_prompt 占位符替换（读真实 prompt 文件，仅验占位符机制）
    rendered = render_prompt("topic_chapter_writer_v1", {"kind": "business",
                                                          "title": "主营业务构成",
                                                          "paragraphs_hint": "2-4",
                                                          "fact_context": "f",
                                                          "calc_context": "c"})
    check("business" in rendered and "{{kind}}" not in rendered
          and "{{title}}" not in rendered and "{{fact_context}}" not in rendered,
          "render_prompt 替换占位符")

    # 9) build_chapter 端到端（合规）
    good_para = ParagraphDraft(topic_id="company_business",
                               question_id="company_business_main",
                               sentences=(good, infer_ok))
    chapter = build_chapter(topic_id="company_business",
                            question_id="company_business_main", kind="business",
                            paragraphs=(good_para,), tables=(table,),
                            fact_display=fdisp, calc_display=cdisp, authority=auth,
                            fact_index=idx)
    check(chapter.verdict.ok, "合规段落 → verdict.ok")
    check("# 主营业务构成" in chapter.markdown and "动力电池系统" in chapter.markdown
          and "3,165.06 亿元" in chapter.markdown,
          "Markdown 渲染：标题 + 表格 + 占位符已替换")

    # 10) build_chapter 校验 fail-closed（裸数字 → not ok）
    bad_para = ParagraphDraft(topic_id="company_business",
                              question_id="company_business_main", sentences=(bare,))
    chapter_bad = build_chapter(topic_id="company_business",
                                question_id="company_business_main", kind="business",
                                paragraphs=(bad_para,), tables=(table,),
                                fact_display=fdisp, calc_display=cdisp, authority=auth,
                                fact_index=idx)
    check(not chapter_bad.verdict.ok
          and any(i.code == "bare_number" for i in chapter_bad.verdict.issues),
          "裸数字段落 → verdict 不通过（fail-closed）")

    # 11) chapter_draft_id 确定性
    check(chapter_draft_id(chapter) == chapter_draft_id(chapter)
          and chapter_draft_id(chapter).startswith("ch_"),
          "chapter_draft_id 内容寻址确定性")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
