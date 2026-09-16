"""Eval: §三 显式引用「触发 / 尝试 / 结果」四态分离反例（P1-2，全部离线/确定性）。

用法: python -m evals.test_r2_explicit_reference_audit

缺陷现场（真实 v8 产物 ``r2_material_slice_r2_sixcat_v8_financial_notes_20260916/``）：
``rolling_read_outcomes.json`` 的 ``targets`` 为 ``[]``、``expansion_trace.jsonl`` 中**没有**
任何 ``mode == "explicit_reference"`` 步骤，但六类验收让
``explicit_cross_reference.target_resolvable`` **通过**并给出 ``capability_verdict=PASS`` ——
「未测试被当成通过」。旧实现的根因是把「无 dangling」当成「目标可解析」。

本用例逐条钉死四态分离（全部由**真实材料正文** + **真实 Expansion trace** + **真实已采纳
材料身份**派生，绝不采信 executor 自报结论字段）：

1. 无触发 + 无尝试 → ``not_exercised`` → ``capability_verdict=NOT_TESTED``（不冒充通过）；
2. 触发但无尝试（= 真实 v8 形状）→ ``not_exercised`` → NOT_TESTED，**无 dangling 也绝不
   标记 ``target_resolvable`` 通过**；
3. 尝试 + 目标 dangling → 材料``not_obtained`` + 能力 PASS（机制跑了并如实判定），但
   ``target_resolvable`` **不得标记通过**（记为「未判定」）；
4. 尝试 + 真实解析成功 → **唯一**允许 ``target_resolvable`` 通过的形态；
5. 篡改反例：目标不是任何已采纳真实材料 / 尝试无目标且正文无标记 / 尝试既无产出又无停止
   与未读记录 → ``contradictory`` → capability FAIL（fail-closed）；
6. 普通跨页相邻读取（``adjacent_after``）即使真的采纳了材料，也不得冒充显式引用能力
   （既不算触发、也不算尝试）。

零 LLM / 零网络 / 零 DB 写入：纯函数 + 临时 run 目录。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.evidence_reader import (  # noqa: E402
    REFERENCE_KIND_NAMED,
    iter_reference_occurrences,
)

from harness.six_category_acceptance import (
    CAPABILITY_FAIL,
    CAPABILITY_NOT_TESTED,
    CAPABILITY_PASS,
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    MATERIAL_STATE_NOT_OBTAINED,
    VERDICT_ACCEPTED,
    VERDICT_SAMPLE_NOT_OBTAINED,
    verify_category,
)

from evals.test_six_category_acceptance import (  # noqa: E402
    _canonical_json,
    _seed,
    _write_run_dir,
    DOC_VERSION,
    DOC_VERSION_B,
    SET_VERSION,
)

CAT = CATEGORY_EXPLICIT_CROSS_REFERENCE
GATE = "explicit_cross_reference.target_resolvable"
ASPECT = "company_finance.notes_to_financial_statements"

# 真实触发语料（通用标记，非公司/页码/表号专用）：正文里的「详见…」引用。
_MARKER_TEXT = "货币资金 详见 24、所有权或使用权受到限制的资产。"
# trace 真实记录的解析尝试声明的引用目标（trace 事实，非 executor 结论字段）。
_DECLARED = "24、所有权或使用权受到限制的资产"
_DANGLING_STOP = "cross reference target dangling"
_ANCHOR_EID = "ev-seed-0001"      # 承载 _MARKER_TEXT 的发起块（seed）


def _named_request_args(**extra) -> dict:
    """§三 P1-3：请求侧 typed occurrence 身份**由真实正文经生产侧同一原语重算**得出。

    绝不手写 marker/start/end/序号（手写的偏移一旦与正文不符，验收侧会 fail-closed —— 这
    正是「请求/绑定/正文三方同源」被验证的形式）。``extra`` 用于覆盖文档身份字段。
    """
    occ = next(o for o in iter_reference_occurrences(_MARKER_TEXT)
               if o.reference_kind == REFERENCE_KIND_NAMED)
    args = {"mode": "explicit_reference", "reference_target": _DECLARED}
    args.update(occ.request_args())
    args.update(extra)
    return args


def _run(base: Path, name: str, **kw):
    """写一个 explicit_cross_reference run 目录并强验收（材料键名可携带真实标记文本）。"""
    _write_run_dir(base, name, aspect_id=ASPECT, **kw)
    return verify_category(CAT, base / name)


def _seed_with_marker():
    s = _seed(ASPECT)
    s["text"] = _MARKER_TEXT
    return s


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

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # ------------------------------------------------------------------
        # 1. 无触发 + 无尝试 → not_exercised → NOT_TESTED
        # ------------------------------------------------------------------
        v = _run(base, "xr_none", materials=("m1", "m2"), trace=[
            {"step_index": 0, "action": "inspect_bounded",
             "arguments": {"mode": "adjacent_after", "limit": 5},
             "outputs": [], "stop_reason": "topic section closed (sibling heading)"}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "not_exercised",
              "§三①：无触发 + 无尝试 → not_exercised")
        check(a["trigger_detected"] is False and a["resolution_attempted"] is False,
              "§三①：触发/尝试两项事实均为假（由真实文本与真实 trace 派生）")
        check(v.capability_verdict == CAPABILITY_NOT_TESTED,
              "§三①：能力未被测过 → capability_verdict=NOT_TESTED（绝不冒充 PASS）")
        check(GATE not in v.passed_gates,
              "§三①：target_resolvable 绝不标记通过")
        check(not v.failed_gates,
              "§三①：「未测试」是能力未测，不是能力失败（不制造 failed gate）")
        check([e["gate"] for e in v.facts["indeterminate_gates"]] == [GATE],
              "§三①：该门被确定为「未判定」（既非通过、也非失败）")
        check("explicit_reference.not_exercised" in v.facts["capability_not_tested_reason"],
              "§三①：显式给出 gate/原因 explicit_reference.not_exercised")
        check(v.verdict != VERDICT_ACCEPTED,
              "§三①：未测试 ⇒ 绝不 accepted（兼容视图同步收紧）")

        # ------------------------------------------------------------------
        # 2. 触发但无尝试（真实 v8 形状：targets=[]，无 explicit_reference 步骤）
        #    旧缺陷：无 dangling ⇒ gate 通过 ⇒ capability PASS。
        # ------------------------------------------------------------------
        v = _run(base, "xr_trigger_no_attempt",
                 seed=_seed_with_marker(), materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "resolve_seed",
                      "arguments": {}, "outputs": ["ev-seed-0001"], "stop_reason": None},
                     {"step_index": 1, "action": "stop", "arguments": {}, "outputs": [],
                      "stop_reason": "boundary policy unavailable: no expansion"}],
                 rolling_targets=[])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "not_exercised",
              "§三②：正文存在真实引用标记但无任何解析尝试 → not_exercised")
        check(a["trigger_from_text"] is True and a["trigger_from_trace"] is False,
              "§三②：触发由**真实材料/seed 正文**派生（通用提取规则，非自报字段）")
        check(a["trigger_targets"], "§三②：提取出的目标文本如实落盘（可人工复核）")
        check(a["resolution_attempted"] is False and a["attempt_step_count"] == 0,
              "§三②：真实 trace 中无 mode=explicit_reference 步骤")
        check(v.facts["explicit_ref_dangling"] is False,
              "§三②：该形态确实「无 dangling」（旧实现据此误判通过）")
        check(GATE not in v.passed_gates,
              "§三②：无 dangling **绝不**等于 target_resolvable —— 该门不得标记通过")
        check(v.capability_verdict == CAPABILITY_NOT_TESTED,
              "§三②：P1-2 修复：未测试的能力不再冒充 PASS（NOT_TESTED）")
        check("explicit_reference.not_exercised" in v.facts["capability_not_tested_reason"],
              "§三②：NOT_TESTED 附带显式 gate/原因")
        check(v.verdict != VERDICT_ACCEPTED,
              "§三②：未测试 ⇒ 绝不 accepted")

        # ------------------------------------------------------------------
        # 3. 尝试 + 目标 dangling → 诚实 not_obtained + 能力 PASS，但门不得通过
        # ------------------------------------------------------------------
        v = _run(base, "xr_dangling", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
            {"step_index": 0, "action": "inspect_bounded",
             "anchor_evidence_id": _ANCHOR_EID,
             "arguments": _named_request_args(),
             "outputs": [], "stop_reason": _DANGLING_STOP}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "dangling", "§三③：识别 + 尝试 + 目标不可达 → dangling")
        check(a["target_dangling"] is True and a["target_resolved"] is False,
              "§三③：dangling 与 resolved 互斥且由真实停止原因派生")
        check(v.material_state == MATERIAL_STATE_NOT_OBTAINED,
              "§三③：材料状态诚实落成 not_obtained（不伪装获得）")
        check(v.capability_verdict == CAPABILITY_PASS,
              "§三③：机制正确、可复核地判出负面结果 → 能力 PASS（冻结定义）")
        check(GATE not in v.passed_gates and GATE not in [g for g, _ in v.failed_gates],
              "§三③：target_resolvable **不得**标记通过（未解析出目标），也未误判为失败")
        check([e["gate"] for e in v.facts["indeterminate_gates"]] == [GATE],
              "§三③：该门如实记为「未判定」")
        check(v.verdict == VERDICT_SAMPLE_NOT_OBTAINED,
              "§三③：兼容视图 sample_not_obtained（诚实负面）")

        # ------------------------------------------------------------------
        # 4. 尝试 + 真实解析成功 → 唯一允许 target_resolvable 通过的形态
        # ------------------------------------------------------------------
        v = _run(base, "xr_resolved", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "anchor_evidence_id": _ANCHOR_EID,
                      "arguments": _named_request_args(
                          company_id="300750",
                          document_id="NDSD_KCZ_2026",
                          document_version=DOC_VERSION,
                          evidence_set_version=SET_VERSION),
                      "outputs": ["ev-m1"], "stop_reason": None}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "resolved",
              "§三④：识别 + 尝试 + 目标落在真实已采纳材料内 → resolved")
        check(a["same_document_bound"] is True,
              "§四④：尝试步骤真实声明了与本 seed 相同的 document_id/version/"
              "evidence_set → 同文档性成立（目标必须保持同 document_id/version）")
        check(a["resolved_targets"] == ["ev-m1"],
              "§三④：解析出的目标绑定**真实已采纳材料**的 evidence 身份")
        check(GATE in v.passed_gates,
              "§三④：只有此形态才允许 target_resolvable 标记通过")
        check(v.capability_verdict == CAPABILITY_PASS,
              "§三④：真实解析成功 → 能力 PASS")

        # ------------------------------------------------------------------
        # 5. 篡改反例（fail-closed）
        # ------------------------------------------------------------------
        # 5a. 声称解析到「真实材料以外」的目标（凭空 evidence id）。
        v = _run(base, "xr_unbacked", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "arguments": {"mode": "explicit_reference",
                                    "reference_target": _DECLARED},
                      "outputs": ["ev-ghost"], "stop_reason": None}])
        check(v.facts["explicit_reference_audit"]["state"] == "contradictory"
              and GATE in [g for g, _ in v.failed_gates],
              "§三⑤a：目标不是任何已采纳真实材料 → contradictory → 门失败")
        check(v.capability_verdict == CAPABILITY_FAIL,
              "§三⑤a：矛盾证据 → capability FAIL（fail-closed）")
        check(v.facts["explicit_reference_audit"]["unbacked_outputs"] == ["ev-ghost"],
              "§三⑤a：未被真实材料支撑的目标逐条落盘（可复核）")

        # 5b. 尝试无目标 且 正文无标记 → 无从复核。
        v = _run(base, "xr_no_target", materials=("m1", "m2"), trace=[
            {"step_index": 0, "action": "inspect_bounded",
             "arguments": {"mode": "explicit_reference"},
             "outputs": [], "stop_reason": _DANGLING_STOP}])
        check(v.facts["explicit_reference_audit"]["state"] == "contradictory"
              and v.capability_verdict == CAPABILITY_FAIL,
              "§三⑤b：尝试既无真实触发标记又无声明目标 → contradictory + FAIL")

        # 5c. 尝试既无产出、又无停止原因/未读记录 → 结果未记录。
        v = _run(base, "xr_no_result", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "arguments": {"mode": "explicit_reference",
                                    "reference_target": _DECLARED},
                      "outputs": []}])
        check(v.facts["explicit_reference_audit"]["state"] == "contradictory"
              and v.capability_verdict == CAPABILITY_FAIL,
              "§三⑤c：尝试无产出且无停止/未读记录 → contradictory + FAIL")

        # 5d. 有尝试、无产出，但**如实记录停止原因** → 诚实负面（attempted_unresolved）。
        v = _run(base, "xr_attempt_unresolved", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "anchor_evidence_id": _ANCHOR_EID,
                      "arguments": _named_request_args(),
                      "outputs": [], "stop_reason": "hard budget (per_seed_cap)"}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "attempted_unresolved",
              "§三⑤d：尝试 + 无产出 + 有如实停止原因 → attempted_unresolved（不是矛盾）")
        check(v.material_state == MATERIAL_STATE_NOT_OBTAINED
              and v.capability_verdict == CAPABILITY_PASS
              and GATE not in v.passed_gates,
              "§三⑤d：诚实未获得 not_obtained + 能力 PASS，且该门不通过")

        # 5e. §四：目标必须与本 seed 同 document_id/document_version。尝试步骤**真实声明**
        #     了另一文档（或另一 evidence_set）⇒ 该解析无法被认证为同文档显式引用。
        _BASE_DOC_ARGS = _named_request_args(
            company_id="300750", document_id="NDSD_KCZ_2026",
            document_version=DOC_VERSION, evidence_set_version=SET_VERSION)
        v = _run(base, "xr_cross_document", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "anchor_evidence_id": _ANCHOR_EID,
                      "arguments": {**_BASE_DOC_ARGS, "document_id": "OTHER_DOC"},
                      "outputs": ["ev-m1"], "stop_reason": None}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "contradictory" and a["same_document_bound"] is False,
              "§四⑤e：尝试跨 document 读取（目标必须同 document_id）→ contradictory")
        check(a["attempt_document_mismatches"]
              and a["attempt_document_mismatches"][0]["step_index"] == 0,
              "§四⑤e：跨文档步骤逐条落盘（可复核是哪一步越了文档边界）")
        check(GATE in [g for g, _ in v.failed_gates]
              and v.capability_verdict == CAPABILITY_FAIL,
              "§四⑤e：跨文档解析 → 门失败 + capability FAIL（fail-closed）")

        # 5e-2. 同 document_id 但**另一 document_version** ⇒ 同样不可认证。
        v = _run(base, "xr_cross_version", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "anchor_evidence_id": _ANCHOR_EID,
                      "arguments": {**_BASE_DOC_ARGS, "document_version": "sha256-other"},
                      "outputs": ["ev-m1"], "stop_reason": None}])
        check(v.facts["explicit_reference_audit"]["state"] == "contradictory"
              and v.capability_verdict == CAPABILITY_FAIL,
              "§四⑤e：跨 document_version 解析 → contradictory + FAIL（版本也是身份）")

        # 5f. 尝试步骤**未声明**文档身份 ⇒ 同文档性不可复核（None，绝不当作 True）
        #     → target_resolvable 不得标记通过（未测试），绝不冒充 resolved。
        v = _run(base, "xr_doc_unverifiable", seed=_seed_with_marker(),
                 materials=("m1", "m2"), trace=[
                     {"step_index": 0, "action": "inspect_bounded",
                      "anchor_evidence_id": _ANCHOR_EID,
                      "arguments": _named_request_args(),
                      "outputs": ["ev-m1"], "stop_reason": None}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "resolved" and a["same_document_bound"] is None,
              "§四⑤f：尝试步骤未声明文档身份 ⇒ 同文档性不可复核（None，不得当作 True）")
        check(GATE not in v.passed_gates
              and GATE not in [g for g, _ in v.failed_gates],
              "§四⑤f：同文档性不可复核 ⇒ target_resolvable 不标记通过（记为未判定）")
        check(v.capability_verdict == CAPABILITY_NOT_TESTED
              and "same_document_unverifiable" in v.facts["capability_not_tested_reason"],
              "§四⑤f：不可复核 ⇒ NOT_TESTED（未测试 ≠ 通过）")

        # ------------------------------------------------------------------
        # 6. 普通跨页相邻读取不得冒充显式引用能力
        # ------------------------------------------------------------------
        v = _run(base, "xr_adjacent_masquerade", materials=("m1", "m2"), trace=[
            {"step_index": 0, "action": "inspect_bounded",
             "arguments": {"mode": "adjacent_after", "limit": 9},
             "outputs": ["ev-m1", "ev-m2"],
             "stop_reason": "topic section closed (sibling heading)"},
            {"step_index": 1, "action": "inspect_bounded",
             "arguments": {"mode": "table_continuation", "limit": 5},
             "outputs": ["ev-m2"], "stop_reason": None}])
        a = v.facts["explicit_reference_audit"]
        check(a["state"] == "not_exercised" and a["target_resolved"] is False,
              "§三⑥：相邻/续表读取（真的采纳了材料）绝不构成显式引用解析")
        check(a["resolution_attempted"] is False,
              "§三⑥：只有 mode=explicit_reference 的步骤才算尝试")
        check(GATE not in v.passed_gates and v.capability_verdict == CAPABILITY_NOT_TESTED,
              "§三⑥：跨页相邻读取不得冒充显式引用能力（NOT_TESTED）")

        # ------------------------------------------------------------------
        # 7. 审计事实的确定性：同一真实产物重复验收 → 同一审计结论；
        #    且审计事实可确定性序列化落盘（人工/Codex 可独立复核）。
        # ------------------------------------------------------------------
        v1 = verify_category(CAT, base / "xr_trigger_no_attempt")
        v2 = verify_category(CAT, base / "xr_trigger_no_attempt")
        check(v1.facts["explicit_reference_state"] == "not_exercised"
              and v2.facts["explicit_reference_state"] == "not_exercised",
              "§三⑦：同一真实产物可重复复核 → 同一审计状态（not_exercised）")
        check(_canonical_json(v1.facts["explicit_reference_audit"])
              == _canonical_json(v2.facts["explicit_reference_audit"]),
              "§三⑦：审计事实逐字段确定（重跑不复现即视为不稳定）")
        check(json.loads(_canonical_json(v1.facts["explicit_reference_audit"]))
              == v1.facts["explicit_reference_audit"],
              "§三⑦：审计事实可确定性序列化落盘（人工/Codex 可独立复核）")

        # ------------------------------------------------------------------
        # 8. §四：多 seed run 的**逐 seed 文档归属**（真实 v11 形状）。
        #    一个 run 的 trace 会**顺序包含多个 seed** 的步骤（``seed_evidence_id`` 逐步骤
        #    归因）。第二个 seed 在**自己的**文档内解析引用完全合法，绝不因与本 run 第一个
        #    seed 的文档不同而被误判为「跨文档解析」。
        # ------------------------------------------------------------------
        from harness.six_category_acceptance import _explicit_reference_audit

        _s1 = _seed(ASPECT)
        _s2 = {**_seed(ASPECT), "evidence_id": "ev-seed-0002", "text": _MARKER_TEXT,
               "document_id": "NDSD_SECOND_DOC", "document_version": DOC_VERSION_B}
        _step2 = {"step_index": 0, "action": "inspect_bounded",
                  "seed_evidence_id": "ev-seed-0002",
                  "anchor_evidence_id": "ev-seed-0002",
                  "arguments": _named_request_args(
                      company_id="300750", document_id="NDSD_SECOND_DOC",
                      document_version=DOC_VERSION_B,
                      evidence_set_version=SET_VERSION),
                  "outputs": ["ev-m1"], "stop_reason": None}
        a = _explicit_reference_audit(
            trace=[_step2], rolling={}, seed=_s1,
            material_index=[{"component_evidence_id": "ev-m1"}], payload_previews=[],
            seed_entries=[_s1, _s2])
        check(a["same_document_bound"] is True and a["state"] == "resolved",
              "§四⑧：第二 seed 在自己的文档内解析引用 ⇒ 同文档性成立（按步骤自身 seed 归属比较）")
        check(a["attempt_document_mismatches"] == [],
              "§四⑧：绝不与本 run 第一个 seed 的文档比较（否则第二 seed 的合法读取"
              "会被误判为跨文档 → 真实产物能力 FAIL）")
        # 归属 seed 声明了却不在 seed 清单内 ⇒ 应有身份不可知（不猜、不判矛盾）。
        a = _explicit_reference_audit(
            trace=[{**_step2, "seed_evidence_id": "ev-ghost-seed"}], rolling={}, seed=_s1,
            material_index=[{"component_evidence_id": "ev-m1"}], payload_previews=[],
            seed_entries=[_s1, _s2])
        check(a["same_document_bound"] is None and a["state"] == "resolved"
              and a["attempt_document_unknown_steps"] == [0],
              "§四⑧：归属 seed 不在清单内 ⇒ 同文档性不可复核（None），不猜、也不算矛盾")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
