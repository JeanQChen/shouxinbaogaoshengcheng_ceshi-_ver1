"""R2 §八：六类真实材料验收 manifest 生成器（v11 = 续页扩读来源定点修复后的一轮）。

与 v10 相比，本轮唯一的生产差异是 **trace 逐步骤落盘真实锚点块身份**
（``ExpansionStep.anchor_evidence_id``），验收侧据此重建 seed/frontier 闭包，
不再把「seed 自身相邻读取不带目标参数」推断成「锚点不在任何 frontier」。v10 目录
（修复前的那一轮）原样保留，作为「修复前 3b 为假」的证据。

**显式引用能力类别的 run 绑定（§四 P1-C，事前陈述的能力判据，非事后挑样本）**：

`capability_verdict` 回答的是「系统是否**正确实现了**显式引用能力」，不是「某个样本恰好
有没有走到这一步」。因此受验 run 必须满足事前可陈述的三条判据：①真实材料正文（seed 或
已采纳材料）含通用引用标记；②该 aspect 的边界策略**可用**（``boundary_policy_unavailable``
会让整轮扩读 fail-closed，任何方向都读不了，此时该 run 对本能力**无信息量**，不是「不利
样本」）；③该 run 的真实 ``mode=explicit_reference`` 尝试在 trace 中可复核（锚点/参数/
outputs/停止原因），且目标与本 seed 同 document_id/document_version/evidence_set_version。
满足者中优先「真实解析出并通过 assembly 采纳了目标」的 run（正向证明最强）；无正向样本时
才用「真实尝试 + 目标 dangling」的 run（§21.6 明确允许 ``not_obtained``+PASS）。

按此判据，v11 满足者为 ``major_subsidiaries``：candidate-13 的 seed 正文含「如下表」等结构表
引用标记，trace step 2（``mode=explicit_reference``、``reference_target="如下表"``、
``anchor=c783f227…``）真实 outputs 出目标块 ``049de1a2…``（p43，同文档同版本），并已作为
正式材料 ``mat-af87f339…`` 采纳、形成 ``relation="reference"`` 的 assembly。
**各候选 run 的原始状态在下方「显式引用逐 run 原始状态」一节逐条列出**（由生产验收器独立
重算，不由本脚本自报），不隐藏、不删除任一 run。要点：

- ``core_competitiveness``：真实尝试 + 目标 dangling（``cross reference target dangling``，
  目标为已采纳块中发现的「如下表/下表」，锚点之后有界窗口内无可确定性解析的表结构）
  ⇒ 诚实负面 ``not_obtained`` + capability PASS；该 run 自身类别条目完整保留；
- ``financial_notes``：正文含真实命名引用标记，但 aspect
  ``company_finance.notes_to_financial_statements`` **不在冻结 Contract v2**（
  ``templates/contracts/standard_v3.yaml``）的 ``topic_harness`` 覆盖内 ⇒
  ``boundary_policy_unavailable`` ⇒ 整轮 fail-closed（trace 只有 resolve_seed + stop）；
  该类别**如实记 not_exercised / NOT_TESTED**，并在 README 记录其根因；
- ``main_business`` / ``non_300750_fixture``：正文无通用引用标记 ⇒ not_exercised。

**与 v9/v10 的绑定差异必须显式披露**：v9/v10 把本类别绑在 ``financial_notes`` 上（当时其
trace 仍能跑出 ``cross reference target dangling``）；v11 的 P1-A.2 fail-closed 使该 aspect
在整轮层面不可扩读，因此该 run 对本能力不再有信息量。本轮的重新绑定是**判据驱动的**，
不是删除不利样本：``financial_notes`` 目录与它自身类别的条目一并保留，其 NOT_TESTED 状态
与根因在 README 与条件 7 审计中如实披露，供用户/Codex 裁决。
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
    verify_category,
)

RESULTS = Path("evaluation/results")
RUN_ID = "r2_sixcat_v11_20260916"
SUFFIX = "20260916"

# cid → v11 run 目录名。``explicit_cross_reference`` 绑定 major_subsidiaries：该 run 是本轮
# 唯一「边界策略可用 + 真实引用标记 + 真实解析出并采纳目标」的 run（规则见模块 docstring）；
# 其余 run 的显式引用状态（not_exercised / dangling）在条件 7 的逐类别审计里逐条披露。
CATEGORY_RUN_DIRS = {
    CATEGORY_MAIN_BUSINESS: f"r2_material_slice_r2_sixcat_v11_main_business_{SUFFIX}",
    CATEGORY_CORE_COMPETITIVENESS: f"r2_material_slice_r2_sixcat_v11_core_competitiveness_{SUFFIX}",
    CATEGORY_MAJOR_SUBSIDIARIES: f"r2_material_slice_r2_sixcat_v11_major_subsidiaries_{SUFFIX}",
    CATEGORY_FINANCIAL_NOTES: f"r2_material_slice_r2_sixcat_v11_financial_notes_{SUFFIX}",
    CATEGORY_EXPLICIT_CROSS_REFERENCE: f"r2_material_slice_r2_sixcat_v11_major_subsidiaries_{SUFFIX}",
    CATEGORY_NON_300750_FIXTURE: f"r2_material_slice_r2_sixcat_v11_non_300750_fixture_{SUFFIX}",
}

OUT_DIR = RESULTS / "r2_six_category_acceptance_v11_20260916"


def _xref_specimen_rows() -> list[dict]:
    """逐 run 独立重算显式引用四态（生产验收器，非本脚本自报），供 README 如实披露。"""
    rows: list[dict] = []
    for cid, run_name in CATEGORY_RUN_DIRS.items():
        run_dir = RESULTS / run_name
        if not run_dir.is_dir():
            rows.append({"run": run_name, "state": "<run dir missing>"})
            continue
        v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, run_dir)
        ref = (v.facts or {}).get("explicit_reference_audit") or {}
        rows.append({
            "category_of_run": cid,
            "run": run_name,
            "state": ref.get("state"),
            "declared_targets": ref.get("declared_targets"),
            "attempt_outputs": ref.get("attempt_outputs"),
            "resolved_targets": ref.get("resolved_targets"),
            "unbacked_outputs": ref.get("unbacked_outputs"),
            "target_resolved": ref.get("target_resolved"),
            "target_dangling": ref.get("target_dangling"),
            "dangling_attempted": ref.get("dangling_attempted"),
            "attempt_step_count": ref.get("attempt_step_count"),
            "resolution_attempted": ref.get("resolution_attempted"),
            "same_document_bound": ref.get("same_document_bound"),
            "attempt_documents": ref.get("attempt_documents"),
            "expansion_stop_reasons": (v.facts or {}).get("expansion_stop_reasons"),
            "capability_verdict": v.capability_verdict,
            "material_state": v.material_state,
            "detail": str(ref.get("detail"))[:400],
        })
    return rows


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 本轮自身的 manifest 若已生成过（同一轮内的中间版本），**改名保留**而非删除：
    # run 目录产物一字未改（生产代码最后一次修改早于 run 生成），只有验收侧的派生
    # manifest 需要按修好的条件 7 重算。
    prev = OUT_DIR / "six_category_manifest.json"
    if prev.exists():
        n, target = 1, OUT_DIR / "six_category_manifest.pre_cond7fix.json"
        while target.exists():
            n += 1
            target = OUT_DIR / f"six_category_manifest.pre_cond7fix{n}.json"
        prev.rename(target)
        print(f"[retired] {prev.name} → {target.name}")

    manifest = build_six_category_manifest(
        CATEGORY_RUN_DIRS, run_id=RUN_ID,
        generated_at="20260916T190000Z", results_root=RESULTS)

    with open(OUT_DIR / "six_category_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    lines = ["# R2 §八 六类真实材料验收（v11：续页扩读来源定点修复后的一轮）\n",
             "本 manifest 由 `harness/six_category_acceptance.build_six_category_manifest` "
             "从各 v11 run 目录原始事实独立重算聚合，非 runner 自报；"
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
    lines.append("\n## 显式引用逐 run 原始状态（生产验收器独立重算，全 specimen 披露）\n\n")
    lines.append("`explicit_cross_reference` 类别绑定 "
                 f"`{CATEGORY_RUN_DIRS[CATEGORY_EXPLICIT_CROSS_REFERENCE]}`；"
                 "以下列出本轮**全部**候选 run 的原始状态，未删除任何 run：\n\n")
    lines.append("```json\n"
                 + json.dumps(_xref_specimen_rows(), ensure_ascii=False, indent=2)
                 + "\n```\n\n")
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
