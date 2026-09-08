"""Phase 3 Tool Layer 外部能力适配器（博查搜索 / 安全正文获取 / 不可变快照）。

把 external_v2 层（search / fetch / store）封装为 Registry 可注册的 executor。
executor 是 `arguments -> ToolResult` 的纯函数（不落盘 audit、不重试——由 Registry 承担），
但会真实调用 external_v2 后端并产生可回查引用。

对齐（PHASE3 任务书 §6.4～§6.6 + 用户修订「Phase 3 唯一启用博查」）：
- `search_external_sources` → external_v2.search.search_external（provider 恒为 bocha，
  Tavily 不参与运行时/fallback/验收）；
- `fetch_external_content` → external_v2.fetch.fetch_external（SSRF/重定向/类型/大小/抽取，
  含 PDF 文本层）；
- `snapshot_external_source` → external_v2.store.store_snapshot（不可变快照，content_hash
  幂等复用），快照字段含 file_hash/page_count（PDF 按字节审计）。

外部搜索结果摘要（snippet）绝不冒充已抓取正文：fetch 的 content_text 才是正文，
snapshot 的 content_text 与 fetch 的 content_text 同源。
CLI: 见 tools/adapters.py 的 search-external / fetch-external / snapshot-external 子命令。
"""

from __future__ import annotations

import uuid

from tools import contracts as C

# ---------------------------------------------------------------------------
# ToolSpec 定义
# ---------------------------------------------------------------------------

