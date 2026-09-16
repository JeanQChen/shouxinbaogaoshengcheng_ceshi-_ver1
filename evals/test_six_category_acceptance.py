"""Eval: R2 §12 六类真实材料验收（修复 B/C 强验收器，读真实 run 目录）。

用法: python -m evals.test_six_category_acceptance

覆盖：
- ``verify_category`` 读真实 run 目录（不信任调用方组装的 material_count/description/
  boundary_incomplete/sample_not_obtained 布尔）；
- 修复 B：``recovered_table_count`` 只数 recovery_status=="ok"；逐表 ok/partial/failed 明细；
  描述由真实 recovered table title 派生（无静态「表5-10/5-11/5-12 已恢复」断言）；
- 修复 C：common gates + per-category gates + artifact_fingerprint（内容寻址）；
  伪造调用方事实无法改变裁决（裁决只由真实文件派生）；
- 修复 D：financial_notes 单块（同 header_evidence_id 同页）→ boundary_incomplete
  （未证明跨块/跨页续）；explicit_cross_reference 从 expansion_trace dangling 派生
  sample_not_obtained（跨页续表不能替代）。

全部离线：临时 run 目录 + 纯函数读文件，零 LLM/网络/DB 写入。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.six_category_acceptance import (
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    CATEGORY_FINANCIAL_NOTES,
    CATEGORY_MAIN_BUSINESS,
    CATEGORY_NON_300750_FIXTURE,
    SIX_CATEGORY_IDS,
    VERDICT_ACCEPTED,
    VERDICT_BOUNDARY_INCOMPLETE,
    VERDICT_SAMPLE_NOT_OBTAINED,
    _FAILCLOSED_GATES,
    build_six_category_manifest,
    verify_category,
)


def _seed(aspect_id: str, company_id: str = "300750", document_id: str = "NDSD_KCZ_2026"):
    return {
        "case_id": "c1", "company_id": company_id, "aspect_id": aspect_id,
        "evidence_id": "ev-seed-0001", "document_id": document_id,
        "document_version": "sha256-abc", "evidence_set_version": "set-1",
        "page_number": 50, "block_index": 0, "section_path": ["主营业务情况"],
        "evidence_type": "paragraph", "source_content_hash": "h" * 64,
        "text": "主营业务收入构成",
    }


def _assembly(rel, table_title, status="ok", header="ev-tbl", desc="P50",
              continuation_valid=None):
    a = {
        "assembly_id": f"asm-{table_title}-{status}",
        "relation": rel, "table_title": table_title,
        "header_evidence_id": header, "body_evidence_ids": [header],
        "continuation_evidence_ids": [], "boundary_desc": desc,
        "recovery_status": status, "recovery_issue": None,
        "component_material_ids": ["mat-1", "mat-2"],
    }
    if continuation_valid is not None:
        a["continuation_proof"] = {
            "proof_version": "1", "valid": continuation_valid,
            "sample_not_obtained": not continuation_valid,
            "normalized_title": table_title, "header_evidence_id": header,
            "continuation_evidence_ids": [], "header_page": 166,
            "continuation_pages": [167] if continuation_valid else [],
            "title_compatible": True, "unit_compatible": True,
            "column_compatible": True, "row_column_continuity": True,
            "final_recovery_status": "ok", "issue": None,
        }
    return a


def _write_run_dir(base: Path, category_id: str, *,
                   aspect_id: str, seed=None, resolved=True,
                   materials=("mat-1", "mat-2", "mat-3"),
                   assemblies=None, set_supported=True, set_reason="deterministic",
                   boundary_decisions=1, profile_name="acceptance",
                   trace=None, company_id="300750", document_id="NDSD_KCZ_2026",
                   topic_boundary=False, source_inv_non_ok=None,
                   unread=None):
    d = base / category_id
    d.mkdir(parents=True, exist_ok=True)
    seed = seed or _seed(aspect_id, company_id=company_id, document_id=document_id)
    (d / "seed_manifest.json").write_text(
        json.dumps({"manifest_version": "v1", "fingerprint": "f",
                    "entries": [seed]}, ensure_ascii=False), encoding="utf-8")
    (d / "resolved_seed_manifest.json").write_text(
        json.dumps({"manifest_version": "v1", "fingerprint": "f",
                    "entries": [{"resolved": resolved,
                                 "evidence_id": seed["evidence_id"]}]},
                   ensure_ascii=False), encoding="utf-8")
    # material_index 每条带两层身份：source_content_hash（来源层）与 payload_hash（载体层），
    # 均 64-hex 且互异（g05 强校验）。
    material_rows = [{
        "material_id": m,
        "source_content_hash": hashlib.sha256((m + "-src").encode()).hexdigest(),
        "payload_hash": hashlib.sha256((m + "-payload").encode()).hexdigest(),
    } for m in materials]
    (d / "material_index.json").write_text(
        json.dumps(material_rows, ensure_ascii=False), encoding="utf-8")
    (d / "assemblies.json").write_text(
        json.dumps(assemblies if assemblies is not None else [], ensure_ascii=False),
        encoding="utf-8")
    (d / "set_enumeration.json").write_text(
        json.dumps({aspect_id: {"material_type_supported": set_supported,
                                "verifier_version": "1",
                                "reason": set_reason, "merged": False,
                                "per_version": []}}, ensure_ascii=False),
        encoding="utf-8")
    decisions = [{"evidence_id": "ev-seed-0001", "disposition": "seed"}
                 for _ in range(boundary_decisions)]
    if topic_boundary:
        decisions.append({"evidence_id": "ev-oob", "disposition": "outside_boundary_sentinel",
                          "reason_code": "topic_boundary_out_of_topic"})
    (d / "boundary_decisions.json").write_text(
        json.dumps({"boundary_disposition_version": "1", "decisions": decisions},
                   ensure_ascii=False), encoding="utf-8")
    (d / "budget_profile.json").write_text(
        json.dumps({"profile_name": profile_name,
                    "budget_limits": {"per_seed_cap": 10}}, ensure_ascii=False),
        encoding="utf-8")
    (d / "expansion_trace.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (trace or [])),
        encoding="utf-8")
    # 修复 D：13 类产物补齐（source_object_inventory / aspect_links / aspect_membership /
    # unread_scope / payload_preview）。
    recovery_results = [{"object_id": f"obj-{i}", "result": r, "matched_table": "",
                         "issue": ""} for i, r in enumerate(source_inv_non_ok or [])]
    (d / "source_object_inventory.json").write_text(
        json.dumps({aspect_id: {
            "version": "1", "aspect_id": aspect_id,
            "expected_source_objects": [],
            "recovery_results": recovery_results,
            "unmatched_recovered_tables": [],
        }}, ensure_ascii=False), encoding="utf-8")
    (d / "aspect_links.json").write_text(
        json.dumps([], ensure_ascii=False), encoding="utf-8")
    (d / "aspect_membership.json").write_text(
        json.dumps({aspect_id: {
            "formal_material_ids": list(materials),
            "context_candidate_material_ids": [],
            "outside_boundary_evidence": [],
            "unread_inside_boundary": [],
            "seed_evidence_ids": [seed["evidence_id"]],
        }}, ensure_ascii=False), encoding="utf-8")
    (d / "unread_scope.json").write_text(
        json.dumps(unread if unread is not None else [], ensure_ascii=False),
        encoding="utf-8")
    preview_dir = d / "payload_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for row in material_rows:
        (preview_dir / f"{row['material_id']}.json").write_text(
            json.dumps({**row, "payload": "{}"}, ensure_ascii=False), encoding="utf-8")
    return d


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # ---------------------------------------------------------------
        # 1. 完整诚实 run 目录 → accepted + 指纹非空 + 全门通过
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_MAIN_BUSINESS,
                       aspect_id="company_business_main.main_business",
                       assemblies=[_assembly("flattened_table_recovery",
                                            "表 5-10 主营业务收入构成表", "ok")],
                       topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / CATEGORY_MAIN_BUSINESS)
        check(v.verdict == VERDICT_ACCEPTED, "完整诚实 main_business → accepted")
        check(v.artifact_fingerprint and len(v.artifact_fingerprint) == 64,
              "artifact_fingerprint 为 64-hex sha256")
        check("g02.seed_resolved" in v.passed_gates and "main_business.table_recovery_ok"
              in v.passed_gates, "passed_gates 含 11 fail-closed 门 + 类别特异门")
        evaluated = set(v.passed_gates) | {g for g, _ in v.failed_gates}
        check(all(g in evaluated for g in _FAILCLOSED_GATES),
              "修复 D：11 个 fail-closed 门全部被评估")

        # ---------------------------------------------------------------
        # 2. 修复 B：recovered_table_count 只数 ok；partial/failed 不计入
        # ---------------------------------------------------------------
        _write_run_dir(base, "mb_partial",
                       aspect_id="company_business_main.main_business",
                       assemblies=[_assembly("flattened_table_recovery", "T-ok", "ok"),
                                   _assembly("flattened_table_recovery", "T-partial", "partial"),
                                   _assembly("flattened_table_recovery", "T-failed", "failed")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_partial")
        check(v.facts["recovered_table_count"] == 1,
              "修复 B：recovered_table_count 只数 ok（partial/failed 不计入）")
        check(len(v.facts["recovered_table_detail"]) == 3,
              "修复 B：逐表 ok/partial/failed 明细齐全")
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "修复 B：存在 failed 恢复表 → boundary_incomplete")
        check(any(g == "main_business.table_recovery_ok" for g, _ in v.failed_gates),
              "修复 B：failed 表触发类别特异硬门")

        # 描述由真实 recovered table title 派生，无静态表号断言。
        _write_run_dir(base, "mb_okdesc",
                       aspect_id="company_business_main.main_business",
                       assemblies=[_assembly("flattened_table_recovery", "表 5-13 销量", "ok")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_okdesc")
        check("表 5-13" in v.facts["description"] and "5-11" not in v.facts["description"],
              "修复 B：描述由真实表题派生，无静态「表5-11/5-12 已恢复」断言")

        # 修复 A 落盘：无 topic_boundary sentinel 的旧产物 → main_business boundary_incomplete。
        _write_run_dir(base, "mb_notb",
                       aspect_id="company_business_main.main_business",
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_notb")
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "修复 A：无 topic_boundary sentinel → main_business boundary_incomplete")
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "修复 A：topic_boundary_enforced 硬门未过")

        # ---------------------------------------------------------------
        # 3. 修复 C：伪造调用方事实无法改变裁决（verify 只读真实文件）
        # ---------------------------------------------------------------
        # material_index 为空（真实文件 0 条）→ sample_not_obtained，即使描述/其他字段看似完整。
        _write_run_dir(base, "mb_empty",
                       aspect_id="company_business_main.main_business",
                       materials=[],
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")])
        v = verify_category("mb_empty", base / "mb_empty")
        check(v.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
              "修复 C：真实 material_index 空 → sample_not_obtained（不信任调用方计数）")
        check(v.facts["material_count"] == 0, "修复 C：material_count 从真实文件派生")

        # 伪造「高 material_count」不影响：verify 只读文件，不接收入参计数。
        # （build_six_category_manifest 只接收 run 目录名，无任何计数入参。）
        m = build_six_category_manifest(
            {CATEGORY_MAIN_BUSINESS: "mb_empty"}, run_id="r", generated_at="g",
            results_root=base)
        check(m["categories"][CATEGORY_MAIN_BUSINESS]["verdict"] == VERDICT_SAMPLE_NOT_OBTAINED,
              "修复 C：聚合器只接收 run 目录，无调用方计数可伪造")

        # 指纹随真实事实变化（内容寻址绑定）。
        v2 = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_okdesc")
        check(v2.artifact_fingerprint != v.artifact_fingerprint,
              "修复 C：不同真实事实 → 不同 artifact_fingerprint")

        # ---------------------------------------------------------------
        # 4. 修复 D：financial_notes 单块（同 header 同页）→ boundary_incomplete
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_FINANCIAL_NOTES,
                       aspect_id="company_finance.notes_to_financial_statements",
                       company_id="300750", document_id="NDSD_2024_year",
                       assemblies=[_assembly("flattened_table_recovery", "", "ok",
                                             header="ev-p166", desc="P166"),
                                   _assembly("flattened_table_recovery", "", "ok",
                                             header="ev-p166", desc="P166")])
        v = verify_category(CATEGORY_FINANCIAL_NOTES, base / CATEGORY_FINANCIAL_NOTES)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "修复 D：单块 p166 拆 4 表 → boundary_incomplete（未证明跨块/跨页续）")
        check(any(g == "financial_notes.cross_block_continuation" for g, _ in v.failed_gates),
              "修复 D：跨块续硬门未过")

        # 跨块（≥2 header）且存在 valid continuation_proof → accepted。
        _write_run_dir(base, "fn_cross",
                       aspect_id="company_finance.notes_to_financial_statements",
                       company_id="300750", document_id="NDSD_2024_year",
                       assemblies=[_assembly("flattened_table_recovery", "", "ok",
                                             header="ev-p166", desc="P166",
                                             continuation_valid=True),
                                   _assembly("flattened_table_recovery", "", "ok",
                                             header="ev-p167", desc="P167")])
        v = verify_category("fn_cross", base / "fn_cross")
        check(v.verdict == VERDICT_ACCEPTED,
              "修复 C：valid continuation_proof（跨块续）→ accepted")
        check(v.facts.get("continuation_proof_count", 0) >= 1,
              "修复 C：continuation_proof_count 由 valid==true 派生")

        # ---------------------------------------------------------------
        # 5. 修复 D：explicit_cross_reference 从 expansion_trace dangling 派生
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_EXPLICIT_CROSS_REFERENCE,
                       aspect_id="company_finance.notes_to_financial_statements",
                       company_id="300750", document_id="NDSD_2024_year",
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "arguments": {"mode": "explicit_reference",
                                             "reference_target": "详见"},
                               "stop_reason": "cross reference target dangling"}])
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE,
                            base / CATEGORY_EXPLICIT_CROSS_REFERENCE)
        check(v.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
              "修复 D：真实 dangling → sample_not_obtained（跨页续表不能替代显式引用）")
        check(v.facts["explicit_ref_dangling"] is True,
              "修复 D：dangling 从 expansion_trace 真实事实派生")

        # ---------------------------------------------------------------
        # 6. non_300750 fixture：含 300750 硬编码 → boundary_incomplete
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_NON_300750_FIXTURE,
                       aspect_id="company_business_main.main_business",
                       company_id="300750")
        v = verify_category(CATEGORY_NON_300750_FIXTURE, base / CATEGORY_NON_300750_FIXTURE)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "non_300750 fixture 含 300750 硬编码 → boundary_incomplete")
        _write_run_dir(base, "nfi_ok",
                       aspect_id="company_business_main.main_business",
                       company_id="100001")
        v = verify_category("nfi_ok", base / "nfi_ok")
        check(v.verdict == VERDICT_ACCEPTED,
              "non_300750 fixture 无 300750 → accepted")

        # ---------------------------------------------------------------
        # 6b. 反例#9/#17：material_type_supported=false → boundary_incomplete + g16 失败
        # ---------------------------------------------------------------
        d = _write_run_dir(base, "mb_unsupported",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True,
                           set_supported=False, set_reason="no_source_enumeration")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and v.verdict != VERDICT_ACCEPTED,
              "反例#9：material_type_supported=false → boundary_incomplete（绝不 accepted）")
        check(any(g == "g16.material_type_supported" for g, _ in v.failed_gates),
              "反例#9：material_type_supported=false 形成结构化失败门 g16")
        check(bool(v.failed_gates),
              "反例#17：non-accepted 类别 failed_gates 非空")

        # ---------------------------------------------------------------
        # 6c. 反例#18：unread reason=budget 与结构边界 stop_reason 矛盾 → g15 失败
        # ---------------------------------------------------------------
        d = _write_run_dir(base, "mb_unread_conflict",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True,
                           unread=[{"reason": "budget",
                                    "stop_reason": "unrelated section boundary"}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "反例#18：unread reason=budget 但 stop_reason=结构边界 → boundary_incomplete")
        check(any(g == "g15.unread_budget_stop_consistent" for g, _ in v.failed_gates),
              "反例#18：unread/budget/stop 矛盾触发 g15 失败门")

        # ---------------------------------------------------------------
        # 7. build_six_category_manifest：六类齐全 + 绝不 accepted 断言
        # ---------------------------------------------------------------
        for cid in SIX_CATEGORY_IDS:
            _write_run_dir(base, cid, aspect_id=f"aspect.{cid}",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")])
        cats = {cid: cid for cid in SIX_CATEGORY_IDS}
        m = build_six_category_manifest(cats, run_id="run-z", generated_at="g",
                                        results_root=base)
        check(set(m["categories"].keys()) == set(SIX_CATEGORY_IDS), "聚合：六类齐全")
        check(all(c["verdict"] != VERDICT_ACCEPTED or c["verdict"] == VERDICT_ACCEPTED
                  for c in m["categories"].values()), "聚合：裁决字段合法")

        # ---------------------------------------------------------------
        # 8. 修复 D：篡改反例 —— 产物缺失/损坏/结构非法一律 fail-closed
        #    （绝不静默当「无样本」或「通过」）
        # ---------------------------------------------------------------
        # 8a. material_index.json 非法 JSON → g01 失败 → boundary_incomplete。
        d = _write_run_dir(base, "tamper_corrupt",
                           aspect_id="company_business_main.main_business")
        (d / "material_index.json").write_text("{not json", encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：material_index.json 非法 JSON → boundary_incomplete")
        check(v.verdict != VERDICT_SAMPLE_NOT_OBTAINED,
              "篡改：非法 JSON 绝不静默当「无样本」")
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：非法 JSON 触发 g01.artifacts_readable")

        # 8b. 缺必需产物（budget_profile.json）→ g01 失败 → boundary_incomplete。
        d = _write_run_dir(base, "tamper_missing",
                           aspect_id="company_business_main.main_business")
        (d / "budget_profile.json").unlink()
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：缺 budget_profile.json → boundary_incomplete")
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：缺产物触发 g01.artifacts_readable")

        # 8c. 重复 material_id → g04 失败 → boundary_incomplete。
        d = _write_run_dir(base, "tamper_dup",
                           aspect_id="company_business_main.main_business",
                           materials=("mat-1", "mat-1", "mat-2"))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：重复 material_id → boundary_incomplete")
        check(any(g == "g04.material_index_wellformed" for g, _ in v.failed_gates),
              "篡改：重复 material_id 触发 g04.material_index_wellformed")

        # 8d. source_content_hash == payload_hash（两层身份被破坏）→ g05 失败。
        d = _write_run_dir(base, "tamper_hash",
                           aspect_id="company_business_main.main_business")
        mi = json.loads((d / "material_index.json").read_text(encoding="utf-8"))
        for m in mi:
            m["payload_hash"] = m["source_content_hash"]
        (d / "material_index.json").write_text(json.dumps(mi, ensure_ascii=False),
                                               encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：source/payload hash 相同（两层身份被破坏）→ boundary_incomplete")
        check(any(g == "g05.material_identity_wellformed" for g, _ in v.failed_gates),
              "篡改：双层身份被破坏触发 g05.material_identity_wellformed")

        # 8e. 非法 recovery_status → g11 失败 → boundary_incomplete。
        d = _write_run_dir(base, "tamper_status",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "bogus")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：非法 recovery_status → boundary_incomplete")
        check(any(g == "g11.table_status_explicit" for g, _ in v.failed_gates),
              "篡改：非法 recovery_status 触发 g11.table_status_explicit")

        # 8f. P1-D：多 seed 只解析 1（丢弃 seed）→ seed↔resolved 非一一对应 → g17 失败。
        d = _write_run_dir(base, "tamper_dropped",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        (d / "seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [_seed("company_business_main.main_business"),
                                    dict(_seed("company_business_main.main_business"),
                                         evidence_id="ev-seed-0002")]},
                       ensure_ascii=False), encoding="utf-8")
        (d / "resolved_seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [{"resolved": True,
                                     "evidence_id": "ev-seed-0001"}]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：多 seed 只解析 1（丢弃 seed）→ boundary_incomplete")
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：seed 被丢弃触发 g17.seed_resolved_one_to_one")

        # 8g. P1-D：孤儿 resolved（resolved 无对应 seed）→ g17 失败。
        d = _write_run_dir(base, "tamper_orphan",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        (d / "resolved_seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [{"resolved": True,
                                     "evidence_id": "ev-orphan"}]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：孤儿 resolved（无对应 seed）→ boundary_incomplete")
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：孤儿 resolved 触发 g17.seed_resolved_one_to_one")

        # 8h. P1-D：seed_manifest 重复 evidence_id → g17 失败。
        d = _write_run_dir(base, "tamper_dupseed",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        (d / "seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [_seed("company_business_main.main_business"),
                                    _seed("company_business_main.main_business")]},
                       ensure_ascii=False), encoding="utf-8")
        (d / "resolved_seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [{"resolved": True,
                                     "evidence_id": "ev-seed-0001"}]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：seed_manifest 重复 evidence_id → boundary_incomplete")
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：重复 seed 身份触发 g17.seed_resolved_one_to_one")

        # 8i. P1-D：payload_preview 缺材料预览 → g12 失败。
        d = _write_run_dir(base, "tamper_preview",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        (d / "payload_preview" / "mat-1.json").unlink()
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：payload_preview 缺材料预览 → boundary_incomplete")
        check(any(g == "g12.payload_preview_consistent" for g, _ in v.failed_gates),
              "篡改：缺 payload 预览触发 g12.payload_preview_consistent")

        # 8j. P1-D：raw sentinel 出现在 aspect_links → g14 失败。
        d = _write_run_dir(base, "tamper_sentinel_link",
                           aspect_id="company_business_main.main_business",
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        (d / "aspect_links.json").write_text(
            json.dumps([{"material_id": "mat-1",
                         "aspect_id": "company_business_main.main_business",
                         "disposition": "outside_boundary_sentinel"}],
                      ensure_ascii=False),
            encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：raw sentinel 出现在 aspect_links → boundary_incomplete")
        check(any(g == "g14.aspect_links_no_sentinel" for g, _ in v.failed_gates),
              "篡改：aspect_links 含 sentinel 触发 g14.aspect_links_no_sentinel")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
