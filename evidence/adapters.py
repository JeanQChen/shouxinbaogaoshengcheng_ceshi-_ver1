"""V1 TextChunk / RetrievedChunk 兼容适配（只读，不改变 V1 检索链路）。

任务书 §10：适配器输出保留 V1 需要的 text / page_number / chunk_index /
source_file / source_type / section_title；不得改变 V1 query、embedding、
权重、排序和 Top-K。

section_path 首版为单元素路径（V1 parser 只产出单层标题），取第 0 个元素映射到
V1 的 section_title；section_level 由 V1 parser 保留，这里固定为 0（不伪造层级）。
"""

from __future__ import annotations

from evidence.schema import EvidenceBlock
from parsers.pdf_parser import TextChunk
from retrieval.retriever import RetrievedChunk


def to_text_chunk(block: EvidenceBlock) -> TextChunk:
    """EvidenceBlock → V1 TextChunk（保留解析阶段需要的最小字段）。"""
    return TextChunk(
        text=block.text,
        page_number=block.page_number,
        chunk_index=block.block_index,
        section_title=block.section_path[0] if block.section_path else "",
        section_level=0,
    )


def to_retrieved_chunk(block: EvidenceBlock, score: float = 0.0) -> RetrievedChunk:
    """EvidenceBlock → V1 RetrievedChunk（保留检索阶段需要的最小字段）。"""
    return RetrievedChunk(
        text=block.text,
        page_number=block.page_number,
        chunk_index=block.block_index,
        source_file=block.source_name,
        source_type=block.source_type,
        section_title=block.section_path[0] if block.section_path else "",
        score=score,
    )


if __name__ == "__main__":
    # 冒烟自检：构造一个合成 EvidenceBlock 做往返，验证字段不丢失。
    from parsers.pdf_parser import TextChunk, PdfParseResult

    from evidence import builder, schema as S

    doc = S.DocumentRecord(
        document_id="doc-smoke", document_version="sha256-" + "a" * 16,
        company_id="ACME", source_name="rpt.pdf", source_path=None,
        source_type="annual_report", material_group="company_industry",
        file_sha256="a" * 64, file_size=1, page_count=1,
        declared_company_name=None, detected_company_names=[],
        parser_version=S.PARSER_VERSION, status="registered",
        quality_flags=[], created_at="2026-01-01T00:00:00Z",
    )
    parsed = PdfParseResult(
        chunks=[TextChunk(text="适配往返文本", page_number=2, chunk_index=3,
                          section_title="节", section_level=0)],
        page_count=2, metadata={},
    )
    block = builder.build(parsed, doc, builder.current_evidence_set_version())[0]
    tc = to_text_chunk(block)
    rc = to_retrieved_chunk(block, score=0.7)
    print(f"text_chunk      : p{tc.page_number} c{tc.chunk_index} section={tc.section_title!r}")
    print(f"retrieved_chunk : p{rc.page_number} c{rc.chunk_index} source={rc.source_file} "
          f"type={rc.source_type} score={rc.score}")
