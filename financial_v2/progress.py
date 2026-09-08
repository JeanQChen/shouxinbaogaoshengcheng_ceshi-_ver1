"""A7-2 进度门面（任务书 §11）：真实持久化 ProgressEvent 的写入与读取。

职责边界（CLAUDE.md / 任务书硬要求）：
- 进度只显示阶段、数量、问题和可恢复性，不展示模型思维链；
- 进度来自真实持久化事件（store.progress_events），失败 / 等待人工绝不显示为成功；
- 只调用公开接口（snapshots.build_snapshot / metrics.compute_all / adapters），不复制
  公式、准入或计算业务逻辑；
- 本模块的 run_pipeline 是 A1~A6 产物的「串联」入口：当前 Record Set + Reconciliation
  → 构建快照 → 计算指标 → 只读载荷，进度逐阶段落盘；
- Checkpoint 由 snapshots / metrics 在完整快照 / 完整指标批次提交后写入，本模块不
  伪造断点。

阶段映射（S.STAGES 冻结，仅做展示映射，不修改 schema）：
  SOURCE_VALIDATION   ≈ VALIDATING_INPUTS（校验输入）
  SNAPSHOT_BUILD      ≈ BUILDING_SNAPSHOT + PERSISTING_SNAPSHOT（构建并固化快照）
  CALCULATION         ≈ COMPUTING_METRICS（计算指标）
  WAITING_CONFIRMATION ≈ WAITING_HUMAN（等待人工确认）
  COMPLETED / FAILED   ≈ COMPLETED / FAILED

CLI:
  python -m financial_v2.progress run --company <id> [--as-of <date>] [--validate-only] [--db <path>]
  python -m financial_v2.progress --run <run_id> [--db <path>]
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from financial_v2 import adapters
from financial_v2 import formulas
from financial_v2 import metrics
from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store

logger = logging.getLogger(__name__)

# 展示标签（只读映射；不修改 S.STAGES）。
STAGE_LABELS: dict[str, str] = {
    "SOURCE_VALIDATION": "校验输入",
    "EXTRACTION": "抽取",
    "NORMALIZATION": "标准化",
    "RECONCILIATION": "对账",
    "WAITING_CONFIRMATION": "等待人工确认",
    "SNAPSHOT_BUILD": "构建并固化快照",
    "CALCULATION": "计算指标",
    "COMPLETED": "完成",
    "FAILED": "失败",
}

_CANONICAL_ORDER = list(S.STAGES)

# 终态集合（摘要据此判定，绝不把失败/等待误报为完成）。
_TERMINAL_STAGES = {"COMPLETED", "FAILED", "WAITING_CONFIRMATION"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 进度事件写入（幂等）
# ---------------------------------------------------------------------------

def _event_id(run_id: str, stage_id: str, status: str, message_code: str,
              completed_units: int | None, total_units: int | None,
              error_code: str | None) -> str:
    payload = "|".join([run_id, stage_id, status, message_code,
                        str(completed_units), str(total_units), str(error_code)])
    return "pe-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def record(run_id: str, stage_id: str, status: str, message_code: str,
           completed_units: int | None = None, total_units: int | None = None,
           error_code: str | None = None, recoverable: bool = True,
           ) -> S.ProgressEvent | None:
    """写入一条进度事件（幂等：同内容事件已存在则跳过，返回 None）。"""
    ev = S.ProgressEvent(
        event_id=_event_id(run_id, stage_id, status, message_code,
                           completed_units, total_units, error_code),
        run_id=run_id, stage_id=stage_id, status=status,
        message_code=message_code, completed_units=completed_units,
        total_units=total_units, error_code=error_code,
        recoverable=recoverable, created_at=_utcnow(),
    )
    try:
        store.record_progress(ev)
        return ev
    except sqlite3.IntegrityError:
        return None  # 幂等：同内容事件已存在


# ---------------------------------------------------------------------------
# 进度摘要（只读）
# ---------------------------------------------------------------------------

@dataclass
class StageStatus:
    """某一阶段的最近一次状态。"""

    stage_id: str
    label: str
    status: str
    message_code: str
    completed_units: int | None
    total_units: int | None
    error_code: str | None
    recoverable: bool
    created_at: str


@dataclass
class ProgressSummary:
    """一次运行的全部阶段状态 + 终态。"""

    run_id: str
    stages: list[StageStatus]
    final_state: str          # completed | failed | waiting_human | in_progress
    recoverable: bool
    error_codes: list[str] = field(default_factory=list)


def summary(run_id: str) -> ProgressSummary:
    """读取一次运行的全部进度事件，按规范顺序折叠为每阶段最新状态。"""
    events = store.history_progress(run_id)
    # 每阶段保留最后一条（按 rowid 升序读入，后写覆盖前写）。
    latest: dict[str, S.ProgressEvent] = {}
    for ev in events:
        latest[ev.stage_id] = ev

    stages: list[StageStatus] = []
    for sid in _CANONICAL_ORDER:
        if sid not in latest:
            continue
        ev = latest[sid]
        stages.append(StageStatus(
            stage_id=sid, label=STAGE_LABELS.get(sid, sid), status=ev.status,
            message_code=ev.message_code, completed_units=ev.completed_units,
            total_units=ev.total_units, error_code=ev.error_code,
            recoverable=ev.recoverable, created_at=ev.created_at,
        ))

    final_state = _derive_final_state(latest)
    recoverable = all(ev.recoverable for ev in latest.values())
    error_codes = sorted({ev.error_code for ev in latest.values()
                          if ev.error_code})
    return ProgressSummary(run_id=run_id, stages=stages, final_state=final_state,
                           recoverable=recoverable, error_codes=error_codes)


def _derive_final_state(latest: dict[str, S.ProgressEvent]) -> str:
    """由终态事件判定最终状态；失败/等待优先于完成，绝不误报成功。"""
    if "FAILED" in latest:
        return "failed"
    if "WAITING_CONFIRMATION" in latest and "COMPLETED" not in latest:
        return "waiting_human"
    if "COMPLETED" in latest:
        return "completed"
    return "in_progress"


# ---------------------------------------------------------------------------
# A1~A6 串联入口（§11：构建请求 + 流水线）
# ---------------------------------------------------------------------------

def build_request_for_company(company_id: str, *, as_of_date: str | None = None,
                              scope: str = "consolidated", currency: str = "CNY",
                              purpose: str = "credit_analysis",
                              run_id: str | None = None,
                              ) -> snapshots.SnapshotBuildRequest:
    """由公司当前 Record Set + Reconciliation 组装 SnapshotBuildRequest（只读，不写）。

    record_set_ids 取各 source_document 的 current_record_set；resolution 由
    build_snapshot 内部读取 active resolution，不在请求层预选。
    """
    record_set_ids: list[str] = []
    periods: list[str] = []
    for doc in store.list_source_documents(company_id):
        cur = store.get_current_record_set(doc.source_document_id)
        if cur is not None:
            record_set_ids.append(cur.record_set_version)
            periods.extend(cur.report_periods)

    if not record_set_ids:
        raise ValueError(f"公司 {company_id} 无 current Record Set，无法构建快照")

    rec = store.get_current_reconciliation(company_id)
    reconciliation_run_id = rec.run_id if rec is not None else None

    if as_of_date is None:
        as_of_date = max(periods)

    if run_id is None:
        run_id = f"v2-{company_id}-{int(time.time())}"

    return snapshots.SnapshotBuildRequest(
        company_id=company_id, as_of_date=as_of_date, scope=scope, currency=currency,
        purpose=purpose, record_set_ids=sorted(set(record_set_ids)),
        reconciliation_run_id=reconciliation_run_id,
        required_formula_ids=list(formulas.DEFAULT_REQUIRED_FORMULA_IDS),
        restatement_selection={}, policy_adjustments={}, run_id=run_id,
    )


@dataclass
class PipelineResult:
    """一次流水线运行的结果（真实产物 + 终态）。"""

    run_id: str
    snapshot_id: str | None
    report_blocked: bool
    final_state: str
    metrics: metrics.MetricsTableV2 | None
    payload: adapters.FinancialAnalysisPayload | None
    progress: ProgressSummary
    error: str | None = None


def run_pipeline(request: snapshots.SnapshotBuildRequest,
                 persist: bool = True) -> PipelineResult:
    """串联：构建快照 → 计算指标 → 只读载荷，进度逐阶段落盘（真实事件）。

    - report_blocked=True 时终态 waiting_human（不写 COMPLETED）；
    - 任何异常终态 failed（不写 COMPLETED）；
    - 成功终态 completed。
    """
    run_id = request.run_id
    record(run_id, "SOURCE_VALIDATION", "completed", "VALIDATED_INPUTS",
           total_units=len(request.record_set_ids))

    snapshot_id: str | None = None
    try:
        record(run_id, "SNAPSHOT_BUILD", "running", "BUILDING_SNAPSHOT")
        built = snapshots.build_snapshot(request, persist=persist)
        snapshot_id = built.snapshot.snapshot_id
        record(run_id, "SNAPSHOT_BUILD", "completed", "PERSISTED_SNAPSHOT",
               completed_units=len(built.items), total_units=len(built.items),
               recoverable=not built.report_blocked)

        table = metrics.compute_all(snapshot_id, persist=persist)
        total = sum(table.status_counts.values())
        record(run_id, "CALCULATION", "completed", "COMPUTED_METRICS",
               completed_units=total, total_units=total)

        payload = adapters.report_financial_payload(snapshot_id)

        if built.report_blocked:
            record(run_id, "WAITING_CONFIRMATION", "running", "WAITING_HUMAN_RESOLUTION",
                   recoverable=True)
            final_state = "waiting_human"
        else:
            record(run_id, "COMPLETED", "completed", "COMPLETED")
            final_state = "completed"
    except Exception as e:  # noqa: BLE001 — 记 FAILED 并重抛语义上转为失败态返回
        logger.exception("V2 pipeline failed: %s", run_id)
        recoverable = not isinstance(e, KeyError)
        record(run_id, "FAILED", "failed", "FAILED",
               error_code=type(e).__name__, recoverable=recoverable)
        return PipelineResult(
            run_id=run_id, snapshot_id=snapshot_id, report_blocked=False,
            final_state="failed", metrics=None, payload=None,
            progress=summary(run_id), error=str(e))

    return PipelineResult(
        run_id=run_id, snapshot_id=snapshot_id,
        report_blocked=built.report_blocked, final_state=final_state,
        metrics=table, payload=payload, progress=summary(run_id))


# ---------------------------------------------------------------------------
# CLI（§11）
# ---------------------------------------------------------------------------

def _summary_to_dict(s: ProgressSummary) -> dict:
    return {
        "run_id": s.run_id,
        "final_state": s.final_state,
        "recoverable": s.recoverable,
        "error_codes": s.error_codes,
        "stages": [
            {
                "stage_id": x.stage_id, "label": x.label, "status": x.status,
                "message_code": x.message_code, "completed_units": x.completed_units,
                "total_units": x.total_units, "error_code": x.error_code,
                "recoverable": x.recoverable,
            }
            for x in s.stages
        ],
    }


def _result_to_dict(r: PipelineResult) -> dict:
    d = {
        "run_id": r.run_id, "snapshot_id": r.snapshot_id,
        "report_blocked": r.report_blocked, "final_state": r.final_state,
        "error": r.error,
        "progress": _summary_to_dict(r.progress),
    }
    if r.metrics is not None:
        d["metrics_status_counts"] = r.metrics.status_counts
        d["metrics_periods"] = r.metrics.periods
    if r.payload is not None:
        d["company_id"] = r.payload.company_id
        d["validity"] = r.payload.validity
        d["metric_count"] = len(r.payload.metrics)
        d["exception_count"] = len(r.payload.exceptions)
    return d


def _main(argv: list[str]) -> int:
    import argparse
    import sys

    # 中文 Windows 下 stdout 默认 GBK，ensure_ascii=False 的 JSON 输出会乱码；
    # 显式切换为 UTF-8，保证被 subprocess(encoding="utf-8") 捕获时编码一致。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.progress", description="A7-2 进度门面")
    parser.add_argument("--db", default=str(store.DEFAULT_DB_PATH),
                        help="SQLite 库路径（dev/test 注入临时库）")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="运行 V2 快照流水线（当前 Record Set → 快照 → 指标）")
    p_run.add_argument("--company", required=True, dest="company_id")
    p_run.add_argument("--as-of", dest="as_of_date", default=None)
    p_run.add_argument("--validate-only", action="store_true",
                       help="只构建/计算不落盘（快照与指标均不持久化）")

    p_sum = sub.add_parser("summary", help="读取一次运行的进度摘要")
    p_sum.add_argument("--run", required=True, dest="run_id")

    args = parser.parse_args(argv)
    store.init_db(args.db)

    if args.cmd == "run":
        request = build_request_for_company(args.company_id, as_of_date=args.as_of_date)
        result = run_pipeline(request, persist=not args.validate_only)
        print(json.dumps(_result_to_dict(result), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "summary":
        print(json.dumps(_summary_to_dict(summary(args.run_id)),
                         ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
