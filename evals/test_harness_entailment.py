"""Eval: 确定性数字/口径预检层 —— Phase 3 Batch B 修订③④。

用法: python -m evals.test_harness_entailment

断言（纯函数，无 LLM / I/O）：
- normalize_amount / amounts_equivalent：金额归一化（40,000万元==4亿元）；
- value_presence：claim 数字在证据正文的数值等价存在性；
- scope_risks：担保/授信/合并/合计 口径宽窄冲突标记（high/low）；
- deterministic_prechecks：not_inspected / value_missing / high_risk_scope；
- backfill_page_numbers：只补空、不重写；
- capture_inspected：inspect 全文 vs search 摘要（is_snippet）。
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import entailment as E
from harness import schema as H
from routing import schema as RS
from tools import contracts as TC


def _need() -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])


def _state(inspected: dict | None = None, question: str = "q") -> H.ResearchState:
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="300750",
                         section_id="company", original_question=question, need=_need())
    if inspected is not None:
        st.inspected_evidence = inspected
    return st


def _answer(claim_text: str, evidence_id: str = "e1",
            kind: str = "fact", page_number: int | None = None) -> H.ResearchAnswer:
    claim = H.Claim(claim_id="c1", text=claim_text, kind=kind, citation_refs=[0])
    cit = H.CitationRef(ref_type="evidence", evidence_id=evidence_id,
                        page_number=page_number)
    return H.ResearchAnswer(question_id="q1", answer_text=claim_text,
                            claims=[claim], citations=[cit])


def _mat(text: str, eid: str = "e1", page: int | None = 3,
         snippet: bool = False, payload: dict | None = None) -> H.InspectedMaterial:
    return H.InspectedMaterial(evidence_id=eid, source_name="s", page_number=page,
                               text=text, is_snippet=snippet, structured_payload=payload)


def _tool_result(tool_name: str, status: str, data: dict) -> TC.ToolResult:
    return TC.ToolResult(call_id="", tool_name=tool_name, tool_version="v1",
                         status=status, data=data)


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

    def raises(exc_type, fn):
        try:
            fn()
            return False
        except exc_type:
            return True

    # ---- normalize_amount ----
    check(E.normalize_amount("40,000万元") == Decimal("400000000"),
          "normalize_amount：40,000万元 → 4亿")
    check(E.normalize_amount("4亿元") == Decimal("400000000"),
          "normalize_amount：4亿元 → 4亿")
    check(E.normalize_amount("0万元") == Decimal("0"),
          "normalize_amount：0万元 → 0")
    check(E.normalize_amount("12.5%") == Decimal("12.5"),
          "normalize_amount：12.5% → 12.5")
    check(E.normalize_amount("1,234.56") == Decimal("1234.56"),
          "normalize_amount：千分位 + 小数")
    check(E.normalize_amount("abc") is None and E.normalize_amount("") is None,
          "normalize_amount：非法输入 → None")

    # ---- amounts_equivalent ----
    check(E.amounts_equivalent("40,000万元", "4亿元") is True,
          "amounts_equivalent：40,000万元 == 4亿元")
    check(E.amounts_equivalent("40,000万元", "40,000元") is False,
          "amounts_equivalent：40,000万元 != 40,000元")
    check(E.amounts_equivalent("0万元", "0元") is True,
          "amounts_equivalent：0万元 == 0元")

    # ---- value_presence ----
    vp = E.value_presence(H.Claim(claim_id="c1", text="授信额度40,000万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("授信额度4亿元")])
    check(vp.missing == [] and vp.matched,
          "value_presence：claim 40,000万元 与证据 4亿元 数值等价 → 无缺失")
    vp = E.value_presence(H.Claim(claim_id="c1", text="授信额度40,000万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("授信额度4万元")])
    check(vp.missing == ["40,000万元"],
          "value_presence：claim 40,000万元 与证据 4万元 不等价 → 缺失")
    vp = E.value_presence(H.Claim(claim_id="c1", text="实控人为曾毓群",
                                  kind="fact", citation_refs=[0]),
                          [_mat("实际控制人为曾毓群")])
    check(vp.missing == [] and vp.claim_amounts == [],
          "value_presence：无数字 claim → 无缺失")

    # ---- scope_risks ----
    risks = E.scope_risks(H.Claim(claim_id="c1", text="公司全部对外担保余额为0万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("为股东及实际控制人提供担保的余额为0万元")], "")
    high = [r for r in risks if r.severity == "high"]
    check(any(r.dimension == "guarantee_scope" for r in high),
          "scope_risks：全部对外担保 vs 为股东子项 → guarantee_scope high")
    risks = E.scope_risks(H.Claim(claim_id="c1", text="公司对外担保余额为0万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("公司对外担保余额为0万元")], "")
    check(not any(r.severity == "high" for r in risks),
          "scope_risks：口径一致 → 无 high")

    # ---- deterministic_prechecks ----
    # 未 inspect（引用证据不在 inspected_evidence）→ not_inspected。
    pc = E.deterministic_prechecks(_state(inspected={}), _answer("授信额度40,000万元"))
    check(pc["c1"]["not_inspected"] is True,
          "deterministic_prechecks：引用证据未捕获 → not_inspected")
    # 数字缺失。
    pc = E.deterministic_prechecks(
        _state(inspected={"e1": _mat("授信额度4万元")}), _answer("授信额度40,000万元"))
    check(pc["c1"]["not_inspected"] is False and pc["c1"]["value_missing"] is True,
          "deterministic_prechecks：数字在证据中未找到数值等价 → value_missing")
    # 高危口径。
    pc = E.deterministic_prechecks(
        _state(inspected={"e1": _mat("为股东及实际控制人提供担保的余额为0万元")}),
        _answer("公司全部对外担保余额为0万元"))
    check(pc["c1"]["high_risk_scope"] is True,
          "deterministic_prechecks：宽窄口径冲突 → high_risk_scope")
    # 非 evidence 引用 / inference claim 跳过。
    infer = _answer("研判", evidence_id="e1", kind="inference")
    pc = E.deterministic_prechecks(_state(inspected={}), infer)
    check(pc == {}, "deterministic_prechecks：inference claim 跳过")

    # ---- backfill_page_numbers ----
    ans = _answer("实控人为曾毓群", page_number=None)
    st = _state(inspected={"e1": _mat("实际控制人为曾毓群", page=3)})
    E.backfill_page_numbers(ans, st)
    check(ans.citations[0].page_number == 3,
          "backfill_page_numbers：缺失页码回填为 3")
    ans2 = _answer("实控人为曾毓群", page_number=5)
    E.backfill_page_numbers(ans2, st)
    check(ans2.citations[0].page_number == 5,
          "backfill_page_numbers：已有页码 5 不被重写")

    # ---- capture_inspected ----
    st = _state()
    E.capture_inspected(st, _tool_result("inspect_evidence", "SUCCESS", {
        "evidence_id": "e1", "document_id": "d1", "source_name": "s",
        "source_type": "annual_report", "page_number": 3, "section_path": "控制关系",
        "evidence_type": "paragraph", "report_period": "2024-12-31",
        "text": "实际控制人为曾毓群", "structured_payload": None}))
    check("e1" in st.inspected_evidence and st.inspected_evidence["e1"].is_snippet is False
          and st.inspected_evidence["e1"].text == "实际控制人为曾毓群"
          and st.inspected_evidence["e1"].page_number == 3,
          "capture_inspected：inspect_evidence 全文（is_snippet=False）")
    st2 = _state()
    E.capture_inspected(st2, _tool_result("search_evidence", "SUCCESS", {
        "evidence_count": 1, "items": [
            {"evidence_id": "e2", "source_name": "s", "page_number": 4,
             "evidence_type": "paragraph", "snippet": "摘要", "score": 0.8, "rank": 1}]}))
    check("e2" in st2.inspected_evidence and st2.inspected_evidence["e2"].is_snippet is True
          and st2.inspected_evidence["e2"].text == "摘要",
          "capture_inspected：search_evidence 摘要（is_snippet=True）")

    # ---- parse_entailment（批量法官输出解析） ----
    raw = json.dumps({"verdicts": [
        {"claim_id": "c1", "citation_ids": ["0"], "verdict": "SUPPORTED",
         "reason": "口径一致", "scope_consistency": "consistent",
         "period_consistency": "consistent", "unit_consistency": "consistent",
         "subject_consistency": "consistent"},
        {"claim_id": "c2", "citation_ids": ["1"], "verdict": "UNSUPPORTED",
         "reason": "只覆盖子项", "scope_consistency": "mismatch",
         "period_consistency": "unknown", "unit_consistency": "unknown",
         "subject_consistency": "unknown"},
    ]}, ensure_ascii=False)
    verdicts = E.parse_entailment(raw)
    check(len(verdicts) == 2
          and verdicts[0].claim_id == "c1" and verdicts[0].verdict == "SUPPORTED"
          and verdicts[0].scope_consistency == "consistent"
          and verdicts[1].verdict == "UNSUPPORTED"
          and verdicts[1].scope_consistency == "mismatch",
          "parse_entailment：SUPPORTED + UNSUPPORTED + 一致性字段")
    # 带 markdown 围栏 + 非法 verdict → fail-closed。
    fenced = "```json\n" + raw + "\n```"
    check(len(E.parse_entailment(fenced)) == 2,
          "parse_entailment：markdown 围栏可剥离")
    def _bad_verdict():
        E.parse_entailment(json.dumps({"verdicts": [
            {"claim_id": "c1", "verdict": "BOGUS"}]}))
    check(raises(ValueError, _bad_verdict),
          "parse_entailment：非法 verdict → ValueError（fail-closed）")

    # ---- evaluate_entailment_batch（单问 1 次调用） ----
    class _BatchLLM:
        def __init__(self, text):
            self._text = text
            self.calls = 0
        def evaluate_entailment_batch(self, prompt_vars):
            self.calls += 1
            self.last_vars = prompt_vars
            return SimpleNamespace(text=self._text)

    st_b = _state(inspected={"e1": _mat("授信额度4亿元", page=3)})
    ans_b = _answer("授信额度40,000万元")
    pc_b = E.deterministic_prechecks(st_b, ans_b)
    batch_llm = _BatchLLM(json.dumps({"verdicts": [
        {"claim_id": "c1", "citation_ids": ["0"], "verdict": "SUPPORTED",
         "reason": "数值等价", "scope_consistency": "consistent",
         "period_consistency": "consistent", "unit_consistency": "mismatch",
         "subject_consistency": "consistent"}]}, ensure_ascii=False))
    vlist = E.evaluate_entailment_batch(st_b, ans_b, batch_llm, pc_b)
    check(len(vlist) == 1 and vlist[0].verdict == "SUPPORTED"
          and vlist[0].unit_consistency == "mismatch"
          and batch_llm.calls == 1,
          "evaluate_entailment_batch：单问 1 次调用，返回 verdict")
    check("claims" in batch_llm.last_vars and "citations" in batch_llm.last_vars
          and "evidence" in batch_llm.last_vars and "required_aspects" in batch_llm.last_vars,
          "entailment_prompt_vars：含 claims/citations/evidence/required_aspects")
    check("授信额度4亿元" in batch_llm.last_vars["evidence"],
          "entailment_prompt_vars：证据正文进入 prompt")
    # llm 无 evaluate_entailment_batch（Mock）→ 跳过返回 []。
    class _NoMethodLLM:
        pass
    check(E.evaluate_entailment_batch(st_b, ans_b, _NoMethodLLM(), pc_b) == [],
          "evaluate_entailment_batch：llm 无方法 → 返回 []")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
