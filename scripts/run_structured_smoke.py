"""Phase 3 Batch B · 用户 §三：原始财务问题接入 Financial Snapshot 冒烟。

背景：上一版 `run_db_lookup_smoke` 证明「原始 41 问里 DB_LOOKUP 路径可达性」，但被人工门
驳回——财务问题大多因「变化/原因」信号被 DEEP 抢占，父路由不落 DB，DB 工具层空转。
本 harness 用「结构化子 need」补上这条链路：原始财务问题 → required aspects →
数值/指标方面确定性派生结构化子 InformationNeed → Router/DB resolver 路由 →
ToolRegistry 执行 → StructuredResultRef 汇入 state；原因/解释方面继续走 Evidence。

本 smoke 验证（确定性，无 LLM；`--execute` 才跑真实 LLM 研究循环）：

1. **临时快照注入**：`prepare_financial_snapshot.build_snapshot` 建临时 300750
   Financial Snapshot，不污染生产 `financial_v2.db`；context 指向临时库。
2. **选定问题**：
   - `FIN-PM1`（纯指标）：方面「净利率」→ `PROF_NET_MARGIN`，子 need 命中快照。
   - `FIN-CF1`（hybrid）：问题直接点名「经营活动现金流净额」→ `OPERATING_CASH_FLOW`
     字段（结构化数字）+「变化原因」走 Evidence。
   - `FIN-GM1`（业务分块反例）：「动力电池业务毛利率」→ segment_scope_qualifier，
     语义不匹配拒绝（不把 segment 毛利率冒充公司整体 PROF_GROSS_MARGIN）。
3. **报告字段**：父路由 / 每个子 need 的路由与状态 / 语义不匹配拒绝 / DB vs Evidence
   aspect 通道 / 结构化结果摘要 /（`--execute`）最终 answer 引用。

父路由判定保持 Phase 2 原样（不篡改 Track B）；子 need 路由单独记录。

CLI:
  python -m scripts.run_structured_smoke                 # 确定性（无 LLM）
  python -m scripts.run_structured_smoke --execute       # 跑真实 LLM 研究循环
  python -m scripts.run_structured_smoke --keep --audit-dir logs/tools
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import load_dataset
from evidence import store as estore
from financial_v2 import store as fstore
from harness import policies as P
from harness import runtime as RT
from harness import structured_needs as SN
from routing import context as routing_context
from routing import router as router_mod
from routing import schema as RS
from scripts import prepare_financial_snapshot as psnap
from tools import adapters
from tools import registry as R

logger = logging.getLogger(__name__)

COMPANY = "300750"
DECLARED_NAME = "宁德时代"
DETECTED_NAME = "宁德时代"
SCOPE = "consolidated"
CURRENCY = "CNY"

DATASET_PATH = "evaluation/datasets/v1_baseline.jsonl"
SAMPLE_EXCELS = [
    "data/samples/300750/financial/NDSD_BALANCESHEET_2023-2026Q1.xlsx",
    "data/samples/300750/financial/NDSD_CASH_2023-2026Q1.xlsx",
    "data/samples/300750/financial/NDSD_EFFORT_2023-2026Q1.xlsx",
]
# §三 选定问题：纯指标 / hybrid（结构化数字 + Evidence 原因）/ 业务分块反例。
SELECTED_CASES = ("FIN-PM1", "FIN-CF1", "FIN-GM1")


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


def _build_need(case) -> RS.InformationNeed:
    return RS.InformationNeed(
        need_id=case.case_id, section_id=case.section_id, question=case.question,
        required_evidence_types=[], required_source_types=[],
        time_scope=case.time_scope, priority=case.priority, depends_on=[])


def _structured_summary(refs: list) -> list[dict]:
    out = []
    for r in refs:
        out.append({
            "result_type": r.result_type,
            "item_code": r.item_code, "formula_id": r.formula_id,
            "formula_version": r.formula_version, "period": r.period,
            "display_value": r.display_value, "unit": r.unit, "status": r.status,
            "reason_code": r.reason_code,
        })
    return out


def _run_subneed_only(case, context, registry, run_id: str) -> dict:
    """只跑结构化子 need 部分（真实 Router + DB 工具，无 LLM 研究循环）。"""
    need = _build_need(case)
    rr = router_mod.route(need, context)   # 父路由（Phase 2 原样）
    parent = {
        "status": rr.status,
        "route": rr.decision.route if rr.decision else None,
        "reason_code": rr.decision.reason_code if rr.decision else None,
    }
    state = RT._new_state(need, rr, run_id=run_id, case_id=case.case_id,
                          company_id=COMPANY, section_id=case.section_id,
                          budget=P.DEFAULT_BUDGET)
    RT._run_structured_subneeds(state, context, registry, P.DEFAULT_BUDGET,
                                run_id, trace_enabled=False)
    return {
        "case_id": case.case_id,
        "question": case.question,
        "parent_route": parent,
        "required_aspects": _jsonable(state.required_aspects),
        "structured_subneeds": _jsonable(state.structured_subneeds),
        "semantic_mismatches_rejected": _jsonable(state.semantic_mismatches_rejected),
        "db_vs_evidence_aspects": SN.classify_aspects(
            state.required_aspects, state.structured_subneeds,
            state.semantic_mismatches_rejected),
        "structured_refs": _structured_summary(state.structured_refs),
    }


def run(*, dataset_path: str = DATASET_PATH, company: str = COMPANY,
        excel_files: list[str] | None = None, audit_dir: str | Path = R.DEFAULT_AUDIT_DIR,
        keep: bool = False, execute: bool = False,
        model: str | None = None) -> dict:
    excel_files = excel_files or SAMPLE_EXCELS
    tmpdir = Path(tempfile.mkdtemp(prefix="structured_smoke_"))
    tmp_fin = tmpdir / "fin.db"
    tmp_ev = tmpdir / "ev.db"

    try:
        # 1. 临时库构建快照（不污染生产库）。
        psnap.build_snapshot(
            company, excel_files, db=tmp_fin, scope=SCOPE, currency=CURRENCY,
            declared_name=DECLARED_NAME, detected_name=DETECTED_NAME)

        # 2. 路由上下文指向临时库。
        fstore.init_db(tmp_fin)
        estore.init_db(tmp_ev)
        context = routing_context.build_route_context(
            company, scope=SCOPE, currency=CURRENCY)

        registry = adapters.build_default_registry(audit_dir=audit_dir)
        cases = load_dataset(dataset_path)
        selected = [c for c in cases if c.case_id in SELECTED_CASES]
        if len(selected) != len(SELECTED_CASES):
            missing = set(SELECTED_CASES) - {c.case_id for c in selected}
            raise ValueError(f"数据集缺少选定 case: {sorted(missing)}")

        run_id = f"structured_smoke_{company}"

        # 3. 结构化子 need 确定性路径（无 LLM）。
        per_question = [_run_subneed_only(c, context, registry, run_id)
                        for c in selected]

        # 4. 统计（父 DB 路由 / 子 need / 成功 / 语义不匹配）。
        n_parent_db = sum(1 for r in per_question
                          if r["parent_route"]["route"] == "DB_LOOKUP")
        n_subneeds = sum(len(r["structured_subneeds"]) for r in per_question)
        n_resolved = sum(
            sum(1 for sn in r["structured_subneeds"] if sn.get("status") == "RESOLVED")
            for r in per_question)
        n_semantic_mismatch = sum(
            len(r["semantic_mismatches_rejected"]) for r in per_question)
        stats = {
            "parent_db_routes": n_parent_db,
            "n_subneeds": n_subneeds,
            "n_resolved": n_resolved,
            "n_semantic_mismatches_rejected": n_semantic_mismatch,
            "n_cases_with_structured": sum(
                1 for r in per_question if r["structured_refs"]),
        }

        result: dict = {
            "company": company,
            "db": str(tmp_fin),
            "db_kept": keep,
            "selected_cases": list(SELECTED_CASES),
            "stats": stats,
            "per_question": per_question,
        }

        # 5. 可选 --execute：跑真实 LLM 研究循环（含 final citations）。
        if execute:
            import config
            llm = RT.RealResearchLLM(model=model or config.LLM_MODEL)
            execution: list[dict] = []
            for case in selected:
                need = _build_need(case)
                rr = router_mod.route(need, context)
                outcome = RT.run_question(
                    need=need, route_result=rr, registry=registry, llm=llm,
                    budget=P.DEFAULT_BUDGET, run_id=run_id, case_id=case.case_id,
                    company_id=company, section_id=case.section_id,
                    trace_enabled=False, context=context)
                st = outcome.state
                ans = outcome.answer
                execution.append({
                    "case_id": case.case_id,
                    "completion_status": outcome.completion_status,
                    "success": outcome.success,
                    "structured_subneeds": _jsonable(st.structured_subneeds),
                    "semantic_mismatches_rejected": _jsonable(
                        st.semantic_mismatches_rejected),
                    "structured_refs": _structured_summary(st.structured_refs),
                    "answer_text": ans.answer_text if ans else None,
                    "citations": [
                        {"ref_type": c.ref_type, "snapshot_id": c.snapshot_id,
                         "item_code": c.item_code, "formula_id": c.formula_id,
                         "period": c.period, "evidence_id": c.evidence_id}
                        for c in (ans.citations if ans else [])
                    ],
                })
            result["execution"] = execution

        return result
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_structured_smoke",
        description="结构化子 need 冒烟（原始财务问题接入 Financial Snapshot，§三）")
    parser.add_argument("--dataset", default=DATASET_PATH)
    parser.add_argument("--company", default=COMPANY)
    parser.add_argument("--excel", action="append", default=None)
    parser.add_argument("--audit-dir", default=str(R.DEFAULT_AUDIT_DIR))
    parser.add_argument("--keep", action="store_true", help="保留临时库")
    parser.add_argument("--execute", action="store_true",
                        help="跑真实 LLM 研究循环（默认关闭）")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run(dataset_path=args.dataset, company=args.company,
                 excel_files=args.excel, audit_dir=args.audit_dir,
                 keep=args.keep, execute=args.execute, model=args.model)
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))
