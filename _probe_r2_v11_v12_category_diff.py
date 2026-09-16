"""只读对比：v11（修复前）与 v12（定点修复后）六类真实材料的逐类别差异。

用于回答两个问题：
1. 定点修复是否**只**改变了显式引用的结构性绑定，而把其它类别的材料/判定原样保留？
2. 修复后是否出现「为了让显式引用通过而放宽 fail-closed」的迹象（例如原本诚实的
   not_obtained/dangling 变成 accepted）？

零写入；只调用生产验收器 ``verify_category``。
"""

from __future__ import annotations

import json
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

RESULTS = Path("evaluation/results")
SUFFIX = "20260916"
_REAL = {
    CATEGORY_MAIN_BUSINESS: "main_business",
    CATEGORY_CORE_COMPETITIVENESS: "core_competitiveness",
    CATEGORY_MAJOR_SUBSIDIARIES: "major_subsidiaries",
    CATEGORY_FINANCIAL_NOTES: "financial_notes",
    CATEGORY_NON_300750_FIXTURE: "non_300750_fixture",
}
_XREF_KEYS = ("state", "declared_targets", "resolution_targets", "verified_binding_targets",
              "resolved_targets", "unbacked_outputs", "table_ref_attempt_count",
              "named_ref_attempt_count", "reference_binding_problems",
              "same_document_bound", "detail")


def _row(cid: str, version: str) -> dict:
    run_dir = RESULTS / f"r2_material_slice_r2_sixcat_{version}_{_REAL[cid]}_{SUFFIX}"
    if not run_dir.is_dir():
        return {"run": run_dir.name, "missing": True}
    v = verify_category(cid, run_dir)
    facts = v.facts or {}
    out = {
        "run": run_dir.name,
        "material_state": v.material_state,
        "capability_verdict": v.capability_verdict,
        "report_impact": v.report_impact,
        "verdict": v.verdict,
        "failed_gates": v.failed_gates,
        "reason": v.reason,
        "material_count": len(facts.get("materials") or []) or facts.get("material_count"),
    }
    ref = facts.get("explicit_reference_audit")
    if ref:
        out["explicit_reference"] = {k: ref.get(k) for k in _XREF_KEYS}
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    # 显式引用类别按其冻结绑定复用 major_subsidiaries 的 run 目录（不是另一个 run）。
    _REAL[CATEGORY_EXPLICIT_CROSS_REFERENCE] = _REAL[CATEGORY_MAJOR_SUBSIDIARIES]
    for cid in [CATEGORY_MAIN_BUSINESS, CATEGORY_CORE_COMPETITIVENESS,
                CATEGORY_MAJOR_SUBSIDIARIES, CATEGORY_FINANCIAL_NOTES,
                CATEGORY_NON_300750_FIXTURE, CATEGORY_EXPLICIT_CROSS_REFERENCE]:
        rows.append({
            "category": cid,
            "v11": _row(cid, "v11"),
            "v12": _row(cid, "v12"),
        })
    for r in rows:
        a, b = r["v11"], r["v12"]
        print(f"\n=== {r['category']} ===")
        print(f"  v11: {a.get('material_state')} / {a.get('capability_verdict')} / "
              f"{a.get('verdict')} gates={a.get('failed_gates')}")
        print(f"  v12: {b.get('material_state')} / {b.get('capability_verdict')} / "
              f"{b.get('verdict')} gates={b.get('failed_gates')}")
        if "explicit_reference" in a or "explicit_reference" in b:
            print(f"  v11 xref: {json.dumps(a.get('explicit_reference'), ensure_ascii=False)[:300]}")
            print(f"  v12 xref: {json.dumps(b.get('explicit_reference'), ensure_ascii=False)[:300]}")
    print("\n" + json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
