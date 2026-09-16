"""只读取证：P50/P51 块的 structured_payload 与完整文本（只 SELECT，不写）。"""

from __future__ import annotations

import json
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = "data/evidence.db"


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    ids = ("53c9b3721904922b1afa7cff95d39d22", "d1606fc111d0de496c337714ddbb3dfa",
           "c614f024a06bb87fdefa127200b2d503")
    for eid in ids:
        r = con.execute("SELECT * FROM evidence_blocks WHERE evidence_id=?", (eid,)).fetchone()
        print("=" * 70)
        print(eid, "P", r["page_number"], "b", r["block_index"], r["evidence_type"])
        print("structured_payload:", r["structured_payload"])
        print("quality_flags:", r["quality_flags"])
        print("-- text --")
        print(r["text"])
    con.close()


if __name__ == "__main__":
    main()
