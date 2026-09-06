"""表格结构可行性探测（E1-01，独立只读）。

- 用 dev-time 依赖 pdfplumber（requirements-probe.txt 固定版本，不写入运行时
  requirements.txt）探测 PDF 是否有可可靠恢复坐标的结构化表格。
- 只读：绝不写入 Evidence Store、绝不产出 table / table_row、绝不从纯文本伪造
  表格坐标。探测结果只用于决定后续是否启用结构化表格抽取。
- 状态枚举见 evidence/schema.py::PROBE_STATUSES：
    PROBE_NOT_RUN                未运行
    PROBE_DEPENDENCY_MISSING     缺少 pdfplumber 依赖
    TABLE_STRUCTURE_AVAILABLE    已运行且可靠恢复坐标
    TABLE_STRUCTURE_UNAVAILABLE  已运行但无法恢复坐标

CLI: python -m evidence.table_probe <pdf> [--pages 1-5]
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from evidence.schema import PROBE_STATUSES

logger = logging.getLogger(__name__)

# 探测实现版本（与表结构探测逻辑绑定，便于日后判断何时需要重跑探测）。
PROBE_VERSION = "1"


@dataclass
class TableProbeResult:
    """一次表格结构探测的完整结果。"""

    status: str                                  # 见 PROBE_STATUSES
    probe_version: str = PROBE_VERSION
    dependency_version: str | None = None        # pdfplumber 版本，缺失时为 None
    pages: str | None = None                     # 请求的页码范围（原始字符串）
    resolved_pages: list[int] = field(default_factory=list)
    table_count: int = 0
    tables: list[dict] = field(default_factory=list)   # 每个含 page/bbox/cells
    failure_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "probe_version": self.probe_version,
            "dependency_version": self.dependency_version,
            "pages": self.pages,
            "resolved_pages": self.resolved_pages,
            "table_count": self.table_count,
            "tables": self.tables,
            "failure_reason": self.failure_reason,
        }


def _parse_pages(spec: str | None) -> list[int] | None:
    """解析页码范围 "1-5" / "1,3,5" / "2" → 1-based 页号列表；None 表示全部页。"""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f"非法页码范围: {spec!r}")
            pages.extend(range(start, end + 1))
        else:
            try:
                pages.append(int(part))
            except ValueError:
                raise ValueError(f"非法页码: {spec!r}")
    # 去重 + 1-based 校验 + 排序
    pages = sorted({p for p in pages if p >= 1})
    return pages or None


def _dependency_version() -> str | None:
    """返回 pdfplumber 版本；未安装返回 None。"""
    try:
        import pdfplumber
    except ImportError:
        return None
    return getattr(pdfplumber, "__version__", None)


def probe(pdf_path: str, pages: str | None = None) -> TableProbeResult:
    """探测 PDF 中指定页的结构化表格坐标。

    依赖 pdfplumber（惰性导入，便于测试注入 mock）。find_tables 基于版面线条，
    只报告可可靠恢复坐标（bbox + 每格 bbox）的表格；无线条的纯文本不会被伪造
    成表格坐标。
    """
    resolved = _parse_pages(pages)

    try:
        import pdfplumber
    except ImportError as e:
        return TableProbeResult(
            status="PROBE_DEPENDENCY_MISSING",
            pages=pages,
            resolved_pages=resolved or [],
            failure_reason=f"缺少 pdfplumber 依赖: {e}",
        )

    p = Path(pdf_path)
    if not p.exists():
        return TableProbeResult(
            status="PROBE_DEPENDENCY_MISSING",
            dependency_version=_dependency_version(),
            pages=pages,
            resolved_pages=resolved or [],
            failure_reason=f"文件不存在: {pdf_path}",
        )

    tables: list[dict] = []
    try:
        with pdfplumber.open(str(p)) as pdf:
            n_pages = len(pdf.pages)
            targets = resolved if resolved is not None else list(range(1, n_pages + 1))
            for page_no in targets:
                if page_no < 1 or page_no > n_pages:
                    continue
                page = pdf.pages[page_no - 1]
                for t in page.find_tables():
                    bbox = _normalize_bbox(getattr(t, "bbox", None))
                    cell_bboxes = _collect_cell_bboxes(t)
                    if bbox is None or not cell_bboxes:
                        # 无可靠坐标的表格不算「可恢复」，跳过。
                        continue
                    tables.append({
                        "page_number": page_no,
                        "bbox": bbox,
                        "cell_bboxes": cell_bboxes,
                    })
    except Exception as e:  # noqa: BLE001 —— 探测失败按「不可用」处理并记录原因
        logger.warning("table probe 运行异常: %s", e)
        return TableProbeResult(
            status="TABLE_STRUCTURE_UNAVAILABLE",
            dependency_version=_dependency_version(),
            pages=pages,
            resolved_pages=resolved or [],
            failure_reason=f"探测运行异常: {e}",
        )

    if tables:
        return TableProbeResult(
            status="TABLE_STRUCTURE_AVAILABLE",
            dependency_version=_dependency_version(),
            pages=pages,
            resolved_pages=resolved or [],
            table_count=len(tables),
            tables=tables,
        )
    return TableProbeResult(
        status="TABLE_STRUCTURE_UNAVAILABLE",
        dependency_version=_dependency_version(),
        pages=pages,
        resolved_pages=resolved or [],
        table_count=0,
        failure_reason="未检出可可靠恢复坐标的结构化表格",
    )


def _normalize_bbox(bbox) -> list[float] | None:
    """把 pdfplumber bbox 归一化为 [x0, top, x1, bottom]（4 个数值）。"""
    if bbox is None:
        return None
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return None
    return vals if len(vals) == 4 else None


def _collect_cell_bboxes(table) -> list[list[float]]:
    """收集表格每格 bbox（每格 4 数值）；任一格缺坐标则该表视为不可靠。"""
    cell_bboxes: list[list[float]] = []
    try:
        cells = table.cells
    except AttributeError:
        return []
    for cell in cells:
        b = _normalize_bbox(getattr(cell, "bbox", None))
        if b is None:
            return []
        cell_bboxes.append(b)
    return cell_bboxes


def _main(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m evidence.table_probe",
        description="探测 PDF 中可可靠恢复坐标的结构化表格（只读，不写库）")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--pages", default=None,
                        help="页码范围，如 1-5 或 1,3,5（缺省全部页）")
    args = parser.parse_args(argv)

    try:
        result = probe(args.pdf_path, args.pages)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    # PROBE_DEPENDENCY_MISSING 返回非零，便于脚本区分「环境缺失」与「结果不可用」。
    return 0 if result.status != "PROBE_DEPENDENCY_MISSING" else 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(_main(sys.argv[1:]))
