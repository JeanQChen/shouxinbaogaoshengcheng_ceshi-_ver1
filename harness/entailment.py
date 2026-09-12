"""Phase 3 Batch B 修订③④：证据支撑校验——确定性数字/口径预检层（无 LLM）。

与批量 entailment（LLM 法官，另见本模块 batch 层，Commit 3）分离。本文件只含**确定性
纯函数**，用于在 LLM 判决前做可复现的数值等价与口径风险预检：

- 值存在（硬）：claim 中的每个数字必须在被引用 Evidence 正文/结构化 payload 中
  找到**数值等价**项（`normalize_amount` 归一化，故 40,000万元 == 4亿元）。
- 口径风险（标记，非终局）：按语义口径维度（担保范围/授信范围/合并口径/合计层级）
  比较 claim 与证据的口径标记，产出 severity=high|low 的风险标记。**只标风险，
  不据单一关键字单独定论**；终局口径一致性由批量 entailment 判定。
- 引用可验证（硬）：claim 引用的 evidence_id 必须已被 capture_inspected 捕获。
- 收入/成本类别（标记，A5）：结构化引用 item_code → classify_revenue_cost 派生类别，
  claim 文本保守 hint（收入 iff 收入/营收 且非 成本/费用）；两者都是 revenue/cost 且
  不一致 → revenue_cost_mismatch（成本不能被采纳为收入）。终局阻断由
  structured_provenance._evaluate_claim 确定性判定（UNSUPPORTED revenue_cost_mismatch）。

gold 不进入本模块；本模块不读证据身份规则之外的任何 gold 内容。

CLI:
  python -m harness.entailment --self-check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from financial_v2.number_identity import classify_revenue_cost
from harness import schema as H


# ---------------------------------------------------------------------------
# 数值归一化（Decimal，非字符串包含）
# ---------------------------------------------------------------------------

# 金额单位 → 元（倍率，长后缀优先，避免「千万元」被「万元」误匹配）。
_UNIT_MULTIPLIERS: tuple[tuple[str, Decimal], ...] = (
    ("千万元", Decimal("10000000")),
    ("百万元", Decimal("1000000")),
    ("亿元", Decimal("100000000")),
    ("万元", Decimal("10000")),
    ("千元", Decimal("1000")),
    ("亿", Decimal("100000000")),
    ("万", Decimal("10000")),
    ("千", Decimal("1000")),
    ("元", Decimal("1")),
)

_FULLWIDTH = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
    "，": ",", "．": ".", "－": "-", "−": "-",
})

# 数值 token 抽取（含可选的金额/比例单位后缀）。预检用，允许误抽取（如年份/代码），
# 因「值存在」最终由批量 entailment 权威判定，此处只做保守信号。
# 后缀另收英文单位（DeepSeek 习惯写 "18.12 percent"/"133219980000.00 yuan"）。
_AMOUNT_RE = re.compile(
    r"[-−]?\d[\d,，]*(?:\.\d+)?\s*(?:千万元|百万元|亿元|万元|千元|亿|万|千|元|％|%|percent|pct|yuan|rmb)?")

# 英文单位后缀 → 中文（归一化前先翻译，使 _UNIT_MULTIPLIERS / 百分号判定统一走中文路径）。
_ENGLISH_UNITS = (("percent", "%"), ("pct", "%"), ("yuan", "元"), ("rmb", "元"))

# 趋势语义词（跨期/变化子 need 与结构化权威的趋势守卫共用）。
_TREND_TOKENS = ("同比", "较上年", "较上一年", "上升", "下降", "趋势",
                 "近三年", "增长率", "增速")


def has_trend_semantics(text: str) -> bool:
    """文本是否含趋势/比较语义（同比/较上年/变化/近三年/增速等）。

    「变化」单列处理：仅当不含「变化原因」时才算趋势——「变化原因」是解释性追问
    （为什么变），不是跨期比较。
    """
    t = text or ""
    if any(tok in t for tok in _TREND_TOKENS):
        return True
    return "变化" in t and "变化原因" not in t


def _translate_english_units(s: str) -> str:
    """把英文单位后缀翻译为中文（percent/pct→%、yuan/rmb→元），供统一归一化。"""
    t = (s or "").strip()
    low = t.lower()
    for en, zh in _ENGLISH_UNITS:
        if low.endswith(en):
            return t[: -len(en)].rstrip() + zh
    return t


def _detect_unit(token: str) -> str:
    """判定一个数值 token 的内联单位类别：percent | yuan | raw。"""
    t = token.strip()
    low = t.lower()
    if t.endswith("%") or t.endswith("％") or low.endswith("percent") or low.endswith("pct"):
        return "percent"
    for name, _ in _UNIT_MULTIPLIERS:
        if t.endswith(name):
            return "yuan"
    if low.endswith("yuan") or low.endswith("rmb"):
        return "yuan"
    return "raw"


def _canonical_kind(unit: str) -> str:
    return {"yuan": "money", "percent": "percent"}.get(unit, "unknown")


def _inline_mult(token: str) -> "Decimal | None":
    """token 内联金额单位的元倍率（无内联金额单位则 None）。"""
    t = _translate_english_units(token.strip())
    for name, mult in _UNIT_MULTIPLIERS:
        if t.endswith(name):
            return mult
    return None


def _bare_decimal(token: str) -> Decimal | None:
    """抽取 token 的纯数字部分（去单位后缀/百分号/逗号，不乘倍率）。"""
    t = _translate_english_units(token.strip())
    t = t.translate(_FULLWIDTH).strip()
    if not t:
        return None
    t = re.sub(r"[%％]\s*$", "", t).strip()
    neg = False
    if t.startswith("-") or t.startswith("−"):
        neg = True
        t = t[1:].strip()
    for name, _ in _UNIT_MULTIPLIERS:
        if t.endswith(name):
            t = t[: -len(name)].strip()
            break
    t = t.replace(",", "").replace("，", "").strip()
    if not t:
        return None
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None
    return -v if neg else v


@dataclass(frozen=True)
class Amount:
    """一个抽取出的数值 token（原文 + canonical Decimal + 单位类别）。

    value = canonical Decimal（金额统一到「元」、比例取数值）；canonical_kind =
    money|percent|unknown；ambiguous = 多单位表头且无法归列（三态里区分 UNSUPPORTED 与
    PARTIAL）。SUPPORTED 只用 canonical_kind + value 的 Decimal 等价判定。
    """

    token: str
    value: Decimal
    unit: str            # yuan | percent | raw
    canonical_kind: str = "unknown"  # money | percent | unknown
    ambiguous: bool = False


def normalize_amount(s: str) -> Decimal | None:
    """把「40,000万元 / 4亿元 / 12.5% / 0」归一化为 Decimal（金额到元，% 取数值）。

    无法解析返回 None（不猜测）。仅做数值归一化，不判业务口径。
    """
    s = (s or "").strip()
    if not s:
        return None
    s = _translate_english_units(s)  # 18.12 percent → 18.12%；… yuan → …元
    s = s.translate(_FULLWIDTH).strip()
    is_pct = s.endswith("%") or s.endswith("％")
    if is_pct:
        s = s[:-1].strip()
    neg = False
    if s.startswith("-") or s.startswith("−"):
        neg = True
        s = s[1:].strip()
    unit = Decimal("1")
    for name, mult in _UNIT_MULTIPLIERS:
        if s.endswith(name):
            unit = mult
            s = s[: -len(name)].strip()
            break
    s = s.replace(",", "").replace("，", "").strip()
    if not s:
        return None
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    v = v * unit
    if neg:
        v = -v
    return v


def amounts_equivalent(a: str, b: str) -> bool:
    """两数值 token 是否数值等价（只判数值等价，不判业务口径）。"""
    na, nb = normalize_amount(a), normalize_amount(b)
    if na is None or nb is None:
        return False
    return na == nb


def extract_amounts(text: str) -> list[Amount]:
    """从文本抽取数值 token（含内联单位），返回 canonical Amount 列表。"""
    out: list[Amount] = []
    seen: set[str] = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        tok = m.group(0).strip()
        if not tok or tok in seen:
            continue
        v = normalize_amount(tok)
        if v is None:
            continue
        unit = _detect_unit(tok)
        seen.add(tok)
        out.append(Amount(token=tok, value=v, unit=unit,
                          canonical_kind=_canonical_kind(unit)))
    return out


# ---------------------------------------------------------------------------
# Claim 业务数值提取（排除年份/日期/页码/引用序号/序数排名等非业务数字）
# ---------------------------------------------------------------------------

# 非业务数字模式（按「长模式优先」排序）：年份区间 → 完整中文日期 → 数字日期 →
# 年份 → 月/日 → 页码/条款/章节/序数排名 → 括号引用序号。全部替换为空格（避免拼接
# 出新数字）；金额/比例带单位后缀（万元/元/% 等）不含「年」或「第」，故不受影响。
# 仅排除序数/排名语义（第X位/第X名/第X次），不排除数量（X位/X名/X家/X项/连续X年）。
_CLAIM_NON_BUSINESS_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\d{4}\s*[—–\-－−~～至到]\s*\d{4}\s*年"),      # 2023—2025年 / 2023至2025年（年份区间）
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"),  # 2025年12月31日 / 2025年12月
    re.compile(r"\d{4}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}"),      # 2025-12-31 / 2025/12/31 / 2025.12.31
    re.compile(r"\d{4}\s*年"),                                     # 2025年 / 2024年度
    re.compile(r"\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"),               # 12月 / 12月31日（无年份）
    re.compile(r"第\s*\d+\s*[页条章节款位名次]"),                  # 第5页 / 第3条 / 第2位 / 第1名（序数/排名）
    re.compile(r"[\[(（]\s*\d+\s*[\])）]"),                        # [3] / (3) / （3）
)


def extract_claim_business_amounts(text: str) -> list[Amount]:
    """从 claim 文本抽取「业务数值」，排除年份/日期/页码/引用序号/序数排名等非业务数字。

    确定性、无 LLM、无 gold、无公司/案例规则。先用非业务模式「挖空」（替换为空格），
    再走 extract_amounts（含单位归一化 + canonical Decimal）。value_presence /
    deterministic_prechecks / structured_provenance 共同复用本实现（禁止复制两套正则）。

    - `2025万元` / `2025%` / `2025元` 无「年」后缀，不受年份模式影响 → 保留；
    - `2023—2025年` / `2023至2025年` → 年份区间整体剔除，无 value_missing；
    - `截至2025年12月31日，资产为4亿元` → 仅 4亿元（日期组成部分全部剔除）；
    - `2位执行董事` / `5家客户` / `连续9年` / `54,538项专利` → 数量保留；
    - `第2位` / `第1名` / `第3条` / `第5页` / `引用[2]` → 序数/排名/页码/引用剔除
      （排名语义仍由 entailment 判定）。
    """
    t = text or ""
    for pat in _CLAIM_NON_BUSINESS_RES:
        t = pat.sub(" ", t)
    return extract_amounts(t)


# ---------------------------------------------------------------------------
# 表头/列级单位上下文（材料解释层，不动 parser/Evidence 身份规则）
# ---------------------------------------------------------------------------

_UNIT_NOTE_RE = re.compile(r"单位\s*[:：]\s*([^\n\r]*)")
_UNIT_SEP_RE = re.compile(r"[，,、;；/\t\s]+")
# 列级单位标注：金额（万元）/ 占比（%）/ 金额(千元) 等括号形式。
_COLUMN_UNIT_RE = re.compile(r"[（(]\s*(万元|千元|百万元|亿元|元|％|%)\s*[）)]")
_COLUMN_HEADER_HINT = ("金额", "占比", "比例", "比率", "项目", "指标", "科目", "余额")


def _resolve_unit_label(p: str) -> tuple[str, "Decimal | None"] | None:
    """单位标签 → (kind, mult)：%/%→percent；金额单位→money；否则 None。"""
    p = _translate_english_units((p or "").strip())
    if p in ("%", "％"):
        return ("percent", None)
    for name, mm in _UNIT_MULTIPLIERS:
        if p == name:
            return ("money", mm)
    return None


def _parse_table_units(text: str) -> list[tuple[str, "Decimal | None"]]:
    """解析「单位：万元，%」→ [("money",10000),("percent",None)]（按类别去重、保序）。"""
    m = _UNIT_NOTE_RE.search(text or "")
    if not m:
        return []
    out: list[tuple[str, "Decimal | None"]] = []
    seen: set[str] = set()
    for part in _UNIT_SEP_RE.split(m.group(1).strip()):
        r = _resolve_unit_label(part)
        if r is None or r[0] in seen:
            continue
        seen.add(r[0])
        out.append(r)
    return out


def _parse_column_header_units(text: str) -> list[tuple[str, "Decimal | None"]]:
    """从表头行解析列级单位（金额（万元）/ 占比（%）），返回数据列序单位列表。

    仅在「非单位 note 行、含列头提示词、且含括号单位标注」时才解析，避免误判正文。
    """
    for line in (text or "").splitlines():
        if "单位" in line:
            continue
        if not any(w in line for w in _COLUMN_HEADER_HINT):
            continue
        labels = _COLUMN_UNIT_RE.findall(line)
        out: list[tuple[str, "Decimal | None"]] = []
        for lab in labels:
            r = _resolve_unit_label(lab)
            if r is not None:
                out.append(r)
        if out:
            return out
    return []


def _assign_bare_unit(table_units, column_units, bare_idx, can_cycle
                      ) -> tuple[str, "Decimal | None", bool]:
    """裸数字单位解析（优先级：列级 > 表级单单位 > 表级多单位按列对位 > raw）。

    返回 (unit, mult, ambiguous)。ambiguous=True 表示单位类别明确但具体列归属不确定
    （多单位表头且无法完整对位）→ 上层记 PARTIAL。
    """
    if column_units:
        if bare_idx < len(column_units):
            kind, mult = column_units[bare_idx]
            return ("yuan" if kind == "money" else kind), mult, False
        return "raw", None, True
    if len(table_units) == 1:
        kind, mult = table_units[0]
        return ("yuan" if kind == "money" else kind), mult, False
    if len(table_units) >= 2:
        if can_cycle:
            kind, mult = table_units[bare_idx % len(table_units)]
            return ("yuan" if kind == "money" else kind), mult, False
        return "raw", None, True
    return "raw", None, False


def extract_evidence_amounts(text: str) -> list[Amount]:
    """证据正文抽取（表头/列级单位上下文传播）。优先级：
    行内后缀 > 列头单位（按位置）> 表级单单位 > 表级多单位按列对位 > 无单位 → raw。

    多单位表（如「单位：万元，%」）按行内裸数字顺序对位（第 i 个裸数字 →
    units[i % len(units)]）；当某行裸数字不足一个完整周期（无法确定列归属）时 →
    ambiguous（PARTIAL）。金额统一换算到「元」、比例取数值；先换算再比较，全程 Decimal。
    """
    table_units = _parse_table_units(text)
    column_units = _parse_column_header_units(text)
    out: list[Amount] = []
    seen: set[tuple[str, str]] = set()
    for line in (text or "").splitlines():
        matches = list(_AMOUNT_RE.finditer(line))
        bare_total = sum(1 for m in matches
                         if _detect_unit(m.group(0).strip()) == "raw")
        can_cycle = len(table_units) >= 2 and bare_total >= len(table_units)
        bare_idx = 0
        for m in matches:
            tok = m.group(0).strip()
            if not tok:
                continue
            unit = _detect_unit(tok)
            mult: "Decimal | None" = None
            ambiguous = False
            if unit == "raw":
                unit, mult, ambiguous = _assign_bare_unit(
                    table_units, column_units, bare_idx, can_cycle)
                bare_idx += 1
            base = _bare_decimal(tok)
            if base is None:
                continue
            if unit == "yuan":
                mult = mult or _inline_mult(tok) or Decimal("1")
                value = base * mult
            else:
                value = base  # percent / raw
            key = (tok, unit)
            if key in seen:
                continue
            seen.add(key)
            out.append(Amount(token=tok, value=value, unit=unit,
                              canonical_kind=_canonical_kind(unit), ambiguous=ambiguous))
    return out


def units_compatible(a: Amount, b: Amount) -> bool:
    """两 Amount 单位类别是否可比：raw 只匹配 raw；金额↔金额、比例↔比例；跨类 False。"""
    ka, kb = a.canonical_kind, b.canonical_kind
    if ka == "unknown" or kb == "unknown":
        return ka == kb
    return ka == kb


# ---------------------------------------------------------------------------
# 口径风险（语义标记，非终局）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScopeRisk:
    """一个口径风险标记（severity=high|low；high 仅当 claim 与证据存在直接宽窄冲突）。"""

    dimension: str
    severity: str            # high | low
    claim_scope: str         # claim 侧的宽口径标记
    evidence_scope: str      # 证据侧发现的口径标记
    detail: str


# 口径维度：claim 用「broad」宽口径，但证据只出现「narrow」窄口径（且无宽口径标记）→ high。
# 这是语义口径对比，不是单一关键字包含：只有当「宽-窄」两两冲突且证据缺宽口径时才 high。
_SCOPE_GROUPS: tuple[dict, ...] = (
    {
        "dimension": "guarantee_scope",
        "broad": ("全部对外担保", "整体对外担保", "对外担保余额", "对外担保总额", "全部担保"),
        "narrow": ("对子公司", "为股东", "为实际控制人", "对股东",
                   "为股东及实际控制人", "对关联方", "对子公司担保"),
    },
    {
        "dimension": "credit_line_scope",
        "broad": ("授信总额", "授信额度总额", "总授信", "整体授信", "授信额度合计"),
        "narrow": ("已用", "已使用", "占用额度", "单笔", "单户", "单家"),
    },
    {
        "dimension": "consolidation_scope",
        "broad": ("合并口径", "合并报表", "合并范围"),
        "narrow": ("母公司", "母公司口径", "单体口径", "母公司报表"),
    },
    {
        "dimension": "aggregate_level",
        "broad": ("合计", "总计", "全部", "整体", "总额"),
        "narrow": ("明细", "单项", "单笔", "单户"),
    },
)


def scope_risks(claim: H.Claim, materials: list[H.InspectedMaterial],
                question: str = "") -> list[ScopeRisk]:
    """按语义口径维度比较 claim 与证据正文，产出风险标记（不据关键字单独定论）。"""
    risks: list[ScopeRisk] = []
    claim_text = claim.text or ""
    ev_text = " ".join((m.text or "") for m in materials)
    for g in _SCOPE_GROUPS:
        broad_hit = next((b for b in g["broad"] if b in claim_text), None)
        if broad_hit is None:
            continue
        narrow_hit = next((n for n in g["narrow"] if n in ev_text), None)
        broad_in_ev = any(b in ev_text for b in g["broad"])
        if narrow_hit and not broad_in_ev:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="high",
                claim_scope=broad_hit, evidence_scope=narrow_hit,
                detail=(f"claim 声称「{broad_hit}」，但证据仅见「{narrow_hit}」子项，"
                        f"无法支撑总体口径")))
        elif broad_in_ev:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="low",
                claim_scope=broad_hit, evidence_scope="（总体口径）",
                detail=f"claim 与证据均含总体口径「{broad_hit}」，口径一致"))
        else:
            risks.append(ScopeRisk(
                dimension=g["dimension"], severity="low",
                claim_scope=broad_hit, evidence_scope="（未见明确口径）",
                detail=f"claim 声称「{broad_hit}」，但证据未见明确总体口径标记"))
    return risks


# ---------------------------------------------------------------------------
# 封闭集合/总数安全门（确定性，fail-closed；LLM 不计数）
# ---------------------------------------------------------------------------

# 总数/完整性信号（claim 侧）：只有显式「共/一共/总共/共计/合计/总计/共有 + 数 + 单位」
# 或「完整名单/全部为/均为/均由/前N名/多少位/几家」才触发封闭集合判定；裸数量
# 「2位执行董事」不是总数断言，不触发（其数值仍由 F1 保留，按一般数值校验）。
_TOTAL_COUNT_RE = re.compile(
    r"(一共有|一共|总共|共计|合计|总计|共有|共)\s*(\d+)\s*([位人名家项个])")
_COMPLETENESS_TERMS = ("完整名单", "全部为", "均为", "均由")
_RANK_TOP_RE = re.compile(r"前\s*(\d+)\s*名")
_COUNT_QUERY_TERMS = ("多少位", "多少家", "多少名", "几位", "几家")
# 诚实缺口/降级表达：承认部分、待核实 → PARTIAL（不作总数断言）。
_HEDGE_TERMS = ("至少", "待核实", "待补充", "尚未", "暂未", "仅确认")


@dataclass(frozen=True)
class ClosedSetGuard:
    """封闭集合/总数 claim 的确定性安全判定。

    triggered=False 表示该 claim 非总数/完整性断言（不参与本门）；
    verdict ∈ SUPPORTED|PARTIAL|UNSUPPORTED，只有 SUPPORTED 才允许该 claim 达 FULL。
    """

    triggered: bool
    verdict: str            # SUPPORTED | PARTIAL | UNSUPPORTED（triggered 时有效）
    reason: str
    claim_total: str = ""   # claim 侧总数表达式（如「共2位」）
    evidence_total: str = ""# 证据侧显式总数/枚举计数
    hedged: bool = False


def _total_value(total_expr: str) -> int | None:
    """从总数表达式抽数值（共2位 → 2、前3名 → 3）；无法抽返回 None。"""
    m = re.search(r"\d+", total_expr or "")
    return int(m.group(0)) if m else None


def _claim_total(text: str) -> str | None:
    """提取 claim 侧显式总数表达式（共2位/共计3人/前3名）；无则 None。"""
    m = _TOTAL_COUNT_RE.search(text or "")
    if m:
        return m.group(0).strip()
    m = _RANK_TOP_RE.search(text or "")
    if m:
        return m.group(0).strip()
    return None


def _count_members(text: str) -> int | None:
    """保守计数证据中顿号/逗号分隔的短项枚举；无法可靠计数返回 None。

    仅当所有分隔片段都是短项（≤6 字，姓名/简称形态）时才信任计数，避免把散文逗号
    误当成员分隔；不确定返回 None → 上层按 PARTIAL（宁可降级也不误判 extra member）。
    """
    t = text or ""
    t = _TOTAL_COUNT_RE.sub(" ", t)      # 去显式总数，避免与列表混在一起计数
    parts = [p.strip() for p in re.split(r"[、，,；;]", t) if p.strip()]
    if len(parts) < 2:
        return None
    if not all(0 < len(p) <= 6 for p in parts):
        return None
    return len(parts)


def closed_set_guard(claim: H.Claim,
                     materials: list[H.InspectedMaterial]) -> ClosedSetGuard:
    """封闭集合/总数安全门（确定性）：总数断言只有在证据显式给出同口径总数时才 SUPPORTED。

    规则（冻结，公司无关）：
    - 触发：claim 含总数表达式（共N位/共计N家/前N名）或完整性词（完整名单/全部为/均为/均由）
      或计数追问词（多少位/几家…）。
    - 诚实降级（至少…待核实）→ PARTIAL（承认部分，不作总数断言）。
    - 证据显式同口径总数、数值相等 → SUPPORTED；数值不等 → UNSUPPORTED total_mismatch。
    - 证据枚举成员数 > claim 总数 → UNSUPPORTED incomplete_closed_set（漏报成员）。
    - 其余（部分列表/无显式总数）→ PARTIAL（部分列表不能证明总数，永不 FULL）。
    """
    text = claim.text or ""
    total = _claim_total(text)
    completeness = any(t in text for t in _COMPLETENESS_TERMS)
    query = any(t in text for t in _COUNT_QUERY_TERMS)
    triggered = total is not None or completeness or query
    if not triggered:
        return ClosedSetGuard(triggered=False, verdict="", reason="")

    if any(h in text for h in _HEDGE_TERMS):
        return ClosedSetGuard(triggered=True, verdict="PARTIAL",
                              reason="hedged_partial", claim_total=total or "",
                              hedged=True)

    ev_text = " ".join((m.text or "") for m in materials)
    ev_total_match = _TOTAL_COUNT_RE.search(ev_text)
    if ev_total_match and total is not None:
        ev_total = ev_total_match.group(0).strip()
        ev_n = _total_value(ev_total)
        claim_n = _total_value(total)
        if ev_n is not None and claim_n is not None:
            if ev_n == claim_n:
                return ClosedSetGuard(triggered=True, verdict="SUPPORTED",
                                      reason="explicit_total_match",
                                      claim_total=total, evidence_total=ev_total)
            return ClosedSetGuard(triggered=True, verdict="UNSUPPORTED",
                                  reason="total_mismatch",
                                  claim_total=total, evidence_total=ev_total)

    if total is not None:
        claim_n = _total_value(total)
        mem = _count_members(ev_text)
        if claim_n is not None and mem is not None and mem > claim_n:
            return ClosedSetGuard(triggered=True, verdict="UNSUPPORTED",
                                  reason="incomplete_closed_set",
                                  claim_total=total,
                                  evidence_total=f"enumerated={mem}")

    return ClosedSetGuard(triggered=True, verdict="PARTIAL",
                          reason="no_explicit_total", claim_total=total or "",
                          evidence_total=(ev_total_match.group(0).strip()
                                          if ev_total_match else ""))


# ---------------------------------------------------------------------------
# 值存在 + 口径预检
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValueCheck:
    """claim 数值在证据中的存在性判定（三态：SUPPORTED/PARTIAL/UNSUPPORTED）。

    matched = 找到 canonical Decimal 等价的 SUPPORTED 项；
    missing = 未达 SUPPORTED 的 claim 数值 token（含 PARTIAL + UNSUPPORTED，均阻止 FULL）；
    partial = missing 中「单位类别明确但换算上下文不确定」的 PARTIAL 子集（仅报告用）。
    """

    claim_amounts: list[Amount]
    matched: list[dict]
    missing: list[str]
    partial: list[str] = ()


def _find_supported(ct: Amount, ev: list[Amount]) -> Amount | None:
    """找 SUPPORTED 匹配：单位类别相同 + canonical Decimal 等价。"""
    for e in ev:
        if ct.canonical_kind == "unknown":
            continue
        if e.canonical_kind == ct.canonical_kind and e.value == ct.value:
            return e
    return None


def _has_ambiguous_match(ct: Amount, ev: list[Amount]) -> bool:
    """claim 数值的裸数字与某 ambiguous 证据项一致（多单位表头无法归列）→ PARTIAL。"""
    if ct.canonical_kind == "unknown":
        return False
    bare = _bare_decimal(ct.token)
    return any(e.ambiguous and e.value == bare for e in ev)


def value_presence(claim: H.Claim, materials: list[H.InspectedMaterial]) -> ValueCheck:
    """claim 中每个数值是否在 cited 证据正文/payload 中找到 canonical 数值等价项。

    证据正文走表头/列级单位上下文（extract_evidence_amounts）；structured_payload 走内联
    路径（extract_amounts，结构化值自带单位）。逐 claim 数值分类 SUPPORTED / PARTIAL /
    UNSUPPORTED（详见模块 docstring 的三态语义）。
    """
    claim_amounts = extract_claim_business_amounts(claim.text or "")
    if not claim_amounts:
        return ValueCheck([], [], [], [])
    ev_amounts: list[Amount] = []
    for m in materials:
        ev_amounts.extend(extract_evidence_amounts(m.text or ""))
        if m.structured_payload:
            ev_amounts.extend(extract_amounts(
                json.dumps(m.structured_payload, ensure_ascii=False, default=str)))
    matched: list[dict] = []
    missing: list[str] = []
    partial: list[str] = []
    for ct in claim_amounts:
        hit = _find_supported(ct, ev_amounts)
        if hit is not None:
            matched.append({"claim_token": ct.token, "evidence_token": hit.token})
            continue
        if _has_ambiguous_match(ct, ev_amounts):
            partial.append(ct.token)
        missing.append(ct.token)
    return ValueCheck(claim_amounts, matched, missing, partial)


def _claim_evidence_ids(claim: H.Claim, answer: H.ResearchAnswer) -> list[str]:
    """claim 引用到的 evidence_id（仅 ref_type=evidence 且下标合法）。"""
    ids: list[str] = []
    for idx in claim.citation_refs:
        if 0 <= idx < len(answer.citations):
            cit = answer.citations[idx]
            if cit.ref_type == "evidence" and cit.evidence_id:
                ids.append(cit.evidence_id)
    return ids


def _claim_evidence_fact_ids(claim: H.Claim, answer: H.ResearchAnswer) -> list[str]:
    """claim 引用到的 evidence_fact_id（仅 ref_type=evidence 且 evidence_fact_id 非空）。"""
    ids: list[str] = []
    for idx in claim.citation_refs:
        if 0 <= idx < len(answer.citations):
            cit = answer.citations[idx]
            if cit.ref_type == "evidence" and cit.evidence_fact_id:
                ids.append(cit.evidence_fact_id)
    return ids


# ---------------------------------------------------------------------------
# A5：收入/成本类别（§17.1 成本不能被采纳为收入）
# ---------------------------------------------------------------------------

def _claim_revenue_cost_hint(text: str) -> str | None:
    """claim 文本的收入/成本语义提示（保守，仅在明确时返回）。

    - "revenue"：含 收入/营收 且不含 成本/费用；
    - "cost"：含 成本/费用 且不含 收入/营收；
    - 同时含两者或均不含 → None（不猜）。

    结构化绑定（CitationRef.item_code → classify_revenue_cost）优先于本提示；本提示仅作
    claim 侧弱信号，供「成本不能被采纳为收入」的确定性判定使用（非自由文本语义抽取）。
    """
    t = text or ""
    has_revenue = ("收入" in t) or ("营收" in t)
    has_cost = ("成本" in t) or ("费用" in t)
    if has_revenue and not has_cost:
        return "revenue"
    if has_cost and not has_revenue:
        return "cost"
    return None


def claim_revenue_cost_hint(text: str) -> str | None:
    """公开别名（structured_provenance 复用同一保守口径）。"""
    return _claim_revenue_cost_hint(text)


def _claim_structured_item_codes(claim: H.Claim, answer: H.ResearchAnswer) -> list[str]:
    """claim 结构化引用中的 item_code（仅 ref_type=structured 且 item_code 非空）。"""
    codes: list[str] = []
    for i in claim.citation_refs:
        if 0 <= i < len(answer.citations):
            cit = answer.citations[i]
            if cit.ref_type == "structured" and cit.item_code:
                codes.append(cit.item_code)
    return codes


def bind_evidence_facts(answer: H.ResearchAnswer, evidence_facts: list) -> None:
    """把 answer 的 evidence 引用确定性绑定到 EvidenceStructuredFact（evidence_fact_id）。

    仅当某 evidence_id 恰好派生出一个 fact 时绑定（无歧义）；多 fact（多板块/多期间）不
    绑特定坐标，交由 revenue_cost_precheck 的 evidence_id 兜底聚合类别判定。evidence_fact_id
    是内部确定性坐标，LLM 不生成也不修改本字段。
    """
    if not evidence_facts:
        return
    by_eid: dict[str, list] = {}
    for f in evidence_facts:
        by_eid.setdefault(f.evidence_id, []).append(f)
    for cit in answer.citations:
        if cit.ref_type == "evidence" and cit.evidence_id and not cit.evidence_fact_id:
            fs = by_eid.get(cit.evidence_id, [])
            if len(fs) == 1:
                cit.evidence_fact_id = fs[0].evidence_fact_id


def revenue_cost_precheck(claim: H.Claim, answer: H.ResearchAnswer,
                          evidence_facts: list | None = None) -> dict:
    """claim 收入/成本标签 vs 结构化引用科目代码类别 + Evidence 背书事实类别的确定性判定。

    - 结构化引用 item_code → classify_revenue_cost 派生类别（revenue/cost/other/None）；
    - Evidence 背书事实：经 evidence_fact_id 绑定（specific）或 evidence_id 兜底（同表全
      事实聚合，附注构成表单一收入/成本语义）→ revenue_cost_category；
    - claim 文本 → 保守 hint（收入 iff 收入/营收 且非 成本/费用）；
    - hint 与类别都是 revenue/cost 且不一致 → mismatch（成本被采纳为收入/反之）；
    - 任一缺失/含混/多类别并存 → 不 mismatch（保守，交结构化权威/LLM 法官）。

    返回 {"hint", "categories", "mismatch"}。
    """
    hint = _claim_revenue_cost_hint(claim.text)
    cats = {classify_revenue_cost(item_code=c)
            for c in _claim_structured_item_codes(claim, answer)}
    if evidence_facts:
        by_fact = {f.evidence_fact_id: f for f in evidence_facts}
        by_eid: dict[str, list] = {}
        for f in evidence_facts:
            by_eid.setdefault(f.evidence_id, []).append(f)
        # 1) 绑定到 specific evidence_fact_id 的类别
        for fid in _claim_evidence_fact_ids(claim, answer):
            f = by_fact.get(fid)
            if f is not None and f.revenue_cost_category:
                cats.add(f.revenue_cost_category)
        # 2) 兜底：引用 evidence_id 派生出的全部事实类别（附注构成表语义单一）
        if not (cats & {"revenue", "cost"}):
            for eid in _claim_evidence_ids(claim, answer):
                for f in by_eid.get(eid, []):
                    if f.revenue_cost_category:
                        cats.add(f.revenue_cost_category)
    rc = {c for c in cats if c in ("revenue", "cost")}
    mismatch = bool(hint in ("revenue", "cost") and rc and hint not in rc)
    return {"hint": hint, "categories": sorted(c for c in cats if c),
            "mismatch": mismatch}


def deterministic_prechecks(state: H.ResearchState,
                            answer: H.ResearchAnswer | None) -> dict[str, dict]:
    """逐 fact claim 的确定性数值/口径预检（仅 evidence 引用）。

    返回 {claim_id: {not_inspected, value_missing, value_missing_tokens,
    scope_risks(asdict), high_risk_scope}}。非 evidence 引用或 inference claim 不在此层。
    """
    out: dict[str, dict] = {}
    if answer is None:
        return out
    for claim in answer.claims:
        if claim.kind != "fact":
            continue
        ev_ids = _claim_evidence_ids(claim, answer)
        if not ev_ids:
            continue
        rc = revenue_cost_precheck(claim, answer,
                                   evidence_facts=state.evidence_structured_facts)
        materials = [state.inspected_evidence[e] for e in ev_ids
                     if e in state.inspected_evidence]
        not_inspected = bool(len(materials) < len(set(ev_ids)))
        csg = closed_set_guard(claim, materials)
        if not materials:
            out[claim.claim_id] = {
                "not_inspected": True,
                "value_missing": False,
                "value_missing_tokens": [],
                "scope_risks": [],
                "high_risk_scope": False,
                "revenue_cost_mismatch": rc["mismatch"],
                "revenue_cost": rc,
                "closed_set": dataclasses_asdict(csg),
            }
            continue
        vc = value_presence(claim, materials)
        risks = scope_risks(claim, materials, state.original_question)
        out[claim.claim_id] = {
            "not_inspected": not_inspected,
            "value_missing": bool(vc.missing),
            "value_missing_tokens": list(vc.missing),
            "scope_risks": [dataclasses_asdict(r) for r in risks],
            "high_risk_scope": any(r.severity == "high" for r in risks),
            "revenue_cost_mismatch": rc["mismatch"],
            "revenue_cost": rc,
            "closed_set": dataclasses_asdict(csg),
        }
    return out


# ---------------------------------------------------------------------------
# inspected 捕获 + page 回填
# ---------------------------------------------------------------------------

def capture_inspected(state: H.ResearchState, result) -> None:
    """把工具结果写入 state.inspected_evidence。

    - inspect_evidence SUCCESS → 全文（is_snippet=False）；
    - search_evidence / search_tables → 每 item 一段 snippet（is_snippet=True）。
    """
    if result.tool_name == "inspect_evidence" and result.status == "SUCCESS":
        d = result.data or {}
        eid = d.get("evidence_id", "")
        if not eid:
            return
        state.inspected_evidence[eid] = H.InspectedMaterial(
            evidence_id=eid,
            document_id=d.get("document_id", ""),
            source_name=d.get("source_name", ""),
            source_type=d.get("source_type", ""),
            page_number=d.get("page_number"),
            section_path=d.get("section_path", ""),
            evidence_type=d.get("evidence_type", ""),
            report_period=d.get("report_period"),
            text=d.get("text", "") or "",
            structured_payload=d.get("structured_payload"),
            is_snippet=False,
        )
        return
    if result.tool_name in ("search_evidence", "search_tables"):
        for item in (result.data or {}).get("items", []):
            eid = item.get("evidence_id", "")
            if not eid or eid in state.inspected_evidence:
                continue
            state.inspected_evidence[eid] = H.InspectedMaterial(
                evidence_id=eid,
                source_name=item.get("source_name", ""),
                page_number=item.get("page_number"),
                evidence_type=item.get("evidence_type", ""),
                text=item.get("snippet", "") or "",
                is_snippet=True,
            )


def backfill_page_numbers(answer: H.ResearchAnswer | None,
                          state: H.ResearchState) -> H.ResearchAnswer | None:
    """对 page_number 缺失的 evidence 引用，从 inspected_evidence 回填（只补空，不重写）。"""
    if answer is None:
        return answer
    for cit in answer.citations:
        if cit.ref_type == "evidence" and cit.page_number is None and cit.evidence_id:
            mat = state.inspected_evidence.get(cit.evidence_id)
            if mat is not None and mat.page_number is not None:
                cit.page_number = mat.page_number
    return answer


# ---------------------------------------------------------------------------
# 批量 entailment（只读法官，每问 1 次调用；非 claim×citation）
# ---------------------------------------------------------------------------

def _bounded_text(text: str, head: int = 1200, tail: int = 300) -> str:
    """有界截断：保留头部（含表头/段落开头）+ 尾部（常含合计/单位），不整段丢失口径。"""
    t = text or ""
    if len(t) <= head + tail:
        return t
    return t[:head] + "\n…[中段截断]…\n" + t[-tail:]


def bounded_text(text: str, head: int = 1200, tail: int = 300) -> str:
    """公开别名（答案 prompt 复用同一有界截断口径，避免正文整段丢失单位/合计行）。"""
    return _bounded_text(text, head, tail)


def _describe_citation(cit: H.CitationRef, state: H.ResearchState) -> str:
    """把一条引用翻译为给法官看的可读描述（含结构化值/正文元数据）。"""
    if cit.ref_type == "evidence":
        mat = state.inspected_evidence.get(cit.evidence_id or "")
        meta = ""
        if mat is not None:
            meta = (f" doc={mat.document_id or '-'} page={mat.page_number} "
                    f"period={mat.report_period or '-'}")
        return f"evidence_id={cit.evidence_id} page={cit.page_number}{meta}"
    if cit.ref_type == "structured":
        key = cit.formula_id or cit.item_code or "?"
        kind = "formula" if cit.formula_id else "item"
        val = ""
        for r in state.structured_refs:
            if (r.snapshot_id == cit.snapshot_id and r.period == cit.period
                    and ((cit.formula_id and r.formula_id == cit.formula_id)
                         or (cit.item_code and r.item_code == cit.item_code))):
                v = r.display_value if r.display_value is not None else r.raw_value
                val = f" value={v} unit={r.unit}"
                # Change 3：比较/趋势字段表面化（方向/差额由代码算好，LLM 只解读不重算）。
                if getattr(r, "direction", None):
                    val += (f" period_a={r.period_a} period_b={r.period_b}"
                            f" direction={r.direction} change={r.change_value}")
                break
        return f"structured {kind}={key} snapshot={cit.snapshot_id} period={cit.period}{val}"
    if cit.ref_type == "external":
        mat = (state.external_material or {}).get(cit.source_snapshot_id or "")
        if mat is not None:
            meta = f" title={mat.title}" if mat.title else ""
            snippet = (f"\n    {_bounded_text(mat.content_text, head=300, tail=120)}"
                       if mat.content_text else "")
            return f"external source_snapshot_id={cit.source_snapshot_id}{meta}{snippet}"
        return f"external source_snapshot_id={cit.source_snapshot_id}"
    return f"ref_type={cit.ref_type}"


def entailment_prompt_vars(state: H.ResearchState, answer: H.ResearchAnswer,
                           prechecks: dict[str, dict],
                           exclude_claim_ids: frozenset[str] = frozenset()) -> dict:
    """拼批量 entailment 的 prompt 变量（claims + 引用映射 + 正文 + 确定性标记）。

    exclude_claim_ids：结构化权威已 SUPPORTED 的 claim，不送 LLM entailment（避免纯
    结构化 claim 因「无正文」被误判 UNSUPPORTED）。
    """
    claim_lines: list[str] = []
    obs_lines: list[str] = []
    for c in answer.claims:
        # retrieval_observation 是运行时诊断（「本次检索未取得 X」），不是事实断言，
        # 不参与 entailment 判定（不判 SUPPORTED/UNSUPPORTED），仅供法官理解缺口上下文。
        if c.kind == "retrieval_observation":
            obs_lines.append(f"- {c.claim_id}: {c.text}")
            continue
        if c.claim_id in exclude_claim_ids:
            continue
        pc = prechecks.get(c.claim_id, {})
        markers: list[str] = []
        if pc.get("not_inspected"):
            markers.append("not_inspected")
        if pc.get("value_missing"):
            markers.append("value_missing(" + ",".join(pc.get("value_missing_tokens", [])) + ")")
        if pc.get("high_risk_scope"):
            markers.append("high_risk_scope")
        if pc.get("revenue_cost_mismatch"):
            markers.append("revenue_cost_mismatch")
        marker = " | ".join(markers) or "-"
        claim_lines.append(
            f"- {c.claim_id} [{c.kind}] cites={c.citation_refs}: {c.text}\n"
            f"  确定性预检: {marker}")
    citation_lines = [f"- [{i}] {_describe_citation(cit, state)}"
                      for i, cit in enumerate(answer.citations)]
    evidence_lines: list[str] = []
    for eid, mat in state.inspected_evidence.items():
        kind = "snippet" if mat.is_snippet else "全文"
        evidence_lines.append(
            f"### evidence_id={eid} [{kind}] doc={mat.document_id or '-'} "
            f"page={mat.page_number} period={mat.report_period or '-'} "
            f"source={mat.source_name}\n{_bounded_text(mat.text)}")
    # 外部快照正文（fetch+snapshot 后写入 state.external_material）进入法官上下文：
    # 快照存在 ≠ 模型读到正文，须把正文一并注入，external 引用才可被支撑校验。
    for sid, mat in (state.external_material or {}).items():
        meta = f"### external source_snapshot_id={sid}"
        if mat.title:
            meta += f" title={mat.title}"
        if mat.source_grade:
            meta += f" source_grade={mat.source_grade}"
        evidence_lines.append(f"{meta}\n{_bounded_text(mat.content_text)}")
    aspects = "\n".join(f"- {a.get('aspect_id')}: {a.get('text')}"
                        for a in state.required_aspects) or "（无）"
    return {
        "company_id": state.company_id,
        "section_id": state.section_id or "",
        "question": state.original_question,
        "required_aspects": aspects,
        "claims": "\n".join(claim_lines) if claim_lines else "（无）",
        "citations": "\n".join(citation_lines) if citation_lines else "（无）",
        "evidence": "\n\n".join(evidence_lines) if evidence_lines else "（无正文）",
        "retrieval_observations": "\n".join(obs_lines) if obs_lines else "（无）",
    }


def _extract_json(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start:end + 1]


def _consistency(v) -> str:
    s = str(v or "").strip().lower()
    return s if s in H.CONSISTENCY_LEVELS else "unknown"


def parse_entailment(raw: str) -> list[H.EntailmentVerdict]:
    """解析批量 entailment 输出；非法抛 ValueError（fail-closed）。"""
    cleaned = _extract_json(raw)
    if cleaned is None:
        raise ValueError("无法从 entailment 输出提取 JSON")
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("entailment 输出必须为 JSON object")
    verdicts: list[H.EntailmentVerdict] = []
    for v in data.get("verdicts", []):
        if not isinstance(v, dict):
            continue
        verdict = str(v.get("verdict", "")).upper()
        if verdict not in H.ENTAILMENT_VERDICTS:
            raise ValueError(f"非法 entailment verdict: {verdict!r}")
        verdicts.append(H.EntailmentVerdict(
            claim_id=str(v.get("claim_id", "")),
            citation_ids=[str(c) for c in v.get("citation_ids", [])],
            verdict=verdict,
            reason=str(v.get("reason", "")),
            scope_consistency=_consistency(v.get("scope_consistency")),
            period_consistency=_consistency(v.get("period_consistency")),
            unit_consistency=_consistency(v.get("unit_consistency")),
            subject_consistency=_consistency(v.get("subject_consistency")),
        ))
    return verdicts


def evaluate_entailment_batch(state: H.ResearchState, answer: H.ResearchAnswer,
                              llm, prechecks: dict[str, dict],
                              exclude_claim_ids: frozenset[str] = frozenset(),
                              on_response=None) -> list[H.EntailmentVerdict]:
    """单次批量 entailment（每问 1 次调用）。

    llm 无 evaluate_entailment_batch（Mock）→ 跳过返回 []；有但调用/解析异常 → 向上抛，
    由 runtime 记 entailment_evaluator_failed（fail-closed）。

    exclude_claim_ids：结构化权威已 SUPPORTED 的 claim，跳过 LLM 判定。

    on_response(resp)：可选回调，在解析前接收原始 LLMResponse（供 runtime 记账
    entailment 分类 usage，不改变本函数语义）。
    """
    if not hasattr(llm, "evaluate_entailment_batch"):
        return []
    prompt_vars = entailment_prompt_vars(state, answer, prechecks, exclude_claim_ids)
    resp = llm.evaluate_entailment_batch(prompt_vars)
    if on_response is not None:
        on_response(resp)
    return parse_entailment(resp.text)


# ---------------------------------------------------------------------------
# 工具：asdict 辅助
# ---------------------------------------------------------------------------

def dataclasses_asdict(obj) -> dict:
    import dataclasses
    return dataclasses.asdict(obj)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m harness.entailment", description="确定性数字/口径预检自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        # 数值等价样例。
        equiv = [
            ("40,000万元", "4亿元"),
            ("0万元", "0元"),
            ("12.5%", "12.5%"),
        ]
        not_equiv = [
            ("40,000万元", "40,000元"),
            ("4亿元", "4万元"),
            ("12.5%", "12.5万元"),
        ]
        # 口径风险样例：claim 声称「全部对外担保」，证据仅见「为股东及实际控制人」子项。
        claim = H.Claim(claim_id="c1", text="公司全部对外担保余额为0万元",
                        kind="fact", citation_refs=[0])
        mat = H.InspectedMaterial(
            evidence_id="e1", text="为股东及实际控制人提供担保的余额为0万元",
            is_snippet=False)
        risks = scope_risks(claim, [mat], "")
        # 表头/列级单位传播样例：单单位表 / 双单位表 / 无单位。
        table_wan = ("表 5-10 主营业务收入构成表\n单位：万元\n项目          金额\n"
                     "动力电池      31,650,636.9\n储能          5,850,000.0\n")
        table_mixed = ("表 5-10 主营业务收入构成表\n单位：万元，%\n项目          金额          占比\n"
                       "动力电池      31,650,636.9  74.7\n储能          5,850,000.0   13.8\n")
        table_none = "项目          金额\n动力电池      31,650,636.9\n"
        print(json.dumps({
            "amounts_equivalent": [
                {"a": a, "b": b, "equivalent": amounts_equivalent(a, b)}
                for a, b in equiv
            ],
            "amounts_not_equivalent": [
                {"a": a, "b": b, "equivalent": amounts_equivalent(a, b)}
                for a, b in not_equiv
            ],
            "canonical_4亿": normalize_amount("4亿元"),
            "canonical_4万": normalize_amount("4万元"),
            "canonical_18pct": normalize_amount("18.12 percent"),
            "scope_risks_example": [dataclasses_asdict(r) for r in risks],
            "table_wan_units": [_parse_table_units(table_wan)],
            "table_mixed_units": [_parse_table_units(table_mixed)],
            "table_wan_amounts": [dataclasses_asdict(a)
                                  for a in extract_evidence_amounts(table_wan)],
            "table_mixed_amounts": [dataclasses_asdict(a)
                                    for a in extract_evidence_amounts(table_mixed)],
            "table_none_amounts": [dataclasses_asdict(a)
                                   for a in extract_evidence_amounts(table_none)],
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
