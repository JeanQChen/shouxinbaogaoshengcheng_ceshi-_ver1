"""Eval: Section Contracts（Phase 0B）契约加载、校验、阻断语义与 41 问覆盖。

不调用 LLM / Embedding / Chroma / 互联网。

覆盖：
1. 标准 V2 契约加载 + 校验成功。
2. 缺失字段、重复 ID、空必答主题、非法枚举、未知条件操作符、无效跨引用失败。
3. 项目分析误入第一阶段失败；财务声明 LLM 计算失败；综合声明生成额度/允许外部搜索失败。
4. 20% 阈值回归失败，15% 通过。
5. 授信类型正确追加且不启用项目章节（含 other 通用契约）。
6. 41 问映射引用不存在的 case/question 失败。
7. 复核表稳定生成并包含所有必答问题；SC 决策由配置驱动，未确认显示“待业务确认”。
8. 通用性：契约不得写死到具体公司/行业/业务。
9. 12 个合成（不依赖具体公司）阻断场景 + 复合阻断语义 + CONFLICT 遵循声明策略 + 加载幂等性。
10. SC 决策失败关闭（空/缺项/重复/未知/非法状态/空字段）与 Phase 0B 关闭判定。

用法: python -m evals.test_contracts
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contracts import schema as S
from contracts.blocking import blocking_label, classify_blocking, classify_question
from contracts.loader import load_contracts, parse_contracts
from contracts.review import (
    build_review_matrix,
    load_sc_decisions,
    render_review_markdown,
    resolve_contracts,
    sc_status_label,
    validate_mapping,
)
from contracts.validator import validate_contracts

STD = ROOT / "templates" / "contracts" / "standard_v2.yaml"
BASELINE = ROOT / "evaluation" / "datasets" / "v1_baseline.jsonl"

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def fresh() -> list[S.SectionContract]:
    return load_contracts(str(STD))


def find_q(contracts: list[S.SectionContract], qid: str) -> S.KeyQuestion:
    for sec in contracts:
        for q in sec.all_questions():
            if q.question_id == qid:
                return q
    raise KeyError(qid)


def synth_q(qid: str, blocking: list[str] | str | None = None,
            missing: str = "write_not_found") -> S.KeyQuestion:
    if blocking is None:
        levels: list[str] = []
    elif isinstance(blocking, str):
        levels = [] if blocking == "NONE" else [blocking]
    else:
        levels = list(blocking)
    return S.KeyQuestion(
        question_id=qid, question="合成通用问题", priority="P1",
        evidence_requirements=[], blocking_policy=levels, missing_policy=missing,
    )


def sc_conf(*blocks: str) -> str:
    """把若干 YAML SC item 块拼成完整配置文本。"""
    return "version: v1\nitems:\n" + "".join(blocks)


def sc_block(sid: str, status: str = "confirmed",
             question: str = "问题", decision: str = "决策") -> str:
    return (f"  - id: {sid}\n    status: {status}\n"
            f"    question: {question}\n    decision: {decision}\n")


def load_sc(text: str) -> list[dict[str, str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        p = f.name
    try:
        return load_sc_decisions(p)
    finally:
        Path(p).unlink(missing_ok=True)


def raises_contains(fn, substr: str, msg: str) -> None:
    try:
        fn()
        check(False, msg + "（未抛错）")
    except ValueError as e:
        check(substr in str(e), f"{msg}（实际: {e}）")


def main() -> dict:
    # ---------------------------------------------------------------
    # 1. 标准契约加载 + 校验成功
    # ---------------------------------------------------------------
    try:
        cs = fresh()
        check([c.section_id for c in cs] == S.SECTION_ORDER, "四章顺序正确，不含项目分析")
        r = validate_contracts(cs)
        check(r.valid, f"标准契约校验通过（否则: {r.errors[:3]}）")
    except Exception as e:  # noqa: BLE001
        check(False, f"标准契约加载崩溃: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------
    # 2. 加载幂等性
    # ---------------------------------------------------------------
    a = fresh()
    b = fresh()
    check(a == b, "重复加载结果一致（幂等）")

    # ---------------------------------------------------------------
    # 3. 通用性：不得写死到具体公司/行业/业务
    # ---------------------------------------------------------------
    raw = STD.read_text(encoding="utf-8")
    for tok in ["宁德", "曾毓群", "动力电池", "储能", "换电", "募书", "年报P", "300750", "SNE"]:
        check(tok not in raw, f"契约不得写死到具体公司/行业/业务: {tok!r}")

    # ---------------------------------------------------------------
    # 4. 校验失败：缺失字段（loader 抛错）
    # ---------------------------------------------------------------
    bad_missing_field = """
