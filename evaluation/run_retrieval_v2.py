"""Track A：V2 Hybrid Retrieval 公平对照 Runner（de-Router，固定本地 Hybrid 决策）。

对冻结 41 问 ELIGIBLE_LOCAL 集合逐题：不调 Router，统一用同一固定本地 Hybrid 决策
（sparse BM25 + BGE-M3 Dense → RRF → EvidencePack），评价正确本地文档页是否进入 Top-K。

与 V1 `run_baseline.py` 的区别（不修改 V1 行为）：
- 检索对象是 V2 的 Evidence Store current 证据块 + 版本化索引，不读 V1 Chroma；
- de-Router：每题不再经 Router 判定，全部 ELIGIBLE_LOCAL 用同一固定决策
  （route=STANDARD_RAG、reason_code=TRACK_A_FIXED_LOCAL、candidate_k=20、context_k=10），
  避免 Router 的 DB/External 判定干扰本地检索计分；DB/External/多轮不计本地召回；
- 命中判据与 V1 完全一致：document_id + PDF 1-based 页码，K=1/5/10 前缀；
- 复用 evaluation 的 dataset / eligibility / metrics 计分管线，分母运行前冻结并
  fail-closed 校验（FROZEN_ELIGIBILITY_BREAKDOWN），空召回计 0。

Track A 通过门槛（任务书 §9）：Macro RequiredPageCoverage@10 ≥ 25.3%、
P0 ≥ 24.4%，逐题均有 trace 与处理结论。真实 BGE-M3 实际加载一次（warmup），
mock 不能替代真实验收。

CLI:
  python -m evaluation.run_retrieval_v2 --dataset evaluation/datasets/v1_baseline.jsonl \
      --corpus-manifest evaluation/datasets/corpus_manifest.json \
      --company 300750 --k 1 5 10
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from evaluation.schema import (
    BaselineRunResult,
    CaseResult,
    CorpusManifest,
    CorpusState,
    Eligibility,
    RetrievedChunkSnapshot,
    RetrievalEvalCase,
)
from routing import schema as S

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Track A 固定本地决策（de-Router）
# ---------------------------------------------------------------------------
#
# Track A 只评「本地 Hybrid 检索」质量，不得被 Router 的 DB/External 判定干扰：
# 对全部 ELIGIBLE_LOCAL 题用同一固定本地 Hybrid 决策（不调 Router），路由固定
# STANDARD_RAG（base hybrid），reason_code 固定 TRACK_A_FIXED_LOCAL，预算固定为
# 任务书 §3 公平对照口径（candidate_k=20、context_k=10、§9 每题一次检索）。
_TRACK_A_BUDGET = S.RetrievalBudget(
    candidate_k_sparse=20, candidate_k_dense=20, fusion_k=20, context_k=10,
    timeout_ms=5000)

# 冻结分母（任务书 §9 Track A）：41 问 → ELIGIBLE_LOCAL 37 / EXTERNAL_ONLY 3 /
# INVALID_GOLD_MAPPING 1（COMP-DZ1 募集资金章节缺页码）。运行前校验，与冻结值不符
# 即 fail-closed，防止 gold/语料/资格规则漂移导致分母被静默重算。
FROZEN_ELIGIBILITY_BREAKDOWN: dict[str, int] = {
    "ELIGIBLE_LOCAL": 37,
    "EXTERNAL_ONLY": 3,
    "INVALID_GOLD_MAPPING": 1,
}


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _validate_ks(ks: list[int]) -> list[int]:
    ks = sorted(set(int(k) for k in ks))
    if not ks:
        raise ValueError("ks 不能为空")
    for k in ks:
        if k <= 0:
            raise ValueError(f"K 必须为正整数: {k}")
        if k > 20:
            raise ValueError(f"当前 context_k=10，拒绝 K>{k}")
    for req in (1, 5, 10):
        if req not in ks:
            raise ValueError(f"ks 必须包含 1/5/10，缺 {req}")
    return ks


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: str | Path) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def _git_state() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        commit = ""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        status = ""
    return {"commit": commit, "dirty": bool(status.strip()), "porcelain": status.strip()}


def _dependency_versions() -> dict:
    out: dict = {}
    for name in ("chromadb", "pypdf", "pandas", "openpyxl", "FlagEmbedding", "anthropic"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", "unknown")
        except Exception:
            out[name] = "unavailable"
    return out


def _model_info(model) -> dict:
    if model is None:
        return {"model_name": None, "use_fp16": None, "device": "not_loaded"}
    info: dict = {
        "model_name": getattr(model, "_model_name", "BAAI/bge-m3"),
        "use_fp16": getattr(model, "_use_fp16", True),
    }
    try:
        import torch
        info["device"] = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        info["device"] = "unknown"
    return info


# ---------------------------------------------------------------------------
# V2 语料盘点（Evidence Store 版 CorpusState）
# ---------------------------------------------------------------------------

def inspect_evidence_corpus(manifest: CorpusManifest, company_id: str) -> CorpusState:
    """从 Evidence Store 只读盘点 current 证据块，产出与 V1 对齐的 CorpusState。

    键按 manifest 的 source_file（文件名）组织，document_id 经 manifest 映射，
    与 classify_eligibility / classify_failure 的 `doc.source_file` 判定一致。
    """
    from retrieval import indexer_v2

    sf_by_id = {d.document_id: d.source_file for d in manifest.documents}
    blocks, _metas = indexer_v2._collect_current(company_id)

    documents_indexed: dict[str, dict] = {}
    indexed_pages: dict[str, set[int]] = {}
    records: list[tuple[str, str, int, int, str]] = []

    for b in blocks:
        sf = sf_by_id.get(b.document_id, b.source_name)
        page = int(b.page_number or 0)
        entry = documents_indexed.setdefault(sf, {
            "page_min": page, "page_max": page, "chunk_count": 0,
            "source_type": b.source_type,
        })
        entry["page_min"] = min(entry["page_min"], page)
        entry["page_max"] = max(entry["page_max"], page)
        entry["chunk_count"] += 1
        if page > 0:
            indexed_pages.setdefault(sf, set()).add(page)
        records.append((b.evidence_id, sf, page, b.block_index,
                        (b.content_hash or "")[:16]))

    records.sort(key=lambda r: r[0])
    fingerprint = hashlib.sha256(
        json.dumps(records, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    warnings: list[str] = []
    manifest_files = {d.source_file for d in manifest.documents}
    for d in manifest.documents:
        if d.source_file not in documents_indexed:
            warnings.append(f"manifest 文档未入 Evidence 索引: {d.source_file}")
    for sf in sorted(set(documents_indexed) - manifest_files):
        warnings.append(f"Evidence 中存在 manifest 未登记的文档: {sf}")

    return CorpusState(
        collection_name=f"v2_evidence__{company_id}",
        exists=bool(blocks),
        chunk_count=len(blocks),
        documents_indexed=documents_indexed,
        fingerprint=fingerprint,
        indexed_pages=indexed_pages,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# EvidenceRef → RetrievedChunkSnapshot（复用 V1 计分管线）
# ---------------------------------------------------------------------------

def _evidence_to_snapshot(ref: S.EvidenceRef, sf_by_id: dict[str, str]) -> RetrievedChunkSnapshot:
    """把 V2 检索 EvidenceRef 映射为 V1 计分所需的快照。

    命中判据（document_id + PDF 1-based 页）经 manifest 映射到 source_file，
    与 metrics.score_case 的 `_source_file_map` 口径一致。
    """
    return RetrievedChunkSnapshot(
        source_file=sf_by_id.get(ref.document_id, ref.source_name),
        page_number=ref.page_number or 0,
        chunk_index=0,                      # EvidenceRef 无 chunk_index；命中判据不看
        score=ref.score or 0.0,
        text_preview=ref.text,
        source_type=ref.source_type,
        section_title="",                   # EvidenceRef 无 section 字段
    )


# ---------------------------------------------------------------------------
# 路由 + 检索的输入构造
# ---------------------------------------------------------------------------

def _build_need(case: RetrievalEvalCase) -> S.InformationNeed:
    return S.InformationNeed(
        need_id=case.case_id, section_id=case.section_id, question=case.question,
        required_evidence_types=[], required_source_types=[],
        time_scope=case.time_scope, priority=case.priority, depends_on=[])


def _build_context(company_id: str, report_as_of: str | None) -> S.RouteContext:
    """Track A 固定上下文：de-Router 后不再需要 DB 能力清单，四清单置空。"""
    return S.RouteContext(
        company_id=company_id, report_as_of=report_as_of,
        available_document_ids=[], available_source_types=[],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=True)


def _fixed_local_decision(need: S.InformationNeed) -> S.RouteDecision:
    """Track A 固定本地决策：所有 ELIGIBLE_LOCAL 共用同一 Hybrid 预算，不调 Router。"""
    return S.RouteDecision(
        need_id=need.need_id, route="STANDARD_RAG",
        reason_code="TRACK_A_FIXED_LOCAL", filters={},
        budget=_TRACK_A_BUDGET, fallback_routes=[],
        decided_by="rule", rule_version=S.RULE_VERSION, confidence="high",
    )


def verify_frozen_denominator(
    exclusion: dict[str, int],
    frozen: dict[str, int] | None = FROZEN_ELIGIBILITY_BREAKDOWN,
) -> None:
    """校验排除计数与冻结分母一致（fail-closed）。

    冻结分母（任务书 §9 Track A）：41 问 → ELIGIBLE_LOCAL 37 / EXTERNAL_ONLY 3 /
    INVALID_GOLD_MAPPING 1（COMP-DZ1）。`frozen=None` 时跳过校验（供 mock/合成小数据集）。
    任一状态计数漂移即抛 ValueError，防止分母被静默重算。
    """
    if frozen is None:
        return
    actual = {k: int(v) for k, v in exclusion.items()}
    expected = {k: int(v) for k, v in frozen.items()}
    if actual != expected:
        raise ValueError(
            f"分母与冻结值不符（fail-closed）：实际 {actual} != 冻结 {expected}")


# ---------------------------------------------------------------------------
# 逐题运行
# ---------------------------------------------------------------------------

def run_case_v2(
    case: RetrievalEvalCase,
    eligibility: Eligibility,
    ks: list[int],
    context: S.RouteContext,
    session,
    sf_by_id: dict[str, str],
) -> tuple[CaseResult, dict]:
    """对单题：固定本地 Hybrid 决策 → 检索 → 映射快照（de-Router，不调 Router）。

    返回 (CaseResult, route_info)。route_info 记录固定决策/状态/trace，供产物落盘。
    Track A 只评本地检索质量，Router 的 DB/External 判定不得干扰本地召回计分。
    """
    need = _build_need(case)
    decision = _fixed_local_decision(need)
    route_info: dict = {
        "route": decision.route, "reason_code": decision.reason_code,
        "decided_by": decision.decided_by,
        "pack_status": None, "failure_code": None, "trace_id": None,
    }
    snapshots: list[RetrievedChunkSnapshot] = []
    error: str | None = None
    t0 = time.perf_counter()

    try:
        pack = session.retrieve(need, decision, context)
        route_info["pack_status"] = pack.status
        route_info["failure_code"] = pack.failure_code
        route_info["trace_id"] = pack.retrieval_trace_id
        if pack.status in ("COMPLETED", "PARTIAL", "EMPTY"):
            snapshots = [_evidence_to_snapshot(r, sf_by_id) for r in pack.evidence]
        elif pack.status == "FAILED":
            error = f"RETRIEVE_FAILED: {pack.failure_code}"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - t0) * 1000.0
    cr = CaseResult(
        case_id=case.case_id, eligibility=eligibility, ks=list(ks),
        retrieved=snapshots, latency_ms=latency_ms, error=error,
        call_id=route_info["trace_id"], audit_path=None, legacy_log=None,
    )
    return cr, route_info


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_retrieval_v2(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    ks: list[int],
    ev_db_path: str = "data/evidence.db",
    fin_db_path: str = "data/financial_v2.db",
    chroma_dir: str = "data/chroma_v2",
    sparse_dir: str = "data/sparse_v2",
    manifest_dir: str = "data/index_v2",
    output_root: str = "evaluation/results",
    report_as_of: str | None = "2024-12-31",
    validate_only: bool = False,
    frozen_eligibility: dict[str, int] | None = None,
    session=None,
) -> BaselineRunResult:
    from evaluation.dataset import load_corpus_manifest, load_dataset, validate_dataset
    from evaluation.failure_classifier import classify_eligibility, classify_failure
    from evaluation.metrics import aggregate_metrics, score_case
    from evaluation.page_mapping import resolve_gold_pages

    ks = _validate_ks(ks)

    cases = load_dataset(dataset_path)
    manifest = load_corpus_manifest(corpus_manifest_path)
    validation = validate_dataset(cases, manifest)
    if validation.errors:
        raise ValueError("数据集校验失败: " + "; ".join(validation.errors))

    from evidence import store as estore
    estore.init_db(ev_db_path)

    corpus_state = inspect_evidence_corpus(manifest, company_id)
    sf_by_id = {d.document_id: d.source_file for d in manifest.documents}

    # resolve + eligibility（分母运行前冻结）
    resolved_by_id: dict[str, object] = {}
    elig_by_id: dict[str, Eligibility] = {}
    for case in cases:
        resolved = resolve_gold_pages(case, manifest)
        elig = classify_eligibility(case, manifest, corpus_state)
        resolved.eligibility = elig
        resolved_by_id[case.case_id] = resolved
        elig_by_id[case.case_id] = elig

    n_eligible = sum(1 for e in elig_by_id.values() if e.is_eligible)
    if n_eligible == 0:
        raise ValueError("没有任何 eligible case，无法运行 Track A")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    result = BaselineRunResult(
        run_id=run_id, company_id=company_id, collection="v2_hybrid", ks=ks,
        output_dir=None, completed=False, status="pending",
        validation=validation, corpus_state=corpus_state,
        manifest_snapshot={"company_id": manifest.company_id,
                           "page_system": manifest.page_system,
                           "documents": [asdict(d) for d in manifest.documents]},
    )

    exclusion: dict[str, int] = {}
    for e in elig_by_id.values():
        exclusion[e.status] = exclusion.get(e.status, 0) + 1
    # 冻结分母校验（fail-closed）：真实 41 问必须与 FROZEN_ELIGIBILITY_BREAKDOWN 一致。
    verify_frozen_denominator(exclusion, frozen_eligibility)
    result.validation.exclusion_breakdown = exclusion
    result.validation.n_eligible = n_eligible

    if validate_only:
        result.status = "validated"
        result.completed = True
        return result

    # ── 正式运行 ──
    from financial_v2 import store as fstore
    from retrieval import indexer_v2, retriever_v2

    fstore.init_db(fin_db_path)

    context = _build_context(company_id, report_as_of)

    model_load_ms: float | None = None
    model = None
    if session is None:
        from retrieval.embedding import get_embedding_model
        t_model0 = time.perf_counter()
        model = get_embedding_model()
        model.encode(["__v2_warmup__"])   # 触发 BGE-M3 权重实际加载，逐题 latency 不含冷启动
        model_load_ms = (time.perf_counter() - t_model0) * 1000.0
        session = retriever_v2.RetrievalSession(
            company_id, chroma_dir=Path(chroma_dir), sparse_dir=Path(sparse_dir),
            manifest_dir=Path(manifest_dir), model=model)

    # 逐题运行（仅 eligible 实际检索）
    case_results: list[CaseResult] = []
    route_by_id: dict[str, dict] = {}
    for case in cases:
        elig = elig_by_id[case.case_id]
        if elig.is_eligible:
            cr, route_info = run_case_v2(case, elig, ks, context, session, sf_by_id)
        else:
            cr = CaseResult(case_id=case.case_id, eligibility=elig, ks=list(ks),
                            retrieved=[], latency_ms=0.0, error=None)
            route_info = {"route": None, "reason_code": None, "decided_by": None,
                          "pack_status": None, "failure_code": None, "trace_id": None}
        case_results.append(cr)
        route_by_id[case.case_id] = route_info

    result_by_id = {r.case_id: r for r in case_results}

    # 计分（复用 V1 口径）
    case_metrics = [
        score_case(result_by_id[c.case_id], resolved_by_id[c.case_id], ks, manifest)
        for c in cases
    ]
    metric_by_id = {m.case_id: m for m in case_metrics}

    max_k = max(ks)
    failures = []
    for c in cases:
        cr = result_by_id[c.case_id]
        m = metric_by_id[c.case_id]
        if not cr.eligibility.is_eligible or cr.error or not m.all_group_hit.get(max_k):
            failures.append(classify_failure(
                c, resolved_by_id[c.case_id], cr, corpus_state, manifest))

    aggregate = aggregate_metrics(cases, case_results, case_metrics, ks)

    index_version: str | None = None
    collection: str | None = None
    m = indexer_v2.load_manifest(company_id, Path(manifest_dir))
    if m is not None:
        index_version = m.index_version
        collection = m.collection

    result.case_results = case_results
    result.case_metrics = case_metrics
    result.failures = failures
    result.aggregate = aggregate
    result.metadata = {
        "runner": "run_retrieval_v2",
        "runner_label": "V2 Hybrid Retrieval",
        "dataset_path": dataset_path,
        "dataset_sha256": _sha256_file(dataset_path),
        "corpus_manifest_path": corpus_manifest_path,
        "corpus_manifest_sha256": _sha256_file(corpus_manifest_path),
        "company_id": company_id,
        "collection": collection,
        "index_version": index_version,
        "ks": ks,
        "ev_db_path": ev_db_path,
        "fin_db_path": fin_db_path,
        "chroma_dir": chroma_dir,
        "sparse_dir": sparse_dir,
        "manifest_dir": manifest_dir,
        "report_as_of": report_as_of,
        "corpus_fingerprint": corpus_state.fingerprint,
        "model_load_ms": model_load_ms,
        "embedding_model": _model_info(model or getattr(session, "model", None)),
        "git": _git_state(),
        "dependencies": _dependency_versions(),
        "generated_at": _now_iso(),
    }
    result.completed = True
    result.status = (
        "completed_with_case_errors"
        if any(cr.error for cr in case_results)
        else "completed"
    )

    out_dir = _write_v2_artifacts(result, output_root, cases=cases,
                                  resolved_by_id=resolved_by_id, route_by_id=route_by_id)
    result.output_dir = str(out_dir)
    return result


# ---------------------------------------------------------------------------
# 产物输出（V2 标注；不修改 V1 report_writer）
# ---------------------------------------------------------------------------

def _jsonable(o):
    from dataclasses import asdict as _asdict, is_dataclass as _is_dc
    if _is_dc(o):
        return _jsonable(_asdict(o))
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, set):
        return sorted(_jsonable(x) for x in o)
    if isinstance(o, Path):
        return str(o)
    return o


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _write_v2_artifacts(result: BaselineRunResult, output_root, cases, resolved_by_id,
                        route_by_id) -> Path:
    output_root = Path(output_root)
    final_dir = output_root / result.run_id
    tmp_dir = output_root / f".{result.run_id}.tmp"
    if final_dir.exists():
        raise FileExistsError(f"run_id 已存在，拒绝覆盖: {final_dir}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = tmp_dir / "inputs"
    inputs_dir.mkdir()

    import shutil, os as _os

    try:
        metric_by_id = {m.case_id: m for m in result.case_metrics}
        failure_by_id = {f.case_id: f for f in result.failures}

        # 1. run_manifest.json
        manifest_payload = dict(result.metadata)
        manifest_payload.update({
            "run_id": result.run_id, "company_id": result.company_id,
            "collection": result.collection, "ks": result.ks,
            "status": result.status, "completed": result.completed,
            "manifest_snapshot": _jsonable(result.manifest_snapshot),
        })
        (tmp_dir / "run_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2. case_results.jsonl（含 route_info）
        with (tmp_dir / "case_results.jsonl").open("w", encoding="utf-8") as f:
            for case in cases:
                cr = next((r for r in result.case_results if r.case_id == case.case_id), None)
                m = metric_by_id.get(case.case_id)
                fl = failure_by_id.get(case.case_id)
                rec: dict = {
                    "case_id": case.case_id, "question": case.question,
                    "section_id": case.section_id,
                    "expected_route_v2": case.expected_route_v2,
                    "priority": case.priority,
                    "eligibility": cr.eligibility.status if cr else "UNKNOWN",
                    "route": route_by_id.get(case.case_id) or {},
                }
                if cr is not None:
                    rec["retrieved"] = [
                        {"rank": i + 1, "source_file": c.source_file,
                         "page_number": c.page_number, "score": c.score,
                         "source_type": c.source_type}
                        for i, c in enumerate(cr.retrieved)
                    ]
                    rec["latency_ms"] = cr.latency_ms
                    rec["error"] = cr.error
                    rec["trace_id"] = cr.call_id
                if m is not None:
                    rec["required_page_coverage"] = m.required_page_coverage
                    rec["recall_status"] = m.recall_status
                if fl is not None:
                    rec["failure"] = {"primary": fl.primary, "reason": fl.reason}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 3. metrics.json
        (tmp_dir / "metrics.json").write_text(
            json.dumps(_jsonable(result.aggregate), ensure_ascii=False, indent=2),
            encoding="utf-8")

        # 4. report.md
        a = result.aggregate
        lines: list[str] = [
            f"# V2 Hybrid Retrieval — {result.company_id} (Track A)",
            "",
            f"- run_id: `{result.run_id}`",
            f"- 状态: `{result.status}`",
            f"- 数据: {a.n_total} 题（eligible {a.n_eligible} / 排除 {a.n_total - a.n_eligible}）",
            "",
            "| 指标 | 值 |",
            "|---|---|",
            f"| Macro **RequiredPageCoverage@10**（总体主分） | **{_fmt_pct(a.required_page_coverage.get(10))}** |",
            f"| P0 **RequiredPageCoverage@10** | **{_fmt_pct(a.p0_required_page_coverage10)}** |",
            f"| **PageHit@10** | **{_fmt_pct(a.page_hit.get(10))}** |",
            f"| **AllGroupHit@10** | **{_fmt_pct(a.all_group_hit.get(10))}** |",
            f"| MRR@10 | {a.mrr10:.3f} |",
            "",
            "### K 维命中",
            "| K | PageHit@K | AllGroupHit@K | RequiredPageCoverage@K |",
            "|---|---|---|---|",
        ]
        for K in result.ks:
            lines.append(f"| {K} | {_fmt_pct(a.page_hit.get(K))} | "
                         f"{_fmt_pct(a.all_group_hit.get(K))} | "
                         f"{_fmt_pct(a.required_page_coverage.get(K))} |")
        lines += ["", "### 失败题清单", ""]
        for fl in result.failures:
            lines.append(f"- **{fl.case_id}** — `{fl.primary}` — {fl.reason}")
        (tmp_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 5. inputs/ 快照
        for src_key, dst_name in [("dataset_path", "v1_baseline.jsonl"),
                                  ("corpus_manifest_path", "corpus_manifest.json")]:
            src = result.metadata.get(src_key)
            if src and Path(src).exists():
                shutil.copy2(src, inputs_dir / dst_name)
        (inputs_dir / "corpus_inventory.json").write_text(
            json.dumps(_jsonable(result.corpus_state), ensure_ascii=False, indent=2),
            encoding="utf-8")

        _os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    logger.info("Wrote V2 run artifacts to %s", final_dir)
    return final_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]):
    import argparse
    p = argparse.ArgumentParser(description="V2 Hybrid Retrieval Track A Runner")
    p.add_argument("--dataset", required=True)
    p.add_argument("--corpus-manifest", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--k", nargs="+", type=int, default=[1, 5, 10])
    p.add_argument("--ev-db", default="data/evidence.db")
    p.add_argument("--fin-db", default="data/financial_v2.db")
    p.add_argument("--chroma-dir", default="data/chroma_v2")
    p.add_argument("--sparse-dir", default="data/sparse_v2")
    p.add_argument("--manifest-dir", default="data/index_v2")
    p.add_argument("--output-root", default="evaluation/results")
    p.add_argument("--report-as-of", default="2024-12-31")
    p.add_argument("--validate-only", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = run_retrieval_v2(
            dataset_path=args.dataset, corpus_manifest_path=args.corpus_manifest,
            company_id=args.company, ks=args.k, ev_db_path=args.ev_db,
            fin_db_path=args.fin_db, chroma_dir=args.chroma_dir,
            sparse_dir=args.sparse_dir, manifest_dir=args.manifest_dir,
            output_root=args.output_root, report_as_of=args.report_as_of,
            validate_only=args.validate_only,
            frozen_eligibility=FROZEN_ELIGIBILITY_BREAKDOWN,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.validate_only:
        print(json.dumps({
            "status": result.status, "n_cases": result.validation.n_cases,
            "n_eligible": result.validation.n_eligible,
            "exclusion_breakdown": result.validation.exclusion_breakdown,
            "corpus": {"exists": result.corpus_state.exists,
                       "chunk_count": result.corpus_state.chunk_count},
        }, ensure_ascii=False, indent=2))
        return 0

    agg = result.aggregate
    print(f"run_id: {result.run_id}")
    print(f"status: {result.status}")
    print(f"eligible: {agg.n_eligible}/{agg.n_total}")
    print(f"RequiredPageCoverage@10 (Macro): {agg.required_page_coverage.get(10, 0.0):.1%}")
    print(f"P0 RequiredPageCoverage@10:      {_fmt_pct(agg.p0_required_page_coverage10)}")
    print(f"PageHit@10 (Macro): {agg.page_hit.get(10, 0.0):.1%}")
    print(f"AllGroupHit@10:     {agg.all_group_hit.get(10, 0.0):.1%}")
    print(f"MRR@10:             {agg.mrr10:.3f}")
    print(f"output: {result.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
