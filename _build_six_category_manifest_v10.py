"""R2 §八：六类真实材料验收 manifest 生成器（v10 = 续表滚动 frontier / 续页证明防伪 /
显式引用 / 未知态 fail-closed 定点修复后的一轮）。

v10 与 v9 的差别只有**输入 run 目录**（全新 v10 run）与新增的关闭条件证据能力态：

- 条件 3 已拆成 ``same_table_recovery_positive`` 与
  ``table_continuation_expansion_positive``（两页材料经其它途径恢复同一张表**只**使前者
  成立；后者要求同一 seed/frontier 的**真实** table_continuation 步骤真的输出并采纳了
  续页材料）；
- 条件 6 ``tamper_counterexamples_unbypassable`` 具 satisfied + 具体反例清单 + 每条命中的门；
- 条件 7 直接引用并复核清单/aspect 归属/边界验证/未读范围/预算消耗/扩读 trace 与
  续表·引用来源；公司硬编码扫描范围与排除规则显式列出。

只写 ``evaluation/results/r2_six_category_acceptance_v10_20260916``（``exist_ok=False``）；
v9 及更早目录一律保留，不读不写不改；本目录内上一版 manifest 保留为 ``*.pre_closure_fix.*``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.six_category_acceptance import (
    CATEGORY_CORE_COMPETITIVENESS,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    CATEGORY_FINANCIAL_NOTES,
    CATEGORY_MAIN_BUSINESS,
    CATEGORY_MAJOR_SUBSIDIARIES,
    CATEGORY_NON_300750_FIXTURE,
    build_six_category_manifest,
)

RESULTS = Path("evaluation/results")
RUN_ID = "r2_sixcat_v10_20260916"
SUFFIX = "20260916"

# cid → v10 run 目录名（explicit_cross_reference 复用 financial_notes，其显式引用事实在该 seed 中）。
CATEGORY_RUN_DIRS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v10_main_business_{SUFFIX}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v10_core_competitiveness_{SUFFIX}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v10_major_subsidiaries_{SUFFIX}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v10_financial_notes_{SUFFIX}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v10_financial_notes_{SUFFIX}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v10_non_300750_fixture_{SUFFIX}",
}

OUT_DIR = RESULTS / "r2_six_category_acceptance_v10_20260916"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest = build_six_category_manifest(
        CATEGORY_RUN_DIRS, run_id=RUN_ID,
        generated_at="20260916T160000Z", results_root=RESULTS)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    lines = ["# R2 §八 六类真实材料验收（v10：续表 frontier / 续页防伪 / 显式引用 / 未知态定点修复轮）\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 v10 run 目录原始事实独立重算聚合，非 runner 自报；"
             "三轴（material_state / capability_verdict / report_impact）不互相自动映射；"
             "诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。\n",
             "条件 3 拆分为 `same_table_recovery_positive` 与 "
             "`table_continuation_expansion_positive`：两页材料经其它途径（含第二 seed）"
             "恢复同一张表**只**使前者成立；后者要求同一 seed/frontier 的真实 "
             "table_continuation 步骤真的输出**并**采纳了续页材料。\n",
             "| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |\n",
             "|---|---|---|---|---|---|\n"]
    for cid, c in manifest["categories"].items():
        lines.append(f"| {cid} | {c.get('material_state','')} | "
                     f"{c.get('capability_verdict','')} | {c.get('report_impact','')} | "
                     f"{c['verdict']} | {c['reason']} |\n")
    lines.append("\n## 关闭条件（§七，实施方不自行宣布关闭）\n\n")
    for k, v in (manifest.get("closure_conditions") or {}).items():
        lines.append(f"### `{k}`\n\n```json\n"
                     + json.dumps(v, ensure_ascii=False, indent=2) + "\n```\n\n")
    with open(OUT_DIR / "README.md", "w", encoding="utf-8") as f:
        f.write("".join(lines))

    print(json.dumps({
        "out_dir": str(OUT_DIR),
        "manifest_version": manifest["manifest_version"],
        "acceptance_model": manifest.get("acceptance_model"),
        "categories": {c: {"material_state": v.get("material_state"),
                           "capability_verdict": v.get("capability_verdict"),
                           "report_impact": v.get("report_impact"),
                           "verdict": v["verdict"],
                           "failed_gates": v.get("failed_gates"),
                           "reason": v["reason"]}
                       for c, v in manifest["categories"].items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
