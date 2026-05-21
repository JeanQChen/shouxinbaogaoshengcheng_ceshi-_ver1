"""akshare 封装 — A 股公开数据查询。

所有函数返回结构化数据，不在此模块做业务判断。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)


def search_company(keyword: str) -> list[dict]:
    """按关键词搜索 A 股公司，返回匹配列表。

    每条记录含 stock_code, company_name, industry_code 等。
    """
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        logger.warning("akshare not installed, returning empty")
        return []

    try:
        df = ak.stock_info_a_code_name()
        mask = df["name"].str.contains(keyword, na=False)
        results = df[mask].head(10)
        return [
            {
                "stock_code": str(r.get("code", "")),
                "company_name": str(r.get("name", "")),
            }
            for _, r in results.iterrows()
        ]
    except Exception:
        logger.warning("search_company failed", exc_info=True)
        return []


def get_company_info(stock_code: str) -> dict:
    """获取公司基本信息（全称、行业、注册地等）。

    使用东方财富个股信息接口。
    """
    try:
        import akshare as ak
    except ImportError:
        return {}

    try:
        df = ak.stock_individual_info_em(symbol=stock_code)
        info: dict = {}
        for _, row in df.iterrows():
            key = str(row.get("item", ""))
            val = str(row.get("value", ""))
            info[key] = val
        return info
    except Exception:
        logger.warning("get_company_info failed for %s", stock_code, exc_info=True)
        return {}


def get_stock_news(stock_code: str, limit: int = 10) -> list[dict]:
    """获取个股近期新闻（东方财富来源）。"""
    try:
        import akshare as ak
    except ImportError:
        return []

    try:
        import pandas as pd
        _old_storage = pd.options.mode.string_storage
        pd.options.mode.string_storage = "python"
        try:
            df = ak.stock_news_em(symbol=stock_code)
        finally:
            pd.options.mode.string_storage = _old_storage
        if df is None or df.empty:
            return []
        results = df.head(limit)
        return [
            {
                "title": str(r.get("title", "")),
                "time": str(r.get("public_time", "")),
                "source": str(r.get("source", "")),
            }
            for _, r in results.iterrows()
        ]
    except Exception:
        logger.warning("get_stock_news failed for %s", stock_code, exc_info=True)
        return []


def get_industry_news(industry: str, limit: int = 10) -> list[dict]:
    """搜索行业相关新闻。"""
    try:
        import akshare as ak
    except ImportError:
        return []

    try:
        import pandas as pd
        _old_storage = pd.options.mode.string_storage
        pd.options.mode.string_storage = "python"
        try:
            df = ak.stock_news_em(symbol=industry)
        finally:
            pd.options.mode.string_storage = _old_storage
        if df is None or df.empty:
            return []
        results = df.head(limit)
        return [
            {
                "title": str(r.get("title", "")),
                "time": str(r.get("public_time", "")),
                "source": str(r.get("source", "")),
            }
            for _, r in results.iterrows()
        ]
    except Exception:
        logger.warning("get_industry_news failed for %s", industry, exc_info=True)
        return []


# ── CLI ──

if __name__ == "__main__":
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    code = sys.argv[1] if len(sys.argv) > 1 else "300750"

    print(f"=== Company Info: {code} ===")
    info = get_company_info(code)
    print(_json.dumps(info, ensure_ascii=False, indent=2))

    print(f"\n=== Stock News: {code} ===")
    for n in get_stock_news(code, 5):
        print(f"  [{n['time']}] {n['title']} ({n['source']})")
