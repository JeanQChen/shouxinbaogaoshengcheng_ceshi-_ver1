"""只读取证（**修复前**基线）：三个 P1 在旧实现下的真实失败现场，逐条留证。

P1-1：同一 marker 被拆成两条请求（「如下表」+ 其子串「下表」）；请求不携带 occurrence
      身份；reader 固定取 ``occurrences[0]`` → 第二个 marker 永远解析到第一个。
P1-2：``reference_target_table_objects`` 把折行散文/治理文字当作表对象候选。
P1-3：``table_object_id`` 不绑定表体内容 → 表体数据不同但行数相同即撞 ID。

零写入：临时 evidence.db（只读）+ 内存 fixture，不触碰 evaluation/results。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import ids as evidence_ids  # noqa: E402
from harness.context_expansion import _detect_reference_targets  # noqa: E402
from harness.evidence_reader import (  # noqa: E402
    BoundedEvidenceInspectionAdapter,
    iter_reference_marker_occurrences,
)
from harness.table_structure import (  # noqa: E402
    reference_target_table_objects,
    table_object_id,
)

_COMPANY, _DOC, _DOCV, _SETV = "100001", "doc_p", "v1", "set1"

# 同块内**两个**互不重叠的「如下表」，各自后随自己的表。
_TWO_MARKERS = (
    "参股企业情况如下表：\n"
    "\n"
    "表5-5参股企业情况\n"
    "  序号  企业名称  持股比例\n"
    "  1  A公司  24.9%\n"
    "\n"
    "各板块构成如下表：\n"
    "\n"
    "表5-6板块构成表\n"
    "  序号  板块  金额\n"
    "  1  电池  1000\n"
)
# 折行散文（真实语料里被误判为表对象的形状）：标题形态行 + 空行 + 含多空格的折行。
_PROSE_WRAP = (
    "（一）公司治理结构\n"
    "公司严格按照《公司法》《证券法》《上市公司治理准则》《深圳证券交易所创业\n"
    "\n"
    "板股票上市规则》《深圳证券交易所上市公司自律监管指引第  2号——创业板上市公司\n"
    "规范运作》等法律、法规及规范性文件的要求，不断完善公司法人治理机构。\n"
)


def _make_db(dirpath: Path, blocks) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    db = dirpath / "evidence.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE documents (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, source_name TEXT NOT NULL,
            source_path TEXT, source_type TEXT NOT NULL, material_group TEXT NOT NULL,
            file_sha256 TEXT NOT NULL, file_size INTEGER NOT NULL, page_count INTEGER,
            declared_company_name TEXT, detected_company_names TEXT,
            parser_version TEXT NOT NULL, status TEXT NOT NULL, quality_flags TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version));
        CREATE TABLE evidence_sets (
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            dependency_versions TEXT NOT NULL, status TEXT NOT NULL,
            block_count INTEGER, created_at TEXT NOT NULL,
            PRIMARY KEY (company_id, document_id, document_version, evidence_set_version));
        CREATE TABLE evidence_blocks (
            evidence_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
            company_id TEXT NOT NULL, document_id TEXT NOT NULL,
            document_version TEXT NOT NULL, evidence_set_version TEXT NOT NULL,
            source_name TEXT NOT NULL, source_type TEXT NOT NULL, source_uri TEXT,
            page_number INTEGER NOT NULL, block_index INTEGER NOT NULL,
            section_path TEXT, evidence_type TEXT NOT NULL, text TEXT NOT NULL,
            structured_payload TEXT, report_period TEXT, published_at TEXT,
            entities TEXT, quality_flags TEXT, content_hash TEXT NOT NULL,
            builder_version TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE (company_id, document_id, document_version, evidence_set_version,
                    page_number, block_index));
        """
    )
    conn.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, "年报", None, "annual_report", "annual", "f" * 64,
         100, 20, "示例公司", None, "p1", "current", None, "2026-01-01"))
    conn.execute(
        "INSERT INTO evidence_sets VALUES (?,?,?,?,?,?,?,?)",
        (_COMPANY, _DOC, _DOCV, _SETV, json.dumps({}), "current", len(blocks),
         "2026-01-01"))
    for (page, blk, sp, etype, text) in blocks:
        ch = evidence_ids.content_hash(text, None)
        eid = evidence_ids.make_evidence_id(_COMPANY, _DOC, _DOCV, _SETV, page, blk, ch)
        conn.execute(
            "INSERT INTO evidence_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "1", _COMPANY, _DOC, _DOCV, _SETV, "年报", "annual_report", None,
             page, blk, json.dumps(list(sp), ensure_ascii=False), etype, text, None,
             "2025-12-31", "2026-04-01", None, None, ch, "b1", "2026-01-01"))
    conn.commit()
    conn.close()
    return db


