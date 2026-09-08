"""Phase 3 Batch A 真实验收 runner（博查外部检索闭环 + 财务工具快照验收）。

用途（任务书 §6/§7 + 用户修订「Phase 3 唯一启用博查」）：经 `ToolRegistry`（唯一
执行入口）真实走通两条验收路径，产出可回查的结构化验收报告：

1. **外部检索闭环**（6 类查询，provider 恒为 bocha）：
   `search_external_sources` → 取首位 URL → `fetch_external_content` →
   `snapshot_external_source`（仅 fetch SUCCESS 后固化，绝不把「搜索返回 URL」当作
   完整成功；正文不经人工复制，fetch 的 content_text 直接喂给 snapshot）。
2. **财务工具快照验收**：复用 `scripts.prepare_financial_snapshot`（临时库构建 300750
   快照 + 经 Registry 真实调用三个财务工具）。

报告区分 search / fetch / snapshot / HTML-snapshot / PDF-snapshot / blocked / failure
计数；博查鉴权失败 / 网络不可达 → 标记 `BATCH_A_BLOCKED_EXTERNAL_PROVIDER`。
本 runner 不打印真实 Key；Authorization Header 在 external_v2 层已脱敏。

CLI:
  python -m scripts.run_batch_a_acceptance \
    [--company 300750] [--out <报告.json>] [--audit-dir logs/tools] \
    [--skip-external] [--skip-financial] \
    [--excel bs --excel income --excel cash]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from scripts import prepare_financial_snapshot as prep
from tools import adapters
from tools import contracts as C
from tools import registry as R

logger = logging.getLogger(__name__)

_EXTERNAL_ROUTE = "EXTERNAL_RESEARCH"
_COMPANY = "300750"

# 6 类查询（对应授信报告研究面；类别→查询）。
_QUERY_CATEGORIES: list[tuple[str, str, str | None]] = [
    ("company_subject", "宁德时代 实际控制人 董事长 曾毓群", None),
    ("industry_market", "宁德时代 2025 动力电池 全球市场份额 排名", None),
    ("recent_news", "宁德时代 最新公告 新闻", "month"),
    ("financial_results", "宁德时代 2025 年报 营业收入 净利润", None),
    ("business_operations", "宁德时代 产能 扩产 生产基地 2025", None),
    ("risk_compliance", "宁德时代 诉讼 行政处罚 风险", None),
]


# ---------------------------------------------------------------------------
# Registry 调用封装
# ---------------------------------------------------------------------------

def _execute(reg: R.ToolRegistry, tool_name: str, arguments: dict,
             run_id: str) -> C.ToolResult:
    call = C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=arguments,
        idempotency_key=uuid.uuid4().hex, need_id="batch_a_external",
        batch_id="batch_a_external")
    return reg.execute(call, route=_EXTERNAL_ROUTE, run_id=run_id)


def _search(reg: R.ToolRegistry, query: str, *, limit: int,
            freshness: str | None, run_id: str) -> C.ToolResult:
    args = {"query": query, "limit": limit}
    if freshness:
        args["freshness"] = freshness
    return _execute(reg, "search_external_sources", args, run_id)


def _fetch(reg: R.ToolRegistry, url: str, run_id: str) -> C.ToolResult:
    return _execute(reg, "fetch_external_content", {"url": url}, run_id)


def _snapshot(reg: R.ToolRegistry, company: str, fetch_data: dict,
              search_data: dict, run_id: str) -> C.ToolResult:
    args = {
        "company_id": company,
        "canonical_url": fetch_data.get("canonical_url") or fetch_data.get("original_url"),
        "content_text": fetch_data.get("content_text", ""),
        "original_url": fetch_data.get("original_url"),
        "provider": "bocha",
        "query": search_data.get("query", ""),
        "title": search_data.get("title", ""),
        "snippet": search_data.get("snippet", ""),
        "published_at": search_data.get("published_at"),
        "content_type": fetch_data.get("content_type"),
        "http_status": fetch_data.get("http_status"),
        "content_hash": fetch_data.get("content_hash"),
        "source_grade": search_data.get("source_grade"),
        "file_hash": fetch_data.get("file_hash"),
        "page_count": fetch_data.get("page_count"),
    }
    return _execute(reg, "snapshot_external_source", args, run_id)


# ---------------------------------------------------------------------------
# 外部检索闭环验收
# ---------------------------------------------------------------------------

def run_external_acceptance(reg: R.ToolRegistry, *, company: str,
                            limit: int, run_id: str) -> dict:
    """跑 6 类查询的 搜索→抓取→快照 闭环，返回结构化结果与分类计数。"""
    queries: list[dict] = []
    counts = {
        "search_total": 0, "search_success": 0, "search_failure": 0,
        "fetch_total": 0, "fetch_success": 0, "fetch_blocked": 0,
        "fetch_empty": 0, "fetch_failure": 0,
        "snapshot_total": 0, "html_snapshot": 0, "pdf_snapshot": 0,
        "blocked": 0, "failure": 0,
    }

    for category, query, freshness in _QUERY_CATEGORIES:
        entry: dict = {"category": category, "query": query, "freshness": freshness}

        sres = _search(reg, query, limit=limit, freshness=freshness, run_id=run_id)
        counts["search_total"] += 1
        sdata = sres.data
        top = sdata.get("results", [])[0] if sdata.get("results") else None
        entry["search"] = {
            "status": sres.status,
            "provider": sdata.get("provider"),
            "provider_request_id": sdata.get("provider_request_id"),
            "result_count": sdata.get("result_count"),
            "latency_ms": sdata.get("latency_ms"),
            "error_code": sres.error_code,
            "message": sres.message,
            "top_result": {
                "title": top.get("title") if top else None,
                "url": top.get("url") if top else None,
                "published_at": top.get("published_at") if top else None,
                "source_name": top.get("source_name") if top else None,
                "source_grade": top.get("source_grade") if top else None,
            } if top else None,
        }
        if sres.is_error():
            counts["search_failure"] += 1
            counts["failure"] += 1
            entry["fetch"] = None
            entry["snapshot"] = None
            queries.append(entry)
            continue
        if sres.status == "EMPTY":
            # 空结果：搜索合法但无 URL 可抓，不记 failure（非错误），但也不进入 fetch。
            counts["search_success"] += 1
            entry["fetch"] = None
            entry["snapshot"] = None
            queries.append(entry)
            continue
        counts["search_success"] += 1

        # 抓取首位结果（绝不把「搜索返回 URL」当作完整成功）。
        url = top["url"] if top else None
        if not url:
            entry["fetch"] = None
            entry["snapshot"] = None
            queries.append(entry)
            continue

        fres = _fetch(reg, url, run_id=run_id)
        counts["fetch_total"] += 1
        fdata = fres.data
        entry["fetch"] = {
            "status": fres.status,
            "original_url": fdata.get("original_url"),
            "canonical_url": fdata.get("canonical_url"),
            "content_type": fdata.get("content_type"),
            "http_status": fdata.get("http_status"),
            "content_hash": fdata.get("content_hash"),
            "file_hash": fdata.get("file_hash"),
            "page_count": fdata.get("page_count"),
            "content_length": fdata.get("content_length"),
            "error_code": fres.error_code,
            "message": fres.message,
        }

        if fres.status == "SUCCESS":
            counts["fetch_success"] += 1
        elif fres.status == "EMPTY":
            counts["fetch_empty"] += 1
        elif fres.error_code in ("EXTERNAL_FETCH_BLOCKED", "SOURCE_UNTRUSTED",
                                 "PDF_TEXT_UNAVAILABLE", "EXTERNAL_CONTENT_EMPTY"):
            counts["fetch_blocked"] += 1
            counts["blocked"] += 1
        elif fres.is_error():
            counts["fetch_failure"] += 1
            counts["failure"] += 1

        # 仅 fetch SUCCESS 且正文非空 → 固化快照。
        if fres.status == "SUCCESS" and fdata.get("content_text"):
            sres2 = _snapshot(reg, company, fdata, {
                "query": query, "title": top.get("title"),
                "snippet": top.get("snippet"),
                "published_at": top.get("published_at"),
                "source_grade": top.get("source_grade"),
            }, run_id=run_id)
            counts["snapshot_total"] += 1
            entry["snapshot"] = {
                "status": sres2.status,
                "source_snapshot_id": sres2.data.get("source_snapshot_id"),
                "content_version": sres2.data.get("content_version"),
                "content_hash": sres2.data.get("content_hash"),
                "error_code": sres2.error_code,
                "message": sres2.message,
            }
            media = (fdata.get("content_type") or "").split(";")[0].strip().lower()
            if media == "application/pdf":
                counts["pdf_snapshot"] += 1
            else:
                counts["html_snapshot"] += 1
        else:
            entry["snapshot"] = None

        queries.append(entry)

    return {"queries": queries, "counts": counts}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(*, company: str, excel_files: list[str], out: str | None,
        audit_dir: str | Path, skip_external: bool, skip_financial: bool,
        limit: int) -> dict:
    reg = adapters.build_default_registry(audit_dir=audit_dir)

    report: dict = {
        "batch": "A",
        "search_provider": "bocha",
        "company": company,
        "external": None,
        "financial": None,
        "blocked": None,
    }

    if not skip_external:
        external = run_external_acceptance(
            reg, company=company, limit=limit, run_id="batch_a_external")
        report["external"] = external
        # 鉴权失败 / 提供方不可用 → 阻断标记（不伪装成功）。
        auth_failed = any(
            q["search"]["status"] in ("FATAL_ERROR", "RETRYABLE_ERROR")
            and q["search"]["error_code"] in ("EXTERNAL_AUTH_FAILED",
                                              "EXTERNAL_SEARCH_UNAVAILABLE",
                                              "EXTERNAL_NETWORK_ERROR")
            for q in external["queries"])
        if auth_failed:
            report["blocked"] = "BATCH_A_BLOCKED_EXTERNAL_PROVIDER"

    if not skip_financial:
        if not excel_files:
            logger.warning("未提供 --excel，跳过财务工具快照验收")
        else:
            report["financial"] = prep.run(
                company, excel_files, db=None, scope="consolidated",
                currency="CNY", declared_name="宁德时代",
                detected_name="宁德时代", audit_dir=audit_dir, keep=False)

    if out:
        Path(out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_batch_a_acceptance",
        description="Phase 3 Batch A 真实验收（博查外部闭环 + 财务工具快照）")
    parser.add_argument("--company", default=_COMPANY)
    parser.add_argument("--excel", action="append", default=None,
                        help="财务 Excel 报表路径（可重复；缺省跳过财务验收）")
    parser.add_argument("--out", default=None, help="验收报告 JSON 输出路径")
    parser.add_argument("--audit-dir", default=str(R.DEFAULT_AUDIT_DIR))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--skip-financial", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    report = run(
        company=args.company, excel_files=args.excel or [], out=args.out,
        audit_dir=args.audit_dir, skip_external=args.skip_external,
        skip_financial=args.skip_financial, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
