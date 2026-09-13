"""来源政策 v1 的只读 schema / loader / validator。

载体：templates/policies/source_policy_v1.yaml。

职责：把版本化来源政策（A/B/C/D 分级、关键结论支撑、独立性、时效窗口、
来源类、行业风险传导四层）加载为只读对象并做纯声明式校验。

边界（R1-A §二/§九）：本模块不接正式 Router / Harness runtime / Worker / Writer，
不改变正式检索与来源判定行为；source_grades 的域名判定实现仍在
external_v2/schema.py grade_source，本模块只声明政策契约。

CLI：
    python -m contracts.source_policy templates/policies/source_policy_v1.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from contracts import schema_v2 as S

logger = logging.getLogger("contracts.source_policy")

# 唯一政策身份（禁止 source_policy_v1 / source-policy-v1 混用）
POLICY_ID = "source_policy_v1"
POLICY_VERSION = "v1"
SCHEMA_VERSION = "source-policy-v1"

_SOURCE_GRADE_FIELDS = ("label", "ordinary_fact", "key_conclusion")
_KEY_CONCLUSION_RULE_FIELDS = (
    "description", "threshold", "single_c", "d_grade",
    "unknown_date", "insufficient_handling")
# 强时点时效窗口（权威，与 sections/industry_source_policy 一致）
_FRESHNESS_WINDOWS = {
    "near_3m": 90,
    "near_6m": 180,
    "near_1y": 365,
    "near_2y": 730,
    "near_3y": 1095,
}
# 关键行业结论主题（通用契约 topic_id，非公司特定）
_KEY_INDUSTRY_TOPICS = (
    "industry_scale_cycle",
    "industry_position",
    "industry_competition",
    "industry_risk_transmission",
    "industry_supply_demand",
)


@dataclass(frozen=True)
class SourcePolicy:
    policy_id: str
    policy_version: str
    schema_version: str
    status: str
    created_at: str
    approved_at: str | None
    frozen_at: str | None
    applies_to: dict
    source_grades: dict
    key_conclusion_rule: dict
    independence_rule: dict
    key_industry_topics: list
    freshness_windows_days: dict
    source_classes: list
    transmission_layers: list
    transmission_channels: list
    transmission_note: str
    raw: dict = field(repr=False)


@dataclass(frozen=True)
class SourcePolicyValidationResult:
    valid: bool
    errors: list
    warnings: list
    stats: dict


def load_source_policy(path: str) -> SourcePolicy:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"来源政策文件不存在: {path}")
    return parse_source_policy(p.read_text(encoding="utf-8"))


def parse_source_policy(text: str) -> SourcePolicy:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("来源政策 YAML 顶层必须是 dict")
    return SourcePolicy(
        policy_id=doc.get("policy_id", ""),
        policy_version=doc.get("policy_version", ""),
        schema_version=doc.get("schema_version", ""),
        status=doc.get("status", "candidate"),
        created_at=doc.get("created_at", ""),
        approved_at=doc.get("approved_at"),
        frozen_at=doc.get("frozen_at"),
        applies_to=doc.get("applies_to", {}),
        source_grades=doc.get("source_grades", {}),
        key_conclusion_rule=doc.get("key_conclusion_rule", {}),
        independence_rule=doc.get("independence_rule", {}),
        key_industry_topics=list(doc.get("key_industry_topics", [])),
        freshness_windows_days=doc.get("freshness_windows_days", {}),
        source_classes=list(doc.get("source_classes", [])),
        transmission_layers=list(doc.get("transmission_layers", [])),
        transmission_channels=list(doc.get("transmission_channels", [])),
        transmission_note=doc.get("transmission_note", ""),
        raw=dict(doc),
    )


def validate_source_policy(policy: SourcePolicy) -> SourcePolicyValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if policy.policy_id != POLICY_ID:
        errors.append(f"policy_id 应为 {POLICY_ID!r}，实际 {policy.policy_id!r}")
    if policy.policy_version != POLICY_VERSION:
        errors.append(f"policy_version 应为 {POLICY_VERSION!r}，实际 {policy.policy_version!r}")
    if policy.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION!r}，实际 {policy.schema_version!r}")

    # 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二）
    errors.extend(S.frozen_asset_errors(policy.raw))

    # applies_to 与 Contract v2 source_policy_ref 一致
    if policy.applies_to.get("source_policy_ref") != policy.policy_id:
        errors.append("applies_to.source_policy_ref 应与 policy_id 一致")
    if policy.applies_to.get("contract_version") != S.CONTRACT_VERSION_V2:
        errors.append("applies_to.contract_version 应为 v2")

    # A/B/C/D 分级
    if set(policy.source_grades.keys()) != set(S.SOURCE_GRADES):
        errors.append(f"source_grades 应覆盖 {S.SOURCE_GRADES}")
    for g, spec in policy.source_grades.items():
        for f in _SOURCE_GRADE_FIELDS:
            if f not in spec:
                errors.append(f"source_grades.{g} 缺少字段 {f!r}")

    # 关键结论支撑规则
    for f in _KEY_CONCLUSION_RULE_FIELDS:
        if f not in policy.key_conclusion_rule:
            errors.append(f"key_conclusion_rule 缺少字段 {f!r}")

    # 独立性判定
    if policy.independence_rule.get("by") != "canonical_domain":
        errors.append("independence_rule.by 应为 canonical_domain")

    # 关键行业结论主题（通用，非公司特定）
    if set(policy.key_industry_topics) != set(_KEY_INDUSTRY_TOPICS):
        errors.append(f"key_industry_topics 应为 {list(_KEY_INDUSTRY_TOPICS)}")

    # 时效窗口
    if policy.freshness_windows_days != _FRESHNESS_WINDOWS:
        errors.append(f"freshness_windows_days 应为 {_FRESHNESS_WINDOWS}")

    # 来源类
    if not policy.source_classes:
        errors.append("source_classes 不能为空")

    # 行业风险传导四层
    if list(policy.transmission_layers) != list(S.TRANSMISSION_LAYERS):
        errors.append(f"transmission_layers 应为 {list(S.TRANSMISSION_LAYERS)}")

    # 行业风险传导四条独立通道（R1-A §四：需求/收入、原材料/成本、产能/资本开支、现金流/偿债）
    tx_channels = [c.get("channel") for c in policy.transmission_channels if isinstance(c, dict)]
    if tx_channels != list(S.TRANSMISSION_CHANNELS):
        errors.append(f"transmission_channels 应为 {list(S.TRANSMISSION_CHANNELS)}，实际 {tx_channels}")

    if not policy.transmission_note:
        errors.append("transmission_note 不能为空")

    stats = {
        "policy_id": policy.policy_id,
        "source_grades": sorted(policy.source_grades.keys()),
        "key_industry_topics": len(policy.key_industry_topics),
        "transmission_layers": len(policy.transmission_layers),
        "transmission_channels": len(policy.transmission_channels),
    }
    return SourcePolicyValidationResult(
        valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验来源政策 v1")
    parser.add_argument("path", help="source_policy_v1.yaml 路径")
    args = parser.parse_args(argv)

    try:
        p = load_source_policy(args.path)
        r = validate_source_policy(p)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验来源政策失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if not r.valid:
        print(f"校验失败（{len(r.errors)} 处）:")
        for err in r.errors:
            print(f"  - {err}")
        return 1
    print(f"来源政策校验通过: policy_id={r.stats['policy_id']} "
          f"grades={r.stats['source_grades']} 关键主题={r.stats['key_industry_topics']} "
          f"传导层={r.stats['transmission_layers']}")
    for w in r.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
