"""R2 真实本地材料验收 runner + 两阶段 seed（R2_IMPLEMENTATION_PLAN §12/§13）。

CLI：
  python -m harness.material_slice_runner --run-id <id> --seed-manifest <path> [--out-root evaluation/results]
  python -m harness.material_slice_runner --discover-seeds --run-id <id> --company-id <id> [--out-root ...]

硬约束（零 LLM / 零网络 / 零博查）：
- 只读 ``data/evidence.db``（经现有 ToolRegistry 的 bounded Evidence inspection 工具，mode=ro+
  query_only）+ 写 ``data/harness.db`` 的 ``topic_material_payload`` 表；
- 不调用 ``sections.topic_research.run_topic``，不建第二 Router/Harness/Retriever/ToolRegistry 循环；
- 不调用 ``evidence.store.init_db()``，不修改 ``evidence.store._db_path``；
- 不把 candidate seed 自行标记为「人工确认」，不进入 R3 调度；不硬编码 300750/evidence_id/页码/表号。

两阶段 seed（修正二）：
- 阶段一 ``discover-seeds``（evaluation-only）：经现有正式 ``search_evidence``/``search_tables``
  链确定性派生候选（不调 LLM），只输出 candidate manifest；无法在严格不 init/migrate 条件下运行
  → fail-closed，要求显式提供已确认 seed manifest。
- 阶段二正式验收 runner（``--seed-manifest`` 必填）：不信任 manifest 自报值，经 bounded
  ToolRegistry 复验 seed 身份；不一致 → fail-closed（写 trace/unread，绝不静默重选页面）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from harness import topic_schema as TS
from harness import topic_store as Store
from harness.context_expansion import (
    EXPANSION_DIRECTIONS,
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    expand,
)
from harness.evidence_reader import (
    DEFAULT_EVIDENCE_DB_PATH,
    register_bounded_evidence_tool,
)
from harness.set_enumeration import (
    FormalSetEnumerationVerifier,
    build_formal_set_enumeration_verifier,
)
from harness.topic_materials import build_material_result
from tools import contracts as C
from tools.registry import ToolRegistry

DEFAULT_HARNESS_DB_PATH = Path("data/harness.db")
DEFAULT_OUT_ROOT = Path("evaluation/results")

SEED_MANIFEST_VERSION = "1"

# 三个 set_complete aspect（§8）；验收 runner 只为这三个 aspect 出 set_enumeration.json。
SET_ASPECTS = (
    "company_subsidiaries.major_subsidiaries",
    "company_business_main.main_business",
    "company_competitiveness.core_competitiveness",
)

# aspect 材料覆盖矩阵六态（§13）；第 6 态禁止写成「材料不存在」。
MATRIX_STATES = (
    ("obtained", "已取得材料"),
    ("seed_only", "只有 seed、尚未完成扩读"),
    ("authority_failed", "权威不通过"),
    ("boundary_incomplete", "集合边界不完整"),
    ("unread_scope", "未读取范围"),
    ("not_covered", "本轮样本未覆盖"),
)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# seed manifest（版本化、仅 evaluation 输入）
# ---------------------------------------------------------------------------

@dataclass
class SeedEntry:
    case_id: str
    company_id: str
    aspect_id: str
    query: str
    evidence_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_number: int | None
    block_index: int | None
    section_path: tuple[str, ...]
    evidence_type: str
    source_content_hash: str
    selection_reason: str
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "company_id": self.company_id,
            "aspect_id": self.aspect_id,
            "query": self.query,
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "section_path": list(self.section_path),
            "evidence_type": self.evidence_type,
            "source_content_hash": self.source_content_hash,
            "selection_reason": self.selection_reason,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SeedEntry":
        return cls(
            case_id=str(d.get("case_id", "")),
            company_id=str(d.get("company_id", "")),
            aspect_id=str(d.get("aspect_id", "")),
            query=str(d.get("query", "")),
            evidence_id=str(d.get("evidence_id", "")),
            document_id=str(d.get("document_id", "")),
            document_version=str(d.get("document_version", "")),
            evidence_set_version=str(d.get("evidence_set_version", "")),
            page_number=d.get("page_number"),
            block_index=d.get("block_index"),
            section_path=tuple(d.get("section_path") or ()),
            evidence_type=str(d.get("evidence_type", "")),
            source_content_hash=str(d.get("source_content_hash", "")),
            selection_reason=str(d.get("selection_reason", "")),
            text=str(d.get("text", "")),
        )


@dataclass
class SeedManifest:
    manifest_version: str
    fingerprint: str
    entries: tuple[SeedEntry, ...]

    def to_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "fingerprint": self.fingerprint,
            "entries": [e.to_dict() for e in self.entries],
        }


def compute_seed_manifest_fingerprint(entries: tuple[SeedEntry, ...]) -> str:
    canon = [dict(sorted(e.to_dict().items())) for e in entries]
    return _sha(json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _manifest_from_entries(entries: tuple[SeedEntry, ...]) -> SeedManifest:
    return SeedManifest(
        manifest_version=SEED_MANIFEST_VERSION,
        fingerprint=compute_seed_manifest_fingerprint(entries),
        entries=entries,
    )


def load_seed_manifest(path: str | Path) -> SeedManifest:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    version = str(d.get("manifest_version", ""))
    if version != SEED_MANIFEST_VERSION:
        raise ValueError(
            f"seed manifest 版本不符：期望 {SEED_MANIFEST_VERSION!r}，得到 {version!r}")
    entries = tuple(SeedEntry.from_dict(e) for e in d.get("entries", []))
    manifest = _manifest_from_entries(entries)
    declared = str(d.get("fingerprint", ""))
    if declared and declared != manifest.fingerprint:
        raise ValueError("seed manifest fingerprint 与内容不一致（身份漂移）")
    return manifest


# ---------------------------------------------------------------------------
# 阶段一：discover-seeds（evaluation-only，可 fail-closed）
# ---------------------------------------------------------------------------

def discover_seeds(company_id: str,
                   aspect_queries: tuple[tuple[str, str], ...],
                   registry: ToolRegistry,
                   out_dir: Path,
                   run_id: str) -> dict:
    """经现有 ``search_evidence``/``search_tables`` 链派生候选 seed（不调 LLM）。

    registry 必须已注册 search_evidence/search_tables；缺工具 / 检索链不可用 → fail-closed
    （输出 ``discovery_failed=true`` 与空 candidate，绝不初始化/迁移 DB、绝不改 ``_db_path``）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[SeedEntry] = []
    trace_lines: list[dict] = []
    discovery_failed = False
    for aspect_id, query in aspect_queries:
        for tool_name in ("search_evidence", "search_tables"):
            spec = registry.get(tool_name)
            call = C.ToolCall(
                call_id=uuid.uuid4().hex, tool_name=tool_name,
                arguments={"company_id": company_id, "query": query, "k": 5},
                idempotency_key=f"{run_id}:{tool_name}:{aspect_id}",
                need_id="", batch_id="")
            if spec is None:
                discovery_failed = True
                trace_lines.append({
                    "tool_name": tool_name, "aspect_id": aspect_id,
                    "status": "FAIL_CLOSED", "reason": "检索工具未注册（不可运行）",
                })
                continue
            try:
                result = registry.execute(call, route="DIRECT_EVIDENCE", run_id=run_id)
            except Exception as e:  # noqa: BLE001 - 检索链异常 → fail-closed
                discovery_failed = True
                trace_lines.append({
                    "tool_name": tool_name, "aspect_id": aspect_id,
                    "status": "FAIL_CLOSED", "reason": f"{type(e).__name__}: {e}",
                })
                continue
            trace_lines.append({
                "tool_name": tool_name, "aspect_id": aspect_id, "query": query,
                "status": result.status, "error_code": result.error_code,
                "message": result.message, "trace_id": result.trace_id,
            })
            if result.status in ("SUCCESS", "PARTIAL"):
                for item in result.data.get("items", []):
                    entries.append(SeedEntry(
                        case_id=f"candidate-{len(entries) + 1}",
                        company_id=company_id, aspect_id=aspect_id, query=query,
                        evidence_id=str(item.get("evidence_id", "")),
                        document_id="", document_version="", evidence_set_version="",
                        page_number=item.get("page_number"),
                        block_index=None, section_path=(),
                        evidence_type=str(item.get("evidence_type", "")),
                        source_content_hash="",
                        selection_reason="candidate（待人工确认）",
                        text=str(item.get("snippet", ""))))

    manifest = _manifest_from_entries(tuple(entries))
    _write_json(out_dir / "seed_manifest.json", manifest.to_dict())
    _write_jsonl(out_dir / "seed_discovery_trace.jsonl", trace_lines)
    return {"discovery_failed": discovery_failed, "candidate_count": len(entries),
            "out_dir": str(out_dir)}


