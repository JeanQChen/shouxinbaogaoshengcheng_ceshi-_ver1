"""Phase 2 Hybrid 融合：Reciprocal Rank Fusion（RRF），按 evidence_id 先聚合。

- 纯函数，无 I/O；输入是各通道按相关性排序的 evidence_id 列表（下标 0 = rank 1）。
- RRF 分数 = Σ_channel 1/(rrf_k + rank)，rrf_k=60（冻结默认）。
- 同一 evidence_id 跨通道先聚合（契约修正 5）：先按 evidence_id 合并各通道贡献，
  再排序；同一通道内重复 evidence_id 只计首次出现。
- 输出 evidence_id / rrf_score / rank；确定性排序（score 降序，evidence_id 升序
  打破平局）。

不引入 reranker（首轮不加入 reranker，任务书 §3/§9）。
"""

from __future__ import annotations

from dataclasses import dataclass

RRF_K = 60.0


@dataclass
class FusedHit:
    evidence_id: str
    rrf_score: float
    rank: int


def rrf_fuse(*channel_lists: list[str], rrf_k: float = RRF_K) -> list[FusedHit]:
    """按 evidence_id 聚合多通道排名，返回 RRF 排序结果。

    channel_lists：每个参数是一条通道的有序 evidence_id 列表（rank 由下标 +1 决定）。
    """
    if rrf_k <= 0:
        raise ValueError("rrf_k 必须为正数")
    scores: dict[str, float] = {}
    for lst in channel_lists:
        seen: set[str] = set()
        for rank, eid in enumerate(lst, start=1):
            if eid in seen:
                continue
            seen.add(eid)
            scores[eid] = scores.get(eid, 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [FusedHit(evidence_id=eid, rrf_score=s, rank=i + 1)
            for i, (eid, s) in enumerate(ranked)]


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m retrieval.fusion",
        description="RRF 融合（按 evidence_id 聚合多通道排名）")
    parser.add_argument("--dense", default="",
                        help="逗号分隔的 dense evidence_id 序列（rank 按顺序）")
    parser.add_argument("--sparse", default="",
                        help="逗号分隔的 sparse evidence_id 序列（rank 按顺序）")
    parser.add_argument("--rrf-k", type=float, default=RRF_K)
    args = parser.parse_args(argv)

    def _split(s: str) -> list[str]:
        return [x for x in s.split(",") if x]

    hits = rrf_fuse(_split(args.dense), _split(args.sparse), rrf_k=args.rrf_k)
    print(json.dumps([{"evidence_id": h.evidence_id, "rrf_score": h.rrf_score,
                       "rank": h.rank} for h in hits], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