contract_version: v1
policies:
  missing:
    - {policy_id: write_not_found, description: 未检索到}
sections:
  - section_id: company
    title: t
    purpose: p
    research_policy: harness
    allowed_capabilities: []
    required_topics:
      - topic_id: t1
        title: x
        required: true
        key_questions:
          - question_id: q1
            priority: P0
            blocking_policy: NONE
            missing_policy: write_not_found
"""
    try:
        parse_contracts(bad_missing_field)
        check(False, "缺失 question 字段应抛错")
    except Exception:  # noqa: BLE001
        check(True, "缺失 question 字段抛错")

    # ---------------------------------------------------------------
    # 5. 校验失败：空必答主题 / 重复 ID / 非法枚举 / 无效跨引用
    # ---------------------------------------------------------------
    cs = fresh()
    cs[0].required_topics[0].key_questions = []
    r = validate_contracts(cs)
    check(not r.valid and any("必答主题无 KeyQuestion" in e for e in r.errors), "空必答主题失败")

    cs = fresh()
    cs[0].required_topics[0].key_questions[0].question_id = \
        cs[0].required_topics[0].key_questions[1].question_id
    r = validate_contracts(cs)
    check(not r.valid and any("question_id 重复" in e for e in r.errors), "重复 question_id 失败")

    cs = fresh()
    cs[0].required_topics[0].key_questions[0].priority = "P9"
    r = validate_contracts(cs)
    check(not r.valid and any("priority 非法" in e for e in r.errors), "非法 priority 失败")

    cs = fresh()
    cs[0].required_topics[0].key_questions[0].evidence_requirements[0].evidence_kind = "bogus"
    r = validate_contracts(cs)
    check(not r.valid and any("evidence_kind 非法" in e for e in r.errors), "非法 evidence_kind 失败")

    cs = fresh()
    cs[0].completion_rules[0].scope_id = "nonexistent_scope"
    r = validate_contracts(cs)
    check(not r.valid and any("scope_id 引用不存在" in e for e in r.errors), "无效跨引用失败")

    cs = fresh()
    cs[0].required_topics[0].key_questions[0].impact_scope = ["bogus_scope"]
    r = validate_contracts(cs)
    check(not r.valid and any("impact_scope 非法" in e for e in r.errors), "非法 impact_scope 失败")

    # ---------------------------------------------------------------
    # 6. 未知条件操作符（loader 抛错）
    # ---------------------------------------------------------------
    bad_cond = """
contract_version: v1
policies:
  missing:
    - {policy_id: write_not_found, description: 未检索到}
conditions:
  - condition_id: c1
    kind: credit_type_in
    field: credit_type
    op: bogus_op
    value: [other]
sections:
  - section_id: company
    title: t
    purpose: p
    research_policy: harness
    allowed_capabilities: []
    required_topics:
      - topic_id: t1
        title: x
        required: true
        applies_when: {ref: c1}
        key_questions: []
