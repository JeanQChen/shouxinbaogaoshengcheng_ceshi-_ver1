"""只读取证：v10 core_competitiveness 的「如下表/下表」发起块结构（判定 dangling 是否诚实）。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.table_structure import (  # noqa: E402
    first_table_signature,
    is_columnar_row,
    is_explicit_table_title,
    is_unit_line,
    normalize_line,
)

DB = "data/evidence.db"
RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v10_core_competitiveness_20260916")


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    mats = json.loads((RUN / "material_index.json").read_text(encoding="utf-8"))
    ids = [m.get("component_evidence_id") for m in mats if m.get("component_evidence_id")]
    print("run materials:", len(ids))
    for eid in ids:
        row = con.execute("SELECT * FROM evidence_blocks WHERE evidence_id=?", (eid,)).fetchone()
        text = row["text"] or ""
        if "下表" not in text:
            continue
        print("=" * 78)
        print(f"eid={eid} P{row['page_number']} b{row['block_index']} sec={row['section_path']}")
        print("  first_table_signature =",
              json.dumps(first_table_signature(text), ensure_ascii=False))
        lines = [normalize_line(l) for l in text.splitlines()]
        for i, t in enumerate(lines[:45]):
            tag = "COL" if is_columnar_row(t) else ("UNIT" if is_unit_line(t) else
                                                   ("TITLE" if is_explicit_table_title(t) else ""))
            mark = " <<< 下表" if "下表" in t else ""
            print(f"   {i:>3} {tag:6s} {t[:88]}{mark}")
    con.close()


if __name__ == "__main__":
    main()