# ---------------------------------------------------------------------------
# 阶段二：正式材料验收 runner
# ---------------------------------------------------------------------------

def _runner_dependency_fingerprint() -> str:
    return TS.compute_dependency_fingerprint(
        _sha("r2-acceptance-contract"), "v1",
        {"set_enumerator": TS.SET_ENUMERATION_VERIFIER_VERSION})


def _step_to_dict(step) -> dict:
    tool = step.tool_call
    return {
        "step_index": step.step_index,
        "action": step.action,
        "tool_name": tool.tool_name if tool is not None else None,
        "arguments": tool.arguments if tool is not None else {},
        "outputs": list(step.outputs),
        "stop_reason": step.stop_reason,
        "budget_remaining": step.budget_remaining,
    }


def _material_entry(m: TS.ResearchMaterial, aspect_ids: tuple[str, ...]) -> dict:
    auth = m.authority_assessment
    loc = m.locator
    return {
        "material_id": m.material_id,
        "material_type": m.material_type,
        "source_identity": m.source_identity,
        "component_evidence_id": getattr(auth, "evidence_id", ""),
        "source_content_hash": getattr(auth, "content_hash", ""),
        "payload_hash": m.content_hash,
        "document_id": getattr(auth, "document_id", ""),
        "document_version": getattr(auth, "document_version", ""),
        "section_path": loc.section_path if loc is not None else "",
        "page": loc.page if loc is not None else None,
        "block_range": list(loc.block_range) if loc is not None and loc.block_range else None,
        "table_title": loc.table_title if loc is not None else None,
        "authority_verdict": getattr(auth, "verdict", ""),
        "aspect_ids": list(aspect_ids),
    }


