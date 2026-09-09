"""Eval: 防过拟合 split manifest —— Phase 3 Batch B plan §split manifest。

用法: python -m evals.test_split_manifest

断言（纯逻辑，无 LLM / I/O 副作用）：
- total=41；dev=固定 8（COMP-S1/S2/R1/MV1/CR1/FIN-PM1/FIN-CF1/FIN-GM1）；
- unseen_validation ∈ [8,10]；frozen_final = 41 − 8 − len(unseen_validation)；
- dev / unseen_validation / frozen_final 两两不相交、并集 = 全部 case_id；
- 路由覆盖：unseen_validation 覆盖剩余集出现的全部 expected_route_v2 值；
- 两次 generate 幂等（确定性，不依赖时间戳/run 结果）；
- manifest 不含 gold 字段（gold_answer/gold_evidence/gold 页码绝不进分组）；
- 已提交的 manifest 文件与 generate() 结果一致（在库中可复现）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import split_manifest as SM


DATASET = "evaluation/datasets/v1_baseline.jsonl"
MANIFEST = "evaluation/datasets/v1_baseline.split_manifest.json"

DEV_FIXED = ["COMP-S1", "COMP-S2", "COMP-R1", "COMP-MV1",
             "COMP-CR1", "FIN-PM1", "FIN-CF1", "FIN-GM1"]

_GOLD_KEYS = ("gold_answer", "gold_evidence", "gold_evidence_raw",
              "gold_evidence_groups")


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

    m = SM.generate(DATASET, tuple(DEV_FIXED), seed=42, val_size=9)

    # ---- 总量与三段规模 ----
    check(m["total"] == 41, f"total=41（实际 {m['total']}）")
    check(m["dev"] == DEV_FIXED, "dev = 固定 8 题（顺序与 plan 一致）")
    check(8 <= len(m["unseen_validation"]) <= 10,
          f"unseen_validation ∈ [8,10]（实际 {len(m['unseen_validation'])}）")
    check(len(m["frozen_final"]) == 41 - 8 - len(m["unseen_validation"]),
          "frozen_final = 41 − 8 − len(unseen_validation)")

    # ---- 三段不相交且并集 = 全部 case_id ----
    cases, _ = SM._load_cases(DATASET)
    all_ids = {c["case_id"] for c in cases}
    dev = set(m["dev"])
    uv = set(m["unseen_validation"])
    ff = set(m["frozen_final"])
    check(dev & uv == set() and dev & ff == set() and uv & ff == set(),
          "dev / unseen_validation / frozen_final 两两不相交")
    check(dev | uv | ff == all_ids,
          "三段并集 = 全部 case_id（无遗漏、无多出）")
    check(len(all_ids) == 41, "数据集共 41 题（切分前置校验）")

    # ---- 路由覆盖：unseen_validation 覆盖剩余集全部路由 ----
    remaining_routes = {c["expected_route_v2"] for c in cases
                        if c["case_id"] not in dev}
    uv_routes = {c["expected_route_v2"] for c in cases
                 if c["case_id"] in uv}
    check(remaining_routes <= uv_routes,
          f"unseen_validation 覆盖剩余集全部路由 {sorted(remaining_routes)}")
    check(m["route_coverage"]["covered"] is True, "manifest 记录 covered=true")

    # ---- 幂等：两次 generate 结果一致 ----
    m2 = SM.generate(DATASET, tuple(DEV_FIXED), seed=42, val_size=9)
    check(m == m2, "两次 generate 结果幂等（确定性切分）")

    # ---- 不读 gold：manifest 与分组逻辑均不含 gold 字段 ----
    gold_leak = [k for k in m if any(g in k for g in _GOLD_KEYS)]
    check(not gold_leak, "manifest 顶层不含 gold 字段")
    check(set(SM._ALLOWED_FIELDS) == {"case_id", "section_id", "priority",
                                      "expected_route_v2"},
          "_ALLOWED_FIELDS 只含 case_id/section_id/priority/expected_route_v2（不读 gold）")

    # ---- 已提交 manifest 文件与 generate() 一致（库中可复现） ----
    manifest_path = Path(__file__).resolve().parent.parent / MANIFEST
    if manifest_path.exists():
        on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
        check(on_disk == m, "已提交的 manifest 文件与 generate() 输出一致")
    else:
        skipped += 1
        details.append(f"SKIP: {MANIFEST} 不存在，跳过文件一致性检查")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
