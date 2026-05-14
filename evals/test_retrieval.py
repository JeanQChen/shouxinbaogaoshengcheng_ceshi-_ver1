"""Eval: retrieval 层（indexer + retriever + embedding）。

Mock embedding 模型，不加载 BGE-M3 权重。ChromaDB 用临时目录。

用法: python -m evals.test_retrieval
"""

import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DIM = 1024


class MockEmbeddingModel:
    """确定性 hash-based mock，不同文本 → 不同向量，避免加载 BGE-M3。"""

    dim = DIM

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vec = [0.0] * self.dim
            for i in range(min(32, self.dim)):
                vec[i] = (h[i] - 127.5) / 127.5
            # L2 normalize so cosine distance behaves
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ── Setup: inject mock embedding ──
    from retrieval import embedding as emb_module

    mock_emb = MockEmbeddingModel()
    _orig_model = getattr(emb_module, "_default_model", None)
    emb_module._default_model = mock_emb

    from retrieval.indexer import index_pdf
    from retrieval.retriever import retrieve, RetrievedChunk, LOGS_DIR
    from parsers.pdf_parser import TextChunk

    tmpdir = tempfile.mkdtemp(prefix="eval_retrieval_")
    chroma_path = os.path.join(tmpdir, "chroma")

    # Override log dir to temp for testing
    _orig_logs_dir = LOGS_DIR
    # Use a relative path trick: we can't easily override the module constant,
    # so we redirect by patching the module attribute.
    test_logs_dir = Path(tmpdir) / "logs" / "retrieval"

    # ── Test 1: EmbeddingModel class ──
    from retrieval.embedding import EmbeddingModel

    model = EmbeddingModel(model_name="test/model", use_fp16=False)
    check(model._model_name == "test/model", "EmbeddingModel stores model_name")
    check(model._use_fp16 is False, "EmbeddingModel stores use_fp16")
    check(model._model is None, "EmbeddingModel starts with _model=None (lazy)")

    # encode([]) should return [] without loading model
    check(model.encode([]) == [], "encode([]) returns []")

    # Mock: encode returns correct shape
    vecs = mock_emb.encode(["hello", "world"])
    check(len(vecs) == 2, "Mock encode returns 2 vectors for 2 texts")
    check(len(vecs[0]) == DIM, f"Mock vector dim == {DIM}")
    check(vecs[0] != vecs[1], "Different texts → different vectors")

    # ── Test 2: index_pdf ──
    chunks = [
        TextChunk(text="宁德时代是全球领先的新能源创新科技公司。", page_number=1, chunk_index=0),
        TextChunk(text="公司主要从事动力电池及储能电池的研发、生产及销售。", page_number=1, chunk_index=1),
        TextChunk(text="2025年度公司实现营业收入约4000亿元。", page_number=2, chunk_index=0),
    ]

    coll_name = index_pdf(
        chunks, company_id="300750", collection="company_docs",
        db_path=chroma_path, source_file="test.pdf",
    )
    check(coll_name == "company_docs__300750", f"Collection name correct: {coll_name}")

    # Verify collection exists in ChromaDB
    import chromadb
    client = chromadb.PersistentClient(path=chroma_path)
    coll = client.get_collection(name="company_docs__300750")
    check(coll.count() == 3, f"Collection has 3 documents (got {coll.count()})")

    # Verify metadata on stored chunks
    sample = coll.get(limit=1)
    check(len(sample["ids"]) == 1, "Can get documents from collection")
    check("page_number" in sample["metadatas"][0], "Metadata has page_number")

    # Empty chunks
    empty_name = index_pdf([], company_id="300750", collection="company_docs",
                           db_path=chroma_path)
    check(empty_name == "company_docs__300750",
          "Empty chunks returns collection name without error")

    # ── Test 3: retrieve ──
    results = retrieve(
        company_id="300750", collection="company_docs",
        query="宁德时代营业收入", k=2, db_path=chroma_path,
    )
    check(len(results) == 2, f"retrieve returns k=2 results (got {len(results)})")
    check(isinstance(results[0], RetrievedChunk), "Result is RetrievedChunk")
    check(results[0].page_number > 0, "RetrievedChunk has page_number")
    check(results[0].source_file == "test.pdf", "RetrievedChunk has source_file")
    check(isinstance(results[0].score, float), "Score is float")
    # Scores should be in reasonable range after cosine distance→similarity conversion
    check(-1.0 <= results[0].score <= 1.0,
          f"Score in [-1,1] (got {results[0].score:.4f})")

    # k larger than collection → returns all available
    results_all = retrieve(
        company_id="300750", collection="company_docs",
        query="电池", k=10, db_path=chroma_path,
    )
    check(len(results_all) == 3, f"k=10 on 3 docs returns 3 (got {len(results_all)})")

    # Empty query → ValueError
    try:
        retrieve(company_id="300750", collection="company_docs",
                 query="   ", k=2, db_path=chroma_path)
        check(False, "Empty query should raise ValueError")
    except ValueError:
        check(True, "Empty query raises ValueError")

    # Non-existent collection → ValueError
    try:
        retrieve(company_id="999999", collection="company_docs",
                 query="测试", k=2, db_path=chroma_path)
        check(False, "Non-existent collection should raise ValueError")
    except ValueError:
        check(True, "Non-existent collection raises ValueError")

    # ── Test 4: Logging side effect ──
    # Patch LOGS_DIR for this test
    emb_module._default_model = mock_emb
    # We need to redirect retriever's LOGS_DIR. Since it's imported as a module
    # constant, we patch the module attribute.
    import retrieval.retriever as ret_module
    _orig_ret_logs = ret_module.LOGS_DIR
    ret_module.LOGS_DIR = test_logs_dir

    retrieve(
        company_id="300750", collection="company_docs",
        query="动力电池", k=2, db_path=chroma_path,
    )
    log_files = list(test_logs_dir.glob("*.jsonl"))
    check(len(log_files) >= 1, f"Log file created (found {len(log_files)})")
    if log_files:
        log_content = json.loads(log_files[0].read_text(encoding="utf-8"))
        check("query" in log_content, "Log contains 'query'")
        check(log_content["query"] == "动力电池", "Log query matches")
        check("results" in log_content, "Log contains 'results'")
        check(len(log_content["results"]) == 2, "Log has 2 results")
        check("timestamp" in log_content, "Log has timestamp")
        check("collection" in log_content, "Log has collection")

    # ── Cleanup ──
    ret_module.LOGS_DIR = _orig_ret_logs
    emb_module._default_model = _orig_model

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
