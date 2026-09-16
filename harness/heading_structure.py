"""P1-A.3：文档标题层级（通用、确定性、公司无关的结构权威）。

主题边界不能只看「标题里有没有主题关键词」（那会在「（四）安全生产情况」这类
**同级兄弟小节标题**上返回 ambiguous 而静默把越界内容当成主题内材料），也不能靠策略
自己的关键词自证。文档自身的编号形式已经给出了层级：同一份披露材料里，编号形式的深浅
是**文档层级**的确定性证据，与任何公司/主题词典无关。

本模块提供两件事：

1. ``numbering_level`` / ``leading_heading_level``：把编号形式映射为层级序（数字越小越浅）；
2. ``iter_heading_spans``：按出现顺序枚举文本中的标题片段（含层级与字符偏移）。

层级序（中文披露材料的通用编号习惯，确定性、可版本化）：

============================  =====
编号形式                        层级
============================  =====
``第N节`` / ``第N章``           1
``一、`` ``二、``（中文顿号）     2
``（一）`` ``（二）``            3
``1、`` ``2、``（阿拉伯顿号）     4
``（1）`` ``（2）``             5
============================  =====

**只做结构判定，不做语义判定**：层级只说明「A 标题比 B 标题更深/同深/更浅」，
是否属于当前主题仍由主题边界策略（``harness.topic_boundary``）裁决。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 层级序常量（数字越小越浅）。
LEVEL_CHAPTER = 1          # 第N节 / 第N章
LEVEL_CN_ITEM = 2          # 一、 二、
LEVEL_CN_PAREN = 3         # （一）（二）
LEVEL_ARABIC_ITEM = 4      # 1、 2、
LEVEL_ARABIC_PAREN = 5     # （1）（2）

# 逐形式的锚定正则（判定编号 token 自身的层级）。
_RE_CHAPTER = re.compile(r"^第\s*[一二三四五六七八九十百千\d]+\s*[章节]")
_RE_CN_ITEM = re.compile(r"^[一二三四五六七八九十百千]+\s*[、]")
_RE_CN_PAREN = re.compile(r"^[（(]\s*[一二三四五六七八九十百千]+\s*[）)]")
_RE_ARABIC_ITEM = re.compile(r"^\d{1,3}\s*[、]")
_RE_ARABIC_PAREN = re.compile(r"^[（(]\s*\d{1,3}\s*[）)]")

_LEVEL_FORMS: tuple[tuple[re.Pattern[str], int], ...] = (
    (_RE_CHAPTER, LEVEL_CHAPTER),
    (_RE_CN_ITEM, LEVEL_CN_ITEM),
    (_RE_CN_PAREN, LEVEL_CN_PAREN),
    (_RE_ARABIC_ITEM, LEVEL_ARABIC_ITEM),
    (_RE_ARABIC_PAREN, LEVEL_ARABIC_PAREN),
)

# 条目枚举（一是/二是/三是）→ 不是标题编号（继续同一主题）。
ITEM_ENUMERATION_RE = re.compile(r"^\s*[一二三四五六七八九十百千\d]+\s*是")

# 文本中任意位置出现的标题编号（与 topic_boundary 历史结构信号同语义）：真实材料的
# PDF 提取会把同级标题紧贴在上一段末尾（无换行、无空格），因此不能只认行首。为避免把
# 句子中间的编号（「其中 1、」「同比增长（2）」）误当标题，仅当编号**前面不是 CJK 汉字
# 或阿拉伯数字**（即位于行首、空白、或句读/括号之后）才成立，比历史 ANYWHERE 正则更严。
_SPAN_NUMBERING = re.compile(
    r"(?P<token>"
    r"第\s*[一二三四五六七八九十百千\d]+\s*[章节]"
    r"|[（(]\s*[一二三四五六七八九十百千\d]+\s*[）)]"
    r"|[一二三四五六七八九十百千]+\s*[、]"
    r"|\d{1,3}\s*[、]"
    r")")


def numbering_level(token: str) -> int | None:
    """编号 token 的层级序；未识别形式 → None（不猜）。"""
    t = unicodedata.normalize("NFC", (token or "").strip())
    if not t:
        return None
    for pattern, level in _LEVEL_FORMS:
        if pattern.match(t):
            return level
    return None


def leading_heading_level(line: str | None) -> int | None:
    """行首标题编号的层级序；该行不是标题（行首无编号 / 条目枚举）→ None。"""
    s = unicodedata.normalize("NFC", (line or "").strip())
    if not s:
        return None
    if ITEM_ENUMERATION_RE.match(s):
        return None
    for pattern, level in _LEVEL_FORMS:
        m = pattern.match(s)
        if m:
            return level
    return None


@dataclass(frozen=True)
class HeadingSpan:
    """文本中的一个标题片段（``offset`` 为编号起始字符位置）。

    ``end`` 为标题**所在行**的结束字符位置（不含换行），用于在原文中切出
    「标题之后」的主题内区域（向后方向片段投影需要它）。
    """

    offset: int
    level: int
    heading: str          # 编号所在行的完整文本（截断到 80 字符）
    end: int = 0          # 标题行结束字符位置（不含换行）；0 表示未计算


def iter_heading_spans(text: str | None) -> list[HeadingSpan]:
    """按出现顺序枚举文本中的标题片段（行首或句读之后），含层级与字符偏移。

    ``line`` 为编号所在行的完整文本（供主题/关键词分类使用，仅编号无法分类）。
    """
    s = unicodedata.normalize("NFC", text or "")
    if not s:
        return []
    spans: list[HeadingSpan] = []
    for m in _SPAN_NUMBERING.finditer(s):
        # 编号前一个字符是 CJK 汉字或阿拉伯数字 → 位于句子/数值中间，不是标题
        # （「其中 1、」「同比增长（2）」）；行首、空白、句读、括号之后均成立。
        if m.start() > 0:
            prev = s[m.start() - 1]
            if prev != "\n" and (("一" <= prev <= "鿿") or prev.isdigit()):
                continue
        level = numbering_level(m.group("token"))
        if level is None:
            continue
        line = s[m.start():].split("\n", 1)[0].strip()
        if not line:
            continue
        spans.append(HeadingSpan(offset=m.start(), level=level, heading=line[:80],
                                 end=m.start() + len(s[m.start():].split("\n", 1)[0])))
    return spans


def heading_level_of_text(text: str | None, target: str) -> int | None:
    """在文本中查找与 ``target`` 对应的标题片段并返回其层级序；未找到 → None。

    匹配口径：标题片段（去掉编号 token 后）以 ``target`` 开头，或 ``target`` 以标题
    片段开头（标题中夹带编号/缀词时的保守双向前缀匹配）。
    """
    want = unicodedata.normalize("NFC", (target or "").strip())
    if not want:
        return None
    for span in iter_heading_spans(text):
        body = unicodedata.normalize("NFC", span.heading).strip()
        stripped = _strip_leading_numbering(body)
        if not stripped:
            continue
        if stripped.startswith(want) or want.startswith(stripped):
            return span.level
    return None


def _strip_leading_numbering(line: str) -> str:
    """去掉行首编号 token，返回标题正文（如「（二）主营业务情况」→「主营业务情况」）。"""
    s = unicodedata.normalize("NFC", (line or "").strip())
    for pattern, _level in _LEVEL_FORMS:
        m = pattern.match(s)
        if m:
            return s[m.end():].strip(" \t　:：.．、")
    return s


def strip_leading_numbering(line: str) -> str:
    """公开包装：去掉行首编号 token 后的标题正文。"""
    return _strip_leading_numbering(line)


def topic_level_of(text: str | None, section_path: tuple[str, ...] | None) -> tuple[int | None, str]:
    """推断**主题小节标题**的层级序，返回 ``(level, source)``。

    ``source`` 取值：
    - ``section_path_leaf``：在文本中找到与 ``section_path`` 叶子同名的标题（最可信）；
    - ``unknown``：该文本中没有可用的层级权威（**绝不**退化为「首个标题的层级」猜测：
      猜测层级若偏浅 → 兄弟小节正文混入主题材料；偏深 → 主题内子标题被误判为兄弟标题、
      产生假失败与静默丢材料。层级未知时不做结构性关闭，由同一 aspect/文档版本的其它
      seed 文本提供权威层级，见 :func:`topic_level_from_seed_set`）。

    绝不使用固定页码 / 公司名 / 主题词典；只用文档自身编号形式。
    """
    spans = iter_heading_spans(text)
    if not spans:
        return None, "unknown"
    leaf = _topic_leaf(section_path)
    if leaf:
        leaf_body = _strip_leading_numbering(leaf)
        for span in spans:
            body = _strip_leading_numbering(span.heading)
            if not body:
                continue
            if body == leaf_body or body.startswith(leaf_body) or leaf_body.startswith(body):
                return span.level, "section_path_leaf"
    return None, "unknown"


def _topic_leaf(section_path: tuple[str, ...] | None) -> str:
    """``section_path`` 中最深的一段非空文本（主题小节标题的来源）。"""
    for seg in reversed(tuple(section_path or ())):
        if seg and str(seg).strip():
            return str(seg).strip()
    return ""


def topic_level_from_seed_set(seed_texts: tuple[str, ...] | list[str],
                              section_path: tuple[str, ...] | None
                              ) -> tuple[int | None, str]:
    """由**同一 aspect / 同一文档版本**的全部 seed 文本确定主题小节标题层级。

    主题小节标题文本由 ``section_path`` 叶子给出；只要**任一条** seed 文本真实包含该标题，
    其编号层级就是该文档自身结构给出的主题层级（同文档版本内层级唯一）。找不到 →
    ``(None, "unknown")``：无权威层级，绝不猜测、绝不虚构结构性关闭。

    确定性：按 ``seed_texts`` 顺序取首个命中（同层级时结果相同）。
    """
    leaf = _topic_leaf(section_path)
    if not leaf:
        return None, "unknown"
    for text in tuple(seed_texts or ()):
        level, source = topic_level_of(text, section_path)
        if level is not None and source == "section_path_leaf":
            return level, "aspect_seed_document_structure"
    return None, "unknown"


def sibling_or_outer(level: int | None, topic_level: int | None) -> bool:
    """该标题是否为「主题小节的同级或更浅标题」（结构性关闭信号）。

    仅当两侧层级都已知时成立；未知 → False（绝不据此关闭/撤回）。
    """
    if level is None or topic_level is None:
        return False
    return level <= topic_level


def child_of_topic(level: int | None, topic_level: int | None) -> bool:
    """该标题是否为「主题小节内的更深层级子标题」（结构性留在主题内）。"""
    if level is None or topic_level is None:
        return False
    return level > topic_level
