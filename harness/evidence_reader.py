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
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence import ids
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

# seed 身份解析工具名（expand 第 0 步的正式工具；与 inspect_evidence_bounded 的
# verify_seed 模式同语义但独立 ToolSpec，trace 的 resolve_seed action 必须能映射到真实工具名）。
RESOLVE_SEED_IDENTITY_TOOL_NAME = "resolve_seed_identity"


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


# 结构性表引用标记（explicit_reference 可离线确定性解析为「seed 之后下一个 table」）。
_TABLE_REFERENCE_MARKERS = ("见下表", "如下表", "下表", "续表", "接上表")


def _is_table_reference(reference_target: str | None) -> bool:
    if not reference_target:
        return False
    return any(m in reference_target for m in _TABLE_REFERENCE_MARKERS)


# 命名跨章节引用编号：第24节 / 24、 / （24） / 24.
_SECTION_REFERENCE_NUMBERING = re.compile(
    r"(?:第\s*([一二三四五六七八九十百千\d]+)\s*[章节条款])"
    r"|(?:[（(]?\s*([一二三四五六七八九十百千\d]+)\s*[、.．）)])")


def _parse_section_reference(reference_target: str | None) -> tuple[str | None, str | None]:
    """解析命名跨章节引用（「详见 24、所有权或使用权受到限制的资产」）→ (编号, 标题)。

    去掉引用标记词（详见/参见/见/如）后，取首个编号（第N节 / N、 / （N）），编号后剩余文字
    即标题。两者均无法提取 → (None, None)。通用解析，无公司/页码硬编码。
    """
    s = unicodedata.normalize("NFC", (reference_target or "").strip())
    if not s:
        return None, None
    for kw in ("详见", "参见", "见", "如"):
        s = re.sub(rf"^\s*{kw}\s*", "", s)
    # 取**最后一个**编号（叶子编号）：嵌套引用「第十节 …之 七、… 24、标题」→ (24, 标题)，
    # 而非首个「第十节」。
    m = None
    for cand in _SECTION_REFERENCE_NUMBERING.finditer(s):
        m = cand
    if not m:
        return None, (s or None)
    num = m.group(1) or m.group(2)
    title = s[m.end():].strip("、，。 ：:（）()\t\r\n")
    # PDF 折行会把标题拆成「受到\n限制」；归一化内部空白以匹配单行 section_path。
    title = re.sub(r"\s+", "", title)
    return (num or None), (title or None)


def recompute_evidence_identity(block: EvidenceReadResult) -> tuple[str, str]:
    """按 formal 身份算法（evidence.ids）重算 (evidence_id, content_hash)。

    不信任读取侧/存储侧自报值；来源身份必须由正文 + structured_payload 确定性重建。
    """
    ch = ids.content_hash(block.text, block.structured_payload)
    eid = ids.make_evidence_id(
        block.company_id, block.document_id, block.document_version,
        block.evidence_set_version, block.page_number, block.block_index, ch)
    return eid, ch


