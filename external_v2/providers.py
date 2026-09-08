"""外部搜索 Provider 适配层（可配置，首个真实实现 Tavily）。

- Provider 接口统一输出「规范化 dict 列表」（title/url/snippet/published_at/
  source_name/raw），供 search.py 映射为 SearchResult；
- 无 key、provider 缺失、SDK 未安装 → ProviderUnavailable，由上层翻译为
  EXTERNAL_SEARCH_UNAVAILABLE，绝不静默换假数据；
- 不硬编码 TAVILY_API_KEY；key 一律来自环境/config。

CLI: 无（本模块只提供 provider；由 `python -m external_v2.search` 触发）。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import config

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """搜索 provider 不可用（无 key / 未安装 / 未知 provider）。"""


@runtime_checkable
class SearchProvider(Protocol):
    """搜索 provider 最小接口：search 返回规范化 dict 列表。"""

    name: str

    def search(self, query: str, limit: int) -> list[dict]:
        ...


def _normalize_tavily(resp: dict, limit: int) -> list[dict]:
    """把 Tavily 响应规范化为统一 dict 列表。

    Tavily 结果字段：title / url / content / score / raw_content / published_date。
    保留 raw 原样用于审计；published_date 缺失时为 None。
    """
    rows = resp.get("results") or []
    out: list[dict] = []
    for r in rows[:limit]:
        out.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "snippet": (r.get("content") or "")[:2000],
            "published_at": r.get("published_date") or None,
            "source_name": r.get("title") or None,
            "raw": dict(r),
        })
    return out


class TavilyProvider:
    """Tavily 搜索 provider（真实网络实现）。"""

    name = "tavily"

    def __init__(self, api_key: str):
        self._api_key = api_key

    def search(self, query: str, limit: int) -> list[dict]:
        if not self._api_key:
            raise ProviderUnavailable(
                "TAVILY_API_KEY 未配置，外部搜索不可用（不静默降级为假数据）")
        try:
            from tavily import TavilyClient  # type: ignore
        except ImportError as e:
            raise ProviderUnavailable(
                "tavily-python 未安装（pip install tavily-python）") from e

        client = TavilyClient(api_key=self._api_key)
        resp = client.search(
            query=query, search_depth="basic", max_results=limit)
        return _normalize_tavily(resp or {}, limit)


# provider 名 → 构造函数。
_PROVIDERS: dict[str, type] = {"tavily": TavilyProvider}


def register_provider(name: str, cls: type) -> None:
    """注册/覆盖一个 provider（供测试注入假 provider）。"""
    _PROVIDERS[name] = cls


def get_search_provider(provider: str | None = None,
                        api_key: str | None = None) -> SearchProvider:
    """解析并构造搜索 provider。

    provider 缺省取 config.EXTERNAL_SEARCH_PROVIDER；api_key 缺省取 config.TAVILY_API_KEY
    （仅 tavily）。未知 provider → ProviderUnavailable。
    """
    name = provider or config.EXTERNAL_SEARCH_PROVIDER
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ProviderUnavailable(f"未知搜索提供方: {name!r}")
    key = api_key if api_key is not None else config.TAVILY_API_KEY
    return cls(key)
