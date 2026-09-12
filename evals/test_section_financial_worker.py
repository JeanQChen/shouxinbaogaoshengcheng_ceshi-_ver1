"""Eval: Phase 4 Batch B — 财务章节 Worker（确定性流程 + 幂等 + 故障注入 + 原子提交）。

不调用真实 LLM / Embedding / Chroma / 互联网。LLM 用注入的确定性 fake（llm_generate），
financial_v2 / sections 库用临时文件。

覆盖（关闭前定点修复回归）：
A. 展示格式化 / 数字复核 / marker 解析（原有）。
B. Snapshot 权威校验（req 1）：非 current、validity=None/stale/superseded、scope/currency/
   purpose/as_of 不一致、report_blocked、quarantined → 全部 fail-closed。
C. run_task 端到端：完整覆盖 → COMPLETED；required 缺口 → COMPLETED_WITH_GAPS；
   Contract 覆盖缺口 → unresolved；伪造/跨 topic question_id → claim 拒绝；裸数字 fail-closed。
D. 依赖指纹进入 section_version（req 2）：同输入幂等；快照公式版本 / 契约依赖版本变化 → 新版本。
E. Store 加固（req 6）：非法 section_result_id / 同 id 不同 payload / 错 task/section /
   失败回滚不切 current。

用法: python -m evals.test_section_financial_worker
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planning import schema as PS  # noqa: E402
from sections import common as SC  # noqa: E402
from sections import financial_worker as FW  # noqa: E402
from sections import store as ST  # noqa: E402
from financial_v2 import schema as FS  # noqa: E402
from financial_v2 import store as FST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raise(fn, exc_type, msg: str) -> None:
    try:
        fn()
        check(False, f"{msg}（未抛异常）")
    except exc_type:
        check(True, msg)
    except Exception as e:  # noqa: BLE001
        check(False, f"{msg}（抛错类型不对: {type(e).__name__}: {e}）")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _task(task_id: str, plan_id: str, *, dependency_versions=None,
          questions=None, topic_ids=None) -> PS.SectionTask:
    return PS.SectionTask(
        task_id=task_id, plan_id=plan_id, section_id="financial",
        title="财务分析", purpose="credit_analysis", research_policy="workflow",
        topic_ids=topic_ids or ("fin_balance_structure", "fin_solvency"),
        questions=questions or (
            PS.PlannedQuestion("q_bal", "财务规模与结构如何", "high",
                               "fin_balance_structure", impact_scope=("key_financial",)),
            PS.PlannedQuestion("q_solv", "偿债能力如何", "high",
                               "fin_solvency", impact_scope=("solvency",)),
        ),
        output_requirements=(), evaluation_rule_ids=(), allowed_capabilities=(),
        blocking_rules=(), dependency_versions=dependency_versions or {},
    )


def _plan(plan_id: str, job_id: str) -> PS.ReportPlan:
    return PS.ReportPlan(
        plan_id=plan_id, job_id=job_id, company_id="300750", company_name="测试公司",
        credit_type="other", report_as_of="2025-12-31", template_id="standard_v2",
        input_fingerprint="fp", contract_fingerprint="fp_c",
        planner_version=PS.PLANNER_VERSION,
        section_tasks=(_task(PS.derive_task_id(plan_id, "financial"), plan_id),),
        created_at="2026-01-01T00:00:00Z",
    )


BASE_ITEMS = {
    "TOTAL_ASSETS": "1000", "TOTAL_LIABILITIES": "600", "TOTAL_EQUITY": "400",
    "CURRENT_ASSETS": "500", "CURRENT_LIABILITIES": "300",
    "TOTAL_REVENUE": "800", "OPERATING_COST": "500",
    "NET_PROFIT": "100", "OPERATING_CASH_FLOW": "120",
}


def _insert_snapshot(conn: sqlite3.Connection, snapshot_id: str, company_id: str,
                     as_of_date: str, items: dict[str, str], *,
                     scope: str = "consolidated", currency: str = "CNY",
                     purpose: str = "credit_analysis", validity: str | None = "valid",
                     report_blocked: bool = False,
                     required_formula_versions: str = "{}") -> None:
    conn.execute(
        "INSERT INTO financial_snapshot (snapshot_id, snapshot_version, company_id, "
        "as_of_date, scope, currency, purpose, source_versions, resolution_versions, "
        "created_at, record_set_ids, reconciliation_run_id, restatement_selection, "
        "policy_adjustments, required_formula_versions, snapshot_builder_version, "
        "admission_rule_version, report_blocked, admission_dependencies) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snapshot_id, "1.0", company_id, as_of_date, scope, currency, purpose,
         "[]", "[]", "2026-01-01T00:00:00Z", "[]", None, "{}", "[]",
         required_formula_versions, "1.0", "1.1", int(report_blocked), "{}"))
    for code, amount in items.items():
        conn.execute(
            "INSERT INTO snapshot_item (snapshot_id, comparison_key, standard_item_code, "
            "amount, unit, source_refs, resolution_id, amount_text, report_period, "
            "period_type, statement_type, statement_scope, currency, restatement_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot_id, f"ck_{code}_{as_of_date}", code, float(amount), "yuan", "[]",
             None, amount, as_of_date, "annual", "balance_sheet", scope, currency, "0"))
    if validity is not None:
        conn.execute(
            "INSERT INTO snapshot_validity (event_id, snapshot_id, status, invalidated_by, "
            "invalidated_reason, event_at) VALUES (?,?,?,?,?,?)",
            ("v-test-" + snapshot_id, snapshot_id, validity, None, None,
             "2026-01-01T00:00:00Z"))


def _set_current(conn: sqlite3.Connection, snapshot_id: str, company_id: str,
                 as_of_date: str, *, scope: str = "consolidated",
                 currency: str = "CNY", purpose: str = "credit_analysis") -> None:
    conn.execute(
        "INSERT INTO current_snapshot (company_id, scope, currency, as_of_date, purpose, "
        "snapshot_id, switched_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(company_id, scope, currency, as_of_date, purpose) DO UPDATE SET "
        "snapshot_id=excluded.snapshot_id, switched_at=excluded.switched_at",
        (company_id, scope, currency, as_of_date, purpose, snapshot_id,
         "2026-01-01T00:00:00Z"))


def _quarantine(conn: sqlite3.Connection, object_id: str) -> None:
    conn.execute(
        "INSERT INTO quarantine (quarantine_id, object_type, object_id, reason, quarantined_at) "
        "VALUES (?,?,?,?,?)",
        ("q-test-" + object_id, "financial_snapshot", object_id, "test",
         "2026-01-01T00:00:00Z"))


def _seed_financial_db(db_path: Path, company_id: str = "300750") -> None:
    FST.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _insert_snapshot(conn, "snap_test_1", company_id, "2025-12-31", BASE_ITEMS)
        _set_current(conn, "snap_test_1", company_id, "2025-12-31")
        _insert_snapshot(conn, "snap_test_2", company_id, "2025-12-31", {
            "TOTAL_ASSETS": "2000", "TOTAL_LIABILITIES": "900", "TOTAL_EQUITY": "1100",
            "CURRENT_ASSETS": "700", "CURRENT_LIABILITIES": "350",
            "TOTAL_REVENUE": "900", "OPERATING_COST": "550",
            "NET_PROFIT": "150", "OPERATING_CASH_FLOW": "160",
        })
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmpdir: Path) -> Path:
    fin_db = tmpdir / "fin.db"
    FST.init_db(fin_db)
    return fin_db


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _make_current(db_path: Path, snapshot_id: str, company_id: str = "300750",
                  as_of_date: str = "2025-12-31") -> None:
    c = _conn(db_path)
    try:
        _set_current(c, snapshot_id, company_id, as_of_date)
        c.commit()
    finally:
        c.close()


# LLM fakes（确定性；数字一律 [[fact_id]] 占位）
def _fake_valid(messages, system):  # noqa: ARG001
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact",
         "text": "公司总资产 [[item_TOTAL_ASSETS_2025-12-31]]，总负债 "
                 "[[item_TOTAL_LIABILITIES_2025-12-31]]，净资产 "
                 "[[item_TOTAL_EQUITY_2025-12-31]]。"},
        {"topic_id": "fin_solvency", "question_ids": ["q_solv"], "claim_type": "calculation",
         "text": "流动比率 [[metric_SOLV_CURRENT_RATIO_2025-12-31]]，资产负债率 "
                 "[[metric_SOLV_DEBT_RATIO_2025-12-31]]。"},
    ]})


def _fake_cover_one(messages, system):  # noqa: ARG001
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact",
         "text": "公司总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_forged_qid(messages, system):  # noqa: ARG001
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_ghost"],
         "claim_type": "fact",
         "text": "公司总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_cross_topic(messages, system):  # noqa: ARG001
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_solv"],
         "claim_type": "fact",
         "text": "公司总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_bare(messages, system):  # noqa: ARG001
    return json.dumps({"claims": [
        {"topic_id": "fin_solvency", "question_ids": ["q_solv"], "claim_type": "fact",
         "text": "资产负债率 60%，流动比率良好。"},
    ]})


def _fake_company_id_marker(messages, system):  # noqa: ARG001
    """证券代码用受信任 marker [[meta_company_id]]，不手写六位数字。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact",
         "text": "公司（证券代码 [[meta_company_id]]）总资产 "
                 "[[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_raw_stock_code(messages, system):  # noqa: ARG001
    """直接手写六位证券代码 300750 → 应被裸数字复核拒绝。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact",
         "text": "公司（证券代码 300750）总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_six_digit_amount(messages, system):  # noqa: ARG001
    """六位金额 300750万元 → 应被裸数字复核拒绝。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact",
         "text": "总资产 300750万元 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]})


