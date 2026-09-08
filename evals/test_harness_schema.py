"""Eval: harness 公共契约 + 动作协议 —— Phase 3 Batch B commit 2。

用法: python -m evals.test_harness_schema

断言（纯逻辑，无 I/O / LLM / 工具）：
- schema 枚举白名单（9 态 / 5 完成态 / 11 动作 / 10 LLM 可见）；
- 答案/引用/账本/研究状态 dataclass 默认值；
- 动作→工具映射（含终态 None、SNAPSHOT_EXTERNAL 为 Rules 内部）；
- parse_action 成功/失败、validate_action 边界（Rules 内部动作拒绝、INSPECT_EVIDENCE 二选一、
  未知参数、缺参）；
- repair_once 仅修格式（围栏/包裹文字），语义非法仍返回 None。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import actions as A
from harness import schema as H
from routing import schema as RS


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

    def raises(exc_type, fn):
        try:
            fn()
            return False
        except exc_type:
            return True

    # ---- 枚举白名单 ----
    check(len(H.QUESTION_STATUSES) == 9, "QUESTION_STATUSES 9 态")
    check(len(H.COMPLETION_STATUSES) == 5, "COMPLETION_STATUSES 5 态")
    check("COMPLETED" in H.QUESTION_STATUSES and "COMPLETED_WITH_GAPS" in H.QUESTION_STATUSES,
          "问题态含 COMPLETED / COMPLETED_WITH_GAPS")
    check(len(A.ACTIONS) == 11 and len(A.LLM_SELECTABLE_ACTIONS) == 10
          and A.RULES_INTERNAL_ACTIONS == ("SNAPSHOT_EXTERNAL",),
          "11 动作 / 10 LLM 可见 / SNAPSHOT_EXTERNAL 为 Rules 内部")

    # ---- 动作→工具映射 ----
    check(A.ACTION_TOOL["SEARCH_LOCAL"] == "search_evidence", "SEARCH_LOCAL → search_evidence")
    check(A.ACTION_TOOL["LOOKUP_COMPANY_FIELD"] == "lookup_company_field",
          "LOOKUP_COMPANY_FIELD → lookup_company_field")
    check(A.ACTION_TOOL["SNAPSHOT_EXTERNAL"] == "snapshot_external_source",
          "SNAPSHOT_EXTERNAL → snapshot_external_source")
    check(A.ACTION_TOOL["ANSWER"] is None and A.ACTION_TOOL["STOP_WITH_GAP"] is None
          and A.ACTION_TOOL["REQUEST_HUMAN"] is None, "终态动作不映射工具")

    # ---- 答案/引用/账本 dataclass ----
    cit = H.CitationRef(ref_type="external", source_snapshot_id="ext-1")
    claim = H.Claim(claim_id="c1", text="营收为 X", kind="fact", citation_refs=[0])
    ans = H.ResearchAnswer(question_id="q1", answer_text="a", claims=[claim],
                           citations=[cit], completion_status="COMPLETED")
    check(ans.claims[0].citation_refs == [0] and ans.citations[0].source_snapshot_id == "ext-1",
          "ResearchAnswer/CitationRef/Claim 构造正确")
    ledger = H.UsageLedger()
    check(ledger.rounds == 0 and ledger.usage_unknown_calls == 0, "UsageLedger 默认值")

    # ---- ResearchState 默认 ----
    need = RS.InformationNeed(
        need_id="n1", section_id="company", question="q",
        required_evidence_types=[], required_source_types=[], time_scope=None,
        priority="P0", depends_on=[])
    st = H.ResearchState(run_id="r", case_id="c", question_id="q1", company_id="300750",
                         section_id="company", original_question="q", need=need)
    check(st.status == "PENDING" and st.stop_reason is None, "ResearchState 默认 PENDING")

    # ---- parse_action 成功 ----
    a1 = A.parse_action('{"action": "SEARCH_EXTERNAL", "arguments": {"query": "宁德时代 市场份额"}}')
    check(a1.action == "SEARCH_EXTERNAL" and a1.tool_name == "search_external_sources",
          "parse_action：SEARCH_EXTERNAL 映射工具")
    a2 = A.parse_action('{"action": "ANSWER", "arguments": {}}')
    check(a2.action == "ANSWER" and a2.tool_name is None, "parse_action：终态 ANSWER")

    # ---- parse_action 非法 ----
    check(raises(A.ActionParseError, lambda: A.parse_action('not json')),
          "parse_action：非 JSON → ActionParseError")
    check(raises(A.ActionParseError, lambda: A.parse_action('{"action": "HACK", "arguments": {}}')),
          "parse_action：未知动作 → ActionParseError")

    # ---- validate_action 边界 ----
    check(len(A.validate_action("SNAPSHOT_EXTERNAL", {})) > 0,
          "validate_action：SNAPSHOT_EXTERNAL（Rules 内部）拒绝 LLM 发起")
    check(len(A.validate_action("INSPECT_EVIDENCE", {"evidence_id": "e1", "evidence_ids": ["e1", "e2"]})) > 0,
          "validate_action：INSPECT_EVIDENCE 二选一冲突报错")
    check(len(A.validate_action("INSPECT_EVIDENCE", {})) > 0,
          "validate_action：INSPECT_EVIDENCE 缺参报错")
    check(len(A.validate_action("SEARCH_EXTERNAL", {"query": "x", "evil": 1})) > 0,
          "validate_action：未知参数拒绝")
    check(len(A.validate_action("COMPARE_FINANCIAL_PERIODS", {"period_a": "2024-12-31"})) > 0,
          "validate_action：缺 period_b 报错")
    check(A.validate_action("SEARCH_EXTERNAL", {"query": "x"}) == [],
          "validate_action：合法 SEARCH_EXTERNAL 无错误")

    # ---- repair_once 仅修格式 ----
    r1 = A.repair_once('```json\n{"action": "ANSWER", "arguments": {}}\n```', "")
    check(r1 is not None and r1.action == "ANSWER", "repair_once：剥离围栏后解析成功")
    r2 = A.repair_once('好的，下一步是 {"action": "STOP_WITH_GAP", "arguments": {"reason": "缺材料"}}', "")
    check(r2 is not None and r2.action == "STOP_WITH_GAP", "repair_once：提取包裹 JSON 成功")
    check(A.repair_once('{"action": "HACK", "arguments": {}}', "") is None,
          "repair_once：语义非法（未知动作）仍 None")
    check(A.repair_once('完全没有 JSON', "") is None, "repair_once：无 JSON → None")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
