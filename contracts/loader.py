"""Section Contract 加载器：YAML 配置 → list[SectionContract]。

只做结构化解析（含命名条件内联、缺失策略目录解析），不承担业务校验；
业务校验由 contracts.validator.validate_contracts 负责。

CLI：
    python -m contracts.loader templates/contracts/standard_v2.yaml
    python -m contracts.loader templates/contracts/standard_v2.yaml \
        --credit-type working_capital \
        --review-matrix evaluation/datasets/v1_baseline.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from contracts import schema as S

logger = logging.getLogger("contracts.loader")


def _parse_condition(raw, named: dict[str, dict]) -> S.Condition:
    """把 YAML 条件节点解析为受限 Condition。

    支持三种写法：
      1. 命名引用： {ref: condition_id}
      2. 叶子：     {kind: always | credit_type_in | credit_type_not_in, field, op, value}
      3. 组合：     {kind: all_of | any_of | none_of, children: [...]}
    """
    if not isinstance(raw, dict):
        raise ValueError(f"条件必须是 dict，实际为 {type(raw).__name__}: {raw!r}")

    if "ref" in raw:
        ref_id = raw["ref"]
        if ref_id not in named:
            raise ValueError(f"引用了未定义的条件: {ref_id}")
        return _parse_condition(named[ref_id], named)

    kind = raw.get("kind")
    if kind not in S.CONDITION_KINDS:
        raise ValueError(f"未知条件 kind: {kind!r}，允许 {S.CONDITION_KINDS}")

    if kind == "always":
        return S.Condition(kind="always")

    if kind in ("all_of", "any_of", "none_of"):
        children_raw = raw.get("children")
        if not isinstance(children_raw, list) or not children_raw:
            raise ValueError(f"{kind} 条件必须提供非空 children 列表")
        return S.Condition(
            kind=kind,
            children=[_parse_condition(c, named) for c in children_raw],
        )

    field = raw.get("field")
    if field not in S.CONDITION_FIELDS:
        raise ValueError(f"未知条件字段: {field!r}，允许 {S.CONDITION_FIELDS}")
    op = raw.get("op", "in")
    if op not in S.CONDITION_OPS:
        raise ValueError(f"未知条件操作符: {op!r}，允许 {S.CONDITION_OPS}")
    value = raw.get("value")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"条件 value 必须是字符串列表: {value!r}")
    return S.Condition(kind=kind, field=field, op=op, value=value)


def _parse_evidence_req(raw: dict) -> S.EvidenceRequirement:
    return S.EvidenceRequirement(
        requirement_id=raw["requirement_id"],
        evidence_kind=raw["evidence_kind"],
        source_classes=list(raw.get("source_classes", [])),
        minimum_sources=int(raw.get("minimum_sources", 1)),
        freshness_policy=raw.get("freshness_policy"),
        required_fields=list(raw.get("required_fields", [])),
    )


def _parse_blocking_policy(raw) -> list[str]:
    """把 YAML 阻断声明规范化为后果集合（可复合，空列表 = NONE）。

    兼容三种写法：省略 → []；字符串 "NONE"/"JOB_BLOCKED" → []/[单值]；
    列表 → 去掉 "NONE" 后的元素（SECTION_BLOCKED + REPORT_BLOCKED 复合保留）。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        if raw in ("NONE", "none", ""):
            return []
        return [raw]
    if isinstance(raw, list):
        out = []
        for x in raw:
            if not isinstance(x, str):
                raise ValueError(f"blocking_policy 列表元素必须是字符串: {x!r}")
            if x in ("NONE", "none", ""):
                continue
            out.append(x)
        return out
    raise ValueError(f"blocking_policy 必须是字符串或列表: {raw!r}")


def _parse_question(raw: dict) -> S.KeyQuestion:
    return S.KeyQuestion(
        question_id=raw["question_id"],
        question=raw["question"],
        priority=raw.get("priority", "P1"),
        evidence_requirements=[
            _parse_evidence_req(er) for er in raw.get("evidence_requirements", [])
        ],
        calculation_requirements=list(raw.get("calculation_requirements", [])),
        analysis_requirements=list(raw.get("analysis_requirements", [])),
        missing_policy=raw.get("missing_policy", "write_not_found"),
        blocking_policy=_parse_blocking_policy(raw.get("blocking_policy")),
        impact_scope=list(raw.get("impact_scope", [])),
    )


def _parse_topic(raw: dict, named: dict[str, dict]) -> S.TopicContract:
    applies_when = None
    if "applies_when" in raw and raw["applies_when"] is not None:
        applies_when = _parse_condition(raw["applies_when"], named)
    return S.TopicContract(
        topic_id=raw["topic_id"],
        title=raw["title"],
        required=bool(raw.get("required", True)),
        key_questions=[_parse_question(q) for q in raw.get("key_questions", [])],
        applies_when=applies_when,
    )


