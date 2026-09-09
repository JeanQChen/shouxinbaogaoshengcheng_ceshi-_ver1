"""Demo 环境只读 preflight（Phase 3 最终验收准备）。

用途：在重跑 COMP-SW1（或后续 frozen_final）前，确认 Demo 数据环境是否就绪。**只检查状态**，
不修改 Router / Prompt / 规则 / 数据库，不执行检索，不写任何库。

检查项（缺失即 fail-closed，exit≠0，并逐项打印恢复方法）：
1. 财务快照：current 且 valid 且未 quarantine 的 `active_snapshot_id`（复用
   `routing.context.build_route_context` 的只读健康门：非 None、非 report_blocked、
   非 quarantine、最新 validity 非 stale/superseded）；
2. `report_as_of`、`scope`、`currency`、`purpose`（读 `financial_snapshot` 行）；
3. `snapshot_item` 与 `metric_result` 数量（>0）；
4. Evidence current-set inventory（status=current 且有 current_version 的文档清单）；
5. `EXTERNAL_SEARCH_PROVIDER == "bocha"` 且 `BOCHA_API_KEY` 已设置（**绝不打印 Key**）。

公司无关：company / scope / currency / purpose / 库路径全部经 CLI 传入，不硬编码公司名、金额或科目。

CLI:
  python -m scripts.demo_preflight \
    --company 300750 --fin-db data/financial_v2.db --ev-db data/evidence.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evidence import store as estore
from financial_v2 import store as fstore
from routing import context as routing_context

logger = logging.getLogger(__name__)


@dataclass
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    recovery: str | None = None

    def as_dict(self) -> dict:
        d = {"name": self.name, "ok": self.ok, "detail": self.detail}
        if self.recovery:
            d["recovery"] = self.recovery
        return d


# ---------------------------------------------------------------------------
# 各检查项（只读）
# ---------------------------------------------------------------------------

def _check_financial(company: str, scope: str, currency: str,
                     purpose: str) -> list[PreflightCheck]:
    """财务快照健康门 + 维度 + 数量（复用 build_route_context 的只读解析）。"""
    checks: list[PreflightCheck] = []

    try:
        ctx = routing_context.build_route_context(
            company, scope=scope, currency=currency, purpose=purpose)
    except Exception as e:  # 库未初始化等真实错误：fail-closed
        logger.exception("build_route_context 失败")
        return [PreflightCheck(
            name="financial.snapshot", ok=False,
            detail=f"RouteContext 构建异常: {e}",
            recovery="检查 --fin-db/--ev-db 路径与库是否可打开；用 "
                     "`python -m scripts.run_financial_v2_chain` 重建财务主链")]

    snapshot_id = ctx.snapshot_id
    report_as_of = ctx.report_as_of

    if snapshot_id is None:
        checks.append(PreflightCheck(
            name="financial.active_snapshot_id", ok=False,
            detail=f"company={company} 无 current/valid/未 quarantine 的快照"
                   f"（report_as_of={report_as_of or 'None'}）",
            recovery="重建财务主链：`python -m scripts.run_financial_v2_chain "
                     "--company <id> --db <fin-db> --excel <资产负债.xlsx> "
                     "--excel <利润.xlsx> --excel <现金流.xlsx>`（或 make demo-data）"))
        return checks

    snap = fstore.get_snapshot(snapshot_id)
    validity = fstore.latest_snapshot_validity(snapshot_id)

    checks.append(PreflightCheck(
        name="financial.active_snapshot_id", ok=True,
        detail=f"{snapshot_id}（validity={validity or 'valid'}）"))

    checks.append(PreflightCheck(
        name="financial.report_as_of", ok=bool(report_as_of),
        detail=report_as_of or "None",
        recovery=None if report_as_of else "快照缺少 as_of_date，重建财务主链"))

    if snap is not None:
        checks.append(PreflightCheck(
            name="financial.scope_currency_purpose", ok=True,
            detail=f"scope={snap.scope} currency={snap.currency} "
                   f"purpose={snap.purpose} report_blocked={snap.report_blocked}"))
    else:
        checks.append(PreflightCheck(
            name="financial.scope_currency_purpose", ok=False,
            detail=f"snapshot_id={snapshot_id} 在 financial_snapshot 表缺失",
            recovery="重建财务主链"))

    items = fstore.list_snapshot_items(snapshot_id)
    metrics = fstore.list_metric_results(snapshot_id)
    checks.append(PreflightCheck(
        name="financial.snapshot_item_count", ok=len(items) > 0,
        detail=f"{len(items)}",
        recovery=None if len(items) > 0 else "snapshot_item 为空：重建财务主链"))
    checks.append(PreflightCheck(
        name="financial.metric_result_count", ok=len(metrics) > 0,
        detail=f"{len(metrics)}",
        recovery=None if len(metrics) > 0 else "metric_result 为空：重建财务主链"))
    return checks


def _check_evidence(company: str) -> list[PreflightCheck]:
    """Evidence current-set inventory：status=current 且有 current_version 的文档。"""
    docs = estore.list_documents(company)
    current: list[str] = []
    for d in docs:
        if d.status != "current":
            continue
        if estore.current_document_version(company, d.document_id) is None:
            continue
        current.append(d.document_id)
    ok = len(current) > 0
    return [PreflightCheck(
        name="evidence.current_set_inventory", ok=ok,
        detail=f"{len(current)} 份 current 文档：{current}",
        recovery=None if ok else "重建 Evidence current set（make demo-data / 入模管线）")]


def _check_provider() -> list[PreflightCheck]:
    """外部搜索提供方：仅确认 bocha 与 Key 存在，绝不打印 Key。"""
    checks: list[PreflightCheck] = []
    provider = config.EXTERNAL_SEARCH_PROVIDER
    checks.append(PreflightCheck(
        name="provider.external_search_provider", ok=provider == "bocha",
        detail=provider,
        recovery=None if provider == "bocha" else "在 .env 设 EXTERNAL_SEARCH_PROVIDER=bocha"))
    checks.append(PreflightCheck(
        name="provider.bocha_api_key_present", ok=bool(config.BOCHA_API_KEY),
        detail="已配置" if config.BOCHA_API_KEY else "未配置",
        recovery=None if config.BOCHA_API_KEY else "在 .env 设 BOCHA_API_KEY=<key>"))
    return checks


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(*, company: str, scope: str, currency: str, purpose: str,
        fin_db: str, ev_db: str) -> dict:
    fstore.init_db(fin_db)
    estore.init_db(ev_db)

    checks = (
        _check_financial(company, scope, currency, purpose)
        + _check_evidence(company)
        + _check_provider()
    )
    ok = all(c.ok for c in checks)
    return {
        "company": company,
        "fin_db": fin_db,
        "ev_db": ev_db,
        "ok": ok,
        "passed": sum(1 for c in checks if c.ok),
        "failed": sum(1 for c in checks if not c.ok),
        "checks": [c.as_dict() for c in checks],
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.demo_preflight",
        description="Demo 环境只读 preflight（缺失 fail-closed，不改任何规则/库）")
    parser.add_argument("--company", required=True, help="公司标识（company_id）")
    parser.add_argument("--scope", default="consolidated", help="statement_scope")
    parser.add_argument("--currency", default="CNY", help="currency")
    parser.add_argument("--purpose", default="credit_analysis", help="快照 purpose")
    parser.add_argument("--fin-db", default=str(fstore.DEFAULT_DB_PATH),
                        help="financial_v2 SQLite 库路径")
    parser.add_argument("--ev-db", default=str(estore.DEFAULT_DB_PATH),
                        help="evidence SQLite 库路径")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    result = run(company=args.company, scope=args.scope, currency=args.currency,
                 purpose=args.purpose, fin_db=args.fin_db, ev_db=args.ev_db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv[1:]))
