"""只读调试：为什么 fixture 表对象在新资格规则下变成 None（逐行信号）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.table_structure import (  # noqa: E402
    _collapse,
    has_composite_table_signals,
    has_prose_punct,
    is_closure_row,
    is_columnar_row,
    is_explicit_table_title,
    is_unit_line,
    normalize_line,
    reference_target_table_objects,
    table_body_signals,
    table_start_indices,
)

_FIX = (
    "截至 2025年12月末，发行人主要参股及联营、合营企业情况如下表：\n"
    "\n"
    "表5-5主要参股及联营、合营企业情况\n"
    "  序号  企业名称  注册地  持股比例\n"
    "  1  A公司  洛阳市  24.9%\n"
)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = _FIX
    norm = [normalize_line(l) for l in text.splitlines()]
    starts = table_start_indices(norm)
    print("table_start_indices:", starts)
    for i, t in enumerate(norm):
        sig = table_body_signals(t)
        print(f"[{i}] title={is_explicit_table_title(t)} col={is_columnar_row(t)} "
              f"closure={is_closure_row(t)} unit={is_unit_line(t)} prose={has_prose_punct(t)} "
              f"| {t!r}")
        print(f"     signals={sig} composite={has_composite_table_signals(sig)}")
    objs = reference_target_table_objects(text, 13)
    print("candidates:", len(objs))
    for o in objs:
        print("  ", _collapse(o["target_table_title"]), o["target_body_rows"],
              o["target_structure_rows"], o["target_header_rows"], o["target_end_boundary"])


if __name__ == "__main__":
    main()
