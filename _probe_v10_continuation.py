"""只读取证：v10 各 run 的 expansion trace / rolling outcomes / material index 摘要。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RESULTS = Path("evaluation/results")


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "v10"
    for cat in ("main_business", "core_competitiveness", "major_subsidiaries",
                "financial_notes", "non_300750_fixture"):
        d = RESULTS / f"r2_material_slice_r2_sixcat_{version}_{cat}_20260916"
        print("=" * 78)
        print(cat, d.as_posix())
        if not d.exists():
            print("  MISSING")
            continue
        ro = d / "rolling_read_outcomes.json"
        if ro.exists():
            j = json.loads(ro.read_text(encoding="utf-8"))
            for t in j.get("targets", []):
                print(f"  target mode={t.get('mode'):18s} dir={t.get('direction'):34s} "
                      f"anchor={t.get('anchor_evidence_id')} target={t.get('target')!r} "
                      f"has_more={t.get('has_more')} batches={t.get('batches')} "
                      f"adopted={t.get('adopted')} stop={t.get('stop_reason')!r}")
            for u in j.get("direction_unread", []):
                print(f"  unread dir={u.get('direction'):34s} reason={u.get('reason')} "
                      f"stop={u.get('stop_reason')!r}")
        tr = d / "expansion_trace.jsonl"
        if tr.exists():
            for line in tr.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                s = json.loads(line)
                args = s.get("arguments") or {}
                mode = args.get("mode")
                if mode in ("table_continuation", "explicit_reference"):
                    print(f"    step{s.get('step_index')} seed={s.get('seed_evidence_id')} "
                          f"mode={mode} eid={args.get('evidence_id')} "
                          f"title={args.get('table_title')!r} "
                          f"ref={args.get('reference_target')!r} limit={args.get('limit')}")
                    print(f"       outputs={s.get('outputs')} stop={s.get('stop_reason')!r}")
        mi = d / "material_index.md"
        if mi.exists():
            for line in mi.read_text(encoding="utf-8").splitlines():
                if "表 5-11" in line or "表5-11" in line or "c614f024" in line:
                    print("    material_index:", line[:200])


if __name__ == "__main__":
    main()