def _read(adapter, page, blk, **extra):
    args = {"company_id": _COMPANY, "document_id": _DOC, "document_version": _DOCV,
            "evidence_set_version": _SETV, "mode": "explicit_reference",
            "page_number": page, "block_index": blk, "limit": 5}
    args.update(extra)
    return adapter.execute(args)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import inspect
    post_fix = "body_row_texts" in inspect.signature(table_object_id).parameters
    print(f"=== 版本探测：当前实现 = {'修复后（v13）' if post_fix else '修复前（v12）'} ===")
    print("  说明：本脚本记录三个 P1 的**修复前现场**；若在修复后执行，各节会显式标注"
          "「修复后行为」，避免把修复后结果误读为修复前证据。")
    print("=== P1-1(a) 同一 marker 是否被拆成两条请求（子串重复） ===")
    _targets = _detect_reference_targets(
        "截至 2025年12月末，发行人主要参股及联营、合营企业情况如下表：")
    print(f"  实得 {json.dumps(_targets, ensure_ascii=False)} -> "
          f"{'修复后：1 个，无「下表」子串重复' if len(_targets) == 1 else '修复前：2 个（「如下表」+「下表」）'}")
    print("=== P1-1(b) 两个非重叠 marker：请求是否携带 occurrence 身份 ===")
    print(json.dumps(iter_reference_marker_occurrences(_TWO_MARKERS), ensure_ascii=False))
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(Path(td) / "d", [(1, 0, ["参股企业"], "paragraph", _TWO_MARKERS)])
        ad = BoundedEvidenceInspectionAdapter(db)
        for label in ("target=如下表（首个）", "target=如下表（第二个，同一字符串）"):
            res = _read(ad, 1, 0, reference_target="如下表")
            b = (res.data or {}).get("reference_binding") or {}
            print(f"  {label} -> scope={b.get('resolution_scope')} "
                  f"marker={b.get('reference_marker')} start={b.get('marker_start')} "
                  f"title={b.get('target_table_title')!r}")
        print("  结论：修复后裸 `reference_target=\"如下表\"`（不带 occurrence 身份）一律 fail-closed；"
              "修复前则两处都绑到第一个表，无法按 occurrence 区分。")
    print("=== P1-2 折行散文是否被当作同块表对象候选 ===")
    objs = reference_target_table_objects(_PROSE_WRAP, 0)
    for o in objs:
        print(f"  title={o['target_table_title']!r} body_rows={o['target_body_rows']} "
              f"structure_rows={o['target_structure_rows']} "
              f"header={o['target_header_rows']!r}")
    print(f"  候选数={len(objs)} -> "
          f"{'修复后：散文不再被判为表对象' if not objs else '修复前：散文被误判为表对象候选'}")
    print("=== P1-3 表体内容不入 object_id：数据不同、行数相同即撞 ID ===")
    common = dict(title="表5-5参股企业情况", unit="", header_rows=("序号  企业名称  持股比例",),
                  body_rows=1, structure_rows=2)
    print("  修复前记录：旧 payload 只含 title/unit/header/行数，故上面这组参数两次算出同一个 "
          "object_id；表体企业名/金额/比例**不入**身份，表体篡改对 ID 无影响"
          "（旧实现无表体字段可篡）。")
    try:
        table_object_id(**common)
    except TypeError as exc:
        print(f"  现状：旧签名已不存在 -> TypeError: {exc}")
        print("  （这正是 P1-3 已修复的痕迹：object_id 现在必须由真实表体行内容派生；"
              "本脚本是**修复前**的留证脚本，不再代表当前行为。）")
    else:
        print("  警告：旧签名仍然可用（本脚本的「修复前」前提不成立）")


if __name__ == "__main__":
    main()
