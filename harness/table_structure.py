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

import hashlib
import json
import re
import unicodedata
from typing import Sequence

# 表题弱信号关键词：**已废弃**，仅保留常量以免外部导入断裂。
# P1-B.2：宽泛关键词（情况/公司/业务/名称/负债…）**绝不**作为表格对象的必要条件 ——
# 表格对象一律由**组合结构信号**裁定（明确表号/表题 / 单位 / 表头 / 数据行 / 合计行 /
# continuation 标志），见 ``detect_table_start_flags``。
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


# 单元格内部的空白（业务无关排版空白）：只在**单元格内部**归一，绝不跨越列边界。
_INTRA_CELL_WS_RE = re.compile(r"\s+")


def canonical_cells(s: str) -> list[str]:
    """行 → **保列**的规范单元格数组（列边界/列序/列数/单元格内容全部保留）。

    P1-1：表对象的**内容身份绝不能在「把整行空白删光」的表示上计算** —— 那会把
    ``A | 1 | 23`` 与 ``A | 12 | 3`` 折叠成同一个身份（列边界被删除，业务内容不同却撞 ID）。

    本函数只把**单元格内部**的连续空白归一为单个空格并去首尾空白；列边界由
    ``column_cells`` 的「≥2 连续空白」切分保留，因此：

    - 仅列间距宽度不同（``A  1  23`` vs ``A    1    23``）→ 同一数组 → 同一身份；
    - 列数、列序、任一单元格内容（企业名称/金额/比例/期间/币种）不同 → 不同数组 → 不同身份。
    """
    return [_INTRA_CELL_WS_RE.sub(" ", c).strip() for c in column_cells(s)]


_NUMERIC_CELL_RE = re.compile(r"^[-+]?[\d,，.]+(?:%|％)?$")
# 数字 token 允许出现的字符（千分位/小数点/百分号/正负号/括号：负数的会计写法）。
_NUMERIC_TOKEN_CHARS = set("0123456789,，.%-+（）()")


def is_numeric_token(p: str) -> bool:
    """单个数字 token：非空、含数字、且只由数字/千分位/小数点/百分号/正负号构成。"""
    return bool(p) and any(ch.isdigit() for ch in p) and all(
        ch in _NUMERIC_TOKEN_CHARS for ch in p)


def is_numeric_cell(c: str) -> bool:
    """数字单元（**单一原语**：结构侧与恢复侧共用同一口径，绝不各写一套）。

    真实 PDF 会把若干列数字挤进一个空白分隔的单元（如「7,544,197.2 67.8 23.8」
    「4.15%-11.04%」）；这类单元仍是**数据**单元，绝不因粘连而误判为表头行。

    这是「第二层表头（子列层）」与「首个数据行」之间的**结构性**区分依据：真实第二层表头
    是列名（标签），数据行至少含一个数字单元。绝不按「最多吞两行」的位置规则贪心吞并。
    """
    s = (c or "").strip()
    if not s:
        return False
    if _NUMERIC_CELL_RE.match(s):
        return True
    parts = s.split()
    return bool(parts) and all(is_numeric_token(p) for p in parts)


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


def table_body_signals(s: str) -> tuple[str, ...]:
    """单行的**组合结构信号**（可多值）：unit / header / data / closure / continuation。

    - ``unit``：「单位：」行（表头区首行）；
    - ``columnar``：多列行（表头行或数据行签名，无句读）；
    - ``data``：多列行且非合计行（正在进行的数据行）；
    - ``closure``：合计/总计/小计行；
    - ``continuation``：续表标记。
    """
    t = normalize_line(s)
    if not t:
        return ()
    out: list[str] = []
    if is_unit_line(t):
        out.append("unit")
    if is_continuation_marker(t):
        out.append("continuation")
    if is_closure_row(t) and not has_prose_punct(t):
        out.append("closure")
    if is_columnar_row(t):
        out.append("columnar")
        if not is_closure_row(t):
            out.append("data")
    return tuple(out)


