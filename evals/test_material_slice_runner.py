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

§九：fixture 的 evidence_id/source_content_hash 一律经 ``evidence.ids.content_hash()`` +
``make_evidence_id()`` 计算权威身份（不再使用 ``ev-5-1`` / ``sha(text)`` 作为权威身份）。

全部离线：临时 SQLite evidence.db + harness.db，不调 LLM/网络/博查。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader_v2 as LV2
from contracts import schema_v2 as SV2
from contracts import source_policy as SP
from evidence import ids as evidence_ids
from evidence import store as estore
from harness.context_expansion import BoundaryDecision
from harness.evidence_reader import EvidenceReadResult
from harness.material_slice_runner import (
    SEED_MANIFEST_VERSION,
    SET_ASPECTS,
    SeedEntry,
    SeedManifest,
    _append_unique,
    _contract_content_fingerprint,
    _contract_version,
    _dedup_assemblies,
    _effective_disposition,
    _frozen_contract,
    _runner_dependency_fingerprint,
    _source_policy_content_fingerprint,
    compute_seed_manifest_fingerprint,
    discover_seeds,
    load_seed_manifest,
    run_material_slice,
)
from harness.topic_materials import (
    MaterialAssembly,
    TableAssembly,
    _assembly_id,
    _build_flattened_table_assembly,
)
from harness.set_enumeration import recover_flattened_tables
from retrieval import indexer_v2 as iv2
from retrieval import retriever_v2 as rv2
from retrieval import sparse
from routing import schema as S
from tools import adapters
from tools import contracts as C
from tools.registry import ToolRegistry


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


def _insert_block(db: Path, b: EvidenceReadResult) -> None:
    """向已存在的 evidence.db 追加一块并递增 block_count（不重建索引 → 版本漂移）。"""
    conn = sqlite3.connect(str(db))
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
    conn.execute(
        "UPDATE evidence_sets SET block_count = block_count + 1 "
        "WHERE company_id = ? AND document_id = ? AND document_version = ? "
        "AND evidence_set_version = ?",
        (b.company_id, b.document_id, b.document_version, b.evidence_set_version))
    conn.commit()
    conn.close()


def _seed_entry(case_id: str, aspect_id: str, *,
                page: int = 5, block: int = 1, section: tuple = ("主营业务分析",),
                etype: str = "paragraph", text: str = "公司主要从事动力电池研发与制造。",
                company_id: str = _COMPANY, document_id: str = _DOC,
                document_version: str = _DOCV, evidence_set_version: str = _SETV,
                evidence_id: str | None = None,
                content_hash: str | None = None) -> SeedEntry:
    ch = content_hash if content_hash is not None else evidence_ids.content_hash(text, None)
    eid = evidence_id if evidence_id is not None else evidence_ids.make_evidence_id(
        company_id, document_id, document_version, evidence_set_version, page, block, ch)
    return SeedEntry(
        case_id=case_id, company_id=company_id, aspect_id=aspect_id, query="主营业务",
        evidence_id=eid, document_id=document_id, document_version=document_version,
        evidence_set_version=evidence_set_version, page_number=page, block_index=block,
        section_path=section, evidence_type=etype,
        source_content_hash=ch, selection_reason="eval 合成", text=text)


