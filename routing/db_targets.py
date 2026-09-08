"""确定性 DB target resolver（无 LLM、无 RAG、无 I/O）。

把自然语言问题映射为结构化 DB 目标：

- financial_field   → standard_item_code（来自 V2 标准科目 / Mapping Registry）
- financial_metric  → formula_id + formula_version（来自 Formula Registry）

契约修正 A：supported_db_fields 使用 V2 标准科目 / Mapping Registry，
supported_metric_ids 使用 Formula Registry；resolve 是确定性映射，未知/歧义返回 None，
绝不回退 LLM 发明代码。

匹配策略（确定性、fail-closed）：
- 指标优先于字段（避免「资产负债率」被拆成「负债」字段）；
- 同一类别内按「最长命中别名」决胜（避免「流动负债」被「负债」抢注）；
- 最长长度并列且指向不同 code → 歧义 → None。

路由层仅额外维护一小份口语同义词表（_METRIC_SYNONYMS / _FIELD_SYNONYMS），
用于补足报表正式名（「资产总计」）与提问口语（「总资产」）的差异，不覆盖
Financial_v2 注册表的权威性。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from financial_v2 import formulas
from financial_v2 import mapping


@dataclass
class DbTarget:
    """一个已解析的 DB 目标（field 或 metric）。"""

    target_type: str              # "field" | "metric"
    standard_item_code: str | None
    formula_id: str | None
    formula_version: str | None


# ---------------------------------------------------------------------------
# 口语同义词（路由层，补足提问口语 vs 报表正式名差异）
# ---------------------------------------------------------------------------

# 指标中文口语名（公式 name 为 ROE/ROA/EBITDA 等英文缩写时补中文）。
_METRIC_SYNONYMS: dict[str, list[str]] = {
    "PROF_ROE": ["净资产收益率", "净资产回报率", "股东权益报酬率"],
    "PROF_ROA": ["总资产收益率", "总资产净利率", "资产收益率", "资产净利率"],
    "SOLV_INTEREST_COVER": ["利息覆盖倍数", "利息保障倍数", "已获利息倍数"],
    "EBITDA": ["息税折旧摊销前利润", "息税折旧及摊销前利润"],
    "SOLV_DEBT_RATIO": ["资产负债率"],
    "PROF_GROSS_MARGIN": ["毛利率"],
    "PROF_NET_MARGIN": ["净利率", "销售净利率"],
}

# 字段口语名（标准科目 code ← 提问口语）。
_FIELD_SYNONYMS: dict[str, list[str]] = {
    "TOTAL_ASSETS": ["总资产", "资产总额"],
    "TOTAL_LIABILITIES": ["总负债", "负债总额", "负债"],
    "TOTAL_EQUITY": ["净资产", "股东权益", "所有者权益", "权益"],
    "TOTAL_REVENUE": ["营业总收入", "总营收"],
    "OPERATING_REVENUE": ["营收"],
    "NET_PROFIT_PARENT": ["归母净利润", "归母净利", "归属于母公司股东的净利润"],
    "OPERATING_CASH_FLOW": ["经营活动现金流", "经营现金流", "经营活动现金流量"],
    "CURRENT_ASSETS": ["流动资产"],
    "CURRENT_LIABILITIES": ["流动负债"],
}


# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """NFKC → 全角转半角 → 小写 → 去空白。用于别名/提问的确定性比较。"""
    s = unicodedata.normalize("NFKC", s)
    out: list[str] = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            continue
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        if ch.isspace():
            continue
        out.append(ch)
    return "".join(out).lower()


# ---------------------------------------------------------------------------
# 注册表读取（纯函数，惰性缓存）
# ---------------------------------------------------------------------------

_field_alias_cache: dict[str, str] | None = None   # normalized_alias → code
_metric_alias_cache: dict[str, str] | None = None  # normalized_name → formula_id


def _field_aliases() -> dict[str, str]:
    """normalized 字段别名 → standard_item_code（Mapping Registry 权威别名 + 口语）。"""
    global _field_alias_cache
    if _field_alias_cache is None:
        d: dict[str, str] = {}
        for rule in mapping.build_builtin_rules():
            for alias in rule.aliases:
                d[_normalize(alias)] = rule.standard_item_code
        for code, synonyms in _FIELD_SYNONYMS.items():
            for syn in synonyms:
                d[_normalize(syn)] = code
        _field_alias_cache = d
    return _field_alias_cache


def _metric_aliases() -> dict[str, str]:
    """normalized 指标名 → formula_id（Formula Registry name + 口语）。"""
    global _metric_alias_cache
    if _metric_alias_cache is None:
        d: dict[str, str] = {}
        for fid, fdef in formulas.build_registry().items():
            d[_normalize(fdef.name)] = fid
        for fid, synonyms in _METRIC_SYNONYMS.items():
            for syn in synonyms:
                d[_normalize(syn)] = fid
        _metric_alias_cache = d
    return _metric_alias_cache


def supported_db_fields() -> list[str]:
    """V2 标准科目 / Mapping Registry 支持的字段 code 清单（静态，与快照内容无关）。"""
    codes = sorted({r.standard_item_code for r in mapping.build_builtin_rules()})
    return codes


def supported_metric_ids() -> list[str]:
    """Formula Registry 支持的指标 id 清单（静态）。"""
    return sorted(formulas.ACTIVE_FORMULA_VERSIONS.keys())


# ---------------------------------------------------------------------------
# 确定性解析
# ---------------------------------------------------------------------------

def _collect_spans(alias_map: dict[str, str], question_norm: str) -> list[tuple[int, int, str]]:
    """收集全部命中区间 (start, end, code)，含重复出现；未命中返回空列表。"""
    spans: list[tuple[int, int, str]] = []
    for alias, code in alias_map.items():
        if not alias:
            continue
        start = question_norm.find(alias)
        while start != -1:
            spans.append((start, start + len(alias), code))
            start = question_norm.find(alias, start + 1)
    return spans


def _resolve_spans(spans: list[tuple[int, int, str]]) -> str | None:
    """嵌套消解后取唯一 code。

    - 被更长别名严格包含的短别名视为冗余（「归母净利润」内含「净利润」）；
    - 剩余的「极大别名」指向唯一 code → 解析；指向多个 code → 歧义 None。
    """
    if not spans:
        return None
    maximal: list[tuple[int, int, str]] = []
    for s in spans:
        nested = any(
            other != s and other[0] <= s[0] and s[1] <= other[1] for other in spans
        )
        if not nested:
            maximal.append(s)
    codes = {code for _, _, code in maximal}
    return codes.pop() if len(codes) == 1 else None


def resolve_db_target(question: str) -> DbTarget | None:
    """把问题解析为结构化 DB 目标；无法确定/歧义返回 None（fail-closed）。

    指标优先：只要有任何指标命中（哪怕歧义），就不回退到字段，避免
    「流动比率、速动比率、资产负债率」被拆成「负债」字段。
    """
    if not question or not question.strip():
        return None
    q = _normalize(question)

    metric_id = _resolve_spans(_collect_spans(_metric_aliases(), q))
    if metric_id is not None:
        return DbTarget(
            target_type="metric", standard_item_code=None, formula_id=metric_id,
            formula_version=formulas.ACTIVE_FORMULA_VERSIONS.get(metric_id),
        )
    # 存在指标命中但歧义 → 明确不可解析，不越级到字段。
    if _collect_spans(_metric_aliases(), q):
        return None

    field_code = _resolve_spans(_collect_spans(_field_aliases(), q))
    if field_code is not None:
        return DbTarget(
            target_type="field", standard_item_code=field_code, formula_id=None,
            formula_version=None,
        )
    return None


# ---------------------------------------------------------------------------
# target_period 提取（快照内期间；与 snapshot_as_of_date 分离）
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def resolve_target_period(question: str) -> str | None:
    """从问题提取目标报告期（快照内 report_period）。

    契约修正 2：target_period 用于在快照内选择 SnapshotItem / MetricResult 的
    report_period，与用于选择 current snapshot 的 snapshot_as_of_date 分离。

    规则（确定性、fail-closed）：
    - 问题中恰好出现一个 4 位年份 → 返回该年度报告期 "YYYY-12-31"；
    - 0 个年份 → None（executor 回退为 snapshot_as_of_date，即「当前快照期」）；
    - ≥2 个年份 → None（跨期比较应走 DEEP_RETRIEVAL，不落到单值 DB target）。
    """
    if not question:
        return None
    years = sorted({m.group() for m in _YEAR_RE.finditer(question)})
    if len(years) == 1:
        return f"{years[0]}-12-31"
    return None


if __name__ == "__main__":
    # 冒烟自检：确定性样例。
    samples = [
        "2024 年总资产是多少？",
        "流动比率、速动比率、资产负债率分别是多少？",
        "2025 年归母净利润是多少？",
        "净资产收益率是多少？",
        "公司主营业务是什么？",
        "经营活动现金流量净额是多少？",
    ]
    for q in samples:
        t = resolve_db_target(q)
        print(f"{q}  ->  {t}")
