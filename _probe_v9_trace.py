"""§二 取证：只读查看 v9 main_business 的 expansion trace / rolling outcomes。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v9_main_business_20260916")


def main() -> None:
    sm = json.loads((RUN / "seed_manifest.json").read_text(encoding="utf-8"))
    print("== seed entries ==")
    for e in sm["entries"]:
        print("  case_id=", e.get("case_id"), "| eid=", e.get("evidence_id"),
              "| P", e.get("page_number"), "b", e.get("block_index"),
              "| type=", e.get("evidence_type"))
        print("     section_path=", e.get("section_path"))
        print("     text[0:300]=", (e.get("text") or "")[:300].replace("\n", "\\n"))

    print("\n== rolling_read_outcomes.json ==")
    print((RUN / "rolling_read_outcomes.json").read_text(encoding="utf-8"))

    print("\n== expansion_trace.jsonl ==")
    for line in (RUN / "expansion_trace.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        print("-- seed_eid=", d.get("seed_evidence_id"), "case_id=", d.get("case_id"),
              "stop=", d.get("stop_reason"), "adopted=", len(d.get("adopted") or []))
        for st in (d.get("steps") or []):
            print("     step", st.get("step_index"), st.get("action"),
                  "outputs=", st.get("outputs"), "stop=", st.get("stop_reason"))
            print("        inputs=", json.dumps(st.get("inputs"), ensure_ascii=False)[:400])
            print("        block_outcomes=", st.get("block_outcomes"))


if __name__ == "__main__":
    main()
