"""只读取证：v13 run 的 P1-4 现场事实（assembly / material / aspect link / source inventory）。

零写入：只读 run 目录。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RUN_DIR = Path("evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916")


def _j(name: str):
    return json.loads((RUN_DIR / name).read_text(encoding="utf-8"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asm = _j("assemblies.json")
    items = asm.get("assemblies") if isinstance(asm, dict) else asm
    print("---- assemblies：relation 分布 ----")
    print(json.dumps(Counter(a.get("relation") for a in items), ensure_ascii=False))
    print("---- assemblies：含「表5-5」或 title 为表号的项 ----")
    for a in items:
        t = str(a.get("table_title") or "")
        if "5-5" in t or "5-6" in t or (t and "表" in t):
            print(json.dumps({k: a.get(k) for k in
                              ("assembly_id", "relation", "table_title", "recovery_status",
                               "component_material_ids", "headers", "rows")},
                             ensure_ascii=False)[:600])
    print("---- assemblies 全量精简 ----")
    for a in items:
        print(json.dumps({"aid": a.get("assembly_id"), "rel": a.get("relation"),
                          "title": a.get("table_title"), "st": a.get("recovery_status"),
                          "comps": a.get("component_material_ids")}, ensure_ascii=False))

    inv = _j("source_object_inventory.json")
    print("---- source_object_inventory ----")
    print(json.dumps({k: inv.get(k) for k in
                      ("aspect_id", "version", "document_id", "recovery_source",
                       "orphan_assemblies", "untitled_recovered_tables",
                       "unmatched_recovered_tables")}, ensure_ascii=False))
    for o in inv.get("expected_source_objects") or []:
        print(json.dumps({"oid": o.get("object_id"), "kind": o.get("kind"),
                          "label": str(o.get("label"))[:60]}, ensure_ascii=False))
    for r in inv.get("recovery_results") or []:
        print(json.dumps({"oid": r.get("object_id"), "result": r.get("result"),
                          "matched": r.get("matched_table"), "aid": r.get("assembly_id"),
                          "issue": str(r.get("issue"))[:80]}, ensure_ascii=False))

    mi = _j("material_index.json")
    mats = mi.get("materials") if isinstance(mi, dict) else mi
    print("---- material_index ----")
    print(json.dumps(Counter(m.get("material_type") for m in mats), ensure_ascii=False))
    for m in mats:
        print(json.dumps({"mid": m.get("material_id"), "type": m.get("material_type"),
                          "eid": (m.get("authority_assessment") or {}).get("evidence_id")},
                         ensure_ascii=False))

    al = _j("aspect_links.json")
    links = al.get("aspect_links") if isinstance(al, dict) else al
    print("---- aspect_links ----")
    for l in links:
        print(json.dumps({"aspect": l.get("aspect_id"), "role": l.get("role"),
                          "mids": l.get("material_ids")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
