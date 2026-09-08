"""Eval: routing/audit_v2.py —— Router 审计落盘（fail-closed，与 Retrieval 分离）。

用法: python -m evals.test_router_audit

覆盖（契约修正 6）：
- write_router_audit 落一条 JSONL，字段完整（status/route/reason/invoke_reason/filters）；
- route() 每次调用自动写一条审计（通过 spy 验证接线，不污染真实 logs/）；
- 落盘失败 fail-closed 抛 RouterAuditError（不静默吞错）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing import audit_v2
from routing import router as R
from routing import schema as S


def _need(q, **over):
    base = dict(need_id="A1", section_id="SEC", question=q,
                required_evidence_types=["paragraph"],
                required_source_types=["annual_report"], time_scope=None,
                priority="P0", depends_on=[])
    base.update(over)
    return S.InformationNeed(**base)


def _context(**over):
    base = dict(company_id="300750", report_as_of="2025-06-30",
                available_document_ids=[], available_source_types=[],
                supported_db_fields=["TOTAL_ASSETS"], supported_metric_ids=["PROF_ROE"],
                available_db_fields=[], available_metric_ids=[],
                external_research_enabled=False)
    base.update(over)
    return S.RouteContext(**base)


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

    # ---- 落一条审计：字段完整 ----
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp) / "router_v2"
        need = _need("2024 年总资产是多少？")
        result = R._route(need, _context())
        path = audit_v2.write_router_audit(result, need, _context(), logs_dir=logs_dir)
        files = list(logs_dir.glob("*.jsonl"))
        check(len(files) == 1, "落一条审计产生一个 JSONL 文件")
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        check(rec["status"] == "DECIDED" and rec["route"] == "DB_LOOKUP",
              "审计记录 status/route 正确")
        check(rec["filters"].get("snapshot_as_of_date") == "2025-06-30"
              and rec["filters"].get("target_period") == "2024-12-31",
              "审计记录携带完整五元组 filter")
        check(rec["company_id"] == "300750" and rec["scope"] == "consolidated"
              and rec["currency"] == "CNY" and rec["purpose"] == "credit_analysis",
              "审计记录携带维度（company_id/scope/currency/purpose）")
        check(rec["invoke_reason"] is None,
              "DECIDED（非 fallback）时 invoke_reason 为 None")

    # ---- fallback 结果携带 invoke_reason（TIME_SCOPE_UNPARSEABLE）----
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp) / "router_v2"
        need = _need("总资产是多少？", time_scope="过去三年")
        result = R._route(need, _context())
        path = audit_v2.write_router_audit(result, need, _context(), logs_dir=logs_dir)
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        check(rec["status"] == "FALLBACK_UNAVAILABLE"
              and rec["invoke_reason"] == "TIME_SCOPE_UNPARSEABLE",
              "FALLBACK_UNAVAILABLE 时 invoke_reason=TIME_SCOPE_UNPARSEABLE")

    # ---- 落盘失败 fail-closed：logs_dir 被文件占据 ----
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        result = R._route(_need("2024 年总资产是多少？"), _context())
        try:
            audit_v2.write_router_audit(result, _need("2024 年总资产是多少？"),
                                        _context(), logs_dir=blocker)
            check(False, "不可写 logs_dir 应抛 RouterAuditError")
        except audit_v2.RouterAuditError:
            check(True, "落盘失败抛 RouterAuditError（fail-closed）")

    # ---- route() 接线：每次调用自动写一条审计（spy 验证，不污染真实 logs/）----
    calls: list[tuple] = []
    orig = audit_v2.write_router_audit

    def _spy(result, need, context, logs_dir=audit_v2.LOGS_DIR):
        calls.append((result, need, context, logs_dir))
        return "spy-path"

    audit_v2.write_router_audit = _spy
    try:
        R.route(_need("2024 年总资产是多少？"), _context())
        check(len(calls) == 1 and calls[0][1].need_id == "A1",
              "route() 每次调用自动写一条 Router 审计")
    finally:
        audit_v2.write_router_audit = orig

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
