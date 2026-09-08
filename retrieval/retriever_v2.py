"""Phase 2 V2 Hybrid Retriever：把 RouteDecision 执行成 EvidencePack。

- 会话（RetrievalSession）装载 current Evidence 块用于构造 EvidenceRef + 权威守卫；
  dense manifest / sparse 索引在 **Hybrid 路径才懒加载**（DB_LOOKUP 无需本地索引，
  公司有财务无 PDF evidence 时仍可取数）；BGE-M3 同样懒加载（仅 Dense 通道触发）。
- 索引新鲜度校验：dense manifest + sparse 索引均须存在且 index_version 与当前 evidence
  派生版本一致，否则 fail-closed（IndexNotReady → FAILED pack）；
- 路由分派：
  - DB_LOOKUP            → StructuredResultRef（不伪造成 EvidenceRef，契约修正 C），
                           按 decision.filters 的 snapshot_as_of_date / target_period /
                           scope / currency / purpose / formula_version 精确取数（契约修正 2）；
  - DIRECT_EVIDENCE / STANDARD_RAG / DEEP_RETRIEVAL
                          → sparse BM25 + dense Chroma（metadata filter）→ RRF 按
                            evidence_id 融合 → EvidenceRef；两通道并发 + 统一 deadline；
  - EXTERNAL_RESEARCH    → EXTERNAL_RESEARCH_NOT_IMPLEMENTED（首轮不做外部检索）；
- 软超时熔断（契约修正 5/6 + 额外点 2）：Sparse/Dense 并发启动、统一 deadline；超时后
  executor 不等待孤儿任务（shutdown wait=False），RetrievalSession 标记 poisoned；
  当前题按既有规则返回 PARTIAL/FAILED 并完整落盘 trace；poisoned Session 拒绝后续
  retrieve；不新建线程池掩盖孤儿任务。
- 每次检索（含 DB/EXTERNAL/失败/熔断）先定最终 status，再落盘 trace 到
  logs/retrieval_v2/（V2 与 V1 分离）；落盘失败 fail-closed → TRACE_WRITE_FAILED。
- EvidenceRef 由 current Evidence Block 权威字段构造，并用 validator 做 current /
  跨公司守卫。

要求调用方先 init 两个 Store（estore.init_db / fstore.init_db），与 indexer_v2 /
sparse 的调用约定一致。

CLI: python -m retrieval.retriever_v2 --company 300750 --question "实际控制人是谁"
"""

from __future__ import annotations

import logging
import time
import uuid
from concurrent import futures
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


class RetrievalSessionPoisoned(RetrievalError):
    """Session 已熔断（软超时后拒绝后续检索）。"""


def _rss_bytes() -> tuple[int | None, str | None]:
    """尽力获取当前进程 RSS（字节）；不可获取返回 (None, 说明)。"""
    try:
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024, None
    except Exception as e:  # Windows 无 resource 模块等 → null + 说明
        return None, f"rss 不可获取: {e}"


