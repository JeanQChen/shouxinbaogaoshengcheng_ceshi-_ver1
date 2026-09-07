"""真实电子年报 PDF 验收（A2~A5 定点修复 5）：走 Evidence 联动入口完成三张主表定位 + 金额抽样。

用法: python -m scripts.verify_pdf_acceptance <pdf> --company 300750 [--pages 114-124]
     [--declared-name 宁德时代新能源科技股份有限公司] [--detected-name 宁德时代新能源科技股份有限公司]

流程（全部走已实现接口，不绕过契约）：
1. source_registry.register_source —— 登记财务来源，PDF 自动联动 Phase 1 Evidence Registry
   写入并校验 document_id / document_version（evidence.store.register_document）；
2. evidence.store.get_document —— 回查 Evidence 文档身份，确认 company / file_sha256 /
   document_version 一致；
3. pdf_table_extractor.extract_pdf —— 定位三张主表（资产负债表/利润表/现金流量表），
   逐数据行×期间列生成带真实 pdf_page / table_bbox / cell_bbox 的候选；
4. 输出：来源登记摘要、Evidence 身份、表区域坐标、每张主表抽样金额 + 真实坐标。

默认用临时数据库（不污染 data/financial_v2.db / data/evidence.db），可用 --db 指定。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import schema as S
from financial_v2 import source_registry
from financial_v2 import store
from financial_v2.pdf_table_extractor import (
    PdfFinancialExtractionPolicy,
    extract_pdf,
)

_STATEMENT_CN = {
    "balance_sheet": "资产负债表",
    "income_statement": "利润表",
    "cash_flow": "现金流量表",
}


def _round3(v: float) -> float:
    return round(float(v), 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="真实电子年报 PDF 路径")
    ap.add_argument("--company", required=True, help="company_id（如 300750）")
    ap.add_argument("--pages", default=None, help="页范围，如 114-124（1-based 物理页）")
    ap.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省临时库）")
    ap.add_argument("--evidence-db", default=None, help="Evidence SQLite 路径（缺省临时库）")
    ap.add_argument("--source-doc-id", default=None, help="外部业务文档编号（缺省用文件名）")
    ap.add_argument("--declared-name", default=None, help="申报主体名（缺省 None → subject_match_status=unverified）")
    ap.add_argument("--detected-name", default=None, help="检测主体名（缺省 None → subject_match_status=unverified）")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(json.dumps({"error": f"文件不存在: {pdf_path}"}, ensure_ascii=False), file=sys.stderr)
        return 2

    fin_db = args.db
    evid_db = args.evidence_db
    tmpdir = None
    if fin_db is None or evid_db is None:
        tmpdir = tempfile.mkdtemp(prefix="verify_pdf_")
        fin_db = fin_db or str(Path(tmpdir) / "financial_v2.db")
        evid_db = evid_db or str(Path(tmpdir) / "evidence.db")

    try:
        store.init_db(fin_db)
        from evidence import store as estore
        estore.init_db(evid_db)

        ctx = S.FinancialSourceContext(
            company_id=args.company,
            source_name=pdf_path.name,
            source_class="financial_statement",
            external_document_id=args.source_doc_id or pdf_path.name,
            declared_company_name=args.declared_name,
            detected_company_name=args.detected_name,
        )
        reg = source_registry.register_source(str(pdf_path), ctx, evidence_db_path=evid_db)
        v = reg.version
        doc = reg.document

        # Evidence 回查：确认 document_id/document_version/company/sha 一致。
        edoc = estore.get_document(doc.company_id, v.document_id, v.document_version)
        evidence_ok = (
            edoc is not None
            and edoc.company_id == doc.company_id
            and edoc.file_sha256 == v.file_sha256
            and edoc.document_version == v.document_version
        )

        policy = PdfFinancialExtractionPolicy(
            file_path=str(pdf_path),
            pages=args.pages,
        )
        res = extract_pdf(v.source_version, policy, persist=True)

        # 表区域（按 statement_type + scope 归组）。
        regions_by_stmt: dict[str, list[dict]] = {}
        for r in res.table_regions:
            key = f"{r.statement_type}|{r.scope or 'None'}"
            regions_by_stmt.setdefault(key, []).append({
                "pdf_page": r.pdf_page,
                "table_id": r.table_id,
                "statement_type": r.statement_type,
                "scope": r.scope,
                "table_bbox": [_round3(x) for x in r.table_bbox],
                "unit": r.unit,
                "header_row_index": r.header_row_index,
                "item_column_index": r.item_column_index,
                "period_columns": r.period_columns,
            })

        # 每张主表抽样：优先取已解析金额（EXTRACTED）的候选（按 statement_type 归组）。
        samples_by_stmt: dict[str, list[dict]] = {}
        for c in res.candidates:
            st = c.statement_type_candidate
            samples_by_stmt.setdefault(st, []).append({
                "item": c.raw_item_text,
                "value": str(c.parsed_numeric_value) if c.parsed_numeric_value is not None else None,
                "period": c.period_candidate,
                "status": c.status,
                "unit": c.unit_candidate,
                "scope": c.scope_candidate,
                "pdf_page": c.locator.pdf.pdf_page,
                "table_id": c.locator.pdf.table_id,
                "cell_bbox": [_round3(x) for x in c.locator.pdf.bbox],
            })

        def _sample(rows: list[dict]) -> list[dict]:
            extracted = [r for r in rows if r["value"] is not None]
            return (extracted + rows)[:5]

        report = {
            "source_registration": {
                "company_id": doc.company_id,
                "source_document_id": doc.source_document_id,
                "source_version": v.source_version,
                "file_sha256": v.file_sha256[:16],
                "file_size": v.file_size,
                "subject_match_status": doc.subject_match_status,
                "reused": reg.reused,
                "document_id": v.document_id,
                "document_version": v.document_version,
            },
            "evidence_lookup": {
                "found": edoc is not None,
                "consistent": evidence_ok,
                "document_id": edoc.document_id if edoc else None,
                "document_version": edoc.document_version if edoc else None,
                "company_id": edoc.company_id if edoc else None,
            },
            "record_set_version": res.record_set_version,
            "candidate_count": len(res.candidates),
            "issue_count": len(res.issues),
            "issue_types": sorted({i.issue_type for i in res.issues}),
            "table_regions_by_statement": regions_by_stmt,
            "sample_candidates_by_statement": {
                st: _sample(rows) for st, rows in samples_by_stmt.items()
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if tmpdir is not None:
            for f in os.listdir(tmpdir):
                try:
                    os.remove(Path(tmpdir) / f)
                except OSError:
                    pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
