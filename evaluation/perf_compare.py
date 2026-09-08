"""同机同进程 V1 Dense vs V2 Hybrid 性能对照（warm P50/P95）。

任务书 §9 正式性能门：`v1_baseline_final` 是重算结果，其继承的 latency 不能作为
性能门的依据。必须在**同一台机器、同一进程、模型 warmup 后**，在冻结 37 问上分别
跑 V1 Dense 与 V2 Hybrid，输出 warm P50/P95，且 **V2 P95 ≤ 2× 同 run V1 P95**。

- 相同题集、交错顺序（每题先 V1 后 V2），模型只 warmup 一次（V1/V2 共享同一 BGE-M3 单例）；
- V1 Dense = `retrieval.retriever.retrieve(...)`（ChromaDB + BGE-M3 + 优先级加权）；
- V2 Hybrid = `retrieval.retriever_v2.RetrievalSession.retrieve(...)`（BM25 + Dense + RRF，
  固定 STANDARD_RAG 决策，de-Router）；
- 结果与环境信息单独落盘，不修改检索配置、不用旧 recalc latency 顶替。

CLI:
  python -m evaluation.perf_compare --dataset evaluation/datasets/v1_baseline.jsonl \
      --corpus-manifest evaluation/datasets/corpus_manifest.json --company 300750 --k 10
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 性能门（任务书 §9）：V2 Hybrid P95 不得慢过 2× 同 run V1 Dense P95。
GATE_RATIO_MAX = 2.0


def _sha256_file(path: str | Path) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percentile(values: list[float], q: float) -> float:
    """最近秩百分位（q∈[0,1]），空序列返回 0.0。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
    return s[idx]


def check_gate(v1_p95_ms: float, v2_p95_ms: float,
               ratio_max: float = GATE_RATIO_MAX) -> dict:
    """性能门判定：V2 P95 ≤ ratio_max × V1 P95（v1 为 0 时记 ratio=None，通过）。"""
    ratio = (v2_p95_ms / v1_p95_ms) if v1_p95_ms else None
    return {
        "v1_p95_ms": v1_p95_ms,
        "v2_p95_ms": v2_p95_ms,
        "ratio": ratio,
        "ratio_max": ratio_max,
        "pass": ratio is None or ratio <= ratio_max,
    }


def _env_info(model) -> dict:
    info: dict = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "embedding_model": getattr(model, "model_name", None)
        or getattr(model, "_model_name", None),
        "embedding_device": getattr(model, "device", None),
    }
    try:
        import torch
        info["torch_cuda_available"] = bool(torch.cuda.is_available())
        info["torch_device"] = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        info["torch_cuda_available"] = None
        info["torch_device"] = "unknown"
    return info


def run_perf_compare(
    dataset_path: str,
    corpus_manifest_path: str,
    company_id: str,
    k: int = 10,
    ev_db_path: str = "data/evidence.db",
    fin_db_path: str = "data/financial_v2.db",
    chroma_dir: str = "data/chroma_v2",
    sparse_dir: str = "data/sparse_v2",
    manifest_dir: str = "data/index_v2",
    v1_db_path: str = "data/chroma",
    output_root: str = "evaluation/results",
    report_as_of: str | None = "2024-12-31",
) -> dict:
    from evaluation import run_retrieval_v2 as track_a
    from evaluation.dataset import load_corpus_manifest, load_dataset, validate_dataset
    from evaluation.failure_classifier import classify_eligibility

    cases = load_dataset(dataset_path)
    manifest = load_corpus_manifest(corpus_manifest_path)
    validation = validate_dataset(cases, manifest)
    if validation.errors:
        raise ValueError("数据集校验失败: " + "; ".join(validation.errors))

    from evidence import store as estore
    from financial_v2 import store as fstore
    estore.init_db(ev_db_path)
    fstore.init_db(fin_db_path)

    corpus_state = track_a.inspect_evidence_corpus(manifest, company_id)
    eligible = [c for c in cases
                if classify_eligibility(c, manifest, corpus_state).is_eligible]
    if not eligible:
        raise ValueError("没有任何 eligible case，无法运行性能对照")

    # 模型 warmup：BGE-M3 只加载一次，V1/V2 共享同一单例。
    from retrieval.embedding import get_embedding_model
    model = get_embedding_model()
    model.encode(["__perf_warmup__"])

    # V2 Hybrid session（de-Router 固定 STANDARD_RAG）。
    from retrieval import retriever_v2
    session = retriever_v2.RetrievalSession(
        company_id, chroma_dir=Path(chroma_dir), sparse_dir=Path(sparse_dir),
        manifest_dir=Path(manifest_dir), model=model)
    context = track_a._build_context(company_id, report_as_of)

    from retrieval import retriever as v1_retriever

    def _v1(q: str) -> None:
        v1_retriever.retrieve(company_id, "company_docs", q, k=k, db_path=v1_db_path)

    def _v2(case) -> None:
        need = track_a._build_need(case)
        decision = track_a._fixed_local_decision(need)
        session.retrieve(need, decision, context)

    # 预热各跑一次首题（丢弃）：避免 DB 连接/首查冷启动计入 P50/P95。
    _v1(eligible[0].question)
    _v2(eligible[0])

    v1_lat: list[float] = []
    v2_lat: list[float] = []
    per_question: list[dict] = []
    errors: list[dict] = []

    for case in eligible:
        t0 = time.perf_counter()
        try:
            _v1(case.question)
            v1_ms = (time.perf_counter() - t0) * 1000.0
            v1_err = None
        except Exception as e:  # 单题失败仍记录 latency，不中断整轮
            v1_ms = (time.perf_counter() - t0) * 1000.0
            v1_err = f"{type(e).__name__}: {e}"

        t0 = time.perf_counter()
        try:
            _v2(case)
            v2_ms = (time.perf_counter() - t0) * 1000.0
            v2_err = None
        except Exception as e:
            v2_ms = (time.perf_counter() - t0) * 1000.0
            v2_err = f"{type(e).__name__}: {e}"

        v1_lat.append(v1_ms)
        v2_lat.append(v2_ms)
        per_question.append({"case_id": case.case_id, "v1_ms": v1_ms, "v2_ms": v2_ms})
        if v1_err or v2_err:
            errors.append({"case_id": case.case_id, "v1_error": v1_err,
                           "v2_error": v2_err})

    v1_p50 = _percentile(v1_lat, 0.5)
    v1_p95 = _percentile(v1_lat, 0.95)
    v2_p50 = _percentile(v2_lat, 0.5)
    v2_p95 = _percentile(v2_lat, 0.95)

    summary: dict = {
        "generated_at": _now_iso(),
        "company_id": company_id,
        "k": k,
        "n_questions": len(eligible),
        "dataset_path": dataset_path,
        "dataset_sha256": _sha256_file(dataset_path),
        "corpus_manifest_path": corpus_manifest_path,
        "corpus_manifest_sha256": _sha256_file(corpus_manifest_path),
        "v1": {
            "label": "V1 Dense (ChromaDB + BGE-M3 + priority boost)",
            "p50_ms": v1_p50,
            "p95_ms": v1_p95,
            "latencies_ms": v1_lat,
        },
        "v2": {
            "label": "V2 Hybrid (BM25 + BGE-M3 Dense + RRF)",
            "p50_ms": v2_p50,
            "p95_ms": v2_p95,
            "latencies_ms": v2_lat,
        },
        "gate": check_gate(v1_p95, v2_p95),
        "per_question": per_question,
        "errors": errors,
        "env": _env_info(model),
    }
    summary["output_dir"] = str(_write_perf_artifacts(summary, output_root))
    return summary


