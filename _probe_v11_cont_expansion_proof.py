"""只读取证：v11 各 run 的 ``mode=table_continuation`` 步骤是否真的输出并采纳了续页材料。

逐 run、逐 seed 打印：锚点 evidence_id / 参数 / outputs / adopted / stop_reason / budget，
并给出「同一 seed 的续表步骤 outputs 非空且 adopted 非空」的原始计数（不猜、不聚合）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RESULTS = Path("evaluation/results")
RUNS = [
    "r2_material_slice_r2_sixcat_v11_main_business_20260916",
    "r2_material_slice_r2_sixcat_v11_core_competitiveness_20260916",
    "r2_material_slice_r2_sixcat_v11_major_subsidiaries_20260916",
    "r2_material_slice_r2_sixcat_v11_financial_notes_20260916",
    "r2_material_slice_r2_sixcat_v11_non_300750_fixture_20260916",
]


def main() -> None:
    for name in RUNS:
        run = RESULTS / name
        print("=" * 78)
        print(name)
        tp = run / "expansion_trace.jsonl"
        if not tp.exists():
            print("  <no expansion_trace.jsonl>")
            continue
        steps = [json.loads(ln) for ln in tp.read_text(encoding="utf-8").splitlines() if ln.strip()]

        def _mode(s: dict) -> str:
            # trace 的 mode 落在 arguments.mode（与生产侧 _mode() 同一读取口径）。
            return str((s.get("arguments") or {}).get("mode") or s.get("mode") or "")

        def _anchor(s: dict) -> str:
            return str(s.get("anchor_evidence_id")
                       or (s.get("arguments") or {}).get("evidence_id") or "")

        print(f"  steps={len(steps)}")
        for s in steps:
            print(f"  STEP idx={s.get('step_index')} action={s.get('action')} mode={_mode(s)!r} "
                  f"seed={str(s.get('seed_evidence_id'))[:12]} anchor={_anchor(s)[:12]}")
        positives = 0
        for s in steps:
            if _mode(s) != "table_continuation":
                continue
            outs = s.get("outputs") or []
            adopted = s.get("adopted") or []
            if outs and adopted:
                positives += 1
            print(f"  TC idx={s.get('step_index')} case={s.get('case_id')} "
                  f"seed={str(s.get('seed_evidence_id'))[:12]} "
                  f"anchor={_anchor(s)[:12]}")
            print(f"      args={json.dumps(s.get('arguments'), ensure_ascii=False)}")
            print(f"      outputs={[str(o)[:12] for o in outs]} "
                  f"adopted={[str(o)[:12] for o in adopted]}")
            print(f"      stop_reason={s.get('stop_reason')!r}")
        print(f"  → table_continuation 步骤中 outputs 非空且 adopted 非空 的条数 = {positives}")

        mi = run / "material_index.json"
        if mi.exists():
            try:
                m = json.loads(mi.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"  material_index 读取失败: {exc}")
                continue
            entries = m if isinstance(m, list) else (m.get("entries") or [])
            print(f"  material_index entries={len(entries)}")
            for e in entries:
                if not isinstance(e, dict):
                    continue
                src = str(e.get("source_evidence_ids") or e.get("source_evidence_id") or "")
                print(f"    MAT {str(e.get('material_id'))[:14]} "
                      f"disp={e.get('boundary_disposition')} role={e.get('aspect_role')} "
                      f"src={src[:40]}")


if __name__ == "__main__":
    main()
