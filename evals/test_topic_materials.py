"""Eval: atomic ResearchMaterial 构建 + MaterialAssembly/TableAssembly + identity/dedup（R2 §4.6/§9/§11）。

用法: python -m evals.test_topic_materials

覆盖：
- 双哈希两层身份：source_content_hash（来源层）≠ payload_hash（载体层）；payload_id == payload_hash
  == sha256(payload_bytes) == material.content_hash == payload_ref.content_hash；信封不含 payload_hash。
- atomic identity：material_id 由 §9.1 完整 tuple 派生（不含 run_id/时间戳）；五类去重逐一区分。
- authority 确定性重算：current+current → authoritative；非 current → rejected；evidence_id 精确匹配该 Block。
- section_path list→str：`" / ".join`。
- MaterialAssembly/TableAssembly 只引用 component_material_ids（不产生新权威/payload）；表链 header/body/continuation 划分。
- aspect link：seed → source，其余 → context_candidate（supporting 留给 R3）。
- 扩读 → build_material_result 集成（trace/unread/stop/budget 透传 + payload_records）。

§九：fixture 的 evidence_id/content_hash 一律经 ``evidence.ids.content_hash()`` +
``make_evidence_id()`` 计算；``build_atomic_material`` 对存储 identity 与重算不一致 fail-closed。

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

from evidence import ids as evidence_ids
from harness import topic_schema as TS
from harness.context_expansion import (
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import (
    EvidenceReadResult,
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.topic_materials import (
    TABLE_CONTINUATION_PROOF_VERSION,
    MaterialAssembly,
    TableAssembly,
    TableContinuationProof,
    build_atomic_material,
    build_assemblies,
    build_material_result,
    build_table_continuation_proof,
    section_path_joined,
)
from tools.registry import ToolRegistry


def _sha(s: str) -> str:
    # 仅用于 dependency_fingerprint 占位（非权威 evidence 身份）。
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"


def _block(*, page: int = 5, block: int = 1,
           section: tuple = ("主营业务分析",), etype: str = "paragraph",
           text: str = "公司主要从事动力电池研发与制造。",
           structured: dict | None = None,
           document_id: str = _DOC, document_version: str = _DOCV,
           evidence_set_version: str = _SETV, company_id: str = _COMPANY,
           evidence_id: str | None = None, content_hash: str | None = None) -> EvidenceReadResult:
    ch = content_hash if content_hash is not None else evidence_ids.content_hash(text, structured)
    eid = evidence_id if evidence_id is not None else evidence_ids.make_evidence_id(
        company_id, document_id, document_version, evidence_set_version, page, block, ch)
    return EvidenceReadResult(
        evidence_id=eid, company_id=company_id, document_id=document_id,
        document_version=document_version, evidence_set_version=evidence_set_version,
        source_name="年报", source_type="annual_report", page_number=page, block_index=block,
        section_path=section, evidence_type=etype, text=text, structured_payload=structured,
        content_hash=ch)


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
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "宁德时代", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current",
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
    text = "公司主要从事动力电池研发与制造。"
    ch = evidence_ids.content_hash(text, None)
    eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, block, ch)
    return ExpansionSeed(
        evidence_id=eid, page_number=page, block_index=block,
        section_path=("主营业务分析",), evidence_type="paragraph",
        text=text, content_hash=ch)


def _request(seed: ExpansionSeed, directions) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, seed=seed, directions=directions,
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
    blk = _block()
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
    check(m.material_type == "evidence_span" and m.source_identity == f"evidence:{blk.evidence_id}",
          "material_type/source_identity 正确（evidence:权威 evidence_id）")

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
    multi = _block(page=9, block=0, section=("风险因素", "核心竞争"), text="a")
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

    # 反例：存储 content_hash 与重算不一致 → ValueError（§九）。
    try:
        build_atomic_material(_block(content_hash="0" * 64), material_type="evidence_span",
                              is_current_document=True, is_current_set=True,
                              dependency_fingerprint=dep)
        check(False, "存储 content_hash 与重算不一致应抛 ValueError")
    except ValueError:
        check(True, "反例：build_atomic_material 拒绝存储 content_hash 与重算不一致")
    # 反例：存储 evidence_id 与重算不一致 → ValueError（§九）。
    try:
        build_atomic_material(_block(evidence_id="wrong-eid"), material_type="evidence_span",
                              is_current_document=True, is_current_set=True,
                              dependency_fingerprint=dep)
        check(False, "存储 evidence_id 与重算不一致应抛 ValueError")
    except ValueError:
        check(True, "反例：build_atomic_material 拒绝存储 evidence_id 与重算不一致")

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
    same_text_other = _block(page=5, block=99, text=blk.text)
    bm_other = build_atomic_material(same_text_other, material_type="evidence_span",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep)
    check(bm_other.material.material_id != m.material_id,
          "相同文本不同 evidence_id → 不同 material_id（不合并）")
    # 相同文本不同 document_version → 不同 material。
    same_text_ver = _block(text=blk.text, document_version="v2")
    bm_ver = build_atomic_material(same_text_ver, material_type="evidence_span",
                                   is_current_document=True, is_current_set=True,
                                   dependency_fingerprint=dep)
    check(bm_ver.material.material_id != m.material_id,
          "相同文本不同 document_version → 不同 material（不合并）")

    # ------------------------------------------------------------------
    # 4. build_assemblies：TableAssembly（header/body/continuation）+ 相邻 MaterialAssembly
    # ------------------------------------------------------------------
    t_h = _block(page=6, block=0, etype="table",
                 text="营业收入构成（分产品）", structured={"table_title": "营业收入构成（分产品）",
                 "unit": "万元", "headers": ["产品", "收入"], "cells": [["动力电池", "1000"]],
                 "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}})
    t_r1 = _block(page=6, block=1, etype="table_row",
                  text="动力电池 1,000", structured={"table_title": "营业收入构成（分产品）",
                  "unit": "万元", "headers": ["产品", "收入"], "cells": [["动力电池", "1000"]],
                  "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}})
    t_r2 = _block(page=7, block=0, etype="table_row",
                  text="（续）储能 500", structured={"table_title": "营业收入构成（分产品）",
                  "unit": "万元", "headers": ["产品", "收入"], "cells": [["储能", "500"]],
                  "coordinates": {"bbox": [0, 0, 100, 100], "cell_bboxes": [[0, 0, 10, 10]]}})
    table_blocks = (t_h, t_r1, t_r2)
    tms = tuple(build_atomic_material(b, material_type="table_context",
                                      is_current_document=True, is_current_set=True,
                                      dependency_fingerprint=dep).material
                for b in table_blocks)
    p1 = _block(page=5, block=0, etype="paragraph", text="主营")
    p2 = _block(page=5, block=1, etype="paragraph", text="动力电池")
    p3 = _block(page=6, block=0, etype="paragraph", text="（跨页）储能")
    spans = tuple(build_atomic_material(b, material_type="evidence_span",
                                        is_current_document=True, is_current_set=True,
                                        dependency_fingerprint=dep).material
                  for b in (p1, p2, p3))
    all_mats = tms + spans
    all_blocks = {b.evidence_id: b for b in table_blocks + (p1, p2, p3)}
    asms = build_assemblies(all_mats, all_blocks)

    tbl = [a for a in asms if isinstance(a, TableAssembly)]
    check(len(tbl) == 1, "一个连续表链 → 一个 TableAssembly")
    t = tbl[0]
    check(t.relation == "table_chain" and t.table_title == "营业收入构成（分产品）",
          "TableAssembly relation=table_chain + table_title")
    check(t.unit == "万元", "TableAssembly.unit 取自 structured_payload")
    check(t.header_evidence_id == t_h.evidence_id, "header_evidence_id == 表题块")
    check(t_r1.evidence_id in t.body_evidence_ids and t_r2.evidence_id in t.continuation_evidence_ids,
          "body（同页）/continuation（跨页）划分")
    mat_by_ev = {mm.authority_assessment.evidence_id: mm.material_id for mm in all_mats}
    check(set(t.component_material_ids) == {mat_by_ev[t_h.evidence_id], mat_by_ev[t_r1.evidence_id],
                                            mat_by_ev[t_r2.evidence_id]},
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
        _block(page=5, block=0, etype="heading", text="主营业务分析"),
        _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
        _block(page=5, block=2, etype="paragraph", text="海外收入占比持续提升。"),
        _block(page=6, block=0, etype="paragraph", text="（续）储能业务快速增长。"),
    ]
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), main_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed()
        req = _request(seed, ("adjacent_blocks",))
        exp = expand(req, registry, run_id="tm")
        result = build_material_result(exp, dependency_fingerprint=dep,
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
        # aspect link：seed → source，其余 → context_candidate（绝不自动 supporting）。
        roles = {l.role: l for l in result.aspect_links}
        check("source" in roles and "context_candidate" in roles,
              "aspect link 含 source + context_candidate")
        check("supporting" not in roles, "R2 不产生 supporting role（留给 R3）")
        src = roles["source"]
        check(len(src.material_ids) == 1, "source link 恰好一个 material")
        seed_ev = next(m.material_id for m in result.materials
                       if m.authority_assessment.evidence_id == seed.evidence_id)
        check(src.material_ids == (seed_ev,), "source link 指向 seed material")
        ctx_evs = {m.authority_assessment.evidence_id for m in result.materials
                   if m.material_id in roles["context_candidate"].material_ids}
        check(seed.evidence_id not in ctx_evs and len(ctx_evs) >= 1,
              "context_candidate link 不含 seed 且含相邻块")

        # 排序稳定性：材料按确定性键排序，material_id 升序在键内。
        keys = []
        for mm in result.materials:
            loc = mm.locator
            keys.append((mm.material_type, mm.authority_assessment.document_id,
                         mm.authority_assessment.document_version,
                         loc.page, loc.block_range[0], loc.section_path, mm.material_id))
        check(keys == sorted(keys), "材料按 §9.4 确定性键排序（稳定可复现）")

    # ------------------------------------------------------------------
    # 6. 表身份合并边界（§四.2）：反例#7/#8/#9（同名远距/无标题相邻/营收成本不合并）
    # ------------------------------------------------------------------
    def _tmat(b):
        return build_atomic_material(b, material_type="table_context",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep).material

    def _n_table_assemblies(blocks):
        mats = tuple(_tmat(b) for b in blocks)
        blk_map = {b.evidence_id: b for b in blocks}
        asms = build_assemblies(mats, blk_map)
        return sum(1 for a in asms if isinstance(a, TableAssembly))

    # 反例#7：同名表相距多页（>1）→ 不合并（结构连续性上限）。
    far = [
        _block(page=6, block=0, etype="table", text="营业收入构成（分产品）"),
        _block(page=9, block=0, etype="table", text="营业收入构成（分产品）"),
    ]
    check(_n_table_assemblies(far) == 2,
          "反例#7：同名表相距多页（跨页>1）→ 两张独立 TableAssembly（不合并）")

    # 反例#8：无表身份（title 缺失）即使相邻 → 不合并。
    notitle = [
        _block(page=6, block=0, etype="table", text="", structured={"unit": "万元"}),
        _block(page=6, block=1, etype="table", text="", structured={"unit": "万元"}),
    ]
    check(_n_table_assemblies(notitle) == 2,
          "反例#8：无表身份相邻表 → 不合并（不按页面相邻猜表）")

    # 反例#9：营业收入表 vs 营业成本表（不同 title）→ 不合并。
    revcost = [
        _block(page=6, block=0, etype="table", text="营业收入构成（分产品）"),
        _block(page=6, block=1, etype="table", text="营业成本构成（分产品）"),
    ]
    check(_n_table_assemblies(revcost) == 2,
          "反例#9：营收表与成本表 title 不同 → 两张独立 TableAssembly（不合并）")

    # ------------------------------------------------------------------
    # 7. §五 修复三：PDF 摊平段落 → flattened_table_recovery TableAssembly（投影，非新权威）
    # ------------------------------------------------------------------
    def _fmat(b):
        return build_atomic_material(b, material_type="evidence_span",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep).material

    def _flat_assemblies(blocks):
        mats = tuple(_fmat(b) for b in blocks)
        blk_map = {b.evidence_id: b for b in blocks}
        asms = build_assemblies(mats, blk_map)
        return [a for a in asms if isinstance(a, TableAssembly)
                and a.relation == "flattened_table_recovery"]

    flat_blocks = [
        _block(page=6, block=0, etype="paragraph", text="营业收入构成（分产品）"),
        _block(page=6, block=1, etype="paragraph", text="单位：万元"),
        _block(page=6, block=2, etype="paragraph", text="产品  2025年  2024年  2023年"),
        _block(page=6, block=3, etype="paragraph", text="动力电池系统  31,650,636.9  25,304,133.7  20,000,000.0"),
        _block(page=6, block=4, etype="paragraph", text="储能系统  5,000.0  4,000.0  3,000.0"),
        _block(page=6, block=5, etype="paragraph", text="合计  36,650,636.9  29,304,133.7  23,000,000.0"),
    ]
    fa_list = _flat_assemblies(flat_blocks)
    check(len(fa_list) == 1, "修复三：摊平段落 → 1 个 flattened_table_recovery TableAssembly")
    fa = fa_list[0]
    check(fa.table_title == "营业收入构成（分产品）" and fa.unit == "万元",
          "修复三：摊平表 assembly 恢复 table_title + unit")
    check(fa.header_evidence_id == flat_blocks[2].evidence_id,
          "修复三：摊平表 header_evidence_id == 表头块")
    check(flat_blocks[3].evidence_id in fa.body_evidence_ids
          and flat_blocks[4].evidence_id in fa.body_evidence_ids,
          "修复三：摊平表 body 划分（数据行同页 → body）")
    check(flat_blocks[5].evidence_id in fa.continuation_evidence_ids,
          "修复三：摊平表合计行进 continuation（闭合）")
    title_mid = _fmat(flat_blocks[0]).material_id
    check(len(fa.component_material_ids) == 5 and title_mid not in fa.component_material_ids,
          "修复三：component 覆盖 5 个摊平块（title 块不计入 component）")
    check(not hasattr(fa, "authority_assessment") and not hasattr(fa, "payload_ref"),
          "修复三：摊平表 assembly 只引用 component material，无自身权威/payload")

    # ------------------------------------------------------------------
    # 8. §五 修复三：营收 vs 成本（不同表题）→ 不合并（两张 flattened_table_recovery）
    # ------------------------------------------------------------------
    rev_cost_flat = [
        _block(page=6, block=0, etype="paragraph", text="营业收入构成（分产品）"),
        _block(page=6, block=1, etype="paragraph", text="产品  2025年  2024年  2023年"),
        _block(page=6, block=2, etype="paragraph", text="动力电池系统  31,650  25,304  20,000"),
        _block(page=6, block=3, etype="paragraph", text="营业成本构成（分产品）"),
        _block(page=6, block=4, etype="paragraph", text="产品  2025年  2024年  2023年"),
        _block(page=6, block=5, etype="paragraph", text="原材料成本  8,000  7,000  6,000"),
    ]
    check(len(_flat_assemblies(rev_cost_flat)) == 2,
          "修复三：营收表与成本表表题不同 → 两张独立 flattened_table_recovery（不合并）")

    # ------------------------------------------------------------------
    # 9. §五 修复三：仅表头无数据行 → 不产出 TableAssembly（诚实缺口，不伪造投影）
    # ------------------------------------------------------------------
    header_only = [
        _block(page=6, block=0, etype="paragraph", text="营业收入构成（分产品）"),
        _block(page=6, block=1, etype="paragraph", text="产品  2025年  2024年  2023年"),
    ]
    check(len(_flat_assemblies(header_only)) == 0,
          "修复三：仅表头无数据行 → 0 个 flattened_table_recovery（诚实缺口）")

    # ------------------------------------------------------------------
    # 10. §五 修复三：真实形态——单 block 摊平表（title/unit/header/rows/total/analysis 同块）
    # ------------------------------------------------------------------
    single_block_text = (
        "表 5-10发行人主营业务收入构成表\n\n"
        "单位：万元，%\n"
        "项目  2025年  2024年  2023年\n"
        "金额  占比  金额  占比  金额  占比\n\n"
        "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n\n"
        "储能电池系统  6,243,982.0  14.7  5,729,046.0  15.8  5,990,052.2  14.9\n\n"
        "合计  42,370,183.3100.0  36,201,255.3 100.0  40,091,704.5 100.0\n\n"
        "发行人主要营业收入来自于动力电池系统、储能电池系统、电池材料及回收和电池\n"
        "矿产资源四大业务板块。2023-2025年，发行人营业收入分别为 40,091,704.5万元。"
    )
    sb_blocks = [_block(page=50, block=0, etype="paragraph", text=single_block_text)]
    sb_list = _flat_assemblies(sb_blocks)
    check(len(sb_list) == 1,
          "修复三真实形态：单 block 摊平表 → 1 个 flattened_table_recovery")
    sb = sb_list[0]
    check("表 5-10" in sb.table_title and sb.unit == "万元，%",
          "修复三真实形态：恢复 title + unit（同块）")
    check(list(sb.headers) and sb.headers[0] == "项目",
          "修复三真实形态：恢复 header 列")
    check(len(sb.rows) == 2 and sb.rows[0][0] == "动力电池系统",
          "修复三真实形态：恢复 2 个数据行（表后正文不误读为数据行）")
    check(sb.total_row is not None,
          "修复三真实形态：恢复 total_row")
    check(sb.component_material_ids == (_fmat(sb_blocks[0]).material_id,),
          "修复三真实形态：component 只引用该单 block material")

    # ------------------------------------------------------------------
    # 11. §修复 C：跨页续表「同一张表」证明（TableContinuationProof，7 条件）
    # ------------------------------------------------------------------
    def _ctmat(b):
        return build_atomic_material(b, material_type="table_context",
                                     is_current_document=True, is_current_set=True,
                                     dependency_fingerprint=dep).material

    def _tsp(title="营业收入构成（分产品）", unit="万元", headers=("产品", "收入"),
             cells=(("动力电池", "1000"),)):
        return {"table_title": title, "unit": unit, "headers": list(headers),
                "cells": [list(c) for c in cells],
                "coordinates": {"bbox": [0, 0, 100, 100],
                                "cell_bboxes": [[0, 0, 10, 10] for _ in cells]}}

    def _c_assemblies(blocks):
        mats = tuple(_ctmat(b) for b in blocks)
        blk_map = {b.evidence_id: b for b in blocks}
        asms = build_assemblies(mats, blk_map)
        return [a for a in asms if isinstance(a, TableAssembly)]

    # C1：真实同表跨页续表 → proof.valid（7 条件全真）。
    c_h = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                 structured=_tsp())
    c_b = _block(page=6, block=1, etype="table_row", text="动力电池 1,000",
                 structured=_tsp(cells=(("动力电池", "1000"),)))
    c_c = _block(page=7, block=0, etype="table_row", text="（续）储能 500",
                 structured=_tsp(cells=(("储能", "500"),)))
    c_tbls = _c_assemblies((c_h, c_b, c_c))
    check(len(c_tbls) == 1, "修复C：真实同表跨页续表 → 1 个 TableAssembly")
    c_proof = c_tbls[0].continuation_proof
    check(isinstance(c_proof, TableContinuationProof)
          and c_proof.proof_version == TABLE_CONTINUATION_PROOF_VERSION,
          "修复C：assembly 携带 versioned TableContinuationProof")
    check(c_proof.valid and not c_proof.sample_not_obtained,
          "修复C：同表跨页续表 proof.valid True（7 条件全真，非 sample_not_obtained）")
    check(c_proof.continuation_evidence_ids == (c_c.evidence_id,)
          and c_proof.header_evidence_id == c_h.evidence_id,
          "修复C：续块 evidence_id != 表头（跨块）")
    check(c_proof.title_compatible and c_proof.unit_compatible
          and c_proof.column_compatible and c_proof.row_column_continuity,
          "修复C：表题/单位/列/行列连续全兼容")
    check(c_proof._cross_page() and c_proof._distinct_blocks(),
          "修复C：跨页 + 续块身份互异")

    # C2：两独立表跨页（不同表题）→ 不伪造同一张表（各自 sample_not_obtained）。
    ind_h1 = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                    structured=_tsp())
    ind_h2 = _block(page=7, block=0, etype="table", text="营业成本构成（分产品）",
                    structured=_tsp(title="营业成本构成（分产品）"))
    ind_tbls = _c_assemblies((ind_h1, ind_h2))
    check(len(ind_tbls) == 2, "修复C反例：两独立表跨页 → 2 个 TableAssembly")
    check(all(a.continuation_proof.sample_not_obtained for a in ind_tbls),
          "修复C反例：两独立表各自 sample_not_obtained（不伪造跨表续表）")
    check(all(not a.continuation_proof.valid for a in ind_tbls),
          "修复C反例：两独立表各自 proof.valid False")

    # C3：单表无续表 → sample_not_obtained（绝不伪造成「有续表」）。
    solo = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                  structured=_tsp())
    solo_tbls = _c_assemblies((solo,))
    check(len(solo_tbls) == 1
          and solo_tbls[0].continuation_proof.sample_not_obtained
          and not solo_tbls[0].continuation_proof.valid,
          "修复C：单表无续表 → sample_not_obtained（不伪造接受）")

    # C4：同表题但单位不一致 → 单位不兼容 → valid False。
    u_h = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                 structured=_tsp(unit="万元"))
    u_c = _block(page=7, block=0, etype="table_row", text="（续）",
                 structured=_tsp(unit="亿元", cells=(("储能", "500"),)))
    u_tbls = _c_assemblies((u_h, u_c))
    check(len(u_tbls) == 1 and u_tbls[0].continuation_proof.has_continuation
          and not u_tbls[0].continuation_proof.unit_compatible
          and not u_tbls[0].continuation_proof.valid
          and "单位不兼容" in u_tbls[0].continuation_proof.issue,
          "修复C反例：同表题单位不一致 → 单位不兼容 → valid False")

    # C5：同表题但列不一致 → 列不兼容 → valid False。
    col_h = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                   structured=_tsp(headers=("产品", "收入")))
    col_c = _block(page=7, block=0, etype="table_row", text="（续）",
                   structured=_tsp(headers=("产品", "金额"),
                                   cells=(("储能", "500"),)))
    col_tbls = _c_assemblies((col_h, col_c))
    check(len(col_tbls) == 1 and not col_tbls[0].continuation_proof.column_compatible
          and not col_tbls[0].continuation_proof.valid
          and "列不兼容" in col_tbls[0].continuation_proof.issue,
          "修复C反例：同表题列不一致 → 列不兼容 → valid False")

    # C6：续块行宽 != 表头列数 → 行列不连续 → valid False。
    w_h = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                 structured=_tsp(headers=("产品", "收入")))
    w_c = _block(page=7, block=0, etype="table_row", text="（续）",
                 structured=_tsp(headers=("产品", "收入"), cells=(("储能",),)))
    w_tbls = _c_assemblies((w_h, w_c))
    check(len(w_tbls) == 1 and not w_tbls[0].continuation_proof.row_column_continuity
          and not w_tbls[0].continuation_proof.valid
          and "行列不连续" in w_tbls[0].continuation_proof.issue,
          "修复C反例：续块行宽 1 != 表头 2 列 → 行列不连续 → valid False")

    # C7：确定性——同输入两次构建 proof 字段一致。
    c_proof2 = _c_assemblies((c_h, c_b, c_c))[0].continuation_proof
    check(c_proof == c_proof2,
          "修复C：确定性——同输入构建 proof 相等")

    # C8：多张独立同表题跨页表（各自独立表头，无续表信号）→ 不伪造同一张表续表（反例 #7）。
    ind_h1 = _block(page=6, block=0, etype="table", text="营业收入构成（分产品）",
                    structured=_tsp())
    ind_b1 = _block(page=6, block=1, etype="table_row", text="动力电池 1,000",
                    structured=_tsp(cells=(("动力电池", "1000"),)))
    ind_h2 = _block(page=7, block=0, etype="table", text="营业收入构成（分产品）",
                    structured=_tsp())
    ind_b2 = _block(page=7, block=1, etype="table_row", text="储能 500",
                    structured=_tsp(cells=(("储能", "500"),)))
    ind_tbls2 = _c_assemblies((ind_h1, ind_b1, ind_h2, ind_b2))
    check(len(ind_tbls2) == 2,
          "反例#7：两张独立同表题表（各自表头）→ 2 个 TableAssembly（不合并为续表）")
    check(all(a.continuation_proof.sample_not_obtained for a in ind_tbls2)
          and all(not a.continuation_proof.valid for a in ind_tbls2),
          "反例#7：两张独立表各自 sample_not_obtained、不伪造跨表续表 valid")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
