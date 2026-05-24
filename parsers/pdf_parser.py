"""PDF → text chunks，含质量检测兜底 + 节段感知切块。

使用 pypdf >= 5.0.0 提取文本，按 ~1200 token 粒度分块（节段感知），
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
    section_title: str = ""   # 当前所在节段标题
    section_level: int = 0    # 标题层级（1=章, 2=节, 3=小节）


@dataclass
class PdfParseResult:
    chunks: list[TextChunk]
    page_count: int
    metadata: dict = field(default_factory=dict)


# ── 常量 ──

_CHUNK_CHAR_LIMIT = 1200      # ~2400 BGE-M3 tokens（安全范围内）
_CHUNK_OVERLAP = 100           # char overlap between adjacent chunks
_LOW_QUALITY_CHAR_THRESHOLD = 100
_LOW_QUALITY_WARN_RATIO = 0.10
_LOW_QUALITY_REJECT_RATIO = 0.30
_REPEATED_LINE_THRESHOLD = 0.20

# TOC detection: leader dots (.....) followed by page numbers
_TOC_PATTERN = re.compile(r'\.{5,}\s*\d+$|^\d+\.?\s*\.{5,}')
_TOC_TITLE = re.compile(r'目\s*录|目次')

# ── 节段标题检测 ──

# 中国公文/年报常见标题模式
_SECTION_PATTERNS: list[tuple[re.Pattern, int]] = [
    # 第X节 / 第一节  释义
    (re.compile(r'^第[一二三四五六七八九十百零\d]+[节章]\s*[：:]*\s*(.*)'), 1),
    # 一、公司概况 / 二、主营业务分析
    (re.compile(r'^[一二三四五六七八九十]+、\s*(.*)'), 1),
    # （一）基本情况 / （二）主要产品
    (re.compile(r'^（[一二三四五六七八九十]+）\s*(.*)'), 2),
    # 1. 概述 / 2.1 行业概况
    (re.compile(r'^\d+\.?\d*\s+(.*)'), 2),
    # 1.1 标题 / 2.3.1 子标题
    (re.compile(r'^\d+\.\d+(?:\.\d+)?\s+(.*)'), 3),
]

# 排除误判：纯数字开头但不是标题（如年份、金额）
_SECTION_FALSE_POSITIVE = re.compile(
    r'^\d{4}年|^\d{2,4}万|^\d{2,4}亿|^\d+%|^\d+元|^\d+人'
)


def _detect_section_heading(line: str) -> tuple[str, int] | None:
    """检测一行文本是否为节段标题。

    Returns:
        (section_title, level) 或 None（非标题行）。
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 60:
        return None

    # 排除明显的非标题行
    if _SECTION_FALSE_POSITIVE.match(stripped):
        return None

    for pattern, level in _SECTION_PATTERNS:
        m = pattern.match(stripped)
        if m:
            title = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else stripped
            if not title:
                title = stripped
            # 截断过长的标题
            if len(title) > 50:
                title = title[:50] + "…"
            return (title, level)

    return None


# ── 内部函数 ──

def _extract_text_from_page(page) -> str:
    """提取单页文本，过滤多余空白。"""
    try:
        text = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        text = page.extract_text() or ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip()


def _page_has_images(page) -> bool:
    """检测页面是否包含图片。"""
    try:
        return len(list(page.images)) > 0
    except Exception:
        return False


def _split_into_chunks(
    text: str,
    page_number: int,
    chunk_limit: int = _CHUNK_CHAR_LIMIT,
    overlap: int = _CHUNK_OVERLAP,
    section_title: str = "",
    section_level: int = 0,
) -> list[TextChunk]:
    """将单页文本切分为 chunks（节段感知）。

    按双换行（段落边界）切分，多段落合并直到接近 chunk_limit 才 flush。
    单页内不再因段落边界频繁截断——一个 chunk 可能跨多个段落。
    所有 chunk 继承当前页面的 section 上下文。
    """
    if not text:
        return [TextChunk(
            text="（空白页）", page_number=page_number, chunk_index=0,
            section_title=section_title, section_level=section_level,
        )]

    # 按双换行（段落边界）切分，保留段落内部换行
    raw_paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    chunks: list[TextChunk] = []
    buffer = ""
    idx = 0

    def _flush():
        nonlocal idx
        t = buffer.strip()
        if t:
            chunks.append(TextChunk(
                text=t, page_number=page_number, chunk_index=idx,
                section_title=section_title, section_level=section_level,
            ))
            idx += 1

    for para in paragraphs:
        if len(buffer) + len(para) + 2 <= chunk_limit:
            buffer = (buffer + "\n\n" + para) if buffer else para
        else:
            # buffer 满了，先 flush
            if buffer:
                _flush()
                buffer = ""

            # 单段超过限制，按字符切（保留 overlap）
            if len(para) > chunk_limit:
                start = 0
                while start < len(para):
                    end = min(start + chunk_limit, len(para))
                    segment = para[start:end].strip()
                    if segment:
                        chunks.append(TextChunk(
                            text=segment, page_number=page_number, chunk_index=idx,
                            section_title=section_title, section_level=section_level,
                        ))
                        idx += 1
                    start = end - overlap if end - overlap > start else end
            else:
                buffer = para

    if buffer:
        _flush()

    if not chunks:
        chunks.append(TextChunk(
            text="（无内容）", page_number=page_number, chunk_index=0,
            section_title=section_title, section_level=section_level,
        ))

    return chunks


