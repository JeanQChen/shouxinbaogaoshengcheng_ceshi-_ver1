"""Phase 2 Router 审计落盘（与 Retrieval trace 分离，V2 不混 V1）。

每次 route() 落一条 JSONL 到 logs/router_v2/，记录 InformationNeed / RouteContext /
RouterResult 的决策与原因，供 Track B 逐 case 审计 Router 决策（Track A 不调 Router，
故 Track A 运行应产生 0 条 Router 审计）。

契约修正 6：Router 与 Retrieval 审计分离；落盘失败 fail-closed（抛 RouterAuditError），
不静默吞错。

CLI: python -m routing.audit_v2  # 冒烟自检
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

LOGS_DIR = Path("logs/router_v2")


class RouterAuditError(Exception):
    """Router 审计落盘失败（fail-closed）。"""


@dataclass
class RouterAudit:
    trace_id: str
    timestamp: str
    need_id: str
    section_id: str
    question: str
    time_scope: str | None
    company_id: str
    report_as_of: str | None
    scope: str
    currency: str
    purpose: str
    status: str
    route: str | None
    reason_code: str | None      # 最终决策的 reason_code（DECIDED 时来自 decision）
    invoke_reason: str | None    # 触发 fallback 的原因（非 DECIDED 时来自 RouterResult）
    decided_by: str | None
    rule_version: str
    error_code: str | None
    filters: dict


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def write_router_audit(result, need, context, logs_dir: Path = LOGS_DIR) -> str:
    """落盘一条 Router 审计；任何写失败抛 RouterAuditError。"""
    decision = result.decision
    audit = RouterAudit(
        trace_id=result.trace_id,
        timestamp=_now(),
        need_id=need.need_id,
        section_id=need.section_id,
        question=need.question,
        time_scope=need.time_scope,
        company_id=context.company_id,
        report_as_of=context.report_as_of,
        scope=context.scope,
        currency=context.currency,
        purpose=context.purpose,
        status=result.status,
        route=decision.route if decision is not None else None,
        reason_code=decision.reason_code if decision is not None else None,
        invoke_reason=result.reason_code,
        decided_by=decision.decided_by if decision is not None else None,
        rule_version=decision.rule_version if decision is not None else "",
        error_code=result.error_code,
        filters=decision.filters if decision is not None else {},
    )
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        qhash = hashlib.sha256((need.question or "").encode("utf-8")).hexdigest()[:12]
        tid = (result.trace_id or "")[:6]
        path = logs_dir / f"{audit.timestamp}__{qhash}__{tid}.jsonl"
        path.write_text(json.dumps(asdict(audit), ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return str(path)
    except Exception as e:
        raise RouterAuditError(f"router audit 落盘失败: {e}") from e


if __name__ == "__main__":
    import sys
    print(f"LOGS_DIR = {LOGS_DIR}")
    sys.exit(0)
