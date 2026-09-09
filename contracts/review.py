"""契约解析（授信类型条件）与 41 问覆盖矩阵。

- resolve_contracts：按授信类型展开 applies_when，标记不适用主题。
- build_review_matrix：读取 41 问基线与显式映射，统计完整/部分/未覆盖/超出范围。
- render_review_markdown：生成非技术复核表（含 SC-01～SC-05 待确认清单）。

不调用 LLM / Embedding / Chroma / 网络。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from contracts import schema as S
from contracts.blocking import blocking_label

# ---------------------------------------------------------------------------
# SC-01～SC-05 业务决策配置（确认状态由此文件驱动，渲染代码不硬编码“已确认”）
# ---------------------------------------------------------------------------

SC_DECISIONS_PATH = Path(__file__).parent / "sc_decisions.yaml"


SC_IDS = ["SC-01", "SC-02", "SC-03", "SC-04", "SC-05"]


def load_sc_decisions(path: str | None = None) -> list[dict[str, str]]:
    """读取并严格校验 SC-01～SC-05 决策配置（失败关闭）。

    返回按 SC_IDS 顺序排列的 [{id, status, question, decision}]。必须恰好包含
    SC-01～SC-05，ID 不得缺失/重复/未知，status 只能是 pending|confirmed，
    question/decision 不得为空；配置为空、缺项、非法或读取异常时一律 raise，
    不通过默认值或容错逻辑把异常配置视为已确认。
    """
    p = Path(path) if path else SC_DECISIONS_PATH
    if not p.exists():
        raise FileNotFoundError(f"SC 决策配置不存在: {p}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    items = (doc or {}).get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("SC 决策配置缺少 items 列表或为空")

    by_id: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"SC 决策项必须是 dict: {item!r}")
        sid = item.get("id")
        if sid not in SC_IDS:
            raise ValueError(f"未知 SC ID: {sid!r}，允许 {SC_IDS}")
        if sid in by_id:
            raise ValueError(f"SC ID 重复: {sid!r}")
        status = item.get("status")
        if status not in ("pending", "confirmed"):
            raise ValueError(f"[{sid}] status 非法: {status!r}，允许 pending/confirmed")
        question = (item.get("question") or "").strip()
        decision = (item.get("decision") or "").strip()
        if not question:
            raise ValueError(f"[{sid}] question 为空")
        if not decision:
            raise ValueError(f"[{sid}] decision 为空")
        by_id[sid] = {"id": sid, "status": status, "question": question,
                      "decision": decision}

    missing = [i for i in SC_IDS if i not in by_id]
    if missing:
        raise ValueError(f"SC 决策缺少以下项: {missing}")
    return [by_id[i] for i in SC_IDS]


def sc_status_label(status: str) -> str:
    """确认状态 → 人类可读标签（未确认显示“待业务确认”）。"""
    return "已确认" if status == "confirmed" else "待业务确认"


# ---------------------------------------------------------------------------
# 条件 → 人类可读
# ---------------------------------------------------------------------------

def _describe_condition(cond: S.Condition | None) -> str:
    if cond is None:
        return "全部"
    if cond.kind == "always":
        return "全部"
    if cond.kind == "credit_type_in":
        return " / ".join(cond.value or [])
    if cond.kind == "credit_type_not_in":
        return "非 " + " / ".join(cond.value or [])
    if cond.kind == "all_of":
        return " 且 ".join(_describe_condition(c) for c in cond.children)
    if cond.kind == "any_of":
        return " 或 ".join(_describe_condition(c) for c in cond.children)
    if cond.kind == "none_of":
        return "排除 " + "、".join(_describe_condition(c) for c in cond.children)
    return cond.kind


def _credit_type_label(ct: str) -> str:
    labels = {
        "working_capital": "流动资金贷款",
        "trade_finance": "贸易融资",
        "fixed_asset": "固定资产贷款",
        "project_loan": "项目贷款",
        "other": "其他",
    }
    return labels.get(ct, ct)


# ---------------------------------------------------------------------------
# 授信类型解析
# ---------------------------------------------------------------------------

def resolve_contracts(
    contracts: list[S.SectionContract],
    credit_type: str,
) -> list[S.ResolvedSectionContract]:
    if credit_type not in S.CREDIT_TYPES:
        raise ValueError(f"未知授信类型: {credit_type!r}，允许 {S.CREDIT_TYPES}")

    resolved: list[S.ResolvedSectionContract] = []
    for sec in contracts:
        topics: list[S.ResolvedTopic] = []
        for t in sec.required_topics:
            applies = t.applies_when is None or t.applies_when.matches(credit_type)
            topics.append(
                S.ResolvedTopic(
                    topic_id=t.topic_id,
                    applies=applies,
                    questions=t.key_questions if applies else [],
                )
            )
        resolved.append(
            S.ResolvedSectionContract(
                section_id=sec.section_id,
                credit_type=credit_type,
                enabled=True,
                topics=topics,
            )
        )
    return resolved


# ---------------------------------------------------------------------------
# 41 问映射
# ---------------------------------------------------------------------------

def load_mapping(path: str) -> list[S.BaselineContractMapping]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"映射文件不存在: {path}")
    mappings: list[S.BaselineContractMapping] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        mappings.append(
            S.BaselineContractMapping(
                case_id=obj["case_id"],
                question_ids=list(obj.get("question_ids", [])),
                coverage_role=obj.get("coverage_role", "partial"),
                note=obj.get("note", ""),
                covered_aspects=list(obj.get("covered_aspects", [])),
            )
        )
    return mappings


def validate_mapping(
    contracts: list[S.SectionContract],
    case_ids: set[str],
    mappings: list[S.BaselineContractMapping],
) -> list[str]:
    """校验映射：case_id 均存在、question_id 有效、角色合法。返回错误列表。"""
    errors: list[str] = []
    valid_qids = {q.question_id for sec in contracts for q in sec.all_questions()}
    for m in mappings:
        if m.case_id not in case_ids:
            errors.append(f"映射引用不存在的 case_id: {m.case_id}")
        if m.coverage_role not in S.COVERAGE_ROLES:
            errors.append(f"[{m.case_id}] 非法 coverage_role: {m.coverage_role!r}")
        for qid in m.question_ids:
            if qid not in valid_qids:
                errors.append(f"[{m.case_id}] 映射引用不存在的 question_id: {qid!r}")
    return errors


def build_review_matrix(
    contracts: list[S.SectionContract],
    baseline_cases_path: str,
    mapping_path: str | None = None,
) -> S.ContractReviewMatrix:
    """统计 41 问对契约问题的完整/部分/未覆盖与超出范围情况。"""
    baseline_path = Path(baseline_cases_path)
    cases = [
        json.loads(line)
        for line in baseline_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = {c["case_id"] for c in cases}

    if mapping_path is None:
        mapping_path = str(baseline_path.parent / "baseline_contract_mapping.jsonl")
    mappings = load_mapping(mapping_path)

    errors = validate_mapping(contracts, case_ids, mappings)
    if errors:
        raise ValueError("41 问映射校验失败:\n" + "\n".join(f"  - {e}" for e in errors))

    # question_id → 覆盖角色集合
    coverage: dict[str, set[str]] = {}
    out_of_scope: list[str] = []
    for m in mappings:
        if m.coverage_role == "out_of_scope":
            out_of_scope.append(m.case_id)
            continue
        for qid in m.question_ids:
            coverage.setdefault(qid, set()).add(m.coverage_role)

    full: list[str] = []
    partial: list[str] = []
    uncovered: list[str] = []
    for sec in contracts:
        for qid in sec.question_ids():
            roles = coverage.get(qid, set())
            if "full" in roles:
                full.append(qid)
            elif roles:  # partial 或 supporting
                partial.append(qid)
            else:
                uncovered.append(qid)

    # 复核表行（每题一行）
    rows: list[dict[str, Any]] = []
    for sec in contracts:
        for t in sec.required_topics:
            for q in t.key_questions:
                mp_desc = next(
                    (mp.description for mp in sec.missing_policies
                     if mp.policy_id == q.missing_policy),
                    "",
                )
                ev = "; ".join(
                    f"{er.evidence_kind}:{','.join(er.source_classes)}"
                    for er in q.evidence_requirements
                )
                calc = "; ".join(q.calculation_requirements)
                roles = coverage.get(q.question_id, set())
                role_label = (
                    "full" if "full" in roles
                    else "partial" if roles
                    else "未覆盖"
                )
                rows.append({
                    "section": sec.title,
                    "topic": t.title,
                    "question": q.question,
                    "evidence": ev or calc or "—",
                    "missing": mp_desc,
                    "blocking": blocking_label(q.blocking_policy),
                    "credit_types": _describe_condition(t.applies_when),
                    "coverage": role_label,
                })

    return S.ContractReviewMatrix(
        full=full,
        partial=partial,
        uncovered=uncovered,
        out_of_scope=out_of_scope,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Markdown 复核表渲染
# ---------------------------------------------------------------------------

def render_review_markdown(
    contracts: list[S.SectionContract],
    matrix: S.ContractReviewMatrix,
    sc_decisions: list[dict[str, str]] | None = None,
) -> str:
    if sc_decisions is None:
        sc_decisions = load_sc_decisions()
    # 失败关闭：只有恰好 SC-01～SC-05 齐全且全部 confirmed 才视为关闭。
    all_confirmed = (
        [d["id"] for d in sc_decisions] == SC_IDS
        and all(d["status"] == "confirmed" for d in sc_decisions)
    )
    lines: list[str] = []
    lines.append("# Section Contract 业务复核表（Phase 0B）")
    lines.append("")
    lines.append(
        "> 状态：SC-01～SC-05 全部已确认，Phase 0B 关闭。"
        if all_confirmed
        else "> 状态：SC-01～SC-05 存在待业务确认项（待业务确认）。"
    )
    lines.append("> 说明：本表面向业务复核，不要求审查 Python 字段或 YAML 语法。")
    lines.append("")

    lines.append("## 覆盖概览")
    lines.append("")
    total = len(matrix.full) + len(matrix.partial) + len(matrix.uncovered)
    lines.append(f"- 契约问题总数：{total}")
    lines.append(f"- 完整覆盖：{len(matrix.full)}")
    lines.append(f"- 部分覆盖：{len(matrix.partial)}")
    lines.append(f"- 未覆盖：{len(matrix.uncovered)}")
    lines.append(f"- 超出第一阶段范围（out_of_scope）case：{len(matrix.out_of_scope)}")
    lines.append("")

    lines.append("## 逐题复核表")
    lines.append("")
    lines.append("| 章节 | 主题 | 必答问题 | 所需证据/计算 | 缺失时怎么写 | 是否阻断 | 适用授信类型 | 41问覆盖 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in matrix.rows:
        lines.append(
            f"| {r['section']} | {r['topic']} | {r['question']} | {r['evidence']} "
            f"| {r['missing']} | {r['blocking']} | {r['credit_types']} | {r['coverage']} |"
        )
    lines.append("")

    lines.append("## SC-01～SC-05 确认清单")
    lines.append("")
    lines.append("以下决策状态由 `contracts/sc_decisions.yaml` 驱动。")
    lines.append("")
    for sc in sc_decisions:
        label = sc_status_label(sc["status"])
        lines.append(f"### {sc['id']}（{label}）")
        lines.append("")
        lines.append(f"**问题**：{sc['question']}")
        lines.append("")
        lines.append(f"**{label}的规则**：{sc['decision']}")
        lines.append("")

    return "\n".join(lines)
