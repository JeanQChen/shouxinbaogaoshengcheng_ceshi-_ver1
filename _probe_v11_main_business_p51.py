"""只读取证：main_business v11 run 的 seed / 续表扩读 / 采纳 / 同表恢复证明链。

回答两个必须如实回答的问题：
1. candidate-1 的**同一个 seed**（53c9b3721904，p50 表 5-11 表头块）是否真的通过
   ``mode=table_continuation`` 步骤 output 出续页块，且该块真的被采纳进本 aspect 材料？
2. 该续页块是否**同时**是第二个 seed（即：不依赖续表扩读也会进材料池）？——
   若是，必须如实披露「正向 trace 事实成立，但材料获得路径与之并存」。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v11_main_business_20260916")


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"__error__": str(exc)}


def main() -> None:
    print("=" * 78)
    print(RUN.name)

    sm = _load(RUN / "seed_manifest.json") or {}
    print("\n--- seed_manifest.entries ---")
    for e in (sm.get("entries") or []):
        if not isinstance(e, dict):
            continue
        print(f"  eid={str(e.get('evidence_id'))[:12]} aspect={e.get('aspect_id')} "
              f"p={e.get('page_number')} blk={e.get('block_index')} "
              f"doc={e.get('document_id')} ver={str(e.get('document_version'))[:24]}")
        print(f"      text={str(e.get('text'))[:120]!r}")

    res = _load(RUN / "resolved_seeds.json") or {}
    print("\n--- resolved_seeds.entries ---")
    for e in (res.get("entries") or []):
        if isinstance(e, dict):
            print(f"  eid={str(e.get('evidence_id'))[:12]} aspect={e.get('aspect_id')} "
                  f"reason={e.get('reason') or e.get('resolve_reason')}")

    mi = _load(RUN / "material_index.json")
    entries = mi if isinstance(mi, list) else ((mi or {}).get("entries") or [])
    print(f"\n--- material_index ({len(entries)}) ---")
    for m in entries:
        if not isinstance(m, dict):
            continue
        print(f"  MAT {str(m.get('material_id'))[:14]}")
        print(f"      component_evidence_id={str(m.get('component_evidence_id'))[:12]} "
              f"aspect_ids={m.get('aspect_ids')}")
        print(f"      disposition={m.get('boundary_disposition')} role={m.get('aspect_role')}")

    asm = _load(RUN / "assemblies.json")
    alist = asm if isinstance(asm, list) else ((asm or {}).get("assemblies") or [])
    print(f"\n--- assemblies ({len(alist)}) ---")
    for a in alist:
        if not isinstance(a, dict):
            continue
        print(f"  ASM relation={a.get('relation')} title={a.get('table_title')!r} "
              f"status={a.get('recovery_status')}")
        p = a.get("continuation_proof")
        if isinstance(p, dict):
            print("      continuation_proof:")
            for k in ("valid", "identity_source", "header_evidence_id", "header_page",
                      "continuation_evidence_ids", "continuation_pages",
                      "normalized_title", "title_compatible", "unit_compatible",
                      "column_compatible", "row_column_continuity",
                      "final_recovery_status", "header_repeat_verified",
                      "boundary_consecutive", "section_path_shared",
                      "same_document_verified", "span_fact_count"):
                v = p.get(k)
                if isinstance(v, list):
                    v = [str(x)[:12] for x in v]
                print(f"        {k} = {v}")

    tr = RUN / "expansion_trace.jsonl"
    print("\n--- table_continuation 步骤（真实 outputs）---")
    for ln in tr.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        s = json.loads(ln)
        if str((s.get("arguments") or {}).get("mode") or "") != "table_continuation":
            continue
        print(f"  seed={str(s.get('seed_evidence_id'))[:12]} "
              f"anchor={str(s.get('anchor_evidence_id'))[:12]} "
              f"args_evidence_id={str((s.get('arguments') or {}).get('evidence_id'))[:12]} "
              f"p={(s.get('arguments') or {}).get('page_number')} "
              f"blk={(s.get('arguments') or {}).get('block_index')}")
        print(f"      outputs={[str(o)[:12] for o in (s.get('outputs') or [])]} "
              f"adopted={[str(o)[:12] for o in (s.get('adopted') or [])]} "
              f"stop={s.get('stop_reason')!r}")


if __name__ == "__main__":
    main()
