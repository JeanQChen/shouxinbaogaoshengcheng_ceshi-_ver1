"""外部搜索编排：query → provider → SearchOutcome。

职责：
- 解析 provider（可传 SearchProvider 实例、provider 名、或走 config 默认值）；
- 软超时包装 provider 调用（不阻塞等待孤儿线程）；
- 把 provider 的规范化 dict 映射为 SearchResult（附候选来源级别）；
- 把失败分类为可区分状态：无 key/provider → EXTERNAL_SEARCH_UNAVAILABLE，
  超时 → TOOL_TIMEOUT（RETRYABLE），空结果 → EMPTY（合法结果，不写「不存在」）。

CLI: python -m external_v2.search --query "..." --provider tavily --limit 5
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from external_v2 import schema as S
from external_v2 import providers as P

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 5
DEFAULT_TIMEOUT_S = 15.0


class _SearchTimeout(Exception):
    """搜索软超时信号。"""


def _resolve_provider(provider: P.SearchProvider | str | None) -> P.SearchProvider:
    if isinstance(provider, P.SearchProvider):
        return provider
    if isinstance(provider, str):
        return P.get_search_provider(provider=provider)
    return P.get_search_provider()


def _call_with_timeout(provider: P.SearchProvider, query: str, limit: int,
                       timeout_s: float) -> list[dict]:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(provider.search, query, limit)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout:
            raise _SearchTimeout() from None
    finally:
        pool.shutdown(wait=False)


def _to_search_result(row: dict, provider: str, rank: int) -> S.SearchResult:
    url = row.get("url") or ""
    return S.SearchResult(
        title=row.get("title") or "",
        url=url,
        snippet=row.get("snippet") or "",
        published_at=row.get("published_at"),
        source_name=row.get("source_name"),
        source_grade=S.grade_source(url),
        provider=provider,
        rank=rank,
        raw=row.get("raw") or {},
    )


def search_external(
    query: str,
    *,
    provider: P.SearchProvider | str | None = None,
    limit: int = DEFAULT_LIMIT,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> S.SearchOutcome:
    """执行一次外部搜索，返回结构化 SearchOutcome。

    provider 可传实例（测试/复用）、provider 名（如 "tavily"）或 None（走 config）。
    """
    if not query or not query.strip():
        return S.SearchOutcome(
            query=query, provider="", status="FATAL_ERROR", results=(),
            error_code="INTERNAL_ERROR", message="query 为空",
            fetched_at=S.utcnow_iso(), latency_ms=0)

    prov_name = getattr(provider, "name", None) if isinstance(provider, P.SearchProvider) \
        else (provider if isinstance(provider, str) else None)
    t0 = time.perf_counter()
    try:
        prov = _resolve_provider(provider)
    except P.ProviderUnavailable as e:
        return S.SearchOutcome(
            query=query, provider=prov_name or "", status="FATAL_ERROR", results=(),
            error_code="EXTERNAL_SEARCH_UNAVAILABLE", message=str(e),
            fetched_at=S.utcnow_iso(), latency_ms=0)
    prov_name = getattr(prov, "name", prov_name or "")

    try:
        rows = _call_with_timeout(prov, query, limit, timeout_s)
    except _SearchTimeout:
        return S.SearchOutcome(
            query=query, provider=prov_name, status="RETRYABLE_ERROR", results=(),
            error_code="TOOL_TIMEOUT", message=f"搜索超过 {timeout_s}s 超时",
            fetched_at=S.utcnow_iso(),
            latency_ms=int((time.perf_counter() - t0) * 1000))
    except P.ProviderUnavailable as e:
        return S.SearchOutcome(
            query=query, provider=prov_name, status="FATAL_ERROR", results=(),
            error_code="EXTERNAL_SEARCH_UNAVAILABLE", message=str(e),
            fetched_at=S.utcnow_iso(),
            latency_ms=int((time.perf_counter() - t0) * 1000))
    except Exception as e:
        logger.warning("外部搜索异常: %s", e, exc_info=True)
        return S.SearchOutcome(
            query=query, provider=prov_name, status="FATAL_ERROR", results=(),
            error_code="INTERNAL_ERROR", message=f"{type(e).__name__}: {e}",
            fetched_at=S.utcnow_iso(),
            latency_ms=int((time.perf_counter() - t0) * 1000))

    results = tuple(_to_search_result(r, prov_name, i + 1) for i, r in enumerate(rows))
    latency_ms = int((time.perf_counter() - t0) * 1000)
    if not results:
        return S.SearchOutcome(
            query=query, provider=prov_name, status="EMPTY", results=(),
            error_code=None, message="搜索返回 0 条结果",
            fetched_at=S.utcnow_iso(), latency_ms=latency_ms)
    return S.SearchOutcome(
        query=query, provider=prov_name, status="SUCCESS", results=results,
        error_code=None, message=None, fetched_at=S.utcnow_iso(), latency_ms=latency_ms)


def _outcome_to_dict(outcome: S.SearchOutcome) -> dict:
    return {
        "query": outcome.query,
        "provider": outcome.provider,
        "status": outcome.status,
        "error_code": outcome.error_code,
        "message": outcome.message,
        "fetched_at": outcome.fetched_at,
        "latency_ms": outcome.latency_ms,
        "results": [
            {
                "rank": r.rank,
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet[:300],
                "published_at": r.published_at,
                "source_grade": r.source_grade,
                "source_name": r.source_name,
            }
            for r in outcome.results
        ],
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m external_v2.search")
    parser.add_argument("--query", required=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    outcome = search_external(args.query, provider=args.provider,
                              limit=args.limit, timeout_s=args.timeout)
    print(json.dumps(_outcome_to_dict(outcome), ensure_ascii=False, indent=2))
    return 0 if outcome.status in ("SUCCESS", "EMPTY") else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
