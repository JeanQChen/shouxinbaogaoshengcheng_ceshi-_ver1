"""只读取证：C.1 结构续表 frontier 为何未采纳（表结构签名/承接逐条打印）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.context_expansion import ExpansionBudget, expand  # noqa: E402
from harness.evidence_reader import (  # noqa: E402
    register_bounded_evidence_tool,
    register_resolve_seed_identity_tool,
)
from harness.table_structure import (  # noqa: E402
    continue_table_signals,
    open_table_signature,
)
from tools.registry import ToolRegistry  # noqa: E402

from evals.test_context_expansion import _make_db, _request, _seed  # noqa: E402

_TITLE = "主营业务收入构成表"
_HDR = ("项目    本期金额    上期金额", "产品    收入    收入")


def _flat(*rows: str) -> str:
    return "\n".join((_TITLE, "单位：万元") + _HDR + tuple(rows))


def _cont(*rows: str) -> str:
    """续页块：块首直接重排本表物理表头（跨页续表真实形状，无重复表题）。"""
    return "\n".join(_HDR + tuple(rows))


def main() -> None:
    seed_text = _flat("动力电池系统    1,000    900", "储能电池系统    800    700")
    cont_texts = [
        _cont("其他业务    200    180"),
        _cont("分部间抵销    -50    -40"),
        _cont("合计    2,000    1,780"),
        _cont("少数股东权益    30    25"),
    ]
    sig = open_table_signature(seed_text)
    print("seed signature =", json.dumps(sig, ensure_ascii=False, indent=2))
    for i, t in enumerate(cont_texts):
        print("-" * 70)
        print("cont[%d] continues =" % i,
              json.dumps(continue_table_signals(sig, t), ensure_ascii=False))
    with tempfile.TemporaryDirectory() as td:
        blocks = [(5, 1, ["主营业务分析"], "paragraph", seed_text)] + [
            (5, i + 2, ["主营业务分析"], "paragraph", t)
            for i, t in enumerate(cont_texts)]
        db = _make_db(Path(td), blocks)
        registry = ToolRegistry(audit_dir=Path(td) / "audit")
        register_bounded_evidence_tool(registry, db_path=db)
        register_resolve_seed_identity_tool(registry, db_path=db)
        seed = _seed(page=5, block=1, text=seed_text)
        res = expand(_request(seed, ("table_continuation",),
                              ExpansionBudget(table_continuation=3)),
                     registry, run_id="r2c1p")
        print("=" * 70)
        print("stop_reason =", res.stop_reason)
        print("adopted     =", [b.evidence_id[:12] for b in res.adopted])
        print("unread      =", [c.evidence_id[:12] for c in res.candidates_unread])
        print("unread_scope=", res.unread_scope)
        print("target_outcomes =", json.dumps(res.target_outcomes,
                                              ensure_ascii=False, indent=2))
        for d in res.boundary_decisions:
            print("  decision:", d.evidence_id[:12], d.disposition, d.reason_code,
                  d.relation, d.structural_signals)


if __name__ == "__main__":
    main()
