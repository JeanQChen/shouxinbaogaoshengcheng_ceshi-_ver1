"""命中计分与聚合。

- score_case：对单题在 K=1/5/10 上计算 PageHit / AllGroupHit / RR / 相邻诊断 /
  GoldPageResultPrecision（诊断代理，非 Context Precision）。
- aggregate_metrics：跨题聚合总体与各切片指标，分母在运行前冻结，排除题不计入。

命中要求 document_id + pdf_page 同时匹配；document_id 经 manifest 映射到 source_file
（即 ChromaDB metadata 中的文件名）后与检索结果的 source_file 比较。
"""

from __future__ import annotations

from evaluation.schema import (
    AggregateMetrics,
    CaseMetrics,
    CaseResult,
    CorpusManifest,
    GoldEvidenceGroup,
    GoldEvidenceTarget,
    ResolvedGoldCase,
    RetrievalEvalCase,
)


def _source_file_map(manifest: CorpusManifest) -> dict[str, str]:
    return {d.document_id: d.source_file for d in manifest.documents}


def _target_hit(
    t: GoldEvidenceTarget,
    top_k: list,
    sf_by_id: dict[str, str],
) -> bool:
    if not t.document_id or t.pdf_page is None:
        return False
    sf = sf_by_id.get(t.document_id)
    if sf is None:
        return False
    return any(c.source_file == sf and c.page_number == t.pdf_page for c in top_k)


def _group_hit(
    g: GoldEvidenceGroup,
    top_k: list,
    sf_by_id: dict[str, str],
) -> bool:
    # requirement 固定 all：组内全部 target 均命中
    return all(_target_hit(t, top_k, sf_by_id) for t in g.targets)


def _adjacent_any(
    targets: list[GoldEvidenceTarget],
    top_k: list,
    sf_by_id: dict[str, str],
) -> bool:
    """同文档绝对页差 ≤1（含严格命中）。"""
    for t in targets:
        if not t.document_id or t.pdf_page is None:
            continue
        sf = sf_by_id.get(t.document_id)
        if sf is None:
            continue
        for c in top_k:
            if c.source_file == sf and abs(c.page_number - t.pdf_page) <= 1:
                return True
    return False


