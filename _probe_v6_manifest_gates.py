"""临时探针（只读）：打印 v6 六类验收清单的逐类失败门与三轴状态。

只读 evaluation/results/r2_six_category_acceptance_v6_20260916/six_category_manifest.json，
不写任何产物。
"""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(
    "evaluation/results/r2_six_category_acceptance_v6_20260916/six_category_manifest.json")


def main() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print("top keys:", sorted(data.keys()))
    cats = data.get("categories") or data.get("results") or []
    if isinstance(cats, dict):
        cats = [dict(v, category_id=k) for k, v in cats.items()]
    for c in cats:
        cid = c.get("category_id") or c.get("case_id") or c.get("category")
        print("=" * 100)
        print(f"{cid}: material_state={c.get('material_state')} "
              f"verdict={c.get('capability_verdict')} impact={c.get('report_impact')} "
              f"compat={c.get('verdict')}")
        print(f"  reason={str(c.get('reason'))[:200]}")
        print(f"  honest_gap_reason={c.get('honest_gap_reason')}")
        fg = c.get("failed_gates") or []
        print(f"  failed_gates={len(fg)}")
        for g in fg:
            if isinstance(g, dict):
                print(f"     {g.get('gate_id')}: {str(g.get('detail') or g.get('reason'))[:220]}")
            else:
                print(f"     {g}")
        pg = c.get("passed_gates") or []
        print(f"  passed_gates={len(pg)}")


if __name__ == "__main__":
    main()
