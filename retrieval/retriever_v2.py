"""Phase 2 V2 Hybrid Retriever：把 RouteDecision 执行成 EvidencePack。

- 会话（RetrievalSession）装载 current Evidence 块用于构造 EvidenceRef + 权威守卫；
  dense manifest / sparse 索引在 **Hybrid 路径才懒加载**（DB_LOOKUP 无需本地索引，
  公司有财务无 PDF evidence 时仍可取数）；
- 索引新鲜度校验：dense manifest + sparse 索引均须存在且 index_version 与当前 evidence
  派生版本一致，否则 fail-closed（IndexNotReady → FAILED pack）；
- 路由分派：
  - DB_LOOKUP            → StructuredResultRef（不伪造成 EvidenceRef，契约修正 C）；
  - DIRECT_EVIDENCE / STANDARD_RAG / DEEP_RETRIEVAL
                          → sparse BM25 + dense Chroma → RRF 按 evidence_id 融合 → EvidenceRef；
  - EXTERNAL_RESEARCH    → EXTERNAL_RESEARCH_NOT_IMPLEMENTED（首轮不做外部检索）；
- 每次检索落盘 trace 到 logs/retrieval/（RAG 可观测性硬要求），回填
  EvidencePack.retrieval_trace_id；
- EvidenceRef 由 current Evidence Block 权威字段构造，并用 validator 做 current /
  跨公司守卫。

要求调用方先 init 两个 Store（estore.init_db / fstore.init_db），与 indexer_v2 /
sparse 的调用约定一致。

CLI: python -m retrieval.retriever_v2 --company 300750 --question "实际控制人是谁"
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from evidence import store as estore
from financial_v2 import snapshots
from financial_v2 import store as fstore
from retrieval import fusion, indexer_v2, sparse
from retrieval import trace_v2
from retrieval.embedding import get_embedding_model
from routing import schema as S
from routing.validator import (
    validate_authority_company,
    validate_context,
    validate_decision,
    validate_need,
    validate_pack,
)

logger = logging.getLogger(__name__)

_AVAILABLE_METRIC_STATUSES = ("CALCULATED_EXACT", "CALCULATED_PROXY")


class RetrievalError(Exception):
    """retriever_v2 错误基类。"""


class IndexNotReady(RetrievalError):
    """索引缺失 / 版本不匹配（携带 failure_code）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class RetrievalSession:
    """一次检索会话：装载 current Evidence 块，Hybrid 路径懒加载索引并校验新鲜度。"""

    def __init__(self, company_id: str, *,
                 chroma_dir: Path | None = None,
                 sparse_dir: Path | None = None,
                 manifest_dir: Path | None = None,
                 model=None):
        self.company_id = company_id
        self.chroma_dir = Path(chroma_dir) if chroma_dir is not None else indexer_v2.DEFAULT_CHROMA_DIR
        self.sparse_dir = Path(sparse_dir) if sparse_dir is not None else sparse.DEFAULT_SPARSE_DIR
        self.manifest_dir = Path(manifest_dir) if manifest_dir is not None else indexer_v2.DEFAULT_MANIFEST_DIR
        self.model = model if model is not None else get_embedding_model()

        self.blocks_by_id: dict[str, object] = {}
        self._authority: dict[str, dict] = {}
        self.current_version: str = ""
        self.manifest: indexer_v2.IndexManifest | None = None
        self.sparse_index: sparse.SparseIndex | None = None

        self._load_current()

    # -- 装载 + 新鲜度 -------------------------------------------------------

    def _load_current(self) -> None:
        blocks, metas = indexer_v2._collect_current(self.company_id)
        self.blocks_by_id = {b.evidence_id: b for b in blocks}
        self._authority = {
            b.evidence_id: {
                "company_id": b.company_id,
                "document_id": b.document_id,
                "evidence_set_version": b.evidence_set_version,
                "is_current": True,
            }
            for b in blocks
        }
        versions = indexer_v2._component_versions()
        fp = indexer_v2._inventory_fingerprint(blocks)
        self.current_version = indexer_v2.derive_index_version(
            self.company_id, metas, len(blocks), fp, versions)

    def _ensure_indexes(self) -> None:
        """懒加载 dense manifest + sparse 索引并校验新鲜度（仅 Hybrid 路径需要）。"""
        if self.manifest is None or self.sparse_index is None:
            self.manifest = indexer_v2.load_manifest(self.company_id, self.manifest_dir)
            if self.manifest is None:
                raise IndexNotReady("INDEX_NOT_FOUND", f"dense manifest 缺失: {self.company_id}")
            self.sparse_index = sparse.load_index(self.company_id, self.sparse_dir)
            if self.sparse_index is None:
                raise IndexNotReady("INDEX_NOT_FOUND", f"sparse 索引缺失: {self.company_id}")
            if (self.manifest.index_version != self.current_version
                    or self.sparse_index.index_version != self.current_version):
                raise IndexNotReady(
                    "INDEX_VERSION_MISMATCH",
                    f"索引版本不匹配: dense={self.manifest.index_version}, "
                    f"sparse={self.sparse_index.index_version}, current={self.current_version}")

    # -- 主入口 --------------------------------------------------------------

    def retrieve(self, need: S.InformationNeed, decision: S.RouteDecision,
                 context: S.RouteContext) -> S.EvidencePack:
        validate_need(need)
        validate_decision(decision)
        validate_context(context)
        if need.need_id != decision.need_id:
            raise RetrievalError(
                f"need_id 与 decision.need_id 不一致: {need.need_id} != {decision.need_id}")

        if decision.route == "DB_LOOKUP":
            pack = self._db_lookup(need, decision, context)
        elif decision.route == "EXTERNAL_RESEARCH":
            pack = self._external(need, decision)
        elif decision.route in ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"):
            pack = self._hybrid(need, decision)
        else:
            pack = S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="UNSUPPORTED_ROUTE", retrieval_trace_id=uuid.uuid4().hex)

        validate_pack(pack, self._authority)
        validate_authority_company(pack, self.company_id, self._authority)
        return pack

    # -- DB_LOOKUP -----------------------------------------------------------

    def _db_lookup(self, need: S.InformationNeed, decision: S.RouteDecision,
                   context: S.RouteContext) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        try:
            results, missing = self._execute_db_target(decision, context)
        except Exception as e:  # 快照缺失等 → DB_SNAPSHOT_UNAVAILABLE（fail-closed）
            logger.warning("DB 执行失败: %s", e)
            trace_v2.write_trace(trace_v2.RetrievalTrace(
                trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
                need_id=need.need_id, route=decision.route, query=need.question,
                index_version=self.current_version, status="FAILED",
                failure_code="DB_SNAPSHOT_UNAVAILABLE"))
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="DB_SNAPSHOT_UNAVAILABLE", missing_requirements=[str(e)],
                retrieval_trace_id=trace_id)

        if results:
            trace_v2.write_trace(trace_v2.RetrievalTrace(
                trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
                need_id=need.need_id, route=decision.route, query=need.question,
                index_version=self.current_version, status="DB_RESULT_AVAILABLE",
                failure_code=None))
            return S.EvidencePack(
                need_id=need.need_id, status="DB_RESULT_AVAILABLE",
                route_decision=decision, structured_results=results,
                retrieval_trace_id=trace_id)
        return S.EvidencePack(
            need_id=need.need_id, status="DB_FIELD_UNAVAILABLE",
            route_decision=decision, missing_requirements=missing,
            retrieval_trace_id=trace_id)

    def _execute_db_target(self, decision: S.RouteDecision, context: S.RouteContext
                           ) -> tuple[list[S.StructuredResultRef], list[str]]:
        """执行 DB target：字段/指标 → StructuredResultRef（契约修正 C）。"""
        snap = self._current_snapshot(context)
        if snap is None:
            raise RetrievalError("无当前快照，DB 取数不可用")

        target_type = decision.filters["db_target_type"]
        if target_type == "field":
            code = decision.filters["standard_item_code"]
            for it in fstore.list_snapshot_items(snap.snapshot_id):
                if it.standard_item_code == code and it.amount is not None:
                    ref = S.StructuredResultRef(
                        result_type="financial_field", snapshot_id=snap.snapshot_id,
                        item_code=code, formula_id=None, formula_version=None,
                        period=it.report_period, raw_value=str(it.amount),
                        display_value=str(it.amount), unit=it.unit, status="available",
                        reason_code=None, input_record_refs=it.source_refs,
                        input_snapshot_item_refs=[it.comparison_key])
                    return [ref], []
            return [], [f"字段不可用: {code}"]

        formula_id = decision.filters["formula_id"]
        for mr in fstore.list_metric_results(snap.snapshot_id):
            if mr.formula_id == formula_id and mr.status in _AVAILABLE_METRIC_STATUSES:
                ref = S.StructuredResultRef(
                    result_type="financial_metric", snapshot_id=snap.snapshot_id,
                    item_code=None, formula_id=formula_id,
                    formula_version=mr.formula_version, period=mr.period,
                    raw_value=str(mr.raw_value) if mr.raw_value is not None else None,
                    display_value=str(mr.display_value) if mr.display_value is not None else None,
                    unit=mr.unit, status=mr.status, reason_code=mr.reason_code,
                    input_record_refs=mr.input_record_refs,
                    input_snapshot_item_refs=mr.input_snapshot_item_refs)
                return [ref], []
        return [], [f"指标不可用: {formula_id}"]

    def _current_snapshot(self, context: S.RouteContext):
        if context.report_as_of is None:
            return None
        return snapshots.current_snapshot(
            context.company_id, scope="consolidated", currency="CNY",
            as_of_date=context.report_as_of, purpose="credit_analysis")

    # -- EXTERNAL_RESEARCH ---------------------------------------------------

    def _external(self, need: S.InformationNeed, decision: S.RouteDecision) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        trace_v2.write_trace(trace_v2.RetrievalTrace(
            trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
            need_id=need.need_id, route=decision.route, query=need.question,
            index_version=self.current_version, status="EXTERNAL_RESEARCH_NOT_IMPLEMENTED",
            failure_code=None))
        return S.EvidencePack(
            need_id=need.need_id, status="EXTERNAL_RESEARCH_NOT_IMPLEMENTED",
            route_decision=decision, missing_requirements=["外部检索未实现（首轮不启用）"],
            retrieval_trace_id=trace_id)

    # -- Hybrid（sparse + dense → RRF） --------------------------------------

    def _hybrid(self, need: S.InformationNeed, decision: S.RouteDecision) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        try:
            self._ensure_indexes()
        except IndexNotReady as e:
            trace_v2.write_trace(trace_v2.RetrievalTrace(
                trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
                need_id=need.need_id, route=decision.route, query=need.question,
                index_version=self.current_version, status="FAILED", failure_code=e.code))
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code=e.code, retrieval_trace_id=trace_id)

        budget = decision.budget
        q = need.question

        sparse_hits: list[dict] = []
        dense_hits: list[dict] = []
        failures: list[str] = []
        try:
            sparse_hits = [{"evidence_id": h.evidence_id, "score": h.score, "rank": h.rank}
                           for h in sparse.bm25_search(self.sparse_index, q,
                                                       budget.candidate_k_sparse)]
        except Exception as e:
            logger.warning("sparse 检索失败: %s", e)
            failures.append("SPARSE_FAILED")
        try:
            dense_hits = self._dense_search(q, budget.candidate_k_dense)
        except Exception as e:
            logger.warning("dense 检索失败: %s", e)
            failures.append("DENSE_FAILED")

        sparse_ids = [h["evidence_id"] for h in sparse_hits]
        dense_ids = [h["evidence_id"] for h in dense_hits]
        fused = fusion.rrf_fuse(sparse_ids, dense_ids)[:budget.fusion_k]

        sparse_rank = {h["evidence_id"]: h["rank"] for h in sparse_hits}
        dense_rank = {h["evidence_id"]: h["rank"] for h in dense_hits}

        evidence: list[S.EvidenceRef] = []
        for fh in fused[:budget.context_k]:
            ref = self._build_ref(fh, sparse_rank, dense_rank)
            if ref is not None:
                evidence.append(ref)

        trace_v2.write_trace(trace_v2.RetrievalTrace(
            trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
            need_id=need.need_id, route=decision.route, query=q,
            index_version=self.current_version, status="", failure_code=None,
            sparse_hits=sparse_hits, dense_hits=dense_hits,
            fused=[{"evidence_id": f.evidence_id, "rrf_score": f.rrf_score, "rank": f.rank}
                   for f in fused],
            returned_evidence_ids=[r.evidence_id for r in evidence]))

        if evidence:
            status = "PARTIAL" if failures else "COMPLETED"
            failure_code = failures[0] if failures else None
        elif failures:
            status = "FAILED" if len(failures) == 2 else "PARTIAL"
            failure_code = "BOTH_CHANNELS_FAILED" if len(failures) == 2 else failures[0]
        else:
            status, failure_code = "EMPTY", None

        return S.EvidencePack(
            need_id=need.need_id, status=status, route_decision=decision,
            evidence=evidence, failure_code=failure_code, retrieval_trace_id=trace_id)

    def _dense_search(self, query: str, k: int) -> list[dict]:
        import chromadb

        client = chromadb.PersistentClient(path=str(self.chroma_dir))
        coll = client.get_collection(name=self.manifest.collection)
        q_emb = self.model.encode([query])[0]
        res = coll.query(query_embeddings=[q_emb], n_results=k)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return [{"evidence_id": ids[i], "score": round(1.0 - dists[i], 6), "rank": i + 1}
                for i in range(len(ids))]

    def _build_ref(self, fh: fusion.FusedHit, sparse_rank: dict, dense_rank: dict
                   ) -> S.EvidenceRef | None:
        blk = self.blocks_by_id.get(fh.evidence_id)
        if blk is None:
            logger.warning("融合命中但无 current 块: %s", fh.evidence_id)
            return None
        channels: list[str] = []
        channel_ranks: dict[str, int] = {}
        if fh.evidence_id in sparse_rank:
            channels.append("sparse")
            channel_ranks["sparse"] = sparse_rank[fh.evidence_id]
        if fh.evidence_id in dense_rank:
            channels.append("dense")
            channel_ranks["dense"] = dense_rank[fh.evidence_id]
        page = blk.page_number if (blk.page_number or 0) > 0 else None
        return S.EvidenceRef(
            evidence_id=blk.evidence_id, document_id=blk.document_id,
            evidence_set_version=blk.evidence_set_version, source_name=blk.source_name,
            source_type=blk.source_type, page_number=page, evidence_type=blk.evidence_type,
            text=blk.text, structured_payload=blk.structured_payload,
            score=fh.rrf_score, rank=fh.rank, retrieval_channels=channels,
            channel_ranks=channel_ranks)


