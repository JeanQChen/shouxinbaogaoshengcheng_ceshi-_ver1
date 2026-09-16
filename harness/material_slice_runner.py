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
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from contracts import loader_v2 as LV2
from contracts import schema_v2 as SV2
from contracts import source_policy as SP
from contracts import validator_v2 as VV2
from harness import heading_structure as HS
from harness import run_manifest as RM
from harness import source_object_inventory as SOI
from harness import topic_schema as TS
from harness import topic_store as Store
from harness.context_expansion import (
    BOUNDARY_DISPOSITION_VERSION,
    BUDGET_PROFILE_VERSION,
    DISPOSITION_FRAGMENT_PROJECTION,
    DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL,
    DISPOSITION_UNREAD_INSIDE_BOUNDARY,
    EXPANSION_DIRECTIONS,
    ContextExpansionRequest,
    ExpansionBudget,
    ExpansionSeed,
    budget_profile,
    expand,
)
from harness.evidence_reader import (
    DEFAULT_EVIDENCE_DB_PATH,
    RESOLVE_SEED_IDENTITY_TOOL_NAME,
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.set_enumeration import (
    FormalSetEnumerationVerifier,
    aggregate_per_version_enumeration,
    build_formal_set_enumeration_verifier,
    build_source_object_inventory,
    derive_enumeration_boundary_proof,
    group_materials_by_document_version,
)
from harness.topic_materials import build_material_result
from harness.topic_boundary import (
    BOUNDARY_POLICY_UNAVAILABLE,
    BOUNDARY_VERIFICATION_ALGORITHM,
    BOUNDARY_VERIFICATION_VERSION,
    TOPIC_BOUNDARY_VERSION,
    BoundarySemanticsVerification,
    topic_boundary_coverage,
    topic_boundary_policy,
)
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
    # 身份复验结果（resolve_seed_identity 完整身份）+ 检索来源（§二 candidate 身份闭环）。
    source_name: str = ""
    source_tool: str = ""
    rank: int | None = None
    score: float | None = None
    is_current_document: bool = True
    is_current_set: bool = True

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
            "source_name": self.source_name,
            "source_tool": self.source_tool,
            "rank": self.rank,
            "score": self.score,
            "is_current_document": self.is_current_document,
            "is_current_set": self.is_current_set,
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
            source_name=str(d.get("source_name", "")),
            source_tool=str(d.get("source_tool", "")),
            rank=d.get("rank"),
            score=d.get("score"),
            is_current_document=bool(d.get("is_current_document", True)),
            is_current_set=bool(d.get("is_current_set", True)),
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

def _aspect_suggested_use(aspect_id: str) -> str:
    if aspect_id == "company_business_main.main_business":
        return "主营业务集合枚举（分产品/分行业）"
    if aspect_id == "company_subsidiaries.major_subsidiaries":
        return "主要子公司集合枚举（表名/表行）"
    if aspect_id == "company_competitiveness.core_competitiveness":
        return "核心竞争力披露（条目式枚举）"
    return "set_complete 集合枚举材料"


def _text_summary(text: str, limit: int = 80) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[:limit - 1] + "…"


def _write_candidate_review_md(path: Path, candidates: tuple[SeedEntry, ...],
                               company_id: str) -> None:
    """把系统候选渲染为可读复核表（checkbox 留空，绝不自填人工确认）。"""
    lines = [
        f"# 候选 seed 人工复核表（company_id={company_id}）",
        "",
        "> 本表由只读发现链（search_evidence/search_tables → resolve_seed_identity）生成，",
        "> 仅列出**系统候选**，未做任何人工确认。请在 checkbox 处由用户/Codex 显式裁决后，",
        "> 再据此产生 confirmed_seed_manifest.json（本发现链绝不生成 confirmed 清单）。",
        "",
    ]
    if not candidates:
        lines.append("（无候选）")
    for i, e in enumerate(candidates, 1):
        section = " / ".join(e.section_path) if e.section_path else "（无）"
        doc_name = e.source_name or "（未知文档名）"
        score = f"{e.score}" if e.score is not None else "—"
        lines.append(f"## {i}. {e.aspect_id}")
        lines.append("")
        lines.append(f"- [ ] 确认（编号 `{e.case_id}`）")
        lines.append(f"  - aspect：`{e.aspect_id}`")
        lines.append(f"  - query：{e.query}")
        lines.append(f"  - 文档名/版本：{doc_name} / {e.document_version}（doc `{e.document_id}`，set `{e.evidence_set_version}`）")
        lines.append(f"  - 章节路径：{section}")
        lines.append(f"  - 页/块：page {e.page_number} / block {e.block_index}")
        lines.append(f"  - evidence 类型：{e.evidence_type}")
        lines.append(f"  - 检索来源/名次：{e.source_tool} / rank {e.rank}（score {score}）")
        lines.append(f"  - 文本摘要：{_text_summary(e.text)}")
        lines.append(f"  - 建议用途：{_aspect_suggested_use(e.aspect_id)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _resolve_candidate_identity(registry: ToolRegistry, company_id: str, evidence_id: str,
                                *, self_reported_page: int | None,
                                self_reported_evidence_type: str, run_id: str) -> dict:
    """经正式 ``resolve_seed_identity`` 工具复验一个检索命中的完整身份（§二，不调 LLM）。

    - 最小可信输入 company_id + evidence_id；检索命中自报 page_number 作为绑定字段（提供则
      精确断言，mismatch → resolver fail-closed）。
    - 成功后再比对检索命中自报 evidence_type 与复验结果；不一致 → fail-closed。
    - 空文本 / 非 current / 身份重算不符 → fail-closed（由 resolver 或本函数给出原因）。
    返回 {"ok": bool, "block": dict, "reason": str, "is_current_document": bool,
          "is_current_set": bool}。
    """
    spec = registry.get(RESOLVE_SEED_IDENTITY_TOOL_NAME)
    if spec is None:
        return {"ok": False, "reason": "resolve_seed_identity 工具未注册（无法复验身份）",
                "is_current_document": False, "is_current_set": False}
    args: dict = {"company_id": company_id, "evidence_id": evidence_id}
    if self_reported_page is not None:
        args["page_number"] = self_reported_page
    call = C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=RESOLVE_SEED_IDENTITY_TOOL_NAME,
        arguments=args, idempotency_key=f"{run_id}:resolve:{evidence_id}",
        need_id="", batch_id="")
    try:
        result = registry.execute(call, route="DIRECT_EVIDENCE", run_id=run_id)
    except Exception as e:  # noqa: BLE001 - resolver 异常 → fail-closed
        return {"ok": False, "reason": f"resolve_seed_identity 异常: {type(e).__name__}: {e}",
                "is_current_document": False, "is_current_set": False}
    if result.is_error():
        return {"ok": False,
                "reason": f"身份复验 fail-closed: {result.message or result.error_code}",
                "is_current_document": False, "is_current_set": False}
    if result.status != "SUCCESS":
        return {"ok": False,
                "reason": f"身份不可解析（{result.status}）: {result.message or result.error_code}",
                "is_current_document": False, "is_current_set": False}
    blocks = result.data.get("blocks", [])
    if not blocks:
        return {"ok": False, "reason": "身份复验未返回 block（空）",
                "is_current_document": False, "is_current_set": False}
    block = blocks[0]
    # 检索命中自报 evidence_type vs 复验结果 mismatch → fail-closed。
    if self_reported_evidence_type and \
            self_reported_evidence_type != str(block.get("evidence_type", "")):
        return {"ok": False,
                "reason": f"检索命中自报 evidence_type={self_reported_evidence_type!r} "
                          f"与复验 {block.get('evidence_type')!r} 不一致",
                "is_current_document": False, "is_current_set": False}
    # 空文本 → 拒绝（不得把空正文当候选材料）。
    if not str(block.get("text", "")).strip():
        return {"ok": False, "reason": "候选 block 文本为空",
                "is_current_document": False, "is_current_set": False}
    return {"ok": True, "block": block,
            "is_current_document": bool(result.data.get("is_current_document", False)),
            "is_current_set": bool(result.data.get("is_current_set", False))}


