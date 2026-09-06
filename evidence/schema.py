"""Evidence Architecture 数据模型（dataclass + 枚举白名单）。

本模块只定义声明式数据结构，不含 I/O、不含业务计算。所有持久化与编排
代码共同引用同一份字段语义，避免散落的中文名硬编码。

三个正交的版本身份（与 DESIGN_V2.md §5.7 / 任务书 §7 对齐）：
- document_version    输入身份，由文件内容哈希派生；
- evidence_set_version 处理规则身份，由 schema|parser|builder 版本哈希派生；
- run_id              单次执行身份，uuid 唯一。

表格能力（E1-01）：Phase 1 冻结 table/table_row 枚举，但现有纯文本 parser
只能产出 paragraph/heading；结构化表格抽取留待坐标可行性验证通过后启用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 版本常量（决定 evidence_set_version 的处理规则身份）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1"
PARSER_VERSION = "v1"      # 冻结的 V1 parsers.pdf_parser 逻辑版本
BUILDER_VERSION = "1"

# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# 证据类型：首版仅 paragraph | heading 可由纯文本 parser 产生；
# table | table_row 保留，仅在真实结构化抽取成功后使用（E1-01）。
EVIDENCE_TYPES = ["paragraph", "heading", "table", "table_row"]

# 来源类型（与 V1 indexer._infer_doc_type 对齐，见 retrieval/indexer.py）。
SOURCE_TYPES = ["annual_report", "debt_circular", "other"]

# 材料分组（行业公司 / 财务 / 项目）。
MATERIAL_GROUPS = ["company_industry", "financial", "project"]

# 文档版本状态：registered（已登记未构建）/ current（当前可用）/ superseded（被新版本取代）。
DOCUMENT_STATUSES = ["registered", "current", "superseded"]

# 证据集合状态：building（事务内临时）/ current（当前可用）/ retired（停用或被取代）。
EVIDENCE_SET_STATUSES = ["building", "current", "retired"]

# 进度事件状态：running / completed / failed。
PROGRESS_STATUSES = ["running", "completed", "failed"]

# 阶段名（DESIGN_V2.md §5.7 首版最小集合）。
STAGES = [
    "VALIDATING_INPUT",
    "PARSING_DOCUMENT",
    "BUILDING_EVIDENCE",
    "PERSISTING_EVIDENCE",
    "COMPLETED",
    "FAILED",
]

# 表格结构探测状态（E1-01）。
PROBE_STATUSES = [
    "PROBE_NOT_RUN",
    "PROBE_DEPENDENCY_MISSING",
    "TABLE_STRUCTURE_AVAILABLE",
    "TABLE_STRUCTURE_UNAVAILABLE",
]


# ---------------------------------------------------------------------------
# Document / Evidence 模型
# ---------------------------------------------------------------------------

@dataclass
class DocumentContext:
    """登记一份业务文档所需的上下文（CLI / 未来 ingest 层提供）。

    - document_id 为业务文档稳定身份；为 None 时由 store 自动生成并在登记表
      持久化，后续通过内容哈希识别复用（E1-03）。
    - 文件名仅作为 source_name，不作为唯一身份。
    """

    company_id: str
    source_name: str
    source_type: str
    material_group: str
    source_path: str | None = None
    document_id: str | None = None
    declared_company_name: str | None = None
    detected_company_names: list[str] = field(default_factory=list)


@dataclass
class DocumentRecord:
    """登记后的文档版本记录（对应 documents 表一行）。"""

    document_id: str
    document_version: str
    company_id: str
    source_name: str
    source_path: str | None
    source_type: str
    material_group: str
    file_sha256: str
    file_size: int
    page_count: int | None
    declared_company_name: str | None
    detected_company_names: list[str]
    parser_version: str
    status: str
    quality_flags: list[str]
    created_at: str


@dataclass
class EvidenceBlock:
    """一条可追溯证据（对应 evidence_blocks 表一行）。

    page_number 为 PDF 1-based 物理页序号；section_path 为章节层级路径
    （V1 parser 只产出单层标题，故首版最多一个元素）。
    """

    evidence_id: str
    schema_version: str
    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    source_name: str
    source_type: str
    source_uri: str | None
    page_number: int
    block_index: int
    section_path: list[str]
    evidence_type: str
    text: str
    structured_payload: dict | None
    report_period: str | None
    published_at: str | None
    entities: list[str]
    quality_flags: list[str]
    content_hash: str
    builder_version: str
    created_at: str


@dataclass
class EvidenceRef:
    """指向一条 Evidence 的引用（引用守卫，防误删）。"""

    evidence_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_number: int | None
    source_name: str


@dataclass
class CommitResult:
    """commit_document 的返回结果。"""

    document: DocumentRecord
    evidence_set_version: str
    written: int          # 本次新写入的 Evidence 数
    reused: int           # 本次幂等复用的 Evidence 数
    checkpoint_id: str
    evidence_ids: list[str]


@dataclass
class DeleteCheckResult:
    """删除前检查结果（E1-04：Phase 1 仅检查不物理删除）。"""

    deletable: bool
    referenced: bool
    references: list[str]
    reason: str


# ---------------------------------------------------------------------------
# ProgressEvent / Checkpoint 模型
# ---------------------------------------------------------------------------

@dataclass
class ProgressEvent:
    """单次进度事件（真实阶段 / 计数 / 错误，不展示模型思维链）。"""

    event_id: str
    run_id: str
    stage_id: str
    status: str
    message_code: str
    completed_units: int | None
    total_units: int | None
    error_code: str | None
    recoverable: bool
    created_at: str


@dataclass
class Checkpoint:
    """产物成功持久化后的一次恢复断点。

    state_version 为 checkpoint 结构版本；artifact_refs 为已持久化产物引用；
    input_hashes / dependency_versions 用于恢复时校验是否可续跑。
    """

    checkpoint_id: str
    run_id: str
    stage_id: str
    state_version: int
    artifact_refs: list[str]
    input_hashes: dict[str, str]
    dependency_versions: dict[str, str]
    resolution_refs: list[str]
    completed_unit_ids: list[str]
    created_at: str


@dataclass
class ResumeResult:
    """恢复决策结果。"""

    run_id: str
    can_resume: bool
    checkpoint: Checkpoint | None
    reason: str | None
