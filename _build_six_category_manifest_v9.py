"""R2 §六：六类真实材料验收 manifest 生成器（v9 = 关闭条件证据收紧轮）。

v9 与 v8 的差别只在**关闭条件的证据来源**（读的是全新 v9 run 目录）：

- 正向对照不再只看 ``capability_verdict == PASS``，而是逐条核对
  真实材料 / 真实边界记录 / 期望结构能力 / 合法身份与无公司硬编码 /
  真实续表证明链（``_main_business_positive_control`` /
  ``_non_300750_positive_control`` / ``_continuation_positive_control``）。
- 「负面结果有证据」绑定真实输入 + 真实扩读 trace + 停止或缺口原因 +
  未读/dangling 记录（``_negative_state_evidence``），不再接受 artifact 哈希。
- 「A–D + 通用材料身份 P1 已关闭」由各项显式不变量与验收结果派生
  （``_abcd_and_identity_closure``），不再等同于「无 integrity gate 失败」。

只写 ``evaluation/results/r2_six_category_acceptance_v9_20260916``（``exist_ok=False``）；
v8 及更早目录一律保留，不读不写不改。
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
RUN_ID = "r2_sixcat_v9_20260916"
SUFFIX = "20260916"

# cid → v9 run 目录名（explicit_cross_reference 复用 financial_notes，其显式引用事实在该 seed 中）。
CATEGORY_RUN_DIRS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v9_main_business_{SUFFIX}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v9_core_competitiveness_{SUFFIX}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v9_major_subsidiaries_{SUFFIX}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v9_financial_notes_{SUFFIX}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v9_financial_notes_{SUFFIX}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v9_non_300750_fixture_{SUFFIX}",
}

OUT_DIR = RESULTS / "r2_six_category_acceptance_v9_20260916"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest = build_six_category_manifest(
        CATEGORY_RUN_DIRS, run_id=RUN_ID,
        generated_at="20260916T120000Z", results_root=RESULTS)

    # 本轮同一 v9 目录内若已有上一版 manifest（关闭条件收紧**之前**的派生），
    # 保留为 ``*.pre_closure_fix.*`` 现场证据而非删除/覆盖；v8 及更早目录完全不动。
    if OUT_DIR.exists():
        for name, kept in (("six_category_manifest.json",
                            "six_category_manifest.pre_closure_fix.json"),
                           ("README.md", "README.pre_closure_fix.md")):
            src = OUT_DIR / name
            if src.exists():
                src.replace(OUT_DIR / kept)
                print(f"[preserved] {name} → {kept}")
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=False)
    with open(OUT_DIR / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 人读 README 摘要（由 manifest 派生，不另写结论）。
    lines = ["# R2 §六 六类真实材料验收（v9：关闭条件证据收紧轮）\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 v9 run 目录原始事实独立重算聚合，非 runner 自报；"
             "三轴（material_state / capability_verdict / report_impact）不互相自动映射；"
             "诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。\n",
             "关闭条件不再使用 weak proxies：正向对照核对真实材料/边界记录/结构能力/"
             "续表证明链，负面结论绑定真实输入+trace+停止原因+未读或 dangling 记录，"
             "A–D 与身份 P1 由各项显式不变量派生。\n",
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
