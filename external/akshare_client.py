"""akshare 封装 — A 股公开数据查询。"""


def search_company(keyword: str) -> list[dict]:
    """按关键词搜索 A 股公司，返回匹配列表。

    每条记录含 stock_code, company_name, industry_code 等。
    """
    raise NotImplementedError


def get_company_info(stock_code: str) -> dict:
    """获取公司基本信息（全称、行业、注册地、注册资本等）。"""
    raise NotImplementedError
