"""Demo 环境只读 preflight（Phase 3 最终验收准备）。

用途：在重跑 COMP-SW1（或后续 frozen_final）前，确认 Demo 数据环境是否就绪。**严格只读**：
不调用 `init_db()`，不建表、不迁移、不写任何库；用 SQLite `mode=ro` 只读探测 `--fin-db` /
`--ev-db`。缺失 / 不完整 / 版本不符 / 快照无效一律 fail-closed（exit≠0），并逐项打印恢复方法。

检查项（全部同时成立才算 PASS）：
1. 只读打开 `--fin-db` / `--ev-db`（文件不存在 / 无法只读打开 → fail）；
2. schema 版本：`schema_migrations` 应用序列是 `MIGRATIONS` 声明顺序的合法前缀，且最新
   == `financial_v2.schema.SCHEMA_VERSION`（版本不符 / 空 / 非法前缀 → fail）；
3. 所需表齐全（缺表 → fail）；
4. current Record Set 存在且有 report_periods（推导 as_of_date）；
5. `current_snapshot` 指针解析出快照，且 **`snapshot_validity` 最新状态严格 == "valid"**
   （None / stale / superseded 一律 fail，绝不默认 valid）；
6. `report_blocked=false`、未 quarantine、scope/currency/purpose/as_of 匹配；
7. `snapshot_item` 与 `metric_result` 数量 > 0；
8. Evidence current-set inventory（status=current 且有 current evidence set 的文档）非空；
9. `EXTERNAL_SEARCH_PROVIDER == "bocha"` 且 `BOCHA_API_KEY` 已设置（**绝不打印 Key**）。

公司无关：company / scope / currency / purpose / 库路径全部经 CLI 传入，不硬编码公司名、
金额或科目；schema 版本经 `financial_v2.schema.SCHEMA_VERSION` + `MIGRATIONS` 声明校验，
不硬编码版本号。

CLI:
  python -m scripts.demo_preflight \
    --company 300750 --fin-db data/financial_v2.db --ev-db data/evidence.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from evidence import store as estore
from financial_v2 import schema as fschema
from financial_v2 import store as fstore

logger = logging.getLogger(__name__)

# preflight 只读查询所需的最小表集合（缺任一表 → fail-closed）。
_FIN_TABLES = (
    "schema_migrations",
    "financial_source_document",
    "current_record_set",
    "financial_record_set",
    "current_snapshot",
    "financial_snapshot",
    "snapshot_validity",
    "quarantine",
    "snapshot_item",
    "metric_result",
    "formula_definition",
)


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
# 只读连接（不 init_db，不建表 / 迁移 / 写库）
# ---------------------------------------------------------------------------

def _ro_conn(path: str) -> sqlite3.Connection:
    """以 SQLite 只读模式打开现有库；文件缺失 / 打不开即抛错（fail-closed）。"""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"数据库文件不存在: {path}")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _check_schema_version(conn: sqlite3.Connection) -> tuple[bool, str]:
    """校验 schema_migrations 是 MIGRATIONS 合法前缀，且最新 == SCHEMA_VERSION。"""
    expected = fschema.SCHEMA_VERSION
    declared = fstore.MIGRATIONS[-1][0]
    if expected != declared:
        return False, f"代码不一致：SCHEMA_VERSION={expected} != 最新 migration={declared}"
    known = [v for v, _ in fstore.MIGRATIONS]
    try:
        rows = conn.execute("SELECT version FROM schema_migrations ORDER BY rowid").fetchall()
    except sqlite3.Error as e:
        return False, f"schema_migrations 表缺失或不可读: {e}"
    applied = [r["version"] for r in rows]
    if not applied:
        return False, "schema_migrations 为空（库未初始化 / 不完整）"
    if applied != known[:len(applied)]:
        return False, (f"schema_migrations 应用序列非法前缀: {applied} "
                       f"（期望 {known[:len(applied)]}）")
    latest = known[len(applied) - 1]
    if latest != expected:
        return False, f"最新 migration {latest} != 期望 {expected}（需迁移）"
    return True, f"schema 版本 {latest}（期望 {expected}）"


def _missing_tables(conn: sqlite3.Connection, tables: tuple[str, ...]) -> list[str]:
    try:
        existing = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return list(tables)  # 无法读 schema（0 字节/非库文件）→ 视为全部缺失（fail-closed）
    return [t for t in tables if t not in existing]


def _derive_as_of_date(conn: sqlite3.Connection, company: str) -> str | None:
    """由 current Record Set 的 report_periods 推导 as_of_date（只读，与 progress 一致）。"""
    periods: list[str] = []
    docs = conn.execute(
        "SELECT source_document_id FROM financial_source_document WHERE company_id=?",
        (company,)).fetchall()
    for d in docs:
        cur = conn.execute(
            "SELECT record_set_version FROM current_record_set WHERE source_document_id=?",
            (d["source_document_id"],)).fetchone()
        if cur is None:
            continue
        rs = conn.execute(
            "SELECT report_periods FROM financial_record_set WHERE record_set_version=?",
            (cur["record_set_version"],)).fetchone()
        if rs is None:
            continue
        periods.extend(json.loads(rs["report_periods"] or "[]"))
    return max(periods) if periods else None


def _latest_validity(conn: sqlite3.Connection, snapshot_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM snapshot_validity WHERE snapshot_id=? "
        "ORDER BY event_at DESC, rowid DESC LIMIT 1",
        (snapshot_id,)).fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# 财务检查（严格只读）
# ---------------------------------------------------------------------------

def _check_financial(company: str, scope: str, currency: str,
                     purpose: str, fin_db: str) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []

    try:
        conn = _ro_conn(fin_db)
    except Exception as e:  # noqa: BLE001 — fail-closed
        logger.warning("只读打开 fin-db 失败: %s", e)
        return [PreflightCheck(
            name="financial.db_readonly_open", ok=False,
            detail=f"无法只读打开 --fin-db: {e}",
            recovery="检查 --fin-db 路径；用 `python -m scripts.run_financial_v2_chain` "
                     "重建财务主链")]

    try:
        ver_ok, ver_detail = _check_schema_version(conn)
        checks.append(PreflightCheck(
            name="financial.schema_version", ok=ver_ok, detail=ver_detail,
            recovery=None if ver_ok else "重建财务库 / 执行迁移"))

        missing = _missing_tables(conn, _FIN_TABLES)
        checks.append(PreflightCheck(
            name="financial.required_tables", ok=not missing,
            detail="缺表: " + ", ".join(missing) if missing else "所需表齐全",
            recovery="重建财务库" if missing else None))
        if not ver_ok or missing:
            return checks

        as_of_date = _derive_as_of_date(conn, company)
        if as_of_date is None:
            checks.append(PreflightCheck(
                name="financial.current_record_set", ok=False,
                detail=f"company={company} 无 current Record Set / report_periods",
                recovery="重建财务主链（run_financial_v2_chain / make demo-data）"))
            checks.append(PreflightCheck(
                name="financial.active_snapshot_id", ok=False,
                detail="无 current Record Set", recovery="重建财务主链"))
            return checks
        checks.append(PreflightCheck(
            name="financial.current_record_set", ok=True,
            detail=f"as_of_date={as_of_date}"))

        snap = conn.execute(
            "SELECT s.* FROM current_snapshot c "
            "JOIN financial_snapshot s ON s.snapshot_id = c.snapshot_id "
            "WHERE c.company_id=? AND c.scope=? AND c.currency=? AND c.as_of_date=? "
            "AND c.purpose=?",
            (company, scope, currency, as_of_date, purpose)).fetchone()
        if snap is None:
            checks.append(PreflightCheck(
                name="financial.active_snapshot_id", ok=False,
                detail=f"company={company} as_of={as_of_date} 无 current 快照指针",
                recovery="重建财务主链"))
            return checks

        snapshot_id = snap["snapshot_id"]

        validity = _latest_validity(conn, snapshot_id)
        checks.append(PreflightCheck(
            name="financial.validity", ok=validity == "valid",
            detail=f"{validity or 'None'}" + ("" if validity == "valid" else "（要求 valid）"),
            recovery=None if validity == "valid" else "重新构建快照 / 修复 validity"))

        blocked = bool(snap["report_blocked"])
        checks.append(PreflightCheck(
            name="financial.report_blocked", ok=not blocked,
            detail=f"report_blocked={blocked}",
            recovery=None if not blocked else "处理被阻断快照（人工门）"))

        q = conn.execute(
            "SELECT 1 FROM quarantine WHERE object_type='financial_snapshot' "
            "AND object_id=? LIMIT 1", (snapshot_id,)).fetchone()
        checks.append(PreflightCheck(
            name="financial.quarantine", ok=q is None,
            detail="未 quarantine" if q is None else "被 quarantine",
            recovery=None if q is None else "解除 quarantine / 重建快照"))

        dims_ok = (snap["scope"] == scope and snap["currency"] == currency
                   and snap["purpose"] == purpose and snap["as_of_date"] == as_of_date)
        checks.append(PreflightCheck(
            name="financial.scope_currency_purpose", ok=dims_ok,
            detail=(f"scope={snap['scope']} currency={snap['currency']} "
                    f"purpose={snap['purpose']} as_of={snap['as_of_date']}"),
            recovery=None if dims_ok else "快照维度与 --scope/--currency/--purpose 不符"))

        n_items = conn.execute(
            "SELECT COUNT(*) AS c FROM snapshot_item WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()["c"]
        n_metrics = conn.execute(
            "SELECT COUNT(*) AS c FROM metric_result WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()["c"]
        checks.append(PreflightCheck(
            name="financial.snapshot_item_count", ok=n_items > 0, detail=f"{n_items}",
            recovery=None if n_items > 0 else "snapshot_item 为空：重建财务主链"))
        checks.append(PreflightCheck(
            name="financial.metric_result_count", ok=n_metrics > 0, detail=f"{n_metrics}",
            recovery=None if n_metrics > 0 else "metric_result 为空：重建财务主链"))

        # 公式注册表完整性：formula_definition 非空，且本快照每个 metric_result 的
        # (formula_id, formula_version) 都能在 formula_definition 找到。缺失会令
        # CitationAuthority 把真实指标判 formula_not_found（→ 批量重复 rework target）。
        n_formulas = conn.execute(
            "SELECT COUNT(*) AS c FROM formula_definition").fetchone()["c"]
        checks.append(PreflightCheck(
            name="financial.formula_definition_count", ok=n_formulas > 0,
            detail=f"{n_formulas}",
            recovery=(None if n_formulas > 0 else
                      f"公式注册表为空：运行 `python -m financial_v2.formulas persist "
                      f"--db {fin_db}`")))

        missing_formula_rows = conn.execute(
            "SELECT DISTINCT m.formula_id, m.formula_version FROM metric_result m "
            "WHERE m.snapshot_id=? AND NOT EXISTS (SELECT 1 FROM formula_definition f "
            "WHERE f.formula_id=m.formula_id AND f.formula_version=m.formula_version)",
            (snapshot_id,)).fetchall()
        missing_formulas = [f"{r['formula_id']}@{r['formula_version']}"
                            for r in missing_formula_rows]
        checks.append(PreflightCheck(
            name="financial.metric_formula_versions_present", ok=not missing_formulas,
            detail=("全部指标公式版本已注册" if not missing_formulas
                    else f"缺失公式定义: {missing_formulas}"),
            recovery=(None if not missing_formulas else
                      f"运行 `python -m financial_v2.formulas persist --db {fin_db}` "
                      f"补齐公式注册")))

        active_ok = (validity == "valid" and not blocked and q is None and dims_ok
                     and n_items > 0 and n_metrics > 0
                     and n_formulas > 0 and not missing_formulas)
        checks.append(PreflightCheck(
            name="financial.active_snapshot_id", ok=active_ok, detail=snapshot_id,
            recovery=None if active_ok else "存在缺陷：见上列具体检查"))
        return checks
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evidence 检查（严格只读）
# ---------------------------------------------------------------------------

def _check_evidence(company: str, ev_db: str) -> list[PreflightCheck]:
    try:
        conn = _ro_conn(ev_db)
    except Exception as e:  # noqa: BLE001 — fail-closed
        logger.warning("只读打开 ev-db 失败: %s", e)
        return [PreflightCheck(
            name="evidence.db_readonly_open", ok=False,
            detail=f"无法只读打开 --ev-db: {e}",
            recovery="检查 --ev-db 路径；重建 Evidence current set（make demo-data）")]
    try:
        rows = conn.execute(
            "SELECT d.document_id FROM documents d "
            "JOIN evidence_sets es ON es.company_id=d.company_id "
            "AND es.document_id=d.document_id AND es.document_version=d.document_version "
            "AND es.status='current' "
            "WHERE d.company_id=? AND d.status='current' ORDER BY d.document_id",
            (company,)).fetchall()
    except sqlite3.Error as e:  # 空库/缺 documents/evidence_sets 表 → fail-closed
        return [PreflightCheck(
            name="evidence.current_set_inventory", ok=False,
            detail=f"documents/evidence_sets 表缺失或不可读: {e}",
            recovery="重建 Evidence current set（make demo-data / 入模管线）")]
    finally:
        conn.close()
    current = [r["document_id"] for r in rows]
    ok = len(current) > 0
    return [PreflightCheck(
        name="evidence.current_set_inventory", ok=ok,
        detail=f"{len(current)} 份 current 文档：{current}",
        recovery=None if ok else "重建 Evidence current set（make demo-data / 入模管线）")]


# ---------------------------------------------------------------------------
# 外部搜索提供方（只确认配置，绝不打印 Key）
# ---------------------------------------------------------------------------

def _check_provider() -> list[PreflightCheck]:
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
# 主流程（无 init_db，全程只读）
# ---------------------------------------------------------------------------

def run(*, company: str, scope: str, currency: str, purpose: str,
        fin_db: str, ev_db: str) -> dict:
    checks = (
        _check_financial(company, scope, currency, purpose, fin_db)
        + _check_evidence(company, ev_db)
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
