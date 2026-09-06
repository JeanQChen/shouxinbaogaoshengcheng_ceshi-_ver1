"""financial_v2 数据模型（dataclass + 枚举白名单 + 纯函数身份派生）。

本模块只定义声明式数据结构与无 I/O 的纯函数（身份/版本/比较键派生），不含
数据库访问、不含业务计算。所有持久化与编排代码共同引用同一份字段语义，避免
散落的中文名硬编码。

身份与版本关系（任务书 §7，v3 + A1 修订）：

    company_id + external_document_id
        → 公司作用域的内部 source_document_id（全局唯一、含 company 命名空间）
    source_document_id + file_sha256
        → 一次内容版本（financial_source_version，source_version 为其身份）
    内容版本(source_version) + extractor/mapping/normalization/dependency 版本
        → record_set_version（financial_record_set）
    record_set_version + 规范化后记录内容 + 坐标
        → record_id（source_financial_record，不可变）

职责划分（A1 修订）：
- financial_source_version 只保存登记时真实已知且不可变的文件事实（哈希、类型、
  大小、Evidence 文档关联、登记时间），不含任何抽取占位值（币种/scope/审计/抽取器）。
- 抽取产生的期间、币种、单位、scope、审计状态、抽取器名与各规则版本，保存到
  不可变 financial_record_set；未知值显式为 None/空，绝不默认猜测。

source_version 非全局唯一哈希：它是「某业务文档内的一次内容版本身份」，唯一性由
复合唯一键 UNIQUE(source_document_id, file_sha256) 保证。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "3"


# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# 三张主表。
STATEMENT_TYPES = ["balance_sheet", "income_statement", "cash_flow"]

# 期间类型。
PERIOD_TYPES = ["annual", "interim", "quarterly"]

# 合并/母公司口径。v3 修订 5：scope 明确即非不足；仅 scope 缺失/无法确认或
# 明确请求 consolidated 但只有 parent 数据时才 INSUFFICIENT_SCOPE。
STATEMENT_SCOPES = ["consolidated", "parent"]

# 币种（A 股 demo 以 CNY 为主，白名单可扩展）。
CURRENCIES = ["CNY"]

# 金额单位（对齐 V1 parsers.excel_parser metadata["unit"] 语义；百/千万为真实
# 样本「百万元/千万元」所需，A2 提取候选单位校验会用到）。
UNITS = ["yuan", "wan_yuan", "qian_yuan", "yi_yuan",
         "baiwan_yuan", "qianwan_yuan", "unknown"]

# 科目映射方式（FA-04：规则唯一匹配自动批准；LLM 仅候选；人工确认后进入快照）。
MAPPING_MODES = ["rule", "llm_suggested", "human_confirmed"]

# 来源文件类型。
FILE_TYPES = ["xlsx", "pdf"]

# 来源业务类别（FA-01：三张主表/附注电子 PDF + 用户上传 xlsx；征信报告仅登记）。
SOURCE_CLASSES = ["financial_statement", "credit_report", "other"]

# 审计状态。
AUDIT_STATUSES = ["audited", "unaudited", "unknown"]

# 主体匹配状态（§6.1：主体不一致按 Section Contract 语义 JOB_BLOCKED）。
SUBJECT_MATCH_STATUSES = ["matched", "mismatch", "unverified"]

# 对账组状态（§10.2）。
RECONCILE_STATES = ["MATCHED", "CONFLICT", "INSUFFICIENT_SCOPE", "SINGLE_SOURCE"]

# 决议理由代码（FA-05；OTHER_WITH_NOTE 必须填 note）。
RESOLUTION_REASON_CODES = [
    "AUDITED_SOURCE",
    "LATEST_RESTATEMENT",
    "SCOPE_MATCH",
    "PERIOD_MATCH",
    "CORRECTED_MATERIAL",
    "OTHER_WITH_NOTE",
]

# 决议有效性状态（存于 resolution_validity 关系表，不修改决议本体）。
RESOLUTION_VALIDITY_STATUSES = ["active", "stale", "superseded"]

# 快照有效性状态（存于 snapshot_validity 关系表，不修改快照本体）。
SNAPSHOT_VALIDITY_STATUSES = ["valid", "stale", "superseded"]

# 快照异常类型（固化未纳入计算的状态摘要，MetricResult reason_code 来源）。
SNAPSHOT_EXCEPTION_TYPES = [
    "missing_item",        # 缺输入科目
    "excluded_item",       # 被排除的科目
    "unresolved_conflict", # 未解决冲突
    "insufficient_scope",  # scope 不足
    "unconfirmed_mapping", # 未确认映射（LLM 候选等）
]

# 指标计算状态与 reason_code。
METRIC_STATUSES = [
    "ok",
    "missing_input",
    "zero_denominator",
    "insufficient_period",
    "conflict_blocked",
]

# 进度事件状态。
PROGRESS_STATUSES = ["running", "completed", "failed"]

# 财务阶段名（复用 Phase 1 ProgressEvent 语义，任务书 §14）。
STAGES = [
    "SOURCE_VALIDATION",
    "EXTRACTION",
    "NORMALIZATION",
    "RECONCILIATION",
    "WAITING_CONFIRMATION",
    "SNAPSHOT_BUILD",
    "CALCULATION",
    "COMPLETED",
    "FAILED",
]

# 隔离对象类型（quarantine 表，不修改受保护历史行）。
QUARANTINE_OBJECT_TYPES = [
    "financial_source_version",
    "financial_record_set",
    "source_financial_record",
    "resolution_record",
    "financial_snapshot",
    "snapshot_item",
    "extracted_financial_cell",   # A2/A3 原始候选层
    "mapping_resolution",         # A5 科目映射确认
    "reconciliation_run",         # A4 对账运行
]

# 候选提取状态（§4.1 提取时点，不可变）。下游 MAPPING_REQUIRED / NORMALIZATION_REQUIRED /
# READY_FOR_RECORD / REJECTED 为派生状态，由 extraction_issue / record 关联表达。
CANDIDATE_STATUSES = ["EXTRACTED", "EMPTY_OR_NOT_APPLICABLE", "PARSE_FAILED", "CLASSIFICATION_REQUIRED"]

# 抽取 / 映射 / 标准化问题类型（A2/A3/A4 记录；A5 集中确认解决）。
# A3 追加（§6.6 失败分类）；HEADER_UNRESOLVED 为 A2/A3 共用的表头未解析（A2 已引用）。
EXTRACTION_ISSUE_TYPES = [
    "PARSE_FAILED",
    "CLASSIFICATION_REQUIRED",
    "MAPPING_REQUIRED",
    "NORMALIZATION_REQUIRED",
    "UNIT_UNRESOLVED",
    "PERIOD_UNRESOLVED",
    "SCOPE_UNRESOLVED",
    "CURRENCY_UNRESOLVED",
    "SUBJECT_MISMATCH",
    "UNSUPPORTED_FORMAT",
    "FILE_HASH_MISMATCH",
    "HEADER_UNRESOLVED",
    "LOW_TEXT_QUALITY",
    "TABLE_NOT_FOUND",
    "TABLE_GRID_UNAVAILABLE",
    "CELL_PARSE_FAILED",
    "CROSS_PAGE_UNCERTAIN",
    "DEPENDENCY_ERROR",
    "DOCUMENT_LINK_UNAVAILABLE",
    "CHECK_FAILED",              # A4 同源勾稽失败（§7.4：勾稽不满足 → 生成 issue，不改来源值）
    "RECONCILIATION_CONFLICT",   # A4 跨来源冲突（§7.6 CONFLICT：差异无法由展示舍入解释）
    "INSUFFICIENT_SCOPE",        # A4 关键维度不完整（§7.6：无法形成有效比较组）
]

# 同源勾稽状态（§7.4）。
CHECK_STATUSES = ["PASS", "FAIL", "NOT_RUN_MISSING_INPUT"]

# 同源勾稽类型（§7.4 首版四类）。
CHECK_TYPES = [
    "BALANCE_SHEET_IDENTITY",      # 资产总计 ≈ 负债合计 + 所有者权益合计
    "CASH_BALANCE_RECONCILIATION", # 期末现金 ≈ 期初 + 净增加额（字段可得时）
    "NET_INCOME_CASH_START",       # 利润表净利润 vs 现金流量表补充资料起点（字段可得时）
    "REVENUE_COST_BREAKDOWN",      # 收入/成本附注构成合计 vs 主表（字段可得且口径一致时）
]


# ---------------------------------------------------------------------------
# 纯函数：身份 / 版本 / 比较键派生（无 I/O）
# ---------------------------------------------------------------------------

def _sha256_hex(raw: str, length: int | None = None) -> str:
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return h if length is None else h[:length]


def scope_source_document_id(company_id: str, external_document_id: str) -> str:
    """把外部业务文档编号规范化为公司作用域的内部稳定 source_document_id。

    source_document_id 全局唯一（表主键），身份含 company 命名空间：同一外部编号
    用于不同公司得到不同内部 id，绝不碰撞/串数据（A1 修订 4）。
    """
    return "sd-" + _sha256_hex(f"{company_id}|{external_document_id}", 16)


def derive_source_version(source_document_id: str, file_sha256: str) -> str:
    """内容版本身份：source_document_id + file_sha256 的稳定派生。

    不包含时间戳；同一业务文档的同一文件内容永远得到相同 source_version。
    唯一性由复合唯一键 UNIQUE(source_document_id, file_sha256) 兜底，而非本哈希
    的全局唯一性。
    """
    return "sv-" + _sha256_hex(f"{source_document_id}|{file_sha256}", 24)


def derive_record_set_version(
    source_version: str,
    extractor_version: str,
    mapping_rule_version: str,
    normalization_rule_version: str,
    dependency_versions: dict[str, str] | None = None,
) -> str:
    """记录集合版本：内容版本 + 处理规则身份 + 依赖版本的稳定派生。"""
    dep = json.dumps(dependency_versions or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    raw = "|".join([
        source_version,
        extractor_version,
        mapping_rule_version,
        normalization_rule_version,
        dep,
    ])
    return "rs-" + _sha256_hex(raw, 24)


def comparison_key(
    company_id: str,
    standard_item_code: str,
    statement_type: str,
    report_period: str,
    period_type: str,
    statement_scope: str,
    currency: str,
    restatement_version: str,
) -> str:
    """对账比较键（任务书 §6.4）：只有 8 字段完整一致才可比较。

    未知字段不得用空串互配成同组（v3 修订 5）；本函数返回稳定哈希作为 group 身份。
    """
    raw = json.dumps({
        "company_id": company_id,
        "standard_item_code": standard_item_code,
        "statement_type": statement_type,
        "report_period": report_period,
        "period_type": period_type,
        "statement_scope": statement_scope,
        "currency": currency,
        "restatement_version": restatement_version,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "ck-" + _sha256_hex(raw, 32)


def record_identity_fields(record: "SourceFinancialRecord") -> dict:
    """记录身份字段：规范化内容 + 坐标（不含原始值，原始值属 provenance）。"""
    return {
        "company_id": record.company_id,
        "standard_item_code": record.standard_item_code,
        "statement_type": record.statement_type,
        "report_period": record.report_period,
        "period_type": record.period_type,
        "statement_scope": record.statement_scope,
        "currency": record.currency,
        "restatement_version": record.restatement_version,
        "std_value": record.std_value,
        "std_unit": record.std_unit,
        "std_currency": record.std_currency,
        "locator": locator_to_dict(record.locator),
    }


def derive_record_id(record_set_version: str, identity: dict) -> str:
    """记录不可变身份：record_set_version + 规范化内容 + 坐标。"""
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "rec-" + _sha256_hex(f"{record_set_version}|{canonical}", 32)


def derive_candidate_id(
    record_set_version: str,
    locator: "SourceLocator | None",
    raw_item_text: str,
    raw_value_text: str | None,
) -> str:
    """原始候选不可变身份：record_set_version + 坐标 + 原始文本/值。

    同一文件、同一抽取器版本重复运行 → 同一 record_set_version → 同一坐标与原始
    内容 → 同一 candidate_id（幂等复用，§7）。不含时间戳。
    """
    raw = json.dumps({
        "record_set_version": record_set_version,
        "locator": locator_to_dict(locator),
        "raw_item_text": raw_item_text,
        "raw_value_text": raw_value_text,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "cand-" + _sha256_hex(raw, 32)


# ---------------------------------------------------------------------------
# SourceLocator（显式联合类型，任务书 §6.2）
# ---------------------------------------------------------------------------

@dataclass
class ExcelCellLocator:
    """Excel 单元格坐标（1-based 行/列，另存 A1 地址）。"""

    sheet_name: str
    row_number: int
    column_number: int
    cell_address: str
    row_header: str | None = None
    column_header: str | None = None
    unit_text: str | None = None


@dataclass
class PdfCellLocator:
    """电子 PDF 单元格坐标（1-based 物理页 + 表格/单元格 bbox）。"""

    document_id: str
    document_version: str
    pdf_page: int
    row_index: int
    column_index: int
    bbox: list[float]              # [x0, top, x1, bottom]
    table_id: str | None = None
    row_header: str | None = None
    column_header: str | None = None
    unit_text: str | None = None


@dataclass
class SourceLocator:
    """显式联合类型容器：kind 决定承载哪种 locator。"""

    kind: str                      # "excel" | "pdf"
    excel: ExcelCellLocator | None = None
    pdf: PdfCellLocator | None = None


def locator_to_dict(locator: SourceLocator | None) -> dict | None:
    """把 SourceLocator 序列化为可存储 JSON 的 dict（validator 保证合法性）。"""
    if locator is None:
        return None
    if locator.kind == "excel" and locator.excel is not None:
        e = locator.excel
        return {
            "kind": "excel",
            "sheet_name": e.sheet_name,
            "row_number": e.row_number,
            "column_number": e.column_number,
            "cell_address": e.cell_address,
            "row_header": e.row_header,
            "column_header": e.column_header,
            "unit_text": e.unit_text,
        }
    if locator.kind == "pdf" and locator.pdf is not None:
        p = locator.pdf
        return {
            "kind": "pdf",
            "document_id": p.document_id,
            "document_version": p.document_version,
            "pdf_page": p.pdf_page,
            "table_id": p.table_id,
            "row_index": p.row_index,
            "column_index": p.column_index,
            "bbox": p.bbox,
            "row_header": p.row_header,
            "column_header": p.column_header,
            "unit_text": p.unit_text,
        }
    return {"kind": locator.kind}


def locator_from_dict(d: dict | None) -> SourceLocator | None:
    """从存储 JSON 反序列化 SourceLocator。"""
    if not d:
        return None
    kind = d.get("kind")
    if kind == "excel":
        return SourceLocator(kind="excel", excel=ExcelCellLocator(
            sheet_name=d.get("sheet_name", ""),
            row_number=d.get("row_number", 0),
            column_number=d.get("column_number", 0),
            cell_address=d.get("cell_address", ""),
            row_header=d.get("row_header"),
            column_header=d.get("column_header"),
            unit_text=d.get("unit_text"),
        ))
    if kind == "pdf":
        return SourceLocator(kind="pdf", pdf=PdfCellLocator(
            document_id=d.get("document_id", ""),
            document_version=d.get("document_version", ""),
            pdf_page=d.get("pdf_page", 0),
            row_index=d.get("row_index", 0),
            column_index=d.get("column_index", 0),
            bbox=list(d.get("bbox", [])),
            table_id=d.get("table_id"),
            row_header=d.get("row_header"),
            column_header=d.get("column_header"),
            unit_text=d.get("unit_text"),
        ))
    return SourceLocator(kind=kind)


# ---------------------------------------------------------------------------
# 来源登记 / 内容版本 / 记录集合
# ---------------------------------------------------------------------------

@dataclass
class FinancialSourceContext:
    """登记一份来源文件所需的上下文（CLI / 未来 ingest 层提供）。"""

    company_id: str
    source_name: str
    source_class: str
    external_document_id: str | None = None   # 外部业务文档编号（公司范围内稳定）
    declared_company_name: str | None = None
    detected_company_name: str | None = None


@dataclass
class FinancialSourceDocument:
    """业务文档登记头（内部 source_document_id 全局唯一、含公司命名空间）。"""

    source_document_id: str
    company_id: str
    source_name: str
    source_class: str
    declared_company_name: str | None
    detected_company_name: str | None
    subject_match_status: str
    created_at: str


@dataclass
class FinancialSourceVersion:
    """一次内容版本（已提交后不可变，仅保存登记时真实已知的文件事实）。

    A1 修订：不含任何抽取占位值（币种/scope/审计/抽取器/期间）。抽取产物见
    FinancialRecordSet。
    """

    source_version: str
    source_document_id: str
    file_sha256: str
    file_type: str
    file_size: int
    document_id: str | None                # PDF 关联 Phase 1 Evidence Registry
    document_version: str | None
    created_at: str


@dataclass
class FinancialRecordSet:
    """一次抽取/映射/标准化规则组合产生的记录集合（已提交后不可变）。

    承载抽取产物：规则版本（extractor/mapping/normalization/dependency）是身份
    一部分（参与 record_set_version 派生）；期间/币种/单位/scope/审计状态为
    抽取事实，未知时显式为 None/空，不默认猜测。
    """

    record_set_version: str
    source_version: str
    extractor_name: str | None
    extractor_version: str
    mapping_rule_version: str
    normalization_rule_version: str
    dependency_versions: dict[str, str]
    report_periods: list[str]
    currency: str | None
    unit: str | None
    statement_scope: str | None
    audit_status: str | None
    block_count: int
    record_count: int
    created_at: str


# ---------------------------------------------------------------------------
# 来源记录 / 对账 / 决议 / 快照 / 公式
# ---------------------------------------------------------------------------

@dataclass
class SourceFinancialRecord:
    """不可覆盖的原始来源记录（§6.3）。"""

    record_id: str
    record_set_version: str
    company_id: str
    standard_item_code: str
    statement_type: str
    raw_item_text: str
    raw_value: float | None
    raw_unit: str
    raw_currency: str
    std_value: float | None
    std_unit: str
    std_currency: str
    conversion_rule_version: str
    report_period: str
    period_type: str
    statement_scope: str
    currency: str
    restatement_version: str
    locator: SourceLocator | None
    mapping_mode: str
    confidence: float
    record_hash: str
    quality_flags: list[str]
    created_at: str
    candidate_id: str | None = None   # 溯源指针：指向原始候选（A2/A3 生成，A1 记录可为 None）


@dataclass
class ReconciliationGroup:
    """对账组（§6.4）：按 comparison_key 聚合的候选事实。"""

    comparison_key: str
    state: str
    candidate_record_ids: list[str]
    std_values: list[float | None]
    diff_detail: dict
    created_at: str


@dataclass
class ResolutionRecord:
    """人工决议审计事件（本体不可变，§6.5 / v3 修订 6）。"""

    resolution_id: str
    group_id: str
    candidate_set_hash: str
    source_hashes: list[str]
    comparison_key: str
    rule_versions: dict[str, str]
    accepted_record_ids: list[str]
    rejected_record_ids: list[str]
    reason_code: str
    note: str | None
    operator: str
    confirmed_at: str


@dataclass
class FinancialSnapshot:
    """财务快照（不可变，§6.6 / v3 修订 3：无 stale/retired 状态列）。"""

    snapshot_id: str
    snapshot_version: str
    company_id: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    source_versions: list[str]
    resolution_versions: list[str]
    created_at: str


@dataclass
class SnapshotItem:
    """快照条目：标准值只出现一次，source_refs 恒非空，resolution_id 可空。"""

    snapshot_id: str
    comparison_key: str
    standard_item_code: str
    amount: float | None
    unit: str | None
    source_refs: list[str]       # 至少一个 SourceFinancialRecord.record_id
    resolution_id: str | None


@dataclass
class SnapshotException:
    """固化未纳入计算的状态摘要（§6.6 缺失/排除/冲突/scope/未确认）。"""

    snapshot_id: str
    comparison_key: str
    standard_item_code: str
    exception_type: str
    blocking_reason: str
    impact_scope: list[str]
    detail: dict


@dataclass
class FormulaDefinition:
    """公式定义（§6.7，A6 填充内容）。"""

    formula_id: str
    formula_version: str
    input_item_codes: list[str]
    period_requirement: str
    scope_requirement: str
    python_impl: str
    missing_rule: str
    zero_denominator_rule: str
    rounding_rule: str
    effective_at: str


@dataclass
class MetricResult:
    """指标计算结果（§6.7，A6 填充）。"""

    snapshot_id: str
    formula_id: str
    formula_version: str
    period: str
    value: float | None
    unit: str | None
    input_refs: list[str]
    status: str
    reason_code: str | None


# ---------------------------------------------------------------------------
# 进度事件 / checkpoint（复用 Phase 1 字段语义，任务书 §14）
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
    """产物成功持久化后的一次恢复断点。"""

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


# ---------------------------------------------------------------------------
# A2/A3 原始候选层（§4.1）：抽取时点事实，不可变；下游处理用追加事件/派生记录表达
# ---------------------------------------------------------------------------

@dataclass
class ExtractedFinancialCell:
    """原始抽取候选：真实坐标 + 原始文本/值，未知维度显式 None/候选，绝不伪装标准值。

    parsed_numeric_value / cached_formula_value / min_display_increment 用 Decimal
    承载（库内以 TEXT 保存十进制字符串，往返不损失审计精度）。status 为提取时点
    不可变状态；下游 MAPPING_REQUIRED / NORMALIZATION_REQUIRED / READY_FOR_RECORD /
    REJECTED 由 extraction_issue 与 record 关联表达，不 UPDATE 本行。
    """

    candidate_id: str
    record_set_version: str
    company_id: str
    source_version: str
    statement_type_candidate: str | None
    raw_item_text: str
    raw_value_text: str | None
    parsed_numeric_value: Decimal | None
    formula_text: str | None
    cached_formula_value: Decimal | None
    period_text: str | None
    period_candidate: str | None
    period_type_candidate: str | None
    scope_candidate: str | None
    currency_candidate: str | None
    unit_candidate: str | None
    restatement_candidate: str | None
    min_display_increment: Decimal | None
    locator: SourceLocator | None
    detection_evidence: dict
    status: str
    quality_flags: list[str]
    created_at: str


@dataclass
class MappingRule:
    """版本化确定性科目映射规则（A4 mapping，非不可变事实；新版本追加新行）。"""

    rule_id: str
    rule_version: str
    statement_type: str
    standard_item_code: str
    aliases: list[str]
    exclude_words: list[str]
    priority: int
    effective_at: str


@dataclass
class ExtractionIssue:
    """抽取 / 映射 / 标准化问题（不可变事实；解决状态由 resolution 关联推导）。"""

    issue_id: str
    record_set_version: str
    issue_type: str
    candidate_id: str | None
    comparison_key: str | None
    detail: dict
    created_at: str


# ---------------------------------------------------------------------------
# A4 对账运行（§7.8）：run 绑定公司 / 输入 Record Set / 规则版本 / input hash
# ---------------------------------------------------------------------------

@dataclass
class ReconciliationRun:
    """一次对账运行（不可变；新运行不覆盖旧运行，current 用独立指针）。"""

    run_id: str
    company_id: str
    input_record_set_ids: list[str]
    rule_versions: dict[str, str]
    input_hash: str
    created_at: str


@dataclass
class ReconciliationGroupResult:
    """run 作用域的对账组结果（§7.6/7.7，std_values 用十进制字符串保存）。"""

    run_id: str
    comparison_key: str
    state: str
    candidate_record_ids: list[str]
    std_values: list[str]
    diff_detail: dict
    impact_item_codes: list[str]
    impact_section_contracts: list[str]
    created_at: str


@dataclass
class ReconciliationCheck:
    """同源勾稽结果（§7.4，版本化 Decimal 计算 + 输入/差异/容差）。"""

    check_id: str
    run_id: str
    record_set_version: str
    check_type: str
    input_record_ids: list[str]
    left_value: str | None
    right_value: str | None
    diff: str | None
    tolerance: str | None
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# A5 科目映射确认（§8.4，不可变审计事件）
# ---------------------------------------------------------------------------

@dataclass
class MappingResolution:
    """人工科目映射确认（chosen_item_code 为 None 表示「无法确认/需补充材料」）。"""

    resolution_id: str
    record_set_version: str
    candidate_id: str
    issue_id: str | None
    chosen_item_code: str | None
    reason_code: str
    note: str | None
    operator: str
    confirmed_at: str
