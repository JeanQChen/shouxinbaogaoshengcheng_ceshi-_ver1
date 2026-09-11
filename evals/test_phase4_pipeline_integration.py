"""Eval: Phase 4 三章节编排级离线 dry-run（真实 ``run_phase4`` 服务入口，15 条断言）。

只 mock 不稳定边界（LLM 返回固定合法 JSON；外部检索/快照用预建临时库；embedding/检索
由 monkeypatch ``harness.runtime.run_question`` 绕过，不触真实 Chroma / 网络 / LLM）：

- 真实 ``sections.service.run_phase4``（load_contracts → planner.plan → Store DDL/迁移 →
  ``_prepare_stores`` → ``build_route_context`` → ``build_citation_authority`` → 三章节
  Worker → ``evaluate_section_and_rework``（真实 Rules Evaluator + 注入 LLM Evaluator）
  → ``commit_section_result`` → ``build_manifest`` → ``commit_manifest``）；
- 真实 Evidence / Financial V2 / External Snapshot / Sections SQLite（相对路径 + chdir 临时目录）；
- 真实 CitationAuthority（evidence / structured / external 三类只读校验）；
- 真实审计意见 enrichment（确定性五字段抽取，不触发 LLM）；
- 真实行业 source policy（A 级来源 + published_at → key conclusion 保留）。

覆盖关闭前定点修复回归：
- 审计意见只读连接相对路径（``_ro_conn`` 经 ``expanduser().resolve()``）；
- ResearchAnswer → SectionClaim 边界折叠重复 CitationRef（industry_position 重复证据引用
  → 1 条；industry_supply_demand 证据+外部 → 2 条）。

用法: python -m evals.test_phase4_pipeline_integration
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidence import store as estore  # noqa: E402
from external_v2 import schema as XS  # noqa: E402
from external_v2 import store as extstore  # noqa: E402
from financial_v2 import formulas as fformulas  # noqa: E402
from financial_v2 import metrics as fmetrics  # noqa: E402
from financial_v2 import store as FST  # noqa: E402
from harness import runtime as harness_runtime  # noqa: E402
from harness import schema as HS  # noqa: E402
from planning import schema as PS  # noqa: E402
from sections import service as SV  # noqa: E402
from sections import store as sstore  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


# ---------------------------------------------------------------------------
# fixtures（复制自 evals.test_section_financial_worker.py 的已证 seeding）
# ---------------------------------------------------------------------------

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


def _seed_financial_db(db_path: Path, company_id: str = "TESTCO") -> None:
    FST.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _insert_snapshot(conn, "snap_test_1", company_id, "2025-12-31", BASE_ITEMS)
        _set_current(conn, "snap_test_1", company_id, "2025-12-31")
        conn.commit()
    finally:
        conn.close()


def _seed_evidence_db(db_path: Path, company_id: str = "TESTCO") -> None:
    estore.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    audit_text = ("我们审计了测试公司合并财务报表。"
                  "安永华明会计师事务所（特殊普通合伙）出具了标准无保留意见。"
                  "截至 2025年12月31日。单位：万元。")
    try:
        conn.execute(
            "INSERT INTO documents (company_id, document_id, document_version, "
            "source_name, source_path, source_type, material_group, file_sha256, "
            "file_size, page_count, declared_company_name, detected_company_names, "
            "parser_version, status, quality_flags, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (company_id, "doc_1", "dv1", "2025年度报告.pdf", None, "pdf",
             "annual_report", "sha_doc1", 1234, 10, "测试公司", "[]",
             "1.0", "current", "[]", "2026-01-01T00:00:00Z"))
        conn.execute(
            "INSERT INTO evidence_sets (company_id, document_id, document_version, "
            "evidence_set_version, dependency_versions, status, block_count, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (company_id, "doc_1", "dv1", "sv1", "{}", "current", 1,
             "2026-01-01T00:00:00Z"))
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, "
            "source_type, source_uri, page_number, block_index, section_path, "
            "evidence_type, text, structured_payload, report_period, published_at, "
            "entities, quality_flags, content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ev_1", "1", company_id, "doc_1", "dv1", "sv1", "2025年度报告.pdf",
             "annual_report", None, 3, 0, "[]", "paragraph", audit_text, None,
             "2025-12-31", None, "[]", "[]", "h1", "1.0",
             "2026-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def _seed_external_db(db_path: Path) -> None:
    extstore.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    body = "正文内容，非 URL 非 snippet"
    try:
        conn.execute(
            "INSERT INTO source_snapshots (source_snapshot_id, company_id, canonical_url, "
            "original_url, provider, query, title, snippet, published_at, fetched_at, "
            "content_type, http_status, content_text, content_hash, source_grade, "
            "content_version, status, error_code, retrieval_metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("snap_1", "", "https://stats.gov.cn/x", "https://stats.gov.cn/x",
             "web", "q", "某政府网", "摘要", "2025-06-01", "2025-06-01",
             "text/html", 200, body, XS.content_hash(body), "A", 1, "SNAPSHOTTED",
             None, "{}", "2025-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 注入 fakes（仅不稳定边界；确定性 / 编排 / 校验全部走真实实现）
# ---------------------------------------------------------------------------

def _fake_financial_llm(messages, system):  # noqa: ARG001
    """财务 Worker LLM：10 个非条件主题各一条 fact claim，全部用 [[item_*]] 占位（无裸数字）。"""
    return json.dumps({"claims": [
        {"topic_id": "fin_source_scope", "question_ids": ["fin_statements_availability"],
         "claim_type": "fact",
         "text": "最新年度三张主表齐备，总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
        {"topic_id": "fin_consistency", "question_ids": ["fin_consistency"],
         "claim_type": "fact",
         "text": "三张报表勾稽一致，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                 "总负债 [[item_TOTAL_LIABILITIES_2025-12-31]]。"},
        {"topic_id": "fin_balance_structure", "question_ids": ["fin_balance_structure"],
         "claim_type": "fact",
         "text": "资产规模 [[item_TOTAL_ASSETS_2025-12-31]]，"
                 "负债 [[item_TOTAL_LIABILITIES_2025-12-31]]，"
                 "净资产 [[item_TOTAL_EQUITY_2025-12-31]]。"},
        {"topic_id": "fin_solvency", "question_ids": ["fin_solvency"],
         "claim_type": "fact",
         "text": "偿债能力方面，流动资产 [[item_CURRENT_ASSETS_2025-12-31]]，"
                 "流动负债 [[item_CURRENT_LIABILITIES_2025-12-31]]。"},
        {"topic_id": "fin_profitability", "question_ids": ["fin_profitability"],
         "claim_type": "fact",
         "text": "营业收入 [[item_TOTAL_REVENUE_2025-12-31]]，"
                 "净利润 [[item_NET_PROFIT_2025-12-31]]。"},
        {"topic_id": "fin_operating", "question_ids": ["fin_operating"],
         "claim_type": "fact",
         "text": "营运效率方面，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                 "营业收入 [[item_TOTAL_REVENUE_2025-12-31]]。"},
        {"topic_id": "fin_cashflow", "question_ids": ["fin_cashflow"],
         "claim_type": "fact",
         "text": "经营现金流净额 [[item_OPERATING_CASH_FLOW_2025-12-31]]，"
                 "净利润 [[item_NET_PROFIT_2025-12-31]]。"},
        {"topic_id": "fin_growth", "question_ids": ["fin_growth"],
         "claim_type": "fact",
         "text": "成长性方面，营业收入 [[item_TOTAL_REVENUE_2025-12-31]]。"},
        {"topic_id": "fin_asset_quality", "question_ids": ["fin_asset_quality"],
         "claim_type": "fact",
         "text": "资产质量方面，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                 "流动资产 [[item_CURRENT_ASSETS_2025-12-31]]。"},
        {"topic_id": "fin_risk_summary", "question_ids": ["fin_risk_summary"],
         "claim_type": "fact",
         "text": "整体财务风险可控，净资产 [[item_TOTAL_EQUITY_2025-12-31]]。"},
    ]})


def _fake_evaluator(messages, system):  # noqa: ARG001
    """LLM Evaluator：规则全过后返回 PASS，无 issue / 无返工目标。"""
    return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})


def _inject_trailing_commas(s: str) -> str:
    """在每个 object 末字段（字符串值）后的 ``}`` 前注入一个尾逗号。

    产生 ``"text": "...", }`` 形非法 JSON（DeepSeek 真实失败形状），供 Financial Worker
    的「有限尾逗号规范化」在完整编排路径中修复。
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


