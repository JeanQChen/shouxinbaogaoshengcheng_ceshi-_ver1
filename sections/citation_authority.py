"""Phase 4 Batch C — 正式引用权威性校验（确定性、只读、无 LLM）。

任务书关闭前定点修复二：任何正式 Claim 的 CitationRef 在进入章节前必须通过
「权威性校验」。本模块只做只读查询与纯判定，**绝不调用 init_db() 创建或迁移
外部库**——通过 `module._db_path = Path(db)` 指向既有库后直接只读查询。

三类引用各自权威性口径：
- **evidence**：evidence_id 存在；company_id 匹配；属于当前文档版本 + 当前证据集合；
  page_number 与权威 EvidenceBlock 一致；无正文（不可回查）不得通过。
- **structured**：snapshot_id 存在；company/scope/currency/purpose 匹配；快照
  current + validity==valid + 非 report_blocked + 非 quarantine；item/formula/period
  真实存在（复用 ``harness.structured_provenance.query_snapshot_authority``）。
- **external**：source_snapshot_id 存在；快照 status==SNAPSHOTTED；正文非空；
  content_hash 与正文重算一致；不是仅 URL / 仅 snippet；published_at 未知 → 记
  soft warning（不能支撑强时点结论，但不硬过滤）。

权威查询失败时标签可降级，但正式 Claim **不得 fail-open**：任一引用校验失败 → 该
Claim 被过滤，并由上层生成显式 unresolved/diagnostic。

CLI: python -m sections.citation_authority --self-check
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from external_v2 import schema as XS
from harness import schema as HS
from routing import schema as RS

logger = logging.getLogger("sections.citation_authority")


# ---------------------------------------------------------------------------
# 判定结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitationVerdict:
    """单个 CitationRef 的权威性判定。"""

    ref_type: str
    valid: bool
    reason: str | None = None          # hard invalid 原因（valid=False 时非空）
    warnings: tuple[str, ...] = ()     # soft 降级标记（valid=True 时可能非空）


# ---------------------------------------------------------------------------
# 纯判定函数（无 I/O，注入已解析对象）
# ---------------------------------------------------------------------------

def validate_evidence_ref(
    ref: HS.CitationRef,
    *,
    company_id: str,
    evidence,                          # EvidenceBlock | None
    current_document_version: Callable | None = None,
    current_evidence_set: Callable | None = None,
) -> str | None:
    """Evidence 引用权威性。返回 None=通过；否则失败原因字符串。"""
    if not ref.evidence_id:
        return "evidence_id_missing"
    if evidence is None:
        return "evidence_not_found"
    if evidence.company_id != company_id:
        return "evidence_company_mismatch"
    if current_document_version is not None:
        cv = current_document_version(company_id, evidence.document_id)
        if cv is None or cv != evidence.document_version:
            return "evidence_not_current_document"
    if current_evidence_set is not None:
        cs = current_evidence_set(company_id, evidence.document_id, evidence.document_version)
        if cs is None or cs != evidence.evidence_set_version:
            return "evidence_not_current_set"
    if ref.page_number is not None and evidence.page_number != ref.page_number:
        return "evidence_page_mismatch"
    if not (evidence.text or "").strip():
        return "evidence_no_body"
    return None


def validate_structured_ref(
    ref: HS.CitationRef,
    *,
    company_id: str,
    context: RS.RouteContext | None,
    snapshot_authority: Callable | None,
    snapshot_get: Callable | None,
    metric_get: Callable | None,
    item_list: Callable | None,
    formula_get: Callable | None,
) -> str | None:
    """Structured 引用权威性（Store 权威 + 维度一致 + item/formula/period 真实存在）。"""
    if not ref.snapshot_id:
        return "structured_snapshot_missing"
    if snapshot_authority is None:
        return "authority_unavailable"
    auth = snapshot_authority(ref.snapshot_id)
    if auth is None or not auth.exists:
        return "snapshot_not_found"
    if not auth.is_current:
        return "snapshot_not_current"
    if auth.validity != "valid":
        return f"snapshot_{auth.validity or 'validity_missing'}"
    if auth.report_blocked:
        return "snapshot_report_blocked"
    if auth.quarantined:
        return "snapshot_quarantined"

    if snapshot_get is None:
        return "authority_unavailable"
    snap = snapshot_get(ref.snapshot_id)
    if snap is None:
        return "snapshot_not_found"
    if snap.company_id != company_id:
        return "company_mismatch"
    if context is not None:
        if snap.scope != context.scope:
            return "scope_mismatch"
        if snap.currency != context.currency:
            return "currency_mismatch"
        if snap.purpose != context.purpose:
            return "purpose_mismatch"

    if ref.formula_id:
        if not ref.formula_version:
            return "formula_version_missing"
        if formula_get is not None and formula_get(ref.formula_id, ref.formula_version) is None:
            return "formula_not_found"
        if metric_get is not None:
            if not ref.period:
                return "period_missing"
            if metric_get(ref.snapshot_id, ref.formula_id, ref.formula_version, ref.period) is None:
                return "metric_result_not_found"
    elif ref.item_code:
        if item_list is not None:
            if not ref.period:
                return "period_missing"
            items = item_list(ref.snapshot_id)
            if not any(it.standard_item_code == ref.item_code and it.report_period == ref.period
                       for it in items):
                return "item_not_found"
    else:
        return "structured_no_item_or_formula"
    return None


def validate_external_ref(
    ref: HS.CitationRef,
    *,
    company_id: str,
    snapshot,                          # ExternalSourceSnapshot | None
    content_hash_fn: Callable | None = None,
) -> tuple[str | None, tuple[str, ...]]:
    """External 引用权威性。返回 (hard_reason|None, soft_warnings)。"""
    if not ref.source_snapshot_id:
        return "external_snapshot_id_missing", ()
    if snapshot is None:
        return "external_snapshot_not_found", ()
    if snapshot.status != "SNAPSHOTTED":
        return f"external_snapshot_{snapshot.status}", ()
    if snapshot.company_id and snapshot.company_id != company_id:
        return "external_company_mismatch", ()
    body = (snapshot.content_text or "").strip()
    if not body:
        return "external_content_empty", ()
    if content_hash_fn is not None and content_hash_fn(snapshot.content_text) != snapshot.content_hash:
        return "external_content_hash_mismatch", ()
    if not snapshot.content_hash:
        return "external_content_hash_missing", ()
    # 仅 URL / 仅 snippet：正文必须实质区别于 URL 与搜索摘要。
    if body in (snapshot.canonical_url.strip(), snapshot.original_url.strip()):
        return "external_just_url", ()
    snippet = (snapshot.snippet or "").strip()
    if snippet and body == snippet:
        return "external_just_snippet", ()
    warnings: tuple[str, ...] = ()
    if snapshot.published_at is None:
        warnings = ("published_at_unknown",)
    return None, warnings


# ---------------------------------------------------------------------------
# 编排器（注入式，便于离线测试；只读查询，不 init_db）
# ---------------------------------------------------------------------------

class CitationAuthority:
    """正式引用权威校验器（只读；lookup 为 None 时 fail-closed）。"""

    def __init__(self, *, company_id: str, context: RS.RouteContext | None = None,
                 evidence_get: Callable | None = None,
                 current_document_version: Callable | None = None,
                 current_evidence_set: Callable | None = None,
                 snapshot_authority: Callable | None = None,
                 snapshot_get: Callable | None = None,
                 metric_get: Callable | None = None,
                 item_list: Callable | None = None,
                 formula_get: Callable | None = None,
                 external_get: Callable | None = None):
        self.company_id = company_id
        self.context = context
        self.evidence_get = evidence_get
        self.current_document_version = current_document_version
        self.current_evidence_set = current_evidence_set
        self.snapshot_authority = snapshot_authority
        self.snapshot_get = snapshot_get
        self.metric_get = metric_get
        self.item_list = item_list
        self.formula_get = formula_get
        self.external_get = external_get

    def validate(self, ref: HS.CitationRef) -> CitationVerdict:
        if ref.ref_type == "evidence":
            if self.evidence_get is None:
                return CitationVerdict("evidence", False, "authority_unavailable")
            reason = validate_evidence_ref(
                ref, company_id=self.company_id, evidence=self.evidence_get(ref.evidence_id),
                current_document_version=self.current_document_version,
                current_evidence_set=self.current_evidence_set)
            return CitationVerdict("evidence", reason is None, reason)
        if ref.ref_type == "structured":
            reason = validate_structured_ref(
                ref, company_id=self.company_id, context=self.context,
                snapshot_authority=self.snapshot_authority, snapshot_get=self.snapshot_get,
                metric_get=self.metric_get, item_list=self.item_list,
                formula_get=self.formula_get)
            return CitationVerdict("structured", reason is None, reason)
        if ref.ref_type == "external":
            if self.external_get is None:
                return CitationVerdict("external", False, "authority_unavailable")
            reason, warnings = validate_external_ref(
                ref, company_id=self.company_id, snapshot=self.external_get(ref.source_snapshot_id),
                content_hash_fn=XS.content_hash)
            return CitationVerdict("external", reason is None, reason, warnings)
        return CitationVerdict(ref.ref_type or "?", False, "unknown_ref_type")


def _ro_conn(path: str | Path) -> sqlite3.Connection:
    """只读连接：库文件缺失/不可读 fail-closed，绝不创建、不迁移。

    复用 scripts.demo_preflight 的 ``sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)``
    模式——通过 URI 显式只读打开，缺文件直接抛错（不建空库）。

    相对路径/含空格或中文路径先经 ``expanduser().resolve()`` 规范化为绝对路径，否则
    ``Path.as_uri()`` 对相对路径抛 ``ValueError``（Windows 与 Linux 行为一致）。
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"只读库不存在（不创建）: {p}")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# 只读查询层（复用各 store 的 `_row_to_*` 反序列化器，SQL 只读、每查询独立连接）
# ---------------------------------------------------------------------------

