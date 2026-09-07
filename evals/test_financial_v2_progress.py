"""Eval: financial_v2 进度门面 + Streamlit 薄层（A7 Commit 8：progress.py）。

用法: python -m evals.test_financial_v2_progress

覆盖（任务书 §11 / §13.1 / §16）：
- record / summary：进度为真实持久化事件，summary 折叠每阶段最新状态并派生终态；
- 终态正确：completed / waiting_human / failed；失败与等待人工绝不显示为成功；
- run_pipeline 串联：构建快照 → 计算指标 → 只读载荷，进度逐阶段落盘，幂等重放不重复写；
- build_request_for_company：由当前 Record Set + Reconciliation 组装请求（只读）；
- Streamlit 薄层（静态 spy）：V2 快照渲染函数只调用公开接口，不复制公式/准入/计算逻辑。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import progress
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_prog_")
    os.close(fd)
    return p


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _locator(row: int) -> S.SourceLocator:
    return S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=row, column_number=2, cell_address=f"B{row}",
        row_header="科目", column_header="2024-12-31", unit_text="万元"))


_TS = "2026-01-01T00:00:00Z"

_STMT = {
    "CURRENT_ASSETS": "balance_sheet", "CURRENT_LIABILITIES": "balance_sheet",
    "TOTAL_ASSETS": "balance_sheet", "TOTAL_LIABILITIES": "balance_sheet",
    "TOTAL_EQUITY": "balance_sheet", "TOTAL_REVENUE": "income_statement",
    "NET_PROFIT": "income_statement",
}


class _Seed:
    """直接构造标准化记录并落盘（跳过候选/映射，全字段可控）。"""

    def __init__(self, company: str = "ACME"):
        self.company = company
        self._n = 0

    def persist_records(self, ext_id: str, specs: list[dict]) -> str:
        self._n += 1
        source_document_id = S.scope_source_document_id(self.company, ext_id)
        source_version = S.derive_source_version(
            source_document_id, hashlib.sha256(ext_id.encode()).hexdigest())
        store.register_source_atomic(
            S.FinancialSourceDocument(
                source_document_id=source_document_id, company_id=self.company,
                source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                declared_company_name=self.company, detected_company_name=self.company,
                subject_match_status="matched", created_at=_TS),
            S.FinancialSourceVersion(
                source_version=source_version, source_document_id=source_document_id,
                file_sha256=hashlib.sha256(ext_id.encode()).hexdigest(), file_type="xlsx",
                file_size=100, document_id=None, document_version=None, created_at=_TS))

        rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
        records = []
        for i, sp in enumerate(specs):
            r = S.SourceFinancialRecord(
                record_id="", record_set_version=rs, company_id=self.company,
                standard_item_code=sp["item_code"],
                statement_type=sp.get("statement_type", "balance_sheet"),
                raw_item_text=sp["item_code"], raw_value=sp["value"], raw_unit="wan_yuan",
                raw_currency="CNY", std_value=sp["value"], std_unit="yuan",
                std_currency="CNY", conversion_rule_version="1.0",
                report_period=sp["period"], period_type="annual",
                statement_scope="consolidated", currency="CNY",
                restatement_version="0", locator=_locator(i + 2),
                mapping_mode="rule", confidence=1.0, record_hash="", quality_flags=[],
                created_at=_TS, candidate_id=None)
            r.record_hash = validator._record_hash(r)
            r.record_id = S.derive_record_id(rs, S.record_identity_fields(r))
            records.append(r)

        record_set = S.FinancialRecordSet(
            record_set_version=rs, source_version=source_version,
            extractor_name=None, extractor_version="1.0", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={},
            report_periods=sorted({r.report_period for r in records}),
            currency="CNY", unit="wan_yuan", statement_scope="consolidated",
            audit_status="audited", block_count=0, record_count=len(records),
            created_at=_TS, input_candidate_set_version=rs)
        store.commit_normalization_atomic(record_set, records, [], source_document_id)
        return rs


def _specs(values: dict[str, Decimal], period: str) -> list[dict]:
    return [{"item_code": code, "value": val, "period": period,
             "statement_type": _STMT.get(code, "balance_sheet")}
            for code, val in values.items()]


def _request(company, record_set_ids, *, run_id="run-prog"):
    return snapshots.SnapshotBuildRequest(
        company_id=company, as_of_date="2024-12-31", scope="consolidated",
        currency="CNY", purpose="credit_analysis", record_set_ids=record_set_ids,
        reconciliation_run_id=None, required_formula_ids=["SOLV_CURRENT_RATIO"],
        restatement_selection={}, policy_adjustments={}, run_id=run_id)


def _count(db: str, sql: str, params: tuple = ()) -> int:
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Streamlit 薄层静态 spy
# ---------------------------------------------------------------------------

def _streamlit_v2_snapshot_slice() -> str:
    src = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(
        encoding="utf-8")
    start = src.index("def _render_financial_v2_snapshot")
    end = src.index("def _generate_report", start)
    return src[start:end]


def _streamlit_v2_checkbox_slice() -> str:
    src = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(
        encoding="utf-8")
    start = src.index("enable_v2_snapshot = st.checkbox")
    return src[start:start + 700]


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

    db = _tmp_db()
    try:
        store.init_db(db)
        seed = _Seed("ACME")

        # ---- record / summary：真实事件 + 幂等 ----
        progress.record("run-x", "SNAPSHOT_BUILD", "running", "BUILDING_SNAPSHOT")
        progress.record("run-x", "SNAPSHOT_BUILD", "completed", "PERSISTED_SNAPSHOT",
                        completed_units=3, total_units=3)
        progress.record("run-x", "COMPLETED", "completed", "COMPLETED")
        n_before = len(store.history_progress("run-x"))
        progress.record("run-x", "SNAPSHOT_BUILD", "completed", "PERSISTED_SNAPSHOT",
                        completed_units=3, total_units=3)  # 重复 → 跳过
        n_after = len(store.history_progress("run-x"))
        check(n_before == 3 and n_after == 3, "record 幂等：重复事件不重复写")
        s = progress.summary("run-x")
        check(s.final_state == "completed", "summary 终态 completed")
        check(any(x.stage_id == "SNAPSHOT_BUILD" and x.status == "completed"
                  for x in s.stages), "summary 折叠为每阶段最新状态")
        check(len(s.stages) == 2, f"summary 只保留出现过的阶段（{len(s.stages)}）")

        # ---- run_pipeline 成功路径 ----
        rs = seed.persist_records("ok", _specs({
            "CURRENT_ASSETS": Decimal("200"), "CURRENT_LIABILITIES": Decimal("100"),
            "TOTAL_ASSETS": Decimal("500"), "TOTAL_LIABILITIES": Decimal("300"),
            "TOTAL_EQUITY": Decimal("200"), "TOTAL_REVENUE": Decimal("1000"),
            "NET_PROFIT": Decimal("120"),
        }, "2024-12-31"))
        req = _request("ACME", [rs], run_id="run-ok")
        res = progress.run_pipeline(req)
        check(res.final_state == "completed" and res.snapshot_id is not None,
              "run_pipeline 成功 → completed 且有 snapshot_id")
        check(res.report_blocked is False and res.payload is not None,
              "成功路径 report_blocked=False 且有 payload")
        check(res.payload.metrics != [], "成功路径 payload 有指标")
        check(res.progress.final_state == "completed", "progress 终态 completed")
        check(any(x.stage_id == "COMPLETED" for x in res.progress.stages),
              "progress 含 COMPLETED 阶段")
        check(len(store.history_progress("run-ok")) > 0, "进度为真实持久化事件")

        # ---- run_pipeline 幂等重放：不重复写进度事件 ----
        cnt_prog = len(store.history_progress("run-ok"))
        progress.run_pipeline(req)
        check(len(store.history_progress("run-ok")) == cnt_prog,
              "run_pipeline 幂等重放不重复写进度事件")

        # ---- run_pipeline 等待人工（未解决冲突 → report_blocked） ----
        rs_conf = seed.persist_records("conflict", [
            {"item_code": "TOTAL_ASSETS", "value": Decimal("500"), "period": "2024-12-31"},
            {"item_code": "TOTAL_ASSETS", "value": Decimal("700"), "period": "2024-12-31"},
            {"item_code": "CURRENT_ASSETS", "value": Decimal("200"), "period": "2024-12-31"},
            {"item_code": "CURRENT_LIABILITIES", "value": Decimal("100"), "period": "2024-12-31"},
        ])
        req_c = _request("ACME", [rs_conf], run_id="run-wait")
        res_c = progress.run_pipeline(req_c)
        check(res_c.report_blocked is True, "冲突快照 report_blocked=True")
        check(res_c.final_state == "waiting_human",
              "report_blocked → 终态 waiting_human（不显示成功）")
        check(res_c.progress.final_state == "waiting_human",
              "progress 终态 waiting_human")
        check(not any(x.stage_id == "COMPLETED" for x in res_c.progress.stages),
              "等待人工不写 COMPLETED，绝不误报成功")
        check(any(x.stage_id == "WAITING_CONFIRMATION" for x in res_c.progress.stages),
              "progress 含 WAITING_CONFIRMATION 阶段")

        # ---- run_pipeline 失败路径 ----
        req_f = _request("ACME", [], run_id="run-fail")
        res_f = progress.run_pipeline(req_f)
        check(res_f.final_state == "failed" and res_f.error is not None,
              "空 record_set_ids → 终态 failed")
        check(res_f.progress.final_state == "failed",
              "progress 终态 failed")
        check(any(x.stage_id == "FAILED" for x in res_f.progress.stages),
              "progress 含 FAILED 阶段")
        check(not any(x.stage_id == "COMPLETED" for x in res_f.progress.stages),
              "失败不写 COMPLETED")

        # ---- build_request_for_company（只读组装） ----
        req_b = progress.build_request_for_company("ACME")
        check(set(req_b.record_set_ids) == {rs, rs_conf},
              f"build_request 取全部 source 的 current record_set（{req_b.record_set_ids}）")
        check(req_b.as_of_date == "2024-12-31", "build_request as_of_date 派生最新期间")
        check(req_b.company_id == "ACME" and req_b.scope == "consolidated"
              and req_b.currency == "CNY", "build_request 头字段正确")
        raised = False
        try:
            progress.build_request_for_company("NO-SUCH-CO")
        except ValueError:
            raised = True
        check(raised, "无 Record Set 公司 → ValueError")

    finally:
        _cleanup_db(db)

    # ---- Streamlit 薄层静态 spy（不依赖 DB / streamlit runtime） ----
    fn = _streamlit_v2_snapshot_slice()
    check("build_request_for_company" in fn and "run_pipeline" in fn,
          "Streamlit V2 渲染只调用 progress 公开接口")
    for forbidden in ("compute_formula", "round_display", "build_registry",
                      "ACTIVE_FORMULA_VERSIONS", "compute_metric(", "compute_all(",
                      "build_snapshot(", "_admit"):
        check(forbidden not in fn,
              f"Streamlit V2 渲染不复制业务逻辑（未引用 {forbidden}）")
    cb = _streamlit_v2_checkbox_slice()
    check("value=False" in cb, "Streamlit V2 开关默认关闭（value=False）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
