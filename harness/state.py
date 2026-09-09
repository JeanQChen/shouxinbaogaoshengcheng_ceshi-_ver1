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
import re

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
# required-aspect 覆盖（G2 门，契约优先派生）
# ---------------------------------------------------------------------------

# 数值型方面标记：这些方面要求覆盖 claim 文本出现具体数字（阿拉伯数字或金额/比例单位）。
_NUMERIC_ASPECT_TOKENS = (
    "占比", "比例", "份额", "集中度", "率", "金额", "余额", "额度", "规模",
    "增速", "增长", "多少", "几家", "几个", "持股", "注册资本", "实缴资本",
)

# 数字 token 判定：阿拉伯数字，或 %/元/万/亿/千 等金额·比例单位（覆盖“单位：万元”这类无阿拉伯数字的值）。
_NUMBER_HINTS = ("%", "％", "元", "万", "亿", "千")


def _is_numeric_aspect(text: str) -> bool:
    return any(tok in text for tok in _NUMERIC_ASPECT_TOKENS)


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", text)) or any(t in text for t in _NUMBER_HINTS)


def aspect_answers(state: H.ResearchState,
                   answer: H.ResearchAnswer | None) -> list[dict]:
    """逐 required-aspect 的覆盖情况（含 claim 归属与是否有数字）。

    返回 [{aspect_id, text, source, answered, claim_ids, claim_texts, has_number}]。
    required_aspects 为空时返回 []（旧测试/非契约题不受影响）。
    """
    required = state.required_aspects or []
    claim_by_id = {c.claim_id: c for c in (answer.claims if answer else [])}
    aspect_by_id = {a.aspect_id: a for a in (answer.aspects if answer else [])}
    rows: list[dict] = []
    for asp in required:
        aspect_id = asp.get("aspect_id", "")
        aa = aspect_by_id.get(aspect_id)
        claim_ids = list(aa.claim_ids) if aa else []
        claim_texts: list[str] = []
        valid_claim_ids: list[str] = []
        for cid in claim_ids:
            c = claim_by_id.get(cid)
            if c is not None:
                valid_claim_ids.append(cid)
                claim_texts.append(c.text)
        rows.append({
            "aspect_id": aspect_id,
            "text": asp.get("text", ""),
            "source": asp.get("source", ""),
            "answered": bool(aa is not None and valid_claim_ids),
            "claim_ids": valid_claim_ids,
            "claim_texts": claim_texts,
            "has_number": any(_has_number(t) for t in claim_texts),
        })
    return rows


def uncovered_aspects(state: H.ResearchState,
                      answer: H.ResearchAnswer | None) -> list[str]:
    """返回未覆盖的必要方面（文本列表）。

    - 无 AspectAnswer 或支撑 claim 无效 → 该方面文本；
    - 数值型方面覆盖了但没有数字 → 该方面文本 + "（缺具体数字）"。
    """
    missing: list[str] = []
    for row in aspect_answers(state, answer):
        if not row["answered"]:
            missing.append(row["text"])
        elif _is_numeric_aspect(row["text"]) and not row["has_number"]:
            missing.append(f"{row['text']}（缺具体数字）")
    return missing


# ---------------------------------------------------------------------------
# 成功判定
# ---------------------------------------------------------------------------

def _result(success: bool, completion_status: str, reasons: list[str],
            uncovered_aspects: list[str] | None = None,
            unsupported_claims: list[str] | None = None,
            aspect_answers: list[dict] | None = None) -> dict:
    return {
        "success": success,
        "completion_status": completion_status,
        "reasons": reasons,
        "uncovered_aspects": uncovered_aspects or [],
        "unsupported_claims": unsupported_claims or [],
        "aspect_answers": aspect_answers or [],
    }


def evaluate_success(state: H.ResearchState,
                     answer: H.ResearchAnswer | None) -> dict:
    """确定性成功判定（主判据；LLM evaluator 仅作诊断，不改变此结果）。

    四层门：
      G0 路由 DECIDED（已有）；G1 结构合法 + 引用可回查（已有）；
      G2 required-aspect 覆盖（契约优先派生，数值方面须有数字）；
      G3/G4 数字/口径预检 + 批量 entailment（见 harness.entailment，经
      state.unsupported_claims 汇总进本判定）。

    返回 {success, completion_status, reasons, uncovered_aspects,
    unsupported_claims, aspect_answers}；completion_status 属于
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

    # G2 required-aspect 覆盖。
    aa = aspect_answers(state, answer)
    uncovered = uncovered_aspects(state, answer)

    reasons: list[str] = list(answer.unresolved_items or [])
    if uncovered:
        reasons.extend(f"未覆盖方面: {u}" for u in uncovered)

    # G3/G4 汇总（entailment 层把不支持 claim 写入 state；此处由调用方在
    # ANSWER 分支填充 state.unsupported_claims 后再调 evaluate_success）。
    unsupported = list(getattr(state, "unsupported_claims", []) or [])

    if reasons or unsupported:
        return _result(False, "COMPLETED_WITH_GAPS", reasons,
                       uncovered_aspects=uncovered,
                       unsupported_claims=unsupported, aspect_answers=aa)
    return _result(True, "COMPLETED", [],
                   uncovered_aspects=uncovered, unsupported_claims=unsupported,
                   aspect_answers=aa)


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
