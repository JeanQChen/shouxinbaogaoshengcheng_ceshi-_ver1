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

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