def _enumeration_assessment(aspect_id: str, source_material_ids: tuple[str, ...],
                            document_version: str, source_boundary: str,
                            dep: str) -> TS.SetCompletenessAssessment:
    return TS.SetCompletenessAssessment(
        aspect_id=aspect_id, rule_version=TS.SET_COMPLETENESS_RULE_VERSION,
        source_material_ids=source_material_ids, document_version=document_version,
        source_boundary=source_boundary, expected_member_ids=("__preview__",),
        observed_member_ids=("__preview__",), excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=(), supporting_fact_ids=(), scope_complete=True,
        assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=_sha("r2-acceptance-contract"), dependency_fingerprint=dep)


def _aspect_state(aspect_id: str, *, material_ids: tuple[str, ...],
                  materials_by_id: dict, seed_evidence_ids: tuple[str, ...],
                  rejected_aspects: set, unread_aspects: set,
                  boundary_incomplete_aspects: set) -> str:
    if not material_ids:
        return "authority_failed" if aspect_id in rejected_aspects else "not_covered"
    mats = [materials_by_id[mid] for mid in material_ids if mid in materials_by_id]
    if len(material_ids) == 1 and material_ids[0] not in materials_by_id:
        return "seed_only"
    evidence_ids = {getattr(m.authority_assessment, "evidence_id", "") for m in mats}
    if len(mats) == 1 and evidence_ids == set(seed_evidence_ids):
        return "seed_only"
    verdicts = {getattr(m.authority_assessment, "verdict", "") for m in mats}
    if "authoritative" not in verdicts:
        return "authority_failed"
    if aspect_id in boundary_incomplete_aspects:
        return "boundary_incomplete"
    if aspect_id in unread_aspects:
        return "unread_scope"
    return "obtained"


