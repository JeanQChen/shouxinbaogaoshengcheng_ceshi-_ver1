"""只读取证：把 ``explicit_cross_reference`` 类别指向不同 v11 run 时的四态审计。

对每个候选 run 目录，用**生产验收器** ``verify_category`` 独立重算，打印
显式引用四态（trigger / attempted / resolved / dangling / not_exercised）、
可解析目标、已采纳材料，以及该类别是否产生失败门。只读。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.six_category_acceptance import (
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    verify_category,
)

RESULTS = Path("evaluation/results")


def main() -> None:
    names = sys.argv[1:] or [
        "r2_material_slice_r2_sixcat_v11_financial_notes_20260916",
        "r2_material_slice_r2_sixcat_v11_major_subsidiaries_20260916",
        "r2_material_slice_r2_sixcat_v11_main_business_20260916",
        "r2_material_slice_r2_sixcat_v11_core_competitiveness_20260916",
        "r2_material_slice_r2_sixcat_v11_non_300750_fixture_20260916",
    ]
    for name in names:
        run_dir = RESULTS / name
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, run_dir)
        f = v.facts
        ref = f.get("explicit_reference_audit") or {}
        print("=" * 78)
        print(name)
        print("  verdict:", v.verdict, "| material_state:", v.material_state,
              "| capability:", v.capability_verdict)
        print("  failed_gates:", [g for g, _ in v.failed_gates])
        print("  reason:", str(v.reason)[:220])
        print("  ref state:", ref.get("state"))
        print("  detail:", str(ref.get("detail"))[:400])
        print("  declared_targets:", ref.get("declared_targets"))
        print("  resolved_targets:", ref.get("resolved_targets"))
        print("  dangling_targets:", ref.get("dangling_targets"))
        print("  attempt_step_count:", ref.get("attempt_step_count"),
              "| resolution_attempted:", ref.get("resolution_attempted"),
              "| target_resolved:", ref.get("target_resolved"),
              "| target_dangling:", ref.get("target_dangling"))
        print("  attempt_stop_reasons:", ref.get("attempt_stop_reasons"))
        print("  read modes:", f.get("expansion_read_modes"))
        print("  trace steps:", f.get("expansion_trace_steps"),
              "| stop reasons:", f.get("expansion_stop_reasons"))
        print("  boundary_verified:", f.get("boundary_verified"),
              "| boundary_status:", [
                  (s.get("aspect_id"), s.get("status"))
                  for s in (f.get("boundary_verification_status") or [])])


if __name__ == "__main__":
    main()