# ---------------------------------------------------------------------------
# 便捷入口 + CLI
# ---------------------------------------------------------------------------

def retrieve(need: S.InformationNeed, decision: S.RouteDecision, context: S.RouteContext,
             *, chroma_dir: Path | None = None, sparse_dir: Path | None = None,
             manifest_dir: Path | None = None, model=None) -> S.EvidencePack:
    """单次检索便捷入口：构建会话并执行（每调用新建会话，适合 CLI / 单 need）。"""
    session = RetrievalSession(
        context.company_id, chroma_dir=chroma_dir, sparse_dir=sparse_dir,
        manifest_dir=manifest_dir, model=model)
    return session.retrieve(need, decision, context)


def _main(argv: list[str]) -> int:
    import argparse
    import json

    from routing import context as routing_context
    from routing import router as routing_router

    parser = argparse.ArgumentParser(
        prog="python -m retrieval.retriever_v2",
        description="V2 Hybrid Retriever（RouteDecision → EvidencePack）")
    parser.add_argument("--company", required=True, dest="company_id")
    parser.add_argument("--question", required=True)
    parser.add_argument("--chroma-dir", default=str(indexer_v2.DEFAULT_CHROMA_DIR))
    parser.add_argument("--sparse-dir", default=str(sparse.DEFAULT_SPARSE_DIR))
    parser.add_argument("--manifest-dir", default=str(indexer_v2.DEFAULT_MANIFEST_DIR))
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH))
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH))
    args = parser.parse_args(argv)

    fstore.init_db(args.fin_db)
    estore.init_db(args.ev_db)

    context = routing_context.build_route_context(args.company_id)
    need = S.InformationNeed(
        need_id="cli", section_id="cli", question=args.question,
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="normal", depends_on=[])
    result = routing_router.route(need, context)
    if result.status != "DECIDED":
        print(json.dumps({"status": result.status, "error_code": result.error_code},
                         ensure_ascii=False, indent=2))
        return 1

    pack = retrieve(need, result.decision, context,
                    chroma_dir=Path(args.chroma_dir), sparse_dir=Path(args.sparse_dir),
                    manifest_dir=Path(args.manifest_dir))
    print(json.dumps({
        "need_id": pack.need_id, "status": pack.status, "failure_code": pack.failure_code,
        "route": pack.route_decision.route if pack.route_decision else None,
        "evidence_count": len(pack.evidence),
        "structured_count": len(pack.structured_results),
        "evidence_ids": [r.evidence_id for r in pack.evidence],
        "missing_requirements": pack.missing_requirements,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
