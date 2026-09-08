"""Eval: llm.client —— LLMResponse / chat_with_usage / 唯一日志文件名（Phase 3 Batch B commit 1）。

用法: python -m evals.test_llm_client

断言（全 mock，不触真实 API）：
- LLMResponse dataclass 字段完整（text/input_tokens/output_tokens/latency_ms/model/call_id/finish_reason）；
- chat_with_usage 返回 usage / latency / call_id / finish_reason；
- provider 未返回 usage → input/output_tokens 为 None（绝不记 0）；
- chat 向后兼容：委托 chat_with_usage 并返回其 .text（str）；
- _log_llm_call 以 call_id 唯一文件名落盘（同秒不覆盖）；
- 日志记录含 prompt_version / finish_reason / latency_ms / call_id，且不含 API Key。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm.client as LC


class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResp:
    def __init__(self, text, usage, stop_reason):
        self.content = [_FakeBlock(text)]
        self.usage = usage
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, resp):
        self.resp = resp

    def create(self, **kwargs):
        return self.resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    tmp_log = Path(tempfile.mkdtemp(prefix="eval_llm_client_"))

    # ---- LLMResponse dataclass 字段 ----
    fields = set(LC.LLMResponse.__dataclass_fields__)
    expected_fields = {"text", "input_tokens", "output_tokens", "latency_ms",
                       "model", "call_id", "finish_reason"}
    check(fields == expected_fields, "LLMResponse 字段完整且无多余")

    # ---- chat_with_usage：正常 usage ----
    resp_ok = _FakeResp("hello", _FakeUsage(10, 5), "end_turn")
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        r = LC.chat_with_usage(
            [{"role": "user", "content": "hi"}], system="sys",
            prompt_version="research_action_v1")
    check(r.text == "hello", "chat_with_usage 返回 text")
    check(r.input_tokens == 10 and r.output_tokens == 5, "usage 正常返回 token 数")
    check(r.finish_reason == "end_turn", "finish_reason 正确捕获")
    check(r.model == LC.LLM_MODEL, "model 使用默认 LLM_MODEL")
    check(r.call_id != "" and r.latency_ms >= 0, "call_id/latency_ms 已填充")

    # ---- chat_with_usage：usage 缺失 → None ----
    resp_none = _FakeResp("x", None, None)
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_none)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        r2 = LC.chat_with_usage([{"role": "user", "content": "hi"}])
    check(r2.input_tokens is None and r2.output_tokens is None,
          "provider 未返回 usage → input/output_tokens 为 None（不记 0）")
    check(r2.finish_reason is None, "finish_reason 缺失 → None")

    # ---- chat 向后兼容：返回 str ----
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        txt = LC.chat([{"role": "user", "content": "hi"}])
    check(txt == "hello" and isinstance(txt, str), "chat 向后兼容返回 str（委托 chat_with_usage）")

    # ---- 唯一日志文件名：同秒不覆盖 ----
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        a = LC.chat_with_usage([{"role": "user", "content": "a"}], prompt_version="pv")
        b = LC.chat_with_usage([{"role": "user", "content": "b"}], prompt_version="pv")
    log_files = list(tmp_log.glob("*.jsonl"))
    check(len(log_files) >= 2 and a.call_id != b.call_id, "两次调用各落独立文件（不覆盖）")

    # ---- 日志内容含审计字段、不含 Key ----
    written = [json.loads(p.read_text(encoding="utf-8")) for p in log_files]
    rec = written[-1]
    check(rec["call_id"] != "" and rec["prompt_version"] == "pv",
          "日志含 call_id / prompt_version")
    check("finish_reason" in rec and "latency_ms" in rec,
          "日志含 finish_reason / latency_ms")
    serialized = json.dumps(written, ensure_ascii=False)
    check("DEEPSEEK_API_KEY" not in serialized and "sk-" not in serialized.lower(),
          "日志不含 API Key")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
