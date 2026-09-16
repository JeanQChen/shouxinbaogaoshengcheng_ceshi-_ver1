"""R2 §六：六类真实材料验收 manifest 生成器（v13 = 三个生产 P1 窄范围收口后的一轮）。

与 v12 的差别
-------------
1. 类别→run 绑定**只**读自 v13 事前冻结清单 ``r2_xref_specimen_v13_20260916``（本轮只重跑
   ``major_subsidiaries``，其余五个样本绑定 v12 已冻结 run，逐条标注
   ``reused_from_previous_round``）；本脚本没有「优先选 resolved 的 run」逻辑；
2. 冻结清单必须满足「生成时 v13 run 目录尚不存在」（``frozen_before_run.all_run_dirs_absent``），
   且 ``specimen_file_facts.json`` / ``run_file_facts.json`` 证明清单先于 run 存在且未被改写；
3. 冻结清单记录的输入身份（evidence.db / harness.db / 冻结 Contract / v2 seed manifest /
   本轮改动代码）sha256 必须与当前一致，任一漂移即 fail-closed；
4. 受验 run（显式引用正样本）的 seed manifest 必须与冻结样本同身份（逐字段），否则 fail-closed。

其余与 v12 一致：从各 run 目录原始事实独立重算聚合，三轴（material_state /
capability_verdict / report_impact）不互相自动映射，诚实的负面材料状态绝不标记 accepted。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    build_six_category_manifest,
    verify_category,
)

RESULTS = Path("evaluation/results")
RUN_VERSION = "v13"
SUFFIX = "20260916"
RUN_ID = f"r2_sixcat_{RUN_VERSION}_{SUFFIX}"
SPEC_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
SPEC_PATH = SPEC_DIR / "specimen_manifest.json"
SPEC_FACTS = SPEC_DIR / "specimen_file_facts.json"
RUN_FACTS = SPEC_DIR / "run_file_facts.json"
OUT_DIR = RESULTS / f"r2_six_category_acceptance_{RUN_VERSION}_{SUFFIX}"

_ASPECT_TO_CATEGORY = {
    "company_business_main.main_business": "main_business",
    "company_competitiveness.core_competitiveness": "core_competitiveness",
    "company_subsidiaries.major_subsidiaries": "major_subsidiaries",
    "company_finance.notes_to_financial_statements": "financial_notes",
}


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _load_specimen() -> tuple[dict, list[str]]:
    """读冻结清单并做事前性/完整性核对；返回 (spec, problems)。"""
    problems: list[str] = []
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    if not spec.get("frozen_before_run", {}).get("all_run_dirs_absent"):
        problems.append("冻结清单不是在 v13 run 目录出现之前生成的（存在事后绑定风险）")
    if SPEC_FACTS.exists():
        want = json.loads(SPEC_FACTS.read_text(encoding="utf-8"))[
            "specimen_manifest"]["sha256"]
        got = _sha256_file(SPEC_PATH)
        if want != got:
            problems.append(f"冻结清单在生成后已被改写：freeze={want} now={got}")
    else:
        problems.append("缺 specimen_file_facts.json（无法证明清单落盘时刻/未被改写）")
    if RUN_FACTS.exists():
        rf = json.loads(RUN_FACTS.read_text(encoding="utf-8"))
        if not rf.get("manifest_ordering_holds"):
            problems.append("run_file_facts 未证明「清单先于 run 存在且未被改写」")
        if rf["specimen_manifest"]["sha256_before_run"] != rf["specimen_manifest"]["sha256_after_run"]:
            problems.append("清单哈希在 run 前后不一致（run 期间被改写）")
    else:
        problems.append("缺 run_file_facts.json（无法证明 run 在清单之后发生）")

    ident = spec["input_identity"]
    checks = {"evidence_db": Path("data/evidence.db"),
              "harness_db": Path("data/harness.db"),
              "contract": Path("templates/contracts/standard_v3.yaml")}
    for key, path in checks.items():
        want = (ident.get(key) or {}).get("sha256")
        got = _sha256_file(path) if path.exists() else None
        if want != got:
            problems.append(f"{key} sha256 漂移：freeze={want} now={got}")
    for rel, fact in (ident.get("production_and_test_code") or {}).items():
        want = (fact or {}).get("sha256")
        got = _sha256_file(Path(rel)) if Path(rel).exists() else None
        if want != got:
            problems.append(f"code {rel} sha256 漂移：freeze={want} now={got}")
    for category, fact in (ident.get("seed_manifests") or {}).items():
        got = _sha256_file(Path(fact["path"])) if Path(fact["path"]).exists() else None
        if got != fact.get("file_sha256"):
            problems.append(f"seed manifest {category} sha256 漂移")
    return spec, problems


def _bound_run_dirs(spec: dict) -> dict:
    """类别→run 目录**只**来自冻结清单（不做任何按结果的重新挑选）。"""
    run_ids = spec["bound_run_binding"]["run_ids"]
    out: dict[str, str] = {}
    expected = set(_ASPECT_TO_CATEGORY.values()) | {"non_300750_fixture"}
    missing = expected - set(run_ids)
    if missing:
        raise SystemExit(f"拒绝：冻结清单缺少类别 run 绑定 {sorted(missing)}")
    for category, rid in run_ids.items():
        out[category] = f"r2_material_slice_{rid}"
    xref = spec["bound_run_binding"]["explicit_cross_reference_binding"]["run_id"]
    out[CATEGORY_EXPLICIT_CROSS_REFERENCE] = f"r2_material_slice_{xref}"
    return out


def _check_bound_sample_identity(spec: dict, run_dirs: dict) -> list[str]:
    """受验 run 的 seed manifest 必须与冻结样本同身份（逐字段）。"""
    pos = spec["positive_sample"]
    problems: list[str] = []
    rid = spec["bound_run_binding"]["explicit_cross_reference_binding"]["run_id"]
    run_dir = RESULTS / run_dirs[CATEGORY_EXPLICIT_CROSS_REFERENCE]
    sm_path = run_dir / "seed_manifest.json"
    if not sm_path.exists():
        return [f"受验 run 缺 seed_manifest.json：{sm_path}"]
    entries = json.loads(sm_path.read_text(encoding="utf-8")).get("entries") or []
    hit = [e for e in entries if str(e.get("evidence_id")) == pos["seed"]["evidence_id"]]
    if not hit:
        problems.append(f"受验 run {rid} 的 seed manifest 不含冻结 seed")
        return problems
    keys = ("aspect_id", "company_id", "document_id", "document_version",
            "evidence_set_version", "page_number", "block_index", "source_content_hash")
    for k in keys:
        if hit[0].get(k) != pos["seed"].get(k):
            problems.append(f"冻结 seed 字段 {k} 不一致：freeze={pos['seed'].get(k)} "
                            f"run={hit[0].get(k)}")
    anchor = pos["anchor_block"]
    from harness.evidence_reader import ReadonlyEvidenceReader
    reader = ReadonlyEvidenceReader(Path("data/evidence.db"))
    blk = reader.get_block(pos["seed"]["evidence_id"])
    got = hashlib.sha256(((blk.text if blk else "") or "").encode("utf-8")).hexdigest()
    if got != anchor["text_sha256"]:
        problems.append("锚点块原文 sha256 与冻结值不一致（输入语料漂移）")
    return problems


def _xref_rows(spec: dict, run_dirs: dict) -> list[dict]:
    """逐 run 独立重算显式引用四态（生产验收器）；本轮重跑/复用来源逐条披露。"""
    rerun = spec["bound_run_binding"].get("rerun_this_round") or {}
    reused = spec["bound_run_binding"].get("reused_from_previous_round") or {}
    rows: list[dict] = []
    for cid, run_name in run_dirs.items():
        run_dir = RESULTS / run_name
        if not run_dir.is_dir():
            rows.append({"category_of_run": cid, "run": run_name, "state": "<run dir missing>"})
            continue
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, run_dir)
        ref = (v.facts or {}).get("explicit_reference_audit") or {}
        rows.append({
            "category_of_run": cid,
            "run": run_name,
            "run_provenance": ("rerun_in_v13" if cid in rerun else
                               ("reused_from_v12" if cid in reused else "unknown")),
            "state": ref.get("state"),
            "declared_targets": ref.get("declared_targets"),
            "resolution_targets": ref.get("resolution_targets"),
            "verified_binding_targets": ref.get("verified_binding_targets"),
            "resolved_targets": ref.get("resolved_targets"),
            "unbacked_outputs": ref.get("unbacked_outputs"),
            "table_ref_attempt_count": ref.get("table_ref_attempt_count"),
            "named_ref_attempt_count": ref.get("named_ref_attempt_count"),
            "reference_binding_problems": ref.get("reference_binding_problems"),
            "duplicate_consumption": ref.get("duplicate_consumption"),
            "same_document_bound": ref.get("same_document_bound"),
            "capability_verdict": v.capability_verdict,
            "material_state": v.material_state,
            "detail": str(ref.get("detail"))[:400],
        })
    return rows


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if OUT_DIR.exists():
        raise SystemExit(f"拒绝：{OUT_DIR.name} 已存在（v13 必须写入全新目录）")

    spec, spec_problems = _load_specimen()
    run_dirs = _bound_run_dirs(spec)
    problems = spec_problems + _check_bound_sample_identity(spec, run_dirs)
    if problems:
        for p in problems:
            print("[fail-closed]", p)
        raise SystemExit("拒绝：冻结清单/受验 run 与事前冻结不一致，聚合 fail-closed")

    now = _dt.datetime.now(_dt.timezone.utc)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_six_category_manifest(
        run_dirs, run_id=RUN_ID, generated_at=now.strftime("%Y%m%dT%H%M%SZ"),
        results_root=RESULTS)
    manifest["specimen_binding"] = {
        "specimen_manifest": str(SPEC_PATH).replace("\\", "/"),
        "specimen_fingerprint": spec["specimen_fingerprint"],
        "specimen_frozen_at_utc": spec["frozen_at_utc_iso"],
        "frozen_before_run": spec["frozen_before_run"],
        "bound_run_dirs": run_dirs,
        "run_provenance": {
            "rerun_in_v13": spec["bound_run_binding"].get("rerun_this_round"),
            "reused_from_previous_round": spec["bound_run_binding"].get("reused_from_previous_round"),
        },
        "result_driven_reselection": False,
        "note": "类别→run 绑定只读自事前冻结清单；本脚本无「优先选 resolved 的 run」逻辑；"
                "本轮只有 major_subsidiaries 是新 run，其余五类沿用 v12 已冻结 run（不重跑、不调参）",
    }
    with open(OUT_DIR / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    lines = [f"# R2 §六 六类真实材料验收（{RUN_VERSION}：三个生产 P1 窄范围收口后的一轮）\n",
             f"受验样本事前冻结清单：`{SPEC_PATH.as_posix()}`（指纹 "
             f"`{spec['specimen_fingerprint']}`，真实 UTC 冻结时刻 "
             f"`{spec['frozen_at_utc_iso']}`，生成时 v13 run 目录尚不存在）。\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 run 目录原始事实独立重算聚合，非 runner 自报；"
             "三轴（material_state / capability_verdict / report_impact）不互相自动映射；"
             "诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。\n",
             "**样本绑定不可事后挑选**：类别→run 映射只读自冻结清单。本轮**只重跑** "
             "`company_subsidiaries.major_subsidiaries`（显式引用正样本），"
             "其余五个真实样本绑定 v12 已冻结 run，逐条标注 `reused_from_previous_round`。\n",
             "| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |\n",
             "|---|---|---|---|---|---|\n"]
    for cid, c in manifest["categories"].items():
        lines.append(f"| {cid} | {c.get('material_state','')} | "
                     f"{c.get('capability_verdict','')} | {c.get('report_impact','')} | "
                     f"{c['verdict']} | {c['reason']} |\n")
    lines.append("\n## 显式引用逐 run 原始状态（生产验收器独立重算，全 specimen 披露）\n\n")
    lines.append("```json\n" + json.dumps(_xref_rows(spec, run_dirs), ensure_ascii=False, indent=2)
                 + "\n```\n\n")
    lines.append("\n## 关闭条件（§七，实施方不自行宣布关闭）\n\n")
    for k, v in (manifest.get("closure_conditions") or {}).items():
        lines.append(f"### `{k}`\n\n```json\n"
                     + json.dumps(v, ensure_ascii=False, indent=2) + "\n```\n\n")
    (OUT_DIR / "README.md").write_text("".join(lines), encoding="utf-8")

    print(json.dumps({
        "out_dir": str(OUT_DIR),
        "generated_at_utc": now.isoformat(),
        "specimen_fingerprint": spec["specimen_fingerprint"],
        "manifest_version": manifest["manifest_version"],
        "categories": {c: {"material_state": v.get("material_state"),
                           "capability_verdict": v.get("capability_verdict"),
                           "report_impact": v.get("report_impact"),
                           "verdict": v["verdict"], "failed_gates": v.get("failed_gates"),
                           "reason": v["reason"]}
                       for c, v in manifest["categories"].items()},
        "closure_conditions": {k: v.get("satisfied")
                               for k, v in (manifest.get("closure_conditions") or {}).items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
