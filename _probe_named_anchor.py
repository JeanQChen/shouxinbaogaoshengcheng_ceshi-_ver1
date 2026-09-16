"""只读取证：候选命名引用发起句的 typed occurrence 与其目标匹配数（选唯一可解析的 fixture 锚点）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import (  # noqa: E402
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

CANDS = [
    "详见风险因素",
    "详见 风险因素",
    "关于风险因素的说明详见风险因素",
    "详见 24、存货",
    "详见所有权或使用权受到限制的资产",
    "详见 24、所有权或使用权受到限制的资产",
    "详见 24、所有权或使用权受到限制的资产及相关附注",
]

with tempfile.TemporaryDirectory() as td:
    db = _make_evidence_db(Path(td))
    r = ReadonlyEvidenceReader(db)
    for t in CANDS:
        occs = iter_reference_occurrences(t)
        print(f"--- {t!r}")
        if not occs:
            print("    (无 occurrence)")
        for o in occs:
            found = r.find_by_section_reference(
                _COMPANY, _DOC, _DOCV, _SETV, o.declared_target)
            print(f"    occ={o.to_dict()} kind={o.reference_kind} 匹配={len(found)}")
