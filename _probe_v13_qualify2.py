"""只读调试 2：直接进 ``_table_object_at`` 看每一步为何 None。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness.table_structure as TS  # noqa: E402

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
    raw = _FIX
    norm, offsets, ends = [], [], []
    pos = 0
    for line in raw.splitlines(keepends=True):
        b = line.rstrip("\r\n")
        norm.append(TS.normalize_line(b))
        offsets.append(pos)
        ends.append(pos + len(b))
        pos += len(line)
    print("offsets:", offsets)
    print("starts:", TS.table_start_indices(norm))
    start = 2
    title = norm[start]
    print("title prose:", TS.has_prose_punct(title), "unit:", TS.is_unit_line(title))
    region_idx, closed, term = TS._region_indices(norm, start + 1)
    print("region_idx:", region_idx, "closed:", closed, "term:", term)
    body = [norm[i] for i in region_idx]
    print("body:", body)
    sig = []
    for row in body:
        sig.extend(TS.table_body_signals(row))
    print("signals:", sig, "composite:", TS.has_composite_table_signals(sig))
    unit = body[0] if body and TS.is_unit_line(body[0]) else ""
    rest = list(body[1:] if unit else body)
    header_rows = []
    while rest and TS.is_columnar_row(rest[0]) and len(header_rows) < TS._MAX_PHYSICAL_HEADER_ROWS:
        header_rows.append(rest.pop(0))
    print("unit:", repr(unit), "header_rows:", header_rows, "rest:", rest)
    data = tuple(rest)
    body_texts = tuple(t for t in data if TS.is_columnar_row(t) and not TS.is_closure_row(t))
    print("body_row_texts:", body_texts)
    obj = TS._table_object_at(norm, offsets, ends, start)
    print("object:", "None" if obj is None else obj["target_table_title"],
          json_dump(obj))


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    main()
