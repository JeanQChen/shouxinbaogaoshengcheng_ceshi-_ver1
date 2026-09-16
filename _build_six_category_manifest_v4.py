"""R2 §12 六类真实材料验收 manifest 生成器（v4：修复 D 强验收器读 v4 真实 run 目录）。

六类 v4 run 目录 → ``harness/six_category_acceptance.build_six_category_manifest`` 确定性聚合：
- ``verify_category`` 逐类读 8 个必需产物，派生 11 fail-closed gates + 类别特异 gates +
  ``artifact_fingerprint``（内容寻址指纹）；绝不信任调用方组装的 material_count/description/
  boundary_incomplete/sample_not_obtained。
- ``explicit_cross_reference`` 复用 ``financial_notes`` v4 run 目录（显式引用事实在财务附注 seed 中），
  从 expansion_trace 的真实 dangling 事实派生 sample_not_obtained。
- ``boundary_incomplete``/``sample_not_obtained`` 类别绝不标记 accepted（聚合层断言）。

只写 evaluation/results 产物（全新 v4 目录），不改冻结资产。
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
RUN_ID = "r2_sixcat_v4_20260915"
SUFFIX = "20260915"

# cid → v4 run 目录名（explicit_cross_reference 复用 financial_notes，其显式引用事实在财务附注 seed 中）。
CATEGORY_RUN_DIRS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v4_main_business_{SUFFIX}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v4_core_competitiveness_{SUFFIX}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v4_major_subsidiaries_{SUFFIX}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v4_financial_notes_{SUFFIX}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v4_financial_notes_{SUFFIX}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v4_non_300750_fixture_{SUFFIX}",
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest = build_six_category_manifest(
        CATEGORY_RUN_DIRS, run_id=RUN_ID,
        generated_at="20260915T000000Z", results_root=RESULTS)

    out_dir = RESULTS / "r2_six_category_acceptance_v4_20260915"
    out_dir.mkdir(parents=True, exist_ok=False)
    with open(out_dir / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 人读 README 摘要（由 manifest 派生，不另写结论）。
    lines = ["# R2 §12 六类真实材料验收（v4，修复 D 强验收器读 v4 run 目录）\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 v4 run 目录原始事实确定性聚合，非 executor 自报；"
             "`boundary_incomplete`/`sample_not_obtained` 绝不标记 accepted。\n",
             "| 类别 | 裁决 | 说明 |\n|---|---|---|\n"]
    for cid, c in manifest["categories"].items():
        lines.append(f"| {cid} | {c['verdict']} | {c['reason']} |\n")
    with open(out_dir / "README.md", "w", encoding="utf-8") as f:
        f.write("".join(lines))

    print(json.dumps({
        "out_dir": str(out_dir),
        "manifest_version": manifest["manifest_version"],
        "verdicts": {c: manifest["categories"][c]["verdict"]
                     for c in manifest["categories"]},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
