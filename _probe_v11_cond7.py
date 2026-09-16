"""只读取证：v11 条件 7 逐类别七项 checks 何者为假（含具体观测值）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MANIFEST = Path(sys.argv[1] if len(sys.argv) > 1 else
                "evaluation/results/r2_six_category_acceptance_v11_20260916/"
                "six_category_manifest.json")


def main() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    audit = ((m.get("closure_conditions") or {})
             .get("7_inventory_boundary_unread_trace_consistent") or {}).get("audit") or {}
    for cid, row in (audit.get("per_category") or {}).items():
        checks = row.get("checks") or {}
        bad = {k: v for k, v in checks.items() if v is False}
        print("=" * 78)
        print(cid, "holds =", row.get("holds"), " bad checks =", list(bad))
        for k in bad:
            print(f"  --- {k} ---")
            print("   ",
                  k if k == "inventory" else "",
                  json.dumps(row.get(k) or row.get(
                      "continuation_and_reference_provenance"
                      if k == "continuation_and_reference_provenance" else
                      k, {}), ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