def score_case(
    case_result: CaseResult,
    resolved_gold: ResolvedGoldCase,
    ks: list[int],
    manifest: CorpusManifest,
) -> CaseMetrics:
    """对单题计分。

    检索结果保留原始排序；K 指标均为最大 K 一次调用返回结果的前缀，
    不对结果先去重再截取。
    """
    sf_by_id = _source_file_map(manifest)
    local_groups = [g for g in resolved_gold.groups if g.is_local]
    local_targets = [t for g in local_groups for t in g.targets]
    n_local_targets = len(local_targets)
    n_local_groups = len(local_groups)
    eligible = case_result.eligibility.is_eligible

    gold_pages = {
        (sf_by_id.get(t.document_id), t.pdf_page)
        for t in local_targets
        if t.document_id and t.pdf_page and sf_by_id.get(t.document_id)
    }
    unique_required = {
        (t.document_id, t.pdf_page)
        for t in local_targets
        if t.document_id and t.pdf_page is not None
    }
    n_unique = len(unique_required)

    retrieved = case_result.retrieved

    # RR@10：首个命中任一 target 的排名倒数
    rr = 0.0
    for rank, c in enumerate(retrieved[:10], start=1):
        if (c.source_file, c.page_number) in gold_pages:
            rr = 1.0 / rank
            break

    page_hit: dict[int, bool] = {}
    all_group_hit: dict[int, bool] = {}
    adjacent_hit: dict[int, bool] = {}
    adjacent_only: dict[int, bool] = {}
    precision: dict[int, float] = {}
    hit_required: dict[int, int] = {}
    missing_required: dict[int, list] = {}
    coverage: dict[int, float] = {}
    recall_status: dict[int, str] = {}

    for K in ks:
        top_k = retrieved[:K]
        ph = any(_target_hit(t, top_k, sf_by_id) for t in local_targets) if local_targets else False
        agh = all(_group_hit(g, top_k, sf_by_id) for g in local_groups) if local_groups else False
        adj = _adjacent_any(local_targets, top_k, sf_by_id) if local_targets else False

        page_hit[K] = ph
        all_group_hit[K] = agh
        adjacent_hit[K] = adj
        adjacent_only[K] = adj and not ph

        if top_k:
            in_gold = sum(1 for c in top_k if (c.source_file, c.page_number) in gold_pages)
            precision[K] = in_gold / len(top_k)
        else:
            precision[K] = 0.0

        # 部分召回：唯一必需本地页命中数（document_id+pdf_page 去重）
        hit_keys = {
            (t.document_id, t.pdf_page)
            for t in local_targets
            if t.document_id and t.pdf_page is not None and _target_hit(t, top_k, sf_by_id)
        }
        hit_n = len(hit_keys)
        hit_required[K] = hit_n
        coverage[K] = (hit_n / n_unique) if n_unique > 0 else 0.0
        missing_required[K] = sorted(f"{doc_id}P{page}" for (doc_id, page) in (unique_required - hit_keys))
        if n_unique == 0:
            recall_status[K] = "FULL_RECALL"
        elif hit_n == 0:
            recall_status[K] = "ZERO_RECALL"
        elif hit_n >= n_unique:
            recall_status[K] = "FULL_RECALL"
        else:
            recall_status[K] = "PARTIAL_RECALL"

    return CaseMetrics(
        case_id=case_result.case_id,
        eligible=eligible,
        n_local_targets=n_local_targets,
        n_local_groups=n_local_groups,
        page_hit=page_hit,
        all_group_hit=all_group_hit,
        rr=rr,
        adjacent_hit=adjacent_hit,
        adjacent_only=adjacent_only,
        gold_page_result_precision=precision,
        n_unique_required_pages=n_unique,
        required_page_count=n_unique,
        hit_required_page_count=hit_required,
        missing_required_pages=missing_required,
        required_page_coverage=coverage,
        recall_status=recall_status,
    )


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _group_metrics(
    metrics: list[CaseMetrics],
    ks: list[int],
) -> dict:
    out: dict = {"n": len(metrics)}
    if not metrics:
        out.update({"page_hit": {}, "all_group_hit": {}, "required_page_coverage": {}, "mrr10": None})
        return out
    out["page_hit"] = {
        K: sum(1 for m in metrics if m.page_hit.get(K)) / len(metrics) for K in ks
    }
    out["all_group_hit"] = {
        K: sum(1 for m in metrics if m.all_group_hit.get(K)) / len(metrics) for K in ks
    }
    out["required_page_coverage"] = {
        K: _mean([m.required_page_coverage[K] for m in metrics]) for K in ks
    }
    out["mrr10"] = sum(m.rr for m in metrics) / len(metrics)
    return out


