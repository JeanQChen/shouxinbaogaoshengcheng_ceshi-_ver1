"""R2 §五：v14 最小真实本地验收重跑（只跑 ``major_subsidiaries``；该 run 同时承载
``explicit_cross_reference`` 类别）。

与 v13 的差别
-------------
1. 本轮以全新 run_id 重跑事前冻结的显式引用正样本 ``major_subsidiaries``，并**同时**对该
   新 run 独立重算 ``explicit_cross_reference`` 类别的引用机制事实（§五：重跑范围 =
   major_subsidiaries + explicit_cross_reference），其余四个真实样本原样绑定 v12 冻结 run；
2. 跑完把 run 目录逐文件大小/sha256/mtime 与开跑前后的清单哈希记进
   ``r2_xref_specimen_v14_20260916/run_file_facts.json``，作为「清单先于 run」的落盘证据；
3. 额外落盘 ``xref_category_facts.json``：新 run 上**独立重算**的显式引用诊断原始事实
   （含目标对象是否真的进入材料库的逐层事实），供验收侧核对（不属于材料能力命名空间）。

写盘纪律
--------
- 只写 ``r2_material_slice_r2_sixcat_v14_major_subsidiaries_20260916`` 全新目录；
  v12/v13 及更早目录一律不读不写不改、不重命名；
- 开跑前必须已存在事前冻结清单，且其 ``specimen_manifest.json`` 的 sha256 必须等于
  ``specimen_file_facts.json`` 记录值（证明清单未被改写）。

零 LLM / 零网络 / 零博查；不 init/migrate Evidence/Financial DB；harness.db payload 幂等复用。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.material_slice_runner import load_seed_manifest, run_material_slice  # noqa: E402
from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    verify_category,
)

RESULTS = Path("evaluation/results")
EVIDENCE_DB = "data/evidence.db"
HARNESS_DB = "data/harness.db"
SUFFIX = "20260916"
RUN_VERSION = "v14"
CATEGORY = "major_subsidiaries"

SPEC_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
SPEC_MANIFEST = SPEC_DIR / "specimen_manifest.json"
SPEC_FACTS = SPEC_DIR / "specimen_file_facts.json"
RUN_ID = f"r2_sixcat_{RUN_VERSION}_{CATEGORY}_{SUFFIX}"
OUT_DIR = RESULTS / f"r2_material_slice_{RUN_ID}"

# 真实 seed 来源（v2 目录，只读复用；与 v12/v13 完全同一份）。
SEED_MANIFEST = (RESULTS / "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915"
                 / "seed_manifest.json")


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _utc(p: Path) -> str:
    return _dt.datetime.fromtimestamp(p.stat().st_mtime, _dt.timezone.utc).isoformat()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if not SPEC_MANIFEST.exists() or not SPEC_FACTS.exists():
        raise SystemExit(f"拒绝：缺少事前冻结清单/落盘事实 {SPEC_DIR}")
    if OUT_DIR.exists():
        raise SystemExit(f"拒绝：{OUT_DIR.name} 已存在（v14 必须写入全新目录）")

    facts = json.loads(SPEC_FACTS.read_text(encoding="utf-8"))
    want = facts["specimen_manifest"]["sha256"]
    got = _sha256_file(SPEC_MANIFEST)
    if want != got:
        raise SystemExit(f"拒绝：冻结清单已被改写 freeze={want} now={got}")

    spec = json.loads(SPEC_MANIFEST.read_text(encoding="utf-8"))
    spec_fp = spec["specimen_fingerprint"]
    binding = spec["bound_run_binding"]["run_ids"][CATEGORY]
    if binding != RUN_ID:
        raise SystemExit(f"拒绝：run_id 与冻结绑定不一致 freeze={binding} now={RUN_ID}")
    xref = spec["bound_run_binding"]["explicit_cross_reference_binding"]["run_id"]
    if xref != RUN_ID:
        raise SystemExit(f"拒绝：explicit_cross_reference 绑定与本 run 不一致 freeze={xref}")

    started = _dt.datetime.now(_dt.timezone.utc)
    manifest = load_seed_manifest(SEED_MANIFEST)
    summary = run_material_slice(
        RUN_ID, manifest, evidence_db=EVIDENCE_DB, harness_db=HARNESS_DB,
        out_root=str(RESULTS), budget_profile_name="acceptance")
    finished = _dt.datetime.now(_dt.timezone.utc)

    files = {}
    for p in sorted(OUT_DIR.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(OUT_DIR)).replace("\\", "/")] = {
                "size": p.stat().st_size, "sha256": _sha256_file(p), "mtime_utc": _utc(p)}
    run_facts = {
        "run_version": RUN_VERSION,
        "run_id": RUN_ID,
        "run_dir": str(OUT_DIR).replace("\\", "/"),
        "run_started_utc": started.isoformat(),
        "run_finished_utc": finished.isoformat(),
        "specimen_manifest": {
            "path": str(SPEC_MANIFEST).replace("\\", "/"),
            "sha256_before_run": want,
            "sha256_after_run": _sha256_file(SPEC_MANIFEST),
            "frozen_at_utc_iso": facts.get("frozen_at_utc_iso"),
            "manifest_mtime_utc": _utc(SPEC_MANIFEST),
            "specimen_fingerprint": spec_fp,
            "unchanged_across_run": _sha256_file(SPEC_MANIFEST) == want,
        },
        "seed_manifest": {"path": str(SEED_MANIFEST).replace("\\", "/"),
                          "sha256": _sha256_file(SEED_MANIFEST),
                          "manifest_fingerprint": json.loads(
                              SEED_MANIFEST.read_text(encoding="utf-8")).get("fingerprint")},
        "run_files": files,
        "ordering_proof": "清单 mtime < run_started_utc ≤ run 文件 mtime；且清单 sha256 在 run "
                          "前后不变（清单先于 run 存在且未被改写）",
        "manifest_ordering_holds": (
            _dt.datetime.fromtimestamp(SPEC_MANIFEST.stat().st_mtime, _dt.timezone.utc) < started
            and _sha256_file(SPEC_MANIFEST) == want),
    }
    (SPEC_DIR / "run_file_facts.json").write_text(
        json.dumps(run_facts, ensure_ascii=False, indent=2), encoding="utf-8")

    # §五：在新 run 上**独立重算**显式引用类别事实（不是材料能力命名空间）。
    v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, OUT_DIR)
    ref = dict((v.facts or {}).get("explicit_reference_audit") or {})
    asm = json.loads((OUT_DIR / "assemblies.json").read_text(encoding="utf-8"))
    asm_items = asm.get("assemblies") if isinstance(asm, dict) else asm
    ref_asm = [a for a in (asm_items or [])
               if isinstance(a, dict) and a.get("relation") == "reference_table_object"]
    (SPEC_DIR / "xref_category_facts.json").write_text(json.dumps({
        "namespace": "explicit_reference_diagnostics.explicit_cross_reference",
        "material_capability_namespace": f"categories.{CATEGORY}",
        "run_id": RUN_ID,
        "run_dir": str(OUT_DIR).replace("\\", "/"),
        "recomputed_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "recomputed_from": "harness.six_category_acceptance.verify_category"
                           "（只读 run 目录原始产物，不信 runner 自报）",
        "explicit_reference_state": ref.get("state"),
        "explicit_reference_audit": ref,
        "reference_table_object_projections": [
            {"assembly_id": a.get("assembly_id"), "table_object_id": a.get("table_object_id"),
             "table_title": a.get("table_title"),
             "object_schema_version": a.get("object_schema_version"),
             "binding_version": a.get("binding_version"),
             "component_evidence_ids": a.get("component_evidence_ids"),
             "component_material_ids": a.get("component_material_ids"),
             "target_start": a.get("target_start"), "target_end": a.get("target_end"),
             "reference_kind": a.get("reference_kind"),
             "reference_marker": a.get("reference_marker"),
             "reference_occurrence_index": a.get("reference_occurrence_index")}
            for a in ref_asm],
        "reference_table_object_projection_count": len(ref_asm),
        "claim_scope": "本文件只陈述**引用机制**在新 run 上被演练到什么程度；"
                       "不构成对任何类别 material_state / capability_verdict 的判定",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "run_version": RUN_VERSION,
        "run_id": RUN_ID,
        "specimen_fingerprint": spec_fp,
        "specimen_manifest_sha256": want,
        "manifest_ordering_holds": run_facts["manifest_ordering_holds"],
        "run_started_utc": run_facts["run_started_utc"],
        "run_finished_utc": run_facts["run_finished_utc"],
        "explicit_reference_state": ref.get("state"),
        "reference_table_object_projections": len(ref_asm),
        "run_summary": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
