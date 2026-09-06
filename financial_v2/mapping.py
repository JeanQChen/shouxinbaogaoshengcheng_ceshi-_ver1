"""A4 确定性科目映射（§5.7 / §7.1 前半）：原始候选 → 标准科目代码。

规则（任务书硬要求）：
- 只在「报表类型内」做精确别名匹配，绝不跨表、绝不做模糊包含（「现金」≠「货币资金」）；
- 匹配前对候选原文做 Unicode / 空白 / 标点 / 编号规范化，别名同样规范化后比较；
- 唯一且无排除词冲突的规则命中 → 确定性 mapped；
- 零命中 / 多规则命中不同 standard_item_code / 缺报表类型 → MAPPING_REQUIRED（派生问题，
  由 A5 集中确认），绝不猜测、绝不伪造成标准记录。

本模块无 RAG / 无 LLM / 无 OCR，纯确定性。规则集为版本化追加式（mapping_rule 表，
新版本追加新行，不覆盖旧行）；MAPPING_REQUIRED 问题写入 extraction_issue（不可变）。

CLI: python -m financial_v2.mapping --record-set <rs_id> [--validate-only] [--db <path>]
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from financial import schema as V1
from financial_v2 import schema as S
from financial_v2 import store

logger = logging.getLogger(__name__)

# 规则版本：与 A2/A3 抽取时写入 record_set.mapping_rule_version 的默认值一致。
RULE_VERSION = "1.0"
EFFECTIVE_AT = "2026-01-01"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 文本规范化（Unicode / 全角 / 编号 / 空白标点）
# ---------------------------------------------------------------------------

def _fullwidth_to_halfwidth(s: str) -> str:
    out: list[str] = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII 标点/字母/数字
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


# 编号前缀：数字编号（1、 (1) 1. 1） 等）与中文数字编号（一、 （一））。
# 中文数字仅当被括号包裹或后跟分隔符时才视为编号，避免误伤「一年内到期的非流动资产」。
_LEADING_DIGIT_NUM = re.compile(
    r"^[\s]*[（(【\[｛{]?\s*\d+\s*[）)】\]｝}]?\s*[、.．:：]+\s*")
_LEADING_DIGIT_NUM_BRACKET = re.compile(
    r"^[\s]*[（(【\[｛{]\s*\d+\s*[）)】\]｝}]\s*")
_LEADING_CN_NUM_BRACKET = re.compile(
    r"^[\s]*[（(【\[｛{]\s*[一二三四五六七八九十百]+\s*[）)】\]｝}]\s*")
_LEADING_CN_NUM_SEP = re.compile(
    r"^[\s]*[一二三四五六七八九十百]+\s*[、．.:：]\s*")


def _strip_leading_numbering(s: str) -> str:
    s = _LEADING_DIGIT_NUM.sub("", s)
    s = _LEADING_DIGIT_NUM_BRACKET.sub("", s)
    s = _LEADING_CN_NUM_BRACKET.sub("", s)
    s = _LEADING_CN_NUM_SEP.sub("", s)
    return s


def normalize_item_text(raw: str) -> str:
    """规范化科目原文：Unicode NFC → 全角转半角 → 去编号前缀 → 去空白/标点 → 小写。"""
    if not raw:
        return ""
    s = unicodedata.normalize("NFC", raw)
    s = _fullwidth_to_halfwidth(s)
    s = _strip_leading_numbering(s)
    s = s.lower()
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return s


# ---------------------------------------------------------------------------
# 内置规则集（标准科目代码 ← V1 中文名；别名规范化后精确比较）
# ---------------------------------------------------------------------------

# (standard_item_code, aliases)。aliases 全部来自 V1 financial.schema，避免中文名
# 二次硬编码漂移；code 串与 V1 常量名一致。
_BALANCE_RULES: list[tuple[str, list[str]]] = [
    ("TOTAL_ASSETS", [V1.TOTAL_ASSETS]),
    ("CURRENT_ASSETS", [V1.CURRENT_ASSETS]),
    ("NON_CURRENT_ASSETS", [V1.NON_CURRENT_ASSETS]),
    ("TOTAL_LIABILITIES", [V1.TOTAL_LIABILITIES]),
    ("CURRENT_LIABILITIES", [V1.CURRENT_LIABILITIES]),
    ("NON_CURRENT_LIABILITIES", [V1.NON_CURRENT_LIABILITIES]),
    ("TOTAL_EQUITY", [V1.TOTAL_EQUITY]),
    ("TOTAL_LIABILITIES_AND_EQUITY", [V1.TOTAL_LIABILITIES_AND_EQUITY]),
    ("CASH_AND_EQUIVALENTS", [V1.CASH_AND_EQUIVALENTS]),
    ("ACCOUNTS_RECEIVABLE", [V1.ACCOUNTS_RECEIVABLE]),
    ("NOTES_RECEIVABLE", [V1.NOTES_RECEIVABLE]),
    ("ACCOUNTS_RECEIVABLE_COMBINED", [V1.ACCOUNTS_RECEIVABLE_COMBINED]),
    ("INVENTORY", [V1.INVENTORY]),
    ("ACCOUNTS_PAYABLE", [V1.ACCOUNTS_PAYABLE]),
    ("SHORT_TERM_BORROWINGS", [V1.SHORT_TERM_BORROWINGS]),
    ("LONG_TERM_BORROWINGS", [V1.LONG_TERM_BORROWINGS]),
    ("FIXED_ASSETS", [V1.FIXED_ASSETS]),
    ("CONSTRUCTION_IN_PROGRESS", [V1.CONSTRUCTION_IN_PROGRESS]),
    ("INTANGIBLE_ASSETS", [V1.INTANGIBLE_ASSETS]),
    ("GOODWILL", [V1.GOODWILL]),
    ("LONG_TERM_DEFERRED_EXPENSES", [V1.LONG_TERM_DEFERRED_EXPENSES]),
    ("LONG_TERM_EQUITY_INVEST", [V1.LONG_TERM_EQUITY_INVEST]),
    ("RIGHT_OF_USE_ASSET", [V1.RIGHT_OF_USE_ASSET]),
    ("DEFERRED_TAX_ASSETS", [V1.DEFERRED_TAX_ASSETS]),
    ("DEFERRED_TAX_LIABILITIES", [V1.DEFERRED_TAX_LIABILITIES]),
    ("DEFERRED_INCOME", [V1.DEFERRED_INCOME]),
    ("OTHER_RECEIVABLES", [V1.OTHER_RECEIVABLES]),
    ("OTHER_PAYABLES", [V1.OTHER_PAYABLES]),
    ("INTEREST_RECEIVABLE", [V1.INTEREST_RECEIVABLE]),
    ("INTEREST_PAYABLE", [V1.INTEREST_PAYABLE]),
    ("DIVIDEND_RECEIVABLE", [V1.DIVIDEND_RECEIVABLE]),
    ("DIVIDEND_PAYABLE", [V1.DIVIDEND_PAYABLE]),
    ("NON_CURRENT_ASSET_DUE_WITHIN_ONE_YEAR", [V1.NON_CURRENT_ASSET_DUE_WITHIN_ONE_YEAR]),
    ("NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR", [V1.NON_CURRENT_LIAB_DUE_WITHIN_ONE_YEAR]),
    ("TRADING_FINANCIAL_ASSETS", [V1.TRADING_FINANCIAL_ASSETS]),
    ("TRADING_FINANCIAL_LIABILITIES", [V1.TRADING_FINANCIAL_LIABILITIES]),
    ("DERIVATIVE_FINANCIAL_ASSETS", [V1.DERIVATIVE_FINANCIAL_ASSETS]),
    ("DERIVATIVE_FINANCIAL_LIABILITIES", [V1.DERIVATIVE_FINANCIAL_LIABILITIES]),
    ("OTHER_CURRENT_ASSETS", [V1.OTHER_CURRENT_ASSETS]),
    ("OTHER_NON_CURRENT_ASSETS", [V1.OTHER_NON_CURRENT_ASSETS]),
    ("OTHER_CURRENT_LIABILITIES", [V1.OTHER_CURRENT_LIABILITIES]),
    ("OTHER_NON_CURRENT_LIABILITIES", [V1.OTHER_NON_CURRENT_LIABILITIES]),
    ("OTHER_EQUITY_INSTRUMENT_INVEST", [V1.OTHER_EQUITY_INSTRUMENT_INVEST]),
    ("OTHER_NON_CURRENT_FINANCIAL_ASSETS", [V1.OTHER_NON_CURRENT_FINANCIAL_ASSETS]),
    ("OTHER_COMPREHENSIVE_INCOME", [V1.OTHER_COMPREHENSIVE_INCOME]),
    ("AVAILABLE_FOR_SALE_FINANCIAL_ASSETS", [V1.AVAILABLE_FOR_SALE_FINANCIAL_ASSETS]),
    ("ASSETS_HELD_FOR_SALE", [V1.ASSETS_HELD_FOR_SALE]),
    ("CONTRACT_ASSETS", [V1.CONTRACT_ASSETS]),
    ("CONTRACT_LIABILITIES", [V1.CONTRACT_LIABILITIES]),
    ("LEASE_LIABILITIES", [V1.LEASE_LIABILITIES]),
    ("PREPAYMENTS", [V1.PREPAYMENTS]),
    ("ADVANCES_FROM_CUSTOMERS", [V1.ADVANCES_FROM_CUSTOMERS]),
    ("PROVISIONS", [V1.PROVISIONS]),
    ("BONDS_PAYABLE", [V1.BONDS_PAYABLE]),
    ("EMPLOYEE_BENEFITS_PAYABLE", [V1.EMPLOYEE_BENEFITS_PAYABLE]),
    ("TAXES_PAYABLE", [V1.TAXES_PAYABLE]),
    ("LONG_TERM_RECEIVABLES", [V1.LONG_TERM_RECEIVABLES]),
    ("LONG_TERM_PAYABLES", [V1.LONG_TERM_PAYABLES]),
    ("PAID_IN_CAPITAL", [V1.PAID_IN_CAPITAL]),
    ("CAPITAL_RESERVE", [V1.CAPITAL_RESERVE]),
    ("SURPLUS_RESERVE", [V1.SURPLUS_RESERVE]),
    ("UNDISTRIBUTED_PROFIT", [V1.UNDISTRIBUTED_PROFIT]),
    ("TREASURY_STOCK", [V1.TREASURY_STOCK]),
    ("SPECIAL_RESERVE", [V1.SPECIAL_RESERVE]),
    ("MINORITY_INTEREST", [V1.MINORITY_INTEREST]),
]

_INCOME_RULES: list[tuple[str, list[str]]] = [
    ("TOTAL_REVENUE", [V1.TOTAL_REVENUE]),
    ("OPERATING_REVENUE", [V1.OPERATING_REVENUE]),
    ("OPERATING_COST", [V1.OPERATING_COST]),
    ("TOTAL_OPERATING_COST", [V1.TOTAL_OPERATING_COST]),
    ("TAXES_AND_SURCHARGES", [V1.TAXES_AND_SURCHARGES]),
    ("SALES_EXPENSES", [V1.SALES_EXPENSES]),
    ("ADMIN_EXPENSES", [V1.ADMIN_EXPENSES]),
    ("R_AND_D_EXPENSES", [V1.R_AND_D_EXPENSES]),
    ("FINANCE_EXPENSES", [V1.FINANCE_EXPENSES]),
    ("INVESTMENT_INCOME", [V1.INVESTMENT_INCOME]),
    ("FAIR_VALUE_CHANGE_GAINS", [V1.FAIR_VALUE_CHANGE_GAINS]),
    ("CREDIT_IMPAIRMENT_LOSS", [V1.CREDIT_IMPAIRMENT_LOSS]),
    ("ASSET_IMPAIRMENT_LOSS", [V1.ASSET_IMPAIRMENT_LOSS]),
    ("ASSET_DISPOSAL_INCOME", [V1.ASSET_DISPOSAL_INCOME]),
    ("OTHER_INCOME", [V1.OTHER_INCOME]),
    ("OPERATING_PROFIT", [V1.OPERATING_PROFIT]),
    ("NON_OPERATING_INCOME", [V1.NON_OPERATING_INCOME]),
    ("NON_OPERATING_EXPENSES", [V1.NON_OPERATING_EXPENSES]),
    ("TOTAL_PROFIT", [V1.TOTAL_PROFIT]),
    ("INCOME_TAX", [V1.INCOME_TAX]),
    ("NET_PROFIT", [V1.NET_PROFIT]),
    ("NET_PROFIT_PARENT", [V1.NET_PROFIT_PARENT]),
    ("MINORITY_INTEREST_PROFIT", [V1.MINORITY_INTEREST_PROFIT]),
    ("TOTAL_COMPREHENSIVE_INCOME", [V1.TOTAL_COMPREHENSIVE_INCOME]),
    ("COMPREHENSIVE_INCOME_PARENT", [V1.COMPREHENSIVE_INCOME_PARENT]),
    ("COMPREHENSIVE_INCOME_MINORITY", [V1.COMPREHENSIVE_INCOME_MINORITY]),
    ("CONTINUING_OP_NET_PROFIT", [V1.CONTINUING_OP_NET_PROFIT]),
    ("EPS", [V1.EPS]),
    ("EPS_BASIC", [V1.EPS_BASIC]),
    ("EPS_DILUTED", [V1.EPS_DILUTED]),
    ("INTEREST_INCOME", [V1.INTEREST_INCOME]),
    ("INTEREST_EXPENSE", [V1.INTEREST_EXPENSE]),
    ("INVESTMENT_INCOME_FROM_ASSOCIATES", [V1.INVESTMENT_INCOME_FROM_ASSOCIATES]),
    ("FINANCIAL_ASSET_TERMINATION_INCOME", [V1.FINANCIAL_ASSET_TERMINATION_INCOME]),
]

_CASHFLOW_RULES: list[tuple[str, list[str]]] = [
    ("OPERATING_CASH_FLOW", [V1.OPERATING_CASH_FLOW, V1.OPERATING_CASH_FLOW_SHORT]),
    ("INVESTING_CASH_FLOW", [V1.INVESTING_CASH_FLOW, V1.INVESTING_CASH_FLOW_SHORT]),
    ("FINANCING_CASH_FLOW", [V1.FINANCING_CASH_FLOW, V1.FINANCING_CASH_FLOW_SHORT]),
    ("NET_CASH_INCREASE", [V1.NET_CASH_INCREASE]),
    ("CASH_RECEIVED_FROM_SALES", [V1.CASH_RECEIVED_FROM_SALES]),
    ("TAX_REFUNDS_RECEIVED", [V1.TAX_REFUNDS_RECEIVED]),
    ("OTHER_OPERATING_CASH_RECEIVED", [V1.OTHER_OPERATING_CASH_RECEIVED]),
    ("SUBTOTAL_OPERATING_CASH_INFLOW", [V1.SUBTOTAL_OPERATING_CASH_INFLOW]),
    ("CASH_PAID_FOR_GOODS", [V1.CASH_PAID_FOR_GOODS]),
    ("CASH_PAID_TO_EMPLOYEES", [V1.CASH_PAID_TO_EMPLOYEES]),
    ("TAXES_PAID", [V1.TAXES_PAID]),
    ("OTHER_OPERATING_CASH_PAID", [V1.OTHER_OPERATING_CASH_PAID]),
    ("SUBTOTAL_OPERATING_CASH_OUTFLOW", [V1.SUBTOTAL_OPERATING_CASH_OUTFLOW]),
    ("CASH_RECEIVED_FROM_INVESTMENT_RETURN", [V1.CASH_RECEIVED_FROM_INVESTMENT_RETURN]),
    ("CASH_RECEIVED_FROM_INVESTMENT_INCOME", [V1.CASH_RECEIVED_FROM_INVESTMENT_INCOME]),
    ("CASH_RECEIVED_FROM_DISPOSAL", [V1.CASH_RECEIVED_FROM_DISPOSAL]),
    ("OTHER_INVESTING_CASH_RECEIVED", [V1.OTHER_INVESTING_CASH_RECEIVED]),
    ("SUBTOTAL_INVESTING_CASH_INFLOW", [V1.SUBTOTAL_INVESTING_CASH_INFLOW]),
    ("CASH_PAID_FOR_INVESTMENT", [V1.CASH_PAID_FOR_INVESTMENT]),
    ("CASH_PAID_FOR_ACQUISITION", [V1.CASH_PAID_FOR_ACQUISITION]),
    ("OTHER_INVESTING_CASH_PAID", [V1.OTHER_INVESTING_CASH_PAID]),
    ("SUBTOTAL_INVESTING_CASH_OUTFLOW", [V1.SUBTOTAL_INVESTING_CASH_OUTFLOW]),
    ("CASH_RECEIVED_FROM_CAPITAL", [V1.CASH_RECEIVED_FROM_CAPITAL]),
    ("CASH_RECEIVED_MINORITY_INVESTMENT", [V1.CASH_RECEIVED_MINORITY_INVESTMENT]),
    ("CASH_RECEIVED_FROM_BORROWINGS", [V1.CASH_RECEIVED_FROM_BORROWINGS]),
    ("CASH_RECEIVED_FROM_BOND_ISSUANCE", [V1.CASH_RECEIVED_FROM_BOND_ISSUANCE]),
    ("OTHER_FINANCING_CASH_RECEIVED", [V1.OTHER_FINANCING_CASH_RECEIVED]),
    ("SUBTOTAL_FINANCING_CASH_INFLOW", [V1.SUBTOTAL_FINANCING_CASH_INFLOW]),
    ("CASH_PAID_FOR_DEBT", [V1.CASH_PAID_FOR_DEBT]),
    ("CASH_PAID_FOR_DIVIDEND_INTEREST", [V1.CASH_PAID_FOR_DIVIDEND_INTEREST]),
    ("CASH_PAID_MINORITY_DIVIDEND", [V1.CASH_PAID_MINORITY_DIVIDEND]),
    ("OTHER_FINANCING_CASH_PAID", [V1.OTHER_FINANCING_CASH_PAID]),
    ("SUBTOTAL_FINANCING_CASH_OUTFLOW", [V1.SUBTOTAL_FINANCING_CASH_OUTFLOW]),
    ("EXCHANGE_RATE_EFFECT", [V1.EXCHANGE_RATE_EFFECT]),
    ("BEGINNING_CASH_BALANCE", [V1.BEGINNING_CASH_BALANCE]),
    ("ENDING_CASH_BALANCE", [V1.ENDING_CASH_BALANCE]),
    ("CASH_AND_EQUIVALENTS_CF", [V1.CASH_AND_EQUIVALENTS_CF]),
]

_STATEMENT_RULES: dict[str, list[tuple[str, list[str]]]] = {
    "balance_sheet": _BALANCE_RULES,
    "income_statement": _INCOME_RULES,
    "cash_flow": _CASHFLOW_RULES,
}


def build_builtin_rules(rule_version: str = RULE_VERSION) -> list[S.MappingRule]:
    """构造确定性内置规则集（纯函数，无 I/O）。"""
    rules: list[S.MappingRule] = []
    for statement_type, specs in _STATEMENT_RULES.items():
        for code, aliases in specs:
            rules.append(S.MappingRule(
                rule_id=code,
                rule_version=rule_version,
                statement_type=statement_type,
                standard_item_code=code,
                aliases=list(aliases),
                exclude_words=[],
                priority=100,
                effective_at=EFFECTIVE_AT,
            ))
    return rules


def ensure_rules_persisted(rule_version: str = RULE_VERSION) -> int:
    """幂等落盘内置规则集（追加式，同版本同 id 已存在则忽略）。返回规则条数。"""
    rules = build_builtin_rules(rule_version)
    for r in rules:
        store.insert_mapping_rule(r)
    return len(rules)


# ---------------------------------------------------------------------------
# 映射结果
# ---------------------------------------------------------------------------

@dataclass
class MappingOutcome:
    candidate_id: str
    raw_item_text: str
    normalized_item_text: str
    statement_type_candidate: str | None
    status: str                     # "mapped" | "mapping_required"
    standard_item_code: str | None
    matched_rule_id: str | None
    matched_alias: str | None
    reason: str                     # "rule_match" | "no_rule_match" | "ambiguous_mapping" | "no_statement_type" | "empty_item_text" | "no_rules_for_version"
    conflicting_rules: list[dict] = field(default_factory=list)  # 歧义时列出冲突规则


@dataclass
class MappingResult:
    record_set_version: str
    rule_version: str
    outcomes: list[MappingOutcome]
    mapped_count: int
    unmapped_count: int
    issues_committed: int


# ---------------------------------------------------------------------------
# 纯映射判定
# ---------------------------------------------------------------------------

def _find_rule_matches(applicable_rules: list[S.MappingRule], norm: str) -> list[tuple[S.MappingRule, str]]:
    """报表类型内精确别名匹配；排除词（若命中则整条规则失效）→ 唯一规则 + 别名。"""
    matches: list[tuple[S.MappingRule, str]] = []
    for rule in applicable_rules:
        # 排除词冲突：规范化后的排除词是原文子串 → 本规则不参与匹配。
        if any((nw := normalize_item_text(w)) and nw in norm for w in rule.exclude_words):
            continue
        for alias in rule.aliases:
            if normalize_item_text(alias) == norm:
                matches.append((rule, alias))
                break
    return matches


def map_candidate(candidate: S.ExtractedFinancialCell,
                  rules: list[S.MappingRule]) -> MappingOutcome:
    """对单个候选做确定性映射（纯函数，不读写 DB）。

    rules 应为目标规则版本全集（内部按 statement_type_candidate 过滤）。
    """
    raw = candidate.raw_item_text
    st = candidate.statement_type_candidate
    norm = normalize_item_text(raw)

    if not raw or not raw.strip() or not norm:
        return MappingOutcome(candidate.candidate_id, raw, norm, st,
                              "mapping_required", None, None, None, "empty_item_text")
    if st not in S.STATEMENT_TYPES:
        return MappingOutcome(candidate.candidate_id, raw, norm, st,
                              "mapping_required", None, None, None, "no_statement_type")

    applicable = [r for r in rules if r.statement_type == st]
    if not applicable:
        return MappingOutcome(candidate.candidate_id, raw, norm, st,
                              "mapping_required", None, None, None, "no_rules_for_version")

    matches = _find_rule_matches(applicable, norm)
    if not matches:
        return MappingOutcome(candidate.candidate_id, raw, norm, st,
                              "mapping_required", None, None, None, "no_rule_match")

    codes = {r.standard_item_code for r, _ in matches}
    if len(codes) == 1:
        r, alias = matches[0]
        return MappingOutcome(candidate.candidate_id, raw, norm, st,
                              "mapped", r.standard_item_code, r.rule_id, alias, "rule_match")

    conflicting = [
        {"rule_id": r.rule_id, "standard_item_code": r.standard_item_code, "alias": a}
        for r, a in matches
    ]
    return MappingOutcome(candidate.candidate_id, raw, norm, st,
                          "mapping_required", None, None, None, "ambiguous_mapping",
                          conflicting_rules=conflicting)


# ---------------------------------------------------------------------------
# 派生问题（与 A2/A3 一致的稳定 issue_id 约定）
# ---------------------------------------------------------------------------

def _make_mapping_issue(record_set_version: str, outcome: MappingOutcome,
                        now: str) -> S.ExtractionIssue:
    detail = {
        "raw_item_text": outcome.raw_item_text,
        "normalized_item_text": outcome.normalized_item_text,
        "statement_type_candidate": outcome.statement_type_candidate,
        "reason": outcome.reason,
    }
    if outcome.conflicting_rules:
        detail["conflicting_rules"] = outcome.conflicting_rules
    canonical = json.dumps(detail, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    issue_id = "iss-" + hashlib.sha256(
        f"{record_set_version}|MAPPING_REQUIRED|{outcome.candidate_id}|"
        f"|{canonical}".encode("utf-8")).hexdigest()[:24]
    return S.ExtractionIssue(
        issue_id=issue_id,
        record_set_version=record_set_version,
        issue_type="MAPPING_REQUIRED",
        candidate_id=outcome.candidate_id,
        comparison_key=None,
        detail=detail,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 编排：读候选 + 规则 → 映射 + 落盘 MAPPING_REQUIRED
# ---------------------------------------------------------------------------

def map_record_set(record_set_version: str, rule_version: str | None = None,
                   persist: bool = True) -> MappingResult:
    """对某记录集合的全部候选做确定性科目映射。

    rule_version 缺省优先取该 record_set 已登记（normalization 后）的
    mapping_rule_version；抽取阶段尚无 record_set 行时回退 RULE_VERSION（与 A2/A3
    抽取默认一致）。persist=True 时先幂等落盘内置规则，再把 MAPPING_REQUIRED 问题
    原子追加到 extraction_issue。
    """
    candidates = store.list_candidates(record_set_version)
    if not candidates:
        raise KeyError(f"record_set 下无候选: {record_set_version}")

    if rule_version is None:
        rs = store.get_record_set(record_set_version)
        rule_version = (rs.mapping_rule_version if rs and rs.mapping_rule_version
                        else RULE_VERSION)

    # 计算用规则恒来自内置集（确定性，不依赖库内状态）；persist 时落盘为审计镜像。
    rules = build_builtin_rules(rule_version)
    if persist:
        ensure_rules_persisted(rule_version)

    outcomes = [map_candidate(c, rules) for c in candidates]
    mapped = [o for o in outcomes if o.status == "mapped"]
    unmapped = [o for o in outcomes if o.status == "mapping_required"]

    issues_committed = 0
    if persist and unmapped:
        now = _utcnow()
        issues = [_make_mapping_issue(record_set_version, o, now) for o in unmapped]
        issues_committed = store.commit_issues(issues)

    return MappingResult(
        record_set_version=record_set_version,
        rule_version=rule_version,
        outcomes=outcomes,
        mapped_count=len(mapped),
        unmapped_count=len(unmapped),
        issues_committed=issues_committed,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m financial_v2.mapping",
                                     description="A4 确定性科目映射（候选 → 标准代码）")
    parser.add_argument("--record-set", required=True, help="record_set_version")
    parser.add_argument("--rule-version", default=None, help="映射规则版本（缺省取 record_set）")
    parser.add_argument("--validate-only", action="store_true",
                        help="只映射不落盘（不写规则 / 不写问题）")
    parser.add_argument("--db", default=None, help="financial_v2 SQLite 路径（缺省 data/financial_v2.db）")
    args = parser.parse_args(argv)

    store.init_db(args.db or store.DEFAULT_DB_PATH)
    result = map_record_set(
        args.record_set, rule_version=args.rule_version, persist=not args.validate_only)

    summary = {
        "record_set_version": result.record_set_version,
        "rule_version": result.rule_version,
        "mapped_count": result.mapped_count,
        "unmapped_count": result.unmapped_count,
        "issues_committed": result.issues_committed,
        "mapped": [
            {"item": o.raw_item_text, "standard_item_code": o.standard_item_code,
             "statement_type": o.statement_type_candidate}
            for o in result.outcomes if o.status == "mapped"
        ],
        "unmapped": [
            {"item": o.raw_item_text, "reason": o.reason,
             "statement_type": o.statement_type_candidate}
            for o in result.outcomes if o.status == "mapping_required"
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
