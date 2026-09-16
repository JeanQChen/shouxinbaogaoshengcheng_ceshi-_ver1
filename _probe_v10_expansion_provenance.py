"""只读取证：v10 六类里那一张有效的跨页续表证明，其「续表扩读来源」为何未成立。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.six_category_acceptance import (  # noqa: E402
    _continuation_expansion_provenance,
    _continuation_expansion_verdict,
    _read_jsonl_failclosed,
    verify_category,
)
from _build_six_category_manifest_v10 import CATEGORY_RUN_DIRS, RESULTS  # noqa: E402


def main() -> None:
    for cid, dirname in CATEGORY_RUN_DIRS.items():
        d = RESULTS / dirname
        if not d.is_dir():
            print(cid, "MISSING", d)
            continue
        v = verify_category(cid, d)
        rows = v.facts.get("recovered_table_detail") or []
        valid = [r for r in rows if isinstance(r.get("continuation_proof"), dict)
                 and r["continuation_proof"].get("valid") is True]
        print("=" * 78)
        print(cid, d.name, "rows =", len(rows), "valid =", len(valid))
        if not valid:
            continue
        trace, _err = _read_jsonl_failclosed(d, "expansion_trace.jsonl")
        adopted = sorted({str(m.get("component_evidence_id") or "")
                          for m in (v.facts.get("material_index") or [])
                          if isinstance(m, dict)}
                         - {""})
        seeds = sorted({str(e.get("evidence_id") or "")
                        for e in (v.facts.get("seed_entries") or [])
                        if isinstance(e, dict)} - {""})
        print("  trace steps =", len(trace), "err =", _err)
        print("  seeds =", seeds)
        print("  adopted =", adopted)
        prov = _continuation_expansion_provenance(
            trace, seed_evidence_ids=seeds, adopted_evidence_ids=adopted)
        print("  provenance =", json.dumps(prov, ensure_ascii=False, indent=2)[:2500])
        for r in valid:
            p = r["continuation_proof"]
            print("  table_title =", r.get("table_title"))
            print("  proof.header_evidence_id =", p.get("header_evidence_id"),
                  "page", p.get("header_page"))
            print("  proof.continuation_evidence_ids =",
                  p.get("continuation_evidence_ids"),
                  "pages", p.get("continuation_pages"))
            print("  continuation_expansion =",
                  json.dumps(r.get("continuation_expansion"), ensure_ascii=False))
            print("  verdict =",
                  json.dumps(_continuation_expansion_verdict(p, prov), ensure_ascii=False))


if __name__ == "__main__":
    main()
