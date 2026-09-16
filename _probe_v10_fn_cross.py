"""只读取证：fn_cross fixture 的续页证明链为何未构成正向（P1-B 复算）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.six_category_acceptance import (  # noqa: E402
    _continuation_proof_chain_complete,
    verify_category,
)
from evals.test_six_category_acceptance import (  # noqa: E402
    CATEGORY_FINANCIAL_NOTES,
    _assembly,
    _write_run_dir,
)
import evals.test_six_category_acceptance as T  # noqa: E402

NOTES_ASPECT = "financial_notes"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        _write_run_dir(base, "fn_cross", aspect_id=NOTES_ASPECT,
                       document_id="NDSD_2024_year",
                       materials=("m1", "m2", "m3"), topic_boundary=True,
                       assemblies=[
                           _assembly("flattened_table_recovery",
                                     "表 1 主营业务收入构成表", "ok",
                                     components=("m1", "m2"),
                                     continuation_valid=True, component_order=True),
                           _assembly("cross_page", "表 1 主营业务收入构成表", "ok",
                                     components=("m2", "m3"))])
        v = verify_category(CATEGORY_FINANCIAL_NOTES, base / "fn_cross")
        print("material_state =", v.material_state)
        print("capability     =", v.capability_verdict)
        print("verdict        =", v.verdict)
        print("failed_gates   =", json.dumps([list(g) for g in v.failed_gates],
                                              ensure_ascii=False, indent=2))
        print("continuation_proof_count =", v.facts.get("continuation_proof_count"))
        for row in (v.facts.get("recovered_table_detail") or []):
            p = row.get("continuation_proof") or {}
            print("-" * 70)
            print("table_title      =", json.dumps(row.get("table_title"),
                                                   ensure_ascii=False))
            print("boundary_desc    =", row.get("boundary_desc"))
            print("chain_complete   =", _continuation_proof_chain_complete(p))
            print("proof =", json.dumps(p, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