def _fake_financial_llm_trailing_comma(messages, system):
    """财务 Worker LLM：与 _fake_financial_llm 同内容，但注入 object 末字段尾逗号。"""
    return _inject_trailing_commas(_fake_financial_llm(messages, system))


def _make_rework_financial_llm():
    """stateful 财务 LLM：fin_solvency 用两个 [[metric_*]] 占位（同 claim 两条结构化引用）。

    - 第 1 次调用（全量 Worker）：claim 正文「流动比率 [[metric_SOLV_CURRENT_RATIO_..]]，
      资产负债率 [[metric_SOLV_DEBT_RATIO_..]]」→ 两条 formula 引用在 formula_definition 空时
      各自 emit `citation:formula_not_found` → 同一 (claim, 原因) 重复 ReworkTarget。
    - 第 2 次调用（定向返工重跑）：正文追加「（已复核）」→ 新 claim_id → 新 SectionResult。
    每次调用返回全新闭包，保证同输入重复执行时序列确定性一致（供幂等断言）。
    """
    calls = {"n": 0}

    def _llm(messages, system):  # noqa: ARG001
        calls["n"] += 1
        suffix = "" if calls["n"] == 1 else "（已复核）"
        return json.dumps({"claims": [
            {"topic_id": "fin_source_scope", "question_ids": ["fin_statements_availability"],
             "claim_type": "fact",
             "text": "最新年度三张主表齐备，总资产 [[item_TOTAL_ASSETS_2025-12-31]]。"},
            {"topic_id": "fin_consistency", "question_ids": ["fin_consistency"],
             "claim_type": "fact",
             "text": "三张报表勾稽一致，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                     "总负债 [[item_TOTAL_LIABILITIES_2025-12-31]]。"},
            {"topic_id": "fin_balance_structure", "question_ids": ["fin_balance_structure"],
             "claim_type": "fact",
             "text": "资产规模 [[item_TOTAL_ASSETS_2025-12-31]]，"
                     "负债 [[item_TOTAL_LIABILITIES_2025-12-31]]，"
                     "净资产 [[item_TOTAL_EQUITY_2025-12-31]]。"},
            {"topic_id": "fin_solvency", "question_ids": ["fin_solvency"],
             "claim_type": "calculation",
             "text": "偿债能力方面，流动比率 [[metric_SOLV_CURRENT_RATIO_2025-12-31]]，"
                     "资产负债率 [[metric_SOLV_DEBT_RATIO_2025-12-31]]" + suffix + "。"},
            {"topic_id": "fin_profitability", "question_ids": ["fin_profitability"],
             "claim_type": "fact",
             "text": "营业收入 [[item_TOTAL_REVENUE_2025-12-31]]，"
                     "净利润 [[item_NET_PROFIT_2025-12-31]]。"},
            {"topic_id": "fin_operating", "question_ids": ["fin_operating"],
             "claim_type": "fact",
             "text": "营运效率方面，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                     "营业收入 [[item_TOTAL_REVENUE_2025-12-31]]。"},
            {"topic_id": "fin_cashflow", "question_ids": ["fin_cashflow"],
             "claim_type": "fact",
             "text": "经营现金流净额 [[item_OPERATING_CASH_FLOW_2025-12-31]]，"
                     "净利润 [[item_NET_PROFIT_2025-12-31]]。"},
            {"topic_id": "fin_growth", "question_ids": ["fin_growth"],
             "claim_type": "fact",
             "text": "成长性方面，营业收入 [[item_TOTAL_REVENUE_2025-12-31]]。"},
            {"topic_id": "fin_asset_quality", "question_ids": ["fin_asset_quality"],
             "claim_type": "fact",
             "text": "资产质量方面，总资产 [[item_TOTAL_ASSETS_2025-12-31]]，"
                     "流动资产 [[item_CURRENT_ASSETS_2025-12-31]]。"},
            {"topic_id": "fin_risk_summary", "question_ids": ["fin_risk_summary"],
             "claim_type": "fact",
             "text": "整体财务风险可控，净资产 [[item_TOTAL_EQUITY_2025-12-31]]。"},
        ]})

    return _llm


def _db_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _make_fake_run_question():
    """monkeypatch ``harness.runtime.run_question``：对任意公司/行业问题返回 COMPLETED。

    - industry_position：引用 [ev_1, ev_1]（重复证据引用 → 边界折叠为 1 条）；
    - industry_supply_demand：引用 [ev_1, snap_1]（证据 + 外部 → 2 条）；
    - 其余：引用 [ev_1]（单一证据引用）。
    """
    EV1 = HS.CitationRef(ref_type="evidence", evidence_id="ev_1", page_number=3)
    EXT1 = HS.CitationRef(ref_type="external", source_snapshot_id="snap_1")

    def _run(need=None, route_result=None, registry=None, llm=None, budget=None,
             run_id="", case_id="", company_id="TESTCO", section_id="",
             trace_enabled=True, context=None, **kwargs):  # noqa: ARG001
        nid = need.need_id if need is not None else "?"
        question = (need.question if need is not None else None) or "合成问题"

        if nid == "industry_position":
            citations = [EV1]
            claims = [HS.Claim(claim_id=f"{nid}_c1",
                               text="公司在行业中的地位突出（离线合成证据）。",
                               kind="fact", citation_refs=[0, 0])]
            ext_sids: list[str] = []
        elif nid == "industry_supply_demand":
            citations = [EV1, EXT1]
            claims = [HS.Claim(claim_id=f"{nid}_c1",
                               text="行业供需关系稳定（离线合成证据）。",
                               kind="fact", citation_refs=[0, 1])]
            ext_sids = ["snap_1"]
        else:
            citations = [EV1]
            claims = [HS.Claim(claim_id=f"{nid}_c1",
                               text="研究结论（离线合成证据）。",
                               kind="fact", citation_refs=[0])]
            ext_sids = []

        state = HS.ResearchState(
            run_id=run_id or "r", case_id=case_id or "c", question_id=nid,
            company_id=company_id, section_id=section_id, original_question=question,
            need=need)
        state.evidence_ids = ["ev_1"]
        state.external_snapshot_ids = ext_sids

        answer = HS.ResearchAnswer(
            question_id=nid, answer_text="离线合成答案", claims=claims,
            citations=citations, unresolved_items=[], confidence="high")

        return HS.ResearchOutcome(
            state=state, answer=answer, success=True,
            completion_status="COMPLETED", stop_reason="COMPLETED")

    return _run


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _sec(res, sid: str):
    return next(o for o in res.sections if o.section_id == sid)


def _run_rework_scenario(tmp: Path, contracts_abs: Path) -> None:
    """REWORK 触发 + 去重 + 定向返工原子持久化 + 幂等 + 公式补齐后结构化引用通过的 dry-run。

    独立子目录，避免与 happy-path 库串扰；全程真实 ``run_phase4``（相对库路径 + chdir）。
    """
    sub = tmp / "rework"
    sub.mkdir()
    _seed_financial_db(sub / "fin.db", company_id="TESTCO")   # 无 formula_definition（计数 0）
    # 官方指标计算入口落盘 metric_result（不落 formula_definition）—— 真实 Demo 主链产物。
    # 仅落 metric_result：场景 A 仍走 formula_not_found fail-closed；场景 C 补齐公式后
    # metric_get + formula_get 双通过 → Structured Citation 权威。
    fmetrics.compute_all("snap_test_1", periods=["2025-12-31"], persist=True)
    _seed_evidence_db(sub / "ev.db", company_id="TESTCO")
    _seed_external_db(sub / "ext.db")

    old_cwd = os.getcwd()
    try:
        os.chdir(str(sub))
        cfg = SV.ServiceConfig(
            contracts_path=str(contracts_abs),
            fin_db="fin.db", ev_db="ev.db", ext_db="ext.db",
            harness_db="harness.db", section_db="sections.db",
            scope="consolidated", currency="CNY", purpose="credit_analysis",
            model="offline-test", external_research_enabled=True,
            audit_dir=None, checkpoint=False)
        job = PS.ReportJobInput(
            job_id="job_rework", company_id="TESTCO", company_name="测试公司",
            credit_type="other", report_as_of="2025-12-31", template_id="standard_v2",
            enabled_sections=("company", "financial", "industry"),
            evidence_inventory_fingerprint="", financial_snapshot_id="snap_test_1")

        # --- 场景 A：formula_definition 缺失 → REWORK，重复 target 稳定去重 ---
        harness_runtime.run_question = _make_fake_run_question()
        res = SV.run_phase4(
            job, service_cfg=cfg, run_id="run_rework",
            llm_generate=_make_rework_financial_llm(),
            llm_evaluator_generate=_fake_evaluator, audit_llm_extract=None)
        fin = _sec(res, "financial") if res is not None else None

        check(res is not None and fin is not None, "REWORK 场景：编排返回 financial 章节")
        check(fin is not None and fin.evaluation is not None
              and fin.evaluation.decision == "REWORK",
              "formula_definition 缺失 → 财务章节决策为 REWORK")
        check(fin is not None and len(fin.evaluation.rework_targets) == 1
              and fin.evaluation.rework_targets[0].reason == "citation:formula_not_found",
              "重复 ReworkTarget（2 条同 claim 引用）稳定去重为 1 条")
        check(fin is not None and sum(
            1 for i in fin.evaluation.issues if i.rule_id == "citation_unresolvable") == 2,
              "issues 全保留（2 条 formula_not_found 明细，去重不丢 issue）")
        check(fin is not None and fin.rework_run is not None
              and fin.rework_run.from_section_result_id != fin.section_result.section_result_id,
              "定向返工产生新 SectionResult（from != 返工后）")
        check(fin is not None and fin.final_rules_passed is False,
              "返工确定性最终检查仍 fail（formula 未补齐）—— 至多一批，不循环")

        check(_db_count(sub / "sections.db", "section_rework") == 1,
              "commit_evaluation 原子落盘 section_rework=1（无 rework_id 主键冲突）")
        check(_db_count(sub / "sections.db", "section_rework_run") == 1,
              "定向返工原子落盘 section_rework_run=1")

        n_result_a = _db_count(sub / "sections.db", "section_result")
        n_rework_a = _db_count(sub / "sections.db", "section_rework")
        n_rework_run_a = _db_count(sub / "sections.db", "section_rework_run")

        # --- 场景 B：同输入重复执行严格幂等（无新行 / 无冲突 / 不抛异常）---
        try:
            SV.run_phase4(
                job, service_cfg=cfg, run_id="run_rework",
                llm_generate=_make_rework_financial_llm(),
                llm_evaluator_generate=_fake_evaluator, audit_llm_extract=None)
            idem_ok = True
        except Exception as e:  # noqa: BLE001
            idem_ok = False
            check(False, f"幂等重跑抛异常: {type(e).__name__}: {e}")
        check(idem_ok, "同输入重复执行不抛异常")
        check(_db_count(sub / "sections.db", "section_result") == n_result_a,
              "幂等：section_result 行数不变")
        check(_db_count(sub / "sections.db", "section_rework") == n_rework_a,
              "幂等：section_rework 行数不变（复用不重复写）")
        check(_db_count(sub / "sections.db", "section_rework_run") == n_rework_run_a,
              "幂等：section_rework_run 行数不变")

        # --- 场景 C：官方入口补齐 formula_definition → Structured Citation 通过 ---
        FST._db_path = (sub / "fin.db").resolve()
        n_persisted = fformulas.ensure_formulas_persisted()
        check(n_persisted > 0, f"官方入口 ensure_formulas_persisted 落盘 {n_persisted} 条公式")

        # 独立 sections 库：公式补齐后是同输入不同 run_id 的新一次编排，不与场景 A/B 的
        # section_result 内容身份串扰（source_run_ids 属 run 级 provenance，非内容身份）。
        cfg_p = SV.ServiceConfig(
            contracts_path=str(contracts_abs),
            fin_db="fin.db", ev_db="ev.db", ext_db="ext.db",
            harness_db="harness.db", section_db="sections_persisted.db",
            scope="consolidated", currency="CNY", purpose="credit_analysis",
            model="offline-test", external_research_enabled=True,
            audit_dir=None, checkpoint=False)
        res_p = SV.run_phase4(
            job, service_cfg=cfg_p, run_id="run_rework_persisted",
            llm_generate=_make_rework_financial_llm(),
            llm_evaluator_generate=_fake_evaluator, audit_llm_extract=None)
        fin_p = _sec(res_p, "financial") if res_p is not None else None

        check(fin_p is not None and fin_p.evaluation is not None
              and fin_p.evaluation.decision in ("PASS", "PASS_WITH_GAPS"),
              "公式补齐后 → 财务章节决策通过（非 REWORK）")
        check(fin_p is not None and fin_p.evaluation.llm_evaluator_calls == 1,
              "公式补齐后 → 规则通过，走 LLM Evaluator 一次")
        check(fin_p is not None and not any(
            i.rule_id == "citation_unresolvable" for i in fin_p.evaluation.issues),
              "公式补齐后 → 无 formula_not_found 引用失败")

    finally:
        os.chdir(old_cwd)


def _run_trailing_comma_scenario(tmp: Path, contracts_abs: Path) -> None:
    """注入一次与真实日志同形的尾逗号输出，验证完整财务章节继续进入 Evaluation/Store。

    独立子目录；全程真实 ``run_phase4``（相对库路径 + chdir），仅替换财务 Worker LLM 为
    尾逗号输出，其余（Rules Evaluator / Store / CitationAuthority / audit enrichment）真实。
    """
    sub = tmp / "trailing_comma"
    sub.mkdir()
    _seed_financial_db(sub / "fin.db", company_id="TESTCO")
    _seed_evidence_db(sub / "ev.db", company_id="TESTCO")
    _seed_external_db(sub / "ext.db")

    old_cwd = os.getcwd()
    try:
        os.chdir(str(sub))
        cfg = SV.ServiceConfig(
            contracts_path=str(contracts_abs),
            fin_db="fin.db", ev_db="ev.db", ext_db="ext.db",
            harness_db="harness.db", section_db="sections.db",
            scope="consolidated", currency="CNY", purpose="credit_analysis",
            model="offline-test", external_research_enabled=True,
            audit_dir=None, checkpoint=False)
        job = PS.ReportJobInput(
            job_id="job_tc", company_id="TESTCO", company_name="测试公司",
            credit_type="other", report_as_of="2025-12-31", template_id="standard_v2",
            enabled_sections=("company", "financial", "industry"),
            evidence_inventory_fingerprint="", financial_snapshot_id="snap_test_1")

        harness_runtime.run_question = _make_fake_run_question()
        res = SV.run_phase4(
            job, service_cfg=cfg, run_id="run_tc",
            llm_generate=_fake_financial_llm_trailing_comma,
            llm_evaluator_generate=_fake_evaluator, audit_llm_extract=None)
        fin = _sec(res, "financial") if res is not None else None

        check(res is not None and fin is not None and fin.error is None,
              "尾逗号场景：编排无异常（financial error=None）")
        check(fin is not None and fin.section_result is not None
              and len(fin.section_result.claims) > 0,
              "尾逗号场景：财务章节 claim_count > 0（尾逗号修复后进入下游）")
        check(fin is not None and fin.evaluation is not None
              and fin.evaluation.decision in ("PASS", "PASS_WITH_GAPS"),
              "尾逗号场景：财务章节进入 Evaluation 且决策通过")
        check(_db_count(sub / "sections.db", "section_result") >= 3,
              "尾逗号场景：Store 原子提交（section_result 行数≥3）")
    finally:
        os.chdir(old_cwd)


def main() -> dict:
    global _results
    _results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

    orig_run_question = harness_runtime.run_question
    orig_fstore_db = FST._db_path
    orig_estore_db = estore._db_path
    orig_extstore_db = extstore._db_path
    orig_sstore_db = sstore._db_path
    old_cwd = os.getcwd()

    tmp = Path(tempfile.mkdtemp(prefix="phase4_dryrun_"))
    contracts_abs = ROOT / "templates" / "contracts" / "standard_v2.yaml"

    res = None
    try:
        # 1) 预建临时库（绝对路径；随后 chdir 用相对路径跑真实入口）。
        _seed_financial_db(tmp / "fin.db", company_id="TESTCO")
        _seed_evidence_db(tmp / "ev.db", company_id="TESTCO")
        _seed_external_db(tmp / "ext.db")

        os.chdir(str(tmp))
        cfg = SV.ServiceConfig(
            contracts_path=str(contracts_abs),
            fin_db="fin.db", ev_db="ev.db", ext_db="ext.db",
            harness_db="harness.db", section_db="sections.db",
            scope="consolidated", currency="CNY", purpose="credit_analysis",
            model="offline-test", external_research_enabled=True,
            audit_dir=None, checkpoint=False)

        job = PS.ReportJobInput(
            job_id="job_dryrun", company_id="TESTCO", company_name="测试公司",
            credit_type="other", report_as_of="2025-12-31",
            template_id="standard_v2",
            enabled_sections=("company", "financial", "industry"),
            evidence_inventory_fingerprint="", financial_snapshot_id="snap_test_1")

        harness_runtime.run_question = _make_fake_run_question()

        # 2) 真实服务入口（规划 → 三章节 → 评估/返工 → manifest → Store 原子提交）。
        res = SV.run_phase4(
            job, service_cfg=cfg, run_id="run_dryrun",
            llm_generate=_fake_financial_llm,
            llm_evaluator_generate=_fake_evaluator,
            audit_llm_extract=None)

        # --- 断言 ---
        check(res is not None, "run_phase4 返回非空 Phase4RunResult")
        check(res is not None and res.success is True, "编排成功（success=True）")
        check(res is not None and len(res.sections) == 3, "编排恰好三个章节")
        check(res is not None and sorted(o.section_id for o in res.sections)
              == ["company", "financial", "industry"], "章节身份为 company/financial/industry")
        check(res is not None and all(o.error is None for o in res.sections),
              "所有章节 worker 无异常（error=None）")
        check(res is not None and all(o.section_result is not None for o in res.sections),
              "所有章节产出非空 SectionResult")

        fin = _sec(res, "financial") if res is not None else None
        comp = _sec(res, "company") if res is not None else None
        ind = _sec(res, "industry") if res is not None else None

        check(res is not None and all(
            o.section_result.status not in ("SECTION_BLOCKED", "FAILED")
            for o in res.sections), "所有章节状态非阻断（非 SECTION_BLOCKED/FAILED）")

        check(fin is not None and len(fin.section_result.claims) > 0,
              "财务章节 claim_count > 0")

        audit_claims = ([c for c in fin.section_result.claims if "标准无保留意见" in c.text]
                        if fin is not None else [])
        check(bool(audit_claims) and any(
            r.ref_type == "evidence" for c in audit_claims for r in c.citation_refs),
            "财务章节含审计意见 claim（证据引用 + 标准无保留意见，确定性 enrichment）")

        pos = ([c for c in ind.section_result.claims if "industry_position" in c.question_ids]
               if ind is not None else [])
        check(bool(pos) and len(pos[0].citation_refs) == 1,
              "industry_position 重复证据引用折叠为 1 条（canonicalize_citation_refs）")

        sdp = ([c for c in ind.section_result.claims if "industry_supply_demand" in c.question_ids]
               if ind is not None else [])
        check(bool(sdp) and len(sdp[0].citation_refs) == 2 and any(
            r.ref_type == "external" for r in sdp[0].citation_refs),
            "industry_supply_demand 证据+外部引用保留 2 条（含 external）")

        check(comp is not None and len(comp.section_result.claims) > 0,
              "公司章节 claim_count > 0")
        check(ind is not None and len(ind.section_result.claims) > 0,
              "行业章节 claim_count > 0")

        check(res is not None and all(
            o.evaluation is not None and o.evaluation.llm_evaluator_calls == 1
            for o in res.sections), "真实 Rules + LLM Evaluator 已运行（每章 1 次）")

        n_rows = 0
        if res is not None:
            conn = sqlite3.connect(str(tmp / "sections.db"))
            try:
                n_rows = conn.execute(
                    "SELECT COUNT(*) FROM section_result").fetchone()[0]
            finally:
                conn.close()
        check(res is not None and bool(res.manifest_id) and res.manifest is not None
              and n_rows >= 3, "manifest 非空且 Store 已原子提交（section_result 行数≥3）")

        # --- REWORK 触发 / 去重 / 定向返工 / 幂等 / 公式补齐 场景（独立子目录）---
        _run_rework_scenario(tmp, contracts_abs)

        # --- 尾逗号 JSON 输出（真实失败形状）经完整编排进入 Evaluation/Store ---
        _run_trailing_comma_scenario(tmp, contracts_abs)

    except Exception as e:  # noqa: BLE001
        check(False, f"run_phase4 抛异常: {type(e).__name__}: {e}")
    finally:
        os.chdir(old_cwd)
        harness_runtime.run_question = orig_run_question
        FST._db_path = orig_fstore_db
        estore._db_path = orig_estore_db
        extstore._db_path = orig_extstore_db
        sstore._db_path = orig_sstore_db
        shutil.rmtree(tmp, ignore_errors=True)

    return dict(_results)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    print(json.dumps(main(), ensure_ascii=False, indent=2))
