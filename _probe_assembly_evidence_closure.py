"""临时探针（只读）：验证 assembly 的 evidence id 是否落在 component 材料集合内。

只读 evaluation/results 下已有 v5 run 目录，不写任何产物。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path("evaluation/results")
RUNS = [
    "r2_material_slice_r2_sixcat_v5_main_business_20260916c",
    "r2_material_slice_r2_sixcat_v5_core_competitiveness_20260916c",
    "r2_material_slice_r2_sixcat_v5_major_subsidiaries_20260916c",
    "r2_material_slice_r2_sixcat_v5_financial_notes_20260916c",
    "r2_material_slice_r2_sixcat_v5_non_300750_fixture_20260916c",
    "r2_material_slice_r2_p0_credit_v5_20260916c",
]


def main() -> None:
    for run in RUNS:
        d = ROOT / run
        mi_p, as_p = d / "material_index.json", d / "assemblies.json"
        if not mi_p.exists() or not as_p.exists():
            print(f"{run}: 缺产物")
            continue
        index = json.loads(mi_p.read_text(encoding="utf-8"))
        assemblies = json.loads(as_p.read_text(encoding="utf-8"))
        ev_to_mat = {m.get("component_evidence_id"): m.get("material_id")
                     for m in index if m.get("component_evidence_id")}
        unresolved = []
        not_member = []
        for a in assemblies:
            comp = set(a.get("component_material_ids") or [])
            eids = []
            if a.get("header_evidence_id") is not None:
                eids.append(("header", a.get("header_evidence_id")))
            eids += [("body", e) for e in (a.get("body_evidence_ids") or [])]
            eids += [("cont", e) for e in (a.get("continuation_evidence_ids") or [])]
            for kind, eid in eids:
                if not eid:
                    continue
                mid = ev_to_mat.get(eid)
                if mid is None:
                    unresolved.append((a.get("assembly_id"), kind, eid))
                elif mid not in comp:
                    not_member.append((a.get("assembly_id"), a.get("relation"), kind, eid, mid))
        print(f"{run}: assemblies={len(assemblies)} evidence_unresolved={len(unresolved)} "
              f"evidence_not_member={len(not_member)}")
        for row in (unresolved + not_member)[:4]:
            print("   ", row)


if __name__ == "__main__":
    main()
