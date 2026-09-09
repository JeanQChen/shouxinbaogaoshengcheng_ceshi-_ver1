"""Phase 3 Batch B 41 问 Actual-Path Runner（真实 Router + 受限研究循环）。

对冻结 41 问逐题：Router 判定实际路径 → harness.runtime.run_question 执行有界研究循环
（真实 Registry + 真实 LLM）→ checkpoint 落盘 outcome → 聚合统计。

关键约束（PHASE3 任务书 §7.7 + 用户修订）：
- 问题文本只使用 `case.question` 原文，绝不使用 gold_answer 扩展、不注入答案关键词；
- 成功判定不靠 LLM 自评：harness.state.evaluate_success 确定性主判据（引用可回查 +
  无核心 unresolved），再加路由级确定性下限（DB 需结构化结果、EXTERNAL 需真实快照等）；
- gold document/page 仅进入 Runner 完成后的离线诊断（page_diagnosis），绝不进 runtime；
- 未实现/未配置/未执行路径标记 NOT_IMPLEMENTED / UNRESOLVED，不计成功；
- --resume-run-id 先做 RunManifest 全量兼容校验，任一字段不一致 fail-closed 拒绝 resume；
- 外部调用「可获得的成本」：provider 不返回 cost，报告记 counts + 明示 cost 不可得。

CLI:
  python -m evaluation.run_actual_path_41 \
    --dataset evaluation/datasets/v1_baseline.jsonl \
    --company 300750 \
    --output evaluation/results/actual_path_41
  python -m evaluation.run_actual_path_41 --dataset ... --company 300750 --validate-only
  python -m evaluation.run_actual_path_41 --dataset ... --company 300750 --case-id COMP-S1
  python -m evaluation.run_actual_path_41 --dataset ... --company 300750 --limit 5
  python -m evaluation.run_actual_path_41 --dataset ... --company 300750 --resume-run-id <run_id>
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from evaluation.dataset import load_dataset
from harness import checkpoint as C
from harness import policies as P
from harness import runtime as RT
from harness import schema as H
from harness import state as HS
from harness import structured_needs as SN
from routing import context as routing_context
from routing import router as router_mod
from routing import schema as RS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 实际路径完成分类（§7.7 FULL/PARTIAL/UNRESOLVED/NOT_IMPLEMENTED/FAILED）。
ACTUAL_STATUSES = ("FULL", "PARTIAL", "UNRESOLVED", "NOT_IMPLEMENTED", "FAILED")

# completion_status（runtime 五态）→ 实际路径分类（未做路由级降级前的基础映射）。
_COMPLETION_TO_ACTUAL = {
    "COMPLETED": "FULL",
    "COMPLETED_WITH_GAPS": "PARTIAL",
    "UNRESOLVED": "UNRESOLVED",
    "NOT_IMPLEMENTED": "NOT_IMPLEMENTED",
    "FAILED": "FAILED",
}

# 外部来源报告口径 policy 版本（P3-B01/B02 按推荐默认值运行，标记 provisional）。
EXTERNAL_POLICY_VERSION = "v1-provisional"

# harness 源文件指纹范围（代码漂移进入 RunManifest，供 resume 兼容校验）。
_HARNESS_FILES = (
    "schema.py", "state.py", "policies.py", "actions.py",
    "runtime.py", "checkpoint.py", "trace.py",
)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: str | Path) -> str:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _harness_fingerprint() -> str:
    """harness 包源码指纹（代码漂移检测，resume fail-closed 用）。"""
    harness_dir = Path(__file__).resolve().parent.parent / "harness"
    h = hashlib.sha256()
    for name in _HARNESS_FILES:
        p = harness_dir / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(b"MISSING")
        h.update(b"\0")
    return h.hexdigest()


def _evidence_fingerprint(company_id: str, ev_db_path: str) -> str:
    """Evidence Store current 文档清单指纹（document_id + version，排序稳定）。"""
    from evidence import store as estore
    estore.init_db(ev_db_path)
    records: list[list] = []
    for d in estore.list_documents(company_id):
        if d.status != "current":
            continue
        records.append([d.document_id, estore.current_document_version(company_id, d.document_id)])
    records.sort()
    return hashlib.sha256(json.dumps(records, ensure_ascii=False).encode("utf-8")).hexdigest()


def _current_snapshot_id(company_id: str, context: RS.RouteContext,
                         fin_db_path: str) -> str | None:
    """解析 current Financial Snapshot id（无快照返回 None，合法空态）。"""
    from financial_v2 import progress, snapshots, store as fstore
    fstore.init_db(fin_db_path)
    as_of = context.report_as_of
    try:
        if as_of is None:
            req = progress.build_request_for_company(
                company_id, scope=context.scope, currency=context.currency,
                purpose=context.purpose)
            as_of = req.as_of_date
        snap = snapshots.current_snapshot(company_id, context.scope,
                                          context.currency, as_of, context.purpose)
        return snap.snapshot_id if snap is not None else None
    except ValueError:
        return None


def _git_state() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        commit = ""
    return {"commit": commit}


# ---------------------------------------------------------------------------
# 需求构造 / 路由
# ---------------------------------------------------------------------------

def _build_need(case) -> RS.InformationNeed:
    """从 case 构造 InformationNeed（只取 question 原文，不碰 gold_answer）。"""
    return RS.InformationNeed(
        need_id=case.case_id, section_id=case.section_id, question=case.question,
        required_evidence_types=[], required_source_types=[],
        time_scope=case.time_scope, priority=case.priority, depends_on=[])


# ---------------------------------------------------------------------------
# 完成分类（确定性，§7.7 最低规则）
# ---------------------------------------------------------------------------

def classify_completion(outcome: H.ResearchOutcome, route: str | None) -> str:
    """把 runtime completion_status 映射为实际路径五态，并叠加路由级确定性下限。

    runtime 已通过 state.evaluate_success 强制「引用可回查」，这里再加路由级
    belt-and-suspenders：某路由的必需最小材料缺失时，即便 runtime 判 COMPLETED
    也降为 PARTIAL（如 EXTERNAL 只有 snippet 无快照、DB 无结构化结果、本地无证据）。
    """
    cs = outcome.completion_status
    if cs == "COMPLETED":
        st = outcome.state
        if route == "EXTERNAL_RESEARCH" and not st.external_snapshot_ids:
            return "PARTIAL"
        if route == "DB_LOOKUP" and not st.structured_refs:
            return "PARTIAL"
        if route in ("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL") and not st.evidence_ids:
            return "PARTIAL"
        return "FULL"
    return _COMPLETION_TO_ACTUAL.get(cs, "FAILED")


# ---------------------------------------------------------------------------
# 本地证据页离线诊断（gold 只在此进入，不回流 runtime）
# ---------------------------------------------------------------------------

def _diagnose_local_pages(case, evidence_pages: set[tuple[str, int]]) -> dict:
    """把已取得 Evidence (document_id, page) 与 gold 本地页比对（仅诊断）。

    Returns:
        {n_required_pages, hit_required_pages, required_page_coverage, page_hit}。
    无本地 gold 组返回 {applicable: False}。
    """
    targets = case.local_targets()
    if not targets:
        return {"applicable": False}
    gold = {t.key() for t in targets}
    required = [t for t in targets if t.document_id is not None and t.pdf_page is not None]
    if not required:
        return {"applicable": True, "n_required_pages": 0, "hit_required_pages": 0,
                "required_page_coverage": 0.0, "page_hit": False}
    hit = {k for k in gold if k in evidence_pages}
    return {
        "applicable": True,
        "n_required_pages": len(required),
        "hit_required_pages": len(hit),
        "required_page_coverage": len(hit) / len(required),
        "page_hit": len(hit) > 0,
    }


def _resolve_evidence_pages(evidence_ids: list[str], ev_db_path: str) -> set[tuple[str, int]]:
    """把 state.evidence_ids 解析为 (document_id, page_number) 集合（离线诊断用）。"""
    from evidence import store as estore
    estore.init_db(ev_db_path)
    pages: set[tuple[str, int]] = set()
    for eid in evidence_ids:
        b = estore.get_evidence(eid)
        if b is not None and b.document_id is not None:
            pages.add((b.document_id, int(b.page_number or 0)))
    return pages


# ---------------------------------------------------------------------------
# RunManifest
# ---------------------------------------------------------------------------

def _build_run_manifest(*, run_id: str, dataset_path: str, company_id: str,
                        report_as_of: str | None, model: str, budget: dict,
                        context: RS.RouteContext, ev_db_path: str,
                        fin_db_path: str) -> C.RunManifest:
    prompt_versions = {
        "research_action_v1": _sha256_text(RT.llm_client.load_prompt("research_action_v1")),
        "research_answer_v1": _sha256_text(RT.llm_client.load_prompt("research_answer_v1")),
    }
    return C.RunManifest(
        run_id=run_id,
        dataset_sha256=_sha256_file(dataset_path),
        company_id=company_id,
        report_as_of=report_as_of,
        contract_version=H.HARNESS_VERSION,
        router_fingerprint=RS.RULE_VERSION,
        prompt_versions=prompt_versions,
        model=model,
        budget=budget,
        evidence_fingerprint=_evidence_fingerprint(company_id, ev_db_path),
        snapshot_id=_current_snapshot_id(company_id, context, fin_db_path),
        external_policy_version=EXTERNAL_POLICY_VERSION,
        harness_fingerprint=_harness_fingerprint(),
    )


def manifest_mismatches(stored: C.RunManifest, new: C.RunManifest) -> list[str]:
    """返回 stored 与 new 之间不一致的字段名列表（空 = 兼容）。

    fail-closed：run_id 一致但任一输入指纹漂移 → 拒绝 resume。
    """
    mismatches: list[str] = []
    a, b = stored.as_dict(), new.as_dict()
    for key in a:
        if a[key] != b.get(key):
            mismatches.append(key)
    return mismatches


# ---------------------------------------------------------------------------
# 单题结果记录
# ---------------------------------------------------------------------------

def _jsonable(o):
    if is_dataclass(o):
        return _jsonable(asdict(o))
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, set):
        return sorted(_jsonable(x) for x in o)
    if isinstance(o, Path):
        return str(o)
    return o


def _structured_summary(refs: list) -> list[dict]:
    out: list[dict] = []
    for r in refs:
        key = r.formula_id or r.item_code or "?"
        out.append({
            "type": r.result_type,
            "key": key,
            "formula_version": r.formula_version,
            "period": r.period,
            "value": r.display_value or r.raw_value,
            "unit": r.unit,
        })
    return out


def _claim_evidence_summary(claim, answer, state) -> list[dict]:
    """claim 的 evidence 引用 → 已 inspect 的正文摘要（供报告，不影响判分）。"""
    if answer is None:
        return []
    inspected = state.inspected_evidence or {}
    rows: list[dict] = []
    for idx in claim.citation_refs:
        if not (0 <= idx < len(answer.citations)):
            continue
        cit = answer.citations[idx]
        if cit.ref_type != "evidence" or not cit.evidence_id:
            continue
        m = inspected.get(cit.evidence_id)
        page = cit.page_number
        if m is not None:
            page = m.page_number if m.page_number is not None else page
            rows.append({
                "evidence_id": cit.evidence_id,
                "page_number": page,
                "source_name": m.source_name,
                "report_period": m.report_period,
                "is_snippet": m.is_snippet,
                "text": (m.text[:400] if m.text else None),
            })
        else:
            rows.append({"evidence_id": cit.evidence_id, "page_number": page,
                         "source_name": "", "report_period": None,
                         "is_snippet": True, "text": None})
    return rows


def _record_case(case, route_result: RS.RouterResult, outcome: H.ResearchOutcome,
                 evidence_pages: set[tuple[str, int]]) -> dict:
    st = outcome.state
    u = st.usage
    route = route_result.decision.route if route_result.decision else None
    answer = outcome.answer
    entailment_by_claim = {v.claim_id: v for v in st.entailment_verdicts}
    return {
        "case_id": case.case_id,
        "question": case.question,
        "section_id": case.section_id,
        "priority": case.priority,
        "expected_route_v2": case.expected_route_v2,
        "route": {
            "route": route,
            "reason_code": route_result.decision.reason_code if route_result.decision else None,
            "decided_by": route_result.decision.decided_by if route_result.decision else None,
            "router_status": route_result.status,
        },
        "completion": {
            "actual_status": classify_completion(outcome, route),
            "completion_status": outcome.completion_status,
            "stop_reason": outcome.stop_reason,
            "success": outcome.success,
        },
        "tool_calls": [
            {
                "tool": rec.result.tool_name,
                "status": rec.result.status,
                "error_code": rec.result.error_code,
                "elapsed_ms": rec.elapsed_ms,
                "auto": rec.auto,
            }
            for rec in st.tool_history
        ],
        "evidence_ids": list(st.evidence_ids),
        "structured_refs": _structured_summary(st.structured_refs),
        "external_snapshot_ids": list(st.external_snapshot_ids),
        "answer": {
            "answer_text": answer.answer_text if answer else None,
            "claims": [
                {"claim_id": c.claim_id, "text": c.text, "kind": c.kind,
                 "citation_refs": c.citation_refs,
                 "entailment": _jsonable(entailment_by_claim.get(c.claim_id)),
                 "evidence_summary": _claim_evidence_summary(c, answer, st)}
                for c in (answer.claims if answer else [])
            ],
            "citations": [
                {"ref_type": c.ref_type, "evidence_id": c.evidence_id,
                 "snapshot_id": c.snapshot_id, "item_code": c.item_code,
                 "formula_id": c.formula_id, "formula_version": c.formula_version,
                 "period": c.period, "source_snapshot_id": c.source_snapshot_id,
                 "page_number": c.page_number}
                for c in (answer.citations if answer else [])
            ],
            "unresolved_items": list(st.unresolved_items),
            "confidence": answer.confidence if answer else None,
        },
        "usage": {
            "llm_calls": u.llm_calls,
            "tool_calls": u.tool_calls,
            "local_searches": u.local_searches,
            "external_searches": u.external_searches,
            "fetches": u.fetches,
            "snapshots": u.snapshots,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "usage_unknown_calls": u.usage_unknown_calls,
            "elapsed_ms": u.elapsed_ms,
            # §二：LLM 分类记账（action/answer/entailment）+ latency 之和。
            "llm_latency_ms": u.llm_latency_ms,
            "llm_by_category": _jsonable(u.llm_by_category),
        },
        "page_diagnosis": _diagnose_local_pages(case, evidence_pages),
        # 修订①③④：契约优先 aspects + 批量 entailment + 去重审计（读自 state，不影响判分）。
        "required_aspects": _jsonable(st.required_aspects),
        "aspect_source": st.aspect_source,
        "aspects": HS.aspect_answers(st, answer),
        "uncovered_aspects": HS.uncovered_aspects(st, answer),
        "entailment_verdicts": _jsonable(st.entailment_verdicts),
        "entailment_evaluator_failed": st.entailment_evaluator_failed,
        "unsupported_claims": _jsonable(st.unsupported_claims),
        "rejected_duplicate_actions": _jsonable(st.rejected_duplicate_actions),
        # §三：结构化子 need（原始财务问题接入 Financial Snapshot，父路由不变）。
        "structured_subneeds": _jsonable(st.structured_subneeds),
        "semantic_mismatches_rejected": _jsonable(st.semantic_mismatches_rejected),
        "db_vs_evidence_aspects": SN.classify_aspects(
            st.required_aspects, st.structured_subneeds,
            st.semantic_mismatches_rejected),
    }


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def _pct(values: list[int]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0}
    s = sorted(values)
    return {
        "avg": sum(s) / len(s),
        "p50": s[len(s) // 2],
        "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
    }


def _aggregate(records: list[dict]) -> dict:
    n = len(records)
    route_dist: dict[str, int] = {}
    completion_dist: dict[str, int] = {s: 0 for s in ACTUAL_STATUSES}
    p0_completion: dict[str, int] = {s: 0 for s in ACTUAL_STATUSES}
    stop_reason_dist: dict[str, int] = {}
    n_evidence = n_structured = n_external = 0
    n_tool_error = 0
    n_tool_empty = 0
    budget_stops = 0
    invalid_loops = 0
    latencies: list[int] = []
    tokens: list[int] = []
    n_unknown_usage = 0
    page_coverages: list[float] = []
    page_hits = 0
    n_page_applicable = 0
    # §三：结构化子 need 统计（父路由 DB 数 / 子 need 数 / 成功 / 语义不匹配）。
    n_parent_db_route = 0
    n_subneeds = 0
    n_subneeds_resolved = 0
    n_subneeds_unavailable = 0
    n_subneeds_not_db = 0
    n_semantic_mismatch = 0
    n_cases_structured = 0

    for r in records:
        route = r["route"]["route"] or "UNDECIDED"
        route_dist[route] = route_dist.get(route, 0) + 1

        st = r["completion"]["actual_status"]
        completion_dist[st] += 1
        if r["priority"] == "P0":
            p0_completion[st] += 1

        sr = r["completion"]["stop_reason"] or "NONE"
        stop_reason_dist[sr] = stop_reason_dist.get(sr, 0) + 1
        if sr in ("BUDGET_ITERATIONS", "BUDGET_TOOL_CALLS", "BUDGET_TOKENS",
                  "BUDGET_ELAPSED", "BUDGET_EXTERNAL", "BUDGET_EXHAUSTED"):
            budget_stops += 1
        if sr == "CONSECUTIVE_NO_NEW_EVIDENCE":
            invalid_loops += 1

        if r["evidence_ids"]:
            n_evidence += 1
        if r["structured_refs"]:
            n_structured += 1
        if r["external_snapshot_ids"]:
            n_external += 1

        for tc in r["tool_calls"]:
            if tc["status"] == "FATAL_ERROR":
                n_tool_error += 1
            if tc["status"] == "EMPTY":
                n_tool_empty += 1

        u = r["usage"]
        latencies.append(u["elapsed_ms"])
        if u["usage_unknown_calls"] == 0:
            tokens.append(u["input_tokens"] + u["output_tokens"])
        else:
            n_unknown_usage += 1

        pd = r["page_diagnosis"]
        if pd.get("applicable"):
            n_page_applicable += 1
            page_coverages.append(pd.get("required_page_coverage", 0.0))
            if pd.get("page_hit"):
                page_hits += 1

        # §三：结构化子 need 统计。
        if r["route"]["route"] == "DB_LOOKUP":
            n_parent_db_route += 1
        sns = r.get("structured_subneeds") or []
        n_subneeds += len(sns)
        for sn in sns:
            if sn.get("status") == "RESOLVED":
                n_subneeds_resolved += 1
            elif sn.get("status") == "UNAVAILABLE":
                n_subneeds_unavailable += 1
            elif sn.get("status") == "NOT_DB_ROUTED":
                n_subneeds_not_db += 1
        n_semantic_mismatch += len(r.get("semantic_mismatches_rejected") or [])
        if sns:
            n_cases_structured += 1

    return {
        "n_cases": n,
        "route_distribution": route_dist,
        "completion_distribution": completion_dist,
        "p0_completion_distribution": p0_completion,
        "stop_reason_distribution": stop_reason_dist,
        "source_coverage": {
            "n_with_evidence": n_evidence,
            "n_with_structured": n_structured,
            "n_with_external": n_external,
        },
        "tool_errors": n_tool_error,
        "tool_empty": n_tool_empty,
        "budget_stops": budget_stops,
        "invalid_loops": invalid_loops,
        "latency_ms": _pct(latencies),
        "tokens": _pct(tokens),
        "usage_unknown_calls": n_unknown_usage,
        "page_diagnosis": {
            "n_applicable": n_page_applicable,
            "macro_required_page_coverage": (
                sum(page_coverages) / len(page_coverages) if page_coverages else None),
            "page_hit": page_hits,
        },
        "external_cost_available": False,
        "structured_subneeds": {
            "parent_db_routes": n_parent_db_route,
            "n_subneeds": n_subneeds,
            "n_resolved": n_subneeds_resolved,
            "n_unavailable": n_subneeds_unavailable,
            "n_not_db_routed": n_subneeds_not_db,
            "n_semantic_mismatches_rejected": n_semantic_mismatch,
            "n_cases_with_structured": n_cases_structured,
        },
    }


# ---------------------------------------------------------------------------
# 产物输出
# ---------------------------------------------------------------------------

def _write_artifacts(run_id: str, output_root: str, records: list[dict],
                     metrics: dict, meta: dict) -> Path:
    output_root = Path(output_root)
    final_dir = output_root / run_id
    if final_dir.exists():
        raise FileExistsError(f"run 产物已存在，拒绝覆盖: {final_dir}")
    tmp_dir = output_root / f".{run_id}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = tmp_dir / "inputs"
    inputs_dir.mkdir()

    import shutil
    import os as _os

    try:
        (tmp_dir / "run_manifest.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        with (tmp_dir / "case_results.jsonl").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(_jsonable(r), ensure_ascii=False) + "\n")

        (tmp_dir / "metrics.json").write_text(
            json.dumps(_jsonable(metrics), ensure_ascii=False, indent=2), encoding="utf-8")

        lines = _report_lines(run_id, meta, metrics, records)
        (tmp_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        # inputs/ 快照（仅数据集副本 + git 状态）
        dataset_path = meta.get("dataset_path")
        if dataset_path and Path(dataset_path).exists():
            shutil.copy2(dataset_path, inputs_dir / "v1_baseline.jsonl")

        # trace_inventory.json（logs/harness/<run_id> 下的 trace 文件清单）
        trace_dir = Path("logs/harness") / run_id
        trace_files = sorted(p.name for p in trace_dir.glob("*.jsonl")) if trace_dir.exists() else []
        (tmp_dir / "trace_inventory.json").write_text(
            json.dumps({"run_id": run_id, "trace_dir": str(trace_dir),
                        "n_trace_files": len(trace_files),
                        "files": trace_files}, ensure_ascii=False, indent=2),
            encoding="utf-8")

        _os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    logger.info("Wrote actual-path artifacts to %s", final_dir)
    return final_dir


def _report_lines(run_id: str, meta: dict, metrics: dict, records: list[dict]) -> list[str]:
    m = metrics
    lines = [
        f"# Phase 3 Actual-Path — 41 问（{meta.get('company_id')}）",
        "",
        f"- run_id: `{run_id}`",
        f"- model: `{meta.get('model')}`",
        f"- 数据: {m['n_cases']} 题",
        "",
        "## 完成状态（§7.7 确定性最低规则，非 LLM 自评）",
        "",
        "| 状态 | 数量 |",
        "|---|---|",
    ]
    for s in ACTUAL_STATUSES:
        lines.append(f"| {s} | {m['completion_distribution'].get(s, 0)} |")
    lines += ["", "## 路由分布（实际）", "", "| route | 数量 |", "|---|---|"]
    for route, cnt in sorted(m["route_distribution"].items()):
        lines.append(f"| {route} | {cnt} |")
    lines += ["", "## 来源覆盖", "",
              f"- Evidence: {m['source_coverage']['n_with_evidence']} 题",
              f"- Structured: {m['source_coverage']['n_with_structured']} 题",
              f"- External: {m['source_coverage']['n_with_external']} 题",
              "",
              "## 结构化子 need（§三 原始财务问题接入 Financial Snapshot）",
              "",
              f"- 父路由 DB_LOOKUP 题数: {m['structured_subneeds']['parent_db_routes']}",
              f"- 结构化子 need 数: {m['structured_subneeds']['n_subneeds']}",
              f"- 子 need RESOLVED（结构化结果命中）: {m['structured_subneeds']['n_resolved']}",
              f"- 子 need UNAVAILABLE（快照缺失）: {m['structured_subneeds']['n_unavailable']}",
              f"- 子 need NOT_DB_ROUTED（子表达未被路由到 DB）: {m['structured_subneeds']['n_not_db_routed']}",
              f"- 语义不匹配拒绝（数值方面无法精确表达）: {m['structured_subneeds']['n_semantic_mismatches_rejected']}",
              f"- 含结构化子 need 的题数: {m['structured_subneeds']['n_cases_with_structured']}",
              "",
              "## 耗时 / token",
              "",
              f"- 耗时 avg/p50/p95: {m['latency_ms']['avg']:.0f}/{m['latency_ms']['p50']:.0f}/{m['latency_ms']['p95']:.0f} ms",
              f"- token avg/p50/p95: {m['tokens']['avg']:.0f}/{m['tokens']['p50']:.0f}/{m['tokens']['p95']:.0f}",
              f"- usage 未知调用题数: {m['usage_unknown_calls']}",
              "",
              "## 诊断",
              "",
              f"- 工具致命错误: {m['tool_errors']} 次",
              f"- 工具空结果: {m['tool_empty']} 次",
              f"- 预算停止: {m['budget_stops']} 题",
              f"- 无效循环（连续无新证据）: {m['invalid_loops']} 题",
              f"- 外部成本可获取: {m['external_cost_available']}",
              "",
              "### 本地证据页级诊断（gold 仅离线比对，不并入主判据）",
              "",
              f"- 适用题数: {m['page_diagnosis']['n_applicable']}",
              f"- Macro RequiredPageCoverage: "
              f"{m['page_diagnosis']['macro_required_page_coverage']}",
              f"- PageHit 题数: {m['page_diagnosis']['page_hit']}",
              "",
              "## 逐题摘要",
              "",
              "| case | route | 实际状态 | stop_reason | 工具数 | 证据 | 结构化 | 外部 | 子need(命中/总) |",
              "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        c = r["completion"]
        sns = r.get("structured_subneeds") or []
        sn_hit = sum(1 for sn in sns if sn.get("status") == "RESOLVED")
        lines.append(
            f"| {r['case_id']} | {r['route']['route'] or 'UNDECIDED'} | "
            f"{c['actual_status']} | {c['stop_reason'] or '-'} | "
            f"{len(r['tool_calls'])} | {len(r['evidence_ids'])} | "
            f"{len(r['structured_refs'])} | {len(r['external_snapshot_ids'])} | "
            f"{sn_hit}/{len(sns)} |")
    return lines


# ---------------------------------------------------------------------------
# 主运行
# ---------------------------------------------------------------------------

def run_actual_path_41(
    *,
    dataset_path: str,
    company_id: str,
    output_root: str,
    ev_db_path: str = "data/evidence.db",
    fin_db_path: str = "data/financial_v2.db",
    harness_db_path: str = "data/harness.db",
    audit_dir: str = "logs/tools",
    model: str | None = None,
    resume_run_id: str | None = None,
    run_id: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
    validate_only: bool = False,
) -> dict:
    from tools import adapters

    import config

    # 路由上下文 / 本地工具 / 页诊断均依赖 Evidence + Financial 两个 Store。
    from evidence import store as estore
    from financial_v2 import store as fstore

    C.init_db(harness_db_path)
    estore.init_db(ev_db_path)
    fstore.init_db(fin_db_path)

    cases = load_dataset(dataset_path)
    if case_id:
        cases = [c for c in cases if c.case_id == case_id]
        if not cases:
            raise ValueError(f"未找到 case_id: {case_id}")
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        raise ValueError("没有要运行的 case")

    # 冻结输入指纹（先建 context，report_as_of 进入 RunManifest）。
    context = routing_context.build_route_context(company_id)
    report_as_of = context.report_as_of
    model_name = model or config.LLM_MODEL
    budget_dict = P.DEFAULT_BUDGET.as_dict()

    # resume 处理：全量 RunManifest 兼容校验（fail-closed）。
    completed: set[str] = set()
    if resume_run_id:
        run_id = resume_run_id
        stored = C.get_run_manifest(resume_run_id, harness_db_path)
        if stored is None:
            raise ValueError(f"resume-run-id 不存在于 harness checkpoint: {resume_run_id}")
        new_manifest = _build_run_manifest(
            run_id=resume_run_id, dataset_path=dataset_path, company_id=company_id,
            report_as_of=report_as_of, model=model_name, budget=budget_dict,
            context=context, ev_db_path=ev_db_path, fin_db_path=fin_db_path)
        mismatches = manifest_mismatches(stored, new_manifest)
        if mismatches:
            raise ValueError(
                f"resume 拒绝（fail-closed）：RunManifest 字段不一致: {mismatches}")
        completed = C.list_completed_questions(resume_run_id, harness_db_path)
    else:
        run_id = run_id or (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8])
        manifest = _build_run_manifest(
            run_id=run_id, dataset_path=dataset_path, company_id=company_id,
            report_as_of=report_as_of, model=model_name, budget=budget_dict,
            context=context, ev_db_path=ev_db_path, fin_db_path=fin_db_path)
        C.write_run_manifest(manifest, harness_db_path)

    meta = {
        "runner": "run_actual_path_41",
        "run_id": run_id,
        "company_id": company_id,
        "dataset_path": dataset_path,
        "dataset_sha256": _sha256_file(dataset_path),
        "report_as_of": report_as_of,
        "model": model_name,
        "budget": budget_dict,
        "contract_version": H.HARNESS_VERSION,
        "router_fingerprint": RS.RULE_VERSION,
        "external_policy_version": EXTERNAL_POLICY_VERSION,
        "harness_fingerprint": _harness_fingerprint(),
        "resume_run_id": resume_run_id,
        "generated_at": _now_iso(),
        "git": _git_state(),
    }

    # validate-only：只路由不跑工具/LLM。
    if validate_only:
        route_dist: dict[str, int] = {}
        for case in cases:
            need = _build_need(case)
            try:
                rr = router_mod.route(need, context)
                r = rr.decision.route if rr.decision else ("FALLBACK:" + (rr.error_code or "?"))
            except Exception as e:
                r = f"FAILED:ROUTER_VALIDATION({type(e).__name__})"
            route_dist[r] = route_dist.get(r, 0) + 1
        return {
            "run_id": run_id,
            "validate_only": True,
            "n_cases": len(cases),
            "report_as_of": report_as_of,
            "route_distribution": route_dist,
            "manifest": meta,
        }

    registry = adapters.build_default_registry(audit_dir=audit_dir)
    llm = RT.RealResearchLLM(model=model_name)

    records: list[dict] = []
    for case in cases:
        need = _build_need(case)
        route_error = None
        try:
            route_result = router_mod.route(need, context)
        except Exception as e:
            # 路由契约校验失败（如无快照却解析出 DB 目标）→ 诚实标记 FAILED，不 crash。
            route_error = f"{type(e).__name__}: {e}"
            route_result = RS.RouterResult(
                status="FAILED", decision=None, error_code="ROUTER_FAILED",
                trace_id=uuid.uuid4().hex)
        if case.case_id in completed:
            # resume 跳过：从 checkpoint 读回 outcome，重建记录。
            raw = C.load_outcome(run_id, case.case_id, harness_db_path)
            if raw is None:
                raise ValueError(f"resume 跳过题 {case.case_id} 但 checkpoint 无 outcome")
            # 用 state 里的 evidence_ids 重建页诊断。
            evidence_pages = _resolve_evidence_pages(
                raw.get("state", {}).get("evidence_ids", []), ev_db_path)
            records.append(_record_case(
                case, route_result, _outcome_from_raw(raw, need, company_id),
                evidence_pages))
            continue

        outcome = RT.run_question(
            need=need, route_result=route_result, registry=registry, llm=llm,
            budget=P.DEFAULT_BUDGET, run_id=run_id, case_id=case.case_id,
            company_id=company_id, section_id=case.section_id, trace_enabled=True,
            context=context)
        C.write_question_outcome(run_id, outcome, harness_db_path)
        evidence_pages = _resolve_evidence_pages(outcome.state.evidence_ids, ev_db_path)
        rec = _record_case(case, route_result, outcome, evidence_pages)
        if route_error is not None:
            rec["route"]["route_error"] = route_error
        records.append(rec)

    metrics = _aggregate(records)
    out_dir = _write_artifacts(run_id, output_root, records, metrics, meta)
    return {"run_id": run_id, "validate_only": False, "n_cases": len(records),
            "metrics": metrics, "output_dir": str(out_dir)}


def _outcome_from_raw(raw: dict, need: RS.InformationNeed,
                      company_id: str) -> H.ResearchOutcome:
    """把 checkpoint 里 asdict 化的 outcome 还原为对象（resume 跳过题用）。"""
    st_data = raw["state"]
    route_result = None
    if st_data.get("route_result"):
        rr = st_data["route_result"]
        if rr.get("decision"):
            d = rr["decision"]
            budget = RS.RetrievalBudget(**d["budget"])
            decision = RS.RouteDecision(
                need_id=d["need_id"], route=d["route"], reason_code=d["reason_code"],
                filters=d["filters"], budget=budget, fallback_routes=d["fallback_routes"],
                decided_by=d["decided_by"], rule_version=d["rule_version"],
                confidence=d["confidence"])
            route_result = RS.RouterResult(
                status=rr["status"], decision=decision, error_code=rr["error_code"],
                trace_id=rr["trace_id"], reason_code=rr.get("reason_code"))
    state = H.ResearchState(
        run_id=st_data["run_id"], case_id=st_data["case_id"],
        question_id=st_data["question_id"], company_id=st_data["company_id"],
        section_id=st_data["section_id"], original_question=st_data["original_question"],
        need=need)
    state.status = st_data["status"]
    state.route_result = route_result
    state.evidence_ids = st_data["evidence_ids"]
    state.external_snapshot_ids = st_data["external_snapshot_ids"]
    state.unresolved_items = st_data["unresolved_items"]
    # structured_refs 还原为轻量对象（仅摘要需 item_code/formula_id/period/value）。
    state.structured_refs = _refs_from_raw(st_data.get("structured_refs", []))
    # §三：结构化子 need 记录（resume 跳过题仅保证可回读，不影响判分）。
    state.structured_subneeds = st_data.get("structured_subneeds", [])
    state.semantic_mismatches_rejected = st_data.get("semantic_mismatches_rejected", [])
    answer = None
    if raw.get("answer"):
        a = raw["answer"]
        answer = H.ResearchAnswer(
            question_id=a["question_id"], answer_text=a["answer_text"],
            claims=[H.Claim(**c) for c in a["claims"]],
            citations=[H.CitationRef(**c) for c in a["citations"]],
            unresolved_items=a["unresolved_items"], confidence=a["confidence"],
            completion_status=a["completion_status"])
    return H.ResearchOutcome(state=state, answer=answer, success=raw["success"],
                             completion_status=raw["completion_status"],
                             stop_reason=raw["stop_reason"])


def _refs_from_raw(refs: list) -> list[RS.StructuredResultRef]:
    out: list[RS.StructuredResultRef] = []
    for r in refs:
        out.append(RS.StructuredResultRef(
            result_type=r.get("result_type", "financial_field"),
            snapshot_id=r.get("snapshot_id", ""), item_code=r.get("item_code"),
            formula_id=r.get("formula_id"), formula_version=r.get("formula_version"),
            period=r.get("period", ""), raw_value=r.get("raw_value"),
            display_value=r.get("display_value"), unit=r.get("unit"),
            status=r.get("status", ""), reason_code=r.get("reason_code"),
            input_record_refs=r.get("input_record_refs", []),
            input_snapshot_item_refs=r.get("input_snapshot_item_refs", [])))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]):
    import argparse
    p = argparse.ArgumentParser(description="Phase 3 Batch B 41 问 Actual-Path Runner")
    p.add_argument("--dataset", required=True)
    p.add_argument("--company", required=True, dest="company_id")
    p.add_argument("--output", default="evaluation/results/actual_path_41",
                   dest="output_root")
    p.add_argument("--ev-db", default="data/evidence.db")
    p.add_argument("--fin-db", default="data/financial_v2.db")
    p.add_argument("--harness-db", default="data/harness.db")
    p.add_argument("--audit-dir", default="logs/tools")
    p.add_argument("--model", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--resume-run-id", default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--validate-only", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        result = run_actual_path_41(
            dataset_path=args.dataset, company_id=args.company_id,
            output_root=args.output_root, ev_db_path=args.ev_db,
            fin_db_path=args.fin_db, harness_db_path=args.harness_db,
            audit_dir=args.audit_dir, model=args.model,
            resume_run_id=args.resume_run_id, run_id=args.run_id,
            case_id=args.case_id, limit=args.limit,
            validate_only=args.validate_only)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if result["validate_only"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps({
        "run_id": result["run_id"],
        "n_cases": result["n_cases"],
        "output_dir": result["output_dir"],
        "completion_distribution": result["metrics"]["completion_distribution"],
        "route_distribution": result["metrics"]["route_distribution"],
        "source_coverage": result["metrics"]["source_coverage"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