class RetrievalSession:
    """一次检索会话：装载 current Evidence 块，Hybrid 路径懒加载索引/模型并校验新鲜度。"""

    def __init__(self, company_id: str, *,
                 chroma_dir: Path | None = None,
                 sparse_dir: Path | None = None,
                 manifest_dir: Path | None = None,
                 model=None,
                 timeout_ms: int | None = None):
        self.company_id = company_id
        self.chroma_dir = Path(chroma_dir) if chroma_dir is not None else indexer_v2.DEFAULT_CHROMA_DIR
        self.sparse_dir = Path(sparse_dir) if sparse_dir is not None else sparse.DEFAULT_SPARSE_DIR
        self.manifest_dir = Path(manifest_dir) if manifest_dir is not None else indexer_v2.DEFAULT_MANIFEST_DIR
        self.model = model  # None → Dense 通道懒加载 BGE-M3
        self.timeout_ms = timeout_ms  # None → 用 decision.budget.timeout_ms

        self.blocks_by_id: dict[str, object] = {}
        self._authority: dict[str, dict] = {}
        self.current_version: str = ""
        self.manifest: indexer_v2.IndexManifest | None = None
        self.sparse_index: sparse.SparseIndex | None = None

        # 版本派生原料（_load_current 填充，供 sparse 期望版本重算）。
        self._metas: list[dict] = []
        self._record_count: int = 0
        self._inventory_fp: str = ""
        self._code_config_fp: str = ""

        # 熔断状态。
        self._poisoned: bool = False
        self._poison_reason: str | None = None

        # 懒加载 embedding 元信息（契约修正 6）。
        self._embedding_model_name: str | None = None
        self._embedding_device: str | None = None
        self._embedding_first_load_ms: float | None = None
        self._embedding_timed: bool = False

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
        self._metas = metas
        self._record_count = len(blocks)
        self._inventory_fp = fp
        self.current_version = indexer_v2.derive_index_version(
            self.company_id, metas, len(blocks), fp, versions)
        self._code_config_fp = trace_v2.code_config_fingerprint(versions, S.RULE_VERSION)

    def _ensure_indexes(self) -> None:
        """懒加载 dense manifest + sparse 索引并校验新鲜度（仅 Hybrid 路径需要）。

        dense 与 sparse 的 index_version 各自独立：dense 用组件版本派生，sparse 额外
        叠加 BM25 参数身份（k1/b/format，契约修正 4），故分别对各自期望版本校验。
        """
        if self.manifest is None or self.sparse_index is None:
            self.manifest = indexer_v2.load_manifest(self.company_id, self.manifest_dir)
            if self.manifest is None:
                raise IndexNotReady("INDEX_NOT_FOUND", f"dense manifest 缺失: {self.company_id}")
            self.sparse_index = sparse.load_index(self.company_id, self.sparse_dir)
            if self.sparse_index is None:
                raise IndexNotReady("INDEX_NOT_FOUND", f"sparse 索引缺失: {self.company_id}")
            sparse_expected = sparse.derive_sparse_version(
                self.company_id, self._metas, self._record_count, self._inventory_fp,
                sparse._sparse_versions(
                    float(self.sparse_index.params.get("k1", sparse.DEFAULT_K1)),
                    float(self.sparse_index.params.get("b", sparse.DEFAULT_B))))
            if (self.manifest.index_version != self.current_version
                    or self.sparse_index.index_version != sparse_expected):
                raise IndexNotReady(
                    "INDEX_VERSION_MISMATCH",
                    f"索引版本不匹配: dense={self.manifest.index_version}, "
                    f"sparse={self.sparse_index.index_version}, "
                    f"dense_current={self.current_version}, "
                    f"sparse_current={sparse_expected}")

    def _ensure_model(self):
        """懒加载 BGE-M3（仅 Dense 通道触发；DB_LOOKUP / EXTERNAL 不触碰）。"""
        if self.model is None:
            self.model = get_embedding_model()
            self._embedding_model_name = getattr(self.model, "_model_name", None)
            self._embedding_device = getattr(self.model, "device", None)
        return self.model

    # -- 主入口 --------------------------------------------------------------

    def retrieve(self, need: S.InformationNeed, decision: S.RouteDecision,
                 context: S.RouteContext, *, trace_meta: dict | None = None) -> S.EvidencePack:
        validate_need(need)
        validate_decision(decision)
        validate_context(context)
        if need.need_id != decision.need_id:
            raise RetrievalError(
                f"need_id 与 decision.need_id 不一致: {need.need_id} != {decision.need_id}")

        # 熔断：Session 已 poisoned → 拒绝后续检索（不排队、不产生结果）。
        if self._poisoned:
            return self._poisoned_pack(need, decision, trace_meta)

        if decision.route == "DB_LOOKUP":
            pack = self._db_lookup(need, decision, context, trace_meta)
        elif decision.route == "EXTERNAL_RESEARCH":
            pack = self._external(need, decision, trace_meta)
        elif decision.route in ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"):
            pack = self._hybrid(need, decision, trace_meta)
        else:
            pack = S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="UNSUPPORTED_ROUTE", retrieval_trace_id=uuid.uuid4().hex)

        validate_pack(pack, self._authority)
        validate_authority_company(pack, self.company_id, self._authority)
        return pack

    def _poisoned_pack(self, need: S.InformationNeed, decision: S.RouteDecision,
                       trace_meta: dict | None) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        status, failure_code = "FAILED", "TIMEOUT"
        tr = self._base_trace(need, decision, trace_id, status, failure_code, trace_meta)
        self._write_trace_or_fail(tr, need, decision, trace_id)
        return S.EvidencePack(
            need_id=need.need_id, status=status, route_decision=decision,
            failure_code=failure_code,
            missing_requirements=[self._poison_reason or "session poisoned（软超时熔断）"],
            retrieval_trace_id=trace_id)

    # -- trace 公共构造 -------------------------------------------------------

    def _base_trace(self, need: S.InformationNeed, decision: S.RouteDecision,
                    trace_id: str, status: str, failure_code: str | None,
                    trace_meta: dict | None) -> trace_v2.RetrievalTrace:
        meta = trace_meta or {}
        rss, rss_note = _rss_bytes()
        return trace_v2.RetrievalTrace(
            trace_id=trace_id, timestamp=trace_v2._now(), company_id=self.company_id,
            need_id=need.need_id, route=decision.route, query=need.question,
            index_version=self.current_version, status=status, failure_code=failure_code,
            run_id=meta.get("run_id"), case_id=meta.get("case_id"),
            dataset_sha256=meta.get("dataset_sha256"),
            corpus_manifest_sha256=meta.get("corpus_manifest_sha256"),
            evidence_inventory_fingerprint=self._inventory_fp,
            code_config_fingerprint=self._code_config_fp,
            embedding_model=self._embedding_model_name,
            embedding_device=self._embedding_device,
            embedding_first_load_ms=self._embedding_first_load_ms,
            rss_bytes=rss, rss_note=rss_note)

    def _write_trace_or_fail(self, tr: trace_v2.RetrievalTrace,
                             need: S.InformationNeed, decision: S.RouteDecision,
                             trace_id: str) -> str | None:
        """落盘 trace；失败返回 None（调用方据此降级为 TRACE_WRITE_FAILED pack）。"""
        try:
            return trace_v2.write_trace(tr)
        except trace_v2.TraceWriteError as e:
            logger.error("trace 落盘失败（fail-closed）: %s", e)
            return None

    # -- DB_LOOKUP -----------------------------------------------------------

    def _db_lookup(self, need: S.InformationNeed, decision: S.RouteDecision,
                   context: S.RouteContext, trace_meta: dict | None) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        status: str
        failure_code: str | None
        results: list[S.StructuredResultRef] = []
        missing: list[str] = []

        try:
            results, missing = self._execute_db_target(decision, context)
            if results:
                status, failure_code = "DB_RESULT_AVAILABLE", None
            else:
                status, failure_code = "DB_FIELD_UNAVAILABLE", None
        except Exception as e:  # 快照缺失等 → DB_SNAPSHOT_UNAVAILABLE（fail-closed）
            logger.warning("DB 执行失败: %s", e)
            status, failure_code = "FAILED", "DB_SNAPSHOT_UNAVAILABLE"
            missing = [str(e)]

        # 先定最终 status 再落盘 trace（契约修正 5）。
        tr = self._base_trace(need, decision, trace_id, status, failure_code, trace_meta)
        if self._write_trace_or_fail(tr, need, decision, trace_id) is None:
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="TRACE_WRITE_FAILED", retrieval_trace_id=trace_id)

        if results:
            return S.EvidencePack(
                need_id=need.need_id, status="DB_RESULT_AVAILABLE",
                route_decision=decision, structured_results=results,
                retrieval_trace_id=trace_id)
        return S.EvidencePack(
            need_id=need.need_id, status=status, route_decision=decision,
            failure_code=failure_code, missing_requirements=missing,
            retrieval_trace_id=trace_id)

    def _execute_db_target(self, decision: S.RouteDecision, context: S.RouteContext
                           ) -> tuple[list[S.StructuredResultRef], list[str]]:
        """执行 DB target：字段/指标 → StructuredResultRef（契约修正 C + 2）。

        契约修正 2：snapshot_as_of_date 选 current snapshot；target_period 在快照内
        选 report_period；scope/currency/purpose 限定快照键；formula_version 匹配
        指标结果版本（fail-closed，禁止返回旧版本结果）。
        """
        snap = self._current_snapshot(decision)
        if snap is None:
            raise RetrievalError("无当前快照，DB 取数不可用")

        f = decision.filters
        target_period = f.get("target_period") or None
        target_type = f["db_target_type"]
        if target_type == "field":
            code = f["standard_item_code"]
            for it in fstore.list_snapshot_items(snap.snapshot_id):
                if it.standard_item_code != code or it.amount is None:
                    continue
                if target_period and it.report_period != target_period:
                    continue
                ref = S.StructuredResultRef(
                    result_type="financial_field", snapshot_id=snap.snapshot_id,
                    item_code=code, formula_id=None, formula_version=None,
                    period=it.report_period, raw_value=str(it.amount),
                    display_value=str(it.amount), unit=it.unit, status="available",
                    reason_code=None, input_record_refs=it.source_refs,
                    input_snapshot_item_refs=[it.comparison_key])
                return [ref], []
            return [], [f"字段不可用: {code}"]

        formula_id = f["formula_id"]
        formula_version = f.get("formula_version") or None
        for mr in fstore.list_metric_results(snap.snapshot_id):
            if mr.formula_id != formula_id or mr.status not in _AVAILABLE_METRIC_STATUSES:
                continue
            if target_period and mr.period != target_period:
                continue
            if formula_version and mr.formula_version != formula_version:
                continue
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

    def _current_snapshot(self, decision: S.RouteDecision):
        """由 decision.filters 精确限定快照（契约修正 2：不再硬编码 scope/currency）。"""
        f = decision.filters
        as_of = f.get("snapshot_as_of_date") or None
        if not as_of:
            return None
        return snapshots.current_snapshot(
            self.company_id, scope=f.get("scope", "consolidated"),
            currency=f.get("currency", "CNY"), as_of_date=as_of,
            purpose=f.get("purpose", "credit_analysis"))

    # -- EXTERNAL_RESEARCH ---------------------------------------------------

    def _external(self, need: S.InformationNeed, decision: S.RouteDecision,
                  trace_meta: dict | None) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        status, failure_code = "EXTERNAL_RESEARCH_NOT_IMPLEMENTED", None
        tr = self._base_trace(need, decision, trace_id, status, failure_code, trace_meta)
        if self._write_trace_or_fail(tr, need, decision, trace_id) is None:
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="TRACE_WRITE_FAILED", retrieval_trace_id=trace_id)
        return S.EvidencePack(
            need_id=need.need_id, status=status, route_decision=decision,
            missing_requirements=["外部检索未实现（首轮不启用）"],
            retrieval_trace_id=trace_id)

    # -- Hybrid（sparse + dense → RRF，含软超时熔断） -------------------------

    def _hybrid(self, need: S.InformationNeed, decision: S.RouteDecision,
                trace_meta: dict | None) -> S.EvidencePack:
        trace_id = uuid.uuid4().hex
        try:
            self._ensure_indexes()
        except IndexNotReady as e:
            tr = self._base_trace(need, decision, trace_id, "FAILED", e.code, trace_meta)
            if self._write_trace_or_fail(tr, need, decision, trace_id) is None:
                return S.EvidencePack(
                    need_id=need.need_id, status="FAILED", route_decision=decision,
                    failure_code="TRACE_WRITE_FAILED", retrieval_trace_id=trace_id)
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code=e.code, retrieval_trace_id=trace_id)

        budget = decision.budget
        q = need.question
        timeout_ms = self.timeout_ms if self.timeout_ms is not None else budget.timeout_ms

        sparse_hits, dense_hits, failures, timed_out, timings = self._run_channels(
            q, budget.candidate_k_sparse, budget.candidate_k_dense, timeout_ms)

        # 软超时熔断：标记 poisoned，不等待孤儿任务；当前题按既有规则降级。
        if timed_out:
            self._poisoned = True
            self._poison_reason = f"检索超时（{timeout_ms}ms deadline）"

        filter_pre, filter_post = self._last_filter_counts()
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

        if timed_out:
            status = "PARTIAL" if evidence else "FAILED"
            failure_code = "TIMEOUT"
        elif evidence:
            status = "PARTIAL" if failures else "COMPLETED"
            failure_code = failures[0] if failures else None
        elif failures:
            status = "FAILED" if len(failures) == 2 else "PARTIAL"
            failure_code = "BOTH_CHANNELS_FAILED" if len(failures) == 2 else failures[0]
        else:
            status, failure_code = "EMPTY", None

        # 先定最终 status 再落盘 trace（契约修正 5）。
        tr = self._base_trace(need, decision, trace_id, status, failure_code, trace_meta)
        tr.sparse_hits = sparse_hits
        tr.dense_hits = dense_hits
        tr.fused = [{"evidence_id": fh.evidence_id, "rrf_score": fh.rrf_score, "rank": fh.rank}
                    for fh in fused[:budget.context_k]]
        tr.rrf_full_ranking = [{"evidence_id": fh.evidence_id, "rrf_score": fh.rrf_score,
                                "rank": fh.rank} for fh in fused]
        tr.returned_evidence_ids = [r.evidence_id for r in evidence]
        tr.filter_pre_count = filter_pre
        tr.filter_post_count = filter_post
        tr.timings_ms = timings
        if self._write_trace_or_fail(tr, need, decision, trace_id) is None:
            return S.EvidencePack(
                need_id=need.need_id, status="FAILED", route_decision=decision,
                failure_code="TRACE_WRITE_FAILED", retrieval_trace_id=trace_id)

        return S.EvidencePack(
            need_id=need.need_id, status=status, route_decision=decision,
            evidence=evidence, failure_code=failure_code, retrieval_trace_id=trace_id)

    def _run_channels(self, q: str, k_sparse: int, k_dense: int, timeout_ms: int
                      ) -> tuple[list[dict], list[dict], list[str], bool, dict]:
        """并发启动 sparse/dense，统一 deadline；超时返回 timed_out=True 并熔断。"""
        t_total = time.perf_counter()
        sparse_hits: list[dict] = []
        dense_hits: list[dict] = []
        failures: list[str] = []
        timed_out = False
        timings: dict = {"sparse": 0.0, "dense": 0.0}

        ex = futures.ThreadPoolExecutor(max_workers=2)
        deadline = time.monotonic() + timeout_ms / 1000.0
        f_sparse = ex.submit(self._run_sparse, q, k_sparse)
        f_dense = ex.submit(self._run_dense, q, k_dense)

        try:
            for name, fut, bucket in (("sparse", f_sparse, "SPARSE_FAILED"),
                                      ("dense", f_dense, "DENSE_FAILED")):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                t0 = time.perf_counter()
                try:
                    hits = fut.result(timeout=remaining)
                    timings[name] = (time.perf_counter() - t0) * 1000.0
                    if name == "sparse":
                        sparse_hits = hits
                    else:
                        dense_hits = hits
                except futures.TimeoutError:
                    timings[name] = (time.perf_counter() - t0) * 1000.0
                    timed_out = True
                    break
                except Exception as e:
                    logger.warning("%s 检索失败: %s", name, e)
                    failures.append(bucket)
        finally:
            if timed_out:
                # 不等待孤儿任务：shutdown(wait=False) 隔离后台线程，避免无限等待。
                ex.shutdown(wait=False, cancel_futures=True)
            else:
                ex.shutdown(wait=False)

        timings["total"] = (time.perf_counter() - t_total) * 1000.0
        return sparse_hits, dense_hits, failures, timed_out, timings

    def _run_sparse(self, q: str, k: int) -> list[dict]:
        return [{"evidence_id": h.evidence_id, "score": h.score, "rank": h.rank}
                for h in sparse.bm25_search(self.sparse_index, q, k)]

    def _run_dense(self, q: str, k: int) -> list[dict]:
        return self._dense_search(q, k)

    def _dense_search(self, query: str, k: int) -> list[dict]:
        import chromadb

        self._ensure_model()
        client = chromadb.PersistentClient(path=str(self.chroma_dir))
        coll = client.get_collection(name=self.manifest.collection)
        if not self._embedding_timed:
            t0 = time.perf_counter()
            q_emb = self.model.encode([query])[0]
            self._embedding_first_load_ms = (time.perf_counter() - t0) * 1000.0
            self._embedding_timed = True
        else:
            q_emb = self.model.encode([query])[0]
        # 真实 metadata filter（契约修正 6）：按 company_id 过滤 Chroma 结果。
        res = coll.query(query_embeddings=[q_emb], n_results=k,
                         where={"company_id": self.company_id})
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        self._last_dense_filter_pre = len(ids)
        # 仅保留 current inventory 内的 evidence（当前证据守卫）。
        out: list[dict] = []
        for i in range(len(ids)):
            if ids[i] not in self.blocks_by_id:
                continue
            out.append({"evidence_id": ids[i], "score": round(1.0 - dists[i], 6),
                        "rank": i + 1})
        self._last_dense_filter_post = len(out)
        return out

    def _last_filter_counts(self) -> tuple[int | None, int | None]:
        return (getattr(self, "_last_dense_filter_pre", None),
                getattr(self, "_last_dense_filter_post", None))

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
             manifest_dir: Path | None = None, model=None,
             trace_meta: dict | None = None) -> S.EvidencePack:
    """单次检索便捷入口：构建会话并执行（每调用新建会话，适合 CLI / 单 need）。"""
    session = RetrievalSession(
        context.company_id, chroma_dir=chroma_dir, sparse_dir=sparse_dir,
        manifest_dir=manifest_dir, model=model)
    return session.retrieve(need, decision, context, trace_meta=trace_meta)


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
