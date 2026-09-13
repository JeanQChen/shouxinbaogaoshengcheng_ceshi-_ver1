"""Contract v2 加载器：standard_v3.yaml → ContractV2 对象。

只做结构化解析（YAML 锚点由 yaml.safe_load 展开，逐 aspect 补齐 22 字段），
不承担业务校验；业务校验由 contracts.validator_v2.validate_contract_v2 负责。

本模块不 import 任何正式 service / runtime / Worker / Writer，不执行 I/O 写库，
不调用 LLM。仅被 validator_v2 / review 导出器 / 离线测试引用。

CLI：
    python -m contracts.loader_v2 templates/contracts/standard_v3.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from contracts import schema_v2 as S

logger = logging.getLogger("contracts.loader_v2")


def _parse_aspect(raw: dict) -> S.AspectV2:
    return S.AspectV2(
        aspect_id=raw["aspect_id"],
        question_id=raw["question_id"],
        topic_id=raw["topic_id"],
        requirement_text=raw.get("requirement_text", ""),
        kind=raw["kind"],
        producer_kind=raw["producer_kind"],
        execution_path=raw["execution_path"],
        required_fields=list(raw.get("required_fields", [])),
        coverage_rules=list(raw.get("coverage_rules", [])),
        complete_set_rule=raw.get("complete_set_rule", ""),
        evidence_requirement_ids=list(raw.get("evidence_requirement_ids", [])),
        source_policy_ref=raw.get("source_policy_ref", ""),
        time_scope=raw.get("time_scope", ""),
        display_tier=raw.get("display_tier", ""),
        content_role=raw.get("content_role", ""),
        missing_policy=raw.get("missing_policy", ""),
        blocking_policy=list(raw.get("blocking_policy", [])),
        applicability_policy=raw.get("applicability_policy"),
        impact_scope=list(raw.get("impact_scope", [])),
        output_destination=raw.get("output_destination", ""),
        derived_from=list(raw.get("derived_from", [])),
        business_review_status=raw.get("business_review_status", "CONFIRMED"),
        business_review_reason=raw.get("business_review_reason", ""),
        derived_from_scope=raw.get("derived_from_scope"),
        transmission_layers=list(raw.get("transmission_layers", [])),
        transmission_channel=raw.get("transmission_channel", ""),
        raw=dict(raw),
    )


def _parse_question(raw: dict, topic_id: str) -> S.QuestionV2:
    return S.QuestionV2(
        question_id=raw["question_id"],
        topic_id=topic_id,
        question=raw.get("question", ""),
        priority=raw.get("priority", "P1"),
        producer_kind=raw.get("producer_kind", ""),
        execution_path=raw.get("execution_path", ""),
        blocking_policy=list(raw.get("blocking_policy", [])),
        missing_policy=raw.get("missing_policy", ""),
        aspects=[_parse_aspect(a) for a in raw.get("aspects", [])],
    )


def _parse_topic(raw: dict) -> S.TopicV2:
    return S.TopicV2(
        topic_id=raw["topic_id"],
        title=raw.get("title", ""),
        producer_kind=raw.get("producer_kind", ""),
        questions=[_parse_question(q, raw["topic_id"]) for q in raw.get("questions", [])],
    )


def _parse_section(raw: dict) -> S.SectionV2:
    return S.SectionV2(
        section_id=raw["section_id"],
        title=raw.get("title", ""),
        purpose=raw.get("purpose", ""),
        research_policy=raw.get("research_policy", ""),
        allowed_capabilities=list(raw.get("allowed_capabilities", [])),
        topics=[_parse_topic(t) for t in raw.get("topics", [])],
    )


def parse_contract_v2(text: str) -> S.ContractV2:
    """从 YAML 文本解析 Contract v2（供 load_contract_v2 与测试复用）。"""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("Contract v2 YAML 顶层必须是 dict")
    if doc.get("contract_version") != S.CONTRACT_VERSION_V2:
        raise ValueError(
            f"contract_version 必须为 {S.CONTRACT_VERSION_V2!r}，"
            f"实际 {doc.get('contract_version')!r}")
    sections = [_parse_section(s) for s in doc.get("sections", [])]
    return S.ContractV2(
        contract_version=doc["contract_version"],
        sections=sections,
        time_policies=doc.get("time_policies", {}),
        missing_policies=doc.get("missing_policies", {}),
        coverage_rules_registry=doc.get("coverage_rules_registry", {}),
        allowed_capabilities=list(doc.get("allowed_capabilities", [])),
        source_policy_ref=doc.get("source_policy_ref", ""),
        status=doc.get("status", "candidate"),
        created_at=doc.get("created_at", ""),
        approved_at=doc.get("approved_at"),
        frozen_at=doc.get("frozen_at"),
        raw=dict(doc),
    )


def load_contract_v2(path: str) -> S.ContractV2:
    """从文件加载并解析 Contract v2。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Contract v2 文件不存在: {path}")
    return parse_contract_v2(p.read_text(encoding="utf-8"))


def _summarize(c: S.ContractV2) -> str:
    lines = [f"Contract 版本: {c.contract_version} | source_policy_ref={c.source_policy_ref}"]
    for sec in c.sections:
        nq = len(sec.all_questions())
        na = len(sec.all_aspects())
        lines.append(f"  [{sec.section_id}] {sec.title} "
                     f"({len(sec.topics)} 主题 / {nq} 问题 / {na} aspects)")
    lines.append(f"合计: {len(c.all_questions())} 问题 / {len(c.all_aspects())} aspects")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="加载 Contract v2")
    parser.add_argument("path", help="standard_v3.yaml 路径")
    parser.add_argument("--validate", action="store_true", help="加载后校验")
    args = parser.parse_args(argv)

    try:
        c = load_contract_v2(args.path)
    except Exception as e:  # noqa: BLE001 - CLI 顶层统一报错
        logger.exception("加载 Contract v2 失败")
        print(f"加载失败: {type(e).__name__}: {e}")
        return 1

    print(_summarize(c))
    if args.validate:
        from contracts.validator_v2 import validate_contract_v2
        r = validate_contract_v2(c)
        print()
        if not r.valid:
            print(f"校验失败（{len(r.errors)} 处）:")
            for err in r.errors:
                print(f"  - {err}")
            return 1
        print(f"校验通过（{len(r.warnings)} 处 warning）:")
        for w in r.warnings:
            print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
