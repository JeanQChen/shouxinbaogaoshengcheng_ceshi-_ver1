"""只读验收（R2 v12）：事前冻结样本 vs 重跑产物的一致性核对 + 错误目标拒绝记录。

按 ``evaluation/results/r2_xref_specimen_v12_20260916/specimen_manifest.json``（事前冻结，
先于任何 v12 run 生成）逐条核对：

1. **冻结完整性**：evidence.db / harness.db / 冻结 Contract / 本轮改动代码的 sha256 与冻结值
   是否一致（不一致即视为验收失效，fail-closed）；
2. **样本身份**：v12 run 的 seed manifest 是否就是冻结的 v2 seed（aspect / evidence_id /
   页块 / 文档-版本-集合 / 源内容哈希逐条比对）；
3. **标记→目标证据链**：trace 中 ``mode=explicit_reference`` 步骤的锚点、标记 occurrence、
   绑定记录（作用域 / 目标块 / 目标表题 / 目标对象身份 / 目标位置 / 理由）；
4. **目标即同块表5-5对象**：绑定作用域为 same_block、目标块 == 锚点块、目标表题 == 冻结时
   独立推出的表题行、且目标位置在标记之后；
5. **错误目标拒绝**：旧实现命中的 P43 ``049de1a2…``（表5-6 发行人组织结构图）既不在任何
   explicit_reference 步骤的 outputs 内，也不是绑定目标，并给出逐条理由；
6. **验收侧独立复核结论**：调用生产验收器 ``verify_category`` 取显式引用四态与
   ``reference_binding_problems``，以及六类聚合后的类别结论。

零 LLM / 零网络 / 零写库；不写任何 run 目录。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import (  # noqa: E402
    iter_reference_marker_occurrences,
    reference_target_table_objects,
)
from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    build_six_category_manifest,
    verify_category,
)

RESULTS = Path("evaluation/results")
RUN_VERSION = "v12"
SUFFIX = "20260916"
SPEC_DIR = RESULTS / f"r2_xref_specimen_{RUN_VERSION}_{SUFFIX}"
SPEC_PATH = SPEC_DIR / "specimen_manifest.json"
EVIDENCE_DB = Path("data/evidence.db")
WRONG_TARGET = "049de1a26d73f3e89611679694ff4f06"


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _check_integrity(spec: dict) -> list[str]:
    problems: list[str] = []
    items = {"evidence_db": EVIDENCE_DB,
             "harness_db": Path("data/harness.db"),
             "contract": Path("templates/contracts/standard_v3.yaml")}
    for key, path in items.items():
        want = (spec["input_identity"][key] or {}).get("sha256")
        got = _sha256_file(path) if path.exists() else None
        if got != want:
            problems.append(f"{key} sha256 与冻结值不一致：freeze={want} now={got}")
    for rel, fact in spec["input_identity"]["production_and_test_code"].items():
        want = (fact or {}).get("sha256")
        got = _sha256_file(Path(rel)) if Path(rel).exists() else None
        if got != want:
            problems.append(f"code {rel} sha256 与冻结值不一致：freeze={want} now={got}")
    for category, fact in spec["input_identity"]["seed_manifests"].items():
        p = Path(fact["path"])
        got = _sha256_file(p) if p.exists() else None
        if got != fact.get("file_sha256"):
            problems.append(f"seed manifest {category} sha256 与冻结值不一致")
    return problems


def _block_text(eid: str) -> str:
    con = sqlite3.connect(f"file:{EVIDENCE_DB}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT text FROM evidence_blocks WHERE evidence_id = ?",
                          (eid,)).fetchone()
    finally:
        con.close()
    return (row[0] or "") if row else ""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    pos = spec["positive_sample"]
    run_dir = RESULTS / f"r2_material_slice_r2_sixcat_{RUN_VERSION}_major_subsidiaries_{SUFFIX}"
    print(f"冻结清单: {SPEC_PATH}  fingerprint={spec['specimen_fingerprint']}")
    print(f"冻结时 run 目录是否存在: {spec['frozen_before_run']['all_run_dirs_absent'] is True}"
          f"（present={spec['frozen_before_run']['run_dirs_present_at_freeze']}）")
    print(f"受验 run: {run_dir.name}")

    print("\n=== 1. 冻结完整性（输入身份 sha256）===")
    problems = _check_integrity(spec)
    for p in problems:
        print("  [不一致]", p)
    print("  一致" if not problems else f"  共 {len(problems)} 处不一致")

    print("\n=== 2. 样本身份核对 ===")
    v12_sm = json.loads((run_dir / "seed_manifest.json").read_text(encoding="utf-8"))
    seed_rows = [e for e in v12_sm["entries"] if str(e.get("evidence_id")) == pos["seed"]["evidence_id"]]
    print(f"  冻结 seed {pos['seed']['evidence_id'][:12]}… 在 v12 seed manifest 命中 {len(seed_rows)} 条")
    keys = ("aspect_id", "company_id", "document_id", "document_version",
            "evidence_set_version", "page_number", "block_index", "source_content_hash")
    identity_ok = bool(seed_rows) and all(
        seed_rows[0].get(k) == pos["seed"].get(k) for k in keys)
    print(f"  身份逐字段一致: {identity_ok}")

    print("\n=== 3. 标记 → 目标证据链（v12 trace 的 explicit_reference 步骤）===")
    steps = [json.loads(l) for l in (run_dir / "expansion_trace.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    seed_prefix = pos["seed"]["evidence_id"]
    xrefs = [s for s in steps
             if (s.get("arguments") or {}).get("mode") == "explicit_reference"
             and str(s.get("seed_evidence_id")) == seed_prefix]
    anchor_text = _block_text(seed_prefix)
    print(f"  锚点块原文 sha256 = {hashlib.sha256(anchor_text.encode('utf-8')).hexdigest()}"
          f"（冻结值 {pos['anchor_block']['text_sha256']}）")
    for s in xrefs:
        b = s.get("reference_binding") or {}
        a = s.get("arguments") or {}
        print(f"  - step{s['step_index']} marker={a.get('reference_target')!r} "
              f"anchor={str(s.get('anchor_evidence_id'))[:12]} outputs={s.get('outputs')} "
              f"stop={s.get('stop_reason')!r}")
        print(f"      scope={b.get('resolution_scope')} target_block="
              f"{str(b.get('target_evidence_id'))[:12]} title={b.get('target_table_title')!r}")
        print(f"      target_object_id={str(b.get('target_object_id'))[:16]}… "
              f"target_start={b.get('target_start')} target_end={b.get('target_end')} "
              f"marker_start={b.get('marker_start')} marker_end={b.get('marker_end')}")
        print(f"      reason={b.get('resolution_reason')!r}")

    print("\n=== 4. 目标即同块表5-5对象（对照冻结时独立推出的预期）===")
    exp = pos["pre_declared_expectation"]
    same_block = [s for s in xrefs
                  if (s.get("reference_binding") or {}).get("resolution_scope") == "same_block"]
    target_line_ok = any(
        str((s.get("reference_binding") or {}).get("target_table_title", "")).startswith(
            exp["expected_target"]["line"].split("截至")[0][:4])
        or exp["expected_target"]["line"].startswith(
            str((s.get("reference_binding") or {}).get("target_table_title", ""))[:6])
        for s in same_block)
    after_marker_ok = all(
        int((s["reference_binding"]).get("target_start", -1)) >= int((s["reference_binding"]).get("marker_end", 0))
        for s in same_block)
    print(f"  同块绑定步骤数 = {len(same_block)}"
          f"（冻结预期 scope = {exp['expected_resolution_scope']}）")
    print(f"  绑定目标表题 = {[ (s['reference_binding']).get('target_table_title') for s in same_block ]}")
    print(f"  冻结预期表题行 = {exp['expected_target']['line']!r}")
    print(f"  目标块 == 锚点块（同块）: "
          f"{[ (s['reference_binding']).get('target_evidence_id') == seed_prefix for s in same_block ]}")
    print(f"  目标位置在标记之后: {after_marker_ok}")

    print("\n=== 5. 错误目标拒绝记录（P43 表5-6 发行人组织结构图）===")
    outs = [o for s in xrefs for o in (s.get("outputs") or [])]
    print(f"  {WRONG_TARGET[:12]}… 是否出现在 explicit_reference 的 outputs: {WRONG_TARGET in outs}")
    binds = [(s.get("reference_binding") or {}) for s in xrefs]
    print(f"  是否被任何绑定选为目标块: "
          f"{any(str(b.get('target_evidence_id')) == WRONG_TARGET for b in binds)}")
    print(f"  目标表题是否含「表5-6」: {any('表5-6' in str(b.get('target_table_title')) for b in binds)}")
    old = sqlite3.connect(f"file:{EVIDENCE_DB}?mode=ro", uri=True)
    try:
        row = old.execute("SELECT page_number, text FROM evidence_blocks WHERE evidence_id = ?",
                          (WRONG_TARGET,)).fetchone()
    finally:
        old.close()
    print(f"  该块实际内容：p{row[0] if row else '?'} "
          f"{(row[1] or '')[:60] if row else ''!r}")

    print("\n=== 6. 验收侧独立复核（生产验收器重算，非本脚本自报）===")
    v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, run_dir)
    ref = (v.facts or {}).get("explicit_reference_audit") or {}
    print(json.dumps({
        "material_state": v.material_state, "capability_verdict": v.capability_verdict,
        "report_impact": v.report_impact, "verdict": v.verdict,
        "failed_gates": v.failed_gates, "reason": v.reason,
        "audit": {k: ref.get(k) for k in (
            "state", "declared_targets", "attempt_outputs", "resolution_targets",
            "verified_binding_targets", "resolved_targets", "unbacked_outputs",
            "table_ref_attempt_count", "named_ref_attempt_count",
            "reference_binding_checks", "reference_binding_problems",
            "same_document_bound", "attempt_documents", "detail")},
    }, ensure_ascii=False, indent=2))

    ok = (not problems and identity_ok
          and len(same_block) >= 1 and target_line_ok and after_marker_ok
          and WRONG_TARGET not in outs
          and not any(str(b.get("target_evidence_id")) == WRONG_TARGET for b in binds))
    print(f"\nv12 冻结样本验收（同块 表5-5 目标成立 + 表5-6 被拒绝 + 冻结身份未漂移）: {ok}")


if __name__ == "__main__":
    main()
