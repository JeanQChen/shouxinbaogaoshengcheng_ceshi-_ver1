"""R2 依赖束：``build_r2_material_dependencies``（§10，非完整正式 runtime）。

R2 只构建：
- R2 Evidence payload resolver（``TopicMaterialPayloadResolver``，仅 evidence_span/table_context）；
- 正式、版本化、确定性的 ``SetEnumerationVerifier``。

``source_policy_resolver`` / ``set_completeness_verifier`` 是 R1-B 冻结边界与 R3 调度职责，
R2 **不构建**（返回 ``None`` 占位）——因此 R2 依赖束**不称完整正式 runtime、不提前接入 R3 调度**；
注入该束提交 ``set_complete`` Pack 仍会因缺 SourcePolicyResolver / SetCompletenessVerifier 而
fail-closed（复验 R1-B 硬门）。真正 ``harness.topic_runtime`` 组合入口由 R3 接入；R4 外部 resolver
经 typed resolver registry/multiplexer 扩展。
"""

from __future__ import annotations

from pathlib import Path

from harness import topic_store as Store
from harness.set_enumeration import build_formal_set_enumeration_verifier


def build_r2_material_dependencies(
        db_path: str | Path) -> tuple[Store.TopicMaterialPayloadResolver,
                                       object | None,
                                       object | None,
                                       object]:
    """构造 R2 依赖束：``(resolver, source_policy_resolver, set_completeness_verifier,
    set_enumeration_verifier)``。

    - ``resolver``：``TopicMaterialPayloadResolver(db_path)``（R2 Evidence payload resolver）。
    - ``source_policy_resolver``：``None``（R1-B/R3 职责，R2 不构建）。
    - ``set_completeness_verifier``：``None``（R1-B/R3 职责，R2 不构建）。
    - ``set_enumeration_verifier``：正式 ``FormalSetEnumerationVerifier``。
    """
    resolver = Store.TopicMaterialPayloadResolver(db_path)
    set_enumeration_verifier = build_formal_set_enumeration_verifier()
    return (resolver, None, None, set_enumeration_verifier)