"""
    try:
        parse_contracts(bad_cond)
        check(False, "未知条件操作符应抛错")
    except ValueError as e:
        check("未知条件操作符" in str(e), "未知条件操作符抛错")

    # ---------------------------------------------------------------
    # 7. 项目分析误入第一阶段
    # ---------------------------------------------------------------
    cs = fresh()
    cs.append(S.SectionContract(contract_version="v1", section_id="project",
                                title="项目分析", purpose="", required_topics=[]))
    r = validate_contracts(cs)
    check(not r.valid and any("第一阶段章节集合/顺序错误" in e for e in r.errors),
          "项目分析误入第一阶段失败")

    # ---------------------------------------------------------------
    # 8. 财务声明 LLM 计算
    # ---------------------------------------------------------------
    cs = fresh()
    fin = next(s for s in cs if s.section_id == "financial")
    fin.required_topics[0].key_questions[0].analysis_requirements.append("由模型计算")
    r = validate_contracts(cs)
    check(not r.valid and any("财务问题声明由 LLM 计算" in e for e in r.errors),
          "财务声明 LLM 计算失败")

    # ---------------------------------------------------------------
    # 9. 综合声明生成额度 / 允许外部搜索
    # ---------------------------------------------------------------
    cs = fresh()
    syn = next(s for s in cs if s.section_id == "synthesizer")
    syn.allowed_capabilities.append("search_external_sources")
    r = validate_contracts(cs)
    check(not r.valid and any("综合章节不得声明检索" in e for e in r.errors),
          "综合章节允许外部搜索失败")

    cs = fresh()
    syn = next(s for s in cs if s.section_id == "synthesizer")
    syn.required_topics[0].key_questions[0].question += " 设计授信额度"
    r = validate_contracts(cs)
    check(not r.valid and any("综合章节声明生成新授信方案" in e for e in r.errors),
          "综合章节声明生成额度失败")

    # ---------------------------------------------------------------
    # 10. 20% 阈值回归失败，15% 通过
    # ---------------------------------------------------------------
    cs = fresh()
    fin = next(s for s in cs if s.section_id == "financial")
    fin.required_topics[2].key_questions[0].question += "（重大科目阈值 20%）"
    r = validate_contracts(cs)
    check(not r.valid and any("20%" in e for e in r.errors), "20% 阈值回归失败")

    cs = fresh()
    r = validate_contracts(cs)
    check(r.valid, "标准契约（15% 阈值）校验通过")
    check("15%" in raw and "20%" not in raw, "契约声明 15% 且不含 20% 旧值")

    # ---------------------------------------------------------------
    # 11. 授信类型正确追加，且不启用项目章节；other 走通用契约
    # ---------------------------------------------------------------
    resolved_wc = resolve_contracts(fresh(), "working_capital")
    fin = next(r for r in resolved_wc if r.section_id == "financial")
    applied = {t.topic_id for t in fin.topics if t.applies}
    check("fin_working_capital_needs" in applied, "流动资金贷款追加流动资金需求测算")
    check("fin_trade_finance_focus" not in applied, "流动资金贷款不追加贸易融资")
    check("fin_capital_capacity" not in applied, "流动资金贷款不追加固定资产承载能力")

    resolved_other = resolve_contracts(fresh(), "other")
    fin_other = next(r for r in resolved_other if r.section_id == "financial")
    not_applied = {t.topic_id for t in fin_other.topics if not t.applies}
    check({"fin_working_capital_needs", "fin_trade_finance_focus", "fin_capital_capacity"}
          <= not_applied, "other 不启用任何授信类型追加")
    check(all(r.enabled for r in resolved_other), "other 仍启用全部四章")

    # ---------------------------------------------------------------
    # 12. 41 问映射引用不存在的 case/question 失败
    # ---------------------------------------------------------------
    cs = fresh()
    bad_mappings = [
        S.BaselineContractMapping("COMP-S1", ["nonexistent_qid"], "full", ""),
        S.BaselineContractMapping("NOPE", ["company_identity_basic"], "full", ""),
    ]
    errs = validate_mapping(cs, {"COMP-S1"}, bad_mappings)
    check(len(errs) == 2, f"映射引用不存在 case/question 失败（{len(errs)} 处）")

    # ---------------------------------------------------------------
    # 13. 复核表稳定生成并包含所有必答问题；SC 决策由配置驱动
    # ---------------------------------------------------------------
    cs = fresh()
    matrix = build_review_matrix(cs, str(BASELINE))
    total_q = sum(len(s.question_ids()) for s in cs)
    check(len(matrix.rows) == total_q, f"复核表覆盖全部问题（{len(matrix.rows)}/{total_q}）")
    md = render_review_markdown(cs, matrix)
    for sc in ["SC-01", "SC-02", "SC-03", "SC-04", "SC-05"]:
        check(sc in md, f"复核表含 {sc}")
    check("COMP-MV1" in matrix.out_of_scope, "总市值 case 标记 out_of_scope")
    synth_qids = {q for s in cs if s.section_id == "synthesizer" for q in s.question_ids()}
    check(synth_qids <= set(matrix.uncovered), "综合章节全部问题未覆盖（41问无综合 case）")

    # SC 决策由配置驱动：默认 confirmed，未确认显示“待业务确认”
    decs = load_sc_decisions()
    check([d["id"] for d in decs] == ["SC-01", "SC-02", "SC-03", "SC-04", "SC-05"],
          "SC 决策含 SC-01～SC-05")
    check(all(d["status"] == "confirmed" for d in decs), "SC-01～SC-05 均已确认")
    check(sc_status_label("confirmed") == "已确认", "confirmed → 已确认")
    check(sc_status_label("pending") == "待业务确认", "pending → 待业务确认")
    md_confirmed = render_review_markdown(cs, matrix)
    check("全部已确认" in md_confirmed and "Phase 0B 关闭" in md_confirmed,
          "五项全确认时显示 Phase 0B 已关闭")
    md_pending = render_review_markdown(
        cs, matrix, sc_decisions=[{**d, "status": "pending"} for d in decs])
    check("待业务确认" in md_pending and "Phase 0B 关闭" not in md_pending,
          "存在 pending 时不得显示 Phase 0B 已关闭")

    # SC 决策失败关闭：空 / 缺项 / 重复 / 未知 / status 非法 / question 或 decision 为空
    raises_contains(lambda: load_sc(sc_conf()),
                    "缺少 items 列表或为空", "SC 决策为空失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-01"), sc_block("SC-03"), sc_block("SC-04"), sc_block("SC-05"))),
        "缺少以下项", "缺少任意 SC 失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-01"), sc_block("SC-01"), sc_block("SC-02"),
        sc_block("SC-03"), sc_block("SC-04"), sc_block("SC-05"))),
        "SC ID 重复", "SC ID 重复失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-99"), sc_block("SC-01"), sc_block("SC-02"),
        sc_block("SC-03"), sc_block("SC-04"), sc_block("SC-05"))),
        "未知 SC ID", "未知 SC ID 失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-01", "bogus"), sc_block("SC-02"), sc_block("SC-03"),
        sc_block("SC-04"), sc_block("SC-05"))),
        "status 非法", "status 非法失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-01", question=""), sc_block("SC-02"), sc_block("SC-03"),
        sc_block("SC-04"), sc_block("SC-05"))),
        "question 为空", "question 为空失败关闭")
    raises_contains(lambda: load_sc(sc_conf(
        sc_block("SC-01", decision=""), sc_block("SC-02"), sc_block("SC-03"),
        sc_block("SC-04"), sc_block("SC-05"))),
        "decision 为空", "decision 为空失败关闭")

    # ---------------------------------------------------------------
    # 14. 12 个合成（不依赖具体公司）阻断场景 + 复合阻断语义
    # ---------------------------------------------------------------
    # (1) 无实际控制人 → 合法 SATISFIED，不阻断；控制关系无法确认 → REPORT_BLOCKED
    q_control = synth_q("company_control_chain", ["REPORT_BLOCKED"], "valid_no_controller")
    check(classify_blocking(q_control, "SATISFIED") == [], "无实际控制人(SATISFIED)→NONE")
    check(classify_blocking(q_control, "WAITING_HUMAN") == ["REPORT_BLOCKED"],
          "控制关系无法确认(WAITING_HUMAN)→REPORT_BLOCKED")

    # (2) 主体与材料不一致 → JOB_BLOCKED（基础前提错误）
    q_subject = synth_q("company_subject_match", ["JOB_BLOCKED"])
    check(classify_blocking(q_subject, "NOT_PROVIDED") == ["JOB_BLOCKED"],
          "主体不一致→JOB_BLOCKED")

    # (3) 主营业务完全无法确认 → SECTION_BLOCKED
    q_biz = synth_q("company_business_main", ["SECTION_BLOCKED"])
    check(classify_blocking(q_biz, "NOT_FOUND_AFTER_SEARCH") == ["SECTION_BLOCKED"],
          "主营业务完全无法确认→SECTION_BLOCKED")

    # (4) 缺最新完整年度任一主表 → 复合 SECTION_BLOCKED + REPORT_BLOCKED
    q_stmt = synth_q("fin_statements_availability", ["SECTION_BLOCKED", "REPORT_BLOCKED"])
    check(classify_blocking(q_stmt, "NOT_PROVIDED") == ["SECTION_BLOCKED", "REPORT_BLOCKED"],
          "缺最新完整年度任一主表→SECTION_BLOCKED+REPORT_BLOCKED（复合）")

    # (5) 财务期间/单位/合并口径不明 → 复合 SECTION_BLOCKED + REPORT_BLOCKED
    q_audit = synth_q("fin_audit_opinion", ["SECTION_BLOCKED", "REPORT_BLOCKED"])
    check(classify_blocking(q_audit, "NOT_PROVIDED") == ["SECTION_BLOCKED", "REPORT_BLOCKED"],
          "财务期间/单位/合并口径不明→SECTION_BLOCKED+REPORT_BLOCKED")

    # (6) 财务关键数字冲突 → REPORT_BLOCKED（暂停受影响计算，禁止导出）
    q_conflict = synth_q("fin_consistency", ["REPORT_BLOCKED"])
    check(classify_blocking(q_conflict, "CONFLICT") == ["REPORT_BLOCKED"],
          "财务冲突→REPORT_BLOCKED")

    # (6b) 主体一致性冲突 → JOB_BLOCKED（不被通用分类器降级为财务冲突）
    q_subject_conflict = synth_q("company_subject_match", ["JOB_BLOCKED"])
    check(classify_blocking(q_subject_conflict, "CONFLICT") == ["JOB_BLOCKED"],
          "主体一致性冲突→JOB_BLOCKED")

    # (6c) 复合阻断策略在 CONFLICT 下完整保留
    q_comp_conflict = synth_q("fin_statements_availability",
                              ["SECTION_BLOCKED", "REPORT_BLOCKED"])
    check(classify_blocking(q_comp_conflict, "CONFLICT")
          == ["SECTION_BLOCKED", "REPORT_BLOCKED"],
          "复合阻断冲突完整保留")

    # (6d) 非关键问题 CONFLICT 遵循自身 blocking_policy，不被全局强制升级
    q_noncrit = synth_q("company_rd_capacity", [])
    check(classify_blocking(q_noncrit, "CONFLICT") == [],
          "非关键问题冲突不强制升级")

    # (7) 客户名称依法未披露 → NOT_FOUND_AFTER_SEARCH，不阻断（非事实不存在）
    q_cust = synth_q("company_customer_concentration", [], "write_not_found_disclose")
    check(classify_blocking(q_cust, "NOT_FOUND_AFTER_SEARCH") == [],
          "客户名称依法未披露→NONE")

    # (8) 行业只能代理指标 → SATISFIED 不阻断；缺单一行业数字 → 不阻断
    q_scale = synth_q("industry_scale_cycle", [], "proxy_allowed")
    check(classify_blocking(q_scale, "SATISFIED") == [], "行业代理指标(SATISFIED)→NONE")
    check(classify_blocking(q_scale, "NOT_FOUND_AFTER_SEARCH") == [],
          "缺单一行业数字→NONE（不机械阻断）")

    # (9) 可比公司不足/不存在 → 不阻断（3~5 家是目标不是门禁）
    q_comp = synth_q("industry_comparables", [])
    check(classify_blocking(q_comp, "NOT_FOUND_AFTER_SEARCH") == [],
          "可比公司不足/不存在→NONE（不因数量不足阻断）")

    # (10) other 授信类型走通用契约（已在 #11 覆盖）

    # (11) 非核心 WAITING_HUMAN 不阻断无关判断
    q_rd = synth_q("company_rd_capacity", [])
    check(classify_blocking(q_rd, "WAITING_HUMAN") == [],
          "非核心研发问题 WAITING_HUMAN→NONE（不阻断无关判断）")

    # (12) 关键 WAITING_HUMAN 阻止受影响结论及导出
    q_debt = synth_q("company_debt_credit", ["REPORT_BLOCKED"])
    check(classify_blocking(q_debt, "WAITING_HUMAN") == ["REPORT_BLOCKED"],
          "关键债务问题 WAITING_HUMAN→REPORT_BLOCKED（阻止导出）")

    # 复合阻断的布尔化语义
    info = classify_question(q_stmt, "NOT_PROVIDED")
    check(info["blocks_section"] and info["blocks_export"] and not info["pauses_job"],
          "复合阻断 blocks_section+blocks_export，不 pauses_job")
    check(blocking_label([]) == "NONE", "空阻断集合渲染为 NONE")
    check(blocking_label(["SECTION_BLOCKED", "REPORT_BLOCKED"]) == "SECTION_BLOCKED + REPORT_BLOCKED",
          "复合阻断渲染为 A + B")

    # 回归：真实契约的阻断边界符合已确认决策
    cs = fresh()
    comp = next(s for s in cs if s.section_id == "company")
    comp_blocking = {q.question_id: q.blocking_policy for q in comp.all_questions()}
    check(comp_blocking.get("company_subject_match") == ["JOB_BLOCKED"],
          "公司：主体不一致→JOB_BLOCKED")
    check(comp_blocking.get("company_control_chain") == ["REPORT_BLOCKED"],
          "公司：控制关系无法确认→REPORT_BLOCKED")
    check(comp_blocking.get("company_business_main") == ["SECTION_BLOCKED"],
          "公司：主营业务无法确认→SECTION_BLOCKED")
    check(comp_blocking.get("company_debt_credit") == ["REPORT_BLOCKED"],
          "公司：债务无法核实→REPORT_BLOCKED")
    check(comp_blocking.get("company_debt_guarantee") == ["REPORT_BLOCKED"],
          "公司：对外担保无法核实→REPORT_BLOCKED")
    blocking_company = {qid for qid, blk in comp_blocking.items() if blk}
    check(blocking_company == {"company_subject_match", "company_control_chain",
                               "company_business_main", "company_debt_credit",
                               "company_debt_guarantee"},
          f"公司：仅这五个问题阻断，实际 {sorted(blocking_company)}")

    fin = next(s for s in cs if s.section_id == "financial")
    fin_blocking = {q.question_id: q.blocking_policy for q in fin.all_questions()}
    check(fin_blocking.get("fin_statements_availability") == ["SECTION_BLOCKED", "REPORT_BLOCKED"],
          "财务：缺主表→复合阻断")
    check(fin_blocking.get("fin_audit_opinion") == ["SECTION_BLOCKED", "REPORT_BLOCKED"],
          "财务：口径不明→复合阻断")
    check(fin_blocking.get("fin_consistency") == ["REPORT_BLOCKED"],
          "财务：冲突→REPORT_BLOCKED")
    blocking_fin = {qid for qid, blk in fin_blocking.items() if blk}
    check(blocking_fin == {"fin_statements_availability", "fin_audit_opinion", "fin_consistency"},
          f"财务：仅主表/口径/冲突阻断，实际 {sorted(blocking_fin)}")

    ind = next(s for s in cs if s.section_id == "industry")
    ind_blocking = {q.question_id: q.blocking_policy for q in ind.all_questions()}
    check(all(blk == [] for blk in ind_blocking.values()),
          "行业：单一问题不机械阻断（整体不足才 SECTION_BLOCKED）")

    # 影响范围（SC-04 结构化依赖标签）
    comp_impact = {q.question_id: q.impact_scope for q in comp.all_questions()}
    check("subject" in comp_impact.get("company_subject_match", []),
          "主体一致问题 impact_scope=subject")
    check("solvency" in comp_impact.get("company_debt_credit", []),
          "债务问题 impact_scope=solvency")
    fin_impact = {q.question_id: q.impact_scope for q in fin.all_questions()}
    check("key_financial" in fin_impact.get("fin_statements_availability", []),
          "主表问题 impact_scope=key_financial")
    check(set(fin_impact.get("fin_consistency", [])) >= {"key_financial", "solvency"},
          "财务冲突影响关键数字与偿债")

    # 行业来源分级 / 代理指标 / 整体不足声明存在
    ind_cr = " ".join(cr.outcome for cr in ind.completion_rules)
    check("A/B/C/D" in ind_cr or "A=监管" in ind_cr, "行业声明来源 A/B/C/D 分级")
    check("代理指标" in ind_cr, "行业声明代理指标六项")
    check("整体不足" in ind_cr, "行业声明整体不足才 SECTION_BLOCKED")

    # 综合声明 impact_scope 影响面判断 + other 提示
    syn = next(s for s in cs if s.section_id == "synthesizer")
    syn_cr = " ".join(cr.outcome for cr in syn.completion_rules)
    check("impact_scope" in syn_cr, "综合声明按 impact_scope 判断影响面")
    check("尚未按具体授信业务类型追加专项分析" in syn_cr, "综合声明 other 未追加专项提示")

    # 缺失策略语义完整性
    pols = {mp.policy_id: mp.description for s in cs for mp in s.missing_policies}
    check("不视为事实不存在" in pols.get("write_not_found", ""), "write_not_found 不视为事实不存在")
    check("依法未披露" in pols.get("write_not_found_disclose", ""), "依法未披露策略存在")
    check("无实际控制人" in pols.get("valid_no_controller", ""), "无实际控制人=合法结论")
    check("代理指标" in pols.get("proxy_allowed", ""), "代理指标策略存在")
    check("暂停受影响" in pols.get("conflict_pause", ""), "冲突只暂停受影响计算/Claim")

    # JOB_BLOCKED 仅限主体前提
    cs = fresh()
    cs[0].required_topics[1].key_questions[0].blocking_policy = ["JOB_BLOCKED"]
    r = validate_contracts(cs)
    check(not r.valid and any("JOB_BLOCKED 仅限主体前提" in e for e in r.errors),
          "非主体问题使用 JOB_BLOCKED 失败")

    # conflict_pause 必须明确包含 REPORT_BLOCKED（仅空/SECTION/JOB 均不满足）
    def fin_consistency_blocking(blocking: list[str]) -> tuple[bool, list[str]]:
        cs = fresh()
        find_q(cs, "fin_consistency").blocking_policy = list(blocking)
        r = validate_contracts(cs)
        return r.valid, r.errors

    for bad in ([], ["SECTION_BLOCKED"], ["JOB_BLOCKED"]):
        ok, errs = fin_consistency_blocking(bad)
        check(not ok and any("conflict_pause" in e for e in errs),
              f"conflict_pause + {bad} → 校验失败")
    for good in (["REPORT_BLOCKED"], ["SECTION_BLOCKED", "REPORT_BLOCKED"]):
        ok, errs = fin_consistency_blocking(good)
        check(ok, f"conflict_pause + {good} → 校验通过")

    return _results


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
