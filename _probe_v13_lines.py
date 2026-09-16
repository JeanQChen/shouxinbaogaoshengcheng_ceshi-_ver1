"""只读取证：真实锚点块的**逐行**结构信号明细（P1-2 修复判据的经验依据）。

打印每行：绝对偏移 / 规范化文本 / is_title_form / is_columnar_row / is_unit_line /
is_closure_row / has_prose_punct / is_explicit_table_title / is_continuation_marker，
以及 ``table_start_indices`` 的标记结果与 ``_line_index`` 的 lead 结果。
零写入。
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import (  # noqa: E402
    ReadonlyEvidenceReader,
    iter_reference_marker_occurrences,
)
from harness.table_structure import (  # noqa: E402
    _line_index,
    has_prose_punct,
    is_closure_row,
    is_columnar_row,
    is_continuation_marker,
    is_explicit_table_title,
    is_title_form,
    is_unit_line,
    normalize_line,
    table_start_indices,
)

SEED = "c783f2277baa5eda1659bc5c3fab5d46"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    reader = ReadonlyEvidenceReader(Path("data/evidence.db"))
    blk = reader.get_block(SEED)
    raw = unicodedata.normalize("NFC", (blk.text or ""))
    norm: list[str] = []
    offsets: list[int] = []
    pos = 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        norm.append(normalize_line(body))
        offsets.append(pos)
        pos += len(line)
    if pos < len(raw):
        norm.append(normalize_line(raw[pos:]))
        offsets.append(pos)

    flags = table_start_indices(norm)
    occ = iter_reference_marker_occurrences(raw)
    me = occ[0][2] if occ else 0
    lead = _line_index(norm, offsets, me)
    print(f"marker_end={me} flagged_title_indices={sorted(flags)} lead_index={lead}")
    print(f"{'idx':>4} {'off':>5} {'tit':>4} {'col':>4} {'unit':>5} {'clos':>5} "
          f"{'punct':>6} {'expl':>5} {'cont':>5}  line")
    for i, t in enumerate(norm):
        print(f"{i:>4} {offsets[i]:>5} {int(is_title_form(t)):>4} "
              f"{int(is_columnar_row(t)):>4} {int(is_unit_line(t)):>5} "
              f"{int(is_closure_row(t)):>5} {int(has_prose_punct(t)):>6} "
              f"{int(is_explicit_table_title(t)):>5} {int(is_continuation_marker(t)):>5}  "
              f"{t!r}")


if __name__ == "__main__":
    main()
