"""临时探针（只读）：诊断 v6 真实产物在强化验收器下暴露的每一处矛盾。

只读 evaluation/results 下 v6 run 目录，不写任何产物。
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path("evaluation/results")
RUNS = {
    "main_business": "r2_material_slice_r2_sixcat_v6_main_business_20260916",
    "core_competitiveness": "r2_material_slice_r2_sixcat_v6_core_competitiveness_20260916",
    "major_subsidiaries": "r2_material_slice_r2_sixcat_v6_major_subsidiaries_20260916",
    "financial_notes": "r2_material_slice_r2_sixcat_v6_financial_notes_20260916",
    "non_300750_fixture": "r2_material_slice_r2_sixcat_v6_non_300750_fixture_20260916",
}


def _load(d: Path, name: str):
    p = d / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> None:
    for cid, run in RUNS.items():
        d = ROOT / run
        index = _load(d, "material_index.json") or []
        asms = _load(d, "assemblies.json") or []
        inv = _load(d, "source_object_inventory.json") or {}
        se = _load(d, "set_enumeration.json") or {}
        bd = _load(d, "boundary_decisions.json") or {}
        print("=" * 100)
        print(f"{cid}: materials={len(index)} assemblies={len(asms)}")
        for aspect, entry in se.items():
            pv = entry.get("per_version") or []
            print(f"  SE[{aspect}] supported={entry.get('material_type_supported')} "
                  f"reason={str(entry.get('reason'))[:160]}")
            for p in pv:
                r = p.get("result") or {}
                print(f"     ver={p.get('document_version')} supported={r.get('material_type_supported')} "
                      f"| {str(r.get('reason'))[:120]}")
        for aspect, entry in inv.items():
            res = entry.get("recovery_results") or []
            bad = [r for r in res if r.get("result") != "recovered_ok"]
            print(f"  INV[{aspect}] expected={len(entry.get('expected_source_objects') or [])} "
                  f"results={len(res)} non_ok={len(bad)} "
                  f"unmatched={entry.get('unmatched_recovered_tables')}")
            for r in bad:
                print(f"     {r.get('object_id')}: {r.get('result')} status={r.get('recovery_status')} "
                      f"asm={r.get('assembly_id')} reason={str(r.get('reason'))[:100]}")
        # 摊平表 assembly 认领情况
        claimed = set()
        for aspect, entry in inv.items():
            for r in (entry.get("recovery_results") or []):
                if r.get("assembly_id"):
                    claimed.add(r["assembly_id"])
        flat = [a for a in asms if a.get("relation") == "flattened_table_recovery"]
        orphans = [a for a in flat if a.get("assembly_id") not in claimed]
        print(f"  ASM flattened={len(flat)} orphans={len(orphans)}")
        for a in orphans:
            comps = a.get("component_material_ids") or []
            known = {m.get("material_id") for m in index}
            print(f"     orphan {a.get('assembly_id')} status={a.get('recovery_status')} "
                  f"title={str(a.get('table_title'))[:40]!r} comps={len(comps)} "
                  f"dangling={[c for c in comps if c not in known]}")
        # 边界决策 reason/disposition 分布
        decisions = bd.get("decisions") or []
        cnt = collections.Counter((x.get("reason_code"), x.get("disposition"))
                                  for x in decisions if isinstance(x, dict))
        print(f"  BD decisions={len(decisions)}")
        for (rc, dp), n in sorted(cnt.items(), key=lambda kv: -kv[1])[:12]:
            print(f"     {rc} / {dp}: {n}")
        # 材料里是否混入主题外标题
        for kw in ("安全生产", "在建工程", "未来规划"):
            hits = [m.get("material_id") for m in index
                    if kw in json.dumps(m, ensure_ascii=False)]
            if hits:
                print(f"  !! material_index 含关键词 {kw}: {hits[:4]}")


if __name__ == "__main__":
    main()
