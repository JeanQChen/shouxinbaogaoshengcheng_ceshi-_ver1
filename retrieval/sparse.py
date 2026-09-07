"""Phase 2 Sparse（BM25）通道：中文字符二元组分词 + 本地 BM25 检索。

- 分词：NFKC、英文小写、空白规范；中文连续文本生成字符二元组，数字/日期/百分比/
  股票代码/英文缩写作为整词元保留；不新增 jieba 依赖（任务书 §3）。
- BM25 仅使用 Evidence text + 允许的 section metadata（不用 gold_answer）。
- 持久化词表、文档长度、倒排表、参数与指纹；重复构建幂等（同 index_version 跳过）。
- 查询返回 evidence_id / raw score / rank；空查询、损坏、版本不匹配明确失败。

index_version 与 Dense 通道共用（同源 evidence + 同组件版本），从 retrieval.indexer_v2
的确定性派生函数复用，保证 Dense/Sparse 身份一致。

CLI:
  python -m retrieval.sparse build --company 300750
  python -m retrieval.sparse search --company 300750 --query "实际控制人" --k 20
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from evidence import store as estore
from retrieval import indexer_v2

logger = logging.getLogger(__name__)

DEFAULT_SPARSE_DIR = Path("data/sparse_v2")
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

# 词元：CJK 连续段 | 字母数字连续段（可含小数/百分比后缀）。
_TOKEN_RE = re.compile(r"[一-鿿]+|[A-Za-z0-9]+(?:\.[0-9]+)?%?")
_CJK_RE = re.compile(r"[一-鿿]")


# ---------------------------------------------------------------------------
# 异常（retriever_v2 会映射为 failure_code）
# ---------------------------------------------------------------------------

class SparseError(Exception):
    """Sparse 通道错误基类。"""


class SparseIndexNotFound(SparseError):
    """sparse 索引未构建。"""


class SparseIndexVersionMismatch(SparseError):
    """sparse 索引版本与当前 evidence 不一致。"""


class SparseCorrupt(SparseError):
    """sparse 索引文件损坏/无法解析。"""


@dataclass
class SparseIndex:
    """一份已持久化的 BM25 索引。"""

    company_id: str
    index_version: str
    built_at: str
    params: dict                 # {"k1": .., "b": ..}
    record_count: int
    documents: list[dict]
    versions: dict
    fingerprint: str
    avg_doc_len: float
    doc_lengths: dict            # evidence_id -> 词元数
    vocab: dict                  # term -> df
    postings: dict               # term -> {evidence_id: tf}


@dataclass
class SparseHit:
    evidence_id: str
    score: float
    rank: int


# ---------------------------------------------------------------------------
# 分词（纯函数）
# ---------------------------------------------------------------------------

def _cjk_bigrams(s: str) -> list[str]:
    if len(s) == 1:
        return [s]
    return [s[i:i + 2] for i in range(len(s) - 1)]


def tokenize(text: str) -> list[str]:
    """NFKC → 英文小写 → 中文字符二元组 / 整词元。"""
    if not text:
        return []
    norm = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(norm):
        tok = m.group()
        if _CJK_RE.match(tok):
            tokens.extend(_cjk_bigrams(tok))
        else:
            tokens.append(tok.lower())
    return tokens


# ---------------------------------------------------------------------------
# 纯派生 / 打分
# ---------------------------------------------------------------------------

def _searchable_text(block) -> str:
    """Evidence text + 允许的 section metadata（路径）。"""
    section = " / ".join(block.section_path) if block.section_path else ""
    return f"{section} {block.text}".strip()


def _idf(n_docs: int, df: int) -> float:
    return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))


def _bm25_scores(index: SparseIndex, query_tokens: list[str]) -> dict[str, float]:
    """query_tokens → {evidence_id: bm25 原始分}。"""
    k1 = float(index.params.get("k1", DEFAULT_K1))
    b = float(index.params.get("b", DEFAULT_B))
    n_docs = len(index.doc_lengths)
    avg = max(float(index.avg_doc_len), 1e-9)
    scores: dict[str, float] = {}
    for t in query_tokens:
        post = index.postings.get(t)
        if not post:
            continue
        idf = _idf(n_docs, index.vocab.get(t, 0))
        for doc_id, tf in post.items():
            dl = float(index.doc_lengths.get(doc_id, 0))
            denom = tf + k1 * (1.0 - b + b * dl / avg)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * (k1 + 1.0)) / denom
    return scores


def bm25_search(index: SparseIndex, query: str, k: int) -> list[SparseHit]:
    """在已加载索引上检索 top-k（空查询 / 无可检索词元显式失败）。"""
    if k <= 0:
        raise ValueError("k 必须为正整数")
    if not query or not query.strip():
        raise ValueError("空查询")
    q_tokens = tokenize(query)
    if not q_tokens:
        raise ValueError("查询无可检索词元")
    scores = _bm25_scores(index, q_tokens)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [SparseHit(evidence_id=d, score=round(s, 6), rank=i + 1)
            for i, (d, s) in enumerate(ranked[:k])]


# ---------------------------------------------------------------------------
# 构建 + 持久化 + 加载
# ---------------------------------------------------------------------------

def _path(data_dir: Path, company_id: str) -> Path:
    return Path(data_dir) / f"{company_id}.json"


def current_index_version(company_id: str) -> str:
    """由当前 evidence 重新派生 index_version（与 Dense 同源同规则）。"""
    blocks, metas = indexer_v2._collect_current(company_id)
    versions = indexer_v2._component_versions()
    fp = indexer_v2._inventory_fingerprint(blocks)
    return indexer_v2.derive_index_version(company_id, metas, len(blocks), fp, versions)


def build_index(company_id: str, *, data_dir: Path = DEFAULT_SPARSE_DIR,
                k1: float = DEFAULT_K1, b: float = DEFAULT_B,
                force: bool = False) -> SparseIndex:
    """构建并持久化 BM25 索引；同 index_version 已存在时幂等跳过（force 重建）。"""
    blocks, metas = indexer_v2._collect_current(company_id)
    versions = indexer_v2._component_versions()
    fingerprint = indexer_v2._inventory_fingerprint(blocks)
    index_version = indexer_v2.derive_index_version(
        company_id, metas, len(blocks), fingerprint, versions)

    existing = _try_load(company_id, data_dir)
    if existing is not None and existing.index_version == index_version and not force:
        logger.info("sparse 索引已是最新（%s），跳过重建", index_version)
        return existing

    doc_lengths: dict[str, int] = {}
    postings: dict[str, dict[str, int]] = {}
    total_len = 0
    for blk in blocks:
        toks = tokenize(_searchable_text(blk))
        doc_lengths[blk.evidence_id] = len(toks)
        total_len += len(toks)
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        for t, c in tf.items():
            postings.setdefault(t, {})[blk.evidence_id] = c

    vocab = {t: len(docs) for t, docs in postings.items()}
    avg = (total_len / len(blocks)) if blocks else 0.0

    index = SparseIndex(
        company_id=company_id, index_version=index_version,
        built_at=indexer_v2._utcnow(), params={"k1": k1, "b": b},
        record_count=len(blocks), documents=metas, versions=versions,
        fingerprint=fingerprint, avg_doc_len=avg, doc_lengths=doc_lengths,
        vocab=vocab, postings=postings,
    )
    p = _path(data_dir, company_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(index), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    logger.info("built sparse index for %s: %d docs, %d terms (%s)",
                company_id, len(blocks), len(vocab), index_version)
    return index


def _try_load(company_id: str, data_dir: Path) -> SparseIndex | None:
    p = _path(data_dir, company_id)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return SparseIndex(**raw)
    except Exception as e:
        raise SparseCorrupt(f"sparse 索引损坏: {e}") from e


def load_index(company_id: str, data_dir: Path = DEFAULT_SPARSE_DIR) -> SparseIndex | None:
    """加载持久化索引；未构建返回 None；损坏抛 SparseCorrupt。"""
    return _try_load(company_id, data_dir)


# ---------------------------------------------------------------------------
# 对外检索（含版本校验）
# ---------------------------------------------------------------------------

def search(company_id: str, query: str, k: int = 20, *,
           data_dir: Path = DEFAULT_SPARSE_DIR,
           expected_version: str | None = None) -> list[SparseHit]:
    """加载索引并检索 top-k；版本不匹配 / 未构建 / 损坏显式失败。"""
    index = load_index(company_id, data_dir)
    if index is None:
        raise SparseIndexNotFound(f"sparse 索引未构建: {company_id}")
    if expected_version is None:
        expected_version = current_index_version(company_id)
    if index.index_version != expected_version:
        raise SparseIndexVersionMismatch(
            f"sparse 索引版本不匹配: {index.index_version} != {expected_version}")
    return bm25_search(index, query, k)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m retrieval.sparse",
        description="V2 Sparse（BM25）通道")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="构建/更新 BM25 索引")
    b.add_argument("--company", required=True, dest="company_id")
    b.add_argument("--data-dir", default=str(DEFAULT_SPARSE_DIR))
    b.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH))
    b.add_argument("--k1", type=float, default=DEFAULT_K1)
    b.add_argument("--b", type=float, default=DEFAULT_B)
    b.add_argument("--force", action="store_true")

    s = sub.add_parser("search", help="BM25 检索")
    s.add_argument("--company", required=True, dest="company_id")
    s.add_argument("--query", required=True)
    s.add_argument("--k", type=int, default=20)
    s.add_argument("--data-dir", default=str(DEFAULT_SPARSE_DIR))
    s.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH))

    args = parser.parse_args(argv)
    estore.init_db(args.ev_db)

    if args.cmd == "build":
        idx = build_index(args.company_id, data_dir=Path(args.data_dir),
                          k1=args.k1, b=args.b, force=args.force)
        print(json.dumps({
            "company_id": idx.company_id, "index_version": idx.index_version,
            "record_count": idx.record_count, "vocab_size": len(idx.vocab),
            "params": idx.params,
        }, ensure_ascii=False, indent=2))
        return 0

    hits = search(args.company_id, args.query, args.k, data_dir=Path(args.data_dir))
    print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
