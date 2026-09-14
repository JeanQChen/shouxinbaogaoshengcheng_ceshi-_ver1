"""R2：bounded Evidence inspection ToolSpec/adapter + 内部 ReadonlyEvidenceReader。

只读读取链（R2_IMPLEMENTATION_PLAN §3/§5）：所有扩读读取必须经现有 ToolRegistry 正式链，
``ContextExpansion → ToolRegistry.execute → inspect_evidence_bounded ToolSpec/adapter →
ReadonlyEvidenceReader → evidence.db (mode=ro + query_only)``。

硬约束：
- ``ReadonlyEvidenceReader`` 是 adapter 内部实现，扩读算法与材料验收 runner 不得绕过
  ToolRegistry 直接调用它；
- 绝不初始化 / 建库 / 迁移 / 写 ``evidence.db``，绝不修改 ``evidence.store._db_path``；
- 绝不把 ``list_document_evidence`` 当扩读实现（一次性整篇加载反模式），本模块只做有界
  SELECT（LIMIT + 邻接/方向），每次读取返回有界块集合；
- 版本 / current / seed locator / content_hash 任一 mismatch → fail-closed（写 trace/unread，
  绝不把 stale 材料静默降级后继续作正式输入）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness._readonly_sqlite import open_readonly_conn
from tools import contracts as C

DEFAULT_EVIDENCE_DB_PATH = Path("data/evidence.db")

# 有界读取方向（mode）。每个 mode 是一次真实 ToolCall，返回有界块集合。
INSPECT_MODES = (
    "verify_seed",         # 复验 seed：evidence_id + 绑定字段 + content_hash 精确一致
    "adjacent_before",     # seed 之前同 section 相邻块（受控回读）
    "adjacent_after",      # seed 之后同 section 相邻块（受控正读）
    "table_continuation",  # seed 之后同表的续表块（table/table_row）
    "explicit_reference",  # 显式交叉引用目标块（按 evidence_id / reference_target）
)

# 扩读工具名（context_expansion 与 runner 统一引用，不重复造字符串）。
TOOL_NAME = "inspect_evidence_bounded"


# ---------------------------------------------------------------------------
# 有界读取结果（单个 EvidenceBlock 的只读投影）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceReadResult:
    """一次有界读取得到的单个 EvidenceBlock 只读投影（R2 读取侧 section_path 统一 tuple）。"""

    evidence_id: str
    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    source_name: str
    source_type: str
    page_number: int
    block_index: int
    section_path: tuple[str, ...]
    evidence_type: str
    text: str
    structured_payload: dict | None
    content_hash: str
    report_period: str | None = None
    published_at: str | None = None
    source_uri: str | None = None

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "company_id": self.company_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "section_path": list(self.section_path),
            "evidence_type": self.evidence_type,
            "text": self.text,
            "structured_payload": self.structured_payload,
            "content_hash": self.content_hash,
            "report_period": self.report_period,
            "published_at": self.published_at,
            "source_uri": self.source_uri,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceReadResult":
        return cls(
            evidence_id=d["evidence_id"],
            company_id=d["company_id"],
            document_id=d["document_id"],
            document_version=d["document_version"],
            evidence_set_version=d["evidence_set_version"],
            source_name=d.get("source_name", ""),
            source_type=d.get("source_type", ""),
            page_number=d["page_number"],
            block_index=d["block_index"],
            section_path=tuple(d.get("section_path") or ()),
            evidence_type=d["evidence_type"],
            text=d["text"],
            structured_payload=d.get("structured_payload"),
            content_hash=d["content_hash"],
            report_period=d.get("report_period"),
            published_at=d.get("published_at"),
            source_uri=d.get("source_uri"),
        )


def _json_loads(raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _row_to_result(row: Any) -> EvidenceReadResult:
    return EvidenceReadResult(
        evidence_id=row["evidence_id"],
        company_id=row["company_id"],
        document_id=row["document_id"],
        document_version=row["document_version"],
        evidence_set_version=row["evidence_set_version"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        page_number=row["page_number"],
        block_index=row["block_index"],
        section_path=tuple(_json_loads(row["section_path"]) or ()),
        evidence_type=row["evidence_type"],
        text=row["text"],
        structured_payload=_json_loads(row["structured_payload"]),
        content_hash=row["content_hash"],
        report_period=row["report_period"],
        published_at=row["published_at"],
        source_uri=row["source_uri"],
    )


# ---------------------------------------------------------------------------
# 内部只读 reader（adapter 专用；不对外公开为扩读 Protocol）
# ---------------------------------------------------------------------------

class ReadonlyEvidenceReader:
    """对 ``evidence.db`` 的严格只读、有界读取（mode=ro + query_only，绝不建库/写库）。"""

    def __init__(self, db_path: str | Path = DEFAULT_EVIDENCE_DB_PATH):
        self._db_path = Path(db_path)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self):
        return open_readonly_conn(self._db_path)

    def current_document_version(self, company_id: str, document_id: str) -> str | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT document_version FROM documents "
                "WHERE company_id=? AND document_id=? AND status='current'",
                (company_id, document_id),
            ).fetchone()
            return row["document_version"] if row else None
        finally:
            conn.close()

    def current_evidence_set(self, company_id: str, document_id: str,
                             document_version: str) -> str | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT evidence_set_version FROM evidence_sets "
                "WHERE company_id=? AND document_id=? AND document_version=? AND status='current'",
                (company_id, document_id, document_version),
            ).fetchone()
            return row["evidence_set_version"] if row else None
        finally:
            conn.close()

    def get_block(self, evidence_id: str) -> EvidenceReadResult | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM evidence_blocks WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            return _row_to_result(row) if row else None
        finally:
            conn.close()

    def get_block_at(self, company_id: str, document_id: str, document_version: str,
                     evidence_set_version: str, page_number: int,
                     block_index: int) -> EvidenceReadResult | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM evidence_blocks WHERE company_id=? AND document_id=? "
                "AND document_version=? AND evidence_set_version=? "
                "AND page_number=? AND block_index=?",
                (company_id, document_id, document_version, evidence_set_version,
                 page_number, block_index),
            ).fetchone()
            return _row_to_result(row) if row else None
        finally:
            conn.close()

    def bounded_blocks(self, company_id: str, document_id: str, document_version: str,
                       evidence_set_version: str, *, before: tuple[int, int] | None = None,
                       after: tuple[int, int] | None = None,
                       section_path: tuple[str, ...] | None = None,
                       evidence_types: tuple[str, ...] | None = None,
                       limit: int) -> list[EvidenceReadResult]:
        """有界读取：seed 之前/之后的块，可按 section_path / evidence_type 过滤，LIMIT 有界。

        before / after 为 (page_number, block_index) 锚点；before 表示返回锚点之前按序（倒序）
        的块，after 表示锚点之后按序（正序）的块。绝不整篇加载。
        """
        conn = self._connect()
        if conn is None:
            return []
        try:
            where = ("company_id=? AND document_id=? AND document_version=? "
                     "AND evidence_set_version=?")
            params: list[Any] = [company_id, document_id, document_version, evidence_set_version]

            order = ""
            if after is not None:
                page, blk = after
                where += (" AND (page_number > ? OR (page_number = ? AND block_index > ?))")
                params += [page, page, blk]
                order = "page_number ASC, block_index ASC"
            elif before is not None:
                page, blk = before
                where += (" AND (page_number < ? OR (page_number = ? AND block_index < ?))")
                params += [page, page, blk]
                order = "page_number DESC, block_index DESC"
            else:
                order = "page_number ASC, block_index ASC"

            if section_path is not None:
                sp = json.dumps(list(section_path), ensure_ascii=False, separators=(",", ":"))
                where += " AND section_path=?"
                params.append(sp)
            if evidence_types is not None:
                placeholders = ",".join("?" for _ in evidence_types)
                where += f" AND evidence_type IN ({placeholders})"
                params.extend(evidence_types)

            rows = conn.execute(
                f"SELECT * FROM evidence_blocks WHERE {where} ORDER BY {order} LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
            return [_row_to_result(r) for r in rows]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# ToolSpec + executor（经现有 ToolRegistry 注册）
# ---------------------------------------------------------------------------

INSPECT_EVIDENCE_BOUNDED_SPEC = C.ToolSpec(
    name=TOOL_NAME,
    version="v1",
    description=(
        "有界只读扩读 Evidence 块（相邻/续表/交叉引用），经只读 reader 读取，"
        "绝不整篇加载、绝不建库/写库"),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["company_id", "document_id", "document_version",
                     "evidence_set_version", "mode"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "document_id": {"type": "string", "minLength": 1},
            "document_version": {"type": "string", "minLength": 1},
            "evidence_set_version": {"type": "string", "minLength": 1},
            "mode": {"type": "string", "enum": list(INSPECT_MODES)},
            "page_number": {"type": "integer", "minimum": 1},
            "block_index": {"type": "integer", "minimum": 0},
            "section_path": {"type": "array",
                             "items": {"type": "string"}},
            "evidence_id": {"type": "string"},
            "content_hash": {"type": "string"},
            "reference_target": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=20, timeout_ms=3000, retry_policy="none", cost_class="local",
)


class BoundedEvidenceInspectionAdapter:
    """ToolRegistry 的 bounded Evidence inspection adapter（内部含只读 reader）。

    所有读取走 :meth:`ReadonlyEvidenceReader`，绝不初始化 / 建库 / 迁移 / 写 evidence.db。
    """

    def __init__(self, db_path: str | Path = DEFAULT_EVIDENCE_DB_PATH):
        self._reader = ReadonlyEvidenceReader(db_path)

    @property
    def reader(self) -> ReadonlyEvidenceReader:
        return self._reader

    def execute(self, arguments: dict) -> C.ToolResult:
        return _bounded_inspect_executor(self._reader, arguments)


def _empty_result(mode: str, message: str) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=TOOL_NAME, tool_version="", status="EMPTY",
        data={}, error_code="RETRIEVAL_EMPTY", message=message,
        retryable=False, trace_id=uuid.uuid4().hex)


def _fail_result(mode: str, message: str) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=TOOL_NAME, tool_version="", status="FATAL_ERROR",
        data={}, error_code="INTERNAL_ERROR", message=message,
        retryable=False, trace_id=uuid.uuid4().hex)


def _bounded_inspect_executor(reader: ReadonlyEvidenceReader, args: dict) -> C.ToolResult:
    company_id = args["company_id"]
    document_id = args["document_id"]
    document_version = args["document_version"]
    evidence_set_version = args["evidence_set_version"]
    mode = args["mode"]
    limit = args.get("limit", 1)

    if mode == "verify_seed":
        evidence_id = args.get("evidence_id", "")
        if not evidence_id:
            return _fail_result(mode, "verify_seed 缺 evidence_id")
        block = reader.get_block(evidence_id)
        if block is None:
            return _empty_result(mode, f"seed 不存在: {evidence_id}")
        # 绑定字段 + locator + content_hash 精确一致，否则 fail-closed。
        mismatches: list[str] = []
        if block.company_id != company_id:
            mismatches.append("company_id")
        if block.document_id != document_id:
            mismatches.append("document_id")
        if block.document_version != document_version:
            mismatches.append("document_version")
        if block.evidence_set_version != evidence_set_version:
            mismatches.append("evidence_set_version")
        if "page_number" in args and block.page_number != args["page_number"]:
            mismatches.append("page_number")
        if "block_index" in args and block.block_index != args["block_index"]:
            mismatches.append("block_index")
        if "section_path" in args and tuple(args["section_path"]) != block.section_path:
            mismatches.append("section_path")
        if "content_hash" in args and args["content_hash"] and \
                args["content_hash"] != block.content_hash:
            mismatches.append("content_hash")
        if mismatches:
            return _fail_result(mode, f"seed mismatch: {', '.join(mismatches)}")
        cur_doc = reader.current_document_version(company_id, document_id)
        cur_set = reader.current_evidence_set(company_id, document_id, document_version)
        return C.ToolResult(
            call_id="", tool_name=TOOL_NAME, tool_version="", status="SUCCESS",
            data={
                "blocks": [block.to_dict()],
                "is_current_document": cur_doc == document_version,
                "is_current_set": cur_set == evidence_set_version,
            },
            evidence_ids=[block.evidence_id], error_code=None, message=None,
            retryable=False, trace_id=uuid.uuid4().hex)

    anchor = (args.get("page_number", 1), args.get("block_index", 0))
    section_path = tuple(args.get("section_path") or ()) or None

    if mode == "adjacent_before":
        blocks = reader.bounded_blocks(
            company_id, document_id, document_version, evidence_set_version,
            before=anchor, section_path=section_path, limit=limit)
    elif mode == "adjacent_after":
        blocks = reader.bounded_blocks(
            company_id, document_id, document_version, evidence_set_version,
            after=anchor, section_path=section_path, limit=limit)
    elif mode == "table_continuation":
        blocks = reader.bounded_blocks(
            company_id, document_id, document_version, evidence_set_version,
            after=anchor, section_path=section_path,
            evidence_types=("table", "table_row"), limit=limit)
    elif mode == "explicit_reference":
        evidence_id = args.get("evidence_id", "")
        if evidence_id:
            block = reader.get_block(evidence_id)
            blocks = [block] if block is not None else []
        else:
            # 无 evidence_id 时退回有界正读（reference_target 只作人读说明，不参与过滤）。
            blocks = reader.bounded_blocks(
                company_id, document_id, document_version, evidence_set_version,
                after=anchor, section_path=section_path, limit=limit)
    else:
        return _fail_result(mode, f"未知 mode: {mode}")

    if not blocks:
        return _empty_result(mode, f"无有界读取结果（mode={mode}）")
    return C.ToolResult(
        call_id="", tool_name=TOOL_NAME, tool_version="", status="SUCCESS",
        data={"blocks": [b.to_dict() for b in blocks]},
        evidence_ids=[b.evidence_id for b in blocks], error_code=None, message=None,
        retryable=False, trace_id=uuid.uuid4().hex)


def register_bounded_evidence_tool(registry, adapter: BoundedEvidenceInspectionAdapter | None = None,
                                   db_path: str | Path = DEFAULT_EVIDENCE_DB_PATH) -> None:
    """把 bounded Evidence inspection ToolSpec/adapter 注册到现有 ToolRegistry（不新建循环）。"""
    adapter = adapter or BoundedEvidenceInspectionAdapter(db_path)
    registry.register(INSPECT_EVIDENCE_BOUNDED_SPEC, adapter.execute)