def discover_seeds(company_id: str,
                   aspect_queries: tuple[tuple[str, str], ...],
                   registry: ToolRegistry,
                   out_dir: Path,
                   run_id: str) -> dict:
    """经 ``search_evidence``/``search_tables`` 链派生候选 seed，并对每个命中经正式
    ``resolve_seed_identity`` 工具复验完整身份（§二 candidate 身份闭环，不调 LLM）。

    - 检索命中 → 逐条 ``resolve_seed_identity``（同 registry，只读 evidence DB）复验完整
      技术身份；身份缺失/非 current/哈希不符/空文本/自报身份与复验不一致 → fail-closed 进
      rejected_candidates.json（含原因）。
    - 确定性去重：``evidence_id + source_content_hash``（同一块被 search_evidence 与
      search_tables 同时命中只显示一次）。
    - 绝不把候选标记为「人工确认」：只输出系统候选 ``candidate_seed_manifest.json`` 与
      ``candidate_review.md``（checkbox 留空）；``confirmed_seed_manifest.json`` 仅由显式人工
      裁决产生，本函数绝不生成。
    - 输出四个产物：candidate_seed_manifest.json / candidate_review.md /
      rejected_candidates.json / seed_discovery_trace.jsonl。
    - registry 必须已注册 search_evidence/search_tables/resolve_seed_identity；缺工具或检索链
      不可用 → fail-closed（绝不初始化/迁移 DB、绝不改 ``_db_path``）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[SeedEntry] = []
    rejected: list[dict] = []
    trace_lines: list[dict] = []
    discovery_failed = False
    seen: set[tuple[str, str]] = set()  # (evidence_id, source_content_hash) 确定性去重

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
            if result.is_error():
                # 检索链不可用（FATAL_ERROR/RETRYABLE_ERROR，如 Evidence DB 未 init）→
                # fail-closed，绝不据此伪造 candidate；不得用 init_db()/迁移换取可运行。
                discovery_failed = True
                continue
            if result.status not in ("SUCCESS", "PARTIAL"):
                # EMPTY / UNSUPPORTED_FOR_DOCUMENT：非错误，也无候选。
                continue
            for item in (result.data or {}).get("items", []):
                evidence_id = str(item.get("evidence_id", ""))
                if not evidence_id:
                    rejected.append({
                        "aspect_id": aspect_id, "tool_name": tool_name,
                        "evidence_id": "", "rank": item.get("rank"),
                        "score": item.get("score"), "reason": "检索命中缺 evidence_id",
                    })
                    trace_lines.append({
                        "tool_name": RESOLVE_SEED_IDENTITY_TOOL_NAME,
                        "aspect_id": aspect_id, "status": "REJECTED",
                        "evidence_id": "", "reason": "检索命中缺 evidence_id",
                    })
                    continue
                resolved = _resolve_candidate_identity(
                    registry, company_id, evidence_id,
                    self_reported_page=item.get("page_number"),
                    self_reported_evidence_type=str(item.get("evidence_type", "") or ""),
                    run_id=run_id)
                if not resolved["ok"]:
                    rejected.append({
                        "aspect_id": aspect_id, "tool_name": tool_name,
                        "evidence_id": evidence_id, "rank": item.get("rank"),
                        "score": item.get("score"), "reason": resolved["reason"],
                    })
                    trace_lines.append({
                        "tool_name": RESOLVE_SEED_IDENTITY_TOOL_NAME,
                        "aspect_id": aspect_id, "status": "REJECTED",
                        "evidence_id": evidence_id, "reason": resolved["reason"],
                    })
                    continue
                block = resolved["block"]
                content_hash = str(block.get("content_hash", ""))
                key = (evidence_id, content_hash)
                if key in seen:
                    trace_lines.append({
                        "tool_name": RESOLVE_SEED_IDENTITY_TOOL_NAME,
                        "aspect_id": aspect_id, "status": "DUPLICATE",
                        "evidence_id": evidence_id,
                        "reason": "同一 evidence_id+content_hash 已被另一检索工具命中",
                    })
                    continue
                seen.add(key)
                candidates.append(SeedEntry(
                    case_id=f"candidate-{len(candidates) + 1}",
                    company_id=company_id, aspect_id=aspect_id, query=query,
                    evidence_id=evidence_id,
                    document_id=str(block.get("document_id", "")),
                    document_version=str(block.get("document_version", "")),
                    evidence_set_version=str(block.get("evidence_set_version", "")),
                    page_number=block.get("page_number"),
                    block_index=block.get("block_index"),
                    section_path=tuple(block.get("section_path") or ()),
                    evidence_type=str(block.get("evidence_type", "")),
                    source_content_hash=content_hash,
                    selection_reason="candidate（待人工确认）",
                    text=str(block.get("text", "")),
                    source_name=str(block.get("source_name", "")),
                    source_tool=tool_name,
                    rank=item.get("rank"),
                    score=item.get("score"),
                    is_current_document=bool(resolved.get("is_current_document", False)),
                    is_current_set=bool(resolved.get("is_current_set", False)),
                ))

    manifest = _manifest_from_entries(tuple(candidates))
    _write_json(out_dir / "candidate_seed_manifest.json", manifest.to_dict())
    _write_json(out_dir / "rejected_candidates.json",
                {"candidate_count": len(candidates), "rejected": rejected})
    _write_candidate_review_md(out_dir / "candidate_review.md", tuple(candidates), company_id)
    _write_jsonl(out_dir / "seed_discovery_trace.jsonl", trace_lines)
    return {"discovery_failed": discovery_failed, "candidate_count": len(candidates),
            "rejected_count": len(rejected), "out_dir": str(out_dir)}


# ---------------------------------------------------------------------------
# 阶段二：正式材料验收 runner
# ---------------------------------------------------------------------------

# 冻结 Contract v2（standard_v3.yaml，contract_version=v2）是 R2 材料验收的权威契约载体；
# 必须经 formal v2 loader + validator 加载/校验（不得退回 standard_v2.yaml / Contract v1）。
# 依赖指纹绑定真实冻结资产内容指纹（Contract v2 内容指纹 + Source Policy v1 内容指纹）与
# R2 实现版本，不硬编码占位字符串 / pseudo-SHA。
_R2_CONTRACT_PATH = Path(SV2.CONTRACT_V2_ASSET)
_SOURCE_POLICY_PATH = Path("templates/policies/source_policy_v1.yaml")


@lru_cache(maxsize=1)
def _frozen_contract() -> SV2.ContractV2:
    """经 formal v2 loader 加载 Contract v2 并用 formal v2 validator 校验（fail-closed）。

    ``loader_v2.parse_contract_v2`` 要求 ``contract_version == "v2"``，故 standard_v2.yaml /
    Contract v1 在此被拦截（ValueError），不可能静默绑定错误契约载体。
    """
    contract = LV2.load_contract_v2(str(_R2_CONTRACT_PATH))
    result = VV2.validate_contract_v2(contract)
    if not result.valid:
        raise RuntimeError(
            f"冻结 Contract v2 校验失败（{len(result.errors)} 处）: {result.errors[:5]}")
    return contract


@lru_cache(maxsize=1)
def _frozen_source_policy() -> SP.SourcePolicy:
    """经 formal source_policy loader + validator 加载 source_policy_v1.yaml（fail-closed）。"""
    policy = SP.load_source_policy(str(_SOURCE_POLICY_PATH))
    result = SP.validate_source_policy(policy)
    if not result.valid:
        raise RuntimeError(
            f"冻结 Source Policy v1 校验失败（{len(result.errors)} 处）: {result.errors[:5]}")
    return policy


def _contract_content_fingerprint() -> str:
    """冻结 Contract v2（standard_v3.yaml）的真实内容指纹（排除冻结元数据键）。"""
    return SV2.content_fingerprint(_frozen_contract().raw)


def _source_policy_content_fingerprint() -> str:
    """冻结 Source Policy v1（source_policy_v1.yaml）的真实内容指纹（排除冻结元数据键）。"""
    return SV2.content_fingerprint(_frozen_source_policy().raw)


def _contract_version() -> str:
    return _frozen_contract().contract_version


def _runner_dependency_fingerprint() -> str:
    """真实 R2 依赖指纹：全部 6 个 DEPENDENCY_VERSION_KEYS 取真实实现版本常量 +
    冻结 Contract v2 内容指纹 + 冻结 Source Policy v1 内容指纹 + source_policy 政策版本
    （不硬编码 _sha("r2-acceptance-contract") / pseudo-SHA）。"""
    dependency_versions = {
        "contract": _contract_version(),
        "source_policy": SP.POLICY_VERSION,
        "topic_schema": TS.TOPIC_PACK_SCHEMA_VERSION,
        "assessor": TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        "validator": TS.SET_COMPLETENESS_VERIFIER_VERSION,
        "set_enumerator": TS.SET_ENUMERATION_VERIFIER_VERSION,
    }
    base = TS.compute_dependency_fingerprint(
        _contract_content_fingerprint(), SP.POLICY_VERSION, dependency_versions)
    return TS.sha256_canonical({
        "base_dependency_fingerprint": base,
        "source_policy_content_fingerprint": _source_policy_content_fingerprint(),
    })


def _step_to_dict(step) -> dict:
    tool = step.tool_call
    return {
        "step_index": step.step_index,
        # §三.3：本步所属的 seed frontier（真实 seed evidence_id）；「续表材料是否由同一
        # seed/frontier 的扩读得到」必须可判定，绝不把第二 seed 的步骤算作第一 seed 的成果。
        "seed_evidence_id": getattr(step, "seed_evidence_id", "") or "",
        # §三.3/§二 P1-A：本步**真实锚点块**身份（读取从哪一块发起）。没有它，「同一
        # seed/frontier 的续表扩读链」只能靠**缺省字段推断** —— 而 seed 自身的相邻读取
        # 恰好不带显式锚点参数，其带回来的表头块因此被判为「不在任何 frontier」，
        # 真实的续表扩读正向样本被误判为未获证。
        "anchor_evidence_id": getattr(step, "anchor_evidence_id", "") or "",
        # §二（R2 定点修复）：结构性表引用的**确定性绑定记录**逐字落盘（anchor/marker
        # occurrence 偏移/目标对象身份与位置/同块或后续块/文档版本集合身份/理由）。
        # 验收侧据此**独立复算**，绝不只凭「输出属于已采纳材料」判 resolved —— 后者正是
        # 错误绑定的错误正例（输出的确是真实已采纳材料，但与引用对象不匹配）。
        "reference_binding": getattr(step, "reference_binding", None) or None,
        "action": step.action,
        "tool_name": tool.tool_name if tool is not None else None,
        "arguments": tool.arguments if tool is not None else {},
        "outputs": list(step.outputs),
        "stop_reason": step.stop_reason,
        "budget_remaining": step.budget_remaining,
    }


def _material_boundary_disposition(m: TS.ResearchMaterial,
                                   boundary_by_aspect_evidence: dict | None,
                                   aspect_ids: tuple[str, ...] = ()) -> str:
    """修复 A.3：fragment（locator.offset 非 None）的 link disposition 是
    ``fragment_projection``（独立可关联材料）；raw sentinel 的 ``outside_boundary_sentinel``
    只属于原始完整 block，绝不落到 fragment 上。

    P1-A.4/A.5：查找键为 ``(aspect_id, evidence_id)``，且只在该 material **自身**的
    aspect 集合内取值 —— 绝不跨 aspect 用 evidence_id 单键合并（同一 evidence 对不同
    aspect 的边界结论必须互不污染）。
    """
    loc = m.locator
    if isinstance(loc, TS.EvidenceLocator) and loc.offset is not None:
        return DISPOSITION_FRAGMENT_PROJECTION
    evidence_id = getattr(m.authority_assessment, "evidence_id", "")
    best = ""
    best_rank = -1
    for aspect_id in aspect_ids or ():
        bd = (boundary_by_aspect_evidence or {}).get((aspect_id, evidence_id))
        if not isinstance(bd, dict):
            continue
        disp = bd.get("disposition", "")
        rank = _BOUNDARY_DISPOSITION_RANK.get(disp, -1)
        if rank > best_rank:
            best, best_rank = disp, rank
    return best


def _decision_identity_key(d: dict) -> tuple:
    """边界处置**生产身份**（§四.A.4）：至少含 aspect_id / evidence_id / direction /
    relation / reason_code / disposition / section_path / content_hash。

    绝不用 evidence_id 单键：同一 evidence 被不同 aspect、以不同方向、得到不同边界结论
    时身份必须互不相同，否则一条决策会覆盖另一条。
    """
    return (d.get("aspect_id", ""), d.get("evidence_id", ""), d.get("direction") or "",
            d.get("relation", ""), d.get("reason_code", ""), d.get("disposition", ""),
            tuple(d.get("section_path") or ()), d.get("content_hash", ""))


def _material_entry(m: TS.ResearchMaterial, aspect_ids: tuple[str, ...],
                    boundary_by_aspect_evidence: dict | None = None,
                    role_by_aspect_material: dict | None = None) -> dict:
    auth = m.authority_assessment
    loc = m.locator
    evidence_id = getattr(auth, "evidence_id", "")
    bd = {}
    for _aspect_id in aspect_ids or ():
        cand = (boundary_by_aspect_evidence or {}).get((_aspect_id, evidence_id))
        if isinstance(cand, dict):
            bd = _effective_disposition(bd or None, cand)
    bd = bd or {}
    return {
        "material_id": m.material_id,
        "material_type": m.material_type,
        "source_identity": m.source_identity,
        "component_evidence_id": evidence_id,
        "source_content_hash": getattr(auth, "content_hash", ""),
        "payload_hash": m.content_hash,
        "document_id": getattr(auth, "document_id", ""),
        "document_version": getattr(auth, "document_version", ""),
        "section_path": loc.section_path if loc is not None else "",
        "page": loc.page if loc is not None else None,
        "block_range": list(loc.block_range) if loc is not None and loc.block_range else None,
        "table_title": loc.table_title if loc is not None else None,
        "authority_verdict": getattr(auth, "verdict", ""),
        # 权威 / 边界成员 / aspect 资格 / supporting 四者分离（§三/§四）：
        # 边界处置与 aspect 角色分开呈现，sentinel/拒绝块绝不进入 material library。
        "boundary_disposition": _material_boundary_disposition(
            m, boundary_by_aspect_evidence, aspect_ids),
        "boundary_reason_code": bd.get("reason_code", ""),
        # §四修复四：role 键为 (aspect_id, material_id)，同一 material 对 A 可为 source、对 B 可为
        # context_candidate；按 aspect 输出角色映射，不再用单一 material_id → 单一 role 的扁平键。
        "aspect_roles": {
            aspect_id: role
            for (aspect_id, mid), role in (role_by_aspect_material or {}).items()
            if mid == m.material_id
        },
        # 向后兼容：单一「主导角色」字符串（跨 aspect 取最高 _ROLE_RANK，确定性），仅作展示
        # 摘要；权威的逐 aspect 角色见 aspect_roles / aspect_links.json。
        "aspect_role": max(
            ((role_by_aspect_material or {}).get((aspect_id, m.material_id), "")
             for aspect_id in aspect_ids
             if (aspect_id, m.material_id) in (role_by_aspect_material or {})),
            key=_ROLE_RANK.get, default=""),
        "aspect_ids": list(aspect_ids),
    }


def _enumeration_assessment(aspect_id: str, source_material_ids: tuple[str, ...],
                            document_version: str, source_boundary: str,
                            dep: str, member_ids: tuple[str, ...],
                            boundary_proof: TS.EnumerationBoundaryProof | None = None,
                            ) -> TS.SetCompletenessAssessment:
    """构建 set_complete 的完整证明 assessment（不再使用 ``__preview__`` 占位）。

    - expected == observed == 传入 member_ids，excluded 为空：R2 在明确披露边界内枚举，
      权威完整集合 == 枚举器产出的成员集合（无排除）。
    - contract_sha256 取 v1 基线 contract 的真实 content_fingerprint（非占位 sha）。
    - boundary_proof（§六）：由真实扩读 ExpansionResult/Trace/UnreadScope 派生的枚举边界
      闭合输入；set_complete 必须携带（Store/verifier 对缺失 fail-closed）。
    """
    return TS.SetCompletenessAssessment(
        aspect_id=aspect_id, rule_version=TS.SET_COMPLETENESS_RULE_VERSION,
        source_material_ids=source_material_ids, document_version=document_version,
        source_boundary=source_boundary, expected_member_ids=member_ids,
        observed_member_ids=member_ids, excluded_member_ids=(), exclusion_reasons=(),
        supporting_material_ids=(), supporting_fact_ids=(), scope_complete=True,
        assessor_version=TS.SET_COMPLETENESS_ASSESSOR_VERSION,
        contract_sha256=_contract_content_fingerprint(), dependency_fingerprint=dep,
        boundary_proof=boundary_proof)


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


def _enumerate_single_version(aspect_id: str, materials: list,
                              doc_id: str, doc_ver: str, resolver, enumerator,
                              exps: tuple, dep: str,
                              assemblies: tuple = (),
                              known_material_ids=None) -> tuple[dict, bool]:
    """P1-4：对单一 (document_id, document_version) 的 source materials 枚举一次。

    返回 ({"result": SetEnumerationResult.to_dict(), "source_boundary": str},
          boundary_incomplete: bool)。该版本的 boundary_proof 只从同 document_version 的
    扩读派生（不继承其他版本的 unread/工具错误/预算耗尽），绝不跨版本合并。
    """
    source_materials = tuple(materials)
    mids = tuple(m.material_id for m in source_materials)
    resolved_payloads: list[TS.ResolvedPayload] = []
    for m in source_materials:
        try:
            rp = TS.verify_material_payload_ref(m.payload_ref, resolver)
        except TS.SchemaValidationError as e:
            return ({"result": TS.SetEnumerationResult(
                material_type_supported=False,
                verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                reason=f"payload 不可解析: {e}").to_dict(),
                "source_boundary": ""}, True)
        if rp.payload_bytes is None:
            return ({"result": TS.SetEnumerationResult(
                material_type_supported=False,
                verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
                reason="payload bytes 不可用").to_dict(),
                "source_boundary": ""}, True)
        resolved_payloads.append(rp)

    boundary = getattr(source_materials[0].locator, "section_path", "") or ""
    if not doc_ver or not boundary:
        return ({"result": TS.SetEnumerationResult(
            material_type_supported=False,
            verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
            reason="边界/版本缺失，无法枚举").to_dict(),
            "source_boundary": boundary}, True)

    # 只取同 document_version 的扩读（每版本独立闭合判定，不跨版本继承未读/错误）。
    version_exps = tuple(
        e for e in exps if e.trace.request.document_version == doc_ver)
    boundary_proof = derive_enumeration_boundary_proof(
        aspect_id, version_exps, document_id=doc_id, document_version=doc_ver,
        evidence_set_version=(version_exps[0].trace.request.evidence_set_version
                              if version_exps else ""),
        source_boundary_identity=boundary, component_material_ids=mids,
        dependency_fingerprint=dep)
    request = _enumeration_assessment(
        aspect_id, mids, doc_ver, boundary, dep, mids, boundary_proof=boundary_proof)
    # §四.B.6/B.7：assembly component 外键的存在性宇宙 = **本次运行全部已持久化材料**
    # （formal + context_candidate），与独立落盘的 source_object_inventory 使用同一集合，
    # 避免同一份恢复真相在两处得到不同结论。
    enum = enumerator.enumerate(
        request, source_materials, tuple(resolved_payloads), dep, assemblies,
        known_material_ids=known_material_ids)
    if enum is None:
        return ({"result": TS.SetEnumerationResult(
            material_type_supported=False,
            verifier_version=TS.SET_ENUMERATION_VERIFIER_VERSION,
            reason="枚举器无法枚举").to_dict(),
            "source_boundary": boundary}, True)
    result = enum.to_dict()
    incomplete = enum.material_type_supported is not True
    if not incomplete:
        final_assessment = _enumeration_assessment(
            aspect_id, mids, doc_ver, boundary, dep, enum.enumerated_member_ids,
            boundary_proof=boundary_proof)
        verdict = TS.compute_set_completeness_verdict(final_assessment, dep)
        if not verdict.set_complete:
            incomplete = True
    return ({"result": result, "source_boundary": boundary}, incomplete)


def _enumerate_set_by_version(aspect_id: str, mids: tuple[str, ...],
                              all_materials: dict, resolver, enumerator,
                              aspect_expansions: dict, dep: str,
                              assemblies: tuple = (),
                              known_material_ids=None) -> tuple[dict, bool]:
    """P1-4：按单一 document_version 逐版本枚举 set_complete aspect，绝不跨版本合并。

    返回 (aggregate_dict, boundary_incomplete)。aggregate_dict 结构：
    ``{"material_type_supported", "verifier_version", "reason", "merged", "per_version"}``，
    ``per_version`` 每项 ``{document_id, document_version, source_boundary, result}``。
    多版本 → material_type_supported=False + merged=False（历史视图共存，不伪造单一完整集合）。
    """
    verifier_version = TS.SET_ENUMERATION_VERIFIER_VERSION
    if not mids:
        return ({
            "material_type_supported": False,
            "verifier_version": verifier_version,
            "reason": "本轮样本未覆盖该 aspect（无 source payload）",
            "merged": False,
            "per_version": [],
        }, False)

    source_materials = [all_materials[mid] for mid in mids]
    groups = group_materials_by_document_version(source_materials)
    exps = tuple(aspect_expansions.get(aspect_id, []))

    per_version: list[dict] = []
    any_incomplete = False
    for (doc_id, doc_ver), version_materials in groups:
        entry, incomplete = _enumerate_single_version(
            aspect_id, version_materials, doc_id, doc_ver, resolver, enumerator,
            exps, dep, assemblies, known_material_ids=known_material_ids)
        per_version.append({
            "document_id": doc_id,
            "document_version": doc_ver,
            "source_boundary": entry["source_boundary"],
            "result": entry["result"],
        })
        if incomplete:
            any_incomplete = True

    aggregate = aggregate_per_version_enumeration(per_version, verifier_version)
    multi_version = len(groups) > 1
    return aggregate, (any_incomplete or multi_version)


def _write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


# 边界处置有效值排序（§四：确定性合并，与 seed 处理顺序无关）。
# source(seed) > inside_boundary > context_candidate > unread > sentinel > rejected > duplicate。
_BOUNDARY_DISPOSITION_RANK = {
    "seed": 6,
    "inside_boundary": 5,
    "context_candidate": 4,
    "unread_inside_boundary": 3,
    "outside_boundary_sentinel": 2,
    "rejected_boundary_mismatch": 1,
    # 修复 A.2：已撤回（上一章节）块不再构成有效边界成员，rank 最低，绝不覆盖正当采纳。
    "rolled_back_previous_section": 0,
    "duplicate": 0,
}

# aspect 角色有效值排序（§四：source > supporting(R3 only) > context_candidate）。
_ROLE_RANK = {"source": 3, "supporting": 2, "context_candidate": 1}

# run manifest 绑定的产物集合（§五.4：run/Pack/依赖身份必须可验证绑定）。
# 目录内其余文件（md 报告、payload_preview/、tool_audit/）不参与 manifest 指纹。
RUN_ARTIFACTS = (
    "seed_manifest.json",
    "resolved_seed_manifest.json",
    "seed_discovery_trace.jsonl",
    "expansion_trace.jsonl",
    "unread_scope.json",
    "boundary_decisions.json",
    "boundary_verification.json",
    "rolling_read_outcomes.json",
    "aspect_membership.json",
    "set_enumeration.json",
    "source_object_inventory.json",
    "topic_boundary_coverage.json",
    "aspect_material_matrix.json",
    "aspect_links.json",
    "budget_profile.json",
    "assemblies.json",
    "assemblies_superseded.json",
    "material_index.json",
)


def _effective_disposition(existing: dict | None, bd) -> dict:
    """确定性 effective_disposition 合并（rank-max，first-wins 反例修正）。

    同一 **aspect 内** 的同一 evidence 从多个 seed/方向得到不同处置时取「更靠内」的
    处置；绝不因 seed 顺序而漂移。调用方必须已按 (aspect_id, evidence_id) 分组 ——
    绝不跨 aspect 合并（§四.A.5）。``bd`` 可为本模块决策对象或已序列化 dict。
    """
    if isinstance(bd, dict):
        cand = {"disposition": bd.get("disposition", ""),
                "reason_code": bd.get("reason_code", "")}
    else:
        cand = {"disposition": bd.disposition, "reason_code": bd.reason_code}
    if existing is None:
        return cand
    if _BOUNDARY_DISPOSITION_RANK.get(cand["disposition"], -1) > \
            _BOUNDARY_DISPOSITION_RANK.get(existing.get("disposition", ""), -1):
        return cand
    return existing


def _append_unique(mapping: dict, key: str, value: str) -> None:
    lst = mapping.setdefault(key, [])
    if value not in lst:
        lst.append(value)


def run_material_slice(run_id: str, manifest: SeedManifest, *,
                       evidence_db: str | Path = DEFAULT_EVIDENCE_DB_PATH,
                       harness_db: str | Path = DEFAULT_HARNESS_DB_PATH,
                       out_root: str | Path = DEFAULT_OUT_ROOT,
                       directions: tuple[str, ...] = EXPANSION_DIRECTIONS,
                       budget_profile_name: str = "production") -> dict:
    """正式材料验收 runner（阶段二，--seed-manifest 必填）。

    每个 seed 经 ``expand``（bounded ToolRegistry 链）复验身份 → 受控扩读 → atomic material
    构建 → payload 单事务原子落盘 → （set_complete aspect）正式枚举。seed 不一致 → fail-closed。
    """
    out_dir = Path(out_root) / f"r2_material_slice_{run_id}"
    # 拒绝覆盖既有 run：产物目录必须全新（防止同一 run_id 残留旧产物被误读为新结果）。
    if out_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖既有 run：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)

    dep = _runner_dependency_fingerprint()
    registry = ToolRegistry(audit_dir=out_dir / "tool_audit")
    register_bounded_evidence_tool(registry, db_path=evidence_db)
    register_resolve_seed_identity_tool(registry, db_path=evidence_db)

    Store.init_topic_store(Path(harness_db))
    resolver = Store.TopicMaterialPayloadResolver(Path(harness_db))
    enumerator = build_formal_set_enumeration_verifier()

    # -- 逐 seed 处理 --
    resolved_entries: list[dict] = []
    all_materials: dict[str, TS.ResearchMaterial] = {}
    all_assemblies: list = []
    aspect_materials: dict[str, list[str]] = {}        # aspect_id → formal(source) material_ids
    context_candidate_materials: dict[str, list[str]] = {}  # aspect_id → context_candidate material_ids
    outside_boundary_evidence: dict[str, list[str]] = {}    # aspect_id → sentinel evidence_ids
    unread_inside_boundary_evidence: dict[str, list[str]] = {}  # aspect_id → unread evidence_ids
    aspect_seeds: dict[str, list[str]] = {}
    aspect_expansions: dict[str, list] = {}
    rejected_aspects: set[str] = set()
    unread_aspects: set[str] = set()
    topic_boundary_unavailable_aspects: set[str] = set()
    seed_before_after: list[dict] = []
    seed_budget_records: list[dict] = []
    expansion_steps: list[dict] = []
    unread_scopes: list[dict] = []
    boundary_decisions: list[dict] = []
    # P1-A.4/A.5：处置查找键 (aspect_id, evidence_id)（绝不跨 aspect 合并）；
    # 完整生产身份 → 出现记录，供独立验收器交叉校验。
    boundary_by_aspect_evidence: dict[tuple[str, str], dict] = {}
    boundary_by_identity: dict[tuple, list[dict]] = {}
    role_by_aspect_material: dict[tuple[str, str], str] = {}
    # §三 P1-4：引用表对象的方面归属声明（键 = (aspect_id, 目标 material_id) → {object_id: 声明}）。
    ref_objects_by_aspect_material: dict[tuple[str, str], dict[str, dict]] = {}
    # 无处落地的引用表对象声明（缺归属行 / 目标 evidence 未采纳）→ 显式披露，绝不静默。
    ref_link_orphans: list[dict] = []
    # P1-A.2：逐 aspect 的边界资格状态（由扩读路径**实际消费**得到，非事后补写）。
    boundary_status_by_aspect: dict[str, dict] = {}
    # P1-A.1/A.2/A.3：逐 aspect 运行时派生的 BoundaryVerificationRecord（真实边界验证）。
    boundary_verification_by_aspect: dict[str, dict] = {}
    target_outcomes_all: list[dict] = []
    direction_unread_all: list[dict] = []

    # P1-A.3：主题小节标题层级由**同一 aspect / 同一 document_version 的 seed 文本集**给出
    # （文档自身编号形式，见 ``heading_structure.topic_level_from_seed_set``）。逐 seed 取
    # 自身 section_path 叶子在**本批 seed 文本**中的实际标题层级，manifest 顺序确定性取首个命中。
    # 全批都无法确定 → None：绝不猜层级（猜浅 → 兄弟小节正文混入主题材料；猜深 → 主题内子标题
    # 被误判为兄弟标题），该 aspect 的边界诚实保持未闭合。
    seed_texts_by_scope: dict[tuple[str, str, str], list[str]] = {}
    seed_sections_by_scope: dict[tuple[str, str, str], list[tuple[str, ...]]] = {}
    for entry in manifest.entries:
        _key = (entry.aspect_id, entry.document_id or "", entry.document_version or "")
        seed_texts_by_scope.setdefault(_key, []).append(entry.text or "")
        seed_sections_by_scope.setdefault(_key, []).append(tuple(entry.section_path or ()))
    topic_level_by_scope: dict[tuple[str, str, str], int | None] = {}
    for _key, _texts in seed_texts_by_scope.items():
        _level: int | None = None
        for _section in seed_sections_by_scope.get(_key, ()):
            _level, _ = HS.topic_level_from_seed_set(_texts, _section)
            if _level is not None:
                break
        topic_level_by_scope[_key] = _level

    for entry in manifest.entries:
        aspect_seeds.setdefault(entry.aspect_id, []).append(entry.evidence_id)
        # 修复 A：主题边界策略不可用（未知/无法从冻结契约派生的 aspect）→ fail-closed，
        # 该 aspect 验收保持 boundary_incomplete，绝不默认 ambiguous 无限采纳、绝不静默闭合。
        if not topic_boundary_policy(entry.aspect_id).available:
            topic_boundary_unavailable_aspects.add(entry.aspect_id)
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
            directions=directions, budget=budget_profile(budget_profile_name),
            dependency_fingerprint=dep, aspect_id=entry.aspect_id,
            topic_level_hint=topic_level_by_scope.get(
                (entry.aspect_id, entry.document_id or "",
                 entry.document_version or "")))

        expansion = expand(request, registry, run_id=run_id)
        seed_budget_records.append({
            "case_id": entry.case_id,
            "aspect_id": entry.aspect_id,
            "evidence_id": entry.evidence_id,
            "budget_consumed": dict(expansion.budget_consumed),
            "stop_reason": expansion.stop_reason,
            "unread_scope": expansion.unread_scope.scope_desc,
        })
        for step in expansion.trace.steps:
            expansion_steps.append(_step_to_dict(step))
        # P1-A.2：边界资格状态逐 aspect 落盘（独立验证结论 + 主题小节层级与其来源）。
        boundary_status_by_aspect[entry.aspect_id] = {
            "aspect_id": entry.aspect_id,
            "status": expansion.boundary_status or "not_applicable",
            "eligible": bool(expansion.boundary_eligible),
            "reason": expansion.boundary_reason,
            "topic_level": expansion.topic_level,
            "topic_level_source": expansion.topic_level_source,
            "verification": expansion.boundary_verification,
        }
        # P1-A.1/A.2/A.3：运行时派生的边界验证记录（含身份三元组 + 结构观察 + 结论）。
        # 同一 aspect 多个 seed → 取**最弱**状态（诚实优先：incomplete/unavailable 胜过 verified），
        # 身份不合并（每个 seed 一条独立记录）。
        _rec = dict(getattr(expansion, "boundary_verification_record", {}) or {})
        if _rec:
            _rec["seed_evidence_id"] = entry.evidence_id
            _rec["case_id"] = entry.case_id
            boundary_verification_by_aspect.setdefault(entry.aspect_id, {})
            if not boundary_verification_by_aspect[entry.aspect_id]:
                boundary_verification_by_aspect[entry.aspect_id] = {
                    "aspect_id": entry.aspect_id, "records": []}
            boundary_verification_by_aspect[entry.aspect_id]["records"].append(_rec)
        for _t in getattr(expansion, "target_outcomes", ()) or ():
            target_outcomes_all.append({**dict(_t), "aspect_id": entry.aspect_id,
                                        "seed_evidence_id": entry.evidence_id,
                                        "case_id": entry.case_id})
        for _u in getattr(expansion, "direction_unread", ()) or ():
            direction_unread_all.append({**dict(_u), "aspect_id": entry.aspect_id,
                                         "seed_evidence_id": entry.evidence_id,
                                         "case_id": entry.case_id})
        for bd in expansion.boundary_decisions:
            d = bd.to_dict()
            # §四：路径化键（aspect_id, seed_evidence_id, evidence_id），保留全部 reachability 决策。
            d["aspect_id"] = entry.aspect_id
            d["seed_evidence_id"] = entry.evidence_id
            boundary_decisions.append(d)
            # P1-A.4/A.5：处置合并键为 **(aspect_id, evidence_id)**，绝不跨 aspect 用
            # evidence_id 单键 rank-max 合并。完整生产身份（含 direction / locator 片段
            # 身份）另存 boundary_by_identity，供独立验收器交叉校验。
            _aek = (entry.aspect_id, bd.evidence_id)
            boundary_by_aspect_evidence[_aek] = _effective_disposition(
                boundary_by_aspect_evidence.get(_aek), bd)
            boundary_by_identity.setdefault(_decision_identity_key(d), []).append(
                {"seed_evidence_id": entry.evidence_id,
                 "direction": bd.direction, "relation": bd.relation})
            if bd.disposition == DISPOSITION_OUTSIDE_BOUNDARY_SENTINEL:
                _append_unique(outside_boundary_evidence, entry.aspect_id, bd.evidence_id)
            elif bd.disposition == DISPOSITION_UNREAD_INSIDE_BOUNDARY:
                _append_unique(unread_inside_boundary_evidence, entry.aspect_id, bd.evidence_id)

        if not expansion.adopted:
            reason = expansion.unread_scope.scope_desc or expansion.stop_reason
            resolved_entries.append({**base, "resolved": False,
                                     "resolution": "seed_mismatch", "message": reason})
            rejected_aspects.add(entry.aspect_id)
            unread_scopes.append({
                "evidence_id": entry.evidence_id, "reason": expansion.unread_scope.reason,
                "scope_desc": expansion.unread_scope.scope_desc,
                "stop_reason": expansion.unread_scope.stop_reason or expansion.stop_reason})
            continue

        result = build_material_result(
            expansion, dependency_fingerprint=dep, aspect_id=entry.aspect_id)
        # §四：role 键 (aspect_id, material_id)，source > supporting(R3) > context_candidate；
        # 确定性合并（source 优先，绝不 last-wins 漂移）。同一 material 可对 aspect A 为
        # source、对 aspect B 为 context_candidate。
        for link in result.aspect_links:
            for mid in link.material_ids:
                key = (link.aspect_id, mid)
                prev = role_by_aspect_material.get(key)
                if prev is None or _ROLE_RANK.get(link.role, 0) > _ROLE_RANK.get(prev, 0):
                    role_by_aspect_material[key] = link.role

        # §三 P1-4：引用表对象的**正式 aspect 归属声明**挂到其目标 material 的归属行上
        # （目标 material 一定是该 aspect 的真实材料）。没有归属行 ⇒ 声明无处落地 ⇒
        # 验收侧按「目标对象缺正式 aspect 绑定」fail-closed，绝不静默丢弃。
        _expansion_evidence_ids = {str(m.authority_assessment.evidence_id)
                                   for m in result.materials}
        for decl in result.reference_table_objects:
            owner = str(decl.get("target_material_id") or "")
            owner_evidence = str(decl.get("target_evidence_id") or "")
            key = (str(decl.get("aspect_id") or ""), owner)
            if key not in role_by_aspect_material:
                ref_link_orphans.append({**dict(decl), "reason": "no_aspect_material_link"})
                continue
            if owner_evidence not in _expansion_evidence_ids:
                ref_link_orphans.append(
                    {**dict(decl), "reason": "target_evidence_not_adopted"})
                continue
            ref_objects_by_aspect_material.setdefault(
                key, {}).setdefault(str(decl.get("target_object_id") or ""), dict(decl))

        # payload 单事务原子落盘（幂等复用）。
        if result.payload_records:
            resolver.commit_payload_batch(tuple(result.payload_records))

        for m in result.materials:
            all_materials[m.material_id] = m
        # §四：formal（source）与 context_candidate 分离。context 进全局库/审计，但
        # 不计为 aspect 正式材料（不推进 coverage、不进矩阵 obtained）；绝不无条件全量
        # 挂到 aspect（反例：financial risk/credit 片段不再被挂在 major_subsidiaries 下）。
        formal_ids: list[str] = []
        context_ids: list[str] = []
        for link in result.aspect_links:
            for mid in link.material_ids:
                if link.role == "source":
                    formal_ids.append(mid)
                else:
                    context_ids.append(mid)
        for mid in formal_ids:
            _append_unique(aspect_materials, entry.aspect_id, mid)
        for mid in context_ids:
            _append_unique(context_candidate_materials, entry.aspect_id, mid)
        aspect_expansions.setdefault(entry.aspect_id, []).append(expansion)
        all_assemblies.extend(result.assemblies)
        if result.unread_scope.refs:
            unread_aspects.add(entry.aspect_id)
            unread_scopes.append({
                "evidence_id": entry.evidence_id, "reason": result.unread_scope.reason,
                "scope_desc": result.unread_scope.scope_desc,
                "stop_reason": result.unread_scope.stop_reason or result.stop_reason})

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

    # P1-2：按 content-addressed assembly_id 确定性去重（同 ID 同内容 → 一条；同 ID 不同
    # 内容 → fail-closed 抛错）。多个 seed 可能派生同一组合投影（同 relation + 同 component
    # material），产物绝不含重复 assembly，且绝不因 seed 顺序漂移。
    all_assemblies = _dedup_assemblies(all_assemblies)
    # §四修复：**同一源对象的跨 pass 投影收敛为唯一恢复真相**（明细见函数文档）。
    all_assemblies, superseded_assemblies = _converge_source_object_projections(
        all_assemblies, all_materials)
    # P1-B.6：清单、枚举门与持久化产物共用**同一份** assembly 记录（单一恢复真相）。
    all_assemblies_dicts = tuple(_assembly_to_dict(a) for a in all_assemblies)

    # §四（补充）：formal/context 以 source-wins 为唯一依据。同一 material 既是某 seed 的
    # source、又是另一 seed 的 context_candidate 时，只归 formal（source 优先，绝不重复归入
    # context_candidate_material_ids）。反例：多 seed 交叉时 source 材料被第二 seed 当作 context
    # 重复挂入 context 列表，导致 formal∩context 非空、context 计数虚高。
    for _asp in list(context_candidate_materials.keys()):
        _formal = set(aspect_materials.get(_asp, []))
        context_candidate_materials[_asp] = [
            _m for _m in context_candidate_materials[_asp] if _m not in _formal]

    # §四（补充）：material id 列表稳定排序，保证产物不随 seed 顺序漂移（seed 顺序无关）。
    for _asp in list(aspect_materials.keys()):
        aspect_materials[_asp] = sorted(set(aspect_materials[_asp]))
    for _asp in list(context_candidate_materials.keys()):
        context_candidate_materials[_asp] = sorted(set(context_candidate_materials[_asp]))

    # -- set 枚举（三个 set_complete aspect；P1-4 按单一 document_version 逐版本枚举） --
    set_results: dict[str, dict] = {}
    boundary_incomplete_aspects: set[str] = set()
    # 修复 A：主题边界策略不可用的 aspect 一律并入 boundary_incomplete（fail-closed）。
    boundary_incomplete_aspects |= topic_boundary_unavailable_aspects
    for aspect_id in SET_ASPECTS:
        mids = tuple(dict.fromkeys(aspect_materials.get(aspect_id, [])))
        aggregate, incomplete = _enumerate_set_by_version(
            aspect_id, mids, all_materials, resolver, enumerator,
            aspect_expansions, dep, tuple(all_assemblies_dicts),
            known_material_ids=set(all_materials))
        set_results[aspect_id] = aggregate
        if incomplete:
            boundary_incomplete_aspects.add(aspect_id)

    # -- 修复 B：源对象清单（材料管线硬门）—— 对全部边界内 formal+context 材料建立清单，
    #    独立于 set enumeration / boundary proof 是否闭合，逐 aspect 持久化（绝不 null）。 --
    source_object_inventories: dict[str, dict] = {}
    for aspect_id in sorted(set(aspect_materials) | set(context_candidate_materials)):
        mids = tuple(dict.fromkeys(
            list(aspect_materials.get(aspect_id, []))
            + list(context_candidate_materials.get(aspect_id, []))))
        rps: list[TS.ResolvedPayload] = []
        for mid in mids:
            m = all_materials.get(mid)
            if m is None:
                continue
            try:
                rp = TS.verify_material_payload_ref(m.payload_ref, resolver)
            except TS.SchemaValidationError:
                continue
            if rp.payload_bytes is not None:
                rps.append(rp)
        # P1-B.1/B.5/B.6：清单按规范源顺序（document_version→page→block_index→offset）建立，
        # 并对账到**已持久化的 assemblies**（同一份恢复真相）；component 外键必须真实存在。
        source_object_inventories[aspect_id] = build_source_object_inventory(
            aspect_id, tuple(rps), all_assemblies_dicts,
            set(all_materials)).to_dict()

    # -- 材料索引 / aspect 矩阵 / before_after / payload 预览 --
    # aspect_of_material 关联全部 aspect（formal + context_candidate），并稳定去重/排序。
    # §四修复四：formal 与 context 必须分别遍历做稳定并集，不得用覆盖式 dict 展开
    # （{**a, **b} 会让同 aspect 的 context 覆盖 formal，导致 source 材料的 aspect 归属丢失）。
    aspect_of_material: dict[str, list[str]] = {}
    for aspect_id in sorted(set(aspect_materials) | set(context_candidate_materials)):
        mids = list(dict.fromkeys(
            list(aspect_materials.get(aspect_id, []))
            + list(context_candidate_materials.get(aspect_id, []))))
        for mid in mids:
            aspect_of_material.setdefault(mid, [])
            if aspect_id not in aspect_of_material[mid]:
                aspect_of_material[mid].append(aspect_id)
    for mid in aspect_of_material:
        aspect_of_material[mid].sort()

    # §四修复四：显式 aspect_links（aspect_id / material_id / role / disposition /
    # seed_reachability）。role 键 (aspect_id, material_id)；seed_reachability 从
    # boundary_decisions 的 (aspect_id, evidence_id) 反查可达的 seed evidence_id 集合，
    # 与 seed 处理顺序无关（确定性排序）。
    reach_by_aspect_evidence: dict[tuple[str, str], set[str]] = {}
    for d in boundary_decisions:
        reach_by_aspect_evidence.setdefault(
            (d["aspect_id"], d["evidence_id"]), set()).add(d["seed_evidence_id"])

    aspect_links: list[dict] = []
    for (aspect_id, mid), role in sorted(role_by_aspect_material.items()):
        m = all_materials.get(mid)
        evidence_id = getattr(m.authority_assessment, "evidence_id", "") if m else ""
        aspect_links.append({
            "aspect_id": aspect_id,
            "material_id": mid,
            "role": role,
            # §三 P1-4：该 (aspect, material) 归属行**拥有**的引用表对象（内容寻址身份 + assembly
            # + 锚点/标记/occurrence 身份 + component evidence 身份）。按 target_object_id 稳定排序，
            # 使本行可独立复核「目标对象确有正式 aspect 绑定，且绑定归属正确」。
            "reference_table_objects": [
                ref_objects_by_aspect_material[(aspect_id, mid)][oid]
                for oid in sorted(ref_objects_by_aspect_material.get((aspect_id, mid), {}))],
            "disposition": _material_boundary_disposition(
                m, boundary_by_aspect_evidence, (aspect_id,)),
            "boundary_disposition_identity": [
                {"aspect_id": aspect_id, "evidence_id": evidence_id,
                 "direction": cand.get("direction", ""),
                 "disposition": cand.get("disposition", ""),
                 "reason_code": cand.get("reason_code", "")}
                for cand in [boundary_by_aspect_evidence.get((aspect_id, evidence_id))]
                if isinstance(cand, dict)],
            "seed_reachability": sorted(
                reach_by_aspect_evidence.get((aspect_id, evidence_id), set())),
        })
    aspect_links.sort(key=lambda l: (l["aspect_id"], l["material_id"], l["role"]))

    material_entries = [
        _material_entry(m, tuple(aspect_of_material.get(m.material_id, [])),
                        boundary_by_aspect_evidence, role_by_aspect_material)
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
    _write_json(out_dir / "boundary_decisions.json", {
        "boundary_disposition_version": BOUNDARY_DISPOSITION_VERSION,
        "topic_boundary_version": TOPIC_BOUNDARY_VERSION,
        "decisions": boundary_decisions,
        "policy_status": boundary_status_by_aspect,
        # §四.A.4/A.5：处置生产身份（aspect_id/evidence_id/direction/relation/reason_code/
        # disposition/section_path/content_hash）+ 逐 aspect 有效处置（键 (aspect_id, evidence_id)）。
        "decision_identities": [
            {"identity": list(k), "occurrences": v}
            for k, v in sorted(boundary_by_identity.items(), key=lambda kv: kv[0])
        ],
        "effective_disposition_by_aspect_evidence": [
            {"aspect_id": a, "evidence_id": e, "disposition": v.get("disposition", ""),
             "reason_code": v.get("reason_code", "")}
            for (a, e), v in sorted(boundary_by_aspect_evidence.items())
        ],
    })
    # §四.A.1–A.3：运行时派生的边界验证记录（逐 aspect，身份独立不合并）。
    _write_json(out_dir / "boundary_verification.json", {
        "boundary_verification_algorithm": BOUNDARY_VERIFICATION_ALGORITHM,
        "boundary_verification_version": BOUNDARY_VERIFICATION_VERSION,
        "dependency_fingerprint": dep,
        "aspects": [boundary_verification_by_aspect[a]
                    for a in sorted(boundary_verification_by_aspect)],
    })
    # §四.C.2/C.4：逐引用/续表目标的滚动观察 + 逐方向未读因果链（互不覆盖）。
    _write_json(out_dir / "rolling_read_outcomes.json", {
        "targets": target_outcomes_all,
        "direction_unread": direction_unread_all,
    })
    # §四：四类分离（formal / context_candidate / outside_boundary / unread）显式落盘。
    _write_json(out_dir / "aspect_membership.json", {
        aspect_id: {
            "formal_material_ids": aspect_materials.get(aspect_id, []),
            "context_candidate_material_ids": context_candidate_materials.get(aspect_id, []),
            "outside_boundary_evidence": outside_boundary_evidence.get(aspect_id, []),
            "unread_inside_boundary": unread_inside_boundary_evidence.get(aspect_id, []),
            "seed_evidence_ids": aspect_seeds.get(aspect_id, []),
        }
        for aspect_id in aspect_ids
    })
    _write_json(out_dir / "set_enumeration.json", set_results)
    # 修复 B：源对象清单独立落盘（六类验收器必须读取；逐 aspect 四态逐项结果）。
    _write_json(out_dir / "source_object_inventory.json", source_object_inventories)
    # 修复 A / P1-A.1：全部 115 个 topic_harness aspects 的主题边界策略覆盖审计（四态）。
    # 本次运行**实际**得到独立验证的 aspect 结论经 verifications 传入，其余 aspect 如实
    # 记为 policy_self_consistent（绝不冒充 boundary_semantics_verified）。
    _run_verifications = {}
    for _aid, _st in boundary_status_by_aspect.items():
        _vd = _st.get("verification") or {}
        if _vd:
            _run_verifications[_aid] = BoundarySemanticsVerification(
                aspect_id=_aid, verified=bool(_vd.get("verified")),
                reason=str(_vd.get("reason") or ""),
                cases=(), policy_version=str(_vd.get("policy_version") or ""))
    _write_json(out_dir / "topic_boundary_coverage.json",
                topic_boundary_coverage(verifications=_run_verifications))
    _write_json(out_dir / "aspect_material_matrix.json", matrix_rows)
    _write_json(out_dir / "aspect_links.json", aspect_links)
    # §十二 Codex 结论 2：验收所用预算的来源/版本/限制/实际消耗/未读范围显式落盘，
    # 供验收审计回查（绝不把验收窗口并入生产默认）。
    _write_json(out_dir / "budget_profile.json", {
        "budget_profile_version": BUDGET_PROFILE_VERSION,
        "profile_name": budget_profile_name,
        "budget_limits": budget_profile(budget_profile_name).as_limits(),
        "seed_budget_records": seed_budget_records,
    })
    _write_json(out_dir / "assemblies.json", [
        _assembly_to_dict(a) for a in all_assemblies])
    # §四：被取代的跨 pass 投影**逐条披露**（单一恢复真相的收敛明细；绝不静默丢弃）。
    _write_json(out_dir / "assemblies_superseded.json", {
        "superseded_count": len(superseded_assemblies),
        "superseded": superseded_assemblies,
    })

    _write_material_index_md(out_dir, material_entries, all_assemblies,
                             all_materials, resolver)
    _write_aspect_matrix_md(out_dir, matrix_rows, aspect_materials,
                            context_candidate_materials, all_materials, resolver)
    _write_before_after_md(out_dir, seed_before_after, all_materials, resolver)

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

    # -- run manifest：run/Pack/依赖身份（§五.4）必须可验证绑定 --
    # 产物内容指纹 + 冻结 Contract/SourcePolicy 身份 + R2 dependency 指纹 + 真实计数。
    # 内容缺口（未读/未解析）如实记录为计数与额外审计，绝不填占位值。
    artifacts = {
        name: RM.artifact_fingerprint(out_dir / name) for name in RUN_ARTIFACTS
    }
    missing_artifacts = sorted(n for n, fp in artifacts.items() if not fp)
    run_manifest = RM.build_run_manifest(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed_manifest_fingerprint=manifest.fingerprint,
        artifacts=artifacts,
        counts={
            "seed_count": len(manifest.entries),
            "resolved_count": sum(1 for e in resolved_entries if e.get("resolved")),
            "unresolved_count": sum(1 for e in resolved_entries if not e.get("resolved")),
            "material_count": len(all_materials),
            "assembly_count": len(all_assemblies),
            "assembly_superseded_count": len(superseded_assemblies),
            "boundary_decision_count": len(boundary_decisions),
            "unread_scope_count": len(unread_scopes),
            "rolling_target_count": len(target_outcomes_all),
            "direction_unread_count": len(direction_unread_all),
        },
        harness_db=({"path": str(harness_db)} if harness_db else {}),
        evidence_db=({"path": str(evidence_db)} if evidence_db else {}),
        accept_reject_audit={
            "rejected_aspects": sorted(rejected_aspects),
            "unread_aspects": sorted(unread_aspects),
            "boundary_incomplete_aspects": sorted(boundary_incomplete_aspects),
            "topic_boundary_unavailable_aspects": sorted(
                topic_boundary_unavailable_aspects),
            "missing_artifacts": missing_artifacts,
            # §三 P1-4：无处落地的引用表对象声明（缺 aspect 归属行 / 目标 evidence 未采纳）
            # → 显式披露；验收侧同样要求「目标对象确有正式 aspect 绑定」，两处都不静默。
            "reference_object_link_orphans": ref_link_orphans,
        },
        extra={"budget_profile_name": budget_profile_name,
               "budget_profile_version": BUDGET_PROFILE_VERSION})
    RM.write_run_manifest(out_dir, run_manifest)

    return {
        "run_id": run_id, "out_dir": str(out_dir),
        "seed_count": len(manifest.entries),
        "resolved_count": sum(1 for e in resolved_entries if e.get("resolved")),
        "material_count": len(all_materials),
        "assembly_count": len(all_assemblies),
        "assembly_superseded_count": len(superseded_assemblies),
        "reference_object_link_orphan_count": len(ref_link_orphans),
        "run_manifest_fingerprint": run_manifest["run_manifest_fingerprint"],
    }


def _dedup_assemblies(assemblies: list) -> list:
    """按 content-addressed assembly_id 确定性去重（§四/P1-2）。

    assembly_id = sha256([relation, component_material_ids])（content-addressed 身份）。
    同 ID 必须同内容（序列化后逐字段一致）；若同 ID 但内容不同 → fail-closed 抛 ValueError
    （绝不静默丢弃或保留冲突投影）。返回每 ID 一条，按 assembly_id 稳定排序。
    """
    by_id: dict[str, list] = {}
    order: list[str] = []
    for a in assemblies:
        aid = a.assembly_id
        if aid not in by_id:
            by_id[aid] = [a]
            order.append(aid)
        else:
            by_id[aid].append(a)
    deduped: list = []
    for aid in order:
        group = by_id[aid]
        canonical = _assembly_to_dict(group[0])
        for other in group[1:]:
            if _assembly_to_dict(other) != canonical:
                raise ValueError(
                    f"assembly_id 冲突：同一 {aid} 对应不同内容（fail-closed，拒绝覆盖）")
        deduped.append(group[0])
    return deduped


def _component_coverage(mid: str, materials_by_id: dict) -> tuple[str, int]:
    """component material 的**来源覆盖度**：(evidence_id, fragment_offset)。

    ``fragment_offset`` 为 0/None 表示该 material 是所属 Block 的**完整投影**；> 0 表示它是
    mixed block 的「主题内前缀片段投影」（同一 Block 的子集文本）。片段投影与其完整投影指同
    一 evidence_id，完整投影覆盖更多源文本。
    """
    m = materials_by_id.get(mid)
    if m is None:
        return (f"?{mid}", 0)
    ev = getattr(m.authority_assessment, "evidence_id", "") or ""
    off = getattr(m.locator, "offset", None)
    return (ev, off if isinstance(off, int) and off > 0 else 0)


def _projection_coverage(components, materials_by_id: dict) -> dict[str, int]:
    """投影的**源文本覆盖度**：``evidence_id → 最小 fragment_offset``（越小覆盖越多）。

    component 的 payload 文本 = 该 Block 正文的 ``[0, fragment_offset)`` 前缀（完整投影
    ``fragment_offset == 0`` → 整块）。同一 evidence_id 在投影内取**最小** offset（覆盖最多的
    那份），使同一 Block 出现多个投影时按「谁覆盖得更多」比较。
    """
    cov: dict[str, int] = {}
    for mid in components or ():
        ev, off = _component_coverage(mid, materials_by_id)
        cov[ev] = min(cov.get(ev, off), off)
    return cov


def _projection_is_complete_over(cand: list, other: list, materials_by_id: dict) -> bool:
    """``cand`` 是否覆盖 ``other``：other 的每个源 Block 都在 cand 里被等/更多地覆盖。

    形式化：对 other 的每个 ``(evidence_id, offset_o)``，cand 必须含同 evidence_id 且
    ``offset_c <= offset_o`` 的 component（前缀包含关系）。全部满足 → cand 是同一批源 Block 的
    更完整投影；否则不可比（``False``）。
    """
    cand_cov = _projection_coverage(cand, materials_by_id)
    for mid in other or ():
        ev, off = _component_coverage(mid, materials_by_id)
        if ev not in cand_cov or cand_cov[ev] > off:
            return False
    return True


def _converge_source_object_projections(
        assemblies: list, materials_by_id: dict) -> tuple[list, list]:
    """**同一源对象（同一张表）跨 seed pass 只保留一份恢复真相**（§四）。

    多 seed 扩读会把同一份源 Block 以**不同投影**（完整块 material / 主题内前缀片段 material）
    带进材料集合；摊平表恢复按 pass 各自构建，于是同一张表会留下多条 assembly。源对象清单把
    「同一表对象对应 ≥2 条 assembly」判为**重复/错误合并**（recovery_failed），因此必须在持久化
    前确定性收敛，而不是靠清单侧放行。

    收敛规则（纯结构、确定性、无表号/页码/公司规则）：
      - 只处理**摊平表恢复**投影（``relation == flattened_table_recovery``）且带表题；
        组键 = 源对象清单使用的同一表对象键（``SOI.table_object_key``，表号优先，否则表题 slug）；
      - 组内**恢复结构必须一致**（``SOI.flattened_table_structure_identity`` 判别串相同），
        结构不同 ⇒ 真不同的表/错误合并 → 原样保留交回清单 fail-closed；
      - 组内所有 assembly 必须来自**同一 document_id + document_version**（由 component 材料的
        真实来源判定）；跨文档同名表**绝不收敛**（原样保留 → 清单照旧 fail-closed）；
      - 组内若存在某条投影**覆盖**其余全部投影（``_projection_is_complete_over``：每个 component
        都能在对方找到同一 Block 的等/更完整对应），则只保留**覆盖最完整**的那一条；同分时按
        ``assembly_id`` 取最小（确定性）；
      - 被取代的投影**逐条披露**（assembly_id / 表题 / component / 取代原因），写入
        ``assemblies_superseded.json``，绝不静默丢弃；
      - 无可比关系（互相都不覆盖，例如两张真不同的同名表）→ **原样保留**，交回清单 fail-closed。
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for a in assemblies:
        # 只收敛**摊平表恢复**投影：同一源 Block 的跨 pass 投影差异由「材料覆盖度」确定性裁决。
        # 结构化表链（table_chain）保持原行为，其重复由源对象清单 fail-closed 报错。
        if getattr(a, "relation", "") != SOI.FLATTENED_TABLE_RELATION:
            continue
        title = getattr(a, "table_title", "") or ""
        if not title:
            continue
        key = SOI.table_object_key(title)
        if not key:
            continue
        groups.setdefault(key, []).append(a)
        if key not in order:
            order.append(key)

    superseded: list[dict] = []
    for key in order:
        group = groups[key]
        if len(group) < 2:
            continue
        docs = {_assembly_document_identity(a, materials_by_id) for a in group}
        if len(docs) != 1:
            continue          # 跨文档同名表：不收敛（清单侧 fail-closed 如实报错）
        # 只有**恢复结构一致**的投影才可收敛为一份真相；结构不同 ⇒ 真不同的表/错误合并，
        # 原样保留交回清单 fail-closed（绝不静默合并两张不同的表）。
        if len({_assembly_structure_signature(a) for a in group}) != 1:
            continue
        ranked = sorted(group, key=lambda a: a.assembly_id)
        winner = None
        for cand in ranked:
            others = [o for o in ranked if o is not cand]
            if others and all(_projection_is_complete_over(
                    list(cand.component_material_ids),
                    list(o.component_material_ids), materials_by_id) for o in others):
                winner = cand
                break
        if winner is None:
            continue          # 无可比关系 → 不合并（交回清单判重复/错误合并）
        win_cov = _projection_coverage(winner.component_material_ids, materials_by_id)
        for o in sorted(group, key=lambda a: a.assembly_id):
            if o.assembly_id == winner.assembly_id:
                continue
            less = []
            for mid in o.component_material_ids:
                ev, off = _component_coverage(mid, materials_by_id)
                if ev in win_cov and win_cov[ev] < off:
                    less.append({"evidence_id": ev,
                                 "superseded_fragment_offset": off,
                                 "retained_fragment_offset": win_cov[ev]})
            superseded.append({
                "table_object_key": key,
                "superseded_assembly_id": o.assembly_id,
                "retained_assembly_id": winner.assembly_id,
                "table_title": getattr(o, "table_title", ""),
                "component_material_ids": list(o.component_material_ids),
                "retained_component_material_ids": list(winner.component_material_ids),
                "less_covered_blocks": sorted(less, key=lambda d: d["evidence_id"]),
                "reason": "同一源对象（同一张表）的较不完整投影：同一 Block 的文本覆盖更少"
                          "（主题内前缀片段投影），已被更完整投影取代；两条投影的恢复结构一致",
            })

    if not superseded:
        return list(assemblies), []
    superseded_ids = {s["superseded_assembly_id"] for s in superseded}
    out = [a for a in assemblies if a.assembly_id not in superseded_ids]
    return out, superseded


