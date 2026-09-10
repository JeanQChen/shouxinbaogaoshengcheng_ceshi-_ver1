"""Eval: Phase 4 Batch D — 多 Evidence 审计意见 enrichment 专项评测。

纯离线：注入 fake EvidenceBlock / fake llm_extract，不读真实库、不调真实 LLM。
覆盖：确定性五字段抽取、意见特异性、报告期/scope/unit 归一化、多 Evidence 支持、
冲突→unresolved、LLM 填缺（至多一次）、悬空引用 fail-closed、空块不默认意见、
current 候选块只读查询（过滤非 current / 年报回退）。

用法: python -m evals.test_section_audit_opinion
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidence import schema as ES  # noqa: E402
from sections import audit_opinion as AO  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def _block(eid, page, text, company_id="C", source_type="annual_report"):
    return ES.EvidenceBlock(
        evidence_id=eid, schema_version="1", company_id=company_id,
        document_id="doc", document_version="dv", evidence_set_version="sv",
        source_name="年报", source_type=source_type, source_uri=None,
        page_number=page, block_index=0, section_path=[], evidence_type="paragraph",
        text=text, structured_payload=None, report_period=None, published_at=None,
        entities=[], quality_flags=[], content_hash="h", builder_version="1",
        created_at="2026-01-01T00:00:00Z")


_GOOD_TEXT = ("我们审计了 XX 公司合并财务报表。安永华明会计师事务所（特殊普通合伙）"
              "出具了标准无保留意见。截至 2025年12月31日。单位：万元。")


def _seed_evidence_db(db_path: Path) -> None:
    from evidence import store as estore
    estore.init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        def doc(did, dv, stype, status):
            conn.execute(
                "INSERT INTO documents (company_id, document_id, document_version, "
                "source_name, source_path, source_type, material_group, file_sha256, "
                "file_size, page_count, declared_company_name, detected_company_names, "
                "parser_version, status, quality_flags, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("C", did, dv, "文件", None, stype, "financial", f"sha_{did}", 10, 10,
                 None, "[]", "v1", status, "[]", "t"))

        def setrow(did, dv, sv, status):
            conn.execute(
                "INSERT INTO evidence_sets (company_id, document_id, document_version, "
                "evidence_set_version, dependency_versions, status, block_count, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("C", did, dv, sv, "{}", status, 1, "t"))

        def block(eid, did, dv, sv, page, stype, text):
            conn.execute(
                "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
                "document_id, document_version, evidence_set_version, source_name, "
                "source_type, source_uri, page_number, block_index, section_path, "
                "evidence_type, text, structured_payload, report_period, published_at, "
                "entities, quality_flags, content_hash, builder_version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, "1", "C", did, dv, sv, "文件", stype, None, page, 0, "[]",
                 "paragraph", text, None, None, None, "[]", "[]", f"h_{eid}", "1", "t"))

        # 年报 current 文档 + current 集合（2 块，其中一块含审计意见）
        doc("doc1", "dv1", "annual_report", "current")
        setrow("doc1", "dv1", "sv1", "current")
        block("e1", "doc1", "dv1", "sv1", 3, "annual_report", _GOOD_TEXT)
        block("e2", "doc1", "dv1", "sv1", 4, "annual_report", "无审计相关字段。")
        # 旧年报（superseded）→ 应被过滤
        doc("doc1", "dv0", "annual_report", "superseded")
        setrow("doc1", "dv0", "sv0", "retired")
        block("e_old", "doc1", "dv0", "sv0", 1, "annual_report", "旧版本审计意见。")
        # 债券募集说明书（current）→ 非年报，年报命中时被过滤
        doc("doc2", "dv2", "debt_circular", "current")
        setrow("doc2", "dv2", "sv2", "current")
        block("e3", "doc2", "dv2", "sv2", 5, "debt_circular", "募集说明书正文。")
        conn.commit()
    finally:
        conn.close()


def main():
    # ---- 1. 意见特异性 ----
    check(AO._detect_field("audit_opinion", _GOOD_TEXT) == ["标准无保留意见"],
          "标准无保留意见 特异性命中（不降级为无保留意见）")
    check(AO._detect_field("audit_opinion", "我们出具了保留意见。") == ["保留意见"],
          "保留意见 命中")
    check(AO._detect_field("audit_opinion", "无相关字段。") == [],
          "无意见措辞 → 空")

    # ---- 2. 报告期归一化 ----
    check(AO._detect_field("report_period", "截至 2025年12月31日。") == ["2025-12-31"],
          "报告期 归一化到 YYYY-12-31")
    check(AO._detect_field("report_period", "2025年度利润表。") == ["2025年度"],
          "报告期 年度形式")

    # ---- 3. scope / unit 归一化 ----
    check(AO._detect_field("scope", "合并财务报表。") == ["consolidated"], "scope 合并")
    check(AO._detect_field("scope", "母公司财务报表。") == ["parent"], "scope 母公司")
    check(AO._detect_field("unit", "单位：万元。") == ["wan_yuan"], "unit 万元归一化")
    check(AO._detect_field("unit", "单位：元。") == ["yuan"], "unit 元归一化")

    # ---- 4. 会计事务所抽取 ----
    check("安永华明会计师事务所" in AO._detect_field("accounting_firm", _GOOD_TEXT)[0],
          "会计事务所名称抽取")
    check(AO._detect_field("accounting_firm", "审计机构为安永华明会计师事务所（特殊普通合伙）。")
          == ["安永华明会计师事务所（特殊普通合伙）"],
          "所名前缀结构词（审计机构为）被剥离")
    check(AO._detect_field("accounting_firm", "本公司审计机构为立信会计师事务所。")
          == ["立信会计师事务所"],
          "所名前缀结构词（本公司审计机构为）被剥离")

    # ---- 5. 确定性全字段 + 多 Evidence 支持 ----
    firm_block = _block("e2", 4, "审计机构为安永华明会计师事务所（特殊普通合伙）。")
    det_fields, det_conflicts = AO._deterministic_extract([_block("e1", 3, _GOOD_TEXT),
                                                           firm_block])
    check(set(det_fields.keys()) == set(AO.AUDIT_FIELDS),
          f"确定性抽齐五字段（got {sorted(det_fields)}）")
    check(det_fields["audit_opinion"][0] == "标准无保留意见", "确定性审计意见值")
    firm_refs = det_fields["accounting_firm"][1]
    check(len(firm_refs) == 2 and {r.evidence_id for r in firm_refs} == {"e1", "e2"},
          "会计事务所由多 Evidence 共同支持")

    # ---- 6. 冲突 → conflicts ----
    conflict = _block("e5", 5, "出具了保留意见。")
    _, conflicts = AO._deterministic_extract([_block("e1", 3, _GOOD_TEXT), conflict])
    check("audit_opinion" in conflicts and conflicts["audit_opinion"] == ["保留意见", "标准无保留意见"],
          f"跨块意见冲突 → conflicts（got {conflicts.get('audit_opinion')}）")

    # ---- 7. extract 端到端：确定性齐备 → method=deterministic、无 LLM ----
    e = AO.extract("C", evidence_blocks=[_block("e1", 3, _GOOD_TEXT), firm_block])
    check(e.method == "deterministic" and e.llm_calls == 0 and not e.unresolved,
          f"确定性齐备 → deterministic / llm_calls=0 / 无 unresolved（got {e.method},{e.llm_calls},{e.unresolved}）")
    check(len(e.fields) == 5, "enrichment 五字段齐备")

    # ---- 8. LLM 填缺（至多一次）→ method=mixed ----
    partial = _block("e9", 1, "2025年度 本公司。")
    def fake_llm(blocks):  # noqa: ARG001
        return json.dumps({"fields": [
            {"field": "audit_opinion", "value": "保留意见", "evidence_ids": ["e9"]},
        ]})
    e2 = AO.extract("C", evidence_blocks=[partial], llm_extract=fake_llm)
    check(e2.method == "mixed" and e2.llm_calls == 1,
          f"LLM 填缺 → method=mixed / llm_calls=1（got {e2.method},{e2.llm_calls}）")
    op = next((f for f in e2.fields if f.field == "audit_opinion"), None)
    check(op is not None and op.evidence_refs[0].evidence_id == "e9",
          "LLM 填缺字段携带 evidence 引用")

    # ---- 9. 悬空引用 → 丢弃（fail-closed）----
    def fake_dangling(blocks):  # noqa: ARG001
        return json.dumps({"fields": [
            {"field": "audit_opinion", "value": "标准无保留意见", "evidence_ids": ["ghost"]},
        ]})
    e3 = AO.extract("C", evidence_blocks=[partial], llm_extract=fake_dangling)
    check("audit_opinion" in e3.unresolved and not any(
        f.field == "audit_opinion" for f in e3.fields),
        "悬空 evidence_id 字段被丢弃（fail-closed）")

    # ---- 10. 空块 → 全 unresolved，不默认意见 ----
    e4 = AO.extract("C", evidence_blocks=[])
    check(e4.method == "none" and len(e4.unresolved) == len(AO.AUDIT_FIELDS) and not e4.fields,
          "空块 → 全字段 unresolved，绝不默认标准无保留意见")

    # ---- 11. 冲突 → unresolved ----
    e5 = AO.extract("C", evidence_blocks=[_block("e1", 3, _GOOD_TEXT), conflict])
    check("audit_opinion" in e5.unresolved, "跨块意见冲突 → unresolved")

    # ---- 12. LLM 非法 JSON → fail-closed 抛错 ----
    def fake_bad(blocks):  # noqa: ARG001
        return "not json at all"
    raised = False
    try:
        AO.extract("C", evidence_blocks=[partial], llm_extract=fake_bad)
    except AO.AuditOpinionError:
        raised = True
    check(raised, "LLM 非法 JSON → fail-closed 抛 AuditOpinionError")

    # ---- 13. current 候选块只读查询 ----
    tmp = Path(tempfile.mkdtemp(prefix="audit_opinion_test_"))
    ev_db = tmp / "evidence.db"
    _seed_evidence_db(ev_db)
    blocks = AO.gather_candidate_blocks("C", ev_db=ev_db)
    ids = {b.evidence_id for b in blocks}
    check(ids == {"e1", "e2"}, f"只取 current 年报块（got {sorted(ids)}）")
    # 无年报 → 回退全部 current 块
    conn = sqlite3.connect(str(ev_db))
    try:
        conn.execute("UPDATE evidence_blocks SET source_type='other' "
                     "WHERE document_id='doc1'")
        conn.commit()
    finally:
        conn.close()
    blocks2 = AO.gather_candidate_blocks("C", ev_db=ev_db)
    ids2 = {b.evidence_id for b in blocks2}
    check("e3" in ids2, f"无年报 → 回退全部 current 块（got {sorted(ids2)}）")

    # ---- 14. 序列化形状 ----
    d = AO.enrichment_to_dict(e)
    check(set(d.keys()) == {"company_id", "method", "llm_calls", "fields",
                            "unresolved", "consulted_evidence_ids"},
          "enrichment_to_dict 形状完整")
    check(d["fields"][0]["field"] in AO.AUDIT_FIELDS, "序列化字段名合法")

    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
    sys.exit(0 if _results["failed"] == 0 else 1)