class _BagEmbedding:
    """确定性 bag-of-tokens 稠密向量（不加载 BGE-M3），用于只读发现检索的 dense 通道。"""

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for t in sparse.tokenize(text):
            h = int(hashlib.sha256(t.encode("utf-8")).hexdigest(), 16) % self.dim
            v[h] += 1.0
        return v

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def _build_readonly_discovery_fixture(root: Path, blocks, model) -> tuple[Path, Path, Path, Path]:
    """构建只读发现 fixture：evidence.db + dense manifest/chroma + sparse 索引。

    索引构建需要 estore 读 evidence（build-time），构建后复位 ``estore._db_path`` 以模拟
    发现阶段的「fresh process」（发现路径不得依赖 init_db / 不得改 _db_path）。
    """
    ev_db = _make_db(root, blocks)
    chroma_dir = root / "chroma"
    sparse_dir = root / "sparse"
    manifest_dir = root / "manifest"
    estore.init_db(str(ev_db))
    iv2.build_index(_COMPANY, db_path=chroma_dir, manifest_dir=manifest_dir, model=model)
    sparse.build_index(_COMPANY, data_dir=sparse_dir)
    estore._db_path = None  # 复位：模拟发现阶段 fresh process
    return ev_db, chroma_dir, sparse_dir, manifest_dir


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
    # 0. item 8：Contract v2 正式绑定（反例#20/#21）+ 冻结内容指纹（无 pseudo-SHA）
    # ------------------------------------------------------------------
    # 反例#21：material_slice_runner 绑定 standard_v3.yaml（contract_version=v2），经 formal v2
    # loader/validator 加载成功（Contract v2 正确绑定）。
    check(_contract_version() == "v2", "反例#21：冻结 Contract v2 的 contract_version == 'v2'")
    check(SV2.CONTRACT_V2_ASSET.endswith("standard_v3.yaml"),
          "反例#21：CONTRACT_V2_ASSET 指向 standard_v3.yaml（v2 载体）")
    check(_frozen_contract().contract_version == "v2",
          "反例#21：_frozen_contract 经 formal v2 loader 加载成功（correct binding）")

    # 反例#20：standard_v2.yaml（Contract v1）经 v2 loader 解析被拦截（不得静默绑定错误契约载体）。
    v1_path = Path("templates") / "contracts" / "standard_v2.yaml"
    try:
        LV2.parse_contract_v2(v1_path.read_text(encoding="utf-8"))
        check(False, "反例#20：Contract v1 经 v2 loader 应被拦截")
    except ValueError:
        check(True, "反例#20：Contract v1（standard_v2.yaml）→ parse_contract_v2 拦截（ValueError）")

    # item 8：冻结内容指纹绑定真实资产（Contract v2 + Source Policy v1），不硬编码 pseudo-SHA。
    real_cfp = SV2.content_fingerprint(LV2.load_contract_v2(str(SV2.CONTRACT_V2_ASSET)).raw)
    check(_contract_content_fingerprint() == real_cfp,
          "item 8：Contract v2 内容指纹 == 真实标准资产内容指纹（非伪哈希）")
    real_spfp = SV2.content_fingerprint(
        SP.load_source_policy("templates/policies/source_policy_v1.yaml").raw)
    check(_source_policy_content_fingerprint() == real_spfp,
          "item 8：Source Policy v1 内容指纹 == 真实冻结资产内容指纹（非伪哈希）")
    dep1 = _runner_dependency_fingerprint()
    dep2 = _runner_dependency_fingerprint()
    check(dep1 == dep2 and len(dep1) == 64 and all(c in "0123456789abcdef" for c in dep1),
          "item 8：runner 依赖指纹确定性 + 64-hex（绑定真实内容指纹与 R2 实现版本）")

    # ------------------------------------------------------------------
    # 1. seed manifest 版本 / fingerprint 校验（fail-closed）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        e1 = _seed_entry("c1", "company_business_main.main_business")
        e2 = _seed_entry("c2", "company_competitiveness.core_competitiveness",
                         evidence_id="ev-bogus", content_hash="0" * 64)
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
        _block(page=5, block=0, etype="heading", text="主营业务分析"),
        _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
        _block(page=5, block=2, etype="paragraph", text="海外收入占比持续提升。"),
        _block(page=6, block=0, etype="paragraph", text="（续）储能业务快速增长。"),
    ]
    seed_block = main_blocks[1]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, main_blocks)
        harness_db = root / "harness.db"
        out_root = root / "out"

        e_ok = _seed_entry("c1", "company_business_main.main_business",
                           page=seed_block.page_number, block=seed_block.block_index,
                           text=seed_block.text)
        e_bogus = _seed_entry("c2", "company_competitiveness.core_competitiveness",
                              evidence_id="ev-bogus", page=99, block=0, section=(),
                              text="不存在", content_hash="0" * 64)
        e_incomplete = SeedEntry(
            case_id="c3", company_id=_COMPANY,
            aspect_id="company_subsidiaries.major_subsidiaries", query="子公司",
            evidence_id=main_blocks[2].evidence_id, document_id=_DOC, document_version=_DOCV,
            evidence_set_version=_SETV, page_number=5, block_index=2,
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
                     "unread_scope.json", "boundary_decisions.json",
                     "set_enumeration.json", "source_object_inventory.json",
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
        seed_mat = next(m for m in index if m["component_evidence_id"] == seed_block.evidence_id)
        for field in ("material_id", "material_type", "component_evidence_id",
                      "source_content_hash", "payload_hash", "authority_verdict",
                      "document_id", "document_version", "section_path"):
            check(seed_mat.get(field) not in (None, ""), f"material_index 必填字段: {field}")
        check(seed_mat.get("boundary_disposition") == "seed",
              "material_index 记录边界处置（seed 块 → seed）")
        check(seed_mat.get("aspect_role") == "source",
              "material_index 记录 aspect 角色（seed 块 → source）")
        check(seed_mat["source_content_hash"] == seed_block.content_hash,
              "source_content_hash == 真实 EvidenceBlock.content_hash（来源层，evidence.ids）")
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

        # 修复 B.2（反例#3）：源对象清单独立于 boundary proof 是否闭合仍生成并持久化（绝不 null）。
        si = json.loads((out_dir / "source_object_inventory.json").read_text(encoding="utf-8"))
        check("company_business_main.main_business" in si,
              "反例#3：source_object_inventory.json 含 main_business aspect 清单")
        mb_inv = si.get("company_business_main.main_business")
        check(isinstance(mb_inv, dict) and isinstance(mb_inv.get("recovery_results"), list)
              and isinstance(mb_inv.get("expected_source_objects"), list),
              "反例#3：main_business 源对象清单非 null（含 expected_source_objects + 逐对象四态）")
        # 即使 set_enumeration 判 material_type_supported=false（boundary proof 不闭合），
        # 清单仍已生成（不提前 return 导致 null）。
        mb_se = (se.get("company_business_main.main_business") or {})
        if mb_se.get("material_type_supported") is False:
            check(isinstance(mb_inv, dict),
                  "反例#3：material_type_supported=false 时清单仍非 null（boundary proof 失败不隐藏清单）")

    # ------------------------------------------------------------------
    # 3. discover_seeds fail-closed（无检索工具，不 init/migrate）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        summary = discover_seeds(
            _COMPANY, (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc")
        check(summary["discovery_failed"] is True, "无检索工具 → discovery_failed=True")
        check(summary["candidate_count"] == 0, "无检索工具 → 空 candidate")
        manifest_file = root / "out" / "candidate_seed_manifest.json"
        check(manifest_file.exists(), "fail-closed 仍写 candidate_seed_manifest.json（空候选）")
        check((root / "out" / "rejected_candidates.json").exists(),
              "fail-closed 仍写 rejected_candidates.json（空）")
        check((root / "out" / "candidate_review.md").exists(),
              "fail-closed 仍写 candidate_review.md（空）")
        check((root / "out" / "seed_discovery_trace.jsonl").exists(),
              "fail-closed 仍写 seed_discovery_trace.jsonl")

    # ------------------------------------------------------------------
    # 4. discover_seeds 检索链返回 FATAL_ERROR → discovery_failed=True
    #    （反例：未 init DB 时 execute 把异常转成 FATAL_ERROR ToolResult，
    #      而非抛出；discover_seeds 必须据此 fail-closed，而非报 0 候选的成功）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")

        def _fatal(args: dict) -> C.ToolResult:
            return C.ToolResult(
                call_id="", tool_name="", tool_version="v1",
                status="FATAL_ERROR", data={}, error_code="INTERNAL_ERROR",
                message="Evidence DB not initialized", retryable=False, trace_id="t")

        for name in ("search_evidence", "search_tables"):
            registry.register(
                C.ToolSpec(name=name, version="v1", description="", input_schema={},
                           output_schema={}, allowed_routes=("DIRECT_EVIDENCE",),
                           max_results=5, timeout_ms=0, retry_policy="none",
                           cost_class="local"),
                _fatal)
        summary = discover_seeds(
            _COMPANY, (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc-fatal")
        check(summary["discovery_failed"] is True,
              "检索链 FATAL_ERROR → discovery_failed=True（不报成功）")
        check(summary["candidate_count"] == 0,
              "检索链 FATAL_ERROR → 不伪造 candidate")

    # ------------------------------------------------------------------
    # 5. §五：只读发现成功（fresh process：不 init_db、不改 _db_path、DB 只读不变、trace 落盘）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        blocks = [
            _block(page=5, block=0, etype="heading", text="主营业务分析"),
            _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
            _block(page=5, block=2, etype="paragraph", text="储能业务快速增长，海外收入占比持续提升。"),
        ]
        ev_db, chroma_dir, sparse_dir, manifest_dir = _build_readonly_discovery_fixture(
            root, blocks, _BagEmbedding())
        db_bytes_before = ev_db.read_bytes()

        registry = adapters.build_readonly_discovery_registry(
            ev_db=ev_db, chroma_dir=chroma_dir, sparse_dir=sparse_dir,
            manifest_dir=manifest_dir, audit_dir=root / "tool_audit",
            model=_BagEmbedding())
        queries = (("company_business_main.main_business", "主营业务"),
                   ("company_competitiveness.core_competitiveness", "核心竞争力"))
        summary = discover_seeds(_COMPANY, queries, registry, root / "out", "disc-ro")
        check(summary["discovery_failed"] is False,
              "§五：只读发现（fresh process）→ discovery_failed=False")
        check(summary["candidate_count"] >= 1, "§五：只读发现产生 >=1 候选")
        check(estore._db_path is None,
              "§五：发现后 estore._db_path 仍为 None（未 init_db / 未改全局路径）")
        check(ev_db.read_bytes() == db_bytes_before,
              "§五：只读发现不改 evidence.db（字节不变）")

        manifest = load_seed_manifest(root / "out" / "candidate_seed_manifest.json")
        candidates = [e for e in manifest.entries
                      if e.aspect_id == "company_business_main.main_business"]
        cand_ids = {e.evidence_id for e in candidates}
        check(len(candidates) >= 1, "§五：主营业务 aspect 有候选")
        check(cand_ids <= {b.evidence_id for b in blocks},
              "§五：候选 evidence_id 全部来自 fixture（不伪造外源）")
        check(blocks[0].evidence_id in cand_ids,
              "§五：候选含「主营业务分析」heading 块（正式 search_evidence 排序）")
        # §二：候选身份闭环——每个候选的完整技术身份必须已由 resolve_seed_identity 复验填满。
        for e in candidates:
            ok = bool(e.document_id and e.document_version and e.evidence_set_version
                      and e.source_content_hash and e.block_index is not None
                      and e.section_path and e.is_current_document and e.is_current_set)
            check(ok, f"§二：候选 {e.evidence_id} 完整身份已复验（非空 doc/version/set/hash/block/section）")
        check(all(e.source_tool in ("search_evidence", "search_tables") for e in candidates),
              "§二：候选记录检索来源工具")
        # rejected_candidates.json / candidate_review.md 均落盘（§二产物布局）。
        check((root / "out" / "rejected_candidates.json").exists(),
              "§五：rejected_candidates.json 落盘")
        check((root / "out" / "candidate_review.md").exists(),
              "§五：candidate_review.md 落盘")

        audit_dir = root / "tool_audit"
        audit_files = list(audit_dir.glob("*")) if audit_dir.exists() else []
        check(len(audit_files) >= 1, "§五：ToolRegistry 审计 trace 已落盘")

    # ------------------------------------------------------------------
    # 6. §五：只读发现 fail-closed（缺 evidence.db / 缺索引 → 不建库、不伪造候选）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        missing_ev = root / "missing" / "evidence.db"
        registry = adapters.build_readonly_discovery_registry(
            ev_db=missing_ev, chroma_dir=root / "chroma", sparse_dir=root / "sparse",
            manifest_dir=root / "manifest", audit_dir=root / "tool_audit",
            model=_BagEmbedding())
        summary = discover_seeds(
            _COMPANY, (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc-missing")
        check(summary["discovery_failed"] is True,
              "§五：缺 evidence.db → discovery_failed=True")
        check(summary["candidate_count"] == 0, "§五：缺库不伪造候选")
        check(not missing_ev.exists(), "§五：缺库路径不被创建（不建库）")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        blocks = [_block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。")]
        ev_db, _, _, _ = _build_readonly_discovery_fixture(root, blocks, _BagEmbedding())
        registry = adapters.build_readonly_discovery_registry(
            ev_db=ev_db, chroma_dir=root / "chroma", sparse_dir=root / "no_sparse",
            manifest_dir=root / "manifest", audit_dir=root / "tool_audit",
            model=_BagEmbedding())
        summary = discover_seeds(
            _COMPANY, (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc-noindex")
        check(summary["discovery_failed"] is True,
              "§五：缺 sparse 索引 → discovery_failed=True")
        check(summary["candidate_count"] == 0, "§五：缺索引不伪造候选")

    # ------------------------------------------------------------------
    # 7. §五：索引版本漂移 → fail-closed（不伪造候选）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        blocks = [_block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。")]
        ev_db, chroma_dir, sparse_dir, manifest_dir = _build_readonly_discovery_fixture(
            root, blocks, _BagEmbedding())
        # 追加新块但不重建索引 → 索引版本漂移（content 指纹/record_count 变化）。
        _insert_block(ev_db, _block(page=6, block=0, etype="paragraph",
                                    text="新增储能业务板块。"))
        registry = adapters.build_readonly_discovery_registry(
            ev_db=ev_db, chroma_dir=chroma_dir, sparse_dir=sparse_dir,
            manifest_dir=manifest_dir, audit_dir=root / "tool_audit",
            model=_BagEmbedding())
        summary = discover_seeds(
            _COMPANY, (("company_business_main.main_business", "主营业务"),),
            registry, root / "out", "disc-drift")
        check(summary["discovery_failed"] is True,
              "§五：索引版本漂移 → discovery_failed=True")
        check(summary["candidate_count"] == 0, "§五：版本漂移不伪造候选")

    # ------------------------------------------------------------------
    # 8. §二：候选身份闭环（resolve_seed_identity 逐条复验；未注册/fail/mismatch/空文本/去重）
    # ------------------------------------------------------------------
    def _mk_spec(name: str) -> C.ToolSpec:
        return C.ToolSpec(name=name, version="v1", description="", input_schema={},
                          output_schema={}, allowed_routes=("DIRECT_EVIDENCE",),
                          max_results=5, timeout_ms=0, retry_policy="none", cost_class="local")

    def _sr(items: list[dict]) -> dict:
        return {"items": items}

    def _sres(items: list[dict]) -> C.ToolResult:
        return C.ToolResult(call_id="", tool_name="search_evidence", tool_version="v1",
                            status="SUCCESS", data=_sr(items), error_code=None, message=None,
                            retryable=False, trace_id="t")

    def _block_dict(evidence_id: str = "ev-A", text: str = "公司主要从事动力电池研发与制造。",
                    page: int = 5, block: int = 1, etype: str = "paragraph",
                    document_id: str = _DOC, document_version: str = _DOCV,
                    evidence_set_version: str = _SETV) -> dict:
        return {
            "evidence_id": evidence_id, "company_id": _COMPANY,
            "document_id": document_id, "document_version": document_version,
            "evidence_set_version": evidence_set_version,
            "source_name": "年报", "source_type": "annual_report",
            "page_number": page, "block_index": block,
            "section_path": ["主营业务分析"], "evidence_type": etype,
            "text": text, "structured_payload": None,
            "content_hash": evidence_ids.content_hash(text, None),
            "report_period": None, "published_at": None, "source_uri": None,
        }

    def _rres(data: dict, status: str = "SUCCESS", message: str | None = None,
              error_code: str | None = None) -> C.ToolResult:
        return C.ToolResult(call_id="", tool_name="resolve_seed_identity", tool_version="v1",
                            status=status, data=data, error_code=error_code, message=message,
                            retryable=False, trace_id="t")

    # 8a. resolver 未注册 → 命中无法复验身份 → 0 候选，全部 rejected（绝不伪造 confirmable）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        registry.register(_mk_spec("search_evidence"),
                          lambda args: _sres([{"evidence_id": "ev-A", "page_number": 5,
                                               "evidence_type": "paragraph", "score": 1.0,
                                               "rank": 1, "snippet": "s"}]))
        registry.register(_mk_spec("search_tables"), lambda args: _sres([]))
        summary = discover_seeds(_COMPANY, (("company_business_main.main_business", "主营业务"),),
                                 registry, root / "out", "disc-noresolver")
        check(summary["candidate_count"] == 0, "§二：resolver 未注册 → 0 候选")
        check(summary["rejected_count"] >= 1, "§二：resolver 未注册 → 命中全部 rejected")
        rejected = json.loads((root / "out" / "rejected_candidates.json").read_text(encoding="utf-8"))
        check(any("未注册" in r["reason"] for r in rejected["rejected"]),
              "§二：rejected 记录 resolver 未注册原因")
        check(not (root / "out" / "confirmed_seed_manifest.json").exists(),
              "§二：candidate/confirmed 严格分离（绝不生成 confirmed）")

    # 8b. search → resolve 成功 → 完整身份候选（含 source_tool/rank/score/current）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        registry.register(_mk_spec("search_evidence"),
                          lambda args: _sres([{"evidence_id": "ev-A", "page_number": 5,
                                               "evidence_type": "paragraph", "score": 0.9,
                                               "rank": 1, "snippet": "s"}]))
        registry.register(_mk_spec("search_tables"), lambda args: _sres([]))
        registry.register(_mk_spec("resolve_seed_identity"),
                          lambda args: _rres({"blocks": [_block_dict(evidence_id="ev-A")],
                                              "is_current_document": True, "is_current_set": True}))
        summary = discover_seeds(_COMPANY, (("company_business_main.main_business", "主营业务"),),
                                 registry, root / "out", "disc-ok")
        check(summary["candidate_count"] == 1, "§二：resolve 成功 → 1 个完整身份候选")
        manifest = load_seed_manifest(root / "out" / "candidate_seed_manifest.json")
        e = manifest.entries[0]
        check(e.evidence_id == "ev-A" and e.document_id == _DOC
              and e.document_version == _DOCV and e.evidence_set_version == _SETV
              and e.source_content_hash == evidence_ids.content_hash(e.text, None)
              and e.block_index is not None and e.section_path,
              "§二：候选完整身份已由 resolve 填满")
        check(e.source_tool == "search_evidence" and e.rank == 1 and e.score == 0.9,
              "§二：候选记录 source_tool/rank/score")
        check(e.is_current_document is True and e.is_current_set is True,
              "§二：候选记录 current document/set 判定")

    # 8c. resolve fail-closed（mismatch）→ rejected（不进 candidate）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        registry.register(_mk_spec("search_evidence"),
                          lambda args: _sres([{"evidence_id": "ev-A", "page_number": 5,
                                               "evidence_type": "paragraph", "score": 1.0,
                                               "rank": 1, "snippet": "s"}]))
        registry.register(_mk_spec("search_tables"), lambda args: _sres([]))
        registry.register(_mk_spec("resolve_seed_identity"),
                          lambda args: _rres({}, status="FATAL_ERROR",
                                             message="seed mismatch: content_hash",
                                             error_code="INTERNAL_ERROR"))
        summary = discover_seeds(_COMPANY, (("company_business_main.main_business", "主营业务"),),
                                 registry, root / "out", "disc-mismatch")
        check(summary["candidate_count"] == 0, "§二：resolve mismatch → 0 候选")
        rejected = json.loads((root / "out" / "rejected_candidates.json").read_text(encoding="utf-8"))
        check(any("mismatch" in r["reason"] for r in rejected["rejected"]),
              "§二：rejected 记录 mismatch 原因")

    # 8d. resolve 成功但 block 文本为空 → rejected（不得把空正文当候选材料）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        registry.register(_mk_spec("search_evidence"),
                          lambda args: _sres([{"evidence_id": "ev-A", "page_number": 5,
                                               "evidence_type": "paragraph", "score": 1.0,
                                               "rank": 1, "snippet": ""}]))
        registry.register(_mk_spec("search_tables"), lambda args: _sres([]))
        registry.register(_mk_spec("resolve_seed_identity"),
                          lambda args: _rres({"blocks": [_block_dict(evidence_id="ev-A", text="  ")],
                                              "is_current_document": True, "is_current_set": True}))
        summary = discover_seeds(_COMPANY, (("company_business_main.main_business", "主营业务"),),
                                 registry, root / "out", "disc-empty")
        check(summary["candidate_count"] == 0, "§二：空文本 → 0 候选")
        rejected = json.loads((root / "out" / "rejected_candidates.json").read_text(encoding="utf-8"))
        check(any("文本为空" in r["reason"] for r in rejected["rejected"]),
              "§二：rejected 记录空文本原因")

    # 8e. 同一 evidence_id+content_hash 被 search_evidence 与 search_tables 同时命中 → 去重（1 候选）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        registry = ToolRegistry(audit_dir=root / "audit")
        item = {"evidence_id": "ev-A", "page_number": 5, "evidence_type": "paragraph",
                "score": 1.0, "rank": 1, "snippet": "s"}
        registry.register(_mk_spec("search_evidence"), lambda args: _sres([item]))
        registry.register(_mk_spec("search_tables"), lambda args: _sres([item]))
        registry.register(_mk_spec("resolve_seed_identity"),
                          lambda args: _rres({"blocks": [_block_dict(evidence_id="ev-A")],
                                              "is_current_document": True, "is_current_set": True}))
        summary = discover_seeds(_COMPANY, (("company_business_main.main_business", "主营业务"),),
                                 registry, root / "out", "disc-dup")
        check(summary["candidate_count"] == 1,
              "§二：同一 evidence_id+content_hash 被两检索工具命中 → 去重为 1 候选")

    # ------------------------------------------------------------------
    # 9. §四 修复二：_effective_disposition rank-max（非 first-wins）+ _append_unique 稳定去重
    # ------------------------------------------------------------------
    def _bd(disposition, reason_code="r"):
        return BoundaryDecision(
            evidence_id="ev-x", disposition=disposition, reason_code=reason_code,
            relation="adjacent", direction="adjacent_blocks_after",
            page_number=5, block_index=2, section_path=("s",),
            evidence_type="paragraph", content_hash="c" * 64,
            document_id=_DOC, document_version=_DOCV, evidence_set_version=_SETV,
            seed_section_path=("s",), path_quality="reliable")

    check(_effective_disposition(None, _bd("inside_boundary")) ==
          {"disposition": "inside_boundary", "reason_code": "r"},
          "修复二：无既有 → 直接取当前（rank-max 基线）")
    check(_effective_disposition(
              {"disposition": "outside_boundary_sentinel", "reason_code": "s"},
              _bd("inside_boundary"))["disposition"] == "inside_boundary",
          "修复二：sentinel + inside → inside（rank-max 非 first-wins 反例）")
    check(_effective_disposition(
              {"disposition": "seed", "reason_code": "s"},
              _bd("inside_boundary"))["disposition"] == "seed",
          "修复二：seed 优先（高 rank 不被低 rank 覆盖）")
    check(_effective_disposition(
              {"disposition": "inside_boundary", "reason_code": "i"},
              _bd("outside_boundary_sentinel"))["disposition"] == "inside_boundary",
          "修复二：inside 不被 sentinel 降级（确定性，与 seed 顺序无关）")

    m = {}
    _append_unique(m, "a1", "mid-1")
    _append_unique(m, "a1", "mid-1")
    _append_unique(m, "a1", "mid-2")
    check(m["a1"] == ["mid-1", "mid-2"],
          "修复二：_append_unique 稳定去重（无重复 material_id）")

    # ------------------------------------------------------------------
    # 10. §四 修复二：多 seed 聚合集成——rank-max 边界处置 / source-wins 角色 / formal-context 分离去重
    # ------------------------------------------------------------------
    blocks = [
        _block(page=5, block=0, etype="heading", text="主营业务分析"),
        _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
        _block(page=5, block=2, etype="paragraph", text="海外收入占比提升。"),
        _block(page=5, block=3, etype="paragraph", text="储能业务快速增长。"),
        _block(page=5, block=4, etype="heading", section=("风险因素",), text="风险因素"),
        _block(page=5, block=5, etype="paragraph", section=("风险因素",),
               text="公司面临原材料价格波动风险。"),
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, blocks)
        harness_db = root / "harness.db"
        out_root = root / "out"
        aspect = "company_business_main.main_business"
        e_a = _seed_entry("ca", aspect, page=5, block=1, section=("主营业务分析",),
                          text="公司主要从事动力电池研发与制造。")
        e_c = _seed_entry("cc", aspect, page=5, block=3, section=("主营业务分析",),
                          text="储能业务快速增长。")
        e_b = _seed_entry("cb", aspect, page=5, block=4, section=("风险因素",),
                          text="风险因素", etype="heading")
        manifest = SeedManifest(
            manifest_version=SEED_MANIFEST_VERSION,
            fingerprint=compute_seed_manifest_fingerprint((e_a, e_c, e_b)),
            entries=(e_a, e_c, e_b))
        run_material_slice("r2agg", manifest, evidence_db=db,
                           harness_db=harness_db, out_root=out_root)
        out_dir = out_root / "r2_material_slice_r2agg"
        index = json.loads((out_dir / "material_index.json").read_text(encoding="utf-8"))
        by_ev = {e["component_evidence_id"]: e for e in index}

        def disp(i):
            return by_ev[blocks[i].evidence_id]["boundary_disposition"]

        def role(i):
            return by_ev[blocks[i].evidence_id]["aspect_role"]

        check(disp(4) == "seed",
              "修复二：(5,4) sentinel(from A/C) + seed(from B) → 有效处置 seed（rank-max）")
        check(disp(3) == "seed",
              "修复二：(5,3) inside(from A) + seed(from C) + sentinel(from B) → seed")
        check(disp(2) == "inside_boundary",
              "修复二：(5,2) inside(from A/C) → inside_boundary（绝不 sentinel）")
        check(role(4) == "source",
              "修复二：(5,4) 角色 source（source-wins，非 last-wins）")
        check(role(2) == "context_candidate",
              "修复二：(5,2) 角色 context_candidate（非 seed）")
        membership = json.loads(
            (out_dir / "aspect_membership.json").read_text(encoding="utf-8"))[aspect]
        formal = membership["formal_material_ids"]
        context = membership["context_candidate_material_ids"]
        check(len(formal) == 3 and len(context) == 3,
              "修复二：formal==3（三个 seed）+ context==3（三个非 seed inside）")
        check(not set(formal) & set(context),
              "修复二：formal 与 context_candidate 无交集（context 不计正式材料）")
        check(len(context) == len(set(context)) and len(formal) == len(set(formal)),
              "修复二：formal/context material_id 各自稳定去重")
        all_mids = [e["material_id"] for e in index]
        check(len(all_mids) == len(set(all_mids)),
              "修复二：material_index 无重复 material_id")

    # ------------------------------------------------------------------
    # 11. §四 修复四：跨 aspect 角色分离 + 显式 aspect_links（seed 顺序无关）
    # ------------------------------------------------------------------
    xblocks = [
        _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
        _block(page=5, block=2, etype="paragraph", text="海外收入占比提升。"),
        _block(page=5, block=3, etype="paragraph", text="储能业务快速增长。"),
    ]
    aspect_a = "company_business_main.main_business"
    aspect_b = "company_competitiveness.core_competitiveness"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, xblocks)
        harness_db = root / "harness.db"
        out_root = root / "out"
        e_a = _seed_entry("ca", aspect_a, page=5, block=1,
                          text="公司主要从事动力电池研发与制造。")
        e_b = _seed_entry("cb", aspect_b, page=5, block=2,
                          text="海外收入占比提升。")

        def _run(run_id, entries):
            mf = SeedManifest(
                manifest_version=SEED_MANIFEST_VERSION,
                fingerprint=compute_seed_manifest_fingerprint(entries),
                entries=entries)
            run_material_slice(run_id, mf, evidence_db=db,
                               harness_db=harness_db, out_root=out_root)
            return out_root / f"r2_material_slice_{run_id}"

        out_dir = _run("r2cross", (e_a, e_b))
        index = json.loads((out_dir / "material_index.json").read_text(encoding="utf-8"))
        by_ev = {e["component_evidence_id"]: e for e in index}
        # block2：source for B（seed）＋ context_candidate for A（扩读相邻），同一 material 双角色。
        b2 = by_ev[xblocks[1].evidence_id]
        check(b2.get("aspect_roles", {}).get(aspect_b) == "source",
              "修复四：同一 material 对 B 为 source、对 A 为 context（跨 aspect 双角色，非扁平单键）")
        check(b2.get("aspect_roles", {}).get(aspect_a) == "context_candidate",
              "修复四：block2 对 A 的角色为 context_candidate")
        # block1：source for A ＋ context_candidate for B。
        b1 = by_ev[xblocks[0].evidence_id]
        check(b1.get("aspect_roles", {}).get(aspect_a) == "source"
              and b1.get("aspect_roles", {}).get(aspect_b) == "context_candidate",
              "修复四：block1 对 A 为 source、对 B 为 context_candidate")

        links = json.loads((out_dir / "aspect_links.json").read_text(encoding="utf-8"))
        check(all(k in l for l in links
                  for k in ("aspect_id", "material_id", "role", "disposition", "seed_reachability")),
              "修复四：aspect_links.json 每条含 aspect_id/role/disposition/seed_reachability")
        b2_links = [l for l in links if l["material_id"] == b2["material_id"]]
        roles_by_aspect = {l["aspect_id"]: l["role"] for l in b2_links}
        check(roles_by_aspect.get(aspect_b) == "source"
              and roles_by_aspect.get(aspect_a) == "context_candidate",
              "修复四：aspect_links 显式记录 block2 的双角色（按 aspect 分列）")
        b2_link_b = next(l for l in b2_links if l["aspect_id"] == aspect_b)
        check(b2_link_b["disposition"] == "seed"
              and e_b.evidence_id in b2_link_b["seed_reachability"],
              "修复四：aspect_links seed_reachability 记录可达 seed evidence_id")

        # budget_profile.json 显式落盘（来源/版本/限制/实际消耗/未读范围）。
        bp = json.loads((out_dir / "budget_profile.json").read_text(encoding="utf-8"))
        check(bp.get("profile_name") == "production"
              and isinstance(bp.get("budget_limits"), dict)
              and isinstance(bp.get("seed_budget_records"), list),
              "修复四：budget_profile.json 显式记录 profile/limits/每 seed 消耗")

        # seed 顺序无关：反转 seed 顺序重跑，aspect_links 应逐条一致。
        out_dir2 = _run("r2cross_rev", (e_b, e_a))
        links2 = json.loads((out_dir2 / "aspect_links.json").read_text(encoding="utf-8"))
        norm = lambda l: {  # noqa: E731
            "aspect_id": l["aspect_id"], "material_id": l["material_id"],
            "role": l["role"], "disposition": l["disposition"],
            "seed_reachability": sorted(l["seed_reachability"])}
        check(sorted((norm(l)["aspect_id"], norm(l)["material_id"], norm(l)["role"],
                      norm(l)["disposition"], tuple(norm(l)["seed_reachability"]))
                     for l in links) ==
              sorted((norm(l)["aspect_id"], norm(l)["material_id"], norm(l)["role"],
                      norm(l)["disposition"], tuple(norm(l)["seed_reachability"]))
                     for l in links2),
              "修复四：aspect_links 与 seed 处理顺序无关（反转顺序结果一致）")

    # ------------------------------------------------------------------
    # 12. P1-2：assembly 按 content-addressed ID 确定性去重（同 ID 同内容 → 一条；
    #     同 ID 不同内容 → fail-closed 抛错）
    # ------------------------------------------------------------------
    def _mk_asm(assembly_id: str, component: tuple, relation: str = "adjacent",
                boundary_desc: str = "P5"):
        return MaterialAssembly(
            assembly_id=assembly_id, context_parent_id=component[0],
            component_material_ids=component, relation=relation,
            boundary_desc=boundary_desc)

    dup = [
        _mk_asm("asm-dup", ("m1", "m2")),
        _mk_asm("asm-dup", ("m1", "m2")),
        _mk_asm("asm-other", ("m3",)),
    ]
    deduped = _dedup_assemblies(dup)
    check([a.assembly_id for a in deduped] == ["asm-dup", "asm-other"],
          "P1-2：同 ID 同内容 → 去重为一条（顺序稳定）")
    conflict = [
        _mk_asm("asm-x", ("m1", "m2")),
        _mk_asm("asm-x", ("m1", "m3")),
    ]
    try:
        _dedup_assemblies(conflict)
        check(False, "P1-2：同 ID 不同内容应抛 ValueError")
    except ValueError:
        check(True, "P1-2：同 ID 不同内容 → fail-closed（拒绝覆盖/静默丢弃）")

    # ------------------------------------------------------------------
    # 13. P1-3：单个摊平 block 内多张不同表的 assembly_id 唯一（content-addressed 身份
    #     并入恢复出的表结构，杜绝同 component 异表冲突）
    # ------------------------------------------------------------------
    check(_assembly_id(("m1",), "flattened_table_recovery", "A")
          != _assembly_id(("m1",), "flattened_table_recovery", "B"),
          "P1-3：同 component 异 discriminator → 异 assembly_id（不再冲突）")
    check(_assembly_id(("m1",), "flattened_table_recovery", "A")
          == _assembly_id(("m1",), "flattened_table_recovery", "A"),
          "P1-3：同 component 同 discriminator → 同 assembly_id（去重仍有效）")
    # 同一 block 摊平出研发投入表 + 现金流量表（结构不同但 component 相同）。
    multi_table_block = (
        "表 5-13 发行人研发投入构成表\n单位：万元\n项目  2025年  2024年\n"
        "研发投入金额  22,146,581  18,606,756\n合计  22,146,581  18,606,756\n"
        "表 5-14 现金流量构成表\n单位：万元\n项目  2025年  2024年\n"
        "经营活动现金流量净额  133,219,982  96,990,345\n合计  133,219,982  96,990,345\n"
    )
    mtables = recover_flattened_tables([multi_table_block])
    check(len(mtables) == 2,
          "P1-3：单 block 摊平两张表被独立恢复")
    check(all(t["structure_text_indices"] == [0] for t in mtables),
          "P1-3：两张表同属同一 block（component 相同，是历史冲突根因）")
    fake_mat = types.SimpleNamespace(
        material_id="mat-block0",
        authority_assessment=types.SimpleNamespace(evidence_id="ev-block0", page=1))
    mt_asm = [_build_flattened_table_assembly(t, [fake_mat], {}, [multi_table_block])
              for t in mtables]
    mt_asm = [a for a in mt_asm if a is not None]
    check(len(mt_asm) == 2 and mt_asm[0].assembly_id != mt_asm[1].assembly_id,
          "P1-3：同 component 的两张表 → 异 assembly_id（结构并入身份，杜绝冲突）")
    check(all(a.component_material_ids == ("mat-block0",) for a in mt_asm),
          "P1-3：component 仍指向真实 block material（身份仅加结构判别，不改投影）")

    # ------------------------------------------------------------------
    # 14. 修复 A.3：fragment（主题内前缀投影）可关联、raw sentinel 不落成材料链接
    # ------------------------------------------------------------------
    fx_blocks = [
        _block(page=5, block=1, etype="paragraph", text="公司主要从事动力电池研发与制造。"),
        _block(page=5, block=2, etype="paragraph",
               text="海外收入占比提升。（三）在建工程情况：报告期内在建工程余额较大。"),
    ]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = _make_db(root, fx_blocks)
        harness_db = root / "harness.db"
        out_root = root / "out"
        e_fx = _seed_entry("cfrag", aspect_a, page=5, block=1,
                           text="公司主要从事动力电池研发与制造。")
        mf = SeedManifest(
            manifest_version=SEED_MANIFEST_VERSION,
            fingerprint=compute_seed_manifest_fingerprint((e_fx,)),
            entries=(e_fx,))
        run_material_slice("r2frag", mf, evidence_db=db,
                           harness_db=harness_db, out_root=out_root)
        out_dir = out_root / "r2_material_slice_r2frag"
        links = json.loads((out_dir / "aspect_links.json").read_text(encoding="utf-8"))
        check(any(l.get("disposition") == "fragment_projection" for l in links),
              "修复 A.3：aspect_links 含 fragment_projection（前缀投影材料可关联）")
        check(not any(l.get("disposition") == "outside_boundary_sentinel" for l in links),
              "修复 A.3：raw sentinel 的 outside_boundary_sentinel 不落成 aspect_links")
        f_index = json.loads((out_dir / "material_index.json").read_text(encoding="utf-8"))
        frag_entries = [e for e in f_index
                        if e.get("boundary_disposition") == "fragment_projection"]
        check(len(frag_entries) == 1
              and frag_entries[0]["component_evidence_id"] == fx_blocks[1].evidence_id
              and frag_entries[0]["material_type"] == "evidence_span",
              "修复 A.3：material_index 中 fragment 材料 component 指向 raw block evidence_id")
        check(not any(e.get("boundary_disposition") == "outside_boundary_sentinel"
                      for e in f_index),
              "修复 A.3：material_index 无 raw sentinel 材料（sentinel 不进 material library）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
