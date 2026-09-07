"""Phase 2 检索 trace 落盘（RAG 可观测性硬要求：每次 retrieve 落盘 logs/retrieval/）。

- RetrievalTrace 记录一次信息需求的完整检索链：route / query / 通道命中 / 融合结果 /
  状态 / failure_code / index_version，供 retriever_v2 回填 EvidencePack.retrieval_trace_id
  并落盘一条 JSONL。
- 文件名 <timestamp>__<query_hash>__<trace_id6>.jsonl：保留 query_hash 便于 grep，
  trace_id 短串避免同秒同 query 碰撞。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs/retrieval")


@dataclass
class RetrievalTrace:
    trace_id: str
    timestamp: str
    company_id: str
    need_id: str
    route: str
    query: str
    index_version: str | None
    status: str
    failure_code: str | None
    sparse_hits: list[dict] = field(default_factory=list)   # [{evidence_id, score, rank}]
    dense_hits: list[dict] = field(default_factory=list)    # [{evidence_id, score, rank}]
    fused: list[dict] = field(default_factory=list)         # [{evidence_id, rrf_score, rank}]
    returned_evidence_ids: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def write_trace(trace: RetrievalTrace, logs_dir: Path = LOGS_DIR) -> str:
    """落盘一条检索 trace 到 logs/retrieval/（JSONL），返回文件路径。"""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        qhash = hashlib.sha256((trace.query or "").encode()).hexdigest()[:12]
        tid = (trace.trace_id or "")[:6]
        path = logs_dir / f"{trace.timestamp or _now()}__{qhash}__{tid}.jsonl"
        path.write_text(json.dumps(asdict(trace), ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return str(path)
    except Exception:
        logger.warning("retrieval trace 落盘失败", exc_info=True)
        return ""
