"""Eval: External V2 schema + provider search adapter —— Phase 3 Batch A commit 4。

用法: python -m evals.test_external_v2

断言：
- schema 纯函数：content_hash 确定性；grade_source 域名候选分级（A/B/C/D，未知保守 D）；
  _extract_host 剥端口/路径/userinfo；
- provider：未知 provider → ProviderUnavailable；无 key Tavily → ProviderUnavailable；
  register_provider 注入假 provider 可被工厂解析；_normalize_tavily 字段映射 + 保留 raw；
- search_external（注入 FakeProvider，不触网）：SUCCESS 映射 + 候选分级 + rank；
  空结果 → EMPTY（合法，不写「不存在」）；超时 → RETRYABLE_ERROR/TOOL_TIMEOUT；
  ProviderUnavailable → FATAL_ERROR/EXTERNAL_SEARCH_UNAVAILABLE；空 query → INTERNAL_ERROR。

不发起任何真实网络请求；不依赖 TAVILY_API_KEY。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from external_v2 import schema as S
from external_v2 import providers as P
from external_v2 import search as SRCH
from external_v2 import fetch as F


class FakeProvider:
    name = "fake"

    def __init__(self, rows=None, raise_unavailable=False, raise_other=False):
        self.rows = rows if rows is not None else []
        self.raise_unavailable = raise_unavailable
        self.raise_other = raise_other

    def search(self, query, limit):
        if self.raise_unavailable:
            raise P.ProviderUnavailable("no key")
        if self.raise_other:
            raise RuntimeError("boom")
        return self.rows[:limit]


class SlowProvider:
    name = "slow"

    def search(self, query, limit):
        time.sleep(0.3)
        return []


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

    # ---- schema 纯函数 ----
    h1 = S.content_hash("正文")
    h2 = S.content_hash("正文")
    check(h1 == h2 and len(h1) == 64, "content_hash 确定性 sha256")
    check(S.content_hash("") != S.content_hash("x"), "content_hash 区分不同内容")

    check(S.grade_source("https://www.csrc.gov.cn/x") == "A", "监管/政府域名 → A")
    check(S.grade_source("http://sse.com.cn/abc") == "A", "交易所域名 → A")
    check(S.grade_source("https://www.cninfo.com.cn/ann") == "B", "法定披露平台 → B")
    check(S.grade_source("https://www.eastmoney.com/a/1.html") == "C", "财经媒体 → C")
    check(S.grade_source("https://example.com/x") == "D", "未知域名保守 → D")
    check(S.grade_source("") == "D", "空 URL → D")

    check(S._extract_host("https://user@example.com:8080/a?b=1#c") == "example.com",
          "_extract_host 剥 userinfo/端口/路径/查询/片段")

    # ---- provider 工厂 ----
    try:
        P.get_search_provider(provider="nope")
        check(False, "未知 provider 应拒绝")
    except P.ProviderUnavailable:
        check(True, "未知 provider → ProviderUnavailable")

    P.register_provider("fake", FakeProvider)
    prov = P.get_search_provider(provider="fake")
    check(isinstance(prov, FakeProvider), "register_provider 注入后可被工厂解析")

    tav = P.TavilyProvider("")
    try:
        tav.search("q", 3)
        check(False, "无 key Tavily 应拒绝")
    except P.ProviderUnavailable:
        check(True, "无 key Tavily → ProviderUnavailable")

    norm = P._normalize_tavily(
        {"results": [
            {"title": "T", "url": "https://eastmoney.com/x", "content": "snippet",
             "raw_content": "raw", "published_date": "2025-01-01"},
        ]}, 5)
    check(len(norm) == 1 and norm[0]["published_at"] == "2025-01-01"
          and norm[0]["url"] == "https://eastmoney.com/x" and "raw" in norm[0],
          "_normalize_tavily 字段映射 + 保留 raw")

    # ---- search_external（FakeProvider）----
    rows = [
        {"title": "宁德时代公告", "url": "https://www.cninfo.com.cn/a",
         "snippet": "s1", "published_at": "2025-01-01", "source_name": "巨潮"},
        {"title": "行业分析", "url": "https://example.com/b",
         "snippet": "s2", "published_at": None, "source_name": "未知"},
    ]
    out = SRCH.search_external("宁德时代", provider=FakeProvider(rows=rows), limit=5)
    check(out.status == "SUCCESS" and len(out.results) == 2,
          "search_external SUCCESS + 结果数")
    check(out.results[0].source_grade == "B" and out.results[1].source_grade == "D",
          "搜索结果附候选来源分级")
    check(out.results[0].rank == 1 and out.results[1].rank == 2, "搜索结果 rank 递增")
    check(out.results[1].published_at is None, "published_at 缺失 → None")

    out = SRCH.search_external("q", provider=FakeProvider(rows=[]), limit=5)
    check(out.status == "EMPTY" and out.error_code is None,
          "空结果 → EMPTY（不写「不存在」）")

    out = SRCH.search_external("q", provider=FakeProvider(raise_unavailable=True))
    check(out.status == "FATAL_ERROR" and out.error_code == "EXTERNAL_SEARCH_UNAVAILABLE",
          "ProviderUnavailable → EXTERNAL_SEARCH_UNAVAILABLE")

    out = SRCH.search_external("q", provider=SlowProvider(), timeout_s=0.05)
    check(out.status == "RETRYABLE_ERROR" and out.error_code == "TOOL_TIMEOUT",
          "超时 → RETRYABLE_ERROR/TOOL_TIMEOUT")

    out = SRCH.search_external("q", provider=FakeProvider(raise_other=True))
    check(out.status == "FATAL_ERROR" and out.error_code == "INTERNAL_ERROR",
          "provider 异常 → INTERNAL_ERROR")

    out = SRCH.search_external("   ")
    check(out.status == "FATAL_ERROR" and out.error_code == "INTERNAL_ERROR",
          "空 query → INTERNAL_ERROR")

    # ---- fetch SSRF 安全边界（纯函数，不触网）----
    check(F._ip_is_public("8.8.8.8") and F._ip_is_public("2001:4860:4860::8888"),
          "公网 IPv4/IPv6 → public")
    for bad in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
                "169.254.169.254", "::1", "fc00::1", "::ffff:127.0.0.1"):
        check(not F._ip_is_public(bad), f"私网/环回/link-local/mapped → 拒绝: {bad}")

    for bad_url in ("file:///etc/passwd", "ftp://x.com", "http://localhost/",
                    "http://127.0.0.1/", "http://10.0.0.1/x", "http://[::1]/",
                    "http://192.168.1.1/", "http://169.254.169.254/latest"):
        try:
            F._validate_url(bad_url)
            check(False, f"SSRF 应拒绝: {bad_url}")
        except F._UntrustedSource:
            check(True, f"SSRF 拒绝私网/file/环回: {bad_url}")

    # DNS 解析分支（monkeypatch _resolve_ips，避免真实 DNS）
    orig_resolve = F._resolve_ips
    F._resolve_ips = lambda h: ["10.1.2.3"]
    try:
        try:
            F._validate_url("http://internal.example.com/")
            check(False, "DNS 解析到私网应拒绝")
        except F._UntrustedSource:
            check(True, "DNS 解析到私网 → SOURCE_UNTRUSTED")
    finally:
        F._resolve_ips = orig_resolve
    F._resolve_ips = lambda h: ["8.8.8.8"]
    try:
        F._validate_url("http://public.example.com/")
        check(True, "DNS 解析到公网 → 放行")
    finally:
        F._resolve_ips = orig_resolve

    # ---- _extract_text（text/plain，不依赖 trafilatura）----
    check(F._extract_text("text/plain; charset=utf-8", "你好".encode("utf-8")) == "你好",
          "text/plain 直接解码")

    # ---- fetch_external 全流程（monkeypatch httpx.Client，不触网）----
    class _FakeResp:
        def __init__(self, status_code=200, headers=None, url="", body=b""):
            self.status_code = status_code
            self.headers = headers or {}
            self.url = url
            self._body = body

        def read(self):
            return self._body

    import httpx as _httpx

    def _fake_client(handler):
        class _C:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, url):
                return handler(url)
        return _C

    orig_client = _httpx.Client
    # 内容类型拒绝
    _httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "application/pdf"}, u, b"%PDF"))
    try:
        out = F.fetch_external("https://example.com/doc.pdf")
    finally:
        _httpx.Client = orig_client
    check(out.status == "FATAL_ERROR" and out.error_code == "EXTERNAL_FETCH_BLOCKED",
          "内容类型不允许 → EXTERNAL_FETCH_BLOCKED")

    # HTTP 404
    _httpx.Client = _fake_client(
        lambda u: _FakeResp(404, {"content-type": "text/html"}, u, b"nope"))
    try:
        out = F.fetch_external("https://example.com/x")
    finally:
        _httpx.Client = orig_client
    check(out.status == "FATAL_ERROR" and out.error_code == "EXTERNAL_FETCH_BLOCKED"
          and out.http_status == 404, "HTTP 4xx → EXTERNAL_FETCH_BLOCKED")

    # 成功（text/plain）
    _httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "text/plain"}, u, "正文内容".encode("utf-8")))
    try:
        out = F.fetch_external("https://example.com/txt")
    finally:
        _httpx.Client = orig_client
    check(out.status == "SUCCESS" and out.content_text == "正文内容"
          and out.content_hash == S.content_hash("正文内容"),
          "text/plain 抓取成功 + content_hash")

    # 空正文
    _httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "text/plain"}, u, b""))
    try:
        out = F.fetch_external("https://example.com/empty")
    finally:
        _httpx.Client = orig_client
    check(out.status == "EMPTY" and out.error_code == "EXTERNAL_CONTENT_EMPTY",
          "正文为空 → EMPTY/EXTERNAL_CONTENT_EMPTY")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
