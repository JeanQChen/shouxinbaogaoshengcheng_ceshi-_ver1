"""R2 §12 修复 九：以全新 run_id 重生成真实验收产物（v5，绝不覆盖历史 v2/v3/v4 目录）。

v5 是本轮「接线与验收纠错」的最终真实产物：以当前（含修复 A/B/C/D/E）的
material build + 六类强验收器 + 授信权威链，重跑真实 ``data/evidence.db``。

重生成对象（全部只读真实 ``data/evidence.db`` 经现有 ToolRegistry + 只读 Evidence reader；
零 LLM/网络/博查；不 init/migrate Evidence/Financial DB；写 harness.db payload 为幂等复用）：
1-4. ``main_business`` / ``core_competitiveness`` / ``major_subsidiaries`` / ``financial_notes``
    —— 含修复 B（源对象清单独立落盘）+ 修复 C（跨页续表同一张表证明）的 material build；
5. ``credit``（授信语义）—— p0 credit seed 复用，供修复 E 预览派生（授信双轴）；
6. ``non_300750_fixture`` —— 合成 fixture（无公司硬编码），独立持久化。

``explicit_cross_reference`` 复用 ``financial_notes`` v5 目录（显式引用事实在财务附注 seed 中）。
seed manifest 复用 v2 目录已落盘的**真实 seed**（身份经 runner 逐条复验；不一致 fail-closed）。
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
from harness.material_slice_runner import (
    SeedEntry,
    _manifest_from_entries,
    load_seed_manifest,
    run_material_slice,
)

RESULTS = Path("evaluation/results")
EVIDENCE_DB = "data/evidence.db"
HARNESS_DB = "data/harness.db"
SUFFIX = "20260916c"

# v2 → v5 run_id 映射（4 个真实类别 + credit；explicit_reference 复用 financial_notes）。
_CATEGORY_V2_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915",
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
}
_CREDIT_V2_DIR = "r2_material_slice_r2_p0_credit_preview_20260915"


def _v5_run_id(category: str) -> str:
    return f"r2_sixcat_v5_{category}_{SUFFIX}"


# --- 非 300750 合成 fixture（复用 _gen_non_300750_fixture.py 的合成块） ---

_COMPANY = "100001"
_DOC = "NDSD_DEMO"
_DOCV = "v-demo-001"
_SETV = "set-demo-1"
_NAME = "示例科技"


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


def _fixture_block(*, page: int, block: int, section: tuple, etype: str,
                   text: str) -> EvidenceReadResult:
    ch = evidence_ids.content_hash(text, None)
    eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, block, ch)
    return EvidenceReadResult(
        evidence_id=eid, company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, source_name="示例年报.pdf", source_type="annual_report",
        page_number=page, block_index=block, section_path=section, evidence_type=etype,
        text=text, structured_payload=None, content_hash=ch)


def _fixture_db(dirpath: Path, blocks) -> Path:
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
        (_COMPANY, _DOC, _DOCV, "示例年报.pdf", None, "annual_report", "annual", "f" * 64,
         100, 20, _NAME, None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current", len(blocks), "2026-01-01"))
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


def _gen_fixture() -> dict:
    block = _fixture_block(
        page=1, block=0, section=("主营业务分析",), etype="paragraph",
        text=("（二）主营业务情况\n表 1 主营业务收入构成表\n单位：万元\n"
              "项目  2025年  2024年\n"
              "智能装备  12,000  10,000\n"
              "软件服务  8,000  6,500\n"
              "合计  20,000  16,500\n"
              "公司主营业务收入主要来自智能装备与软件服务两大板块。\n"))
    tmp = Path(tempfile.mkdtemp(prefix="non300750_fixture_v5_"))
    ev_db = _fixture_db(tmp, [block])
    harness_db = tmp / "harness.db"
    entry = SeedEntry(
        case_id="non-300750-1", company_id=_COMPANY,
        aspect_id="company_business_main.main_business", query="主营业务",
        evidence_id=block.evidence_id, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, page_number=block.page_number,
        block_index=block.block_index, section_path=block.section_path,
        evidence_type=block.evidence_type, source_content_hash=block.content_hash,
        selection_reason="非 300750 合成 fixture（证明无公司硬编码）", text=block.text,
        source_name=block.source_name, source_tool="synthetic_fixture", rank=1, score=1.0,
        is_current_document=True, is_current_set=True)
    manifest = _manifest_from_entries((entry,))
    run_id = _v5_run_id("non_300750_fixture")
    return run_material_slice(
        run_id, manifest, evidence_db=ev_db, harness_db=harness_db,
        out_root=str(RESULTS), budget_profile_name="acceptance")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    summaries: list[dict] = []

    # 1-4. 复用 v2 真实 seed manifest，重跑 v5。
    for category, v2_dir in _CATEGORY_V2_DIRS.items():
        seed_path = RESULTS / v2_dir / "seed_manifest.json"
        manifest = load_seed_manifest(seed_path)
        run_id = _v5_run_id(category)
        summary = run_material_slice(
            run_id, manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
            out_root=str(RESULTS), budget_profile_name="acceptance")
        summaries.append({"category": category, "run_id": run_id, **summary})

    # 5. credit（授信语义）复用 p0 credit seed，重跑 v5。
    credit_seed = RESULTS / _CREDIT_V2_DIR / "seed_manifest.json"
    credit_manifest = load_seed_manifest(credit_seed)
    credit_run_id = "r2_p0_credit_v5_" + SUFFIX
    credit_summary = run_material_slice(
        credit_run_id, credit_manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
        out_root=str(RESULTS), budget_profile_name="acceptance")
    summaries.append({"category": "credit", "run_id": credit_run_id, **credit_summary})

    # 6. 非 300750 合成 fixture（v5）。
    fixture_summary = _gen_fixture()
    summaries.append({"category": "non_300750_fixture",
                      "run_id": _v5_run_id("non_300750_fixture"), **fixture_summary})

    print(json.dumps({"v5_artifacts": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
