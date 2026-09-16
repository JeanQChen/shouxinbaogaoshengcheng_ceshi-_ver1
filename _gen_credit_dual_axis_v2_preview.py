"""R2 §12 修复 E：授信双轴 v2 预览生成器（从真实 R2 材料产物派生，无手写 _FACTS）。

Codex 定点返修结论 6 + 修复 E：旧 credit 预览仍保留旧错误状态（``total_credit_line`` 标
``authority_failed``）与手写 ``_FACTS``。本生成器：
- **删除手写 ``_FACTS``**：事实只从真实 R2 材料切片 run 目录的 ``material_index.json`` +
  ``payload_preview/*.json`` 派生（``harness.credit_fact_extraction``）；
- **重算来源权威**：``authority_valid`` 从真实 Evidence 身份/版本/locator/source hash/
  payload hash 重算（绝不手写 True）；
- **每条事实携带 provenance**：``evidence_id``/``material_id``/``document_id``+``version``/
  ``locator``/``source_content_hash``/``payload_hash``；
- **标量不可靠提取 → not_obtained**（``value=None``，显式缺口，绝不回填）；
- 双轴状态 / ``blocking_policy`` / ``missing_policy`` 仍只经 ``harness.credit_semantics``
  正式函数派生（supporting / non_blocking，绝不 REPORT_BLOCKED）。

只写 evaluation/results 产物目录（全新 run_id，不覆盖历史 credit 预览）。零 LLM/网络/DB 写入。

**定位（R2 §五，必须明示）**：本预览是 **evaluation diagnostic / R3 candidate**，不是正式运行
链接线：授信金额语义模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、
授信 REPORT_BLOCKED 映射、授信正式 Writer/报告展示均属 R3 待办，本轮不扩展、不删除。
依赖指纹经 ``harness.credit_semantics.credit_dependency_fingerprint`` 绑定
``runner_dependency_fingerprint()``（冻结 Contract/SourcePolicy + R2 实现版本）与**材料 run
manifest 自身指纹**（Pack/run 身份，只读校验后取得）；两者任一变化 → 指纹变化。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import credit_authority as CA
from harness import credit_semantics as CS
from harness import run_manifest as RM
from harness.credit_authority import (
    CREDIT_AUTHORITY_VERSION,
    resolve_credit_materials,
)
from harness.credit_fact_extraction import (
    CREDIT_FACT_EXTRACTION_VERSION,
    extract_credit_facts,
)

# 真实 R2 材料切片 run 目录（授信语义的事实来源；可由 argv[1] 覆盖）。
MATERIAL_RUN_DIR = Path(
    "evaluation/results/r2_material_slice_r2_p0_credit_v5_20260916c")
# 权威链数据（严格只读）：harness.db 权威 payload 字节 + evidence.db current doc/set。
HARNESS_DB_PATH = Path("data/harness.db")
EVIDENCE_DB_PATH = Path("data/evidence.db")
COMPANY_ID = "300750"
RUN_ID = "r2_credit_dual_axis_v7_preview_20260916"
OUT_ROOT = Path("evaluation/results")

_ASPECTS = (
    CS.FROZEN_TOTAL_CREDIT_LINE,
    CS.FROZEN_USED_CREDIT,
    CS.FROZEN_UNUSED_CREDIT,
    CS.SUCCESSOR_AUTHORIZED_APPLICATION_CEILING,
)


def _claimed_formal_credit_links(material_run_dir: Path) -> int:
    """材料 run 自报的**可提升**授信正式关联条数（role 正式 + disposition 在白名单内）。"""
    try:
        links = json.loads((material_run_dir / "aspect_links.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    if not isinstance(links, list):
        return 0
    n = 0
    for link in links:
        if not isinstance(link, dict) or link.get("aspect_id") not in _ASPECTS:
            continue
        role = str(link.get("role") or "")
        if str(link.get("disposition") or "") in CA.ROLE_DISPOSITIONS.get(role, ()):
            n += 1
    return n


def _resolve_materials(material_run_dir: Path, *, harness_db_path: Path,
                       evidence_db_path: Path, company_id: str) -> list:
    """经正式权威链解析真实材料（E.1）：绝不读 payload_preview/*.json 的复制字段。

    对全部授信 aspect 取并集，逐材料经 ``TopicMaterialPayloadResolver``（harness.db 权威
    payload 字节）+ ``ReadonlyEvidenceReader``（evidence.db current doc/set）+ 块身份/
    fragment 重算 + **经独立佐证的** aspect association 解析；缺 payload/dangling/损坏信封/
    非 current/跨公司/非提升处置/关联未被边界决策记录佐证 → 逐材料 fail-closed（不产出）。

    材料 run 自报存在可提升授信正式关联、但权威链一条都解析不出 → **矛盾，直接拒绝派生**
    （绝不静默产出 0 事实的「预览」：那会把「关联不可验证」伪装成「无材料」）。
    """
    seen: dict[str, object] = {}
    for aspect_id in _ASPECTS:
        for m in resolve_credit_materials(
                material_run_dir, harness_db_path=harness_db_path,
                evidence_db_path=evidence_db_path, company_id=company_id,
                aspect_id=aspect_id):
            seen[m.material_id] = m
    if not seen:
        claimed = _claimed_formal_credit_links(material_run_dir)
        if claimed:
            raise ValueError(
                f"材料 run 自报 {claimed} 条可提升授信正式关联，但权威链解析出 0 条材料"
                f"（关联/材料库/边界决策记录矛盾，fail-closed）：{material_run_dir}")
    return list(seen.values())


def _material_run_manifest_fingerprint(material_run_dir: Path) -> str:
    """材料 run manifest 自身的权威指纹（E.11/E.3）：只读校验通过后取自 manifest，fail-closed。

    绝不手写、绝不从目录名推断；manifest 被改写 / 版本不符 / 依赖指纹不符 / Contract 或
    SourcePolicy 身份不符 → 直接拒绝派生（无 Pack/run 身份的授信事实不得进入预览）。
    """
    ok, reason, manifest = RM.verify_run_manifest(material_run_dir)
    if not ok:
        raise ValueError(
            f"材料 run manifest 不可验证，拒绝派生授信预览：{material_run_dir}（{reason}）")
    return str(manifest.get("run_manifest_fingerprint") or "")


def _build_preview(material_run_dir: Path, *, harness_db_path: Path,
                   evidence_db_path: Path, company_id: str,
                   run_id: str = RUN_ID, generated_at: str = "20260916T000000Z") -> dict:
    material_run_manifest_fingerprint = _material_run_manifest_fingerprint(material_run_dir)
    materials = _resolve_materials(material_run_dir, harness_db_path=harness_db_path,
                                   evidence_db_path=evidence_db_path, company_id=company_id)
    facts = extract_credit_facts(materials)

    # 双轴（逐 fact → aspect）：来源权威轴 ⊥ 语义支撑轴，只经正式函数。
    # 逐字段取**权威值**并显式传入（含 not_obtained / conflict_status / value），绝不靠缺省
    # 推断：`scope_closed` 缺字段或为 None 一律视为**未闭合**（缺口不得被默认成闭合）。
    fact_axes = [{
        "semantic_type": f.get("semantic_type", ""),
        "authority_valid": bool(f.get("authority_valid", False)),
        "authority_reason": "" if f.get("authority_valid") else "来源权威不通过",
        "scope_closed": f.get("scope_closed") is True,
        "not_obtained": bool(f.get("not_obtained")) or f.get("value") is None,
        "conflict_status": str(f.get("conflict_status") or ""),
    } for f in facts]
    aspect_dual_axis: dict = {}
    for aspect_id in _ASPECTS:
        aspect_dual_axis[aspect_id] = CS.credit_aspect_dual_axis(
            aspect_id, tuple(fact_axes))

    # used/unused 对账：绝不求和/求差（scope_not_reconciled）。
    used = next((f for f in facts
                 if f["semantic_type"] == CS.SEMANTIC_TYPE_USED_CREDIT), None)
    unused = next((f for f in facts
                   if f["semantic_type"] == CS.SEMANTIC_TYPE_UNUSED_CREDIT), None)
    reconciliation = (CS.reconcile_used_unused(used, unused)
                      if used and unused else "scope_not_reconciled")

    changelist = CS.successor_changelist()

    return {
        "preview_version": "credit-dual-axis-v2",
        "fact_extraction_version": CREDIT_FACT_EXTRACTION_VERSION,
        "credit_semantics_version": CS.CREDIT_SEMANTICS_VERSION,
        "credit_authority_version": CREDIT_AUTHORITY_VERSION,
        "credit_dependency_fingerprint": CS.credit_dependency_fingerprint(
            extraction_version=CREDIT_FACT_EXTRACTION_VERSION,
            semantics_version=CS.CREDIT_SEMANTICS_VERSION,
            authority_version=CREDIT_AUTHORITY_VERSION,
            runner_dependency_fingerprint=RM.runner_dependency_fingerprint(),
            material_run_manifest_fingerprint=material_run_manifest_fingerprint,
        ),
        "run_id": run_id,
        "material_run_id": material_run_dir.name,
        "material_run_manifest_fingerprint": material_run_manifest_fingerprint,
        "generated_at": generated_at,
        "purpose": (
            "R2 P1-E：授信双轴 v2 预览，事实经正式权威链（TopicMaterialPayloadResolver 权威 "
            "payload 字节 + ReadonlyEvidenceReader current doc/set + 块身份/fragment 重算 + "
            "正式 aspect association）解析真实材料后确定性派生（无手写 _FACTS、不信任 "
            "payload_preview 复制字段）；逐 aspect 由 harness.credit_semantics 正式函数派生 "
            "双轴；每条事实携带 provenance；authority_valid 从真实 Evidence 身份/版本/locator/"
            "双哈希重算；total_credit_line 是 not_obtained（在本轮已纳入材料及检索范围内未取得），"
            "绝不 authority_failed；新增 authorized_application_ceiling 的 blocking_policy="
            "supporting、缺失 non_blocking（NOT REPORT_BLOCKED）。"),
        "facts": facts,
        "aspect_dual_axis": aspect_dual_axis,
        "used_unused_reconciliation": {
            "status": reconciliation,
            "rule": "used_credit 与 unused_credit 绝不求和/求差；scope 不一致 → scope_not_reconciled",
        },
        "successor_changelist": changelist,
    }


def _render_md(preview: dict) -> str:
    lines = [
        "# 授信双轴 v2 预览（修复 E：从真实材料派生）",
        "",
        "> **定位：evaluation diagnostic / R3 candidate，不是正式运行链接线。**",
        "> 授信金额语义模式、币种推断、used/unused 业务对账、multi-source conflict 双轴、",
        "> 授信 REPORT_BLOCKED 映射、授信正式 Writer/报告展示均属 R3 待办（R2 §五），本轮不扩展。",
        "",
        f"> run_id `{preview['run_id']}`（全新目录，不覆盖历史 credit 预览）。",
        f"> 事实来源：真实材料切片 run 目录 `{preview['material_run_id']}`（无手写 _FACTS）。",
        "> 双轴状态与阻断策略只经 `harness.credit_semantics` 正式函数派生，零 LLM/网络。",
        f"> 依赖指纹 `{preview['credit_dependency_fingerprint']}`"
        "（extractor/semantics/authority 三版本绑定，E.11）。",
        f"> 材料 run manifest 指纹 `{preview.get('material_run_manifest_fingerprint','')}`"
        "（Pack/run 身份，只读校验后取得）。",
        "",
        "## 逐 aspect 双轴状态（来源权威轴 ⊥ 语义支撑轴）",
        "",
        "| aspect | 来源权威 | 语义支撑 | 语义说明 |",
        "|---|---|---|---|",
    ]
    for aspect_id, ax in preview["aspect_dual_axis"].items():
        lines.append(f"| `{aspect_id}` | `{ax['authority_status']}` | "
                     f"`{ax['semantic_status']}` | {ax['semantic_reason']} |")
    lines.append("")
    lines.append("**关键纠正**：`total_credit_line` 的语义支撑是 `not_obtained`（实际获批授信总额"
                 "在本轮已纳入材料及检索范围内未取得，已有事实是拟申请上限），来源权威仍 `valid` "
                 "—— 绝不标 `authority_failed`。")
    lines.append("")
    lines.append("## 事实（从真实材料派生 + provenance）")
    lines.append("")
    lines.append("| 语义类型 | 文本 | 值 | 口径闭合 | evidence_id | 材料 | 定位 |")
    lines.append("|---|---|---|---|---|---|---|")
    for f in preview["facts"]:
        if f.get("not_obtained"):
            lines.append(f"| `{f['semantic_type']}` | (not_obtained) | — | — | "
                         f"{f.get('evidence_id','')} | {f.get('material_id','')} | "
                         f"{f.get('locator','')} |")
        else:
            lines.append(f"| `{f['semantic_type']}` | {f.get('text','')} | {f.get('value','')} | "
                         f"{'是' if f.get('scope_closed') else '否（口径未闭合）'} | "
                         f"{f.get('evidence_id','')} | {f.get('material_id','')} | "
                         f"{f.get('locator','')} |")
    lines.append("")
    lines.append("> 每条事实携带 `evidence_id`/`material_id`/`document_id`+`document_version`/"
                 "`locator`/`source_content_hash`/`payload_hash`（见 JSON）。")
    lines.append("")
    lines.append(f"## used/unused 对账：`{preview['used_unused_reconciliation']['status']}`")
    lines.append("")
    lines.append("> 两者 entity_scope/facility_scope/document 不一致，绝不求和/求差。")
    lines.append("")
    cl = preview["successor_changelist"]
    add = cl["add_aspect"]
    lines.append("## 后继 changelist（successor_changelist 派生）")
    lines.append("")
    lines.append(f"- 新增 aspect：`{add['aspect_id']}`（{add['label']}）")
    lines.append(f"- `blocking_policy`：`{add['blocking_policy']}`（supporting，非 REPORT_BLOCKED）")
    lines.append(f"- `missing_policy`：`{add['missing_policy']}`")
    lines.append(f"- 文本纠正：{cl['text_correction']['correct']}")
    lines.append("")
    lines.append("## 硬规则")
    for r in cl["hard_rules"]:
        lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    material_run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else MATERIAL_RUN_DIR
    run_id = sys.argv[2] if len(sys.argv) > 2 else RUN_ID
    generated_at = sys.argv[3] if len(sys.argv) > 3 else "20260916T000000Z"

    preview = _build_preview(
        material_run_dir, harness_db_path=HARNESS_DB_PATH,
        evidence_db_path=EVIDENCE_DB_PATH, company_id=COMPANY_ID,
        run_id=run_id, generated_at=generated_at)
    out_dir = OUT_ROOT / run_id
    if out_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "credit_dual_axis_v2_preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "credit_dual_axis_v2_preview.md").write_text(
        _render_md(preview), encoding="utf-8")

    print(json.dumps({
        "run_id": run_id,
        "material_run_dir": str(material_run_dir),
        "out_dir": str(out_dir),
        "fact_count": len(preview["facts"]),
        "aspect_dual_axis": {
            a: {"authority": preview["aspect_dual_axis"][a]["authority_status"],
                "semantic": preview["aspect_dual_axis"][a]["semantic_status"]}
            for a in _ASPECTS
        },
        "blocking_policy": preview["successor_changelist"]["add_aspect"]["blocking_policy"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
