"""Eval: service 级正式链离线集成（§八 补充）—— 真正从 ``sections.service.run_phase4`` 起。

用法: python -m evals.test_phase4_service_formal_chain

与 ``test_phase4_formal_chain`` 的差异：后者直接调 ``company_worker.run_task``，本测试
从服务入口 ``sections.service.run_phase4`` 起，证明唯一正式主链：

    run_phase4 → company_worker.run_task → research_common.run_task
      → routing.router.route → harness.runtime.run_question → ToolRegistry.execute

仅 mock 外部状态/时间（真实 LLM / 博查 / 网络 / Embedding / 只读 DB / 检索审计 / 路由审计 /
trace 落盘 / citation authority / 财务 LLM），**不 mock** 主链编排：
``run_phase4`` / ``company_worker.run_task`` / ``research_common.run_task`` / ``router.route`` /
``harness.runtime.run_question`` / ``ToolRegistry.execute``。用临时目录 + 临时契约（含单问
company_business_main），不写真实 ``data/*.db``、不联网、不生成真实报告。

覆盖：一个正常完成路径（5 aspect 全覆盖）＋ 一个显式缺口路径（STOP_WITH_GAP → no_valid_claim）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import schema as PS
from sections import service as SV
from sections import company_worker as CW
from sections import topic_research as STR
from harness import runtime as RT
from harness import trace as HT
from routing import router as router_mod
from routing import context as rctx_mod
from routing import schema as RS
from routing import audit_v2
from sections import citation_authority as CA
from tools import adapters as AD

from evals.test_harness_runtime import MockLLM, _fake_registry

COMPANY_ID = "300750"
COMPANY_NAME = "宁德时代"
REPORT_AS_OF = "2025-12-31"

# 最小契约：四章节结构合法（validator 要求 section 顺序/阈值），但仅 company 启用且只含
# company_business_main 单问，保证离线可控。question_id 与 standard_v2.yaml 一致，使
# harness.aspects 的 SECTION_CONTRACT 来源命中真实契约的 5 个 required_aspects。
_CONTRACT = """\
contract_version: v1
policies:
  missing:
    - policy_id: write_not_found
      description: 明确写"未在给定材料及已执行来源中检索到"，不视为事实不存在，允许带缺口预览
sections:
  - section_id: company
    title: 公司信用研究
    purpose: 确认申请授信的法人主体是否合法存续、控制权是否清晰、经营是否有效
    research_policy: harness
    allowed_capabilities: [search_evidence, inspect_evidence, compare_evidence, verify_claim, search_external_sources]
    required_topics:
      - topic_id: company_business
        title: 主营业务、经营模式、产业链、收入成本毛利构成
        required: true
        key_questions:
          - question_id: company_business_main
            question: 主营业务、收入/成本/毛利构成与产业链位置；主营业务完全无法确认时阻断章节
            required_aspects:
              - 主营业务构成
              - 各业务收入及收入占比
              - 各业务成本与毛利构成
              - 产业链位置
              - 对应报告期与口径
            priority: P0
            blocking_policy: [SECTION_BLOCKED]
            impact_scope: [subject]
            missing_policy: write_not_found
            evidence_requirements:
              - requirement_id: er_comp_business_main
                evidence_kind: table
                source_classes: [company_industry]
                minimum_sources: 1
                required_fields: [主营业务, 收入构成, 毛利构成]
  - section_id: financial
    title: 财务分析
    purpose: 识别超过 15% 的重大科目并评估其对偿债能力的影响
    research_policy: workflow
  - section_id: industry
    title: 行业分析
    purpose: 分析行业竞争格局与政策
    research_policy: harness
  - section_id: synthesizer
    title: 综合分析
    purpose: 汇总各章节结论
    research_policy: workflow
