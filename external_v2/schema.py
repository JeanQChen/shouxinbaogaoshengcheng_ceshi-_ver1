"""External V2 公共契约（dataclass + 枚举白名单 + 纯函数）。

本模块只定义声明式数据结构与纯函数，不含 I/O、不调用 provider、不发起网络。
providers / search / fetch / store 共同引用同一份字段语义。

对齐（PHASE3_TOOL_HARNESS_DEVELOPMENT_TASK.md §6.4～§6.5）：
- 搜索 provider 可配置，`EXTERNAL_SEARCH_PROVIDER=tavily` + `TAVILY_API_KEY`，不硬编码 key；
- 无 key / provider 不可用 → 明确 `EXTERNAL_SEARCH_UNAVAILABLE`，不得静默换假数据；
- 来源分级复用 `contracts.schema.SOURCE_GRADES`（A/B/C/D），本层仅依据域名规则形成
  「候选级别」（candidate grade），最终章节语义留给 Phase 4；
- 网页正文是不可信数据，本层只做字节/文本搬运与哈希，不执行、不解读为指令。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from contracts.schema import SOURCE_GRADES  # 复用 A/B/C/D 单一定义

# ---------------------------------------------------------------------------
# 枚举白名单
# ---------------------------------------------------------------------------

# 搜索/正文/快照结果状态（与 ToolResult 五态对齐，供上层工具适配器直接翻译）。
EXTERNAL_STATUSES = (
    "SUCCESS",
    "PARTIAL",
    "EMPTY",
    "RETRYABLE_ERROR",
    "FATAL_ERROR",
)

# 本层可产生的错误码（与 tools.contracts.TOOL_ERROR_CODES 同名子集对齐；
# 博查 provider 鉴权/限流/服务端/网络/坏响应等做明确分类，见 providers.BochaProvider）。
EXTERNAL_ERROR_CODES = (
    "EXTERNAL_SEARCH_UNAVAILABLE",   # 缺 key / 未知 provider / provider 未启用
    "EXTERNAL_AUTH_FAILED",          # 401/403 鉴权失败（key 存在但无效）
    "EXTERNAL_RATE_LIMITED",         # 429 限流
    "EXTERNAL_SERVER_ERROR",         # 5xx 服务端异常
    "EXTERNAL_NETWORK_ERROR",        # 连接/网络失败（非超时）
    "EXTERNAL_BAD_RESPONSE",         # 非法 JSON / 响应字段缺失
    "EXTERNAL_FETCH_BLOCKED",
    "EXTERNAL_CONTENT_EMPTY",
    "EXTERNAL_SNAPSHOT_ERROR",
    "PDF_TEXT_UNAVAILABLE",          # 电子 PDF 无文本层/文本质量不合格
    "SOURCE_UNTRUSTED",
    "TOOL_TIMEOUT",
    "INTERNAL_ERROR",
)

# 快照状态（区别于结果状态：描述一条来源快照本身的生命周期）。
SNAPSHOT_STATUSES = (
    "SNAPSHOTTED",        # 正文已成功取得并按 content_hash 固化
    "FETCH_BLOCKED",      # robots / 登录墙 / 私网 / 内容类型受限，未抓正文
    "FETCH_FAILED",       # 网络/超时/HTTP 错误，未抓正文
    "CONTENT_EMPTY",      # 抓取成功但正文为空
    "UNTRUSTED",          # SSRF 或来源安全校验未通过，拒绝抓取
)

# 候选来源分级规则版本（domain → 候选级别；变更需递增）。
GRADE_RULE_VERSION = "v1"


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    """UTC 时间戳（ISO 8601），供各模块统一落盘时间口径。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(text: str) -> str:
    """正文内容哈希（sha256），用于不可变快照去重与幂等复用。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    """原始字节哈希（sha256），用于 PDF 等按文件字节审计。"""
    return hashlib.sha256(data).hexdigest()


# 候选来源分级：仅依据域名规则，A/B/C 白名单之外一律 D（来源不明）。
# 这是「候选级别」，不是最终来源质量判断；Phase 4 才做章节语义级判断。
_GOV_DOMAINS = (
    "gov.cn",
    "csrc.gov.cn",
    "sse.com.cn",
    "szse.cn",
    "bse.cn",
    "ndrc.gov.cn",
    "pbc.gov.cn",
    "miit.gov.cn",
    "samr.gov.cn",
    "stats.gov.cn",
    "cma.gov.cn",
)
_OFFICIAL_DOMAINS = (
    "cninfo.com.cn",   # 巨潮资讯网（法定信息披露平台）
)
_MEDIA_DOMAINS = (
    "eastmoney.com",
    "10jqka.com.cn",
    "sina.com.cn",
    "163.com",
    "thepaper.cn",
    "yicai.com",
    "cls.cn",
    "stcn.com",
    "caixin.com",
    "hexun.com",
    "xueqiu.com",
    "cnstock.com",
)


def grade_source(url: str) -> str:
    """按域名给出候选来源级别（A/B/C/D）。

    A=监管/政府/交易所；B=法定披露平台/行业协会；C=券商/财经媒体；D=来源不明。
    域名无法识别时保守返回 D，不向上猜测。
    """
    host = _extract_host(url).lower()
    if not host:
        return "D"
    if any(host == d or host.endswith("." + d) for d in _GOV_DOMAINS):
        return "A"
    if any(host == d or host.endswith("." + d) for d in _OFFICIAL_DOMAINS):
        return "B"
    if any(host == d or host.endswith("." + d) for d in _MEDIA_DOMAINS):
        return "C"
    return "D"


def _extract_host(url: str) -> str:
    """从 URL 提取 host（不依赖第三方库，保守处理异常输入）。"""
    s = (url or "").strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.split("#", 1)[0]
    # 去掉端口与 userinfo。
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    if ":" in s and not s.startswith("["):
        s = s.rsplit(":", 1)[0]
    return s.strip("[] ")


# ---------------------------------------------------------------------------
# 公共 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchResult:
    """一条外部搜索结果（搜索阶段，尚未抓取正文）。"""

    title: str
    url: str
    snippet: str
    published_at: str | None
    source_name: str | None
    source_grade: str | None       # 候选级别 A/B/C/D（grade_source）
    provider: str
    rank: int
    raw: dict = field(default_factory=dict)   # provider 原始负载（审计用）


@dataclass(frozen=True)
class SearchOutcome:
    """一次外部搜索的完整结果（状态 + 结果列表 + 错误码）。

    provider_request_id 为搜索提供方返回的请求 ID（博查 log_id），用于审计关联；
    提供方未返回时为 None。
    """

    query: str
    provider: str
    status: str                     # SUCCESS | EMPTY | RETRYABLE_ERROR | FATAL_ERROR
    results: tuple[SearchResult, ...]
    error_code: str | None
    message: str | None
    fetched_at: str
    latency_ms: int
    provider_request_id: str | None = None


@dataclass(frozen=True)
class FetchOutcome:
    """一次安全正文抓取 + 抽取的完整结果。

    SSRF 拒绝（私网/环回/file/重定向到私网）→ SOURCE_UNTRUSTED；
    robots/登录墙/内容类型/大小/重定向上限 → EXTERNAL_FETCH_BLOCKED；
    正文为空 → EXTERNAL_CONTENT_EMPTY（EMPTY，合法结果）；超时/网络 → TOOL_TIMEOUT（可重试）；
    PDF 无文本层/文本质量不合格 → PDF_TEXT_UNAVAILABLE。

    content_hash 为抽取正文的 sha256；file_hash 为原始响应字节的 sha256（HTML/PDF 均记录，
    PDF 用于按文件字节审计，HTML 亦保留原始字节指纹）。
    """

    original_url: str
    canonical_url: str              # 重定向后的最终 URL
    status: str                     # SUCCESS | EMPTY | RETRYABLE_ERROR | FATAL_ERROR
    content_text: str
    content_hash: str
    content_type: str | None
    http_status: int | None
    error_code: str | None
    message: str | None
    fetched_at: str
    latency_ms: int
    file_hash: str = ""             # 原始响应字节 sha256（PDF 尤其重要）
    page_count: int = 0             # PDF 提取的页数（HTML 为 0）


@dataclass(frozen=True)
class ExternalSourceSnapshot:
    """一条不可变外部来源快照（任务书 §6.5 最低字段）。

    content_hash 相同 → 幂等复用；内容变化 → 新快照版本。历史快照不可覆盖。
    """

    source_snapshot_id: str
    canonical_url: str
    original_url: str
    provider: str
    query: str
    title: str
    snippet: str
    published_at: str | None
    fetched_at: str
    content_type: str | None
    http_status: int | None
    content_text: str
    content_hash: str
    source_grade: str | None
    status: str                     # SNAPSHOT_STATUSES 之一
    error_code: str | None
    retrieval_metadata: dict = field(default_factory=dict)
    # 存储附加字段（store 层写入；§6.5 最低字段之外的可选扩展）。
    company_id: str = ""
    content_version: int = 1
