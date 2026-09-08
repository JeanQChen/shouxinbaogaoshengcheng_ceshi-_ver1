"""Eval: routing/periods.py —— 期间规范化（CanonicalPeriod=(year, month, day)）。

用法: python -m evals.test_periods

覆盖（契约修正 3 + 额外点 1）：
- YYYY / YYYYQn / YYYY-Hn / YYYY-MM / YYYY-MM-DD 的规范化；
- 非法日期（2月30日、13月、Q0、Q5）→ None；
- 无法解析（相对期间、混合含义、空串）→ None；
- 三元组可直接比较（按位比较语义正确），半年期 == 对应 6月30日。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import periods


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    good = [
        ("2024", (2024, 12, 31)),
        ("2024Q1", (2024, 3, 31)),
        ("2024-Q2", (2024, 6, 30)),
        ("2024q3", (2024, 9, 30)),
        ("2024Q4", (2024, 12, 31)),
        ("2024-H1", (2024, 6, 30)),
        ("2024h2", (2024, 12, 31)),
        ("2024-02", (2024, 2, 29)),   # 闰年 2 月最后一天
        ("2023-02", (2023, 2, 28)),   # 平年 2 月最后一天
        ("2024-01-31", (2024, 1, 31)),
    ]
    for s, expected in good:
        check(periods.parse_period(s) == expected,
              f"parse_period({s!r}) == {expected}")

    bad = [
        "2024-13", "2024-02-30", "2024Q0", "2024Q5", "2025-H3", "2024-00",
        "2024-04-31", "过去三年", "近期", "2024年", "", None, "2024Q",
    ]
    for s in bad:
        check(periods.parse_period(s) is None,
              f"parse_period({s!r}) is None（非法/无法解析）")

    # 三元组按位比较即语义正确。
    check(periods.parse_period("2025-H1") == periods.parse_period("2025-06-30"),
          "2025-H1 == 2025-06-30（半年期末 = 6月30日）")
    check(periods.parse_period("2026Q1") > periods.parse_period("2025-06-30"),
          "2026Q1 > 2025-06-30")
    check(periods.parse_period("2024Q1") < periods.parse_period("2024"),
          "2024Q1 < 2024（季度早于年度期末）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
