"""Eval: financial_v2 跨来源对账（A4 reconciliation）。

用法: python -m evals.test_financial_v2_reconciliation

覆盖（§7.5/7.6/7.7/7.8 + 测试矩阵 §12.4）：
- 展示精度区间（candidate.min_display_increment × 单位换算，Decimal 无浮点漂移）；
- 区间共同相交（重叠 / 恰好相触 / 刚好不相交）；
- 维度完整性（八维逐项缺一 → INSUFFICIENT_SCOPE，不混组）；
- 分组状态：单来源 SINGLE_SOURCE、同源异值 CONFLICT、多来源同值/舍入重叠 MATCHED、
  未知精度同值 MATCHED / 异值 CONFLICT、八维字段逐项不混组；
- 冲突不平均/不投票（保留各来源原值）；
- 端到端 run_reconciliation：run + group results + issues 单事务原子落盘、
  current 指针切换、幂等重放；写失败保留旧 current（由原子事务保证，此处验证指针）。

全部合成 fixture（公司无关），临时 DB 注入，不污染生产库。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import normalization as norm
from financial_v2 import reconciliation as recon
from financial_v2 import schema as S
from financial_v2 import store


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_recon_")
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


def _record(record_id: str, *, record_set_version: str = "rs-x", company_id: str = "ACME",
            item_code: str = "TOTAL_ASSETS", statement_type: str = "balance_sheet",
            std_value: float = 1000.0, raw_unit: str = "yuan", period: str = "2024-12-31",
            period_type: str = "annual", scope: str = "consolidated",
            currency: str = "CNY", restatement: str = "0",
            candidate_id: str | None = None) -> S.SourceFinancialRecord:
    loc = S.SourceLocator(kind="excel", excel=S.ExcelCellLocator(
        sheet_name="报表", row_number=2, column_number=2, cell_address="B2"))
    return S.SourceFinancialRecord(
        record_id=record_id, record_set_version=record_set_version, company_id=company_id,
        standard_item_code=item_code, statement_type=statement_type, raw_item_text="x",
        raw_value=std_value, raw_unit=raw_unit, raw_currency=currency,
        std_value=std_value, std_unit="yuan", std_currency=currency,
        conversion_rule_version="1.0", report_period=period, period_type=period_type,
        statement_scope=scope, currency=currency, restatement_version=restatement,
        locator=loc, mapping_mode="rule", confidence=1.0, record_hash="",
        quality_flags=[], created_at="2026-01-01T00:00:00Z", candidate_id=candidate_id)


def _cell(parsed: Decimal, min_inc: Decimal | None, unit: str = "wan_yuan",
          candidate_id: str = "cand-x") -> S.ExtractedFinancialCell:
    return S.ExtractedFinancialCell(
        candidate_id=candidate_id, record_set_version="rs-x", company_id="ACME",
        source_version="sv-x", statement_type_candidate="balance_sheet",
        raw_item_text="x", raw_value_text=str(parsed), parsed_numeric_value=parsed,
        formula_text=None, cached_formula_value=None, period_text="2024-12-31",
        period_candidate="2024-12-31", period_type_candidate="annual",
        scope_candidate="consolidated", currency_candidate="CNY", unit_candidate=unit,
        restatement_candidate=None, min_display_increment=min_inc,
        locator=None, detection_evidence={}, status="EXTRACTED", quality_flags=[],
        created_at="2026-01-01T00:00:00Z")


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

    src_map = {"rs-a": "sv-a", "rs-b": "sv-b"}

    # ---- 展示精度区间 ----
    by_id = {"c1": _cell(Decimal("1000"), Decimal("0.01"), unit="wan_yuan", candidate_id="c1")}
    r = _record("r1", record_set_version="rs-a", candidate_id="c1",
                raw_unit="wan_yuan", std_value=10000000.0)
    iv = recon._value_interval(r, by_id, src_map)
    check(iv.value == Decimal("10000000") and iv.low == Decimal("9999950")
          and iv.high == Decimal("10000050") and iv.precision_known,
          f"万元×0.01 精度 → 区间 [9999950,10000050] 元（实际 {iv.low}~{iv.high}）")
    check(iv.source_version == "sv-a", "区间携带 source_version")

    r2 = _record("r2", candidate_id=None, raw_unit="wan_yuan", std_value=10000000.0)
    iv2 = recon._value_interval(r2, by_id, src_map)
    check(iv2.low == iv2.high == iv2.value and not iv2.precision_known,
          "精度未知 → 点区间且 precision_known=False")

    r3 = _record("r3", candidate_id="c1", raw_unit="wan_yuan", std_value=None)
    check(recon._value_interval(r3, by_id, src_map) is None, "std_value=None → 无区间（跳过）")

    # ---- 共同相交 ----
    def _iv(lo, hi, rid="r"):
        return recon._Interval(rid, "sv", Decimal(lo), Decimal(lo), Decimal(hi), True)

    check(recon._common_intersection([_iv("0", "10"), _iv("5", "15")]) == (Decimal("5"), Decimal("10")),
          "区间重叠 → [5,10]")
    check(recon._common_intersection([_iv("0", "10"), _iv("10", "20")]) == (Decimal("10"), Decimal("10")),
          "边界恰好相触 → 单点相交")
    check(recon._common_intersection([_iv("0", "10"), _iv("10.1", "20")]) is None,
          "刚好不相交 → None")

    # ---- 维度完整性 ----
    check(recon._missing_dimensions(_record("r")) == [], "八维完整 → 无缺失")
    check(recon._missing_dimensions(_record("r", scope="")) == ["statement_scope"],
          "scope 空串 → 缺 statement_scope")

    # ---- 分组：单来源 / 同源异值 / 多来源 / 未知精度 / 八维不混组 ----
    by_id2 = {}

    # 单来源同值（同事实不同坐标）→ SINGLE_SOURCE，保留两坐标。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0),
         _record("r2", record_set_version="rs-a", std_value=10.0)],
        by_id2, src_map)
    check(len(g) == 1 and g[0].state == "SINGLE_SOURCE", "单来源同值 → SINGLE_SOURCE")
    check(sorted(g[0].candidate_record_ids) == ["r1", "r2"], "SINGLE_SOURCE 保留全部坐标")
    check(g[0].std_values == ["10.0"], "标准值只出现一次")

    # 单来源异值 → CONFLICT（同源重复冲突，不投票）。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0),
         _record("r2", record_set_version="rs-a", std_value=11.0)],
        by_id2, src_map)
    check(g[0].state == "CONFLICT" and g[0].std_values == ["10.0", "11.0"],
          "同源异值 → CONFLICT 且保留两个原值")

    # 多来源同值 → MATCHED。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0),
         _record("r2", record_set_version="rs-b", std_value=10.0)],
        by_id2, src_map)
    check(g[0].state == "MATCHED", "多来源同值 → MATCHED")

    # 多来源舍入重叠（候选精度 0.01 万元）→ MATCHED。
    cells = {"ca": _cell(Decimal("100.00"), Decimal("0.01"), unit="wan_yuan", candidate_id="ca"),
             "cb": _cell(Decimal("100.01"), Decimal("0.01"), unit="wan_yuan", candidate_id="cb")}
    g = recon.group_records(
        [_record("ra", record_set_version="rs-a", raw_unit="wan_yuan", std_value=1000000.0,
                 candidate_id="ca"),
         _record("rb", record_set_version="rs-b", raw_unit="wan_yuan", std_value=1000100.0,
                 candidate_id="cb")],
        cells, src_map)
    check(g[0].state == "MATCHED", "舍入区间相触（100.00/100.01 万元）→ MATCHED")

    # 多来源刚好不相交 → CONFLICT。
    cells = {"ca": _cell(Decimal("100.00"), Decimal("0.01"), unit="wan_yuan", candidate_id="ca"),
             "cb": _cell(Decimal("100.02"), Decimal("0.01"), unit="wan_yuan", candidate_id="cb")}
    g = recon.group_records(
        [_record("ra", record_set_version="rs-a", raw_unit="wan_yuan", std_value=1000000.0,
                 candidate_id="ca"),
         _record("rb", record_set_version="rs-b", raw_unit="wan_yuan", std_value=1000200.0,
                 candidate_id="cb")],
        cells, src_map)
    check(g[0].state == "CONFLICT", "舍入区间不相交（100.00/100.02 万元）→ CONFLICT")

    # 未知精度：同值 → MATCHED；异值 → CONFLICT。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0),
         _record("r2", record_set_version="rs-b", std_value=10.0)],
        by_id2, src_map)
    check(g[0].state == "MATCHED", "未知精度同值 → MATCHED")
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0),
         _record("r2", record_set_version="rs-b", std_value=10.001)],
        by_id2, src_map)
    check(g[0].state == "CONFLICT", "未知精度非零差异 → CONFLICT")

    # 八维字段逐项不混组：period 不同 → 两个独立组。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0, period="2023-12-31"),
         _record("r2", record_set_version="rs-b", std_value=10.0, period="2024-12-31")],
        by_id2, src_map)
    check(len(g) == 2 and all(x.state == "SINGLE_SOURCE" for x in g),
          "不同 report_period → 两个独立组（不混组）")

    # scope 不同 → 不混组。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0, scope="consolidated"),
         _record("r2", record_set_version="rs-b", std_value=10.0, scope="parent")],
        by_id2, src_map)
    check(len(g) == 2, "不同 statement_scope → 不混组")

    # restatement 不同 → 不混组。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0, restatement="0"),
         _record("r2", record_set_version="rs-b", std_value=10.0, restatement="1")],
        by_id2, src_map)
    check(len(g) == 2, "不同 restatement_version → 不混组")

    # 维度缺失 → INSUFFICIENT_SCOPE（不与其它记录混组）。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0, scope=""),
         _record("r2", record_set_version="rs-a", std_value=10.0, scope="consolidated")],
        by_id2, src_map)
    insuff = [x for x in g if x.state == "INSUFFICIENT_SCOPE"]
    check(len(insuff) == 1 and insuff[0].candidate_record_ids == ["r1"],
          "缺 scope → INSUFFICIENT_SCOPE（不混入有效组）")
    check(insuff[0].diff_detail["missing_dimensions"] == ["statement_scope"],
          "INSUFFICIENT_SCOPE 记录缺失维度")

    # 冲突不平均/不投票：保留各来源原值，不产生合成均值。
    g = recon.group_records(
        [_record("r1", record_set_version="rs-a", std_value=10.0, item_code="OPERATING_REVENUE",
                 statement_type="income_statement"),
         _record("r2", record_set_version="rs-b", std_value=30.0, item_code="OPERATING_REVENUE",
                 statement_type="income_statement")],
        by_id2, src_map)
    check(g[0].state == "CONFLICT" and g[0].std_values == ["10.0", "30.0"],
          "冲突保留原值 [10,30]，不平均成 20")
    check(g[0].impact_item_codes == ["OPERATING_REVENUE"]
          and g[0].impact_section_contracts == ["financial.performance"],
          "影响映射由静态契约确定（不调 LLM）")

    # ---- 端到端：两来源 + MATCHED/CONFLICT/SINGLE_SOURCE，原子落盘 + current + 幂等 ----
    db = _tmp_db()
    try:
        store.init_db(db)

        def register(company, ext_id):
            source_document_id = S.scope_source_document_id(company, ext_id)
            file_hash = hashlib.sha256(ext_id.encode()).hexdigest()
            source_version = S.derive_source_version(source_document_id, file_hash)
            store.register_source_atomic(
                S.FinancialSourceDocument(
                    source_document_id=source_document_id, company_id=company,
                    source_name=f"{ext_id}.xlsx", source_class="financial_statement",
                    declared_company_name=company, detected_company_name=company,
                    subject_match_status="matched", created_at="2026-01-01T00:00:00Z"),
                S.FinancialSourceVersion(
                    source_version=source_version, source_document_id=source_document_id,
                    file_sha256=file_hash, file_type="xlsx", file_size=100,
                    document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z"))
            return source_document_id, source_version

        def make_candidate(rs, sv, company, raw, st, value, *, unit="wan_yuan", row=2,
                           min_inc=Decimal("0.01")):
            loc = _locator(row)
            cid = S.derive_candidate_id(rs, loc, raw, str(value))
            return S.ExtractedFinancialCell(
                candidate_id=cid, record_set_version=rs, company_id=company,
                source_version=sv, statement_type_candidate=st, raw_item_text=raw,
                raw_value_text=str(value), parsed_numeric_value=value, formula_text=None,
                cached_formula_value=None, period_text="2024-12-31",
                period_candidate="2024-12-31", period_type_candidate="annual",
                scope_candidate="consolidated", currency_candidate="CNY", unit_candidate=unit,
                restatement_candidate=None, min_display_increment=min_inc,
                locator=loc, detection_evidence={}, status="EXTRACTED", quality_flags=[],
                created_at="2026-01-01T00:00:00Z")

        def seed(company, ext_id, items):
            source_document_id, source_version = register(company, ext_id)
            rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
            cands = [make_candidate(rs, source_version, company, raw, st, value, row=i + 2)
                     for i, (raw, st, value) in enumerate(items)]
            store.commit_extracted_candidates(cands, [], source_document_id)
            norm.normalize_record_set(rs, persist=True)
            return rs

        rs_a = seed("ACME", "doc-a", [
            ("资产总计", "balance_sheet", Decimal("1000")),       # TOTAL_ASSETS
            ("营业收入", "income_statement", Decimal("500")),     # OPERATING_REVENUE
            ("净利润", "income_statement", Decimal("80")),        # NET_PROFIT（仅 A）
        ])
        rs_b = seed("ACME", "doc-b", [
            ("资产总计", "balance_sheet", Decimal("1000")),       # 同值 → MATCHED
            ("营业收入", "income_statement", Decimal("400")),     # 异值 → CONFLICT
        ])

        result = recon.run_reconciliation("ACME", [rs_a, rs_b], persist=True)
        check(result.single_source_count == 1 and result.matched_count == 1
              and result.conflict_count == 1,
              f"1 SINGLE + 1 MATCHED + 1 CONFLICT（实际 {result.single_source_count}/"
              f"{result.matched_count}/{result.conflict_count}）")
        check(result.issues_committed == 1, f"仅 CONFLICT 产生 1 条 issue（实际 {result.issues_committed}）")
        check(result.groups_committed == 3, f"落盘 3 组结果（实际 {result.groups_committed}）")

        # current 指针已切换。
        cur = store.get_current_reconciliation("ACME")
        check(cur is not None and cur.run_id == result.run_id, "current_reconciliation 指向新 run")

        # 落库 group results + issue。
        persisted = store.list_reconciliation_group_results(result.run_id)
        check(len(persisted) == 3, "reconciliation_group_result 表 3 条")
        states = {p.state for p in persisted}
        check(states == {"SINGLE_SOURCE", "MATCHED", "CONFLICT"}, "组状态齐全")
        conflict_group = next(p for p in persisted if p.state == "CONFLICT")
        check(conflict_group.impact_item_codes == ["OPERATING_REVENUE"],
              "CONFLICT 组影响科目为 OPERATING_REVENUE")

        issues = store.list_extraction_issues(rs_a) + store.list_extraction_issues(rs_b)
        conflict_issues = [i for i in issues if i.issue_type == "RECONCILIATION_CONFLICT"]
        check(len(conflict_issues) == 1, "extraction_issue 表 1 条 RECONCILIATION_CONFLICT")
        check(conflict_issues[0].comparison_key == conflict_group.comparison_key,
              "issue.comparison_key 关联冲突组")

        # 幂等重放：同 run 复用，不重复写。
        result2 = recon.run_reconciliation("ACME", [rs_a, rs_b], persist=True)
        check(result2.run_id == result.run_id, "二次对账 run_id 一致")
        check(result2.run_reused is True and result2.groups_committed == 0
              and result2.issues_committed == 0, "二次对账复用，不重复写组/问题")
        check(len(store.list_reconciliation_group_results(result.run_id)) == 3, "组未重复")

        # validate-only 不落盘（不写 run / 不切 current）。
        result3 = recon.run_reconciliation("ACME", [rs_a, rs_b], persist=False)
        check(result3.run_reused is False and result3.groups_committed == 0
              and result3.issues_committed == 0, "validate-only 不落盘")

        # ---- 故障注入：reconciliation_group_result 插入失败 → 全事务回滚，旧 current 保留 ----
        # 造一个新输入（净利润异值 → CONFLICT），使第二次 run 是「新 run」而非复用。
        rs_c = seed("ACME", "doc-c", [("净利润", "income_statement", Decimal("70"))])
        rs_d = seed("ACME", "doc-d", [("净利润", "income_statement", Decimal("90"))])
        conn = sqlite3.connect(db)
        conn.execute("CREATE TRIGGER tmp_fail_recon_group BEFORE INSERT ON reconciliation_group_result "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            recon.run_reconciliation("ACME", [rs_c, rs_d], persist=True)
            check(False, "reconciliation group 插入失败被注入触发")
        except sqlite3.IntegrityError:
            check(True, "reconciliation group 插入失败（注入）")
        conn = sqlite3.connect(db)
        conn.execute("DROP TRIGGER tmp_fail_recon_group")
        conn.commit()
        conn.close()
        # 旧 current 保留（仍指向第一次 run），不残留半成品 run。
        cur_after = store.get_current_reconciliation("ACME")
        check(cur_after is not None and cur_after.run_id == result.run_id,
              "故障后旧 current_reconciliation 保留")
        conn = sqlite3.connect(db)
        run_rows = conn.execute("SELECT COUNT(*) FROM reconciliation_run").fetchone()[0]
        group_rows = conn.execute("SELECT COUNT(*) FROM reconciliation_group_result").fetchone()[0]
        conn.close()
        check(run_rows == 1, f"故障后 reconciliation_run 仅 1 行（不残留半成品 run，实际 {run_rows}）")
        check(group_rows == 3, f"故障后 reconciliation_group_result 仅首次 run 的 3 行（实际 {group_rows}）")
    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
