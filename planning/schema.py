"""Phase 4 Batch A — 章节规划数据模型（声明式，无 I/O、无业务计算、无 LLM）。

ReportPlan / SectionTask 由确定性 Planner 派生，供章节 Worker（P4-B/C）、Store、
Evaluator（P4-D）共同引用同一份字段语义（任务书 §7.1~§7.3 的数据契约）。

身份与版本默认（任务书 §8.1~§8.2）：
- plan_id = "plan_" + sha256(input_fingerprint | contract_fingerprint | planner_version)[:24]
- task_id = "task_" + sha256(plan_id | section_id | task_schema_version)[:24]
同输入 + 同契约 + 同版本 → 相同 plan_id 与相同任务顺序（确定性、可复用）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from contracts import schema as CS

# 版本常量（变更必须递增，进 plan/task 身份派生）
PLANNER_VERSION = "p4-planner-v1"
TASK_SCHEMA_VERSION = "p4-task-v1"

# 第一阶段章节执行顺序（synthesizer 属 Phase 5；project 属产品第二阶段，均不在本批执行）
PHASE4_SECTION_ORDER = ("company", "financial", "industry")


def sha256_json(obj) -> str:
    """确定性 JSON 哈希（sort_keys + 无空格 + 不转义非 ASCII）。"""
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def derive_plan_id(input_fingerprint: str, contract_fingerprint: str) -> str:
    """plan_id 派生（任务书 §8.1）。"""
    digest = sha256_json([input_fingerprint, contract_fingerprint, PLANNER_VERSION])
    return f"plan_{digest[:24]}"


def derive_task_id(plan_id: str, section_id: str) -> str:
    """task_id 派生（任务书 §8.2）。"""
    digest = sha256_json([plan_id, section_id, TASK_SCHEMA_VERSION])
    return f"task_{digest[:24]}"


@dataclass(frozen=True)
class CreditScheme:
    """授信方案输入（Phase 5 才要求；Phase 4 允许 None，不评价方案）。"""

    amount: str | None = None
    currency: str | None = None
    term: str | None = None
    credit_type: str | None = None


@dataclass(frozen=True)
class ReportJobInput:
    """一次授信报告任务的外部输入（任务书 §7.1）。"""

    job_id: str
    company_id: str
    company_name: str
    credit_type: str
    report_as_of: str
    template_id: str = "standard_v2"
    enabled_sections: tuple[str, ...] = ("company", "financial", "industry")
    proposed_scheme: CreditScheme | None = None
    contract_version: str = CS.CONTRACT_VERSION
    evidence_inventory_fingerprint: str = ""
    financial_snapshot_id: str | None = None

    def canonical_input_dict(self) -> dict:
        """内容输入规范形（不含 job_id —— job_id 是执行标签，非内容输入）。"""
        d = {
            "company_id": self.company_id,
            "company_name": self.company_name,
            "credit_type": self.credit_type,
            "report_as_of": self.report_as_of,
            "template_id": self.template_id,
            "enabled_sections": sorted(self.enabled_sections),
            "contract_version": self.contract_version,
            "evidence_inventory_fingerprint": self.evidence_inventory_fingerprint,
            "financial_snapshot_id": self.financial_snapshot_id or "",
        }
        if self.proposed_scheme is not None:
            d["proposed_scheme"] = asdict(self.proposed_scheme)
        return d

    def input_fingerprint(self) -> str:
        return sha256_json(self.canonical_input_dict())


@dataclass(frozen=True)
class PlannedQuestion:
    """规划后的必答问题（任务书 §7.3；原样携带契约字段，供 Worker 与 Evaluator 引用）。"""

    question_id: str
    question: str
    priority: str
    topic_id: str
    required_aspects: tuple[str, ...] = ()
    evidence_requirements: tuple[dict, ...] = ()
    calculation_requirements: tuple[str, ...] = ()
    analysis_requirements: tuple[str, ...] = ()
    missing_policy: str = "write_not_found"
    blocking_policy: tuple[str, ...] = ()
    impact_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedBlockingRule:
    """解析后的完成规则（任务书 §7.3；condition 已对 credit_type 求值）。"""

    rule_id: str
    scope_id: str
    outcome: str
    applies: bool


@dataclass(frozen=True)
class SectionTask:
    """一个章节的研究任务（任务书 §7.3）。"""

    task_id: str
    plan_id: str
    section_id: str
    title: str
    purpose: str
    research_policy: str
    topic_ids: tuple[str, ...]
    questions: tuple[PlannedQuestion, ...]
    output_requirements: tuple[dict, ...]
    evaluation_rule_ids: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    blocking_rules: tuple[ResolvedBlockingRule, ...]
    dependency_versions: dict = field(default_factory=dict)

    def question_ids(self) -> list[str]:
        return [q.question_id for q in self.questions]


@dataclass(frozen=True)
class ReportPlan:
    """一次授信报告的完整规划（任务书 §7.2）。"""

    plan_id: str
    job_id: str
    company_id: str
    company_name: str
    credit_type: str
    report_as_of: str
    template_id: str
    input_fingerprint: str
    contract_fingerprint: str
    planner_version: str
    section_tasks: tuple[SectionTask, ...]
    created_at: str = ""

    def section_ids(self) -> list[str]:
        return [t.section_id for t in self.section_tasks]

    def task_ids(self) -> list[str]:
        return [t.task_id for t in self.section_tasks]


# ---------------------------------------------------------------------------
# 序列化（Store 落盘用；asdict + 显式重建，tuple/list 边界显式转换）
# ---------------------------------------------------------------------------

def section_task_to_dict(task: SectionTask) -> dict:
    return asdict(task)


def section_task_from_dict(d: dict) -> SectionTask:
    return SectionTask(
        task_id=d["task_id"],
        plan_id=d["plan_id"],
        section_id=d["section_id"],
        title=d["title"],
        purpose=d["purpose"],
        research_policy=d["research_policy"],
        topic_ids=tuple(d.get("topic_ids") or []),
        questions=tuple(
            PlannedQuestion(
                question_id=q["question_id"],
                question=q["question"],
                priority=q["priority"],
                topic_id=q["topic_id"],
                required_aspects=tuple(q.get("required_aspects") or []),
                evidence_requirements=tuple(q.get("evidence_requirements") or []),
                calculation_requirements=tuple(q.get("calculation_requirements") or []),
                analysis_requirements=tuple(q.get("analysis_requirements") or []),
                missing_policy=q.get("missing_policy") or "write_not_found",
                blocking_policy=tuple(q.get("blocking_policy") or []),
                impact_scope=tuple(q.get("impact_scope") or []),
            )
            for q in d.get("questions") or []
        ),
        output_requirements=tuple(d.get("output_requirements") or []),
        evaluation_rule_ids=tuple(d.get("evaluation_rule_ids") or []),
        allowed_capabilities=tuple(d.get("allowed_capabilities") or []),
        blocking_rules=tuple(
            ResolvedBlockingRule(
                rule_id=b["rule_id"],
                scope_id=b["scope_id"],
                outcome=b["outcome"],
                applies=bool(b["applies"]),
            )
            for b in d.get("blocking_rules") or []
        ),
        dependency_versions=dict(d.get("dependency_versions") or {}),
    )
