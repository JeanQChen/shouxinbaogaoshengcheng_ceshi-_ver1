"""Eval: financial_v2 标准化（A4 normalization）。

用法: python -m evals.test_financial_v2_normalization

覆盖（§7.2 / §4.2）：
- 单位 → 元 换算（Decimal 精确，未知单位拒绝）；
- 准入维度逐一检查（statement type / 期间 / scope / 币种 / 单位），任一缺失 → NORMALIZATION_REQUIRED；
- 记录构造（Decimal 换算、raw/std value+unit、conversion rule、record_id/hash 重算）；
- 端到端 normalize_record_set（落盘 record_set + records + NORMALIZATION_REQUIRED 问题 + 幂等）；
- policy 版本与 record_set_version 不一致 → 拒绝；
- 未映射 / 非数值候选不进入记录、不重复处理。

全部合成 fixture（公司无关），临时 DB / 临时目录注入，不污染生产库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import normalization as norm
from financial_v2 import schema as S
from financial_v2 import store


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_norm_")
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
        sheet_name="资产负债表", row_number=row, column_number=2,
        cell_address=f"B{row}", row_header="科目", column_header="2024-12-31", unit_text="万元"))


def _make_candidate(record_set_version: str, source_version: str, company_id: str,
                    raw_item_text: str, statement_type: str | None,
                    value: Decimal | None, *, status: str = "EXTRACTED",
                    period: str | None = "2024-12-31", period_type: str | None = "annual",
                    scope: str | None = "consolidated", currency: str | None = "CNY",
                    unit: str | None = "wan_yuan", row: int = 2) -> S.ExtractedFinancialCell:
    locator = _locator(row)
    raw_value_text = str(value) if value is not None else None
    cid = S.derive_candidate_id(record_set_version, locator, raw_item_text, raw_value_text)
    return S.ExtractedFinancialCell(
        candidate_id=cid, record_set_version=record_set_version, company_id=company_id,
        source_version=source_version, statement_type_candidate=statement_type,
        raw_item_text=raw_item_text, raw_value_text=raw_value_text,
        parsed_numeric_value=value, formula_text=None, cached_formula_value=None,
        period_text=period, period_candidate=period, period_type_candidate=period_type,
        scope_candidate=scope, currency_candidate=currency, unit_candidate=unit,
        restatement_candidate=None, min_display_increment=Decimal("0.01"),
        locator=locator, detection_evidence={}, status=status, quality_flags=[],
        created_at="2026-01-01T00:00:00Z")


def _register(db: str) -> tuple[str, str]:
    store.init_db(db)
    source_document_id = S.scope_source_document_id("ACME", "doc-norm")
    file_hash = "f" * 64
    source_version = S.derive_source_version(source_document_id, file_hash)
    store.register_source_atomic(
        S.FinancialSourceDocument(
            source_document_id=source_document_id, company_id="ACME", source_name="norm.xlsx",
            source_class="financial_statement", declared_company_name="Acme",
            detected_company_name="Acme", subject_match_status="matched",
            created_at="2026-01-01T00:00:00Z"),
        S.FinancialSourceVersion(
            source_version=source_version, source_document_id=source_document_id,
            file_sha256=file_hash, file_type="xlsx", file_size=100,
            document_id=None, document_version=None, created_at="2026-01-01T00:00:00Z"))
    return source_document_id, source_version


def _policy() -> norm.NormalizationPolicy:
    return norm.NormalizationPolicy(
        extractor_version="1.0", mapping_rule_version="1.0",
        normalization_rule_version="1.0", dependency_versions={})


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

    # ---- 纯函数：单位换算（Decimal 精确）----
    check(norm.unit_to_yuan("yuan") == Decimal("1"), "yuan × 1")
    check(norm.unit_to_yuan("wan_yuan") == Decimal("10000"), "万元 × 10000")
    check(norm.unit_to_yuan("qian_yuan") == Decimal("1000"), "千元 × 1000")
    check(norm.unit_to_yuan("yi_yuan") == Decimal("100000000"), "亿元 × 1e8")
    check(norm.unit_to_yuan("baiwan_yuan") == Decimal("1000000"), "百万元 × 1e6")
    check(norm.unit_to_yuan("qianwan_yuan") == Decimal("10000000"), "千万元 × 1e7")
    check(norm.unit_to_yuan("unknown") is None, "unknown 单位拒绝换算")
    check(norm.unit_to_yuan(None) is None, "None 单位拒绝换算")
    check(Decimal("1234.56") * norm.unit_to_yuan("wan_yuan") == Decimal("12345600"),
          "Decimal 换算不漂移")

    # ---- 纯函数：准入判定 ----
    rs = "rs-test"
    sv = "sv-test"
    p = _policy()
    full = _make_candidate(rs, sv, "ACME", "货币资金", "balance_sheet", Decimal("1000"))
    check(norm._admission_block_reason(full) is None, "全维度明确 → 通过准入")

    check(norm._admission_block_reason(
        _make_candidate(rs, sv, "ACME", "货币资金", None, Decimal("1000"))) == "statement_type",
        "缺 statement type → block")
    check(norm._admission_block_reason(
        _make_candidate(rs, sv, "ACME", "货币资金", "balance_sheet", Decimal("1000"), period=None))
        == "period", "缺期间 → block")
    check(norm._admission_block_reason(
        _make_candidate(rs, sv, "ACME", "货币资金", "balance_sheet", Decimal("1000"), scope=None))
        == "scope", "缺 scope → block")
    check(norm._admission_block_reason(
        _make_candidate(rs, sv, "ACME", "货币资金", "balance_sheet", Decimal("1000"), currency=None))
        == "currency", "缺币种 → block")
    check(norm._admission_block_reason(
        _make_candidate(rs, sv, "ACME", "货币资金", "balance_sheet", Decimal("1000"), unit="unknown"))
        == "unit", "未知单位 → block")

    # ---- 纯函数：记录构造 ----
    rec = norm.build_record(full, "CASH_AND_EQUIVALENTS", p)
    check(rec.std_unit == "yuan" and rec.std_value == 10000000.0,
          f"万元 → 元：1000 万 → 10000000 元（实际 {rec.std_value}）")
    check(rec.raw_unit == "wan_yuan" and rec.raw_value == 1000.0, "raw value/unit 保留")
    check(rec.standard_item_code == "CASH_AND_EQUIVALENTS", "标准科目写入")
    check(rec.conversion_rule_version == norm.CONVERSION_RULE_VERSION, "转换规则版本写入")
    check(rec.mapping_mode == "rule" and rec.confidence == 1.0, "mapping_mode=rule")
    check(rec.restatement_version == "0", "restatement_version=0（as-reported）")
    check(rec.candidate_id == full.candidate_id, "candidate_id 溯源指针")
    check(rec.record_id.startswith("rec-"), "record_id 派生")
    check(rec.record_hash, "record_hash 非空")

    # ---- 端到端 normalize_record_set ----
    db = _tmp_db()
    try:
        source_document_id, source_version = _register(db)
        record_set_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})

        good1 = _make_candidate(record_set_version, source_version, "ACME",
                                "货币资金", "balance_sheet", Decimal("1000"), row=2)
        good2 = _make_candidate(record_set_version, source_version, "ACME",
                                "净利润", "income_statement", Decimal("80.5"), unit="yi_yuan", row=3)
        no_currency = _make_candidate(record_set_version, source_version, "ACME",
                                      "存货", "balance_sheet", Decimal("500"), currency=None, row=4)
        no_scope = _make_candidate(record_set_version, source_version, "ACME",
                                   "应收账款", "balance_sheet", Decimal("200"), scope=None, row=5)
        no_unit = _make_candidate(record_set_version, source_version, "ACME",
                                  "固定资产", "balance_sheet", Decimal("900"), unit="unknown", row=6)
        unmapped = _make_candidate(record_set_version, source_version, "ACME",
                                   "某个未知科目", "balance_sheet", Decimal("1"), row=7)
        empty = _make_candidate(record_set_version, source_version, "ACME",
                                "长期借款", "balance_sheet", None, status="EMPTY_OR_NOT_APPLICABLE", row=8)

        candidates = [good1, good2, no_currency, no_scope, no_unit, unmapped, empty]
        store.commit_extracted_candidates(candidates, [], source_document_id)

        result = norm.normalize_record_set(record_set_version, _policy(), persist=True)

        check(result.normalized_count == 2, f"2 条记录（实际 {result.normalized_count}）")
        check(result.blocked_count == 3, f"3 条 NORMALIZATION_REQUIRED（实际 {result.blocked_count}）")
        check(result.issues_committed == 3, f"落盘 3 条问题（实际 {result.issues_committed}）")

        by_code = {r.standard_item_code: r for r in result.records}
        check("CASH_AND_EQUIVALENTS" in by_code, "货币资金 成记录")
        check(by_code["CASH_AND_EQUIVALENTS"].std_value == 10000000.0, "万元 → 元")
        check("NET_PROFIT" in by_code, "净利润 成记录")
        check(by_code["NET_PROFIT"].std_value == 8050000000.0, "亿元 → 元（80.5 亿 → 8050000000）")

        reasons = {i.detail.get("reason") for i in result.issues}
        check(reasons == {"currency", "scope", "unit"}, f"阻断原因 {sorted(reasons)}")

        # 持久化后可读。
        stored_records = store.list_records(record_set_version)
        check(len(stored_records) == 2, "记录已落库")
        rs_row = store.get_record_set(record_set_version)
        check(rs_row is not None and rs_row.record_count == 2, "record_set 行已落库")
        check(rs_row.currency == "CNY", "集合级币种汇总 CNY")
        check(rs_row.block_count == 3, "集合级 block_count=3")

        # 幂等：二次标准化复用，不重复写问题。
        result2 = norm.normalize_record_set(record_set_version, _policy(), persist=True)
        check(result2.reused is True, "二次标准化复用")
        check(result2.issues_committed == 0, "二次标准化不重复写问题")
        check(len(store.list_records(record_set_version)) == 2, "记录未重复")

        # 未映射 / 非数值候选不进入记录、不产生标准化问题。
        issue_types = {i.issue_type for i in store.list_extraction_issues(record_set_version)}
        check("NORMALIZATION_REQUIRED" in issue_types, "NORMALIZATION_REQUIRED 已落库")
        check(not any(i.candidate_id == unmapped.candidate_id for i in result.issues),
              "未映射候选不产生标准化问题")

        # policy 版本不一致 → 拒绝。
        bad_policy = norm.NormalizationPolicy(
            extractor_version="1.1", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={})
        try:
            norm.normalize_record_set(record_set_version, bad_policy, persist=False)
            check(False, "policy 版本不一致应拒绝")
        except ValueError:
            check(True, "policy 版本不一致 → ValueError")
    finally:
        _cleanup_db(db)

    # ---- 未知单位候选不伪造 yuan；validate-only 不落盘 ----
    db2 = _tmp_db()
    try:
        source_document_id, source_version = _register(db2)
        record_set_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        c = _make_candidate(record_set_version, source_version, "ACME",
                            "货币资金", "balance_sheet", Decimal("1000"), unit="unknown")
        store.commit_extracted_candidates([c], [], source_document_id)
        r = norm.normalize_record_set(record_set_version, _policy(), persist=False)
        check(r.normalized_count == 0 and r.blocked_count == 1, "validate-only 未知单位不伪造记录")
        check(store.get_record_set(record_set_version) is None, "validate-only 不写 record_set")
    finally:
        _cleanup_db(db2)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
