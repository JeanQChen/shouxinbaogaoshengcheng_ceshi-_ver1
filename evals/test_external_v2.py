"""Eval: External V2 schema + 博查 provider 搜索适配 —— Phase 3 Batch A。

用法: python -m evals.test_external_v2

断言：
- schema 纯函数：content_hash/bytes_hash 确定性；grade_source 域名候选分级（A/B/C/D，
  未知保守 D）；_extract_host 剥端口/路径/userinfo；
- provider：未知 provider → ProviderUnavailable；tavily → ProviderUnavailable（未启用，
  不读 key、不发请求）；register_provider 注入假 provider 可被工厂解析；
  _normalize_bocha 字段映射 + 保留 raw；BochaProvider.search（mock httpx.post，不触网）
  成功解析 + 捕获 log_id；鉴权/限流/服务端/非 JSON/字段缺失 → 独立异常分类；
- search_external（注入 FakeProvider，不触网）：SUCCESS 映射 + 候选分级 + rank +
  provider_request_id；空结果 → EMPTY；超时 → RETRYABLE_ERROR/TOOL_TIMEOUT；
  ProviderUnavailable → EXTERNAL_SEARCH_UNAVAILABLE；鉴权/限流/服务端/网络/坏响应
  → 对应错误码；空 query → INTERNAL_ERROR；
- fetch SSRF 安全边界（纯函数，不触网）与 text/plain 抽取。

不发起任何真实网络请求；不依赖 TAVILY_API_KEY / BOCHA_API_KEY。
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
    last_request_id = None

    def __init__(self, rows=None, raise_unavailable=False, raise_other=False,
                 request_id=None):
        self.rows = rows if rows is not None else []
        self.raise_unavailable = raise_unavailable
        self.raise_other = raise_other
        self.last_request_id = request_id

    def search(self, query, limit, **kwargs):
        if self.raise_unavailable:
            raise P.ProviderUnavailable("no key")
        if self.raise_other:
            raise RuntimeError("boom")
        return self.rows[:limit]


class RaiseProvider:
    """按指定异常 fail-closed 的假 provider，验证错误码映射。"""
    name = "raise"
    last_request_id = None

    def __init__(self, exc):
        self.exc = exc

    def search(self, query, limit, **kwargs):
        raise self.exc


class SlowProvider:
    name = "slow"

    def search(self, query, limit, **kwargs):
        time.sleep(0.3)
        return []


class _FakeBochaResp:
    """mock httpx 响应：status_code + json()。"""

    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def _patch_httpx_post(make_resp):
    """把 httpx.post 替换为 make_resp(url, kwargs) → 响应；返回原函数用于还原。"""
    import httpx

    orig = httpx.post
    httpx.post = lambda url, **kw: make_resp(url, kw)
    return orig


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
    b1 = S.bytes_hash(b"%PDF-1.4\x00\x01")
    b2 = S.bytes_hash(b"%PDF-1.4\x00\x01")
    check(b1 == b2 and len(b1) == 64, "bytes_hash 确定性 sha256（原始字节）")
    check(S.bytes_hash(b"a") != S.bytes_hash(b"b"), "bytes_hash 区分不同字节")

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

    # tavily 显式未启用：不读 key、不发请求、返回「未启用」。
    try:
        P.get_search_provider(provider="tavily")
        check(False, "tavily 应显式未启用")
    except P.ProviderUnavailable as e:
        check(True, "tavily → ProviderUnavailable（未启用）")
        check("未启用" in str(e) or "未启用" in str(e), "tavily 拒绝消息含「未启用」")

    # 运行时注册表仅含 bocha。
    check("bocha" in P._PROVIDERS and "tavily" not in P._PROVIDERS,
          "运行时注册表仅含 bocha，Tavily 已移除")

    P.register_provider("fake", FakeProvider)
    prov = P.get_search_provider(provider="fake")
    check(isinstance(prov, FakeProvider), "register_provider 注入后可被工厂解析")

    # TavilyProvider 历史类保留：直接实例化并 search 应显式报「未启用」。
    tav = P.TavilyProvider("")
    try:
        tav.search("q", 3)
        check(False, "TavilyProvider.search 应显式未启用")
    except P.ProviderUnavailable as e:
        check("未启用" in str(e), "TavilyProvider.search → ProviderUnavailable（未启用）")

    # ---- _normalize_bocha 字段映射 ----
    norm = P._normalize_bocha([
        {"name": "T", "url": "https://eastmoney.com/x", "snippet": "短摘要",
         "summary": "长摘要", "datePublished": "2025-01-01T00:00:00+08:00",
         "siteName": "东方财富", "extra": 1},
        {"name": "T2", "url": "https://example.com/y", "snippet": "s2",
         "dateLastCrawled": "2025-02-02T00:00:00Z"},
    ], 5)
    check(len(norm) == 2, "_normalize_bocha 结果数")
    check(norm[0]["title"] == "T" and norm[0]["url"] == "https://eastmoney.com/x",
          "_normalize_bocha title/url 映射")
    check(norm[0]["snippet"] == "长摘要", "_normalize_bocha snippet 优先 summary")
    check(norm[0]["published_at"] == "2025-01-01T00:00:00+08:00",
          "_normalize_bocha published_at 优先 datePublished")
    check(norm[1]["published_at"] == "2025-02-02T00:00:00Z",
          "_normalize_bocha published_at 回退 dateLastCrawled")
    check(norm[0]["source_name"] == "东方财富", "_normalize_bocha source_name=siteName")
    check("raw" in norm[0] and norm[0]["raw"]["extra"] == 1,
          "_normalize_bocha 保留 raw 整条负载")

    # ---- BochaProvider.search（mock httpx.post）----
    captured: dict = {}

    def _success_resp(url, kw):
        captured["body"] = kw["json"]
        captured["headers"] = kw["headers"]
        return _FakeBochaResp(200, {
            "code": 200, "log_id": "abc123", "msg": "ok",
            "data": {"webPages": {"value": [
                {"name": "T", "url": "https://x.com/a", "snippet": "snip"},
            ]}},
        })

    import httpx
    orig = _patch_httpx_post(_success_resp)
    try:
        prov = P.BochaProvider("test-key", base_url="https://api.test/search")
        rows = prov.search("q", 3)
    finally:
        httpx.post = orig
    check(len(rows) == 1 and rows[0]["title"] == "T", "BochaProvider 成功解析结果")
    check(prov.last_request_id == "abc123", "BochaProvider 捕获 log_id（request ID）")
    check(captured["headers"]["Authorization"] == "Bearer test-key",
          "BochaProvider Authorization: Bearer <key>")
    check(captured["body"]["query"] == "q" and captured["body"]["count"] == 3,
          "BochaProvider body 携带 query/count")
    check(captured["body"]["summary"] is True, "BochaProvider 默认 summary=true")

    # 鉴权 401 → SearchAuthError（分类可区分，不回显 key）
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(
        401, {"code": "401", "message": "Invalid API KEY", "log_id": "x"}))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "401 应抛 SearchAuthError")
    except P.SearchAuthError:
        check(True, "401 → SearchAuthError")
    finally:
        httpx.post = orig

    # 429 → SearchRateLimited
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(429, {}))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "429 应抛 SearchRateLimited")
    except P.SearchRateLimited:
        check(True, "429 → SearchRateLimited")
    finally:
        httpx.post = orig

    # 500 → SearchServerError
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(500, {}))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "5xx 应抛 SearchServerError")
    except P.SearchServerError:
        check(True, "5xx → SearchServerError")
    finally:
        httpx.post = orig

    # 非 JSON → SearchBadResponse
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(200, bad_json=True))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "非 JSON 应抛 SearchBadResponse")
    except P.SearchBadResponse:
        check(True, "非 JSON → SearchBadResponse")
    finally:
        httpx.post = orig

    # 字段缺失 → SearchBadResponse
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(
        200, {"code": 200, "data": {"webPages": {}}}))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "缺 webPages.value 应抛 SearchBadResponse")
    except P.SearchBadResponse:
        check(True, "缺 data.webPages.value → SearchBadResponse")
    finally:
        httpx.post = orig

    # HTTP 200 但业务 code 非成功 → SearchBadResponse
    orig = _patch_httpx_post(lambda u, kw: _FakeBochaResp(
        200, {"code": "500", "message": "server boom"}))
    try:
        P.BochaProvider("k", base_url="https://api.test/search").search("q", 1)
        check(False, "业务 code 非 200 应抛 SearchBadResponse")
    except P.SearchBadResponse:
        check(True, "HTTP200 但 code=500 → SearchBadResponse")
    finally:
        httpx.post = orig

    # 无 key → ProviderUnavailable
    try:
        P.BochaProvider("", base_url="https://api.test/search").search("q", 1)
        check(False, "无 key 应抛 ProviderUnavailable")
    except P.ProviderUnavailable:
        check(True, "无 key → ProviderUnavailable")

    # ---- search_external（FakeProvider）----
    rows = [
        {"title": "宁德时代公告", "url": "https://www.cninfo.com.cn/a",
         "snippet": "s1", "published_at": "2025-01-01", "source_name": "巨潮"},
        {"title": "行业分析", "url": "https://example.com/b",
         "snippet": "s2", "published_at": None, "source_name": "未知"},
    ]
    out = SRCH.search_external("宁德时代", provider=FakeProvider(rows=rows, request_id="rid-9"),
                               limit=5)
    check(out.status == "SUCCESS" and len(out.results) == 2,
          "search_external SUCCESS + 结果数")
    check(out.provider_request_id == "rid-9",
          "search_external 捕获 provider_request_id")
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

    # 鉴权/限流/服务端/网络/坏响应 → 对应错误码
    mapping = [
        (P.SearchAuthError("bad key"), "EXTERNAL_AUTH_FAILED", "FATAL_ERROR"),
        (P.SearchRateLimited("429"), "EXTERNAL_RATE_LIMITED", "RETRYABLE_ERROR"),
        (P.SearchServerError("500"), "EXTERNAL_SERVER_ERROR", "RETRYABLE_ERROR"),
        (P.SearchNetworkError("net"), "EXTERNAL_NETWORK_ERROR", "RETRYABLE_ERROR"),
        (P.SearchBadResponse("bad"), "EXTERNAL_BAD_RESPONSE", "FATAL_ERROR"),
    ]
    for exc, code, status in mapping:
        out = SRCH.search_external("q", provider=RaiseProvider(exc))
        check(out.error_code == code and out.status == status,
              f"{type(exc).__name__} → {code}/{status}")

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

    orig_client = httpx.Client
    # 内容类型拒绝
    httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "application/zip"}, u, b"PK"))
    try:
        out = F.fetch_external("https://example.com/doc.zip")
    finally:
        httpx.Client = orig_client
    check(out.status == "FATAL_ERROR" and out.error_code == "EXTERNAL_FETCH_BLOCKED",
          "内容类型不允许 → EXTERNAL_FETCH_BLOCKED")

    # HTTP 404
    httpx.Client = _fake_client(
        lambda u: _FakeResp(404, {"content-type": "text/html"}, u, b"nope"))
    try:
        out = F.fetch_external("https://example.com/x")
    finally:
        httpx.Client = orig_client
    check(out.status == "FATAL_ERROR" and out.error_code == "EXTERNAL_FETCH_BLOCKED"
          and out.http_status == 404, "HTTP 4xx → EXTERNAL_FETCH_BLOCKED")

    # 成功（text/plain）
    httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "text/plain"}, u, "正文内容".encode("utf-8")))
    try:
        out = F.fetch_external("https://example.com/txt")
    finally:
        httpx.Client = orig_client
    check(out.status == "SUCCESS" and out.content_text == "正文内容"
          and out.content_hash == S.content_hash("正文内容"),
          "text/plain 抓取成功 + content_hash")

    # 空正文
    httpx.Client = _fake_client(
        lambda u: _FakeResp(200, {"content-type": "text/plain"}, u, b""))
    try:
        out = F.fetch_external("https://example.com/empty")
    finally:
        httpx.Client = orig_client
    check(out.status == "EMPTY" and out.error_code == "EXTERNAL_CONTENT_EMPTY",
          "正文为空 → EMPTY/EXTERNAL_CONTENT_EMPTY")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
