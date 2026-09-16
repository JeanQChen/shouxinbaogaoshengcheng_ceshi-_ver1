"""只读调试：§七 fixture（seed「营业收入构成详见下表。」）在 v13 扩读链下的逐步骤事实。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evals.test_context_expansion import _make_db, _request, _seed  # noqa: E402
from harness.context_expansion import (  # noqa: E402
    detect_reference_occurrences,
    expand,
)
from harness.evidence_reader import register_bounded_evidence_tool  # noqa: E402
from harness.evidence_reader import register_resolve_seed_identity_tool  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402

_TEXT = "营业收入构成详见下表。"
_HDR7 = ("项目    本期金额    上期金额", "产品    收入    收入")
_TABLE7 = "\n".join(("营业收入构成表", "单位：万元") + _HDR7 +
                    ("动力电池系统    1,000    900",))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for o in detect_reference_occurrences(_TEXT):
        print("occ:", o.to_dict(), "request_target=", o.request_target)
    with tempfile.TemporaryDirectory() as td:
        blocks = [
            (5, 0, ["主营业务分析"], "paragraph", _TEXT),
            (5, 1, ["主营业务分析"], "paragraph", _TABLE7),
        ]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=0, text=_TEXT)
        res = expand(_request(seed, ("explicit_reference",)), registry, run_id="t7")
        print("adopted:", [b.evidence_id for b in res.adopted])
        print("budget_consumed:", json.dumps(res.budget_consumed, ensure_ascii=False))
        print("stop_reason:", res.stop_reason)
        for st in res.trace:
            print("step:", json.dumps({
                "direction": getattr(st, "direction", None),
                "step_index": getattr(st, "step_index", None),
                "tool": getattr(getattr(st, "call", None), "tool_name", None),
                "arguments": getattr(getattr(st, "call", None), "arguments", None),
                "outputs": getattr(st, "outputs", None),
                "stop_reason": getattr(st, "stop_reason", None),
                "message": getattr(st, "message", None),
            }, ensure_ascii=False))


if __name__ == "__main__":
    main()