def table_title_of(block: EvidenceReadResult) -> str | None:
    """表身份：优先 structured_payload.table_title，其次 table 块正文（续表/表体共用该表身份）。"""
    sp = block.structured_payload
    if isinstance(sp, dict):
        t = sp.get("table_title")
        if t:
            return str(t)
    if block.evidence_type == "table" and block.text.strip():
        return block.text.strip()
    return None


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

    def find_by_section_reference(self, company_id: str, document_id: str,
                                  document_version: str, evidence_set_version: str,
                                  reference_target: str) -> list[EvidenceReadResult]:
        """通用同文档命名跨章节引用解析（修复 D）。

        解析「详见 N、标题」→ (编号, 标题)，在同 document_id/document_version/evidence_set 内
        按**确定性锚定**匹配：编号匹配块首文本「N、/（N）」或 section_path 含「N、」；
        标题只匹配 section_path 含标题（绝不在正文任意位置 LIKE，避免把**引用发起块**（seed
        正文含「详见…」字样）误当目标）。返回所有匹配（有界 LIMIT 20）；调用方据此
        fail-closed：0 匹配 → dangling，>1 匹配 → 歧义（不任意选）。零整篇加载、零建库。
        """
        num, title = _parse_section_reference(reference_target)
        if not num and not title:
            return []
        conn = self._connect()
        if conn is None:
            return []
        try:
            where = ("company_id=? AND document_id=? AND document_version=? "
                     "AND evidence_set_version=?")
            params: list[Any] = [company_id, document_id, document_version,
                                 evidence_set_version]
            conds: list[str] = []
            if num:
                conds.append("(text LIKE ? OR text LIKE ? OR section_path LIKE ?)")
                params += [f"{num}、%", f"（{num}）%", f"%{num}、%"]
            if title:
                conds.append("(section_path LIKE ?)")
                params.append(f"%{title}%")
            if not conds:
                return []
            rows = conn.execute(
                f"SELECT * FROM evidence_blocks WHERE {where} AND ({' OR '.join(conds)}) "
                "ORDER BY page_number ASC, block_index ASC LIMIT 20",
                params,
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
            "table_title": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=20, timeout_ms=3000, retry_policy="none", cost_class="local",
)


