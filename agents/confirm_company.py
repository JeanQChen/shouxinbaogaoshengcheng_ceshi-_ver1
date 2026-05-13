"""Agent 0: 公司确认 — 调用 akshare 查公司全称/股票代码/行业代码。"""


def run(company_name: str) -> dict:
    """搜索公司并返回确认信息。

    Returns:
        {
            "company_name": "宁德时代新能源科技股份有限公司",
            "stock_code": "300750",
            "industry_code": "C38",
            "industry_name": "电气机械和器材制造业",
        }
    """
    raise NotImplementedError
