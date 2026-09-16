"""只读取证：真实 seed 块内逐行结构判定 + 通用表题 flag 的观察窗口取证。

用于设计「通用表题（无显式表号）资格」规则：治理类折行散文必须在 **该规则的判据上**
可证伪，而真实表题（表5-5 等）必须保留。零写入。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import ReadonlyEvidenceReader  # noqa: E402
from harness import table_structure as TBL  # noqa: E402

SEED = "c783f2277baa5eda1659bc5c3fab5d46"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = ReadonlyEvidenceReader(Path("data/evidence.db")).get_block(SEED).text
    lines = [TBL.normalize_line(l) for l in text.splitlines()]
    flags = TBL.detect_table_start_flags(lines)
    starts = TBL.table_start_indices(lines)
    print("=== 逐行 ===")
    for i, t in enumerate(lines):
        if not t:
            continue
        print(f"[{i:3d}] flag={int(flags[i])} start={int(i in starts)} "
              f"form={int(TBL.is_title_form(t))} col={int(TBL.is_columnar_row(t))} "
              f"exp={int(TBL.is_explicit_table_title(t))} "
              f"cont={int(TBL.is_continuation_marker(t))} "
              f"unit={int(TBL.is_unit_line(t))} prose={int(TBL.has_prose_punct(t))} "
              f"| {t[:64]}")
    print("=== 通用表题 flag 的观察窗口逐行信号 ===")
    nonempty = [i for i, t in enumerate(lines) if t]
    for pos, i in enumerate(nonempty):
        if not flags[i]:
            continue
        print(f"--- flag line [{i}] {lines[i][:60]!r}")
        rows = 0
        for j in nonempty[pos + 1:pos + 1 + TBL.TABLE_START_LOOKAHEAD]:
            t = lines[j]
            sig = TBL.table_body_signals(t)
            rows += 1 if sig else 0
            print(f"    [{j}] sig={list(sig)} prose={int(TBL.has_prose_punct(t))} "
                  f"| {t[:64]}")
        print(f"    -> 窗口行数(有信号)={rows}，组合充分性="
              f"{TBL.has_composite_table_signals([s for j in nonempty[pos+1:pos+1+TBL.TABLE_START_LOOKAHEAD] for s in TBL.table_body_signals(lines[j])])}")


if __name__ == "__main__":
    main()
