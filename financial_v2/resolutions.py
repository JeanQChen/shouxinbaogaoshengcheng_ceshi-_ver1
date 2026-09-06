"""A5 集中确认与 ResolutionRecord（§8）：两类批量确认、审计与定向失效。

职责（纯确定性，无 RAG / 无 LLM / 无 OCR，绝不自动选值 / 绝不让用户手填金额）：
- list_pending：把「科目映射待确认」（MAPPING_REQUIRED）与「跨来源冲突待选源」
  （CONFLICT）汇总成审计可见的 pending 列表；INSUFFICIENT_SCOPE 单独列出，仅提示
  「补充/更正材料」，不接受替代值输入。
- submit_mapping_resolutions / submit_value_resolutions：全有或全无批量事务。
  先逐项校验（跨公司 / 不存在 / 隔离 / 非 current / 重复 / 非法 reason / 空 note /
  accepted 与 rejected 重叠或越界全部拒绝），任一条失败 committed=false 且零写入；
  全部通过后单事务追加不可变 Resolution、active validity 事件与 head 指针。
- 定向失效：store.invalidate_* 追加 stale 事件并移除 head，旧 ResolutionRecord 保留
  可回查；list_active_* 供审计扫描。
- derive_confirmed_record：基于 mapping confirmation 派生新 SourceFinancialRecord /
  Record Set 版本（mapping_mode="human_confirmed"），不 UPDATE 旧候选 / 旧记录。

本模块实现 A5；A6 Snapshot、指标与下游 Claim 级联失效留 A6/1F-B。

CLI:
  python -m financial_v2.resolutions pending --company <id>
  python -m financial_v2.resolutions validate --input <batch.json>
  python -m financial_v2.resolutions submit --input <batch.json>
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from financial_v2 import mapping
from financial_v2 import normalization as norm
from financial_v2 import reconciliation
from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator

logger = logging.getLogger(__name__)

# 决议待确认类型（§8.1）：两类可提交 + 一类仅提示补充材料。
ISSUE_TYPE_MAPPING = "MAPPING_CONFIRMATION"
ISSUE_TYPE_VALUE = "VALUE_SOURCE_RESOLUTION"
ISSUE_TYPE_INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 结果 / 请求类型（A5 服务接口；schema.py 未承载 UI 层 DTO）
# ---------------------------------------------------------------------------

@dataclass
class PendingFilters:
    issue_type: str | None = None      # 上述三类之一；None = 全部
    statement_type: str | None = None  # balance_sheet / income_statement / cash_flow
    unresolved_only: bool = True       # 恒 True（已确认项不进入 pending）


@dataclass
class PendingItem:
    issue_type: str
    issue_id: str
    company_id: str
    record_set_versions: list[str]
    stale: bool
    payload: dict                      # 审计字段：科目/期间/scope/币种/原值/标准值/坐标/差异/影响


@dataclass
class PendingIssueList:
    company_id: str
    items: list[PendingItem]
    mapping_confirmation_count: int
    value_source_resolution_count: int
    insufficient_scope_count: int


@dataclass
class MappingResolutionItem:
    candidate_id: str
    chosen_item_code: str | None       # None = 无法确认 / 需补充材料
    reason_code: str
    note: str | None = None


@dataclass
class ValueResolutionItem:
    group_id: str
    accepted_record_ids: list[str]
    rejected_record_ids: list[str]
    reason_code: str
    note: str | None = None


@dataclass
class MappingResolutionBatchRequest:
    company_id: str
    operator: str
    items: list[MappingResolutionItem]


@dataclass
class ValueResolutionBatchRequest:
    company_id: str
    operator: str
    items: list[ValueResolutionItem]


@dataclass
class BatchError:
    index: int                          # -1 = 整批级错误
    field: str
    message: str


@dataclass
class BatchResult:
    committed: bool
    accepted: list[str]                 # resolution_ids
    errors: list[BatchError]


@dataclass
class DeriveResult:
    record_set_version: str
    record: S.SourceFinancialRecord
    reused: bool


# ---------------------------------------------------------------------------
# 身份派生（确定性 → 幂等重放，无时间戳）
# ---------------------------------------------------------------------------

def _derive_mapping_resolution_id(candidate_id: str, chosen_item_code: str | None,
                                  reason_code: str, note: str | None,
                                  operator: str) -> str:
    raw = json.dumps({
        "kind": "mapping_resolution",
        "candidate_id": candidate_id,
        "chosen_item_code": chosen_item_code,
        "reason_code": reason_code,
        "note": note,
        "operator": operator,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "mres-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _derive_value_resolution_id(group_id: str, candidate_set_hash: str,
                                comparison_key: str, accepted_record_ids: list[str],
                                rejected_record_ids: list[str], reason_code: str,
                                note: str | None, operator: str) -> str:
    raw = json.dumps({
        "kind": "value_resolution",
        "group_id": group_id,
        "candidate_set_hash": candidate_set_hash,
        "comparison_key": comparison_key,
        "accepted_record_ids": sorted(accepted_record_ids),
        "rejected_record_ids": sorted(rejected_record_ids),
        "reason_code": reason_code,
        "note": note,
        "operator": operator,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "vres-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _candidate_set_hash(records: list[S.SourceFinancialRecord]) -> str:
    """决议绑定输入的候选集合哈希：按 (record_id, record_hash) 稳定聚合。

    record_hash 是内容派生且不可变，任何来源记录变化都会改变本哈希 → 定向失效。
    """
    parts = sorted(f"{r.record_id}:{r.record_hash}" for r in records)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _source_hashes(records: list[S.SourceFinancialRecord]) -> list[str]:
    """决议绑定的文件哈希集合（§8.6）。"""
    hashes: set[str] = set()
    for r in records:
        rs = store.get_record_set(r.record_set_version)
        if rs is None:
            continue
        sv = store.get_source_version(rs.source_version)
        if sv is not None and sv.file_sha256:
            hashes.add(sv.file_sha256)
    return sorted(hashes)


def _rule_versions() -> dict[str, str]:
    """决议绑定的规则版本（§8.6）。当前阶段均冻结为 1.0。"""
    return {
        "mapping_rule_version": mapping.RULE_VERSION,
        "normalization_rule_version": norm.NORMALIZATION_RULE_VERSION,
        "reconciliation_rule_version": reconciliation.RECONCILIATION_RULE_VERSION,
        "rounding_tolerance_rule_version": reconciliation.ROUNDING_TOLERANCE_RULE_VERSION,
    }


# ---------------------------------------------------------------------------
# 辅助：标准科目 / 隔离 / 准入 / 过期
# ---------------------------------------------------------------------------

def _known_item_codes(statement_type: str | None = None) -> set[str]:
    return {r.standard_item_code for r in mapping.build_builtin_rules()
            if statement_type is None or r.statement_type == statement_type}


def _candidate_stale(cand: S.ExtractedFinancialCell) -> bool:
    """候选所属 record_set 是否已非其 source document 的 current（被更新版本取代）。"""
    rs = store.get_record_set(cand.record_set_version)
    if rs is None:
        return False  # 抽取-only，尚无 current 概念
    sv = store.get_source_version(rs.source_version)
    if sv is None:
        return False
    cur = store.get_current_record_set(sv.source_document_id)
    return cur is not None and cur.record_set_version != cand.record_set_version


def _mapping_issue_id_for(cand: S.ExtractedFinancialCell) -> str | None:
    for iss in store.list_extraction_issues(cand.record_set_version):
        if iss.issue_type == "MAPPING_REQUIRED" and iss.candidate_id == cand.candidate_id:
            return iss.issue_id
    return None


def _admission_block_reason(cand: S.ExtractedFinancialCell) -> str | None:
    if cand.statement_type_candidate not in S.STATEMENT_TYPES:
        return "statement_type"
    if not cand.period_candidate or not cand.period_type_candidate:
        return "period"
    if cand.scope_candidate not in S.STATEMENT_SCOPES:
        return "scope"
    if cand.currency_candidate not in S.CURRENCIES:
        return "currency"
    if norm.unit_to_yuan(cand.unit_candidate) is None:
        return "unit"
    return None


def _record_source(r: S.SourceFinancialRecord) -> dict:
    rs = store.get_record_set(r.record_set_version)
    source_version = rs.source_version if rs else None
    file_sha256 = None
    if source_version:
        sv = store.get_source_version(source_version)
        file_sha256 = sv.file_sha256 if sv else None
    return {
        "record_id": r.record_id,
        "record_set_version": r.record_set_version,
        "source_version": source_version,
        "file_sha256": file_sha256,
        "raw_item_text": r.raw_item_text,
        "raw_value": r.raw_value,
        "raw_unit": r.raw_unit,
        "std_value": r.std_value,
        "std_unit": r.std_unit,
        "report_period": r.report_period,
        "period_type": r.period_type,
        "scope": r.statement_scope,
        "currency": r.currency,
        "restatement_version": r.restatement_version,
        "locator": S.locator_to_dict(r.locator),
    }


# ---------------------------------------------------------------------------
# list_pending（§8.2）
# ---------------------------------------------------------------------------

def _pending_mapping(company_id: str) -> list[PendingItem]:
    items: list[PendingItem] = []
    for rs in store.list_company_record_set_versions(company_id):
        candidates = {c.candidate_id: c for c in store.list_candidates(rs)}
        for iss in store.list_extraction_issues(rs):
            if iss.issue_type != "MAPPING_REQUIRED" or iss.candidate_id is None:
                continue
            cand = candidates.get(iss.candidate_id)
            if cand is None:
                continue
            if store.get_active_mapping_resolution(iss.candidate_id) is not None:
                continue
            stale = _candidate_stale(cand)
            items.append(PendingItem(
                issue_type=ISSUE_TYPE_MAPPING,
                issue_id=iss.issue_id,
                company_id=company_id,
                record_set_versions=[cand.record_set_version],
                stale=stale,
                payload={
                    "issue_id": iss.issue_id,
                    "candidate_id": cand.candidate_id,
                    "raw_item_text": cand.raw_item_text,
                    "normalized_item_text": iss.detail.get("normalized_item_text"),
                    "reason": iss.detail.get("reason"),
                    "conflicting_rules": iss.detail.get("conflicting_rules", []),
                    "statement_type": cand.statement_type_candidate,
                    "report_period": cand.period_candidate,
                    "period_type": cand.period_type_candidate,
                    "scope": cand.scope_candidate,
                    "currency": cand.currency_candidate,
                    "unit": cand.unit_candidate,
                    "record_set_version": cand.record_set_version,
                    "source_version": cand.source_version,
                    "locator": S.locator_to_dict(cand.locator),
                    "stale": stale,
                },
            ))
    return items


def _pending_value(company_id: str) -> list[PendingItem]:
    cur = store.get_current_reconciliation(company_id)
    if cur is None:
        return []
    items: list[PendingItem] = []
    for g in store.list_reconciliation_group_results(cur.run_id):
        if g.state not in ("CONFLICT", "INSUFFICIENT_SCOPE"):
            continue
        if store.get_active_resolution(g.comparison_key) is not None:
            continue
        records = [r for r in (store.get_record(rid) for rid in g.candidate_record_ids)
                   if r is not None]
        issue_type = ISSUE_TYPE_VALUE if g.state == "CONFLICT" else ISSUE_TYPE_INSUFFICIENT_SCOPE
        items.append(PendingItem(
            issue_type=issue_type,
            issue_id=g.comparison_key,
            company_id=company_id,
            record_set_versions=sorted({r.record_set_version for r in records}),
            stale=False,
            payload={
                "run_id": cur.run_id,
                "group_id": g.comparison_key,
                "state": g.state,
                "comparison_key": g.comparison_key,
                "standard_item_code": g.diff_detail.get("standard_item_code"),
                "statement_type": g.diff_detail.get("statement_type"),
                "diff_detail": g.diff_detail,
                "std_values": g.std_values,
                "impact_item_codes": g.impact_item_codes,
                "impact_section_contracts": g.impact_section_contracts,
                "sources": [_record_source(r) for r in records],
                "resolvable": g.state == "CONFLICT",
                "required_action": "select_source" if g.state == "CONFLICT" else "resupply_material",
            },
        ))
    return items


def list_pending(company_id: str,
                 filters: PendingFilters | None = None) -> PendingIssueList:
    """汇总公司待确认项（默认不勾选任何来源 / 映射；不含已 active 决议项）。"""
    filters = filters or PendingFilters()
    items: list[PendingItem] = []

    if filters.issue_type is None or filters.issue_type == ISSUE_TYPE_MAPPING:
        items.extend(_pending_mapping(company_id))
    if filters.issue_type is None or filters.issue_type in (ISSUE_TYPE_VALUE, ISSUE_TYPE_INSUFFICIENT_SCOPE):
        items.extend(_pending_value(company_id))
    if filters.issue_type is not None:
        items = [i for i in items if i.issue_type == filters.issue_type]

    # 去重（同一候选/同一 group 只出现一次）。
    dedup: dict[str, PendingItem] = {}
    for it in items:
        dedup.setdefault(f"{it.issue_type}|{it.issue_id}", it)
    items = list(dedup.values())

    if filters.statement_type:
        items = [i for i in items
                 if i.payload.get("statement_type") == filters.statement_type]

    items.sort(key=lambda i: (i.issue_type, i.issue_id))
    return PendingIssueList(
        company_id=company_id,
        items=items,
        mapping_confirmation_count=sum(1 for i in items if i.issue_type == ISSUE_TYPE_MAPPING),
        value_source_resolution_count=sum(1 for i in items if i.issue_type == ISSUE_TYPE_VALUE),
        insufficient_scope_count=sum(1 for i in items if i.issue_type == ISSUE_TYPE_INSUFFICIENT_SCOPE),
    )


# ---------------------------------------------------------------------------
# submit_mapping_resolutions（§8.3/8.4，全有或全无）
# ---------------------------------------------------------------------------

def submit_mapping_resolutions(request: MappingResolutionBatchRequest) -> BatchResult:
    if not request.company_id:
        return BatchResult(False, [], [BatchError(-1, "company_id", "company_id 不能为空")])
    if not request.operator:
        return BatchResult(False, [], [BatchError(-1, "operator", "operator 不能为空")])
    if not request.items:
        return BatchResult(False, [], [BatchError(-1, "items", "items 不能为空")])

    errors: list[BatchError] = []
    resolutions: list[S.MappingResolution] = []
    now = _utcnow()
    seen: set[str] = set()

    for i, item in enumerate(request.items):
        if not item.candidate_id:
            errors.append(BatchError(i, "candidate_id", "candidate_id 不能为空"))
            continue
        if item.candidate_id in seen:
            errors.append(BatchError(i, "candidate_id", "批内 candidate_id 重复"))
            continue
        seen.add(item.candidate_id)
        if item.reason_code not in S.RESOLUTION_REASON_CODES:
            errors.append(BatchError(i, "reason_code", f"非法 reason_code: {item.reason_code!r}"))
            continue
        if item.reason_code == "OTHER_WITH_NOTE" and not (item.note and item.note.strip()):
            errors.append(BatchError(i, "note", "OTHER_WITH_NOTE 必须填写说明"))
            continue

        cand = store.get_candidate(item.candidate_id)
        if cand is None:
            errors.append(BatchError(i, "candidate_id", f"候选不存在: {item.candidate_id}"))
            continue
        if cand.company_id != request.company_id:
            errors.append(BatchError(i, "candidate_id",
                                     f"跨公司候选: {cand.company_id!r} != {request.company_id!r}"))
            continue
        if store.is_quarantined("extracted_financial_cell", cand.candidate_id) \
                or store.is_quarantined("financial_source_version", cand.source_version):
            errors.append(BatchError(i, "candidate_id", "候选或来源已隔离"))
            continue
        if store.get_active_mapping_resolution(cand.candidate_id) is not None:
            errors.append(BatchError(i, "candidate_id", "候选已有 active 决议，不得重复确认"))
            continue
        if item.chosen_item_code is not None \
                and item.chosen_item_code not in _known_item_codes(cand.statement_type_candidate):
            errors.append(BatchError(i, "chosen_item_code",
                                     f"标准科目不在该系统展示候选内: {item.chosen_item_code!r}"))
            continue

        resolutions.append(S.MappingResolution(
            resolution_id=_derive_mapping_resolution_id(
                item.candidate_id, item.chosen_item_code, item.reason_code, item.note,
                request.operator),
            record_set_version=cand.record_set_version,
            candidate_id=cand.candidate_id,
            issue_id=_mapping_issue_id_for(cand),
            chosen_item_code=item.chosen_item_code,
            reason_code=item.reason_code,
            note=item.note,
            operator=request.operator,
            confirmed_at=now,
        ))

    if errors:
        return BatchResult(False, [], errors)

    try:
        store.commit_mapping_resolutions(resolutions)
    except Exception as e:  # noqa: BLE001 —— 全有或全无：捕获后返回业务错误，不写任何行
        logger.warning("commit_mapping_resolutions 失败: %s", e)
        return BatchResult(False, [], [BatchError(-1, "batch", str(e))])

    return BatchResult(True, [r.resolution_id for r in resolutions], [])


# ---------------------------------------------------------------------------
# submit_value_resolutions（§8.3/8.5，全有或全无）
# ---------------------------------------------------------------------------

def submit_value_resolutions(request: ValueResolutionBatchRequest) -> BatchResult:
    if not request.company_id:
        return BatchResult(False, [], [BatchError(-1, "company_id", "company_id 不能为空")])
    if not request.operator:
        return BatchResult(False, [], [BatchError(-1, "operator", "operator 不能为空")])
    if not request.items:
        return BatchResult(False, [], [BatchError(-1, "items", "items 不能为空")])

    cur = store.get_current_reconciliation(request.company_id)
    if cur is None:
        return BatchResult(False, [], [BatchError(-1, "company_id",
                                                  "无 current reconciliation，无法提交冲突决议")])
    groups = {g.comparison_key: g for g in store.list_reconciliation_group_results(cur.run_id)}

    errors: list[BatchError] = []
    resolutions: list[S.ResolutionRecord] = []
    now = _utcnow()
    seen: set[str] = set()

    for i, item in enumerate(request.items):
        if not item.group_id:
            errors.append(BatchError(i, "group_id", "group_id 不能为空"))
            continue
        if item.group_id in seen:
            errors.append(BatchError(i, "group_id", "批内 group_id 重复"))
            continue
        seen.add(item.group_id)

        g = groups.get(item.group_id)
        if g is None:
            errors.append(BatchError(i, "group_id", "group 不存在或非当前 reconciliation"))
            continue
        if g.state != "CONFLICT":
            errors.append(BatchError(i, "group_id",
                                     f"该 group 非 CONFLICT（{g.state}），不可选择来源"))
            continue
        if store.get_active_resolution(item.group_id) is not None:
            errors.append(BatchError(i, "group_id", "group 已有 active 决议，不得重复确认"))
            continue
        if item.reason_code not in S.RESOLUTION_REASON_CODES:
            errors.append(BatchError(i, "reason_code", f"非法 reason_code: {item.reason_code!r}"))
            continue
        if item.reason_code == "OTHER_WITH_NOTE" and not (item.note and item.note.strip()):
            errors.append(BatchError(i, "note", "OTHER_WITH_NOTE 必须填写说明"))
            continue

        accepted = item.accepted_record_ids
        rejected = item.rejected_record_ids
        group_ids = set(g.candidate_record_ids)
        if not accepted:
            errors.append(BatchError(i, "accepted_record_ids", "accepted_record_ids 不能为空"))
            continue
        if set(accepted) & set(rejected):
            errors.append(BatchError(i, "accepted_record_ids", "accepted 与 rejected 存在重叠"))
            continue
        if not set(accepted).issubset(group_ids) or not set(rejected).issubset(group_ids):
            errors.append(BatchError(i, "accepted_record_ids", "选择的记录必须全部属于当前 group"))
            continue
        if set(accepted) | set(rejected) != group_ids:
            errors.append(BatchError(i, "accepted_record_ids",
                                     "accepted ∪ rejected 必须覆盖 group 全部记录"))
            continue

        records = [r for r in (store.get_record(rid) for rid in group_ids) if r is not None]
        if len(records) != len(group_ids):
            errors.append(BatchError(i, "accepted_record_ids", "group 内存在不存在的记录"))
            continue
        for r in records:
            if store.is_quarantined("source_financial_record", r.record_id) \
                    or store.is_quarantined("financial_record_set", r.record_set_version):
                errors.append(BatchError(i, "accepted_record_ids", f"记录/集合已隔离: {r.record_id}"))
                break
        else:
            accepted_recs = [r for r in records if r.record_id in set(accepted)]
            accepted_std = {str(r.std_value) for r in accepted_recs}
            if len(accepted_std) > 1:
                errors.append(BatchError(i, "accepted_record_ids",
                                         "accepted 来源标准值不一致，不得同时接受"))
                continue

            resolutions.append(S.ResolutionRecord(
                resolution_id=_derive_value_resolution_id(
                    g.comparison_key, _candidate_set_hash(records), g.comparison_key,
                    accepted, rejected, item.reason_code, item.note, request.operator),
                group_id=g.comparison_key,
                candidate_set_hash=_candidate_set_hash(records),
                source_hashes=_source_hashes(records),
                comparison_key=g.comparison_key,
                rule_versions=_rule_versions(),
                accepted_record_ids=sorted(accepted),
                rejected_record_ids=sorted(rejected),
                reason_code=item.reason_code,
                note=item.note,
                operator=request.operator,
                confirmed_at=now,
            ))

    if errors:
        return BatchResult(False, [], errors)

    try:
        store.commit_resolution_records(resolutions)
    except Exception as e:  # noqa: BLE001
        logger.warning("commit_resolution_records 失败: %s", e)
        return BatchResult(False, [], [BatchError(-1, "batch", str(e))])

    return BatchResult(True, [r.resolution_id for r in resolutions], [])


# ---------------------------------------------------------------------------
# 审计与定向失效（§8.6）
# ---------------------------------------------------------------------------

def list_active_resolutions(company_id: str) -> list[S.ResolutionRecord]:
    """公司全部 active 冲突来源决议（跨 group；审计 / 定向失效扫描用）。"""
    out: list[S.ResolutionRecord] = []
    for group_id, resolution_id in store.list_active_resolution_ids():
        r = store.get_active_resolution(group_id)
        if r is None:
            continue
        # 归属过滤：决议绑定的记录必须属于该公司。
        recs = [store.get_record(rid) for rid in r.accepted_record_ids]
        if any(rec is not None and rec.company_id == company_id for rec in recs):
            out.append(r)
    return out


def invalidate_resolution_records(resolution_ids: list[str], *,
                                  invalidated_by: str | None = None,
                                  invalidated_reason: str | None = None) -> int:
    """定向失效一批冲突来源决议（追加 stale + 移除 head，旧决议保留）。"""
    for rid in resolution_ids:
        store.invalidate_resolution_record(rid, invalidated_by, invalidated_reason)
    return len(resolution_ids)


def invalidate_mapping_resolutions(resolution_ids: list[str], *,
                                   invalidated_by: str | None = None,
                                   invalidated_reason: str | None = None) -> int:
    """定向失效一批科目映射决议。"""
    for rid in resolution_ids:
        store.invalidate_mapping_resolution(rid, invalidated_by, invalidated_reason)
    return len(resolution_ids)


def invalidate_value_resolution_if_input_changed(group_id: str, *,
                                                 invalidated_by: str | None = None,
                                                 invalidated_reason: str = "dependency_changed") -> bool:
    """定向失效：重算某 group 当前输入候选集合哈希，与 active 决议存储值不一致 → 置 stale。

    仅失效实际依赖变化的决议；返回是否发生失效。
    """
    r = store.get_active_resolution(group_id)
    if r is None:
        return False
    records = [rec for rec in (store.get_record(rid) for rid in r.accepted_record_ids + r.rejected_record_ids)
               if rec is not None]
    if _candidate_set_hash(records) == r.candidate_set_hash:
        return False
    store.invalidate_resolution_record(r.resolution_id, invalidated_by, invalidated_reason)
    return True


# ---------------------------------------------------------------------------
# derive_confirmed_record（§8.4 派生新版本，不 UPDATE 旧候选/旧记录）
# ---------------------------------------------------------------------------

def derive_confirmed_record(candidate_id: str,
                            policy: norm.NormalizationPolicy | None = None,
                            persist: bool = True) -> DeriveResult:
    """基于 active 科目映射确认派生新 SourceFinancialRecord / Record Set 版本。

    chosen_item_code 为 None（无法确认）时不派生。派生记录 mapping_mode="human_confirmed"，
    写入新的 record_set_version（dependency_versions 携带 mapping_resolution 引用）。
    """
    resolution = store.get_active_mapping_resolution(candidate_id)
    if resolution is None:
        raise KeyError(f"无 active 映射决议: {candidate_id}")
    if resolution.chosen_item_code is None:
        raise ValueError("该决议未选择标准科目（无法确认），不能派生记录")

    cand = store.get_candidate(candidate_id)
    if cand is None:
        raise KeyError(f"候选不存在: {candidate_id}")

    block = _admission_block_reason(cand)
    if block is not None:
        raise ValueError(f"候选不满足标准化准入，无法派生记录: {block}")

    policy = policy or norm.NormalizationPolicy()
    rec = norm.build_record(cand, resolution.chosen_item_code, policy,
                            mapping_mode="human_confirmed")

    deps = dict(policy.dependency_versions)
    deps["mapping_resolution"] = resolution.resolution_id
    new_rs_version = S.derive_record_set_version(
        cand.source_version, policy.extractor_version, policy.mapping_rule_version,
        policy.normalization_rule_version, deps)

    rec.record_set_version = new_rs_version
    rec.record_id = S.derive_record_id(new_rs_version, S.record_identity_fields(rec))
    rec.record_hash = validator._record_hash(rec)

    extractor_name = ("excel_extractor" if cand.locator and cand.locator.kind == "excel"
                      else "pdf_table_extractor")
    record_set = S.FinancialRecordSet(
        record_set_version=new_rs_version,
        source_version=cand.source_version,
        extractor_name=extractor_name,
        extractor_version=policy.extractor_version,
        mapping_rule_version=policy.mapping_rule_version,
        normalization_rule_version=policy.normalization_rule_version,
        dependency_versions=deps,
        report_periods=[rec.report_period],
        currency=rec.currency,
        unit=rec.raw_unit,
        statement_scope=rec.statement_scope,
        audit_status=None,
        block_count=0,
        record_count=1,
        created_at=_utcnow(),
    )

    reused = False
    if persist:
        sv = store.get_source_version(cand.source_version)
        if sv is None:
            raise KeyError(f"source_version 不存在: {cand.source_version}")
        result = store.commit_record_set(record_set, [rec], sv.source_document_id)
        reused = result.reused

    return DeriveResult(record_set_version=new_rs_version, record=rec, reused=reused)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_request(path: str) -> tuple[str, MappingResolutionBatchRequest | ValueResolutionBatchRequest]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    kind = data.get("type")
    company_id = data.get("company_id")
    operator = data.get("operator")
    items = data.get("items", [])
    if kind == "mapping":
        req = MappingResolutionBatchRequest(
            company_id=company_id, operator=operator,
            items=[MappingResolutionItem(
                candidate_id=it.get("candidate_id"),
                chosen_item_code=it.get("chosen_item_code"),
                reason_code=it.get("reason_code"),
                note=it.get("note")) for it in items])
        return "mapping", req
    if kind == "value":
        req = ValueResolutionBatchRequest(
            company_id=company_id, operator=operator,
            items=[ValueResolutionItem(
                group_id=it.get("group_id"),
                accepted_record_ids=list(it.get("accepted_record_ids", [])),
                rejected_record_ids=list(it.get("rejected_record_ids", [])),
                reason_code=it.get("reason_code"),
                note=it.get("note")) for it in items])
        return "value", req
    raise ValueError(f"未知 batch type: {kind!r}")


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.resolutions",
        description="A5 集中确认（pending 查询 / 批量 validate / 批量 submit）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pending = sub.add_parser("pending", help="列出待确认项")
    p_pending.add_argument("--company", required=True, help="company_id")
    p_pending.add_argument("--issue-type", default=None,
                           help="MAPPING_CONFIRMATION / VALUE_SOURCE_RESOLUTION / INSUFFICIENT_SCOPE")
    p_pending.add_argument("--db", default=None)

    p_val = sub.add_parser("validate", help="只校验批量请求，不落盘")
    p_val.add_argument("--input", required=True, help="batch JSON 路径")
    p_val.add_argument("--db", default=None)

    p_sub = sub.add_parser("submit", help="提交批量确认（全有或全无）")
    p_sub.add_argument("--input", required=True, help="batch JSON 路径")
    p_sub.add_argument("--db", default=None)

    args = parser.parse_args(argv)
    store.init_db(args.db or store.DEFAULT_DB_PATH)

    if args.command == "pending":
        result = list_pending(args.company, PendingFilters(issue_type=args.issue_type))
        summary = {
            "company_id": result.company_id,
            "mapping_confirmation_count": result.mapping_confirmation_count,
            "value_source_resolution_count": result.value_source_resolution_count,
            "insufficient_scope_count": result.insufficient_scope_count,
            "items": [
                {"issue_type": i.issue_type, "issue_id": i.issue_id,
                 "statement_type": i.payload.get("statement_type"),
                 "standard_item_code": i.payload.get("standard_item_code")
                 or i.payload.get("raw_item_text"),
                 "stale": i.stale,
                 "resolvable": i.payload.get("resolvable", True)}
                for i in result.items[:20]
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    kind, req = _load_request(args.input)
    if args.command == "validate":
        # validate 只做逐项校验，不提交：复用提交函数但不落盘。
        if kind == "mapping":
            result = submit_mapping_resolutions(req)
        else:
            result = submit_value_resolutions(req)
        print(json.dumps({
            "kind": kind, "committed": result.committed,
            "accepted": result.accepted,
            "errors": [{"index": e.index, "field": e.field, "message": e.message}
                       for e in result.errors],
        }, ensure_ascii=False, indent=2))
        return 0

    if kind == "mapping":
        result = submit_mapping_resolutions(req)
    else:
        result = submit_value_resolutions(req)
    print(json.dumps({
        "kind": kind, "committed": result.committed,
        "accepted": result.accepted,
        "errors": [{"index": e.index, "field": e.field, "message": e.message}
                   for e in result.errors],
    }, ensure_ascii=False, indent=2))
    return 0 if result.committed else 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
