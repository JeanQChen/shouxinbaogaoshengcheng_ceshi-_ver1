"""Agent 6: 回检 — 三类规则比对。

规则：
  yellow (数值): 指标差异 > 5%，需复核
  red   (实体): 公司名/代码/人名等实体与公开数据源不一致
  orange(时效): 引用的数据或报告已过时
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial.db import init_db, list_periods, query_metric
from financial.schema import (
    TOTAL_ASSETS, CURRENT_ASSETS, TOTAL_LIABILITIES, CURRENT_LIABILITIES,
    TOTAL_EQUITY, INVENTORY, TOTAL_REVENUE, OPERATING_COST, NET_PROFIT,
    TOTAL_PROFIT, FINANCE_EXPENSES, OPERATING_CASH_FLOW, OPERATING_PROFIT,
    NET_PROFIT_PARENT, ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED,
)

logger = logging.getLogger(__name__)


# ── Dataclasses ──

@dataclass
class Issue:
    severity: str        # "yellow" | "red" | "orange"
    rule: str
    location: str        # 在原文中的位置
    detail: str
    evidence: list[str]  # 公开数据源的对比依据


@dataclass
class VerificationResult:
    annotated_markdown: str
    issues: list[Issue] = field(default_factory=list)


@dataclass
class _NumericalClaim:
    """从报告中提取的数值声明。"""
    metric_name: str       # "流动比率"
    reported_value: float
    unit: str              # "ratio" | "percent" | "yi_yuan" | "wan_yuan" | "yuan"
    context: str           # 原文片段
    line_index: int        # 在 markdown 中的行号


# ── 工具 ──

def _safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


# ── 指标声明提取 ──

# Pattern groups:
#   1. "XXX率" / "XXX比率" / "XXX倍数" / "XXX乘数" / ROE/ROA/ROIC/EPS
#   2. optional separator: 为|：|:|=|降至|提升至|下降至|上升至|降至|跌至|涨至|约|达到|\s+
#   3. the number: digits with optional decimal and %
#   4. optional unit suffix: 亿元|万元|元|%|倍

_METRIC_PATTERN = re.compile(
    r"([A-Za-z]{2,6}|[一-鿿]{2,8}(?:比率|倍数|乘数|率|占比|比例|覆盖率|集中度)?)"
    r"(?:为|：|:|=|降至|提升至|下降至|上升至|跌至|涨至|约|达到|为|约|是)\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(%|亿元|万元|元|倍)?"
)

_NUMBER_IN_TABLE = re.compile(r"\|[^|]*?([\d,]+(?:\.\d+)?)\s*(%|亿元|万元|元|倍)?[^|]*?\|")

_UNIT_TO_SCALE = {
    "亿元": 100_000_000,
    "万元": 10_000,
    "元": 1,
    "倍": 1,
    "%": None,   # special: divide by 100
    "": 1,
}

# Chinese metric name → (schema_codes, compute_fn_or_None)
# compute_fn(company_id, latest_period) → actual_value | None
_METRIC_VERIFY_MAP: dict[str, tuple[list[str], object]] = {
    # ── 直接查询项 ──
    "资产总计": ([TOTAL_ASSETS], None),
    "流动资产合计": ([CURRENT_ASSETS], None),
    "流动资产": ([CURRENT_ASSETS], None),
    "负债合计": ([TOTAL_LIABILITIES], None),
    "流动负债合计": ([CURRENT_LIABILITIES], None),
    "流动负债": ([CURRENT_LIABILITIES], None),
    "所有者权益合计": ([TOTAL_EQUITY], None),
    "所有者权益": ([TOTAL_EQUITY], None),
    "存货": ([INVENTORY], None),
    "营业总收入": ([TOTAL_REVENUE], None),
    "营业收入": ([TOTAL_REVENUE], None),
    "营业成本": ([OPERATING_COST], None),
    "净利润": ([NET_PROFIT], None),
    "归母净利润": ([NET_PROFIT_PARENT], None),
    "利润总额": ([TOTAL_PROFIT], None),
    "营业利润": ([OPERATING_PROFIT], None),
    "财务费用": ([FINANCE_EXPENSES], None),
    "经营活动现金流": ([OPERATING_CASH_FLOW], None),
    "经营活动现金流量净额": ([OPERATING_CASH_FLOW], None),
    "应收账款": ([ACCOUNTS_RECEIVABLE, ACCOUNTS_RECEIVABLE_COMBINED], None),
    # ── 复合指标（需要计算）──
    "资产负债率": ([TOTAL_LIABILITIES, TOTAL_ASSETS], "compute_debt_ratio"),
    "流动比率": ([CURRENT_ASSETS, CURRENT_LIABILITIES], "compute_current_ratio"),
    "速动比率": ([CURRENT_ASSETS, CURRENT_LIABILITIES, INVENTORY], "compute_quick_ratio"),
}


def _compute_debt_ratio(cid: str, period: str) -> float | None:
    tl = query_metric(cid, TOTAL_LIABILITIES, period)
    ta = query_metric(cid, TOTAL_ASSETS, period)
    return _safe_div(tl, ta)


def _compute_current_ratio(cid: str, period: str) -> float | None:
    ca = query_metric(cid, CURRENT_ASSETS, period)
    cl = query_metric(cid, CURRENT_LIABILITIES, period)
    return _safe_div(ca, cl)


def _compute_quick_ratio(cid: str, period: str) -> float | None:
    ca = query_metric(cid, CURRENT_ASSETS, period)
    inv = query_metric(cid, INVENTORY, period)
    cl = query_metric(cid, CURRENT_LIABILITIES, period)
    qn = (ca - inv) if ca is not None and inv is not None else None
    return _safe_div(qn, cl)


_COMPUTE_FNS = {
    "compute_debt_ratio": _compute_debt_ratio,
    "compute_current_ratio": _compute_current_ratio,
    "compute_quick_ratio": _compute_quick_ratio,
}


def _extract_numerical_claims(markdown: str) -> list[_NumericalClaim]:
    """从 Markdown 报告中提取数值声明。"""
    claims: list[_NumericalClaim] = []
    lines = markdown.split("\n")
    for i, line in enumerate(lines):
        # 跳过标题行和表格分隔行
        if line.strip().startswith("#") or re.match(r"^\s*\|[-:\s|]+\|\s*$", line):
            continue
        for m in _METRIC_PATTERN.finditer(line):
            name = m.group(1).strip()
            raw_val = m.group(2).replace(",", "")
            unit = m.group(3) or ""
            try:
                val = float(raw_val)
            except ValueError:
                continue
            claims.append(_NumericalClaim(
                metric_name=name,
                reported_value=val,
                unit=unit,
                context=line.strip()[:120],
                line_index=i,
            ))
    return claims


# ── Yellow: 数值核对 ──

def _get_reported_value_in_yuan(claim: _NumericalClaim) -> float | None:
    """将声明值转为元（原始单位）。百分比保持原样。"""
    scale = _UNIT_TO_SCALE.get(claim.unit)
    if scale is None:  # %
        return claim.reported_value / 100.0
    return claim.reported_value * scale


def _check_yellow_rules(
    claims: list[_NumericalClaim], company_id: str
) -> list[Issue]:
    """对照 SQLite 数据库核验数值。差异 > 5% 标记为 yellow。"""
    issues: list[Issue] = []
    periods = list_periods(company_id)
    if not periods:
        return issues
    latest = periods[-1]

    for claim in claims:
        match = _METRIC_VERIFY_MAP.get(claim.metric_name)
        if match is None:
            continue  # 不在可验证列表中，跳过

        codes, fn_key = match

        if fn_key is not None and isinstance(fn_key, str):
            # 复合指标：调用计算函数
            compute_fn = _COMPUTE_FNS.get(fn_key)
            if compute_fn is None:
                continue
            actual = compute_fn(company_id, latest)
            # 复合指标值通常是比率（如 0.60 表示 60%），报告可能以 % 呈现
            if claim.unit == "%":
                reported = claim.reported_value / 100.0
            else:
                reported = claim.reported_value
        else:
            # 直接查数据库
            actual = None
            for code in codes:
                actual = query_metric(company_id, code, latest)
                if actual is not None:
                    break
            reported = _get_reported_value_in_yuan(claim)

        if actual is None or actual == 0:
            continue  # 数据库中没有该指标，跳过

        if reported is None or reported == 0:
            continue

        diff_pct = abs(reported - actual) / abs(actual)
        if diff_pct > 0.05:
            if claim.unit == "%" and fn_key is not None:
                # 比率类：报告显示为百分比，实际是小数
                detail_report = f"{claim.reported_value:.2f}%"
                detail_actual = f"{actual:.2%}"
            elif claim.unit == "%":
                detail_report = f"{claim.reported_value:.2f}%"
                detail_actual = f"{actual:,.2f}元"
            else:
                unit_label = claim.unit or "元"
                detail_report = f"{claim.reported_value:.2f}{unit_label}"
                detail_actual = f"{actual:,.2f}元"
            issues.append(Issue(
                severity="yellow",
                rule=f"数值核对: {claim.metric_name}",
                location=claim.context,
                detail=(
                    f"报告值: {detail_report}，"
                    f"数据库值: {detail_actual}，"
                    f"差异: {diff_pct:.1%}"
                ),
                evidence=[
                    f"SQLite {codes[0]} @ {latest} = {actual}",
                ],
            ))

    return issues


# ── Red: 实体核对 ──

def _check_red_rules(markdown: str, company_id: str) -> list[Issue]:
    """核验公司名称、股票代码等实体。"""
    issues: list[Issue] = []

    # 从 akshare 获取公司信息
    try:
        from external.akshare_client import get_company_info
        info = get_company_info(company_id)
    except Exception:
        logger.warning("akshare unavailable for red rules", exc_info=True)
        info = {}

    expected_name = info.get("company_name", "") or info.get("公司名称", "")

    # 检查股票代码格式
    if company_id and not re.search(re.escape(company_id), markdown):
        issues.append(Issue(
            severity="red",
            rule="实体核对: 股票代码",
            location=f"全文未找到股票代码 {company_id}",
            detail=f"报告中未提及股票代码 {company_id}",
            evidence=[f"输入参数 company_id={company_id}"],
        ))

    # 检查公司全称
    if expected_name:
        # 用模糊匹配：允许简称
        short_name = expected_name.replace("股份有限公司", "").replace("有限公司", "")
        if expected_name not in markdown and short_name not in markdown:
            issues.append(Issue(
                severity="red",
                rule="实体核对: 公司名称",
                location="报告全文",
                detail=f"报告中未找到公司全称“{expected_name}”或其简称",
                evidence=[f"akshare 返回: {expected_name}"],
            ))

    return issues


# ── Orange: 时效核对 ──

def _check_orange_rules(markdown: str, company_id: str) -> list[Issue]:
    """检查报告中引用的数据是否过时。"""
    issues: list[Issue] = []
    current_year = datetime.now().year
    stale_threshold = current_year - 2  # 2024 及以前 > 2年

    # 提取年份引用
    year_pattern = re.compile(r"(20\d{2})\s*年")
    years_found = set()
    for m in year_pattern.finditer(markdown):
        years_found.add(int(m.group(1)))

    for y in sorted(years_found):
        if y <= stale_threshold:
            issues.append(Issue(
                severity="orange",
                rule="时效核对: 数据年份过旧",
                location=f"引用 {y} 年数据",
                detail=f"引用了 {y} 年的数据，距今已超过 2 年，可能已过时。",
                evidence=[f"当前年份: {current_year}，阈值: {stale_threshold}"],
            ))

    # 检查数据库最新报告期
    try:
        periods = list_periods(company_id)
        if periods:
            latest = periods[-1]
            try:
                latest_date = datetime.strptime(latest, "%Y-%m-%d")
                months_behind = (datetime.now() - latest_date).days / 30
                if months_behind > 12:
                    issues.append(Issue(
                        severity="orange",
                        rule="时效核对: 财务数据滞后",
                        location=f"数据库最新报告期: {latest}",
                        detail=f"最新财务数据为 {latest}，距今约 {months_behind:.0f} 个月，已超过一年。",
                        evidence=[f"SQLite report_meta: latest period = {latest}"],
                    ))
            except ValueError:
                pass
    except Exception:
        logger.warning("Could not check latest period", exc_info=True)

    return issues


# ── 标注 ──

def _annotate_markdown(markdown: str, issues: list[Issue]) -> str:
    """在报告中插入回检标注。"""
    if not issues:
        return markdown

    lines = markdown.split("\n")

    # 按行号分组 issues
    issues_by_line: dict[int, list[Issue]] = {}
    yellow_count = red_count = orange_count = 0
    for issue in issues:
        # 尝试在正文中定位 issue.location
        found = False
        for i, line in enumerate(lines):
            if issue.location and issue.location[:30] in line:
                issues_by_line.setdefault(i, []).append(issue)
                found = True
                break
        if not found:
            issues_by_line.setdefault(-1, []).append(issue)

        if issue.severity == "yellow":
            yellow_count += 1
        elif issue.severity == "red":
            red_count += 1
        elif issue.severity == "orange":
            orange_count += 1

    # 在每个有问题的行末尾添加标注
    for line_idx, line_issues in sorted(issues_by_line.items()):
        if line_idx < 0 or line_idx >= len(lines):
            continue
        for iss in line_issues:
            tag = {
                "yellow": f'<span style="background:#fff3cd;padding:2px 4px;border-radius:3px" title="{iss.detail}">⚠️ 数值存疑</span>',
                "red": f'<span style="color:#dc3545;font-weight:bold;padding:2px 4px" title="{iss.detail}">🔴 实体异常</span>',
                "orange": f'<span style="background:#ffeaa7;padding:2px 4px;border-radius:3px" title="{iss.detail}">🟠 时效提醒</span>',
            }.get(iss.severity, "⚠️")
            lines[line_idx] = lines[line_idx].rstrip() + " " + tag

    # 底部追加回检摘要
    summary = (
        f"\n\n---\n\n"
        f"## 回检摘要\n\n"
        f"| 类别 | 数量 | 说明 |\n"
        f"|------|------|------|\n"
        f"| 🟡 数值存疑 | {yellow_count} | 报告数值与数据库差异 &gt; 5%，请复核 |\n"
        f"| 🔴 实体异常 | {red_count} | 公司名/代码与公开数据不一致 |\n"
        f"| 🟠 时效提醒 | {orange_count} | 引用的数据可能已过时 |\n"
    )
    if issues:
        summary += "\n### 详细问题列表\n\n"
        for i, iss in enumerate(issues, 1):
            summary += (
                f"**{i}. [{iss.severity.upper()}] {iss.rule}**\n"
                f"- 位置: {iss.location[:80]}\n"
                f"- 描述: {iss.detail}\n"
                f"- 依据: {'; '.join(iss.evidence)}\n\n"
            )

    return "\n".join(lines) + summary


# ── 主入口 ──

def run(report_markdown: str, company_id: str) -> VerificationResult:
    """对报告全文执行三类回检规则，返回带标注的 markdown 和问题列表。"""
    init_db()

    all_issues: list[Issue] = []

    # 1. Yellow: 数值核对
    try:
        claims = _extract_numerical_claims(report_markdown)
        yellow_issues = _check_yellow_rules(claims, company_id)
        all_issues.extend(yellow_issues)
    except Exception:
        logger.warning("Yellow rules failed", exc_info=True)

    # 2. Red: 实体核对
    try:
        red_issues = _check_red_rules(report_markdown, company_id)
        all_issues.extend(red_issues)
    except Exception:
        logger.warning("Red rules failed", exc_info=True)

    # 3. Orange: 时效核对
    try:
        orange_issues = _check_orange_rules(report_markdown, company_id)
        all_issues.extend(orange_issues)
    except Exception:
        logger.warning("Orange rules failed", exc_info=True)

    # 4. 标注
    annotated = _annotate_markdown(report_markdown, all_issues)

    return VerificationResult(annotated_markdown=annotated, issues=all_issues)


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    company = sys.argv[1] if len(sys.argv) > 1 else "300750"
    md_path = sys.argv[2] if len(sys.argv) > 2 else None

    if md_path:
        report = Path(md_path).read_text(encoding="utf-8")
    else:
        print("用法: python -m agents.verifier <company_id> <report.md>")
        print("（需要先生成报告 markdown 文件）")
        sys.exit(1)

    result = run(report, company)
    print(f"\n回检完成：{len(result.issues)} 个问题")
    for iss in result.issues:
        print(f"  [{iss.severity.upper()}] {iss.rule}: {iss.detail}")
