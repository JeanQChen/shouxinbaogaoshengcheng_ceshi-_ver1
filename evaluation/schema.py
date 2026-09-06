"""evaluation 模块的公共 dataclass 定义。

数据模型依据 DESIGN_V2.md §12.5 与 BASELINE_RUNNER_DEVELOPMENT_TASK.md。
所有类型均为纯数据容器，无业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 数据集模型 ──

@dataclass
class GoldEvidenceTarget:
    """单个必需证据页（每 target 只表示一个页面）。"""
    document_id: str | None      # 解析后的语料文档 ID（external 时为 None）
    pdf_page: int | None         # PDF 1-based 页序；未映射为 None
    mapping_status: str          # verified | missing（external 无本地页时用 "" 表示不适用）
    source_note: str             # 原始引用片段，如 "年报P97" / "募书P35-40"
    mapping_note: str = ""       # 映射/校验说明

    def key(self) -> tuple[str | None, int | None]:
        """命中判据键：document_id + pdf_page 同时匹配。"""
        return (self.document_id, self.pdf_page)


@dataclass
class GoldEvidenceGroup:
    """按问题子要求 / channel 组织的一组必需证据。"""
    group_id: str
    requirement: str             # 当前 41 问固定 "all"，不支持 any
    channel: str                 # local | external | structured_db
    targets: list[GoldEvidenceTarget] = field(default_factory=list)

    @property
    def is_local(self) -> bool:
        return self.channel == "local"


@dataclass
class RetrievalEvalCase:
    """规范化后的单题评测数据。"""
    case_id: str
    company_id: str
    section_id: str
    question: str
    expected_route_raw: str
    expected_route_v2: str
    priority: str
    time_scope: str | None
    gold_evidence_raw: dict       # 原始 source/page/origin，保留供审计
    notes: str
    gold_answer: Any
    gold_evidence_groups: list[GoldEvidenceGroup] = field(default_factory=list)

    def local_groups(self) -> list[GoldEvidenceGroup]:
        return [g for g in self.gold_evidence_groups if g.is_local]

    def local_targets(self) -> list[GoldEvidenceTarget]:
        return [t for g in self.local_groups() for t in g.targets]


# ── 语料清单模型 ──

@dataclass
class CorpusDocument:
    """语料中的一份本地 PDF 文档。"""
    document_id: str
    aliases: list[str]
    file_path: str
    source_type: str             # debt_circular | annual_report | other
    sha256: str
    page_count: int
    page_system: str = "pdf"     # 已确认：引用页码即 PDF 1-based 页序
    page_offset: int = 0         # gold 引用页码 → PDF 物理页的偏移（0 = 恒等）
    mapping_status: str = ""     # verified | unverified
    verification_cases: list = field(default_factory=list)  # 页码口径抽查记录

    @property
    def source_file(self) -> str:
        """ChromaDB metadata 中记录的 source_file（文件名）。"""
        from pathlib import Path
        return Path(self.file_path).name


@dataclass
class CorpusManifest:
    company_id: str
    page_system: str             # "pdf_1based"
    page_system_note: str
    documents: list[CorpusDocument] = field(default_factory=list)

    def by_id(self) -> dict[str, CorpusDocument]:
        return {d.document_id: d for d in self.documents}

    def by_source_file(self) -> dict[str, CorpusDocument]:
        return {d.source_file: d for d in self.documents}

    def resolve_alias(self, alias: str) -> CorpusDocument | None:
        """将文档别名解析到唯一 document。别名冲突返回 None 表示歧义。"""
        hits = [d for d in self.documents if alias in d.aliases]
        if len(hits) == 1:
            return hits[0]
        return None


# ── 语料盘点模型 ──

@dataclass
class CorpusState:
    """只读盘点 ChromaDB collection 得到的库存状态。"""
    collection_name: str
    exists: bool
    chunk_count: int
    documents_indexed: dict[str, dict]   # source_file -> {page_min, page_max, chunk_count, source_type}
    fingerprint: str                     # 稳定库存指纹（排序后的记录摘要哈希）
    indexed_pages: dict[str, set[int]] = field(default_factory=dict)  # source_file -> 已入索引的页码集合
    warnings: list[str] = field(default_factory=list)


# ── 资格与结果模型 ──

@dataclass
class Eligibility:
    """参评资格判定结果。"""
    status: str                          # ELIGIBLE_LOCAL | EXTERNAL_ONLY | STRUCTURED_DB_ONLY | NON_LOCAL_MIXED | INVALID_GOLD_MAPPING | MISSING_CORPUS_DOCUMENT
    auxiliary_tags: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def is_eligible(self) -> bool:
        return self.status == "ELIGIBLE_LOCAL"


@dataclass
class ResolvedGoldCase:
    """gold 页码解析结果。"""
    case_id: str
    groups: list[GoldEvidenceGroup]       # 重新解析后的证据组
    eligibility: Eligibility | None = None  # 由 classify_eligibility 填充


# ── 检索结果模型 ──

@dataclass
class RetrievedChunkSnapshot:
    """单条检索返回的快照（独立于 retrieval.RetrievedChunk，避免耦合）。"""
    source_file: str
    page_number: int
    chunk_index: int
    score: float
    text_preview: str
    source_type: str = ""
    section_title: str = ""


@dataclass
class CaseResult:
    """单题检索运行结果。"""
    case_id: str
    eligibility: Eligibility
    ks: list[int]
    retrieved: list[RetrievedChunkSnapshot]    # 保留原始排序，长度为实际返回数
    latency_ms: float
    error: str | None = None
    call_id: str | None = None                 # 审计关联
    audit_path: str | None = None
    legacy_log: str | None = None              # 关联到的 V1 retriever 日志（missing/ambiguous 显式记录）


# ── 计分模型 ──

@dataclass
class CaseMetrics:
    """单题命中指标。"""
    case_id: str
    eligible: bool
    n_local_targets: int
    n_local_groups: int
    page_hit: dict[int, bool]                  # K -> 任一必需本地 target 命中
    all_group_hit: dict[int, bool]             # K -> 所有本地组均命中
    rr: float                                  # 1/首个命中 target 排名，前10；无命中 0
    adjacent_hit: dict[int, bool]              # K -> 允许 ±1 页的诊断命中
    adjacent_only: dict[int, bool]             # K -> 仅相邻（非严格）命中
    gold_page_result_precision: dict[int, float]  # K -> 前K中落在 gold 页的 chunk 数 / 实际返回数
    n_unique_required_pages: int               # 唯一必需本地页数（document_id+pdf_page 去重）
    required_page_count: int                   # 同 n_unique_required_pages（任务口径名）
    hit_required_page_count: dict[int, int]    # K -> Top-K 命中的唯一必需页数
    missing_required_pages: dict[int, list]    # K -> 未命中的必需页 ["doc_id:Ppage"]
    required_page_coverage: dict[int, float]   # K -> 覆盖率 hit / required（0~1）
    recall_status: dict[int, str]              # K -> ZERO_RECALL | PARTIAL_RECALL | FULL_RECALL


@dataclass
class FailureClassification:
    """失败分类（仅对 AllGroupHit@max(K) 未完成的题）。"""
    case_id: str
    primary: str                               # 唯一主失败原因
    auxiliary: list[str]
    reason: str


# ── 聚合与运行结果模型 ──

@dataclass
class AggregateMetrics:
    """总体与切片指标。"""
    ks: list[int]
    n_eligible: int
    n_total: int
    page_hit: dict[int, float]                 # K -> Macro PageHit@K
    all_group_hit: dict[int, float]            # K -> AllGroupHit@K
    mrr10: float
    adjacent_page_hit: dict[int, float]
    gold_page_result_precision: dict[int, float]
    p0_page_hit10: float                       # P0 切片 PageHit@10
    required_page_coverage: dict[int, float]   # Macro RequiredPageCoverage@K（eligible 等权）
    p0_required_page_coverage10: float | None  # P0 切片 RequiredPageCoverage@10
    recall_status: dict[int, dict[str, int]]   # K -> {ZERO_RECALL, PARTIAL_RECALL, FULL_RECALL} 题数
    multi_page_page_hit: dict[int, float]      # multi_page 切片
    multi_page_all_group_hit: dict[int, float]
    multi_page_required_page_coverage: dict[int, float]
    multi_page_n: int
    by_section: dict[str, dict]                # section_id -> 指标
    by_route_raw: dict[str, dict]
    by_route_v2: dict[str, dict]
    by_priority: dict[str, dict]
    latency: dict[str, float]                  # avg / p50 / p95
    n_empty_retrieval: int
    n_errors: int
    n_missing_doc: int
    n_invalid_mapping: int
    n_external_only: int
    n_required_pages_gt_k: dict[int, int]      # K -> 必需唯一页数>K 的题数
    exclusion_breakdown: dict[str, int]        # 各排除状态计数


@dataclass
class DatasetValidationResult:
    """数据集校验结果。"""
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_cases: int = 0
    n_eligible: int = 0
    exclusion_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class BaselineRunResult:
    """一次完整运行结果。"""
    run_id: str
    company_id: str
    collection: str
    ks: list[int]
    output_dir: str | None
    completed: bool                            # 产物与审计完整（不要求全命中）
    status: str                                # completed | completed_with_case_errors | failed
    validation: DatasetValidationResult | None = None
    corpus_state: CorpusState | None = None
    aggregate: AggregateMetrics | None = None
    case_results: list[CaseResult] = field(default_factory=list)
    case_metrics: list[CaseMetrics] = field(default_factory=list)
    failures: list[FailureClassification] = field(default_factory=list)
    manifest_snapshot: dict | None = None
    metadata: dict = field(default_factory=dict)   # run_manifest.json 内容（输入/版本/参数/语料/环境快照）
