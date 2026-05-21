"""Web 搜索封装。

当前使用 DuckDuckGo Instant Answer API（免费，无需 API key）。
日后可换 tavily-python，改一行。

用法: python -m external.web_search "宁德时代 行业地位"
"""

import json
import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def search_web(query: str, max_results: int = 8) -> str:
    """搜索互联网，返回 Markdown 格式的搜索结果摘要。

    用于 agent 的互联网检索输入。
    """
    results = _search_ddg(query, max_results)
    if not results:
        return f"（未检索到与「{query}」相关的互联网信息。）"

    lines = [f"搜索结果（{len(results)} 条）："]
    for i, r in enumerate(results, 1):
        lines.append(f"- [{i}] **{r['title']}**")
        if r.get("snippet"):
            lines.append(f"  {r['snippet'][:300]}")
        if r.get("url"):
            lines.append(f"  来源: {r['url']}")
    return "\n".join(lines)


def _search_ddg(query: str, max_results: int = 8) -> list[dict]:
    """通过 DuckDuckGo Instant Answer API 搜索。"""
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "no_html": 1,
        "skip_disambig": 1,
        "t": "credit_report_demo",
    })

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "credit-report-demo/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.warning("DuckDuckGo search failed for query '%.40s'", query, exc_info=True)
        return []

    results: list[dict] = []

    # 1. Abstract (main answer)
    abstract = data.get("Abstract", "")
    abstract_url = data.get("AbstractURL", "")
    if abstract:
        results.append({
            "title": data.get("Heading", query),
            "snippet": abstract,
            "url": abstract_url,
        })

    # 2. Related topics
    for topic in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict):
            results.append({
                "title": topic.get("Text", "").split(" - ")[0] if " - " in topic.get("Text", "") else topic.get("Text", ""),
                "snippet": topic.get("Text", ""),
                "url": topic.get("FirstURL", ""),
            })

    # 3. Infobox content (structured data)
    infobox = data.get("Infobox", {})
    if infobox and isinstance(infobox, dict):
        content = "; ".join(f"{k}: {v}" for k, v in infobox.get("content", []))
        if content:
            results.append({
                "title": infobox.get("meta", {}).get("name", "信息框"),
                "snippet": content[:500],
                "url": "",
            })

    return results[:max_results]


# ── CLI ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    query = sys.argv[1] if len(sys.argv) > 1 else "宁德时代 锂电池 行业地位 市场份额"
    print(f"Searching: {query}\n")
    print(search_web(query))