def _assembly_structure_signature(a) -> str:
    """assembly 的**恢复结构身份**（复用 ``SOI.flattened_table_structure_identity``，无第二套）。"""
    return SOI.flattened_table_structure_identity({
        "title": getattr(a, "table_title", "") or "",
        "unit": getattr(a, "unit", None),
        "headers": getattr(a, "headers", ()) or (),
        "rows": getattr(a, "rows", ()) or (),
        "total_row": getattr(a, "total_row", None),
    })


def _assembly_document_identity(a, materials_by_id: dict) -> tuple[str, str]:
    """assembly 的文档身份（component 材料的真实 document_id/document_version；缺失 → ""）。"""
    doc = ""
    ver = ""
    for mid in getattr(a, "component_material_ids", ()) or ():
        m = materials_by_id.get(mid)
        if m is None:
            continue
        aa = getattr(m, "authority_assessment", None)
        doc = doc or str(getattr(aa, "document_id", "") or "")
        ver = ver or str(getattr(aa, "document_version", "") or "")
    return (doc, ver)


def _assembly_to_dict(a) -> dict:
    d = {
        "assembly_id": a.assembly_id,
        "context_parent_id": a.context_parent_id,
        "component_material_ids": list(a.component_material_ids),
        "relation": a.relation,
        "boundary_desc": a.boundary_desc,
    }
    # §三 P1-4：引用目标表对象投影（内容寻址、可复核）。它只由「成功解析 + 目标真的被采纳」
    # 的绑定记录派生，落盘后由清单/验收按同一份身份逐层复核。必须先于摊平表/表链分支判定：
    # 两者字段集不同（引用表对象没有 header_evidence_id / continuation_proof 等表链字段）。
    if getattr(a, "table_object_id", None) is not None:
        d.update({
            "table_object_id": a.table_object_id,
            "object_schema_version": a.object_schema_version,
            "binding_version": a.binding_version,
            "table_payload": a.table_payload,
            "table_title": a.table_title,
            "unit": a.unit,
            "headers": [list(h) for h in a.header_rows],
            "header_decision": a.header_decision,
            "column_count": a.column_count,
            "rows": [list(r) for r in a.body_rows],
            "closure_row": list(a.closure_row),
            "body_digest": a.body_digest,
            "structure_rows": a.structure_rows,
            "anchor_evidence_id": a.anchor_evidence_id,
            "anchor_material_id": a.anchor_material_id,
            "target_evidence_id": a.target_evidence_id,
            "reference_kind": a.reference_kind,
            "reference_marker": a.reference_marker,
            "marker_start": a.marker_start,
            "marker_end": a.marker_end,
            "reference_occurrence_index": a.reference_occurrence_index,
            "target_start": a.target_start,
            "target_end": a.target_end,
            "target_end_boundary": a.target_end_boundary,
            "target_closed": a.target_closed,
            "component_evidence_ids": list(a.component_evidence_ids),
            "dependency_fingerprint": a.dependency_fingerprint,
            # 与摊平表恢复共用同一套「结果 ↔ assembly.recovery_status」闭合规则：
            # 通过全部结构资格判定的引用目标对象即 ok（partial/failed 不会产出投影）。
            "recovery_status": "ok",
            "recovery_issue": None,
        })
        return d
    if getattr(a, "table_title", None) is not None:
        d.update({
            "table_title": a.table_title, "unit": a.unit,
            "header_evidence_id": a.header_evidence_id,
            "body_evidence_ids": list(a.body_evidence_ids),
            "continuation_evidence_ids": list(a.continuation_evidence_ids),
            # §五修复三：摊平表恢复出的列结构显式落盘（title/unit/header/rows/total）。
            "headers": list(getattr(a, "headers", ())),
            "rows": [list(r) for r in getattr(a, "rows", ())],
            "total_row": list(getattr(a, "total_row", ()))
            if getattr(a, "total_row", None) is not None else None,
            # P1-2：严格结构校验结果显式落盘（ok/partial/failed + 原因）。
            "recovery_status": getattr(a, "recovery_status", "ok"),
            "recovery_issue": getattr(a, "recovery_issue", None),
            # §修复 C：跨页续表「同一张表」证明显式落盘（valid/sample_not_obtained/7 条件）。
            "continuation_proof": _continuation_proof_to_dict(
                getattr(a, "continuation_proof", None)),
        })
    return d


