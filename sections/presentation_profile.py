"""PresentationProfile v1 的只读 schema / loader / validator。

载体：templates/presentation_profiles/interview_demo_v1.yaml。

职责：加载唯一版本化呈现配置（折叠层级、展示规则、截图区域、附录项），
做纯声明式校验，硬约束呈现边界——只允许 display/folding/screenshots/appendix，
禁止 fact_change/coverage_change/citation_change/business_judgment_change。

边界（R1-A §十）：本模块不接正式渲染 runtime，不改变事实、覆盖、引用或业务判断。

CLI：
    python -m sections.presentation_profile templates/presentation_profiles/interview_demo_v1.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from contracts import schema_v2 as S

logger = logging.getLogger("sections.presentation_profile")

PROFILE_ID = "interview_demo_v1"
SCHEMA_VERSION = "presentation-profile-v1"
WRITING_SPEC_REF = "credit_report_v1"

_ALLOWED_SCOPE = ("display", "folding", "screenshots", "appendix")
_FORBIDDEN_SCOPE = ("fact_change", "coverage_change",
                    "citation_change", "business_judgment_change")
_REQUIRED_DISPLAY_RULES = (
    "toc", "tables", "gap_marker", "unresolved_marker", "diagnostic_marker",
    "citation_display", "negative_event_body",
)


@dataclass(frozen=True)
class PresentationProfile:
    presentation_profile_id: str
    schema_version: str
    status: str
    created_at: str
    approved_at: str | None
    frozen_at: str | None
    profile_name: str
    writing_spec_ref: str
    contract_ref: dict
    scope: dict
    fold_levels: dict
    display_rules: dict
    screenshot_regions: list
    appendix_items: list
    raw: dict = field(repr=False)


@dataclass(frozen=True)
class PresentationProfileValidationResult:
    valid: bool
    errors: list
    warnings: list
    stats: dict


def load_presentation_profile(path: str) -> PresentationProfile:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"呈现配置文件不存在: {path}")
    return parse_presentation_profile(p.read_text(encoding="utf-8"))


def parse_presentation_profile(text: str) -> PresentationProfile:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError("PresentationProfile YAML 顶层必须是 dict")
    return PresentationProfile(
        presentation_profile_id=doc.get("presentation_profile_id", ""),
        schema_version=doc.get("schema_version", ""),
        status=doc.get("status", "candidate"),
        created_at=doc.get("created_at", ""),
        approved_at=doc.get("approved_at"),
        frozen_at=doc.get("frozen_at"),
        profile_name=doc.get("profile_name", ""),
        writing_spec_ref=doc.get("writing_spec_ref", ""),
        contract_ref=doc.get("contract_ref", {}),
        scope=doc.get("scope", {}),
        fold_levels=doc.get("fold_levels", {}),
        display_rules=doc.get("display_rules", {}),
        screenshot_regions=list(doc.get("screenshot_regions", [])),
        appendix_items=list(doc.get("appendix_items", [])),
        raw=dict(doc),
    )


def validate_presentation_profile(
        prof: PresentationProfile) -> PresentationProfileValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if prof.presentation_profile_id != PROFILE_ID:
        errors.append(f"presentation_profile_id 应为 {PROFILE_ID!r}，实际 "
                      f"{prof.presentation_profile_id!r}")
    if prof.schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version 应为 {SCHEMA_VERSION!r}")

    # 冻结资产生命周期 + 内容指纹（R1-A §九 / 冻结收口 §二）
    errors.extend(S.frozen_asset_errors(prof.raw))
    if prof.writing_spec_ref != WRITING_SPEC_REF:
        errors.append(f"writing_spec_ref 应为 {WRITING_SPEC_REF!r}，实际 "
                      f"{prof.writing_spec_ref!r}")
    if prof.contract_ref.get("contract_version") != S.CONTRACT_VERSION_V2:
        errors.append("contract_ref.contract_version 应为 v2")

    # 呈现边界（硬约束）：允许集/禁止集必须精确
    if tuple(prof.scope.get("allowed", [])) != _ALLOWED_SCOPE:
        errors.append(f"scope.allowed 应为 {list(_ALLOWED_SCOPE)}")
    if tuple(prof.scope.get("forbidden", [])) != _FORBIDDEN_SCOPE:
        errors.append(f"scope.forbidden 应为 {list(_FORBIDDEN_SCOPE)}")

    if prof.fold_levels.get("default") != "h2":
        errors.append("fold_levels.default 应为 h2")

    for rule in _REQUIRED_DISPLAY_RULES:
        if rule not in prof.display_rules:
            errors.append(f"display_rules 缺少 {rule!r}")

    for i, r in enumerate(prof.screenshot_regions):
        if not isinstance(r, dict) or "id" not in r or "label" not in r:
            errors.append(f"screenshot_regions[{i}] 缺 id/label")

    for i, item in enumerate(prof.appendix_items):
        if not isinstance(item, dict) or "id" not in item or "label" not in item:
            errors.append(f"appendix_items[{i}] 缺 id/label")

    stats = {
        "presentation_profile_id": prof.presentation_profile_id,
        "screenshot_regions": len(prof.screenshot_regions),
        "appendix_items": len(prof.appendix_items),
        "display_rules": len(prof.display_rules),
    }
    return PresentationProfileValidationResult(
        valid=len(errors) == 0, errors=errors, warnings=warnings, stats=stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 PresentationProfile v1")
    parser.add_argument("path", help="interview_demo_v1.yaml 路径")
    args = parser.parse_args(argv)

    try:
        prof = load_presentation_profile(args.path)
        r = validate_presentation_profile(prof)
    except Exception as e:  # noqa: BLE001
        logger.exception("校验 PresentationProfile 失败")
        print(f"校验异常: {type(e).__name__}: {e}")
        return 1

    if not r.valid:
        print(f"校验失败（{len(r.errors)} 处）:")
        for err in r.errors:
            print(f"  - {err}")
        return 1
    print(f"PresentationProfile 校验通过: {r.stats['presentation_profile_id']} "
          f"截图区={r.stats['screenshot_regions']} 附录={r.stats['appendix_items']} "
          f"展示规则={r.stats['display_rules']}")
    for w in r.warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
