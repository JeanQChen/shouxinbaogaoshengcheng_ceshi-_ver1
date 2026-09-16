"""只读取证：显式引用「真实执行」的原始证据（供 run 绑定决策，不写任何产物）。

对指定 run 打印：
- seed 正文与 ``detect_reference_targets`` 命中；
- trace 里每个 ``mode=explicit_reference`` 步骤的锚点/参数/outputs/停止原因/预算；
- outputs 指向的 evidence 是否真的被采纳（material_index / aspect_membership）；
- 目标材料 payload 正文预览。

只读。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.context_expansion import detect_reference_targets

RESULTS = Path("evaluation/results")


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 只读诊断
        return {"__error__": str(exc)}


def main() -> None:
    names = sys.argv[1:]
    for name in names:
        run = RESULTS / name
        print("=" * 78)
        print(name)
        if not run.is_dir():
            print("  <not found>")
            continue

        sm = _load(run / "seed_manifest.json") or {}
        for e in (sm.get("entries") or []):
            text = str(e.get("text") or "")
            hits = detect_reference_targets(text)
            print("  SEED", str(e.get("evidence_id"))[:12], "p", e.get("page_number"),
                  "hits:", hits)
            if hits:
                print("       text:", text[:160].replace("\n", "⏎"))

        steps = []
        trace_path = run / "expansion_trace.jsonl"
        if trace_path.exists():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    steps.append(json.loads(line))
        for s in steps:
            if s.get("mode") != "explicit_reference":
                continue
            print("  XREF-STEP idx", s.get("step_index"),
                  "seed", str(s.get("seed_evidence_id"))[:12],
                  "anchor", str(s.get("anchor_evidence_id"))[:12])
            print("       args:", json.dumps(s.get("arguments"), ensure_ascii=False))
            print("       outputs:", [str(o)[:12] for o in (s.get("outputs") or [])],
                  "| adopted:", [str(o)[:12] for o in (s.get("adopted") or [])])
            print("       stop_reason:", s.get("stop_reason"),
                  "| budget_after:", s.get("budget_after") or s.get("budget"))

        mi = _load(run / "material_index.json") or {}
        if isinstance(mi, dict):
            entries = mi.get("entries") or mi.get("materials") or []
        else:
            entries = mi if isinstance(mi, list) else []
        by_id = {str(x.get("evidence_id") or x.get("material_id")): x for x in entries
                 if isinstance(x, dict)}
        am = _load(run / "aspect_membership.json") or {}
        print("  material_index entries:", len(entries),
              "| aspect_membership keys:", list(am.keys())[:4])

        prev = run / "payload_preview"
        if prev.is_dir():
            for f in sorted(prev.glob("*.json")):
                doc = _load(f)
                items = doc if isinstance(doc, list) else [doc]
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    text = str(it.get("text") or it.get("content") or "")
                    print("  MAT", f.stem[:12], "in_index:",
                          f.stem[:12] in by_id or any(f.stem.startswith(k) for k in by_id),
                          "|", text[:80].replace("\n", "⏎"))


if __name__ == "__main__":
    main()
