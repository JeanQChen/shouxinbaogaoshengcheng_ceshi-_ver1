"""Eval: 扩读编排 ContextExpansion（R2_IMPLEMENTATION_PLAN §11）。

用法: python -m evals.test_context_expansion

覆盖：经 ToolRegistry 的相邻块/续表/交叉引用、章节边界停止、连续性断裂（跨页）、
seed mismatch fail-closed、预算各轴（per_seed_cap/max_bytes）、trace 每步含 ToolCall、
trace 无 material_id、adopted 稳定序（seed 在前）。

全部离线：临时 SQLite 文件模拟 evidence.db + 真实 ToolRegistry，不调 LLM/网络。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.context_expansion import (
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import register_bounded_evidence_tool
from tools.registry import ToolRegistry


def _sp(seq) -> str:
    return json.dumps(list(seq), ensure_ascii=False, separators=(",", ":"))


def _make_db(dirpath: Path, blocks) -> Path:
    """建临时 evidence.db；blocks = [(page, block, section_path, etype, text), ...]"""
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
    for page, blk, sp, etype, text in blocks:
        conn.execute(
            "INSERT INTO evidence_blocks (evidence_id, schema_version, company_id, "
            "document_id, document_version, evidence_set_version, source_name, source_type, "
            "source_uri, page_number, block_index, section_path, evidence_type, text, "
            "structured_payload, report_period, published_at, entities, quality_flags, "
            "content_hash, builder_version, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ev-{page}-{blk}", "1", "300750", "doc1", "v1", "set1",
             "年报", "annual_report", None, page, blk, _sp(sp), etype, text,
             None, "2025-12-31", "2026-04-01", None, None, f"hash-{page}-{blk}",
             "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _seed(page: int = 5, block: int = 1, section: tuple = ("主营业务分析",),
          text: str = "公司主要从事动力电池研发与制造。",
          etype: str = "paragraph") -> ExpansionSeed:
    return ExpansionSeed(
        evidence_id=f"ev-{page}-{block}", page_number=page, block_index=block,
        section_path=section, evidence_type=etype, text=text,
        content_hash=f"hash-{page}-{block}")


def _request(seed: ExpansionSeed, directions, budget: ExpansionBudget | None = None) -> ContextExpansionRequest:
    return ContextExpansionRequest(
        company_id="300750", document_id="doc1", document_version="v1",
        evidence_set_version="set1", seed=seed, directions=directions,
        budget=budget or ExpansionBudget(), dependency_fingerprint="dep-fp")


MAIN_BLOCKS = [
    (5, 0, ["主营业务分析"], "heading", "主营业务分析"),
    (5, 1, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
    (5, 2, ["主营业务分析"], "table", "营业收入构成（分产品）"),
    (5, 3, ["主营业务分析"], "table_row", "动力电池系统 1,000"),
    (6, 0, ["主营业务分析"], "paragraph", "（续）其中海外收入……"),
    (7, 0, ["风险因素"], "heading", "风险因素"),
]


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

    def build_registry(td) -> tuple[ToolRegistry, Path]:
        db = _make_db(Path(td), MAIN_BLOCKS)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        return registry, db

    # ------------------------------------------------------------------
    # 1. seed mismatch → fail-closed（不读任何扩读）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()  # ev-5-1 存在，但用错误 content_hash 复验
        seed = ExpansionSeed(evidence_id=seed.evidence_id, page_number=seed.page_number,
                             block_index=seed.block_index, section_path=seed.section_path,
                             evidence_type=seed.evidence_type, text=seed.text,
                             content_hash="WRONG-HASH")
        req = _request(seed, ("adjacent_blocks",))
        bad = expand(req, registry, run_id="t1")
        check(bad.adopted == (), "seed mismatch → adopted 为空")
        check(bad.stop_reason == "authority", "seed mismatch → stop_reason=authority")
        check(bad.unread_scope.reason == "authority", "seed mismatch → unread reason=authority")
        check(len(bad.trace.steps) == 1 and bad.trace.steps[0].action == "resolve_seed",
              "seed mismatch → 仅 resolve_seed 一步，无扩读")

    # ------------------------------------------------------------------
    # 2. 相邻扩读 + 章节边界停止
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed()  # content_hash hash-5-1 正确
        req = _request(seed, ("adjacent_blocks",))
        res = expand(req, registry, run_id="t2")

        ids = [b.evidence_id for b in res.adopted]
        check(ids[0] == "ev-5-1", "adopted 首个为 seed")
        check(set(ids) >= {"ev-5-0", "ev-5-2", "ev-5-3", "ev-6-0"},
              "相邻扩读采纳同 section 前/后块")
        check("ev-7-0" not in ids, "章节边界块不被采纳")
        check(any(c.evidence_id == "ev-7-0" for c in res.candidates_unread),
              "章节边界块进入 candidates_unread")
        check(res.stop_reason == "unrelated section boundary",
              "章节边界 → stop_reason=unrelated section boundary")
        check(res.unread_scope.reason == "boundary", "unread reason=boundary")

        # trace：resolve_seed + 两次 inspect_bounded；每步含 ToolCall。
        actions = [s.action for s in res.trace.steps]
        check(actions[0] == "resolve_seed" and actions[1] == "inspect_bounded",
              "trace 首步 resolve_seed、次步 inspect_bounded")
        check(all(s.tool_call is not None for s in res.trace.steps),
              "每个 ExpansionStep 含 ToolCall")
        # trace 无 material_id。
        check(not any("mat-" in o for s in res.trace.steps for o in s.outputs),
              "trace outputs 只含 evidence_id、无 material_id")
        check(all(s.outputs and s.outputs[0].startswith("ev-") or not s.outputs
                  for s in res.trace.steps),
              "trace outputs 为 evidence_id 前缀 ev-")

        # budget_consumed 已填充。
        check(res.budget_consumed.get("per_seed_cap", 0) >= 1,
              "budget_consumed.per_seed_cap 计入 seed")

    # ------------------------------------------------------------------
    # 3. per_seed_cap 预算
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        req = _request(_seed(), ("adjacent_blocks",), ExpansionBudget(per_seed_cap=2))
        res = expand(req, registry, run_id="t3")
        check(len(res.adopted) == 2, "per_seed_cap=2 → 只采纳 seed + 1 块")
        check(res.stop_reason.startswith("hard budget (per_seed_cap)"),
              "per_seed_cap 到顶 → hard budget stop_reason")
        check(res.unread_scope.budget_axis == "per_seed_cap", "budget_axis=per_seed_cap")

    # ------------------------------------------------------------------
    # 4. max_bytes 预算
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        req = _request(_seed(), ("adjacent_blocks",), ExpansionBudget(max_bytes=1))
        res = expand(req, registry, run_id="t4")
        check(len(res.adopted) == 1, "max_bytes=1 → 只采纳 seed")
        check(res.stop_reason.startswith("hard budget (max_bytes)"),
              "max_bytes 到顶 → hard budget stop_reason")

    # ------------------------------------------------------------------
    # 5. 连续性断裂（跨页 > adjacent_pages）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        far_blocks = [
            (5, 0, ["主营业务分析"], "paragraph", "公司主要从事动力电池研发与制造。"),
            (9, 0, ["主营业务分析"], "paragraph", "（远处同 section 块，跨 4 页）"),
        ]
        db = _make_db(Path(td), far_blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        req = _request(_seed(page=5, block=0), ("adjacent_blocks",))
        res = expand(req, registry, run_id="t5")
        check(res.stop_reason == "continuity break",
              "同 section 但跨页 > adjacent_pages → continuity break")
        check(any(c.evidence_id == "ev-9-0" for c in res.candidates_unread),
              "跨页块进入 candidates_unread")

    # ------------------------------------------------------------------
    # 6. table_continuation 方向
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        req = _request(_seed(), ("table_continuation",))
        res = expand(req, registry, run_id="t6")
        adopted_etypes = [b.evidence_type for b in res.adopted]
        check(adopted_etypes.count("table") >= 1 and "table_row" in adopted_etypes,
              "table_continuation 采纳 table/table_row 块")
        check(all(b.section_path == ("主营业务分析",) for b in res.adopted if b.evidence_type == "table"),
              "续表块在同一 section")

    # ------------------------------------------------------------------
    # 7. explicit_reference 方向
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        registry, _ = build_registry(td)
        seed = _seed(text="营业收入构成详见下表。")
        req = _request(seed, ("explicit_reference",))
        res = expand(req, registry, run_id="t7")
        check(len(res.adopted) > 1, "explicit_reference 扩读到目标块")
        check(res.budget_consumed.get("explicit_references", 0) >= 1,
              "explicit_references 预算轴计入")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
