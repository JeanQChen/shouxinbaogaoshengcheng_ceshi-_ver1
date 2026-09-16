"""§十 报告取证：从 v9 manifest 只读抽取关闭条件证据（不做任何判定，只打印事实）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MANIFEST = Path("evaluation/results/r2_six_category_acceptance_v9_20260916/"
                "six_category_manifest.json")


def main() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pc = m["positive_controls"]

    print("=" * 72)
    print("§五.1 main_business 正向对照")
    print("=" * 72)
    mb = pc["main_business_capability"]
    print("satisfied =", mb["satisfied"])
    print("invariants =", json.dumps(mb["invariants"], ensure_ascii=False))
    print("materials  =", json.dumps(mb["materials"], ensure_ascii=False))
    print("boundary   =", json.dumps(mb["boundary_records"], ensure_ascii=False))
    print("structure(不含 continuation_proofs) =",
          json.dumps({k: v for k, v in mb["structure"].items()
                      if k != "continuation_proofs"}, ensure_ascii=False))
    print("observed   =", json.dumps(mb["observed"], ensure_ascii=False))

    print("\n" + "=" * 72)
    print("§五.2 non_300750 正向对照")
    print("=" * 72)
    nf = pc["non_300750_fixture_capability"]
    print("satisfied =", nf["satisfied"])
    print("invariants =", json.dumps(nf["invariants"], ensure_ascii=False))
    for k in ("company_id", "document_id", "document_version", "material_count",
              "assembly_count", "recovered_table_count", "recovered_table_titles",
              "recovered_table_failed", "boundary_decision_count",
              "source_inventory_present", "expansion_trace_steps",
              "expansion_stop_reasons", "company_hardcode_probe", "observed"):
        print(f"  {k} = {json.dumps(nf.get(k), ensure_ascii=False)}")

    print("\n" + "=" * 72)
    print("§四/§五.3 续表正向样本")
    print("=" * 72)
    cc = pc["continuation"]
    print("satisfied =", cc["satisfied"],
          "| sample_not_obtained =", cc["sample_not_obtained"],
          "| positive_control_not_available =", cc["positive_control_not_available"])
    print("samples =", json.dumps(cc["samples"], ensure_ascii=False, indent=2))
    print("incomplete_samples =", json.dumps(cc["incomplete_samples"], ensure_ascii=False))

    print("\n" + "=" * 72)
    print("§五.4 A–D + P1 关闭派生")
    print("=" * 72)
    abcd = m["closure_conditions"]["5_ABCD_and_identity_p1_closed"]
    print("satisfied =", abcd["satisfied"],
          "| closed =", abcd.get("closed_items"), "| open =", abcd.get("open_items"))
    for k, item in abcd["items"].items():
        print(f"\n-- {k}: satisfied={item['satisfied']}")
        print("   invariants =", json.dumps(item.get("invariants"), ensure_ascii=False))
        if item.get("requires_external_test_evidence"):
            print("   requires_external_test_evidence =",
                  json.dumps(item["requires_external_test_evidence"], ensure_ascii=False))

    print("\n" + "=" * 72)
    print("§五.3 负面结果证据（非 complete 类别）")
    print("=" * 72)
    ne = m["closure_conditions"]["4_negative_states_evidenced"]
    print("satisfied =", ne["satisfied"])
    for cid, e in ne["per_category"].items():
        print(f"\n-- {cid}: material_state={e['material_state']} "
              f"capability={e['capability_verdict']} evidenced={e['evidenced']}")
        for bk, b in e["bound"].items():
            print(f"   {bk}: holds={b['holds']}")
            print("     ", json.dumps({k: v for k, v in b.items() if k != "holds"},
                                      ensure_ascii=False)[:600])

    print("\n" + "=" * 72)
    print("其余关闭条件")
    print("=" * 72)
    for k, v in m["closure_conditions"].items():
        if k in ("4_negative_states_evidenced", "5_ABCD_and_identity_p1_closed"):
            continue
        print(f"{k}: {json.dumps(v, ensure_ascii=False)[:900]}")


if __name__ == "__main__":
    main()
