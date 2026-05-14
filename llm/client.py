"""Anthropic API 封装，通过 DeepSeek 兼容接口调用。

所有 LLM 调用必须经过此模块，不得在 agent 内部直接创建 client。
每次 LLM 调用自动落盘到 logs/llm/。
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

_client: Anthropic | None = None
LOGS_DIR = Path("logs/llm")


def get_client() -> Anthropic:
    """返回已配置的 Anthropic client（指向 DeepSeek base_url）。"""
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not configured. Check your .env file.")
        _client = Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def chat(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """单轮对话，返回 LLM 回复文本。自动落盘日志到 logs/llm/。"""
    client = get_client()
    model_name = model or LLM_MODEL
    t0 = time.time()

    system_params = [{"type": "text", "text": system}] if system else None

    resp = client.messages.create(
        model=model_name,
        max_tokens=max_tokens,
        system=system_params,
        messages=messages,
    )

    elapsed = time.time() - t0
    # DeepSeek 可能返回 ThinkingBlock，只取 TextBlock
    completion = ""
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            completion = block.text
            break
    input_tokens = resp.usage.input_tokens
    output_tokens = resp.usage.output_tokens

    _log_llm_call(model_name, messages, system, completion, input_tokens, output_tokens, elapsed)

    logger.info("LLM call: model=%s input=%d output=%d elapsed=%.1fs",
                model_name, input_tokens, output_tokens, elapsed)

    return completion


def chat_stream(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
):
    """流式对话，yield text delta。"""
    client = get_client()
    model_name = model or LLM_MODEL

    system_params = [{"type": "text", "text": system}] if system else None

    with client.messages.stream(
        model=model_name,
        max_tokens=max_tokens,
        system=system_params,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def load_prompt(name: str) -> str:
    """从 llm/prompts/<name>.txt 加载 prompt 模板。"""
    prompt_path = Path(__file__).resolve().parent / "prompts" / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _log_llm_call(
    model: str,
    messages: list[dict],
    system: str | None,
    completion: str,
    input_tokens: int,
    output_tokens: int,
    elapsed: float,
) -> None:
    """落盘 LLM 调用日志到 logs/llm/。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        record = {
            "timestamp": ts,
            "model": model,
            "system": system,
            "messages": messages,
            "completion": completion,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "elapsed_s": round(elapsed, 2),
        }
        filepath = LOGS_DIR / f"{ts}.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write LLM log", exc_info=True)
