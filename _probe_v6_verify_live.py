"""临时探针（只读）：对现存 v6 run 目录**现场**跑 verify_category，打印三轴与失败门。

不写任何产物；只读 evaluation/results 下已存在的 v6 run 目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_CORE_COMPETITIVENESS,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    CATEGORY_FINANCIAL_NOTES,
    CATEGORY_MAIN_BUSINESS,
    CATEGORY_MAJOR_SUBSIDIARIES,
    CATEGORY_NON_300750_FIXTURE,
    verify_category,
)

S = "20260916"
RUNS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v6_main_business_{S}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v6_core_competitiveness_{S}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v6_major_subsidiaries_{S}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v6_financial_notes_{S}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v6_financial_notes_{S}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v6_non_300750_fixture_{S}",
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path("evaluation/results")
    for cid, run in RUNS.items():
        d = root / run
        if not d.is_dir():
            print(f"{cid}: 缺 run 目录 {run}")
            continue
        v = verify_category(cid, d)
        print("=" * 100)
        print(f"{cid}: state={v.material_state} verdict={v.capability_verdict} "
              f"impact={v.report_impact} compat={v.verdict}")
        print(f"  reason={v.reason[:220]}")
        print(f"  honest_gap={v.facts.get('honest_gap_reason')}")
        print(f"  failed={len(v.failed_gates)} passed={len(v.passed_gates)}")
        for g, det in v.failed_gates:
            print(f"    {g}: {str(det)[:260]}")


if __name__ == "__main__":
    main()
