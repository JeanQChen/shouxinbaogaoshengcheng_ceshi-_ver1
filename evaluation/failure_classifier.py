"""参评资格与失败分类。

- classify_eligibility：按 DESIGN_V2 §12.5.4 判定每题资格。
- classify_failure：按 §12.5.6 对 AllGroupHit@max(K) 未完成的题给出唯一主失败原因。

判定顺序（§12.5.4）：
  无本地 target：仅 external→EXTERNAL_ONLY；仅 structured_db→STRUCTURED_DB_ONLY；
  两者都有→NON_LOCAL_MIXED。
  存在本地组：先查别名/页码映射，任一无效→INVALID_GOLD_MAPPING；再查必需文档入语料，
  缺任一→MISSING_CORPUS_DOCUMENT；否则 ELIGIBLE_LOCAL。

主失败原因优先级（§12.5.6）：
  DATASET_MAPPING_ERROR → CORPUS_MISSING → NOT_APPLICABLE_LOCAL（仅排除题）→
  RETRIEVAL_ERROR → EMPTY_RETRIEVAL → PARSE_PAGE_EMPTY → INDEX_MISSING →
  INDEXED_NOT_RETURNED_TOP_K；无法区分时 DIAGNOSTIC_UNAVAILABLE。
"""

from __future__ import annotations

from evaluation.schema import (
    CaseResult,
    CorpusManifest,
    CorpusState,
    Eligibility,
    FailureClassification,
    ResolvedGoldCase,
    RetrievalEvalCase,
)

# 页面级主原因优先级（高→低）
_PAGE_REASON_ORDER = [
    "PARSE_PAGE_EMPTY",
    "INDEX_MISSING",
    "INDEXED_NOT_RETURNED_TOP_K",
    "DIAGNOSTIC_UNAVAILABLE",
]

# 解析缓存：document_id -> set[page_number]（解析产物有 chunk 的页），None 表示解析失败
_parse_cache: dict[str, set[int] | None] = {}


def classify_eligibility(
    case: RetrievalEvalCase,
    manifest: CorpusManifest,
    corpus_state: CorpusState | None = None,
) -> Eligibility:
    """判定单题参评资格。"""
    local_groups = case.local_groups()
    by_id = manifest.by_id()

    if not local_groups:
        channels = {g.channel for g in case.gold_evidence_groups}
        has_external = "external" in channels
        has_db = "structured_db" in channels
        if has_external and has_db:
            return Eligibility("NON_LOCAL_MIXED", reason="external 与 structured_db 混合，无本地页")
        if has_external:
            return Eligibility("EXTERNAL_ONLY", reason="gold 仅来自外部网页/行情")
        if has_db:
            return Eligibility("STRUCTURED_DB_ONLY", reason="gold 仅应来自结构化数据库")
        return Eligibility("INVALID_GOLD_MAPPING", reason="无任何 gold 证据组")

    # 存在本地组：先查别名与页码映射
    aux: list[str] = []
    for t in case.local_targets():
        if t.document_id is None:
            return Eligibility(
                "INVALID_GOLD_MAPPING", auxiliary_tags=["missing_document_id"],
                reason="local target 缺 document_id",
            )
        doc = by_id.get(t.document_id)
        if doc is None:
            return Eligibility(
                "INVALID_GOLD_MAPPING", auxiliary_tags=["unknown_alias"],
                reason=f"document_id 未在 manifest 注册: {t.document_id}",
            )
        if t.pdf_page is None or not (1 <= t.pdf_page <= doc.page_count):
            return Eligibility(
                "INVALID_GOLD_MAPPING", auxiliary_tags=["page_out_of_range"],
                reason=f"{t.document_id} 页码 {t.pdf_page} 缺失/越界（共 {doc.page_count} 页）",
            )

    # 再查必需文档是否进入语料
    if corpus_state is not None and corpus_state.exists:
        for t in case.local_targets():
            doc = by_id[t.document_id]
            if doc.source_file not in corpus_state.documents_indexed:
                return Eligibility(
                    "MISSING_CORPUS_DOCUMENT",
                    auxiliary_tags=[doc.source_file],
                    reason=f"{doc.document_id}（{doc.source_file}）未进入当前语料",
                )

    return Eligibility("ELIGIBLE_LOCAL")