def aggregate_metrics(
    cases: list[RetrievalEvalCase],
    case_results: list[CaseResult],
    case_metrics: list[CaseMetrics],
    ks: list[int],
) -> AggregateMetrics:
    """跨题聚合。cases / case_results / case_metrics 按同一 case_id 顺序对齐。"""
    cases_by_id = {c.case_id: c for c in cases}
    result_by_id = {r.case_id: r for r in case_results}
    metric_by_id = {m.case_id: m for m in case_metrics}

    eligible = [m for m in case_metrics if m.eligible]
    eligible_ids = {m.case_id for m in eligible}

    n_total = len(cases)

    def _macro(field: str) -> dict[int, float]:
        return {
            K: sum(1 for m in eligible if getattr(m, field).get(K)) / len(eligible)
            if eligible else 0.0
            for K in ks
        }

    page_hit = _macro("page_hit")
    all_group_hit = _macro("all_group_hit")
    adjacent_page_hit = _macro("adjacent_hit")
    gold_page_result_precision = {
        K: _mean([m.gold_page_result_precision[K] for m in eligible]) if eligible else 0.0
        for K in ks
    }
    mrr10 = _mean([m.rr for m in eligible]) if eligible else 0.0

    # Macro RequiredPageCoverage@K：先算每题覆盖率，再对题等权平均（非 Micro 合并页面）
    required_page_coverage = {
        K: _mean([m.required_page_coverage[K] for m in eligible]) if eligible else 0.0
        for K in ks
    }

    # recall 状态分布（按 K）
    recall_status: dict[int, dict[str, int]] = {}
    for K in ks:
        cnt = {"ZERO_RECALL": 0, "PARTIAL_RECALL": 0, "FULL_RECALL": 0}
        for m in eligible:
            cnt[m.recall_status.get(K, "ZERO_RECALL")] += 1
        recall_status[K] = cnt

    # P0 切片
    p0 = [m for m in eligible if cases_by_id[m.case_id].priority == "P0"]
    p0_page_hit10 = (
        sum(1 for m in p0 if m.page_hit.get(10)) / len(p0) if p0 else None
    )
    p0_required_page_coverage10 = (
        _mean([m.required_page_coverage[10] for m in p0]) if p0 else None
    )

    # multi_page 切片（≥2 唯一必需本地页）
    multi = [m for m in eligible if m.n_unique_required_pages >= 2]
    multi_page_page_hit = {
        K: sum(1 for m in multi if m.page_hit.get(K)) / len(multi) if multi else None
        for K in ks
    }
    multi_page_all_group_hit = {
        K: sum(1 for m in multi if m.all_group_hit.get(K)) / len(multi) if multi else None
        for K in ks
    }
    multi_page_required_page_coverage = {
        K: _mean([m.required_page_coverage[K] for m in multi]) if multi else None
        for K in ks
    }

    # 分组
    def _slice(metrics_list: list[CaseMetrics]) -> dict:
        return _group_metrics(metrics_list, ks)

    by_section: dict[str, dict] = {}
    by_route_raw: dict[str, dict] = {}
    by_route_v2: dict[str, dict] = {}
    by_priority: dict[str, dict] = {}

    for m in eligible:
        c = cases_by_id[m.case_id]
        by_section.setdefault(c.section_id, []).append(m)
        by_route_raw.setdefault(c.expected_route_raw, []).append(m)
        by_route_v2.setdefault(c.expected_route_v2, []).append(m)
        by_priority.setdefault(c.priority, []).append(m)

    # latency（仅实际调用检索的 eligible 题）
    latencies = [
        r.latency_ms for r in case_results
        if r.eligibility.is_eligible and r.error is None
    ]

    # 各状态计数
    status_counts: dict[str, int] = {}
    for r in case_results:
        status_counts[r.eligibility.status] = status_counts.get(r.eligibility.status, 0) + 1

    n_empty_retrieval = sum(
        1 for r in case_results
        if r.eligibility.is_eligible and r.error is None and not r.retrieved
    )
    n_errors = sum(1 for r in case_results if r.error is not None)

    n_required_pages_gt_k = {
        K: sum(1 for m in eligible if m.n_unique_required_pages > K) for K in ks
    }

    return AggregateMetrics(
        ks=ks,
        n_eligible=len(eligible),
        n_total=n_total,
        page_hit=page_hit,
        all_group_hit=all_group_hit,
        mrr10=mrr10,
        adjacent_page_hit=adjacent_page_hit,
        gold_page_result_precision=gold_page_result_precision,
        p0_page_hit10=p0_page_hit10,
        required_page_coverage=required_page_coverage,
        p0_required_page_coverage10=p0_required_page_coverage10,
        recall_status=recall_status,
        multi_page_page_hit=multi_page_page_hit,
        multi_page_all_group_hit=multi_page_all_group_hit,
        multi_page_required_page_coverage=multi_page_required_page_coverage,
        multi_page_n=len(multi),
        by_section={k: _slice(v) for k, v in by_section.items()},
        by_route_raw={k: _slice(v) for k, v in by_route_raw.items()},
        by_route_v2={k: _slice(v) for k, v in by_route_v2.items()},
        by_priority={k: _slice(v) for k, v in by_priority.items()},
        latency={
            "avg": _mean(latencies),
            "p50": _pct(latencies, 0.5),
            "p95": _pct(latencies, 0.95),
        },
        n_empty_retrieval=n_empty_retrieval,
        n_errors=n_errors,
        n_missing_doc=status_counts.get("MISSING_CORPUS_DOCUMENT", 0),
        n_invalid_mapping=status_counts.get("INVALID_GOLD_MAPPING", 0),
        n_external_only=status_counts.get("EXTERNAL_ONLY", 0),
        n_required_pages_gt_k=n_required_pages_gt_k,
        exclusion_breakdown=status_counts,
    )
