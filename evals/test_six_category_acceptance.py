"""Eval: R2 §12 六类真实材料验收（修复 B/C/D 强验收器，读真实 run 目录）。

用法: python -m evals.test_six_category_acceptance

覆盖：
- ``verify_category`` 读真实 run 目录（不信任调用方组装的 material_count/description/
  boundary_incomplete/sample_not_obtained 布尔）；
- 修复 B：``recovered_table_count`` 只数 recovery_status=="ok"；逐表 ok/partial/failed 明细；
  描述由真实 recovered table title 派生（无静态「表5-10/5-11/5-12 已恢复」断言）；
- 修复 C：common gates + per-category gates + artifact_fingerprint（内容寻址）；
  伪造调用方事实无法改变裁决（裁决只由真实文件派生）；
- 修复 D（三轴状态，§二）：``material_state`` / ``capability_verdict`` / ``report_impact``
  分别建模、互不自动映射；``verdict`` 只是「accepted ⟺ 材料完整且无失败门」的兼容视图；
- 修复 D.4（独立重算）：fixture 落盘**真实** payload 信封与 content-addressed material_id /
  assembly_id，验收器独立重算 payload hash、material 身份、assembly 身份与跨产物闭合；
  任一不闭合 → fail-closed（绝不信任 runner 自报）。

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
    CAPABILITY_FAIL,
    CAPABILITY_PASS,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    CATEGORY_FINANCIAL_NOTES,
    CATEGORY_MAIN_BUSINESS,
    CATEGORY_NON_300750_FIXTURE,
    MATERIAL_STATE_BOUNDARY_INCOMPLETE,
    MATERIAL_STATE_COMPLETE,
    MATERIAL_STATE_INVALID,
    MATERIAL_STATE_NOT_OBTAINED,
    MATERIAL_STATE_PARTIAL,
    MATERIAL_STATE_UNSUPPORTED,
    REPORT_IMPACT_BLOCKING,
    REPORT_IMPACT_NON_BLOCKING,
    SIX_CATEGORY_IDS,
    VERDICT_ACCEPTED,
    VERDICT_BOUNDARY_INCOMPLETE,
    VERDICT_SAMPLE_NOT_OBTAINED,
    _FAILCLOSED_GATES,
    build_six_category_manifest,
    verify_category,
)

DOC_VERSION = "sha256-doc-1"
DOC_VERSION_B = "sha256-doc-2"
SET_VERSION = "set-1"
AUTHORITY = "local_evidence_db"

MB_ASPECT = "company_business_main.main_business"
NOTES_ASPECT = "company_finance.notes_to_financial_statements"


# ---------------------------------------------------------------------------
# 独立实现的内容寻址身份（与 harness.topic_materials 规范形同形；fixture 侧独立书写，
# 使「验收器独立重算」不是拿验收器自己的私有函数回代）。
# ---------------------------------------------------------------------------

def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _locator_key(document_id, document_version, set_version, section_path, page,
                 block_index, table_title, offset) -> str:
    return "|".join([
        str(document_id or ""), str(document_version or ""), str(set_version or ""),
        str(section_path or ""), str(page), str(block_index), str(table_title or ""),
        "" if offset is None else str(offset),
    ])


def _material_id_of(material_type, evidence_id, source_identity, document_version,
                    set_version, locator_key, payload_hash) -> str:
    identity = [material_type, evidence_id, source_identity, document_version,
                set_version, locator_key, payload_hash]
    return "mat-" + _sha256_hex(_canonical_json(identity).encode("utf-8"))[:32]


def _assembly_id_of(component_material_ids, relation, discriminator="") -> str:
    raw = _canonical_json([relation, list(component_material_ids), discriminator])
    return "asm-" + _sha256_hex(raw.encode("utf-8"))[:32]


def _structure_identity(title, unit, headers, rows, total_row) -> str:
    return _canonical_json([
        title or "", unit, list(headers or []), [list(r) for r in (rows or [])],
        (list(total_row) if total_row else None)])


# ---------------------------------------------------------------------------
# fixture 构建
# ---------------------------------------------------------------------------

def _seed(aspect_id: str, company_id: str = "300750", document_id: str = "NDSD_KCZ_2026"):
    return {
        "case_id": "c1", "company_id": company_id, "aspect_id": aspect_id,
        "evidence_id": "ev-seed-0001", "document_id": document_id,
        "document_version": DOC_VERSION, "evidence_set_version": SET_VERSION,
        "page_number": 50, "block_index": 0, "section_path": ["主营业务情况"],
        "evidence_type": "paragraph", "source_content_hash": _sha256_hex(b"seed-src"),
        "text": "主营业务收入构成",
    }


def _material(key: str, *, aspect_id: str, company_id: str, document_id: str,
              page: int = 50, block_index: int = 0, section_path: str = "主营业务情况",
              material_type: str = "evidence_span", table_title=None,
              doc_version: str = DOC_VERSION):
    """一个**真实可复算**的材料：落盘信封 bytes → payload_hash → material_id。

    ``doc_version`` 用于构造**真实的多 document_version 样本**（§四.D.2/D.3）：版本来自真实
    payload 信封的 ``document_identity``，验收器只认这里的版本，绝不采信自报版本集合。
    """
    evidence_id = f"ev-{key}"
    source_content_hash = _sha256_hex((key + "-source").encode("utf-8"))
    env = {
        "object_type": material_type,
        "evidence_id": evidence_id,
        "authority_identity": AUTHORITY,
        "source_content_hash": source_content_hash,
        "document_identity": {
            "company_id": company_id, "document_id": document_id,
            "document_version": doc_version, "evidence_set_version": SET_VERSION,
        },
        "locator": {
            "document_id": document_id, "document_version": doc_version,
            "section_path": section_path, "page": page,
            "block_range": [block_index, block_index], "table_title": table_title,
            "offset": None,
        },
        "payload": {"text": f"{key} 主营业务收入构成", "table_title": table_title},
    }
    payload_text = _canonical_json(env)
    payload_hash = _sha256_hex(payload_text.encode("utf-8"))
    material_id = _material_id_of(
        material_type, evidence_id, AUTHORITY, doc_version, SET_VERSION,
        _locator_key(document_id, doc_version, SET_VERSION, section_path, page,
                     block_index, table_title, None), payload_hash)
    entry = {
        "material_id": material_id, "material_type": material_type,
        "component_evidence_id": evidence_id,
        "source_content_hash": source_content_hash, "payload_hash": payload_hash,
        "aspect_ids": [aspect_id],
    }
    return entry, {**entry, "payload": payload_text}


def _assembly(rel, table_title, status="ok", components=("m1", "m2"), *,
              unit="单位：万元", headers=("项目", "金额"), rows=(("主营业务收入", "1"),),
              total_row=("合计", "1"), desc="P50", continuation_valid=None,
              continuation_issue=None, component_order=False, table_number=""):
    """assembly 规格（``_components`` 为 material key；id 由 ``_write_run_dir`` 最终化）。"""
    spec = {
        "_components": list(components), "relation": rel, "table_title": table_title,
        "recovery_status": status, "recovery_issue": None, "boundary_desc": desc,
        "unit": unit, "headers": list(headers), "rows": [list(r) for r in rows],
        "total_row": list(total_row) if total_row else None,
        "table_number": table_number,
    }
    if continuation_valid is not None:
        # 与生产 ``TableContinuationProof.to_dict()`` 同形：正向链的**每一项**都必须落盘
        # （归一化表题 / 四项兼容 / 恢复状态 / 四个结构见证 / 逐 span 真实结构事实），
        # 否则验收侧无法独立复算，只能被迫相信自报 ``valid`` —— 这正是 §三 要收紧的形态。
        # 生产侧 ``title_compatible`` 要求表题非空（``_normalize_table_identity`` 非空
        # 且续页重排表头），空表题的表链在真实产物中**不可能** valid，fixture 不得伪造。
        spec["continuation_proof"] = {
            "proof_version": "2", "valid": continuation_valid,
            "sample_not_obtained": not continuation_valid,
            "identity_source": "recovered_structure",
            "normalized_title": table_title, "issue": continuation_issue,
            "header_page": 166, "continuation_pages": [167] if continuation_valid else [],
            "title_compatible": bool(continuation_valid),
            "unit_compatible": bool(continuation_valid),
            "column_compatible": bool(continuation_valid),
            "row_column_continuity": bool(continuation_valid),
            "final_recovery_status": status,
            "header_repeat_verified": bool(continuation_valid),
            "boundary_consecutive": bool(continuation_valid),
            "section_path_shared": bool(continuation_valid),
            "same_document_verified": bool(continuation_valid),
            "continued_from": "p166",
            "_component_order": component_order,
        }
    return spec


def _component_of(key: str, by_key: dict) -> dict:
    """组件键 → 材料身份。未解析的键回落为「外键悬空」（仅用于构造 B.7 反例）。"""
    return by_key.get(key, {"material_id": key, "component_evidence_id": f"ev-{key}"})


def _finalize_assembly(spec: dict, by_key: dict) -> dict:
    comp = [_component_of(k, by_key)["material_id"] for k in spec["_components"]]
    # 生产 ``TableAssembly`` 的续页真实身份 = 表头组件之后**真实存在的**续页组件。
    # 旧 fixture 把它硬编码为 ``[]``，使「自报 valid=true 但没有任何续页证据」这种
    # 真实产物中不可能出现的形态被当成正向样本 —— 正是 §三 要收紧的伪造形态。
    cont_evs = [_component_of(k, by_key)["component_evidence_id"]
                for k in spec["_components"][1:]]
    relation = spec["relation"]
    disc = ""
    if relation == "flattened_table_recovery":
        disc = _structure_identity(spec["table_title"], spec["unit"], spec["headers"],
                                   spec["rows"], spec["total_row"])
    a = {
        "assembly_id": _assembly_id_of(comp, relation, disc),
        "relation": relation, "table_title": spec["table_title"], "unit": spec["unit"],
        "headers": list(spec["headers"]), "rows": [list(r) for r in spec["rows"]],
        "total_row": spec["total_row"],
        "header_evidence_id": _component_of(spec["_components"][0],
                                            by_key)["component_evidence_id"],
        "body_evidence_ids": [_component_of(k, by_key)["component_evidence_id"]
                              for k in spec["_components"]],
        "continuation_evidence_ids": list(cont_evs),
        "boundary_desc": spec["boundary_desc"],
        "recovery_status": spec["recovery_status"],
        "recovery_issue": spec["recovery_issue"],
        "component_material_ids": comp,
    }
    proof = spec.get("continuation_proof")
    if proof is not None:
        proof = dict(proof)
        if proof.pop("_component_order", False):
            proof["component_order"] = list(comp)
        proof["header_evidence_id"] = a["header_evidence_id"]
        proof["continuation_evidence_ids"] = list(cont_evs)
        # §三.2：正向证明必须带**逐 span 真实结构事实**（绑定真实续页证据 id），
        # 否则验收侧无法独立复算，只能被迫相信自报 valid。
        proof["span_facts"] = ([
            {"evidence_id": e, "page": 167, "header_repeat_matched": True,
             "unit_conflict": [], "column_width_ok": True,
             "contributed_structure": True, "adjacent_to_previous": True}
            for e in cont_evs] if proof.get("valid") is True else [])
        a["continuation_proof"] = proof
    return a


_TABLE_RESULT = {"ok": "recovered_ok", "partial": "recovered_partial",
                 "failed": "recovery_failed"}


def _write_run_dir(base: Path, category_id: str, *,
                   aspect_id: str, seed=None, resolved=True,
                   materials=("m1", "m2", "m3"),
                   assemblies=None, set_supported=True, set_reason="deterministic",
                   boundary_decisions=1, profile_name="acceptance",
                   trace=None, company_id="300750", document_id="NDSD_KCZ_2026",
                   topic_boundary=False, source_inv_non_ok=None,
                   unread=None, boundary_status=None, rolling_targets=None,
                   rolling_direction_unread=None, inventory_binding=True,
                   set_enum_key=None, doc_versions=(DOC_VERSION,),
                   set_per_version_versions=None, set_per_version_reason=None):
    d = base / category_id
    d.mkdir(parents=True, exist_ok=True)
    # 无 topic_boundary sentinel ⇒ 边界未被真正验证（运行时记录必须诚实报 incomplete）。
    if boundary_status is None:
        boundary_status = "verified" if topic_boundary else "incomplete"
    seed = seed or _seed(aspect_id, company_id=company_id, document_id=document_id)
    (d / "seed_manifest.json").write_text(
        _canonical_json({"manifest_version": "v1", "fingerprint": "f",
                         "entries": [seed]}), encoding="utf-8")
    (d / "resolved_seed_manifest.json").write_text(
        _canonical_json({"manifest_version": "v1", "fingerprint": "f",
                         "entries": [{"resolved": resolved,
                                      "evidence_id": seed["evidence_id"]}]}),
        encoding="utf-8")

    # ---- 材料：真实信封 + 内容寻址身份（验收器独立重算）----
    by_key: dict[str, dict] = {}
    previews: list[dict] = []
    keys = list(materials)
    versions = tuple(doc_versions) or (DOC_VERSION,)
    for i, key in enumerate(keys):
        entry, preview = _material(key, aspect_id=aspect_id, company_id=company_id,
                                   document_id=document_id,
                                   page=50 + keys.index(key), block_index=keys.index(key),
                                   doc_version=versions[i % len(versions)])
        by_key[key] = entry
        previews.append(preview)
    (d / "material_index.json").write_text(
        _canonical_json([dict(p) for p in previews]), encoding="utf-8")
    preview_dir = d / "payload_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for p in previews:
        (preview_dir / f"{p['material_id']}.json").write_text(
            _canonical_json(p), encoding="utf-8")

    # ---- assemblies：组件外键 + content-addressed id ----
    specs = list(assemblies or [])
    finalized = [_finalize_assembly(s, by_key) for s in specs]
    (d / "assemblies.json").write_text(_canonical_json(finalized), encoding="utf-8")

    # ---- 材料 ↔ aspect 关联（与 material_index 同一材料集合，闭合）----
    ordered = sorted({p["material_id"] for p in previews})
    (d / "aspect_links.json").write_text(
        _canonical_json([{"material_id": m, "aspect_id": aspect_id, "role": "source"}
                         for m in ordered]), encoding="utf-8")
    (d / "aspect_membership.json").write_text(
        _canonical_json({aspect_id: {
            "formal_material_ids": ordered,
            "context_candidate_material_ids": [],
            # §四.A.8：主题外证据必须记录在案（哨兵证据被停止并留痕，绝不静默丢弃）。
            "outside_boundary_evidence": ["ev-oob"] if topic_boundary else [],
            "unread_inside_boundary": [],
            "seed_evidence_ids": [seed["evidence_id"]],
        }}), encoding="utf-8")

    # ---- set enumeration：逐版本身份（不合并跨版本）----
    enum_key = set_enum_key if set_enum_key is not None else aspect_id
    pv_versions = (tuple(set_per_version_versions) if set_per_version_versions is not None
                   else versions)
    pv_reason = set_per_version_reason if set_per_version_reason is not None else set_reason
    (d / "set_enumeration.json").write_text(
        _canonical_json({enum_key: {
            "material_type_supported": set_supported, "verifier_version": "1",
            "reason": set_reason, "merged": False,
            "per_version": [{
                "document_id": document_id, "document_version": dv,
                "source_boundary": f"{document_id}|{aspect_id}",
                "result": {"verifier_version": "1",
                           "material_type_supported": set_supported,
                           "reason": pv_reason},
            } for dv in pv_versions],
        }}), encoding="utf-8")

    decisions = [{"evidence_id": seed["evidence_id"], "disposition": "seed",
                  "aspect_id": aspect_id, "reason_code": "seed_entry"}
                 for _ in range(boundary_decisions)]
    if topic_boundary:
        # 与生产（``BoundaryDecision.to_dict()``）同形：真实块身份 + 方向 + 结构证据。
        # 空壳 reason_code（旧 fixture）会让「主题边界已执行」这一断言不可复核。
        decisions.append({
            "evidence_id": "ev-oob", "aspect_id": aspect_id,
            "disposition": "outside_boundary_sentinel",
            "reason_code": "topic_boundary_out_of_topic",
            "relation": "adjacent", "direction": "adjacent_blocks_after",
            "page_number": 51, "block_index": 3,
            "section_path": ["安全生产情况"], "evidence_type": "paragraph",
            "content_hash": _sha256_hex(b"oob-src"),
            "structural_signals": ["topic_boundary", "安全生产情况",
                                   "stop_direction", "true"],
        })
    (d / "boundary_decisions.json").write_text(
        _canonical_json({"boundary_disposition_version": "1", "decisions": decisions}),
        encoding="utf-8")
    (d / "budget_profile.json").write_text(
        _canonical_json({"profile_name": profile_name,
                         "budget_limits": {"per_seed_cap": 10,
                                           "rolling_target_probe": 4}}),
        encoding="utf-8")
    (d / "expansion_trace.jsonl").write_text(
        "".join(_canonical_json(r) + "\n" for r in (trace or [])), encoding="utf-8")

    # ---- 源对象清单 ↔ 持久化 assembly 逐对象闭合（单一恢复真相）----
    results: list[dict] = []
    expected: list[dict] = []
    if inventory_binding:
        for spec, a in zip(specs, finalized):
            if spec["relation"] != "flattened_table_recovery":
                continue
            num = spec.get("table_number") or ""
            oid = (f"table:{num}" if num
                   else f"title:{(spec['table_title'] or '').strip()}")
            kind = "table_number" if num else "table_start_signal"
            expected.append({"object_id": oid, "kind": kind,
                             "label": spec["table_title"] or "",
                             "document_id": document_id,
                             "document_version": DOC_VERSION,
                             "source_object_id": f"{document_id}|{DOC_VERSION}|{oid}"})
            res = _TABLE_RESULT.get(spec["recovery_status"], "target_not_obtained")
            # 与 ``source_object_inventory.reconcile_source_object_inventory`` 同形：
            # 命中持久化 assembly 的源对象一律绑定该 assembly_id（含 partial/failed，
            # 使「清单声明失败却绑定 assembly」这一矛盾显式暴露给闭合门）。
            results.append({
                "object_id": oid, "result": res, "matched_table": oid,
                "issue": "" if res == "recovered_ok" else "fixture：恢复未完整",
                "assembly_id": a["assembly_id"],
                "component_material_ids": a["component_material_ids"],
                "recovery_status": spec["recovery_status"],
                "recovery_reason": "",
            })
    for i, spec in enumerate(source_inv_non_ok or []):
        # §四.B.5/B.7 + §四.D.9/D.10：注入的源对象结果支持显式控制 result / issue /
        # recovery_reason，用于构造「诚实未获得（可归因）」与「不可归因」两类反例。
        if isinstance(spec, dict):
            res = spec.get("result", "target_not_obtained")
            issue = spec.get("issue", "fixture：注入未闭合源对象")
            rec_reason = spec.get("recovery_reason", "")
        else:
            res, issue, rec_reason = spec, "fixture：注入未闭合源对象", ""
        results.append({"object_id": f"table:extra-{i}", "result": res,
                        "matched_table": "", "issue": issue,
                        "assembly_id": "", "component_material_ids": [],
                        "recovery_status": "", "recovery_reason": rec_reason})
    (d / "source_object_inventory.json").write_text(
        _canonical_json({aspect_id: {
            "version": "1", "aspect_id": aspect_id,
            "recovery_source": "material_slice", "document_id": document_id,
            "source_boundary_identity": f"{document_id}|{aspect_id}",
            "expected_source_objects": expected,
            "recovery_results": results,
            "unmatched_recovered_tables": [], "orphan_assemblies": [],
        }}), encoding="utf-8")

    (d / "unread_scope.json").write_text(
        _canonical_json(list(unread or [])), encoding="utf-8")

    # ---- 运行时派生的边界验证记录（不靠 Contract 词表自洽）----
    # 形状必须与**真实 runner**（``harness.material_slice_runner``）落盘一致：逐 aspect 一条
    # 条目、其内 ``records`` 为**逐个 seed 独立身份**的验证记录（身份绝不合并成一条）。
    # 旧 fixture 自造扁平形状 → 与生产不一致，使验收器在真实产物上假失败。
    boundary_identity = f"{document_id}|{DOC_VERSION}|{aspect_id}"
    (d / "boundary_verification.json").write_text(
        _canonical_json({
            "boundary_verification_algorithm": "runtime_boundary_verification",
            "boundary_verification_version": "1",
            "dependency_fingerprint": _sha256_hex(b"r2-dependency-bundle"),
            "aspects": [{
                "aspect_id": aspect_id,
                "records": [{
                "aspect_id": aspect_id, "document_id": document_id,
                "document_version": DOC_VERSION, "evidence_set_version": SET_VERSION,
                "source_boundary_identity": boundary_identity,
                "verification_algorithm": "runtime_boundary_verification",
                "verification_version": "1",
                "dependency_fingerprint": _sha256_hex(b"r2-dependency-bundle"),
                "section_path": list(seed.get("section_path") or []),
                "heading_levels": [2], "topic_level": 2,
                "topic_level_source": "section_path_leaf",
                "in_topic_anchor": str(seed.get("section_path", ["主营业务"])[-1]),
                "sibling_evidence": [], "parent_evidence": [],
                "out_of_topic_evidence": [],
                "expansion_trace_fingerprint": _sha256_hex(b"trace"),
                "fragment_trace_fingerprint": _sha256_hex(b"fragment"),
                "unread_scope": [], "budget_exhaustion": [],
                "unresolved_ambiguity": [], "unresolved_reference": [],
                "unresolved_continuation": [],
                "status": boundary_status,
                "reason": "fixture：真实 section_path + 锚点 + 边界证据 + trace 指纹",
                "policy_version": "source_policy_v1",
                }],
            }],
        }), encoding="utf-8")

    # ---- 逐目标滚动观察 + 逐方向未读因果链 ----
    targets = rolling_targets
    if targets is None:
        targets = [{"target_id": "seed", "direction": "forward",
                    "budget_axis": "per_seed_cap", "budget_limit": 10,
                    "budget_consumed": 1, "limit_plus_one_probe": True,
                    "has_more": False, "unread_scope": [],
                    "unresolved_reason": "", "stop_reason": "table boundary reached"}]
    (d / "rolling_read_outcomes.json").write_text(
        _canonical_json({"rolling_read_outcomes_version": "1", "targets": list(targets),
                         "direction_unread": list(rolling_direction_unread or [])}),
        encoding="utf-8")
    return d


def _rewrite_links(d: Path, aspect_id: str):
    """按当前 material_index 重写 aspect_links / aspect_membership（篡改后重新闭合）。"""
    index = json.loads((d / "material_index.json").read_text(encoding="utf-8"))
    ordered = sorted({m["material_id"] for m in index})
    (d / "aspect_links.json").write_text(
        _canonical_json([{"material_id": m, "aspect_id": aspect_id, "role": "source"}
                         for m in ordered]), encoding="utf-8")
    mem = json.loads((d / "aspect_membership.json").read_text(encoding="utf-8"))
    mem[aspect_id]["formal_material_ids"] = ordered
    (d / "aspect_membership.json").write_text(_canonical_json(mem), encoding="utf-8")


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
        # 1. 完整诚实 run 目录 → 三轴 complete/PASS/non_blocking + 兼容视图 accepted
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_MAIN_BUSINESS,
                       aspect_id=MB_ASPECT,
                       materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery",
                                             "表 5-10 主营业务收入构成表", "ok",
                                             components=("m1", "m2"),
                                             table_number="5-10")],
                       topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / CATEGORY_MAIN_BUSINESS)
        check(v.material_state == MATERIAL_STATE_COMPLETE
              and v.capability_verdict == CAPABILITY_PASS
              and v.report_impact == REPORT_IMPACT_NON_BLOCKING,
              "完整诚实 main_business → 三轴 complete/PASS/non_blocking")
        check(v.verdict == VERDICT_ACCEPTED, "完整诚实 main_business → 兼容视图 accepted")
        check(not v.failed_gates, "完整诚实 main_business → 无失败门")
        check(v.artifact_fingerprint and len(v.artifact_fingerprint) == 64,
              "artifact_fingerprint 为 64-hex sha256")
        check("g02.seed_resolved" in v.passed_gates and "main_business.table_recovery_ok"
              in v.passed_gates, "passed_gates 含 fail-closed 门 + 类别特异门")
        evaluated = set(v.passed_gates) | {g for g, _ in v.failed_gates}
        check(all(g in evaluated for g in _FAILCLOSED_GATES),
              f"修复 D：{len(_FAILCLOSED_GATES)} 个 fail-closed 门全部被评估")
        check(not v.facts["payload_recompute_problems"]
              and not v.facts["material_identity_recompute_problems"]
              and not v.facts["assembly_closure_problems"]
              and not v.facts["source_inventory_closure_problems"]
              and not v.facts["cross_artifact_closure_problems"],
              "修复 D：真实 payload/身份/assembly/清单/跨产物独立重算零问题")
        check(v.facts["artifact_content_hashes"]
              and len(v.facts["artifact_content_hashes"]) >= 15,
              "修复 D.8：artifact fingerprint 绑定全部规范化产物内容")

        # ---------------------------------------------------------------
        # 2. 修复 B：recovered_table_count 只数 ok；partial/failed 不计入
        # ---------------------------------------------------------------
        _write_run_dir(base, "mb_partial",
                       aspect_id=MB_ASPECT,
                       materials=("m1", "m2", "m3", "m4", "m5", "m6"),
                       assemblies=[_assembly("flattened_table_recovery", "T-ok", "ok",
                                             components=("m1", "m2")),
                                   _assembly("flattened_table_recovery", "T-partial",
                                             "partial", components=("m3", "m4")),
                                   _assembly("flattened_table_recovery", "T-failed",
                                             "failed", components=("m5", "m6"))])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_partial")
        check(v.facts["recovered_table_count"] == 1,
              "修复 B：recovered_table_count 只数 ok（partial/failed 不计入）")
        check(len(v.facts["recovered_table_detail"]) == 3,
              "修复 B：逐表 ok/partial/failed 明细齐全")
        check(v.capability_verdict == CAPABILITY_FAIL
              and v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and v.material_state != MATERIAL_STATE_COMPLETE,
              "修复 B：存在 failed 恢复表 → capability FAIL + 非 accepted 非 complete")
        check(any(g == "main_business.table_recovery_ok" for g, _ in v.failed_gates),
              "修复 B：failed 表触发类别特异硬门")
        # failed 结果的 assembly 绑定与 closure 规则冲突（生产 reconcile 同形）→ 显式矛盾可见。
        check(any("矛盾声明" in det for g, det in v.failed_gates
                  if g == "g22.source_inventory_closure"),
              "修复 B.7：failed 结果绑定 assembly → 清单↔assembly 矛盾显式暴露（fail-closed）")

        # 描述由真实 recovered table title 派生，无静态表号断言。
        _write_run_dir(base, "mb_okdesc",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "表 5-13 销量",
                                             "ok", components=("m1", "m2"),
                                             table_number="5-13")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_okdesc")
        check("表 5-13" in v.facts["description"] and "5-11" not in v.facts["description"],
              "修复 B：描述由真实表题派生，无静态「表5-11/5-12 已恢复」断言")

        # 修复 A 落盘：无 topic_boundary sentinel 的旧产物 → main_business boundary_incomplete。
        _write_run_dir(base, "mb_notb",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_notb")
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and v.material_state == MATERIAL_STATE_BOUNDARY_INCOMPLETE,
              "修复 A：无 topic_boundary sentinel → main_business boundary_incomplete")
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "修复 A：topic_boundary_enforced 硬门未过")

        # ---------------------------------------------------------------
        # §四.A.8 反例：主题边界必须**结构性**证明（空壳 reason_code / 未记录 / 混入材料
        # 三类都必须失败）。旧实现只查 reason_code + disposition → 三类都会放行。
        # ---------------------------------------------------------------
        def _edit_json(path: Path, fn) -> None:
            obj = json.loads(path.read_text(encoding="utf-8"))
            fn(obj)
            path.write_text(_canonical_json(obj), encoding="utf-8")

        d = _write_run_dir(base, "mb_tb_hollow", aspect_id=MB_ASPECT,
                           materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        _edit_json(d / "boundary_decisions.json", lambda o: [
            dec.pop("structural_signals", None) for dec in o["decisions"]
            if dec.get("reason_code") == "topic_boundary_out_of_topic"])
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "A.8-反例：主题外决策无 structural_signals（空壳 reason_code）→ 门失败")

        d = _write_run_dir(base, "mb_tb_unrecorded", aspect_id=MB_ASPECT,
                           materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True)
        _edit_json(d / "aspect_membership.json",
                   lambda o: o[MB_ASPECT].__setitem__("outside_boundary_evidence", []))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "A.8-反例：主题外证据未记入 membership → 门失败（不得静默丢弃）")

        d = _write_run_dir(base, "mb_tb_leaked", aspect_id=MB_ASPECT,
                           materials=("m1", "oob"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "oob"))],
                           topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "A.8-反例：主题外（sibling）证据混入该 aspect 材料 → 门失败")

        # A.8 例外条件（多 seed 结构性冲突）：同一 aspect 的**另一个已声明 seed** 自身块，
        # 会是别的 seed 的方向停止点。仅当 ①该块在本 aspect 已解析 seed 清单内（独立于决策
        # 自报）且 ②决策列表中存在其 seed 处置记录（冲突显式落盘）时才不判混入。
        d = _write_run_dir(base, "mb_tb_own_seed", aspect_id=MB_ASPECT,
                           materials=("m1", "seed-0001"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "seed-0001"))],
                           topic_boundary=True)
        _edit_json(d / "boundary_decisions.json", lambda o: [
            dec.__setitem__("evidence_id", "ev-seed-0001")
            for dec in o["decisions"]
            if dec.get("disposition") == "outside_boundary_sentinel"])
        _edit_json(d / "aspect_membership.json",
                   lambda o: o[MB_ASPECT].__setitem__("outside_boundary_evidence",
                                                      ["ev-seed-0001"]))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(not any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "A.8-例外：自身已声明 seed 块同时是方向停止点 + 冲突已落盘 → 不判混入"
              "（多 seed 结构性冲突）")
        check(v.facts["topic_boundary_seed_conflicts"] == ["ev-seed-0001"],
              "A.8-例外：自身 seed 冲突逐条落盘为审计事实（topic_boundary_seed_conflicts）")

        # 反例：同一块在 seed 清单内，但决策列表**没有**它的 seed 处置记录 → 冲突未落盘 →
        # 仍判混入（不得靠「它可能是 seed」放行）。
        d = _write_run_dir(base, "mb_tb_own_seed_unrecorded", aspect_id=MB_ASPECT,
                           materials=("m1", "seed-0001"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "seed-0001"))],
                           topic_boundary=True)
        _edit_json(d / "boundary_decisions.json", lambda o: (
            [dec.__setitem__("evidence_id", "ev-seed-0001")
             for dec in o["decisions"]
             if dec.get("disposition") == "outside_boundary_sentinel"],
            [dec.__setitem__("evidence_id", "ev-other") for dec in o["decisions"]
             if dec.get("disposition") == "seed"]))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates),
              "A.8-反例：自身 seed 冲突未显式落盘（无 seed 处置记录）→ 门失败")

        # ---------------------------------------------------------------
        # 3. 修复 C：伪造调用方事实无法改变裁决（verify 只读真实文件）
        # ---------------------------------------------------------------
        # 诚实空材料（无 assembly）→ material_state=not_obtained + sample_not_obtained。
        _write_run_dir(base, "mb_empty", aspect_id=MB_ASPECT, materials=[],
                       assemblies=[])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_empty")
        check(v.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
              "修复 C：真实 material_index 空 → sample_not_obtained（不信任调用方计数）")
        check(v.material_state == MATERIAL_STATE_NOT_OBTAINED
              and v.facts["material_count"] == 0,
              "修复 C：material_state/material_count 从真实文件派生")

        # 聚合器只接收 run 目录名，无调用方计数可伪造。
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
        # 3b. 修复 B.7：inventory/assembly 矛盾（component 外键悬空）→ invalid，绝不 accepted
        # ---------------------------------------------------------------
        _write_run_dir(base, "mb_dangling_fk", aspect_id=MB_ASPECT, materials=[],
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                             components=("m1", "m2"))])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_dangling_fk")
        check(v.material_state == MATERIAL_STATE_INVALID
              and v.capability_verdict == CAPABILITY_FAIL,
              "反例 B.7：assembly component 外键悬空 → material_state=invalid + FAIL")
        check(any(g == "g21.assembly_closure" for g, _ in v.failed_gates),
              "反例 B.7：外键悬空触发 g21.assembly_closure")

        # ---------------------------------------------------------------
        # 4. 修复 C/D：财务附注同表续页证明
        # ---------------------------------------------------------------
        # 4a. 逐表无任何续页审计记录 → 能力失败（不得伪造通过）。
        _write_run_dir(base, CATEGORY_FINANCIAL_NOTES,
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       materials=("m1", "m2", "m3", "m4"),
                       assemblies=[_assembly("flattened_table_recovery", "", "ok",
                                             components=("m1", "m2")),
                                   _assembly("flattened_table_recovery", "", "ok",
                                             components=("m3", "m4"))])
        v = verify_category(CATEGORY_FINANCIAL_NOTES, base / CATEGORY_FINANCIAL_NOTES)
        check(v.capability_verdict == CAPABILITY_FAIL
              and any(g == "financial_notes.cross_block_continuation"
                      for g, _ in v.failed_gates),
              "修复 D：摊平表无续页审计记录 → capability FAIL（不伪造通过）")
        check(v.facts["unaudited_continuation_count"] >= 1,
              "修复 D：未审计续页记录数由真实产物派生")

        # 4b. §六：确无有效同表续页证明，但逐表诚实记录「未获得」→ boundary_incomplete + PASS。
        _write_run_dir(base, "fn_honest",
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       materials=("m1", "m2", "m3", "m4"),
                       assemblies=[_assembly("flattened_table_recovery", "", "ok",
                                             components=("m1", "m2"),
                                             continuation_valid=False,
                                             continuation_issue="同表续页证明未获得"),
                                   _assembly("flattened_table_recovery", "", "ok",
                                             components=("m3", "m4"),
                                             continuation_valid=False,
                                             continuation_issue="同表续页证明未获得")])
        v = verify_category(CATEGORY_FINANCIAL_NOTES, base / "fn_honest")
        check(v.material_state == MATERIAL_STATE_BOUNDARY_INCOMPLETE
              and v.capability_verdict == CAPABILITY_PASS,
              "§六：诚实「无续页正向样本」→ boundary_incomplete + capability PASS")
        check(v.report_impact == REPORT_IMPACT_BLOCKING,
              "§六：诚实缺口 material_state 非 complete → report_impact=blocking")
        check(v.verdict != VERDICT_ACCEPTED and bool(v.facts["honest_gap_reason"]),
              "§六：诚实缺口气缺口不得标记 accepted，并显式落盘 honest_gap_reason")
        check(not v.failed_gates, "§六：诚实负面状态绝不制造 failed gate（无代码失败）")

        # 4c. 存在真实正向续页证明（含 component_order / continued_from）→ complete + accepted。
        _write_run_dir(base, "fn_cross",
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       materials=("m1", "m2", "m3"),
                       topic_boundary=True,
                       assemblies=[_assembly("flattened_table_recovery",
                                             "表 1 主营业务收入构成表", "ok",
                                             components=("m1", "m2"),
                                             continuation_valid=True,
                                             component_order=True),
                                   _assembly("cross_page",
                                             "表 1 主营业务收入构成表", "ok",
                                             components=("m2", "m3"))])
        v = verify_category(CATEGORY_FINANCIAL_NOTES, base / "fn_cross")
        check(v.material_state == MATERIAL_STATE_COMPLETE
              and v.capability_verdict == CAPABILITY_PASS
              and v.verdict == VERDICT_ACCEPTED,
              "修复 C：valid continuation_proof（真实正向样本）→ complete + accepted")
        check(v.facts["continuation_proof_count"] >= 1,
              "修复 C：continuation_proof_count 由 valid==true 派生")

        # ---------------------------------------------------------------
        # 5. 修复 D：explicit_cross_reference 从 expansion_trace dangling 派生
        # v14 §三 P1-3：dangling 尝试也必须是**可信的 typed 命名引用尝试** —— 请求必须携带
        # occurrence 身份，且该身份落在发起块真实正文内（fixture 以**字面常量**声明它读到
        # 的位置，验收侧用自己的提取器独立重算，两边不共用常量）。
        # ---------------------------------------------------------------
        xr_seed = _seed(NOTES_ASPECT, document_id="NDSD_2024_year")
        xr_seed["text"] = "公司受限资产情况详见 30、不存在的资产。"
        _write_run_dir(base, CATEGORY_EXPLICIT_CROSS_REFERENCE,
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       seed=xr_seed, materials=("m1", "m2"),
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "anchor_evidence_id": "ev-seed-0001",
                               "arguments": {"mode": "explicit_reference",
                                             "reference_kind": "named",
                                             "reference_marker": "详见",
                                             "marker_start": 8, "marker_end": 10,
                                             "reference_occurrence_index": 0,
                                             "reference_target": "30、不存在的资产"},
                               "stop_reason": "cross reference target dangling"}])
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE,
                            base / CATEGORY_EXPLICIT_CROSS_REFERENCE)
        check(v.material_state == MATERIAL_STATE_NOT_OBTAINED
              and v.capability_verdict == CAPABILITY_PASS,
              "§六：真实 dangling 且审计完整 → not_obtained + capability PASS")
        check(v.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
              "修复 D：审计完整的 dangling → 兼容视图 sample_not_obtained")
        check(v.facts["explicit_ref_dangling"] is True,
              "修复 D：dangling 从 expansion_trace 真实事实派生")
        check(v.facts["explicit_reference_audit"]["named_ref_attempt_count"] == 1
              and not v.facts["explicit_reference_audit"]["named_ref_problems"]
              and v.facts["explicit_reference_audit"]["untyped_ref_attempt_count"] == 0,
              "§三 P1-3：dangling 尝试为 typed 命名引用且 occurrence 身份端到端同源")
        # 反例：同一条尝试把序号篡改成不存在的 occurrence → contradictory（fail-closed）。
        xr_tamper_seed = _seed(NOTES_ASPECT, document_id="NDSD_2024_year")
        xr_tamper_seed["text"] = "公司受限资产情况详见 30、不存在的资产。"
        _write_run_dir(base, "xr_typed_tamper",
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       seed=xr_tamper_seed, materials=("m1", "m2"),
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "anchor_evidence_id": "ev-seed-0001",
                               "arguments": {"mode": "explicit_reference",
                                             "reference_kind": "named",
                                             "reference_marker": "详见",
                                             "marker_start": 14, "marker_end": 16,
                                             "reference_occurrence_index": 0,
                                             "reference_target": "30、不存在的资产"},
                               "stop_reason": "cross reference target dangling"}])
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, base / "xr_typed_tamper")
        check(v.capability_verdict == CAPABILITY_FAIL
              and v.facts["explicit_reference_audit"]["state"] == "contradictory",
              "反例：typed 身份偏移被篡改 → contradictory（绝不按目标文本改判 dangling）")

        # 5b. dangling 审计链不完整（缺 reference_target）→ 能力失败。
        _write_run_dir(base, "xr_unaudited",
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       materials=("m1", "m2"),
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "arguments": {"mode": "explicit_reference"},
                               "stop_reason": "cross reference target dangling"}])
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, base / "xr_unaudited")
        check(v.capability_verdict == CAPABILITY_FAIL
              and any(g == "explicit_cross_reference.target_resolvable"
                      for g, _ in v.failed_gates),
              "§六：dangling 但审计链不完整 → capability FAIL")

        # 5c. 非显式引用步骤报 dangling → 跨产物闭合失败（跨页续表不能替代显式引用）。
        _write_run_dir(base, "xr_wrong_mode",
                       aspect_id=NOTES_ASPECT, document_id="NDSD_2024_year",
                       materials=("m1", "m2"),
                       trace=[{"step_index": 0, "action": "inspect_bounded",
                               "arguments": {"mode": "table_continuation"},
                               "stop_reason": "cross reference target dangling"}])
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, base / "xr_wrong_mode")
        check(any(g == "g23.cross_artifact_closure" for g, _ in v.failed_gates),
              "修复 D：非 explicit_reference 模式报 dangling → g23 闭合失败")
        check(v.material_state == MATERIAL_STATE_INVALID,
              "修复 D：跨产物不闭合 → material_state=invalid")

        # ---------------------------------------------------------------
        # 6. non_300750 fixture：含 300750 硬编码 → boundary_incomplete
        # ---------------------------------------------------------------
        _write_run_dir(base, CATEGORY_NON_300750_FIXTURE, aspect_id=MB_ASPECT,
                       company_id="300750")
        v = verify_category(CATEGORY_NON_300750_FIXTURE, base / CATEGORY_NON_300750_FIXTURE)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and v.capability_verdict == CAPABILITY_FAIL,
              "non_300750 fixture 含 300750 硬编码 → boundary_incomplete + FAIL")
        _write_run_dir(base, "nfi_ok", aspect_id=MB_ASPECT, company_id="100001",
                       materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                             components=("m1", "m2"))],
                       topic_boundary=True)
        v = verify_category(CATEGORY_NON_300750_FIXTURE, base / "nfi_ok")
        check(v.verdict == VERDICT_ACCEPTED and v.material_state == MATERIAL_STATE_COMPLETE,
              "non_300750 fixture 无 300750 → complete + accepted")

        # ---------------------------------------------------------------
        # 6b. 反例#9/#17：负面理由声称「本轮未覆盖」但真实有 source 材料 → 被反证 + g16 失败
        # ---------------------------------------------------------------
        d = _write_run_dir(base, "mb_unsupported",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True,
                           set_supported=False, set_reason="no_source_enumeration")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.material_state == MATERIAL_STATE_UNSUPPORTED
              and v.capability_verdict == CAPABILITY_FAIL
              and v.verdict != VERDICT_ACCEPTED,
              "反例#9：负面理由被真实产物反证 → unsupported + FAIL（绝不 accepted）")
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates),
              "反例#9：不可归因的负面枚举形成结构化失败门 g16")
        check(bool(v.failed_gates),
              "反例#17：能力失败的类别 failed_gates 非空")

        # ---------------------------------------------------------------
        # 6b-attr. §四.D.3/D.9/D.10：**诚实**负面枚举（多 document_version 共存，逐版本
        # 审计完整且与真实 source 材料一致）→ boundary_incomplete + capability PASS。
        # 旧实现把一切 material_type_supported=false 判成能力失败 → 该反例在旧实现下失败。
        # ---------------------------------------------------------------
        d = _write_run_dir(base, "mb_multi_version_honest",
                           aspect_id=MB_ASPECT, materials=("m1", "m2", "m3"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "m2", "m3"))],
                           topic_boundary=True, doc_versions=(DOC_VERSION, DOC_VERSION_B),
                           set_supported=False,
                           set_reason="多 document_version 共存，不合并为单一完整集合"
                                      "（每版本独立枚举，见 per_version）",
                           set_per_version_reason="未枚举出任何成员")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.material_state == MATERIAL_STATE_BOUNDARY_INCOMPLETE,
              "D.9：诚实负面枚举 → material_state=boundary_incomplete（不是 unsupported）")
        check(v.capability_verdict == CAPABILITY_PASS,
              "D.9：诚实负面枚举不得自动判 capability FAIL（§四.D.9/D.10）")
        check(v.report_impact == REPORT_IMPACT_BLOCKING,
              "D.9：boundary_incomplete → report_impact=blocking（三轴不自动映射）")
        check(not any(g.startswith("g16.") for g, _ in v.failed_gates),
              "D.9：诚实负面枚举不形成 g16 失败门")
        check(v.facts["enumeration_negative_attribution"] == "attributed",
              "D.9：可归因性事实落盘为 attributed")

        # 6b-attr-2. 负面但**静默丢弃**一个真实 source document_version → 未逐版本审计 → FAIL。
        d = _write_run_dir(base, "mb_enum_dropped_version",
                           aspect_id=MB_ASPECT, materials=("m1", "m2", "m3"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "m2", "m3"))],
                           topic_boundary=True, doc_versions=(DOC_VERSION, DOC_VERSION_B),
                           set_supported=False,
                           set_reason="多 document_version 共存，不合并为单一完整集合",
                           set_per_version_versions=(DOC_VERSION,))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates)
              and v.capability_verdict == CAPABILITY_FAIL,
              "D.9-反例：负面枚举静默丢弃真实 source 版本 → g16 失败（未逐版本审计）")

        # 6b-attr-3. 负面但 per_version 含**编造版本**（真实 source 中不存在）→ FAIL。
        d = _write_run_dir(base, "mb_enum_fabricated_version",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True, set_supported=False,
                           set_reason="多 document_version 共存，不合并为单一完整集合",
                           set_per_version_versions=(DOC_VERSION, DOC_VERSION_B))
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates)
              and v.capability_verdict == CAPABILITY_FAIL,
              "D.9-反例：per_version 编造真实材料中不存在的版本 → g16 失败")

        # 6b-attr-4. 负面理由声称「多 document_version 共存」但真实只有 1 个版本 → 被反证。
        d = _write_run_dir(base, "mb_enum_false_multiversion",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True, set_supported=False,
                           set_reason="多 document_version 共存，不合并为单一完整集合")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates),
              "D.9-反例：单版本却声称多版本共存 → 理由被真实产物反证 → g16 失败")

        # 6b-attr-5. 负面理由归因于**本管线自身缺陷**（未逐项闭合/外键悬空）→ 不是诚实材料结果。
        d = _write_run_dir(base, "mb_enum_pipeline_defect",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True, set_supported=False,
                           set_reason="source_object_inventory 未逐项闭合: table:9:recovery_failed"
                                      "(assembly 外键不存在)")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates)
              and v.material_state == MATERIAL_STATE_UNSUPPORTED,
              "D.9-反例：负面理由归因于本管线缺陷 → g16 失败 + unsupported（非诚实材料状态）")

        # 6b-attr-6. 负面但**无理由**（顶层 reason 为空）→ 不可归因 → FAIL。
        d = _write_run_dir(base, "mb_enum_no_reason",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True, set_supported=False, set_reason="")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g16.material_type_supported_attributed" for g, _ in v.failed_gates),
              "D.9-反例：负面枚举无 reason → 不可归因 → g16 失败")

        # ---------------------------------------------------------------
        # 6b-inv. §四.B.5/B.7 + §四.D.9/D.10：源对象清单逐项闭合——诚实的 target_not_obtained
        # 必须逐对象带原因（不得因材料本身的诚实缺失而误判能力失败）；**不可归因**的
        # 未获得 与 recovery_failed 才是能力失败。旧实现把一切非 recovered_ok 判成失败。
        # ---------------------------------------------------------------
        _write_run_dir(base, "mb_inv_honest_not_obtained",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                       topic_boundary=True,
                       source_inv_non_ok=[{"result": "target_not_obtained",
                                           "issue": "本轮材料中未出现该源对象（逐目标审计完整）"}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_inv_honest_not_obtained")
        check(not any(g == "main_business.source_object_inventory_closed"
                      for g, _ in v.failed_gates),
              "B.5-反例：诚实 target_not_obtained（带原因）不制造能力失败")
        check(v.facts["source_inventory_non_ok"],
              "B.5：诚实未获得仍逐对象落盘（不静默丢弃）")
        _write_run_dir(base, "mb_inv_unattributed",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                       topic_boundary=True,
                       source_inv_non_ok=[{"result": "target_not_obtained",
                                           "issue": "", "recovery_reason": ""}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_inv_unattributed")
        check(any(g == "main_business.source_object_inventory_closed"
                  for g, _ in v.failed_gates),
              "B.5-反例：无原因的 target_not_obtained → 不可归因 → 能力失败")
        _write_run_dir(base, "mb_inv_recovery_failed",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                       topic_boundary=True,
                       source_inv_non_ok=[{"result": "recovery_failed",
                                           "issue": "恢复失败"}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_inv_recovery_failed")
        check(any(g == "main_business.source_object_inventory_closed"
                  for g, _ in v.failed_gates),
              "B.5-反例：recovery_failed → 能力失败（不是诚实负面材料状态）")

        # 6b'. set_complete 类别缺 enumeration 条目 → 绝不当成「非 set_complete」放行。
        _write_run_dir(base, "mb_no_enum", aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                       topic_boundary=True, set_enum_key="other.aspect")
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_no_enum")
        check(any(g == "g18.set_complete_enumeration_identity" for g, _ in v.failed_gates),
              "反例#18a：set_complete 类别缺目标 aspect 条目 → g18 失败")
        check(v.material_state == MATERIAL_STATE_UNSUPPORTED,
              "反例#18a：集合枚举身份未建立 → 诚实 unsupported（绝不 complete）")

        # ---------------------------------------------------------------
        # 6c. 反例#18b：unread reason=budget 与结构边界 stop_reason 矛盾 → g15 失败
        # ---------------------------------------------------------------
        d = _write_run_dir(base, "mb_unread_conflict",
                           aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                           topic_boundary=True,
                           unread=[{"reason": "budget",
                                    "stop_reason": "unrelated section boundary",
                                    "direction": "forward"}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "反例#18b：unread reason=budget 但 stop_reason=结构边界 → boundary_incomplete")
        check(any(g == "g15.unread_budget_stop_consistent" for g, _ in v.failed_gates),
              "反例#18b：unread/budget/stop 矛盾触发 g15 失败门")

        # 6c'. 诚实 unread（预算耗尽 + 预算停止原因）→ partial，且无失败门。
        _write_run_dir(base, "mb_partial_state",
                       aspect_id=MB_ASPECT, materials=("m1", "m2"),
                       assemblies=[_assembly("flattened_table_recovery", "T", "ok")],
                       topic_boundary=True,
                       unread=[{"reason": "budget", "direction": "forward",
                                "stop_reason": "hard budget (per_seed_cap)"}],
                       rolling_direction_unread=[
                           {"direction": "forward", "reason": "budget",
                            "stop_reason": "hard budget (per_seed_cap)"}])
        v = verify_category(CATEGORY_MAIN_BUSINESS, base / "mb_partial_state")
        check(not v.failed_gates and v.capability_verdict == CAPABILITY_PASS,
              "三轴：诚实 unread（预算耗尽）无失败门 → capability PASS")
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "三轴：unread 未读 → 兼容视图非 accepted（unread 即材料不完整）")

        # ---------------------------------------------------------------
        # 7. build_six_category_manifest：六类齐全 + 三轴合法 + 关闭条件（不自行宣布关闭）
        # ---------------------------------------------------------------
        for cid in SIX_CATEGORY_IDS:
            _write_run_dir(base, cid, aspect_id=MB_ASPECT, materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1", "m2"))],
                           topic_boundary=True)
        cats = {cid: cid for cid in SIX_CATEGORY_IDS}
        m = build_six_category_manifest(cats, run_id="run-z", generated_at="g",
                                        results_root=base)
        check(set(m["categories"].keys()) == set(SIX_CATEGORY_IDS), "聚合：六类齐全")
        check(all(c["material_state"] in {
            MATERIAL_STATE_COMPLETE, MATERIAL_STATE_PARTIAL,
            MATERIAL_STATE_BOUNDARY_INCOMPLETE, MATERIAL_STATE_NOT_OBTAINED,
            MATERIAL_STATE_UNSUPPORTED, MATERIAL_STATE_INVALID}
            for c in m["categories"].values()), "聚合：material_state 合法")
        # 三轴一致：非 accepted 必须由三种**诚实**解释之一支撑 —— 失败门 / 非 complete 材料
        # 状态 / 能力未被测过（`capability_verdict=NOT_TESTED` 或存在未判定门）。§三/§五：
        # 「未测试」既不构成通过，也不允许无解释地非 accepted。
        def _non_accepted_explained(c):
            if c["verdict"] == VERDICT_ACCEPTED:
                return True
            return bool(c["failed_gates"]) \
                or c["material_state"] != MATERIAL_STATE_COMPLETE \
                or c["capability_verdict"] == "NOT_TESTED" \
                or bool(c.get("indeterminate_gates"))

        check(all(_non_accepted_explained(c) for c in m["categories"].values()),
              "聚合：非 accepted 必由失败门 / 非 complete 材料状态 / 能力未测（NOT_TESTED）解释（三轴一致）")
        # §五.5：NOT_TESTED 绝不等于 PASS —— capability 未测过的类别绝不 accepted。
        check(all(c["verdict"] != VERDICT_ACCEPTED
                  for c in m["categories"].values()
                  if c["capability_verdict"] == "NOT_TESTED"),
              "聚合：capability_verdict=NOT_TESTED 的类别绝不 accepted（未测试 ≠ 通过）")
        check(all(c["capability_verdict"] in {CAPABILITY_PASS, CAPABILITY_FAIL, "NOT_TESTED"}
                  for c in m["categories"].values()), "聚合：capability_verdict 合法")
        check(all(c["report_impact"] in {"blocking", "non_blocking", "audit_only"}
                  for c in m["categories"].values()), "聚合：report_impact 合法")
        check(m.get("acceptance_model") and "closure_conditions" in m
              and "positive_controls" in m,
              "聚合：三轴验收模型 + 关闭条件 + 正向能力样本已落盘")
        check("未声明" in str(m.get("closure_declaration", "")),
              "聚合：实施方不自行宣布 R2 关闭")
        # 未提供 run 目录 = NOT_TESTED，绝不冒充通过/失败。
        m2 = build_six_category_manifest({}, run_id="r2", generated_at="g",
                                         results_root=base)
        check(all(c["capability_verdict"] == "NOT_TESTED"
                  and c["material_state"] == MATERIAL_STATE_NOT_OBTAINED
                  and c["verdict"] != VERDICT_ACCEPTED
                  for c in m2["categories"].values()),
              "聚合：未提供 run 目录 → NOT_TESTED（绝不冒充通过/失败）")

        # ---------------------------------------------------------------
        # 8. 修复 D：篡改反例 —— 产物缺失/损坏/结构非法一律 fail-closed
        #    （绝不静默当「无样本」或「通过」，绝不抛未处理异常）
        # ---------------------------------------------------------------
        honest = dict(materials=("m1", "m2", "m3"),
                      assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                            components=("m1", "m2"))],
                      topic_boundary=True)

        # 8a. material_index.json 非法 JSON → g01 失败 → boundary_incomplete。
        d = _write_run_dir(base, "tamper_corrupt", aspect_id=MB_ASPECT, **honest)
        (d / "material_index.json").write_text("{not json", encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and v.verdict != VERDICT_SAMPLE_NOT_OBTAINED,
              "篡改：material_index.json 非法 JSON → boundary_incomplete（不当「无样本」）")
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：非法 JSON 触发 g01.artifacts_readable")

        # 8b. 缺必需产物（budget_profile.json）→ g01 失败。
        d = _write_run_dir(base, "tamper_missing", aspect_id=MB_ASPECT, **honest)
        (d / "budget_profile.json").unlink()
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：缺 budget_profile.json 触发 g01.artifacts_readable")

        # 8c. 重复 material_id → g04 失败。
        d = _write_run_dir(base, "tamper_dup", aspect_id=MB_ASPECT, materials=("m1", "m1"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "ok",
                                                 components=("m1",))],
                           topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g04.material_index_wellformed" for g, _ in v.failed_gates),
              "篡改：重复 material_id 触发 g04.material_index_wellformed")

        # 8d. source_content_hash == payload_hash（两层身份被破坏）→ g05 失败。
        d = _write_run_dir(base, "tamper_hash", aspect_id=MB_ASPECT, **honest)
        mi = json.loads((d / "material_index.json").read_text(encoding="utf-8"))
        for x in mi:
            x["payload_hash"] = x["source_content_hash"]
        (d / "material_index.json").write_text(json.dumps(mi, ensure_ascii=False),
                                               encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g05.material_identity_wellformed" for g, _ in v.failed_gates),
              "篡改：双层身份被破坏触发 g05.material_identity_wellformed")

        # 8e. 非法 recovery_status → g11 失败。
        d = _write_run_dir(base, "tamper_status", aspect_id=MB_ASPECT,
                           materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery", "T", "bogus",
                                                 components=("m1", "m2"))],
                           topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g11.table_status_explicit" for g, _ in v.failed_gates),
              "篡改：非法 recovery_status 触发 g11.table_status_explicit")

        # 8f. P1-D：多 seed 只解析 1（丢弃 seed）→ seed↔resolved 非一一对应 → g17 失败。
        d = _write_run_dir(base, "tamper_dropped", aspect_id=MB_ASPECT, **honest)
        (d / "seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [_seed(MB_ASPECT),
                                    dict(_seed(MB_ASPECT), evidence_id="ev-seed-0002")]},
                       ensure_ascii=False), encoding="utf-8")
        (d / "resolved_seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [{"resolved": True, "evidence_id": "ev-seed-0001"}]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：seed 被丢弃触发 g17.seed_resolved_one_to_one")

        # 8g. P1-D：孤儿 resolved（resolved 无对应 seed）→ g17 失败。
        d = _write_run_dir(base, "tamper_orphan", aspect_id=MB_ASPECT, **honest)
        (d / "resolved_seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [{"resolved": True, "evidence_id": "ev-orphan"}]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：孤儿 resolved（无对应 seed）触发 g17")

        # 8h. P1-D：seed_manifest 重复 evidence_id → g17 失败。
        d = _write_run_dir(base, "tamper_dupseed", aspect_id=MB_ASPECT, **honest)
        (d / "seed_manifest.json").write_text(
            json.dumps({"manifest_version": "v1", "fingerprint": "f",
                        "entries": [_seed(MB_ASPECT), _seed(MB_ASPECT)]},
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g17.seed_resolved_one_to_one" for g, _ in v.failed_gates),
              "篡改：重复 seed 身份触发 g17")

        # 8i. P1-D：payload_preview 缺材料预览 → g12 失败。
        d = _write_run_dir(base, "tamper_preview", aspect_id=MB_ASPECT, **honest)
        first = json.loads((d / "material_index.json").read_text(encoding="utf-8"))[0]
        (d / "payload_preview" / f"{first['material_id']}.json").unlink()
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g12.payload_preview_consistent" for g, _ in v.failed_gates),
              "篡改：缺 payload 预览触发 g12.payload_preview_consistent")

        # 8j. P1-D：raw sentinel 出现在 aspect_links → g14 失败。
        d = _write_run_dir(base, "tamper_sentinel_link", aspect_id=MB_ASPECT, **honest)
        first = json.loads((d / "material_index.json").read_text(encoding="utf-8"))[0]
        (d / "aspect_links.json").write_text(
            json.dumps([{"material_id": first["material_id"], "aspect_id": MB_ASPECT,
                         "role": "source",
                         "disposition": "outside_boundary_sentinel"}],
                       ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g14.aspect_links_no_sentinel" for g, _ in v.failed_gates),
              "篡改：aspect_links 含 sentinel 触发 g14.aspect_links_no_sentinel")

        # 8k. P1-D.4：payload bytes 被改写（hash 不自洽）→ g19 失败 + material_state=invalid。
        d = _write_run_dir(base, "tamper_payload_bytes", aspect_id=MB_ASPECT, **honest)
        first = json.loads((d / "material_index.json").read_text(encoding="utf-8"))[0]
        pv_path = d / "payload_preview" / f"{first['material_id']}.json"
        pv = json.loads(pv_path.read_text(encoding="utf-8"))
        env = json.loads(pv["payload"])
        env["payload"]["text"] = "被篡改的正文"
        pv["payload"] = json.dumps(env, ensure_ascii=False, separators=(",", ":"),
                                   sort_keys=True)
        pv_path.write_text(json.dumps(pv, ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g19.payload_bytes_recomputed" for g, _ in v.failed_gates),
              "篡改：payload bytes 改写 → g19.payload_bytes_recomputed 失败")
        check(v.material_state == MATERIAL_STATE_INVALID,
              "篡改：payload 不可复算 → material_state=invalid")

        # 8l. §五.1：material_id 自报但不可重算 → g20 失败 + invalid。
        d = _write_run_dir(base, "tamper_material_id", aspect_id=MB_ASPECT, **honest)
        mi = json.loads((d / "material_index.json").read_text(encoding="utf-8"))
        old = mi[0]["material_id"]
        bogus = "mat-" + "0" * 32
        mi[0]["material_id"] = bogus
        (d / "material_index.json").write_text(json.dumps(mi, ensure_ascii=False),
                                               encoding="utf-8")
        pv = json.loads((d / "payload_preview" / f"{old}.json").read_text(encoding="utf-8"))
        pv["material_id"] = bogus
        (d / "payload_preview" / f"{old}.json").unlink()
        (d / "payload_preview" / f"{bogus}.json").write_text(
            json.dumps(pv, ensure_ascii=False), encoding="utf-8")
        _rewrite_links(d, MB_ASPECT)
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g20.material_identity_recomputed" for g, _ in v.failed_gates),
              "篡改：material_id 不可重算 → g20.material_identity_recomputed 失败")
        check(v.material_state == MATERIAL_STATE_INVALID,
              "篡改：material 身份不可重算 → material_state=invalid")

        # 8m. §四.D.5：assembly_id 被改写（content-addressed 不自洽）→ g21 失败。
        d = _write_run_dir(base, "tamper_assembly_id", aspect_id=MB_ASPECT, **honest)
        asm = json.loads((d / "assemblies.json").read_text(encoding="utf-8"))
        old_aid = asm[0]["assembly_id"]
        asm[0]["assembly_id"] = "asm-" + "1" * 32
        (d / "assemblies.json").write_text(json.dumps(asm, ensure_ascii=False),
                                           encoding="utf-8")
        si = json.loads((d / "source_object_inventory.json").read_text(encoding="utf-8"))
        si[MB_ASPECT]["recovery_results"][0]["assembly_id"] = "asm-" + "1" * 32
        (d / "source_object_inventory.json").write_text(
            json.dumps(si, ensure_ascii=False), encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g21.assembly_closure" for g, _ in v.failed_gates)
              and any("重算不一致" in det for g, det in v.failed_gates
                      if g == "g21.assembly_closure"),
              "篡改：assembly_id 不可重算 → g21.assembly_closure 失败")
        check(v.material_state == MATERIAL_STATE_INVALID,
              "篡改：assembly 身份不可重算 → material_state=invalid")
        check(old_aid != asm[0]["assembly_id"], "篡改：assembly_id 确实被改写")

        # 8n. §四.C.5：continuation_proof.component_order 与组件顺序不一致 → g21 失败。
        d = _write_run_dir(base, "tamper_order", aspect_id=NOTES_ASPECT,
                           document_id="NDSD_2024_year", materials=("m1", "m2"),
                           assemblies=[_assembly("flattened_table_recovery",
                                                 "表 1 主营业务收入构成表", "ok",
                                                 components=("m1", "m2"),
                                                 continuation_valid=True,
                                                 component_order=True)])
        asm = json.loads((d / "assemblies.json").read_text(encoding="utf-8"))
        asm[0]["continuation_proof"]["component_order"] = list(
            reversed(asm[0]["continuation_proof"]["component_order"]))
        (d / "assemblies.json").write_text(json.dumps(asm, ensure_ascii=False),
                                           encoding="utf-8")
        v = verify_category(CATEGORY_FINANCIAL_NOTES, d)
        check(any(g == "g21.assembly_closure" for g, _ in v.failed_gates)
              and any("组件顺序" in det for g, det in v.failed_gates
                      if g == "g21.assembly_closure"),
              "篡改：component_order 与组件顺序不一致 → g21 失败")

        # 8o. §四.D.6：membership 与 aspect_links 不闭合 → g23 失败 + invalid。
        d = _write_run_dir(base, "tamper_membership", aspect_id=MB_ASPECT, **honest)
        mem = json.loads((d / "aspect_membership.json").read_text(encoding="utf-8"))
        mem[MB_ASPECT]["formal_material_ids"] = mem[MB_ASPECT]["formal_material_ids"][:1]
        (d / "aspect_membership.json").write_text(json.dumps(mem, ensure_ascii=False),
                                                  encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g23.cross_artifact_closure" for g, _ in v.failed_gates),
              "篡改：membership 与 aspect_links 不闭合 → g23 失败")

        # 8s. §四.A.1/A.2：aspect 条目下 records 被清空（身份被合并成「一条都没有」）→
        # 未验证边界不得伪装成 verified → g23 失败。
        d = _write_run_dir(base, "tamper_bv_no_records", aspect_id=MB_ASPECT, **honest)
        bv = json.loads((d / "boundary_verification.json").read_text(encoding="utf-8"))
        bv["aspects"][0]["records"] = []
        (d / "boundary_verification.json").write_text(json.dumps(bv, ensure_ascii=False),
                                                     encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g23.cross_artifact_closure" for g, _ in v.failed_gates)
              and any("无 records" in det for g, det in v.failed_gates
                      if g == "g23.cross_artifact_closure"),
              "反例 A.2：records 为空 → g23 失败（逐 seed 身份不得合并/缺失）")

        # 8t. §四.A.1/A.7：声明 verified 但剥掉结构证据（无 in-topic anchor / 无主题小节层级）
        # → 词表自洽不得冒充已验证 → g23 失败。
        d = _write_run_dir(base, "tamper_bv_verified_no_evidence", aspect_id=MB_ASPECT,
                           **honest)
        bv = json.loads((d / "boundary_verification.json").read_text(encoding="utf-8"))
        rec = bv["aspects"][0]["records"][0]
        rec["in_topic_anchor"] = ""
        rec["topic_level"] = None
        (d / "boundary_verification.json").write_text(json.dumps(bv, ensure_ascii=False),
                                                     encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g23.cross_artifact_closure" for g, _ in v.failed_gates)
              and any("in-topic anchor" in det for g, det in v.failed_gates
                      if g == "g23.cross_artifact_closure")
              and any("主题小节层级" in det for g, det in v.failed_gates
                      if g == "g23.cross_artifact_closure"),
              "反例 A.7：verified 无结构证据 → g23 失败（绝不靠词表自洽声称 verified）")

        # 8p. §四.D.7：JSONL 出现非 object 行 → fail-closed（不抛未处理异常）。
        d = _write_run_dir(base, "tamper_jsonl_type", aspect_id=MB_ASPECT, **honest)
        (d / "expansion_trace.jsonl").write_text("[1, 2, 3]\n", encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates)
              and v.verdict == VERDICT_BOUNDARY_INCOMPLETE,
              "篡改：expansion_trace 非 object 行 → fail-closed（不减数为空样本）")

        # 8q. §四.D.7：顶层容器类型错误（material_index 为 object）→ fail-closed。
        d = _write_run_dir(base, "tamper_container_type", aspect_id=MB_ASPECT, **honest)
        (d / "material_index.json").write_text('{"not": "a list"}', encoding="utf-8")
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：material_index 顶层类型错误 → g01 fail-closed")

        # 8r. 全部产物损坏为不可读类型（目录替代文件）→ 绝不抛异常。
        d = _write_run_dir(base, "tamper_dirfile", aspect_id=MB_ASPECT, **honest)
        (d / "assemblies.json").unlink()
        (d / "assemblies.json").mkdir()
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(v.verdict == VERDICT_BOUNDARY_INCOMPLETE
              and any(g == "g01.artifacts_readable" for g, _ in v.failed_gates),
              "篡改：产物被目录替代 → fail-closed（不抛未处理异常）")

        # 8u. §四.A.8：主题外（sibling）证据混入本 aspect 材料 —— sentinel 决策块出现在
        # material_index、且不在本 aspect 已声明 seed 清单内 → 类别特异门
        # main_business.topic_boundary_enforced 失败（绝不靠词表自洽放行）。
        d = _write_run_dir(base, "tamper_oob_leak", aspect_id=MB_ASPECT,
                           materials=("m1", "m2", "oob"), topic_boundary=True)
        v = verify_category(CATEGORY_MAIN_BUSINESS, d)
        check(any(g == "main_business.topic_boundary_enforced" for g, _ in v.failed_gates)
              and any("混入" in det for g, det in v.failed_gates
                      if g == "main_business.topic_boundary_enforced"),
              "反例：主题外（sibling）证据混入本 aspect 材料 → "
              "main_business.topic_boundary_enforced 失败（结构性判定，不看关键词）")

        # ===============================================================
        # 9. §五：关闭条件证据收紧 —— 逐条不变量 + 反例（不得只用 weak proxies）
        # ===============================================================
        from harness.six_category_acceptance import (
            CategoryVerification as _CV,
            _abcd_and_identity_closure,
            _continuation_expansion_provenance,
            _continuation_expansion_verdict,
            _continuation_positive_control,
            _continuation_proof_chain_complete,
            _continuation_proof_classify,
            _inventory_boundary_unread_trace_audit,
            _main_business_positive_control,
            _negative_state_evidence,
            _non_300750_positive_control,
            _production_company_hardcode_hits,
            _seed_hardcode_probe,
            _tri_settle,
        )

        def _cv(cid, facts, *, material_state=MATERIAL_STATE_BOUNDARY_INCOMPLETE,
                capability_verdict=CAPABILITY_PASS, passed=("g19.payload_bytes",),
                failed=()):
            return _CV(category_id=cid, verdict=VERDICT_BOUNDARY_INCOMPLETE,
                       reason="test", passed_gates=tuple(passed),
                       failed_gates=tuple(failed), artifact_fingerprint="0" * 64,
                       facts=facts, material_state=material_state,
                       capability_verdict=capability_verdict,
                       report_impact=REPORT_IMPACT_BLOCKING)

        # 9a. 续表证明三分类：只有完整正向链才算 positive；失败无 issue 记 inconsistent。
        # §三.1/§三.2：正向链形状与真实 ``assemblies.json`` 的 continuation_proof 摘要一致
        # （归一化表题 / 四项兼容 / 恢复状态 / 四个结构见证 / 逐 span 真实结构事实）。
        full = {"valid": True, "identity_source": "recovered_structure",
                "normalized_title": "表 6-1 主营业务成本构成表",
                "title_compatible": True, "unit_compatible": True,
                "column_compatible": True, "row_column_continuity": True,
                "final_recovery_status": "ok",
                "header_repeat_verified": True, "boundary_consecutive": True,
                "section_path_shared": True, "same_document_verified": True,
                "span_fact_count": 1,
                "header_evidence_id": "h1", "continuation_evidence_ids": ["c1"],
                "header_page": 50, "continuation_pages": [51],
                "continuation_span_facts": [
                    {"evidence_id": "c1", "page": 51, "header_repeat_matched": True,
                     "unit_conflict": [], "column_width_ok": True,
                     "contributed_structure": True, "adjacent_to_previous": True}]}
        check(_continuation_proof_chain_complete(full)
              and _continuation_proof_classify(full) == "positive",
              "§四 续表：合法真实续表正向样本（完整正向链）→ positive")
        check(_continuation_proof_classify({**full, "continuation_evidence_ids": []})
              == "inconsistent",
              "反例：valid=true 但缺续页证据 id → inconsistent（不信 self-report 的 valid）")
        check(_continuation_proof_classify(
            {**full, "span_fact_count": 0, "continuation_span_facts": []})
              == "inconsistent",
              "反例：valid=true 但 span 事实为空 → inconsistent（空 span 不得冒充续页）")
        check(_continuation_proof_classify({"valid": False, "issue": "未重复表头"})
              == "honest_negative",
              "§四 续表：valid=false + 具体 issue → honest_negative")
        check(_continuation_proof_classify({"valid": False}) == "inconsistent",
              "反例：valid=false 但无任何 issue → inconsistent（失败无据）")

        # §三.1：四个结构见证必须**逐项显式 is True**（缺失 ⇒ None ⇒ 不冒充通过）。
        for _witness, _label in (
                ("header_repeat_verified", "缺失 header_repeat_verified"),
                ("boundary_consecutive", "缺失 boundary_consecutive"),
                ("section_path_shared", "缺失 section_path_shared"),
                ("same_document_verified", "缺失 same_document_verified")):
            _missing = {k: v for k, v in full.items() if k != _witness}
            check(_continuation_proof_classify(_missing) == "inconsistent",
                  f"反例：{_label}（见证缺失 ⇒ None）→ 正向链不成立 → inconsistent")
        check(_continuation_proof_classify({**full, "section_path_shared": None})
              == "inconsistent",
              "反例：见证字段为 None（不适用 ≠ 通过）→ inconsistent")
        check(_continuation_proof_classify({**full, "same_document_verified": False})
              == "inconsistent",
              "反例：不同 document/version（同文档未验证）→ inconsistent")
        check(_continuation_proof_classify({**full, "header_repeat_verified": False})
              == "inconsistent",
              "反例：续页未重排本表表头（header_repeat_verified=False）→ inconsistent")

        # §三.2：表题/单位/列/行列/恢复状态逐项可反证（任一项不成立即不得称正向）。
        check(_continuation_proof_classify({**full, "title_compatible": False})
              == "inconsistent",
              "反例：表题不一致（title_compatible=False）→ inconsistent")
        check(_continuation_proof_classify({**full, "normalized_title": ""})
              == "inconsistent",
              "反例：表题身份为空（无归一化表题）→ inconsistent")
        check(_continuation_proof_classify({**full, "unit_compatible": False})
              == "inconsistent",
              "反例：单位不一致（unit_compatible=False）→ inconsistent")
        check(_continuation_proof_classify({**full, "column_compatible": False})
              == "inconsistent",
              "反例：列宽不一致（column_compatible=False）→ inconsistent")
        check(_continuation_proof_classify({**full, "row_column_continuity": False})
              == "inconsistent",
              "反例：行列连续性不成立（row_column_continuity=False）→ inconsistent")
        check(_continuation_proof_classify({**full, "final_recovery_status": "partial"})
              == "inconsistent",
              "反例：恢复状态非 ok（final_recovery_status=partial）→ inconsistent")
        check(_continuation_proof_classify({**full, "continuation_pages": [50]})
              == "inconsistent",
              "反例：非跨页（续页与表头同页）→ inconsistent")
        check(_continuation_proof_classify(
            {**full, "continuation_span_facts": [
                {"evidence_id": "c1", "page": 51, "header_repeat_matched": True,
                 "unit_conflict": [], "column_width_ok": True,
                 "contributed_structure": False, "adjacent_to_previous": True}]})
              == "inconsistent",
              "反例：续页 span 未贡献任何真实结构（contributed_structure=False）"
              "→ inconsistent")
        check(_continuation_proof_classify(
            {**full, "identity_source": "structured_payload",
             "header_repeat_verified": None, "boundary_consecutive": None,
             "section_path_shared": None, "same_document_verified": None})
              == "positive",
              "§三.1：结构化载荷表链不要求四个结构见证（None = 不适用），"
              "但其余链路必须完整 → positive")

        # 9a-2. §三.3/§二：续页材料必须回指**同一 seed/frontier 的真实续表扩读链**
        # （trace 的 table_continuation 步骤真实 outputs + 真实采纳）。第二 seed 引入、
        # trace 无输出、未被采纳 —— 都不得冒充扩读成果（也不得让第二个能力态为真）。
        _seed_step = {"action": "inspect_bounded", "step_index": 0,
                      "seed_evidence_id": "s1",
                      "arguments": {"mode": "adjacent_after", "evidence_id": "s1"},
                      "outputs": ["h1"], "stop_reason": None}
        _cont_step = {"action": "inspect_bounded", "step_index": 1,
                      "seed_evidence_id": "s1",
                      "arguments": {"mode": "table_continuation", "evidence_id": "s1"},
                      "outputs": ["c1"], "stop_reason": None}
        _proof_ref = {"header_evidence_id": "h1",
                      "continuation_evidence_ids": ["c1"]}

        _prov = _continuation_expansion_provenance(
            [dict(_seed_step), dict(_cont_step)],
            seed_evidence_ids=["s1"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _prov)
        check(_verdict["expansion_provenanced"] is True
              and _verdict["origin_seed"] == "s1",
              "§三.3：合法真实续表正向样本 —— 同一 seed 的 table_continuation 步骤真实"
              "输出续页并已被采纳 ⇒ 扩读来源成立（可回指 seed/frontier）")

        _no_output = _continuation_expansion_provenance(
            [dict(_seed_step), {**_cont_step, "outputs": []}],
            seed_evidence_ids=["s1"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _no_output)
        check(_verdict["expansion_provenanced"] is False
              and _verdict["reason"] == "continuation_not_from_trace_outputs"
              and _verdict["unprovenanced_continuation_evidence_ids"] == ["c1"],
              "反例：证明自报 valid 但 trace 无输出（table_continuation outputs=[]）"
              "⇒ 扩读来源不成立，且逐条披露未获证续页 id")

        _second_seed = _continuation_expansion_provenance(
            [dict(_seed_step), {**_cont_step, "outputs": []},
             {"action": "inspect_bounded", "step_index": 2,
              "seed_evidence_id": "c1",
              "arguments": {"mode": "adjacent_before", "evidence_id": "c1"},
              "outputs": ["h1"], "stop_reason": None}],
            seed_evidence_ids=["s1", "c1"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _second_seed)
        check(_verdict["expansion_provenanced"] is False,
              "反例：续页只由第二 seed 引入（第一 seed 的续表步骤无输出）"
              "⇒ 扩读来源不成立（材料后来出现在池中不算扩读成果）")
        check(_second_seed["seed_attribution_available"] is True
              and _second_seed["continuation_outputs_by_seed"]["s1"] == []
              and _second_seed["continuation_outputs_by_seed"]["c1"] == [],
              "§三.3：逐 seed 归属落盘 —— 步骤按 seed_evidence_id 独立归因，"
              "绝不跨 seed 归因")

        _unadopted = _continuation_expansion_provenance(
            [dict(_seed_step), dict(_cont_step)],
            seed_evidence_ids=["s1"], adopted_evidence_ids=["h1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _unadopted)
        check(_verdict["expansion_provenanced"] is False
              and _verdict["reason"] == "continuation_not_adopted",
              "反例：续页未被采纳（trace 有输出但未进本 aspect 材料）"
              "⇒ 扩读来源不成立")

        _header_elsewhere = _continuation_expansion_provenance(
            [dict(_seed_step), dict(_cont_step)],
            seed_evidence_ids=["s9"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _header_elsewhere)
        check(_verdict["expansion_provenanced"] is False
              and _verdict["reason"] == "header_not_in_any_frontier",
              "反例：表头证据不在任何 seed frontier 内 ⇒ 扩读来源不成立")
        check(_continuation_expansion_verdict(None, _prov)["reason"] == "no_proof",
              "反例：无续表证明 ⇒ 扩读来源不成立（no_proof）")
        check(_continuation_expansion_provenance(
            [], seed_evidence_ids=[], adopted_evidence_ids=[])
            ["seed_attribution_available"] is False,
              "§三.3：历史产物无 seed 归属字段 ⇒ 显式标记归属不可判定"
              "（不静默当作成立）")

        # 9a-3. §三.3/§二 P1-A：**真实产物形状** —— seed 自身的读取（adjacent / seed 引用）
        # 在真实 trace 里*不带*目标参数，锚点由本步 ``anchor_evidence_id`` 落盘。若验收侧
        # 从「无残留参数」推断「无锚点」，seed 相邻读取带回的**表头块**会被判为「不在任何
        # frontier」，真实的续表扩读正向样本被误判为未获证（v10 实测正是如此）。
        _real_adj = {"action": "inspect_bounded", "step_index": 1,
                     "seed_evidence_id": "s1", "anchor_evidence_id": "s1",
                     "arguments": {"mode": "adjacent_after"}, "outputs": ["h1"],
                     "stop_reason": None}
        _real_cont = {"action": "inspect_bounded", "step_index": 2,
                      "seed_evidence_id": "s1", "anchor_evidence_id": "s1",
                      "arguments": {"mode": "table_continuation",
                                    "evidence_id": "s1"}, "outputs": ["c1"],
                      "stop_reason": "target exhausted"}
        _prov = _continuation_expansion_provenance(
            [dict(_real_adj), dict(_real_cont)],
            seed_evidence_ids=["s1"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _prov)
        check(_verdict["expansion_provenanced"] is True
              and _verdict["origin_seed"] == "s1",
              "§三.3：真实产物形状（锚点由 trace 的 anchor_evidence_id 落盘）—— 同一 seed 的"
              "相邻读取带回表头 + 同一 seed 的续表步骤真实输出并采纳续页 ⇒ 扩读来源成立"
              "（绝不因「相邻读取不带目标参数」误判为无前沿）")
        check(_prov["steps_by_seed"]["s1"][0]["anchor_evidence_id"] == "s1"
              and _prov["frontier_by_seed"]["s1"] == ["c1", "h1", "s1"],
              "§三.3：逐 seed 步骤/锚点/前沿闭包逐条落盘（可独立复核表头为何进前沿）")

        # 反例：续页**只**经第二 seed 的 resolve_seed 进入材料池（两个 seed 都没有输出它的
        # 续表步骤）—— 第一 seed 的续表扩读来源不得因此成立。
        _second_introduced = _continuation_expansion_provenance(
            [{**_real_adj, "outputs": []}, {**_real_cont, "outputs": []},
             {"action": "resolve_seed", "step_index": 0,
              "seed_evidence_id": "c1", "anchor_evidence_id": "c1",
              "arguments": {"seed_evidence_id": "c1"}, "outputs": ["c1"],
              "stop_reason": None},
             {"action": "inspect_bounded", "step_index": 1,
              "seed_evidence_id": "c1", "anchor_evidence_id": "c1",
              "arguments": {"mode": "adjacent_before"}, "outputs": ["h1"],
              "stop_reason": None}],
            seed_evidence_ids=["s1", "c1"], adopted_evidence_ids=["h1", "c1"])
        _verdict = _continuation_expansion_verdict(_proof_ref, _second_introduced)
        check(_verdict["expansion_provenanced"] is False
              and _verdict["reason"] == "continuation_not_from_trace_outputs",
              "反例：续页只由第二 seed 引入（resolve_seed 进池，无任一 seed 的续表步骤"
              "输出它）⇒ 扩读来源不成立，第二 seed 存在不使第一 seed 通过")
        check(_second_introduced["continuation_outputs_by_seed"] == {"c1": [], "s1": []},
              "§三.3：resolve_seed 步骤不参与前沿闭包、也不计入续表输出（逐 seed 为空）")

        # 反例：锚点字段被清空（历史/被篡改 trace）—— 锚点缺失即不推断 ⇒ 不成立。
        _no_anchor = _continuation_expansion_provenance(
            [{"action": "inspect_bounded", "step_index": 1,
              "seed_evidence_id": "s1", "anchor_evidence_id": "",
              "arguments": {"mode": "adjacent_after"}, "outputs": ["h1"],
              "stop_reason": None},
             {"action": "inspect_bounded", "step_index": 2,
              "seed_evidence_id": "s1", "anchor_evidence_id": "",
              "arguments": {"mode": "table_continuation"}, "outputs": ["c1"],
              "stop_reason": None}],
            seed_evidence_ids=["s1"], adopted_evidence_ids=["h1", "c1"])
        check(_continuation_expansion_verdict(_proof_ref, _no_anchor)
              ["expansion_provenanced"] is False,
              "反例：步锚点字段为空且无目标参数 ⇒ 锚点不推断、前沿不扩 ⇒ 扩读来源不成立")

        # 9b. 三值归并：能力断言未演练不得算通过；守卫断言真空成立。
        check(not _tri_settle({"holds": [], "violated": ["a"], "not_exercised": []})["satisfied"],
              "三值：任一 violated ⇒ 不成立")
        check(not _tri_settle({"holds": [], "violated": [], "not_exercised": ["a"]})["satisfied"],
              "三值：能力断言全部 not_exercised ⇒ 不成立（未演练不冒充通过）")
        check(_tri_settle({"holds": [], "violated": [], "not_exercised": ["a"]},
                          require_exercised=False)["satisfied"],
              "三值：守卫断言全部 not_exercised ⇒ 真空成立")
        _s = _tri_settle({"holds": ["b"], "violated": [], "not_exercised": ["a"]})
        check(_s["satisfied"] and _s["outcomes"] == {"a": "not_exercised", "b": "holds"},
              "三值：有 holds 且无 violated ⇒ 成立，并逐类别披露 not_exercised")

        # 9c. 公司硬编码检测器必须非空转：真实 if 规则报警，文档示例不误报。
        with tempfile.TemporaryDirectory() as hd:
            hroot = Path(hd)
            (hroot / "harness").mkdir()
            (hroot / "harness" / "rule.py").write_text(
                'def f(company_id):\n    if company_id == "300750":\n        return 1\n'
                '    return 0\n', encoding="utf-8")
            (hroot / "harness" / "doc.py").write_text(
                '"""usage: --company 300750"""\ndef g():\n'
                '    company = "宁德时代"\n    return company\n', encoding="utf-8")
            _orig = _production_company_hardcode_hits.__module__
            import harness.six_category_acceptance as _sca
            _saved = _sca.__file__
            _sca.__file__ = str(hroot / "harness" / "six_category_acceptance.py")
            try:
                scan = _production_company_hardcode_hits()
            finally:
                _sca.__file__ = _saved
            check(scan["token_in_condition_count"] == 1
                  and scan["token_in_condition"][0]["file"] == "harness/rule.py",
                  "反例：生产代码 if company_id == '300750' → 检测为硬编码规则")
            check(scan["occurrence_count"] == 3
                  and {o["kind"] for o in scan["occurrences"]} == {"docstring", "literal"}
                  and scan["token_in_condition_count"] == 1,
                  "反例：docstring 示例与普通赋值 → 记录为 occurrence，不计为规则")
            check(scan["excluded_checker_modules"] == [
                str(_saved.replace("\\", "/"))] or scan["excluded_checker_modules"],
                  "硬编码扫描显式披露被排除的验收器自身路径")

        # 9d. seed 硬编码探测按字段拆分：说明性元数据单独披露、不参与判定。
        _sp = _seed_hardcode_probe({"case_id": "非-300750-1", "company_id": "100001",
                                    "selection_reason": "非 300750 合成 fixture",
                                    "text": "通用正文"})
        check(_sp["company_id"] == "100001" and "case_id" in _sp["productive_field_matches"]
              and "selection_reason" in _sp["disclosed_non_productive_matches"]
              and sorted(_sp["raw_seed_blob_matching_fields"])
              == ["case_id", "selection_reason"],
              "§五.2：seed 命中按生产字段/说明元数据拆分，且全量披露命中字段")

        # 9e. 负面结果证据：无真实 trace 步骤 ⇒ 不成立。
        check(not _negative_state_evidence(_cv(CATEGORY_MAIN_BUSINESS, {
            "seed_evidence_ids": ["e1"], "company_id": "c", "document_id": "d",
            "expansion_trace_steps": 0, "expansion_stop_reasons": [],
        }))["evidenced"],
              "§五.3：无真实扩读 trace 步骤 ⇒ 负面证据不成立")
        _ne = _negative_state_evidence(_cv(CATEGORY_MAIN_BUSINESS, {
            "seed_evidence_ids": ["e1"], "company_id": "c", "document_id": "d",
            "expansion_trace_steps": 3, "expansion_stop_reasons": ["no expansion"],
            "boundary_incomplete_reason": "structural enumeration",
            "boundary_verification_status": [
                {"aspect_id": "a", "status": "incomplete",
                 "record_statuses": ["incomplete"], "unread_scope_count": 2}],
        }))
        check(_ne["evidenced"] and all(b["holds"] for b in _ne["bound"].values()),
              "§五.3：真实输入+trace+停止原因+未读记录齐备 ⇒ 负面证据成立")

        # 9f. 主业务正向对照：无表题恢复表是**披露**而非缺陷；orphan 才是缺陷。
        _base_mb_facts = {
            "material_count": 2, "material_ids": ["m1", "m2"],
            "distinct_source_blocks": 2, "distinct_source_pages": 2,
            "payload_preview_count": 2, "company_id": "c", "document_id": "d",
            "document_version": "v", "boundary_decision_count": 1, "boundary_verified": False,
            "boundary_verification_status": [
                {"aspect_id": "a", "status": "incomplete", "record_count": 1,
                 "record_statuses": ["incomplete"], "identity_complete_count": 1}],
            "topic_boundary_enforced": True, "source_inventory_present": True,
            "source_inventory_object_count": 1, "source_inventory_result_count": 1,
            "source_inventory_unmatched": [], "source_inventory_orphans": [],
            "source_inventory_untitled": ["asm-x"], "assembly_count": 1,
            "recovered_table_count": 1, "recovered_table_titles": ["表 1 收入构成表"],
            "recovered_table_failed": 0,
        }
        _mb = _main_business_positive_control(_cv(CATEGORY_MAIN_BUSINESS, _base_mb_facts))
        check(_mb["satisfied"] and _mb["invariants"]["boundary_records_reconciled"]
              and _mb["boundary_records"]["untitled_recovered_tables_disclosed"] == ["asm-x"],
              "§五.1：无表题恢复表是显式披露 ⇒ 不算对账缺陷（正向对照仍成立）")
        _mb2 = _main_business_positive_control(_cv(CATEGORY_MAIN_BUSINESS, {
            **_base_mb_facts, "source_inventory_orphans": ["asm-y"]}))
        check(not _mb2["satisfied"] and not _mb2["invariants"]["boundary_records_reconciled"],
              "反例：孤儿恢复表（带表题却未认领）⇒ 正向对照不成立")

        # 9g. 非 300750 正向对照：公司身份被硬编码 ⇒ 不成立。
        _nf_facts = {
            "material_count": 1, "material_ids": ["m1"], "assembly_count": 1,
            "recovered_table_count": 1, "recovered_table_titles": ["表 1 收入构成表"],
            "recovered_table_failed": 0, "company_id": "100001", "document_id": "d",
            "document_version": "v", "seed_evidence_ids": ["e1"],
            "boundary_decision_count": 1, "source_inventory_present": True,
            "expansion_trace_steps": 2, "expansion_stop_reasons": [],
            "seed": {"case_id": "f-1", "company_id": "100001", "text": "通用正文"},
        }
        check(_non_300750_positive_control(
            _cv(CATEGORY_NON_300750_FIXTURE, _nf_facts))["satisfied"],
              "§五.2：真实产出材料 + 通用机制 + 合法身份 + 无硬编码 ⇒ 正向对照成立")
        _nf_bad = _non_300750_positive_control(_cv(CATEGORY_NON_300750_FIXTURE, {
            **_nf_facts, "company_id": "300750",
            "seed": {"case_id": "f-1", "company_id": "300750", "text": "通用正文"}}))
        check(not _nf_bad["satisfied"]
              and not _nf_bad["invariants"]["no_company_hardcode"],
              "反例：company_id 被硬编码为 300750 ⇒ 无硬编码不变量不成立")

        # 9h. A–D 派生：显式引用审计缺失⇒能力未演练（B 不成立），有审计且逐目标尝试⇒成立。
        _mb_base = {
            "boundary_verification_status": [
                {"aspect_id": "a", "status": "incomplete", "record_count": 1,
                 "record_statuses": ["incomplete"], "identity_complete_count": 1,
                 "unresolved_ambiguity_count": 0}],
            "boundary_verified": False, "assembly_count": 1,
            "source_inventory_present": True, "source_inventory_object_count": 1,
            "source_inventory_result_count": 1, "source_inventory_unmatched": [],
            "source_inventory_orphans": [], "source_inventory_untitled": [],
            "recovered_table_count": 1, "rolling_target_count": 1,
            "unread_budget_stop_consistent": True, "expansion_read_modes": ["adjacent_after"],
            "expansion_stop_reasons": ["boundary"], "expansion_trace_steps": 1,
            "material_state": MATERIAL_STATE_BOUNDARY_INCOMPLETE,
        }
        _no_ref = _abcd_and_identity_closure(
            {CATEGORY_MAIN_BUSINESS: _cv(CATEGORY_MAIN_BUSINESS, _mb_base)})
        _ref_inv = _no_ref["items"]["B.source_object_inventory_and_assembly_single_truth"][
            "invariant_detail"]["explicit_references_audited_individually"]
        check(not _ref_inv["satisfied"]
              and _ref_inv["not_exercised_in"] == [CATEGORY_MAIN_BUSINESS],
              "§五.4：无显式引用审计 ⇒ 该能力标记 not_exercised（不算通过）")
        _with_ref = _abcd_and_identity_closure({CATEGORY_MAIN_BUSINESS: _cv(
            CATEGORY_MAIN_BUSINESS,
            {**_mb_base, "explicit_reference_audit": {
                "state": "resolved", "declared_targets": ["表 1", "表 2"],
                "attempt_step_count": 2}})})
        check(_with_ref["items"]["B.source_object_inventory_and_assembly_single_truth"][
            "invariant_detail"]["explicit_references_audited_individually"]["satisfied"],
              "§五.4：显式引用逐目标尝试（去重且尝试步数≥目标数）⇒ 该不变量成立")
        _dup_ref = _abcd_and_identity_closure({CATEGORY_MAIN_BUSINESS: _cv(
            CATEGORY_MAIN_BUSINESS,
            {**_mb_base, "explicit_reference_audit": {
                "state": "resolved", "declared_targets": ["表 1", "表 1"],
                "attempt_step_count": 1}})})
        check(not _dup_ref["items"]["B.source_object_inventory_and_assembly_single_truth"][
            "invariant_detail"]["explicit_references_audited_individually"]["satisfied"],
              "反例：重复声明目标 / 尝试步数不足 ⇒ 该不变量不成立")

        # 9i. 真实正向续表样本缺失 ⇒ C 项如实不成立（不得用诚实否定凑成通过）。
        _no_positive = _abcd_and_identity_closure({CATEGORY_FINANCIAL_NOTES: _cv(
            CATEGORY_FINANCIAL_NOTES,
            {**_mb_base, "recovered_table_detail": [
                {"table_title": "T", "continuation_proof": {
                    "valid": False, "issue": "未重复表头"}}]})})
        check(not _no_positive["items"][
            "C.rolling_expansion_and_real_continued_from"]["invariant_detail"][
                "real_positive_continuation_sample"]["satisfied"],
              "反例：只有诚实否定、无真实正向样本 ⇒ 真实正向样本项不成立")
        # 9i. §二：两个能力态**分别**判决 —— 同表恢复正向 ≠ 续表扩读正向。
        _cont_recovery_only = _continuation_positive_control({CATEGORY_MAIN_BUSINESS: _cv(
            CATEGORY_MAIN_BUSINESS,
            {**_mb_base, "recovered_table_detail": [
                {"table_title": "T", "boundary_desc": "P50–P51",
                 "continuation_proof": dict(full)}]})})
        check(_cont_recovery_only["same_table_recovery_positive"]
              and not _cont_recovery_only["table_continuation_expansion_positive"]
              and _cont_recovery_only["positive_control_not_available"],
              "§二：两页材料经其它途径恢复同一张表 ⇒ 只使 same_table_recovery_positive "
              "成立；table_continuation_expansion_positive 仍为假（不得以第二 seed / "
              "最终装配冒充续表扩读）")
        _cont = _continuation_positive_control({CATEGORY_MAIN_BUSINESS: _cv(
            CATEGORY_MAIN_BUSINESS,
            {**_mb_base, "recovered_table_detail": [
                {"table_title": "T", "boundary_desc": "P50–P51",
                 "continuation_expansion": {"expansion_provenanced": True,
                                            "origin_seed": "s1"},
                 "continuation_proof": dict(full)}]})})
        check(_cont["satisfied"] and _cont["same_table_recovery_positive"]
              and _cont["table_continuation_expansion_positive"]
              and len(_cont["samples"]) == 1 and len(_cont["expansion_samples"]) == 1
              and _cont["expansion_samples"][0]["expansion_origin_seed"] == "s1"
              and not _cont["positive_control_not_available"],
              "§四：合法真实续表正向样本（恢复链完整 + 扩读来源成立）⇒ 两个能力态正向对照均成立")
        _cont_chain_broken = _continuation_positive_control({CATEGORY_MAIN_BUSINESS: _cv(
            CATEGORY_MAIN_BUSINESS,
            {**_mb_base, "recovered_table_detail": [
                {"table_title": "T", "boundary_desc": "P50–P51",
                 "continuation_expansion": {"expansion_provenanced": True,
                                            "origin_seed": "s1"},
                 "continuation_proof": {**full, "header_repeat_verified": None}}]})})
        check(not _cont_chain_broken["satisfied"]
              and not _cont_chain_broken["same_table_recovery_positive"]
              and len(_cont_chain_broken["incomplete_samples"]) == 1,
              "反例：自报 valid + 自报扩读来源，但结构见证为 None ⇒ 两个能力态都不成立"
              "（逐项重算，不信持久化 valid/来源自报）")

        # 9j. §六条件 7：清单/成员资格/边界验证/未读范围/预算消耗/扩读 trace/续表与引用
        # 来源 —— 七项**逐项**观测，任一项为假即 holds=False。既有反例只覆盖了局部聚合
        # 函数，这里补上**清单级**（整份条件 7 审计）的正反例。
        _c7_base = {
            "seed_run": "r", "material_state": MATERIAL_STATE_BOUNDARY_INCOMPLETE,
            "source_inventory_present": True, "source_inventory_object_count": 2,
            "source_inventory_result_count": 2, "source_inventory_unmatched": [],
            "source_inventory_orphans": [], "source_inventory_untitled": [],
            "source_inventory_non_ok": [],
            "aspect_links_sentinel_count": 0, "membership_sentinel_count": 0,
            "topic_boundary_enforced": True,
            "cross_artifact_closure_problems": [], "assembly_closure_problems": [],
            "source_inventory_closure_problems": [],
            "boundary_verified": False,
            "boundary_verification_status": [
                {"aspect_id": "a", "status": "incomplete",
                 "record_statuses": ["incomplete"], "identity_complete_count": 1}],
            "unread_scope_count": 2, "direction_unread_count": 1,
            "unread_budget_stop_consistent": True,
            "budget_profile_name": "acceptance",
            "seed_budget_records": [{"seed_evidence_id": "s1", "steps": 3}],
            "expansion_trace_steps": 3, "expansion_stop_reasons": ["boundary"],
            "expansion_read_modes": ["adjacent_after", "table_continuation"],
            "continuation_proof_count": 0, "continuation_expansion_count": 0,
            "continuation_expansion_provenance": {},
            "explicit_reference_audit": {
                "state": "not_exercised", "resolution_attempted": False,
                "target_resolved": False, "target_dangling": False,
                "attempt_stop_reasons": []},
        }
        _CATS7 = {CATEGORY_MAIN_BUSINESS: {"category_id": CATEGORY_MAIN_BUSINESS}}

        def _c7(facts, categories=None):
            return _inventory_boundary_unread_trace_audit(
                {CATEGORY_MAIN_BUSINESS: _cv(CATEGORY_MAIN_BUSINESS, facts)},
                categories if categories is not None else _CATS7)

        _c7_ok = _c7(_c7_base)
        check(_c7_ok["satisfied"] and _c7_ok["per_category"][CATEGORY_MAIN_BUSINESS]["holds"],
              "§六条件 7：七项观测齐备（清单闭合 / 无 sentinel / 边界状态合法 / 未读一致 / "
              "预算档位 / 真实 trace / 引用状态已记录）⇒ 条件 7 成立")
        check(set(_c7_ok["per_category"][CATEGORY_MAIN_BUSINESS]["checks"]) == {
            "inventory", "aspect_membership", "boundary_verification", "unread_scope",
            "budget_consumption", "expansion_trace",
            "continuation_and_reference_provenance"},
              "§六条件 7：判定面**恰为** §六点名的七项（不得悄悄增删维度）")
        check(not _c7(_c7_base, {})["satisfied"],
              "反例：空清单（无任何类别）⇒ 条件 7 不成立（空集不得冒充通过）")

        # §四.A.8 交叉核对：sentinel 记入 membership 是**被要求的事实**；只要 A 级结构性
        # 边界审计成立，aspect_membership 项仍成立（这是 v9/v10 长期为假的错判来源）。
        _c7_sent = _c7({**_c7_base, "membership_sentinel_count": 1})
        _sent_row = _c7_sent["per_category"][CATEGORY_MAIN_BUSINESS]
        check(_c7_sent["satisfied"] and _sent_row["checks"]["aspect_membership"]
              and _sent_row["aspect_membership"]["sentinel_recorded_in_membership"] == 1
              and _sent_row["aspect_membership"]["sentinel_in_aspect_links"] == 0
              and _sent_row["aspect_membership"]["topic_boundary_enforced"] is True,
              "§四.A.8：sentinel 已记入 membership 且 A 级边界审计成立 ⇒ "
              "aspect_membership 成立（记录在案 ≠ 混入材料）")
        check(not _c7({**_c7_base, "membership_sentinel_count": 1,
                       "topic_boundary_enforced": False})["satisfied"],
              "反例：sentinel 记入 membership 但 A 级结构性边界审计不成立（已记录/未混入/"
              "身份齐全不成立）⇒ aspect_membership 不成立 ⇒ 条件 7 不成立")
        check(_c7({**_c7_base, "membership_sentinel_count": 0,
                   "topic_boundary_enforced": False})["satisfied"],
              "反例：无 sentinel 记入 ⇒ 该条为空真（不得因此要求每个类别都必须有主题外邻居）")
        check(not _c7({**_c7_base, "aspect_links_sentinel_count": 1})["satisfied"],
              "反例：sentinel 混入 aspect_links（正式/上下文关联）⇒ aspect_membership 不成立")
        check(not _c7({**_c7_base,
                       "source_inventory_closure_problems": ["孤儿装配"]})["satisfied"],
              "反例：清单跨产物闭合问题非空 ⇒ aspect_membership 不成立")

        # 显式引用：未执行可接受，但**不允许**状态未记录（v9/v10 五类为空的错判来源）。
        _c7_noref = _c7({**_c7_base, "explicit_reference_audit": {}})
        check(not _c7_noref["satisfied"]
              and not _c7_noref["per_category"][CATEGORY_MAIN_BUSINESS]["checks"][
                  "continuation_and_reference_provenance"],
              "反例：显式引用状态为空（未记录而非未执行）⇒ 续表与引用来源项不成立")

        # 续表：声明有正向证明就必须给出逐 seed 步骤 outputs；否则不得算成立。
        check(not _c7({**_c7_base, "continuation_proof_count": 1,
                       "continuation_expansion_provenance": {
                           "seed_attribution_available": True,
                           "outputs_by_step": [], "frontier_by_seed": {}}})["satisfied"],
              "反例：声明续表正向证明却无任何 trace 步骤 outputs ⇒ 不成立")

        # 显式引用「已解析」声明必须同文档性可复核（§四）；不可复核（None）不得采信。
        check(not _c7({**_c7_base, "explicit_reference_audit": {
            "state": "resolved", "target_resolved": True,
            "same_document_bound": None, "resolution_attempted": True,
            "target_dangling": False, "attempt_stop_reasons": []}})["satisfied"],
              "反例：显式引用自报 resolved 但同文档性不可复核（None）⇒ "
              "续表与引用来源项不成立（未声明文档身份不得冒充已解析）")
        _c7_doc_ok = _c7({**_c7_base, "explicit_reference_audit": {
            "state": "resolved", "target_resolved": True,
            "same_document_bound": True, "resolution_attempted": True,
            "target_dangling": False, "attempt_stop_reasons": []}})
        check(_c7_doc_ok["satisfied"],
              "§四：显式引用真实解析且同文档性已复核 ⇒ 该项成立（正向对照）")

        # 其余各维逐项可反证（本地聚合之外的清单级把关）。
        for _field, _bad, _label in (
                ("source_inventory_present", False, "源对象清单缺失"),
                ("source_inventory_unmatched", ["T"], "存在未匹配恢复表"),
                ("source_inventory_orphans", ["A"], "存在孤儿装配"),
                ("boundary_verification_status", [], "边界验证记录为空"),
                ("unread_budget_stop_consistent", False, "未读范围与 stop 原因不一致"),
                ("budget_profile_name", "", "预算档位缺失"),
                ("expansion_trace_steps", 0, "无真实扩读 trace 步骤")):
            check(not _c7({**_c7_base, _field: _bad})["satisfied"],
                  f"反例：{_label} ⇒ 条件 7 不成立")

        # 多类别：任一类不成立 ⇒ 整条条件不成立（不得以其余类别通过掩盖）。
        _c7_multi = _inventory_boundary_unread_trace_audit(
            {CATEGORY_MAIN_BUSINESS: _cv(CATEGORY_MAIN_BUSINESS, _c7_base),
             CATEGORY_FINANCIAL_NOTES: _cv(CATEGORY_FINANCIAL_NOTES,
                                           {**_c7_base, "expansion_trace_steps": 0})},
            {CATEGORY_MAIN_BUSINESS: {"category_id": CATEGORY_MAIN_BUSINESS},
             CATEGORY_FINANCIAL_NOTES: {"category_id": CATEGORY_FINANCIAL_NOTES}})
        check(not _c7_multi["satisfied"]
              and _c7_multi["per_category"][CATEGORY_MAIN_BUSINESS]["holds"]
              and not _c7_multi["per_category"][CATEGORY_FINANCIAL_NOTES]["holds"],
              "反例：六类中任一类七项不成立 ⇒ 条件 7 不成立（逐类别披露，不取平均）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
