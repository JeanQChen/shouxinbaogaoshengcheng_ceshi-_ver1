"""Eval: PDF parser。

只用 pypdf.PdfWriter().add_blank_page() 生成最简测试 PDF，不引入新依赖。

用法: python -m evals.test_pdf_parser
"""

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_pdf(path: str, page_count: int) -> None:
    """用 pypdf 生成最简 PDF（add_blank_page × N）。"""
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)


def _make_text_pdf(path: str, page_texts: list[str]) -> None:
    """生成含文字的极简 PDF，不引入新依赖。

    用 raw bytes 构建合法 PDF，pypdf 可正常提取文字。
    文字只支持 ASCII（内嵌 Helvetica 字体）。
    """
    buf = io.BytesIO()

    def w(data: bytes) -> int:
        offset = buf.tell()
        buf.write(data)
        return offset

    w(b"%PDF-1.4\n")

    # ── 构建所有间接对象 ──
    # 对象编号：
    #   1 = Catalog, 2 = Pages
    #   每页占 2 个对象: content_stream(2N+1), page(2N+2)
    #   字体 = 最后一个对象 (last = 3 + len(page_texts)*2)
    n_pages = len(page_texts)
    font_obj = 3 + n_pages * 2

    # ── 字体（所有页共享 Helvetica）──
    font_data = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    obj_offsets = {}

    # ── 每页的 Content stream + Page ──
    page_obj_nums = []
    for i, text in enumerate(page_texts):
        cs_num = 3 + i * 2       # content stream 对象号
        pg_num = 4 + i * 2       # page 对象号
        page_obj_nums.append(pg_num)

        # Content stream
        # 对 PDF 字符串转义: \ → \\ , ( → \( , ) → \)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_data = f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode("ascii")

        obj_offsets[cs_num] = w(f"{cs_num} 0 obj\n".encode())
        w(f"<< /Length {len(content_data)} >>\nstream\n".encode())
        w(content_data)
        w(b"\nendstream\nendobj\n")

        # Page
        obj_offsets[pg_num] = w(f"{pg_num} 0 obj\n".encode())
        w(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
          f"   /Contents {cs_num} 0 R\n"
          f"   /Resources << /Font << /F1 {font_obj} 0 R >> >> >>\n".encode())
        w(b"endobj\n")

    # ── Pages tree ──
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    obj_offsets[2] = w(b"2 0 obj\n")
    w(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>\n".encode())
    w(b"endobj\n")

    # ── Catalog ──
    obj_offsets[1] = w(b"1 0 obj\n")
    w(b"<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # ── 字体（放在最后）──
    obj_offsets[font_obj] = w(f"{font_obj} 0 obj\n".encode())
    w(font_data + b"\nendobj\n")

    # ── xref 表 ──
    xref_offset = buf.tell()
    total_objs = font_obj + 1
    w(f"xref\n0 {total_objs}\n".encode())
    w(b"0000000000 65535 f \n")
    for num in range(1, total_objs):
        offset = obj_offsets.get(num, 0)
        w(f"{offset:010d} 00000 n \n".encode())

    # ── Trailer ──
    w(f"trailer << /Size {total_objs} /Root 1 0 R >>\n".encode())
    w(f"startxref\n{xref_offset}\n%%EOF\n".encode())

    with open(path, "wb") as f:
        f.write(buf.getvalue())


_TEXT_100 = "Page content with sufficient text to exceed the low quality threshold of one hundred characters for testing."


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(condition, msg):
        nonlocal passed, failed
        if condition:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    from parsers.pdf_parser import (
        parse, TextChunk, PdfParseResult,
        _split_into_chunks, _CHUNK_CHAR_LIMIT,
    )

    # ── Test 1: _split_into_chunks (pure function) ──
    short_text = "这是一个简短的测试段落。"
    chunks = _split_into_chunks(short_text, page_number=1)
    check(len(chunks) == 1, f"Short text → 1 chunk (got {len(chunks)})")
    check(chunks[0].page_number == 1, "Chunk page_number == 1")
    check(chunks[0].chunk_index == 0, "Chunk chunk_index == 0")
    check(short_text in chunks[0].text, "Chunk contains original text")

    long_text = "测试文本。" * (_CHUNK_CHAR_LIMIT // 5 + 10)
    long_chunks = _split_into_chunks(long_text, page_number=2)
    check(len(long_chunks) >= 2, f"Long text → ≥2 chunks (got {len(long_chunks)})")

    empty_chunks = _split_into_chunks("", page_number=3)
    check(len(empty_chunks) == 1, "Empty text → 1 chunk")
    check("空白页" in empty_chunks[0].text, "Empty text chunk says 空白页")

    para_text = "第一段内容。\n\n第二段内容。\n\n第三段。"
    para_chunks = _split_into_chunks(para_text, page_number=4)
    check(len(para_chunks) >= 1, f"Paragraphs → ≥1 chunk (got {len(para_chunks)})")

    # ── Test 2: dataclass instantiation ──
    chunk = TextChunk(text="测试", page_number=5, chunk_index=2)
    check(chunk.text == "测试", "TextChunk.text")
    check(chunk.page_number == 5, "TextChunk.page_number")
    check(chunk.chunk_index == 2, "TextChunk.chunk_index")

    result_dc = PdfParseResult(chunks=[chunk], page_count=10, metadata={"k": "v"})
    check(len(result_dc.chunks) == 1, "PdfParseResult.chunks")
    check(result_dc.page_count == 10, "PdfParseResult.page_count")
    check(result_dc.metadata["k"] == "v", "PdfParseResult.metadata")

    # ── Test 3: parse() with minimal PDFs ──
    tmpdir = tempfile.mkdtemp(prefix="eval_pdf_")
    try:
        # 3a. 含文字 PDF → 正常解析
        pdf_path = os.path.join(tmpdir, "test_text.pdf")
        _make_text_pdf(pdf_path, [_TEXT_100, _TEXT_100, _TEXT_100])
        result = parse(pdf_path)
        check(isinstance(result, PdfParseResult), "parse() returns PdfParseResult")
        check(result.page_count == 3, f"page_count == 3 (got {result.page_count})")
        check(result.metadata.get("source_file") == "test_text.pdf",
              "metadata.source_file correct")
        check(result.metadata["has_text_layer"] is True,
              "has_text_layer=True for text PDF")
        check(result.metadata["quality_status"] == "ok",
              f"quality_status ok (got {result.metadata.get('quality_status')})")
        # 每页至少 1 chunk
        check(len(result.chunks) >= 3,
              f"≥3 chunks for 3 text pages (got {len(result.chunks)})")

        # 3b. Missing file → FileNotFoundError
        try:
            parse(os.path.join(tmpdir, "nonexistent.pdf"))
            check(False, "parse() should raise for missing file")
        except FileNotFoundError:
            check(True, "parse() raises FileNotFoundError for missing file")

        # 3c. Single page → all metadata keys present
        pdf1 = os.path.join(tmpdir, "test_1p_text.pdf")
        _make_text_pdf(pdf1, [_TEXT_100])
        result_single = parse(pdf1)
        for key in ("source_file", "page_count", "low_quality_pages",
                     "low_quality_ratio", "has_text_layer", "total_chunks",
                     "quality_status", "scanned_pages"):
            check(key in result_single.metadata, f"metadata has '{key}'")

        # 3d. 5 blank pages → low quality reject (>30%)
        pdf5 = os.path.join(tmpdir, "test_5blank.pdf")
        _make_pdf(pdf5, 5)
        try:
            result_blank = parse(pdf5)
            details.append(f"PASS: 5 blank pages parsed "
                           f"(ratio={result_blank.metadata.get('low_quality_ratio')})")
            passed += 1
        except ValueError as e:
            check(True, f"5 blank pages rejected: {str(e)[:80]}")

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json as _json
    result = main()
    for d in result["details"]:
        print(d)
    print(f"\n{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    sys.exit(0 if result["failed"] == 0 else 1)
