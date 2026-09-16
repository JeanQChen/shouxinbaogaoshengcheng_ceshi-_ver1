"""只读取证：v13 run 各产物文件的真实 JSON 形状（顶层键 + 单项样例）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RUN_DIR = Path("evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for name in ("material_index.json", "assemblies.json", "aspect_links.json",
                 "source_object_inventory.json"):
        p = RUN_DIR / name
        if not p.exists():
            print(f"---- {name}: 缺失 ----")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        print(f"---- {name} ----")
        if isinstance(d, dict):
            print("顶层键:", list(d.keys()))
            for k, v in d.items():
                if isinstance(v, list) and v:
                    print(f"  [{k}] len={len(v)} sample=", json.dumps(v[0], ensure_ascii=False)[:900])
                elif not isinstance(v, (list, dict)):
                    print(f"  [{k}] =", json.dumps(v, ensure_ascii=False)[:200])
                elif isinstance(v, dict):
                    print(f"  [{k}] keys=", list(v.keys())[:20])
        else:
            print("list len=", len(d), json.dumps(d[0], ensure_ascii=False)[:900])


if __name__ == "__main__":
    main()