def _ro_evidence_get(ev_db: str | Path, evidence_id: str):
    from evidence import store as estore
    conn = _ro_conn(ev_db)
    try:
        row = conn.execute(
            "SELECT * FROM evidence_blocks WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        return estore._row_to_evidence(row) if row is not None else None
    finally:
        conn.close()


def _ro_current_document_version(ev_db: str | Path, company_id: str, document_id: str) -> str | None:
    conn = _ro_conn(ev_db)
    try:
        row = conn.execute(
            "SELECT d.document_version FROM documents d "
            "JOIN evidence_sets es ON es.company_id=d.company_id AND es.document_id=d.document_id "
            "AND es.document_version=d.document_version AND es.status='current' "
            "WHERE d.company_id=? AND d.document_id=? AND d.status='current' LIMIT 1",
            (company_id, document_id),
        ).fetchone()
        return row["document_version"] if row is not None else None
    finally:
        conn.close()


def _ro_current_evidence_set(ev_db: str | Path, company_id: str, document_id: str,
                             document_version: str) -> str | None:
    conn = _ro_conn(ev_db)
    try:
        row = conn.execute(
            "SELECT evidence_set_version FROM evidence_sets "
            "WHERE company_id=? AND document_id=? AND document_version=? "
            "AND status='current' LIMIT 1",
            (company_id, document_id, document_version),
        ).fetchone()
        return row["evidence_set_version"] if row is not None else None
    finally:
        conn.close()


def _ro_snapshot_get(fin_db: str | Path, snapshot_id: str):
    from financial_v2 import store as fstore
    conn = _ro_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT * FROM financial_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        return fstore._row_to_snapshot(row) if row is not None else None
    finally:
        conn.close()


def _ro_snapshot_items(fin_db: str | Path, snapshot_id: str) -> list:
    from financial_v2 import store as fstore
    conn = _ro_conn(fin_db)
    try:
        rows = conn.execute(
            "SELECT * FROM snapshot_item WHERE snapshot_id=? ORDER BY comparison_key",
            (snapshot_id,),
        ).fetchall()
        return [fstore._row_to_snapshot_item(r) for r in rows]
    finally:
        conn.close()


def _ro_metric_get(fin_db: str | Path, snapshot_id: str, formula_id: str,
                   formula_version: str, period: str):
    from financial_v2 import store as fstore
    conn = _ro_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT * FROM metric_result WHERE snapshot_id=? AND formula_id=? "
            "AND formula_version=? AND period=?",
            (snapshot_id, formula_id, formula_version, period),
        ).fetchone()
        return fstore._row_to_metric_result(row) if row is not None else None
    finally:
        conn.close()