"""

# 答案 JSON：5 个 fact claim 各引用 evidence e1，5 个 required-aspect 全覆盖。
_ANSWER_JSON = json.dumps({
    "answer_text": "主营业务构成及各业务收入成本毛利构成、产业链位置与对应报告期口径均已核实披露",
    "claims": [
        {"claim_id": "c1", "text": "主营业务构成已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c2", "text": "各业务收入及收入占比已核实并披露，规模达数十亿元",
         "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c3", "text": "各业务成本与毛利构成已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c4", "text": "产业链位置已核实并披露", "kind": "fact", "citation_refs": [0]},
        {"claim_id": "c5", "text": "对应报告期与口径已核实并披露", "kind": "fact", "citation_refs": [0]},
    ],
    "citations": [{"ref_type": "evidence", "evidence_id": "e1"}],
    "aspects": [
        {"aspect_id": "a1", "text": "主营业务构成", "claim_ids": ["c1"]},
        {"aspect_id": "a2", "text": "各业务收入及收入占比", "claim_ids": ["c2"]},
        {"aspect_id": "a3", "text": "各业务成本与毛利构成", "claim_ids": ["c3"]},
        {"aspect_id": "a4", "text": "产业链位置", "claim_ids": ["c4"]},
        {"aspect_id": "a5", "text": "对应报告期与口径", "claim_ids": ["c5"]},
    ],
    "unresolved_items": [],
    "confidence": "high",
}, ensure_ascii=False)

_NORMAL_ACTIONS = [
    '{"action": "SEARCH_LOCAL", "arguments": {"query": "主营业务构成"}}',
    '{"action": "INSPECT_EVIDENCE", "arguments": {"evidence_id": "e1"}}',
    '{"action": "ANSWER", "arguments": {}}',
]
_GAP_ACTIONS = [
    '{"action": "STOP_WITH_GAP", "arguments": {"reason": "缺关键材料"}}',
]


def _build_context() -> RS.RouteContext:
    """合成 RouteContext（能力/数据全空 → 无 DB/external/结构化子 need，纯 RAG）。"""
    return RS.RouteContext(
        company_id=COMPANY_ID, report_as_of=REPORT_AS_OF,
        available_document_ids=[], available_source_types=[],
        supported_db_fields=[], supported_metric_ids=[],
        available_db_fields=[], available_metric_ids=[],
        external_research_enabled=False,
        scope="consolidated", currency="CNY", purpose="credit_analysis",
        available_periods=[], snapshot_id=None)


def _fake_llm_eval(messages, system):  # noqa: ARG001
    return json.dumps({"decision": "PASS", "issues": [], "rework_targets": []})


def _run_service(actions: list[str], answers: list[str]) -> tuple[SV.Phase4RunResult, dict]:
    """在临时目录 + 全量 mock（仅外部状态/时间）下从 run_phase4 跑一次，返回 (result, spy)。"""
    spy = {"run_phase4": 0, "worker": 0, "route": 0, "run_question": 0,
           "execute": 0, "run_topic": 0}

    # --- 捕获被替换的原始对象，finally 恢复 ---
    orig = {}
    orig["run_phase4"] = SV.run_phase4
    orig["cw_run_task"] = CW.run_task
    orig["route"] = router_mod.route
    orig["run_question"] = RT.run_question
    orig["build_registry"] = AD.build_default_registry
    orig["run_topic"] = STR.run_topic
    orig["build_context"] = rctx_mod.build_route_context
    orig["build_authority"] = CA.build_citation_authority
    orig["emit"] = HT.emit
    orig["write_router_audit"] = audit_v2.write_router_audit
    orig["real_llm"] = RT.RealResearchLLM

    # --- 主链 spy（真实函数，只计数再委托；run_topic 计数且不委托）---
    def spy_run_phase4(*a, **k):
        spy["run_phase4"] += 1
        return orig["run_phase4"](*a, **k)

    def spy_cw_run_task(*a, **k):
        spy["worker"] += 1
        return orig["cw_run_task"](*a, **k)

    def spy_route(need, context, fallback=None):
        spy["route"] += 1
        return orig["route"](need, context, fallback=fallback)

    def spy_run_question(**k):
        spy["run_question"] += 1
        return orig["run_question"](**k)

    def spy_build_registry(audit_dir=None):
        reg = _fake_registry()
        _exec = reg.execute

        def spy_execute(*a, **k):
            spy["execute"] += 1
            return _exec(*a, **k)

        reg.execute = spy_execute
        return reg

    def spy_run_topic(*_a, **_k):
        spy["run_topic"] += 1

    # --- 外部状态/时间 mock ---
    def fake_build_context(company_id, **k):  # noqa: ARG001
        return _build_context()

    def fake_build_authority(*_a, **_k):
        return None

    def _llm_factory(model=None):  # noqa: ARG001
        return MockLLM(actions, answers)

    SV.run_phase4 = spy_run_phase4
    CW.run_task = spy_cw_run_task
    router_mod.route = spy_route
    RT.run_question = spy_run_question
    AD.build_default_registry = spy_build_registry
    STR.run_topic = spy_run_topic
    rctx_mod.build_route_context = fake_build_context
    CA.build_citation_authority = fake_build_authority
    HT.emit = lambda *_a, **_k: None
    audit_v2.write_router_audit = lambda *_a, **_k: None
    RT.RealResearchLLM = _llm_factory

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            contract_file = tmp / "contract_min.yaml"
            contract_file.write_text(_CONTRACT, encoding="utf-8")
            fin_db = tmp / "financial_v2.db"
            ev_db = tmp / "evidence.db"
            fin_db.touch()
            ev_db.touch()

            cfg = SV.ServiceConfig(
                contracts_path=str(contract_file),
                fin_db=str(fin_db), ev_db=str(ev_db),
                ext_db=str(tmp / "external_sources.db"),
                harness_db=str(tmp / "harness.db"),
                section_db=str(tmp / "sections.db"),
                audit_dir=str(tmp / "audit"),
                checkpoint=False)

            job = PS.ReportJobInput(
                job_id="job_svc_fc", company_id=COMPANY_ID, company_name=COMPANY_NAME,
                credit_type="other", report_as_of=REPORT_AS_OF,
                enabled_sections=("company",))

            res = SV.run_phase4(
                job, service_cfg=cfg,
                llm_generate=lambda m, s: "",  # noqa: ARG001 - 仅 financial 用，本测试不启用
                llm_evaluator_generate=_fake_llm_eval)
            return res, spy
    finally:
        SV.run_phase4 = orig["run_phase4"]
        CW.run_task = orig["cw_run_task"]
        router_mod.route = orig["route"]
        RT.run_question = orig["run_question"]
        AD.build_default_registry = orig["build_registry"]
        STR.run_topic = orig["run_topic"]
        rctx_mod.build_route_context = orig["build_context"]
        CA.build_citation_authority = orig["build_authority"]
        HT.emit = orig["emit"]
        audit_v2.write_router_audit = orig["write_router_audit"]
        RT.RealResearchLLM = orig["real_llm"]


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # -------------------------------------------------------------- 正常完成路径
    res, spy = _run_service(_NORMAL_ACTIONS, [_ANSWER_JSON])
    sec = res.sections[0]

    check(spy["run_phase4"] == 1, "service.run_phase4 被调用（真实入口，非 mock）")
    check(spy["worker"] == 1, "company_worker.run_task 被调用（真实 Worker）")
    check(spy["route"] >= 1, "routing.router.route 被调用（真实 Router）")
    check(spy["run_question"] >= 1, "harness.runtime.run_question 被调用（真实 Harness）")
    check(spy["execute"] >= 1, "ToolRegistry.execute 执行 ≥1 次")
    check(spy["run_topic"] == 0, "sections.topic_research.run_topic == 0（无第二套研究运行时）")

    check(sec.error is None and sec.section_result is not None,
          "正常路径无编排错误")
    check(len(sec.section_result.claims) == 5,
          "正常路径 5 个有效 claim 进入 SectionResult")
    check(sec.section_result.status in ("COMPLETED", "COMPLETED_WITH_GAPS"),
          "正常路径章节 status 完成（非阻断）")
    check(sec.section_result.task_id == PS.derive_task_id(res.plan_id, "company"),
          "正常路径 task_id 由 plan_id + section 确定性派生（Contract 身份保留）")

    # -------------------------------------------------------------- 显式缺口路径
    res_gap, spy_gap = _run_service(_GAP_ACTIONS, [])
    sec_gap = res_gap.sections[0]

    check(spy_gap["run_phase4"] == 1, "缺口路径 run_phase4 被调用")
    check(spy_gap["worker"] == 1 and spy_gap["route"] >= 1
          and spy_gap["run_question"] >= 1, "缺口路径主链各跳均被调用")
    check(spy_gap["run_topic"] == 0, "缺口路径同样不调用 run_topic")
    check(sec_gap.error is None and sec_gap.section_result is not None,
          "缺口路径无编排错误（不因缺口崩溃）")
    check(len(sec_gap.section_result.claims) == 0,
          "缺口路径 0 claim（不伪作结论）")
    check(sec_gap.section_result.status == "SECTION_BLOCKED",
          "缺口路径章节 SECTION_BLOCKED")
    check(any(u.reason_code == "no_valid_claim"
              for u in sec_gap.section_result.unresolved),
          "缺口路径生成显式 no_valid_claim unresolved")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
