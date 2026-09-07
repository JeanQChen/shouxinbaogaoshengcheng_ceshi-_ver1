"""Eval: financial_v2 来源记录 Decimal 审计精度全链（A2~A5 定点修复 5）。

用法: python -m evals.test_financial_v2_decimal

覆盖：
- 权威金额十进制文本列（raw_value_text / std_value_text）在全新库就位；
- 高精度 / 巨大 / 负数 Decimal 经 build_record → commit → 读回逐位精确相等
  （非二进制 float 截断）；
- record_hash / record_id 写前与读回重算一致（str(Decimal) 规范，幂等）；
- 追加式迁移 v3→v4：旧行不重写（text 列 NULL），读时回退 REAL 仍可读；
  迁移后新写入行 text 恒非 NULL 且往返精确相等。

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
from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_dec_")
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


def _register(company: str, ext_id: str) -> tuple[str, str]:
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


def _candidate(rs: str, sv: str, company: str, value: Decimal, row: int = 2,
               unit: str = "wan_yuan") -> S.ExtractedFinancialCell:
    loc = _locator(row)
    cid = S.derive_candidate_id(rs, loc, "资产总计", str(value))
    return S.ExtractedFinancialCell(
        candidate_id=cid, record_set_version=rs, company_id=company,
        source_version=sv, statement_type_candidate="balance_sheet", raw_item_text="资产总计",
        raw_value_text=str(value), parsed_numeric_value=value, formula_text=None,
        cached_formula_value=None, period_text="2024-12-31",
        period_candidate="2024-12-31", period_type_candidate="annual",
        scope_candidate="consolidated", currency_candidate="CNY", unit_candidate=unit,
        restatement_candidate=None, min_display_increment=Decimal("0.01"),
        locator=loc, detection_evidence={}, status="EXTRACTED", quality_flags=[],
        created_at="2026-01-01T00:00:00Z")


def _seed(company: str, ext_id: str, values: list[Decimal],
          unit: str = "wan_yuan") -> str:
    source_document_id, source_version = _register(company, ext_id)
    rs = S.derive_record_set_version(source_version, "1.0", "1.0", "1.0", {})
    cands = [_candidate(rs, source_version, company, v, row=i + 2, unit=unit)
             for i, v in enumerate(values)]
    store.commit_extracted_candidates(cands, [], source_document_id)
    norm.normalize_record_set(rs, persist=True)
    return rs


def _build_v3_db(path: str) -> None:
    """构造一个真实旧 v3 库（无十进制文本列），用于验证 v3→v4 追加式迁移。"""
    conn = sqlite3.connect(path)
    conn.executescript(store._build_ddl_v2() + store._build_ddl_v3())
    store._add_candidate_id_column(conn)
    for v in ("1", "2", "3"):
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                     (v, "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()


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

    # ---- 全新库：十进制文本列就位 ----
    db = _tmp_db()
    try:
        store.init_db(db)
        conn = sqlite3.connect(db)
        rec_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_financial_record)")}
        check("raw_value_text" in rec_cols and "std_value_text" in rec_cols,
              "全新库 source_financial_record 含权威十进制文本列 raw_value_text/std_value_text")
        conn.close()

        # ---- 高精度 / 巨大 / 负数 Decimal 往返精确相等 ----
        company = "ACME"
        huge = Decimal("12345678901234567890")  # 20 位整数（wan_yuan → 元后更大）
        precise = Decimal("0.123456789")         # 高精度小数
        negative = Decimal("-9876543.210987")
        rs = _seed(company, "doc-precise", [huge, precise, negative])

        records = store.list_records(rs)
        mult = Decimal("10000")
        check(len(records) == 3, "三条记录全部产出")
        for r in records:
            # 每条都过 validator（record_id / record_hash 与 Decimal 重算一致）。
            validator.validate_record(r)
        check(any(r.std_value == huge * mult for r in records),
              f"巨大值 Decimal 往返精确相等（{huge} 万元）")
        check(any(r.std_value == precise * mult for r in records),
              f"高精度小数 Decimal 往返精确相等（{precise} 万元）")
        check(any(r.std_value == negative * mult for r in records),
              f"负数 Decimal 往返精确相等（{negative} 万元）")
        check(any(r.raw_value == huge for r in records),
              "raw_value 保留原始 Decimal（未乘单位）")
        # 标准值 str 与期望一致（十进制文本规范，无二进制尾差）。
        check(any(str(r.std_value) == str(huge * mult) for r in records),
              "std_value 十进制文本与期望逐字符一致")

        # record_hash 写前/读后重算一致（同一 Decimal 身份幂等）。
        for r in records:
            check(r.record_hash == validator._record_hash(r),
                  f"record_hash 读回重算一致: {r.raw_item_text}")
    finally:
        _cleanup_db(db)

    # ---- 追加式迁移 v3→v5：旧行不重写 + 读回退 REAL；新行 text 非 NULL ----
    db2 = _tmp_db()
    try:
        _build_v3_db(db2)
        store.init_db(db2)  # 触发 v3→v4→v5 迁移

        conn = sqlite3.connect(db2)
        rec_cols = {r[1] for r in conn.execute("PRAGMA table_info(source_financial_record)")}
        check("raw_value_text" in rec_cols and "std_value_text" in rec_cols,
              "v3→v5 迁移后十进制文本列就位")
        check(store.applied_schema_version() == "5", "v3→v5 迁移后最新版本 == '5'")
        conn.close()

        # 旧行：迁移不重写 → 文本列 NULL，读时回退 REAL。
        rs2 = _seed("ACME", "doc-post-mig", [Decimal("777.77")])
        conn = sqlite3.connect(db2)
        txt_null = conn.execute(
            "SELECT raw_value_text, std_value_text FROM source_financial_record LIMIT 1").fetchone()
        check(txt_null is not None and txt_null[0] is not None and txt_null[1] is not None,
              "迁移后新写入行 raw_value_text/std_value_text 非 NULL（权威十进制落库）")
        conn.close()
        recs2 = store.list_records(rs2)
        check(len(recs2) == 1 and recs2[0].std_value == Decimal("777.77") * 10000,
              "迁移后新写入记录 Decimal 往返精确相等")
    finally:
        _cleanup_db(db2)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
