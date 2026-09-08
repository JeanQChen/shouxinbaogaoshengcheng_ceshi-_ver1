"""安全正文获取 + 抽取（SSRF 防护 + 重定向逐跳校验 + 大小/类型限制）。

安全边界（不得放宽）：
- 仅允许 http/https；拒绝 file URI；
- 拒绝 localhost、环回、私网（10/8、172.16/12、192.168/16）、link-local（169.254/16）、
  ::1、fc00::/7、组播、保留段；主机名 DNS 解析出的所有 A/AAAA 都必须是公网；
- 重定向逐跳校验目标（重定向到私网同样拒绝），并设重定向上限；
- 限制响应大小（超出上限即拒绝）与内容类型（白名单）；
- 网页正文视为不可信数据，只做文本搬运与抽取，不执行、不解读为指令。

CLI: python -m external_v2.fetch --url "<url>"
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import sys
import time
import urllib.parse

import config
from external_v2 import schema as S

logger = logging.getLogger(__name__)

# 允许抽取正文的内容类型（media type 前缀）。
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class _UntrustedSource(Exception):
    """SSRF / 来源安全校验未通过（SOURCE_UNTRUSTED）。"""


class _Blocked(Exception):
    """robots/登录墙/内容类型/大小/重定向上限（EXTERNAL_FETCH_BLOCKED）。"""


class _TooManyRedirects(_Blocked):
    """重定向次数超上限。"""


def _ip_is_public(ip_str: str) -> bool:
    """判断 IP 是否为公网地址（默认拒绝私网/环回/link-local/保留/组播/未指定）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 等映射地址按 IPv4 判断
    return ip.is_global


