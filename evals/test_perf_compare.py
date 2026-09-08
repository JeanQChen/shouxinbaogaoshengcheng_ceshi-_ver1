"""Eval: 性能对照纯函数（evaluation/perf_compare.py）—— Phase 2 收口。

用法: python -m evals.test_perf_compare

断言（纯函数，不加载 BGE-M3、不做检索）：
- _percentile 最近秩 P50/P95；
- check_gate：V2 P95 ≤ 2× V1 P95 通过 / 超 2× 失败 / V1 为 0 时 ratio=None 通过；
- 产物 JSON 结构（用 mock summary 验证 write 路径不抛异常）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import perf_compare as pc


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

    # ---- _percentile ----
    vals = list(range(1, 38))  # 1..37
    check(abs(pc._percentile(vals, 0.5) - 19.0) < 1e-9,
          f"P50（37 项）== 19（{pc._percentile(vals, 0.5)}）")
    check(abs(pc._percentile(vals, 0.95) - 36.0) < 1e-9,
          f"P95（37 项）== 36（{pc._percentile(vals, 0.95)}）")
    check(pc._percentile([], 0.5) == 0.0, "空序列 P50 == 0.0")

    # ---- check_gate ----
    g_pass = pc.check_gate(100.0, 150.0)
    check(g_pass["pass"] and abs(g_pass["ratio"] - 1.5) < 1e-9,
          "V2=150, V1=100 → ratio=1.5 通过")
    g_fail = pc.check_gate(100.0, 250.0)
    check(not g_fail["pass"] and abs(g_fail["ratio"] - 2.5) < 1e-9,
          "V2=250, V1=100 → ratio=2.5 失败")
    g_zero = pc.check_gate(0.0, 10.0)
    check(g_zero["pass"] and g_zero["ratio"] is None,
          "V1=0 → ratio=None 通过")

    # ---- 产物写路径（mock summary，不加载 BGE-M3）----
    summary = {
        "generated_at": "x", "company_id": "ACME", "k": 10, "n_questions": 2,
        "dataset_path": "d", "dataset_sha256": "d" * 64,
        "corpus_manifest_path": "c", "corpus_manifest_sha256": "c" * 64,
        "v1": {"label": "V1", "p50_ms": 1.0, "p95_ms": 2.0, "latencies_ms": [1.0, 2.0]},
        "v2": {"label": "V2", "p50_ms": 1.5, "p95_ms": 3.0, "latencies_ms": [1.5, 3.0]},
        "gate": pc.check_gate(2.0, 3.0),
        "per_question": [{"case_id": "A", "v1_ms": 1.0, "v2_ms": 1.5}],
        "errors": [],
        "env": {"platform": "test", "embedding_model": "BAAI/bge-m3",
                "embedding_device": "cpu", "torch_device": "cpu"},
    }
    out = pc._write_perf_artifacts(summary, tempfile.mkdtemp(prefix="eval_perf_"))
    check((out / "summary.json").exists() and (out / "report.md").exists(),
          "性能对照产物落盘（summary.json + report.md）")
    loaded = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    check(loaded["gate"]["pass"] is True, "summary.json 记录 gate 判定")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
