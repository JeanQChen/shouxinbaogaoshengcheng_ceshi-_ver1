"""临时探针（只读）：验证「独立重算 material 身份」对真实历史产物成立。

重算链：payload_preview.payload(bytes) → sha256 → payload_hash；
envelope(locator/document_identity) + material_index → canonical_locator_key → material_id。
只读 evaluation/results 下已有 v5 run 目录，不写任何产物。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path("evaluation/results")

RUNS = [
    "r2_material_slice_r2_sixcat_v5_main_business_20260916c",
    "r2_material_slice_r2_sixcat_v5_core_competitiveness_20260916c",
    "r2_material_slice_r2_sixcat_v5_major_subsidiaries_20260916c",
    "r2_material_slice_r2_sixcat_v5_financial_notes_20260916c",
    "r2_material_slice_r2_sixcat_v5_non_300750_fixture_20260916c",
    "r2_material_slice_r2_p0_credit_v5_20260916c",
]


def _compute_material_id(material_type, evidence_id, source_identity, document_version,
                         evidence_set_version, canonical_locator_key, payload_hash):
    identity = [material_type, evidence_id, source_identity, document_version,
                evidence_set_version, canonical_locator_key, payload_hash]
    return "mat-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


def main() -> None:
    for run in RUNS:
        d = ROOT / run
        mi_p = d / "material_index.json"
        pp_dir = d / "payload_preview"
        if not mi_p.exists() or not pp_dir.is_dir():
            print(f"{run}: 缺 material_index/payload_preview")
            continue
        index = {m["material_id"]: m for m in json.loads(mi_p.read_text(encoding="utf-8"))}
        hash_bad, id_bad, missing, envelope_bad = [], [], [], []
        checked = 0
        for mid, entry in index.items():
            p = pp_dir / f"{mid}.json"
            if not p.exists():
                missing.append(mid)
                continue
            preview = json.loads(p.read_text(encoding="utf-8"))
            payload_text = preview.get("payload")
            if not isinstance(payload_text, str):
                envelope_bad.append(mid)
                continue
            pb = payload_text.encode("utf-8")
            ph = hashlib.sha256(pb).hexdigest()
            checked += 1
            if ph != preview.get("payload_hash") or ph != entry.get("payload_hash"):
                hash_bad.append((mid, ph, preview.get("payload_hash"),
                                 entry.get("payload_hash")))
                continue
            env = json.loads(payload_text)
            if env.get("source_content_hash") != entry.get("source_content_hash"):
                envelope_bad.append(mid)
            loc = env.get("locator") or {}
            di = env.get("document_identity") or {}
            br = loc.get("block_range") or [None, None]
            key = "|".join([
                str(loc.get("document_id") or ""), str(loc.get("document_version") or ""),
                str(di.get("evidence_set_version") or ""), str(loc.get("section_path") or ""),
                str(loc.get("page")), str(br[0]),
                str(loc.get("table_title") or ""),
                str(loc.get("offset")) if loc.get("offset") is not None else "",
            ])
            rec = _compute_material_id(
                entry.get("material_type") or env.get("object_type"),
                env.get("evidence_id"), env.get("authority_identity"),
                di.get("document_version"), di.get("evidence_set_version"),
                key, ph)
            if rec != mid:
                id_bad.append((mid, rec))
        print(f"{run}: materials={len(index)} checked={checked} "
              f"hash_mismatch={len(hash_bad)} id_mismatch={len(id_bad)} "
              f"missing_preview={len(missing)} envelope_mismatch={len(envelope_bad)}")
        for row in (hash_bad + id_bad)[:3]:
            print("   mismatch:", row)


if __name__ == "__main__":
    main()