def _ro_formula_get(fin_db: str | Path, formula_id: str, formula_version: str):
    from financial_v2 import store as fstore
    conn = _ro_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT * FROM formula_definition WHERE formula_id=? AND formula_version=?",
            (formula_id, formula_version),
        ).fetchone()
        return fstore._row_to_formula_definition(row) if row is not None else None
    finally:
        conn.close()


def _ro_is_quarantined(fin_db: str | Path, object_type: str, object_id: str) -> bool:
    conn = _ro_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM quarantine WHERE object_type=? AND object_id=? LIMIT 1",
            (object_type, object_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _ro_latest_snapshot_validity(fin_db: str | Path, snapshot_id: str) -> str | None:
    conn = _ro_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT status FROM snapshot_validity WHERE snapshot_id=? "
            "ORDER BY event_at DESC, rowid DESC LIMIT 1",
            (snapshot_id,),
        ).fetchone()
        return row["status"] if row is not None else None
    finally:
        conn.close()


def _ro_snapshot_authority(fin_db: str | Path, snapshot_id: str, current_snapshot_id: str | None):
    """结构化快照权威性复合判定（只读，返回 harness SnapshotAuthority）。"""
    from harness import structured_provenance as SP
    snap = _ro_snapshot_get(fin_db, snapshot_id)
    exists = snap is not None
    validity = _ro_latest_snapshot_validity(fin_db, snapshot_id) if exists else None
    return SP.SnapshotAuthority(
        exists=exists,
        is_current=(snapshot_id == current_snapshot_id),
        validity=validity,
        report_blocked=(snap.report_blocked if snap is not None else False),
        quarantined=(_ro_is_quarantined(fin_db, "financial_snapshot", snapshot_id)
                     if exists else False),
    )


