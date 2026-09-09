"""Phase 3 Batch B 修订①：required-aspect 契约优先派生（非字符串切分）。

三级优先级（只取命中到的第一级，不逐级拼接）：
1. SECTION_CONTRACT  —— `question_id` 直接命中契约 KeyQuestion（或其 `section_id` +
   问题原文精确匹配）→ 用契约 `required_aspects`；缺省回退 `evidence_requirements`
   的 `required_fields`；再缺省回退问题原文。
2. DATASET_MAPPING   —— `case_id` 命中 `baseline_contract_mapping.jsonl` → 其
   question_ids 对应的契约 KeyQuestion 的 `required_aspects`（拼接去重）。
3. TEXT_FALLBACK     —— 仅当前两级都缺时，按句末标点（？。！；）保守切句；无法可靠
   切分时**只保留 1 个 aspect = 整句**，不猜。

明确**不做**：不按 `和/及/以及/、` 切名词短语（这会把「授信与对外担保」这类不可分
语义错误拆开，也无法区分「合计 vs 明细」等口径）。

每个 aspect 记录来源（source），运行时落盘 `state.aspect_source` 供审计。

CLI:
  python -m harness.aspects --self-check
  python -m harness.aspects --case-id COMP-R1
  python -m harness.aspects --question "主营业务有哪些？各业务收入占比如何？"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

from contracts import schema as S
from contracts.loader import load_contracts
from contracts.review import load_mapping

DEFAULT_CONTRACT_PATH = "templates/contracts/standard_v2.yaml"
DEFAULT_MAPPING_PATH = "evaluation/datasets/baseline_contract_mapping.jsonl"

# aspect 来源枚举
SOURCE_CONTRACT = "SECTION_CONTRACT"
SOURCE_MAPPING = "DATASET_MAPPING"
SOURCE_FALLBACK = "TEXT_FALLBACK"

# 保守切句：仅句末标点（含中英文），不含顿号/逗号（防止把「授信与担保」误拆）。
_SENT_SPLIT = re.compile(r"[？?。！!；;]")


@dataclass(frozen=True)
class Aspect:
    """一个问题必须覆盖的单个可校验方面。"""

    aspect_id: str   # a1, a2, ...
    text: str
    source: str      # SOURCE_CONTRACT | SOURCE_MAPPING | SOURCE_FALLBACK


def _question_index(contracts: list[S.SectionContract]) -> dict[str, S.KeyQuestion]:
    idx: dict[str, S.KeyQuestion] = {}
    for sec in contracts:
        for q in sec.all_questions():
            idx[q.question_id] = q
    return idx


def _aspects_from_question(q: S.KeyQuestion) -> list[str]:
    """契约内单问题的方面：required_aspects → required_fields → 问题原文。"""
    if q.required_aspects:
        return list(q.required_aspects)
    fields: list[str] = []
    for er in q.evidence_requirements:
        fields.extend(er.required_fields)
    if fields:
        return fields
    return [q.question] if (q.question or "").strip() else []


def _make_aspects(texts: list[str], source: str) -> list[Aspect]:
    out: list[Aspect] = []
    seen: set[str] = set()
    for t in texts:
        t = (t or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(Aspect(aspect_id=f"a{len(out) + 1}", text=t, source=source))
    return out


def _fallback_aspects(question: str) -> list[Aspect]:
    q = (question or "").strip()
    if not q:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(q) if p.strip()]
    if len(parts) >= 2:
        return _make_aspects(parts, SOURCE_FALLBACK)
    # 无法可靠切分 → 只保留 1 个 aspect = 整句，不猜。
    return _make_aspects([q], SOURCE_FALLBACK)


def derive_required_aspects(
    question: str = "",
    *,
    section_id: str | None = None,
    case_id: str | None = None,
    question_id: str | None = None,
    contracts: list[S.SectionContract] | None = None,
    mappings: list[S.BaselineContractMapping] | None = None,
) -> list[Aspect]:
    """派生必须覆盖的方面列表（契约优先，非字符串切分）。返回空列表表示无法派生。"""
    if contracts is None:
        contracts = load_contracts(DEFAULT_CONTRACT_PATH)
    idx = _question_index(contracts)

    # 1. SECTION_CONTRACT：question_id 命中契约 KeyQuestion。
    if question_id:
        q = idx.get(question_id)
        if q is not None:
            return _make_aspects(_aspects_from_question(q), SOURCE_CONTRACT)

    # 1b. SECTION_CONTRACT：section_id + 问题原文精确匹配（question_id 缺失时）。
    if section_id:
        sec = next((s for s in contracts if s.section_id == section_id), None)
        if sec is not None:
            target = (question or "").strip()
            for q in sec.all_questions():
                if q.question.strip() == target:
                    return _make_aspects(_aspects_from_question(q), SOURCE_CONTRACT)

    # 2. DATASET_MAPPING：case_id 命中映射 → 契约 question_ids 的 required_aspects。
    if case_id:
        if mappings is None:
            mappings = load_mapping(DEFAULT_MAPPING_PATH)
        for m in mappings:
            if m.case_id == case_id:
                texts: list[str] = []
                for qid in m.question_ids:
                    q = idx.get(qid)
                    if q is not None:
                        texts.extend(_aspects_from_question(q))
                if texts:
                    return _make_aspects(texts, SOURCE_MAPPING)
                break

    # 3. TEXT_FALLBACK。
    return _fallback_aspects(question)


def aspect_source(aspects: list[Aspect]) -> str:
    """单次派生的统一来源（三级派生每次只命中一级，故所有 aspect 同源）。"""
    return aspects[0].source if aspects else ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m harness.aspects", description="required-aspect 契约优先派生")
    parser.add_argument("--self-check", action="store_true",
                        help="打印契约/mapping/回退 锚点样例")
    parser.add_argument("--question", default=None, help="问题原文（回退切句用）")
    parser.add_argument("--section", default=None, help="section_id")
    parser.add_argument("--case-id", default=None, help="41 问 case_id")
    parser.add_argument("--question-id", default=None, help="契约 question_id")
    args = parser.parse_args(argv)

    def _dump(aspects: list[Aspect]) -> list[dict]:
        return [{"aspect_id": a.aspect_id, "text": a.text, "source": a.source}
                for a in aspects]

    if args.self_check:
        contracts = load_contracts(DEFAULT_CONTRACT_PATH)
        result = {
            "COMP-R1_DATASET_MAPPING": _dump(derive_required_aspects(
                "placeholder", case_id="COMP-R1", contracts=contracts)),
            "COMP-CR1_DATASET_MAPPING": _dump(derive_required_aspects(
                "placeholder", case_id="COMP-CR1", contracts=contracts)),
            "SECTION_CONTRACT_by_question_id": _dump(derive_required_aspects(
                "placeholder", question_id="company_business_main",
                contracts=contracts)),
            "TEXT_FALLBACK_multi_sentence": _dump(derive_required_aspects(
                "主营业务有哪些？各业务收入占比如何？")),
            "TEXT_FALLBACK_single_sentence": _dump(derive_required_aspects(
                "核心竞争力是什么")),
            # 未命中契约/mapping 的 case 走 TEXT_FALLBACK（保 1 不猜），不是「空」；
            # 真正为空仅当 question 本身为空。
            "UNMAPPED_CASE_text_fallback": _dump(derive_required_aspects(
                "实际控制人是谁", case_id="COMP-MV1", contracts=contracts)),
            "EMPTY_question_empty": _dump(derive_required_aspects("")),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    aspects = derive_required_aspects(
        args.question or "", section_id=args.section, case_id=args.case_id,
        question_id=args.question_id)
    print(json.dumps(_dump(aspects), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
