"""Phase 2 检索 trace 落盘（RAG 可观测性硬要求）。

- RetrievalTrace 记录一次信息需求的完整检索链：route / query / 通道命中 / 融合结果 /
  状态 / failure_code / index_version，供 retriever_v2 回填 EvidencePack.retrieval_trace_id
  并落盘一条 JSONL。
- V2 与 V1 分离：落盘到 logs/retrieval_v2/（V1 仍写 logs/retrieval/，互不污染）。
- 文件名 <timestamp>__<query_hash>__<trace_id6>.jsonl：保留 query_hash 便于 grep，
  trace_id 短串避免同秒同 query 碰撞。
- 落盘失败 fail-closed：抛 TraceWriteError（不静默吞错，retriever_v2 映射为
  failure_code=TRACE_WRITE_FAILED）。

契约修正 6 扩展字段（均可选，缺省 None/空，避免破坏既有调用方）：
  run_id / case_id、dataset/corpus manifest SHA256、Evidence inventory fingerprint、
  code/config fingerprint、RRF 完整排名、embedding model/device/首次加载耗时、
  filter 前后候选数、各阶段及总耗时、RSS（不可获取为 null 并说明）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs/retrieval_v2")


class TraceWriteError(Exception):
    """检索 trace 落盘失败（fail-closed）。"""


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

    # ---- 契约修正 6 扩展字段（可观测性） ----
    run_id: str | None = None
    case_id: str | None = None
    dataset_sha256: str | None = None
    corpus_manifest_sha256: str | None = None
    evidence_inventory_fingerprint: str | None = None
    code_config_fingerprint: str | None = None
    rrf_full_ranking: list[dict] = field(default_factory=list)  # 完整 RRF 排名（含未返回）
    embedding_model: str | None = None
    embedding_device: str | None = None
    embedding_first_load_ms: float | None = None
    filter_pre_count: int | None = None
    filter_post_count: int | None = None
    timings_ms: dict = field(default_factory=dict)   # {"sparse":..,"dense":..,"fusion":..,"total":..}
    rss_bytes: int | None = None
    rss_note: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def code_config_fingerprint(versions: dict, rule_version: str) -> str:
    """代码/配置指纹：组件版本 + 路由规则版本 的确定性 SHA256（前 32 位）。"""
    payload = {"versions": dict(sorted(versions.items())), "rule_version": rule_version}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def write_trace(trace: RetrievalTrace, logs_dir: Path = LOGS_DIR) -> str:
    """落盘一条检索 trace 到 logs/retrieval_v2/（JSONL），失败抛 TraceWriteError。"""
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        qhash = hashlib.sha256((trace.query or "").encode()).hexdigest()[:12]
        tid = (trace.trace_id or "")[:6]
        path = logs_dir / f"{trace.timestamp or _now()}__{qhash}__{tid}.jsonl"
        path.write_text(json.dumps(asdict(trace), ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return str(path)
    except Exception as e:
        raise TraceWriteError(f"retrieval trace 落盘失败: {e}") from e
