"""A4 标准化（§7.2 + §4.2 准入）：已映射候选 → 不可变 SourceFinancialRecord。

职责：
- 金额统一 Decimal；标准单位为 yuan，单位换算规则版本化（wan_yuan × 10000 等）。
- 只有满足 §4.2 全部准入条件的候选才生成 SourceFinancialRecord；任一关键维度
  （期间/期间类型/scope/币种/单位）缺失或不可靠 → NORMALIZATION_REQUIRED 问题，
  绝不默认 CNY / consolidated / yuan / 占位科目。
- 原始值/原始单位/标准值/标准单位/转换规则版本一并保存；record_id / record_hash
  由 schema/validator 重算校验。
- 非数值候选（EMPTY_OR_NOT_APPLICABLE / PARSE_FAILED）与未映射候选由上游（抽取 /
  映射）负责，标准化不重复处理。

本模块无 RAG / 无 LLM / 无 OCR，纯确定性。record_set 与 records 经
store.commit_record_set 单事务原子提交（含 current 切换）；NORMALIZATION_REQUIRED
问题经 store.commit_issues 追加。

CLI: python -m financial_v2.normalization --record-set <rs_id> [--validate-only] [--db <path>]
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from financial_v2 import mapping
from financial_v2 import schema as S
from financial_v2 import store
from financial_v2 import validator

logger = logging.getLogger(__name__)

NORMALIZATION_RULE_VERSION = "1.0"
CONVERSION_RULE_VERSION = "1.0"
DEFAULT_RESTATEMENT_VERSION = "0"   # 单一未重述年报 = as-reported 版本（与 A1 约定一致）

# 单位 → 元 换算乘数（版本化规则；未知单位不在此表 → 拒绝标准化）。
UNIT_TO_YUAN: dict[str, Decimal] = {
    "yuan": Decimal("1"),
    "wan_yuan": Decimal("10000"),
    "qian_yuan": Decimal("1000"),
    "yi_yuan": Decimal("100000000"),
    "baiwan_yuan": Decimal("1000000"),
    "qianwan_yuan": Decimal("10000000"),
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 策略与结果
# ---------------------------------------------------------------------------

@dataclass
class NormalizationPolicy:
    """标准化规则版本与依赖（必须与抽取时一致，否则 record_set_version 校验失败）。"""

    extractor_version: str = "1.0"
    mapping_rule_version: str = mapping.RULE_VERSION
    normalization_rule_version: str = NORMALIZATION_RULE_VERSION
    dependency_versions: dict[str, str] = field(default_factory=dict)
    default_restatement_version: str = DEFAULT_RESTATEMENT_VERSION


@dataclass
class NormalizationResult:
    record_set_version: str
    source_version: str
    records: list[S.SourceFinancialRecord]
    issues: list[S.ExtractionIssue]       # NORMALIZATION_REQUIRED
    normalized_count: int
    blocked_count: int
    issues_committed: int
    reused: bool


# ---------------------------------------------------------------------------
# 纯函数：单位换算 / 准入判定 / 记录构造
# ---------------------------------------------------------------------------

def unit_to_yuan(unit: str | None) -> Decimal | None:
    """单位 → 元 乘数；未知/缺失单位返回 None（拒绝标准化）。"""
    return UNIT_TO_YUAN.get(unit) if unit else None


def _admission_block_reason(candidate: S.ExtractedFinancialCell) -> str | None:
    """返回首个阻断标准化准入的原因（None 表示通过）。§4.2 准入维度逐一检查。"""
    if candidate.statement_type_candidate not in S.STATEMENT_TYPES:
        return "statement_type"
    if not candidate.period_candidate or not candidate.period_type_candidate:
        return "period"
    if candidate.scope_candidate not in S.STATEMENT_SCOPES:
        return "scope"
    if candidate.currency_candidate not in S.CURRENCIES:
        return "currency"
    if unit_to_yuan(candidate.unit_candidate) is None:
        return "unit"
    return None


def build_record(candidate: S.ExtractedFinancialCell, standard_item_code: str,
                 policy: NormalizationPolicy, *,
                 mapping_mode: str = "rule") -> S.SourceFinancialRecord:
    """由已通过准入的候选构造标准化记录（std_unit=yuan；Decimal 换算；id/hash 重算）。

    前提：candidate.parsed_numeric_value 非 None、unit_candidate 可换算、各维度已明确。
    mapping_mode 缺省为 "rule"；A5 人工科目映射确认派生时传 "human_confirmed"。
    """
    mult = unit_to_yuan(candidate.unit_candidate)
    assert mult is not None
    std_value_decimal = candidate.parsed_numeric_value * mult

    rec = S.SourceFinancialRecord(
        record_id="",
        record_set_version=candidate.record_set_version,
        company_id=candidate.company_id,
        standard_item_code=standard_item_code,
        statement_type=candidate.statement_type_candidate,
        raw_item_text=candidate.raw_item_text,
        raw_value=float(candidate.parsed_numeric_value),
        raw_unit=candidate.unit_candidate,
        raw_currency=candidate.currency_candidate,
        std_value=float(std_value_decimal),
        std_unit="yuan",
        std_currency=candidate.currency_candidate,
        conversion_rule_version=CONVERSION_RULE_VERSION,
        report_period=candidate.period_candidate,
        period_type=candidate.period_type_candidate,
        statement_scope=candidate.scope_candidate,
        currency=candidate.currency_candidate,
        restatement_version=policy.default_restatement_version,
        locator=candidate.locator,
        mapping_mode=mapping_mode,
        confidence=1.0,
        record_hash="",
        quality_flags=[],
        created_at=_utcnow(),
        candidate_id=candidate.candidate_id,
    )
    rec.record_id = S.derive_record_id(candidate.record_set_version, S.record_identity_fields(rec))
    rec.record_hash = validator._record_hash(rec)
    return rec


def _make_normalization_issue(record_set_version: str, candidate: S.ExtractedFinancialCell,
                              standard_item_code: str, reason: str, now: str) -> S.ExtractionIssue:
    detail = {
        "raw_item_text": candidate.raw_item_text,
        "reason": reason,
        "standard_item_code": standard_item_code,
        "statement_type_candidate": candidate.statement_type_candidate,
        "period_candidate": candidate.period_candidate,
        "period_type_candidate": candidate.period_type_candidate,
        "scope_candidate": candidate.scope_candidate,
        "currency_candidate": candidate.currency_candidate,
        "unit_candidate": candidate.unit_candidate,
    }
    canonical = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    issue_id = "iss-" + hashlib.sha256(
        f"{record_set_version}|NORMALIZATION_REQUIRED|{candidate.candidate_id}|"
        f"|{canonical}".encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=record_set_version,
        issue_type="NORMALIZATION_REQUIRED",
        candidate_id=candidate.candidate_id,
        comparison_key=None,
        detail=detail,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 编排：读候选 → 映射 → 准入 → 记录 + NORMALIZATION_REQUIRED
# ---------------------------------------------------------------------------

def normalize_record_set(record_set_version: str, policy: NormalizationPolicy | None = None,
                         persist: bool = True) -> NormalizationResult:
    """对某记录集合做确定性标准化（映射 + 准入 + Decimal 换算 + 原子落盘）。"""
    policy = policy or NormalizationPolicy()

    candidates = store.list_candidates(record_set_version)
    if not candidates:
        raise KeyError(f"record_set 下无候选: {record_set_version}")

    # 一致性：候选必须同属一个 source_version + record_set_version。
    source_versions = {c.source_version for c in candidates}
    rs_versions = {c.record_set_version for c in candidates}
    if len(source_versions) != 1:
        raise ValueError(f"候选跨 source_version: {sorted(source_versions)}")
    if len(rs_versions) != 1:
        raise ValueError(f"候选跨 record_set_version: {sorted(rs_versions)}")
    source_version = next(iter(source_versions))

    # 校验 policy 版本与候选 record_set_version 一致（防抽取/标准化规则错配）。
    expected_rs = S.derive_record_set_version(
        source_version, policy.extractor_version, policy.mapping_rule_version,
        policy.normalization_rule_version, policy.dependency_versions)
    if expected_rs != record_set_version:
        raise ValueError(
            f"policy 版本与 record_set_version 不一致: 派生 {expected_rs!r} "
            f"!= 候选 {record_set_version!r}（extractor/mapping/normalization/依赖版本须与抽取一致）")

    rules = mapping.build_builtin_rules(policy.mapping_rule_version)
    records: list[S.SourceFinancialRecord] = []
    issues: list[S.ExtractionIssue] = []
    now = _utcnow()

    for c in candidates:
        outcome = mapping.map_candidate(c, rules)
        if outcome.status != "mapped":
            continue  # 未映射由 mapping 阶段处理（MAPPING_REQUIRED）
        if c.status != "EXTRACTED" or c.parsed_numeric_value is None:
            continue  # 非数值/不适用/解析失败由抽取阶段处理，不进入可计算记录

        reason = _admission_block_reason(c)
        if reason is not None:
            issues.append(_make_normalization_issue(
                record_set_version, c, outcome.standard_item_code, reason, now))
            continue
        records.append(build_record(c, outcome.standard_item_code, policy))

    # 记录集合元信息（币种/单位/scope 仅当全部一致时才写集合级汇总，否则 None）。
    periods = sorted({r.report_period for r in records})
    currencies = {r.currency for r in records}
    units = {r.raw_unit for r in records}
    scopes = {r.statement_scope for r in records}

    record_set = S.FinancialRecordSet(
        record_set_version=record_set_version,
        source_version=source_version,
        extractor_name=("excel_extractor" if candidates[0].locator
                        and candidates[0].locator.kind == "excel" else "pdf_table_extractor"),
        extractor_version=policy.extractor_version,
        mapping_rule_version=policy.mapping_rule_version,
        normalization_rule_version=policy.normalization_rule_version,
        dependency_versions=policy.dependency_versions,
        report_periods=periods,
        currency=(next(iter(currencies)) if len(currencies) == 1 else None),
        unit=(next(iter(units)) if len(units) == 1 else None),
        statement_scope=(next(iter(scopes)) if len(scopes) == 1 else None),
        audit_status=None,
        block_count=len(issues),
        record_count=len(records),
        created_at=now,
    )

    issues_committed = 0
    reused = False
    if persist:
        if issues:
            issues_committed = store.commit_issues(issues)
        if records:
            source_document_id = store.get_source_version(source_version).source_document_id
            commit_result = store.commit_record_set(record_set, records, source_document_id)
            reused = commit_result.reused

    return NormalizationResult(
        record_set_version=record_set_version,
        source_version=source_version,
        records=records,
        issues=issues,
        normalized_count=len(records),
        blocked_count=len(issues),
        issues_committed=issues_committed,
        reused=reused,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m financial_v2.normalization",
                                     description="A4 标准化（映射候选 → 标准记录，Decimal/yuan）")
    parser.add_argument("--record-set", required=True, help="record_set_version")
    parser.add_argument("--extractor-version", default="1.0")
    parser.add_argument("--mapping-rule-version", default=mapping.RULE_VERSION)
    parser.add_argument("--normalization-rule-version", default=NORMALIZATION_RULE_VERSION)
    parser.add_argument("--dependency-versions", default="{}",
                        help="JSON dict，须与抽取时一致（默认 {}）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只标准化不落盘")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省 data/financial_v2.db）")
    args = parser.parse_args(argv)

    try:
        deps = json.loads(args.dependency_versions)
        if not isinstance(deps, dict):
            raise ValueError("--dependency-versions 必须为 JSON dict")
    except ValueError as e:
        parser.error(str(e))

    policy = NormalizationPolicy(
        extractor_version=args.extractor_version,
        mapping_rule_version=args.mapping_rule_version,
        normalization_rule_version=args.normalization_rule_version,
        dependency_versions=deps,
    )
    store.init_db(args.db or store.DEFAULT_DB_PATH)
    result = normalize_record_set(args.record_set, policy, persist=not args.validate_only)

    summary = {
        "record_set_version": result.record_set_version,
        "source_version": result.source_version,
        "normalized_count": result.normalized_count,
        "blocked_count": result.blocked_count,
        "issues_committed": result.issues_committed,
        "reused": result.reused,
        "records": [
            {"item": r.raw_item_text, "standard_item_code": r.standard_item_code,
             "report_period": r.report_period, "std_value_yuan": r.std_value,
             "scope": r.statement_scope, "currency": r.currency}
            for r in result.records[:10]
        ],
        "block_reasons": sorted({i.detail.get("reason") for i in result.issues}),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
