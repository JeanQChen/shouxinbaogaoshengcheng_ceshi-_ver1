"""Eval: V2 Hybrid 融合（retrieval/fusion.py）—— Phase 2 Commit 6。

用法: python -m evals.test_fusion

覆盖（任务书 §7 / 契约修正 5）：
- RRF 按 evidence_id 先聚合（跨通道合并贡献）；
- rrf_k=60 冻结默认；rank 1 贡献 1/(k+1)；
- 空输入 / 单通道 / 多通道重叠 / 通道内重复 / 确定性排序。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval import fusion


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

    # ---- 空输入 ----
    check(fusion.rrf_fuse() == [], "无通道 → 空结果")
    check(fusion.rrf_fuse([], []) == [], "双空通道 → 空结果")

    # ---- 单通道：RRF 分数 = 1/(k+rank) ----
    hits = fusion.rrf_fuse(["a", "b", "c"])
    check([h.evidence_id for h in hits] == ["a", "b", "c"], "单通道排名顺序保留")
    check(abs(hits[0].rrf_score - 1.0 / 61.0) < 1e-9, "rank1 分数 = 1/(60+1)")
    check(abs(hits[1].rrf_score - 1.0 / 62.0) < 1e-9, "rank2 分数 = 1/(60+2)")
    check(hits[0].rank == 1 and hits[2].rank == 3, "rank 从 1 递增")

    # ---- 跨通道聚合（契约修正 5）：同 evidence_id 合并贡献 ----
    dense = ["a", "b", "c"]
    sparse = ["b", "a", "d"]
    merged = fusion.rrf_fuse(dense, sparse)
    merged_map = {h.evidence_id: h.rrf_score for h in merged}
    check(set(merged_map) == {"a", "b", "c", "d"}, "并集覆盖全部 evidence_id")
    # a: 1/61 + 1/62 ; b: 1/62 + 1/61 → 相等，按 evidence_id 升序打破平局
    check(abs(merged_map["a"] - (1 / 61 + 1 / 62)) < 1e-9, "a 聚合两份贡献")
    check(abs(merged_map["b"] - (1 / 61 + 1 / 62)) < 1e-9, "b 聚合两份贡献")
    check(merged[0].evidence_id == "a" and merged[1].evidence_id == "b",
          "平局时 evidence_id 升序稳定")

    # ---- 通道内重复 evidence_id 只计首次 ----
    dup = fusion.rrf_fuse(["a", "a", "b"])
    check([h.evidence_id for h in dup] == ["a", "b"], "通道内重复去重")
    check(abs(dup[0].rrf_score - 1 / 61) < 1e-9, "重复项只按首次 rank 计一次")

    # ---- 重叠证据排序提升 ----
    only_dense = fusion.rrf_fuse(["x", "y"])
    both = fusion.rrf_fuse(["x", "y"], ["y"])
    both_map = {h.evidence_id: h.rrf_score for h in both}
    check(both_map["y"] > only_dense[1].rrf_score, "跨通道命中证据分数提升")

    # ---- 确定性 ----
    check(fusion.rrf_fuse(["a", "b"], ["b", "a"]) == fusion.rrf_fuse(["a", "b"], ["b", "a"]),
          "同输入同输出（确定性）")

    # ---- rrf_k 参数 ----
    k60 = fusion.rrf_fuse(["a"], rrf_k=60.0)[0].rrf_score
    k10 = fusion.rrf_fuse(["a"], rrf_k=10.0)[0].rrf_score
    check(k60 < k10, "rrf_k 越小 rank1 权重越大")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
