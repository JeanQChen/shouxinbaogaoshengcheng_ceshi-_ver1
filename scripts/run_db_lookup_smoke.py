"""Phase 3 Batch B · 修订②：原始 41 问 DB_LOOKUP 真实路径冒烟。

背景：上一版 smoke 用「合成问题」证明 DB 工具层可用，但被人工门驳回——那只能证明
工具层可用，不能证明「原始 41 问的 DB 路径真的可达」。本 harness 修正：

1. **合成 DB 工具单测**（单独报告，不作原始路径替代）：临时库构建 300750 Financial
   Snapshot，经 `ToolRegistry` 真实调用 `lookup_company_field` /
   `lookup_financial_metric`（含正/负向用例），证明 DB 工具层可用。
2. **原始 41 问真实路由**：读 `v1_baseline.jsonl`，逐题用 `question` 原文构造
   `InformationNeed`，经真实 `Router`（context 指向临时库）路由——**不改问题、不用
   gold 扩展 query、不强制 Router 结果**。逐题记录实际 route + `resolve_db_target`
   能否表达（及 target/period）。
3. **DB 目标确定性执行**：对真实路由到 `DB_LOOKUP` 的原始题，用 route filters 直接经
   Registry 执行 `lookup_company_field` / `lookup_financial_metric`，校验结构化结果
   真实返回（不依赖 LLM）。
4. **可选 `--execute`**：对 DB 路由题跑 `harness.runtime.run_question`（真实 Registry +
   真实 LLM），校验 `structured_refs` 非空、answer 引用 structured、数字 == 结构化
   display_value。默认关闭（确定性、无 LLM/网络）。
5. 判定 `ORIGINAL_41_DB_PATH_REACHABLE` / `NOT_REACHABLE`：至少一个原始题真实路由到
   DB_LOOKUP 且其 DB 目标返回结构化数据才可达；否则诚实报 NOT_REACHABLE 并给逐题
   路由表 + 抢占原因。

不污染生产 `financial_v2.db` / `evidence.db`；临时库用后清理（`--keep` 保留）。

CLI:
  python -m scripts.run_db_lookup_smoke                 # 确定性（无 LLM）
  python -m scripts.run_db_lookup_smoke --execute       # 对 DB 路由题跑真实 LLM
  python -m scripts.run_db_lookup_smoke --keep --audit-dir logs/tools
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import load_dataset
from evidence import store as estore
from financial_v2 import store as fstore
from routing import context as routing_context
from routing import db_targets
from routing import router as router_mod
from routing import schema as RS
from scripts import prepare_financial_snapshot as psnap
from tools import adapters
from tools import contracts as C
from tools import registry as R

logger = logging.getLogger(__name__)

# 演示常量（与 Phase 1F-A 验收一致；不硬编码金额/期间）。
COMPANY = "300750"
DECLARED_NAME = "宁德时代"
DETECTED_NAME = "宁德时代"
SCOPE = "consolidated"
CURRENCY = "CNY"
_DB_ROUTE = "DB_LOOKUP"

DATASET_PATH = "evaluation/datasets/v1_baseline.jsonl"
SAMPLE_EXCELS = [
    "data/samples/300750/financial/NDSD_BALANCESHEET_2023-2026Q1.xlsx",
    "data/samples/300750/financial/NDSD_CASH_2023-2026Q1.xlsx",
    "data/samples/300750/financial/NDSD_EFFORT_2023-2026Q1.xlsx",
]


# ---------------------------------------------------------------------------
# JSON 序列化（Decimal → str，dataclass → asdict）
# ---------------------------------------------------------------------------

def _jsonable(o):
    if dataclasses.is_dataclass(o):
        return _jsonable(dataclasses.asdict(o))
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, Path):
        return str(o)
    return o


# ---------------------------------------------------------------------------
# 需求构造（只取 question 原文，不碰 gold）
# ---------------------------------------------------------------------------

def _build_need(case) -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id=case.case_id, section_id=case.section_id, question=case.question,
        required_evidence_types=[], required_source_types=[],
        time_scope=case.time_scope, priority=case.priority, depends_on=[])


# ---------------------------------------------------------------------------
# DB 目标确定性执行（用 route filters，与 harness._inject_args 同口径）
# ---------------------------------------------------------------------------

def _db_tool_args(company: str, filters: dict) -> tuple[str, dict]:
    """由 Router 产出的 DB filters 组装工具名 + 参数（字段 vs 指标）。"""
    dims = {k: filters[k] for k in ("snapshot_as_of_date", "target_period",
                                    "scope", "currency", "purpose")
            if filters.get(k)}
    if filters.get("db_target_type") == "field":
        args = {"company_id": company,
                "standard_item_code": filters.get("standard_item_code", "")}
        args.update(dims)
        return "lookup_company_field", args
    args = {"company_id": company, "formula_id": filters.get("formula_id", "")}
    if filters.get("formula_version"):
        args["formula_version"] = filters["formula_version"]
    args.update(dims)
    return "lookup_financial_metric", args


def _execute_db_target(company: str, filters: dict,
                       registry: R.ToolRegistry) -> dict:
    """对单个 DB 目标真实执行并返回结构化结果摘要（无 LLM）。"""
    tool_name, args = _db_tool_args(company, filters)
    call = C.ToolCall(
        call_id=uuid.uuid4().hex, tool_name=tool_name, arguments=args,
        idempotency_key=uuid.uuid4().hex, need_id="db_lookup_smoke",
        batch_id="db_lookup_smoke")
    res = registry.execute(call, route=_DB_ROUTE, run_id="db_lookup_smoke")
    refs = [dataclasses.asdict(r) for r in res.structured_result_refs]
    return {
        "tool": tool_name,
        "status": res.status,
        "error_code": res.error_code,
        "message": res.message,
        "n_structured": len(refs),
        "structured_refs": _jsonable(refs),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(*, dataset_path: str = DATASET_PATH, company: str = COMPANY,
        excel_files: list[str] | None = None, audit_dir: str | Path = R.DEFAULT_AUDIT_DIR,
        keep: bool = False, execute: bool = False,
        model: str | None = None) -> dict:
    excel_files = excel_files or SAMPLE_EXCELS
    tmpdir = Path(tempfile.mkdtemp(prefix="db_lookup_smoke_"))
    tmp_fin = tmpdir / "fin.db"
    tmp_ev = tmpdir / "ev.db"

    try:
        # 1. 临时库构建快照（不污染生产库）+ 合成 DB 工具单测。
        summary = psnap.build_snapshot(
            company, excel_files, db=tmp_fin, scope=SCOPE, currency=CURRENCY,
            declared_name=DECLARED_NAME, detected_name=DETECTED_NAME)
        synthetic = psnap.run_financial_tools(
            tmp_fin, company, summary, audit_dir=audit_dir)

        # 2. 路由上下文指向临时库（evidence 库为空：DB 路由不依赖本地证据）。
        fstore.init_db(tmp_fin)
        estore.init_db(tmp_ev)
        context = routing_context.build_route_context(
            company, scope=SCOPE, currency=CURRENCY)

        registry = adapters.build_default_registry(audit_dir=audit_dir)
        cases = load_dataset(dataset_path)

        # 3. 原始 41 问逐题真实路由 + resolver 表达性。
        route_dist: dict[str, int] = {}
        per_question: list[dict] = []
        db_routed: list[dict] = []
        for case in cases:
            need = _build_need(case)
            target = db_targets.resolve_db_target(case.question)
            period = db_targets.resolve_target_period(case.question)
            expressible = target is not None
            try:
                rr = router_mod.route(need, context)
            except Exception as e:  # noqa: BLE001 —— 路由契约校验失败，诚实标记
                rr = RS.RouterResult(
                    status="FAILED", decision=None,
                    error_code=f"ROUTER_FAILED:{type(e).__name__}", trace_id="")
            if rr.decision is not None:
                route = rr.decision.route
                reason_code = rr.decision.reason_code
                filters = rr.decision.filters
            else:
                route = f"UNDECIDED:{rr.error_code}"
                reason_code = rr.error_code
                filters = {}
            route_dist[route] = route_dist.get(route, 0) + 1

            rec = {
                "case_id": case.case_id,
                "question": case.question,
                "section_id": case.section_id,
                "expected_route_v2": case.expected_route_v2,
                "route": route,
                "reason_code": reason_code,
                "db_target_expressible": expressible,
                "db_target": (None if target is None else {
                    "type": target.target_type,
                    "standard_item_code": target.standard_item_code,
                    "formula_id": target.formula_id,
                    "formula_version": target.formula_version,
                }),
                "target_period": period,
            }
            if route == _DB_ROUTE:
                # 4. 对 DB 路由题确定性执行其 DB 目标（证明结构化数据真实可达）。
                rec["db_execution"] = _execute_db_target(company, filters, registry)
                db_routed.append(rec)
            per_question.append(rec)

        # 5. 判定原始 41 问 DB 路径是否可达 + 诚实原因。
        reachable_cases = [r for r in db_routed
                           if r.get("db_execution", {}).get("n_structured", 0) > 0]
        reachable = bool(reachable_cases)
        reasons: list[str] = []
        for r in db_routed:
            ex = r.get("db_execution", {})
            n = ex.get("n_structured", 0)
            if n > 0:
                reasons.append(f"{r['case_id']}: DB 目标返回 {n} 条结构化结果")
            else:
                reasons.append(f"{r['case_id']}: {ex.get('status')}/"
                               f"{ex.get('error_code')}（字段/指标不可用）")
        if not db_routed:
            reasons.append("没有任何原始 41 问真实路由到 DB_LOOKUP")
        preempted = [r for r in per_question
                     if r["route"] != _DB_ROUTE and r["db_target_expressible"]]
        if preempted:
            reasons.append(f"{len(preempted)} 个原始题 resolver 可表达但被 "
                           f"DEEP/RAG/EXTERNAL 信号抢占"
                           f"（如 {', '.join(r['case_id'] for r in preempted[:3])}）")
        not_expr = [r for r in per_question
                    if not r["db_target_expressible"] and r["section_id"] == "financial"]
        if not_expr:
            reasons.append(f"{len(not_expr)} 个财务题 resolver 无法表达"
                           f"（如 {', '.join(r['case_id'] for r in not_expr[:3])}）")

        original_41_db_path = {
            "reachable": reachable,
            "verdict": ("ORIGINAL_41_DB_PATH_REACHABLE" if reachable
                        else "ORIGINAL_41_DB_PATH_NOT_REACHABLE"),
            "n_db_routed": len(db_routed),
            "n_reachable": len(reachable_cases),
            "reasons": reasons,
        }

        # 6. 可选 --execute：对 DB 路由题跑真实 LLM 研究循环。
        execution: list[dict] = []
        if execute and db_routed:
            from harness import entailment as E
            from harness import policies as P
            from harness import runtime as RT

            import config
            llm = RT.RealResearchLLM(model=model or config.LLM_MODEL)
            for r in db_routed:
                case = next(c for c in cases if c.case_id == r["case_id"])
                need = _build_need(case)
                rr = router_mod.route(need, context)
                outcome = RT.run_question(
                    need=need, route_result=rr, registry=registry, llm=llm,
                    budget=P.DEFAULT_BUDGET, run_id="db_lookup_smoke",
                    case_id=case.case_id, company_id=company,
                    section_id=case.section_id, trace_enabled=False)
                st = outcome.state
                ans = outcome.answer
                # 校验：structured 非空 + answer 引用 structured + 数字==display_value。
                structured_ok = bool(st.structured_refs)
                cites_structured = bool(ans and any(
                    c.ref_type == "structured" for c in ans.citations))
                number_match = _check_number_match(ans, st.structured_refs, E)
                execution.append({
                    "case_id": r["case_id"],
                    "completion_status": outcome.completion_status,
                    "success": outcome.success,
                    "structured_refs_non_empty": structured_ok,
                    "answer_cites_structured": cites_structured,
                    "number_matches_structured": number_match,
                    "answer_text": ans.answer_text if ans else None,
                    "claims": [c.text for c in (ans.claims if ans else [])],
                })
            original_41_db_path["execution"] = execution

        return {
            "company": company,
            "db": str(tmp_fin),
            "db_kept": keep,
            "synthetic_tool_test": synthetic,       # 合成 DB 工具单测（单独报告）
            "routing": {
                "n_cases": len(per_question),
                "route_distribution": route_dist,
                "per_question": per_question,
            },
            "original_41_db_path": original_41_db_path,
        }
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _check_number_match(ans, structured_refs, E) -> bool | None:
    """answer 中至少一个 fact claim 的数字与结构化 display_value 数值等价。

    无结构化引用/无数字 → None（不适用）；否则返回是否匹配。"""
    if not ans or not structured_refs:
        return None
    import re
    claim_numbers: list[str] = []
    for c in ans.claims:
        claim_numbers.extend(t.token for t in E.extract_amounts(c.text or ""))
    if not claim_numbers:
        return None
    ref_values: list[str] = []
    for r in structured_refs:
        v = r.display_value if r.display_value is not None else r.raw_value
        if v is not None:
            ref_values.append(str(v))
    if not ref_values:
        return None
    return any(E.amounts_equivalent(cn, rv)
               for cn in claim_numbers for rv in ref_values)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_db_lookup_smoke",
        description="原始 41 问 DB_LOOKUP 真实路径冒烟（修订②）")
    parser.add_argument("--dataset", default=DATASET_PATH)
    parser.add_argument("--company", default=COMPANY)
    parser.add_argument("--excel", action="append", default=None,
                        help="财务报表 Excel（缺省用 300750 三张样本）")
    parser.add_argument("--audit-dir", default=str(R.DEFAULT_AUDIT_DIR))
    parser.add_argument("--keep", action="store_true", help="保留临时库")
    parser.add_argument("--execute", action="store_true",
                        help="对 DB 路由题跑真实 LLM 研究循环（默认关闭）")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run(
        dataset_path=args.dataset, company=args.company,
        excel_files=args.excel, audit_dir=args.audit_dir,
        keep=args.keep, execute=args.execute, model=args.model)
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))
