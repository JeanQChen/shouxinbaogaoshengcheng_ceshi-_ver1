"""只读取证：各类别 seed 的 aspect / 边界策略可用性 / 显式引用标记。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.context_expansion import detect_reference_targets
from harness.topic_boundary import topic_boundary_policy, topic_boundary_coverage

RESULTS = Path("evaluation/results")

V2_DIRS = {
    "main_business": "r2_material_slice_r2_sixcat_v2_main_business_20260915",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v2_core_competitiveness_20260915",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915",
    "financial_notes": "r2_material_slice_r2_sixcat_v2_financial_notes_20260915",
}


def main() -> None:
    print("== boundary coverage ==")
    print(json.dumps(topic_boundary_coverage().get("coverage", topic_boundary_coverage()),
                     ensure_ascii=False)[:600])
    for cat, d in V2_DIRS.items():
        p = RESULTS / d / "seed_manifest.json"
        if not p.exists():
            print(f"\n[{cat}] MISSING {p}")
            continue
        sm = json.loads(p.read_text(encoding="utf-8"))
        print(f"\n== {cat} ==")
        for e in sm["entries"]:
            asp = e.get("aspect_id") or ""
            pol = topic_boundary_policy(asp) if asp else None
            ts = detect_reference_targets(e.get("text") or "")
            print(f"  case={e.get('case_id')} P{e.get('page_number')} b{e.get('block_index')}"
                  f" aspect={asp}")
            print(f"     policy_available={getattr(pol, 'available', None)}"
                  f" topic_id={getattr(pol, 'topic_id', None)}"
                  f" reason={getattr(pol, 'reason', None)}")
            print(f"     markers={len(ts)} {[t.replace(chr(10), ' ')[:36] for t in ts]}")
            print(f"     etype={e.get('evidence_type')} sec={e.get('section_path')}")


if __name__ == "__main__":
    main()
