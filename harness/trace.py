"""Phase 3 Batch B 研究事件轨迹（JSONL append-only，无业务计算）。

每次研究事件（路由 / 动作选择 / 工具调用 / 预算检查 / 答案产出 / 停止）追加一行到
`logs/harness/<run_id>/<question_id>.jsonl`，供 smoke 报告、Actual-Path 评测与调试
逐题重建。写失败必须显式抛错（fail-closed），不静默吞掉（CLAUDE.md 反模式）。

CLI: python -m harness.trace --self-check
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TRACE_DIR = Path("logs/harness")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def emit(run_id: str, question_id: str, event_type: str, payload: dict) -> None:
    """追加一条事件到该题的 JSONL 轨迹（自动建目录）。

    写失败 raise（TRACE_WRITE_FAILED），由调用方决定是否中止本题。
    """
    if not run_id or not question_id:
        raise ValueError("run_id / question_id 不能为空")
    try:
        question_dir = TRACE_DIR / run_id
        question_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": _utcnow(),
            "run_id": run_id,
            "question_id": question_id,
            "event": event_type,
            **payload,
        }
        path = question_dir / f"{question_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as e:  # noqa: BLE001 —— 必须显式抛错，不吞
        raise RuntimeError(f"trace 写入失败 (run={run_id}, q={question_id}): {e}") from e


def trace_path(run_id: str, question_id: str) -> Path:
    """返回该题的轨迹文件路径（不创建）。"""
    return TRACE_DIR / run_id / f"{question_id}.jsonl"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import sys
    import tempfile

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.trace", description="研究事件轨迹自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        run = f"self_{tempfile.mkdtemp(prefix='tr').split('\\')[-1]}"
        emit(run, "q1", "TEST_EVENT", {"x": 1})
        p = trace_path(run, "q1")
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        rec = json.loads(lines[0])
        print(json.dumps({
            "wrote": p.exists(),
            "lines": len(lines),
            "event": rec.get("event"),
            "has_timestamp": bool(rec.get("timestamp")),
            "run_id": rec.get("run_id"),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