# ── 节段上下文追踪 ──

def _track_section_context(
    page_texts: list[str],
) -> list[dict]:
    """遍历所有页文本，为每页记录当前所处的节段上下文。

    当检测到节段标题时更新上下文，后续页继承直到下一个标题出现。

    Returns:
        list of {"section_title": str, "section_level": int}，与 page_texts 对齐。
    """
    contexts: list[dict] = []
    current_title = ""
    current_level = 0

    for text in page_texts:
        if not text:
            contexts.append({"section_title": current_title, "section_level": current_level})
            continue

        # 扫描前 5 行，检测是否以新节段标题开头
        lines = text.split("\n")
        found_new_section = False
        for line in lines[:5]:
            detected = _detect_section_heading(line)
            if detected:
                current_title, current_level = detected
                found_new_section = True
                logger.debug("Section detected: L%d '%s'", current_level, current_title)
                break

        contexts.append({"section_title": current_title, "section_level": current_level})
        # 如果不是新节段开头，保持上一页的上下文
        if not found_new_section:
            pass

    return contexts


# ── 页眉/页脚检测 ──

def _norm(s: str) -> str:
    """去空白归一化：去掉所有空白字符后比较，容忍 pypdf 排版差异。"""
    return re.sub(r"\s+", "", s)


def _detect_repeated_lines(page_texts: list[str],
                           threshold: float = _REPEATED_LINE_THRESHOLD) -> set[str]:
    """检测跨多页重复出现的行（页眉/页脚/页码）。"""
    from collections import Counter

    first_originals: dict[str, str] = {}
    last_originals: dict[str, str] = {}
    first_counts: Counter[str] = Counter()
    last_counts: Counter[str] = Counter()

    for text in page_texts:
        if not text:
            continue
        lines = [l.strip() for l in text.split("\n")]
        lines = [l for l in lines if l]
        if not lines:
            continue

        n0 = _norm(lines[0])
        if n0:
            first_counts[n0] += 1
            if n0 not in first_originals:
                first_originals[n0] = lines[0]

        if len(lines) > 1:
            n1 = _norm(lines[-1])
            if n1:
                last_counts[n1] += 1
                if n1 not in last_originals:
                    last_originals[n1] = lines[-1]

    min_pages = max(2, int(len(page_texts) * threshold))
    repeated: set[str] = set()

    for norm_line, count in first_counts.items():
        if count >= min_pages:
            repeated.add(first_originals[norm_line])
    for norm_line, count in last_counts.items():
        if count >= min_pages:
            repeated.add(last_originals[norm_line])

    return repeated


def _strip_lines(text: str, to_remove: set[str]) -> str:
    """从文本中移除指定行（空白归一化匹配）。"""
    if not to_remove:
        return text
    remove_norms = {_norm(r): r for r in to_remove}

    lines = text.split("\n")
    stripped = [l.strip() for l in lines]
    total_chars = sum(len(s) for s in stripped if s)
    removable_chars = sum(len(s) for s in stripped if _norm(s) in remove_norms)
    if total_chars > 0 and removable_chars / total_chars > 0.5:
        return text
    kept = [l for l in lines if _norm(l.strip()) not in remove_norms]
    return "\n".join(kept)


def _is_toc_page(text: str) -> bool:
    """检测页面是否为目录页（TOC）。

    特征：多行包含省略号（leader dots）后跟页码，或含"目录"/"目次"标题。
    """
    if not text or len(text) < 20:
        return False

    lines = text.split("\n")
    toc_lines = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _TOC_TITLE.search(line):
            return True
        if _TOC_PATTERN.search(line):
            toc_lines += 1

    # ≥3 行匹配 leader dot 模式，或匹配率 >30%
    return toc_lines >= 3 or (len(lines) > 3 and toc_lines / max(len(lines), 1) > 0.3)


