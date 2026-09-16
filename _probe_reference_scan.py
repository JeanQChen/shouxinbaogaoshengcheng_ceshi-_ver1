"""只读取证：真实 Evidence DB 中显式引用标记块（紧凑） + 冻结 Contract aspect 覆盖。"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB = "data/evidence.db"

from harness.context_expansion import detect_reference_targets


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    print("== 含显式引用标记的块（紧凑） ==")
    for r in con.execute("SELECT evidence_id, document_id, page_number, block_index, "
                         "section_path, text FROM evidence_blocks ORDER BY document_id, "
                         "page_number, block_index"):
        ts = detect_reference_targets(r["text"])
        if not ts:
            continue
        short = [t.replace("\n", " ")[:42] for t in ts]
        print(f"  {r['document_id']} P{r['page_number']} b{r['block_index']} "
              f"eid={r['evidence_id']} sec={r['section_path']}")
        print(f"     n={len(ts)} targets={short}")
    con.close()

    print("\n== 冻结 Contract v2 topic_harness 覆盖的 aspect ==")
    import yaml
    for p in ("templates/contracts/standard_v2.yaml", "templates/contracts/standard_v3.yaml"):
        d = yaml.safe_load(Path(p).read_text(encoding="utf-8"))
        th = (d or {}).get("topic_harness")
        print(f"  {p}: topic_harness keys = {sorted(th) if isinstance(th, dict) else th}")


if __name__ == "__main__":
    main()
