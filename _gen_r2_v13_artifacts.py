"""R2 §六：v13 最小真实本地验收重跑（只跑显式引用正样本 ``major_subsidiaries``）。

与 v12 的差别只有一处：**本轮不重跑其余五类真实样本**（§一/§六：不得重跑/调参），
只以全新 run_id 重跑 ``company_subsidiaries.major_subsidiaries`` 这一个事前冻结的正样本，
其余五个样本在 v13 六类 manifest 中沿用 v12 已冻结 run 并逐条披露 ``reused_from_previous_round``。

写盘纪律
--------
- 只写 ``r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916`` 全新目录；
  v12 及更早目录一律不读不写不改、不重命名；
- 开跑前必须已存在事前冻结清单，且其 ``specimen_manifest.json`` 的 sha256 必须等于
  ``specimen_file_facts.json`` 记录值（证明清单未被改写）；
- 跑完把 run 目录逐文件大小/sha256/mtime 与开跑前后的清单哈希记进
  ``r2_xref_specimen_v13_20260916/run_file_facts.json``，作为「清单先于 run」的落盘证据。

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

RESULTS = Path("evaluation/results")
EVIDENCE_DB = "data/evidence.db"
HARNESS_DB = "data/harness.db"
SUFFIX = "20260916"
RUN_VERSION = "v13"
CATEGORY = "major_subsidiaries"

SPEC_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
SPEC_MANIFEST = SPEC_DIR / "specimen_manifest.json"
SPEC_FACTS = SPEC_DIR / "specimen_file_facts.json"
RUN_ID = f"r2_sixcat_{RUN_VERSION}_{CATEGORY}_{SUFFIX}"
OUT_DIR = RESULTS / f"r2_material_slice_{RUN_ID}"

# 真实 seed 来源（v2 目录，只读复用；与 v12 完全同一份）。
SEED_MANIFEST = RESULTS / "r2_material_slice_r2_sixcat_v2_major_subsidiaries_20260915" / "seed_manifest.json"


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
        raise SystemExit(f"拒绝：{OUT_DIR.name} 已存在（v13 必须写入全新目录）")

    facts = json.loads(SPEC_FACTS.read_text(encoding="utf-8"))
    want = facts["specimen_manifest"]["sha256"]
    got = _sha256_file(SPEC_MANIFEST)
    if want != got:
        raise SystemExit(f"拒绝：冻结清单已被改写 freeze={want} now={got}")
    spec_fp = json.loads(SPEC_MANIFEST.read_text(encoding="utf-8"))["specimen_fingerprint"]

    spec = json.loads(SPEC_MANIFEST.read_text(encoding="utf-8"))
    binding = spec["bound_run_binding"]["run_ids"][CATEGORY]
    if binding != RUN_ID:
        raise SystemExit(f"拒绝：run_id 与冻结绑定不一致 freeze={binding} now={RUN_ID}")

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

    print(json.dumps({
        "run_version": RUN_VERSION,
        "run_id": RUN_ID,
        "specimen_fingerprint": spec_fp,
        "specimen_manifest_sha256": want,
        "manifest_ordering_holds": run_facts["manifest_ordering_holds"],
        "run_started_utc": run_facts["run_started_utc"],
        "run_finished_utc": run_facts["run_finished_utc"],
        "run_summary": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