def _write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def run_material_slice(run_id: str, manifest: SeedManifest, *,
                       evidence_db: str | Path = DEFAULT_EVIDENCE_DB_PATH,
                       harness_db: str | Path = DEFAULT_HARNESS_DB_PATH,
                       out_root: str | Path = DEFAULT_OUT_ROOT,
                       directions: tuple[str, ...] = EXPANSION_DIRECTIONS) -> dict:
    """正式材料验收 runner（阶段二，--seed-manifest 必填）。

    每个 seed 经 ``expand``（bounded ToolRegistry 链）复验身份 → 受控扩读 → atomic material
    构建 → payload 单事务原子落盘 → （set_complete aspect）正式枚举。seed 不一致 → fail-closed。
    """
    out_dir = Path(out_root) / f"r2_material_slice_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    dep = _runner_dependency_fingerprint()
    registry = ToolRegistry(audit_dir=out_dir / "tool_audit")
    register_bounded_evidence_tool(registry, db_path=evidence_db)

    Store.init_topic_store(Path(harness_db))
    resolver = Store.TopicMaterialPayloadResolver(Path(harness_db))
    enumerator = build_formal_set_enumeration_verifier()

    # -- 逐 seed 处理 --
    resolved_entries: list[dict] = []
    all_materials: dict[str, TS.ResearchMaterial] = {}
    all_assemblies: list = []
    aspect_materials: dict[str, list[str]] = {}
    aspect_seeds: dict[str, list[str]] = {}
    rejected_aspects: set[str] = set()
    unread_aspects: set[str] = set()
    seed_before_after: list[dict] = []
    expansion_steps: list[dict] = []
    unread_scopes: list[dict] = []

    for entry in manifest.entries:
        aspect_seeds.setdefault(entry.aspect_id, []).append(entry.evidence_id)
        base = {
            "case_id": entry.case_id, "aspect_id": entry.aspect_id,
            "evidence_id": entry.evidence_id,
        }
        # 身份字段缺失 → 候选未确认，fail-closed（绝不伪造）。
        if not (entry.evidence_id and entry.page_number is not None
                and entry.block_index is not None and entry.document_id
                and entry.document_version and entry.evidence_set_version
                and entry.source_content_hash):
            resolved_entries.append({**base, "resolved": False,
                                     "resolution": "incomplete_candidate",
                                     "message": "seed 身份字段不完整（候选未人工确认）"})
            rejected_aspects.add(entry.aspect_id)
            continue

        seed = ExpansionSeed(
            evidence_id=entry.evidence_id, page_number=entry.page_number,
            block_index=entry.block_index, section_path=entry.section_path,
            evidence_type=entry.evidence_type, text=entry.text,
            content_hash=entry.source_content_hash)
        request = ContextExpansionRequest(
            company_id=entry.company_id, document_id=entry.document_id,
            document_version=entry.document_version,
            evidence_set_version=entry.evidence_set_version, seed=seed,
            directions=directions, budget=ExpansionBudget(),
            dependency_fingerprint=dep)

        expansion = expand(request, registry, run_id=run_id)
        for step in expansion.trace.steps:
            expansion_steps.append(_step_to_dict(step))

        if not expansion.adopted:
            reason = expansion.unread_scope.scope_desc or expansion.stop_reason
            resolved_entries.append({**base, "resolved": False,
                                     "resolution": "seed_mismatch", "message": reason})
            rejected_aspects.add(entry.aspect_id)
            unread_scopes.append({
                "evidence_id": entry.evidence_id, "reason": expansion.unread_scope.reason,
                "scope_desc": expansion.unread_scope.scope_desc,
                "stop_reason": expansion.stop_reason})
            continue

        result = build_material_result(
            expansion, dependency_fingerprint=dep,
            is_current_document=True, is_current_set=True, aspect_id=entry.aspect_id)

        # payload 单事务原子落盘（幂等复用）。
        if result.payload_records:
            resolver.commit_payload_batch(tuple(result.payload_records))

        for m in result.materials:
            all_materials[m.material_id] = m
        aspect_materials.setdefault(entry.aspect_id, []).extend(
            m.material_id for m in result.materials)
        all_assemblies.extend(result.assemblies)
        if result.unread_scope.refs:
            unread_aspects.add(entry.aspect_id)
            unread_scopes.append({
                "evidence_id": entry.evidence_id, "reason": result.unread_scope.reason,
                "scope_desc": result.unread_scope.scope_desc,
                "stop_reason": result.stop_reason})

        resolved_entries.append({**base, "resolved": True, "resolution": "verified",
                                 "message": result.stop_reason,
                                 "material_count": len(result.materials)})
        seed_before_after.append({
            "case_id": entry.case_id, "aspect_id": entry.aspect_id,
            "seed_evidence_id": entry.evidence_id,
            "seed_text": (expansion.seed.text or "")[:400],
            "seed_section_path": list(expansion.seed.section_path),
            "expanded_material_ids": [m.material_id for m in result.materials],
            "assembly_ids": [a.assembly_id for a in result.assemblies],
            "component_evidence_ids": sorted({
                getattr(m.authority_assessment, "evidence_id", "")
                for m in result.materials}),
            "stop_reason": result.stop_reason,
            "unread_scope": result.unread_scope.scope_desc,
        })

    # -- set 枚举（三个 set_complete aspect） --
    set_results: dict[str, dict] = {}
    boundary_incomplete_aspects: set[str] = set()
    for aspect_id in SET_ASPECTS:
        mids = tuple(dict.fromkeys(aspect_materials.get(aspect_id, [])))
        if not mids:
            set_results[aspect_id] = TS.SetEnumerationResult(
                material_type_supported=False,
                verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                reason="本轮样本未覆盖该 aspect（无 source payload）").to_dict()
            continue
        source_materials = tuple(all_materials[mid] for mid in mids)
        resolved_payloads: list[TS.ResolvedPayload] = []
        for m in source_materials:
            try:
                rp = TS.verify_material_payload_ref(m.payload_ref, resolver)
            except TS.SchemaValidationError as e:
                resolved_payloads = []
                set_results[aspect_id] = TS.SetEnumerationResult(
                    material_type_supported=False,
                    verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                    reason=f"payload 不可解析: {e}").to_dict()
                break
            if rp.payload_bytes is None:
                resolved_payloads = []
                set_results[aspect_id] = TS.SetEnumerationResult(
                    material_type_supported=False,
                    verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                    reason="payload bytes 不可用").to_dict()
                break
            resolved_payloads.append(rp)
        else:
            first = source_materials[0]
            document_version = getattr(first.authority_assessment, "document_version", "")
            boundary = getattr(first.locator, "section_path", "") or ""
            if not document_version or not boundary:
                set_results[aspect_id] = TS.SetEnumerationResult(
                    material_type_supported=False,
                    verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                    reason="边界/版本缺失，无法枚举").to_dict()
                boundary_incomplete_aspects.add(aspect_id)
                continue
            assessment = _enumeration_assessment(
                aspect_id, mids, document_version, boundary, dep)
            enum = enumerator.enumerate(
                assessment, tuple(source_materials), tuple(resolved_payloads), dep)
            if enum is None:
                set_results[aspect_id] = TS.SetEnumerationResult(
                    material_type_supported=False,
                    verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                    reason="枚举器无法枚举").to_dict()
            else:
                set_results[aspect_id] = enum.to_dict()
                if enum.material_type_supported is not True:
                    boundary_incomplete_aspects.add(aspect_id)

    # -- 材料索引 / aspect 矩阵 / before_after / payload 预览 --
    aspect_of_material: dict[str, list[str]] = {}
    for aspect_id, mids in aspect_materials.items():
        for mid in mids:
            aspect_of_material.setdefault(mid, []).append(aspect_id)

    material_entries = [
        _material_entry(m, tuple(aspect_of_material.get(m.material_id, [])))
        for m in all_materials.values()
    ]
    material_entries.sort(key=lambda e: (e["material_type"], e["document_id"],
                                         e["document_version"] or "",
                                         e["page"] or 0, e["material_id"]))
    _write_json(out_dir / "material_index.json", material_entries)

    # aspect 矩阵六态。
    aspect_ids = sorted({e.aspect_id for e in manifest.entries})
    matrix_rows = []
    for aspect_id in aspect_ids:
        state = _aspect_state(
            aspect_id,
            material_ids=tuple(dict.fromkeys(aspect_materials.get(aspect_id, []))),
            materials_by_id=all_materials,
            seed_evidence_ids=tuple(aspect_seeds.get(aspect_id, [])),
            rejected_aspects=rejected_aspects, unread_aspects=unread_aspects,
            boundary_incomplete_aspects=boundary_incomplete_aspects)
        matrix_rows.append({"aspect_id": aspect_id, "state": state})

    # -- 写产物 --
    _write_json(out_dir / "seed_manifest.json", manifest.to_dict())
    _write_json(out_dir / "resolved_seed_manifest.json", {
        "manifest_version": SEED_MANIFEST_VERSION,
        "fingerprint": manifest.fingerprint,
        "entries": resolved_entries,
    })
    _write_jsonl(out_dir / "seed_discovery_trace.jsonl", [])
    _write_jsonl(out_dir / "expansion_trace.jsonl", expansion_steps)
    _write_json(out_dir / "unread_scope.json", unread_scopes)
    _write_json(out_dir / "set_enumeration.json", set_results)
    _write_json(out_dir / "aspect_material_matrix.json", matrix_rows)
    _write_json(out_dir / "assemblies.json", [
        _assembly_to_dict(a) for a in all_assemblies])

    _write_material_index_md(out_dir, material_entries, all_assemblies)
    _write_aspect_matrix_md(out_dir, matrix_rows)
    _write_before_after_md(out_dir, seed_before_after)

    preview_dir = out_dir / "payload_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for m in all_materials.values():
        try:
            rp = resolver.resolve(m.payload_ref)
            payload = (rp.payload_bytes.decode("utf-8") if rp and rp.payload_bytes
                       else "（payload 不可用）")
        except Store.StorageCorruptionError as e:
            payload = f"（payload 损坏）: {e}"
        _write_json(preview_dir / f"{m.material_id}.json", {
            "material_id": m.material_id,
            "component_evidence_id": getattr(m.authority_assessment, "evidence_id", ""),
            "source_content_hash": getattr(m.authority_assessment, "content_hash", ""),
            "payload_hash": m.content_hash,
            "payload": payload,
        })
    _write_json(preview_dir / "_assemblies.json", [
        _assembly_to_dict(a) for a in all_assemblies])

    return {
        "run_id": run_id, "out_dir": str(out_dir),
        "seed_count": len(manifest.entries),
        "resolved_count": sum(1 for e in resolved_entries if e.get("resolved")),
        "material_count": len(all_materials),
        "assembly_count": len(all_assemblies),
    }


