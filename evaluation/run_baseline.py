"""V1 Retrieval Baseline Runner。

对当前 retrieval.retriever.retrieve() 逐题检索，评价正确本地文档页是否进入 Top-K。
- 仅调用现有 retrieve()，不改查询、不 query expansion、不调参。
- 每题只调用一次 k=max(ks)；K 指标为同一返回的前缀。
- 强制审计：每次调用在 logs/retrieval/baseline/<run_id>/ 落 started + succeeded/failed。
- 原子输出五类产物 + inputs/ 快照。

CLI:
  python -m evaluation.run_baseline --dataset ... --corpus-manifest ... \
      --company 300750 --collection company_docs --k 1 5 10
  python -m evaluation.run_baseline ... --validate-only
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
    Eligibility,
    RetrievedChunkSnapshot,
    RetrievalEvalCase,
)

logger = logging.getLogger(__name__)

LEGACY_LOG_DIR = Path("logs/retrieval")
AUDIT_ROOT = Path("logs/retrieval/baseline")

_VALID_KS = {1, 5, 10}


def _validate_ks(ks: list[int]) -> list[int]:
    ks = sorted(set(int(k) for k in ks))
    if not ks:
        raise ValueError("ks 不能为空")
    for k in ks:
        if k <= 0:
            raise ValueError(f"K 必须为正整数: {k}")
        if k > 20:
            raise ValueError(f"当前 V1 fetch 上限 20，拒绝 K>{k}")
    for req in (1, 5, 10):
        if req not in ks:
            raise ValueError(f"ks 必须包含 1/5/10，缺 {req}")
    return ks


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    dirty = bool(status.strip())
    return {"commit": commit, "dirty": dirty, "porcelain": status.strip()}


def _code_hashes() -> dict:
    paths = [
        "retrieval/retriever.py",
        "retrieval/indexer.py",
        "retrieval/embedding.py",
        "parsers/pdf_parser.py",
    ]
    out: dict = {}
    for p in paths:
        fp = Path(p)
        out[p] = _sha256_file(fp) if fp.exists() else "MISSING"
    return out


# ── 审计 ──

def _write_audit_line(path: Path, record: dict, mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _list_legacy_logs() -> set[str]:
    if not LEGACY_LOG_DIR.exists():
        return set()
    return {p.name for p in LEGACY_LOG_DIR.glob("*.jsonl")}


def _associate_legacy(before: set[str]) -> str:
    after = _list_legacy_logs()
    new = sorted(after - before)
    if len(new) == 1:
        return str(LEGACY_LOG_DIR / new[0])
    if len(new) == 0:
        return "legacy_log_missing"
    return "ambiguous:" + ",".join(new)


# ── 逐题运行 ──

def run_case(
    case: RetrievalEvalCase,
    eligibility: Eligibility,
    retriever_fn,
    ks: list[int],
    db_path: str,
    collection: str,
    company_id: str,
    audit_dir: Path,
) -> CaseResult:
    """对单题调用一次 retrieve(k=max(ks))，落强制审计。审计写失败会抛出（系统错误）。"""
    k = max(ks)
    call_id = uuid.uuid4().hex
    audit_path = audit_dir / f"{call_id}.jsonl"
    before = _list_legacy_logs()
    t0 = time.perf_counter()

    # started 必须先持久化（不包在 try 内：审计失败是系统错误）
    _write_audit_line(audit_path, {
        "event": "started", "call_id": call_id, "case_id": case.case_id,
        "query": case.question, "k": k, "timestamp": _now_iso(),
    }, "w")

    error: str | None = None
    snapshots: list[RetrievedChunkSnapshot] = []
    try:
        chunks = retriever_fn(company_id, collection, case.question, k=k, db_path=db_path)
        snapshots = [
            RetrievedChunkSnapshot(
                source_file=c.source_file,
                page_number=c.page_number,
                chunk_index=c.chunk_index,
                score=c.score,
                text_preview=c.text,
                source_type=c.source_type,
                section_title=c.section_title,
            )
            for c in chunks
        ]
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - t0) * 1000.0
    legacy_log = _associate_legacy(before)

    if error is not None:
        _write_audit_line(audit_path, {
            "event": "failed", "call_id": call_id, "error": error,
            "latency_ms": latency_ms, "legacy_log": legacy_log, "timestamp": _now_iso(),
        }, "a")
    else:
        _write_audit_line(audit_path, {
            "event": "succeeded", "call_id": call_id, "latency_ms": latency_ms,
            "legacy_log": legacy_log,
            "results": [
                {
                    "rank": i + 1, "source_file": c.source_file,
                    "page_number": c.page_number, "chunk_index": c.chunk_index,
                    "score": c.score, "source_type": c.source_type,
                    "section_title": c.section_title,
                }
                for i, c in enumerate(snapshots)
            ],
            "timestamp": _now_iso(),
        }, "a")

    return CaseResult(
        case_id=case.case_id,
        eligibility=eligibility,
        ks=list(ks),
        retrieved=snapshots,
        latency_ms=latency_ms,
        error=error,
        call_id=call_id,
        audit_path=str(audit_path),
        legacy_log=legacy_log,
    )


# ── metadata ──

def _model_info(model) -> dict:
    if model is None:
        return {"model_name": None, "use_fp16": None, "device": "not_loaded"}
    info: dict = {
        "model_name": getattr(model, "_model_name", "BAAI/bge-m3"),
        "use_fp16": getattr(model, "_use_fp16", True),
    }
    try:
        import torch
        if torch.cuda.is_available():
            info["device"] = "cuda"
        else:
            info["device"] = "cpu"
    except Exception:
        info["device"] = "unknown"
    return info


def _build_metadata(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str,
    corpus_fingerprint: str,
    model_load_ms: float | None = None,
    model=None,
) -> dict:
    return {
        "dataset_path": dataset_path,
        "dataset_sha256": _sha256_file(dataset_path),
        "corpus_manifest_path": corpus_manifest_path,
        "corpus_manifest_sha256": _sha256_file(corpus_manifest_path),
        "company_id": company_id,
        "collection": collection,
        "ks": ks,
        "db_path": db_path,
        "corpus_fingerprint": corpus_fingerprint,
        "model_load_ms": model_load_ms,
        "embedding_model": _model_info(model),
        "git": _git_state(),
        "code_hashes": _code_hashes(),
        "dependencies": _dependency_versions(),
        "generated_at": _now_iso(),
    }


def _dependency_versions() -> dict:
    out: dict = {}
    for name in ("chromadb", "pypdf", "pandas", "openpyxl", "FlagEmbedding", "anthropic"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", "unknown")
        except Exception:
            out[name] = "unavailable"
    return out


def _manifest_snapshot(manifest: CorpusManifest) -> dict:
    return {
        "company_id": manifest.company_id,
        "page_system": manifest.page_system,
        "page_system_note": manifest.page_system_note,
        "documents": [asdict(d) for d in manifest.documents],
    }


# ── 主入口 ──

def run_baseline(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
    validate_only: bool = False,
    retriever_fn=None,
) -> BaselineRunResult:
    from evaluation.dataset import load_corpus_manifest, load_dataset, validate_dataset
    from evaluation.failure_classifier import classify_eligibility, classify_failure
    from evaluation.metrics import aggregate_metrics, score_case
    from evaluation.page_mapping import inspect_corpus_readonly, resolve_gold_pages
    from evaluation.report_writer import write_run_artifacts

    ks = _validate_ks(ks)

    cases = load_dataset(dataset_path)
    manifest = load_corpus_manifest(corpus_manifest_path)
    validation = validate_dataset(cases, manifest)
    if validation.errors:
        raise ValueError("数据集校验失败: " + "; ".join(validation.errors))

    corpus_state = inspect_corpus_readonly(manifest, db_path, collection, company_id)
    if not corpus_state.exists:
        raise ValueError(f"collection 不存在: {corpus_state.collection_name}")

    # resolve + eligibility
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
        raise ValueError("没有任何 eligible case，无法运行 baseline")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    result = BaselineRunResult(
        run_id=run_id,
        company_id=company_id,
        collection=collection,
        ks=ks,
        output_dir=None,
        completed=False,
        status="pending",
        validation=validation,
        corpus_state=corpus_state,
        manifest_snapshot=_manifest_snapshot(manifest),
    )

    # 资格分布
    exclusion: dict[str, int] = {}
    for e in elig_by_id.values():
        exclusion[e.status] = exclusion.get(e.status, 0) + 1
    result.validation.exclusion_breakdown = exclusion
    result.validation.n_eligible = n_eligible

    if validate_only:
        result.status = "validated"
        result.completed = True
        return result

    # ── 正式运行 ──
    audit_dir = AUDIT_ROOT / run_id
    audit_dir.mkdir(parents=True, exist_ok=True)

    t_model0 = time.perf_counter()
    from retrieval.embedding import get_embedding_model
    model = get_embedding_model()
    # 触发 BGE-M3 权重实际加载，使初始加载耗时单列、逐题 latency 不含冷启动
    model.encode(["__baseline_warmup__"])
    model_load_ms = (time.perf_counter() - t_model0) * 1000.0

    from retrieval import retriever as retriever_module
    retriever = retriever_fn or retriever_module.retrieve

    # 逐题检索（仅 eligible）
    case_results: list[CaseResult] = []
    for case in cases:
        elig = elig_by_id[case.case_id]
        if elig.is_eligible:
            cr = run_case(case, elig, retriever, ks, db_path, collection, company_id, audit_dir)
        else:
            cr = CaseResult(
                case_id=case.case_id, eligibility=elig, ks=list(ks),
                retrieved=[], latency_ms=0.0, error=None,
            )
        case_results.append(cr)

    result_by_id = {r.case_id: r for r in case_results}

    # 计分
    case_metrics = [
        score_case(result_by_id[c.case_id], resolved_by_id[c.case_id], ks, manifest)
        for c in cases
    ]
    metric_by_id = {m.case_id: m for m in case_metrics}

    # 失败分类（排除题 + AllGroupHit@max(K) 未完成的 eligible 题）
    max_k = max(ks)
    failures = []
    for c in cases:
        cr = result_by_id[c.case_id]
        m = metric_by_id[c.case_id]
        if not cr.eligibility.is_eligible or cr.error or not m.all_group_hit.get(max_k):
            failures.append(classify_failure(
                c, resolved_by_id[c.case_id], cr, corpus_state, manifest,
            ))

    aggregate = aggregate_metrics(cases, case_results, case_metrics, ks)

    result.case_results = case_results
    result.case_metrics = case_metrics
    result.failures = failures
    result.aggregate = aggregate
    result.metadata = _build_metadata(
        dataset_path, corpus_manifest_path, company_id, collection, ks,
        db_path, corpus_state.fingerprint, model_load_ms, model,
    )
    result.completed = True
    result.status = (
        "completed_with_case_errors"
        if any(cr.error for cr in case_results)
        else "completed"
    )

    out_dir = write_run_artifacts(result, output_root, cases=cases, resolved_by_id=resolved_by_id)
    result.output_dir = str(out_dir)
    return result


# ── 重算（不重新检索）──

def _load_stored_case_results(path: str, ks: list[int]) -> dict[str, CaseResult]:
    """从已有 case_results.jsonl 重建 CaseResult（复用 Top-K，不再检索）。

    eligibility 仅占位，调用方会用 classify_eligibility 重算覆盖。
    """
    by_id: dict[str, CaseResult] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            retrieved = [
                RetrievedChunkSnapshot(
                    source_file=r["source_file"],
                    page_number=r["page_number"],
                    chunk_index=r.get("chunk_index", 0),
                    score=r.get("score", 0.0),
                    text_preview=r.get("text", ""),
                    source_type=r.get("source_type", ""),
                    section_title=r.get("section_title", ""),
                )
                for r in o.get("retrieved", [])
            ]
            by_id[o["case_id"]] = CaseResult(
                case_id=o["case_id"],
                eligibility=Eligibility(o["eligibility"]),
                ks=list(ks),
                retrieved=retrieved,
                latency_ms=o.get("latency_ms") or 0.0,
                error=o.get("error"),
                call_id=o.get("call_id"),
                audit_path=o.get("audit_path"),
                legacy_log=o.get("legacy_log"),
            )
    return by_id


def recompute_baseline(
    case_results_path: str,
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    collection: str,
    ks: list[int],
    db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
) -> BaselineRunResult:
    """从已有 case_results.jsonl 重算指标（不重新调用 Retriever）。

    仅重跑 score_case / aggregate_metrics / 失败分类；Top-K 检索结果原样复用。
    用于修正部分召回计分或调整 gold 页码后的重算，不改写原结果目录。
    """
    from evaluation.dataset import load_corpus_manifest, load_dataset, validate_dataset
    from evaluation.failure_classifier import classify_eligibility, classify_failure
    from evaluation.metrics import aggregate_metrics, score_case
    from evaluation.page_mapping import inspect_corpus_readonly, resolve_gold_pages
    from evaluation.report_writer import write_run_artifacts

    ks = _validate_ks(ks)

    cases = load_dataset(dataset_path)
    manifest = load_corpus_manifest(corpus_manifest_path)
    validation = validate_dataset(cases, manifest)
    if validation.errors:
        raise ValueError("数据集校验失败: " + "; ".join(validation.errors))

    corpus_state = inspect_corpus_readonly(manifest, db_path, collection, company_id)
    if not corpus_state.exists:
        raise ValueError(f"collection 不存在: {corpus_state.collection_name}")

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
        raise ValueError("没有任何 eligible case，无法重算")

    stored = _load_stored_case_results(case_results_path, ks)
    missing = [c.case_id for c in cases if c.case_id not in stored]
    if missing:
        raise ValueError("case_results.jsonl 缺题: " + ", ".join(missing))

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    result = BaselineRunResult(
        run_id=run_id,
        company_id=company_id,
        collection=collection,
        ks=ks,
        output_dir=None,
        completed=False,
        status="pending",
        validation=validation,
        corpus_state=corpus_state,
        manifest_snapshot=_manifest_snapshot(manifest),
    )

    exclusion: dict[str, int] = {}
    for e in elig_by_id.values():
        exclusion[e.status] = exclusion.get(e.status, 0) + 1
    result.validation.exclusion_breakdown = exclusion
    result.validation.n_eligible = n_eligible

    case_results: list[CaseResult] = []
    for case in cases:
        elig = elig_by_id[case.case_id]
        src = stored[case.case_id]
        case_results.append(CaseResult(
            case_id=case.case_id,
            eligibility=elig,
            ks=list(ks),
            retrieved=src.retrieved,
            latency_ms=src.latency_ms,
            error=src.error,
            call_id=src.call_id,
            audit_path=src.audit_path,
            legacy_log=src.legacy_log,
        ))

    result_by_id = {r.case_id: r for r in case_results}
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
                c, resolved_by_id[c.case_id], cr, corpus_state, manifest,
            ))

    aggregate = aggregate_metrics(cases, case_results, case_metrics, ks)

    result.case_results = case_results
    result.case_metrics = case_metrics
    result.failures = failures
    result.aggregate = aggregate
    result.metadata = _build_metadata(
        dataset_path, corpus_manifest_path, company_id, collection, ks,
        db_path, corpus_state.fingerprint, model_load_ms=None, model=None,
    )
    result.metadata["retrieval_performed"] = False
    result.metadata["recompute_from"] = case_results_path
    result.completed = True
    result.status = (
        "completed_with_case_errors"
        if any(cr.error for cr in case_results)
        else "completed"
    )

    out_dir = write_run_artifacts(result, output_root, cases=cases, resolved_by_id=resolved_by_id)
    result.output_dir = str(out_dir)
    return result


# ── CLI ──

def _parse_args(argv: list[str]):
    import argparse
    p = argparse.ArgumentParser(description="V1 Retrieval Baseline Runner")
    p.add_argument("--dataset", required=True)
    p.add_argument("--corpus-manifest", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--collection", default="company_docs")
    p.add_argument("--k", nargs="+", type=int, default=[1, 5, 10])
    p.add_argument("--db-path", default="data/chroma")
    p.add_argument("--output-root", default="evaluation/results")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--recompute-from", default=None,
                   help="从已有 case_results.jsonl 重算（不重新检索）")
    return p.parse_args(argv)


def _print_validate_summary(result: BaselineRunResult) -> None:
    v = result.validation
    cs = result.corpus_state
    print(json.dumps({
        "status": result.status,
        "n_cases": v.n_cases,
        "n_eligible": v.n_eligible,
        "exclusion_breakdown": v.exclusion_breakdown,
        "errors": v.errors,
        "warnings": v.warnings,
        "corpus": {
            "collection": cs.collection_name,
            "exists": cs.exists,
            "chunk_count": cs.chunk_count,
            "fingerprint": cs.fingerprint,
            "documents_indexed": {k: vv for k, vv in cs.documents_indexed.items()},
            "warnings": cs.warnings,
        },
    }, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.recompute_from:
            result = recompute_baseline(
                case_results_path=args.recompute_from,
                dataset_path=args.dataset,
                corpus_manifest_path=args.corpus_manifest,
                company_id=args.company,
                collection=args.collection,
                ks=args.k,
                db_path=args.db_path,
                output_root=args.output_root,
            )
        else:
            result = run_baseline(
                dataset_path=args.dataset,
                corpus_manifest_path=args.corpus_manifest,
                company_id=args.company,
                collection=args.collection,
                ks=args.k,
                db_path=args.db_path,
                output_root=args.output_root,
                validate_only=args.validate_only,
            )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.validate_only:
        _print_validate_summary(result)
        print("validate-only: OK")
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
