"""Eval: B1/B2 中间预览验收 runner 专项（case-specific / 只读 / fail-closed）。

用法: python -m evals.test_intermediate_preview

覆盖（任务书本轮收口前定点修复）：
- 输入主体与文档 / 快照匹配（subject / document_id / snapshot_id 均由权威数据派生）；
- 其它主体不会静默复用 300750 数据（fail-closed，拒绝产出错主体报告）；
- 调用前后 evidence.db / financial_v2.db 的 hash/mtime 不变（严格只读，不 init_db / 不写）；
- 表5-10 收入、表5-11 成本、错误收入判定继续成立（金额归一化 + A5 revenue_cost_mismatch）。

依赖真实 data/evidence.db 与 data/financial_v2.db（git 忽略的本地库）；缺库时整组 skip
（本验收 runner 是 300750 case-specific，必须落在真实库上才有意义，不在缺库环境下假通过）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import build_intermediate_preview as B

_EV_DB = Path(__file__).resolve().parent.parent / "data" / "evidence.db"
_FIN_DB = Path(__file__).resolve().parent.parent / "data" / "financial_v2.db"


def _fingerprint(path: Path):
    if not path.exists():
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (digest, path.stat().st_mtime_ns)


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

    if not _EV_DB.exists() or not _FIN_DB.exists():
        skipped += 1
        details.append("SKIP: 需要真实 data/evidence.db 与 data/financial_v2.db（300750 case fixture）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    ev_before = _fingerprint(_EV_DB)
    fin_before = _fingerprint(_FIN_DB)

    # ---- 1) 主体 / 文档 / 快照派生匹配（只读）----
    with tempfile.TemporaryDirectory() as td:
        res = B.run("300750", out_root=td, run_id="t1")
        check(res["subject"] == "宁德时代", "主体派生：subject == 宁德时代")
        check(res["document_id"] == "NDSD_KCZ_2026", "文档派生：document_id == NDSD_KCZ_2026")
        check(res["snapshot_id"] == "snap-490c67acbc3ad4b770087eb817b2c9e3"
              and res["snapshot_company_id"] == "300750",
              "快照派生：current 指针 + 主体匹配")
        check(res["revenue_fact_count"] == 15 and res["cost_fact_count"] == 15,
              "事实计数：收入 15 / 成本 15")

        # ---- 2) 表5-10 收入 / 表5-11 成本 + 错误收入判定继续成立 ----
        facts = json.loads(
            (Path(res["output_dir"]) / "facts.json").read_text(encoding="utf-8"))
        rev = [f for f in facts if f["revenue_cost_category"] == "revenue"
               and f["business_segment"] == "动力电池系统" and f["period"] == "2025-12-31"]
        cst = [f for f in facts if f["revenue_cost_category"] == "cost"
               and f["business_segment"] == "动力电池系统" and f["period"] == "2025-12-31"]
        check(len(rev) == 1 and rev[0]["value"] == "316506369000.0"
              and rev[0]["evidence_id"] == "53c9b3721904922b1afa7cff95d39d22"
              and rev[0]["item_code"] == "OPERATING_REVENUE",
              "表5-10 收入：动力电池 2025 → 316,506,369,000 元（绑定表题块 evidence_id）")
        check(len(cst) == 1 and cst[0]["value"] == "241064397000.0"
              and cst[0]["evidence_id"] == "d1606fc111d0de496c337714ddbb3dfa"
              and cst[0]["item_code"] == "OPERATING_COST",
              "表5-11 成本：动力电池 2025 → 241,064,397,000 元（绑定数据块 evidence_id）")
        mismatch_cases = [a for a in res["a5_results"] if a["mismatch"]]
        check(len(mismatch_cases) == 2 and all(a["hint"] == "revenue"
              and a["categories"] == ["cost"] for a in mismatch_cases),
              "A5 错误收入判定：成本写收入 → revenue_cost_mismatch ×2（含 specific 绑定）")

        # ---- 3) 调用前后两库 hash/mtime 不变（严格只读）----
        check(_fingerprint(_EV_DB) == ev_before,
              "只读不变：evidence.db hash/mtime 调用前后一致")
        check(_fingerprint(_FIN_DB) == fin_before,
              "只读不变：financial_v2.db hash/mtime 调用前后一致")

    # ---- 4) 其它主体 fail-closed（不静默复用 300750 数据）----
    try:
        B.run("000001", out_root=tempfile.mkdtemp(), run_id="t_other")
        check(False, "其它主体：应 fail-closed 拒绝，但未抛错")
    except B.AcceptanceRunnerError as e:
        check("300750" in str(e) and "不支持其它 company" in str(e),
              "其它主体：fail-closed 拒绝（不复用 300750 数据）")
    check(_fingerprint(_EV_DB) == ev_before and _fingerprint(_FIN_DB) == fin_before,
          "只读不变：fail-closed 分支也未触碰任何库")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    import json as _json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(_json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
