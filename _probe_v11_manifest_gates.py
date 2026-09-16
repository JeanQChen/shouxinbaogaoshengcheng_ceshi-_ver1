"""只读取证：v11 manifest 的关闭条件逐条 satisfied / 明细（§八 条件 3/6/7 与 A–D）。"""

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


def _dump(name: str, node: object, limit: int = 2500) -> None:
    print("=" * 78)
    print(name, "satisfied =", (node or {}).get("satisfied")
          if isinstance(node, dict) else None)
    s = json.dumps(node, ensure_ascii=False, indent=2)
    print(s[:limit] + ("\n… (truncated)" if len(s) > limit else ""))


def main() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cc = m.get("closure_conditions") or {}
    print("manifest:", MANIFEST)
    print("closure_conditions:")
    for k, v in cc.items():
        print(f"  {k:58s} satisfied={v.get('satisfied')}")
    print("=" * 78)
    print("closure_declaration =", m.get("closure_declaration"))
    for key in cc:
        if key.startswith(("3", "7")):
            _dump(key, cc[key], 3000)
    abcd = cc.get("5_ABCD_and_identity_p1_closed") or {}
    print("=" * 78)
    print("ABCD items:")
    for name, item in (abcd.get("items") or {}).items():
        print(f"  {name:52s} satisfied={item.get('satisfied')}")
        print("      invariants =",
              json.dumps({k: v for k, v in (item.get("invariants") or {}).items()
                          if v is False}, ensure_ascii=False))
    print("=" * 78)
    print("positive samples:")
    for k, v in (m.get("positive_capability_samples") or {}).items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:900]}")


if __name__ == "__main__":
    main()
