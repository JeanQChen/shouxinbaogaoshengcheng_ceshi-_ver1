"""Phase 3 Batch B 研究状态机 + 成功判定 + 答案引用校验（纯函数，无 I/O）。

成功/缺口口径（编码前计划 §13 修订版）：
- 运行时充分性只依据：required topics/子问题覆盖（最小化为「有 claim 且全部可回查」）、
  每个关键 claim 可回查引用、无影响核心答案的 unresolved；
- Gold document/page 绝不进入本模块；只用于 Runner 完成后的离线诊断；
- COMPLETED_WITH_GAPS 不计严格成功，单独统计；
- 引用必须可回查（evidence_id / snapshot+formula/item+period / source_snapshot_id）。

CLI: python -m harness.state --self-check
"""

from __future__ import annotations

import json

from harness import schema as H


# ---------------------------------------------------------------------------
# 状态设置
# ---------------------------------------------------------------------------

def set_status(state: H.ResearchState, status: str,
               stop_reason: str | None = None) -> H.ResearchState:
    """设置问题级状态（校验在 QUESTION_STATUSES 内，fail-closed）。"""
    if status not in H.QUESTION_STATUSES:
        raise H.HarnessValidationError(f"非法问题状态: {status!r}")
    if stop_reason is not None and stop_reason not in H.STOP_REASONS:
        raise H.HarnessValidationError(f"非法 stop_reason: {stop_reason!r}")
    state.status = status
    if stop_reason is not None:
        state.stop_reason = stop_reason
    return state


# ---------------------------------------------------------------------------
# 引用解析
# ---------------------------------------------------------------------------

def _structured_matches(cit: H.CitationRef, refs) -> bool:
    for r in refs:
        if r.snapshot_id != cit.snapshot_id:
            continue
        if cit.formula_id is not None:
            if r.formula_id == cit.formula_id and r.period == cit.period:
                if cit.formula_version is None or r.formula_version == cit.formula_version:
                    return True
        elif cit.item_code is not None:
            if r.item_code == cit.item_code and r.period == cit.period:
                return True
    return False


def _citation_error(cit: H.CitationRef, state: H.ResearchState) -> str | None:
    """校验单条引用能否回查到已取得材料；返回错误描述或 None。"""
    if cit.ref_type == "evidence":
        if not cit.evidence_id:
            return "evidence 引用缺 evidence_id"
        if cit.evidence_id not in state.evidence_ids:
            return f"evidence_id 不在已取得材料中: {cit.evidence_id}"
    elif cit.ref_type == "structured":
        if not cit.snapshot_id:
            return "structured 引用缺 snapshot_id"
        if cit.formula_id is None and cit.item_code is None:
            return "structured 引用缺 formula_id/item_code"
        if not cit.period:
            return "structured 引用缺 period"
        if not _structured_matches(cit, state.structured_refs):
            return "structured 引用无法匹配已取得的 StructuredResultRef"
    elif cit.ref_type == "external":
        if not cit.source_snapshot_id:
            return "external 引用缺 source_snapshot_id"
        if cit.source_snapshot_id not in state.external_snapshot_ids:
            return f"source_snapshot_id 不在已取得快照中: {cit.source_snapshot_id}"
    else:
        return f"ref_type 非法: {cit.ref_type}"
    return None


def validate_answer(answer: H.ResearchAnswer | None,
                    state: H.ResearchState) -> list[str]:
    """校验答案结构 + 每个 claim 的引用可回查性；返回错误列表（空=通过）。

    引用不可回查 / 虚构证据 / 无来源数字 → 返回错误（不计成功）；不含 unresolved。
    """
    if answer is None:
        return ["answer_missing"]
    errors: list[str] = []
    if not (answer.answer_text or "").strip():
        errors.append("empty_answer_text")
    if not answer.claims:
        errors.append("no_claims")
    for i, claim in enumerate(answer.claims):
        if claim.kind not in H.CLAIM_KINDS:
            errors.append(f"claim[{i}] kind 非法: {claim.kind}")
        if not claim.citation_refs:
            errors.append(f"claim[{i}] 无引用")
        for idx in claim.citation_refs:
            if not (0 <= idx < len(answer.citations)):
                errors.append(f"claim[{i}] 引用下标越界: {idx}")
    for j, cit in enumerate(answer.citations):
        err = _citation_error(cit, state)
        if err:
            errors.append(f"citation[{j}] {err}")
    return errors


# ---------------------------------------------------------------------------
# 成功判定
# ---------------------------------------------------------------------------

def _result(success: bool, completion_status: str, reasons: list[str]) -> dict:
    return {"success": success, "completion_status": completion_status,
            "reasons": reasons}


def evaluate_success(state: H.ResearchState,
                     answer: H.ResearchAnswer | None) -> dict:
    """确定性成功判定（主判据；LLM evaluator 仅作诊断，不改变此结果）。

    返回 {success, completion_status, reasons}，其中 completion_status 属于
    COMPLETION_STATUSES：COMPLETED / COMPLETED_WITH_GAPS / UNRESOLVED /
    NOT_IMPLEMENTED / FAILED。
    """
    if state.route_result is None or state.route_result.status != "DECIDED":
        return _result(False, "NOT_IMPLEMENTED", ["path_not_implemented"])
    if answer is None:
        return _result(False, "FAILED", ["answer_missing"])
    errors = validate_answer(answer, state)
    if errors:
        return _result(False, "FAILED", errors)
    if answer.unresolved_items:
        return _result(False, "COMPLETED_WITH_GAPS", ["unresolved_items"])
    return _result(True, "COMPLETED", [])


def is_sufficient(state: H.ResearchState,
                  answer: H.ResearchAnswer | None) -> bool:
    """严格成功判定：路径已实现 + 有效答案 + 全引用可回查 + 无 unresolved。"""
    return evaluate_success(state, answer)["success"]


def sufficiency_reasons(state: H.ResearchState,
                        answer: H.ResearchAnswer | None) -> list[str]:
    return evaluate_success(state, answer)["reasons"]


def has_citable_material(state: H.ResearchState) -> bool:
    """是否已取得任何可引用材料（本地证据 / 结构化结果 / 外部快照）。"""
    return bool(state.evidence_ids or state.structured_refs or state.external_snapshot_ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.state", description="状态机/成功判定自检")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    if args.self_check:
        print(json.dumps({
            "question_statuses": list(H.QUESTION_STATUSES),
            "completion_statuses": list(H.COMPLETION_STATUSES),
            "stop_reasons": list(H.STOP_REASONS),
        }, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
