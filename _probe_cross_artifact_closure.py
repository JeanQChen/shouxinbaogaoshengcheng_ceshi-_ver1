"""临时探针（只读）：验证 aspect_links / membership / material_index 的闭合关系。

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

ROLES = ("source", "supporting", "context_candidate")


def main() -> None:
    for run in RUNS:
        d = ROOT / run
        index = json.loads((d / "material_index.json").read_text(encoding="utf-8"))
        links = json.loads((d / "aspect_links.json").read_text(encoding="utf-8"))
        mem = json.loads((d / "aspect_membership.json").read_text(encoding="utf-8"))
        mids = {m["material_id"] for m in index}
        prob = []
        # link 结构 + FK + 唯一性
        seen = set()
        for l in links:
            mid, asp, role = l.get("material_id"), l.get("aspect_id"), l.get("role")
            if mid not in mids:
                prob.append(f"link FK 悬空 {mid}")
            if role not in ROLES:
                prob.append(f"link role 非法 {role!r}")
            if (asp, mid) in seen:
                prob.append(f"link 重复 ({asp},{mid})")
            seen.add((asp, mid))
        # membership ↔ links 闭合
        for asp, entry in mem.items():
            formal = set(entry.get("formal_material_ids") or [])
            ctx = set(entry.get("context_candidate_material_ids") or [])
            src_links = {l["material_id"] for l in links
                         if l.get("aspect_id") == asp and l.get("role") == "source"}
            ctx_links = {l["material_id"] for l in links
                         if l.get("aspect_id") == asp and l.get("role") == "context_candidate"}
            if formal != src_links:
                prob.append(f"membership formal != link source ({len(formal)} vs {len(src_links)})")
            if ctx != ctx_links:
                prob.append(f"membership ctx != link ctx ({len(ctx)} vs {len(ctx_links)})")
            if formal & ctx:
                prob.append("membership formal ∩ context 非空")
            if not formal <= mids:
                prob.append("membership formal 含悬空 material_id")
            idx_aspects = {m["material_id"] for m in index
                           if asp in (m.get("aspect_ids") or [])}
            if not (formal | ctx) <= idx_aspects:
                prob.append("membership 材料不在 material_index aspect_ids 内")
        print(f"{run}: links={len(links)} aspects={len(mem)} problems={len(prob)}")
        for p in prob[:5]:
            print("   ", p)


if __name__ == "__main__":
    main()
