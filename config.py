"""全局配置，从 .env 加载。"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM ---
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")

# --- Demo ---
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"

# --- External V2（互联网研究） ---
# Phase 3 唯一启用的搜索提供方为博查（Bocha Web Search）。
# 不读取 / 不要求 / 不检查 TAVILY_API_KEY；Tavily 不参与运行时、fallback 或验收。
EXTERNAL_SEARCH_PROVIDER: str = os.getenv("EXTERNAL_SEARCH_PROVIDER", "bocha")
BOCHA_API_KEY: str = os.getenv("BOCHA_API_KEY", "")
BOCHA_BASE_URL: str = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1/web-search")
EXTERNAL_FETCH_TIMEOUT_S: float = float(os.getenv("EXTERNAL_FETCH_TIMEOUT_S", "15"))
EXTERNAL_FETCH_MAX_BYTES: int = int(os.getenv("EXTERNAL_FETCH_MAX_BYTES", "5000000"))
EXTERNAL_FETCH_MAX_REDIRECTS: int = int(os.getenv("EXTERNAL_FETCH_MAX_REDIRECTS", "3"))
