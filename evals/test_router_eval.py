"""Eval: Track B Router 评测（run_router_eval 的断言层）—— Phase 2 Commit 6。

用法: python -m evals.test_router_eval

断言（两数据集）：
- 合成 23 题：accuracy == 1.0、severe == 0、denominator == 23、n_invalid_gold == 0；
- 真实 41 题：accuracy >= 0.90、severe == 0、denominator == 41、n_invalid_gold == 0；
- 分母 == case_id 集合大小（== n_cases，无重复）；
- content_hash 非空且两次计算一致（数据集版本可确定性锁定）；
- 各路由切片 total 之和 == denominator。

不加载 BGE-M3、不做检索、不碰生产库；纯规则路由确定性验证。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import run_router_eval as runner

REAL_ACCURACY_MIN = 0.90


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

    both = runner.run_both()
    real = both["real"]
    synth = both["synthetic"]

    # ---- 公共契约（两数据集） ----
    for tag, s in (("real", real), ("synthetic", synth)):
        check(s["n_invalid_gold"] == 0,
              f"[{tag}] 无 INVALID_GOLD_MAPPING 混入（{s['n_invalid_gold']}）")
        check(s["denominator"] == s["n_cases"],
              f"[{tag}] 分母 == case_id 集合大小（{s['denominator']}）")
        route_total = sum(v["total"] for v in s["per_route"].values())
        check(route_total == s["denominator"],
              f"[{tag}] 各路由切片 total 之和 == 分母（{route_total}）")
        check(s["severe"] == 0,
              f"[{tag}] severe 误路由 == 0（{s['severe']}）")

    # ---- 合成题：必须 100% ----
    check(synth["denominator"] == 23,
          f"[synthetic] denominator == 23（{synth['denominator']}）")
    check(synth["accuracy"] == 1.0,
          f"[synthetic] accuracy == 1.0（{synth['correct']}/{synth['denominator']}）")

    # ---- 真实题：允许 <100%，门槛 ≥90% + severe=0 ----
    check(real["denominator"] == 41,
          f"[real] denominator == 41（{real['denominator']}）")
    check(real["accuracy"] >= REAL_ACCURACY_MIN,
          f"[real] accuracy >= {REAL_ACCURACY_MIN:.0%}"
          f"（{real['correct']}/{real['denominator']} = {real['accuracy']:.1%}）")

    # content_hash 确定性：两次计算一致且非空。
    real2 = runner.run_eval(runner.REAL_DATASET)
    synth2 = runner.run_eval(runner.SYNTHETIC_DATASET)
    check(real["content_hash"] and real["content_hash"] == real2["content_hash"],
          "real content_hash 非空且确定性（两次一致）")
    check(synth["content_hash"] and synth["content_hash"] == synth2["content_hash"],
          "synthetic content_hash 非空且确定性（两次一致）")

    for tag, s in (("real", real), ("synthetic", synth)):
        for err in s["errors"]:
            details.append(
                f"MISMATCH: [{tag}:{err['case_id']}] gold={err['gold']} "
                f"actual={err['actual']} severe={err['severe']} :: {err['question']}")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
