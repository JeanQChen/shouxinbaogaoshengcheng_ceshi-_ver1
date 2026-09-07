"""Phase 2 V2 Dense 索引器：只索引公司 current、健康 Evidence Set 到独立版本化 collection。

与 V1 `retrieval/indexer.py` 的区别（不修改 V1 行为）：
- 不写 V1 collection（`company_docs__<id>`），写 `v2_dense__<id>__<index_version>`；
- 索引对象是 Evidence Store 的 current evidence block，metadata 保留
  evidence/company/document/document_version/evidence_set/source/page/type/section/
  period/hash；
- `index_version` 由输入确定性派生（公司/文档+证据集版本/记录数/库存指纹/组件版本），
  **排除 built_at**（契约修正 3）：同一输入重建得到同一版本；built_at 只记入 manifest，
  不参与版本；
- 每次写 manifest（`data/index_v2/<company>.json`），供 retriever_v2 / sparse 定位并
  校验 index_version；旧/current Evidence 因版本化 collection 天然不混装。

CLI: python -m retrieval.indexer_v2 --company 300750 --build
     python -m retrieval.indexer_v2 --company 300750 --inspect
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from evidence import schema as ev_schema
from evidence import store as estore
from retrieval.embedding import get_embedding_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 组件版本（参与 index_version；实现变更需递增）
# ---------------------------------------------------------------------------

INDEXER_VERSION = "1"
EMBEDDING_VERSION = "bge-m3"     # BGE-M3，1024 维稠密向量
TOKENIZER_VERSION = "1"          # retrieval/sparse.py 使用；manifest 冻结其版本

INDEX_PREFIX = "v2_dense"
DEFAULT_CHROMA_DIR = Path("data/chroma_v2")
DEFAULT_MANIFEST_DIR = Path("data/index_v2")
_BATCH_SIZE = 256


@dataclass
class IndexManifest:
    """一次 V2 Dense 索引的冻结清单（built_at 记录但不参与 index_version）。"""

    company_id: str
    index_version: str
    collection: str
    record_count: int
    built_at: str
    documents: list[dict]        # [{document_id, document_version, evidence_set_version}]
    versions: dict               # 组件版本
    inventory_fingerprint: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 纯派生（无 I/O，可独立测试）
# ---------------------------------------------------------------------------

def _inventory_fingerprint(blocks) -> str:
    """evidence_id+content_hash 排序后的稳定指纹：内容或数量变化即变。"""
    parts = sorted(f"{b.evidence_id}:{b.content_hash}" for b in blocks)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _component_versions() -> dict:
    return {
        "evidence_schema": ev_schema.SCHEMA_VERSION,
        "parser": ev_schema.PARSER_VERSION,
        "builder": ev_schema.BUILDER_VERSION,
        "indexer": INDEXER_VERSION,
        "tokenizer": TOKENIZER_VERSION,
        "embedding": EMBEDDING_VERSION,
    }


def derive_index_version(company_id: str, documents: list[dict], record_count: int,
                         inventory_fingerprint: str, versions: dict) -> str:
    """确定性 index_version（签名不含 built_at）。"""
    payload = {
        "company_id": company_id,
        "documents": sorted(documents, key=lambda d: d["document_id"]),
        "record_count": record_count,
        "inventory_fingerprint": inventory_fingerprint,
        "versions": dict(sorted(versions.items())),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _block_metadata(block) -> dict:
    """EvidenceBlock → Chroma metadata（全部标量，兼容 Chroma 存储约束）。"""
    return {
        "evidence_id": block.evidence_id,
        "company_id": block.company_id,
        "document_id": block.document_id,
        "document_version": block.document_version,
        "evidence_set_version": block.evidence_set_version,
        "source_name": block.source_name,
        "source_type": block.source_type,
        "page_number": int(block.page_number or 0),
        "section": " / ".join(block.section_path) if block.section_path else "",
        "evidence_type": block.evidence_type,
        "report_period": block.report_period or "",
        "content_hash": block.content_hash,
    }


# ---------------------------------------------------------------------------
# 只读数据源（Evidence Store）
# ---------------------------------------------------------------------------

def _collect_current(company_id: str) -> tuple[list, list[dict]]:
    """收集 current、健康 Evidence Set 的全部证据块 + 文档清单。

    排除 registered/superseded 文档、retired/invalid evidence set；只取每份文档
    当前版本 + 当前证据集。
    """
    blocks: list = []
    metas: list[dict] = []
    seen: set[str] = set()
    for d in estore.list_documents(company_id):
        if d.status != "current" or d.document_id in seen:
            continue
        seen.add(d.document_id)
        dv = estore.current_document_version(company_id, d.document_id)
        if dv is None:
            continue
        esv = estore.current_evidence_set(company_id, d.document_id, dv)
        if esv is None:
            continue
        metas.append({"document_id": d.document_id, "document_version": dv,
                      "evidence_set_version": esv})
        blocks.extend(estore.list_document_evidence(company_id, d.document_id, dv, esv))
    return blocks, metas


# ---------------------------------------------------------------------------
# 索引构建 + manifest
# ---------------------------------------------------------------------------

def _manifest_path(manifest_dir: Path, company_id: str) -> Path:
    return Path(manifest_dir) / f"{company_id}.json"


def build_index(company_id: str, *, db_path: Path = DEFAULT_CHROMA_DIR,
                manifest_dir: Path = DEFAULT_MANIFEST_DIR,
                model=None) -> IndexManifest:
    """索引公司 current Evidence 到独立版本化 Dense collection，写 manifest。

    model 缺省走 BGE-M3 单例；eval 可注入 mock embedding。
    """
    import chromadb

    blocks, metas = _collect_current(company_id)
    versions = _component_versions()
    fingerprint = _inventory_fingerprint(blocks)
    index_version = derive_index_version(company_id, metas, len(blocks), fingerprint, versions)
    collection = f"{INDEX_PREFIX}__{company_id}__{index_version}"

    client = chromadb.PersistentClient(path=str(db_path))
    coll = client.get_or_create_collection(name=collection,
                                           metadata={"hnsw:space": "cosine"})

    if blocks:
        embedding = model if model is not None else get_embedding_model()
        texts = [b.text for b in blocks]
        ids = [b.evidence_id for b in blocks]
        metadatas = [_block_metadata(b) for b in blocks]
        embeddings = embedding.encode(texts)
        for start in range(0, len(texts), _BATCH_SIZE):
            end = min(start + _BATCH_SIZE, len(texts))
            coll.add(embeddings=embeddings[start:end], documents=texts[start:end],
                     metadatas=metadatas[start:end], ids=ids[start:end])

    manifest = IndexManifest(
        company_id=company_id, index_version=index_version, collection=collection,
        record_count=len(blocks), built_at=_utcnow(), documents=metas,
        versions=versions, inventory_fingerprint=fingerprint)
    _write_manifest(manifest, manifest_dir)
    logger.info("indexed %d blocks -> %s (index_version=%s)",
                len(blocks), collection, index_version)
    return manifest


def _write_manifest(manifest: IndexManifest, manifest_dir: Path) -> None:
    p = _manifest_path(manifest_dir, manifest.company_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
                 encoding="utf-8")


def load_manifest(company_id: str, manifest_dir: Path = DEFAULT_MANIFEST_DIR
                  ) -> IndexManifest | None:
    p = _manifest_path(manifest_dir, company_id)
    if not p.exists():
        return None
    return IndexManifest(**json.loads(p.read_text(encoding="utf-8")))


def inspect(company_id: str, *, db_path: Path = DEFAULT_CHROMA_DIR,
            manifest_dir: Path = DEFAULT_MANIFEST_DIR) -> dict:
    m = load_manifest(company_id, manifest_dir)
    if m is None:
        return {"company_id": company_id, "indexed": False}
    import chromadb
    count: int | None = None
    try:
        client = chromadb.PersistentClient(path=str(db_path))
        count = client.get_collection(name=m.collection).count()
    except Exception as e:  # collection 缺失 → 记 null 而非崩溃
        logger.warning("inspect 读取 collection 失败: %s", e)
    return {"company_id": company_id, "indexed": True, **asdict(m),
            "collection_block_count": count}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m retrieval.indexer_v2",
        description="V2 Dense 索引器（current Evidence Set → 版本化 collection）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--db", default=str(DEFAULT_CHROMA_DIR),
                        help="ChromaDB 目录（dev/test 注入临时目录）")
    parser.add_argument("--manifest-dir", default=str(DEFAULT_MANIFEST_DIR))
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH),
                        help="Evidence SQLite 库路径（dev/test 注入临时库）")
    args = parser.parse_args(argv)

    estore.init_db(args.ev_db)

    if args.build:
        m = build_index(args.company_id, db_path=Path(args.db),
                        manifest_dir=Path(args.manifest_dir))
        print(json.dumps(asdict(m), ensure_ascii=False, indent=2))
        return 0
    if args.inspect:
        print(json.dumps(inspect(args.company_id, db_path=Path(args.db),
                                 manifest_dir=Path(args.manifest_dir)),
                         ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
