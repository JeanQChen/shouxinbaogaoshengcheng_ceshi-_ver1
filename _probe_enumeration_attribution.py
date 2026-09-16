"""只读探针：打印六类 v6 真实产物中 set_enumeration 的**负面归因**可用事实。

用途（§四.D.3/D.9/D.10）：在把 g16 从「material_type_supported=false 一律能力失败」改成
「只有**不可归因/被真实产物反证**的负面才是能力失败」之前，先看清真实产物的
per_version / merged / reason / document_version 事实。只读，不写任何产物。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from harness import topic_schema as TS  # noqa: E402

RES = ROOT / "evaluation" / "results"

DIRS = [
    ("main_business", "r2_material_slice_r2_sixcat_v6_main_business_20260916"),
    ("financial_notes", "r2_material_slice_r2_sixcat_v6_financial_notes_20260916"),
    ("core_competitiveness",
     "r2_material_slice_r2_sixcat_v6_core_competitiveness_20260916"),
    ("major_subsidiaries", "r2_material_slice_r2_sixcat_v6_major_subsidiaries_20260916"),
    ("non_300750_fixture", "r2_material_slice_r2_sixcat_v6_non_300750_fixture_20260916"),
    ("p0_credit", "r2_material_slice_r2_p0_credit_v6_20260916"),
]


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"<读失败：{exc}>"


def main() -> int:
    for label, dirname in DIRS:
        d = RES / dirname
        print(f"\n=== {label} :: {dirname} exists={d.exists()}")
        if not d.exists():
            continue
        se = _load(d / "set_enumeration.json") or {}
        mi = _load(d / "material_index.json") or []
        if not isinstance(se, dict):
            print(f"  set_enumeration 非 dict：{se}")
            continue
        for aspect_id, entry in sorted(se.items()):
            if not isinstance(entry, dict):
                print(f"  [{aspect_id}] 非 dict")
                continue
            pv = entry.get("per_version")
            pv_versions = []
            if isinstance(pv, list):
                for p in pv:
                    if isinstance(p, dict):
                        pv_versions.append(str(p.get("document_version") or ""))
            src_versions = sorted({
                str(m.get("document_version") or "")
                for m in mi if isinstance(m, dict)
                and (m.get("aspect_roles") or {}).get(aspect_id) == "source"})
            any_versions = sorted({
                str(m.get("document_version") or "")
                for m in mi if isinstance(m, dict)
                and aspect_id in (m.get("aspect_ids") or [])})
            src_count = sum(
                1 for m in mi if isinstance(m, dict)
                and (m.get("aspect_roles") or {}).get(aspect_id) == "source")
            print(f"  [{aspect_id}] supported={entry.get('material_type_supported')!r} "
                  f"merged={entry.get('merged')!r} vv={entry.get('verifier_version')!r}")
            print(f"      reason={entry.get('reason')!r}")
            print(f"      per_version_versions={pv_versions} count={len(pv) if isinstance(pv, list) else 'NA'}")
            print(f"      real_source_versions={src_versions} src_material_count={src_count}")
            print(f"      real_any_versions={any_versions}")
            if isinstance(pv, list):
                for p in pv[:4]:
                    if isinstance(p, dict):
                        r = p.get("result") if isinstance(p.get("result"), dict) else {}
                        print(f"      - dv={p.get('document_version')!r} "
                              f"result.supported={r.get('material_type_supported')!r} "
                              f"result.vv={r.get('verifier_version')!r} "
                              f"result.reason={(r.get('reason') or '')[:70]!r}")
    print(f"\nverifier_version_const={TS.SET_ENUMERATION_VERIFIER_VERSION!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
