"""Eval: Track B Router 评测（run_router_eval 的断言层）—— Phase 2 Commit 8。

用法: python -m evals.test_router_eval

断言：
- accuracy == 1.0（23 题全对，gold 由冻结规则逐题手工标注，非 Router 回填）；
- 分母 == case_id 集合大小（== n_cases，无重复）；
- n_invalid_gold == 0（无 INVALID_GOLD_MAPPING 混入）；
- content_hash 非空且两次计算一致（数据集版本可确定性锁定）；
- 各路由切片 total 之和 == denominator。

不加载 BGE-M3、不做检索、不碰生产库；纯规则路由确定性验证。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import run_router_eval as runner


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

    summary = runner.run_eval()

    check(summary["n_invalid_gold"] == 0,
          f"无 INVALID_GOLD_MAPPING 混入数据集（{summary['n_invalid_gold']}）")
    check(summary["denominator"] == summary["n_cases"],
          f"分母 == case_id 集合大小（{summary['denominator']}）")

    per = summary["per_route"]
    route_total = sum(v["total"] for v in per.values())
    check(route_total == summary["denominator"],
          f"各路由切片 total 之和 == 分母（{route_total}）")

    check(summary["errors"] == [],
          f"无路由错误（{len(summary['errors'])} 条）")
    check(summary["accuracy"] == 1.0,
          f"accuracy == 1.0（{summary['correct']}/{summary['denominator']}）")

    # content_hash 确定性：两次计算一致且非空。
    summary2 = runner.run_eval()
    check(summary["content_hash"] and summary["content_hash"] == summary2["content_hash"],
          "content_hash 非空且确定性（两次一致）")

    for err in summary["errors"]:
        details.append(f"FAIL: [{err['case_id']}] gold={err['gold']} "
                       f"actual={err['actual']} :: {err['question']}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
