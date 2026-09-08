"""Anthropic API 封装，通过 DeepSeek 兼容接口调用。

所有 LLM 调用必须经过此模块，不得在 agent 内部直接创建 client。
每次 LLM 调用自动落盘到 logs/llm/（含 token / latency / call_id / finish_reason /
prompt_version），供 Phase 3 Batch B Harness 的可观测性要求使用。

接口契约（向后兼容）：
- `chat(...) -> str` 保持不变，内部委托 `chat_with_usage(...).text`；
- `chat_with_usage(...) -> LLMResponse` 返回带 usage / latency / call_id 的结构化结果；
- usage 缺失（provider 未返回）记 None，绝不记 0。

CLI:
  python -m llm.client --self-check
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

_client: Anthropic | None = None
LOGS_DIR = Path("logs/llm")


@dataclass(frozen=True)
class LLMResponse:
    """一次 LLM 调用的结构化结果（usage 兼容 + 审计元数据）。"""

    text: str
    input_tokens: int | None       # provider 未返回 usage → None，不记 0
    output_tokens: int | None
    latency_ms: int
    model: str
    call_id: str
    finish_reason: str | None


def get_client() -> Anthropic:
    """返回已配置的 Anthropic client（指向 DeepSeek base_url）。"""
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not configured. Check your .env file.")
        _client = Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def _new_call_id() -> str:
    return uuid.uuid4().hex


def chat_with_usage(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    prompt_version: str | None = None,
) -> LLMResponse:
    """单轮对话，返回结构化 LLMResponse（含 usage/latency/call_id）。自动落盘日志。"""
    client = get_client()
    model_name = model or LLM_MODEL
    call_id = _new_call_id()
    t0 = time.perf_counter()

    system_params = [{"type": "text", "text": system}] if system else None

    resp = client.messages.create(
        model=model_name,
        max_tokens=max_tokens,
        system=system_params,
        messages=messages,
    )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # DeepSeek 可能返回 ThinkingBlock，只取 TextBlock。
    completion = ""
    for block in resp.content:
        if hasattr(block, "text") and block.text:
            completion = block.text
            break

    # usage 可能缺失（provider 未返回），保守记 None，不记 0。
    usage = getattr(resp, "usage", None)
    input_tokens = usage.input_tokens if usage is not None else None
    output_tokens = usage.output_tokens if usage is not None else None
    finish_reason = getattr(resp, "stop_reason", None)

    result = LLMResponse(
        text=completion,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=elapsed_ms,
        model=model_name,
        call_id=call_id,
        finish_reason=finish_reason,
    )

    _log_llm_call(result, messages, system, prompt_version)

    logger.info("LLM call: model=%s input=%s output=%s latency=%dms call_id=%s",
                model_name, input_tokens, output_tokens, elapsed_ms, call_id)

    return result


def chat(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """单轮对话，返回 LLM 回复文本。自动落盘日志到 logs/llm/（向后兼容入口）。"""
    return chat_with_usage(messages, system=system, model=model,
                           max_tokens=max_tokens).text


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
    result: LLMResponse,
    messages: list[dict],
    system: str | None,
    prompt_version: str | None,
) -> None:
    """落盘 LLM 调用日志到 logs/llm/（微秒时间戳 + call_id 唯一文件名，防同秒覆盖）。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        record = {
            "timestamp": ts,
            "call_id": result.call_id,
            "model": result.model,
            "prompt_version": prompt_version,
            "finish_reason": result.finish_reason,
            "system": system,
            "messages": messages,
            "completion": result.text,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "elapsed_s": round(result.latency_ms / 1000.0, 3),
        }
        filepath = LOGS_DIR / f"{ts}__{result.call_id}.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write LLM log", exc_info=True)


def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m llm.client", description="LLM 调用层自检")
    parser.add_argument("--self-check", action="store_true",
                        help="打印模块自检摘要（不发起真实调用）")
    args = parser.parse_args(argv)

    if args.self_check:
        summary = {
            "model": LLM_MODEL,
            "api_key_configured": bool(DEEPSEEK_API_KEY),
            "LLMResponse_fields": list(LLMResponse.__dataclass_fields__),
            "log_dir": str(LOGS_DIR),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
