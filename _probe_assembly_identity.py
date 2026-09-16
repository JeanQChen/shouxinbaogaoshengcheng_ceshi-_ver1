"""临时探针（只读）：验证「独立重算 content-addressed assembly_id」对真实历史产物成立。

不修改任何产物；只读 evaluation/results 下已有 v5 run 目录。
"""
from __future__ import annotations

import hashlib
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


def _structure_identity(table: dict) -> str:
    return json.dumps(
        [table.get("title") or "", table.get("unit"),
         list(table.get("headers") or []),
         [list(r) for r in (table.get("rows") or [])],
         (list(table["total_row"]) if table.get("total_row") else None)],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _assembly_id(component, relation, discriminator=""):
    raw = json.dumps([relation, list(component), discriminator],
                     ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "asm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def main() -> None:
    for run in RUNS:
        d = ROOT / run
        asm_p = d / "assemblies.json"
        if not asm_p.exists():
            print(f"{run}: 无 assemblies.json")
            continue
        try:
            assemblies = json.loads(asm_p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"{run}: assemblies.json 不可读 {e}")
            continue
        total = len(assemblies)
        mismatch = []
        for a in assemblies:
            rel = a.get("relation")
            comp = a.get("component_material_ids") or []
            disc = ""
            if rel == "flattened_table_recovery" and "table_title" in a:
                disc = _structure_identity({
                    "title": a.get("table_title") or "",
                    "unit": a.get("unit"),
                    "headers": a.get("headers") or [],
                    "rows": a.get("rows") or [],
                    "total_row": a.get("total_row"),
                })
            rec = _assembly_id(comp, rel, disc)
            if rec != a.get("assembly_id"):
                mismatch.append((a.get("assembly_id"), rec, rel))
        print(f"{run}: assemblies={total} recompute_mismatch={len(mismatch)}")
        for m in mismatch[:3]:
            print("   mismatch:", m)


if __name__ == "__main__":
    main()
