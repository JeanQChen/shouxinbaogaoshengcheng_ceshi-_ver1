"""只读取证：v13 run 的源对象清单逐对象事实（P1-2/P1-4 的 target_not_obtained 现场）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RUN_DIR = Path("evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    d = json.loads((RUN_DIR / "source_object_inventory.json").read_text(encoding="utf-8"))
    for aspect, inv in d.items():
        print(f"==== aspect={aspect} version={inv.get('version')} "
              f"doc={inv.get('document_id')} recovery_source={inv.get('recovery_source')}")
        print("orphans:", json.dumps(inv.get("orphan_assemblies"), ensure_ascii=False),
              "untitled:", json.dumps(inv.get("untitled_recovered_tables"), ensure_ascii=False),
              "unmatched:", json.dumps(inv.get("unmatched_recovered_tables"), ensure_ascii=False))
        print("-- expected_source_objects --")
        for o in inv.get("expected_source_objects") or []:
            print(json.dumps({"oid": o.get("object_id"), "kind": o.get("kind"),
                              "label": str(o.get("label"))[:70],
                              "page": o.get("page_number"), "blk": o.get("block_index")},
                             ensure_ascii=False))
        print("-- recovery_results --")
        for r in inv.get("recovery_results") or []:
            print(json.dumps({"oid": r.get("object_id"), "result": r.get("result"),
                              "matched": r.get("matched_table"),
                              "aid": r.get("assembly_id"),
                              "status": r.get("recovery_status"),
                              "issue": str(r.get("issue"))[:100]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
