"""R2→R3 前授信口径 P0 语义解耦（P1-5）。

双轴状态（来源权威轴 ⊥ 语义支撑/口径可比轴）：
- **来源权威轴**（source authority）：该事实来源是否权威有效（``valid``/``invalid``），与
  「该事实能否支撑某 aspect 的语义口径」无关。来源有效但语义口径不符 ≠ authority_failed。
- **语义支撑轴**（semantic support/scope）：该事实的语义类型是否支撑目标 aspect
  （``supports`` / ``semantic_mismatch`` / ``not_obtained`` / ``scope_qualified``）。

核心纠正（Codex 定点返修 P1-5）：
- ``authorized_application_ceiling``（拟申请综合授信额度上限）NOT
  ``actual_granted_total_credit_line``（实际获批授信总额）、NOT ``total_credit_line``（总授信）。
- 实际获批总额（actual_granted_total_credit_line）在本轮已纳入材料及检索范围内未取得 →
  ``not_obtained``（显式缺口），绝不回填申请上限、绝不标 authority_failed。
- used_credit（综合授信口径）与 unused_credit（银行借款口径）的
  entity_scope/facility_scope/document 均不一致 → 绝不求和/求差。
- 文本纠正：**当前 Contract 缺少独立的申请额度上限 aspect，旧 evaluation 绑定发生语义误配**
  （不是「Contract 把申请额度上限定义成总授信」）。

本模块只做确定性语义解耦（零 LLM / 零网络 / 零公司硬编码），不重写 R2 架构、不进入 R3。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CREDIT_SEMANTICS_VERSION = "2"

# 4 个语义类型。
SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING = "authorized_application_ceiling"
SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL = "actual_granted_total_credit_line"
SEMANTIC_TYPE_USED_CREDIT = "used_credit"
SEMANTIC_TYPE_UNUSED_CREDIT = "unused_credit"

# 双轴状态取值。
AUTHORITY_VALID = "valid"
AUTHORITY_INVALID = "invalid"
SUPPORT_SUPPORTS = "supports"
SUPPORT_MISMATCH = "semantic_mismatch"
SUPPORT_NOT_OBTAINED = "not_obtained"
SUPPORT_SCOPE_QUALIFIED = "scope_qualified"

# 冻结的 company_debt_credit.* 三 aspect（总授信/已用/未用）+ 后继新增 aspect。
FROZEN_TOTAL_CREDIT_LINE = "company_debt_credit.total_credit_line"
FROZEN_USED_CREDIT = "company_debt_credit.used_credit"
FROZEN_UNUSED_CREDIT = "company_debt_credit.unused_credit"
SUCCESSOR_AUTHORIZED_APPLICATION_CEILING = "company_debt_credit.authorized_application_ceiling"

# 文本纠正（P1-5 明确要求，测试逐字断言）。
TEXT_CORRECTION_WRONG = "Contract 把申请额度上限定义成总授信（total_credit_line）"
TEXT_CORRECTION_CORRECT = (
    "当前 Contract 缺少独立的申请额度上限 aspect，旧 evaluation 绑定发生语义误配")

# E.10：未取得的诚实措辞——只允许「本轮已纳入材料及检索范围内未取得」，绝不写「无披露」。
NOT_OBTAINED_WORDING = "在本轮已纳入材料及检索范围内未取得"


def credit_dependency_fingerprint(*, extraction_version: str, semantics_version: str,
                                  authority_version: str) -> str:
    """授信派生的依赖指纹（E.11）：绑定 extractor/semantics/authority 三版本。

    任一版本变化 → 指纹变化，用于区分旧/新代码派生的授信事实（绝不静默复用旧事实）。
    纯函数、确定性（sort_keys / ensure_ascii=False / 无分隔符，与 ``sha256_canonical`` 同构）。
    """
    return hashlib.sha256(json.dumps(
        {
            "credit_fact_extraction_version": extraction_version,
            "credit_semantics_version": semantics_version,
            "credit_authority_version": authority_version,
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CreditDualAxis:
    """一条授信事实对目标 aspect 的双轴状态。"""

    aspect_id: str
    authority_status: str      # valid | invalid
    authority_reason: str
    semantic_status: str       # supports | semantic_mismatch | not_obtained | scope_qualified
    semantic_reason: str

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id,
            "authority_status": self.authority_status,
            "authority_reason": self.authority_reason,
            "semantic_status": self.semantic_status,
            "semantic_reason": self.semantic_reason,
        }


def classify_semantic_type(text: str) -> str | None:
    """确定性归类授信事实语义类型（结构信号，无 LLM/公司/页码专用规则）。

    - 「实际获批/实际授予/实际获得/实际授信/获批授信」→ actual_granted_total_credit_line；
    - 「不超过 +（股东会/董事会/批准/拟）+ 授信额度」→ authorized_application_ceiling；
    - 「已使用/已用」→ used_credit；
    - 「尚未使用/未使用」→ unused_credit。
    """
    t = text or ""
    if any(k in t for k in ("实际获批", "实际授予", "实际获得", "实际授信", "获批授信", "已获批")):
        return SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL
    has_ceiling = ("不超过" in t or "拟" in t or "股东会" in t
                   or "董事会" in t or "批准" in t)
    if has_ceiling and ("授信额度" in t or "综合授信" in t):
        return SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING
    if "已使用" in t or "已用" in t:
        return SEMANTIC_TYPE_USED_CREDIT
    if "尚未使用" in t or "未使用" in t:
        return SEMANTIC_TYPE_UNUSED_CREDIT
    return None


def _target_semantic_type(aspect_id: str) -> str:
    """冻结/后继 aspect → 其语义上需要的目标语义类型。

    ``total_credit_line``（总授信）语义上需要「实际获批授信总额」，不是「拟申请上限」；
    这正是「拟申请上限」与「实际获批总额」语义误配的根因。
    """
    if aspect_id.endswith("authorized_application_ceiling"):
        return SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING
    if aspect_id.endswith("unused_credit"):
        # 必须先判 unused_credit：'unused_credit' 也以 'used_credit' 结尾。
        return SEMANTIC_TYPE_UNUSED_CREDIT
    if aspect_id.endswith("used_credit"):
        return SEMANTIC_TYPE_USED_CREDIT
    if aspect_id.endswith("total_credit_line"):
        return SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL
    return ""


def dual_axis_for_fact(aspect_id: str, *, semantic_type: str,
                       authority_valid: bool, authority_reason: str = "",
                       scope_closed: bool = True) -> CreditDualAxis:
    """计算一条授信事实对目标 aspect 的双轴状态（来源权威轴 ⊥ 语义支撑轴）。

    - 权威无效 → authority_status=invalid（这是 authority_failed 的唯一来源）。
    - 权威有效但语义类型 ≠ 目标语义类型 → semantic_mismatch（绝不 authority_failed）。
    - 权威有效、语义类型相符但口径未闭合 → scope_qualified（partial）。
    - 权威有效、语义类型相符、口径闭合 → supports。
    """
    if not authority_valid:
        return CreditDualAxis(
            aspect_id=aspect_id, authority_status=AUTHORITY_INVALID,
            authority_reason=authority_reason or "来源权威不通过",
            semantic_status=SUPPORT_NOT_OBTAINED,
            semantic_reason="权威无效，语义支撑不可判定")
    target = _target_semantic_type(aspect_id)
    if semantic_type != target:
        if (semantic_type == SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING
                and target == SEMANTIC_TYPE_ACTUAL_GRANTED_TOTAL):
            return CreditDualAxis(
                aspect_id=aspect_id, authority_status=AUTHORITY_VALID,
                authority_reason=authority_reason,
                semantic_status=SUPPORT_MISMATCH,
                semantic_reason="拟申请上限（authorized_application_ceiling）不是"
                                "实际获批授信总额（actual_granted_total_credit_line）")
        return CreditDualAxis(
            aspect_id=aspect_id, authority_status=AUTHORITY_VALID,
            authority_reason=authority_reason,
            semantic_status=SUPPORT_MISMATCH,
            semantic_reason=f"事实语义类型 {semantic_type!r} 与目标 aspect 需要的 "
                            f"{target!r} 不符（语义误配，非权威失败）")
    if not scope_closed:
        return CreditDualAxis(
            aspect_id=aspect_id, authority_status=AUTHORITY_VALID,
            authority_reason=authority_reason,
            semantic_status=SUPPORT_SCOPE_QUALIFIED,
            semantic_reason="已取得但口径未闭合（entity_scope/facility_scope/document 不一致）")
    return CreditDualAxis(
        aspect_id=aspect_id, authority_status=AUTHORITY_VALID,
        authority_reason=authority_reason,
        semantic_status=SUPPORT_SUPPORTS,
        semantic_reason="语义类型相符、口径闭合")


def credit_aspect_dual_axis(aspect_id: str, facts: tuple[dict, ...]) -> dict:
    """aspect 级双轴状态：区分「来源权威」与「语义支撑/口径可比」。

    ``facts`` 每项形如 ``{"semantic_type", "authority_valid", "authority_reason",
    "scope_closed"}``。返回值：
    - ``authority_status``：来源权威轴（任一 fact 权威有效即 valid，否则 invalid）。
    - ``semantic_status``：语义支撑轴（supports / semantic_mismatch / not_obtained /
      scope_qualified，聚合规则见下）。
    - 绝不把「语义口径不符」判成 authority_failed。

    聚合：目标语义类型有 fact 支撑（scope_closed=True）→ supports；有支撑但 scope 未闭合 →
    scope_qualified；目标语义类型完全无 fact 但存在申请上限 fact → not_obtained（显式缺口，非
    authority_failed）；有 fact 但语义类型 ≠ 目标 → semantic_mismatch。
    """
    target = _target_semantic_type(aspect_id)
    axes = [dual_axis_for_fact(
        aspect_id, semantic_type=f.get("semantic_type", ""),
        authority_valid=bool(f.get("authority_valid", False)),
        authority_reason=f.get("authority_reason", ""),
        scope_closed=bool(f.get("scope_closed", True))) for f in facts]

    authority_status = (
        AUTHORITY_VALID if any(a.authority_status == AUTHORITY_VALID for a in axes)
        else AUTHORITY_INVALID)
    authority_reason = next(
        (a.authority_reason for a in axes if a.authority_status == AUTHORITY_INVALID), "")

    # 语义支撑聚合：只看权威有效的 fact。
    valid_axes = [a for a in axes if a.authority_status == AUTHORITY_VALID]
    target_matches = [a for a in valid_axes if a.semantic_status in
                      (SUPPORT_SUPPORTS, SUPPORT_SCOPE_QUALIFIED)]
    if any(a.semantic_status == SUPPORT_SUPPORTS for a in target_matches):
        semantic_status = SUPPORT_SUPPORTS
        semantic_reason = "目标语义类型已取得且口径闭合"
    elif any(a.semantic_status == SUPPORT_SCOPE_QUALIFIED for a in target_matches):
        semantic_status = SUPPORT_SCOPE_QUALIFIED
        semantic_reason = "目标语义类型已取得但口径未闭合（partial/scope_qualified）"
    elif target_matches:
        semantic_status = SUPPORT_SUPPORTS
        semantic_reason = "目标语义类型有支撑"
    elif any(a.semantic_status == SUPPORT_MISMATCH for a in valid_axes):
        semantic_status = SUPPORT_NOT_OBTAINED
        semantic_reason = (NOT_OBTAINED_WORDING + "实际获批授信总额"
                           "（actual_granted_total_credit_line，not_obtained，非 authority_failed）；"
                           "已有事实为申请上限（authorized_application_ceiling），"
                           "对实际获批总额属语义误配")
    elif valid_axes:
        semantic_status = SUPPORT_MISMATCH
        semantic_reason = "权威有效但语义类型与目标 aspect 不符"
    else:
        semantic_status = SUPPORT_NOT_OBTAINED
        semantic_reason = NOT_OBTAINED_WORDING + "（无权威有效事实）"

    return {
        "aspect_id": aspect_id,
        "authority_status": authority_status,
        "authority_reason": authority_reason,
        "semantic_status": semantic_status,
        "semantic_reason": semantic_reason,
        "facts": [a.to_dict() for a in axes],
    }


def reconcile_used_unused(used: dict, unused: dict) -> str:
    """used_credit 与 unused_credit 的对账结论（绝不求和/求差）。

    两者完整口径（entity_scope / facility_scope / consolidation / currency / as_of /
    period / document）任一不一致即 ``scope_not_reconciled``；本函数从不返回任何算术
    和/差（调用方若求和/求差即违反口径规则）。缺失的字段按空串处理（旧合成事实兼容）。
    """
    def _scope_key(f: dict) -> tuple:
        return (f.get("entity_scope", ""), f.get("facility_scope", ""),
                f.get("consolidation", ""), f.get("currency", ""),
                f.get("as_of", ""), f.get("period", ""), f.get("document", ""))
    used_key = _scope_key(used)
    unused_key = _scope_key(unused)
    # E.7：双方皆空（空==空）不算对账，绝不据此判 reconciled。
    if not any(used_key) and not any(unused_key):
        return "scope_not_reconciled"
    if used_key != unused_key:
        return "scope_not_reconciled"
    return "reconciled"


def successor_changelist() -> dict:
    """授信口径后继 changelist（P1-5）：新增独立申请额度上限 aspect（supporting，非阻断）。"""
    return {
        "changelist_version": CREDIT_SEMANTICS_VERSION,
        "text_correction": {
            "wrong": TEXT_CORRECTION_WRONG,
            "correct": TEXT_CORRECTION_CORRECT,
        },
        "add_aspect": {
            "aspect_id": SUCCESSOR_AUTHORIZED_APPLICATION_CEILING,
            "label": "拟申请/授权申请额度上限",
            "semantic_type": SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
            "blocking_policy": "supporting",
            "missing_policy": "non_blocking（缺失不阻断，NOT REPORT_BLOCKED）",
            "relation": ("与 actual_granted_total_credit_line（实际获批授信总额，独立显式缺口）"
                         "分离，二者不可合并"),
        },
        "hard_rules": [
            "authorized_application_ceiling 不是 actual_granted_total_credit_line、"
            "也不是 total_credit_line",
            "actual_granted_total_credit_line 在本轮已纳入材料及检索范围内未取得 → not_obtained"
            "（显式缺口），绝不回填申请上限、绝不标 authority_failed",
            "used_credit 与 unused_credit 绝不求和/求差（entity_scope/facility_scope/consolidation/"
            "currency/as_of/period/document 不一致）",
            "unused_credit 口径未闭合 → scope_qualified（partial），非 obtained 的完整口径",
        ],
    }
