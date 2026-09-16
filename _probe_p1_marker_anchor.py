"""只读取证（P1 错误绑定）：打印 seed 锚点块原文 + 标记 occurrence + 同块表结构。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.evidence_reader import ReadonlyEvidenceReader  # noqa: E402
from harness.evidence_reader import _TABLE_REFERENCE_MARKERS  # noqa: E402
from harness.table_structure import (  # noqa: E402
    first_table_signature, open_table_signature, table_start_indices, normalize_line,
    is_explicit_table_title,
)

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v11_major_subsidiaries_20260916")
ANCHOR = "c783f2277baa5eda1659bc5c3fab5d46"


def main() -> None:
    sm = json.loads((RUN / "seed_manifest.json").read_text(encoding="utf-8"))
    e = (sm.get("entries") or [])[0]
    print("seed:", str(e.get("evidence_id"))[:12], "p", e.get("page_number"),
          "blk", e.get("block_index"), e.get("document_id"), e.get("document_version"))
    print("aspect:", e.get("aspect_id"))

    reader = ReadonlyEvidenceReader()
    blk = reader.get_block(ANCHOR)
    if blk is None:
        print("<anchor block not found>")
        return
    text = blk.text or ""
    print(f"\n--- 锚点块原文（{len(text)} 字符，p{blk.page_number} blk{blk.block_index}）---")
    print(text)

    print("\n--- 标记 occurrence（逐 occurrence，非去重字符串）---")
    for m in _TABLE_REFERENCE_MARKERS:
        for hit in re.finditer(re.escape(m), text):
            print(f"  marker={m!r} start={hit.start()} end={hit.end()} "
                  f"after={text[hit.end():hit.end()+90]!r}")

    print("\n--- 块内表结构起点行 ---")
    lines = [normalize_line(l) for l in text.splitlines()]
    for i in table_start_indices(lines):
        print(f"  line {i}: explicit={is_explicit_table_title(lines[i])} {lines[i]!r}")

    print("\n--- first_table_signature ---")
    print(json.dumps(first_table_signature(text), ensure_ascii=False, indent=2))
    print("\n--- open_table_signature ---")
    print(json.dumps(open_table_signature(text), ensure_ascii=False, indent=2))

    print("\n--- 锚点之后前 6 块 ---")
    for b in reader.bounded_blocks(blk.company_id, blk.document_id, blk.document_version,
                                   blk.evidence_set_version,
                                   after=(blk.page_number, blk.block_index), limit=6):
        sig = first_table_signature(b.text or "")
        print(f"  {b.evidence_id[:12]} p{b.page_number} blk{b.block_index} "
              f"sig_title={(sig or {}).get('title')!r}")
        print(f"      {str(b.text)[:150]!r}")


if __name__ == "__main__":
    main()