RESOLVE_SEED_IDENTITY_SPEC = C.ToolSpec(
    name=RESOLVE_SEED_IDENTITY_TOOL_NAME,
    version="v1",
    description=(
        "复验并解析 seed 完整身份：最小可信输入 company_id+evidence_id，可选绑定字段"
        "（document_id/document_version/evidence_set_version/page/block/section_path/"
        "content_hash）提供则精确断言；evidence_id/content_hash 经 evidence.ids 重算一致 + "
        "current document/set 校验；任一不符 fail-closed。是 expand 第 0 步的正式工具，"
        "与 inspect_evidence_bounded 的 verify_seed 模式同语义但独立 ToolSpec"),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["company_id", "evidence_id"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "document_id": {"type": "string", "minLength": 1},
            "document_version": {"type": "string", "minLength": 1},
            "evidence_set_version": {"type": "string", "minLength": 1},
            "evidence_id": {"type": "string", "minLength": 1},
            "page_number": {"type": "integer", "minimum": 1},
            "block_index": {"type": "integer", "minimum": 0},
            "section_path": {"type": "array",
                             "items": {"type": "string"}},
            "content_hash": {"type": "string"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=1, timeout_ms=3000, retry_policy="none", cost_class="local",
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

    def execute_resolve_seed_identity(self, arguments: dict) -> C.ToolResult:
        return _resolve_seed_identity_executor(self._reader, arguments)


def _empty_result(mode: str, message: str, *, tool_name: str = TOOL_NAME) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=tool_name, tool_version="", status="EMPTY",
        data={}, error_code="RETRIEVAL_EMPTY", message=message,
        retryable=False, trace_id=uuid.uuid4().hex)


def _fail_result(mode: str, message: str, *, tool_name: str = TOOL_NAME) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=tool_name, tool_version="", status="FATAL_ERROR",
        data={}, error_code="INTERNAL_ERROR", message=message,
        retryable=False, trace_id=uuid.uuid4().hex)


def _verify_current_and_identity(reader: ReadonlyEvidenceReader, company_id: str,
                                 document_id: str, document_version: str,
                                 evidence_set_version: str,
                                 blocks: list[EvidenceReadResult]) -> str | None:
    """current + 身份 + 绑定字段三重复验；任一不符返回错误信息（None 表示通过）。

    - 绑定字段：每个 block 的 company_id/document_id/document_version/evidence_set_version
      必须与请求一致（跨公司/文档/版本/集合串读 fail-closed，尤其显式引用按 evidence_id
      直取时不得越界）；
    - current：cur_doc == document_version 且 cur_set == evidence_set_version（非 current fail-closed）；
    - 身份：每个 block 的 evidence_id / content_hash 与 evidence.ids 重算一致（不信任自报）。
    """
    cur_doc = reader.current_document_version(company_id, document_id)
    cur_set = reader.current_evidence_set(company_id, document_id, document_version)
    if cur_doc != document_version or cur_set != evidence_set_version:
        return "非 current document/set（fail-closed）"
    for b in blocks:
        if (b.company_id != company_id or b.document_id != document_id
                or b.document_version != document_version
                or b.evidence_set_version != evidence_set_version):
            return ("block 绑定字段与请求不符（跨公司/文档/版本/集合串读，fail-closed）: "
                    f"{b.evidence_id}")
        eid, ch = recompute_evidence_identity(b)
        if b.evidence_id != eid:
            return f"evidence_id 与重算不一致: {b.evidence_id}"
        if b.content_hash != ch:
            return f"content_hash 与重算不一致: {b.evidence_id}"
    return None


def _resolve_seed_identity_executor(reader: ReadonlyEvidenceReader, args: dict, *,
                                    tool_name: str = RESOLVE_SEED_IDENTITY_TOOL_NAME) -> C.ToolResult:
    """seed 身份解析（expand 第 0 步正式工具）：最小可信输入 + 完整身份解析 + current 校验。

    - 最小可信输入 = company_id + evidence_id（§九）；其余绑定字段可选，由只读 reader 解析
      完整技术身份（document_id/document_version/evidence_set_version/page/block/section）；
    - 可选绑定字段若提供则精确断言，任一 mismatch → fail-closed（不静默降级）；
    - evidence_id/content_hash 用 formal 算法（evidence.ids）重算，不信任自报值；
    - current 校验基于解析出的完整身份，非 current document/set → fail-closed。
    """
    company_id = args.get("company_id", "")
    evidence_id = args.get("evidence_id", "")
    if not company_id:
        return _fail_result("resolve_seed_identity", "resolve_seed_identity 缺 company_id",
                            tool_name=tool_name)
    if not evidence_id:
        return _fail_result("resolve_seed_identity", "resolve_seed_identity 缺 evidence_id",
                            tool_name=tool_name)
    block = reader.get_block(evidence_id)
    if block is None:
        return _empty_result("resolve_seed_identity", f"seed 不存在: {evidence_id}",
                             tool_name=tool_name)
    mismatches: list[str] = []
    # company_id 是唯一必填绑定字段：必须与 block 一致（跨公司串读 fail-closed）。
    if block.company_id != company_id:
        mismatches.append("company_id")
    # 可选绑定字段：提供则精确断言；未提供由只读 reader 解析出的完整身份补足。
    for key, block_val in (
        ("document_id", block.document_id),
        ("document_version", block.document_version),
        ("evidence_set_version", block.evidence_set_version),
    ):
        if args.get(key) not in (None, "") and args[key] != block_val:
            mismatches.append(key)
    if "page_number" in args and block.page_number != args["page_number"]:
        mismatches.append("page_number")
    if "block_index" in args and block.block_index != args["block_index"]:
        mismatches.append("block_index")
    if "section_path" in args and tuple(args["section_path"]) != block.section_path:
        mismatches.append("section_path")
    # 权威身份：不信任自报 content_hash/evidence_id，用 formal 算法（evidence.ids）重算。
    recomputed_eid, recomputed_ch = recompute_evidence_identity(block)
    if block.evidence_id != recomputed_eid:
        mismatches.append("evidence_id（重算）")
    if block.content_hash != recomputed_ch:
        mismatches.append("content_hash（重算）")
    if "content_hash" in args and args["content_hash"] and \
            args["content_hash"] != recomputed_ch:
        mismatches.append("content_hash")
    if mismatches:
        return _fail_result("resolve_seed_identity",
                            f"seed mismatch: {', '.join(mismatches)}", tool_name=tool_name)
    # current 校验：基于解析出的完整身份（company_id + block 的 document/version/set）。
    document_id = block.document_id
    document_version = block.document_version
    evidence_set_version = block.evidence_set_version
    cur_doc = reader.current_document_version(company_id, document_id)
    cur_set = reader.current_evidence_set(company_id, document_id, document_version)
    if cur_doc != document_version or cur_set != evidence_set_version:
        return _fail_result("resolve_seed_identity",
                            "seed 非 current document/set（fail-closed）", tool_name=tool_name)
    return C.ToolResult(
        call_id="", tool_name=tool_name, tool_version="", status="SUCCESS",
        data={
            "blocks": [block.to_dict()],
            "is_current_document": True,
            "is_current_set": True,
        },
        evidence_ids=[block.evidence_id], error_code=None, message=None,
        retryable=False, trace_id=uuid.uuid4().hex)


def _bounded_inspect_executor(reader: ReadonlyEvidenceReader, args: dict) -> C.ToolResult:
    company_id = args["company_id"]
    document_id = args["document_id"]
    document_version = args["document_version"]
    evidence_set_version = args["evidence_set_version"]
    mode = args["mode"]
    limit = args.get("limit", 1)

    if mode == "verify_seed":
        return _resolve_seed_identity_executor(reader, args, tool_name=TOOL_NAME)

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
        # §四：续表身份必须由 title/continued_from/continuation 标记等结构信号确定性判定；
        # 无 table_title 身份信号 → 绝不猜（不把后续任意 table/table_row 当续表）。
        table_title = args.get("table_title", "")
        if not table_title:
            return _empty_result(mode, "无 table_title/continued_from 身份信号，无法确定性判定续表")
        blocks = reader.bounded_blocks(
            company_id, document_id, document_version, evidence_set_version,
            after=anchor, section_path=section_path,
            evidence_types=("table", "table_row"), limit=limit)
        # 同表续表必须满足同 canonical title；遇首处无关表/无 title 块即停止链（不跳表误并，
        # 不把 revenue vs cost 等独立表并进来）。页/块连续性由 expand 层 classify 复核。
        cont: list[EvidenceReadResult] = []
        for b in blocks:
            if table_title_of(b) != table_title:
                break
            cont.append(b)
        blocks = cont
    elif mode == "explicit_reference":
        evidence_id = args.get("evidence_id", "")
        reference_target = args.get("reference_target", "")
        if evidence_id:
            block = reader.get_block(evidence_id)
            if block is None:
                return _empty_result(mode, f"交叉引用目标不存在: {evidence_id}")
            blocks = [block]
        elif _is_table_reference(reference_target):
            # 结构性表引用：具体目标 = seed 之后同 section 第一个 table/table_row（有界，仅取 1）。
            blocks = reader.bounded_blocks(
                company_id, document_id, document_version, evidence_set_version,
                after=anchor, section_path=section_path,
                evidence_types=("table", "table_row"), limit=1)
            if not blocks:
                return _empty_result(mode, "交叉引用表目标不存在（dangling）")
        else:
            # 修复 D：通用同文档命名跨章节引用解析（详见 N、标题）。确定性锚定匹配；
            # 0 匹配 → dangling，>1 匹配 → 歧义（fail-closed 不任意选）。
            found = reader.find_by_section_reference(
                company_id, document_id, document_version, evidence_set_version,
                reference_target)
            if not found:
                return _empty_result(mode, "交叉引用目标无法确定性解析（dangling）")
            if len(found) > 1:
                return _empty_result(mode, "交叉引用目标存在歧义（多匹配，fail-closed 不任意选）")
            blocks = [found[0]]
    else:
        return _fail_result(mode, f"未知 mode: {mode}")

    if not blocks:
        return _empty_result(mode, f"无有界读取结果（mode={mode}）")
    # current + 身份复验（相邻/续表/引用模式一律重新复核，不信任自报身份）。
    err = _verify_current_and_identity(
        reader, company_id, document_id, document_version, evidence_set_version, blocks)
    if err is not None:
        return _fail_result(mode, err)
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


def register_resolve_seed_identity_tool(registry, adapter: BoundedEvidenceInspectionAdapter | None = None,
                                        db_path: str | Path = DEFAULT_EVIDENCE_DB_PATH) -> None:
    """把 seed 身份解析 ToolSpec/adapter 注册到现有 ToolRegistry（expand 第 0 步正式工具）。"""
    adapter = adapter or BoundedEvidenceInspectionAdapter(db_path)
    registry.register(RESOLVE_SEED_IDENTITY_SPEC, adapter.execute_resolve_seed_identity)
