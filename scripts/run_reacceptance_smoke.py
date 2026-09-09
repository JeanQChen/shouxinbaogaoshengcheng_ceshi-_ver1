"""Phase 3 Batch B · 用户 §六：重新验收 smoke。

在上一版 smoke 被人工门驳回后，完成 §一~§五 收口，本脚本重跑**代表性** smoke（不是完整
41 问）：5 个公司问（COMP-S1/S2/R1/MV1/CR1）+ 2 个财务问（FIN-PM1 纯指标 / FIN-CF1
hybrid 结构化数字 + Evidence 原因），用一个新 run_id 通过 `evaluation.run_actual_path_41`
真实 Router + 受限研究循环 + 真实 LLM 跑完，产出可对比的 actual-path 产物。

关键做法（§三）：财务问的数值/指标方面由 harness 确定性派生「结构化子 need」，经 Router/
DB 目标解析器 → ToolRegistry → **临时** Financial Snapshot 命中；父路由保持 Phase 2 原样，
子 need 路由单独记录，不篡改 Track B。临时快照用后清理，不污染生产 `financial_v2.db`。

CLI:
  python -m scripts.run_reacceptance_smoke                       # 真实 LLM 重跑 7 问
  python -m scripts.run_reacceptance_smoke --validate-only       # 只路由 + 确定性子 need，不跑 LLM
  python -m scripts.run_reacceptance_smoke --keep                # 保留临时库
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import load_dataset
from evidence import store as estore
from financial_v2 import store as fstore
from scripts import prepare_financial_snapshot as psnap
from tools import adapters
from tools import registry as R
from routing import context as routing_context
from routing import router as router_mod
from routing import schema as RS
import evaluation.run_actual_path_41 as runner

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

# §六：5 个公司问 + 2 个财务问（≥2 财务问：纯指标 + hybrid）。
SELECTED_CASES = (
    "COMP-S1", "COMP-S2", "COMP-R1", "COMP-MV1", "COMP-CR1",
    "FIN-PM1", "FIN-CF1",
)


def _write_temp_dataset(cases, tmp_dataset: Path) -> list:
    """把选定 case 落成临时 jsonl（不碰原 v1_baseline.jsonl）。"""
    import dataclasses
    lines = [json.dumps(dataclasses.asdict(c), ensure_ascii=False) for c in cases]
    tmp_dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [c.case_id for c in cases]


def _validate_routes(cases, context, run_id: str) -> dict:
    """确定性路由 + 结构化子 need 预检（无 LLM），先于真实运行捕获契约/路由崩溃。"""
    import harness.runtime as RT
    import harness.policies as P
    registry = adapters.build_default_registry(audit_dir=R.DEFAULT_AUDIT_DIR)
    rows: list[dict] = []
    for c in cases:
        need = RS.InformationNeed(
            need_id=c.case_id, section_id=c.section_id, question=c.question,
            required_evidence_types=[], required_source_types=[],
            time_scope=c.time_scope, priority=c.priority, depends_on=[])
        rr = router_mod.route(need, context)
        state = RT._new_state(need, rr, run_id=run_id, case_id=c.case_id,
                              company_id=COMPANY, section_id=c.section_id,
                              budget=P.DEFAULT_BUDGET)
        RT._run_structured_subneeds(state, context, registry, P.DEFAULT_BUDGET,
                                    run_id, trace_enabled=False)
        rows.append({
            "case_id": c.case_id,
            "parent_route": rr.decision.route if rr.decision else None,
            "parent_reason": rr.decision.reason_code if rr.decision else None,
            "n_subneeds": len(state.structured_subneeds),
            "subneed_statuses": [sn["status"] if isinstance(sn, dict) else getattr(sn, "status", "?")
                                 for sn in state.structured_subneeds],
            "n_semantic_mismatch": len(state.semantic_mismatches_rejected),
        })
    return {"n_cases": len(rows), "rows": rows}


def run(*, dataset_path: str = DATASET_PATH, company: str = COMPANY,
        excel_files: list[str] | None = None,
        output_root: str = "evaluation/results/actual_path_41",
        ev_db_path: str = "data/evidence.db", harness_db_path: str = "data/harness.db",
        audit_dir: str = "logs/tools", model: str | None = None,
        run_id: str | None = None, validate_only: bool = False,
        keep: bool = False) -> dict:
    excel_files = excel_files or SAMPLE_EXCELS
    tmpdir = Path(tempfile.mkdtemp(prefix="reaccept_smoke_"))
    tmp_fin = tmpdir / "fin.db"
    tmp_dataset = tmpdir / "reaccept_cases.jsonl"

    try:
        # 1. 临时库构建 Financial Snapshot（不污染生产库）。
        psnap.build_snapshot(
            company, excel_files, db=tmp_fin, scope=SCOPE, currency=CURRENCY,
            declared_name=DECLARED_NAME, detected_name=DETECTED_NAME)

        # 2. 选定 case → 临时数据集。
        all_cases = load_dataset(dataset_path)
        by_id = {c.case_id: c for c in all_cases}
        missing = [cid for cid in SELECTED_CASES if cid not in by_id]
        if missing:
            raise ValueError(f"数据集缺少选定 case: {missing}")
        selected = [by_id[cid] for cid in SELECTED_CASES]
        _write_temp_dataset(selected, tmp_dataset)

        # 3. 路由上下文指向临时库（父路由 + 结构化子 need 都从这里读快照）。
        fstore.init_db(tmp_fin)
        estore.init_db(ev_db_path)
        context = routing_context.build_route_context(company)

        run_id = run_id or (
            "reaccept_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))

        # 4. 确定性预检（无 LLM）：路由 + 结构化子 need 是否按预期派生。
        pre = _validate_routes(selected, context, run_id)

        if validate_only:
            return {"run_id": run_id, "validate_only": True, "precheck": pre}

        # 5. 真实 LLM 研究循环（单一 run_id，一次跑完 7 问）。
        result = runner.run_actual_path_41(
            dataset_path=str(tmp_dataset), company_id=company,
            output_root=output_root, ev_db_path=ev_db_path,
            fin_db_path=str(tmp_fin), harness_db_path=harness_db_path,
            audit_dir=audit_dir, model=model, run_id=run_id)
        result["precheck"] = pre
        return result
    finally:
        if not keep:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_reacceptance_smoke",
        description="重新验收 smoke（§六：5 公司问 + 2 财务问，真实 LLM）")
    parser.add_argument("--dataset", default=DATASET_PATH)
    parser.add_argument("--company", default=COMPANY)
    parser.add_argument("--excel", action="append", default=None)
    parser.add_argument("--output", default="evaluation/results/actual_path_41",
                        dest="output_root")
    parser.add_argument("--ev-db", default="data/evidence.db")
    parser.add_argument("--harness-db", default="data/harness.db")
    parser.add_argument("--audit-dir", default="logs/tools")
    parser.add_argument("--model", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--keep", action="store_true", help="保留临时库")
    parser.add_argument("--validate-only", action="store_true",
                        help="只路由 + 确定性子 need，不跑 LLM")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run(dataset_path=args.dataset, company=args.company,
                 excel_files=args.excel, output_root=args.output_root,
                 ev_db_path=args.ev_db, harness_db_path=args.harness_db,
                 audit_dir=args.audit_dir, model=args.model, run_id=args.run_id,
                 validate_only=args.validate_only, keep=args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))
