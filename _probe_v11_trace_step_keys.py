"""只读取证：expansion_trace.jsonl 步骤记录的真实字段集合（确认 ``adopted`` 是否存在）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v11_main_business_20260916")


def main() -> None:
    for ln in (RUN / "expansion_trace.jsonl").read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        s = json.loads(ln)
        print(f"action={s.get('action')!r} mode={ (s.get('arguments') or {}).get('mode')!r}")
        print("  keys:", sorted(s.keys()))
        print("  has 'adopted':", "adopted" in s,
              "| has 'block_outcomes':", "block_outcomes" in s)
        if "block_outcomes" in s:
            print("  block_outcomes:", json.dumps(s["block_outcomes"], ensure_ascii=False)[:400])
        if "outputs" in s:
            print("  outputs:", [str(o)[:12] for o in (s.get("outputs") or [])])
        print()


if __name__ == "__main__":
    main()
