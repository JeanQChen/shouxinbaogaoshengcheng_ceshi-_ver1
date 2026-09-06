"""Eval: V1 Retrieval Baseline Runner（mock retriever，不加载 BGE-M3）。

覆盖 BASELINE_RUNNER_DEVELOPMENT_TASK.md §9 的 19 类边界：
单页命中、rank 前缀、all 组多页、多组、相邻页、跨文档、外部/DB 排除、
缺文档/映射缺失、空召回、异常续跑、CLI 非零、原子输出、且关系/any 拒绝、
部分映射整题排除、同页多 chunk、K 校验、缺 chunk/缺索引区分、审计闭合、混合排除。

用法: python -m evals.test_baseline_runner
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.schema import (
    BaselineRunResult,
    CaseResult,
    CorpusDocument,
    CorpusManifest,
    CorpusState,
    Eligibility,
    GoldEvidenceGroup,
    GoldEvidenceTarget,
    ResolvedGoldCase,
    RetrievedChunkSnapshot,
    RetrievalEvalCase,
)
from evaluation import dataset as dataset_mod
from evaluation import failure_classifier as fc
from evaluation import metrics as metrics_mod
from evaluation import page_mapping as pm
from evaluation import report_writer as rw
from evaluation import run_baseline as rb

YEAR = "NDSD_2025_year"
KCZ = "NDSD_KCZ_2026"
YEAR_PDF = "NDSD_2025_year.pdf"
KCZ_PDF = "NDSD_KCZ_2026.pdf"


@dataclass
class MockChunk:
    text: str
    page_number: int
    chunk_index: int
    source_file: str
    source_type: str = "annual_report"
    section_title: str = ""
    score: float = 0.9


def make_manifest() -> CorpusManifest:
    return CorpusManifest(
        company_id="300750", page_system="pdf_1based", page_system_note="identity",
        documents=[
            CorpusDocument(YEAR, ["年报"], "data/samples/300750/announcements/NDSD_2025_year.pdf", "annual_report", "a" * 64, 232, "pdf"),
            CorpusDocument(KCZ, ["募书"], "data/samples/300750/announcements/NDSD_KCZ_2026.pdf", "debt_circular", "b" * 64, 141, "pdf"),
        ],
    )


def make_case(case_id: str, groups, **kw) -> RetrievalEvalCase:
    defaults = dict(
        company_id="300750", section_id="FIN", question=f"Q {case_id}",
        expected_route_raw="TOPIC", expected_route_v2="STANDARD_RAG", priority="P0",
        time_scope=None, gold_evidence_raw={}, notes="", gold_answer=None,
    )
    defaults.update(kw)
    return RetrievalEvalCase(case_id=case_id, gold_evidence_groups=groups, **defaults)


def local_group(doc_id, pages, requirement="all") -> GoldEvidenceGroup:
    return GoldEvidenceGroup(
        group_id=f"local__{doc_id}", requirement=requirement, channel="local",
        targets=[GoldEvidenceTarget(doc_id, p, "verified", f"{doc_id}P{p}", "") for p in pages],
    )


def external_group() -> GoldEvidenceGroup:
    return GoldEvidenceGroup(group_id="external", requirement="all", channel="external", targets=[])


def db_group() -> GoldEvidenceGroup:
    return GoldEvidenceGroup(group_id="db", requirement="all", channel="structured_db", targets=[])


def make_corpus_state(indexed_files=None, indexed_pages=None, exists=True) -> CorpusState:
    files = indexed_files if indexed_files is not None else {YEAR_PDF, KCZ_PDF}
    return CorpusState(
        collection_name="company_docs__300750", exists=exists, chunk_count=100,
        documents_indexed={sf: {"page_min": 1, "page_max": 232, "chunk_count": 5, "source_type": "annual_report"} for sf in files},
        fingerprint="fp", indexed_pages=indexed_pages if indexed_pages is not None else {},
    )


def make_resolved(case_id, groups, elig="ELIGIBLE_LOCAL") -> ResolvedGoldCase:
    return ResolvedGoldCase(case_id=case_id, groups=groups, eligibility=Eligibility(elig))


def make_case_result(case_id, snaps, elig="ELIGIBLE_LOCAL", error=None, latency=1.0) -> CaseResult:
    return CaseResult(case_id=case_id, eligibility=Eligibility(elig), ks=[1, 5, 10],
                      retrieved=snaps, latency_ms=latency, error=error)


def snap(source_file, page, chunk_index=0, score=0.9) -> RetrievedChunkSnapshot:
    return RetrievedChunkSnapshot(source_file=source_file, page_number=page,
                                  chunk_index=chunk_index, score=score, text_preview="...")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    manifest = make_manifest()
    K = [1, 5, 10]

    def score_case(cr, resolved):
        return metrics_mod.score_case(cr, resolved, K, manifest)

    # ── 1. 单一正确页 rank1 ──
    c = make_case("C1", [local_group(YEAR, [97])])
    r = make_resolved("C1", [local_group(YEAR, [97])])
    cr = make_case_result("C1", [snap(YEAR_PDF, 97)])
    m = score_case(cr, r)
    check(m.page_hit[1] and m.page_hit[10] and m.all_group_hit[10], "rank1 单页全命中")
    check(abs(m.rr - 1.0) < 1e-9, "rank1 RR=1.0")

    # ── 2. rank6：Hit@5 失败、Hit@10 成功、RR=1/6 ──
    distract = [snap(YEAR_PDF, 10 + i) for i in range(5)]
    correct = snap(YEAR_PDF, 97)
    cr = make_case_result("C2", distract + [correct])
    m = score_case(cr, make_resolved("C2", [local_group(YEAR, [97])]))
    check(not m.page_hit[5] and m.page_hit[10], "rank6：Hit@5 失败、Hit@10 成功")
    check(abs(m.rr - 1 / 6) < 1e-9, "rank6 RR=1/6")

    # ── 3. 同 all 组多页只命中一页 ──
    cr = make_case_result("C3", [snap(YEAR_PDF, 25)])
    m = score_case(cr, make_resolved("C3", [local_group(YEAR, [25, 24])]))
    check(m.page_hit[10] and not m.all_group_hit[10], "同组多页：PageHit 成功、AllGroupHit 失败")

    # ── 4. 多组只命中一组 ──
    groups = [local_group(YEAR, [97]), local_group(KCZ, [39])]
    cr = make_case_result("C4", [snap(YEAR_PDF, 97)])
    m = score_case(cr, make_resolved("C4", groups))
    check(m.page_hit[10] and not m.all_group_hit[10], "多组只命中一组：PageHit 成功、AllGroupHit 失败")

    # ── 5. 相邻 ±1 页仅诊断 ──
    cr = make_case_result("C5", [snap(YEAR_PDF, 26)])
    m = score_case(cr, make_resolved("C5", [local_group(YEAR, [25])]))
    check(not m.page_hit[10] and m.adjacent_hit[10] and m.adjacent_only[10], "相邻页仅进诊断指标")

    # ── 6. 同页码不同文档不得命中 ──
    cr = make_case_result("C6", [snap(KCZ_PDF, 97)])
    m = score_case(cr, make_resolved("C6", [local_group(YEAR, [97])]))
    check(not m.page_hit[10], "同页码不同文档不得命中")

    # ── 7. external-only / DB-only 不进分母 ──
    e_ext = fc.classify_eligibility(make_case("E1", [external_group()]), manifest, make_corpus_state())
    e_db = fc.classify_eligibility(make_case("E2", [db_group()]), manifest, make_corpus_state())
    check(e_ext.status == "EXTERNAL_ONLY", "external-only → EXTERNAL_ONLY")
    check(e_db.status == "STRUCTURED_DB_ONLY", "DB-only → STRUCTURED_DB_ONLY")

    # ── 8. 缺文档 / 映射缺失 ──
    missing_doc_case = make_case("M1", [local_group("NDSD_UNKNOWN", [10])])
    e = fc.classify_eligibility(missing_doc_case, manifest, make_corpus_state())
    check(e.status == "INVALID_GOLD_MAPPING", "未注册 document_id → INVALID_GOLD_MAPPING")
    oor_case = make_case("M2", [local_group(YEAR, [999])])
    e = fc.classify_eligibility(oor_case, manifest, make_corpus_state())
    check(e.status == "INVALID_GOLD_MAPPING", "页码越界 → INVALID_GOLD_MAPPING")
    # doc 在 manifest 但语料缺该文件
    cs_missing = make_corpus_state(indexed_files={KCZ_PDF})
    e = fc.classify_eligibility(make_case("M3", [local_group(YEAR, [97])]), manifest, cs_missing)
    check(e.status == "MISSING_CORPUS_DOCUMENT", "语料缺文档 → MISSING_CORPUS_DOCUMENT")

    # ── 9. 空召回 → EMPTY_RETRIEVAL ──
    resolved = make_resolved("E9", [local_group(YEAR, [97])])
    cr = make_case_result("E9", [])
    f = fc.classify_failure(make_case("E9", [local_group(YEAR, [97])]), resolved, cr, make_corpus_state(indexed_pages={YEAR_PDF: {97}}), manifest)
    check(f.primary == "EMPTY_RETRIEVAL", "空召回 → EMPTY_RETRIEVAL")

    # ── 10. Retriever 异常后其他 case 继续 ──
    def boom(company_id, collection, query, k, db_path):
        raise RuntimeError("boom")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        audit_dir = Path(td) / "audit"
        audit_dir.mkdir()
        cr_err = rb.run_case(make_case("X1", [local_group(YEAR, [97])]), Eligibility("ELIGIBLE_LOCAL"),
                             boom, K, "data/chroma", "company_docs", "300750", audit_dir)
        check(cr_err.error is not None, "异常题记录 error")

        def ok(company_id, collection, query, k, db_path):
            return [MockChunk("t", 97, 0, YEAR_PDF)]

        cr_ok = rb.run_case(make_case("X2", [local_group(YEAR, [97])]), Eligibility("ELIGIBLE_LOCAL"),
                            ok, K, "data/chroma", "company_docs", "300750", audit_dir)
        check(cr_ok.error is None and len(cr_ok.retrieved) == 1, "异常后其他 case 继续")

    # ── 11. 重复 case_id / 非法 JSON / eligible 0 → 非零 ──
    dup_cases = [make_case("D", [local_group(YEAR, [97])]), make_case("D", [local_group(YEAR, [97])])]
    val = dataset_mod.validate_dataset(dup_cases, manifest)
    check(not val.ok and any("重复 case_id" in e for e in val.errors), "重复 case_id → 校验失败")

    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.jsonl"
        bad.write_text("{not json\n", encoding="utf-8")
        try:
            dataset_mod.load_dataset(bad)
            check(False, "非法 JSON 应抛异常")
        except ValueError:
            check(True, "非法 JSON → 抛 ValueError")

    try:
        rb._validate_ks([1, 5])
        check(False, "缺 10 应拒绝")
    except ValueError:
        check(True, "K 缺 1/5/10 → 拒绝")
    try:
        rb._validate_ks([1, 5, 25])
        check(False, "K>20 应拒绝")
    except ValueError:
        check(True, "K>20 → 拒绝")
    try:
        rb._validate_ks([0, 5, 10])
        check(False, "非正数应拒绝")
    except ValueError:
        check(True, "K 非正数 → 拒绝")

    # ── 12/18. 原子输出 + 已存在 run_id 拒绝 ──
    with tempfile.TemporaryDirectory() as td:
        out_root = Path(td) / "results"
        res = BaselineRunResult(run_id="r1", company_id="300750", collection="company_docs",
                                ks=K, output_dir=None, completed=True, status="completed")
        res.metadata = {"dataset_path": "nope", "corpus_manifest_path": "nope"}
        res.aggregate = metrics_mod.AggregateMetrics(
            ks=K, n_eligible=1, n_total=1, page_hit={1: 1.0, 5: 1.0, 10: 1.0},
            all_group_hit={1: 1.0, 5: 1.0, 10: 1.0}, mrr10=1.0,
            adjacent_page_hit={1: 1.0, 5: 1.0, 10: 1.0},
            gold_page_result_precision={1: 1.0, 5: 1.0, 10: 1.0},
            required_page_coverage={1: 1.0, 5: 1.0, 10: 1.0},
            p0_required_page_coverage10=1.0,
            recall_status={1: {"ZERO_RECALL": 0, "PARTIAL_RECALL": 0, "FULL_RECALL": 1},
                           5: {"ZERO_RECALL": 0, "PARTIAL_RECALL": 0, "FULL_RECALL": 1},
                           10: {"ZERO_RECALL": 0, "PARTIAL_RECALL": 0, "FULL_RECALL": 1}},
            p0_page_hit10=1.0, multi_page_page_hit={}, multi_page_all_group_hit={},
            multi_page_required_page_coverage={}, multi_page_n=0,
            by_section={}, by_route_raw={}, by_route_v2={}, by_priority={},
            latency={"avg": None, "p50": None, "p95": None},
            n_empty_retrieval=0, n_errors=0, n_missing_doc=0, n_invalid_mapping=0, n_external_only=0,
            n_required_pages_gt_k={1: 0, 5: 0, 10: 0}, exclusion_breakdown={"ELIGIBLE_LOCAL": 1},
        )
        final = rw.write_run_artifacts(res, out_root, cases=[], resolved_by_id={})
        for name in ("run_manifest.json", "case_results.jsonl", "metrics.json", "data_quality.json", "report.md"):
            check((final / name).exists(), f"产物存在: {name}")
        check((final / "inputs").is_dir(), "inputs/ 快照目录存在")
        try:
            rw.write_run_artifacts(res, out_root, cases=[], resolved_by_id={})
            check(False, "重复 run_id 应拒绝")
        except FileExistsError:
            check(True, "已存在 run_id → 拒绝且无部分目录")
        check(not (out_root / ".r1.tmp").exists(), "失败后无残留 tmp 目录")

    # ── 13. /、+、范围展开为且；any 拒绝 ──
    groups, warns = dataset_mod.parse_page_field("募书P35-40")
    check(len(groups[0].targets) == 6, f"范围展开 6 页（got {len(groups[0].targets)}）")
    groups, _ = dataset_mod.parse_page_field("年报P25 + P24")
    check({t.pdf_page for t in groups[0].targets} == {25, 24}, "+ 展开为且")
    groups, _ = dataset_mod.parse_page_field("募书P39 / 年报P97")
    check(len(groups) == 2, "/ 跨文档拆成两组")
    any_case = make_case("A1", [GoldEvidenceGroup("g", "any", "local", [GoldEvidenceTarget(YEAR, 97, "verified", "", "")])])
    val = dataset_mod.validate_dataset([any_case], manifest)
    check(not val.ok and any("requirement" in e for e in val.errors), "any 输入被拒绝")

    # ── 14. 部分映射整题排除 ──
    partial = GoldEvidenceGroup("g", "all", "local", [
        GoldEvidenceTarget(YEAR, 97, "verified", "", ""),
        GoldEvidenceTarget(YEAR, None, "missing", "年报募集资金章节", "缺页码"),
    ])
    e = fc.classify_eligibility(make_case("P1", [partial]), manifest, make_corpus_state())
    check(e.status == "INVALID_GOLD_MAPPING", "部分映射整题排除（不缩 gold）")

    # ── 15. 同页多 chunk 占排名；异常计0分母不变；空切片 null ──
    cr = make_case_result("S1", [snap(YEAR_PDF, 97, 0), snap(YEAR_PDF, 97, 1), snap(YEAR_PDF, 5)])
    m = score_case(cr, make_resolved("S1", [local_group(YEAR, [97])]))
    check(m.gold_page_result_precision[10] == 2 / 3, "同页多 chunk 不去重（precision=2/3）")
    check(abs(m.rr - 1.0) < 1e-9, "同页多 chunk 首个排名为 1")

    # 空切片 null：无 multi_page 题
    cases = [make_case("S2", [local_group(YEAR, [97])])]
    crs = [make_case_result("S2", [snap(YEAR_PDF, 97)])]
    cms = [score_case(crs[0], make_resolved("S2", [local_group(YEAR, [97])]))]
    agg = metrics_mod.aggregate_metrics(cases, crs, cms, K)
    check(agg.multi_page_n == 0 and agg.multi_page_page_hit[10] is None, "空 multi_page 切片 → null/n=0")

    # ── 16. 一次调用最大 K ──
    calls = []

    def spy(company_id, collection, query, k, db_path):
        calls.append(k)
        return [MockChunk("t", 97, 0, YEAR_PDF)]

    with tempfile.TemporaryDirectory() as td:
        audit_dir = Path(td) / "audit"
        audit_dir.mkdir()
        rb.run_case(make_case("K1", [local_group(YEAR, [97])]), Eligibility("ELIGIBLE_LOCAL"),
                    spy, K, "data/chroma", "company_docs", "300750", audit_dir)
    check(calls == [10], f"单题只调用一次 k=max(ks)=10（got {calls}）")

    # 必需页数 > K 仍参评
    many = local_group(YEAR, list(range(1, 13)))  # 12 页
    e = fc.classify_eligibility(make_case("N1", [many]), manifest, make_corpus_state())
    check(e.status == "ELIGIBLE_LOCAL", "必需页数>K 仍参评（不缩 gold）")
    cr = make_case_result("N1", [snap(YEAR_PDF, 1)])
    m = score_case(cr, make_resolved("N1", [many]))
    check(m.n_unique_required_pages == 12, "唯一必需页数=12")

    # ── 17. 缺 chunk / 缺索引区分；未知来源不判中 ──
    cs = make_corpus_state(indexed_pages={YEAR_PDF: set()})  # 页不在索引
    resolved = make_resolved("F1", [local_group(YEAR, [97])])
    cr = make_case_result("F1", [snap(YEAR_PDF, 5)])
    with patch.object(fc, "_page_has_chunk", return_value=True):
        f = fc.classify_failure(make_case("F1", [local_group(YEAR, [97])]), resolved, cr, cs, manifest)
    check(f.primary == "INDEX_MISSING", "有 chunk 但索引无记录 → INDEX_MISSING")
    with patch.object(fc, "_page_has_chunk", return_value=False):
        f = fc.classify_failure(make_case("F1", [local_group(YEAR, [97])]), resolved, cr, cs, manifest)
    check(f.primary == "PARSE_PAGE_EMPTY", "页无 chunk → PARSE_PAGE_EMPTY")
    cs_hit = make_corpus_state(indexed_pages={YEAR_PDF: {97}})
    f = fc.classify_failure(make_case("F1", [local_group(YEAR, [97])]), resolved, cr, cs_hit, manifest)
    check(f.primary == "INDEXED_NOT_RETURNED_TOP_K", "已入索引未返回 → INDEXED_NOT_RETURNED_TOP_K")

    # ── 18. 审计闭合 + legacy missing ──
    with tempfile.TemporaryDirectory() as td:
        audit_dir = Path(td) / "audit"
        audit_dir.mkdir()

        def ok2(company_id, collection, query, k, db_path):
            return [MockChunk("t", 97, 0, YEAR_PDF)]

        cr = rb.run_case(make_case("A2", [local_group(YEAR, [97])]), Eligibility("ELIGIBLE_LOCAL"),
                         ok2, K, "data/chroma", "company_docs", "300750", audit_dir)
        lines = (audit_dir / f"{cr.call_id}.jsonl").read_text(encoding="utf-8").strip().split("\n")
        check(len(lines) == 2 and json.loads(lines[0])["event"] == "started" and json.loads(lines[1])["event"] == "succeeded",
              "成功调用审计闭合（started+succeeded）")
        check(cr.legacy_log == "legacy_log_missing", "无 V1 日志时显式记录 legacy_log_missing")

    # ── 19. external + structured_db 混合无本地 → NON_LOCAL_MIXED ──
    e = fc.classify_eligibility(make_case("X3", [external_group(), db_group()]), manifest, make_corpus_state())
    check(e.status == "NON_LOCAL_MIXED", "external+structured_db 无本地 → NON_LOCAL_MIXED")

    # ── 20. RequiredPageCoverage + recall_status ──
    five = local_group(YEAR, [1, 2, 3, 4, 5])
    m = score_case(make_case_result("PR0", [snap(YEAR_PDF, 50)]), make_resolved("PR0", [five]))
    check(m.required_page_count == 5, "required_page_count=5")
    check(m.hit_required_page_count[10] == 0, "命中 0 页")
    check(abs(m.required_page_coverage[10]) < 1e-9, "coverage=0")
    check(m.recall_status[10] == "ZERO_RECALL", "ZERO_RECALL")

    m = score_case(make_case_result("PR1", [snap(YEAR_PDF, 1)]), make_resolved("PR1", [five]))
    check(abs(m.required_page_coverage[10] - 0.2) < 1e-9, "coverage=1/5=0.2")
    check(m.recall_status[10] == "PARTIAL_RECALL", "PARTIAL_RECALL (1/5)")

    m = score_case(make_case_result("PR3", [snap(YEAR_PDF, 1), snap(YEAR_PDF, 2), snap(YEAR_PDF, 3)]),
                   make_resolved("PR3", [five]))
    check(abs(m.required_page_coverage[10] - 0.6) < 1e-9, "coverage=3/5=0.6")
    check(m.recall_status[10] == "PARTIAL_RECALL", "PARTIAL_RECALL (3/5)")

    m = score_case(make_case_result("PR5", [snap(YEAR_PDF, i) for i in range(1, 6)]),
                   make_resolved("PR5", [five]))
    check(abs(m.required_page_coverage[10] - 1.0) < 1e-9, "coverage=5/5=1.0")
    check(m.recall_status[10] == "FULL_RECALL", "FULL_RECALL (5/5)")

    # 去重：同页跨组只计一次
    m = score_case(make_case_result("PRD", [snap(YEAR_PDF, 97)]),
                   make_resolved("PRD", [local_group(YEAR, [97]), local_group(YEAR, [97])]))
    check(m.required_page_count == 1 and abs(m.required_page_coverage[10] - 1.0) < 1e-9,
          "跨组重复页去重（required=1，coverage=1）")

    # missing_required_pages 输出
    m = score_case(make_case_result("PRM", [snap(YEAR_PDF, 1)]), make_resolved("PRM", [five]))
    check(m.missing_required_pages[10] == ["NDSD_2025_yearP2", "NDSD_2025_yearP3",
                                            "NDSD_2025_yearP4", "NDSD_2025_yearP5"],
          "missing_required_pages 列表正确")

    # Macro RequiredPageCoverage：题等权，非 Micro 合并页面
    cA = make_case("MA", [local_group(YEAR, [1, 2])])
    cB = make_case("MB", [local_group(YEAR, list(range(1, 11)))])
    crs = [
        make_case_result("MA", [snap(YEAR_PDF, 1)]),
        make_case_result("MB", [snap(YEAR_PDF, i) for i in range(1, 11)]),
    ]
    cms = [
        score_case(crs[0], make_resolved("MA", [local_group(YEAR, [1, 2])])),
        score_case(crs[1], make_resolved("MB", [local_group(YEAR, list(range(1, 11)))])),
    ]
    agg = metrics_mod.aggregate_metrics([cA, cB], crs, cms, K)
    check(abs(agg.required_page_coverage[10] - 0.75) < 1e-9,
          f"Macro RequiredPageCoverage@10 = 0.75（题等权，非 Micro {11/12:.3f}）")
    check(agg.recall_status[10] == {"ZERO_RECALL": 0, "PARTIAL_RECALL": 1, "FULL_RECALL": 1},
          "recall_status 分布正确")

    # 切片同步保存 required_page_coverage（metrics.json 口径，非仅 Markdown）
    check(abs(agg.by_section["FIN"]["required_page_coverage"][10] - 0.75) < 1e-9,
          "by_section 切片 required_page_coverage")
    check(abs(agg.by_priority["P0"]["required_page_coverage"][10] - 0.75) < 1e-9,
          "by_priority 切片 required_page_coverage")
    check(abs(agg.by_route_raw["TOPIC"]["required_page_coverage"][10] - 0.75) < 1e-9,
          "by_route_raw 切片 required_page_coverage")
    check(abs(agg.by_route_v2["STANDARD_RAG"]["required_page_coverage"][10] - 0.75) < 1e-9,
          "by_route_v2 切片 required_page_coverage")

    # multi_page 切片 required_page_coverage（cA=2页、cB=10页均 ≥2）
    check(agg.multi_page_n == 2, "multi_page_n=2")
    check(abs(agg.multi_page_required_page_coverage[10] - 0.75) < 1e-9,
          "multi_page 切片 required_page_coverage")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