def _fake_six_digit_percent(messages, system):  # noqa: ARG001
    """六位比例 300750% → 应被裸数字复核拒绝。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_solvency", "question_ids": ["q_solv"], "claim_type": "fact",
         "text": "占比 300750% [[metric_SOLV_DEBT_RATIO_2025-12-31]]。"},
    ]})


def _fake_meta_only(messages, system):  # noqa: ARG001
    """只有 [[meta_company_id]]、无任何 [[fact_id]] → 整条 claim 拒绝（不满足引用要求）。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "inference",
         "text": "证券代码 [[meta_company_id]]。"},
    ]})


# ---------------------------------------------------------------------------
# JSON 解析健壮性 fakes（模拟 DeepSeek 真实失败形状：object 末字段后尾逗号）
# ---------------------------------------------------------------------------

def _inject_trailing_commas(s: str) -> str:
    """在每个 object 末字段（字符串值）后的 ``}`` 前注入一个尾逗号。

    只处理紧邻 ``}`` 且前一字符是 ``"``（字符串值结束）的闭合处；不触碰字符串内逗号。
    产生 ``"text": "...", }`` 形非法 JSON，供「有限尾逗号规范化」修复验证。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "}" and i > 0 and s[i - 1] == '"':
            out.append(",")
        out.append(ch)
        i += 1
    return "".join(out)


def _fake_trailing_comma_valid(messages, system):  # noqa: ARG001
    return _inject_trailing_commas(_fake_valid(messages, system))


def _fake_trailing_comma_bad_marker(messages, system):  # noqa: ARG001
    return _inject_trailing_commas(json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_bal"],
         "claim_type": "fact", "text": "总资产 [[item_UNKNOWN_2025-12-31]]。"},
    ]}))


def _fake_trailing_comma_bare(messages, system):  # noqa: ARG001
    return _inject_trailing_commas(json.dumps({"claims": [
        {"topic_id": "fin_solvency", "question_ids": ["q_solv"], "claim_type": "fact",
         "text": "资产负债率 60%，流动比率良好。"},
    ]}))


def _fake_trailing_comma_forged_qid(messages, system):  # noqa: ARG001
    return _inject_trailing_commas(json.dumps({"claims": [
        {"topic_id": "fin_balance_structure", "question_ids": ["q_ghost"],
         "claim_type": "fact", "text": "总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
    ]}))


def _fake_bad_then_good():
    """第 1 次返回不可修复的非法 JSON（单引号），第 2 次返回合法 JSON。"""
    def _llm(messages, system):  # noqa: ARG001
        _llm.calls += 1
        if _llm.calls == 1:
            return "{'claims': []}"
        return _fake_valid(messages, system)

    _llm.calls = 0
    return _llm


def _fake_always_bad():
    """恒返回不可修复的非法 JSON（单引号 + 截断）。"""
    def _llm(messages, system):  # noqa: ARG001
        _llm.calls += 1
        return "{'claims': ["

    _llm.calls = 0
    return _llm


def main():
    # ---- A1. 展示格式化 ----
    check(SC.format_yuan_amount(Decimal("431015000000")) == "4,310.15亿元",
          "亿元格式化")
    check(SC.format_yuan_amount(Decimal("50000000")) == "5,000万元", "万元格式化")
    check(SC.format_yuan_amount(Decimal("1000")) == "1,000元", "元格式化")
    check(SC.format_metric_display("SOLV_DEBT_RATIO", Decimal("60.00")) == "60%",
          "百分比指标格式化")
    check(SC.format_metric_display("SOLV_CURRENT_RATIO", Decimal("1.67")) == "1.67",
          "比率指标格式化")

    # ---- A2. 数字复核 ----
    check(SC.bare_number_tokens("流动比率 [[metric_X_2025]] 良好") == [],
          "纯 marker 文本无裸数字")
    check(SC.bare_number_tokens("资产负债率 60%") != [], "手写数字被捕获")
    check(SC.bare_number_tokens("关注阈值 15%") == [], "政策常量 15% 放行")

    # ---- A3. marker 解析 ----
    resolved, errs = SC.resolve_markers("总资产 [[item_A_2025]]", {"item_A_2025": "1000元"})
    check(resolved == "总资产 1000元" and errs == [], "marker 解析替换")
    _, errs2 = SC.resolve_markers("总资产 [[item_UNKNOWN_2025]]", {})
    check(errs2 == ["unresolved_fact_id:item_UNKNOWN_2025"], "未知 fact_id fail-closed")

    # ---- B. Snapshot 权威校验（req 1）----
    tmpdir = Path(tempfile.mkdtemp(prefix="fin_worker_test_"))
    fin_db = _fresh_db(tmpdir)
    c = _conn(fin_db)
    try:
        _insert_snapshot(c, "snap_good", "300750", "2025-12-31", BASE_ITEMS)
        _set_current(c, "snap_good", "300750", "2025-12-31")
        # 非 current（valid 但指针指向别处）
        _insert_snapshot(c, "snap_noncurrent", "300750", "2025-12-31", BASE_ITEMS)
        # validity=None（无有效性事件）
        _insert_snapshot(c, "snap_noval", "300750", "2025-12-31", BASE_ITEMS, validity=None)
        # stale / superseded
        _insert_snapshot(c, "snap_stale", "300750", "2025-12-31", BASE_ITEMS, validity="stale")
        _insert_snapshot(c, "snap_sup", "300750", "2025-12-31", BASE_ITEMS, validity="superseded")
        # scope / currency / purpose 不一致
        _insert_snapshot(c, "snap_scope", "300750", "2025-12-31", BASE_ITEMS, scope="parent")
        _insert_snapshot(c, "snap_cur", "300750", "2025-12-31", BASE_ITEMS, currency="USD")
        _insert_snapshot(c, "snap_purpose", "300750", "2025-12-31", BASE_ITEMS, purpose="other")
        # report_blocked
        _insert_snapshot(c, "snap_blocked", "300750", "2025-12-31", BASE_ITEMS,
                         report_blocked=True)
        # quarantined
        _insert_snapshot(c, "snap_quar", "300750", "2025-12-31", BASE_ITEMS)
        _quarantine(c, "snap_quar")
        c.commit()
    finally:
        c.close()

    fin_db_s = str(fin_db)
    # 成功解析
    good = FW._resolve_snapshot("snap_good", company_id="300750", scope="consolidated",
                                currency="CNY", as_of_date="2025-12-31",
                                purpose="credit_analysis", fin_db=fin_db_s)
    check(good.snapshot_id == "snap_good", "权威快照成功解析")
    # 失败路径（均 fail-closed）
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_noncurrent", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "非 current 快照 fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_noval", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "validity=None fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_stale", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "validity=stale fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_sup", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "validity=superseded fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_scope", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "scope 不一致 fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_cur", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "currency 不一致 fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_purpose", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "purpose 不一致 fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_blocked", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "report_blocked fail-closed")
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_quar", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "quarantined fail-closed")
    # as_of_date 不一致（snap_good 是 2025-12-31，但请求 2024-12-31）
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_good", company_id="300750", scope="consolidated", currency="CNY",
        as_of_date="2024-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "as_of_date 不一致 fail-closed")
    # 公司归属不符
    expect_raise(lambda: FW._resolve_snapshot(
        "snap_good", company_id="600000", scope="consolidated", currency="CNY",
        as_of_date="2025-12-31", purpose="credit_analysis", fin_db=fin_db_s),
        FW.FinancialWorkerError, "公司归属不符 fail-closed")

    # ---- C. run_task 端到端（req 3/4/5）----
    fin_db2 = _fresh_db(tmpdir)
    sections_db = tmpdir / "sections.db"
    _seed_financial_db(fin_db2)

    plan = _plan("plan_w", "job_w")
    task = plan.section_tasks[0]

    # C1. 完整覆盖 → COMPLETED
    wr1 = FW.run_task(task, company_id="300750", company_name="测试公司",
                      snapshot_id="snap_test_1", fin_db=str(fin_db2),
                      llm_generate=_fake_valid)
    check(wr1.fact_count > 0, f"构建了财务事实（fact_count={wr1.fact_count}）")
    check(wr1.claim_count == 2, f"解析出 2 条 claim（got {wr1.claim_count}）")
    check(wr1.section_result.status == "COMPLETED",
          f"完整覆盖 status=COMPLETED（got {wr1.section_result.status}）")
    all_text = "".join(c.text for c in wr1.section_result.claims)
    check("[[" not in all_text, "claim 无残留占位")
    check("60%" in all_text, "资产负债率 60% 已注入")
    check("1.67" in all_text, "流动比率 1.67 已注入")
    check(wr1.section_result.section_result_id == "sr_" + wr1.section_result.section_version,
          "section_result_id 由 section_version 派生")
    for c in wr1.section_result.claims:
        if c.claim_type in ("fact", "calculation"):
            check(bool(c.citation_refs), "fact/calculation 有可回查引用")
    # coverage 报告含全部问题
    check(set(wr1.verification["coverage"]["covered_questions"]) == {"q_bal", "q_solv"},
          "coverage 报告覆盖全部问题")
    check(wr1.verification["coverage"]["uncovered_questions"] == [],
          "无未覆盖问题")

    # C2. required 指标缺口 → unresolved + COMPLETED_WITH_GAPS（req 4）
    c = _conn(fin_db2)
    try:
        _insert_snapshot(c, "snap_reqgap", "300750", "2025-12-31", BASE_ITEMS,
                         required_formula_versions='{"SOLV_QUICK_RATIO": "1.0"}')
        _set_current(c, "snap_reqgap", "300750", "2025-12-31")
        c.commit()
    finally:
        c.close()
    wr_gap = FW.run_task(task, company_id="300750", company_name="测试公司",
                         snapshot_id="snap_reqgap", fin_db=str(fin_db2),
                         llm_generate=_fake_valid)
    check(wr_gap.unresolved_count >= 1, f"required 缺口生成 unresolved（{wr_gap.unresolved_count}）")
    check(wr_gap.section_result.status == "COMPLETED_WITH_GAPS",
          f"required 缺口 status=COMPLETED_WITH_GAPS（got {wr_gap.section_result.status}）")
    quick_gap = [u for u in wr_gap.section_result.unresolved
                 if "SOLV_QUICK_RATIO" in u.reason_code or "速动比率" in u.detail]
    check(bool(quick_gap), "速动比率缺口 unresolved 存在")
    if quick_gap:
        check(quick_gap[0].question_id == "q_solv",
              f"required 缺口绑定到对应 question（got {quick_gap[0].question_id}）")
    check(wr_gap.verification["diagnostic_gaps"] >= 1,
          "非 required 扩展指标缺口只进 diagnostic（不影响状态）")
    _make_current(fin_db2, "snap_test_1")

    # C3. 覆盖缺口（漏答一个 question）→ unresolved（req 3）
    wr_one = FW.run_task(task, company_id="300750", company_name="测试公司",
                         snapshot_id="snap_test_1", fin_db=str(fin_db2),
                         llm_generate=_fake_cover_one)
    check(wr_one.section_result.status == "COMPLETED_WITH_GAPS",
          f"覆盖缺口 status=COMPLETED_WITH_GAPS（got {wr_one.section_result.status}）")
    check("q_solv" in wr_one.verification["coverage"]["uncovered_questions"],
          "coverage 报告标记 q_solv 未覆盖")
    cov_gap = [u for u in wr_one.section_result.unresolved if u.question_id == "q_solv"]
    check(bool(cov_gap), "覆盖缺口生成对应 unresolved")
    if cov_gap:
        check(cov_gap[0].state == "NOT_FOUND_AFTER_SEARCH",
              f"覆盖缺口状态 NOT_FOUND_AFTER_SEARCH（got {cov_gap[0].state}）")

    # C4. 伪造 question_id → claim 拒绝 → 覆盖缺口（req 3）
    wr_forged = FW.run_task(task, company_id="300750", company_name="测试公司",
                            snapshot_id="snap_test_1", fin_db=str(fin_db2),
                            llm_generate=_fake_forged_qid)
    check(any("伪造 question_id" in r for r in wr_forged.verification["rejected"]),
          "伪造 question_id 被拒绝")
    check(wr_forged.claim_count == 0, "伪造 question_id 无有效 claim")
    check(wr_forged.section_result.status == "COMPLETED_WITH_GAPS",
          "伪造 question_id → 全部未覆盖 → COMPLETED_WITH_GAPS")

    # C5. 跨 topic question_id → claim 拒绝（req 3）
    wr_cross = FW.run_task(task, company_id="300750", company_name="测试公司",
                           snapshot_id="snap_test_1", fin_db=str(fin_db2),
                           llm_generate=_fake_cross_topic)
    check(any("跨 topic" in r for r in wr_cross.verification["rejected"]),
          "跨 topic question_id 被拒绝")
    check(wr_cross.claim_count == 0, "跨 topic question_id 无有效 claim")

    # C6. 裸数字 → fail-closed（req 5 / LLM 不算数字）
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_bare),
                 FW.FinancialWorkerError, "LLM 手写数字 fail-closed")

    # C7. 快照与任务锁定不一致 → fail-closed
    task_locked = _task(task.task_id, "plan_w",
                        dependency_versions={"financial_snapshot_id": "snap_OTHER"})
    expect_raise(lambda: FW.run_task(task_locked, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_valid),
                 FW.FinancialWorkerError, "快照与任务锁定不一致 fail-closed")

    # C8. current 解析路径（snapshot_id=None）
    wr_cur = FW.run_task(task, company_id="300750", company_name="测试公司",
                         snapshot_id=None, fin_db=str(fin_db2), as_of_date="2025-12-31",
                         llm_generate=_fake_valid)
    check(wr_cur.section_result.section_result_id == wr1.section_result.section_result_id,
          "current 指针解析到 snap_test_1，与显式传入一致")

    # ---- C9-C14. 公司标识受信任 marker（修复 P1：证券代码不误判为手写数字）----
    check(SC.bare_number_tokens("证券代码 [[meta_company_id]]") == [],
          "[[meta_company_id]] 不算裸数字")
    check(SC.bare_number_tokens("证券代码 300750") != [],
          "直接手写 300750 仍被裸数字复核捕获")

    # C9. marker 形式证券代码通过 + 解析后正文正确
    wr_meta = FW.run_task(task, company_id="300750", company_name="测试公司",
                          snapshot_id="snap_test_1", fin_db=str(fin_db2),
                          llm_generate=_fake_company_id_marker)
    meta_text = "".join(c.text for c in wr_meta.section_result.claims)
    check(wr_meta.claim_count == 1, f"meta marker claim 通过（got {wr_meta.claim_count}）")
    check("证券代码 300750" in meta_text,
          "[[meta_company_id]] 解析为公司证券代码 300750")
    check("[[meta_company_id]]" not in meta_text,
          "marker 已替换，无残留占位")
    check(wr_meta.section_result.status == "COMPLETED_WITH_GAPS"
          or wr_meta.section_result.status == "COMPLETED",
          "meta marker 未触发 fail-closed")

    # C10. 直接手写六位证券代码 → fail-closed（不得因「数字附近有代码」放行）
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_raw_stock_code),
                 FW.FinancialWorkerError, "直接手写证券代码 300750 fail-closed")

    # C11. 六位金额 → fail-closed
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_six_digit_amount),
                 FW.FinancialWorkerError, "六位金额 300750万元 fail-closed")

    # C12. 六位比例 → fail-closed
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_six_digit_percent),
                 FW.FinancialWorkerError, "六位比例 300750% fail-closed")

    # C13. 只有 [[meta_company_id]] 无 [[fact_id]] → 整条拒绝（不满足引用要求）
    wr_meta_only = FW.run_task(task, company_id="300750", company_name="测试公司",
                               snapshot_id="snap_test_1", fin_db=str(fin_db2),
                               llm_generate=_fake_meta_only)
    check(wr_meta_only.claim_count == 0, "仅 meta marker 无 fact_id 的 claim 被拒绝")
    check(any("无 [[fact_id]] 引用" in r for r in wr_meta_only.verification["rejected"]),
          "rejection 消息明确为「无 [[fact_id]] 引用」")

    # C14. 证券代码 marker 不影响既有财务数字安全（回归：仍拒绝裸数字）
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_bare),
                 FW.FinancialWorkerError, "既有裸数字 fail-closed 不回归")

    # ---- D. 依赖指纹进入 section_version（req 2）----
    # D1. 同输入幂等
    wr2 = FW.run_task(task, company_id="300750", company_name="测试公司",
                      snapshot_id="snap_test_1", fin_db=str(fin_db2),
                      llm_generate=_fake_valid)
    check(wr2.section_result.section_version == wr1.section_result.section_version,
          "同输入幂等 → 同 section_version")

    # D2. 契约/任务依赖版本变化 → 新 section_version
    task_dep = _task(task.task_id, "plan_w",
                     dependency_versions={"contract_version": "v99"})
    wr_dep = FW.run_task(task_dep, company_id="300750", company_name="测试公司",
                         snapshot_id="snap_test_1", fin_db=str(fin_db2),
                         llm_generate=_fake_valid)
    check(wr_dep.section_result.section_version != wr1.section_result.section_version,
          "契约依赖版本变化 → 新 section_version")

    # D3. 快照公式版本变化 → 新 section_version
    c = _conn(fin_db2)
    try:
        _insert_snapshot(c, "snap_fv2", "300750", "2025-12-31", BASE_ITEMS,
                         required_formula_versions='{"SOLV_CURRENT_RATIO": "2.0"}')
        _set_current(c, "snap_fv2", "300750", "2025-12-31")
        c.commit()
    finally:
        c.close()
    wr_fv = FW.run_task(task, company_id="300750", company_name="测试公司",
                        snapshot_id="snap_fv2", fin_db=str(fin_db2),
                        llm_generate=_fake_valid)
    check(wr_fv.section_result.section_version != wr1.section_result.section_version,
          "快照公式版本变化 → 新 section_version")

    # ---- E. Store 加固（req 6）----
    ST.init_db(sections_db)
    ST.commit_plan(plan, run_id="run_w")
    r1 = ST.commit_section_result(wr1.section_result, run_id="run_w")
    check(r1.reused is False and r1.current_switched is True,
          "首次 commit 不 reuse 且切 current")
    check(ST.get_current_section(task.task_id) == wr1.section_result.section_result_id,
          "current_section 指向新产物")
    r2 = ST.commit_section_result(wr1.section_result, run_id="run_w2")
    check(r2.reused is True and r2.current_switched is False,
          "重复 commit 复用且不切 current")

    # E1. 非法 section_result_id（不派生自 version）→ conflict
    bad_id = dataclasses.replace(wr1.section_result, section_result_id="sr_NOTDERIVED")
    expect_raise(lambda: ST.commit_section_result(bad_id, run_id="run_x"),
                 ST.SectionStorageConflictError, "非法 section_result_id conflict")
    # E2. 同 id 不同 payload → corruption
    corrupt = dataclasses.replace(wr1.section_result, markdown="CORRUPTED")
    expect_raise(lambda: ST.commit_section_result(corrupt, run_id="run_x"),
                 ST.SectionStorageCorruptionError, "同 id 不同 payload corruption")
    # E3. 错 section_id → conflict
    wrong_section = dataclasses.replace(wr1.section_result, section_id="industry")
    expect_raise(lambda: ST.commit_section_result(wrong_section, run_id="run_x"),
                 ST.SectionStorageConflictError, "错 section_id conflict")
    # E4. 错 task → conflict
    wrong_task = dataclasses.replace(wr1.section_result, task_id="task_NONEXISTENT")
    expect_raise(lambda: ST.commit_section_result(wrong_task, run_id="run_x"),
                 ST.SectionStorageConflictError, "错 task_id conflict")
    # E5. 结构非法（claim_type=recommendation）→ conflict
    bad_claim = dataclasses.replace(
        wr1.section_result,
        claims=(dataclasses.replace(wr1.section_result.claims[0],
                                    claim_type="recommendation"),) + wr1.section_result.claims[1:])
    expect_raise(lambda: ST.commit_section_result(bad_claim, run_id="run_x"),
                 ST.SectionStorageConflictError, "结构非法 conflict")
    # E6. 失败后 current 不变（回滚）
    check(ST.get_current_section(task.task_id) == wr1.section_result.section_result_id,
          "全部失败后 current 不变（未切换）")

    # ---- F. JSON 解析健壮性（严格 → 有限尾逗号规范化 → 至多一次 regeneration）----
    # F1. 合法 JSON 严格解析，内容逐字段不变
    valid_raw = _fake_valid(None, None)
    payload_ok, aud_ok = FW._parse_llm_json(valid_raw)
    check(payload_ok == json.loads(valid_raw), "合法 JSON 严格解析内容逐字段不变")
    check(aud_ok["parse_mode"] == "strict", "合法 JSON parse_mode=strict")
    check(aud_ok["repaired_trailing_comma_count"] == 0, "合法 JSON 零尾逗号修复")
    check(aud_ok["first_error"] is None, "合法 JSON 无首次解析错误")

    # F2. 真实失败形状：多个 claim object 末字段尾逗号 → 确定性修复
    trailing_raw = _fake_trailing_comma_valid(None, None)
    payload_tc, aud_tc = FW._parse_llm_json(trailing_raw)
    check(payload_tc == json.loads(valid_raw), "尾逗号修复后内容与严格解析逐字段一致")
    check(aud_tc["parse_mode"] == "trailing_comma_repaired",
          "尾逗号 parse_mode=trailing_comma_repaired")
    check(aud_tc["repaired_trailing_comma_count"] == 2,
          f"恰好修复 2 个尾逗号（got {aud_tc['repaired_trailing_comma_count']}）")
    check(aud_tc["first_error"] is not None and "Expecting" in aud_tc["first_error"],
          "记录首次严格解析错误类型（JSONDecodeError）")

    # F3. 字符串正文中的逗号 / } / ] / 转义引号不被修改（状态机）
    emb = '{"a":"x,y","b":"p}q","c":"r]s","d":"esc\\"q"}'
    out_emb, n_emb = FW._repair_trailing_commas(emb)
    check(n_emb == 0 and out_emb == emb, "无尾逗号时字符串内逗号/括号/转义引号原样保留")
    emb_tc = '{"a":"x,y}","b":"p}q","d":"esc\\"q",}'
    out_emb_tc, n_emb_tc = FW._repair_trailing_commas(emb_tc)
    check(n_emb_tc == 1 and out_emb_tc == '{"a":"x,y}","b":"p}q","d":"esc\\"q"}',
          "仅删字符串外尾逗号，字符串内逗号/括号/转义引号不动")

    # F4. 尾逗号修复 → 下游产物（含解析后的数字/百分比/marker/中文）与严格解析完全一致
    _make_current(fin_db2, "snap_test_1")  # D3 把 current 切到了 snap_fv2，切回 snap_test_1
    wr_tc = FW.run_task(task, company_id="300750", company_name="测试公司",
                        snapshot_id="snap_test_1", fin_db=str(fin_db2),
                        llm_generate=_fake_trailing_comma_valid)
    check(wr_tc.section_result.section_result_id == wr1.section_result.section_result_id,
          "尾逗号修复后 section_result_id 与严格解析一致（数字/百分比/marker/中文全等）")
    check(wr_tc.verification["generation"]["parse_mode"] == "trailing_comma_repaired",
          "run_task 审计记录 parse_mode=trailing_comma_repaired")
    check(wr_tc.verification["generation"]["generation_attempts"] == 1,
          "尾逗号可修复 → 只调用一次生成（不重试）")
    check(wr_tc.verification["generation"]["regenerated"] is False,
          "尾逗号可修复 → regenerated=False")

    # F5. 单引号 / 未加引号 key / 缺失 value / 截断 → 不得被本地修复（fail-closed）
    expect_raise(lambda: FW._parse_llm_json("{'claims': []}"),
                 FW.FinancialWorkerError, "单引号 JSON 不修复")
    expect_raise(lambda: FW._parse_llm_json("{claims: []}"),
                 FW.FinancialWorkerError, "未加引号 key 不修复")
    expect_raise(lambda: FW._parse_llm_json('{"claims": [{"text": }]}'),
                 FW.FinancialWorkerError, "缺失 value 不修复")
    expect_raise(lambda: FW._parse_llm_json('{"claims": [{"topic_id": "fin_x", "text": "未完成'),
                 FW.FinancialWorkerError, "截断 JSON 不修复")

    # F6. 第一次不可修复、第二次合法 → 恰好两次生成并成功
    bad_then_good = _fake_bad_then_good()
    payload_btg, aud_btg = FW._generate_and_parse(
        bad_then_good, [{"role": "user", "content": "x"}], "s")
    check(aud_btg["generation_attempts"] == 2 and bad_then_good.calls == 2,
          "第一次不可修复 → 恰好两次生成")
    check(aud_btg["regenerated"] is True, "标记 regenerated=True")
    check(aud_btg["parse_mode"] == "strict", "第二次严格解析成功")
    check(payload_btg == json.loads(valid_raw), "第二次生成内容正确")

    # F7. 第一次尾逗号可修复 → 只调用一次，不进行多余重试
    trailing_once = _fake_trailing_comma_valid
    # 用计数 wrapper 确保只调用一次（不重试）
    def _once(messages, system):  # noqa: ARG001
        _once.calls += 1
        return trailing_once(messages, system)
    _once.calls = 0
    payload_once, aud_once = FW._generate_and_parse(
        _once, [{"role": "user", "content": "x"}], "s")
    check(aud_once["generation_attempts"] == 1 and _once.calls == 1,
          "尾逗号可修复 → 只调用一次")
    check(aud_once["parse_mode"] == "trailing_comma_repaired"
          and aud_once["regenerated"] is False,
          "尾逗号可修复 → 不重试")

    # F8. 两次均非法 → 恰好两次后 fail-closed，不产出 SectionResult / 不切 current
    always_bad = _fake_always_bad()
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=always_bad),
                 FW.FinancialWorkerError, "两次均非法 → fail-closed")
    check(always_bad.calls == 2, f"恰好两次生成后 fail-closed（got {always_bad.calls}）")
    check(ST.get_current_section(task.task_id) == wr1.section_result.section_result_id,
          "两次非法 fail-closed 后 current 不变（无 SectionResult 落地）")

    # F9. 语法修复成功但非法 marker / 手写数字 / 非法 question_id → 仍被下游校验拒绝
    wr_bad_marker = FW.run_task(task, company_id="300750", company_name="测试公司",
                                snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                llm_generate=_fake_trailing_comma_bad_marker)
    check(wr_bad_marker.claim_count == 0
          and any("未知/未解析 fact_id" in r for r in wr_bad_marker.verification["rejected"]),
          "尾逗号修复 + 非法 marker → claim 拒绝")
    expect_raise(lambda: FW.run_task(task, company_id="300750", company_name="测试公司",
                                     snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                     llm_generate=_fake_trailing_comma_bare),
                 FW.FinancialWorkerError, "尾逗号修复 + 手写数字 → fail-closed")
    wr_forged_qid = FW.run_task(task, company_id="300750", company_name="测试公司",
                                snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                llm_generate=_fake_trailing_comma_forged_qid)
    check(wr_forged_qid.claim_count == 0
          and any("伪造 question_id" in r for r in wr_forged_qid.verification["rejected"]),
          "尾逗号修复 + 非法 question_id → claim 拒绝")

    # F10. Worker 版本变化 → 新 dependency fingerprint + 新 section_result_id
    check(SC.WORKER_VERSION == "p4-fin-worker-v2",
          f"Worker 版本已升级为 v2（got {SC.WORKER_VERSION}）")
    snap_obj = FW._resolve_snapshot("snap_test_1", company_id="300750",
                                    scope="consolidated", currency="CNY",
                                    as_of_date="2025-12-31", purpose="credit_analysis",
                                    fin_db=str(fin_db2))
    fp_base = FW._dependency_fingerprint(snap_obj, task)
    old_wv = SC.WORKER_VERSION
    SC.WORKER_VERSION = old_wv + "-bumped"
    try:
        fp_bumped = FW._dependency_fingerprint(snap_obj, task)
        wr_bumped = FW.run_task(task, company_id="300750", company_name="测试公司",
                                snapshot_id="snap_test_1", fin_db=str(fin_db2),
                                llm_generate=_fake_valid)
        check(fp_bumped != fp_base, "Worker 版本进入 dependency fingerprint")
        check(wr_bumped.section_result.section_result_id
              != wr1.section_result.section_result_id,
              "Worker 版本变化 → 新 section_result_id")
    finally:
        SC.WORKER_VERSION = old_wv

    # ---- G. 近三年期间主线（§12 P1：_select_focus_periods，纯函数）----
    def _item(report_period: str, period_type: str) -> FS.SnapshotItem:
        return FS.SnapshotItem(
            snapshot_id="s", comparison_key=f"ck_{report_period}_{period_type}",
            standard_item_code="OPERATING_REVENUE", amount=Decimal("1"), unit="yuan",
            report_period=report_period, period_type=period_type,
            statement_type="income_statement", statement_scope="consolidated",
            currency="CNY", restatement_version="0", source_refs=["rec_x"],
            resolution_id=None)

    # G1. 三年年度 + 一季 → 年度主线完整 + 季度独立补充
    items = [
        _item("2023-12-31", "annual"), _item("2024-12-31", "annual"),
        _item("2025-12-31", "annual"), _item("2025-09-30", "quarterly"),
    ]
    per, note = FW._select_focus_periods(items, as_of_date="2025-12-31")
    check(per == ["2023-12-31", "2024-12-31", "2025-09-30", "2025-12-31"],
          "近三年年度 + 季度 → 4 期间（季度独立，不与年度同比）")
    check(note["full_three_years"] is True and note["annual_years"] == 3,
          "三年度 → full_three_years=True")
    check(note["sub_annual_supplement"] == "2025-09-30", "季度独立补充标记")

    # G2. 五年年度 → 取最近三年
    items5 = [_item(f"{y}-12-31", "annual") for y in range(2021, 2026)]
    per5, note5 = FW._select_focus_periods(items5, as_of_date="2025-12-31")
    check(per5 == ["2023-12-31", "2024-12-31", "2025-12-31"],
          "五年年度 → 取最近三年")
    check(note5["full_three_years"] is True, "五年 → full_three_years=True")

    # G3. 两年年度 → 不足三年诚实说明，不编数
    items2 = [_item("2024-12-31", "annual"), _item("2025-12-31", "annual")]
    per2, note2 = FW._select_focus_periods(items2, as_of_date="2025-12-31")
    check(per2 == ["2024-12-31", "2025-12-31"], "两年年度 → 只取两年")
    check(note2["full_three_years"] is False and note2["annual_years"] == 2,
          "两年 → full_three_years=False（诚实声明不足三年，不编数）")

    # G4. 主报告期为季度（as_of_date 非年度）→ 主报告期始终纳入
    items_q = [_item("2024-12-31", "annual"), _item("2025-12-31", "annual"),
               _item("2025-09-30", "quarterly")]
    per_q, _ = FW._select_focus_periods(items_q, as_of_date="2025-09-30")
    check("2025-09-30" in per_q, "主报告期（季度）纳入焦点期间")

    # G5. 空条目 → fail-closed
    expect_raise(lambda: FW._select_focus_periods([], as_of_date="2025-12-31"),
                 FW.FinancialWorkerError, "无条目 → fail-closed")

    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
