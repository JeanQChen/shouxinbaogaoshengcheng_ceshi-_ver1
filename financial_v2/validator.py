"""financial_v2 运行时 Schema / 状态 / 坐标校验（写库前强制调用）。

本模块为纯函数库（无 I/O），只读 schema，不写库、不解析文件。store 层在任何写入前
必须调用对应 validate_*，保证非法对象 / 非法坐标 / 非法状态在写库前被拒绝；SQLite
唯一约束与不可变触发器仍保留作为第二道防线。

校验范围（A1 修订）：
- 必填字符串非空、枚举白名单；
- 内容版本只允许文件事实，未知抽取字段必须为 None（FinancialRecordSet）；
- record_set_version 与 derive_record_set_version 重算一致（依赖版本纳入身份）；
- Excel 坐标 1-based（row/col >= 1）且 cell_address 与 row/col 一致；
- PDF 坐标 1-based 物理页、bbox 为 4 个有限数值且 x1>x0、bottom>top；
- record_id 与 derive_record_id 重算一致（防篡改坐标/内容却沿用旧 id）；
- record_hash 与全字段重算一致（覆盖原始科目文本 / raw 值单位币种 / 标准值单位
  币种 / conversion rule / mapping mode / 期间 scope restatement / 完整 locator）；
- 决议 OTHER_WITH_NOTE 必须含 note；
- 主体匹配状态、对账状态、有效性状态、指标状态属于白名单。
"""

from __future__ import annotations

import hashlib
import json
import math

from financial_v2 import schema as S


