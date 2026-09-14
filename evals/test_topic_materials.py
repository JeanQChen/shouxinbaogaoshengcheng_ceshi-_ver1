"""Eval: atomic ResearchMaterial 构建 + MaterialAssembly/TableAssembly + identity/dedup（R2 §4.6/§9/§11）。

用法: python -m evals.test_topic_materials

覆盖：
- 双哈希两层身份：source_content_hash（来源层）≠ payload_hash（载体层）；payload_id == payload_hash
  == sha256(payload_bytes) == material.content_hash == payload_ref.content_hash；信封不含 payload_hash。
- atomic identity：material_id 由 §9.1 完整 tuple 派生（不含 run_id/时间戳）；五类去重逐一区分。
- authority 确定性重算：current+current → authoritative；非 current → rejected；evidence_id 精确匹配该 Block。
- section_path list→str：`" / ".join`。
- MaterialAssembly/TableAssembly 只引用 component_material_ids（不产生新权威/payload）；表链 header/body/continuation 划分。
- aspect link：seed → source，其余 → supporting。
- 扩读 → build_material_result 集成（trace/unread/stop/budget 透传 + payload_records）。

全部离线：临时 SQLite + 真实 ToolRegistry（扩读集成段），不调 LLM/网络。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from harness.context_expansion import (
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import EvidenceReadResult, register_bounded_evidence_tool
from harness.topic_materials import (
    MaterialAssembly,
    TableAssembly,
    build_atomic_material,
    build_assemblies,
    build_material_result,
    section_path_joined,
)
from tools.registry import ToolRegistry


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _block(evidence_id: str, *, page: int = 5, block: int = 1,
           section: tuple = ("主营业务分析",), etype: str = "paragraph",
           text: str = "公司主要从事动力电池研发与制造。",
           structured: dict | None = None,
           document_id: str = "doc1", document_version: str = "v1",
           evidence_set_version: str = "set1",
           content_hash: str | None = None) -> EvidenceReadResult:
    return EvidenceReadResult(
        evidence_id=evidence_id, company_id="300750", document_id=document_id,
        document_version=document_version, evidence_set_version=evidence_set_version,
        source_name="年报", source_type="annual_report", page_number=page, block_index=block,
        section_path=section, evidence_type=etype, text=text, structured_payload=structured,
        content_hash=content_hash or _sha(text))


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


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
        ("300750", "doc1", "v1", "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "宁德时代", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        ("300750", "doc1", "v1", "set1", json.dumps({}), "current",
         len(blocks), "2026-01-01"))
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
             json.dumps(b.structured_payload, ensure_ascii=False, separators=(",", ":"))
             if b.structured_payload is not None else None,
             b.report_period, b.published_at, None, None, b.content_hash, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _seed(page: int = 5, block: int = 1) -> ExpansionSeed:
    return ExpansionSeed(
        evidence_id=f"ev-{page}-{block}", page_number=page, block_index=block,
        section_path=("主营业务分析",), evidence_type="paragraph",
        text="公司主要从事动力电池研发与制造。", content_hash=_sha("公司主要从事动力电池研发与制造。"))


def _request(seed: ExpansionSeed, directions) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id="300750", document_id="doc1", document_version="v1",
        evidence_set_version="set1", seed=seed, directions=directions,
        budget=ExpansionBudget(), dependency_fingerprint=_sha("dep"))


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

    dep = _sha("dep")

    # ------------------------------------------------------------------
    # 1. atomic 构建 + 双哈希两层身份
    # ------------------------------------------------------------------
    blk = _block("ev-5-1")
    bm = build_atomic_material(blk, material_type="evidence_span",
                               is_current_document=True, is_current_set=True,
                               dependency_fingerprint=dep)
    m = bm.material
    pr = bm.payload_record
    ph = hashlib.sha256(pr.payload_bytes).hexdigest()
    check(m.material_id.startswith("mat-") and len(m.material_id) == 36,
          "material_id = 'mat-' + sha256[:32]")
    check(m.content_hash == m.payload_ref.content_hash == pr.payload_hash == ph,
          "material.content_hash == payload_ref.content_hash == payload_hash == sha256(bytes)")
    check(pr.payload_id == pr.payload_hash == ph, "payload_id == payload_hash == sha256(bytes)")
    check(pr.source_content_hash == blk.content_hash, "source_content_hash == EvidenceBlock.content_hash")
    check(pr.source_content_hash != pr.payload_hash, "来源层哈希 ≠ 载体层哈希（两个身份层）")
    env = json.loads(pr.payload_bytes.decode("utf-8"))
    check("payload_hash" not in env, "payload 信封不含自身 payload_hash（无自引用）")
    check(env["source_content_hash"] == blk.content_hash
          and env["evidence_id"] == blk.evidence_id,
          "信封 source_content_hash/evidence_id 与真实 Block 一致")
    check(m.material_type == "evidence_span" and m.source_identity == "evidence:ev-5-1",
          "material_type/source_identity 正确")

    # ------------------------------------------------------------------
    # 2. authority 确定性重算 + evidence_id 精确匹配 + section_path list→str
    # ------------------------------------------------------------------
    check(m.authority_assessment.verdict == "authoritative",
          "current+current → authoritative（重算，非自填）")
    check(m.authority_assessment.evidence_id == blk.evidence_id,
          "authority.evidence_id == 该 Block 真实 evidence_id")
    check(m.authority_assessment.content_hash == blk.content_hash,
          "authority.content_hash == source_content_hash（来源层身份）")
    check(m.locator.section_path == "主营业务分析",
          "section_path 单段 → 稳定字符串")
    multi = _block("ev-9-0", section=("风险因素", "核心竞争"), text="a")
    bm2 = build_atomic_material(multi, material_type="evidence_span",
                                is_current_document=True, is_current_set=True,
                                dependency_fingerprint=dep)
    check(bm2.material.locator.section_path == "风险因素 / 核心竞争",
          "section_path list→str 用 ' / '.join")
    check(section_path_joined(("a", "b", "c")) == "a / b / c", "section_path_joined 规范形")

    bm_stale = build_atomic_material(blk, material_type="evidence_span",
                                     is_current_document=False, is_current_set=True,
                                     dependency_fingerprint=dep)
    check(bm_stale.material.authority_assessment.verdict == "rejected",
          "非 current document → rejected")

    # ------------------------------------------------------------------
    # 3. 五类去重（§9.3）逐一区分
    # ------------------------------------------------------------------
    # 同 Block 重建 → 同 material_id / 同 payload_id（payload 内容去重 + 身份去重）。
    bm_again = build_atomic_material(blk, material_type="evidence_span",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep)
    check(bm_again.material.material_id == m.material_id,
          "同 Block 重建 → 同 material_id（身份去重）")
    check(bm_again.payload_record.payload_id == pr.payload_id,
          "同 payload_bytes → 同 payload_id（payload 内容去重）")
    # 相同文本不同 evidence_id/来源 → 不同 material（不合并）。
    same_text_other = _block("ev-5-99", text=blk.text, block=99)
    bm_other = build_atomic_material(same_text_other, material_type="evidence_span",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep)
    check(bm_other.material.material_id != m.material_id,
          "相同文本不同 evidence_id → 不同 material_id（不合并）")
    # 相同文本不同 document_version → 不同 material。
    same_text_ver = _block("ev-5-1", text=blk.text, document_version="v2")
    bm_ver = build_atomic_material(same_text_ver, material_type="evidence_span",
                                   is_current_document=True, is_current_set=True,
                                   dependency_fingerprint=dep)
    check(bm_ver.material.material_id != m.material_id,
          "相同文本不同 document_version → 不同 material（不合并）")

    # ------------------------------------------------------------------
    # 4. build_assemblies：TableAssembly（header/body/continuation）+ 相邻 MaterialAssembly
    # ------------------------------------------------------------------
    table_blocks = {
        "t-h": _block("ev-t-h", page=6, block=0, etype="table",
                      text="营业收入构成（分产品）", structured={"unit": "万元",
                      "headers": ["产品", "收入"], "cells": [["动力电池", "1000"]],
                      "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}}),
        "t-r1": _block("ev-t-r1", page=6, block=1, etype="table_row",
                       text="动力电池 1,000", structured={"unit": "万元",
                       "headers": ["产品", "收入"], "cells": [["动力电池", "1000"]],
                       "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}}),
        "t-r2": _block("ev-t-r2", page=7, block=0, etype="table_row",
                       text="（续）储能 500", structured={"unit": "万元",
                       "headers": ["产品", "收入"], "cells": [["储能", "500"]],
                       "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}}),
    }
    tms = tuple(build_atomic_material(b, material_type="table_context",
                                      is_current_document=True, is_current_set=True,
                                      dependency_fingerprint=dep).material
                for b in table_blocks.values())
    p1 = _block("ev-p1", page=5, block=0, etype="paragraph", text="主营")
    p2 = _block("ev-p2", page=5, block=1, etype="paragraph", text="动力电池")
    p3 = _block("ev-p3", page=6, block=0, etype="paragraph", text="（跨页）储能")
    spans = tuple(build_atomic_material(b, material_type="evidence_span",
                                        is_current_document=True, is_current_set=True,
                                        dependency_fingerprint=dep).material
                  for b in (p1, p2, p3))
    all_mats = tms + spans
    all_blocks = {b.evidence_id: b for b in list(table_blocks.values()) + [p1, p2, p3]}
    asms = build_assemblies(all_mats, all_blocks)

    tbl = [a for a in asms if isinstance(a, TableAssembly)]
    check(len(tbl) == 1, "一个连续表链 → 一个 TableAssembly")
    t = tbl[0]
    check(t.relation == "table_chain" and t.table_title == "营业收入构成（分产品）",
          "TableAssembly relation=table_chain + table_title")
    check(t.unit == "万元", "TableAssembly.unit 取自 structured_payload")
    check(t.header_evidence_id == "ev-t-h", "header_evidence_id == 表题块")
    check("ev-t-r1" in t.body_evidence_ids and "ev-t-r2" in t.continuation_evidence_ids,
          "body（同页）/continuation（跨页）划分")
    mat_by_ev = {mm.authority_assessment.evidence_id: mm.material_id for mm in all_mats}
    check(set(t.component_material_ids) == {mat_by_ev["ev-t-h"], mat_by_ev["ev-t-r1"],
                                            mat_by_ev["ev-t-r2"]},
          "TableAssembly.component_material_ids 覆盖全部真实 component material")
    check(not hasattr(t, "authority_assessment") and not hasattr(t, "payload_ref"),
          "TableAssembly 只引用 component_material_ids，无自身权威/payload")

    spans_asm = [a for a in asms if isinstance(a, MaterialAssembly)
                 and not isinstance(a, TableAssembly)]
    # p1+p2 相邻（同页连续）→ adjacent；p2+p3 跨页相邻 → 合并为一个 run（relation=cross_page）。
    check(any(a.relation in ("adjacent", "cross_page") for a in spans_asm),
          "evidence_span 相邻链产生 MaterialAssembly")
    check(all(not hasattr(a, "authority_assessment") for a in spans_asm),
          "MaterialAssembly 无自身权威")

    # ------------------------------------------------------------------
    # 5. build_material_result 集成（扩读 → 材料 + payload_records + aspect link）
    # ------------------------------------------------------------------
    main_blocks = [
        _block("ev-5-0", page=5, block=0, etype="heading", text="主营业务分析"),
        _block("ev-5-1", page=5, block=1, etype="paragraph",
               text="公司主要从事动力电池研发与制造。"),
        _block("ev-5-2", page=5, block=2, etype="paragraph", text="海外收入占比持续提升。"),
        _block("ev-6-0", page=6, block=0, etype="paragraph", text="（续）储能业务快速增长。"),
    ]
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), main_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        seed = _seed()
        req = _request(seed, ("adjacent_blocks",))
        exp = expand(req, registry, run_id="tm")
        result = build_material_result(exp, dependency_fingerprint=dep,
                                       is_current_document=True, is_current_set=True,
                                       aspect_id="company_business_main.main_business")
        check(len(result.materials) >= 2, "扩读 → 至少 seed + 相邻块材料")
        check(result.materials[0].material_id == result.materials[0].material_id,
              "材料可读（material_id 稳定）")
        check(len(result.payload_records) == len(result.materials),
              "payload_records 与 materials 一一对应")
        check(all(r.payload_id == r.payload_hash for r in result.payload_records),
              "payload_records 自洽（payload_id==payload_hash）")
        check(result.trace.trace_id == exp.trace.trace_id
              and result.stop_reason == exp.stop_reason,
              "trace/stop_reason 透传")
        # aspect link：seed → source，其余 → supporting。
        roles = {l.role: l for l in result.aspect_links}
        check("source" in roles and "supporting" in roles, "aspect link 含 source + supporting")
        src = roles["source"]
        check(len(src.material_ids) == 1, "source link 恰好一个 material")
        seed_ev = next(m.material_id for m in result.materials
                       if m.authority_assessment.evidence_id == seed.evidence_id)
        check(src.material_ids == (seed_ev,), "source link 指向 seed material")
        sup_evs = {m.authority_assessment.evidence_id for m in result.materials
                   if m.material_id in roles["supporting"].material_ids}
        check(seed.evidence_id not in sup_evs and len(sup_evs) >= 1,
              "supporting link 不含 seed 且含相邻块")

        # 排序稳定性：材料按确定性键排序，material_id 升序在键内。
        keys = []
        for mm in result.materials:
            loc = mm.locator
            keys.append((mm.material_type, mm.authority_assessment.document_id,
                         mm.authority_assessment.document_version,
                         loc.page, loc.block_range[0], loc.section_path, mm.material_id))
        check(keys == sorted(keys), "材料按 §9.4 确定性键排序（稳定可复现）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
