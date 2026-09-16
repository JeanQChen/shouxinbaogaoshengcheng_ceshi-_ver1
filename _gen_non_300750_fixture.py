"""R2 §12 第六类：非 300750 合成 fixture 生成器（evaluation-only，一次性）。

生成一个独立、可持久化的纵向验收产物目录，证明材料构建链无 300750 / 宁德时代 / 固定页码 /
答案关键词硬编码：合成公司 100001（示例科技）、通用文档、通用页码、通用业务分板块表格。

只写 evaluation/results 产物目录，不改冻结 Contract/SourcePolicy/WritingSpec/PresentationProfile，
不 init/migrate 生产 Evidence DB（临时 evidence.db + harness.db 隔离）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import ids as evidence_ids
from harness.evidence_reader import EvidenceReadResult
from harness.material_slice_runner import SeedEntry, _manifest_from_entries, run_material_slice


COMPANY = "100001"
DOC = "NDSD_DEMO"
DOCV = "v-demo-001"
SETV = "set-demo-1"
NAME = "示例科技"


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


def _block(*, page: int, block: int, section: tuple, etype: str, text: str) -> EvidenceReadResult:
    ch = evidence_ids.content_hash(text, None)
    eid = evidence_ids.make_evidence_id(COMPANY, DOC, DOCV, SETV, page, block, ch)
    return EvidenceReadResult(
        evidence_id=eid, company_id=COMPANY, document_id=DOC, document_version=DOCV,
        evidence_set_version=SETV, source_name="示例年报.pdf", source_type="annual_report",
        page_number=page, block_index=block, section_path=section, evidence_type=etype,
        text=text, structured_payload=None, content_hash=ch)


def _make_db(dirpath: Path, blocks) -> Path:
    db = dirpath / "evidence.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE documents (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, source_name TEXT NOT NULL,
            source_path TEXT, source_type TEXT NOT NULL, material_group TEXT NOT NULL,
            file_sha256 TEXT NOT NULL, file_size INTEGER NOT NULL, page_count INTEGER,
            declared_company_name TEXT, detected_company_names TEXT,
            parser_version TEXT NOT NULL, status TEXT NOT NULL, quality_flags TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version)
        );
        CREATE TABLE evidence_sets (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            dependency_versions TEXT NOT NULL, status TEXT NOT NULL,
            block_count INTEGER, created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version, evidence_set_version)
        );
        CREATE TABLE evidence_blocks (
            evidence_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            source_name TEXT NOT NULL, source_type TEXT NOT NULL, source_uri TEXT,
            page_number INTEGER NOT NULL, block_index INTEGER NOT NULL,
            section_path TEXT, evidence_type TEXT NOT NULL, text TEXT NOT NULL,
            structured_payload TEXT, report_period TEXT, published_at TEXT,
            entities TEXT, quality_flags TEXT, content_hash TEXT NOT NULL,
            builder_version TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (company_id, document_id, document_version, evidence_set_version, page_number, block_index)
        );
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (COMPANY, DOC, DOCV, "示例年报.pdf", None, "annual_report", "annual", "f" * 64,
         100, 20, NAME, None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (COMPANY, DOC, DOCV, SETV, json.dumps({}), "current", len(blocks), "2026-01-01"))
    for b in blocks:
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b.evidence_id, "1", b.company_id, b.document_id, b.document_version,
             b.evidence_set_version, b.source_name, b.source_type, b.source_uri,
             b.page_number, b.block_index, _sp(b.section_path), b.evidence_type, b.text,
             None, b.report_period, b.published_at, None, None, b.content_hash, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # 通用分业务表格（摊平单 block），无任何 300750/宁德时代/固定页码/答案关键词。
    block = _block(
        page=1, block=0, section=("主营业务分析",), etype="paragraph",
        text=("（二）主营业务情况\n表 1 主营业务收入构成表\n单位：万元\n"
              "项目  2025年  2024年\n"
              "智能装备  12,000  10,000\n"
              "软件服务  8,000  6,500\n"
              "合计  20,000  16,500\n"
              "公司主营业务收入主要来自智能装备与软件服务两大板块。\n"))

    tmp = Path(tempfile.mkdtemp(prefix="non300750_fixture_"))
    ev_db = _make_db(tmp, [block])
    harness_db = tmp / "harness.db"

    entry = SeedEntry(
        case_id="non-300750-1", company_id=COMPANY,
        aspect_id="company_business_main.main_business", query="主营业务",
        evidence_id=block.evidence_id, document_id=DOC, document_version=DOCV,
        evidence_set_version=SETV, page_number=block.page_number,
        block_index=block.block_index, section_path=block.section_path,
        evidence_type=block.evidence_type, source_content_hash=block.content_hash,
        selection_reason="非 300750 合成 fixture（证明无公司硬编码）", text=block.text,
        source_name=block.source_name, source_tool="synthetic_fixture", rank=1, score=1.0,
        is_current_document=True, is_current_set=True)

    manifest = _manifest_from_entries((entry,))
    run_id = "r2_sixcat_v2_non_300750_fixture_20260915"
    summary = run_material_slice(
        run_id, manifest, evidence_db=ev_db, harness_db=harness_db,
        out_root="evaluation/results", budget_profile_name="acceptance")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
