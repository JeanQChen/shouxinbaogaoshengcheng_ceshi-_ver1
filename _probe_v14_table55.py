"""只读取证：真实 seed 块中「如下表」标记之后目标区（表5-5）的逐行单元格结构。

目的：为 P2「表头层/首个数据行」的确定性判据取真实形状（列数、列起始位置、列跨度），
以及 P1-1「保列规范化」在真实行上的表现。零写入。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import ReadonlyEvidenceReader  # noqa: E402
from harness import table_structure as TBL  # noqa: E402

SEED = "c783f2277baa5eda1659bc5c3fab5d46"
_SPLIT = re.compile(r"\s{2,}")


def spans(line: str) -> list[tuple[int, int, int, str]]:
    """(start, end, len, text) 逐列跨度（按 ≥2 连续空白切分，保留绝对列位置）。"""
    out = []
    for m in _SPLIT.finditer(line):
        pass
    pos = 0
    for part in _SPLIT.split(line):
        if not part:
            continue
        i = line.index(part, pos)
        out.append((i, i + len(part), len(part), part))
        pos = i + len(part)
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = ReadonlyEvidenceReader(Path("data/evidence.db")).get_block(SEED).text
    lines = text.splitlines(keepends=True)
    pos = 0
    print("=== 标记附近（偏移 250..470）逐行 ===")
    for line in lines:
        body = line.rstrip("\r\n")
        start, end = pos, pos + len(body)
        pos += len(line)
        if end < 250 or start > 470:
            continue
        t = TBL.normalize_line(body)
        if not t:
            continue
        cells = TBL.column_cells(t)
        sp = spans(t)
        print(f"[{start:4d}-{end:4d}] col={int(TBL.is_columnar_row(t))} "
              f"prose={int(TBL.has_prose_punct(t))} unit={int(TBL.is_unit_line(t))} "
              f"clo={int(TBL.is_closure_row(t))} n={len(cells)} "
              f"cells={cells}")
        print(f"           spans={[(a, b, c) for a, b, c, _ in sp]}")
        print(f"           raw={t!r}")


if __name__ == "__main__":
    main()
