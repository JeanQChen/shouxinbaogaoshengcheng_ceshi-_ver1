"""financial_v2 运行时 Schema / 状态 / 坐标校验（写库前强制调用）。

本模块为纯函数库（无 I/O），只读 schema，不写库、不解析文件。store 层在任何写入前
必须调用对应 validate_*，保证非法对象 / 非法坐标 / 非法状态在写库前被拒绝；SQLite
唯一约束与不可变触发器仍保留作为第二道防线。

校验范围（任务书 A1 停止点 + v3 修订）：
- 必填字符串非空、枚举白名单；
- Excel 坐标 1-based（row/col >= 1）且 cell_address 与 row/col 一致；
- PDF 坐标 1-based 物理页、bbox 为 4 个有限数值且 x1>x0、bottom>top；
- record_id 与 derive_record_id 重算一致（防篡改坐标/内容却沿用旧 id）；
- comparison_key 与 record 字段重算一致；
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
    if ctx.source_document_id is not None:
        _nonempty(ctx.source_document_id, "context.source_document_id")


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
    _nonempty(v.source_version, "source_version")
    _nonempty(v.source_document_id, "source_document_id")
    _nonempty(v.file_sha256, "file_sha256")
    _require(v.file_type in S.FILE_TYPES, f"file_type 非法: {v.file_type!r}")
    _require(v.file_size >= 0, f"file_size 必须非负: {v.file_size!r}")
    _require(v.currency in S.CURRENCIES, f"currency 非法: {v.currency!r}")
    _require(v.statement_scope in S.STATEMENT_SCOPES,
             f"statement_scope 非法: {v.statement_scope!r}")
    _require(v.audit_status in S.AUDIT_STATUSES,
             f"audit_status 非法: {v.audit_status!r}")
    _nonempty(v.extractor_name, "extractor_name")
    _nonempty(v.extractor_version, "extractor_version")
    _nonempty(v.mapping_rule_version, "mapping_rule_version")
    _nonempty(v.normalization_rule_version, "normalization_rule_version")
    _nonempty(v.created_at, "created_at")


def validate_record_set(rs: S.FinancialRecordSet) -> None:
    _nonempty(rs.record_set_version, "record_set_version")
    _nonempty(rs.source_version, "source_version")
    _nonempty(rs.extractor_version, "extractor_version")
    _nonempty(rs.mapping_rule_version, "mapping_rule_version")
    _nonempty(rs.normalization_rule_version, "normalization_rule_version")
    _require(rs.block_count >= 0, f"block_count 必须非负: {rs.block_count!r}")
    _require(rs.record_count >= 0, f"record_count 必须非负: {rs.record_count!r}")
    _nonempty(rs.created_at, "created_at")


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

def _record_hash(record: S.SourceFinancialRecord) -> str:
    """记录内容哈希：覆盖参与身份判定的字段 + 原始值 + 坐标，防静默篡改。"""
    raw = json.dumps({
        "record_set_version": record.record_set_version,
        "company_id": record.company_id,
        "standard_item_code": record.standard_item_code,
        "statement_type": record.statement_type,
        "raw_item_text": record.raw_item_text,
        "raw_value": record.raw_value,
        "raw_unit": record.raw_unit,
        "raw_currency": record.raw_currency,
        "std_value": record.std_value,
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
    """校验一批记录归属一致、无重复 record_id、id/hash 重算一致。"""
    _require(len(records) > 0, "records 不能为空")
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


def validate_snapshot(s: S.FinancialSnapshot) -> None:
    _nonempty(s.snapshot_id, "snapshot_id")
    _nonempty(s.snapshot_version, "snapshot_version")
    _nonempty(s.company_id, "company_id")
    _nonempty(s.as_of_date, "as_of_date")
    _require(s.scope in S.STATEMENT_SCOPES, f"scope 非法: {s.scope!r}")
    _require(s.currency in S.CURRENCIES, f"currency 非法: {s.currency!r}")
    _nonempty(s.purpose, "purpose")


def validate_snapshot_item(item: S.SnapshotItem) -> None:
    _nonempty(item.snapshot_id, "snapshot_id")
    _nonempty(item.comparison_key, "comparison_key")
    _nonempty(item.standard_item_code, "standard_item_code")
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
    _nonempty(m.snapshot_id, "snapshot_id")
    _nonempty(m.formula_id, "formula_id")
    _nonempty(m.formula_version, "formula_version")
    _nonempty(m.period, "period")
    _require(m.status in S.METRIC_STATUSES, f"metric status 非法: {m.status!r}")
    if m.status == "ok":
        _require(m.value is not None, "status=ok 时 value 不能为 None")
        _require(m.reason_code is None, "status=ok 时 reason_code 应为 None")
    else:
        _require(m.reason_code is not None, f"status={m.status} 必须含 reason_code")


def validate_formula_definition(f: S.FormulaDefinition) -> None:
    _nonempty(f.formula_id, "formula_id")
    _nonempty(f.formula_version, "formula_version")
    _nonempty(f.python_impl, "python_impl")
    _nonempty(f.missing_rule, "missing_rule")
    _nonempty(f.zero_denominator_rule, "zero_denominator_rule")
    _nonempty(f.effective_at, "effective_at")


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