def _continuation_proof_to_dict(proof) -> dict | None:
    if proof is None:
        return None
    return {
        "proof_version": proof.proof_version,
        "valid": proof.valid,
        "sample_not_obtained": proof.sample_not_obtained,
        "normalized_title": proof.normalized_title,
        "header_evidence_id": proof.header_evidence_id,
        "continuation_evidence_ids": list(proof.continuation_evidence_ids),
        "header_page": proof.header_page,
        "continuation_pages": list(proof.continuation_pages),
        "title_compatible": proof.title_compatible,
        "unit_compatible": proof.unit_compatible,
        "column_compatible": proof.column_compatible,
        "row_column_continuity": proof.row_column_continuity,
        "final_recovery_status": proof.final_recovery_status,
        "issue": proof.issue,
        # P1-C.5：恢复后的结构（title / unit / headers / rows / total / component 顺序）。
        "recovered_title": getattr(proof, "recovered_title", ""),
        "recovered_unit": getattr(proof, "recovered_unit", ""),
        "recovered_headers": list(getattr(proof, "recovered_headers", ()) or ()),
        "recovered_row_count": getattr(proof, "recovered_row_count", 0),
        "recovered_total": (list(proof.recovered_total)
                            if getattr(proof, "recovered_total", None) is not None else None),
        "component_order": list(getattr(proof, "component_order", ()) or ()),
        # P1-C.6：真实 continued_from 链（逐块读取 + 指向前一块的验证结果）。
        "continued_from_chain": [dict(c) for c in
                                 (getattr(proof, "continued_from_chain", ()) or ())],
        "continued_from_verified": getattr(proof, "continued_from_verified", None),
        # §四：身份来源（structured_payload | recovered_structure）+ 摊平续表结构见证。
        # None = 该条件对本次证明不适用（绝不等于通过）；逐 span 事实可人工/Codex 独立复核。
        "identity_source": getattr(proof, "identity_source", "structured_payload"),
        "header_repeat_verified": getattr(proof, "header_repeat_verified", None),
        "boundary_consecutive": getattr(proof, "boundary_consecutive", None),
        "section_path_shared": getattr(proof, "section_path_shared", None),
        "same_document_verified": getattr(proof, "same_document_verified", None),
        "span_facts": [dict(f) for f in (getattr(proof, "span_facts", ()) or ())],
    }