def _ro_external_get(ext_db: str | Path, source_snapshot_id: str):
    from external_v2 import store as extstore
    conn = _ro_conn(ext_db)
    try:
        row = conn.execute(
            "SELECT * FROM source_snapshots WHERE source_snapshot_id=?",
            (source_snapshot_id,),
        ).fetchone()
        return extstore._row_to_snapshot(row) if row is not None else None
    finally:
        conn.close()


def build_citation_authority(
    company_id: str,
    context: RS.RouteContext | None,
    *,
    ev_db: str | Path | None = None,
    fin_db: str | Path | None = None,
    ext_db: str | Path | None = None,
) -> CitationAuthority:
    """按既有库路径构建只读权威校验器。

    严格只读：所有 lookup 走 ``_ro_conn``（缺文件 fail-closed），不调用 init_db、
    不创建、不迁移、不改 store 模块级 ``_db_path``。某个引用类型对应的库未提供时，
    该类型 lookup 为 None → 校验 fail-closed 返回 ``authority_unavailable``；库已提供
    但缺失/损坏 → 查询抛错，由上层转为 ``authority_query_failed``。
    """
    current_snapshot_id = context.snapshot_id if context is not None else None

    return CitationAuthority(
        company_id=company_id, context=context,
        evidence_get=(lambda eid: _ro_evidence_get(ev_db, eid)) if ev_db is not None else None,
        current_document_version=(
            (lambda cid, did: _ro_current_document_version(ev_db, cid, did))
            if ev_db is not None else None),
        current_evidence_set=(
            (lambda cid, did, dv: _ro_current_evidence_set(ev_db, cid, did, dv))
            if ev_db is not None else None),
        snapshot_authority=(
            (lambda sid: _ro_snapshot_authority(fin_db, sid, current_snapshot_id))
            if fin_db is not None else None),
        snapshot_get=(lambda sid: _ro_snapshot_get(fin_db, sid)) if fin_db is not None else None,
        metric_get=(
            (lambda sid, fid, fv, period: _ro_metric_get(fin_db, sid, fid, fv, period))
            if fin_db is not None else None),
        item_list=(lambda sid: _ro_snapshot_items(fin_db, sid)) if fin_db is not None else None,
        formula_get=(
            (lambda fid, fv: _ro_formula_get(fin_db, fid, fv))
            if fin_db is not None else None),
        external_get=(lambda sid: _ro_external_get(ext_db, sid)) if ext_db is not None else None,
    )


# ---------------------------------------------------------------------------
# CLI 自检（纯函数，注入假对象，不读库）
# ---------------------------------------------------------------------------