def _write_perf_artifacts(summary: dict, output_root: str | Path) -> Path:
    output_root = Path(output_root)
    out_dir = output_root / f"perf_compare_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir.mkdir(parents=True, exist_ok=False)

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    g = summary["gate"]
    v1, v2 = summary["v1"], summary["v2"]
    lines: list[str] = [
        "# V1 Dense vs V2 Hybrid 性能对照（warm P50/P95）",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- company_id: {summary['company_id']}",
        f"- k: {summary['k']}（{summary['n_questions']} 问）",
        f"- dataset_sha256: `{summary['dataset_sha256']}`",
        f"- corpus_manifest_sha256: `{summary['corpus_manifest_sha256']}`",
        f"- embedding_model: `{summary['env'].get('embedding_model')}`",
        f"- embedding_device: `{summary['env'].get('embedding_device')}`",
        f"- torch_device: `{summary['env'].get('torch_device')}`",
        "",
        "| 指标 | V1 Dense | V2 Hybrid |",
        "|---|---|---|",
        f"| P50 (ms) | {v1['p50_ms']:.1f} | {v2['p50_ms']:.1f} |",
        f"| P95 (ms) | {v1['p95_ms']:.1f} | {v2['p95_ms']:.1f} |",
        "",
        f"**性能门**：V2 P95 ≤ {g['ratio_max']:.1f}× V1 P95 → "
        f"{'**PASS**' if g['pass'] else '**FAIL**'} "
        f"(ratio={g['ratio'] if g['ratio'] is not None else 'n/a'})",
        "",
    ]
    if summary["errors"]:
        lines += ["### 失败题", ""]
        for e in summary["errors"]:
            lines.append(f"- `{e['case_id']}` v1={e['v1_error']} v2={e['v2_error']}")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info("Wrote perf comparison artifacts to %s", out_dir)
    return out_dir


def main(argv: list[str] | None = None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="V1 Dense vs V2 Hybrid 性能对照")
    p.add_argument("--dataset", required=True)
    p.add_argument("--corpus-manifest", required=True)
    p.add_argument("--company", required=True)
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--ev-db", default="data/evidence.db")
    p.add_argument("--fin-db", default="data/financial_v2.db")
    p.add_argument("--chroma-dir", default="data/chroma_v2")
    p.add_argument("--sparse-dir", default="data/sparse_v2")
    p.add_argument("--manifest-dir", default="data/index_v2")
    p.add_argument("--v1-db", default="data/chroma")
    p.add_argument("--output-root", default="evaluation/results")
    p.add_argument("--report-as-of", default="2024-12-31")
    args = p.parse_args(argv)

    try:
        summary = run_perf_compare(
            dataset_path=args.dataset, corpus_manifest_path=args.corpus_manifest,
            company_id=args.company, k=args.k, ev_db_path=args.ev_db,
            fin_db_path=args.fin_db, chroma_dir=args.chroma_dir,
            sparse_dir=args.sparse_dir, manifest_dir=args.manifest_dir,
            v1_db_path=args.v1_db, output_root=args.output_root,
            report_as_of=args.report_as_of)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    v1, v2, g = summary["v1"], summary["v2"], summary["gate"]
    print(f"n_questions: {summary['n_questions']}")
    print(f"V1 Dense:  P50={v1['p50_ms']:.1f}ms  P95={v1['p95_ms']:.1f}ms")
    print(f"V2 Hybrid: P50={v2['p50_ms']:.1f}ms  P95={v2['p95_ms']:.1f}ms")
    print(f"gate: V2 P95 <= {g['ratio_max']:.1f}x V1 P95 -> "
          f"{'PASS' if g['pass'] else 'FAIL'} (ratio={g['ratio'] if g['ratio'] is not None else 'n/a'})")
    print(f"output: {summary['output_dir']}")
    return 0 if g["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
