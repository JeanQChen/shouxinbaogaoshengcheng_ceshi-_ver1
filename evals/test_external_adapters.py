"""Eval: 外部工具适配器（tools/external_adapters.py）—— Phase 3 Batch A。

用法: python -m evals.test_external_adapters

断言（mock external_v2 后端，不触网；snapshot 用临时 DB）：
- search_external_sources：SearchOutcome → ToolResult 状态/错误码/结果摘要翻译
  （SUCCESS/EMPTY/RETRYABLE_ERROR/FATAL_ERROR）；
- fetch_external_content：FetchOutcome → ToolResult 翻译（含 file_hash/page_count）；
- snapshot_external_source：ExternalSourceSnapshot 固化落库 + content_hash 幂等复用
  + 返回 source_snapshot_id（不可变快照）；
- 外部工具错误码（EXTERNAL_AUTH_FAILED 等）透传。

不发起真实网络请求；不依赖 BOCHA_API_KEY。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from external_v2 import schema as ES
from external_v2 import store as extstore
from tools import external_adapters as X
from external_v2 import search as esearch
from external_v2 import fetch as efetch


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- search_external_sources：SearchOutcome → ToolResult ----
    def _sr(title, url, grade):
        return ES.SearchResult(title=title, url=url, snippet="snip",
                               published_at=None, source_name="src",
                               source_grade=grade, provider="bocha", rank=1, raw={})

    orig_search = esearch.search_external
    esearch.search_external = lambda query, **kw: ES.SearchOutcome(
        query=query, provider="bocha", status="SUCCESS",
        results=(_sr("T", "https://eastmoney.com/x", "C"),),
        error_code=None, message=None, fetched_at=ES.utcnow_iso(), latency_ms=10,
        provider_request_id="rid-1")
    try:
        res = X._search_external_sources_executor({"query": "q", "limit": 3})
    finally:
        esearch.search_external = orig_search
    check(res.status == "SUCCESS" and res.data["provider"] == "bocha",
          "search_external_sources SUCCESS + provider=bocha")
    check(res.data["provider_request_id"] == "rid-1" and res.data["result_count"] == 1,
          "search_external_sources 捕获 provider_request_id + result_count")
    check(res.data["results"][0]["source_grade"] == "C", "搜索结果含候选来源级别")

    esearch.search_external = lambda query, **kw: ES.SearchOutcome(
        query=query, provider="bocha", status="FATAL_ERROR", results=(),
        error_code="EXTERNAL_AUTH_FAILED", message="bad key",
        fetched_at=ES.utcnow_iso(), latency_ms=0, provider_request_id="rid-x")
    try:
        res = X._search_external_sources_executor({"query": "q"})
    finally:
        esearch.search_external = orig_search
    check(res.status == "FATAL_ERROR" and res.error_code == "EXTERNAL_AUTH_FAILED",
          "search_external_sources 鉴权失败 → EXTERNAL_AUTH_FAILED 透传")

    esearch.search_external = lambda query, **kw: ES.SearchOutcome(
        query=query, provider="bocha", status="EMPTY", results=(),
        error_code=None, message="0 条", fetched_at=ES.utcnow_iso(), latency_ms=0)
    try:
        res = X._search_external_sources_executor({"query": "q"})
    finally:
        esearch.search_external = orig_search
    check(res.status == "EMPTY" and res.error_code is None,
          "search_external_sources 空结果 → EMPTY（合法）")

    # ---- fetch_external_content：FetchOutcome → ToolResult ----
    orig_fetch = efetch.fetch_external
    efetch.fetch_external = lambda url, **kw: ES.FetchOutcome(
        original_url=url, canonical_url="https://example.com/final",
        status="SUCCESS", content_text="正文", content_hash=ES.content_hash("正文"),
        content_type="application/pdf", http_status=200, error_code=None,
        message=None, fetched_at=ES.utcnow_iso(), latency_ms=5,
        file_hash="abc" * 16, page_count=12)
    try:
        res = X._fetch_external_content_executor({"url": "https://example.com/x.pdf"})
    finally:
        efetch.fetch_external = orig_fetch
    check(res.status == "SUCCESS" and res.data["content_text"] == "正文",
          "fetch_external_content SUCCESS + 正文")
    check(res.data["file_hash"] == "abc" * 16 and res.data["page_count"] == 12,
          "fetch_external_content 携带 file_hash/page_count")

    efetch.fetch_external = lambda url, **kw: ES.FetchOutcome(
        original_url=url, canonical_url=url, status="FATAL_ERROR", content_text="",
        content_hash="", content_type=None, http_status=None,
        error_code="PDF_TEXT_UNAVAILABLE", message="无文本层",
        fetched_at=ES.utcnow_iso(), latency_ms=0)
    try:
        res = X._fetch_external_content_executor({"url": "https://example.com/s.pdf"})
    finally:
        efetch.fetch_external = orig_fetch
    check(res.status == "FATAL_ERROR" and res.error_code == "PDF_TEXT_UNAVAILABLE",
          "fetch_external_content PDF 无文本层 → PDF_TEXT_UNAVAILABLE 透传")

    # ---- snapshot_external_source：固化 + 幂等复用 ----
    ext_tmp = Path(tempfile.mkdtemp(prefix="eval_ext_")) / "ext.db"
    extstore.init_db(ext_tmp)
    snap_args = {
        "company_id": "300750", "canonical_url": "https://example.com/a",
        "content_text": "宁德时代 2024 年报正文", "provider": "bocha",
        "query": "宁德时代", "title": "T", "snippet": "s",
        "content_type": "text/html", "http_status": 200, "source_grade": "C",
    }
    res1 = X._snapshot_external_source_executor(snap_args)
    check(res1.status == "SUCCESS" and res1.external_snapshot_ids,
          "snapshot_external_source 固化成功 + 返回 source_snapshot_id")
    sid1 = res1.external_snapshot_ids[0]
    check(extstore.get_snapshot(sid1).content_text == "宁德时代 2024 年报正文",
          "快照可回查，正文落库")

    # 相同 (company, canonical_url, content_hash) → 幂等复用（同一 source_snapshot_id）。
    res2 = X._snapshot_external_source_executor(snap_args)
    check(res2.external_snapshot_ids[0] == sid1 and res2.data["content_version"] == 1,
          "相同内容幂等复用（同一 snapshot_id，版本不增）")

    # 内容变化 → 新版本快照。
    changed = dict(snap_args, content_text="宁德时代 2024 年报正文（修订）")
    res3 = X._snapshot_external_source_executor(changed)
    check(res3.external_snapshot_ids[0] != sid1 and res3.data["content_version"] == 2,
          "内容变化 → 新版本快照（不可变，不覆盖历史）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
