"""只读取证：v9 各 run 的 rolling outcomes 摘要（每 run 只打印 targets 的 mode/outputs/stop）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RESULTS = Path("evaluation/results")
SUFFIX = "20260916"


def main() -> None:
    for cat in ("core_competitiveness", "major_subsidiaries", "financial_notes",
                "non_300750_fixture"):
        d = RESULTS / f"r2_material_slice_r2_sixcat_v9_{cat}_{SUFFIX}"
        print("=" * 72)
        print(cat, d.as_posix())
        ro = d / "rolling_read_outcomes.json"
        if ro.exists():
            j = json.loads(ro.read_text(encoding="utf-8"))
            for t in j.get("targets", []):
                print(f"  target mode={t['mode']:20s} dir={t['direction']:28s} "
                      f"has_more={t['has_more']} batches={t['batches']} "
                      f"adopted={t['adopted']} stop={t['stop_reason']}")
            for u in j.get("direction_unread", []):
                print(f"  unread dir={u['direction']:28s} reason={u['reason']} "
                      f"stop={u['stop_reason']}")
        tr = d / "expansion_trace.jsonl"
        if tr.exists():
            for line in tr.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                s = json.loads(line)
                mode = (s.get("arguments") or {}).get("mode")
                if mode in ("explicit_reference", "table_continuation"):
                    print(f"    trace step{mode}: outputs={s.get('outputs')} "
                          f"stop={s.get('stop_reason')} "
                          f"args={json.dumps(s.get('arguments'), ensure_ascii=False)[:260]}")


if __name__ == "__main__":
    main()
