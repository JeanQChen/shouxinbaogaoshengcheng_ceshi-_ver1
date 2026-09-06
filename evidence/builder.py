"""PdfParseResult → EvidenceBlock 构建 + CLI 编排（register → parse → build → commit）。

- 必须经过现有 parsers.pdf_parser.parse()，不复制隐藏解析流程（任务书 §9）。
- 继承 TextChunk.page_number / chunk_index / section_title，构造可回查 section_path。
- 空白占位（"（空白页）" / "（无内容）"）跳过，不成为可引用事实证据；
  低质量 / 扫描页保留为 quality_flags（诊断来自 parser metadata）。
- 纯文本 TextChunk 没有单元格坐标：首版只产出 evidence_type=paragraph；
  heading / table / table_row 保留，仅在真实结构化抽取成功后使用（E1-01）。
- 首版实体 / 期间 / 表格为空或 None，不调用 LLM 猜测。
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evidence import store
from evidence import progress
from evidence import schema as S
from evidence.ids import (
    content_hash,
    derive_document_version,
    derive_evidence_set_version,
    file_sha256,
    make_evidence_id,
    new_run_id,
)
from evidence.schema import (
    DocumentContext,
    DocumentRecord,
    EvidenceBlock,
)
from parsers.pdf_parser import PdfParseResult, parse as pdf_parse

logger = logging.getLogger(__name__)

# 空白占位文本（parser._split_into_chunks 生成）。
_BLANK_PLACEHOLDERS = {"（空白页）", "（无内容）"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(
    parsed: PdfParseResult,
    document: DocumentRecord,
    evidence_set_version: str,
) -> list[EvidenceBlock]:
    """把解析结果转换为可追溯 EvidenceBlock（纯函数，无 I/O）。

    同一输入、同一 schema/parser/builder 版本重复运行，Evidence ID 与数量一致。
    """
    low = set(parsed.metadata.get("low_quality_pages", []))
    scanned = set(parsed.metadata.get("scanned_pages", []))
    now = _utcnow()
    blocks: list[EvidenceBlock] = []

    for chunk in parsed.chunks:
        if chunk.text.strip() in _BLANK_PLACEHOLDERS:
            continue

        flags: list[str] = []
        if chunk.page_number in low:
            flags.append("LOW_QUALITY")
        if chunk.page_number in scanned:
            flags.append("SCANNED")

        section_path = [chunk.section_title] if chunk.section_title else []
        ch = content_hash(chunk.text, None)
        evidence_id = make_evidence_id(
            document.company_id,
            document.document_id,
            document.document_version,
            evidence_set_version,
            chunk.page_number,
            chunk.chunk_index,
            ch,
        )

        blocks.append(EvidenceBlock(
            evidence_id=evidence_id,
            schema_version=S.SCHEMA_VERSION,
            company_id=document.company_id,
            document_id=document.document_id,
            document_version=document.document_version,
            evidence_set_version=evidence_set_version,
            source_name=document.source_name,
            source_type=document.source_type,
            source_uri=None,
            page_number=chunk.page_number,
            block_index=chunk.chunk_index,
            section_path=section_path,
            evidence_type="paragraph",
            text=chunk.text,
            structured_payload=None,
            report_period=None,
            published_at=None,
            entities=[],
            quality_flags=flags,
            content_hash=ch,
            builder_version=S.BUILDER_VERSION,
            created_at=now,
        ))

    return blocks


def current_dependency_versions() -> dict[str, str]:
    return {"schema": S.SCHEMA_VERSION, "parser": S.PARSER_VERSION, "builder": S.BUILDER_VERSION}


def current_evidence_set_version() -> str:
    return derive_evidence_set_version(S.SCHEMA_VERSION, S.PARSER_VERSION, S.BUILDER_VERSION)


def in_memory_document(pdf_path: str, context: DocumentContext) -> DocumentRecord:
    """构造一份不落盘的 DocumentRecord（validate-only 用）。

    版本由文件内容哈希派生；document_id 缺省时生成临时 id（不写登记表）。
    """
    p = Path(pdf_path)
    sha = file_sha256(str(p))
    doc_id = context.document_id or ("doc-" + uuid.uuid4().hex[:16])
    return DocumentRecord(
        document_id=doc_id,
        document_version=derive_document_version(sha),
        company_id=context.company_id,
        source_name=context.source_name,
        source_path=context.source_path or str(p.resolve()),
        source_type=context.source_type,
        material_group=context.material_group,
        file_sha256=sha,
        file_size=p.stat().st_size,
        page_count=None,
        declared_company_name=context.declared_company_name,
        detected_company_names=context.detected_company_names,
        parser_version=S.PARSER_VERSION,
        status="registered",
        quality_flags=[],
        created_at=_utcnow(),
    )


def run_pipeline(
    pdf_path: str,
    company_id: str,
    document_id: str | None = None,
    source_type: str = "other",
    material_group: str = "company_industry",
    store_it: bool = False,
    run_id: str | None = None,
    declared_company_name: str | None = None,
) -> dict:
    """编排 register → parse → build → commit。

    - store_it=False：纯内存解析 + 构建，不写 Evidence Store、不发射进度事件。
    - store_it=True：完整链路 + 真实进度事件；幂等由 store.commit_document
      保证（同内容重跑走路径 B 复用）。
    返回 dict 摘要（run_id / document / evidence / commit）。
    """
    context = DocumentContext(
        company_id=company_id,
        source_name=Path(pdf_path).name,
        source_type=source_type,
        material_group=material_group,
        source_path=str(Path(pdf_path).resolve()),
        document_id=document_id,
        declared_company_name=declared_company_name,
    )
    set_version = current_evidence_set_version()
    deps = current_dependency_versions()

    if not store_it:
        document = in_memory_document(pdf_path, context)
        parsed = pdf_parse(pdf_path)
        document.page_count = parsed.page_count
        blocks = build(parsed, document, set_version)
        return {
            "run_id": run_id or new_run_id(),
            "document": _document_summary(document),
            "evidence": _evidence_summary(blocks),
            "commit": None,
        }

    store.init_db()
    run_id = run_id or new_run_id()

    progress.start(run_id, "VALIDATING_INPUT", "正在校验材料")
    try:
        document = store.register_document(pdf_path, context)
    except FileNotFoundError:
        progress.fail(run_id, "FAILED", "证据构建失败", error_code="UNSUPPORTED_FILE")
        raise
    progress.complete(run_id, "VALIDATING_INPUT", "材料校验完成", 1, 1)

    progress.start(run_id, "PARSING_DOCUMENT", "正在读取 PDF")
    try:
        parsed = pdf_parse(pdf_path)
    except FileNotFoundError:
        progress.fail(run_id, "FAILED", "证据构建失败", error_code="UNSUPPORTED_FILE")
        raise
    except ValueError:
        progress.fail(run_id, "FAILED", "证据构建失败", error_code="SCANNED_LOW_QUALITY")
        raise
    document.page_count = parsed.page_count
    progress.complete(run_id, "PARSING_DOCUMENT", "PDF 读取完成",
                      parsed.page_count, parsed.page_count)

    progress.start(run_id, "BUILDING_EVIDENCE", "正在构建证据链",
                   total_units=len(parsed.chunks))
    blocks = build(parsed, document, set_version)
    progress.complete(run_id, "BUILDING_EVIDENCE", "证据链构建完成",
                      len(blocks), len(blocks))

    progress.start(run_id, "PERSISTING_EVIDENCE", "正在保存证据", total_units=len(blocks))
    try:
        result = store.commit_document(
            document, blocks, set_version, run_id,
            input_hashes={"file_sha256": document.file_sha256},
            dependency_versions=deps,
        )
    except Exception:
        progress.fail(run_id, "FAILED", "证据构建失败",
                      error_code="STORE_FAILED", recoverable=True)
        raise
    progress.complete(run_id, "PERSISTING_EVIDENCE", "证据保存完成",
                      result.written + result.reused, len(blocks))
    progress.complete(run_id, "COMPLETED", "证据构建完成", len(blocks), len(blocks))

    return {
        "run_id": run_id,
        "document": _document_summary(result.document),
        "evidence": _evidence_summary(blocks),
        "commit": {
            "evidence_set_version": result.evidence_set_version,
            "written": result.written,
            "reused": result.reused,
            "checkpoint_id": result.checkpoint_id,
        },
    }


def _document_summary(document: DocumentRecord) -> dict:
    return {
        "document_id": document.document_id,
        "document_version": document.document_version,
        "source_name": document.source_name,
        "source_type": document.source_type,
        "material_group": document.material_group,
        "file_sha256": document.file_sha256[:16],
        "page_count": document.page_count,
        "status": document.status,
    }


def _evidence_summary(blocks: list[EvidenceBlock]) -> dict:
    type_stats = Counter(b.evidence_type for b in blocks)
    quality_stats = Counter(f for b in blocks for f in b.quality_flags)
    return {
        "count": len(blocks),
        "by_type": dict(type_stats),
        "by_quality_flag": dict(quality_stats),
    }


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m evidence.builder",
                                     description="把一份 PDF 构建为可追溯 Evidence")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--document-id", required=False, default=None,
                        help="业务文档稳定身份；缺省时自动生成并持久保存")
    parser.add_argument("--source-type", default="other",
                        choices=S.SOURCE_TYPES, help="来源类型")
    parser.add_argument("--material-group", default="company_industry",
                        choices=S.MATERIAL_GROUPS, help="材料分组")
    parser.add_argument("--validate-only", action="store_true",
                        help="只解析并构建，不写入 Evidence Store")
    parser.add_argument("--store", action="store_true",
                        help="构建后写入 Evidence Store")
    parser.add_argument("--run-id", default=None, help="指定运行标识")
    parser.add_argument("--resume-run-id", default=None, help="按 checkpoint 续跑某次运行")
    args = parser.parse_args(argv)

    if args.validate_only and args.store:
        print("ERROR: --validate-only 与 --store 互斥", file=sys.stderr)
        return 1

    store_it = args.store

    run_id = args.run_id or new_run_id()

    # 恢复校验：输入哈希 / 依赖版本任一变化拒绝续跑。
    if args.resume_run_id:
        store.init_db()
        sha = file_sha256(args.pdf_path)
        r = progress.resume(
            args.resume_run_id,
            input_hashes={"file_sha256": sha},
            dependency_versions=current_dependency_versions(),
        )
        if not r.can_resume:
            print(json.dumps({"error": "checkpoint 失效", "reason": r.reason},
                             ensure_ascii=False, indent=2))
            return 2
        run_id = args.resume_run_id

    summary = run_pipeline(
        args.pdf_path,
        company_id=args.company,
        document_id=args.document_id,
        source_type=args.source_type,
        material_group=args.material_group,
        store_it=store_it,
        run_id=run_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(_main(sys.argv[1:]))
