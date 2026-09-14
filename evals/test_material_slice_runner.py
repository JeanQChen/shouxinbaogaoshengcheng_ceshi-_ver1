"""Eval: R2 材料验收 runner + 两阶段 seed（R2_IMPLEMENTATION_PLAN §12/§13）。

用法: python -m evals.test_material_slice_runner

覆盖：
- 产物布局：``r2_material_slice_<run_id>/`` 含 seed_manifest.json / resolved_seed_manifest.json /
  seed_discovery_trace.jsonl / expansion_trace.jsonl / unread_scope.json / set_enumeration.json /
  aspect_material_matrix.json（+ .md）/ material_index.json（+ .md）/ before_after.md /
  payload_preview/。
- material_index 必填字段：material_type / component evidence_id / source_content_hash（来源层）
  / payload_hash（载体层，双哈希不等）/ authority_verdict / document_id / document_version / section_path。
- seed 复验 fail-closed：一致 seed → resolved=verified；不存在/错哈希 seed → seed_mismatch；
  身份字段缺失 seed → incomplete_candidate；三者均不产生伪造材料。
- aspect 矩阵六态：matrix md 列出全部六态 label；json 每 aspect 有 state。
- seed manifest 版本/fingerprint 校验 fail-closed。
- discover_seeds 无检索工具 → fail-closed（discovery_failed=True、空 candidate，不 init/migrate）。

全部离线：临时 SQLite evidence.db + harness.db，不调 LLM/网络/博查。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.evidence_reader import EvidenceReadResult
from harness.material_slice_runner import (
    SEED_MANIFEST_VERSION,
    SET_ASPECTS,
    SeedEntry,
    SeedManifest,
    compute_seed_manifest_fingerprint,
    discover_seeds,
    load_seed_manifest,
    run_material_slice,
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


def _seed_entry(case_id: str, aspect_id: str, evidence_id: str, *,
                page: int = 5, block: int = 1, section: tuple = ("主营业务分析",),
                etype: str = "paragraph", text: str = "公司主要从事动力电池研发与制造。",
                content_hash: str | None = None) -> SeedEntry:
    return SeedEntry(
        case_id=case_id, company_id="300750", aspect_id=aspect_id, query="主营业务",
        evidence_id=evidence_id, document_id="doc1", document_version="v1",
        evidence_set_version="set1", page_number=page, block_index=block,
        section_path=section, evidence_type=etype,
        source_content_hash=content_hash or _sha(text),
        selection_reason="eval 合成", text=text)


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

    # ------------------------------------------------------------------
    # 1. seed manifest 版本 / fingerprint 校验（fail-closed）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e1 = _seed_entry("c1", "company_business_main.main_business", "ev-5-1")
        e2 = _seed_entry("c2", "company_competitiveness.core_competitiveness", "ev-bogus",
                         content_hash=_sha("x"))
        manifest = SeedManifest(
            manifest_version=SEED_MANIFEST_VERSION,
            fingerprint=compute_seed_manifest_fingerprint((e1, e2)),
            entries=(e1, e2))
        mpath = root / "seed_manifest.json"
        mpath.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False), encoding="utf-8")
        loaded = load_seed_manifest(mpath)
        check(loaded.fingerprint == manifest.fingerprint
              and len(loaded.entries) == 2,
              "load_seed_manifest 往返一致（fingerprint + entries）")
        # 版本不符 → fail-closed。
        bad = manifest.to_dict()
        bad["manifest_version"] = "0"
        (root / "bad_ver.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        try:
            load_seed_manifest(root / "bad_ver.json")
            check(False, "manifest_version 不符应 fail-closed")
        except ValueError:
            check(True, "manifest_version 不符 → ValueError")
        # fingerprint 篡改 → fail-closed。
        bad = manifest.to_dict()
        bad["fingerprint"] = "0" * 64
        (root / "bad_fp.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        try:
            load_seed_manifest(root / "bad_fp.json")
            check(False, "fingerprint 与内容不一致应 fail-closed")
        except ValueError:
            check(True, "fingerprint 与内容不一致 → ValueError")

    # ------------------------------------------------------------------
    # 2. 正式材料验收 runner（seed 复验 fail-closed + 产物布局 + 双哈希索引）
    # ------------------------------------------------------------------
    main_blocks = [
        _block("ev-5-0", page=5, block=0, etype="heading", text="主营业务分析"),
        _block("ev-5-1", page=5, block=1, etype="paragraph",
               text="公司主要从事动力电池研发与制造。"),
        _block("ev-5-2", page=5, block=2, etype="paragraph", text="海外收入占比持续提升。"),
        _block("ev-6-0", page=6, block=0, etype="paragraph", text="（续）储能业务快速增长。"),
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, main_blocks)
        harness_db = root / "harness.db"
        out_root = root / "out"

        e_ok = _seed_entry("c1", "company_business_main.main_business", "ev-5-1")
        e_bogus = _seed_entry("c2", "company_competitiveness.core_competitiveness",
                              "ev-bogus", page=99, block=0, section=(),
                              text="不存在", content_hash=_sha("不存在"))
        e_incomplete = SeedEntry(
            case_id="c3", company_id="300750",
            aspect_id="company_subsidiaries.major_subsidiaries", query="子公司",
            evidence_id="ev-5-2", document_id="doc1", document_version="v1",
            evidence_set_version="set1", page_number=5, block_index=2,
            section_path=("主营业务分析",), evidence_type="paragraph",
            source_content_hash="", selection_reason="缺哈希", text="")
        manifest = SeedManifest(
            manifest_version=SEED_MANIFEST_VERSION,
            fingerprint=compute_seed_manifest_fingerprint((e_ok, e_bogus, e_incomplete)),
            entries=(e_ok, e_bogus, e_incomplete))

        summary = run_material_slice("r2test", manifest, evidence_db=db,
                                     harness_db=harness_db, out_root=out_root)
        check(summary["seed_count"] == 3, "seed_count == 3")
        check(summary["resolved_count"] == 1, "resolved_count == 1（仅一致 seed）")
        check(summary["material_count"] >= 2, "material_count >= 2（seed + 相邻块）")

        out_dir = out_root / "r2_material_slice_r2test"
        for name in ("seed_manifest.json", "resolved_seed_manifest.json",
                     "seed_discovery_trace.jsonl", "expansion_trace.jsonl",
                     "unread_scope.json", "set_enumeration.json",
                     "aspect_material_matrix.json", "aspect_material_matrix.md",
                     "material_index.json", "material_index.md", "before_after.md",
                     "assemblies.json"):
            check((out_dir / name).exists(), f"产物存在: {name}")
        check((out_dir / "payload_preview").is_dir(), "产物存在: payload_preview/")

        # resolved_seed_manifest：三种 resolution（verified / seed_mismatch / incomplete_candidate）。
        resolved = json.loads((out_dir / "resolved_seed_manifest.json").read_text(encoding="utf-8"))
        by_case = {e["case_id"]: e for e in resolved["entries"]}
        check(by_case["c1"]["resolved"] is True
              and by_case["c1"]["resolution"] == "verified",
              "一致 seed → resolved=verified")
        check(by_case["c2"]["resolved"] is False
              and by_case["c2"]["resolution"] == "seed_mismatch",
              "不存在/错哈希 seed → seed_mismatch（不伪造）")
        check(by_case["c3"]["resolved"] is False
              and by_case["c3"]["resolution"] == "incomplete_candidate",
              "身份字段缺失 seed → incomplete_candidate（不伪造）")

        # material_index 必填字段 + 双哈希两层身份。
        index = json.loads((out_dir / "material_index.json").read_text(encoding="utf-8"))
        check(len(index) >= 2, "material_index.json 有材料条目")
        seed_mat = next(m for m in index if m["component_evidence_id"] == "ev-5-1")
        for field in ("material_id", "material_type", "component_evidence_id",
                      "source_content_hash", "payload_hash", "authority_verdict",
                      "document_id", "document_version", "section_path"):
            check(seed_mat.get(field) not in (None, ""), f"material_index 必填字段: {field}")
        check(seed_mat["source_content_hash"] == _sha("公司主要从事动力电池研发与制造。"),
              "source_content_hash == 真实 EvidenceBlock.content_hash（来源层）")
        check(seed_mat["payload_hash"] != seed_mat["source_content_hash"]
              and len(seed_mat["payload_hash"]) == 64,
              "payload_hash（载体层）≠ source_content_hash，且为 64 hex")
        check(seed_mat["authority_verdict"] == "authoritative",
              "一致 current seed → authoritative")

        # aspect 矩阵六态：md 列出全部六态 label；json 每 aspect 有 state。
        matrix_md = (out_dir / "aspect_material_matrix.md").read_text(encoding="utf-8")
        for label in ("已取得材料", "只有 seed、尚未完成扩读", "权威不通过",
                      "集合边界不完整", "未读取范围", "本轮样本未覆盖"):
            check(label in matrix_md, f"aspect 矩阵含六态: {label}")
        matrix = json.loads((out_dir / "aspect_material_matrix.json").read_text(encoding="utf-8"))
        check(all(r["state"] for r in matrix) and len(matrix) >= 2,
              "aspect_material_matrix.json 每 aspect 有 state")

        # set_enumeration：三个 set_complete aspect 全部有记录。
        se = json.loads((out_dir / "set_enumeration.json").read_text(encoding="utf-8"))
        check(set(se.keys()) == set(SET_ASPECTS),
              "set_enumeration.json 覆盖三个 set_complete aspect")
        check(all("material_type_supported" in v and "verifier_version" in v
                  for v in se.values()),
              "set_enumeration 每条含 material_type_supported + verifier_version")

        # expansion_trace：非空（至少 resolve_seed 步骤）。
        trace_lines = (out_dir / "expansion_trace.jsonl").read_text(encoding="utf-8").splitlines()
        check(len(trace_lines) >= 1, "expansion_trace.jsonl 有步骤记录")

    # ------------------------------------------------------------------
    # 3. discover_seeds fail-closed（无检索工具，不 init/migrate）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        summary = discover_seeds(
            "300750", (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc")
        check(summary["discovery_failed"] is True, "无检索工具 → discovery_failed=True")
        check(summary["candidate_count"] == 0, "无检索工具 → 空 candidate")
        manifest_file = root / "out" / "seed_manifest.json"
        check(manifest_file.exists(), "fail-closed 仍写 seed_manifest.json（空 candidate）")
        check((root / "out" / "seed_discovery_trace.jsonl").exists(),
              "fail-closed 仍写 seed_discovery_trace.jsonl")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
