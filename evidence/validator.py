"""Evidence 运行时 Schema 校验（Store 提交前强制调用）。

本模块提供统一 validator，保证非法对象 / 非法提交在写库前被拒绝，不依赖
SQLite 约束来兜底（SQLite 约束仍保留作为第二道防线）。`evidence.store.commit_document`
必须在任何写入前调用 `validate_commit`。

校验范围（任务书验收问题 3）：
- 必填字符串非空（company/document/version/set/source/content_hash/builder_version 等）；
- `source_type` / `material_group` / `status` / `evidence_type` 属于白名单；
- PDF 页码 1-based（page_number >= 1），block_index >= 0；
- paragraph / heading 的 structured_payload 必须为空；
- table / table_row 仅在完整结构化 payload 合法时允许；
- 表格 payload 必须包含页码、表头、单元格、单位信息和可回查坐标；
- evidence_id 必须与 ID 算法重算结果一致；同一次提交不得出现重复 evidence_id
  或重复 (page_number, block_index)。

本模块为纯函数库（无 I/O），对外只读 schema/ids，不写库、不解析 PDF。
"""

from __future__ import annotations

from evidence import schema as S
from evidence.ids import make_evidence_id
from evidence.schema import DocumentRecord, EvidenceBlock


class ValidationError(ValueError):
    """Evidence 校验失败（含字段名与原因）。"""


# 表格 payload 必须包含的键（任务书 §6.2：表头、单元格、单位和可回查坐标）。
_TABLE_REQUIRED_KEYS = ("page_number", "headers", "cells", "unit", "coordinates")


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------

def validate_document(document: DocumentRecord) -> None:
    """校验 DocumentRecord 的必填字段与枚举白名单。"""
    _require(bool(document.company_id), "document.company_id 不能为空")
    _require(bool(document.document_id), "document.document_id 不能为空")
    _require(bool(document.document_version), "document.document_version 不能为空")
    _require(bool(document.source_name), "document.source_name 不能为空")
    _require(document.source_type in S.SOURCE_TYPES,
             f"document.source_type 非法: {document.source_type!r}")
    _require(document.material_group in S.MATERIAL_GROUPS,
             f"document.material_group 非法: {document.material_group!r}")
    _require(document.status in S.DOCUMENT_STATUSES,
             f"document.status 非法: {document.status!r}")
    _require(bool(document.file_sha256), "document.file_sha256 不能为空")
    _require(bool(document.parser_version), "document.parser_version 不能为空")


# ---------------------------------------------------------------------------
# 表格结构化 payload
# ---------------------------------------------------------------------------

def _validate_table_payload(payload: dict) -> None:
    """校验 table / table_row 的结构化 payload。

    契约（E1-01 / 任务书 §6.2）：payload 必须包含页码、表头、单元格、单位信息
    与可回查坐标（表格 bbox + 每格 bbox）。缺任何关键坐标不得视作结构化表格。
    """
    _require(isinstance(payload, dict), "table payload 必须是 dict")
    for key in _TABLE_REQUIRED_KEYS:
        _require(key in payload, f"table payload 缺必需键: {key}")

    page_number = payload["page_number"]
    _require(isinstance(page_number, int) and page_number >= 1,
             f"table payload page_number 必须为 >=1 的整数: {page_number!r}")

    headers = payload["headers"]
    cells = payload["cells"]
    unit = payload["unit"]
    coords = payload["coordinates"]

    _require(isinstance(headers, list) and len(headers) > 0,
             "table payload headers 必须为非空列表")
    _require(isinstance(cells, list) and len(cells) > 0
             and all(isinstance(row, list) and len(row) > 0 for row in cells),
             "table payload cells 必须为非空的二维列表")
    _require(isinstance(unit, str) and bool(unit.strip()),
             "table payload unit 必须为非空字符串")
    _require(isinstance(coords, dict) and "bbox" in coords,
             "table payload coordinates 必须含 bbox")
    bbox = coords.get("bbox")
    _require(isinstance(bbox, list) and len(bbox) == 4
             and all(isinstance(v, (int, float)) for v in bbox),
             "table payload coordinates.bbox 必须为 4 个数值")
    cell_bboxes = coords.get("cell_bboxes")
    _require(isinstance(cell_bboxes, list) and len(cell_bboxes) > 0
             and all(isinstance(cb, list) and len(cb) == 4 for cb in cell_bboxes),
             "table payload coordinates.cell_bboxes 必须为非空且每格 4 个数值")


# ---------------------------------------------------------------------------
# 单个 EvidenceBlock
# ---------------------------------------------------------------------------