def _parse_section(raw: dict, named: dict[str, dict],
                   missing_policies: list[S.MissingPolicy]) -> S.SectionContract:
    completion_rules = []
    for cr in raw.get("completion_rules", []):
        completion_rules.append(
            S.CompletionRule(
                rule_id=cr["rule_id"],
                scope_id=cr.get("scope_id", raw.get("section_id", "")),
                condition=_parse_condition(cr.get("condition", {"kind": "always"}), named),
                outcome=cr["outcome"],
            )
        )
    evaluation_rules = [
        S.EvaluationRule(rule_id=er["rule_id"], description=er["description"])
        for er in raw.get("evaluation_rules", [])
    ]
    output_requirements = [
        S.OutputRequirement(
            requirement_id=o["requirement_id"],
            kind=o.get("kind", "text"),
            description=o["description"],
        )
        for o in raw.get("output_requirements", [])
    ]
    return S.SectionContract(
        contract_version=raw.get("contract_version", S.CONTRACT_VERSION),
        section_id=raw["section_id"],
        title=raw["title"],
        purpose=raw.get("purpose", ""),
        required_topics=[
            _parse_topic(t, named) for t in raw.get("required_topics", [])
        ],
        output_requirements=output_requirements,
        completion_rules=completion_rules,
        evaluation_rules=evaluation_rules,
        allowed_capabilities=list(raw.get("allowed_capabilities", [])),
        research_policy=raw.get("research_policy", "harness"),
        missing_policies=missing_policies,
    )


def parse_contracts(text: str) -> list[S.SectionContract]:
    """从 YAML 文本解析契约（供 load_contracts 与测试复用）。"""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("契约 YAML 顶层必须是 dict")

    contract_version = doc.get("contract_version", S.CONTRACT_VERSION)

    # 缺失策略目录
    missing_policies: list[S.MissingPolicy] = []
    for mp in (doc.get("policies") or {}).get("missing", []):
        missing_policies.append(
            S.MissingPolicy(policy_id=mp["policy_id"], description=mp["description"])
        )

    # 命名条件（供 applies_when / condition 用 {ref: ...} 引用）
    named: dict[str, dict] = {}
    for c in doc.get("conditions", []):
        named[c["condition_id"]] = {k: v for k, v in c.items() if k != "condition_id"}

    sections: list[S.SectionContract] = []
    for sec in doc.get("sections", []):
        sec = dict(sec)
        sec["contract_version"] = contract_version
        sections.append(_parse_section(sec, named, missing_policies))
    return sections


def load_contracts(path: str) -> list[S.SectionContract]:
    """从文件加载并解析契约。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"契约文件不存在: {path}")
    return parse_contracts(p.read_text(encoding="utf-8"))


def _summarize(contracts: list[S.SectionContract]) -> str:
    lines = [f"契约版本: {S.CONTRACT_VERSION}"]
    total_questions = 0
    for sec in contracts:
        nq = len(sec.question_ids())
        total_questions += nq
        lines.append(f"  [{sec.section_id}] {sec.title} "
                     f"({len(sec.required_topics)} 主题 / {nq} 问题 / "
                     f"policy={sec.research_policy})")
    lines.append(f"合计: {len(contracts)} 章节 / {total_questions} 问题")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="加载并校验 Section Contract")
    parser.add_argument("path", help="契约 YAML 路径")
    parser.add_argument("--credit-type", default=None,
                        choices=S.CREDIT_TYPES, help="按授信类型解析条件")
    parser.add_argument("--review-matrix", default=None,
                        help="41 问基线 jsonl 路径（生成覆盖矩阵）")
    parser.add_argument("--out", default=None,
                        help="覆盖矩阵 markdown 输出路径")
    args = parser.parse_args(argv)

    try:
        contracts = load_contracts(args.path)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("加载契约失败")
        print(f"加载失败: {type(e).__name__}: {e}")
        return 1

    from contracts.validator import validate_contracts
    result = validate_contracts(contracts)
    print(_summarize(contracts))
    print()
    if not result.valid:
        print(f"校验失败（{len(result.errors)} 处）:")
        for err in result.errors:
            print(f"  - {err}")
        return 1
    print("校验通过: 契约结构、跨引用、条件与规则均合法。")

    if args.credit_type:
        from contracts.review import resolve_contracts
        resolved = resolve_contracts(contracts, args.credit_type)
        print(f"\n授信类型 {args.credit_type} 解析结果:")
        for rs in resolved:
            enabled_q = sum(len(t.questions) for t in rs.topics if t.applies)
            skipped_q = sum(len(t.questions) for t in rs.topics if not t.applies)
            print(f"  [{rs.section_id}] enabled={rs.enabled} "
                  f"适用问题={enabled_q} 不适用问题={skipped_q}")

    if args.review_matrix:
        from contracts.review import build_review_matrix, render_review_markdown
        matrix = build_review_matrix(contracts, args.review_matrix)
        md = render_review_markdown(contracts, matrix)
        print("\n" + md)
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
            print(f"\n复核表已写入: {args.out}")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