def _page_has_chunk(
    manifest: CorpusManifest,
    document_id: str,
    page: int,
) -> bool | None:
    """判断该页在解析产物中是否有 chunk（有 chunk→True；无→False；解析失败→None）。"""
    if document_id not in _parse_cache:
        doc = manifest.by_id().get(document_id)
        if doc is None:
            return None
        try:
            from parsers.pdf_parser import parse
            result = parse(doc.file_path)
            _parse_cache[document_id] = {c.page_number for c in result.chunks}
        except Exception:
            _parse_cache[document_id] = None
    pages = _parse_cache[document_id]
    if pages is None:
        return None
    return page in pages


def classify_failure(
    case: RetrievalEvalCase,
    resolved_gold: ResolvedGoldCase,
    case_result: CaseResult,
    corpus_state: CorpusState,
    manifest: CorpusManifest,
) -> FailureClassification:
    """对未完成（AllGroupHit@max(K)=False 或排除）的题给出唯一主失败原因。"""
    case_id = case_result.case_id
    elig = case_result.eligibility

    # 排除题
    if elig.status == "INVALID_GOLD_MAPPING":
        return FailureClassification(case_id, primary="DATASET_MAPPING_ERROR",
                                     auxiliary=list(elig.auxiliary_tags), reason=elig.reason)
    if elig.status == "MISSING_CORPUS_DOCUMENT":
        return FailureClassification(case_id, primary="CORPUS_MISSING",
                                     auxiliary=list(elig.auxiliary_tags), reason=elig.reason)
    if elig.status in ("EXTERNAL_ONLY", "STRUCTURED_DB_ONLY", "NON_LOCAL_MIXED"):
        return FailureClassification(case_id, primary="NOT_APPLICABLE_LOCAL",
                                     auxiliary=[elig.status], reason=elig.reason)

    # eligible 题
    if case_result.error:
        return FailureClassification(case_id, primary="RETRIEVAL_ERROR",
                                     auxiliary=[], reason=case_result.error)
    if not case_result.retrieved:
        return FailureClassification(case_id, primary="EMPTY_RETRIEVAL",
                                     auxiliary=[], reason="Retriever 返回空结果")

    sf_by_id = {d.document_id: d.source_file for d in manifest.documents}
    retrieved = case_result.retrieved
    local_targets = [t for g in resolved_gold.groups if g.is_local for t in g.targets]

    missing: list = []
    for t in local_targets:
        if not t.document_id or t.pdf_page is None:
            continue
        sf = sf_by_id.get(t.document_id)
        if sf is None:
            continue
        if any(c.source_file == sf and c.page_number == t.pdf_page for c in retrieved):
            continue
        missing.append(t)

    if not missing:
        # 全部命中，不应作为失败分类对象；返回空完成态
        return FailureClassification(case_id, primary="COMPLETED",
                                     auxiliary=[], reason="所有必需本地页命中")

    # 对每个缺失页判定原因
    reasons: list[str] = []
    for t in missing:
        sf = sf_by_id.get(t.document_id)
        page = t.pdf_page
        in_index = page in corpus_state.indexed_pages.get(sf, set())
        if in_index:
            reasons.append("INDEXED_NOT_RETURNED_TOP_K")
        else:
            has_chunk = _page_has_chunk(manifest, t.document_id, page)
            if has_chunk is None:
                reasons.append("DIAGNOSTIC_UNAVAILABLE")
            elif has_chunk:
                reasons.append("INDEX_MISSING")
            else:
                reasons.append("PARSE_PAGE_EMPTY")

    primary = next((r for r in _PAGE_REASON_ORDER if r in reasons), "DIAGNOSTIC_UNAVAILABLE")

    auxiliary = []
    for r in reasons:
        if r != primary and r not in auxiliary:
            auxiliary.append(r)

    reason = f"缺失 {len(missing)}/{len(local_targets)} 必需本地页；" + "；".join(
        f"{t.document_id}P{t.pdf_page}" for t in missing
    )
    return FailureClassification(case_id, primary=primary, auxiliary=auxiliary, reason=reason)