SEARCH_EXTERNAL_SPEC = C.ToolSpec(
    name="search_external_sources", version="v1",
    description="博查 Web Search 外部检索（provider 恒为 bocha，返回标题/URL/摘要/发布时间/来源级别）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "freshness": {"type": "string"},
            "include": {"type": "string"},
            "exclude": {"type": "string"},
            "summary": {"type": "boolean"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("EXTERNAL_RESEARCH",),
    max_results=20, timeout_ms=30000, retry_policy="retryable_only", cost_class="external",
)

FETCH_EXTERNAL_SPEC = C.ToolSpec(
    name="fetch_external_content", version="v1",
    description="安全抓取并抽取网页/电子 PDF 正文（SSRF/重定向/类型/大小校验，返回 content_hash）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "minLength": 1},
            "timeout_s": {"type": "number", "minimum": 1},
            "max_bytes": {"type": "integer", "minimum": 1},
            "max_redirects": {"type": "integer", "minimum": 0},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("EXTERNAL_RESEARCH",),
    max_results=1, timeout_ms=30000, retry_policy="retryable_only", cost_class="external",
)

SNAPSHOT_EXTERNAL_SPEC = C.ToolSpec(
    name="snapshot_external_source", version="v1",
    description="把已抓取正文固化为不可变外部来源快照（content_hash 幂等复用）",
    input_schema={
        "type": "object", "additionalProperties": False,
        "required": ["company_id", "canonical_url", "content_text"],
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "canonical_url": {"type": "string", "minLength": 1},
            "content_text": {"type": "string", "minLength": 1},
            "original_url": {"type": "string"},
            "provider": {"type": "string"},
            "query": {"type": "string"},
            "title": {"type": "string"},
            "snippet": {"type": "string"},
            "published_at": {"type": "string"},
            "content_type": {"type": "string"},
            "http_status": {"type": "integer"},
            "content_hash": {"type": "string"},
            "source_grade": {"type": "string"},
            "file_hash": {"type": "string"},
            "page_count": {"type": "integer"},
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("EXTERNAL_RESEARCH",),
    max_results=1, timeout_ms=5000, retry_policy="none", cost_class="external",
)


# ---------------------------------------------------------------------------
# 状态翻译
# ---------------------------------------------------------------------------

def _status_map(external_status: str) -> str:
    """external_v2 结果状态 → ToolResult 五态（枚举同构，直接透传，未知兜底 FATAL）。"""
    return external_status if external_status in C.TOOL_STATUSES else "FATAL_ERROR"


def _result(status: str, tool_name: str, data: dict, *, error_code: str | None,
            message: str | None, external_snapshot_ids: list[str] | None = None) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=tool_name, tool_version="", status=status, data=data,
        external_snapshot_ids=external_snapshot_ids or [],
        error_code=error_code, message=message,
        retryable=(status == "RETRYABLE_ERROR"), trace_id=uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# executors
# ---------------------------------------------------------------------------

def _search_external_sources_executor(args: dict) -> C.ToolResult:
    from external_v2 import search as esearch

    out = esearch.search_external(
        args["query"],
        limit=args.get("limit", 5),
        freshness=args.get("freshness"),
        include=args.get("include"),
        exclude=args.get("exclude"),
        summary=args.get("summary", True),
    )
    data = {
        "provider": out.provider,
        "provider_request_id": out.provider_request_id,
        "latency_ms": out.latency_ms,
        "result_count": len(out.results),
        "results": [
            {
                "rank": r.rank,
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet[:300],
                "published_at": r.published_at,
                "source_name": r.source_name,
                "source_grade": r.source_grade,
            }
            for r in out.results
        ],
    }
    return _result(_status_map(out.status), "search_external_sources", data,
                   error_code=out.error_code, message=out.message)


def _fetch_external_content_executor(args: dict) -> C.ToolResult:
    from external_v2 import fetch as efetch

    out = efetch.fetch_external(
        args["url"],
        timeout_s=args.get("timeout_s"),
        max_bytes=args.get("max_bytes"),
        max_redirects=args.get("max_redirects"),
    )
    data = {
        "original_url": out.original_url,
        "canonical_url": out.canonical_url,
        "content_type": out.content_type,
        "http_status": out.http_status,
        "content_hash": out.content_hash,
        "file_hash": out.file_hash,
        "page_count": out.page_count,
        "content_length": len(out.content_text),
        "content_text": out.content_text,
    }
    return _result(_status_map(out.status), "fetch_external_content", data,
                   error_code=out.error_code, message=out.message)


def _snapshot_external_source_executor(args: dict) -> C.ToolResult:
    from external_v2 import schema as S
    from external_v2 import store as extstore

    content_text = args["content_text"]
    content_hash = args.get("content_hash") or S.content_hash(content_text)

    # 懒初始化（尊重调用方预先 set 的 DB 路径；仅当未初始化时才落到默认路径）。
    if getattr(extstore, "_db_path", None) is None:
        extstore.init_db()

    snap = S.ExternalSourceSnapshot(
        source_snapshot_id="",  # store 落库时分配
        canonical_url=args["canonical_url"],
        original_url=args.get("original_url") or args["canonical_url"],
        provider=args.get("provider") or "bocha",
        query=args.get("query") or "",
        title=args.get("title") or "",
        snippet=args.get("snippet") or "",
        published_at=args.get("published_at"),
        fetched_at=S.utcnow_iso(),
        content_type=args.get("content_type"),
        http_status=args.get("http_status"),
        content_text=content_text,
        content_hash=content_hash,
        source_grade=args.get("source_grade"),
        status="SNAPSHOTTED",
        error_code=None,
        retrieval_metadata={
            "file_hash": args.get("file_hash") or "",
            "page_count": args.get("page_count") or 0,
        },
    )
    stored = extstore.store_snapshot(snap, args["company_id"])
    data = {
        "source_snapshot_id": stored.source_snapshot_id,
        "canonical_url": stored.canonical_url,
        "content_version": stored.content_version,
        "content_hash": stored.content_hash,
        "status": stored.status,
    }
    return _result("SUCCESS", "snapshot_external_source", data, error_code=None,
                   message=None, external_snapshot_ids=[stored.source_snapshot_id])


# ---------------------------------------------------------------------------
# Registry 注册
# ---------------------------------------------------------------------------

def register_external_tools(registry) -> None:
    """把 3 个外部工具注册进 Registry（由 tools.adapters.build_default_registry 调用）。"""
    registry.register(SEARCH_EXTERNAL_SPEC, _search_external_sources_executor)
    registry.register(FETCH_EXTERNAL_SPEC, _fetch_external_content_executor)
    registry.register(SNAPSHOT_EXTERNAL_SPEC, _snapshot_external_source_executor)