def validate_block(block: EvidenceBlock) -> None:
    """校验单条 EvidenceBlock 的字段合法性与枚举白名单。"""
    # 必填字符串非空
    for field_name in (
        "evidence_id", "schema_version", "company_id", "document_id",
        "document_version", "evidence_set_version", "source_name",
        "source_type", "evidence_type", "content_hash", "builder_version",
    ):
        _require(bool(getattr(block, field_name)), f"block.{field_name} 不能为空")

    # 枚举白名单
    _require(block.source_type in S.SOURCE_TYPES,
             f"block.source_type 非法: {block.source_type!r}")
    _require(block.evidence_type in S.EVIDENCE_TYPES,
             f"block.evidence_type 非法: {block.evidence_type!r}")

    # 页码 1-based / block_index 非负
    _require(isinstance(block.page_number, int) and block.page_number >= 1,
             f"block.page_number 必须为 >=1 的整数: {block.page_number!r}")
    _require(isinstance(block.block_index, int) and block.block_index >= 0,
             f"block.block_index 必须为非负整数: {block.block_index!r}")

    if block.evidence_type in ("paragraph", "heading"):
        _require(block.structured_payload is None or block.structured_payload == {},
                 f"{block.evidence_type} 的 structured_payload 必须为空")
        _require(bool(block.text.strip()),
                 f"{block.evidence_type} 的 text 不能为空")
    elif block.evidence_type in ("table", "table_row"):
        _require(block.structured_payload is not None and block.structured_payload != {},
                 f"{block.evidence_type} 必须含非空 structured_payload")
        _validate_table_payload(block.structured_payload)


# ---------------------------------------------------------------------------
# 批量提交
# ---------------------------------------------------------------------------

def validate_blocks(
    blocks: list[EvidenceBlock],
    company_id: str,
    document_id: str,
    document_version: str,
    evidence_set_version: str,
) -> None:
    """校验一批 blocks 的归属一致、ID 重算一致、无重复坐标/ID。"""
    _require(len(blocks) > 0, "blocks 不能为空")
    seen_ids: set[str] = set()
    seen_coords: set[tuple[int, int]] = set()

    for b in blocks:
        validate_block(b)

        _require(b.company_id == company_id,
                 f"block.company_id 与提交目标不一致: {b.company_id!r}")
        _require(b.document_id == document_id,
                 f"block.document_id 与提交目标不一致: {b.document_id!r}")
        _require(b.document_version == document_version,
                 f"block.document_version 与提交目标不一致: {b.document_version!r}")
        _require(b.evidence_set_version == evidence_set_version,
                 f"block.evidence_set_version 与提交目标不一致: {b.evidence_set_version!r}")

        recomputed = make_evidence_id(
            b.company_id, b.document_id, b.document_version,
            b.evidence_set_version, b.page_number, b.block_index, b.content_hash,
        )
        _require(b.evidence_id == recomputed,
                 f"block.evidence_id 与重算结果不一致: {b.evidence_id!r}")

        _require(b.evidence_id not in seen_ids,
                 f"同一次提交重复 evidence_id: {b.evidence_id!r}")
        coord = (b.page_number, b.block_index)
        _require(coord not in seen_coords,
                 f"同一次提交重复坐标 (page={b.page_number}, block_index={b.block_index})")

        seen_ids.add(b.evidence_id)
        seen_coords.add(coord)


def validate_commit(
    document: DocumentRecord,
    blocks: list[EvidenceBlock],
    evidence_set_version: str,
) -> None:
    """提交前统一校验（store.commit_document 必须调用）。"""
    validate_document(document)
    validate_blocks(
        blocks,
        document.company_id,
        document.document_id,
        document.document_version,
        evidence_set_version,
    )


if __name__ == "__main__":
    # 冒烟自检：构造合法/非法 block，验证校验路径可独立运行。
    from evidence.schema import (
        BUILDER_VERSION,
        PARSER_VERSION,
        SCHEMA_VERSION,
    )

    good = EvidenceBlock(
        evidence_id="e" * 32, schema_version=SCHEMA_VERSION, company_id="ACME",
        document_id="doc-1", document_version="sha256-aaaaaaaaaaaaaaaa",
        evidence_set_version="set-x", source_name="rpt.pdf", source_type="annual_report",
        source_uri=None, page_number=1, block_index=0, section_path=[],
        evidence_type="paragraph", text="正文", structured_payload=None,
        report_period=None, published_at=None, entities=[], quality_flags=[],
        content_hash="h" * 64, builder_version=BUILDER_VERSION, created_at="t",
    )
    validate_block(good)
    try:
        from dataclasses import replace
        validate_block(replace(good, page_number=0))
        print("SMOKE FAIL: 非法页码未被拒绝")
    except ValidationError as e:
        print(f"SMOKE OK: 非法页码被拒绝 -> {e}")
