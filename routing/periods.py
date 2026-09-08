"""期间规范化（纯函数）：把 time_scope / report_as_of 解析为可直接比较的 (year, month, day)。

禁止字符串字典序比较（如 "2025-06-30" > "2024-12-31" 碰巧成立，但 "2025-1-1" 会错）：
统一规范为三元组 (year, month, day)，三元组按位比较即语义正确。无法可靠解析或非法
日期返回 None，调用方按 TIME_SCOPE_UNPARSEABLE 交 fallback，绝不猜测。

支持形式（对齐报告期口径）：
  YYYY          → 12-31（年度）
  YYYYQ1..Q4    → 对应季度末（YYYY-Q1 等同）
  YYYYH1/H2     → 06-30 / 12-31（半年，YYYY-H1 等同）
  YYYY-MM       → 该月最后一天（calendar.monthrange）
  YYYY-MM-DD    → 对应日期（datetime 校验合法性，含闰年/31 日越界）

CLI: python -m routing.periods <value>  # 打印解析结果（冒烟自检）
"""

from __future__ import annotations

import calendar
import re
from datetime import date

CanonicalPeriod = tuple[int, int, int]

_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_HALF_END = {1: (6, 30), 2: (12, 31)}

_RE_YEAR = re.compile(r"^\d{4}$")
_RE_QUARTER = re.compile(r"^(\d{4})-?[Qq]([1-4])$")
_RE_HALF = re.compile(r"^(\d{4})-?[Hh]([12])$")
_RE_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_RE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_period(s: str | None) -> CanonicalPeriod | None:
    """解析为 (year, month, day)；无法可靠解析或非法日期返回 None。"""
    if s is None:
        return None
    t = s.strip()
    if not t:
        return None

    if _RE_YEAR.match(t):
        return (int(t), 12, 31)

    m = _RE_QUARTER.match(t)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return (y, *_QUARTER_END[q])

    m = _RE_HALF.match(t)
    if m:
        y, h = int(m.group(1)), int(m.group(2))
        return (y, *_HALF_END[h])

    m = _RE_MONTH.match(t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            return None
        try:
            return (y, mo, calendar.monthrange(y, mo)[1])
        except ValueError:
            return None

    m = _RE_DATE.match(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            date(y, mo, d)
        except ValueError:
            return None
        return (y, mo, d)

    return None


if __name__ == "__main__":
    import sys

    for v in sys.argv[1:]:
        print(f"{v!r}  ->  {parse_period(v)}")
