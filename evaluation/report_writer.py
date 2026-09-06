"""原子输出运行产物与 Markdown 报告。

- 写入临时目录（与最终目录同文件系统），全部成功后 os.replace 原子改名。
- 五类必需产物：run_manifest.json / case_results.jsonl / metrics.json /
  data_quality.json / report.md，另将 dataset、manifest、库存快照复制到 inputs/。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path

from evaluation.schema import BaselineRunResult

logger = logging.getLogger(__name__)


def _jsonable(o):
    """把 dataclass/set/tuple 转为可 JSON 序列化的结构。"""
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


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _build_case_records(
    run_result: BaselineRunResult,
    cases,
    resolved_by_id: dict,
) -> list[dict]:
    metric_by_id = {m.case_id: m for m in run_result.case_metrics}
    failure_by_id = {f.case_id: f for f in run_result.failures}

    records: list[dict] = []
    for case in cases:
        cr = next((r for r in run_result.case_results if r.case_id == case.case_id), None)
        m = metric_by_id.get(case.case_id)
        f = failure_by_id.get(case.case_id)
        resolved = resolved_by_id.get(case.case_id)

        targets = []
        if resolved is not None:
            for g in resolved.groups:
                for t in g.targets:
                    targets.append({
                        "group_id": g.group_id,
                        "channel": g.channel,
                        "document_id": t.document_id,
                        "pdf_page": t.pdf_page,
                        "mapping_status": t.mapping_status,
                        "source_note": t.source_note,
                        "mapping_note": t.mapping_note,
                    })

        rec: dict = {
            "case_id": case.case_id,
            "question": case.question,
            "section_id": case.section_id,
            "expected_route_raw": case.expected_route_raw,
            "expected_route_v2": case.expected_route_v2,
            "priority": case.priority,
            "eligibility": cr.eligibility.status if cr else "UNKNOWN",
            "eligibility_reason": cr.eligibility.reason if cr else "",
            "gold_evidence_raw": case.gold_evidence_raw,
            "gold_targets": targets,
        }

        if cr is not None:
            rec["retrieved"] = [
                {
                    "rank": i + 1,
                    "source_file": c.source_file,
                    "page_number": c.page_number,
                    "chunk_index": c.chunk_index,
                    "score": c.score,
                    "source_type": c.source_type,
                    "section_title": c.section_title,
                    "text": c.text_preview,
                }
                for i, c in enumerate(cr.retrieved)
            ]
            rec["latency_ms"] = cr.latency_ms
            rec["error"] = cr.error
            rec["call_id"] = cr.call_id
            rec["audit_path"] = cr.audit_path
            rec["legacy_log"] = cr.legacy_log
        else:
            rec["retrieved"] = []
            rec["latency_ms"] = None
            rec["error"] = "NOT_RUN"

        if m is not None:
            rec["metrics"] = {
                "page_hit": {str(K): v for K, v in m.page_hit.items()},
                "all_group_hit": {str(K): v for K, v in m.all_group_hit.items()},
                "rr": m.rr,
                "adjacent_hit": {str(K): v for K, v in m.adjacent_hit.items()},
                "adjacent_only": {str(K): v for K, v in m.adjacent_only.items()},
                "gold_page_result_precision": {str(K): v for K, v in m.gold_page_result_precision.items()},
                "n_unique_required_pages": m.n_unique_required_pages,
                "required_page_count": m.required_page_count,
                "hit_required_page_count": {str(K): v for K, v in m.hit_required_page_count.items()},
                "missing_required_pages": {str(K): v for K, v in m.missing_required_pages.items()},
                "required_page_coverage": {str(K): v for K, v in m.required_page_coverage.items()},
                "recall_status": {str(K): v for K, v in m.recall_status.items()},
            }

        if f is not None:
            rec["failure"] = {
                "primary": f.primary,
                "auxiliary": f.auxiliary,
                "reason": f.reason,
            }

        records.append(rec)
    return records


def _build_data_quality(
    run_result: BaselineRunResult,
    resolved_by_id: dict,
) -> dict:
    v = run_result.validation
    cs = run_result.corpus_state
    dq: dict = {
        "validation": _jsonable(v) if v is not None else None,
        "corpus": {
            "collection_name": cs.collection_name if cs else "",
            "exists": cs.exists if cs else False,
            "chunk_count": cs.chunk_count if cs else 0,
            "fingerprint": cs.fingerprint if cs else "",
            "documents_indexed": _jsonable(cs.documents_indexed) if cs else {},
            "warnings": cs.warnings if cs else [],
        },
        "eligibility_breakdown": _jsonable(run_result.aggregate.exclusion_breakdown) if run_result.aggregate else {},
        "mapping_issues": [],
    }
    if resolved_by_id:
        for case_id, resolved in resolved_by_id.items():
            for g in resolved.groups:
                for t in g.targets:
                    if t.mapping_status != "verified":
                        dq["mapping_issues"].append({
                            "case_id": case_id,
                            "group_id": g.group_id,
                            "document_id": t.document_id,
                            "pdf_page": t.pdf_page,
                            "source_note": t.source_note,
                            "mapping_note": t.mapping_note,
                        })
    return dq


def _build_report_md(run_result: BaselineRunResult) -> str:
    a = run_result.aggregate
    ks = run_result.ks
    lines: list[str] = []
    lines.append(f"# V1 Retrieval Baseline — {run_result.company_id} ({run_result.collection})")
    lines.append("")
    lines.append(f"- run_id: `{run_result.run_id}`")
    lines.append(f"- 状态: `{run_result.status}`")
    lines.append(f"- 数据: {a.n_total} 题（eligible {a.n_eligible} / 排除 {a.n_total - a.n_eligible}）")
    lines.append("")

    lines.append("## 首屏关键指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| Macro **RequiredPageCoverage@10**（总体主分） | **{_fmt_pct(a.required_page_coverage.get(10))}** |")
    lines.append(f"| P0 **RequiredPageCoverage@10** | **{_fmt_pct(a.p0_required_page_coverage10)}** |")
    lines.append(f"| **PageHit@10**（至少命中一页） | **{_fmt_pct(a.page_hit.get(10))}** |")
    lines.append(f"| **AllGroupHit@10**（完整证据覆盖） | **{_fmt_pct(a.all_group_hit.get(10))}** |")
    lines.append(f"| MRR@10 | {a.mrr10:.3f} |")
    lines.append(f"| eligible 分母 | {a.n_eligible} |")
    lines.append(f"| 排除分母 | {a.n_total - a.n_eligible} |")
    lines.append("")

    lines.append("### K 维命中")
    lines.append("")
    lines.append("| K | PageHit@K | AllGroupHit@K | RequiredPageCoverage@K | AdjacentPageHit@K | GoldPageResultPrecision@K |")
    lines.append("|---|---|---|---|---|---|")
    for K in ks:
        lines.append(
            f"| {K} | {_fmt_pct(a.page_hit.get(K))} | {_fmt_pct(a.all_group_hit.get(K))} "
            f"| {_fmt_pct(a.required_page_coverage.get(K))} "
            f"| {_fmt_pct(a.adjacent_page_hit.get(K))} | {_fmt_pct(a.gold_page_result_precision.get(K))} |"
        )
    lines.append("")

    lines.append("### recall 状态分布（eligible 题）")
    lines.append("")
    lines.append("| K | ZERO_RECALL | PARTIAL_RECALL | FULL_RECALL |")
    lines.append("|---|---|---|---|")
    for K in ks:
        rs = a.recall_status.get(K, {})
        lines.append(f"| {K} | {rs.get('ZERO_RECALL', 0)} | {rs.get('PARTIAL_RECALL', 0)} | {rs.get('FULL_RECALL', 0)} |")
    lines.append("")

    lines.append("### multi_page 切片（≥2 唯一必需本地页，n=" + str(a.multi_page_n) + "）")
    lines.append("")
    if a.multi_page_n:
        lines.append("| K | PageHit@K | AllGroupHit@K | RequiredPageCoverage@K |")
        lines.append("|---|---|---|---|")
        for K in ks:
            lines.append(f"| {K} | {_fmt_pct(a.multi_page_page_hit.get(K))} | {_fmt_pct(a.multi_page_all_group_hit.get(K))} | {_fmt_pct(a.multi_page_required_page_coverage.get(K))} |")
    else:
        lines.append("（无 multi_page 题）")
    lines.append("")

    lines.append("### 分组")
    lines.append("")
    for label, groups in [
        ("按 section_id", a.by_section),
        ("按 expected_route_raw", a.by_route_raw),
        ("按 expected_route_v2", a.by_route_v2),
        ("按 priority", a.by_priority),
    ]:
        if not groups:
            continue
        lines.append(f"**{label}**")
        lines.append("")
        lines.append("| 分组 | n | PageHit@10 | AllGroupHit@10 | RequiredPageCoverage@10 |")
        lines.append("|---|---|---|---|---|")
        for key in sorted(groups):
            g = groups[key]
            lines.append(
                f"| {key} | {g['n']} | {_fmt_pct(g['page_hit'].get(10))} | {_fmt_pct(g['all_group_hit'].get(10))} | {_fmt_pct(g['required_page_coverage'].get(10))} |"
            )
        lines.append("")

    lines.append("### 预算限制")
    lines.append("")
    lines.append("必需唯一页数 > K 的题数（AllGroupHit@K 无法达成 1）：")
    for K in ks:
        lines.append(f"- 必需页数 > {K}：{a.n_required_pages_gt_k.get(K, 0)} 题")
    lines.append("")

    lines.append("### 失败分布")
    lines.append("")
    if run_result.failures:
        from collections import Counter
        c = Counter(f.primary for f in run_result.failures)
        lines.append("| 主失败原因 | 题数 |")
        lines.append("|---|---|")
        for reason, cnt in c.most_common():
            lines.append(f"| {reason} | {cnt} |")
    else:
        lines.append("（无失败题）")
    lines.append("")

    lines.append("### 失败题清单")
    lines.append("")
    metric_by_id = {m.case_id: m for m in run_result.case_metrics}
    for f in run_result.failures:
        m = metric_by_id.get(f.case_id)
        rs = m.recall_status.get(10) if m and m.eligible else None
        rs_tag = f" recall={rs}" if rs else ""
        lines.append(f"- **{f.case_id}** — `{f.primary}`{rs_tag} {f.auxiliary or ''} — {f.reason}")

    lines.append("")
    lines.append("> PageHit 仅表示“召回至少一页”，不代表证据完整；完整性由 AllGroupHit 验收。")
    lines.append("> RequiredPageCoverage@K 是部分召回覆盖率（唯一必需页命中占比，Macro 题等权），是诊断补充，不替代 AllGroupHit。")
    lines.append("> GoldPageResultPrecision@K 是诊断代理，不是语义层 Context Precision。")
    return "\n".join(lines) + "\n"


def write_run_artifacts(
    run_result: BaselineRunResult,
    output_root: str | Path,
    cases=None,
    resolved_by_id: dict | None = None,
) -> Path:
    """写五类产物 + inputs/ 快照，原子改名。返回最终目录路径。"""
    output_root = Path(output_root)
    final_dir = output_root / run_result.run_id
    tmp_dir = output_root / f".{run_result.run_id}.tmp"

    if final_dir.exists():
        raise FileExistsError(f"run_id 已存在，拒绝覆盖: {final_dir}")

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = tmp_dir / "inputs"
    inputs_dir.mkdir()

    try:
        cases = cases or []
        resolved_by_id = resolved_by_id or {}

        # 1. run_manifest.json
        manifest_payload = dict(run_result.metadata)
        manifest_payload.update({
            "run_id": run_result.run_id,
            "company_id": run_result.company_id,
            "collection": run_result.collection,
            "ks": run_result.ks,
            "status": run_result.status,
            "completed": run_result.completed,
            "manifest_snapshot": _jsonable(run_result.manifest_snapshot),
        })
        (tmp_dir / "run_manifest.json").write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 2. case_results.jsonl
        records = _build_case_records(run_result, cases, resolved_by_id)
        with (tmp_dir / "case_results.jsonl").open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 3. metrics.json
        (tmp_dir / "metrics.json").write_text(
            json.dumps(_jsonable(run_result.aggregate), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 4. data_quality.json
        dq = _build_data_quality(run_result, resolved_by_id)
        (tmp_dir / "data_quality.json").write_text(
            json.dumps(dq, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 5. report.md
        (tmp_dir / "report.md").write_text(_build_report_md(run_result), encoding="utf-8")

        # inputs/ 快照
        meta = run_result.metadata
        for src_key, dst_name in [
            ("dataset_path", "v1_baseline.jsonl"),
            ("corpus_manifest_path", "corpus_manifest.json"),
        ]:
            src = meta.get(src_key)
            if src and Path(src).exists():
                shutil.copy2(src, inputs_dir / dst_name)
        if run_result.corpus_state is not None:
            (inputs_dir / "corpus_inventory.json").write_text(
                json.dumps(_jsonable(run_result.corpus_state), ensure_ascii=False, indent=2), encoding="utf-8"
            )

        # 原子改名
        os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    logger.info("Wrote run artifacts to %s", final_dir)
    return final_dir
