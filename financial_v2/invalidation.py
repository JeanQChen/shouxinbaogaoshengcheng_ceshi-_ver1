"""A6-5 失效与重建边界（任务书 §9）：定向失效。

职责边界（CLAUDE.md / 任务书硬要求）：
- 快照固化 record set / source / reconciliation / resolution 依赖（已由 snapshot_id 派生 +
  snapshot 头字段承载）；
- 依赖漂移 → 追加 snapshot_validity=`stale` 事件（快照本体不可变，历史 MetricResult 保留审计）；
- 失效检测必须定向：不因无关公司 / 来源 / 期间变化使全部快照失效；
- current 查询与指标计算均不再采用 stale 快照（current_snapshot / compute_metric 已处理）；
- Formula 新版本只影响对应旧公式结果的当前计算口径，不使快照 stale（公式版本不在失效依赖内）；
- 本模块只返回依赖与失效范围；自动重生成财务章节/结论属于 Phase 1F-B，不在此实现。

定向失效以依赖筛选候选快照，再逐个走 snapshots.detect_stale_snapshot 复核 → 真正漂移才追加
stale 事件（幂等：已 stale 不产生新事件）。绝不「全库无条件置 stale」。

CLI:
  python -m financial_v2.invalidation invalidate --snapshot <id> [--by <who>] [--reason <text>]
  python -m financial_v2.invalidation invalidate --record-set <id>
  python -m financial_v2.invalidation invalidate --resolution <id>
  python -m financial_v2.invalidation invalidate --company <id>
  python -m financial_v2.invalidation list-stale [--company <id>]
  （--db 为全局参数，须置于子命令前）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from financial_v2 import schema as S
from financial_v2 import snapshots
from financial_v2 import store

logger = logging.getLogger(__name__)


@dataclass
class InvalidationResult:
    """一次定向失效的结果（§9：返回依赖与失效范围，不含重生成）。"""

    snapshot_id: str
    stale: bool                          # 检测后当前是否 stale
    event_id: str | None                 # 本次追加的 stale 事件（已 stale 则 None）
    reasons: list[str]
    dependencies: dict[str, list[str]] = field(default_factory=dict)


def _dependent_snapshots(predicate) -> list[S.FinancialSnapshot]:
    return [s for s in store.list_snapshots() if predicate(s)]


def invalidate_snapshot(snapshot_id: str, invalidated_by: str | None = None,
                        invalidated_reason: str | None = None) -> InvalidationResult:
    """检测单个快照依赖漂移；已漂移则追加 stale 事件（幂等）。"""
    detect = snapshots.detect_stale_snapshot(snapshot_id)
    event_id: str | None = None
    if detect.stale:
        event_id = store.invalidate_snapshot(snapshot_id, invalidated_by, invalidated_reason)
    return InvalidationResult(
        snapshot_id=snapshot_id, stale=detect.stale, event_id=event_id,
        reasons=detect.reasons, dependencies=detect.dependencies)


def invalidate_for_record_set(record_set_id: str, invalidated_by: str | None = None,
                              invalidated_reason: str | None = None) -> list[InvalidationResult]:
    """定向：只失效依赖指定 record_set 的快照。"""
    snaps = _dependent_snapshots(lambda s: record_set_id in s.record_set_ids)
    return [invalidate_snapshot(s.snapshot_id, invalidated_by, invalidated_reason)
            for s in snaps]


def invalidate_for_resolution(resolution_id: str, invalidated_by: str | None = None,
                              invalidated_reason: str | None = None) -> list[InvalidationResult]:
    """定向：只失效依赖指定 resolution 的快照。"""
    snaps = _dependent_snapshots(lambda s: resolution_id in s.resolution_versions)
    return [invalidate_snapshot(s.snapshot_id, invalidated_by, invalidated_reason)
            for s in snaps]


def invalidate_for_source(source_document_id: str, invalidated_by: str | None = None,
                          invalidated_reason: str | None = None) -> list[InvalidationResult]:
    """定向：只失效依赖指定 source_document 的快照（经 source_version → source_document_id 映射）。"""
    def _depends(s: S.FinancialSnapshot) -> bool:
        for sv in s.source_versions:
            src = store.get_source_version(sv)
            if src is not None and src.source_document_id == source_document_id:
                return True
        return False

    snaps = _dependent_snapshots(_depends)
    return [invalidate_snapshot(s.snapshot_id, invalidated_by, invalidated_reason)
            for s in snaps]


def invalidate_for_company(company_id: str, invalidated_by: str | None = None,
                           invalidated_reason: str | None = None) -> list[InvalidationResult]:
    """定向（公司级）：重检该公司全部快照，只失效真正漂移者。"""
    snaps = store.list_snapshots(company_id)
    return [invalidate_snapshot(s.snapshot_id, invalidated_by, invalidated_reason)
            for s in snaps]


def stale_snapshot_ids(company_id: str | None = None) -> list[str]:
    """列出当前 stale / superseded 的快照 id（供审计与 Phase 1F-B 影响分析）。"""
    ids: list[str] = []
    for s in store.list_snapshots(company_id):
        v = store.latest_snapshot_validity(s.snapshot_id)
        if v in ("stale", "superseded"):
            ids.append(s.snapshot_id)
    return ids


# ---------------------------------------------------------------------------
# CLI（§11 / CLAUDE.md：每个核心模块可独立运行）
# ---------------------------------------------------------------------------

def _result_to_dict(r: InvalidationResult) -> dict:
    return {
        "snapshot_id": r.snapshot_id,
        "stale": r.stale,
        "event_id": r.event_id,
        "reasons": r.reasons,
        "dependencies": r.dependencies,
    }


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m financial_v2.invalidation", description="A6-5 定向失效")
    parser.add_argument("--db", default=str(store.DEFAULT_DB_PATH),
                        help="SQLite 库路径（dev/test 注入临时库）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inv = sub.add_parser("invalidate", help="定向失效")
    p_inv.add_argument("--snapshot", dest="snapshot_id", default=None)
    p_inv.add_argument("--record-set", dest="record_set_id", default=None)
    p_inv.add_argument("--resolution", dest="resolution_id", default=None)
    p_inv.add_argument("--company", dest="company_id", default=None)
    p_inv.add_argument("--by", default="cli")
    p_inv.add_argument("--reason", default=None)

    p_list = sub.add_parser("list-stale", help="列出 stale 快照")
    p_list.add_argument("--company", dest="company_id", default=None)

    args = parser.parse_args(argv)
    store.init_db(args.db)

    if args.cmd == "invalidate":
        if args.snapshot_id:
            results = [invalidate_snapshot(args.snapshot_id, args.by, args.reason)]
        elif args.record_set_id:
            results = invalidate_for_record_set(args.record_set_id, args.by, args.reason)
        elif args.resolution_id:
            results = invalidate_for_resolution(args.resolution_id, args.by, args.reason)
        elif args.company_id:
            results = invalidate_for_company(args.company_id, args.by, args.reason)
        else:
            parser.error("invalidate 需指定 --snapshot / --record-set / --resolution / --company 之一")
        print(json.dumps([_result_to_dict(r) for r in results], ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "list-stale":
        print(json.dumps(stale_snapshot_ids(args.company_id), ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
