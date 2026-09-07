"""A6 快照构建：准入、异常固化、current 切换、inspect、定向失效检测（§6）。

职责：
- `build_snapshot(request)` 把「当前 Record Set + 当前 Reconciliation + active Resolution」
  准入成不可变 FinancialSnapshot / SnapshotItem / SnapshotException，并原子切换 current。
- 严格准入（§6.2）：company/scope/currency/期间/期间类型/重述版本完整且相容；未隔离；
  MATCHED 只存一次（source_refs 全保留）、SINGLE_SOURCE 单来源、CONFLICT 仅接受
  candidate_set 匹配的 active Resolution、INSUFFICIENT_SCOPE / 未确认映射 / 未知维度 /
  勾稽失败 / 隔离输入不得作为可计算值。
- 异常固化（§6.3）：缺口不静默丢失；report_blocked 不等于事务失败。
- 原子提交（§6.4）走 store.commit_snapshot_atomic（含 checkpoint 同事务 + current 切换）。
- 定向失效检测（§9）：只报告受影响依赖，不因无关公司/来源/期间变化而全量失效。

本模块不计算任何指标、不调用 LLM、不读取 V1 财务表。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from financial_v2 import formulas
from financial_v2 import mapping
from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator

logger = logging.getLogger(__name__)

# 快照版本标签（首版；不参与 snapshot_id 派生，派生见 S.derive_snapshot_id）。
SNAPSHOT_VERSION = "1.0"

# 公式版本一律取自 formulas.ACTIVE_FORMULA_VERSIONS（注册表活跃版本映射），
# 不硬编码 "1.0"（Fix 4：版本随注册表升级同步派生，杜绝「锁定旧版本」）。

# 阻断模型（任务书 §6.3）：
# - 恒阻断：未解决冲突 / scope 不足 / 重述歧义 / 隔离输入 / 决议失效 / 缺必需科目，
#   均表示某值无法被准入为可计算值 → 阻断正式报告导出。
# - 条件阻断：未确认映射 / 勾稽失败，仅当影响「必算公式 / 主体偿债 / 关键数字」
#   时才阻断（detail["critical"] 判定），非关键问题仍固化审计异常但不阻断。
_ALWAYS_BLOCKING_EXCEPTION_TYPES = frozenset({
    "UNRESOLVED_CONFLICT",
    "INSUFFICIENT_SCOPE",
    "AMBIGUOUS_RESTATEMENT",
    "QUARANTINED_INPUT",
    "STALE_RESOLUTION",
    "MISSING_REQUIRED_ITEM",
})
_CONDITIONAL_EXCEPTION_TYPES = frozenset({"UNCONFIRMED_MAPPING", "CHECK_FAILED"})

# 三大表关键数字 + 主体偿债判断核心科目（§6.3：即使不在 required_formula_ids 也阻断）。
_KEY_CREDIT_ITEMS = frozenset({
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY",
    "CURRENT_ASSETS", "CURRENT_LIABILITIES", "INVENTORY", "PREPAYMENTS",
    "TOTAL_PROFIT", "INTEREST_EXPENSE", "FINANCE_EXPENSES",
    "TOTAL_REVENUE", "OPERATING_COST", "NET_PROFIT", "OPERATING_CASH_FLOW",
})


def _exception_is_blocking(e: S.SnapshotException) -> bool:
    if e.exception_type in _ALWAYS_BLOCKING_EXCEPTION_TYPES:
        return True
    if e.exception_type in _CONDITIONAL_EXCEPTION_TYPES:
        return bool(e.detail.get("critical", False))
    return False


def _critical_item_codes(req: SnapshotBuildRequest) -> set[str]:
    """关键科目集合 = 必算公式输入 ∪ 三大表关键数字/主体偿债核心（§6.3）。

    Fix 4：未知公式 ID 在请求校验阶段已 fail-closed（_validate_request 抛 ValidationError），
    此处不再 try/except KeyError 静默跳过——get_formula 取活跃版本，未知即抛。
    """
    codes: set[str] = set(_KEY_CREDIT_ITEMS)
    for fid in req.required_formula_ids:
        fd = formulas.get_formula(fid)  # 活跃版本；未知 → KeyError（fail-closed）
        codes.update(fd.input_item_codes)
    return codes


@dataclass
class SnapshotBuildRequest:
    """快照构建请求（任务书 §5.1；不得只传 company_id 任选数字）。"""

    company_id: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    record_set_ids: list[str]
    reconciliation_run_id: str | None
    required_formula_ids: list[str]
    restatement_selection: dict[str, str]
    policy_adjustments: dict[str, list[str]]
    run_id: str


@dataclass
class SnapshotBuildResult:
    """快照构建结果（§5.1）。"""

    snapshot: S.FinancialSnapshot
    items: list[S.SnapshotItem]
    exceptions: list[S.SnapshotException]
    reused: bool
    current_switched: bool
    report_blocked: bool


@dataclass
class SnapshotInspection:
    """快照审查视图（§6.1）：头 + items + exceptions + 指标数 + 有效性 + 单来源键。"""

    snapshot: S.FinancialSnapshot
    items: list[S.SnapshotItem]
    exceptions: list[S.SnapshotException]
    metric_count: int
    validity: str | None
    single_source_keys: list[str]


@dataclass
class SnapshotStalenessResult:
    """定向失效检测结果（§9）：依赖变化范围 + 是否已失效。"""

    snapshot_id: str
    stale: bool
    reasons: list[str]
    validity: str | None
    dependencies: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comparison_key(r: S.SourceFinancialRecord) -> str:
    return S.comparison_key(
        r.company_id, r.standard_item_code, r.statement_type, r.report_period,
        r.period_type, r.statement_scope, r.currency, r.restatement_version)


def _restatement_base_key(r: S.SourceFinancialRecord) -> str:
    """重述无关的 7 维基键（restatement_version 用固定占位），用于检测重述歧义。

    基键用于 restatement_selection 的 key：请求据此选定某 7 维条目应采用的
    restatement_version。真实 restatement_version 恒不等于占位串，无碰撞。
    """
    return S.comparison_key(
        r.company_id, r.standard_item_code, r.statement_type, r.report_period,
        r.period_type, r.statement_scope, r.currency, "__RESTATEMENT_ANY__")


def _missing_dimensions(r: S.SourceFinancialRecord) -> list[str]:
    dims = {
        "company_id": r.company_id,
        "standard_item_code": r.standard_item_code,
        "statement_type": r.statement_type,
        "report_period": r.report_period,
        "period_type": r.period_type,
        "statement_scope": r.statement_scope,
        "currency": r.currency,
        "restatement_version": r.restatement_version,
    }
    return [k for k, v in dims.items() if v is None or v == ""]


def _candidate_set_hash(records: list[S.SourceFinancialRecord]) -> str:
    """决议绑定输入的候选集合哈希（与 resolutions._candidate_set_hash 一致）。"""
    parts = sorted(f"{r.record_id}:{r.record_hash}" for r in records)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 请求校验 / 政策调整转换
# ---------------------------------------------------------------------------

def _validate_request(req: SnapshotBuildRequest) -> None:
    if not req.company_id:
        raise validator.ValidationError("company_id 不能为空")
    if not req.as_of_date:
        raise validator.ValidationError("as_of_date 不能为空")
    if req.scope not in S.STATEMENT_SCOPES:
        raise validator.ValidationError(f"scope 非法: {req.scope!r}")
    if req.currency not in S.CURRENCIES:
        raise validator.ValidationError(f"currency 非法: {req.currency!r}")
    if not req.purpose:
        raise validator.ValidationError("purpose 不能为空")
    if not req.record_set_ids:
        raise validator.ValidationError("record_set_ids 不能为空")
    if not req.run_id:
        raise validator.ValidationError("run_id 不能为空")
    if not isinstance(req.required_formula_ids, list):
        raise validator.ValidationError("required_formula_ids 必须为 list")
    if not isinstance(req.restatement_selection, dict):
        raise validator.ValidationError("restatement_selection 必须为 dict")
    if not isinstance(req.policy_adjustments, dict):
        raise validator.ValidationError("policy_adjustments 必须为 dict")
    # Fix 4：必算公式 ID 在请求校验阶段即 fail-closed（注册表校验），未知 ID / 未知版本
    # 立即 ValidationError，快照不得提交；不做 try/except KeyError 静默跳过。
    for fid in req.required_formula_ids:
        if not isinstance(fid, str) or not fid:
            raise validator.ValidationError(f"required_formula_ids 含非法项: {fid!r}")
        version = formulas.ACTIVE_FORMULA_VERSIONS.get(fid)
        if version is None:
            raise validator.ValidationError(f"未知公式 ID（未注册）: {fid!r}")
        formulas.get_formula(fid, version)  # 活跃版本未注册同样 fail-closed


def _build_policy_adjustments(req: SnapshotBuildRequest,
                              records: list[S.SourceFinancialRecord]) -> list[S.PolicyAdjustment]:
    """把请求的 policy_adjustments（type → record_refs）转成 PolicyAdjustment 列表并校验引用。"""
    record_ids = {r.record_id for r in records}
    pas: list[S.PolicyAdjustment] = []
    for atype, refs in req.policy_adjustments.items():
        if atype not in S.POLICY_ADJUSTMENT_TYPES:
            raise validator.ValidationError(f"非法 policy_adjustment 类型: {atype!r}")
        if not isinstance(refs, list):
            raise validator.ValidationError(f"policy_adjustments[{atype!r}] 必须为 list")
        refs = sorted(set(refs))
        for ref in refs:
            if ref not in record_ids:
                raise validator.ValidationError(
                    f"policy_adjustment 引用不存在记录（跨快照/伪造引用拒绝）: {ref!r}")
        decision = "EXCLUDE_CONFIRMED_RECORDS" if refs else "NO_ADDITIONAL_EXCLUSION_CONFIRMED"
        pas.append(S.PolicyAdjustment(
            adjustment_type=atype, decision=decision, record_refs=refs,
            reason_code="HUMAN_CONFIRMED", note="", operator="snapshot_builder",
            confirmed_at=_utcnow()))
    return pas


# ---------------------------------------------------------------------------
# 准入
# ---------------------------------------------------------------------------

def _make_exception(etype: str, r: S.SourceFinancialRecord, detail: dict,
                    *, comparison_key: str | None = None) -> S.SnapshotException:
    return S.SnapshotException(
        snapshot_id="",  # build_snapshot 后补
        comparison_key=comparison_key or _comparison_key(r),
        standard_item_code=r.standard_item_code or "",
        exception_type=etype,
        blocking_reason=detail.get("blocking_reason", etype),
        impact_scope=detail.get("impact_scope", []),
        detail=detail,
    )


def _make_group_exception(etype: str, records: list[S.SourceFinancialRecord],
                          detail: dict) -> S.SnapshotException:
    first = records[0]
    return S.SnapshotException(
        snapshot_id="", comparison_key=_comparison_key(first),
        standard_item_code=first.standard_item_code or "",
        exception_type=etype,
        blocking_reason=detail.get("blocking_reason", etype),
        impact_scope=detail.get("impact_scope", []),
        detail=detail,
    )


def _emit_item(items: list[S.SnapshotItem], records: list[S.SourceFinancialRecord],
               *, resolution_id: str | None) -> None:
    """把一组同标准值记录收敛为单个 SnapshotItem（source_refs 全保留）。"""
    values = {r.std_value for r in records if r.std_value is not None}
    if len(values) != 1:
        raise RuntimeError(
            f"准入组标准值不唯一（上游 MATCHED 却值不等）: {sorted(map(str, values))}")
    first = records[0]
    items.append(S.SnapshotItem(
        snapshot_id="",  # build_snapshot 后补
        comparison_key=_comparison_key(first),
        standard_item_code=first.standard_item_code,
        amount=next(iter(values)),
        unit=first.std_unit or None,
        report_period=first.report_period,
        period_type=first.period_type,
        statement_type=first.statement_type,
        statement_scope=first.statement_scope,
        currency=first.currency,
        restatement_version=first.restatement_version,
        source_refs=sorted({r.record_id for r in records}),
        resolution_id=resolution_id,
    ))


def _resolve_state(comparison_key: str, group_records: list[S.SourceFinancialRecord],
                   groups_by_key: dict[str, S.ReconciliationGroupResult]) -> str:
    """判定组状态：优先用对账运行结果（精确准入），否则本地重算。"""
    g = groups_by_key.get(comparison_key)
    if g is not None:
        return g.state
    distinct = {str(r.std_value) for r in group_records if r.std_value is not None}
    if len(group_records) == 1:
        return "SINGLE_SOURCE"
    if len(distinct) <= 1:
        return "MATCHED"
    return "CONFLICT"


def _admit(req: SnapshotBuildRequest, records: list[S.SourceFinancialRecord],
           groups_by_key: dict[str, S.ReconciliationGroupResult],
           excluded_record_ids: frozenset[str],
           ) -> tuple[list[S.SnapshotItem], list[S.SnapshotException], list[str]]:
    """逐条准入 + 重述歧义 + 冲突决议，返回 (items, exceptions, used_resolution_ids)。

    excluded_record_ids = 未确认映射 ∪ 勾稽失败影响的记录；这两类异常分别由
    _unresolved_mapping_exceptions / _check_failed_exceptions 固化，此处只排除不重复报。
    """
    items: list[S.SnapshotItem] = []
    exceptions: list[S.SnapshotException] = []
    used_resolution_ids: list[str] = []

    admitted: dict[str, list[S.SourceFinancialRecord]] = {}
    for r in records:
        # 不同 scope/currency 的数据属于别的快照，非本次错误，静默排除。
        if r.statement_scope != req.scope:
            continue
        if r.currency != req.currency:
            continue
        if r.record_id in excluded_record_ids:
            continue
        missing = _missing_dimensions(r)
        if missing:
            exceptions.append(_make_exception(
                "INSUFFICIENT_SCOPE", r,
                {"missing_dimensions": missing, "record_id": r.record_id}))
            continue
        if store.is_quarantined("source_financial_record", r.record_id) \
                or store.is_quarantined("financial_record_set", r.record_set_version):
            exceptions.append(_make_exception(
                "QUARANTINED_INPUT", r, {"record_id": r.record_id}))
            continue
        if r.std_value is None:
            exceptions.append(_make_exception(
                "MISSING_REQUIRED_ITEM", r, {"record_id": r.record_id}))
            continue
        admitted.setdefault(_comparison_key(r), []).append(r)

    # 重述歧义：同一 7 维基键存在多个 restatement_version 时，必须明确选择。
    family: dict[str, list[str]] = {}
    for ck in admitted:
        family.setdefault(_restatement_base_key(admitted[ck][0]), []).append(ck)
    for base, cks in family.items():
        if len(cks) == 1:
            continue
        chosen = req.restatement_selection.get(base)
        # 先在删除前快照全部 restatement_version（避免删除后引用 admitted[c] 越界）。
        restatement_versions = sorted({admitted[c][0].restatement_version for c in cks})
        for ck in cks:
            rv = admitted[ck][0].restatement_version
            if chosen is None or rv != chosen:
                exceptions.append(_make_group_exception(
                    "AMBIGUOUS_RESTATEMENT", admitted[ck],
                    {"base_key": base, "restatement_versions": restatement_versions}))
                del admitted[ck]

    # 逐组准入。
    for ck in sorted(admitted):
        group_records = admitted[ck]
        state = _resolve_state(ck, group_records, groups_by_key)
        if state in ("MATCHED", "SINGLE_SOURCE"):
            _emit_item(items, group_records, resolution_id=None)
        elif state == "CONFLICT":
            res = store.get_active_resolution(ck)
            if res is None:
                exceptions.append(_make_group_exception(
                    "UNRESOLVED_CONFLICT", group_records,
                    {"candidate_record_ids": sorted(r.record_id for r in group_records)}))
                continue
            if res.candidate_set_hash != _candidate_set_hash(group_records):
                exceptions.append(_make_group_exception(
                    "STALE_RESOLUTION", group_records,
                    {"resolution_id": res.resolution_id,
                     "reason": "决议绑定候选集合与当前集合不匹配"}))
                continue
            accepted = [r for r in group_records if r.record_id in set(res.accepted_record_ids)]
            if not accepted:
                exceptions.append(_make_group_exception(
                    "UNRESOLVED_CONFLICT", group_records,
                    {"resolution_id": res.resolution_id, "accepted_record_ids": res.accepted_record_ids}))
                continue
            _emit_item(items, accepted, resolution_id=res.resolution_id)
            used_resolution_ids.append(res.resolution_id)
        elif state == "INSUFFICIENT_SCOPE":
            exceptions.append(_make_group_exception(
                "INSUFFICIENT_SCOPE", group_records,
                {"candidate_record_ids": sorted(r.record_id for r in group_records)}))

    return items, exceptions, used_resolution_ids


# ---------------------------------------------------------------------------
# 定点修复：上游候选/问题血缘、勾稽失败、必算公式输入闭环
# ---------------------------------------------------------------------------

def _unresolved_mapping_exceptions(req: SnapshotBuildRequest,
                                   records: list[S.SourceFinancialRecord],
                                   critical_codes: set[str]
                                   ) -> tuple[list[S.SnapshotException], set[str], list[str]]:
    """Fix 1：接入上游候选/问题血缘（精确 input_candidate_set_version 指针，非 source_version 模糊回扫）。

    通过 record_set.input_candidate_set_version 读取候选，再查各候选当前 active 映射决议：
    - 有效决议（chosen_item_code 与已产出记录的 standard_item_code 一致）→ 已确认，可正常
      准入，不排除、不报 UNCONFIRMED_MAPPING；
    - chosen_item_code 与产出记录不一致 / 候选集不匹配 / 决议已失效 → fail-closed（当作未确认）；
    - 无有效决议 → 未确认映射，固化 UNCONFIRMED_MAPPING（命中关键集合 → critical=True 阻断）。
    未确认映射记录不得作为可计算值（§6.2），返回其 record_id 集合供 _admit 排除。
    返回本次准入实际读取的全部 active 映射决议 ID（采纳 + fail-closed 排除）——进入准入依赖
    身份（Fix：影响排除/异常内容的决议也必须派生新 snapshot_id）。
    """
    # 读取候选血缘（精确指针），构建 candidate_id → active 决议 映射（含 record_set_version
    # 用于候选集匹配校验）。
    resolved: dict[str, S.MappingResolution] = {}
    for rs_id in req.record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is None or not rs.input_candidate_set_version:
            continue
        for cand in store.list_candidates(rs.input_candidate_set_version):
            mr = store.get_active_mapping_resolution(cand.candidate_id)
            if mr is not None and mr.chosen_item_code is not None \
                    and mr.record_set_version == cand.record_set_version:
                resolved[cand.candidate_id] = mr

    exceptions: list[S.SnapshotException] = []
    excluded: set[str] = set()
    read_resolution_ids: list[str] = []
    for r in records:
        if r.mapping_mode != "llm_suggested":
            continue
        mr = resolved.get(r.candidate_id)
        if mr is None and r.candidate_id:
            # 候选不在输入候选集（跨集合追溯）→ 回退直查当前 active 决议（仍 fail-closed）。
            mr = store.get_active_mapping_resolution(r.candidate_id)
        if mr is not None and mr.chosen_item_code is not None:
            # 该决议被本次准入实际读取并用于判定（采纳 or fail-closed 排除），进入准入依赖身份。
            read_resolution_ids.append(mr.resolution_id)
        if mr is not None and mr.chosen_item_code == r.standard_item_code:
            continue  # 有效决议 + 产出记录一致 → 已确认，正常准入
        excluded.add(r.record_id)
        exceptions.append(_make_exception(
            "UNCONFIRMED_MAPPING", r,
            {"record_id": r.record_id, "candidate_id": r.candidate_id,
             "critical": r.standard_item_code in critical_codes}))
    return exceptions, excluded, read_resolution_ids


def _check_identity(chk: S.ReconciliationCheck) -> str:
    """勾稽检查身份（进入准入依赖身份）：含 status 与结果值。

    check_id 本身不含 status（见 checks._check_id），故身份必须显式纳入 status 与
    left/right/diff/tolerance，使「同一检查从 PASS 变 FAIL」派生新 snapshot_id（Fix 3）。
    """
    return "|".join([
        chk.check_id, chk.run_id, chk.record_set_version, chk.check_type, chk.status,
        json.dumps(sorted(chk.input_record_ids), ensure_ascii=False),
        str(chk.left_value), str(chk.right_value), str(chk.diff), str(chk.tolerance),
    ])


def _check_failed_exceptions(req: SnapshotBuildRequest,
                             records: list[S.SourceFinancialRecord],
                             critical_codes: set[str]
                             ) -> tuple[list[S.SnapshotException], set[str], list[str]]:
    """Fix 2：接入同源勾稽失败（按输入 record_set_version 读取，非 reconciliation run_id）。

    - NOT_RUN_MISSING_INPUT ≠ FAIL：只有 status=FAIL 才固化 CHECK_FAILED。
    - FAIL 的 input_record_ids 受影响记录不得作为可计算值（§6.2），返回其 record_id 集合。
    - 异常携带 run/check/record/item 引用；受影响科目命中关键集合 → critical=True（阻断）。
    - 无关期间/scope/currency/record_set 的勾稽结果不参与（只读请求的 record_set_ids）。
    - 同时返回本次读取的全部勾稽检查身份（含 PASS/FAIL/NOT_RUN），供准入依赖身份派生（Fix 3）。
    """
    checks = store.list_reconciliation_checks_by_record_sets(req.record_set_ids)
    rec_by_id = {r.record_id: r for r in records}
    exceptions: list[S.SnapshotException] = []
    failed: set[str] = set()
    identities: list[str] = []
    for chk in checks:
        identities.append(_check_identity(chk))
        if chk.status != "FAIL":
            continue
        affected = [rec_by_id[rid] for rid in chk.input_record_ids if rid in rec_by_id]
        if not affected:
            continue
        affected_ids = sorted({r.record_id for r in affected})
        item_codes = sorted({r.standard_item_code for r in affected if r.standard_item_code})
        failed.update(affected_ids)
        critical = any(code in critical_codes for code in item_codes)
        exceptions.append(_make_group_exception(
            "CHECK_FAILED", affected,
            {"run_id": chk.run_id, "check_id": chk.check_id,
             "check_type": chk.check_type, "record_ids": affected_ids,
             "item_codes": item_codes, "critical": critical,
             "left": chk.left_value, "right": chk.right_value, "diff": chk.diff}))
    return exceptions, failed, sorted(set(identities))


def _uncovered_candidate_exceptions(req: SnapshotBuildRequest,
                                    records: list[S.SourceFinancialRecord],
                                    critical_codes: set[str]
                                    ) -> tuple[list[S.SnapshotException], list[str]]:
    """Fix 2：覆盖「完全未产出 SourceFinancialRecord」的候选（按 input_candidate_set_version 精确读）。

    沿 input_candidate_set_version 遍历全部候选，构建 candidate → active 映射决议 →
    已产出记录的闭包：
    - 已产出记录（规则映射或已确认）→ 非「未产出」，跳过（不重复报已闭口候选）；
    - 无产出记录 → 审计为未覆盖候选（固化 UNCONFIRMED_MAPPING 诊断，UNMAPPED 为不可识别代号）；
    - critical=True 仅当候选可识别为必算公式输入/关键科目且 parsed_numeric_value 非空
      （非数值表头/说明行绝不阻断整份报告）；不可识别候选 → 非阻断诊断。
    不做 source_version 模糊回扫（仅走精确 input_candidate_set_version 指针）。
    返回 (exceptions, read_resolution_ids)：read_resolution_ids 为本次读取并用于确定
    identified_item_code / critical / report_blocked 的 active 映射决议 ID（进入准入依赖身份）。
    """
    output_by_candidate: dict[str, S.SourceFinancialRecord] = {
        r.candidate_id: r for r in records if r.candidate_id
    }
    rules = mapping.build_builtin_rules()
    exceptions: list[S.SnapshotException] = []
    read_resolution_ids: list[str] = []
    for rs_id in req.record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is None or not rs.input_candidate_set_version:
            continue
        for cand in store.list_candidates(rs.input_candidate_set_version):
            if cand.candidate_id in output_by_candidate:
                continue  # 已产出记录（规则映射或已确认），非「未产出」候选
            mr = store.get_active_mapping_resolution(cand.candidate_id)
            identified_code: str | None = None
            if mr is not None and mr.chosen_item_code is not None \
                    and mr.record_set_version == cand.record_set_version:
                identified_code = mr.chosen_item_code
                # 该决议被本次准入读取并决定 identified_item_code / critical / report_blocked，
                # 进入准入依赖身份（未产出候选的 active 决议同样影响异常内容）。
                read_resolution_ids.append(mr.resolution_id)
            else:
                outcome = mapping.map_candidate(cand, rules)
                if outcome.status == "mapped":
                    identified_code = outcome.standard_item_code
            critical = (cand.parsed_numeric_value is not None
                        and identified_code is not None
                        and identified_code in critical_codes)
            code = identified_code or "UNMAPPED"
            # 合成稳定 comparison_key（含 candidate_id）保证异常唯一键不碰撞。
            comparison_key = "ck-unc-" + hashlib.sha256(
                cand.candidate_id.encode("utf-8")).hexdigest()[:32]
            exceptions.append(S.SnapshotException(
                snapshot_id="",
                comparison_key=comparison_key,
                standard_item_code=code,
                exception_type="UNCONFIRMED_MAPPING",
                blocking_reason="UNCONFIRMED_MAPPING",
                impact_scope=[],
                detail={
                    "reason": "uncovered_candidate",
                    "candidate_id": cand.candidate_id,
                    "raw_item_text": cand.raw_item_text,
                    "identified_item_code": identified_code,
                    "critical": critical,
                },
            ))
    return exceptions, read_resolution_ids


def _prior_period_of(period: str, period_type: str) -> str | None:
    """由当前报告期推导上一报告期（与 metrics._prior_period 语义一致，供缺口闭环复用）。"""
    try:
        y, m, _ = (int(x) for x in period.split("-"))
    except (ValueError, AttributeError):
        return None
    if period_type == "annual":
        return f"{y - 1:04d}-12-31"
    if period_type == "quarterly":
        return {"03": f"{y - 1:04d}-12-31", "06": f"{y:04d}-03-31",
                "09": f"{y:04d}-06-30", "12": f"{y:04d}-09-30"}.get(f"{m:02d}")
    if period_type == "interim":
        return f"{y:04d}-06-30" if m == 12 else f"{y - 1:04d}-12-31"
    return None


_PRIOR_PERIOD_REQUIREMENTS = frozenset({"flow/avg", "yoy_flow", "yoy_end"})


def _required_formula_gap_exceptions(req: SnapshotBuildRequest,
                                     items: list[S.SnapshotItem],
                                     ) -> list[S.SnapshotException]:
    """Fix 3：快照构建后、提交前，对 request.required_formula_ids 做输入闭环。

    复用 formulas.compute_formula（与 metrics 层口径一致），自动继承代理/前期/可选/NA 判定：
    - CALCULATED_PROXY（如利息保障以 FINANCE_EXPENSES 代理）→ 非缺口；
    - NOT_APPLICABLE（季报增长率）→ 非缺口；
    - MISSING_INPUT 且 detail["missing"] 非空 → 完全缺失的必需科目 → MISSING_REQUIRED_ITEM；
    - 仅有 missing_prior（前期缺失）→ 非「完全缺失必需科目」，不在此固化；
    - 未知 formula_id 已在 _validate_request fail-closed，此处不再 try/except 跳过（Fix 4）。
    按 (formula_id, missing_code, period, scope, currency) 去重；仅检查 requested 公式。
    """
    scope, currency = req.scope, req.currency
    # {report_period: {code: amount}} 仅含请求 scope/currency 的已准入项。
    by_period: dict[str, dict[str, Decimal]] = {}
    period_types: dict[str, str] = {}
    for it in items:
        if it.amount is None or it.statement_scope != scope or it.currency != currency:
            continue
        by_period.setdefault(it.report_period, {})[it.standard_item_code] = it.amount
        period_types.setdefault(it.report_period, it.period_type)

    # 去重维度 (code, period, scope, currency)：同一缺失科目被多个公式引用只固化一条异常，
    # 避免与 snapshot_store 的 (comparison_key, standard_item_code, exception_type) 唯一键冲突。
    # 仅检查主报告期 as_of_date：前期（2023 等）仅为同比/平均的分母依赖，天然只有子集，
    # 其缺失输入属于 MISSING_PRIOR_PERIOD 而非「完全缺失必需科目」，不应触发阻断。
    # Fix 4：as_of_date 不在已准入 items 中时，绝不静默当作「无缺口」——current={} 会让
    # 必算公式 MISSING_INPUT，进而固化阻断缺口（主报告期整体缺失必须阻断）。
    periods_to_check = [req.as_of_date]
    gaps: dict[tuple[str, str], set[str]] = {}
    for fid in req.required_formula_ids:
        fd = formulas.get_formula(fid)  # 活跃版本；未知已在 _validate_request fail-closed
        for period in periods_to_check:
            period_type = period_types.get(period, "annual")
            current = by_period.get(period, {})
            prior: dict[str, Decimal] = {}
            if fd.period_requirement in _PRIOR_PERIOD_REQUIREMENTS:
                pp = _prior_period_of(period, period_type)
                if pp is not None:
                    prior = by_period.get(pp, {})
            outcome = formulas.compute_formula(
                fid, current, prior, policy={}, period_type=period_type)
            if outcome.status != "MISSING_INPUT":
                continue
            for code in (outcome.detail.get("missing") or []):
                gaps.setdefault((code, period), set()).add(fid)

    exceptions: list[S.SnapshotException] = []
    for (code, period), fids in sorted(gaps.items()):
        period_type = period_types.get(period, "annual")
        exceptions.append(S.SnapshotException(
            snapshot_id="",
            comparison_key=S.comparison_key(
                req.company_id, code, "", period, period_type, scope, currency, ""),
            standard_item_code=code,
            exception_type="MISSING_REQUIRED_ITEM",
            blocking_reason="MISSING_REQUIRED_ITEM",
            impact_scope=sorted(fids),
            detail={"formula_ids": sorted(fids), "period": period,
                    "scope": scope, "currency": currency,
                    "missing_item_code": code},
        ))
    return exceptions


def _make_checkpoint(req: SnapshotBuildRequest, snapshot_id: str,
                     resolution_refs: list[str], now: str) -> S.Checkpoint:
    return S.Checkpoint(
        checkpoint_id="cp-" + hashlib.sha256(
            f"{req.run_id}|{snapshot_id}".encode("utf-8")).hexdigest()[:24],
        run_id=req.run_id,
        stage_id="SNAPSHOT_BUILD",
        state_version=1,
        artifact_refs=[snapshot_id],
        input_hashes={rs: rs for rs in sorted(set(req.record_set_ids))},
        dependency_versions={
            "snapshot_builder_version": S.SNAPSHOT_BUILDER_VERSION,
            "admission_rule_version": S.ADMISSION_RULE_VERSION,
        },
        resolution_refs=sorted(resolution_refs),
        completed_unit_ids=[],
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 对外接口（§6.1）
# ---------------------------------------------------------------------------

def build_snapshot(request: SnapshotBuildRequest, persist: bool = True) -> SnapshotBuildResult:
    """准入 + 选择 + 固化 + 原子切换 current（§6.1/§6.4）。"""
    _validate_request(request)
    record_set_ids = sorted(set(request.record_set_ids))

    # 1. 加载输入记录（缺失 record_set 属构建失败，不得提交）。
    records: list[S.SourceFinancialRecord] = []
    for rs_id in record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is None:
            raise KeyError(f"record_set 不存在: {rs_id}")
        records.extend(store.list_records(rs_id))
    if not records:
        raise KeyError("输入 record_sets 下无任何标准化记录")

    # 2. 跨公司拒绝。
    companies = {r.company_id for r in records}
    if companies != {request.company_id}:
        raise ValueError(f"记录公司归属与请求不符: {sorted(companies)} != {[request.company_id]}")

    # 3. 精确准入：reconciliation run 必须存在且输入集合与请求一致。
    groups_by_key: dict[str, S.ReconciliationGroupResult] = {}
    if request.reconciliation_run_id is not None:
        run = store.get_reconciliation_run(request.reconciliation_run_id)
        if run is None:
            raise KeyError(f"reconciliation run 不存在: {request.reconciliation_run_id}")
        if run.company_id != request.company_id:
            raise ValueError("reconciliation run 公司归属与请求不符")
        if sorted(run.input_record_set_ids) != record_set_ids:
            raise ValueError(
                f"reconciliation run 输入集合与请求不一致: "
                f"{sorted(run.input_record_set_ids)} != {record_set_ids}")
        groups_by_key = {
            g.comparison_key: g
            for g in store.list_reconciliation_group_results(request.reconciliation_run_id)
        }

    # 4. 来源版本（参与快照身份）。
    source_versions: list[str] = []
    for rs_id in record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is not None and rs.source_version not in source_versions:
            source_versions.append(rs.source_version)
    source_versions.sort()

    # 5. 政策调整（校验类型 + 引用）。
    policy_adjustments = _build_policy_adjustments(request, records)

    # 6. 关键性判定（必算公式输入 ∪ 关键数字）→ 决定条件阻断异常的 critical 标记。
    critical_codes = _critical_item_codes(request)

    # 7. 上游候选/问题血缘（Fix 1）+ 同源勾稽失败（Fix 2）+ 未覆盖候选（Fix 2）：
    #    固化异常并排除受影响记录，同时收集准入依赖身份（Fix 3）。
    mapping_exceptions, mapping_excluded, mapping_resolution_ids = \
        _unresolved_mapping_exceptions(request, records, critical_codes)
    check_exceptions, check_failed_record_ids, check_identities = \
        _check_failed_exceptions(request, records, critical_codes)
    uncovered_exceptions, uncovered_resolution_ids = \
        _uncovered_candidate_exceptions(request, records, critical_codes)
    excluded_record_ids = frozenset(mapping_excluded | check_failed_record_ids)

    # 8. 准入（未确认映射 / 勾稽失败记录已排除）。
    items, admit_exceptions, used_resolution_ids = _admit(
        request, records, groups_by_key, excluded_record_ids)

    # 9. 必算公式输入闭环（Fix 3）：构建后、提交前，对 required_formula_ids 补 MISSING_REQUIRED_ITEM。
    formula_gap_exceptions = _required_formula_gap_exceptions(request, items)

    exceptions = admit_exceptions + mapping_exceptions + check_exceptions \
        + uncovered_exceptions + formula_gap_exceptions

    # 10. 报告阻断：恒阻断异常，或命中关键集合的条件阻断异常（§6.3）。
    report_blocked = any(_exception_is_blocking(e) for e in exceptions)

    # 11. 公式版本锁定：取自注册表活跃版本映射（Fix 4，不硬编码 "1.0"）。
    required_formula_versions = {
        fid: formulas.ACTIVE_FORMULA_VERSIONS[fid] for fid in request.required_formula_ids
    }

    # 12. 准入依赖身份（Fix 3）：本次读取的全部 active 映射决议（已采纳 + 未产出候选，
    #     含 fail-closed 排除）+ 本次勾稽检查身份 + 输入候选集合版本，全部纳入 snapshot_id
    #     派生，任何变化 → 新 snapshot_id。
    candidate_input_set_versions: list[str] = []
    for rs_id in record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is not None and rs.input_candidate_set_version:
            candidate_input_set_versions.append(rs.input_candidate_set_version)
    admission_dependencies = {
        "mapping_resolution_ids": sorted(set(mapping_resolution_ids) | set(uncovered_resolution_ids)),
        "reconciliation_check_identities": check_identities,
        "candidate_input_set_versions": sorted(set(candidate_input_set_versions)),
    }

    # 13. 派生快照身份 + 头部。
    now = _utcnow()
    snapshot_id = S.derive_snapshot_id(
        request.company_id, request.scope, request.currency, request.as_of_date,
        request.purpose, record_set_ids, request.reconciliation_run_id,
        source_versions, sorted(used_resolution_ids), request.restatement_selection,
        policy_adjustments, required_formula_versions,
        S.SNAPSHOT_BUILDER_VERSION, S.ADMISSION_RULE_VERSION, admission_dependencies)
    snapshot = S.FinancialSnapshot(
        snapshot_id=snapshot_id, snapshot_version=SNAPSHOT_VERSION,
        company_id=request.company_id, as_of_date=request.as_of_date,
        scope=request.scope, currency=request.currency, purpose=request.purpose,
        source_versions=source_versions, resolution_versions=sorted(used_resolution_ids),
        record_set_ids=record_set_ids, reconciliation_run_id=request.reconciliation_run_id,
        restatement_selection=request.restatement_selection,
        policy_adjustments=policy_adjustments,
        required_formula_versions=required_formula_versions,
        snapshot_builder_version=S.SNAPSHOT_BUILDER_VERSION,
        admission_rule_version=S.ADMISSION_RULE_VERSION,
        report_blocked=report_blocked,
        admission_dependencies=admission_dependencies,
        created_at=now)
    for it in items:
        it.snapshot_id = snapshot_id
    for ex in exceptions:
        ex.snapshot_id = snapshot_id

    # 10. 持久化（含 checkpoint 同事务 + current 切换）。
    reused = False
    current_switched = False
    if persist:
        checkpoint = _make_checkpoint(request, snapshot_id, used_resolution_ids, now)
        res = store.commit_snapshot_atomic(
            snapshot, items, exceptions, checkpoint=checkpoint, switch_current=True)
        reused = res.reused
        current_switched = res.current_switched

    return SnapshotBuildResult(
        snapshot=snapshot, items=items, exceptions=exceptions,
        reused=reused, current_switched=current_switched, report_blocked=report_blocked)


def get_snapshot(snapshot_id: str) -> S.FinancialSnapshot | None:
    return store.get_snapshot(snapshot_id)


def current_snapshot(company_id: str, scope: str, currency: str,
                     as_of_date: str, purpose: str) -> S.FinancialSnapshot | None:
    """current 指针指向的快照；stale/superseded 不返回（§9）。"""
    snap = store.get_current_snapshot(company_id, scope, currency, as_of_date, purpose)
    if snap is None:
        return None
    validity = store.latest_snapshot_validity(snap.snapshot_id)
    if validity in ("stale", "superseded"):
        return None
    return snap


def inspect_snapshot(snapshot_id: str) -> SnapshotInspection:
    snap = store.get_snapshot(snapshot_id)
    if snap is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")
    items = store.list_snapshot_items(snapshot_id)
    exceptions = store.list_snapshot_exceptions(snapshot_id)
    metric_count = len(store.list_metric_results(snapshot_id))
    validity = store.latest_snapshot_validity(snapshot_id)
    single_source_keys = [
        it.comparison_key for it in items
        if it.resolution_id is None and len(it.source_refs) == 1
    ]
    return SnapshotInspection(
        snapshot=snap, items=items, exceptions=exceptions,
        metric_count=metric_count, validity=validity,
        single_source_keys=single_source_keys)


def detect_stale_snapshot(snapshot_id: str) -> SnapshotStalenessResult:
    """定向失效检测：只报告本快照依赖（record set/source/resolution/reconciliation）变化。"""
    snap = store.get_snapshot(snapshot_id)
    if snap is None:
        raise KeyError(f"snapshot 不存在: {snapshot_id}")

    validity = store.latest_snapshot_validity(snapshot_id)
    reasons: list[str] = []
    deps: dict[str, list[str]] = {
        "record_sets": [], "sources": [], "resolutions": [], "reconciliation": [],
    }

    if validity in ("stale", "superseded"):
        reasons.append(f"validity={validity}")

    for rs_id in snap.record_set_ids:
        rs = store.get_record_set(rs_id)
        if rs is None:
            reasons.append(f"record_set 缺失: {rs_id}")
            deps["record_sets"].append(rs_id)
            continue
        src = store.get_source_version(rs.source_version)
        if src is None:
            reasons.append(f"record_set 的 source_version 缺失: {rs.source_version}")
            deps["record_sets"].append(rs_id)
            continue
        cur = store.get_current_record_set(src.source_document_id)
        if cur is not None and cur.record_set_version != rs_id:
            reasons.append(f"record_set 已更新: {rs_id} -> {cur.record_set_version}")
            deps["record_sets"].append(rs_id)

    for sv in snap.source_versions:
        src = store.get_source_version(sv)
        if src is None:
            reasons.append(f"source_version 缺失: {sv}")
            deps["sources"].append(sv)
            continue
        cur_rs = store.get_current_record_set(src.source_document_id)
        if cur_rs is not None and cur_rs.source_version != sv:
            reasons.append(f"source_version 已更新: {sv}")
            deps["sources"].append(sv)

    for res_id in snap.resolution_versions:
        v = store.latest_resolution_validity(res_id)
        if v not in (None, "active"):
            reasons.append(f"resolution 失效: {res_id} ({v})")
            deps["resolutions"].append(res_id)

    if snap.reconciliation_run_id is not None:
        cur_rec = store.get_current_reconciliation(snap.company_id)
        if cur_rec is not None and cur_rec.run_id != snap.reconciliation_run_id:
            reasons.append(
                f"reconciliation run 已更新: {snap.reconciliation_run_id} -> {cur_rec.run_id}")
            deps["reconciliation"].append(snap.reconciliation_run_id)

    return SnapshotStalenessResult(
        snapshot_id=snapshot_id, stale=bool(reasons), reasons=reasons,
        validity=validity, dependencies=deps)


# ---------------------------------------------------------------------------
# CLI（§11）
# ---------------------------------------------------------------------------

def _cli_build(req_path: str, validate_only: bool, db: str) -> dict:
    store.init_db(db)
    data = json.loads(Path(req_path).read_text(encoding="utf-8"))
    req = SnapshotBuildRequest(**data)
    result = build_snapshot(req, persist=not validate_only)
    return {
        "snapshot_id": result.snapshot.snapshot_id,
        "item_count": len(result.items),
        "exception_count": len(result.exceptions),
        "report_blocked": result.report_blocked,
        "reused": result.reused,
        "current_switched": result.current_switched,
        "persisted": not validate_only,
        "exceptions": [
            {"comparison_key": e.comparison_key, "exception_type": e.exception_type,
             "standard_item_code": e.standard_item_code}
            for e in result.exceptions
        ],
    }


def _cli_inspect(snapshot_id: str, db: str) -> dict:
    store.init_db(db)
    insp = inspect_snapshot(snapshot_id)
    return {
        "snapshot_id": insp.snapshot.snapshot_id,
        "company_id": insp.snapshot.company_id,
        "as_of_date": insp.snapshot.as_of_date,
        "scope": insp.snapshot.scope,
        "report_blocked": insp.snapshot.report_blocked,
        "validity": insp.validity,
        "item_count": len(insp.items),
        "exception_count": len(insp.exceptions),
        "metric_count": insp.metric_count,
        "single_source_keys": insp.single_source_keys,
    }


def _cli_current(company_id: str, scope: str, currency: str, as_of_date: str,
                 purpose: str, db: str) -> dict:
    store.init_db(db)
    snap = current_snapshot(company_id, scope, currency, as_of_date, purpose)
    if snap is None:
        return {"found": False}
    return {"found": True, "snapshot_id": snap.snapshot_id, "report_blocked": snap.report_blocked}


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m financial_v2.snapshots",
                                     description="financial_v2 Snapshot Builder CLI")
    parser.add_argument("--db", default=str(store.DEFAULT_DB_PATH),
                        help="SQLite 库路径（dev/test 注入临时库）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="构建快照")
    p_build.add_argument("--request", required=True, help="快照构建请求 JSON 文件")
    p_build.add_argument("--validate-only", action="store_true", help="仅准入校验，不持久化")

    p_inspect = sub.add_parser("inspect", help="审查快照")
    p_inspect.add_argument("--snapshot", required=True, dest="snapshot_id")

    p_current = sub.add_parser("current", help="查询 current 快照")
    p_current.add_argument("--company", required=True)
    p_current.add_argument("--scope", required=True)
    p_current.add_argument("--currency", required=True)
    p_current.add_argument("--as-of", required=True, dest="as_of_date")
    p_current.add_argument("--purpose", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "build":
        print(json.dumps(_cli_build(args.request, args.validate_only, args.db),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "inspect":
        print(json.dumps(_cli_inspect(args.snapshot_id, args.db),
                         ensure_ascii=False, indent=2))
    elif args.cmd == "current":
        print(json.dumps(_cli_current(args.company, args.scope, args.currency,
                                      args.as_of_date, args.purpose, args.db),
                         ensure_ascii=False, indent=2))
    else:
        return 1
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
