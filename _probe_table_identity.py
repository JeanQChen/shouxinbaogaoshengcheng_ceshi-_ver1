"""只读取证：真实 evidence.db 的块类型分布 + 结构派生的表身份/续表候选（紧凑）。

用于 §二 P1-A：确认 ``table_continuation`` 分支按 ``evidence_types=("table","table_row")``
过滤在真实语料上是否恒为空，以及结构派生身份能否确定性地找到续块。
"""

from __future__ import annotations

import collections
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.table_structure import (  # noqa: E402
    detect_table_start_flags,
    is_explicit_table_title,
    normalize_line,
)

DB = "data/evidence.db"


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    kinds = collections.Counter()
    docs = set()
    for r in con.execute("SELECT evidence_type, structured_payload, document_id "
                         "FROM evidence_blocks"):
        kinds[r["evidence_type"]] += 1
        docs.add(r["document_id"])
    print("== 块类型分布 ==", dict(kinds), "documents=", len(docs))

    print("\n== 各块的结构派生表题（前 3 行内命中显式表题或结构表题） ==")
    for r in con.execute("SELECT evidence_id, document_id, page_number, block_index, "
                         "section_path, text FROM evidence_blocks "
                         "ORDER BY document_id, page_number, block_index"):
        lines = [normalize_line(l) for l in (r["text"] or "").splitlines()]
        hits = []
        flags = detect_table_start_flags(lines)
        for i, l in enumerate(lines):
            if not l:
                continue
            if is_explicit_table_title(l) or flags[i]:
                hits.append((i, l[:56]))
        if hits:
            print(f"  {r['document_id']} P{r['page_number']} b{r['block_index']} "
                  f"eid={r['evidence_id']} sec={r['section_path']}")
            for i, t in hits[:4]:
                tag = "EXPLICIT" if is_explicit_table_title(lines[i]) else "STRUCT  "
                print(f"     line{i:>3} {tag} {t}")
    con.close()


if __name__ == "__main__":
    main()