def row_signal_groups(rows: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """逐行结构性信号（**每行一个元组**，绝不把行内多值信号跨行累加成计数）。"""
    return tuple(table_body_signals(r) for r in (rows or ()))


def structural_evidence(rows: Sequence[str]) -> dict:
    """逐行结构性证据（P1-2 单一原语）：**每条物理行只贡献一次**行级证据。

    旧实现把全部行的信号摊平成一个字符串列表再计数（``sum(s in ("data","columnar"))``），
    而 ``table_body_signals`` 对**同一行**同时发出 ``columnar`` 与 ``data`` —— 于是
    **一行**就被算成两条证据，``>= 2`` 因此被单行满足（真实语料里块尾折行散文"公司严格
    按照《公司法》…"即由此被误判为表格起点）。

    返回值:
      * ``strong_signal``：任一**行**含 ``unit``/``closure``/``continuation`` 强结构签名；
      * ``independent_rows``：含任一结构信号的**独立物理行数**（去重后的行级计数）；
      * ``max_columns``：结构行中的最大列数（列结构证据；2 空格切出的「2 列」在本语料
        与折行散文的两段文字在结构上不可区分，故必须与本字段一起使用）；
      * ``rows_with_signals``：逐行的信号元组（审计可见）。
    """
    strong = False
    independent = 0
    max_cols = 0
    per_row: list[list[str]] = []
    for r in (rows or ()):
        sig = table_body_signals(r)
        per_row.append(list(sig))
        if not sig:
            continue
        independent += 1
        if {"unit", "closure", "continuation"} & set(sig):
            strong = True
        max_cols = max(max_cols, len(column_cells(r)))
    return {"strong_signal": strong, "independent_rows": independent,
            "max_columns": max_cols, "rows_with_signals": per_row}


# 通用（无显式表号）表题的列结构下限：本语料以「≥2 连续空白」切列，普通折行散文的两段
# 文字同样会被切成 2 段，故 2 列**不构成**通用表题的结构充分性证据。
GENERIC_STRUCTURE_MIN_COLUMNS = 3
# 通用表题的独立结构行下限（列数较少但结构行足够多时仍可充分）。
GENERIC_STRUCTURE_MIN_ROWS = 3


def generic_structure_sufficient(evidence: dict) -> bool:
    """通用（无显式表号）表题的**结构充分性**（列结构 + 独立物理行，非标签计数）。

    - 强信号（单位行 / 合计行 / 续表标记）→ 充分；
    - 否则必须同时具备真实**列结构**与**独立行数**证据：
      ``≥ GENERIC_STRUCTURE_MIN_COLUMNS`` 列且 ``≥ 2`` 独立结构行，
      或 ``≥ GENERIC_STRUCTURE_MIN_ROWS`` 独立结构行。
    """
    ev = evidence or {}
    if ev.get("strong_signal"):
        return True
    rows = int(ev.get("independent_rows") or 0)
    cols = int(ev.get("max_columns") or 0)
    return (rows >= 2 and cols >= GENERIC_STRUCTURE_MIN_COLUMNS) or rows >= GENERIC_STRUCTURE_MIN_ROWS


def has_composite_table_signals(evidence: dict) -> bool:
    """**组合结构信号**充分性（P1-2 单一原语，取代摊平信号的计数）。

    入参必须是 :func:`structural_evidence` 的逐行证据字典（绝不接受摊平的信号列表 ——
    摊平列表正是「一行被算两次」的缺陷入口）。
    """
    return generic_structure_sufficient(evidence)


# ---------------------------------------------------------------------------
# §二 P1-A：**未闭合表结构**的结构签名（续表滚动 frontier 的确定性身份来源）
# ---------------------------------------------------------------------------
#
# 真实语料里 ``evidence_blocks`` 全部是 ``paragraph`` 块、``structured_payload`` 为空
# （769/769），因此「续表身份」**不可能**由 ``evidence_type``/``structured_payload`` 得到；
# 旧实现按 ``evidence_types=("table","table_row")`` 过滤 → 续表方向恒为空
# （``outputs=[]``），并让显式表引用被误判为 dangling。本段把「表身份」改为**结构派生**：
# 块内最后一个仍是**未闭合**结构的表 = 待续的表；其物理表头行 = 续页须重排的表头。
#
# 全部纯确定性、无公司/页码/表号硬编码、零 LLM/网络。

# 结构签名版本（落盘可复核；规则变更必须升版）。
OPEN_TABLE_SIGNATURE_VERSION = "1"

# 物理表头行上限（与恢复侧一致：分组行 + 叶子行）。
_MAX_PHYSICAL_HEADER_ROWS = 2

# 续表的决定性理由码。
CONTINUE_REASON_OK = "continues_open_table"
CONTINUE_REASON_EMPTY = "empty_block"
CONTINUE_REASON_PAGE_NUMBER = "page_number_block"
CONTINUE_REASON_PROSE = "prose_before_structure"
CONTINUE_REASON_NO_STRUCTURE = "no_table_structure"
CONTINUE_REASON_OTHER_TABLE = "different_table_title"
CONTINUE_REASON_NO_HEADER_REPEAT = "header_not_repeated"
CONTINUE_REASON_NO_CONTRIBUTION = "no_structure_contributed"


def _collapse(s: str) -> str:
    """空白归一（PDF 折行 / 跨页重排表头的空白差异按空白无关比较）。

    **仅**用于标量字段（表题 / 单位 / 闭合行）与「同一行是否相等」的结构比较；
    表对象的**内容身份**绝不使用它（见 ``canonical_cells`` / ``table_object_id``）。
    """
    return re.sub(r"\s+", "", normalize_line(s))


# ---------------------------------------------------------------------------
# §四 P2：表头层 / 首个数据行的**确定性结构裁定**（单一原语，绝不按「最多两行」位置规则）
# ---------------------------------------------------------------------------
#
# 缺陷现场：`_signature_from_region` / `_table_object_at` / `continue_table_signals` 都用
# 「``while is_columnar_row(rest[0]) and len(header_rows) < 2``」把紧随第一行表头之后的
# **首个数据行**贪心吞成第二层表头（一行表头 + 两行数据 → 表体只剩一行）。表头行数上限
# 被当成了角色判据。

# 角色裁定结果码（落盘可复核）。
HEADER_DECISION_SINGLE = "single_layer_header"
HEADER_DECISION_SUBCOLUMN = "second_layer_subcolumn_header"
HEADER_DECISION_FIRST_DATA = "second_row_is_first_body_row"
HEADER_DECISION_NONE = "no_physical_header_row"


def second_header_layer_cells(parent_cells: Sequence[str], child_cells: Sequence[str],
                              remaining_cells: Sequence[Sequence[str]]) -> bool:
    """「第二层表头（子列层）」判据（**单元格数组版**：结构侧与恢复侧共用的唯一准则）。

    §三 P1-2 / §四 P2：表头层角色只能由**确定性的结构事实**裁定，绝不按「最多吞两行」的位置
    规则贪心吞并首个数据行，也绝不因调用方拿到的是原始文本行还是已解析单元格数组而各写一套。

    判据（三条同时成立；任一不成立 ⇒ 这一行就是**首个数据行**）：

    1. 子列行是真正的多列行（去空后 ≥2 个单元）；
    2. 子列行**全部是标签单元**（不含任何数字/金额/比例/期间值）—— 真实第二层表头是列名；
    3. 其下方仍存在**比它更宽**的结构行 —— 该表体的列结构在子列行下面被真实实现，子列行
       并没有取代表体（否则「列数关系」只说明这是一条普通数据行）。

    列数关系**不**要求「子列层一定比父列层窄」：真实年报既有「合并父列 → 更窄子列」
    （``序号 名称 注册地 …`` / ``直接 间接``），也有「父列分组 → 更宽子列 + 左侧竖跨列」
    （``项目 2025年 2024年`` / ``金额 占比 金额 占比 …``）；两者都是同一结构性事实的排版
    产物，用列数**方向**区分会把后者误判成数据行。真正区分「第二层表头」与「首个数据行」
    的是**标签 vs 数字**（第 2 条）加**结构是否在下方继续**（第 3 条）。
    """
    child = [str(c) for c in (child_cells or ()) if str(c or "").strip()]
    child_cols = len(child)
    if child_cols < 2:
        return False
    if any(is_numeric_cell(c) for c in child):
        return False
    return any(len(t or ()) > child_cols for t in (remaining_cells or ()))


def _second_header_layer(parent_row: str, child_row: str,
                         remaining: Sequence[str]) -> bool:
    """``child_row`` 是否为 ``parent_row`` 的**第二层物理表头（子列层）** —— 纯结构判据。

    真实的多层表头是「合并父列 + 子列」的排版产物，其**可见**结构特征是：

    1. 子列行本身是多列行；
    2. 子列行**全部是标签单元**（不含数字/金额/比例/期间值）—— 真实第二层表头是列名，
       绝不是数据行；
    3. 该结构区**下方仍存在比子列行更宽的结构行** —— 即列结构在表体里被真实实现，子列行
       并没有取代它（否则「列数关系」只说明这是一条普通数据行）。

    判据**不**用列数方向（子列层既可窄于父列层，也可因左侧竖跨列而宽于父列层），也不用
    列位置（``normalize_line`` 会去掉行首空白，PDF 提取文本里子列行的缩进不可得）。三条
    同时成立才是子列层；任一条不成立 ⇒ 这一行就是**首个数据行**，绝不按「最多吞两行」的
    位置规则贪心吞并（``{表头, 数据, 数据}`` 必须保留两行表体）。
    """
    if not (is_columnar_row(parent_row) and is_columnar_row(child_row)):
        return False
    return second_header_layer_cells(
        column_cells(parent_row), column_cells(child_row),
        [column_cells(t) for t in (remaining or ()) if is_columnar_row(t)])


def assign_table_row_roles(rows: Sequence[str]) -> tuple[list[str], list[str], str]:
    """把结构行切成 ``(物理表头行, 其余行, 角色裁定码)`` —— **单一共享原语**。

    首行是**多列行**时必为第一层表头；其后每一行只有在满足 ``_second_header_layer``
    时才是下一层表头（多层表头允许，但**不由「最多两行」位置规则**裁定），否则即首个
    数据行。首行不是多列行 ⇒ 无物理表头（``HEADER_DECISION_NONE``）。

    调用方（结构签名 / 表对象资格 / 续表承接）**共用**本函数，绝不各自再实现一套角色规则。
    """
    r = [row for row in (rows or ())]
    if not r or not is_columnar_row(r[0]):
        return [], r, HEADER_DECISION_NONE
    header = [r.pop(0)]
    while r and len(header) < _MAX_PHYSICAL_HEADER_ROWS:
        if not _second_header_layer(header[-1], r[0], r[1:]):
            break
        header.append(r.pop(0))
    if len(header) > 1:
        return header, r, HEADER_DECISION_SUBCOLUMN
    return header, r, (HEADER_DECISION_FIRST_DATA if r else HEADER_DECISION_SINGLE)


def table_start_indices(lines: Sequence[str]) -> list[int]:
    """块内表格起点行索引（升序）：显式「表 N」行 + 通用结构表题行。"""
    norm = [normalize_line(l) for l in lines or ()]
    flags = detect_table_start_flags(norm)
    return [i for i, t in enumerate(norm)
            if t and (is_explicit_table_title(t) or flags[i])]


def _leading_structure_start(lines: Sequence[str]) -> int | None:
    """表题缺失时块首即为表结构（单位行/多列行/续表标记）的起点；否则 None。"""
    for i, raw in enumerate(lines or ()):
        t = normalize_line(raw)
        if not t:
            continue
        if is_unit_line(t) or is_continuation_marker(t) or is_columnar_row(t):
            return i
        return None
    return None


def _structural_region(lines: Sequence[str], start: int) -> tuple[list[str], bool]:
    """自 ``start`` 起的连续表结构行（跳过空行），遇正文行/另一张表题即止。

    正文行（含句读，单位行除外）是表格的天然终点：表格行不含句读。返回
    ``(结构行, 是否观察到闭合行)``；闭合行 = 合计/总计/小计行。
    """
    region: list[str] = []
    closed = False
    i = start
    while i < len(lines):
        t = normalize_line(lines[i])
        i += 1
        if not t:
            continue
        if has_prose_punct(t) and not is_unit_line(t):
            break
        if region and (is_explicit_table_title(t) or is_continuation_marker(t)):
            break
        if is_closure_row(t):
            closed = True
        region.append(t)
    return region, closed


def _signature_from_region(region: Sequence[str], *, title: str, explicit: bool,
                           start_line: int, total_lines: int,
                           closed: bool) -> dict:
    """由结构行派生签名（表题/单位/物理表头行/表体行数/是否闭合）。"""
    r = list(region)
    if title and r and _collapse(r[0]) == _collapse(title):
        r = r[1:]
    unit = ""
    if r and is_unit_line(r[0]):
        unit = r[0]
        r = r[1:]
    # §四 P2：角色裁定走**共享原语**（绝不是「最多吞两行」的位置规则）。
    header_rows, rest_rows, _decision = assign_table_row_roles(r)
    body_rows = sum(1 for t in rest_rows if is_columnar_row(t) and not is_closure_row(t))
    return {
        "version": OPEN_TABLE_SIGNATURE_VERSION,
        "start_line": start_line,
        "title": title,
        "title_explicit": bool(explicit),
        "unit": unit,
        "header_rows": header_rows,
        "body_rows": body_rows,
        "structure_rows": len(region),
        "total_lines": total_lines,
        "closed": bool(closed),
    }


def _signature_at(lines: Sequence[str], start: int) -> dict:
    norm = [normalize_line(l) for l in lines]
    title = norm[start]
    region, closed = _structural_region(norm, start)
    return _signature_from_region(region, title=title,
                                  explicit=is_explicit_table_title(title),
                                  start_line=start, total_lines=len(norm), closed=closed)


def first_table_signature(text: str | None) -> dict | None:
    """块内**首个**表结构起点的结构签名（无论是否闭合）；无表结构 → None。

    用于把「显式表引用」（见下表/如下表/下表/续表/接上表）确定性解析为「锚点之后首个
    真实表结构」，而不是按 ``evidence_type`` 过滤（真实语料中恒为空 → 误判 dangling）。
    """
    lines = [normalize_line(l) for l in (text or "").splitlines()]
    starts = table_start_indices(lines)
    explicit = [i for i in starts if is_explicit_table_title(lines[i])]
    if explicit:
        return _signature_at(lines, explicit[0])
    if starts:
        return _signature_at(lines, starts[0])
    s = _leading_structure_start(lines)
    if s is None:
        return None
    return _signature_at(lines, s)


def open_table_signature(text: str | None) -> dict | None:
    """块内**最后一个仍是未闭合结构**的表签名；无未闭合表结构 → None。

    「未闭合」= 该表结构区（自表题/块首结构行起，至正文行或另一张表题止）内未出现
    合计/总计/小计行。**显式「表 N」表题优先于通用结构表题**（强信号优先，避免通用
    结构表题在正文标题上误命中而选错起点）。

    这是续表滚动 frontier 的**唯一**身份来源：锚点块内仍有未闭合表结构 → 该表尚未读完，
    可以向前继续读取；块内已全部闭合 → 本表已结束，**绝不**继续猜读。
    """
    lines = [normalize_line(l) for l in (text or "").splitlines()]
    starts = table_start_indices(lines)
    explicit = [i for i in starts if is_explicit_table_title(lines[i])]
    if explicit:
        sig = _signature_at(lines, explicit[-1])
    elif starts:
        sig = _signature_at(lines, starts[-1])
    else:
        s = _leading_structure_start(lines)
        if s is None:
            return None
        sig = _signature_at(lines, s)
    return None if sig["closed"] else sig


def continue_table_signals(signature: dict | None, text: str | None) -> dict:
    """块是否**承接**给定未闭合表结构（逐条结构事实，供执行器/证明侧/验收侧复核）。

    fail-closed 规则（缺一不可，绝不凭「列数相同」猜续表）：

    1. 块首必须直接是表结构（单位行 / 多列行 / 续表标记 / **同一张表**的表题行）：
       块首是正文行或孤立页码行 → 不承接（正文/页码不是续表）；
    2. 块首显式表题必须是**同一张表**（空白归一后相等）或续表标记；不同表题 → 另一张表；
    3. 必须**重排本表物理表头行**（空白归一后与本表表头行有交集）。锚点只声明了表题/单位、
       物理表头尚未出现时（表头落在续页），续页块首提供的物理表头行即本表表头；
    4. 必须贡献至少一个真实结构（物理表头行 / 闭合行 / 单位行）—— 空块不得冒充续页。
    """
    lines = [normalize_line(l) for l in (text or "").splitlines()]
    nonempty = [t for t in lines if t]
    out: dict = {
        "continues": False,
        "reason": CONTINUE_REASON_EMPTY,
        "first_structural_line": nonempty[0] if nonempty else "",
        "header_candidates": [],
        "header_repeat_matched": False,
        "unit_repeated": False,
        "closure_present": False,
        "contributed_structure": False,
        "different_table_title": "",
        "signature_title": (signature or {}).get("title", ""),
    }
    if not nonempty:
        return out
    start = next(i for i, t in enumerate(lines) if t)
    first = lines[start]
    if is_page_number_line(first) and not is_unit_line(first):
        out["reason"] = CONTINUE_REASON_PAGE_NUMBER
        return out
    if has_prose_punct(first) and not is_unit_line(first):
        out["reason"] = CONTINUE_REASON_PROSE
        return out
    if is_explicit_table_title(first) or is_continuation_marker(first):
        same = _collapse(first) == _collapse((signature or {}).get("title") or "")
        if not (same or is_continuation_marker(first)):
            out["different_table_title"] = first
            out["reason"] = CONTINUE_REASON_OTHER_TABLE
            return out
    elif not (is_columnar_row(first) or is_unit_line(first)):
        out["reason"] = CONTINUE_REASON_NO_STRUCTURE
        return out
    region, closed = _structural_region(lines, start)
    out["closure_present"] = closed
    r = list(region)
    if r and (is_explicit_table_title(r[0]) or is_continuation_marker(r[0])):
        r = r[1:]
    out["unit_repeated"] = bool(r) and is_unit_line(r[0])
    if out["unit_repeated"]:
        r = r[1:]
    # §四 P2：本块重排的物理表头行同样由**共享角色原语**裁定（子列层才算第二层表头）。
    head, _head_rest, _head_decision = assign_table_row_roles(r)
    out["header_candidates"] = head
    sig_headers = {_collapse(h) for h in (signature or {}).get("header_rows") or ()}
    if sig_headers:
        out["header_repeat_matched"] = bool(sig_headers & {_collapse(h) for h in head})
    else:
        out["header_repeat_matched"] = bool(head)
    out["contributed_structure"] = bool(head) or closed or out["unit_repeated"]
    if not out["header_repeat_matched"]:
        out["reason"] = CONTINUE_REASON_NO_HEADER_REPEAT
        return out
    if not out["contributed_structure"]:
        out["reason"] = CONTINUE_REASON_NO_CONTRIBUTION
        return out
    out["continues"] = True
    out["reason"] = CONTINUE_REASON_OK
    return out


def detect_table_start_flags(lines: list[str]) -> list[bool]:
    """逐行判定「通用表题（无显式表号的表格起点）」，返回与 lines 等长的布尔表。

    显式「表 N」行**不**在此判定（调用方另行处理，它是强边界信号，无需结构跟随）。

    P1-B.2：判定**绝不**以宽泛关键词（情况/公司/业务/名称/负债…）为必要条件；表题形态
    只是必要条件，充分性必须由观察窗口内的**组合结构信号**（单位 / 表头 / 数据行 / 合计行 /
    续表标记）成立，且前驱合法、无中介表题 —— 四条同时满足才是表格起点。普通章节标题
    因此不会仅凭词形被当作表格目标（其结果区没有表体结构）。
    """
    n = len(lines)
    flags = [False] * n
    norm = [normalize_line(l) for l in lines]
    nonempty = [i for i in range(n) if norm[i]]
    for pos, i in enumerate(nonempty):
        s = norm[i]
        if not is_title_form(s):
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
        # 结构跟随 + 无中介表题：收集观察窗口内的**独立物理行**（逐行证据，不摊平计数）。
        window_rows: list[str] = []
        for j in nonempty[pos + 1:pos + 1 + TABLE_START_LOOKAHEAD]:
            t = norm[j]
            if is_explicit_table_title(t) or is_continuation_marker(t) or is_title_form(t):
                break  # 中介表题：真正的表格起点在后面，本行只是正文标题。
            # 正文行（含句读）隔断了候选行与其表体：表格行不含句读，真实表题之后**紧跟**
            # 表头区/数据行。跨块拼接使「块尾的普通章节标题」会看到**下一块**的表体结构，
            # 从而被误判为表格起点（§四.B.7：普通章节标题不得当作表格目标）；正文行必须
            # 终止观察窗口。单位行（「单位：万元」）含冒号，单独豁免。
            if has_prose_punct(t) and not is_unit_line(t):
                break
            window_rows.append(t)
        flags[i] = generic_structure_sufficient(structural_evidence(window_rows))
    return flags


# ---------------------------------------------------------------------------
# R2 定点修复（§二）：**结构性表引用目标绑定**（「见下表/如下表/下表/续表/接上表」）
# ---------------------------------------------------------------------------
#
# 缺陷现场：真实 seed 块「…情况如下表：」之后**紧邻**真正目标「表5-5 截至…主要参股及
# 联营、合营企业情况」，而旧实现**忽略发起块自身内容**，直接从后续块取「首个表结构」，
# 于是把目标错误绑到后续块的另一张表（「表5-6 发行人组织结构图」）；验收侧又只因「输出
# 属于已采纳材料」判 resolved（错误正例）。
#
# 本段把「标记之后的表对象」变为**可复核的确定性对象**：表题/单位/物理表头/表体边界 +
# 内容寻址身份（``table_object_id``），全部由既有结构原语派生。**无公司/页码/表号/
# evidence_id 专用规则**，零 LLM/网络。既有续表正向控制（``open_table_signature`` /
# ``continue_table_signals``）**不改动**。

# 绑定记录版本（落盘可复核；规则变更必须升版）。
# v13 升版 1 → 2：对象身份 canonical payload 由「行数」改为**真实表体行内容**（P1-3）。
# v14 升版 2 → 3（§三 P1-1）：对象身份 canonical payload 由「整行去空白」改为**保列单元格
# 数组**（列边界/列序/列数/单元格内容全部保留）。旧版本（"1"/"2"）的绑定记录**不被认证**
# （验收侧 fail-closed，绝不静默按新语义解读旧记录）。
REFERENCE_TARGET_BINDING_VERSION = "3"
# 表对象身份 canonical payload 版本（与绑定记录版本同步升版；缺字段/旧版本一律 fail-closed）。
REFERENCE_TARGET_OBJECT_SCHEMA_VERSION = "3"

# 绑定理由码。
REFERENCE_REASON_SAME_BLOCK = "same_block_first_verifiable_table_object_after_marker"
REFERENCE_REASON_SUBSEQUENT_BLOCK = "subsequent_block_first_verifiable_table_object_same_chapter"


def _region_indices(norm: Sequence[str], start: int) -> tuple[list[int], bool, str]:
    """自 ``start`` 起的连续表结构行**索引**（跳过空行）；返回 (索引, 是否闭合, 终止边界)。

    与 ``_structural_region`` 同一判定（正文行/另一张表题终止结构区，合计行标记闭合），
    但保留**行索引**以便换算原文绝对偏移，并额外返回**终止边界种类**：
    ``prose_line``（遇正文行）/ ``table_title``（遇另一张显式表题）/
    ``continuation_marker``（遇续表标记）/ ``block_end``（读到块末）。
    终止边界是**表对象资格**的一部分（§三.1「明确的终止边界」），且验收侧可由真实正文
    确定性重算 —— 绝不接受自报的边界。
    """
    out: list[int] = []
    closed = False
    terminator = "block_end"
    i = start
    while i < len(norm):
        t = norm[i]
        if not t:
            i += 1
            continue
        if has_prose_punct(t) and not is_unit_line(t):
            terminator = "prose_line"
            break
        if out and (is_explicit_table_title(t) or is_continuation_marker(t)):
            terminator = ("table_title" if is_explicit_table_title(t)
                          else "continuation_marker")
            break
        if is_closure_row(t):
            closed = True
        out.append(i)
        i += 1
    return out, closed, terminator


def _line_index(
    norm: Sequence[str], offsets: Sequence[int], from_offset: int,
) -> int | None:
    """自 ``from_offset`` 起首个**表结构行**的索引（无则 None）。

    表题缺失但标记后直接是结构行（单位行/多列行/续表标记）时的回退起点。
    """
    for i, t in enumerate(norm):
        if not t or offsets[i] < from_offset:
            continue
        if is_unit_line(t) or is_continuation_marker(t) or is_columnar_row(t):
            return i
        return None
    return None


def _object_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def table_object_payload(*, title: str, unit: str = "",
                         header_rows: Sequence[str] = (),
                         body_row_texts: Sequence[str] = (), closure_row: str = "",
                         structure_rows: int = 0) -> dict:
    """表对象身份 canonical payload（**单一原语**：生产侧与验收侧重算同一份）。

    §三 P1-1：**列语义必须保留**，绝不再「把整行空白删光」：

    - ``title`` / ``unit``：标量，按空白无关归一（表题/单位内部的排版空白无业务含义）；
    - ``header_rows`` / ``body_rows`` / ``closure_row``：**有序单元格数组**（``canonical_cells``）
      —— 行边界、列边界、列数、列序、单元格真实文本/金额/比例/期间/币种全部保留；
      仅单元格**内部**的业务无关空白被归一，列间距宽度差异因此不改变身份；
    - ``structure_rows``：确定性结构边界信息（结构区行数）。

    反例（旧语义的缺陷）：``A | 1 | 23`` 与 ``A | 12 | 3`` 在「整行去空白」下都成为
    ``A123`` 而撞同一身份；本 payload 下二者分别得到 ``["A","1","23"]`` 与 ``["A","12","3"]``，
    身份必然不同。验收侧由真实正文独立重算同一 payload 与身份
    （见 ``six_category_acceptance.verify_reference_binding``）。
    """
    return {
        "schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
        "title": _collapse(title or ""),
        "unit": _collapse(unit or ""),
        "header_rows": [canonical_cells(h) for h in (header_rows or ())],
        "body_rows": [canonical_cells(r) for r in (body_row_texts or ())],
        "closure_row": canonical_cells(closure_row or ""),
        "structure_rows": int(structure_rows),
    }


def table_object_id(*, title: str, unit: str = "", header_rows: Sequence[str] = (),
                    body_row_texts: Sequence[str] = (), closure_row: str = "",
                    structure_rows: int = 0) -> str:
    """表对象的**内容寻址身份**（保列规范形上的 SHA256，见 ``table_object_payload``）。"""
    return _object_digest(table_object_payload(
        title=title, unit=unit, header_rows=header_rows,
        body_row_texts=body_row_texts, closure_row=closure_row,
        structure_rows=structure_rows))


def table_object_body_payload(body_row_texts: Sequence[str] = (),
                              closure_row: str = "") -> dict:
    """表体内容 canonical payload（与 ``table_object_payload`` 同口径的**保列**表示）。"""
    return {
        "schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
        "body_rows": [canonical_cells(r) for r in (body_row_texts or ())],
        "closure_row": canonical_cells(closure_row or ""),
    }


def table_object_body_digest(body_row_texts: Sequence[str] = (),
                             closure_row: str = "") -> str:
    """表体内容摘要（§四.5「自报的表体内容摘要」）：验收侧由真实正文独立重算后比对。

    与 ``table_object_id`` 用**同一**保列规范化口径 —— 列间距/行尾空白差异不影响摘要，
    任何实际表体内容（含列边界被重排）变化都会改变摘要。
    """
    return _object_digest(table_object_body_payload(body_row_texts, closure_row))


def reference_object_binding_fields(obj: dict) -> dict:
    """表对象 → 绑定记录/材料 payload 的**目标字段投影**（生产侧与验收侧共用同一份）。

    §三 P1-4：目标对象进入材料库时必须携带**与绑定记录同一身份**的完整表对象描述
    （对象 ID、规范表题/单位/表头层/角色裁定/列数/表体行内容/表体摘要/闭合行/结构行数/
    位置与终止边界），使「绑定 → 材料/assembly/清单/验收」逐层可复核而无需重新发明字段名。
    """
    return {
        "target_object_id": obj["target_object_id"],
        "target_table_title": obj["target_table_title"],
        "target_unit": obj.get("target_unit", ""),
        "target_header_rows": list(obj.get("target_header_rows") or ()),
        "target_header_decision": obj.get("target_header_decision", ""),
        "target_column_count": int(obj.get("target_column_count") or 0),
        "target_body_row_texts": list(obj.get("target_body_row_texts") or ()),
        "target_body_rows": int(obj.get("target_body_rows") or 0),
        "target_closure_row": obj.get("target_closure_row", ""),
        "target_body_digest": obj["target_body_digest"],
        "target_structure_rows": int(obj.get("target_structure_rows") or 0),
        "target_start": int(obj["target_start"]),
        "target_end": int(obj["target_end"]),
        "target_end_boundary": obj["target_end_boundary"],
        "target_closed": bool(obj["target_closed"]),
    }


_TABLE_OBJECT_BINDING_FIELD_TYPES = (
    ("target_table_title", str), ("target_unit", str),
    ("target_header_rows", (list, tuple)), ("target_body_row_texts", (list, tuple)),
    ("target_closure_row", str), ("target_structure_rows", int),
)


def table_object_payload_from_binding(binding) -> dict | None:
    """绑定记录 → 规范保列表 payload（**单一重建原语**：生产侧与验收侧共用）。

    §三 P1-4：目标对象进入材料库、清单与验收时必须能由**绑定记录本身**重建出内容寻址的
    规范保列 payload，再重算 ``table_object_id`` 与绑定记录自报的 ID 比对 —— 这正是「目标
    对象内容寻址、可复核」的可验证形式。缺任一必需字段或类型非法 → ``None``（不猜、不用
    默认值填充：残缺记录一律不可复核）。
    """
    if not isinstance(binding, dict):
        return None
    for key, typ in _TABLE_OBJECT_BINDING_FIELD_TYPES:
        value = binding.get(key)
        if isinstance(typ, bool) or not isinstance(value, typ):
            return None
        if isinstance(value, str) and key in ("target_table_title",):
            if not value.strip():
                return None
    header_rows = [str(h) for h in binding["target_header_rows"]]
    body_rows = [str(r) for r in binding["target_body_row_texts"]]
    if not header_rows or not body_rows:
        return None
    return table_object_payload(
        title=binding["target_table_title"], unit=binding["target_unit"],
        header_rows=header_rows, body_row_texts=body_rows,
        closure_row=binding["target_closure_row"],
        structure_rows=binding["target_structure_rows"])


def select_reference_target_object(candidates: Sequence[dict]) -> tuple[dict | None, str]:
    """自候选表对象中**确定性**选出引用目标；返回 ``(对象或 None, 理由码)``。

    理由码：
      * ``no_candidate``：无候选 → 调用方按 dangling/后续块搜索处理；
      * ``unique_candidate``：唯一候选 → 即目标；
      * ``nearest_of_many``：多个候选且**最近目标唯一可判定**（标记之后源顺序最靠前的
        对象起点）→ 取最近的那个，绝不按「更像表」等其他启发式挑选；
      * ``ambiguous_tie``：多个候选的最近起点**并列**（无法确定唯一最近目标）→
        ``None``，调用方 fail-closed（绝不任意选一个）。
    """
    objs = [o for o in (candidates or ()) if isinstance(o, dict)]
    if not objs:
        return None, "no_candidate"
    first_start = min(int(o.get("target_start") or 0) for o in objs)
    nearest = [o for o in objs if int(o.get("target_start") or 0) == first_start]
    if len(nearest) != 1:
        return None, "ambiguous_tie"
    return nearest[0], ("unique_candidate" if len(objs) == 1 else "nearest_of_many")


def _table_object_at(norm: Sequence[str], offsets: Sequence[int], ends: Sequence[int],
                     start: int) -> dict | None:
    """自行 ``start`` 起的可验证表对象；**不满足资格**（§三 P1-2）→ None。

    表对象资格（缺一即不是表对象）：

    1. **结构起点可验证**：起点行是表题行（显式「表 N」或结构表题形态）或结构行；
    2. **非空结构区域**：结构区至少含一行非空结构行；
    3. **可复核的表头/列结构信号**：结构区内存在物理表头行（多列行）；且
       **通用表题**（无显式表号）还须由 :func:`generic_structure_sufficient` 用
       **独立物理行 + 列结构**证明结构充分；
    4. **至少一条真实表体行 + 明确终止边界**：表体行（多列、非合计行）≥ 1 条，且结构区由
       正文行/另一张表题/续表标记/块末**明确终止**（终止种类落盘、可独立重算）；表体行的
       列数不得超过**最宽表头层**的列数（列结构必须可解释）。
    5. **表头/表体角色**由共享原语 :func:`assign_table_row_roles` 裁定（子列层才算第二层
       表头；裁定码落盘）。

    旧实现的缺陷：(a) 起点行只要有「表题形态」+ 观察窗口里有任一多列行就被当成表对象，于是
    **折行散文**（「公司严格按照《公司法》…」这种被 PDF 折行、行内带多空格的治理文字）也
    进入候选清单（``body_rows=0``、``structure_rows=0``），并靠「正确的那个恰好排在第一位」
    被选中；(b) 结构充分性按**摊平的信号计数**判定，而同一行同时发出 ``columnar`` 与
    ``data`` → 一行就被算成两条证据。资格判定**不得**依赖候选顺序，也不得依赖任何公司/
    页码/表号/关键词。
    """
    title = norm[start]
    explicit = is_explicit_table_title(title)
    if has_prose_punct(title) and not is_unit_line(title):
        # 表题行**自身含句读**（如「表5-5 截至…主要参股及联营、合营企业情况」）：
        # 结构区必须自**下一行**起算，否则表题行会被当作正文行而立刻终止结构区
        # （既有 ``first_table_signature`` 在该情形下得到 structure_rows=0 的空表体）。
        region_idx, closed, terminator = _region_indices(norm, start + 1)
    else:
        region_idx, closed, terminator = _region_indices(norm, start)
    if not region_idx:
        return None                      # 资格 2：空结构区域 → 不是表对象
    body = [norm[i] for i in region_idx]
    if body and _collapse(body[0]) == _collapse(title):
        body = body[1:]
    # 资格 3（§三 P1-2）：**通用表题**（无显式表号）的结构充分性由**独立物理行 + 列结构**
    # 裁定（逐行证据；「2 列」在本语料与折行散文不可区分）。显式「表 N」是强边界信号，
    # 由资格 3'（表头行）+ 资格 4（真实表体行 + 终止边界）裁定。
    if not explicit and not generic_structure_sufficient(structural_evidence(body)):
        return None
    unit = body[0] if body and is_unit_line(body[0]) else ""
    # 副本语义：``rest`` 是**新列表**，角色裁定的 ``pop`` 绝不回写 ``body``，也不影响下面的
    # 表体统计（``target_structure_rows`` 与 ``target_body_rows`` 由同一不可变快照派生）。
    rest = list(body[1:] if unit else body)
    # §四 P2：表头层 / 首个数据行由**共享角色原语**裁定（子列层才算第二层表头），
    # 绝不按「最多吞两行」的位置规则贪心吞并首个数据行。
    header_rows, data_lines, header_decision = assign_table_row_roles(rest)
    data_lines = tuple(data_lines)
    body_row_texts = tuple(t for t in data_lines
                           if is_columnar_row(t) and not is_closure_row(t))
    closure_texts = tuple(t for t in data_lines if is_closure_row(t))
    if not header_rows:
        return None                      # 资格 3'：无可复核表头/列结构信号 → 不是表对象
    if not body_row_texts:
        # 资格 4：没有真实表体行（折行散文/仅有表头）→ 不是表对象。
        return None
    # 资格 4'（§三 P1-2）：表头与表体之间必须存在**可解释的列结构关系** —— 表体行的列数
    # 不得超过**最宽表头层**的列数（表体比表头还宽 ⇒ 列结构无法解释 ⇒ 不是表对象）。
    # 多层表头时比较对象是**父列层**（子列层本来就更窄，是它的细分）。
    max_header_cols = max(len(canonical_cells(h)) for h in header_rows)
    if any(len(canonical_cells(t)) > max_header_cols for t in body_row_texts):
        return None
    closure_row = closure_texts[0] if closure_texts else ""
    return {
        "binding_version": REFERENCE_TARGET_BINDING_VERSION,
        "object_schema_version": REFERENCE_TARGET_OBJECT_SCHEMA_VERSION,
        "target_table_title": title,
        "target_unit": unit,
        "target_header_rows": list(header_rows),
        # §四 P2：表头/表体角色裁定码（可复核：同一原语在验收侧重算）。
        "target_header_decision": header_decision,
        "target_column_count": max_header_cols,
        # §四：表体行**内容**（按源顺序）与摘要 —— 身份与验收复核都基于它，不只看行数。
        "target_body_row_texts": list(body_row_texts),
        "target_body_rows": len(body_row_texts),
        "target_closure_row": closure_row,
        "target_body_digest": table_object_body_digest(body_row_texts, closure_row),
        "target_structure_rows": len(body),
        # §三.1：明确终止边界（可独立重算的边界种类）。
        "target_end_boundary": terminator,
        "target_closed": bool(closed),
        "target_start": int(offsets[start]),
        "target_end": int(ends[region_idx[-1]]) if region_idx else int(ends[start]),
        "target_object_id": table_object_id(
            title=title, unit=unit, header_rows=header_rows,
            body_row_texts=body_row_texts, closure_row=closure_row,
            structure_rows=len(body)),
    }


def reference_target_table_objects(text: str | None, from_offset: int) -> list[dict]:
    """**自 ``from_offset`` 起**（结构性引用标记 occurrence 的 end）按源顺序的全部可验证表对象。

    §二.2/§二.4 的确定性判据：

    - 只考察**起始偏移 ≥ ``from_offset``** 的行 —— 标记所在行及其**之前**的表绝不可能是目标；
    - 逐 occurrence 独立求解（调用方按每个 marker 位置分别调用），不按去重标记字符串合并；
    - 「可验证表对象」= 起点行可验证（表题/结构行）+ 非空结构区 + 可复核表头/列结构信号 +
      **至少一条真实表体行** + 明确终止边界（详见 ``_table_object_at`` 资格四条）。
      **折行散文/多空格法规文本/章节标题/只有表头没有表体行的形状一律不是表对象** —— 资格
      判定与候选顺序无关（绝不靠「正确的那个排在第一位」）；
    - 返回**全部**候选（升序）而非只返回首个：多个候选由 ``select_reference_target_object``
      按「唯一最近目标」确定性选取，最近起点并列 → fail-closed。

    零公司/页码/表号硬编码。
    """
    raw = unicodedata.normalize("NFC", text or "")
    if not raw:
        return []
    norm: list[str] = []
    offsets: list[int] = []
    ends: list[int] = []
    pos = 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        norm.append(normalize_line(body))
        offsets.append(pos)
        ends.append(pos + len(body))
        pos += len(line)
    if pos < len(raw):
        body = raw[pos:]
        norm.append(normalize_line(body))
        offsets.append(pos)
        ends.append(pos + len(body))

    cand = [i for i in table_start_indices(norm) if offsets[i] >= from_offset]
    lead = _line_index(norm, offsets, from_offset)
    if lead is not None and lead not in cand:
        cand.append(lead)
        cand.sort()

    out: list[dict] = []
    for i in cand:
        obj = _table_object_at(norm, offsets, ends, i)
        if obj is not None:
            out.append(obj)
    return out


def reference_target_table_object(text: str | None, from_offset: int) -> dict | None:
    """同块内、标记之后**首个**可验证表对象；无 → None（调用方随后退到后续块有界搜索）。"""
    objs = reference_target_table_objects(text, from_offset)
    return objs[0] if objs else None