def _self_check() -> dict:
    # 用假对象证明三类判定路径与 fail-closed 口径。
    from dataclasses import dataclass as _dc

    @_dc
    class _Ev:
        company_id: str
        document_id: str = "doc"
        document_version: str = "dv1"
        evidence_set_version: str = "sv1"
        page_number: int = 3
        text: str = "正文"

    @_dc
    class _Snap:
        company_id: str = "C"
        scope: str = "consolidated"
        currency: str = "CNY"
        purpose: str = "credit_analysis"

    @_dc
    class _Item:
        standard_item_code: str = "TOTAL_ASSETS"
        report_period: str = "2025-12-31"

    @_dc
    class _Auth:
        exists: bool = True
        is_current: bool = True
        validity: str = "valid"
        report_blocked: bool = False
        quarantined: bool = False

    @_dc
    class _Ext:
        company_id: str = ""
        status: str = "SNAPSHOTTED"
        content_text: str = "全文正文，非 URL 非 snippet"
        content_hash: str = XS.content_hash("全文正文，非 URL 非 snippet")
        canonical_url: str = "https://stats.gov.cn/x"
        original_url: str = "https://stats.gov.cn/x"
        snippet: str = "摘要"
        published_at: str = "2025-06-01"

    ev_ok = validate_evidence_ref(
        HS.CitationRef(ref_type="evidence", evidence_id="e1", page_number=3),
        company_id="C", evidence=_Ev("C"))
    ev_page = validate_evidence_ref(
        HS.CitationRef(ref_type="evidence", evidence_id="e1", page_number=9),
        company_id="C", evidence=_Ev("C"))
    ev_company = validate_evidence_ref(
        HS.CitationRef(ref_type="evidence", evidence_id="e1"),
        company_id="X", evidence=_Ev("C"))

    def _auth(sid):
        return _Auth()
    str_ok = validate_structured_ref(
        HS.CitationRef(ref_type="structured", snapshot_id="S1", item_code="TOTAL_ASSETS",
                       period="2025-12-31"),
        company_id="C", context=RS.RouteContext(
            company_id="C", report_as_of="2025-12-31", available_document_ids=[],
            available_source_types=[], supported_db_fields=[], supported_metric_ids=[],
            available_db_fields=[], available_metric_ids=[], external_research_enabled=True),
        snapshot_authority=_auth, snapshot_get=lambda sid: _Snap(),
        metric_get=None, item_list=lambda sid: [_Item()], formula_get=None)
    str_missing_item = validate_structured_ref(
        HS.CitationRef(ref_type="structured", snapshot_id="S1", item_code="REVENUE",
                       period="2025-12-31"),
        company_id="C", context=RS.RouteContext(
            company_id="C", report_as_of="2025-12-31", available_document_ids=[],
            available_source_types=[], supported_db_fields=[], supported_metric_ids=[],
            available_db_fields=[], available_metric_ids=[], external_research_enabled=True),
        snapshot_authority=_auth, snapshot_get=lambda sid: _Snap(),
        metric_get=None, item_list=lambda sid: [_Item()], formula_get=None)

    ext_ok, ext_warn = validate_external_ref(
        HS.CitationRef(ref_type="external", source_snapshot_id="x1"),
        company_id="C", snapshot=_Ext(), content_hash_fn=XS.content_hash)
    ext_unknown, ext_warn2 = validate_external_ref(
        HS.CitationRef(ref_type="external", source_snapshot_id="x1"),
        company_id="C", snapshot=_Ext(published_at=None), content_hash_fn=XS.content_hash)
    ext_empty, _ = validate_external_ref(
        HS.CitationRef(ref_type="external", source_snapshot_id="x1"),
        company_id="C", snapshot=_Ext(content_text="  "), content_hash_fn=XS.content_hash)

    # 只读连接证明：临时库可读；缺库 fail-closed 且不创建（不 init_db、不迁移）。
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="ca_ro_")
    ro_path = Path(tmpdir) / "ro.db"
    ro_w = sqlite3.connect(str(ro_path))
    ro_w.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v TEXT)")
    ro_w.execute("INSERT INTO t VALUES ('x', 'y')")
    ro_w.commit()
    ro_w.close()
    ro_conn = _ro_conn(ro_path)
    ro_val = ro_conn.execute("SELECT v FROM t WHERE id='x'").fetchone()["v"]
    ro_conn.close()
    missing_path = Path(tmpdir) / "nope.db"
    missing_raised = False
    try:
        _ro_conn(missing_path)
    except FileNotFoundError:
        missing_raised = True
    missing_created = missing_path.exists()

    return {
        "evidence_ok": ev_ok is None,
        "evidence_page_mismatch": ev_page,
        "evidence_company_mismatch": ev_company,
        "structured_ok": str_ok is None,
        "structured_item_not_found": str_missing_item,
        "external_ok": ext_ok is None,
        "external_unknown_date_warning": list(ext_warn2),
        "external_unknown_date_hard": ext_unknown,
        "external_empty": ext_empty,
        "readonly_conn_ok": ro_val == "y",
        "missing_db_fail_closed": missing_raised,
        "missing_db_not_created": not missing_created,
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m sections.citation_authority",
        description="正式引用权威性校验自检（纯函数，不读库）")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps(_self_check(), ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main())
