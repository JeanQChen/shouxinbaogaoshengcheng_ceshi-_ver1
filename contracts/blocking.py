"""阻断等级判定（纯函数，无 I/O）。

把“问题证据状态”（为什么没完成）映射到“阻断等级”（后果多大）。二者正交：
- QUESTION_STATES 描述缺口性质（缺材料 / 已检索未找到 / 冲突 / 待人工 / 不适用）。
- BLOCKING_LEVELS 描述该缺口对章节 / 正式导出 / 整个任务的影响。

核心语义（与 DESIGN_V2.md 及业务确认对齐）：
- SATISFIED / NOT_APPLICABLE 不阻断。合法的“无实际控制人”是 SATISFIED 结论，
  “无股权激励计划”是 NOT_APPLICABLE，均不构成缺失或阻断。
- NOT_FOUND_AFTER_SEARCH / NOT_PROVIDED：本身不等于“事实不存在”，是否阻断由
  该问题声明的 blocking_policy 决定（非核心信息通常 NONE，允许带缺口预览）。
- CONFLICT：后果完全由该问题声明的 blocking_policy 决定（主体一致性 → JOB_BLOCKED、
  财务关键数字 → REPORT_BLOCKED、复合阻断 → 完整保留、非关键 → 不升级）；财务冲突
  的最低 REPORT_BLOCKED 由契约配置与校验规则保证，通用分类器不硬编码。
- WAITING_HUMAN：是否阻断取决于该问题是否影响主体 / 偿债能力 / 授信方案
  （由 blocking_policy 表达），不得看到任意 WAITING_HUMAN 就一律停止。
"""

from __future__ import annotations

from contracts import schema as S


def classify_blocking(question: S.KeyQuestion, state: str) -> list[str]:
    """返回问题在给定状态下的阻断后果集合（BLOCKING_LEVELS 子集，可复合）。

    - SATISFIED / NOT_APPLICABLE → []（不阻断）。
    - CONFLICT → 后果完全由该问题声明的 blocking_policy 决定（主体一致性 →
      JOB_BLOCKED、财务关键数字 → REPORT_BLOCKED、复合阻断 → 完整保留、非关键 →
      不升级）；财务 conflict_pause 至少包含 REPORT_BLOCKED，由 validator 保证。
    - NOT_PROVIDED / NOT_FOUND_AFTER_SEARCH / WAITING_HUMAN → 后果完全由
      该问题声明的 blocking_policy 决定（非核心 → 空 = 允许带缺口预览）。
    - 未知状态按最保守处理（不应发生；调用方负责状态合法性）。
    """
    if state in ("SATISFIED", "NOT_APPLICABLE"):
        return []
    if state == "CONFLICT":
        # 冲突的后果完全由该问题声明的 blocking_policy 决定：
        # 主体一致性冲突 → JOB_BLOCKED；财务关键数字冲突 → REPORT_BLOCKED；
        # 复合阻断 → 完整保留；非关键问题 → 不升级。
        # 财务冲突的最低 REPORT_BLOCKED 由契约配置 + 校验规则保证，不在此硬编码。
        return list(question.blocking_policy)
    if state in ("NOT_PROVIDED", "NOT_FOUND_AFTER_SEARCH", "WAITING_HUMAN"):
        return list(question.blocking_policy)
    return ["REPORT_BLOCKED"]


def blocking_label(levels: list[str]) -> str:
    """阻断后果集合的人类可读标签（空 = NONE）。"""
    if not levels:
        return "NONE"
    return " + ".join(levels)


def classify_question(question: S.KeyQuestion, state: str) -> dict:
    """结构化返回：状态 + 阻断后果集合 + 是否阻止正式导出 / 暂停任务 / 阻断章节。"""
    levels = classify_blocking(question, state)
    return {
        "question_id": question.question_id,
        "state": state,
        "blocking_levels": levels,
        "blocking_label": blocking_label(levels),
        "blocks_export": bool({"REPORT_BLOCKED", "SECTION_BLOCKED", "JOB_BLOCKED"} & set(levels)),
        "pauses_job": "JOB_BLOCKED" in levels,
        "blocks_section": "SECTION_BLOCKED" in levels,
    }
