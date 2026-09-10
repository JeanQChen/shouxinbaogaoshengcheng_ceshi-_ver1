"""Phase 4 Batch C — 行业研究 Worker（复用 Phase 3 Harness 公共入口）。

确定性流程（任务书 §11）：
    对 SectionTask 的每个必答问题 → build_need → Router.route → harness.run_question
    （复用 Phase 3 公共研究入口，不复制/重写 Harness loop）→ ResearchOutcome
    → convert_question_outcome → 状态派生 → 依赖指纹 → 渲染 Markdown
    → 结构校验 → （可选）原子 commit + current_section 切换。

与公司 Worker 的唯一差异（§11.4）：额外产出「来源分级分布」诊断（source_grade_distribution，
A/B/C/D 统计），供 P4-D Evaluator 做充分性判断；来源等级低不自动阻断、不进章节状态，
本 Worker 只报告不判定。

硬约束：
- 不把 retrieval_observation 写成事实 claim；不把搜索 snippet/URL 当正式引用。
- 不把「未检索到」写成「不存在」。
- 不针对任何公司/行业/case_id 写专用业务分支。
- 复用 harness.runtime.run_question，不自造研究循环。

CLI:
    python -m sections.industry_worker --task <task.json> --company <stock> \
        [--company-name <name>] [--ev-db <ev.db>] [--fin-db <fin.db>] \
        [--section-db <sections.db>] [--external-db <ext.db>] [--harness-db <h.db>] \
        [--validate-only] [--store] [--out <markdown>]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from harness import policies as P
from planning import schema as PS
from sections import industry_source_policy as ISP
from sections import research_common as RC

logger = logging.getLogger("sections.industry_worker")

# 本批版本常量（变更必须递增，进依赖指纹 → section_version 派生）。
RENDERER_VERSION = "p4-ind-renderer-v1"
RULES_VERSION = "p4-ind-rules-v1"
PROMPT_VERSION = "research_answer_v1"   # 复用 Phase 3 冻结的研究答案 prompt
WORKER_VERSION = "p4-ind-worker-v1"

# topic_id → 中文标题（renderer 用，与 standard_v2.yaml 契约一致）。
_TOPIC_LABELS: dict[str, str] = {
    "industry_definition": "行业定义、边界与公司所属细分领域",
    "industry_scale_cycle": "行业规模、增速与当前周期位置",
    "industry_supply_demand": "供需关系、价格与成本驱动因素",
    "industry_competition": "竞争格局、集中度和主要参与者",
    "industry_policy": "政策、监管、技术替代和外部冲击",
    "industry_position": "公司行业地位和相对竞争能力",
    "industry_comparables": "可比公司选择与相对比较",
    "industry_risk_transmission": "行业风险向授信主体的传导路径",
    "industry_monitoring": "行业结论的有效期和监测指标",
}


def run_task(task: PS.SectionTask, *, company_id: str, company_name: str = "",
             run_id: str = "", scope: str = "consolidated", currency: str = "CNY",
             purpose: str = "credit_analysis", as_of_date: str | None = None,
             model: str | None = None, external_research_enabled: bool | None = None,
             budget: P.ResearchBudget | None = None,
             audit_dir: str | Path | None = None, registry=None, llm=None,
             build_context=None, route_fn=None, research_question=None,
             external_db: str | None = "data/external_sources.db",
             harness_db: str | None = "data/harness.db",
             checkpoint: bool = True,
             evidence_db: str | None = "data/evidence.db",
             financial_db: str | None = "data/financial_v2.db") -> RC.ResearchWorkerResult:
    """执行行业研究 Worker（复用 Harness + 确定性转换 + 渲染 + 来源分级诊断）。"""
    return RC.run_task(
        task, section_id="industry", topic_labels=_TOPIC_LABELS,
        renderer_version=RENDERER_VERSION, rules_version=RULES_VERSION,
        prompt_version=PROMPT_VERSION, worker_version=WORKER_VERSION,
        company_id=company_id, company_name=company_name, run_id=run_id,
        scope=scope, currency=currency, purpose=purpose, as_of_date=as_of_date,
        model=model, external_research_enabled=external_research_enabled,
        budget=budget, audit_dir=audit_dir, registry=registry, llm=llm,
        build_context=build_context, route_fn=route_fn,
        research_question=research_question,
        external_db=external_db, harness_db=harness_db, checkpoint=checkpoint,
        evidence_db=evidence_db, financial_db=financial_db,
        source_policy=ISP.apply_industry_source_policy)


def main(argv: list[str] | None = None) -> int:
    return RC.run_cli(run_task, module_name="sections.industry_worker", argv=argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
