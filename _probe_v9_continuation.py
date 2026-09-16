"""§四 只读探针：主线「表 5-11 P50→P51 跨页续表」为何未被识别为真实续表。

只读真实 ``data/evidence.db``（经既有只读 Evidence reader / ToolRegistry），
**零写入仓库产物**：run 输出到临时目录，harness payload 缓存用临时副本。
不改任何生产规则，仅观测：

1. ``_build_flattened_table_assemblies`` 实际喂给 ``recover_flattened_tables`` 的文本序列；
2. 恢复出的表（title/headers/rows/total/structure_text_indices）与持久化 assembly 的对照；
3. 表 5-11 assembly 的 component / continuation_evidence_ids / continuation_proof。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness.topic_materials as TM  # noqa: E402
from harness.material_slice_runner import (  # noqa: E402
    load_seed_manifest,
    run_material_slice,
)

RESULTS = Path("evaluation/results")
V2_DIR = RESULTS / "r2_material_slice_r2_sixcat_v2_main_business_20260915"

_captured: list[list[str]] = []
_captured_tables: list[list[dict]] = []
_orig = TM.recover_flattened_tables


def _spy(texts):
    _captured.append(list(texts))
    out = _orig(texts)
    _captured_tables.append([
        {"title": t.get("title"), "unit": t.get("unit"), "headers": t.get("headers"),
         "nrows": len(t.get("rows") or []), "total": t.get("total_row"),
         "widths": [len(r) for r in (t.get("rows") or [])],
         "status": t.get("recovery_status"), "issue": t.get("recovery_issue"),
         "struct_idx": t.get("structure_text_indices"),
         "row_idx": t.get("row_text_indices"),
         "repeat_idx": t.get("repeated_header_text_indices"),
         "cont_idx": t.get("continuation_text_indices"),
         "header_idx": t.get("header_text_index"),
         "total_idx": t.get("total_text_index")}
        for t in out])
    return out


TM.recover_flattened_tables = _spy

_captured_spans: list[list[dict]] = []
_orig_build = TM._build_flattened_table_assemblies


def _spy_build(materials, blocks_by_evidence_id, fragment_texts=None):
    _captured_spans.append([
        {"material_id": m.material_id,
         "evidence_id": getattr(m.authority_assessment, "evidence_id", ""),
         "page": m.authority_assessment.page,
         "block_range": getattr(m.locator, "block_range", None),
         "offset": getattr(m.locator, "offset", None),
         "frag_text": len((fragment_texts or {}).get(m.material_id, "")),
         "text_len": len((blocks_by_evidence_id.get(
             getattr(m.authority_assessment, "evidence_id", "")) or
             type("X", (), {"text": ""})()).text or "")}
        for m in materials if m.material_type == "evidence_span"])
    return _orig_build(materials, blocks_by_evidence_id, fragment_texts)


TM._build_flattened_table_assemblies = _spy_build

_captured_mats: list[dict] = []
_orig_mat = TM.build_material_result


def _spy_mat(expansion, *a, **kw):
    res = _orig_mat(expansion, *a, **kw)
    reads = []
    for step in getattr(getattr(expansion, "trace", None), "steps", ()) or ():
        d = step.to_dict() if hasattr(step, "to_dict") else dict(step)
        reads.append({"mode": (d.get("arguments") or {}).get("mode"),
                      "outputs": d.get("outputs"),
                      "stop": d.get("stop_reason"),
                      "page": (d.get("arguments") or {}).get("page_number")})
    _captured_mats.append({
        "seed_ev": getattr(expansion.seed, "evidence_id", "?"),
        "reads": reads,
        "materials": [{"material_id": m.material_id,
                       "evidence_id": getattr(m.authority_assessment, "evidence_id", ""),
                       "page": m.authority_assessment.page,
                       "loc": str(getattr(m.locator, "block_range", None)),
                       "text_len": len(str(getattr(m, "text", "") or ""))}
                      for m in getattr(res, "materials", ())],
    })
    return res


TM.build_material_result = _spy_mat

import harness.material_slice_runner as MSR  # noqa: E402

_orig_runner_mat = MSR.build_material_result


def _spy_runner_mat(expansion, **kw):
    info = {
        "seed_ev": getattr(expansion.seed, "evidence_id", "?"),
        "reads": [{"mode": (getattr(s, "arguments", {}) or {}).get("mode"),
                   "out": list(getattr(s, "outputs", ()) or ()),
                   "stop": getattr(s, "stop_reason", None),
                   "page": (getattr(s, "arguments", {}) or {}).get("page_number"),
                   "args": dict(getattr(s, "arguments", {}) or {})}
                  for s in (getattr(expansion.trace, "steps", ()) or ())],
        "adopted": [{"evidence_id": b.evidence_id, "page": b.page_number,
                     "text_len": len(b.text or ""),
                     "block_range": getattr(b, "block_range", None),
                     "ev_type": b.evidence_type,
                     "head": (b.text or "")[:24]}
                    for b in expansion.adopted],
        "fragments": [{"evidence_id": getattr(f.block, "evidence_id", ""),
                       "offset": f.char_offset,
                       "prefix_len": len(getattr(f, "prefix_text", "") or "")}
                      for f in (getattr(expansion, "fragment_projections", ()) or ())],
        "stop_reason": expansion.stop_reason,
    }
    _captured_mats.append(info)
    return _orig_runner_mat(expansion, **kw)


MSR.build_material_result = _spy_runner_mat


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest = load_seed_manifest(V2_DIR / "seed_manifest.json")
    tmp = Path(tempfile.mkdtemp(prefix="probe_v9_continuation_"))
    shutil.copy("data/harness.db", tmp / "harness.db")

    summary = run_material_slice(
        "probe_continuation_tmp", manifest,
        evidence_db="data/evidence.db", harness_db=tmp / "harness.db",
        out_root=str(tmp), budget_profile_name="acceptance")
    out_dir = tmp / "r2_material_slice_probe_continuation_tmp"

    print("== 喂入 recover_flattened_tables 的文本批次 ==")
    for bi, batch in enumerate(_captured):
        print(f"batch {bi}: {len(batch)} texts")
        for i, t in enumerate(batch):
            head = t.splitlines()[0] if t.splitlines() else ""
            print(f"  [{i}] len={len(t)} head={head!r}")
            print(f"      tail={t[-40:]!r}")

    print("\n== 逐 seed pass 的材料与真实读取 ==")
    for i, m in enumerate(_captured_mats):
        print(f"pass {i} seed_ev={m.get('seed_ev')} stop={m.get('stop_reason')!r}")
        for r in m.get("reads", ()):
            print("    read:", json.dumps(r, ensure_ascii=False))
        for mm in m.get("adopted", ()):
            print("    adopt:", json.dumps(mm, ensure_ascii=False))
        for f in m.get("fragments", ()):
            print("    frag :", json.dumps(f, ensure_ascii=False))

    print("\n== 每批摊平表恢复的 span 视图（按规范源顺序） ==")
    for bi, spans in enumerate(_captured_spans):
        print(f"batch {bi}: {len(spans)} spans")
        for i, s in enumerate(spans):
            print("   ", i, json.dumps(s, ensure_ascii=False))

    print("\n== 每批恢复出的表 ==")
    for bi, batch in enumerate(_captured_tables):
        print(f"batch {bi}: {len(batch)} tables")
        for t in batch:
            print("   ", json.dumps(t, ensure_ascii=False)[:420])

    print("\n== 持久化 assemblies ==")
    for a in json.loads((out_dir / "assemblies.json").read_text(encoding="utf-8")):
        cp = a.get("continuation_proof") or {}
        print(a["assembly_id"], "|", a.get("relation"), "|", repr(a.get("table_title")),
              "| comp", a.get("component_material_ids"), "| bd", a.get("boundary_desc"),
              "| rows", len(a.get("rows") or []), "| total", a.get("total_row"),
              "| cont", a.get("continuation_evidence_ids"), "| valid", cp.get("valid"),
              "| issue", cp.get("issue"))

    print("\n== recovery_status ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:2000])
    print("\n== 源对象清单（逐 aspect） ==")
    for asp, inv in json.loads(
            (out_dir / "source_object_inventory.json").read_text(encoding="utf-8")).items():
        exp = inv.get("expected_source_objects") or []
        print(f"-- {asp}: expected={len(exp)} results={len(inv.get('recovery_results') or [])} "
              f"orphan={inv.get('orphan_assemblies')} untitled={inv.get('untitled_recovered_tables')}")
        for r in inv.get("recovery_results") or []:
            if r.get("result") != "recovered_ok" or r.get("issue"):
                print("   ", json.dumps(r, ensure_ascii=False)[:260])

    sup = json.loads((out_dir / "assemblies_superseded.json").read_text(encoding="utf-8"))
    print("\n== 被取代的跨 pass 投影（披露） ==")
    print(json.dumps(sup, ensure_ascii=False, indent=2)[:2000])
    print("\ntmp dir:", tmp)


if __name__ == "__main__":
    main()