# ---------------------------------------------------------------------------
# 人读产物
# ---------------------------------------------------------------------------

def _material_content_preview(m: TS.ResearchMaterial, resolver) -> str:
    """解码 payload 信封，返回人读内容摘要（正文文本 + 结构化表格 cells）。

    §八：材料库必须「真正可看」——md 里展示实际内容，而非只列 material_id/hash。
    """
    try:
        rp = resolver.resolve(m.payload_ref)
    except Store.StorageCorruptionError:
        return "（payload 损坏，无法预览）"
    if rp is None or rp.payload_bytes is None:
        return "（payload 不可用）"
    try:
        obj = json.loads(rp.payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "（payload 非法 JSON，无法预览）"
    content = obj.get("content") if isinstance(obj, dict) else {}
    if not isinstance(content, dict):
        return "（payload 无 content）"
    parts: list[str] = []
    etype = content.get("evidence_type", "")
    text = (content.get("text", "") or "").strip()
    if text:
        parts.append(f"正文[{etype}]: {text[:600]}")
    sp = content.get("structured_payload")
    if isinstance(sp, dict):
        headers = sp.get("headers")
        cells = sp.get("cells")
        if isinstance(cells, list) and cells:
            hdr = " | ".join(str(h) for h in headers) if isinstance(headers, list) else ""
            if hdr:
                parts.append(f"表头: {hdr}")
            for row in cells[:8]:
                if isinstance(row, list):
                    parts.append("  · " + " | ".join(str(c) for c in row))
    return "\n".join(parts) if parts else "（无正文/结构化内容）"


def _write_material_index_md(out_dir: Path, entries: list[dict],
                             assemblies: list, materials_by_id: dict,
                             resolver) -> None:
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
        lines.append(f"- 边界处置：`{e.get('boundary_disposition') or '—'}`"
                     f"（`{e.get('boundary_reason_code') or '—'}`）")
        _aspect_roles = e.get("aspect_roles") or {}
        _role_str = ", ".join(f"`{a}`={r}" for a, r in sorted(_aspect_roles.items())) or "—"
        lines.append(f"- aspect 角色（按 aspect）：{_role_str}")
        lines.append(f"- 关联 aspect：{', '.join(f'`{a}`' for a in e['aspect_ids'])}")
        m = materials_by_id.get(e["material_id"])
        if m is not None:
            lines.append("- 实际内容：")
            lines.append("")
            lines.append("  ```")
            lines.append("  " + _material_content_preview(m, resolver).replace("\n", "\n  "))
            lines.append("  ```")
        lines.append("")
    lines.append("## 组合投影（MaterialAssembly/TableAssembly）")
    lines.append("")
    for a in assemblies:
        lines.append(f"- `{a.assembly_id}`（{a.relation}）→ "
                     f"{', '.join('`' + c + '`' for c in a.component_material_ids)}")
    lines.append("")
    (out_dir / "material_index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_aspect_matrix_md(out_dir: Path, rows: list[dict],
                            aspect_materials: dict, context_candidate_materials: dict,
                            materials_by_id: dict, resolver) -> None:
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
    # §八：矩阵下附每个 aspect 的实际材料内容，而非只列状态。
    for r in rows:
        lines.append(f"## {r['aspect_id']}（状态 `{r['state']}`）")
        lines.append("")
        mids = aspect_materials.get(r["aspect_id"], [])
        if not mids:
            lines.append("（无正式材料）")
            lines.append("")
        else:
            for mid in mids:
                m = materials_by_id.get(mid)
                if m is None:
                    lines.append(f"- `{mid}`（材料缺失）")
                    continue
                lines.append(f"### 正式材料 {mid}")
                lines.append("")
                lines.append(f"- 材料类型：`{m.material_type}` · authority："
                             f"`{getattr(m.authority_assessment, 'verdict', '')}`")
                lines.append(f"- component evidence_id：`{getattr(m.authority_assessment, 'evidence_id', '')}`")
                lines.append("")
                lines.append("```")
                lines.append(_material_content_preview(m, resolver).replace("\n", "\n  "))
                lines.append("```")
                lines.append("")
        ctx_ids = context_candidate_materials.get(r["aspect_id"], [])
        if ctx_ids:
            lines.append("**context_candidate（不推进 coverage、不计正式材料）**："
                         + ", ".join(f"`{mid}`" for mid in ctx_ids))
            lines.append("")
    (out_dir / "aspect_material_matrix.md").write_text("\n".join(lines), encoding="utf-8")


def _write_before_after_md(out_dir: Path, rows: list[dict],
                           materials_by_id: dict, resolver) -> None:
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
        # §八：扩读后材料逐条展示实际内容（不只列 material_id）。
        for mid in r["expanded_material_ids"]:
            m = materials_by_id.get(mid)
            if m is None:
                lines.append(f"### {mid}（材料缺失）")
                lines.append("")
                continue
            lines.append(f"### 扩读材料 {mid}")
            lines.append("")
            lines.append("```")
            lines.append(_material_content_preview(m, resolver).replace("\n", "\n  "))
            lines.append("```")
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
    parser.add_argument("--chroma-dir", default="data/chroma_v2")
    parser.add_argument("--sparse-dir", default="data/sparse_v2")
    parser.add_argument("--manifest-dir", default="data/index_v2")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--budget-profile", default="production",
                        choices=("production", "acceptance"))
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.discover_seeds:
        if not args.company_id:
            print("discover-seeds 需 --company-id", file=sys.stderr)
            return 2
        try:
            from tools import adapters
            # §五：evaluation-only 只读发现注册表（复用正式 search_evidence/search_tables
            # ToolSpec + 现有 ToolRegistry；只读 evidence DB + 索引/向量路径，不 init_db、
            # 不改 estore._db_path、skip financial、零博查）。
            registry = adapters.build_readonly_discovery_registry(
                ev_db=Path(args.ev_db), chroma_dir=Path(args.chroma_dir),
                sparse_dir=Path(args.sparse_dir), manifest_dir=Path(args.manifest_dir),
                audit_dir=Path(args.out_root) / "tool_audit")
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
        return 1 if summary.get("discovery_failed") else 0

    if not args.seed_manifest:
        print("正式材料验收 runner 需 --seed-manifest（两阶段 seed 修正二）", file=sys.stderr)
        return 2
    try:
        manifest = load_seed_manifest(args.seed_manifest)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"seed manifest 加载失败：{e}", file=sys.stderr)
        return 2

    try:
        summary = run_material_slice(
            args.run_id, manifest, evidence_db=args.ev_db,
            harness_db=args.harness_db, out_root=args.out_root,
            budget_profile_name=args.budget_profile)
    except FileExistsError as e:
        print(f"材料验收 runner 拒绝运行（输出目录已存在）：{e}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
