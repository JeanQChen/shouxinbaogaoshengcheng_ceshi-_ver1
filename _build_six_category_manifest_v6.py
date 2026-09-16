"""R2 §八.4：六类真实材料验收 manifest 生成器（v6：强化验收器读 v6 真实 run 目录）。

六类 v6 run 目录 → ``harness/six_category_acceptance.build_six_category_manifest`` 确定性聚合：
- ``verify_category`` 逐类独立重算：读 15 个必需产物（含 ``boundary_verification.json`` /
  ``rolling_read_outcomes.json`` / ``run_manifest.json``），重算 material/assembly/payload
  身份与 hash，派生三轴（material_state / capability_verdict / report_impact）+ 兼容视图
  verdict + 24 个 fail-closed gates + ``artifact_fingerprint``。绝不信任 runner 自报。
- ``explicit_cross_reference`` 复用 ``financial_notes`` v6 run 目录（显式引用事实在该 seed 中）。
- 诚实的负面材料状态（boundary_incomplete / not_obtained / unsupported）绝不标记 accepted，
  但**不**因此制造 failed gate；capability_verdict=PASS 只表示系统正确、可复核地得出了材料状态。

只写 evaluation/results 产物（全新 v6 目录），不改冻结资产。
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
RUN_ID = "r2_sixcat_v6_20260916"
SUFFIX = "20260916"

# cid → v6 run 目录名（explicit_cross_reference 复用 financial_notes，其显式引用事实在财务附注 seed 中）。
CATEGORY_RUN_DIRS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v6_main_business_{SUFFIX}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v6_core_competitiveness_{SUFFIX}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v6_major_subsidiaries_{SUFFIX}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v6_financial_notes_{SUFFIX}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v6_financial_notes_{SUFFIX}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v6_non_300750_fixture_{SUFFIX}",
}

OUT_DIR = RESULTS / "r2_six_category_acceptance_v6_20260916"


def _fmt_closure(m: dict) -> str:
    cc = m.get("closure_conditions") or {}
    parts = []
    for k, v in cc.items():
        parts.append(f"{k}={v.get('satisfied') if isinstance(v, dict) else v}")
    return "; ".join(parts)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    manifest = build_six_category_manifest(
        CATEGORY_RUN_DIRS, run_id=RUN_ID,
        generated_at="20260916T000000Z", results_root=RESULTS)

    OUT_DIR.mkdir(parents=True, exist_ok=False)
    with open(OUT_DIR / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 人读 README 摘要（由 manifest 派生，不另写结论）。
    lines = ["# R2 §八.4 六类真实材料验收（v6：三轴状态模型）\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 v6 run 目录原始事实独立重算聚合，非 runner 自报；"
             "三轴（material_state / capability_verdict / report_impact）不互相自动映射；"
             "诚实的负面材料状态绝不标记 accepted，也不自动构成代码失败。\n",
             "| 类别 | material_state | capability_verdict | report_impact | 兼容 verdict | 说明 |\n",
             "|---|---|---|---|---|---|\n"]
    for cid, c in manifest["categories"].items():
        lines.append(f"| {cid} | {c.get('material_state','')} | "
                     f"{c.get('capability_verdict','')} | {c.get('report_impact','')} | "
                     f"{c['verdict']} | {c['reason']} |\n")
    lines.append("\n## 关闭条件（§七）\n\n")
    for k, v in (manifest.get("closure_conditions") or {}).items():
        lines.append(f"- `{k}`：{v}\n")
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
        "closure_conditions": manifest.get("closure_conditions"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
