"""PDF → text chunks，含质量检测兜底。"""

from dataclasses import dataclass, field


@dataclass
class TextChunk:
    text: str
    page_number: int
    chunk_index: int


@dataclass
class PdfParseResult:
    chunks: list[TextChunk]
    page_count: int
    metadata: dict = field(default_factory=dict)


def parse(file_path: str) -> PdfParseResult:
    """解析 PDF 文件，返回 text chunks 及质量检测元信息。"""
    raise NotImplementedError
