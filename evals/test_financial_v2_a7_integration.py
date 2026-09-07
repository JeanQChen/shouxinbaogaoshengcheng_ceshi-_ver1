"""Eval: A7 端到端串联集成测试（任务书 §13.3 / §16）。

用法: python -m evals.test_financial_v2_a7_integration

覆盖（证明 A1～A6 全部产物可通过公开接口 + CLI 串联，公司无关，临时 DB / 临时目录注入）：
- 三条主链：source_registry.register_source → excel_extractor.extract_excel →
  normalization.normalize_record_set → reconciliation.run_reconciliation →
  progress.build_request_for_company + progress.run_pipeline →
  （内部走 snapshots.build_snapshot / metrics.compute_all / adapters.report_financial_payload）；
- 三张独立 Excel（资产负债表/利润表/现金流量表，合并口径，两期间）→ 3 Record Set →
  对账 0 冲突 → 快照 → 全部可得指标 → 只读载荷；抽查流动比率/ROE/营收增长率/利息保障
  倍数/毛利率的 exact 状态与 raw/display 值；
- 未标注 scope → 标准化首轮阻断（不伪造 scope）→ metadata_confirmation.confirm gap-fill
  → 重标准化产出记录（复刻真实 300750 数据缺口路径）；
- 双来源一致值 → 对账 0 冲突 → 快照条目单一标准值 + source_refs 全保留（不翻倍）；
- missing 指标（EBITDA/FCF 缺附注输入）落 MISSING_INPUT 而非成功值；
- progress CLI 串联冒烟：python -m financial_v2.progress run 退出码 0 + final_state completed。

不硬编码 300750 / 宁德时代 / 固定金额 / 固定记录 ID。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl

from financial_v2 import excel_extractor as ex
from financial_v2 import metadata_confirmation as meta
from financial_v2 import normalization
from financial_v2 import progress
from financial_v2 import reconciliation as recon
from financial_v2 import schema as S
from financial_v2 import source_registry
from financial_v2 import store

ROOT = Path(__file__).resolve().parent.parent
COMPANY = "ACME"

_DEPS = {"openpyxl": openpyxl.__version__}
_EXTRACTOR_VERSION = "1.0"
_MAPPING_VERSION = "1.0"
_NORMALIZATION_VERSION = "1.0"

_BS_ITEMS = [
    ("流动资产合计", "CURRENT_ASSETS"),
    ("存货", "INVENTORY"),
    ("预付款项", "PREPAYMENTS"),
    ("应收账款", "ACCOUNTS_RECEIVABLE"),
    ("资产总计", "TOTAL_ASSETS"),
    ("流动负债合计", "CURRENT_LIABILITIES"),
    ("负债合计", "TOTAL_LIABILITIES"),
    ("所有者权益合计", "TOTAL_EQUITY"),
]
_INCOME_ITEMS = [
    ("营业总收入", "TOTAL_REVENUE"),
    ("营业成本", "OPERATING_COST"),
    ("利润总额", "TOTAL_PROFIT"),
    ("利息费用", "INTEREST_EXPENSE"),
    ("净利润", "NET_PROFIT"),
]
_CASH_ITEMS = [
    ("经营活动产生的现金流量净额", "OPERATING_CASH_FLOW"),
]


def _tmp_db() -> str:
    fd, p = tempfile.mkstemp(suffix=".db", prefix="eval_fv2_int_")
    os.close(fd)
    return p


def _tmp_dir() -> str:
    return tempfile.mkdtemp(prefix="eval_fv2_int_dir_")


def _cleanup_db(p: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(p + suffix)
        except FileNotFoundError:
            pass


def _write_statement_xlsx(path: str, title: str, period_cols: list[str],
                          rows: list[tuple[str, int, int]]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title.split("(")[0].strip()
    ws.append([title] + period_cols)
    for item, v_cur, v_prior in rows:
        ws.append([item, v_cur, v_prior])
    wb.save(path)


def _write_bs(path: str, title: str, cur: dict[str, int], prior: dict[str, int]) -> None:
    rows = [(name, cur[code], prior[code]) for name, code in _BS_ITEMS]
    _write_statement_xlsx(path, title, ["2024-12-31", "2023-12-31"], rows)


def _write_income(path: str, title: str, cur: dict[str, int], prior: dict[str, int]) -> None:
    rows = [(name, cur[code], prior[code]) for name, code in _INCOME_ITEMS]
    _write_statement_xlsx(path, title, ["2024-12-31", "2023-12-31"], rows)


def _write_cash(path: str, title: str, cur: dict[str, int], prior: dict[str, int]) -> None:
    rows = [(name, cur[code], prior[code]) for name, code in _CASH_ITEMS]
    _write_statement_xlsx(path, title, ["2024-12-31", "2023-12-31"], rows)


def _register(company: str, file_path: str, source_name: str,
              external_id: str) -> tuple[str, str]:
    ctx = S.FinancialSourceContext(
        company_id=company, source_name=source_name, source_class="financial_statement",
        external_document_id=external_id, declared_company_name=company,
        detected_company_name=company)
    res = source_registry.register_source(str(file_path), ctx)
    return res.document.source_document_id, res.version.source_version


def _extract(source_version: str, file_path: str) -> ex.ExcelExtractionResult:
    policy = ex.ExcelExtractionPolicy(
        file_path=str(Path(file_path).resolve()),
        extractor_version=_EXTRACTOR_VERSION, mapping_rule_version=_MAPPING_VERSION,
        normalization_rule_version=_NORMALIZATION_VERSION, dependency_versions=_DEPS)
    return ex.extract_excel(source_version, policy, persist=True)


def _normalize(rs_input: str) -> normalization.NormalizationResult:
    policy = normalization.NormalizationPolicy(
        extractor_version=_EXTRACTOR_VERSION, mapping_rule_version=_MAPPING_VERSION,
        normalization_rule_version=_NORMALIZATION_VERSION, dependency_versions=_DEPS)
    return normalization.normalize_record_set(rs_input, policy, persist=True)


def _chain(company: str, files: list[tuple[str, str, str]]) -> list[str]:
    """登记 + 抽取 + 币种确认 + 标准化，返回各来源 current 输出 record_set_version。

    Excel 抽取的 currency_candidate 恒为 None（中文报表不标注币种，硬约束不默认 CNY），
    故主链必须走 metadata_confirmation.confirm(currency=CNY) 才可产出标准记录 —— 与真实
    300750 数据缺口一致。
    """
    out: list[str] = []
    for file_path, source_name, external_id in files:
        doc_id, sv = _register(company, file_path, source_name, external_id)
        ext = _extract(sv, file_path)
        assert ext.candidates, f"{source_name} 抽取候选为空"
        meta.confirm(company, doc_id, "currency", "CNY",
                     "user_declaration", "集成测试声明", "eval")
        norm = _normalize(ext.record_set_version)
        assert norm.normalized_count > 0, f"{source_name} 标准化记录为空"
        out.append(norm.record_set_version)
    return out


def _metric(payload, formula_id: str, period: str):
    for m in payload.metrics:
        if m.formula_id == formula_id and m.period == period:
            return m
    return None


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

    db = _tmp_db()
    d = _tmp_dir()
    try:
        store.init_db(db)

        # ------------------------------------------------------------------
        # 场景 A：三张独立表（合并口径）→ 全主链 → 已确认指标
        # ------------------------------------------------------------------
        bs = os.path.join(d, "bs.xlsx")
        inc = os.path.join(d, "income.xlsx")
        cf = os.path.join(d, "cash.xlsx")
        _write_bs(bs, "合并资产负债表(单位：万元)", {
            "CURRENT_ASSETS": 200, "INVENTORY": 50, "PREPAYMENTS": 10,
            "ACCOUNTS_RECEIVABLE": 90, "TOTAL_ASSETS": 500, "CURRENT_LIABILITIES": 100,
            "TOTAL_LIABILITIES": 300, "TOTAL_EQUITY": 200,
        }, {
            "CURRENT_ASSETS": 180, "INVENTORY": 45, "PREPAYMENTS": 8,
            "ACCOUNTS_RECEIVABLE": 80, "TOTAL_ASSETS": 400, "CURRENT_LIABILITIES": 90,
            "TOTAL_LIABILITIES": 250, "TOTAL_EQUITY": 150,
        })
        _write_income(inc, "合并利润表(单位：万元)", {
            "TOTAL_REVENUE": 1000, "OPERATING_COST": 600, "TOTAL_PROFIT": 150,
            "INTEREST_EXPENSE": 20, "NET_PROFIT": 120,
        }, {
            "TOTAL_REVENUE": 800, "OPERATING_COST": 500, "TOTAL_PROFIT": 120,
            "INTEREST_EXPENSE": 15, "NET_PROFIT": 100,
        })
        _write_cash(cf, "合并现金流量表(单位：万元)", {
            "OPERATING_CASH_FLOW": 200,
        }, {
            "OPERATING_CASH_FLOW": 150,
        })

        rs_ids = _chain(COMPANY, [
            (bs, "BS.xlsx", "doc-ACME-BS"),
            (inc, "income.xlsx", "doc-ACME-INC"),
            (cf, "cash.xlsx", "doc-ACME-CF"),
        ])
        check(len(rs_ids) == 3 and len(set(rs_ids)) == 3,
              f"场景A：三张独立表产出 3 个 distinct Record Set（{len(set(rs_ids))}）")

        rec = recon.run_reconciliation(COMPANY, rs_ids, persist=True)
        check(rec.conflict_count == 0 and rec.insufficient_scope_count == 0,
              f"场景A：对账 0 冲突（conflict={rec.conflict_count}）")
        check(rec.single_source_count > 0, "场景A：三表科目互斥 → 单来源分组存在")

        req = progress.build_request_for_company(COMPANY, run_id="run-a")
        check(set(req.record_set_ids) == set(rs_ids), "场景A：build_request 取全部 current Record Set")
        check(req.as_of_date == "2024-12-31" and req.scope == "consolidated"
              and req.currency == "CNY", "场景A：build_request 头字段正确")

        res = progress.run_pipeline(req)
        check(res.final_state == "completed" and res.snapshot_id is not None,
              "场景A：run_pipeline → completed 且有 snapshot_id")
        check(res.report_blocked is False and res.payload is not None,
              "场景A：report_blocked=False 且有 payload")

        p = res.payload
        check(p.periods == ["2023-12-31", "2024-12-31"], "场景A：payload 两期有序")
        check(p.validity == "valid", "场景A：payload validity=valid")

        cur_ratio = _metric(p, "SOLV_CURRENT_RATIO", "2024-12-31")
        check(cur_ratio is not None and cur_ratio.status == "CALCULATED_EXACT",
              "场景A：流动比率 exact")
        check(cur_ratio is not None and cur_ratio.raw_value == Decimal("2")
              and cur_ratio.display_value == Decimal("2.00") and cur_ratio.unit == "ratio",
              f"场景A：流动比率 raw=2 display=2.00 unit=ratio（{cur_ratio and cur_ratio.raw_value}）")

        roe = _metric(p, "PROF_ROE", "2024-12-31")
        check(roe is not None and roe.status == "CALCULATED_EXACT"
              and roe.display_value == Decimal("60.00") and roe.unit == "percent",
              f"场景A：ROE=60.00%（{roe and roe.raw_value}）")

        g_rev = _metric(p, "GROWTH_REVENUE", "2024-12-31")
        check(g_rev is not None and g_rev.status == "CALCULATED_EXACT"
              and g_rev.raw_value == Decimal("0.25") and g_rev.display_value == Decimal("25.00"),
              f"场景A：营收增长率=25.00%（{g_rev and g_rev.raw_value}）")

        ic = _metric(p, "SOLV_INTEREST_COVER", "2024-12-31")
        check(ic is not None and ic.status == "CALCULATED_EXACT"
              and ic.raw_value == Decimal("8.5"), f"场景A：利息保障倍数 exact=8.5（{ic and ic.raw_value}）")

        gm = _metric(p, "PROF_GROSS_MARGIN", "2024-12-31")
        check(gm is not None and gm.display_value == Decimal("40.00"),
              f"场景A：毛利率=40.00%（{gm and gm.raw_value}）")

        check(cur_ratio is not None and cur_ratio.formula_version == "1.0"
              and len(cur_ratio.input_snapshot_item_refs) >= 2
              and len(cur_ratio.input_record_refs) >= 2,
              "场景A：指标行含 formula_version + snapshot_item refs + record refs")

        # missing 指标（EBITDA/FCF 缺附注输入）绝不宣称为成功值。
        ebitda = _metric(p, "EBITDA", "2024-12-31")
        check(ebitda is not None and ebitda.status == "MISSING_INPUT",
              f"场景A：EBITDA 缺折旧/摊销 → MISSING_INPUT（{ebitda and ebitda.status}）")
        check(ebitda is not None and ebitda.raw_value is None,
              "场景A：MISSING_INPUT 行 raw_value 为 None（不伪造成功值）")

        # 状态分布：exact 与 missing 并存，无零分母（分母全非零）。
        sc = res.metrics.status_counts
        check(sc.get("CALCULATED_EXACT", 0) > 0, "场景A：status_counts 含 CALCULATED_EXACT")
        check(sc.get("MISSING_INPUT", 0) > 0, "场景A：status_counts 含 MISSING_INPUT")
        check(sc.get("ZERO_DENOMINATOR", 0) == 0, "场景A：无零分母")
        check(sc.get("NOT_APPLICABLE", 0) == 0, "场景A：年报期间无 NOT_APPLICABLE")

        # 汇总证据引用非空 + 去重有序。
        check(len(p.snapshot_item_refs) >= 2 and len(p.source_record_refs) >= 2,
              "场景A：payload 汇总证据引用非空")

        # ------------------------------------------------------------------
        # 场景 B：未标注 scope → 首轮阻断 → confirm gap-fill → 重标准化
        # ------------------------------------------------------------------
        bs2 = os.path.join(d, "bs_noscope.xlsx")
        _write_bs(bs2, "资产负债表(单位：万元)", {
            "CURRENT_ASSETS": 200, "INVENTORY": 50, "PREPAYMENTS": 10,
            "ACCOUNTS_RECEIVABLE": 90, "TOTAL_ASSETS": 500, "CURRENT_LIABILITIES": 100,
            "TOTAL_LIABILITIES": 300, "TOTAL_EQUITY": 200,
        }, {
            "CURRENT_ASSETS": 180, "INVENTORY": 45, "PREPAYMENTS": 8,
            "ACCOUNTS_RECEIVABLE": 80, "TOTAL_ASSETS": 400, "CURRENT_LIABILITIES": 90,
            "TOTAL_LIABILITIES": 250, "TOTAL_EQUITY": 150,
        })
        doc2, sv2 = _register("BRAVO", bs2, "BS_noscope.xlsx", "doc-BRAVO-BS-NOSCOPE")
        ext2 = _extract(sv2, bs2)
        check(ext2.candidates and all(c.scope_candidate is None for c in ext2.candidates),
              "场景B：未标注 scope → 候选 scope_candidate=None")
        norm_before = _normalize(ext2.record_set_version)
        check(norm_before.normalized_count == 0 and norm_before.blocked_count > 0,
              f"场景B：首轮标准化 0 记录、全阻断（不伪造未知 scope，blocked={norm_before.blocked_count}）")

        meta.confirm("BRAVO", doc2, "statement_scope", "consolidated",
                     "user_declaration", "集成测试声明", "eval")
        meta.confirm("BRAVO", doc2, "currency", "CNY",
                     "user_declaration", "集成测试声明", "eval")
        norm_after = _normalize(ext2.record_set_version)
        check(norm_after.normalized_count > 0 and norm_after.blocked_count == 0,
              f"场景B：confirm scope/currency 后重标准化产出 {norm_after.normalized_count} 条记录")
        check(norm_after.record_set_version != norm_before.record_set_version,
              "场景B：确认变化派生出新的输出 record_set_version（不覆盖旧内容）")

        # ------------------------------------------------------------------
        # 场景 C：双来源一致值 → 单一标准值 + source_refs 全保留
        # ------------------------------------------------------------------
        bsA = os.path.join(d, "bsA.xlsx")
        bsB = os.path.join(d, "bsB.xlsx")
        for pth in (bsA, bsB):
            _write_bs(pth, "合并资产负债表(单位：万元)", {
                "CURRENT_ASSETS": 200, "INVENTORY": 50, "PREPAYMENTS": 10,
                "ACCOUNTS_RECEIVABLE": 90, "TOTAL_ASSETS": 500, "CURRENT_LIABILITIES": 100,
                "TOTAL_LIABILITIES": 300, "TOTAL_EQUITY": 200,
            }, {
                "CURRENT_ASSETS": 180, "INVENTORY": 45, "PREPAYMENTS": 8,
                "ACCOUNTS_RECEIVABLE": 80, "TOTAL_ASSETS": 400, "CURRENT_LIABILITIES": 90,
                "TOTAL_LIABILITIES": 250, "TOTAL_EQUITY": 150,
            })
        rs_c = _chain("CHARLIE", [
            (bsA, "BS_A.xlsx", "doc-CHARLIE-BSA"),
            (bsB, "BS_B.xlsx", "doc-CHARLIE-BSB"),
        ])
        rec_c = recon.run_reconciliation("CHARLIE", rs_c, persist=True)
        check(rec_c.conflict_count == 0 and rec_c.matched_count > 0,
              f"场景C：双来源一致 → 0 冲突、存在 matched 分组（matched={rec_c.matched_count}）")

        res_c = progress.run_pipeline(
            progress.build_request_for_company("CHARLIE", run_id="run-c"))
        # 场景 C 仅资产负债表：必算公式（利润/现金流/利息保障等）输入整体缺失 → 阻断；
        # 但双来源一致的 TOTAL_ASSETS 仍作为单一标准值准入（report_blocked ≠ 事务失败）。
        check(res_c.final_state == "waiting_human" and res_c.report_blocked is True,
              "场景C：双来源一致可提交但仅资产负债表→必算公式缺口阻断（waiting_human）")
        items = {(it.standard_item_code, it.report_period): it
                 for it in store.list_snapshot_items(res_c.snapshot_id)}
        ta = items.get(("TOTAL_ASSETS", "2024-12-31"))
        check(ta is not None and ta.amount == Decimal("5000000"),
              f"场景C：TOTAL_ASSETS 单一标准值=500万元→5000000元（{ta and ta.amount}）")
        check(ta is not None and len(ta.source_refs) == 2,
              f"场景C：TOTAL_ASSETS source_refs 全保留（{len(ta.source_refs) if ta else 0}）")

        # ------------------------------------------------------------------
        # 场景 D：progress CLI 串联冒烟
        # ------------------------------------------------------------------
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-m", "financial_v2.progress", "--db", db,
             "run", "--company", COMPANY],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
        check(proc.returncode == 0,
              f"progress CLI run 退出码 0（stderr={proc.stderr.strip()[:120]!r}）")
        if proc.returncode == 0:
            out = json.loads(proc.stdout)
            check(out.get("final_state") == "completed" and out.get("snapshot_id"),
                  "progress CLI 输出 final_state=completed 且含 snapshot_id")
        else:
            check(False, "progress CLI 失败（详见上一条）")

    finally:
        _cleanup_db(db)
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)
