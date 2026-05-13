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
