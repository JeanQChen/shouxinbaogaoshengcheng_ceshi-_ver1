"""Anthropic API 封装，通过 DeepSeek 兼容接口调用。

所有 LLM 调用必须经过此模块，不得在 agent 内部直接创建 client。
每次 LLM 调用自动落盘到 logs/llm/。
"""

from anthropic import Anthropic
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL


def get_client() -> Anthropic:
    """返回已配置的 Anthropic client（指向 DeepSeek base_url）。"""
    raise NotImplementedError


def chat(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
) -> str:
    """单轮对话，返回 LLM 回复文本。

    自动落盘日志到 logs/llm/。
    """
    raise NotImplementedError


def chat_stream(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
):
    """流式对话，yield text delta。"""
    raise NotImplementedError


def load_prompt(name: str) -> str:
    """从 llm/prompts/<name>.txt 加载 prompt 模板。"""
    raise NotImplementedError
