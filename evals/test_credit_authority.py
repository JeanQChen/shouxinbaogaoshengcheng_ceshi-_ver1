"""Eval: R2 P1-E 授信权威链（反例先行）。

用法: python -m evals.test_credit_authority

覆盖（Codex 独立审计 E.1–E.4）：
- E.1：预览/提取入口经正式 ``TopicMaterialPayloadResolver``（harness.db 权威 payload 字节）
  获取材料，绝不信任 ``payload_preview/*.json`` 的复制字段。反例：预览副本被篡改，权威
  harness.db 未被篡改 → 返回权威正文；预览有副本但 harness.db 无 payload 行（dangling）
  → 不产出该材料。
- E.2：唯一权威重算函数验证 current document/current set、block identity（content_hash）、
  evidence_id、跨公司串读。反例：非 current 版本 / source_content_hash 与块不符 /
  跨 company → 均 fail-closed。
- E.3：fragment 必须校验实际 fragment bytes + offset + 来源身份。反例：offset 与父块
  前缀不符 → fail-closed。
- E.4：未知/空/不合格 disposition 与缺正式 aspect association 一律 fail-closed。

全部离线：严格只读临时 harness.db / evidence.db，零 LLM/网络，零 DB 写入。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evidence import ids
from harness import run_manifest as RM
from harness.credit_authority import (
    CREDIT_AUTHORITY_VERSION,
    PROMOTION_RULE_VERSION,
    ROLE_DISPOSITIONS,
    resolve_credit_materials,
    recompute_material_id,
)

_COMPANY = "300750"
_DOC = "NDSD_KCZ_2026"
_DOCV = "sha256-2b3a1fb3de97f23c"
_SETV = "evidence-set-v1"
_FP = "d" * 64
_ASPECT = "company_debt_credit.authorized_application_ceiling"


def _material_env(*, company_id, document_id, document_version, evidence_set_version,
                  page, block_index, full_text, structured_payload=None,
                  offset=None, fragment_text=None, dependency_fingerprint=None):
    """构造一条**权威链自洽**的材料（信封 + 身份全部重算，绝不手写哈希）。

    - 非片段：content.text = full_text；source_content_hash = content_hash(full_text)。
    - 片段：content.text = fragment_text（= full_text[:offset].strip()）；
      source_content_hash 仍 = content_hash(full_text)（父块身份）。
    """
    source_content_hash = ids.content_hash(full_text, structured_payload)
    section_path = "发行人资信状况"
    locator = {
        "locator_type": "evidence",
        "document_id": document_id,
        "document_version": document_version,
        "section_path": section_path,
        "page": page,
        "table_title": None,
        "block_range": [block_index, block_index],
        "offset": offset,
    }
    evidence_id = ids.make_evidence_id(
        company_id, document_id, document_version, evidence_set_version,
        page, block_index, source_content_hash)
    content_text = (fragment_text if offset is not None else full_text)
    envelope = {
        "material_payload_version": 1,
        "object_type": "evidence_span",
        "authority_identity": f"evidence:{evidence_id}",
        "evidence_id": evidence_id,
        "source_content_hash": source_content_hash,
        "created_dependency_fingerprint": (dependency_fingerprint
                                           if dependency_fingerprint is not None
                                           else RM.runner_dependency_fingerprint()),
        "locator": locator,
        "document_identity": {
            "company_id": company_id,
            "document_id": document_id,
            "document_version": document_version,
            "evidence_set_version": evidence_set_version,
        },
        "content": {"text": content_text, "structured_payload": structured_payload},
    }
    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()
    return {
        "evidence_id": evidence_id,
        "source_content_hash": source_content_hash,
        "locator": locator,
        "payload_bytes": payload_bytes,
        "payload_hash": payload_hash,
        "full_text": full_text,
        "fragment_text": content_text,
    }


def _write_harness_db(path: Path, rows: list[dict], *, dependency_fingerprint: str | None = None) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE topic_material_payload ("
        "payload_id TEXT NOT NULL PRIMARY KEY, object_type TEXT NOT NULL, "
        "authority_identity TEXT NOT NULL, version TEXT NOT NULL, "
        "locator_json TEXT NOT NULL, source_content_hash TEXT NOT NULL, "
        "payload_hash TEXT NOT NULL, payload_bytes BLOB NOT NULL, "
        "created_dependency_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)")
    dep = dependency_fingerprint if dependency_fingerprint is not None \
        else RM.runner_dependency_fingerprint()
    for r in rows:
        conn.execute(
            "INSERT INTO topic_material_payload VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["payload_hash"], "evidence_span",
             f"evidence:{r['evidence_id']}", "1",
             json.dumps(r["locator"], ensure_ascii=False, sort_keys=True),
             r["source_content_hash"], r["payload_hash"], r["payload_bytes"], dep, "now"))
    conn.commit()
    conn.close()


def _write_evidence_db(path: Path, company_id: str, document_id: str,
                       document_version: str, evidence_set_version: str,
                       blocks: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE documents (company_id TEXT, document_id TEXT, "
                 "document_version TEXT, status TEXT)")
    conn.execute("INSERT INTO documents VALUES (?,?,?,?)",
                 (company_id, document_id, document_version, "current"))
    conn.execute("CREATE TABLE evidence_sets (company_id TEXT, document_id TEXT, "
                 "document_version TEXT, evidence_set_version TEXT, status TEXT)")
    conn.execute("INSERT INTO evidence_sets VALUES (?,?,?,?,?)",
                 (company_id, document_id, document_version, evidence_set_version, "current"))
    conn.execute("CREATE TABLE evidence_blocks (evidence_id TEXT PRIMARY KEY, "
                 "company_id TEXT, document_id TEXT, document_version TEXT, "
                 "evidence_set_version TEXT, source_name TEXT, source_type TEXT, "
                 "source_uri TEXT, page_number INTEGER, block_index INTEGER, "
                 "section_path TEXT, evidence_type TEXT, text TEXT, "
                 "structured_payload TEXT, report_period TEXT, published_at TEXT, "
                 "content_hash TEXT)")
    for b in blocks:
        conn.execute(
            "INSERT INTO evidence_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["evidence_id"], company_id, document_id, document_version,
             evidence_set_version, "src", "pdf", None, b["page"], b["block_index"],
             json.dumps(["发行人资信状况"], ensure_ascii=False), "paragraph",
             b["text"], None, None, None, b["content_hash"]))
    conn.commit()
    conn.close()


def _role_for(disposition: str) -> str:
    """fixture 侧「处置 → role」白名单（与 harness.credit_authority.ROLE_DISPOSITIONS 同构）。"""
    for role, disposals in ROLE_DISPOSITIONS.items():
        if disposition in disposals:
            return role
    return "context_candidate"


_FIXTURE_REASON_CODE = {
    "seed": "seed",
    "inside_boundary": "same_section_continuity",
    "fragment_projection": "fragment_projection",
    "context_candidate": "mixed_block_context_candidate",
    "outside_boundary_sentinel": "backward_previous_section_heading",
}
_FIXTURE_DIRECTION = {
    "seed": "seed", "inside_boundary": "forward", "fragment_projection": "forward",
    "context_candidate": "forward", "outside_boundary_sentinel": "adjacent_blocks_before",
}


def _write_run_dir(run_dir: Path, entries: list[dict], links: list[dict],
                   previews: dict[str, str], *, manifest: bool = True,
                   manifest_dependency_fingerprint: str | None = None,
                   decisions: list[dict] | None = None) -> None:
    """写一个 run 目录。

    §五.2/§四.D：关联资产的**自报 role 不被采信**，必须由 ``boundary_decisions.json`` 的
    真实决策记录逐条佐证。因此本 fixture 统一补齐 ``boundary_disposition_identity`` 并写出
    与之逐项相符的决策记录（``decisions`` 显式给出时用其覆盖，供反例使用）。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "material_index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    by_mid = {str(e.get("material_id") or ""): e for e in entries if isinstance(e, dict)}
    for link in links:
        entry = by_mid.get(str(link.get("material_id") or ""))
        if not isinstance(entry, dict):
            link.setdefault("boundary_disposition_identity", [])
            continue
        disposition = str(link.get("disposition") or "")
        link["boundary_disposition_identity"] = [{
            "aspect_id": link.get("aspect_id", ""),
            "evidence_id": entry.get("component_evidence_id", ""),
            "direction": _FIXTURE_DIRECTION.get(disposition, ""),
            "disposition": disposition,
            "reason_code": _FIXTURE_REASON_CODE.get(disposition, "unspecified"),
        }]
    (run_dir / "aspect_links.json").write_text(
        json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
    if decisions is None:
        decisions = [{
            "aspect_id": rec["aspect_id"],
            "evidence_id": rec["evidence_id"],
            "direction": rec["direction"],
            "disposition": rec["disposition"],
            "reason_code": rec["reason_code"],
            "content_hash": by_mid[str(link.get("material_id") or "")].get(
                "source_content_hash", ""),
            "document_id": _DOC,
            "document_version": _DOCV,
            "seed_evidence_id": rec["evidence_id"],
        } for link in links
            for rec in (link.get("boundary_disposition_identity") or [])
            if str(link.get("material_id") or "") in by_mid]
    (run_dir / "boundary_decisions.json").write_text(
        json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_dir = run_dir / "payload_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for mid, payload in previews.items():
        (preview_dir / f"{mid}.json").write_text(
            json.dumps({"material_id": mid, "payload": payload}, ensure_ascii=False),
            encoding="utf-8")
    if not manifest:
        return  # 反例：缺 run manifest（E.9/E.3）→ 关联资产与依赖不可验证。
    man = RM.build_run_manifest(
        run_id="test-run",
        generated_at="20260916T000000Z",
        seed_manifest_fingerprint="e" * 64,
        artifacts={
            "material_index.json": RM.artifact_fingerprint(run_dir / "material_index.json"),
            "aspect_links.json": RM.artifact_fingerprint(run_dir / "aspect_links.json"),
            "boundary_decisions.json": RM.artifact_fingerprint(
                run_dir / "boundary_decisions.json"),
        },
        counts={"materials": len(entries), "assemblies": 0},
        evidence_db={"path": "evidence.db"},
        harness_db={"path": "harness.db"},
        accept_reject_audit={"materials_accepted": len(entries), "materials_rejected": 0},
    )
    if manifest_dependency_fingerprint is not None:
        man["dependency_fingerprint"] = manifest_dependency_fingerprint
        man["run_manifest_fingerprint"] = RM.canonical_manifest_fingerprint(man)
    (run_dir / RM.RUN_MANIFEST_NAME).write_text(
        json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")


def _entry(material_id: str, payload_hash: str, disposition: str = "inside_boundary",
           aspect_ids: list[str] | None = None, *, material_type: str = "evidence_span",
           evidence_id: str = "0" * 32, source_content_hash: str = "0" * 64) -> dict:
    return {
        "material_id": material_id,
        "material_type": material_type,
        "source_identity": f"evidence:{evidence_id}",
        "component_evidence_id": evidence_id,
        "source_content_hash": source_content_hash,
        "payload_hash": payload_hash,
        "document_id": _DOC,
        "document_version": _DOCV,
        "section_path": "发行人资信状况",
        "page": 111,
        "block_range": [0, 0],
        "table_title": None,
        "authority_verdict": "authoritative",
        "boundary_disposition": disposition,
        "boundary_reason_code": "",
        "aspect_role": _role_for(disposition),
        "aspect_ids": aspect_ids if aspect_ids is not None else [_ASPECT],
    }


def _link(aspect_id: str, material_id: str, disposition: str) -> dict:
    """aspect association 条目（自报 role 按提升白名单给出；是否成立由真实决策记录佐证决定）。

    §五.2/§四.D：role 只是条目**自报**，必须由 ``boundary_decisions.json`` 的决策记录逐条
    佐证后才被采信 —— 本 fixture 由 ``_write_run_dir`` 统一补齐身份记录与决策记录。
    """
    return {
        "aspect_id": aspect_id,
        "material_id": material_id,
        "disposition": disposition,
        "role": _role_for(disposition),
    }


def _mid(m: dict) -> str:
    """材料身份规范形：从**权威 payload 信封**重算 material_id（绝不取自 index 自报）。"""
    env = json.loads(m["payload_bytes"].decode("utf-8"))
    return recompute_material_id(env, m["payload_bytes"])


def _write_only(root: Path, m: dict, *, disposition: str, name: str = "run") -> Path:
    """写一个「单材料 + 已提升关联 + 有效 manifest」的 run 目录，返回该目录路径。"""
    run = root / name
    _write_run_dir(run, [_entry(_mid(m), m["payload_hash"], disposition=disposition)],
                   [_link(_ASPECT, _mid(m), disposition)],
                   {"mat-1": json.dumps({"payload": m.get("fragment_text", "")})})
    return run


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

    TEXT = "2025年度公司及控股子公司拟向相关金融机构申请不超过人民币6,000亿元的综合授信额度。"
    TAMPERED = "2025年度公司及控股子公司拟向相关金融机构申请不超过人民币9,999亿元的综合授信额度。"

    # ------------------------------------------------------------------
    # 1. E.1：权威 harness.db 正文优先于被篡改的 payload_preview 副本
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TAMPERED})})

        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats) == 1, "E.1：权威材料解析成功（1 条）")
        check(mats and mats[0].text == TEXT,
              "E.1：返回权威 harness.db 正文（非被篡改预览 9,999亿）")
        check(mats and mats[0].payload_hash == m["payload_hash"]
              and mats[0].authority_verdict == "authoritative",
              "E.1：payload_hash 与权威 verdict 自洽")

    # ------------------------------------------------------------------
    # 2. E.1：harness.db 无 payload 行（dangling）→ 预览有副本也不产出事实
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [])  # 空：dangling
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.1：dangling（harness.db 无 payload）→ fail-closed，预览副本不被信任")

    # ------------------------------------------------------------------
    # 3. E.4：缺正式 aspect association → 排除
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link("company_debt_credit.unused_credit", _mid(m), "inside_boundary")],  # 不属于目标 aspect
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.4：缺正式 aspect association → fail-closed（排除）")

    # ------------------------------------------------------------------
    # 4. E.4：不合格 disposition → 排除
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"], disposition="outside_boundary_sentinel"),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.4：sentinel disposition → fail-closed（排除）")

    # ------------------------------------------------------------------
    # 5. E.2：跨 company 串读 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id="999999", document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.2：材料 company_id 与请求 company 不符 → 跨公司串读 fail-closed")

    # ------------------------------------------------------------------
    # 6. E.3：fragment 实际 bytes + offset 校验通过（合法片段被采纳）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        full = "2025年度公司及控股子公司拟申请不超过人民币6,000亿元的综合授信额度。十、在建工程项目 后续内容。"
        frag = "2025年度公司及控股子公司拟申请不超过人民币6,000亿元的综合授信额度。"
        offset = len(frag)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=full,
                          offset=offset, fragment_text=frag)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": full, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"], disposition="fragment_projection"),
        ], [_link(_ASPECT, _mid(m), "fragment_projection")],
            {"mat-1": json.dumps({"payload": full})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats) == 1 and mats[0].text == frag,
              "E.3：合法 fragment（offset 与父块前缀一致）被采纳")

    # ------------------------------------------------------------------
    # 7. E.3：fragment offset 与父块前缀不符 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        full = "主题内前缀。十、在建工程项目 后续内容。"
        frag = "被篡改的片段。"
        offset = len("主题内前缀。")
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=full,
                          offset=offset, fragment_text=frag)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": full, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"], disposition="fragment_projection"),
        ], [_link(_ASPECT, _mid(m), "fragment_projection")],
            {"mat-1": json.dumps({"payload": full})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.3：fragment offset 与父块前缀不符 → fail-closed")

    # ------------------------------------------------------------------
    # 8. E.2：source_content_hash 与块 content_hash 不符 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        # 块 content_hash 与材料 source_content_hash 不一致。
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": "被篡改的块正文", "content_hash": "a" * 64,
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.2：块 content_hash 与材料 source_content_hash 不符 → fail-closed")

    # ------------------------------------------------------------------
    # 9. E.2：非 current document version → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        # 文档 current 版本是另一个 version。
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, "sha256-oldversion",
                           _SETV, [{
                               "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
                               "text": TEXT, "content_hash": m["source_content_hash"],
                           }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"]),
        ], [_link(_ASPECT, _mid(m), "inside_boundary")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.2：document_version 非 current → fail-closed")

    # ------------------------------------------------------------------
    # 11. E.9/E.3：缺 run manifest → 关联资产与依赖不可验证 → fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})}, manifest=False)
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.9：缺 run manifest → run/Pack 身份不可验证 → fail-closed（不产出材料）")

    # ------------------------------------------------------------------
    # 12. E.3：payload 行的 created_dependency_fingerprint 与 run manifest 不符 → 排除
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }], dependency_fingerprint="0" * 64)
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.3：payload 行依赖指纹与 manifest（Contract/SourcePolicy/R2 dependency）不符 → 排除")

    # ------------------------------------------------------------------
    # 12b. E.3：manifest 的 dependency_fingerprint 被改写 → manifest 自身指纹不符 → 排除
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }], dependency_fingerprint="0" * 64)
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})},
                       manifest_dependency_fingerprint="0" * 64)
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.3：manifest 依赖指纹非当前 Contract/SourcePolicy/R2 dependency → fail-closed")

    # ------------------------------------------------------------------
    # 13. E.1：material_id 不得由 material_index 自报（必须从权威信封内容重算）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        real = _mid(m)
        _write_run_dir(root / "run", [_entry("mat-selfreported", m["payload_hash"])],
                       [_link(_ASPECT, "mat-selfreported", "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.1：material_id 由 material_index 自报（≠ 权威信封重算）→ fail-closed")
        check(real != "mat-selfreported", "E.1：自报 material_id 与重算身份确实不同（反例有效）")

        # 合法路径：material_id == 权威重算身份 → 采纳，且返回身份为重算值。
        _write_run_dir(root / "run2", [_entry(real, m["payload_hash"])],
                       [_link(_ASPECT, real, "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats2 = resolve_credit_materials(root / "run2", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats2) == 1 and mats2[0].material_id == real,
              "E.1：material_id == 权威重算身份 → 采纳（身份来自内容寻址，非自报）")

    # ------------------------------------------------------------------
    # 14. E.2：context_candidate 不得直接形成正式事实（必须显式提升为 source/supporting）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [
            _entry(_mid(m), m["payload_hash"], disposition="context_candidate"),
        ], [_link(_ASPECT, _mid(m), "context_candidate")],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.2：context_candidate 不得直接形成正式事实 → fail-closed（排除）")

        # 合法提升：inside_boundary 显式提升为 supporting 且关联资产 role 一致 → 采纳。
        _write_run_dir(root / "run2", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats2 = resolve_credit_materials(root / "run2", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats2) == 1 and mats2[0].role == "supporting",
              "E.2：inside_boundary 显式提升为 supporting → 采纳（role 来自提升规则）")

        # run3：自报 role=source 但 dispute 是 inside_boundary（不在 source 的提升白名单内）
        # → fail-closed（§五.3：非提升处置不得产生任何正式 role）。
        _write_run_dir(root / "run3", [_entry(_mid(m), m["payload_hash"])],
                       [{"aspect_id": _ASPECT, "material_id": _mid(m), "role": "source",
                         "disposition": "inside_boundary"}],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats3 = resolve_credit_materials(root / "run3", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats3 == [],
              "E.1：role=source 与可提升处置白名单不一致（inside_boundary）→ fail-closed")

        # run4：关联资产自报 role 合法，但**没有任何真实边界决策记录佐证**（§五.2/§四.D）
        # → fail-closed（自报 role 一律不采信）。
        _write_run_dir(root / "run4", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})}, decisions=[])
        mats4 = resolve_credit_materials(root / "run4", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats4 == [],
              "§五.2：缺真实边界决策记录佐证 → 关联不自证 → fail-closed（排除）")

        # run5：决策记录的 content_hash 与材料库 source_content_hash 不符（关联未钉到真实块）
        # → fail-closed。
        _write_run_dir(root / "run5", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})},
                       decisions=[{
                           "aspect_id": _ASPECT,
                           "evidence_id": "0" * 32,
                           "direction": "forward",
                           "disposition": "inside_boundary",
                           "reason_code": "same_section_continuity",
                           "content_hash": "f" * 64,
                       }])
        mats5 = resolve_credit_materials(root / "run5", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats5 == [],
              "§五.2：决策记录 content_hash 与材料库 source_content_hash 不符 → fail-closed")

        # run6：缺 boundary_decisions.json（佐证资产缺失）→ 关联不可验证 → fail-closed。
        _write_run_dir(root / "run6", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        (root / "run6" / "boundary_decisions.json").unlink()
        mats6 = resolve_credit_materials(root / "run6", harness_db_path=root / "harness.db",
                                         evidence_db_path=root / "evidence.db",
                                         company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats6 == [],
              "§五.2：缺 boundary_decisions.json（内容指纹不可验证）→ fail-closed")

    # ------------------------------------------------------------------
    # 15. E.1：aspect association 资产被篡改（指纹不符）→ fail-closed
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": TEXT, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        # 事后篡改 aspect_links.json（manifest 记录的内容指纹不再匹配）。
        links_path = root / "run" / "aspect_links.json"
        links_path.write_text(json.dumps(
            [_link(_ASPECT, _mid(m), "inside_boundary"),
             _link("company_debt_credit.used_credit", _mid(m), "inside_boundary")],
            ensure_ascii=False, indent=2), encoding="utf-8")
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.1：aspect association 资产内容指纹与 manifest 不符（被篡改）→ fail-closed")

    # ------------------------------------------------------------------
    # 16. E.4：fragment 截断点必须严格在块内部（offset 非严格内部 → 排除）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        full = "主题内前缀。十、在建工程项目 后续内容。"
        offset = len("主题内前缀。")
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=full,
                          offset=offset, fragment_text="主题内前缀。")
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": full, "content_hash": m["source_content_hash"],
        }])
        check(len(resolve_credit_materials(
            _write_only(root, m, disposition="fragment_projection"), harness_db_path=root / "harness.db",
            evidence_db_path=root / "evidence.db", company_id=_COMPANY, aspect_id=_ASPECT)) == 1,
            "E.4：fragment 严格内部 + 前缀一致 → 采纳")

        # 整块被当作 fragment（offset == len(parent)）→ 排除。
        full2 = "整块正文。"
        m2 = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                           evidence_set_version=_SETV, page=111, block_index=0,
                           full_text=full2, offset=len(full2), fragment_text=full2)
        _write_harness_db(root / "h2.db", [{
            "payload_hash": m2["payload_hash"], "evidence_id": m2["evidence_id"],
            "locator": m2["locator"], "source_content_hash": m2["source_content_hash"],
            "payload_bytes": m2["payload_bytes"],
        }])
        _write_evidence_db(root / "e2.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m2["evidence_id"], "page": 111, "block_index": 0,
            "text": full2, "content_hash": m2["source_content_hash"],
        }])
        check(resolve_credit_materials(
            _write_only(root, m2, disposition="fragment_projection", name="run2"),
            harness_db_path=root / "h2.db", evidence_db_path=root / "e2.db",
            company_id=_COMPANY, aspect_id=_ASPECT) == [],
            "E.4：offset == len(父块)（整块冒充 fragment）→ 非严格内部截断点 → 排除")

    # ------------------------------------------------------------------
    # 17. E.4：非片段材料的正文必须与父块正文一致（正文被替换 → 排除）
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        m = _material_env(company_id=_COMPANY, document_id=_DOC, document_version=_DOCV,
                          evidence_set_version=_SETV, page=111, block_index=0, full_text=TEXT)
        _write_harness_db(root / "harness.db", [{
            "payload_hash": m["payload_hash"], "evidence_id": m["evidence_id"],
            "locator": m["locator"], "source_content_hash": m["source_content_hash"],
            "payload_bytes": m["payload_bytes"],
        }])
        # 父块正文被替换为另一段文本（hash 同步改，故块身份校验不拦），正文直接比对才会拦。
        tampered = TAMPERED
        _write_evidence_db(root / "evidence.db", _COMPANY, _DOC, _DOCV, _SETV, [{
            "evidence_id": m["evidence_id"], "page": 111, "block_index": 0,
            "text": tampered, "content_hash": m["source_content_hash"],
        }])
        _write_run_dir(root / "run", [_entry(_mid(m), m["payload_hash"])],
                       [_link(_ASPECT, _mid(m), "inside_boundary")],
                       {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [],
              "E.4：非片段材料 payload 正文与父块正文不一致 → fail-closed")

    # ------------------------------------------------------------------
    # 10. 版本号：权威链模块有版本（E.11）
    # ------------------------------------------------------------------
    check(isinstance(CREDIT_AUTHORITY_VERSION, str) and CREDIT_AUTHORITY_VERSION,
          "E.11：credit_authority 有版本号")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
