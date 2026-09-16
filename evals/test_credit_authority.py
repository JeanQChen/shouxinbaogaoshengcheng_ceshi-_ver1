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
from harness.credit_authority import (
    CREDIT_AUTHORITY_VERSION,
    resolve_credit_materials,
)

_COMPANY = "300750"
_DOC = "NDSD_KCZ_2026"
_DOCV = "sha256-2b3a1fb3de97f23c"
_SETV = "evidence-set-v1"
_FP = "d" * 64
_ASPECT = "company_debt_credit.authorized_application_ceiling"


def _material_env(*, company_id, document_id, document_version, evidence_set_version,
                  page, block_index, full_text, structured_payload=None,
                  offset=None, fragment_text=None):
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
        "created_dependency_fingerprint": _FP,
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


def _write_harness_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE topic_material_payload ("
        "payload_id TEXT NOT NULL PRIMARY KEY, object_type TEXT NOT NULL, "
        "authority_identity TEXT NOT NULL, version TEXT NOT NULL, "
        "locator_json TEXT NOT NULL, source_content_hash TEXT NOT NULL, "
        "payload_hash TEXT NOT NULL, payload_bytes BLOB NOT NULL, "
        "created_dependency_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)")
    for r in rows:
        conn.execute(
            "INSERT INTO topic_material_payload VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["payload_hash"], "evidence_span",
             f"evidence:{r['evidence_id']}", "1",
             json.dumps(r["locator"], ensure_ascii=False, sort_keys=True),
             r["source_content_hash"], r["payload_hash"], r["payload_bytes"], _FP, "now"))
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


def _write_run_dir(run_dir: Path, entries: list[dict], links: list[dict],
                   previews: dict[str, str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "material_index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "aspect_links.json").write_text(
        json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
    preview_dir = run_dir / "payload_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for mid, payload in previews.items():
        (preview_dir / f"{mid}.json").write_text(
            json.dumps({"material_id": mid, "payload": payload}, ensure_ascii=False),
            encoding="utf-8")


def _entry(material_id: str, payload_hash: str, disposition: str = "inside_boundary",
           aspect_ids: list[str] | None = None) -> dict:
    return {
        "material_id": material_id,
        "material_type": "evidence_span",
        "source_identity": "evidence:00000000000000000000000000000000",
        "component_evidence_id": "00000000000000000000000000000000",
        "source_content_hash": "0" * 64,
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
        "aspect_role": "source",
        "aspect_ids": aspect_ids if aspect_ids is not None else [_ASPECT],
    }


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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
            {"mat-1": json.dumps({"payload": TAMPERED})})

        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats) == 1, "E.1：权威材料解析成功（1 条）")
        check(mats and mats[0]["text"] == TEXT,
              "E.1：返回权威 harness.db 正文（非被篡改预览 9,999亿）")
        check(mats and mats[0]["payload_hash"] == m["payload_hash"]
              and mats[0]["authority_verdict"] == "authoritative",
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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": "company_debt_credit.unused_credit", "material_id": "mat-1",
             "role": "source"}],  # 不属于目标 aspect
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
            _entry("mat-1", m["payload_hash"], disposition="outside_boundary_sentinel"),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
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
            _entry("mat-1", m["payload_hash"], disposition="fragment_projection"),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
            {"mat-1": json.dumps({"payload": full})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(len(mats) == 1 and mats[0]["text"] == frag,
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
            _entry("mat-1", m["payload_hash"], disposition="fragment_projection"),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
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
            _entry("mat-1", m["payload_hash"]),
        ], [{"aspect_id": _ASPECT, "material_id": "mat-1", "role": "source"}],
            {"mat-1": json.dumps({"payload": TEXT})})
        mats = resolve_credit_materials(root / "run", harness_db_path=root / "harness.db",
                                        evidence_db_path=root / "evidence.db",
                                        company_id=_COMPANY, aspect_id=_ASPECT)
        check(mats == [], "E.2：document_version 非 current → fail-closed")

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
