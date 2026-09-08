"""财务工具真实快照验收 harness（Phase 3 Batch A · commit 5）。

用途（任务书 §7）：空 `financial_v2.db` 不能作为 `lookup_company_field` /
`lookup_financial_metric` / `compare_financial_periods` 的验收证据。本 harness：

1. 复用 Phase 1F-A 主链入口（`scripts.run_financial_v2_chain.run`），在**临时/演示库**
   为 300750 构建 Financial Snapshot（不污染生产库 `data/financial_v2.db`）；
2. 经 `tools.registry.ToolRegistry`（唯一执行入口）真实调用三个财务工具，
   校验「有数据 → SUCCESS + 溯源」「数据缺失 → EMPTY/DB_FIELD_UNAVAILABLE」；
3. 结构化区分三类 disposition：
   - `success`           —— SUCCESS / PARTIAL（工具正常，数据可用或单边缺失）；
   - `data_unavailable`  —— EMPTY（工具正常，字段/指标不可用，非工具错误）；
   - `tool_error`        —— FATAL_ERROR / RETRYABLE_ERROR（鉴权/契约/内部错误）。

不新算财务指标、不让 LLM 算数、不硬编码金额；临时库用后清理（`--keep` 保留）。

CLI:
  python -m scripts.prepare_financial_snapshot \
    --company 300750 --db <临时库.db> \
    --scope consolidated --currency CNY \
    --declared-name 宁德时代 --detected-name 宁德时代 \
    --excel <资产负债.xlsx> --excel <利润.xlsx> --excel <现金流.xlsx> \
    [--audit-dir logs/tools] [--keep]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from financial_v2 import store as fstore
from scripts import run_financial_v2_chain as fchain
from tools import adapters
from tools import contracts as C
from tools import registry as R

logger = logging.getLogger(__name__)

# 三个财务工具可用的字段/指标（稳定契约常量，不硬编码金额与期间）。
_FIELD_CODE = "TOTAL_ASSETS"
_METRIC_FORMULA = "SOLV_CURRENT_RATIO"
# 故意选一个因缺失输入而「不可用」的指标，用于验证 data_unavailable 与 tool_error 的区分。
_UNAVAILABLE_FORMULA = "EBITDA"

_DB_ROUTE = "DB_LOOKUP"


# ---------------------------------------------------------------------------
# 快照构建（复用 Phase 1F-A 入口）
# ---------------------------------------------------------------------------

def build_snapshot(company: str, excel_files: list[str], *, db: str | Path,
                   scope: str, currency: str, declared_name: str | None,
                   detected_name: str | None) -> dict:
    """在指定临时/演示库构建公司 Financial Snapshot，返回主链摘要。"""
    fstore.init_db(db)
    summary = fchain.run(
        company, excel_files, scope=scope, currency=currency,
        declared_name=declared_name, detected_name=detected_name,
        persist=True, sample_items=0, target_period=None)
    if summary.get("error"):
        raise RuntimeError(f"快照构建失败: {summary['error']}")
    return summary


def _annual_periods(summary: dict) -> list[str]:
    """从快照摘要提取年报期（降序），用于选取目标/对比期间。"""
    annual = sorted((p for p in summary.get("periods", []) if p.endswith("-12-31")),
                    reverse=True)
    if not annual:
        raise RuntimeError("快照不含年报期，无法选取财务工具对比期间")
    return annual


# ---------------------------------------------------------------------------
# 财务工具真实调用（经 Registry）
# ---------------------------------------------------------------------------

def _to_jsonable(obj):
    """把 Decimal 等非 JSON 原生类型递归转为可序列化值（Decimal → str）。"""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _tool_result_to_dict(res: C.ToolResult) -> dict:
    def disposition() -> str:
        if res.is_error():
            return "tool_error"
        if res.is_empty():
            return "data_unavailable"
        return "success"

    return {
        "tool_name": res.tool_name,
        "status": res.status,
        "disposition": disposition(),
        "error_code": res.error_code,
        "message": res.message,
        "latency_ms": res.latency_ms,
        "data": _to_jsonable(res.data),
        "structured_result_refs": _to_jsonable(
            [dataclasses.asdict(r) for r in res.structured_result_refs]),
        "trace_id": res.trace_id,
    }


def run_financial_tools(db: str | Path, company: str, summary: dict, *,
                        audit_dir: str | Path = R.DEFAULT_AUDIT_DIR) -> dict:
    """经 Registry 真实调用三个财务工具（含正/负向用例），返回结构化验收结果。"""
    fstore.init_db(db)
    reg = adapters.build_default_registry(audit_dir=audit_dir)

    annual = _annual_periods(summary)
    latest = annual[0]
    prev = annual[1] if len(annual) > 1 else latest
    missing_period = "2099-12-31"  # 不存在期间，验证单边缺失 PARTIAL/missing_period。

    cases = [
        ("lookup_company_field",
         {"company_id": company, "standard_item_code": _FIELD_CODE,
          "target_period": latest}),
        ("lookup_financial_metric",
         {"company_id": company, "formula_id": _METRIC_FORMULA,
          "target_period": latest}),
        ("compare_financial_periods",
         {"company_id": company, "formula_id": _METRIC_FORMULA,
          "period_a": prev, "period_b": latest}),
        ("lookup_financial_metric",
         {"company_id": company, "formula_id": _UNAVAILABLE_FORMULA,
          "target_period": latest}),
        ("compare_financial_periods",
         {"company_id": company, "formula_id": _METRIC_FORMULA,
          "period_a": missing_period, "period_b": latest}),
    ]

    results: list[dict] = []
    for tool_name, arguments in cases:
        call = C.ToolCall(
            call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=arguments,
            idempotency_key=uuid.uuid4().hex, need_id="batch_a_financial",
            batch_id="batch_a_financial")
        res = reg.execute(call, route=_DB_ROUTE, run_id="batch_a_financial")
        results.append(_tool_result_to_dict(res))

    by_disposition: dict[str, int] = {}
    for r in results:
        by_disposition[r["disposition"]] = by_disposition.get(r["disposition"], 0) + 1

    return {
        "company": company,
        "snapshot_id": summary.get("snapshot_id"),
        "target_period": latest,
        "prev_period": prev,
        "case_count": len(results),
        "disposition_counts": by_disposition,
        "cases": results,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(company: str, excel_files: list[str], *, db: str | Path | None,
        scope: str, currency: str, declared_name: str | None,
        detected_name: str | None, audit_dir: str | Path,
        keep: bool) -> dict:
    """构建快照 + 真实调用财务工具，返回完整验收摘要。"""
    own_db = db is None
    db_path: Path
    if own_db:
        fd, tmp = tempfile.mkstemp(suffix=".db", prefix="fv2_tool_accept_")
        os.close(fd)
        db_path = Path(tmp)
    else:
        db_path = Path(db)

    try:
        summary = build_snapshot(
            company, excel_files, db=db_path, scope=scope, currency=currency,
            declared_name=declared_name, detected_name=detected_name)
        tools = run_financial_tools(
            db_path, company, summary, audit_dir=audit_dir)
        return {
            "db": str(db_path),
            "db_cleaned_up": own_db and not keep,
            "snapshot": {
                "snapshot_id": summary.get("snapshot_id"),
                "periods": summary.get("periods"),
                "metric_count": summary.get("metric_count"),
                "metric_status_counts": summary.get("metric_status_counts"),
                "snapshot_item_count": summary.get("snapshot_item_count"),
                "final_state": summary.get("final_state"),
            },
            "financial_tools": tools,
        }
    finally:
        if own_db and not keep:
            for suffix in ("", "-wal", "-shm", "-journal"):
                try:
                    os.remove(str(db_path) + suffix)
                except FileNotFoundError:
                    pass


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.prepare_financial_snapshot",
        description="财务工具真实快照验收（临时/演示库 + 经 Registry 真实调用）")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--db", default=None, help="SQLite 路径（缺省用临时库，用后清理）")
    parser.add_argument("--excel", action="append", required=True,
                        help="Excel 报表路径（可重复）")
    parser.add_argument("--scope", default="consolidated", help="statement_scope")
    parser.add_argument("--currency", default="CNY", help="currency")
    parser.add_argument("--declared-name", default=None, help="声明公司名")
    parser.add_argument("--detected-name", default=None, help="检测公司名")
    parser.add_argument("--audit-dir", default=str(R.DEFAULT_AUDIT_DIR),
                        help="Registry audit 目录")
    parser.add_argument("--keep", action="store_true",
                        help="保留临时库（不清理）")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run(
        args.company, args.excel, db=args.db, scope=args.scope,
        currency=args.currency, declared_name=args.declared_name,
        detected_name=args.detected_name, audit_dir=args.audit_dir,
        keep=args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
