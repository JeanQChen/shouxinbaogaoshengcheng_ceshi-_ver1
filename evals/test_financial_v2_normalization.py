"""Eval: financial_v2 标准化（A4 normalization）。

用法: python -m evals.test_financial_v2_normalization

覆盖（§7.2 / §4.2 + 定点修复 1/3）：
- 单位 → 元 换算（Decimal 精确，未知单位拒绝）；
- 准入维度逐一检查（statement type / 期间 / scope / 币种 / 单位），任一缺失 → NORMALIZATION_REQUIRED；
- 记录构造（Decimal 换算、raw/std value+unit、conversion rule、record_id/hash 重算）；
- 端到端 normalize_record_set（单事务原子落盘 record_set + records + 问题 + current + 幂等）；
- policy 版本与 record_set_version 不一致 → 拒绝；
- 未映射 / 非数值候选不进入记录、不重复处理；
- 输出记录集版本与输入候选版本分离（含当前生效元数据确认身份）；
- 0 条合格记录仍落盘可审计完成态；0→确认→新版本；部分记录→确认→新版本（旧版本保留、
  新版本成为 current）；故障注入全事务回滚零残留。

全部合成 fixture（公司无关），临时 DB / 临时目录注入，不污染生产库。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import metadata_confirmation as mc
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


def _register(db: str, ext_id: str = "doc-norm", company: str = "ACME") -> tuple[str, str]:
    store.init_db(db)
    source_document_id = S.scope_source_document_id(company, ext_id)
    file_hash = "f" * 64
    source_version = S.derive_source_version(source_document_id, file_hash)
    store.register_source_atomic(
        S.FinancialSourceDocument(
            source_document_id=source_document_id, company_id=company, source_name="norm.xlsx",
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
    # record_set_version 重定向：记录身份绑定输出版本，而非候选输入版本。
    rec2 = norm.build_record(full, "CASH_AND_EQUIVALENTS", p, record_set_version="rs-out")
    check(rec2.record_set_version == "rs-out" and rec2.record_id != rec.record_id,
          "record_set_version 重定向改变 record_id 身份")

    # ---- 端到端 normalize_record_set ----
    db = _tmp_db()
    try:
        source_document_id, source_version = _register(db)
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})

        good1 = _make_candidate(input_version, source_version, "ACME",
                                "货币资金", "balance_sheet", Decimal("1000"), row=2)
        good2 = _make_candidate(input_version, source_version, "ACME",
                                "净利润", "income_statement", Decimal("80.5"), unit="yi_yuan", row=3)
        no_currency = _make_candidate(input_version, source_version, "ACME",
                                      "存货", "balance_sheet", Decimal("500"), currency=None, row=4)
        no_scope = _make_candidate(input_version, source_version, "ACME",
                                   "应收账款", "balance_sheet", Decimal("200"), scope=None, row=5)
        no_unit = _make_candidate(input_version, source_version, "ACME",
                                  "固定资产", "balance_sheet", Decimal("900"), unit="unknown", row=6)
        unmapped = _make_candidate(input_version, source_version, "ACME",
                                   "某个未知科目", "balance_sheet", Decimal("1"), row=7)
        empty = _make_candidate(input_version, source_version, "ACME",
                                "长期借款", "balance_sheet", None, status="EMPTY_OR_NOT_APPLICABLE", row=8)

        candidates = [good1, good2, no_currency, no_scope, no_unit, unmapped, empty]
        store.commit_extracted_candidates(candidates, [], source_document_id)

        result = norm.normalize_record_set(input_version, _policy(), persist=True)

        check(result.normalized_count == 2, f"2 条记录（实际 {result.normalized_count}）")
        check(result.blocked_count == 3, f"3 条 NORMALIZATION_REQUIRED（实际 {result.blocked_count}）")
        check(result.issues_committed == 3, f"落盘 3 条问题（实际 {result.issues_committed}）")
        check(result.record_set_version != input_version,
              "输出记录集版本与输入候选版本分离")

        by_code = {r.standard_item_code: r for r in result.records}
        check("CASH_AND_EQUIVALENTS" in by_code, "货币资金 成记录")
        check(by_code["CASH_AND_EQUIVALENTS"].std_value == 10000000.0, "万元 → 元")
        check("NET_PROFIT" in by_code, "净利润 成记录")
        check(by_code["NET_PROFIT"].std_value == 8050000000.0, "亿元 → 元（80.5 亿 → 8050000000）")

        reasons = {i.detail.get("reason") for i in result.issues}
        check(reasons == {"currency", "scope", "unit"}, f"阻断原因 {sorted(reasons)}")

        # 持久化后可读（记录 / record_set 归属输出版本）。
        stored_records = store.list_records(result.record_set_version)
        check(len(stored_records) == 2, "记录已落库（输出版本）")
        rs_row = store.get_record_set(result.record_set_version)
        check(rs_row is not None and rs_row.record_count == 2, "record_set 行已落库")
        check(rs_row.currency == "CNY", "集合级币种汇总 CNY")
        check(rs_row.block_count == 3, "集合级 block_count=3")
        cur = store.get_current_record_set(source_document_id)
        check(cur is not None and cur.record_set_version == result.record_set_version,
              "current 指向输出版本")

        # 幂等：二次标准化复用，不重复写问题。
        result2 = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(result2.reused is True, "二次标准化复用")
        check(result2.record_set_version == result.record_set_version,
              "二次标准化同输出版本（确认未变 → 幂等）")
        check(result2.issues_committed == 0, "二次标准化不重复写问题")
        check(len(store.list_records(result.record_set_version)) == 2, "记录未重复")

        # 未映射 / 非数值候选不进入记录、不产生标准化问题。
        issue_types = {i.issue_type for i in store.list_extraction_issues(input_version)}
        check("NORMALIZATION_REQUIRED" in issue_types, "NORMALIZATION_REQUIRED 已落库")
        check(not any(i.candidate_id == unmapped.candidate_id for i in result.issues),
              "未映射候选不产生标准化问题")

        # policy 版本不一致 → 拒绝。
        bad_policy = norm.NormalizationPolicy(
            extractor_version="1.1", mapping_rule_version="1.0",
            normalization_rule_version="1.0", dependency_versions={})
        try:
            norm.normalize_record_set(input_version, bad_policy, persist=False)
            check(False, "policy 版本不一致应拒绝")
        except ValueError:
            check(True, "policy 版本不一致 → ValueError")
    finally:
        _cleanup_db(db)

    # ---- 未知单位候选不伪造 yuan；validate-only 不落盘 ----
    db2 = _tmp_db()
    try:
        source_document_id, source_version = _register(db2, "doc-norm2")
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        c = _make_candidate(input_version, source_version, "ACME",
                            "货币资金", "balance_sheet", Decimal("1000"), unit="unknown")
        store.commit_extracted_candidates([c], [], source_document_id)
        r = norm.normalize_record_set(input_version, _policy(), persist=False)
        check(r.normalized_count == 0 and r.blocked_count == 1, "validate-only 未知单位不伪造记录")
        check(store.get_record_set(r.record_set_version) is None, "validate-only 不写 record_set")
    finally:
        _cleanup_db(db2)

    # ---- 0 条合格记录：仍落盘可审计完成态（定点修复 3）----
    db3 = _tmp_db()
    try:
        source_document_id, source_version = _register(db3, "doc-norm3")
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        blocked = _make_candidate(input_version, source_version, "ACME",
                                  "货币资金", "balance_sheet", Decimal("1000"), currency=None, row=2)
        store.commit_extracted_candidates([blocked], [], source_document_id)

        result = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(result.normalized_count == 0, "0 条合格记录")
        check(result.blocked_count == 1, "1 条被阻断")
        check(result.issues_committed == 1, "被阻断问题已落库")
        check(result.record_set_version != input_version,
              "0 记录时输出版本仍与输入候选版本分离")

        # 0 条合格记录仍落盘 record_set + 问题 + current（可审计完成态，不锁死输入版本）。
        rs_row = store.get_record_set(result.record_set_version)
        check(rs_row is not None and rs_row.record_count == 0,
              "0 合格记录 normalize 落库 record_count=0 完成态")
        check(store.list_records(result.record_set_version) == [],
              "无 source_financial_record 行")
        check(rs_row.currency is None, "空记录集集合级币种为 None（诚实）")
        check(len(store.list_extraction_issues(input_version)) == 1,
              "阻断问题落库（输入候选版本）")
        cur = store.get_current_record_set(source_document_id)
        check(cur is not None and cur.record_set_version == result.record_set_version,
              "0 合格记录完成态成为 current")
        # 输入候选版本不被锁死：get_record_set(输入版本) 仍为 None（候选层，无 record_set 头）。
        check(store.get_record_set(input_version) is None,
              "输入候选版本不落 record_set 头（候选与输出分离）")
    finally:
        _cleanup_db(db3)

    # ---- 0 记录 → 确认 scope/currency → 派生新版本 + 记录（定点修复 3）----
    db4 = _tmp_db()
    try:
        source_document_id, source_version = _register(db4, "doc-norm4")
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        c = _make_candidate(input_version, source_version, "ACME",
                            "资产总计", "balance_sheet", Decimal("1000"),
                            scope=None, currency=None, row=2)
        store.commit_extracted_candidates([c], [], source_document_id)

        r0 = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(r0.normalized_count == 0, "确认前 0 记录")
        check("scope" in {i.detail.get("reason") for i in r0.issues}, "首阻断维度 scope")

        mc.confirm("ACME", source_document_id, "statement_scope", "consolidated",
                   "user_declaration", "合并报表（用户声明）", operator="tester")
        mc.confirm("ACME", source_document_id, "currency", "CNY",
                   "user_declaration", "人民币（用户声明）", operator="tester")

        r1 = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(r1.normalized_count == 1, "确认后派生 1 条记录")
        check(r1.record_set_version != r0.record_set_version,
              "确认变化派生新输出 record_set_version")
        cur = store.get_current_record_set(source_document_id)
        check(cur is not None and cur.record_set_version == r1.record_set_version,
              "新版本成为 current")
        check(store.get_record_set(r0.record_set_version).record_count == 0,
              "旧 0 记录完成态版本保留（不覆盖）")
    finally:
        _cleanup_db(db4)

    # ---- 部分记录存在 → 确认派生新版本（无冲突）；旧版本保留、新版本 current ----
    db5 = _tmp_db()
    try:
        source_document_id, source_version = _register(db5, "doc-norm5")
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        good = _make_candidate(input_version, source_version, "ACME",
                               "货币资金", "balance_sheet", Decimal("1000"), row=2)
        missing_scope = _make_candidate(input_version, source_version, "ACME",
                                        "应收账款", "balance_sheet", Decimal("200"),
                                        scope=None, row=3)
        store.commit_extracted_candidates([good, missing_scope], [], source_document_id)

        v1 = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(v1.normalized_count == 1 and v1.blocked_count == 1,
              "确认前部分记录（1 记录 + 1 阻断）")
        old_version = v1.record_set_version

        mc.confirm("ACME", source_document_id, "statement_scope", "consolidated",
                   "user_declaration", "合并报表（用户声明）", operator="tester")
        v2 = norm.normalize_record_set(input_version, _policy(), persist=True)
        check(v2.normalized_count == 2 and v2.blocked_count == 0,
              "确认后 2 记录 0 阻断")
        check(v2.record_set_version != old_version,
              "确认派生新输出版本（无 StorageConflict）")
        check(store.get_record_set(old_version).record_count == 1,
              "旧版本保留（1 条，未被 UPDATE）")
        cur = store.get_current_record_set(source_document_id)
        check(cur is not None and cur.record_set_version == v2.record_set_version,
              "新版本成为 current")
    finally:
        _cleanup_db(db5)

    # ---- 故障注入：issue 插入失败 → 全事务回滚（record_set + records + issues + current）----
    db6 = _tmp_db()
    try:
        source_document_id, source_version = _register(db6, "doc-norm6")
        input_version = S.derive_record_set_version(
            source_version, "1.0", "1.0", "1.0", {})
        good = _make_candidate(input_version, source_version, "ACME",
                               "货币资金", "balance_sheet", Decimal("1000"), row=2)
        blocked = _make_candidate(input_version, source_version, "ACME",
                                  "存货", "balance_sheet", Decimal("500"), currency=None, row=3)
        store.commit_extracted_candidates([good, blocked], [], source_document_id)

        conn = sqlite3.connect(db6)
        conn.execute("CREATE TRIGGER tmp_fail_norm_issue BEFORE INSERT ON extraction_issue "
                     "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        conn.commit()
        conn.close()
        try:
            norm.normalize_record_set(input_version, _policy(), persist=True)
            check(False, "normalize 提交失败应抛错")
        except sqlite3.IntegrityError:
            check(True, "normalize 提交失败抛错（单事务回滚）")
        conn = sqlite3.connect(db6)
        conn.execute("DROP TRIGGER tmp_fail_norm_issue")
        conn.commit()
        conn.close()

        # 零残留：record_set / records / issues / current 全不落库。
        check(len(store.list_company_record_set_versions("ACME")) == 1,
              "零残留：仅候选输入版本（无 record_set 头）")
        check(store.get_record_set(input_version) is None, "零残留：无输入版本 record_set 头")
        check(store.get_current_record_set(source_document_id) is None,
              "零残留：current 指针未切换")
        # 输出版本无从得知（未落库），但可通过扫描确认无任何 record_set 行 + 无 issue 行。
        check(store.list_extraction_issues(input_version) == [],
              "零残留：无 NORMALIZATION_REQUIRED 问题")
    finally:
        _cleanup_db(db6)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
