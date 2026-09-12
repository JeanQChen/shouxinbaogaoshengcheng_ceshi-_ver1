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

    # ---- retrieval_observation 不参与 entailment（诊断，非事实断言） ----
    st_obs = _state(inspected={"e1": _mat("授信额度4亿元", page=3)})
    obs_ans = H.ResearchAnswer(
        question_id="q1", answer_text="授信额度4亿元",
        claims=[H.Claim(claim_id="c1", text="授信额度4亿元", kind="fact",
                        citation_refs=[0]),
                H.Claim(claim_id="c2", text="本次检索未取得担保明细",
                        kind="retrieval_observation", citation_refs=[])],
        citations=[H.CitationRef(ref_type="evidence", evidence_id="e1")])
    vars_obs = E.entailment_prompt_vars(st_obs, obs_ans,
                                        E.deterministic_prechecks(st_obs, obs_ans))
    check("c2" not in vars_obs["claims"] and "c1" in vars_obs["claims"],
          "entailment_prompt_vars：retrieval_observation 不进入待判定 claims")
    check("本次检索未取得担保明细" in vars_obs["retrieval_observations"],
          "entailment_prompt_vars：retrieval_observation 进入检索观测上下文")

    # ---- canonical 归一化（金额统一到元、比例取数值；先换算再比较，Decimal 无 float）----
    check(E.normalize_amount("4亿元") == E.normalize_amount("40,000万元")
          == E.normalize_amount("400,000,000元"),
          "canonical：4亿元 == 40,000万元 == 400,000,000元")
    check(E.normalize_amount("4万元") != E.normalize_amount("4亿元"),
          "canonical：4万元 != 4亿元")
    check(E.normalize_amount("18.12%") == E.normalize_amount("18.12％")
          == E.normalize_amount("18.12 percent"),
          "canonical：18.12% == 18.12％ == 18.12 percent")
    check(E.normalize_amount("4千万元") == E.normalize_amount("40,000,000元"),
          "canonical：千万元长后缀优先（4千万元 == 40,000,000元）")

    # ---- units_compatible：金额↔比例、unknown↔有单位 跨类不可比 ----
    amt_wan = E.extract_amounts("18.12万元")[0]
    amt_pct = E.extract_amounts("18.12%")[0]
    amt_raw = E.extract_amounts("18.12")[0]
    check(amt_wan.canonical_kind == "money" and amt_pct.canonical_kind == "percent"
          and amt_raw.canonical_kind == "unknown",
          "Amount.canonical_kind：money/percent/unknown 三态")
    check(E.units_compatible(amt_wan, amt_pct) is False,
          "units_compatible：金额 vs 比例 → False")
    check(E.units_compatible(amt_wan, amt_raw) is False,
          "units_compatible：金额 vs unknown → False")
    check(E.units_compatible(amt_wan, amt_wan) is True,
          "units_compatible：金额 vs 金额 → True")

    # ---- 表头/列级单位上下文传播（Change 1 合成测试，公司无关）----
    # 1) 表级万元：表注「单位：万元」传播到裸数字 → 金额匹配（原 false-positive 消失）。
    vp = E.value_presence(H.Claim(claim_id="c1", text="主营业务收入31,650,636.9万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("表 5-10 主营业务收入构成表\n单位：万元\n项目          金额\n"
                                 "动力电池      31,650,636.9\n")])
    check(vp.missing == [] and vp.matched,
          "表级万元：表注单位传播 → 金额匹配（false-positive 消失）")
    # 2) 金额+百分比双单位：表注「单位：万元，%」按列对位，金额与占比各自匹配。
    mixed_ev = _mat("表 5-10 主营业务收入构成表\n单位：万元，%\n项目          金额          占比\n"
                    "动力电池      31,650,636.9  74.7\n储能          5,850,000.0   13.8\n")
    vp = E.value_presence(H.Claim(claim_id="c1", text="动力电池收入占比74.7%",
                                  kind="fact", citation_refs=[0]), [mixed_ev])
    check(vp.missing == [], "双单位：占比列 % 匹配")
    vp = E.value_presence(H.Claim(claim_id="c1", text="储能收入5,850,000.0万元",
                                  kind="fact", citation_refs=[0]), [mixed_ev])
    check(vp.missing == [], "双单位：金额列万元匹配")
    # 3) 列级单位覆盖表级：表注「单位：元」但列头「金额（万元）」→ 按列级万元。
    col_ev = _mat("单位：元\n项目          金额（万元）\n动力电池      31,650,636.9\n")
    vp = E.value_presence(H.Claim(claim_id="c1", text="主营业务收入31,650,636.9万元",
                                  kind="fact", citation_refs=[0]), [col_ev])
    check(vp.missing == [], "列级覆盖表级：金额（万元）优先于表注「单位：元」")
    # 4) 行内单位覆盖列级：列头万元但数字带内联「元」→ 按内联元。
    inline_ev = _mat("单位：万元\n项目          金额\n动力电池      31,650,636.9元\n")
    vp = E.value_presence(H.Claim(claim_id="c1", text="主营业务收入31,650,636.9元",
                                  kind="fact", citation_refs=[0]), [inline_ev])
    check(vp.missing == [], "行内覆盖列级：内联「元」优先于表注「万元」")
    vp = E.value_presence(H.Claim(claim_id="c1", text="主营业务收入31,650,636.9万元",
                                  kind="fact", citation_refs=[0]), [inline_ev])
    check(vp.missing == ["31,650,636.9万元"],
          "行内覆盖列级：同数字万元不再误匹配（证据实为元）")
    # 5) 单位缺失：证据裸数字无单位 → 与有单位 claim 不等价 → missing。
    vp = E.value_presence(H.Claim(claim_id="c1", text="授信额度40,000万元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("授信额度40,000")])
    check(vp.missing == ["40,000万元"],
          "单位缺失：证据裸数字与万元 claim 不等价 → missing")
    # 6) 同数字不同单位不等价：4万元 != 4亿元。
    vp = E.value_presence(H.Claim(claim_id="c1", text="授信额度4亿元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("授信额度4万元")])
    check(vp.missing == ["4亿元"], "同数字不同单位：4亿元 != 4万元")
    # 7) 多单位表头无法确定列归属 → PARTIAL（不误判 SUPPORTED）。
    part_ev = _mat("单位：万元，%\n项目          金额          占比\n动力电池      74.7\n")
    vp = E.value_presence(H.Claim(claim_id="c1", text="占比74.7%",
                                  kind="fact", citation_refs=[0]), [part_ev])
    check(vp.partial == ["74.7%"] and vp.missing == ["74.7%"],
          "多单位表头无法归列 → PARTIAL（不误判 SUPPORTED）")

    # ---- F1：共享业务数值提取（extract_claim_business_amounts，公司无关）----
    def tokens(text: str) -> list[str]:
        return [a.token for a in E.extract_claim_business_amounts(text)]

    def has_value(text: str, v: str) -> bool:
        return any(a.value == Decimal(v)
                   for a in E.extract_claim_business_amounts(text))

    # 年份区间（em-dash / 至）：整体剔除，不留裸年份。
    check(tokens("2023—2025年营收增长") == [],
          "F1 年份区间 em-dash：2023—2025年 → 无业务数值")
    check(tokens("2023至2025年营收增长") == [],
          "F1 年份区间「至」：2023至2025年 → 无业务数值")
    # 完整中文日期 / 数字日期 / 月日：全部剔除。
    check(tokens("截至2025年12月31日，资产为4亿元") == ["4亿元"],
          "F1 中文日期剔除：仅剩 4亿元")
    check(tokens("截至2025-12-31，资产为4亿元") == ["4亿元"],
          "F1 数字日期剔除：仅剩 4亿元")
    check(tokens("2024年度营收") == [],
          "F1 年份（2024年度）剔除")
    # 页码/条款/引用序号/序数排名：非业务，剔除。
    check(tokens("第5页、第3条、引用[2]") == [],
          "F1 页码/条款/引用序号：均非业务")
    check(tokens("公司为行业第2位") == [] and tokens("排名第1名") == [],
          "F1 序数/排名：第2位/第1名 → 非业务（排名语义交 entailment）")
    # 业务数量：保留。
    check(has_value("2位执行董事", "2"), "F1 数量保留：2位执行董事 → 保留 2")
    check(has_value("5家客户", "5"), "F1 数量保留：5家客户 → 保留 5")
    check(has_value("连续9年增长", "9"), "F1 数量保留：连续9年 → 保留 9")
    check(has_value("54,538项专利", "54538"), "F1 数量保留：54,538项专利 → 保留 54538")
    # 金额/比例带单位后缀：4 位数字不是年份，保留。
    check(has_value("2025万元", "20250000"), "F1 金额保留：2025万元（2025 非年份）")
    check(has_value("2025元", "2025"), "F1 金额保留：2025元")
    check(has_value("2025%", "2025"), "F1 比例保留：2025%")
    # 趋势句仅剩业务数值。
    check(tokens("2024年至2025年净利率由18.1%下降至17.3%") == ["18.1%", "17.3%"],
          "F1 趋势句：仅 18.1% / 17.3%")
    # value_presence 不再误报年份/日期为 value_missing。
    vp = E.value_presence(H.Claim(claim_id="c1", text="2023—2025年营业收入持续增长",
                                  kind="fact", citation_refs=[0]),
                          [_mat("2023—2025年营业收入逐年上升，2025年达4亿元")])
    check(vp.missing == [], "F1 value_presence：年份区间不触发 value_missing")
    vp = E.value_presence(H.Claim(claim_id="c1", text="截至2025年12月31日资产为4亿元",
                                  kind="fact", citation_refs=[0]),
                          [_mat("截至2025年12月31日资产4亿元")])
    check(vp.missing == [], "F1 value_presence：中文日期不触发 value_missing")

    # ---- F2：封闭集合/总数安全门（closed_set_guard，公司无关）----
    def guard(text: str, ev: str) -> E.ClosedSetGuard:
        return E.closed_set_guard(
            H.Claim(claim_id="c1", text=text, kind="fact", citation_refs=[0]),
            [_mat(ev)] if ev else [])

    g = guard("共2人", "甲、乙")
    check(g.triggered and g.verdict != "SUPPORTED",
          "F2 部分列表：证据仅列甲、乙，claim「共2人」不得 SUPPORTED")
    g = guard("共2人", "执行成员共2人：甲、乙")
    check(g.triggered and g.verdict == "SUPPORTED" and g.reason == "explicit_total_match",
          "F2 显式同口径总数：证据「共2人」匹配 → SUPPORTED")
    g = guard("共2人：甲、乙", "甲、乙、丙")
    check(g.triggered and g.verdict == "UNSUPPORTED"
          and g.reason == "incomplete_closed_set",
          "F2 漏报成员：证据甲、乙、丙 3 人 > claim 共2人 → incomplete_closed_set")
    g = guard("目前证据至少确认甲、乙两人，完整名单待核实", "甲、乙")
    check(g.triggered and g.verdict == "PARTIAL" and g.hedged,
          "F2 诚实降级：至少确认…待核实 → PARTIAL")
    g = guard("实控人为曾毓群", "实际控制人为曾毓群")
    check(g.triggered is False,
          "F2 非封闭集合事实不触发")
    check(has_value("共2人", "2"),
          "F2 总数数值不被 F1 过滤：共2人 → 保留 2")
    g = guard("共2人", "执行成员共3人：甲、乙、丙")
    check(g.triggered and g.verdict == "UNSUPPORTED" and g.reason == "total_mismatch",
          "F2 证据显式总数不等 → UNSUPPORTED total_mismatch")

    # ---- A4：外部快照正文进入 entailment 上下文 + 引用可读描述 ----
    # 审计根因：快照存在 ≠ 模型读到正文。external 引用此前无正文进入法官上下文，
    # 外部事实无法被支撑校验。修复后正文注入 evidence + 引用描述。
    st_ext = _state()
    st_ext.external_material["snap1"] = H.ExternalMaterial(
        source_snapshot_id="snap1", title="行业风险传导分析",
        content_text="外部正文：行业风险向公司传导，需关注下游需求")
    ans_ext = H.ResearchAnswer(
        question_id="q1", answer_text="行业风险传导",
        claims=[H.Claim(claim_id="c1", text="行业风险向公司传导", kind="fact",
                        citation_refs=[0])],
        citations=[H.CitationRef(ref_type="external", source_snapshot_id="snap1")])
    vars_ext = E.entailment_prompt_vars(
        st_ext, ans_ext, E.deterministic_prechecks(st_ext, ans_ext))
    check("external source_snapshot_id=snap1" in vars_ext["evidence"]
          and "外部正文：行业风险向公司传导" in vars_ext["evidence"],
          "A4：entailment 上下文注入外部快照正文")
    check("外部正文：行业风险向公司传导" in vars_ext["citations"],
          "A4：_describe_citation 对外部引用附正文片段")

    # ---- A5：收入/成本类别（成本不能被采纳为收入）----
    check(E.claim_revenue_cost_hint("2025年营业收入3165.06亿元") == "revenue",
          "A5 hint：营业收入 → revenue")
    check(E.claim_revenue_cost_hint("2025年营业成本2410.64亿元") == "cost",
          "A5 hint：营业成本 → cost")
    check(E.claim_revenue_cost_hint("营业收入3165亿、营业成本2410亿") is None,
          "A5 hint：同时含收入成本 → None（不猜）")
    check(E.claim_revenue_cost_hint("宁德时代") is None,
          "A5 hint：不含收入成本 → None")

    def _rc_answer(text: str, item_code: str) -> H.ResearchAnswer:
        return H.ResearchAnswer(
            question_id="q1", answer_text=text,
            claims=[H.Claim(claim_id="c1", text=text, kind="fact",
                            citation_refs=[0])],
            citations=[H.CitationRef(ref_type="structured", item_code=item_code,
                                     snapshot_id="S1", period="2025-12-31")])

    # 成本 item + claim 标收入 → mismatch。
    ans = _rc_answer("2025年营业收入2410.64亿元", "OPERATING_COST")
    rc = E.revenue_cost_precheck(ans.claims[0], ans)
    check(rc["mismatch"] is True and rc["hint"] == "revenue"
          and rc["categories"] == ["cost"],
          "A5 precheck：成本 item + claim 标收入 → mismatch")
    # 收入 item + claim 标收入 → match。
    ans = _rc_answer("2025年营业收入2410.64亿元", "OPERATING_REVENUE")
    rc = E.revenue_cost_precheck(ans.claims[0], ans)
    check(rc["mismatch"] is False and rc["hint"] == "revenue",
          "A5 precheck：收入 item + claim 标收入 → match")
    # 成本 item + claim 标成本 → match。
    ans = _rc_answer("2025年营业成本2410.64亿元", "OPERATING_COST")
    rc = E.revenue_cost_precheck(ans.claims[0], ans)
    check(rc["mismatch"] is False and rc["hint"] == "cost",
          "A5 precheck：成本 item + claim 标成本 → match")
    # 非收入/成本 item + claim 标收入 → categories=[other]，不 mismatch。
    ans = _rc_answer("2025年营业收入2410.64亿元", "TOTAL_ASSETS")
    rc = E.revenue_cost_precheck(ans.claims[0], ans)
    check(rc["mismatch"] is False and rc["categories"] == ["other"],
          "A5 precheck：非收入成本 item → [other]，不 mismatch")

    # deterministic_prechecks：混合引用（evidence + structured 成本）→ revenue_cost_mismatch 标记。
    st_rc = _state(inspected={"e1": _mat("营业收入2410.64亿元", page=3)})
    mixed = H.ResearchAnswer(
        question_id="q1", answer_text="2025年营业收入2410.64亿元",
        claims=[H.Claim(claim_id="c1", text="2025年营业收入2410.64亿元",
                        kind="fact", citation_refs=[0, 1])],
        citations=[H.CitationRef(ref_type="evidence", evidence_id="e1", page_number=3),
                   H.CitationRef(ref_type="structured", item_code="OPERATING_COST",
                                 snapshot_id="S1", period="2025-12-31")])
    pc_rc = E.deterministic_prechecks(st_rc, mixed)
    check(pc_rc["c1"]["revenue_cost_mismatch"] is True,
          "deterministic_prechecks：混合引用成本 item → revenue_cost_mismatch")
    vars_rc = E.entailment_prompt_vars(st_rc, mixed, pc_rc)
    check("revenue_cost_mismatch" in vars_rc["claims"],
          "entailment_prompt_vars：revenue_cost_mismatch 进入法官标记")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