# ── 主入口 ──

def parse(file_path: str) -> PdfParseResult:
    """解析 PDF 文件，返回节段感知的 text chunks 及质量检测元信息。

    流程：
    1. 打开 PDF，逐页提取文本
    2. 检测跨页重复行（页眉/页脚），移除
    3. 检测节段标题，建立页级节段上下文
    4. 每页进行质量检测（低文本量 / 扫描件）
    5. 文本按 ~1200 char 粒度 + 节段上下文切块
    6. 汇总 quality metadata

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 低质量页 >30%
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

    # ── Pass 1: extract text per page ──
    raw_page_texts: list[str] = []
    page_has_images: list[bool] = []
    for page in reader.pages:
        text = _extract_text_from_page(page)
        raw_page_texts.append(text)
        page_has_images.append(_page_has_images(page))

    # ── Detect & strip repeated header/footer lines ──
    repeated = _detect_repeated_lines(raw_page_texts)
    if repeated:
        logger.info("Detected %d repeated header/footer lines: %.80s...",
                    len(repeated), str(sorted(repeated))[:80])

    stripped_texts = [_strip_lines(t, repeated) for t in raw_page_texts]

    # ── Pass 2: track section context across pages ──
    section_contexts = _track_section_context(stripped_texts)
    sections_found = sum(1 for c in section_contexts if c["section_title"])
    logger.info("Detected %d pages with section context (total %d pages)",
                sections_found, page_count)

    # ── Pass 3: quality check + chunk ──
    all_chunks: list[TextChunk] = []
    low_quality_pages: list[int] = []
    scanned_pages: list[int] = []
    has_text_layer = False

    for i, text in enumerate(stripped_texts):
        page_num = i + 1

        # Skip TOC pages — their content pollutes retrieval with section references
        if _is_toc_page(text):
            logger.info("Page %d/%d: TOC detected, skipping", page_num, page_count)
            continue

        is_lq = len(text) < _LOW_QUALITY_CHAR_THRESHOLD
        is_scanned = (not text) and page_has_images[i]

        if is_lq:
            low_quality_pages.append(page_num)
        if is_scanned:
            scanned_pages.append(page_num)
        if text:
            has_text_layer = True

        ctx = section_contexts[i]
        chunks = _split_into_chunks(
            text,
            page_number=page_num,
            section_title=ctx["section_title"],
            section_level=ctx["section_level"],
        )
        all_chunks.extend(chunks)

        logger.info("Page %d/%d: %d chars → %d chunks (section='%s', low_quality=%s, scanned=%s)",
                    page_num, page_count, len(text), len(chunks),
                    ctx["section_title"][:40] if ctx["section_title"] else "-",
                    is_lq, is_scanned)

    low_quality_ratio = len(low_quality_pages) / page_count if page_count > 0 else 0

    if low_quality_ratio > _LOW_QUALITY_REJECT_RATIO:
        raise ValueError(
            f"文档质量过低：{len(low_quality_pages)}/{page_count} 页 "
            f"（{low_quality_ratio:.0%}）文本量不足。建议更换清晰版本。"
        )

    quality_status = "warning" if low_quality_ratio >= _LOW_QUALITY_WARN_RATIO else "ok"

    # 统计 section 分布
    from collections import Counter
    section_dist = Counter(
        c.section_title for c in all_chunks if c.section_title
    )
    top_sections = section_dist.most_common(5)

    metadata = {
        "source_file": path.name,
        "page_count": page_count,
        "low_quality_pages": low_quality_pages,
        "scanned_pages": scanned_pages,
        "low_quality_ratio": round(low_quality_ratio, 4),
        "has_text_layer": has_text_layer,
        "total_chunks": len(all_chunks),
        "quality_status": quality_status,
        "header_footer_lines_removed": len(repeated),
        "sections_detected": len(section_dist),
        "top_sections": [(t, c) for t, c in top_sections],
    }

    logger.info("PDF parsed: %d pages → %d chunks, quality=%s (low=%d, scanned=%d, hdr_removed=%d, sections=%d)",
                page_count, len(all_chunks), quality_status,
                len(low_quality_pages), len(scanned_pages), len(repeated), len(section_dist))

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
            {
                "page": c.page_number,
                "idx": c.chunk_index,
                "section": c.section_title or "(无节段)",
                "level": c.section_level,
                "preview": c.text[:120] + "...",
            }
            for c in result.chunks[:10]
        ],
    }
    print(_json.dumps(summary, ensure_ascii=False, indent=2))
