"""只读取证：v11 financial_notes run 的显式引用链条 —— 为什么没有任何
``mode=explicit_reference`` 的尝试（§四 P1-C 定点诊断）。

只读：读 run 目录真实产物 + 对真实 seed/payload 文本调用生产侧
``detect_reference_targets``（不触发扩读、不写任何东西）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.context_expansion import detect_reference_targets
from harness.six_category_acceptance import _read_jsonl_failclosed  # noqa: E402

RUN = Path(sys.argv[1] if len(sys.argv) > 1 else
           "evaluation/results/r2_material_slice_r2_sixcat_v11_financial_notes_20260916")


def _load(name: str):
    p = RUN / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    print("run:", RUN)
    trace, err = _read_jsonl_failclosed(RUN, "expansion_trace.jsonl")
    print("read err:", err)
    rows = [r for r in trace if isinstance(r, dict)]
    print("trace rows:", len(rows))
    from collections import Counter
    print("actions:", Counter(r.get("action") for r in rows))
    modes = Counter((r.get("arguments") or {}).get("mode") for r in rows
                    if r.get("action") == "inspect_bounded")
    print("inspect_bounded modes:", dict(modes))
    print("stop reasons:", Counter(r.get("stop_reason") for r in rows))
    for r in rows[:40]:
        print("  ", json.dumps({k: r.get(k) for k in (
            "step_index", "action", "seed_evidence_id", "anchor_evidence_id",
            "outputs", "stop_reason")}, ensure_ascii=False)[:300],
              "args=", json.dumps(r.get("arguments") or {}, ensure_ascii=False)[:200])

    budget = _load("budget_profile.json") or {}
    print("=" * 78)
    print("budget_profile seed records:")
    for rec in (budget.get("seed_budget") or budget.get("seeds") or []):
        print("  ", json.dumps(rec, ensure_ascii=False)[:500])

    seeds = _load("seed_manifest.json") or {}
    entries = seeds.get("seeds") or seeds.get("entries") or []
    print("=" * 78)
    print("seed entries:", len(entries))
    for e in entries:
        text = str(e.get("text") or "")
        hits = detect_reference_targets(text)
        print("  eid=", e.get("evidence_id"), "case=", e.get("case_id"),
              "page=", e.get("page_number"), "hits=", hits)
        if hits:
            for h in hits:
                idx = text.find(str(h.get("target") or h.get("raw") or ""))
                print("       …", text[max(0, idx - 60):idx + 80].replace("\n", "⏎"))


if __name__ == "__main__":
    main()
