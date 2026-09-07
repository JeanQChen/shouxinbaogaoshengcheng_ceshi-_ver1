"""Eval: 结构化元数据确认（fix #1）——statement_scope/currency/audit_status/restatement_version。

用法: python -m evals.test_financial_v2_metadata_confirmation

覆盖：
- v5 迁移新增不可变历史表 + head 指针表（追加式）；
- 候选缺失 scope/currency（模拟 300750 Excel 抽取）→ 标准化 0 记录 + 阻断问题；
- 结构化确认 scope=consolidated / currency=CNY / audit_status=audited /
  restatement_version → 重新标准化产出 ≥1 合格标准记录；
- record_set_version 不因确认改变（确认是 overlay，非重抽取）；
- 确认历史追加式（list 返回历史，get_active 返回最新 head）；
- 幂等重放：同内容确认复用（inserted=0），不同值追加新历史 + head 切换；
- 确认只填元数据，绝不填替代金额（validator 拒非法字段/值）。

全部合成 fixture（公司无关），临时 DB 注入。
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

from financial_v2 import metadata_confirmation as mc
from financial_v2 import normalization as norm
from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_metac_")
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


def _register(company: str, ext_id: str) -> tuple[str, str, str]:
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


def _seed_missing_meta(company: str, ext_id: str) -> tuple[str, str, str]:
    """登记来源 + 提交一个 scope=None/currency=None 的候选（模拟 Excel 缺失元数据）。"""
    source_document_id, source_version = _register(company, ext_id)
    policy = norm.NormalizationPolicy()
    rs = S.derive_record_set_version(
        source_version, policy.extractor_version, policy.mapping_rule_version,
        policy.normalization_rule_version, policy.dependency_versions)
    loc = _locator(2)
    value = Decimal("100")
    candidate = S.ExtractedFinancialCell(
        candidate_id=S.derive_candidate_id(rs, loc, "资产总计", str(value)),
        record_set_version=rs, company_id=company, source_version=source_version,
        statement_type_candidate="balance_sheet", raw_item_text="资产总计",
        raw_value_text=str(value), parsed_numeric_value=value, formula_text=None,
        cached_formula_value=None, period_text="2024-12-31",
        period_candidate="2024-12-31", period_type_candidate="annual",
        scope_candidate=None, currency_candidate=None, unit_candidate="wan_yuan",
        restatement_candidate=None, min_display_increment=Decimal("0.01"),
        locator=loc, detection_evidence={}, status="EXTRACTED", quality_flags=[],
        created_at="2026-01-01T00:00:00Z")
    store.commit_extracted_candidates([candidate], [], source_document_id)
    return source_document_id, source_version, rs


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

        # ---- v5 表就位 + 不可变触发器 ----
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("financial_metadata_confirmation" in tables
              and "financial_metadata_confirmation_head" in tables,
              "v5 表 financial_metadata_confirmation(_head) 就位")
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        check("trg_financial_metadata_confirmation_no_update" in triggers
              and "trg_financial_metadata_confirmation_no_delete" in triggers,
              "元数据确认历史表不可变触发器就位")
        conn.close()

        company = "ACME"
        source_document_id, source_version, rs = _seed_missing_meta(company, "300750-doc")

        # ---- 未确认前标准化 → 0 记录 + scope 阻断（首缺失维度）----
        r0 = norm.normalize_record_set(rs, persist=True)
        check(r0.normalized_count == 0, "未确认前标准化 0 记录")
        check("scope" in {i.detail.get("reason") for i in r0.issues},
              "未确认前首阻断维度为 scope")

        # ---- 只确认 scope → 仍 0 记录，转而阻断 currency（逐字段 gap-fill）----
        n1 = mc.confirm(company, source_document_id, "statement_scope", "consolidated",
                        "user_declaration", "合并报表（用户声明）", operator="tester")
        check(n1 == 1, "scope 确认新插入 1 条")
        r_half = norm.normalize_record_set(rs, persist=True)
        check(r_half.normalized_count == 0, "只确认 scope 仍 0 记录")
        check("currency" in {i.detail.get("reason") for i in r_half.issues},
              "scope 补齐后阻断维度转为 currency")

        # ---- 结构化确认剩余元数据（用户只确认元数据，不填金额）----
        n2 = mc.confirm(company, source_document_id, "currency", "CNY",
                        "user_declaration", "人民币（用户声明）", operator="tester")
        n3 = mc.confirm(company, source_document_id, "audit_status", "audited",
                        "document_body", "审计报告正文含审计意见", operator="tester")
        n4 = mc.confirm(company, source_document_id, "restatement_version", "0",
                        "user_declaration", "无重述", operator="tester")
        check(n2 == 1 and n3 == 1 and n4 == 1, "currency/audit/restatement 各新插入 1 条")

        # ---- 确认后重新标准化 → ≥1 合格记录 + 元数据生效 ----
        r1 = norm.normalize_record_set(rs, persist=True)
        check(r1.normalized_count >= 1, "确认后标准化产出 ≥1 合格记录")
        check(r1.record_set_version == rs, "record_set_version 不因确认改变")
        recs = store.list_records(rs)
        check(len(recs) >= 1 and all(r.statement_scope == "consolidated" for r in recs),
              "记录 statement_scope = consolidated")
        check(all(r.currency == "CNY" for r in recs), "记录 currency = CNY")
        check(all(r.restatement_version == "0" for r in recs), "记录 restatement_version = 0")
        rs_header = store.get_record_set(rs)
        check(rs_header is not None and rs_header.audit_status == "audited",
              "记录集合头 audit_status = audited")

        # ---- 幂等重放：同内容确认复用 ----
        n5 = mc.confirm(company, source_document_id, "currency", "CNY",
                        "user_declaration", "人民币（用户声明）", operator="tester2")
        check(n5 == 0, "同内容确认幂等复用（inserted=0）")

        # ---- 追加式历史：不同值追加新历史 + head 切换 ----
        n6 = mc.confirm(company, source_document_id, "statement_scope", "parent",
                        "user_declaration", "母公司口径（更正声明）", operator="tester")
        check(n6 == 1, "不同值确认追加新历史行")
        history = store.list_metadata_confirmations(company, source_document_id)
        scopes = [c.value for c in history if c.field == "statement_scope"]
        check(scopes == ["consolidated", "parent"], "scope 确认历史按追加顺序保留新旧值")
        active = store.get_active_metadata_confirmations(company, source_document_id)
        check(active["statement_scope"].value == "parent", "head 指向最新 scope 确认")

        # ---- 校验器拒非法元数据（绝不填金额、非法字段值）----
        try:
            mc.confirm(company, source_document_id, "currency", "USD",
                       "user_declaration", "非法币种", operator="tester")
            check(False, "非法币种值被拒绝")
        except validator.ValidationError:
            check(True, "非法币种值被 validator 拒绝")
        try:
            bad = mc.build_confirmation(company, source_document_id, "amount",
                                        "999", "user_declaration", "试图填金额", "tester")
            validator.validate_metadata_confirmation(bad)
            check(False, "非法字段（amount）被拒绝")
        except validator.ValidationError:
            check(True, "非法字段 amount（金额）被 validator 拒绝（只确认元数据）")
    finally:
        _cleanup_db(db)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
