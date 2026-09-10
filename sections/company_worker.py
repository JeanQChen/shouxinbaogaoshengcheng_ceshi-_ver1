"""Phase 4 Batch C — 公司信用研究 Worker（复用 Phase 3 Harness 公共入口）。

确定性流程（任务书 §11）：
    对 SectionTask 的每个必答问题 → build_need → Router.route → harness.run_question
    （复用 Phase 3 公共研究入口，不复制/重写 Harness loop）→ ResearchOutcome
    → convert_question_outcome（COMPLETED/COMPLETED_WITH_GAPS → 引用支持的 Claim(+缺口)；
    UNRESOLVED/NOT_IMPLEMENTED/FAILED → 不写肯定事实，仅 SectionUnresolved）
    → 状态派生（WAITING_HUMAN > SECTION_BLOCKED/JOB_BLOCKED > COMPLETED_WITH_GAPS）
    → 依赖指纹 → 渲染 Markdown → 结构校验 → （可选）原子 commit + current_section 切换。

硬约束：
- 不把 retrieval_observation 写成事实 claim；不把搜索 snippet/URL 当正式引用。
- 不把「未检索到」写成「不存在」（NOT_FOUND_AFTER_SEARCH ≠ 事实不存在）。
- 不针对任何公司/行业/case_id 写专用业务分支。
- 复用 harness.runtime.run_question（含 routing/registry/checkpoint/trace），不自造研究循环。

CLI:
    python -m sections.company_worker --task <task.json> --company <stock> \
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
from sections import research_common as RC

logger = logging.getLogger("sections.company_worker")

# 本批版本常量（变更必须递增，进依赖指纹 → section_version 派生）。
RENDERER_VERSION = "p4-comp-renderer-v1"
RULES_VERSION = "p4-comp-rules-v1"
PROMPT_VERSION = "research_answer_v1"   # 复用 Phase 3 冻结的研究答案 prompt
WORKER_VERSION = "p4-comp-worker-v1"

# topic_id → 中文标题（renderer 用，与 standard_v2.yaml 契约一致）。
_TOPIC_LABELS: dict[str, str] = {
    "company_identity": "企业基本信息与历史沿革",
    "company_control": "股权结构、控股股东、实际控制人及控制链条",
    "company_subsidiaries": "主要子公司、集团结构与重要关联方",
    "company_business": "主营业务、经营模式、产业链、收入成本毛利构成、客户与供应商集中度",
    "company_competitiveness": "核心竞争力、研发能力、发展计划和在建工程",
    "company_governance": "公司治理、内控、管理层稳定性及主要管理人员履历",
    "company_legal_risks": "重大诉讼、违约、处罚、失信、退市风险、关联交易、股权质押和舆情",
    "company_debt": "债务、授信、发债、金融机构借款和对外担保",
    "company_profit_quality": "非主营业务和利润质量",
    "company_investment": "重大投资、收并购、资产出售、定向增发等影响经营的事件",
    "company_equity_incentive": "股权激励计划及进展",
    "company_credit_summary": "公司层面核心信用优势、风险及其偿债影响",
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
    """执行公司信用研究 Worker（复用 Harness + 确定性转换 + 渲染）。"""
    return RC.run_task(
        task, section_id="company", topic_labels=_TOPIC_LABELS,
        renderer_version=RENDERER_VERSION, rules_version=RULES_VERSION,
        prompt_version=PROMPT_VERSION, worker_version=WORKER_VERSION,
        company_id=company_id, company_name=company_name, run_id=run_id,
        scope=scope, currency=currency, purpose=purpose, as_of_date=as_of_date,
        model=model, external_research_enabled=external_research_enabled,
        budget=budget, audit_dir=audit_dir, registry=registry, llm=llm,
        build_context=build_context, route_fn=route_fn,
        research_question=research_question,
        external_db=external_db, harness_db=harness_db, checkpoint=checkpoint,
        evidence_db=evidence_db, financial_db=financial_db)


def main(argv: list[str] | None = None) -> int:
    return RC.run_cli(run_task, module_name="sections.company_worker", argv=argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