def _resolve_ips(host: str) -> list[str]:
    """DNS 解析主机名，返回全部 A/AAAA 地址（去重）。"""
    infos = socket.getaddrinfo(host, 0, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def _validate_url(url: str) -> None:
    """校验 URL 协议与主机（SSRF 防护），不通过则抛 _UntrustedSource。"""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise _UntrustedSource(f"协议不允许: {scheme!r}（仅 http/https）")
    host = parsed.hostname
    if not host:
        raise _UntrustedSource("缺少主机名")
    host_l = host.lower()
    if (host_l == "localhost" or host_l.endswith(".localhost")
            or host_l.endswith(".local")):
        raise _UntrustedSource("主机为 localhost/.local，拒绝")

    # 若主机本身是 IP 字面量，直接判断；否则解析全部 A/AAAA 并逐一要求公网。
    try:
        ip = ipaddress.ip_address(host)
        if not _ip_is_public(str(ip)):
            raise _UntrustedSource(f"目标 IP 非公网: {host}")
    except ValueError:
        try:
            resolved = _resolve_ips(host)
        except socket.gaierror as e:
            raise _Blocked(f"DNS 解析失败: {host}") from e
        if not resolved:
            raise _Blocked(f"DNS 解析为空: {host}")
        for ip_str in resolved:
            if not _ip_is_public(ip_str):
                raise _UntrustedSource(f"目标域名解析到非公网地址: {host} -> {ip_str}")


def _extract_text(content_type: str, body: bytes) -> str:
    """从响应体抽取正文文本。

    优先 trafilatura（HTML）；text/plain 直接解码；trafilatura 未安装时明确报错
    （依赖不可用与网页阻止分开报告，不静默降级）。
    """
    media = content_type.split(";")[0].strip().lower() if content_type else ""

    if media.startswith("text/plain"):
        return body.decode("utf-8", errors="replace").strip()

    # HTML 族：用 trafilatura 抽取主文本。
    try:
        import trafilatura  # type: ignore
    except ImportError as e:
        raise _Blocked("trafilatura 未安装（pip install trafilatura）") from e

    text = trafilatura.extract(body, include_comments=False, include_tables=False,
                               favor_precision=True)
    return (text or "").strip()


def fetch_external(
    url: str,
    *,
    timeout_s: float | None = None,
    max_bytes: int | None = None,
    max_redirects: int | None = None,
) -> S.FetchOutcome:
    """安全抓取并抽取正文，返回 FetchOutcome。

    逐跳手动跟随重定向并在每一跳前做 SSRF 校验（httpx follow_redirects=False）。
    """
    timeout_s = timeout_s if timeout_s is not None else config.EXTERNAL_FETCH_TIMEOUT_S
    max_bytes = max_bytes if max_bytes is not None else config.EXTERNAL_FETCH_MAX_BYTES
    max_redirects = (max_redirects if max_redirects is not None
                     else config.EXTERNAL_FETCH_MAX_REDIRECTS)

    t0 = time.perf_counter()
    try:
        import httpx  # type: ignore
    except ImportError as e:
        return S.FetchOutcome(
            original_url=url, canonical_url=url, status="FATAL_ERROR",
            content_text="", content_hash="", content_type=None, http_status=None,
            error_code="INTERNAL_ERROR", message="httpx 未安装（pip install httpx）",
            fetched_at=S.utcnow_iso(), latency_ms=0)

    def _fail(status, code, msg, canonical=url, content_type=None, http_status=None,
              text=""):
        return S.FetchOutcome(
            original_url=url, canonical_url=canonical, status=status,
            content_text=text, content_hash=S.content_hash(text),
            content_type=content_type, http_status=http_status,
            error_code=code, message=msg, fetched_at=S.utcnow_iso(),
            latency_ms=int((time.perf_counter() - t0) * 1000))

    current = url
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout_s) as client:
            for _ in range(max_redirects + 1):
                _validate_url(current)
                resp = client.get(current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED",
                                     "重定向缺少 location", canonical=current,
                                     http_status=resp.status_code)
                    current = urllib.parse.urljoin(current, location)
                    continue
                # 非重定向：读取正文（限流）
                content_type = resp.headers.get("content-type")
                media = content_type.split(";")[0].strip().lower() if content_type else ""
                if not media.startswith(_ALLOWED_CONTENT_TYPES):
                    return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED",
                                 f"内容类型不允许: {content_type!r}",
                                 canonical=str(resp.url), content_type=content_type,
                                 http_status=resp.status_code)
                if resp.status_code >= 400:
                    return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED",
                                 f"HTTP {resp.status_code}",
                                 canonical=str(resp.url), content_type=content_type,
                                 http_status=resp.status_code)
                body = resp.read()
                if len(body) > max_bytes:
                    return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED",
                                 f"响应超过大小上限 {max_bytes} 字节（实际 {len(body)}）",
                                 canonical=str(resp.url), content_type=content_type,
                                 http_status=resp.status_code)
                text = _extract_text(content_type, body)
                if not text:
                    return _fail("EMPTY", "EXTERNAL_CONTENT_EMPTY", "正文抽取为空",
                                 canonical=str(resp.url), content_type=content_type,
                                 http_status=resp.status_code)
                return S.FetchOutcome(
                    original_url=url, canonical_url=str(resp.url), status="SUCCESS",
                    content_text=text, content_hash=S.content_hash(text),
                    content_type=content_type, http_status=resp.status_code,
                    error_code=None, message=None, fetched_at=S.utcnow_iso(),
                    latency_ms=int((time.perf_counter() - t0) * 1000))
            raise _TooManyRedirects()
    except _UntrustedSource as e:
        return _fail("FATAL_ERROR", "SOURCE_UNTRUSTED", str(e))
    except _TooManyRedirects:
        return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED",
                     f"重定向超过 {max_redirects} 次")
    except _Blocked as e:
        return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED", str(e))
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
        return _fail("RETRYABLE_ERROR", "TOOL_TIMEOUT", f"{type(e).__name__}: {e}")
    except httpx.HTTPError as e:
        return _fail("FATAL_ERROR", "EXTERNAL_FETCH_BLOCKED", f"{type(e).__name__}: {e}")
    except Exception as e:
        logger.warning("抓取异常: %s", e, exc_info=True)
        return _fail("FATAL_ERROR", "INTERNAL_ERROR", f"{type(e).__name__}: {e}")


def _outcome_to_dict(o: S.FetchOutcome) -> dict:
    return {
        "original_url": o.original_url,
        "canonical_url": o.canonical_url,
        "status": o.status,
        "error_code": o.error_code,
        "message": o.message,
        "content_type": o.content_type,
        "http_status": o.http_status,
        "content_hash": o.content_hash,
        "fetched_at": o.fetched_at,
        "latency_ms": o.latency_ms,
        "content_text_preview": o.content_text[:500],
        "content_length": len(o.content_text),
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m external_v2.fetch")
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    outcome = fetch_external(args.url)
    print(json.dumps(_outcome_to_dict(outcome), ensure_ascii=False, indent=2))
    return 0 if outcome.status in ("SUCCESS", "EMPTY") else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
