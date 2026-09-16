"""Eval: R2 阶段二边界语义反例（§三/§四 权威·边界成员·aspect 资格·supporting 四者分离）。

用法: python -m evals.test_r2_boundary_semantics

覆盖 R2_IMPLEMENTATION_PLAN 阶段二 15 个反例（boundary disposition / section_path 双向 /
material-aspect 绑定 / 段落摊平诚实性）：
- 哨兵（outside_boundary_sentinel）绝不进 candidates_unread、绝不 adopted、绝不 blocking 枚举闭合；
- 真未读仅限预算/工具错误/dangling/未闭合（unread_inside_boundary）；
- 层级路径（seed 合法子路径继续，兄弟/祖先=边界）非关键词、非固定页码；
- 粗粒度 section + 新子主题标题 → context_candidate（保守，非 supporting）；
- 「一是/二是/三是」条目枚举 → inside_boundary（保留）；
- 异常 section_path（表格行摊平）→ path_quality=unreliable 显式诊断；
- 身份不一致（document_version/set）→ 版本隔离 fail-closed（rejected_boundary_mismatch 为纵深防御）；
- seed → source；非 seed 采纳 → context_candidate；绝不 supporting；
- main_business 段落摊平 → material_type_supported=False（缺 table/table_row）。

全部离线：临时 SQLite + 真实 ToolRegistry，不调 LLM/网络。
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
    DISPOSITION_CONTEXT_CANDIDATE,
    DISPOSITION_INSIDE_BOUNDARY,
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    DISPOSITION_REJECTED_BOUNDARY_MISMATCH,
    DISPOSITION_SEED,
    DISPOSITION_UNREAD_INSIDE_BOUNDARY,
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
    section_path_quality,
    section_path_relation,
)
from harness.evidence_reader import (
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.set_enumeration import (
    _enumerate_business,
    _looks_like_flattened_table,
    _trace_fingerprint,
    derive_enumeration_boundary_proof,
)
from harness.topic_materials import build_material_result
from tools.registry import ToolRegistry


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


_COMPANY = "300750"
_DOC = "doc1"
_DOCV = "v1"
_SETV = "set1"


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
    for row in blocks:
        page, blk, sp, etype, text = row[:5]
        payload = row[5] if len(row) > 5 else None
        ch = evidence_ids.content_hash(text, payload)
        eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "1", _COMPANY, _DOC, _DOCV, _SETV,
             "年报", "annual_report", None, page, blk, _sp(sp), etype, text,
             json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None,
             "2025-12-31", "2026-04-01", None, None, ch, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _seed(page: int = 5, block: int = 1, section: tuple = ("主营业务分析",),
          text: str = "公司主要从事动力电池研发与制造。",
          etype: str = "paragraph", payload=None) -> ExpansionSeed:
    section = tuple(section)  # 归一为 tuple（section_path_relation 依赖 tuple==tuple）
    ch = evidence_ids.content_hash(text, payload)
    eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, block, ch)
    return ExpansionSeed(
        evidence_id=eid, page_number=page, block_index=block,
        section_path=section, evidence_type=etype, text=text, content_hash=ch)


def _request(seed: ExpansionSeed, directions, budget: ExpansionBudget | None = None) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
        evidence_set_version=_SETV, seed=seed, directions=directions,
        budget=budget or ExpansionBudget(), dependency_fingerprint="dep-fp")


def _dispositions(res) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in res.boundary_decisions:
        out.setdefault(d.disposition, []).append(d.evidence_id)
    return out


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

    # 反例 R2-1：不同 section_path 块 → 哨兵（不进 candidates_unread、不 adopted）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (7, 0, ["风险因素"], "heading", "风险因素"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",)), registry, run_id="r21")
        disp = _dispositions(res)
        sentinel_ids = set(disp.get(DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL, []))
        check(len(sentinel_ids) >= 1, "R2-1: 不同 section_path 块 → 哨兵存在")
        check(not any(c.evidence_id in sentinel_ids for c in res.candidates_unread),
              "R2-1: 哨兵不进 candidates_unread")
        check(all(b.evidence_id not in sentinel_ids for b in res.adopted),
              "R2-1: 哨兵不 adopted")
        check(res.unread_scope.reason is None,
              "R2-1: 哨兵不产生 unread reason")

    # 反例 R2-2：同 section 跨页（>adjacent_pages）→ 页距预算 unread（非哨兵）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (9, 0, ["主营业务分析"], "paragraph", "（远处同 section 块）"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=0), ("adjacent_blocks",)), registry, run_id="r22")
        disp = _dispositions(res)
        check(len(disp.get(DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL, [])) == 0,
              "R2-2: 同 section 跨页块不进 outside_boundary_sentinels（页距非结构边界）")
        check(any(d.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY
                  and d.reason_code == "budget_adjacent_pages"
                  for d in res.boundary_decisions),
              "R2-2: 跨页块是 unread_inside_boundary（budget_adjacent_pages）")

    # 反例 R2-3：边界内预算耗尽 → unread_inside_boundary（真未读，进 candidates_unread）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (5, 3, ["主营业务分析"], "paragraph", "储能业务快速增长。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",),
                              ExpansionBudget(per_seed_cap=2)), registry, run_id="r23")
        disp = _dispositions(res)
        check(len(disp.get(DISPOSITION_UNREAD_INSIDE_BOUNDARY, [])) >= 1,
              "R2-3: 预算耗尽 → unread_inside_boundary")
        check(res.unread_scope.reason == "budget" and res.unread_scope.budget_axis == "per_seed_cap",
              "R2-3: 预算耗尽 → unread reason=budget + axis=per_seed_cap")
        check(any(d.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY
                  and d.evidence_id in {c.evidence_id for c in res.candidates_unread}
                  for d in res.boundary_decisions),
              "R2-3: unread_inside_boundary 块进 candidates_unread")

    # 反例 R2-4：层级路径——seed 合法子路径继续，兄弟/祖先=哨兵
    with tempfile.TemporaryDirectory() as td:
        base = ["十、财务报告", "1、在子公司中的权益"]
        db = _make_db(Path(td), [
            (5, 0, base, "heading", "在子公司中的权益"),
            (5, 1, base, "paragraph", "主要子公司情况如下。"),
            (5, 2, base + ["（1）主要子公司情况"], "paragraph", "（1）主要子公司情况"),
            (6, 0, ["十、财务报告", "2、在合营企业中的权益"], "heading", "在合营企业中的权益"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1, section=base,
                                    text="主要子公司情况如下。"), ("adjacent_blocks",)),
                     registry, run_id="r24")
        adopted_paths = [tuple(b.section_path) for b in res.adopted]
        check(tuple(base + ["（1）主要子公司情况"]) in adopted_paths,
              "R2-4: seed 合法子路径被采纳（层级内继续）")
        sentinels = [tuple(d.section_path) for d in res.outside_boundary_sentinels]
        check(tuple(["十、财务报告", "2、在合营企业中的权益"]) in sentinels,
              "R2-4: 新同级章节 → 哨兵")
        check(section_path_relation(base, base + ["x"]) == "sub",
              "R2-4: section_path_relation 判定 sub")
        check(section_path_relation(base, ["十、财务报告"]) == "super",
              "R2-4: section_path_relation 判定 super")
        check(section_path_relation(base, ["十、财务报告", "2、合营"]) == "sibling",
              "R2-4: section_path_relation 判定 sibling")

    # 反例 R2-5：粗粒度 section + 新子主题标题 → context_candidate（保守，非 inside_boundary）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["管理层讨论与分析"], "paragraph", "三、核心竞争力分析"),
            (5, 1, ["管理层讨论与分析"], "paragraph", "公司具备核心技术优势。"),
            (5, 2, ["管理层讨论与分析"], "paragraph", "（一）行业政策。欧盟新电池法规出台。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1, section=("管理层讨论与分析",),
                                    text="公司具备核心技术优势。"),
                              ("adjacent_blocks",)), registry, run_id="r25")
        disp = _dispositions(res)
        # 新子主题标题「（一）行业政策」→ context_candidate（保守，不冒名同子主题）。
        check(len(disp.get(DISPOSITION_CONTEXT_CANDIDATE, [])) >= 1,
              "R2-5: 粗粒度 section 新子主题标题 → context_candidate")

    # 反例 R2-6：「一是/二是/三是」条目枚举 → inside_boundary（保留，非新标题）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["核心竞争力"], "paragraph", "一是技术研发优势。"),
            (5, 1, ["核心竞争力"], "paragraph", "二是规模优势。"),
            (5, 2, ["核心竞争力"], "paragraph", "三是成本优势。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=0, section=("核心竞争力",),
                                    text="一是技术研发优势。"),
                              ("adjacent_blocks",)), registry, run_id="r26")
        disp = _dispositions(res)
        # 「一是/二是」条目 → inside_boundary（不判新标题，继续同一主题）。
        check(DISPOSITION_INSIDE_BOUNDARY in disp,
              "R2-6: 「一是/二是」条目 → inside_boundary（保留）")
        check(not any(d.disposition == DISPOSITION_CONTEXT_CANDIDATE
                      for d in res.boundary_decisions
                      if "一是" in (res.seed.text or "")),
              "R2-6: 条目枚举不被误判为 context_candidate")

    # 反例 R2-7：异常 section_path（表格行摊平）→ path_quality=unreliable 显式诊断
    abnormal = ("瑞庭时代（上海）新能源科技有限公  上海市  50,000.0100.0/  设立",)
    check(section_path_quality(abnormal) == "unreliable",
          "R2-7: 表格行摊平 section_path → unreliable")
    check(section_path_quality(("主营业务分析",)) == "reliable",
          "R2-7: 正常标题 section_path → reliable")
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, abnormal, "paragraph", "瑞庭时代（上海）新能源科技有限公司"),
            (5, 1, abnormal, "paragraph", "上海联风新能源科技有限公司"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=0, section=abnormal,
                                    text="瑞庭时代（上海）新能源科技有限公司"),
                              ("adjacent_blocks",)), registry, run_id="r27")
        disp = _dispositions(res)
        check(len(disp.get(DISPOSITION_CONTEXT_CANDIDATE, [])) >= 1,
              "R2-7: 异常路径 → context_candidate（保守）")
        seed_dec = next(d for d in res.boundary_decisions if d.disposition == DISPOSITION_SEED)
        check(seed_dec.path_quality == "unreliable",
              "R2-7: seed 决策显式记录 path_quality=unreliable")

    # 反例 R2-8：身份不一致（document_version 不同）→ 版本隔离 fail-closed
    # （rejected_boundary_mismatch 是 classify 内纵深防御；真实读取链由 reader 的
    #  document_version 过滤 + _verify_current_and_identity 先 fail-closed，故此处验证
    #  跨版本块绝不 adopted 这一可观察保证。）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
        ])
        # 追加一个 document_version 不同的块（不同版本，同一 section）。
        conn = sqlite3.connect(str(db))
        other_text = "（v2 版本块）"
        other_ch = evidence_ids.content_hash(other_text, None)
        other_eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, "v2", _SETV, 5, 1, other_ch)
        conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (_COMPANY, _DOC, "v2", "年报", None, "annual_report", "annual", "f" * 64,
                      100, 20, "宁德时代", None, "p1", "current", None, "2026-01-01"))
        conn.execute("INSERT OR IGNORE INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
                     (_COMPANY, _DOC, "v2", _SETV, json.dumps({}), "current", 1, "2026-01-01"))
        conn.execute(
            "INSERT OR IGNORE INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (other_eid, "1", _COMPANY, _DOC, "v2", _SETV, "年报", "annual_report", None,
             5, 1, _sp(["主营业务分析"]), "paragraph", other_text, None,
             "2025-12-31", "2026-04-01", None, None, other_ch, "b1", "2026-01-01"))
        conn.commit()
        conn.close()
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=0), ("adjacent_blocks",)), registry, run_id="r28")
        check(all(b.evidence_id != other_eid for b in res.adopted),
              "R2-8: 跨 document_version 块不被采纳（reader fail-closed）")

    # 反例 R2-9：哨兵不阻塞枚举边界闭合（violation 只看 unread_inside_boundary）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (7, 0, ["风险因素"], "heading", "风险因素"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",)), registry, run_id="r29")
        check(len(res.outside_boundary_sentinels) >= 1, "R2-9: 存在哨兵")
        proof = derive_enumeration_boundary_proof(
            "company_business_main.main_business", (res,),
            document_id=_DOC, document_version=_DOCV, evidence_set_version=_SETV,
            source_boundary_identity="主营业务分析", component_material_ids=("m-a1",),
            dependency_fingerprint="0" * 64)
        check(proof.unread_candidate_refs == (), "R2-9: 哨兵不进 unread_candidate_refs")
        check(proof.violation() is None,
              "R2-9: 有哨兵但无真未读 → 枚举边界闭合不违规")

    # 反例 R2-10 / R2-11：seed → source；非 seed → context_candidate；绝不 supporting；
    #                          哨兵绝不进入 material library
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (7, 0, ["风险因素"], "heading", "风险因素"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",)), registry, run_id="r210")
        result = build_material_result(res, dependency_fingerprint=_sha("dep"),
                                       aspect_id="company_business_main.main_business")
        roles = {l.role: l for l in result.aspect_links}
        check("supporting" not in roles, "R2-10: 绝不自动 supporting（留给 R3）")
        check("source" in roles and "context_candidate" in roles,
              "R2-10: 含 source + context_candidate")
        seed_ev = next(m.authority_assessment.evidence_id for m in result.materials
                       if m.authority_assessment.evidence_id == res.seed.evidence_id)
        sentinel_evs = {d.evidence_id for d in res.outside_boundary_sentinels}
        mat_evs = {m.authority_assessment.evidence_id for m in result.materials}
        check(seed_ev not in sentinel_evs and not (mat_evs & sentinel_evs),
              "R2-11: 哨兵块绝不进入 material library")

    # 反例 R2-12：main_business 段落摊平 → 缺 table/table_row（不冒充 business_segment）
    check(_looks_like_flattened_table("动力电池系统  31,650,636.9  74.7  25,304,133.7  69.9"),
          "R2-12: 数字密集列分隔段落 → 表格摊平")
    check(_looks_like_flattened_table("单位：万元，%"),
          "R2-12: 「单位：」表头 → 表格摊平")
    check(not _looks_like_flattened_table("公司主营动力电池业务。"),
          "R2-12: 散文段落 → 非表格摊平")
    # 集成：段落摊平 → 枚举失败（缺结构化 table/table_row）。
    env = {
        "material_payload_version": "1", "object_type": "evidence_span",
        "authority_identity": "evidence:ev-1", "document_identity": {"document_id": "doc1"},
        "locator": {}, "evidence_id": "ev-1", "source_content_hash": _sha("src"),
        "content": {"text": "项目  2025年  2024年  2023年", "structured_payload": None,
                    "evidence_type": "paragraph"},
        "created_dependency_fingerprint": _sha("dep"),
    }
    rp = TS.ResolvedPayload(object_type="evidence_span", authority_identity="evidence:ev-1",
                            version="1", locator=TS.EvidenceLocator(document_id="doc1",
                                                                     document_version="v1",
                                                                     section_path="s1", page=1),
                            content_hash=_sha_bytes(json.dumps(env).encode("utf-8")),
                            payload_bytes=json.dumps(env, ensure_ascii=False).encode("utf-8"))
    members, issue, source_object_inventory = _enumerate_business((rp,))
    check(members == [] and issue is not None and "数据行" in issue,
          "R2-12: 段落摊平 → 摊平表恢复失败（缺数据行），诚实 fail-closed（不冒充 business_segment）")

    # 反例 R2-13：before/after 双向独立（before 哨兵不阻塞 after 采纳）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["风险因素"], "heading", "风险因素"),
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",)), registry, run_id="r213")
        adopted_texts = {b.text for b in res.adopted}
        check("海外收入占比提升。" in adopted_texts,
              "R2-13: before 哨兵不阻塞 after 采纳")
        check(any(d.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL
                  and d.direction == "adjacent_blocks_before"
                  for d in res.boundary_decisions),
              "R2-13: before 边界块进入哨兵（方向=adjacent_blocks_before）")

    # 反例 R2-14：确定性——同一逻辑不同 run_id → 同 trace 指纹（含边界决策）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (7, 0, ["风险因素"], "heading", "风险因素"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        req = _request(_seed(page=5, block=1), ("adjacent_blocks",))
        a = expand(req, registry, run_id="r214a")
        b = expand(req, registry, run_id="r214b")
        check(_trace_fingerprint((a,)) == _trace_fingerprint((b,)),
              "R2-14: 不同 run_id 同逻辑 → trace 指纹相同（含边界决策）")

    # 反例 R2-15：边界决策逐块持久化（含 seed/inside/sentinel/unread 各 disposition）
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td), [
            (4, 0, ["风险因素"], "heading", "风险因素"),
            (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
            (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (5, 2, ["主营业务分析"], "paragraph", "海外收入占比提升。"),
            (5, 3, ["主营业务分析"], "paragraph", "储能业务快速增长。"),
        ])
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        res = expand(_request(_seed(page=5, block=1), ("adjacent_blocks",),
                              ExpansionBudget(per_seed_cap=3)), registry, run_id="r215")
        disp = _dispositions(res)
        check(DISPOSITION_SEED in disp, "R2-15: 含 seed 决策")
        check(DISPOSITION_INSIDE_BOUNDARY in disp, "R2-15: 含 inside_boundary 决策")
        check(DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL in disp, "R2-15: 含哨兵决策")
        check(DISPOSITION_UNREAD_INSIDE_BOUNDARY in disp, "R2-15: 含 unread 决策")
        # 每块决策字段完整（evidence_id/disposition/reason_code/document/version/set/page/block）。
        for d in res.boundary_decisions:
            check(bool(d.evidence_id and d.disposition and d.reason_code and d.document_id),
                  f"R2-15: 决策字段完整（{d.disposition}）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
