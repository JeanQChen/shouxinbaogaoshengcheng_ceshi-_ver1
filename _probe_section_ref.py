"""只读取证：find_by_section_reference 对若干目标文本的匹配数（决定 fixture 同步方式）。

零写入：只构造临时 evidence.db（tmp 目录，测试同款 fixture），不碰 Evidence DB。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import (  # noqa: E402
    REFERENCE_KIND_NAMED,
    ReadonlyEvidenceReader,
    iter_reference_occurrences,
)

from evals.test_evidence_reader import (  # noqa: E402
    _COMPANY,
    _DOC,
    _DOCV,
    _SETV,
    _make_evidence_db,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

with tempfile.TemporaryDirectory() as td:
    db = _make_evidence_db(Path(td))
    r = ReadonlyEvidenceReader(db)
    for t in ("详见 24、所有权或使用权受到限制的资产",
              "24、所有权或使用权受到限制的资产",
              "所有权或使用权受到限制的资产",
              "24、存货",
              "详见 30、不存在的资产",
              "30、不存在的资产"):
        found = r.find_by_section_reference(_COMPANY, _DOC, _DOCV, _SETV, t)
        print(f"{t!r} → {len(found)} 匹配")
    anchor = r.get_block_at(_COMPANY, _DOC, _DOCV, _SETV, 9, 0)
    print("anchor(9,0)=", repr(anchor.text if anchor else None))
    for o in iter_reference_occurrences(anchor.text):
        print("  occ=", o.to_dict(), "kind=", o.reference_kind,
              "table_target=", REFERENCE_KIND_NAMED)
