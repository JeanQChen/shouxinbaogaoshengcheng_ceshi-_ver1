"""只读取证：真实 evidence.db 中 表5-11 链（P50b0 → P50b1 → P51b0 → …）的结构派生续表。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.table_structure import (  # noqa: E402
    continue_table_signals,
    open_table_signature,
)

DB = "data/evidence.db"
SEED = sys.argv[1] if len(sys.argv) > 1 else "53c9b3721904922b1afa7cff95d39d22"


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    seed = con.execute("SELECT * FROM evidence_blocks WHERE evidence_id=?", (SEED,)).fetchone()
    rows = con.execute(
        "SELECT * FROM evidence_blocks WHERE document_id=? AND document_version=? "
        "AND evidence_set_version=? ORDER BY page_number, block_index",
        (seed["document_id"], seed["document_version"], seed["evidence_set_version"])).fetchall()
    idx = [i for i, r in enumerate(rows) if r["evidence_id"] == SEED][0]
    sig = open_table_signature(seed["text"] or "")
    print(f"seed {SEED} P{seed['page_number']} b{seed['block_index']}")
    print("open_table_signature =", json.dumps(sig, ensure_ascii=False))
    n = 0
    for r in rows[idx + 1:]:
        st = continue_table_signals(sig, r["text"] or "")
        print(f"  P{r['page_number']} b{r['block_index']} continues={st['continues']} "
              f"reason={st['reason']} hdr_repeat={st['header_repeat_matched']} "
              f"closure={st['closure_present']} first={st['first_structural_line'][:44]!r}")
        if not st["continues"]:
            n += 1
            if n >= 2:
                break
    con.close()


if __name__ == "__main__":
    main()
