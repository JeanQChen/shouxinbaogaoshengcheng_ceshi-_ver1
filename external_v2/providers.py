"""外部搜索 Provider 适配层（Phase 3 唯一启用：博查 Bocha Web Search）。

- Provider 接口统一输出「规范化 dict 列表」（title/url/snippet/published_at/
  source_name/raw），供 search.py 映射为 SearchResult；
- 无 key / provider 未启用 / 未知 provider → ProviderUnavailable，由上层翻译为
  EXTERNAL_SEARCH_UNAVAILABLE，绝不静默换假数据；
- 鉴权失败（401/403）、限流（429）、服务端异常（5xx）、网络失败、非法 JSON /
  响应字段缺失均按独立异常分类，由 search.py 映射为可区分错误码；
- 不硬编码 BOCHA_API_KEY；key 一律来自环境/config；
- 保存博查返回的 log_id（provider request ID）供审计关联。

冻结决策（用户确认）：Phase 3 只使用博查。
- `_PROVIDERS` 运行时注册表仅含 bocha；Tavily 源文件保留为历史代码，但运行时不可达：
  `get_search_provider("tavily")` 显式返回「Provider 未启用」，不读取 key、不发请求。
- 博查失败时如实返回错误，不回退到任何其他 Provider。

CLI: 无（本模块只提供 provider；由 `python -m external_v2.search` 触发）。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import config

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """搜索 provider 不可用（无 key / 未安装 / 未知 provider / 显式未启用）。"""


class SearchAuthError(RuntimeError):
    """401/403 鉴权失败（key 存在但无效）。"""


class SearchRateLimited(RuntimeError):
    """429 限流。"""


class SearchServerError(RuntimeError):
    """5xx 服务端异常。"""


class SearchNetworkError(RuntimeError):
    """连接 / 网络失败（非超时；超时由 search.py 软超时包装为 TOOL_TIMEOUT）。"""


class SearchBadResponse(RuntimeError):
    """非法 JSON / 响应字段缺失 / 业务 code 非成功。"""


@runtime_checkable
class SearchProvider(Protocol):
    """搜索 provider 最小接口：search 返回规范化 dict 列表。"""

    name: str

    def search(self, query: str, limit: int, **kwargs) -> list[dict]:
        ...


# ---------------------------------------------------------------------------
# 博查（唯一启用 provider）
# ---------------------------------------------------------------------------

def _normalize_bocha(rows: list[dict], limit: int) -> list[dict]:
    """把博查 webPages.value 规范化为统一 dict 列表。

    博查结果字段（实测）：
      name / url / displayUrl / snippet / summary / siteName / siteIcon /
      datePublished（推荐，UTC+8 显式偏移）/ dateLastCrawled（历史字段，Z 实为 +08:00）。
    published_at 优先 datePublished；snippet 优先 summary（summary:true 时的长摘要），
    缺失回退 snippet。raw 保留整条原始负载供审计。
    """
    out: list[dict] = []
    for r in rows[:limit]:
        published_at = r.get("datePublished") or r.get("dateLastCrawled")
        out.append({
            "title": r.get("name") or "",
            "url": r.get("url") or "",
            "snippet": (r.get("summary") or r.get("snippet") or "")[:2000],
            "published_at": published_at,
            "source_name": r.get("siteName"),
            "raw": dict(r),
        })
    return out


class BochaProvider:
    """博查 Web Search provider（真实网络实现，唯一启用）。

    官方接口：POST {base_url}，Authorization: Bearer <key>，JSON body
    {query, count, freshness, include?, exclude?, summary}。
    """

    name = "bocha"

    def __init__(self, api_key: str, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url or config.BOCHA_BASE_URL
        self.last_request_id: str | None = None

    def search(self, query: str, limit: int, freshness: str | None = None,
               include: str | None = None, exclude: str | None = None,
               summary: bool = True, timeout_s: float = 15.0) -> list[dict]:
        if not self._api_key:
            raise ProviderUnavailable(
                "BOCHA_API_KEY 未配置，外部搜索不可用（不静默降级为假数据）")

        import httpx  # type: ignore

        body: dict = {"query": query, "count": max(1, min(limit, 50)),
                      "freshness": freshness or "noLimit", "summary": bool(summary)}
        if include:
            body["include"] = include
        if exclude:
            body["exclude"] = exclude
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Content-Type": "application/json"}

        try:
            resp = httpx.post(self._base_url, headers=headers, json=body,
                              timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise SearchNetworkError(f"博查请求超时: {type(e).__name__}") from e
        except (httpx.ConnectError, httpx.NetworkError) as e:
            raise SearchNetworkError(f"博查网络失败: {type(e).__name__}: {e}") from e
        except httpx.HTTPError as e:
            raise SearchNetworkError(f"博查 HTTP 传输错误: {type(e).__name__}: {e}") from e

        self.last_request_id = None
        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001 — 非 JSON 明确分类
            raise SearchBadResponse(f"博查返回非 JSON: {type(e).__name__}") from e

        if isinstance(payload, dict):
            self.last_request_id = payload.get("log_id")

        # 按 HTTP 状态分类（鉴权/限流/服务端优先，实测 401 带 {code:"401",message,log_id}）。
        if resp.status_code in (401, 403):
            raise SearchAuthError(_bocha_error_message(payload, resp.status_code))
        if resp.status_code == 429:
            raise SearchRateLimited(_bocha_error_message(payload, resp.status_code))
        if resp.status_code >= 500:
            raise SearchServerError(_bocha_error_message(payload, resp.status_code))
        if resp.status_code >= 400:
            raise SearchBadResponse(_bocha_error_message(payload, resp.status_code))

        # 业务 code 非成功（HTTP 200 但 code 非 200/“200”时 fail-closed）。
        if not isinstance(payload, dict):
            raise SearchBadResponse("博查响应结构非 JSON object")
        code = payload.get("code")
        if code not in (200, "200"):
            raise SearchBadResponse(f"博查返回 code={code!r}: {payload.get('message')}")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise SearchBadResponse("博查响应缺少 data 字段")
        web_pages = data.get("webPages")
        if not isinstance(web_pages, dict) or "value" not in web_pages:
            raise SearchBadResponse("博查响应缺少 data.webPages.value 字段")
        rows = web_pages.get("value") or []
        if not isinstance(rows, list):
            raise SearchBadResponse("博查 data.webPages.value 非数组")

        return _normalize_bocha(rows, limit)


def _bocha_error_message(payload, status_code: int) -> str:
    """提取博查错误消息，不回显 API key（payload 不含 key）。"""
    if isinstance(payload, dict):
        msg = payload.get("message")
        if msg:
            return f"博查 HTTP {status_code}: {msg}"
    return f"博查 HTTP {status_code}"


# ---------------------------------------------------------------------------
# 历史实现（保留源文件，但运行时不可达）
# ---------------------------------------------------------------------------

def _normalize_tavily(resp: dict, limit: int) -> list[dict]:
    """历史 Tavily 响应规范化（保留源码；Phase 3 不再注册、不再调用）。"""
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
    """历史 Tavily 搜索 provider（保留源码；不注册、不可选、不发请求）。"""

    name = "tavily"

    def __init__(self, api_key: str):
        self._api_key = api_key

    def search(self, query: str, limit: int, **kwargs) -> list[dict]:
        raise ProviderUnavailable(
            "tavily Provider 未启用（Phase 3 唯一启用搜索提供方为博查）")


# provider 名 → 构造函数（运行时注册表：仅博查）。
_PROVIDERS: dict[str, type] = {"bocha": BochaProvider}

# 显式声明不可用的历史 provider：get_search_provider 命中时返回「未启用」，
# 不读取 key、不发请求。避免因历史源码仍存在而被误选。
_DISABLED_PROVIDERS = frozenset({"tavily"})


def register_provider(name: str, cls: type) -> None:
    """注册/覆盖一个 provider（供测试注入假 provider）。"""
    _PROVIDERS[name] = cls


def get_search_provider(provider: str | None = None,
                        api_key: str | None = None) -> SearchProvider:
    """解析并构造搜索 provider。

    - provider 缺省取 config.EXTERNAL_SEARCH_PROVIDER（默认 bocha）；
    - 显式禁用 provider（tavily）→ ProviderUnavailable（「未启用」），不读 key；
    - 未知 provider → ProviderUnavailable（fail-closed）；
    - 仅 bocha 读取 config.BOCHA_API_KEY。
    """
    name = provider or config.EXTERNAL_SEARCH_PROVIDER
    if name in _DISABLED_PROVIDERS:
        raise ProviderUnavailable(
            f"{name} Provider 未启用（Phase 3 唯一启用搜索提供方为博查）")
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ProviderUnavailable(f"未知搜索提供方: {name!r}")
    key = api_key if api_key is not None else config.BOCHA_API_KEY
    return cls(key)