class ValidationError(ValueError):
    """financial_v2 校验失败（含对象与原因）。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def _nonempty(v: str | None, label: str) -> None:
    _require(bool(v) and bool(str(v).strip()), f"{label} 不能为空")


# ---------------------------------------------------------------------------
# 来源登记上下文 / 业务文档头 / 内容版本
# ---------------------------------------------------------------------------

def validate_source_context(ctx: S.FinancialSourceContext) -> None:
    _nonempty(ctx.company_id, "context.company_id")
    _nonempty(ctx.source_name, "context.source_name")
    _require(ctx.source_class in S.SOURCE_CLASSES,
             f"context.source_class 非法: {ctx.source_class!r}")
    if ctx.external_document_id is not None:
        _nonempty(ctx.external_document_id, "context.external_document_id")


def validate_source_document(doc: S.FinancialSourceDocument) -> None:
    _nonempty(doc.source_document_id, "source_document_id")
    _nonempty(doc.company_id, "company_id")
    _nonempty(doc.source_name, "source_name")
    _require(doc.source_class in S.SOURCE_CLASSES,
             f"source_class 非法: {doc.source_class!r}")
    _require(doc.subject_match_status in S.SUBJECT_MATCH_STATUSES,
             f"subject_match_status 非法: {doc.subject_match_status!r}")
    _nonempty(doc.created_at, "created_at")


def validate_source_version(v: S.FinancialSourceVersion) -> None:
    """内容版本只保存文件事实，不含抽取占位值（A1 修订 1）。"""
    _nonempty(v.source_version, "source_version")
    _nonempty(v.source_document_id, "source_document_id")
    _nonempty(v.file_sha256, "file_sha256")
    _require(v.file_type in S.FILE_TYPES, f"file_type 非法: {v.file_type!r}")
    _require(v.file_size >= 0, f"file_size 必须非负: {v.file_size!r}")
    _nonempty(v.created_at, "created_at")


def validate_record_set(rs: S.FinancialRecordSet) -> None:
    _nonempty(rs.record_set_version, "record_set_version")
    _nonempty(rs.source_version, "source_version")
    _nonempty(rs.extractor_version, "extractor_version")
    _nonempty(rs.mapping_rule_version, "mapping_rule_version")
    _nonempty(rs.normalization_rule_version, "normalization_rule_version")
    # 抽取事实：未知显式 None（不得默认猜测）。
    if rs.currency is not None:
        _require(rs.currency in S.CURRENCIES, f"currency 非法: {rs.currency!r}")
    if rs.unit is not None:
        _require(rs.unit in S.UNITS, f"unit 非法: {rs.unit!r}")
    if rs.statement_scope is not None:
        _require(rs.statement_scope in S.STATEMENT_SCOPES,
                 f"statement_scope 非法: {rs.statement_scope!r}")
    if rs.audit_status is not None:
        _require(rs.audit_status in S.AUDIT_STATUSES,
                 f"audit_status 非法: {rs.audit_status!r}")
    _require(rs.block_count >= 0, f"block_count 必须非负: {rs.block_count!r}")
    _require(rs.record_count >= 0, f"record_count 必须非负: {rs.record_count!r}")
    _nonempty(rs.created_at, "created_at")
    # v6 溯源指针：旧 v5 行允许 None（兼容读取）；新 normalized Record Set 由调用方写入非空。
    if rs.input_candidate_set_version is not None:
        _nonempty(rs.input_candidate_set_version, "input_candidate_set_version")
    # record_set_version 与派生规则重算一致（依赖版本纳入身份，A1 修订 5）。
    recomputed = S.derive_record_set_version(
        rs.source_version, rs.extractor_version, rs.mapping_rule_version,
        rs.normalization_rule_version, rs.dependency_versions)
    _require(rs.record_set_version == recomputed,
             f"record_set_version 与派生规则重算不一致: {rs.record_set_version!r}")


# ---------------------------------------------------------------------------
# SourceLocator 坐标
# ---------------------------------------------------------------------------

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def validate_locator(locator: S.SourceLocator | None) -> None:
    """校验坐标合法性（任务书 A1：非法坐标在写库前被拒绝）。"""
    _require(locator is not None, "locator 不能为空")
    _require(locator.kind in ("excel", "pdf"), f"locator.kind 非法: {locator.kind!r}")

    if locator.kind == "excel":
        e = locator.excel
        _require(e is not None, "kind=excel 必须承载 excel locator")
        _nonempty(e.sheet_name, "excel.sheet_name")
        _require(isinstance(e.row_number, int) and e.row_number >= 1,
                 f"excel.row_number 必须为 >=1 的整数: {e.row_number!r}")
        _require(isinstance(e.column_number, int) and e.column_number >= 1,
                 f"excel.column_number 必须为 >=1 的整数: {e.column_number!r}")
        _nonempty(e.cell_address, "excel.cell_address")
    else:  # pdf
        p = locator.pdf
        _require(p is not None, "kind=pdf 必须承载 pdf locator")
        _nonempty(p.document_id, "pdf.document_id")
        _nonempty(p.document_version, "pdf.document_version")
        _require(isinstance(p.pdf_page, int) and p.pdf_page >= 1,
                 f"pdf.pdf_page 必须为 >=1 的整数: {p.pdf_page!r}")
        _require(isinstance(p.row_index, int) and p.row_index >= 0,
                 f"pdf.row_index 必须为非负整数: {p.row_index!r}")
        _require(isinstance(p.column_index, int) and p.column_index >= 0,
                 f"pdf.column_index 必须为非负整数: {p.column_index!r}")
        bbox = p.bbox
        _require(isinstance(bbox, list) and len(bbox) == 4
                 and all(_finite(v) for v in bbox),
                 f"pdf.bbox 必须为 4 个有限数值: {bbox!r}")
        x0, top, x1, bottom = bbox
        _require(x1 > x0, f"pdf.bbox x1 必须大于 x0: {bbox!r}")
        _require(bottom > top, f"pdf.bbox bottom 必须大于 top: {bbox!r}")


# ---------------------------------------------------------------------------
# 来源记录
# ---------------------------------------------------------------------------

def _d(v) -> str | None:
    """Decimal/float → JSON 安全十进制字符串（None 透传；记录权威值为 Decimal）。"""
    return str(v) if v is not None else None


def _record_hash(record: S.SourceFinancialRecord) -> str:
    """记录内容哈希：覆盖参与身份判定的字段 + 原始值 + 坐标，防静默篡改。

    覆盖范围（A1 修订 11）：原始科目文本；raw value/unit/currency；标准 value/unit/
    currency；conversion rule；mapping mode；期间、scope、restatement；完整 locator。
    """
    raw = json.dumps({
        "record_set_version": record.record_set_version,
        "company_id": record.company_id,
        "standard_item_code": record.standard_item_code,
        "statement_type": record.statement_type,
        "raw_item_text": record.raw_item_text,
        "raw_value": _d(record.raw_value),
        "raw_unit": record.raw_unit,
        "raw_currency": record.raw_currency,
        "std_value": _d(record.std_value),
        "std_unit": record.std_unit,
        "std_currency": record.std_currency,
        "conversion_rule_version": record.conversion_rule_version,
        "report_period": record.report_period,
        "period_type": record.period_type,
        "statement_scope": record.statement_scope,
        "currency": record.currency,
        "restatement_version": record.restatement_version,
        "locator": S.locator_to_dict(record.locator),
        "mapping_mode": record.mapping_mode,
        "confidence": record.confidence,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_record(record: S.SourceFinancialRecord) -> None:
    """校验单条来源记录（字段白名单 + 坐标 + id/hash 重算一致）。"""
    _nonempty(record.record_id, "record_id")
    _nonempty(record.record_set_version, "record_set_version")
    _nonempty(record.company_id, "company_id")
    _nonempty(record.standard_item_code, "standard_item_code")
    _require(record.statement_type in S.STATEMENT_TYPES,
             f"statement_type 非法: {record.statement_type!r}")
    _require(record.period_type in S.PERIOD_TYPES,
             f"period_type 非法: {record.period_type!r}")
    _require(record.statement_scope in S.STATEMENT_SCOPES,
             f"statement_scope 非法: {record.statement_scope!r}")
    _require(record.currency in S.CURRENCIES, f"currency 非法: {record.currency!r}")
    _require(record.raw_unit in S.UNITS, f"raw_unit 非法: {record.raw_unit!r}")
    _require(record.raw_currency in S.CURRENCIES,
             f"raw_currency 非法: {record.raw_currency!r}")
    _require(record.std_unit in S.UNITS, f"std_unit 非法: {record.std_unit!r}")
    _require(record.std_currency in S.CURRENCIES,
             f"std_currency 非法: {record.std_currency!r}")
    _require(record.mapping_mode in S.MAPPING_MODES,
             f"mapping_mode 非法: {record.mapping_mode!r}")
    _require(record.restatement_version is not None and bool(record.restatement_version),
             "restatement_version 不能为空（未知重述版本需显式占位，不得空串并组）")
    _require(0.0 <= record.confidence <= 1.0,
             f"confidence 必须 ∈[0,1]: {record.confidence!r}")

    validate_locator(record.locator)

    # record_id 与规范化内容 + 坐标重算一致（防篡改后沿用旧 id）。
    recomputed_id = S.derive_record_id(
        record.record_set_version, S.record_identity_fields(record))
    _require(record.record_id == recomputed_id,
             f"record_id 与内容/坐标重算不一致: {record.record_id!r}")

    # record_hash 与全字段重算一致。
    recomputed_hash = _record_hash(record)
    _require(record.record_hash == recomputed_hash,
             f"record_hash 与字段重算不一致: {record.record_hash!r}")


def validate_records(records: list[S.SourceFinancialRecord], record_set_version: str) -> None:
    """校验一批记录归属一致、无重复 record_id、id/hash 重算一致。

    空列表是合法输入：表示「0 条合格记录」的显式完成态（record_count=0），
    逐条校验自然为空操作，由调用方决定是否落库。
    """
    seen: set[str] = set()
    for r in records:
        validate_record(r)
        _require(r.record_set_version == record_set_version,
                 f"record.record_set_version 与目标不一致: {r.record_set_version!r}")
        _require(r.record_id not in seen, f"同批重复 record_id: {r.record_id!r}")
        seen.add(r.record_id)


# ---------------------------------------------------------------------------
# 对账 / 决议 / 快照 / 公式
# ---------------------------------------------------------------------------

def validate_reconciliation_group(g: S.ReconciliationGroup) -> None:
    _nonempty(g.comparison_key, "comparison_key")
    _require(g.state in S.RECONCILE_STATES, f"reconcile state 非法: {g.state!r}")
    _require(len(g.candidate_record_ids) > 0, "candidate_record_ids 不能为空")


def validate_resolution(res: S.ResolutionRecord) -> None:
    _nonempty(res.resolution_id, "resolution_id")
    _nonempty(res.group_id, "group_id")
    _nonempty(res.candidate_set_hash, "candidate_set_hash")
    _nonempty(res.comparison_key, "comparison_key")
    _require(res.reason_code in S.RESOLUTION_REASON_CODES,
             f"reason_code 非法: {res.reason_code!r}")
    _require(len(res.accepted_record_ids) > 0, "accepted_record_ids 不能为空")
    if res.reason_code == "OTHER_WITH_NOTE":
        _require(bool(res.note) and bool(res.note.strip()),
                 "OTHER_WITH_NOTE 必须填写说明")
    _nonempty(res.operator, "operator")


def validate_policy_adjustment(pa: S.PolicyAdjustment) -> None:
    _require(pa.adjustment_type in S.POLICY_ADJUSTMENT_TYPES,
             f"adjustment_type 非法: {pa.adjustment_type!r}")
    _require(pa.decision in S.POLICY_ADJUSTMENT_DECISIONS,
             f"decision 非法: {pa.decision!r}")
    _nonempty(pa.reason_code, "reason_code")
    _require(isinstance(pa.record_refs, list), "record_refs 必须为 list")
    _nonempty(pa.operator, "operator")
    _nonempty(pa.confirmed_at, "confirmed_at")
    if pa.decision == "EXCLUDE_CONFIRMED_RECORDS":
        _require(len(pa.record_refs) > 0, "EXCLUDE_CONFIRMED_RECORDS 必须提供 record_refs")
        for ref in pa.record_refs:
            _nonempty(ref, "record_refs 元素")
    else:  # NO_ADDITIONAL_EXCLUSION_CONFIRMED
        _require(len(pa.record_refs) == 0,
                 "NO_ADDITIONAL_EXCLUSION_CONFIRMED 不应携带 record_refs")


def validate_snapshot(s: S.FinancialSnapshot) -> None:
    _nonempty(s.snapshot_id, "snapshot_id")
    _nonempty(s.snapshot_version, "snapshot_version")
    _nonempty(s.company_id, "company_id")
    _nonempty(s.as_of_date, "as_of_date")
    _require(s.scope in S.STATEMENT_SCOPES, f"scope 非法: {s.scope!r}")
    _require(s.currency in S.CURRENCIES, f"currency 非法: {s.currency!r}")
    _nonempty(s.purpose, "purpose")
    _require(len(s.record_set_ids) > 0, "record_set_ids 不能为空")
    if s.reconciliation_run_id is not None:
        _nonempty(s.reconciliation_run_id, "reconciliation_run_id")
    _require(isinstance(s.restatement_selection, dict),
             "restatement_selection 必须为 dict")
    for ck, rv in s.restatement_selection.items():
        _nonempty(ck, "restatement_selection.key")
        _nonempty(rv, "restatement_selection.value")
    _require(isinstance(s.required_formula_versions, dict),
             "required_formula_versions 必须为 dict")
    for fid, ver in s.required_formula_versions.items():
        _nonempty(fid, "required_formula_versions.key")
        _nonempty(ver, "required_formula_versions.value")
    for pa in s.policy_adjustments:
        validate_policy_adjustment(pa)
    _nonempty(s.snapshot_builder_version, "snapshot_builder_version")
    _nonempty(s.admission_rule_version, "admission_rule_version")
    _require(isinstance(s.report_blocked, bool), "report_blocked 必须为 bool")
    # snapshot_id 与头部字段重算一致（防篡改头部字段沿用旧 id）。
    recomputed = S.derive_snapshot_id(
        s.company_id, s.scope, s.currency, s.as_of_date, s.purpose,
        s.record_set_ids, s.reconciliation_run_id, s.source_versions,
        s.resolution_versions, s.restatement_selection, s.policy_adjustments,
        s.required_formula_versions, s.snapshot_builder_version,
        s.admission_rule_version)
    _require(s.snapshot_id == recomputed,
             f"snapshot_id 与头部字段重算不一致: {s.snapshot_id!r}")


def validate_snapshot_item(item: S.SnapshotItem) -> None:
    _nonempty(item.snapshot_id, "snapshot_id")
    _nonempty(item.comparison_key, "comparison_key")
    _nonempty(item.standard_item_code, "standard_item_code")
    if item.amount is not None:
        _require(_finite_decimal(item.amount),
                 f"amount 必须为有限 Decimal: {item.amount!r}")
        _require(item.unit is not None and item.unit in S.UNITS,
                 f"amount 非空时 unit 必须为合法单位: {item.unit!r}")
    _nonempty(item.report_period, "report_period")
    _require(item.period_type in S.PERIOD_TYPES, f"period_type 非法: {item.period_type!r}")
    _require(item.statement_type in S.STATEMENT_TYPES,
             f"statement_type 非法: {item.statement_type!r}")
    _require(item.statement_scope in S.STATEMENT_SCOPES,
             f"statement_scope 非法: {item.statement_scope!r}")
    _require(item.currency in S.CURRENCIES, f"currency 非法: {item.currency!r}")
    _nonempty(item.restatement_version, "restatement_version")
    _require(len(item.source_refs) >= 1,
             "snapshot_item.source_refs 必须至少含一个来源引用")
    if item.resolution_id is not None:
        _nonempty(item.resolution_id, "snapshot_item.resolution_id")


def validate_snapshot_exception(exc: S.SnapshotException) -> None:
    _nonempty(exc.snapshot_id, "snapshot_id")
    _nonempty(exc.comparison_key, "comparison_key")
    _nonempty(exc.standard_item_code, "standard_item_code")
    _require(exc.exception_type in S.SNAPSHOT_EXCEPTION_TYPES,
             f"exception_type 非法: {exc.exception_type!r}")
    _nonempty(exc.blocking_reason, "blocking_reason")


def validate_metric_result(m: S.MetricResult) -> None:
    _nonempty(m.metric_result_id, "metric_result_id")
    _nonempty(m.snapshot_id, "snapshot_id")
    _nonempty(m.formula_id, "formula_id")
    _nonempty(m.formula_version, "formula_version")
    _nonempty(m.period, "period")
    _require(m.status in S.METRIC_STATUSES, f"metric status 非法: {m.status!r}")
    _require(isinstance(m.input_snapshot_item_refs, list),
             "input_snapshot_item_refs 必须为 list")
    _require(isinstance(m.input_record_refs, list),
             "input_record_refs 必须为 list")
    _require(isinstance(m.calculation_detail, dict),
             "calculation_detail 必须为 dict")
    _nonempty(m.created_at, "created_at")
    if m.raw_value is not None:
        _require(_finite_decimal(m.raw_value),
                 f"raw_value 必须为有限 Decimal: {m.raw_value!r}")
    if m.display_value is not None:
        _require(_finite_decimal(m.display_value),
                 f"display_value 必须为有限 Decimal: {m.display_value!r}")

    if m.status in ("CALCULATED_EXACT", "CALCULATED_PROXY"):
        _require(m.raw_value is not None, f"status={m.status} 时 raw_value 不能为 None")
        _require(m.display_value is not None, f"status={m.status} 时 display_value 不能为 None")
        _require(bool(m.unit) and bool(str(m.unit).strip()),
                 f"status={m.status} 时 unit 不能为空")
        if m.status == "CALCULATED_EXACT":
            _require(m.reason_code is None, "CALCULATED_EXACT 时 reason_code 应为 None")
        else:  # CALCULATED_PROXY
            _require(m.reason_code in S.METRIC_REASON_CODES,
                     f"CALCULATED_PROXY 时 reason_code 非法: {m.reason_code!r}")
    else:
        # 非成功状态：不得产出数值，且必须给出具体 reason_code（不得用 None 笼统表达失败）。
        _require(m.raw_value is None, f"status={m.status} 时 raw_value 应为 None")
        _require(m.display_value is None, f"status={m.status} 时 display_value 应为 None")
        _require(m.reason_code is not None and bool(str(m.reason_code).strip()),
                 f"status={m.status} 必须含非空 reason_code")
        if m.status in ("MISSING_INPUT", "PARTIAL_INPUT", "BLOCKED_BY_SNAPSHOT"):
            _require(m.reason_code in S.METRIC_REASON_CODES,
                     f"status={m.status} 时 reason_code 非法: {m.reason_code!r}")
        # ZERO_DENOMINATOR / NOT_APPLICABLE：reason_code 允许自描述（如状态本身），只需非空。


def validate_formula_definition(f: S.FormulaDefinition) -> None:
    _nonempty(f.formula_id, "formula_id")
    _nonempty(f.formula_version, "formula_version")
    _nonempty(f.name, "name")
    _nonempty(f.python_impl, "python_impl")
    _nonempty(f.impl_version, "impl_version")
    _nonempty(f.missing_rule, "missing_rule")
    _nonempty(f.zero_denominator_rule, "zero_denominator_rule")
    _nonempty(f.effective_at, "effective_at")
    _require(isinstance(f.proxy_rule, dict), "proxy_rule 必须为 dict")
    _require(len(f.input_item_codes) > 0, "input_item_codes 不能为空")


# ---------------------------------------------------------------------------
# 进度事件
# ---------------------------------------------------------------------------

def validate_progress_event(ev: S.ProgressEvent) -> None:
    _nonempty(ev.event_id, "event_id")
    _nonempty(ev.run_id, "run_id")
    _require(ev.stage_id in S.STAGES, f"stage_id 非法: {ev.stage_id!r}")
    _require(ev.status in S.PROGRESS_STATUSES, f"status 非法: {ev.status!r}")
    _nonempty(ev.message_code, "message_code")
    _nonempty(ev.created_at, "created_at")


# ---------------------------------------------------------------------------
# A2/A3 原始候选 / 映射规则 / 抽取问题
# ---------------------------------------------------------------------------

def _finite_decimal(v) -> bool:
    from decimal import Decimal
    return isinstance(v, Decimal) and v.is_finite()


def validate_extracted_cell(cell: S.ExtractedFinancialCell) -> None:
    _nonempty(cell.candidate_id, "candidate_id")
    _nonempty(cell.record_set_version, "record_set_version")
    _nonempty(cell.company_id, "company_id")
    _nonempty(cell.source_version, "source_version")
    _require(cell.status in S.CANDIDATE_STATUSES,
             f"candidate.status 非法: {cell.status!r}")
    validate_locator(cell.locator)
    _require(isinstance(cell.raw_item_text, str), "raw_item_text 必须为 str")
    _require(isinstance(cell.detection_evidence, dict),
             "detection_evidence 必须为 dict")
    _require(isinstance(cell.quality_flags, list), "quality_flags 必须为 list")
    if cell.parsed_numeric_value is not None:
        _require(_finite_decimal(cell.parsed_numeric_value),
                 f"parsed_numeric_value 必须为有限 Decimal: {cell.parsed_numeric_value!r}")
    if cell.cached_formula_value is not None:
        _require(_finite_decimal(cell.cached_formula_value),
                 f"cached_formula_value 必须为有限 Decimal: {cell.cached_formula_value!r}")
    if cell.min_display_increment is not None:
        _require(_finite_decimal(cell.min_display_increment)
                 and cell.min_display_increment >= 0,
                 f"min_display_increment 必须为非负有限 Decimal: {cell.min_display_increment!r}")
    if cell.statement_type_candidate is not None:
        _require(cell.statement_type_candidate in S.STATEMENT_TYPES,
                 f"statement_type_candidate 非法: {cell.statement_type_candidate!r}")
    if cell.period_type_candidate is not None:
        _require(cell.period_type_candidate in S.PERIOD_TYPES,
                 f"period_type_candidate 非法: {cell.period_type_candidate!r}")
    if cell.scope_candidate is not None:
        _require(cell.scope_candidate in S.STATEMENT_SCOPES,
                 f"scope_candidate 非法: {cell.scope_candidate!r}")
    if cell.currency_candidate is not None:
        _require(cell.currency_candidate in S.CURRENCIES,
                 f"currency_candidate 非法: {cell.currency_candidate!r}")
    if cell.unit_candidate is not None:
        _require(cell.unit_candidate in S.UNITS,
                 f"unit_candidate 非法: {cell.unit_candidate!r}")
    _nonempty(cell.created_at, "created_at")
    recomputed = S.derive_candidate_id(cell.record_set_version, cell.locator,
                                       cell.raw_item_text, cell.raw_value_text)
    _require(cell.candidate_id == recomputed,
             f"candidate_id 与内容/坐标重算不一致: {cell.candidate_id!r}")


def validate_mapping_rule(rule: S.MappingRule) -> None:
    _nonempty(rule.rule_id, "rule_id")
    _nonempty(rule.rule_version, "rule_version")
    _require(rule.statement_type in S.STATEMENT_TYPES,
             f"mapping_rule.statement_type 非法: {rule.statement_type!r}")
    _nonempty(rule.standard_item_code, "standard_item_code")
    _require(len(rule.aliases) > 0, "mapping_rule.aliases 不能为空")
    _require(isinstance(rule.exclude_words, list), "exclude_words 必须为 list")
    _require(isinstance(rule.priority, int) and rule.priority >= 0,
             f"priority 必须为非负整数: {rule.priority!r}")
    _nonempty(rule.effective_at, "effective_at")


def validate_extraction_issue(issue: S.ExtractionIssue) -> None:
    _nonempty(issue.issue_id, "issue_id")
    _nonempty(issue.record_set_version, "record_set_version")
    _require(issue.issue_type in S.EXTRACTION_ISSUE_TYPES,
             f"extraction_issue.issue_type 非法: {issue.issue_type!r}")
    _require(isinstance(issue.detail, dict), "detail 必须为 dict")
    _nonempty(issue.created_at, "created_at")


# ---------------------------------------------------------------------------
# A4 对账运行 / 组结果 / 勾稽
# ---------------------------------------------------------------------------

def validate_reconciliation_run(run: S.ReconciliationRun) -> None:
    _nonempty(run.run_id, "run_id")
    _nonempty(run.company_id, "company_id")
    _require(len(run.input_record_set_ids) > 0, "input_record_set_ids 不能为空")
    _require(isinstance(run.rule_versions, dict), "rule_versions 必须为 dict")
    _nonempty(run.input_hash, "input_hash")
    _nonempty(run.created_at, "created_at")


def validate_reconciliation_group_result(g: S.ReconciliationGroupResult) -> None:
    _nonempty(g.run_id, "run_id")
    _nonempty(g.comparison_key, "comparison_key")
    _require(g.state in S.RECONCILE_STATES, f"reconcile state 非法: {g.state!r}")
    _require(len(g.candidate_record_ids) > 0, "candidate_record_ids 不能为空")
    _nonempty(g.created_at, "created_at")


def validate_reconciliation_check(c: S.ReconciliationCheck) -> None:
    _nonempty(c.check_id, "check_id")
    _nonempty(c.run_id, "run_id")
    _nonempty(c.record_set_version, "record_set_version")
    _require(c.check_type in S.CHECK_TYPES, f"check_type 非法: {c.check_type!r}")
    _require(c.status in S.CHECK_STATUSES, f"check status 非法: {c.status!r}")
    _nonempty(c.created_at, "created_at")


# ---------------------------------------------------------------------------
# A5 科目映射确认
# ---------------------------------------------------------------------------

def validate_mapping_resolution(res: S.MappingResolution) -> None:
    _nonempty(res.resolution_id, "resolution_id")
    _nonempty(res.record_set_version, "record_set_version")
    _nonempty(res.candidate_id, "candidate_id")
    _require(res.reason_code in S.RESOLUTION_REASON_CODES,
             f"reason_code 非法: {res.reason_code!r}")
    if res.chosen_item_code is not None:
        _nonempty(res.chosen_item_code, "chosen_item_code")
    if res.reason_code == "OTHER_WITH_NOTE":
        _require(bool(res.note) and bool(res.note.strip()),
                 "OTHER_WITH_NOTE 必须填写说明")
    _nonempty(res.operator, "operator")
    _nonempty(res.confirmed_at, "confirmed_at")


# ---------------------------------------------------------------------------
# 结构化元数据确认（fix #1）
# ---------------------------------------------------------------------------

def validate_metadata_confirmation(mc: S.MetadataConfirmation) -> None:
    """校验一条元数据确认：字段/值/来源类型白名单 + 内容寻址版本重算一致。"""
    _nonempty(mc.confirmation_id, "confirmation_id")
    _nonempty(mc.version, "version")
    _nonempty(mc.company_id, "company_id")
    _nonempty(mc.source_document_id, "source_document_id")
    _require(mc.field in S.METADATA_CONFIRMATION_FIELDS,
             f"field 非法: {mc.field!r}")
    _nonempty(mc.value, "value")
    _require(mc.source_type in S.METADATA_CONFIRMATION_SOURCE_TYPES,
             f"source_type 非法: {mc.source_type!r}")
    _nonempty(mc.basis, "basis")
    _nonempty(mc.operator, "operator")
    _nonempty(mc.confirmed_at, "confirmed_at")

    # 字段值白名单：不同字段有各自合法取值域（枚举/占位文本）。
    if mc.field == "statement_scope":
        _require(mc.value in S.STATEMENT_SCOPES, f"statement_scope 值非法: {mc.value!r}")
    elif mc.field == "currency":
        _require(mc.value in S.CURRENCIES, f"currency 值非法: {mc.value!r}")
    elif mc.field == "audit_status":
        _require(mc.value in S.AUDIT_STATUSES, f"audit_status 值非法: {mc.value!r}")
    elif mc.field == "restatement_version":
        _require(bool(mc.value.strip()), "restatement_version 值不能为空")

    # 内容寻址版本重算一致（防篡改 field/value/source_type 沿用旧 version）。
    recomputed = S.derive_metadata_confirmation_version(
        mc.company_id, mc.source_document_id, mc.field, mc.value, mc.source_type)
    _require(mc.version == recomputed,
             f"version 与内容重算不一致: {mc.version!r} != {recomputed!r}")


if __name__ == "__main__":
    # 冒烟自检：构造非法坐标 / 非法状态，验证校验路径可独立运行。
    bad_locator = S.SourceLocator(kind="pdf", pdf=S.PdfCellLocator(
        document_id="d", document_version="v", pdf_page=0, row_index=0,
        column_index=0, bbox=[1.0, 2.0, 1.0, 3.0],
    ))
    try:
        validate_locator(bad_locator)
        print("SMOKE FAIL: 非法 PDF 坐标未被拒绝")
    except ValidationError as e:
        print(f"SMOKE OK: 非法 PDF 坐标被拒绝 -> {e}")