def _assembly_to_dict(a) -> dict:
    d = {
        "assembly_id": a.assembly_id,
        "context_parent_id": a.context_parent_id,
        "component_material_ids": list(a.component_material_ids),
        "relation": a.relation,
        "boundary_desc": a.boundary_desc,
    }
    if getattr(a, "table_title", None) is not None:
        d.update({
            "table_title": a.table_title, "unit": a.unit,
            "header_evidence_id": a.header_evidence_id,
            "body_evidence_ids": list(a.body_evidence_ids),
            "continuation_evidence_ids": list(a.continuation_evidence_ids),
        })
    return d


# ---------------------------------------------------------------------------
# 人读产物
# ---------------------------------------------------------------------------

def _write_material_index_md(out_dir: Path, entries: list[dict],
                             assemblies: list) -> None:
    lines = ["# R2 材料索引（material_index）", ""]
    lines.append("> 每条材料必填字段见 §13；source_content_hash 为来源层身份，"
                 "payload_hash 为载体层身份，component evidence_id 可回查原始 Block。")
    lines.append("")
    if not entries:
        lines.append("（本轮无材料）")
        lines.append("")
    for e in entries:
        lines.append(f"## {e['material_id']}")
        lines.append("")
        lines.append(f"- 材料类型：`{e['material_type']}`")
        lines.append(f"- component evidence_id：`{e['component_evidence_id']}`")
        lines.append(f"- source_content_hash（来源层）：`{e['source_content_hash']}`")
        lines.append(f"- payload_hash（载体层）：`{e['payload_hash']}`")
        lines.append(f"- 来源文件/版本：`{e['document_id']}` / `{e['document_version']}`")
        lines.append(f"- 章节路径：`{e['section_path']}` · 页：{e['page']} · 块范围：{e['block_range']}")
        if e.get("table_title"):
            lines.append(f"- 表题：`{e['table_title']}`")
        lines.append(f"- authority 状态：`{e['authority_verdict']}`")
        lines.append(f"- 支撑 aspect：{', '.join(f'`{a}`' for a in e['aspect_ids'])}")
        lines.append("")
    lines.append("## 组合投影（MaterialAssembly/TableAssembly）")
    lines.append("")
    for a in assemblies:
        lines.append(f"- `{a.assembly_id}`（{a.relation}）→ "
                     f"{', '.join('`' + c + '`' for c in a.component_material_ids)}")
    lines.append("")
    (out_dir / "material_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_aspect_matrix_md(out_dir: Path, rows: list[dict]) -> None:
    lines = ["# aspect → 材料覆盖矩阵（六态）", ""]
    lines.append("| 状态 | 含义 |")
    lines.append("|---|---|")
    for key, label in MATRIX_STATES:
        lines.append(f"| `{key}` | {label} |")
    lines.append("")
    lines.append("| aspect_id | 状态 |")
    lines.append("|---|---|")
    for r in rows:
        lines.append(f"| `{r['aspect_id']}` | `{r['state']}` |")
    lines.append("")
    (out_dir / "aspect_material_matrix.md").write_text("\n".join(lines), encoding="utf-8")


def _write_before_after_md(out_dir: Path, rows: list[dict]) -> None:
    lines = ["# seed 命中内容 vs 扩读后完整材料（before/after）", ""]
    if not rows:
        lines.append("（本轮无 seed）")
        lines.append("")
    for r in rows:
        lines.append(f"## {r['case_id']} · {r['aspect_id']}")
        lines.append("")
        lines.append(f"- seed evidence_id：`{r['seed_evidence_id']}`")
        lines.append(f"- seed section_path：`{r['seed_section_path']}`")
        lines.append(f"- seed 命中内容：{r['seed_text'] or '（空）'}")
        lines.append(f"- 扩读后材料：{', '.join('`' + m + '`' for m in r['expanded_material_ids']) or '（无）'}")
        lines.append(f"- component evidence_id："
                     f"{', '.join('`' + e + '`' for e in r['component_evidence_ids']) or '（无）'}")
        lines.append(f"- 停止原因：`{r['stop_reason']}`")
        lines.append(f"- 未读范围：{r['unread_scope']}")
        lines.append("")
    (out_dir / "before_after.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness.material_slice_runner")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed-manifest", default=None)
    parser.add_argument("--discover-seeds", action="store_true")
    parser.add_argument("--company-id", default=None)
    parser.add_argument("--ev-db", default=str(DEFAULT_EVIDENCE_DB_PATH))
    parser.add_argument("--harness-db", default=str(DEFAULT_HARNESS_DB_PATH))
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.discover_seeds:
        if not args.company_id:
            print("discover-seeds 需 --company-id", file=sys.stderr)
            return 2
        try:
            from tools import adapters
            registry = adapters.build_default_registry(audit_dir=Path(args.out_root) / "tool_audit")
        except Exception as e:  # noqa: BLE001 - 检索链不可用 → fail-closed
            print(f"discover-seeds fail-closed：检索链不可用（{type(e).__name__}: {e}）；"
                  f"请显式提供已确认 seed manifest。", file=sys.stderr)
            return 1
        queries = (("company_business_main.main_business", "主营业务"),
                   ("company_competitiveness.core_competitiveness", "核心竞争力"),
                   ("company_subsidiaries.major_subsidiaries", "主要子公司"))
        summary = discover_seeds(args.company_id, queries, registry,
                                 Path(args.out_root), args.run_id)
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    if not args.seed_manifest:
        print("正式材料验收 runner 需 --seed-manifest（两阶段 seed 修正二）", file=sys.stderr)
        return 2
    try:
        manifest = load_seed_manifest(args.seed_manifest)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"seed manifest 加载失败：{e}", file=sys.stderr)
        return 2

    summary = run_material_slice(
        args.run_id, manifest, evidence_db=args.ev_db,
        harness_db=args.harness_db, out_root=args.out_root)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
