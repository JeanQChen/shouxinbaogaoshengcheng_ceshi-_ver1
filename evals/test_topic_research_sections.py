"""Eval: Phase 4 纵向切片 — 主题研究编排（Evidence Matrix / 外部漏斗 / 预算 / Pack）。

用法: python -m evals.test_topic_research_sections

纯函数 + 注入假 registry/authority（不读库、不联网、不调 LLM）。真实 300750 材料
落地路径由 evaluation/run_phase4_vertical_slice.py 验收，本测试只验编排层新逻辑：
- TopicBudget 三样本冻结默认值；
- aspect → revenue/cost 类别派生（A5 前置）；
- MatrixCell.obtained 语义：A5 收入/成本错配拒绝、P3-B02 行业来源分级（≥1 A/B 或
  ≥2 独立 C）、CitationAuthority 通过才计数；
- ExternalFunnel 7 字段 + 分级 loss；
- derive_pack_id 内容寻址确定性；
- run_topic 缺口驱动编排 + 预算硬上限（假 registry 验证工具调用计数与提前停止）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning import schema as PS
from planning.topic_research import TopicResearchContext, derive_topic_queries
from sections.topic_research import (
    DEFAULT_TOPIC_BUDGET, ExternalFunnel, TopicBudget, assess_external_cell,
    assess_local_cell, aspect_fact_categories, budget_for, derive_pack_id,
    run_topic,
)
from tools import contracts as TC


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _Verdict:
    def __init__(self, valid: bool):
        self.valid = valid


class _Auth:
    def __init__(self, valid=True):
        self._valid = valid

    def validate(self, ref):
        return _Verdict(self._valid)


class _Fact:
    def __init__(self, fid, eid, cat, seg="", period="", value=None, page=1):
        self.evidence_fact_id = fid
        self.evidence_id = eid
        self.revenue_cost_category = cat
        self.business_segment = seg
        self.period = period
        self.value = value
        self.page_number = page

    def to_dict(self):
        return {"evidence_fact_id": self.evidence_fact_id,
                "revenue_cost_category": self.revenue_cost_category}


class _Src:
    def __init__(self, sid, grade, url, pub="2025-01-01"):
        self.source_snapshot_id = sid
        self.source_grade = grade
        self.canonical_url = url
        self.published_at = pub


def _tr(status, *, data=None, evidence_ids=None, ext_ids=None, err=None):
    return TC.ToolResult(
        call_id="", tool_name="", tool_version="", status=status,
        data=data or {}, evidence_ids=list(evidence_ids or []),
        structured_result_refs=[], external_snapshot_ids=list(ext_ids or []),
        error_code=err)


class _FakeRegistry:
    """execute 按 tool_name 分发的假 registry；默认全部 EMPTY。"""

    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls: list[str] = []

    def execute(self, call, *, route=None, run_id=None, max_retries=0):
        self.calls.append(call.tool_name)
        h = self.handlers.get(call.tool_name)
        if h:
            return h(call)
        return _tr("EMPTY", err="RETRIEVAL_EMPTY")


def _aspect(qid="company_business_main", text="各业务收入及收入占比",
            kind="table", sources=("company_industry",), min_sources=1):
    from planning.topic_research import AspectQuery
    return AspectQuery(
        aspect_id=f"asp_{text}", aspect_text=text, query_id=f"q_{text}",
        topic_id="company_business", question_id=qid, priority="P0",
        evidence_kind=kind, source_classes=sources, required_fields=(),
        freshness_policy=None, minimum_sources=min_sources,
        local_query=f"宁德时代 {text}", external_query=None)


def _ext_aspect(text="行业规模"):
    from planning.topic_research import AspectQuery
    return AspectQuery(
        aspect_id=f"asp_{text}", aspect_text=text, query_id=f"q_{text}",
        topic_id="industry_scale_cycle", question_id="industry_scale_cycle",
        priority="P0", evidence_kind="web", source_classes=("external",),
        required_fields=(), freshness_policy=None, minimum_sources=1,
        local_query=None, external_query=f"动力电池 {text}")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

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

    # 1) 三样本冻结预算
    b = budget_for("company_business_main")
    check((b.max_tool_calls, b.max_tokens, b.max_llm_calls, b.max_elapsed_ms)
          == (12, 24000, 2, 120000), "company_business_main 预算 12/24000/2/120000")
    s = budget_for("industry_scale_cycle")
    check((s.max_tool_calls, s.max_tokens, s.max_llm_calls, s.max_elapsed_ms,
           s.max_external_queries, s.max_candidate_fetches, s.max_snapshots)
          == (12, 32000, 3, 180000, 3, 4, 4),
          "industry_scale_cycle 预算 12/32000/3/180000 + ext3/fetch4/snap4")
    t = budget_for("industry_risk_transmission")
    check((t.max_tool_calls, t.max_tokens, t.max_llm_calls, t.max_elapsed_ms)
          == (16, 48000, 4, 180000), "industry_risk_transmission 预算 16/48000/4/180000")
    check(budget_for("unknown_q") == DEFAULT_TOPIC_BUDGET, "未知 question_id 回退默认预算")

    # 2) aspect → revenue/cost 类别派生
    check(aspect_fact_categories("各业务收入及收入占比") == ("revenue",),
          "收入 aspect → revenue")
    check(aspect_fact_categories("各业务成本与毛利构成") == ("cost",),
          "成本/毛利 aspect → cost")
    check(aspect_fact_categories("收入与成本构成") == ("revenue", "cost"),
          "收入+成本 aspect → revenue,cost")
    check(aspect_fact_categories("行业规模") == (), "无收入/成本词 aspect → 不限类别")

    # 3) assess_local_cell：A5 + CitationAuthority + min_sources
    auth = _Auth(valid=True)
    rev = _Fact("ef-r", "e1", "revenue", "动力电池系统", "2025-12-31", 100)
    cst = _Fact("ef-c", "e2", "cost", "动力电池系统", "2025-12-31", 80)
    cell_ok = assess_local_cell(aspect=_aspect(), facts=[rev], authority=auth)
    check(cell_ok.obtained and cell_ok.validation_status == "PASS",
          "收入 aspect 用收入事实背书 → obtained")
    check(cell_ok.fact_ids == ("ef-r",) and cell_ok.citation_refs[0].ref_type == "evidence",
          "MatrixCell 绑定 fact_id + evidence 引用")
    cell_mismatch = assess_local_cell(aspect=_aspect(), facts=[cst], authority=auth)
    check(not cell_mismatch.obtained
          and "a5_revenue_cost_mismatch" in cell_mismatch.loss_reasons,
          "A5：收入 aspect 用成本事实背书 → 拒绝")
    cell_min = assess_local_cell(aspect=_aspect(min_sources=2), facts=[rev], authority=auth)
    check(not cell_min.obtained and cell_min.validation_status == "PARTIAL",
          "min_sources=2 但只有 1 个有效事实 → PARTIAL 未 obtained")
    auth_bad = _Auth(valid=False)
    cell_bad = assess_local_cell(aspect=_aspect(), facts=[rev], authority=auth_bad)
    check(not cell_bad.obtained, "权威校验不过 → 不计数（fail-closed）")

    # 4) assess_external_cell：P3-B02 行业来源分级
    key_topic = "industry_scale_cycle"
    single_c = assess_external_cell(
        aspect=_ext_aspect(),
        sources=[_Src("s1", "C", "https://eastmoney.com/a")],
        authority=_Auth(True), topic_id=key_topic)
    check(not single_c.obtained, "关键结论：单个 C 来源不足（single_c_only）")
    two_c = assess_external_cell(
        aspect=_ext_aspect(),
        sources=[_Src("s1", "C", "https://eastmoney.com/a"),
                 _Src("s2", "C", "https://thepaper.cn/b")],
        authority=_Auth(True), topic_id=key_topic)
    check(two_c.obtained, "关键结论：2 个独立 C 来源 → obtained")
    one_b = assess_external_cell(
        aspect=_ext_aspect(), sources=[_Src("s1", "B", "https://gov.cn/a")],
        authority=_Auth(True), topic_id=key_topic)
    check(one_b.obtained, "关键结论：1 个 A/B 来源 → obtained")
    nonkey = assess_external_cell(
        aspect=_ext_aspect(), sources=[_Src("s1", "C", "https://eastmoney.com/a")],
        authority=_Auth(True), topic_id="other_topic")
    check(nonkey.obtained, "非关键主题：单个通过校验来源即可 obtained")
    bad_src = assess_external_cell(
        aspect=_ext_aspect(), sources=[_Src("s1", "C", "https://eastmoney.com/a")],
        authority=_Auth(False), topic_id=key_topic)
    check(not bad_src.obtained and bad_src.validation_status == "NOT_FOUND",
          "外部来源权威校验不过 → NOT_FOUND")

    # 5) ExternalFunnel 7 字段 + 分级 loss
    f = ExternalFunnel()
    f = f.record_loss("candidate_urls", "search_empty").with_counts(candidate_urls=3)
    check(list(ExternalFunnel.__dataclass_fields__)[:7]
          == ["external_queries", "candidate_urls", "fetched", "snapshotted",
              "extracted_facts", "validated_facts", "adopted_facts"],
          "ExternalFunnel 7 字段顺序固定")
    check(f.loss_reasons == (("candidate_urls", "search_empty"),)
          and f.candidate_urls == 3, "漏斗分级 loss + 计数更新")

    # 6) derive_pack_id 内容寻址确定性
    ctx = TopicResearchContext(company_id="300750", company_name="宁德时代",
                               industry_names=("动力电池",), report_as_of="2026-03-31")
    cell = assess_local_cell(aspect=_aspect(), facts=[rev], authority=auth)
    pid1 = derive_pack_id(topic_id="company_business", question_id="company_business_main",
                          context=ctx, budget=b, matrix=(cell,), funnel=f,
                          verified_facts=(), evidence_facts=(rev.to_dict(),))
    pid2 = derive_pack_id(topic_id="company_business", question_id="company_business_main",
                          context=ctx, budget=b, matrix=(cell,), funnel=f,
                          verified_facts=(), evidence_facts=(rev.to_dict(),))
    check(pid1 == pid2 and pid1.startswith("pack_") and len(pid1) == 37,
          "derive_pack_id 确定性（同输入同 id，pack_+32hex）")

    # 7) run_topic：本地空检索 → NOT_FOUND + 工具调用计数 + stop_reason
    from planning.topic_research import TopicQueryPlan
    plan = TopicQueryPlan(topic_id="company_business", context=ctx,
                          aspects=(_aspect(), _aspect(text="各业务成本与毛利构成")))
    reg = _FakeRegistry()  # 全部 EMPTY
    pack = run_topic(plan, question_id="company_business_main", budget=b,
                     company_id="300750", authority=auth, registry=reg)
    check(len(pack.matrix) == 2 and all(not c.obtained for c in pack.matrix),
          "本地空检索：全部 aspect NOT_FOUND")
    check(pack.usage["tool_calls"] == 4,  # 2 aspects × (search_tables + search_evidence)
          "空检索仍计入工具调用（2 aspect × 2 搜索 = 4）")
    check(pack.stop_reason == "completed" and len(pack.verified_facts) == 0,
          "空检索 completed + 无 verified_facts")

    # 8) run_topic：预算硬上限提前停止
    tiny = TopicBudget(max_tool_calls=2, max_tokens=1000, max_llm_calls=1,
                       max_elapsed_ms=10000)
    reg2 = _FakeRegistry()
    pack2 = run_topic(plan, question_id="company_business_main", budget=tiny,
                      company_id="300750", authority=auth, registry=reg2)
    check(pack2.usage["tool_calls"] <= 2 and pack2.stop_reason == "budget_exhausted",
          "预算耗尽提前停止（tool_calls ≤ max_tool_calls）")

    # 9) run_topic：外部路径 happy-path（A 级来源 → obtained + 漏斗计数）
    def _search(call):
        return _tr("SUCCESS", data={"results": [{
            "title": "行业规模统计", "url": "https://gov.cn/scale",
            "snippet": "规模", "published_at": "2025-01-01",
            "source_name": "gov", "source_grade": "A"}]})

    def _fetch(call):
        return _tr("SUCCESS", data={
            "content_text": "2025 年动力电池行业规模约 1.2 万亿元",
            "canonical_url": "https://gov.cn/scale",
            "content_type": "text/html", "http_status": 200,
            "content_hash": "h1"})

    def _snap(call):
        return _tr("SUCCESS", data={"source_snapshot_id": "snap-ext-1"},
                   ext_ids=["snap-ext-1"])

    reg3 = _FakeRegistry(handlers={
        "search_external_sources": _search,
        "fetch_external_content": _fetch,
        "snapshot_external_source": _snap,
    })
    ext_plan = TopicQueryPlan(topic_id="industry_scale_cycle", context=ctx,
                              aspects=(_ext_aspect(),))
    pack3 = run_topic(ext_plan, question_id="industry_scale_cycle", budget=s,
                      company_id="300750", authority=auth, registry=reg3)
    check(len(pack3.matrix) == 1 and pack3.matrix[0].obtained,
          "外部路径：A 级来源 → aspect obtained")
    check(pack3.funnel.external_queries == 1 and pack3.funnel.candidate_urls == 1
          and pack3.funnel.fetched == 1 and pack3.funnel.snapshotted == 1
          and pack3.funnel.adopted_facts == 1,
          "外部漏斗逐级计数：query→candidate→fetch→snapshot→adopted 全 1")
    check(len(pack3.external_sources) == 1
          and pack3.external_sources[0]["source_snapshot_id"] == "snap-ext-1"
          and pack3.external_sources[0]["source_grade"] == "A",
          "已采纳外部来源落 pack（含 snapshot_id + 等级）")

    # 10) pack 序列化：to_dict 含关键结构
    d = pack3.to_dict()
    check(set(["pack_id", "topic_id", "question_id", "context", "budget", "matrix",
               "funnel", "verified_facts", "evidence_facts", "external_sources",
               "usage", "stop_reason", "created_at"]) <= set(d),
          "TopicResearchPack.to_dict 含全部关键字段")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)
