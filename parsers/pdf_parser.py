"""PDF → text chunks，含质量检测兜底。

使用 pypdf >= 5.0.0 提取文本，按 ~512 token 粒度分块，
检测低质量/扫描页并给出警告或拒绝。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 公共类型 ──

@dataclass
class TextChunk:
    text: str
    page_number: int          # 1-based
    chunk_index: int          # 0-based, per page


@dataclass
class PdfParseResult:
    chunks: list[TextChunk]
    page_count: int
    metadata: dict = field(default_factory=dict)


# ── 常量 ──

_CHUNK_CHAR_LIMIT = 500       # ~512 BGE-M3 tokens ≈ 400-500 Chinese chars
_CHUNK_OVERLAP = 50           # char overlap between adjacent chunks
_LOW_QUALITY_CHAR_THRESHOLD = 100   # < 100 chars → low-quality page
_LOW_QUALITY_WARN_RATIO = 0.10
_LOW_QUALITY_REJECT_RATIO = 0.30


# ── 内部函数 ──

def _extract_text_from_page(page) -> str:
    """提取单页文本，过滤多余空白。"""
    try:
        text = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        text = page.extract_text() or ""
    # 压缩连续空白行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip()


def _page_has_images(page) -> bool:
    """检测页面是否包含图片。"""
    try:
        return len(list(page.images)) > 0
    except Exception:
        return False


def _split_into_chunks(text: str, page_number: int, chunk_limit: int = _CHUNK_CHAR_LIMIT,
                       overlap: int = _CHUNK_OVERLAP) -> list[TextChunk]:
    """将单页文本切分为 chunks。

    先按段落切，超长的段落再按字符数切，相邻 chunk 有 overlap。
    """
    if not text:
        return [TextChunk(text="（空白页）", page_number=page_number, chunk_index=0)]

    paragraphs = text.split("\n")
    chunks: list[TextChunk] = []
    buffer = ""
    idx = 0

    def _flush():
        nonlocal idx
        t = buffer.strip()
        if t:
            chunks.append(TextChunk(text=t, page_number=page_number, chunk_index=idx))
            idx += 1

    for para in paragraphs:
        para = para.strip()
        if not para:
            if buffer:
                _flush()
                buffer = ""
            continue

        if len(buffer) + len(para) + 1 <= chunk_limit:
            buffer = (buffer + "\n" + para) if buffer else para
        else:
            # 先 flush buffer
            if buffer:
                _flush()
                buffer = ""
            # 如果单段超过限制，按字符切
            if len(para) > chunk_limit:
                start = 0
                while start < len(para):
                    end = min(start + chunk_limit, len(para))
                    segment = para[start:end].strip()
                    if segment:
                        chunks.append(TextChunk(
                            text=segment, page_number=page_number, chunk_index=idx,
                        ))
                        idx += 1
                    start = end - overlap if end - overlap > start else end
            else:
                buffer = para

    # 末尾 flush
    if buffer:
        _flush()

    if not chunks:
        chunks.append(TextChunk(text="（无内容）", page_number=page_number, chunk_index=0))

    return chunks


# ── 主入口 ──

def parse(file_path: str) -> PdfParseResult:
    """解析 PDF 文件，返回 text chunks 及质量检测元信息。

    流程：
    1. 打开 PDF，逐页提取文本
    2. 每页进行质量检测（低文本量 / 扫描件）
    3. 文本按 ~512 token 粒度过行分块
    4. 汇总 quality metadata

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 低质量页 >30%（建议替换文件）
    """
    from pypdf import PdfReader

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    reader = PdfReader(str(path))
    page_count = len(reader.pages)

    if page_count == 0:
        return PdfParseResult(
            chunks=[],
            page_count=0,
            metadata={
                "source_file": path.name,
                "low_quality_pages": [],
                "low_quality_ratio": 0.0,
                "has_text_layer": False,
                "total_chunks": 0,
                "quality_status": "ok",
            },
        )

    all_chunks: list[TextChunk] = []
    low_quality_pages: list[int] = []
    scanned_pages: list[int] = []
    has_text_layer = False

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        text = _extract_text_from_page(page)
        is_lq = len(text) < _LOW_QUALITY_CHAR_THRESHOLD
        # 扫描件 = 无文本 + 有图片
        is_scanned = (not text) and _page_has_images(page)

        if is_lq:
            low_quality_pages.append(page_num)
        if is_scanned:
            scanned_pages.append(page_num)
        if text:
            has_text_layer = True

        chunks = _split_into_chunks(text, page_number=page_num)
        all_chunks.extend(chunks)

        logger.info("Page %d/%d: %d chars → %d chunks (low_quality=%s, scanned=%s)",
                    page_num, page_count, len(text), len(chunks), is_lq, is_scanned)

    low_quality_ratio = len(low_quality_pages) / page_count if page_count > 0 else 0

    # 质量判定
    if low_quality_ratio > _LOW_QUALITY_REJECT_RATIO:
        raise ValueError(
            f"文档质量过低：{len(low_quality_pages)}/{page_count} 页 "
            f"（{low_quality_ratio:.0%}）文本量不足。建议更换清晰版本。"
        )

    quality_status = "warning" if low_quality_ratio >= _LOW_QUALITY_WARN_RATIO else "ok"

    metadata = {
        "source_file": path.name,
        "page_count": page_count,
        "low_quality_pages": low_quality_pages,
        "scanned_pages": scanned_pages,
        "low_quality_ratio": round(low_quality_ratio, 4),
        "has_text_layer": has_text_layer,
        "total_chunks": len(all_chunks),
        "quality_status": quality_status,
    }

    logger.info("PDF parsed: %d pages → %d chunks, quality=%s (low=%d, scanned=%d)",
                page_count, len(all_chunks), quality_status,
                len(low_quality_pages), len(scanned_pages))

    return PdfParseResult(chunks=all_chunks, page_count=page_count, metadata=metadata)


# ── CLI ──

if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m parsers.pdf_parser <file_path>", file=sys.stderr)
        sys.exit(1)

    try:
        result = parse(sys.argv[1])
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"QUALITY REJECT: {e}", file=sys.stderr)
        sys.exit(2)

    summary = {
        "page_count": result.page_count,
        "total_chunks": len(result.chunks),
        "metadata": result.metadata,
        "sample_chunks": [
            {"page": c.page_number, "idx": c.chunk_index, "preview": c.text[:120] + "..."}
            for c in result.chunks[:5]
        ],
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
