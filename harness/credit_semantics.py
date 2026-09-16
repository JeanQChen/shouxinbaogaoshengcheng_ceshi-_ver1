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

CREDIT_SEMANTICS_VERSION = "3"

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

# E.6：used/unused 对账的关键口径字段（任一缺失 → 绝不 reconciled）。
RECONCILE_SCOPE_FIELDS = (
    "entity_scope", "facility_scope", "consolidation", "currency",
    "as_of", "period", "document",
)

# 冻结 Contract 内的 3 个原授信 aspect（其 blocking_policy 必须从冻结 Contract 加载，E.8）。
FROZEN_CREDIT_ASPECT_IDS = (
    FROZEN_TOTAL_CREDIT_LINE, FROZEN_USED_CREDIT, FROZEN_UNUSED_CREDIT)

# 后继新增 aspect 的阻断策略（不在冻结 Contract 内，不覆盖原 REPORT_BLOCKED 语义，E.8）。
SUCCESSOR_BLOCKING_POLICY = "supporting"
SUCCESSOR_MISSING_POLICY = "non_blocking（缺失不阻断，NOT REPORT_BLOCKED）"


def credit_dependency_fingerprint(*, extraction_version: str, semantics_version: str,
                                  authority_version: str,
                                  runner_dependency_fingerprint: str,
                                  material_run_manifest_fingerprint: str) -> str:
    """授信派生的依赖指纹（E.11/E.3）：绑定代码版本 **与** 真实 Contract/SourcePolicy/
    R2 dependency **与** 材料 Pack/run 身份。

    - ``runner_dependency_fingerprint``：``harness.run_manifest.runner_dependency_fingerprint()``
      （冻结 Contract v2 内容指纹 + 冻结 Source Policy v1 内容指纹 + 六个 R2 实现版本）；
    - ``material_run_manifest_fingerprint``：材料 run manifest 自身指纹（Pack/run 身份）。

    任一变化 → 指纹变化，用于区分旧/新代码或旧/新材料派生的授信事实（绝不静默复用）。
    纯函数、确定性（sort_keys / ensure_ascii=False / 无分隔符，与 ``sha256_canonical`` 同构）。
    """
    return hashlib.sha256(json.dumps(
        {
            "credit_fact_extraction_version": extraction_version,
            "credit_semantics_version": semantics_version,
            "credit_authority_version": authority_version,
            "runner_dependency_fingerprint": runner_dependency_fingerprint,
            "material_run_manifest_fingerprint": material_run_manifest_fingerprint,
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def frozen_credit_aspects(contract=None) -> dict[str, dict]:
    """从**冻结 Contract v2** 读取 3 个原授信 aspect 的阻断影响（E.8，绝不硬编码）。

    返回 ``{aspect_id: {in_frozen_contract, blocking_policy, missing_policy,
    missing_policy_text, source}}``；``missing_policy_text`` 由 Contract 的
    ``missing_policies`` 注册表解析（如 ``transfer_human`` → 转人工/阻断）。
    """
    if contract is None:
        from harness import run_manifest as RM
        contract = RM.frozen_contract()
    by_id = {a.aspect_id: a for a in contract.all_aspects()}
    registry = dict(contract.missing_policies or {})
    out: dict[str, dict] = {}
    for aspect_id in FROZEN_CREDIT_ASPECT_IDS:
        aspect = by_id.get(aspect_id)
        if aspect is None:
            out[aspect_id] = {
                "aspect_id": aspect_id, "in_frozen_contract": False,
                "blocking_policy": [], "missing_policy": "", "missing_policy_text": "",
                "source": "frozen_contract_v2",
            }
            continue
        missing_policy = str(getattr(aspect, "missing_policy", "") or "")
        out[aspect_id] = {
            "aspect_id": aspect_id,
            "in_frozen_contract": True,
            "blocking_policy": [str(p) for p in (aspect.blocking_policy or [])],
            "missing_policy": missing_policy,
            "missing_policy_text": str(registry.get(missing_policy, "") or ""),
            "source": "frozen_contract_v2",
        }
    return out


def blocking_impact(aspect_id: str, *, semantic_status: str, authority_status: str,
                    contract=None) -> dict:
    """按冻结 Contract 判定某 aspect 的阻断影响（E.8）。

    - 冻结 Contract 内的 aspect：``blocking_policy``/``missing_policy`` 全部取自 Contract；
      未满足（非 supports 或权威无效）且策略含非 ``NONE`` 项 → ``blocking=True``；
    - 不在冻结 Contract 内（如后继新增 ``authorized_application_ceiling``）：
      ``in_frozen_contract=False``、``blocking=False``（supporting），**不覆盖**原语义。
    """
    frozen = frozen_credit_aspects(contract)
    rec = frozen.get(aspect_id)
    if rec is None or not rec["in_frozen_contract"]:
        return {
            "aspect_id": aspect_id,
            "in_frozen_contract": False,
            "blocking_policy": [SUCCESSOR_BLOCKING_POLICY],
            "blocking": False,
            "missing_policy": "",
            "missing_policy_text": SUCCESSOR_MISSING_POLICY,
            "satisfied": False,
            "reason": "不在冻结 Contract 内（后继新增 aspect，supporting，不阻断、不覆盖原语义）",
        }
    satisfied = (semantic_status == SUPPORT_SUPPORTS and authority_status == AUTHORITY_VALID)
    blocking = (not satisfied) and any(p != "NONE" for p in rec["blocking_policy"])
    return {
        "aspect_id": aspect_id,
        "in_frozen_contract": True,
        "blocking_policy": rec["blocking_policy"],
        "blocking": blocking,
        "missing_policy": rec["missing_policy"],
        "missing_policy_text": rec["missing_policy_text"],
        "satisfied": satisfied,
        "reason": ("冻结 Contract 该 aspect 已满足（supports + 权威有效）" if satisfied
                   else f"冻结 Contract blocking_policy={rec['blocking_policy']}；"
                        f"missing_policy={rec['missing_policy']}"),
    }


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
                       scope_closed: bool = True, not_obtained: bool = False,
                       conflict_status: str = "") -> CreditDualAxis:
    """计算一条授信事实对目标 aspect 的双轴状态（来源权威轴 ⊥ 语义支撑轴）。

    - 权威无效 → authority_status=invalid（这是 authority_failed 的唯一来源）。
    - **E.5**：``not_obtained``（显式缺口 / ``value is None``）或 ``conflict_status`` 非空
      → semantic_status=not_obtained，**绝不**升为 supports/scope_qualified（缺口不是支撑）。
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
    if not_obtained or conflict_status:
        reason = (f"同最新报告期多源值冲突（{conflict_status}），标量不可可靠提取 → 显式缺口"
                  if conflict_status else
                  NOT_OBTAINED_WORDING + "（显式缺口，value=None）")
        return CreditDualAxis(
            aspect_id=aspect_id, authority_status=AUTHORITY_VALID,
            authority_reason=authority_reason,
            semantic_status=SUPPORT_NOT_OBTAINED,
            semantic_reason=reason + "；缺口绝不作为支撑（E.5）")
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
        scope_closed=bool(f.get("scope_closed", True)),
        not_obtained=bool(f.get("not_obtained")) or (
            "value" in f and f.get("value") is None),
        conflict_status=str(f.get("conflict_status", "") or "")) for f in facts]

    authority_status = (
        AUTHORITY_VALID if any(a.authority_status == AUTHORITY_VALID for a in axes)
        else AUTHORITY_INVALID)
    authority_reason = next(
        (a.authority_reason for a in axes if a.authority_status == AUTHORITY_INVALID), "")

    # 语义支撑聚合：只看权威有效的 fact（E.5：缺口/冲突绝不作为支撑）。
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
    elif any(a.semantic_status == SUPPORT_NOT_OBTAINED for a in valid_axes):
        semantic_status = SUPPORT_NOT_OBTAINED
        semantic_reason = next(
            a.semantic_reason for a in valid_axes
            if a.semantic_status == SUPPORT_NOT_OBTAINED)
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

    完整口径（``RECONCILE_SCOPE_FIELDS``：entity_scope / facility_scope / consolidation /
    currency / as_of / period / document）**任一侧任一字段缺失**，或两侧不一致，即
    ``scope_not_reconciled``；本函数从不返回任何算术和/差（调用方若求和/求差即违反口径规则）。

    E.6：缺失字段不再按空串「相等」处理——双方同样缺字段**不是**对账成功（旧实现会因
    ``("", "", ...) == ("", "", ...)`` 误判 reconciled）。
    """
    def _scope_key(f: dict) -> tuple:
        return tuple(str(f.get(k, "") or "") for k in RECONCILE_SCOPE_FIELDS)
    used_key = _scope_key(used)
    unused_key = _scope_key(unused)
    if not all(used_key) or not all(unused_key):
        return "scope_not_reconciled"
    if used_key != unused_key:
        return "scope_not_reconciled"
    return "reconciled"


def successor_changelist() -> dict:
    """授信口径后继 changelist（P1-5）：新增独立申请额度上限 aspect（supporting，非阻断）。

    E.8：**原** ``total_credit_line`` / ``used_credit`` / ``unused_credit`` 的阻断影响**从冻结
    Contract v2 加载**（``frozen_credit_aspects``），不再硬编码；新增 aspect 的 supporting/
    非阻断策略与之分离，绝不覆盖原 ``REPORT_BLOCKED`` 语义。
    """
    return {
        "changelist_version": CREDIT_SEMANTICS_VERSION,
        "text_correction": {
            "wrong": TEXT_CORRECTION_WRONG,
            "correct": TEXT_CORRECTION_CORRECT,
        },
        "frozen_credit_aspects": frozen_credit_aspects(),
        "add_aspect": {
            "aspect_id": SUCCESSOR_AUTHORIZED_APPLICATION_CEILING,
            "label": "拟申请/授权申请额度上限",
            "semantic_type": SEMANTIC_TYPE_AUTHORIZED_APPLICATION_CEILING,
            "blocking_policy": SUCCESSOR_BLOCKING_POLICY,
            "missing_policy": SUCCESSOR_MISSING_POLICY,
            "in_frozen_contract": False,
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
            "原 total_credit_line/used_credit/unused_credit 的阻断影响从冻结 Contract 加载"
            "（REPORT_BLOCKED + missing_policy=transfer_human），新增 authorized_application_ceiling "
            "保持 supporting，绝不覆盖原 REPORT_BLOCKED 语义",
            "conflict/not_obtained/value=None 的事实不得被双轴提升为 supports；"
            "used/unused 任一关键口径字段缺失不得 reconciled；裸「亿」无可靠币种上下文不得默认 CNY",
        ],
    }
