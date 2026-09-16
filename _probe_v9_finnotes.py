"""§四 取证：只读查看 v9 financial_notes 的 seed / 扩读 trace / 引用目标检测。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v9_financial_notes_20260916")


def main() -> None:
    sm = json.loads((RUN / "seed_manifest.json").read_text(encoding="utf-8"))
    print("== seed entries ==")
    for e in sm["entries"]:
        print("  case_id=", e.get("case_id"), "| eid=", e.get("evidence_id"),
              "| P", e.get("page_number"), "b", e.get("block_index"),
              "| aspect=", e.get("aspect_id"))
        print("     section_path=", e.get("section_path"))
        print("     text=", (e.get("text") or "")[:600].replace("\n", "\\n"))

    from harness.context_expansion import _detect_reference_targets, detect_reference_targets
    for e in sm["entries"]:
        ts = detect_reference_targets(e.get("text") or "")
        print("\n  reference targets in", e.get("case_id"), "→", ts)

    print("\n== expansion_trace.jsonl ==")
    for line in (RUN / "expansion_trace.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            print("  step", d.get("step_index"), d.get("action"), "mode=",
                  (d.get("arguments") or {}).get("mode"),
                  "outputs=", d.get("outputs"), "stop=", d.get("stop_reason"))

    print("\n== rolling_read_outcomes.json ==")
    print((RUN / "rolling_read_outcomes.json").read_text(encoding="utf-8"))

    print("\n== boundary_verification.json (head) ==")
    bv = json.loads((RUN / "boundary_verification.json").read_text(encoding="utf-8"))
    print(json.dumps(bv, ensure_ascii=False)[:2500])


if __name__ == "__main__":
    main()
