"""P1-B.3：结构性表格识别原语（复合结构信号）。

**单一真相**：源对象清单生成（``source_object_inventory``）与摊平表恢复
（``set_enumeration.recover_flattened_tables``）共用本模块的表题/表头/数据行/合计行
判定原语，绝不在两处各自实现一套「表格起点」规则（否则同一份源文本会派生两套恢复真相，
清单说 recovered_ok 而 assembly 缺失）。

「表题」绝不由关键词单独裁决（旧实现：含「情况/公司/业务/名称/金额」即视为表题，
导致「（二）主营业务情况」「安全生产情况」「有限公司」「名称」等普通标题与折行数据
被误判为表格起点）。本模块要求**复合结构信号**同时成立：

1. **表题形态**：非页码行、非「单位：」行、无句读、非多列行、长度受限；
2. **前驱合法**：紧邻上文不得是「单位：」行（那是上一张表的表头区）或一张正在进行的
   数据行（折行续行不是新表题）；
3. **结构跟随**：其后观察窗口内必须出现真实表格结构（「单位：」行 / 多列行 / 合计行）；
4. **无中介表题**：候选行与首个结构行之间不得再出现更靠后的表题（显式「表 N」、
   续表标记或另一个表题形态行）——否则本行只是正文标题，真正的表题在后面。

全部纯确定性、零 LLM、零网络。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

# 表题弱信号关键词：**必须**与复合结构信号联合使用，单独命中绝不构成表格起点。
TABLE_TITLE_KEYWORDS = (
    "构成", "情况", "明细", "占比", "收入", "成本", "资产", "负债", "产品", "行业",
    "地区", "业务", "子公司", "公司", "名称", "金额", "利润", "现金流", "应收", "应付",
)

# 表题候选行之后必须出现真实表格结构的观察窗口（按非空行计）。
TABLE_START_LOOKAHEAD = 4
# 表题候选行之前用于排除「上一张表表头区/数据行续行」的观察窗口（按非空行计）。
TABLE_START_PREDECESSOR_WINDOW = 3

# 表题行长度上限（超出即为正文）。
_TITLE_MAX_LEN = 80

_PROSE_PUNCT = re.compile(r"[。，、；：]")
_UNIT_RE = re.compile(r"单位\s*[:：]")
_COLUMN_SPLIT_RE = re.compile(r"\s{2,}")

# 结构闭合标记（合计/总计/小计）。
CLOSURE_MARKERS = ("合计", "总计", "小计")

# 显式表号行：「表 5-10 主营业务收入构成表」「表5-12…」。
EXPLICIT_TABLE_TITLE_RE = re.compile(r"^表\s*\d")

# 续表标记：「续表」「表 X（续）」「（续 1）」。
CONTINUATION_MARKER_RE = re.compile(r"续表|（续\d*）|\(续\d*\)")

_PAGE_NUMBER_RES = (
    re.compile(r"第\s*\d{1,4}\s*页"),
    re.compile(r"[Pp]age\s*\d{1,4}"),
    re.compile(r"\d{1,4}"),
    re.compile(r"[—\-–·]\s*\d{1,4}\s*[—\-–·]"),
)


def normalize_line(raw: str | None) -> str:
    """行规范化：NFC + 去首尾空白。"""
    return unicodedata.normalize("NFC", raw or "").strip()


def has_prose_punct(s: str) -> bool:
    """是否含句读（正文签名；表格行不含句读）。"""
    return bool(_PROSE_PUNCT.search(s or ""))


def is_unit_line(s: str) -> bool:
    """「单位：万元，%」行。"""
    return bool(_UNIT_RE.search(s or ""))


def is_page_number_line(s: str) -> bool:
    """孤立页码/页眉页脚行（如 '49'、'第 49 页'、'- 49 -'、'Page 49'）。"""
    t = normalize_line(s)
    if not t:
        return False
    return any(r.fullmatch(t) for r in _PAGE_NUMBER_RES)


def column_cells(s: str) -> list[str]:
    """按 ≥2 连续空白切列（不做粘连金额百分比二次拆分：那是恢复侧的口径）。"""
    return [c for c in _COLUMN_SPLIT_RE.split(normalize_line(s)) if c]


def is_columnar_row(s: str) -> bool:
    """多列行（表格行/表头行签名）：≥2 列且无句读。"""
    t = normalize_line(s)
    if not t or has_prose_punct(t):
        return False
    return len(column_cells(t)) >= 2


def is_closure_row(s: str) -> bool:
    """合计/总计/小计行（表格结构闭合签名）。"""
    return any(k in (s or "") for k in CLOSURE_MARKERS)


def is_explicit_table_title(s: str) -> bool:
    """显式「表 N」行（强边界信号）。"""
    return bool(EXPLICIT_TABLE_TITLE_RE.match(normalize_line(s)))


def is_continuation_marker(s: str) -> bool:
    """续表标记行。"""
    return bool(CONTINUATION_MARKER_RE.search(normalize_line(s)))


def is_title_form(s: str) -> bool:
    """表题**形态**（必要条件，非充分条件）：短行、非页码、非单位行、无句读、非多列行。"""
    t = normalize_line(s)
    if not t or len(t) >= _TITLE_MAX_LEN:
        return False
    if is_page_number_line(t) or is_unit_line(t):
        return False
    if has_prose_punct(t):
        return False
    if is_columnar_row(t):
        return False
    return True


def is_structural_follow(s: str) -> bool:
    """真实表格结构行：「单位：」行 / 多列行 / 合计行。"""
    t = normalize_line(s)
    if not t:
        return False
    return bool(is_unit_line(t) or is_columnar_row(t) or (
        is_closure_row(t) and not has_prose_punct(t)))


def detect_table_start_flags_across(groups: Sequence[Sequence[str]]) -> list[list[bool]]:
    """跨文本块的通用表题判定（返回与 ``groups`` 同形的布尔表）。

    真实材料的**表题与其表体常分属相邻的两个 block**（表题单独成块、单位/表头/数据行成下一块），
    因此「结构跟随 / 前驱合法 / 无中介表题」必须在**跨块的单一源流**上判定；若各块各自判定，
    分属两块的真实表题会因「块内无结构跟随」被丢弃，而块内的普通标题又会因「无结构跟随」幸免——
    两侧都错。本函数把各块行拼成单一源流（块边界不插入任何虚拟行，保持源顺序相邻性），
    在源流上判定后按原块形切回。清单侧与恢复侧**共用本函数**，绝不各自实现一套。
    """
    spans: list[tuple[int, int]] = []
    stream: list[str] = []
    for g in groups or ():
        lines = list(g or ())
        spans.append((len(stream), len(stream) + len(lines)))
        stream.extend(lines)
    flags = detect_table_start_flags(stream)
    return [flags[a:b] for a, b in spans]


def detect_table_start_flags(lines: list[str]) -> list[bool]:
    """逐行判定「通用表题（无显式表号的表格起点）」，返回与 lines 等长的布尔表。

    显式「表 N」行**不**在此判定（调用方另行处理，它是强边界信号，无需结构跟随）。
    """
    n = len(lines)
    flags = [False] * n
    norm = [normalize_line(l) for l in lines]
    nonempty = [i for i in range(n) if norm[i]]
    for pos, i in enumerate(nonempty):
        s = norm[i]
        if not is_title_form(s):
            continue
        if not any(k in s for k in TABLE_TITLE_KEYWORDS):
            continue
        if is_explicit_table_title(s) or is_continuation_marker(s):
            continue
        # 前驱合法：上一张表的表头区（「单位：」行）或正在进行的数据行 ⇒ 本行是表内折行。
        prev_window = nonempty[max(0, pos - TABLE_START_PREDECESSOR_WINDOW):pos]
        if any(is_unit_line(norm[j]) for j in prev_window):
            continue
        if prev_window:
            p = norm[prev_window[-1]]
            if is_columnar_row(p) and not is_closure_row(p):
                continue
        # 结构跟随 + 无中介表题。
        followed = False
        for j in nonempty[pos + 1:pos + 1 + TABLE_START_LOOKAHEAD]:
            t = norm[j]
            if is_explicit_table_title(t) or is_continuation_marker(t) or is_title_form(t):
                break  # 中介表题：真正的表格起点在后面，本行只是正文标题。
            if is_structural_follow(t):
                followed = True
                break
        flags[i] = followed
    return flags
