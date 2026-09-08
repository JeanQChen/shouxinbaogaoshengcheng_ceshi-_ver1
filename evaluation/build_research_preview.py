"""Phase 3 Batch B 章节预览生成器（评测产物，非正式章节）。

消费 41 问 Actual-Path Runner 的产物目录（case_results.jsonl），对选定章节的若干
ResearchOutcome 做**确定性排序**，再通过一个明确标为 preview 的轻量 Prompt 形成可读
草稿。本模块严格遵守任务书 §7.8 / §5：

- 不重新检索、不重新计算财务数字；
- 不把「未取得 / 未检索到」改写为「不存在」；
- 不新增输入 Outcome 之外的事实和数字（由 prompt 约束 + 材料只含已取得内容）；
- 水印 `Phase 3 Research Preview — 未经章节质量门` 由本模块**确定性注入**，不依赖 LLM；
- 行内引用编号表 [R1]..[Rn] 由本模块**确定性生成并附加**，可展开回查 Evidence / External Snapshot；
- ResearchPreview.formal_section_passed 恒为 False（不冒充正式章节、不过质量门）。

CLI:
  python -m evaluation.build_research_preview \
    --actual-path-result evaluation/results/actual_path_41/<run_id> \
    --section company
  python -m evaluation.build_research_preview --actual-path-result <dir> --section company --no-llm
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from llm import client as llm_client

logger = logging.getLogger(__name__)

# 水印（任务书 §7.8 固定文案，确定性注入）。
WATERMARK = "Phase 3 Research Preview — 未经章节质量门"

# 预览 prompt 版本（与 llm/prompts/research_preview_v1.txt 对应）。
PREVIEW_PROMPT_VERSION = "research_preview_v1"

# 预览输出默认根目录。
DEFAULT_OUTPUT_ROOT = "evaluation/results/research_preview"


# ---------------------------------------------------------------------------
# 产物数据模型（任务书 §5 建议接口）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchPreview:
    """章节预览产物（评测产物，非正式 SectionResult）。"""

    preview_id: str
    section_id: str
    outcome_ids: list[str]
    markdown: str
    citations: list[dict]
    limitations: list[str]
    generated_at: str
    prompt_version: str
    model_version: str
    formal_section_passed: bool = False


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_template(template: str, vars: dict) -> str:
    """把 {{key}} 替换为 vars[key]；保留 JSON 花括号等其它字面量。"""
    def _sub(m: re.Match) -> str:
        return str(vars[m.group(1)])
    return re.sub(r"\{\{(\w+)\}\}", _sub, template)


def _citation_key(cit: dict) -> str:
    """引用条目 → 稳定去重键（排序字段 JSON）。"""
    return json.dumps(cit, sort_keys=True, ensure_ascii=False)


def _citation_display(cit: dict) -> str:
    """引用条目 → 人类可读描述（可展开回查）。"""
    rt = cit.get("ref_type")
    if rt == "evidence":
        page = cit.get("page_number")
        return "证据 " + (f"evidence_id={cit.get('evidence_id')} 页{page}"
                          if page is not None else f"evidence_id={cit.get('evidence_id')}")
    if rt == "external":
        return f"外部快照 source_snapshot_id={cit.get('source_snapshot_id')}"
    if rt == "structured":
        key = cit.get("formula_id") or cit.get("item_code") or "?"
        return f"结构化 {key} 期间={cit.get('period')}"
    return f"未知引用 {json.dumps(cit, ensure_ascii=False)}"


# ---------------------------------------------------------------------------
# 记录装载 / 选择 / 排序（确定性）
# ---------------------------------------------------------------------------

def _load_records(result_dir: str) -> list[dict]:
    """读取 actual-path 产物目录下的 case_results.jsonl。"""
    p = Path(result_dir)
    if not p.is_dir():
        raise FileNotFoundError(f"actual-path 产物目录不存在: {p}")
    f = p / "case_results.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"目录缺少 case_results.jsonl: {f}")
    records = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if not records:
        raise ValueError(f"case_results.jsonl 为空: {f}")
    return records


def _select_section(records: list[dict], section_id: str) -> list[dict]:
    """按 section_id 过滤；无匹配抛错（fail-closed，不空转生成）。"""
    sel = [r for r in records if r.get("section_id") == section_id]
    if not sel:
        raise ValueError(f"目录中无 section_id={section_id} 的记录")
    return sel


def _sort_key(record: dict) -> tuple:
    """确定性排序键：priority（P0 < P1 < P2）→ case_id 字典序。"""
    prio = {"P0": 0, "P1": 1, "P2": 2}
    return (prio.get(record.get("priority"), 99), str(record.get("case_id", "")))


def _sort_records(records: list[dict]) -> list[dict]:
    return sorted(records, key=_sort_key)


# ---------------------------------------------------------------------------
# 引用编号表（确定性，跨题去重）
# ---------------------------------------------------------------------------

def _build_citation_labels(records: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """把全部记录的 answer.citations 去重后按首次出现顺序编号为 R1..Rn。

    Returns:
        table: list[{"label": "R1", ...原引用字段..., "display": "..."}]
        labels: key → "R1"（供 claims 的 citation_refs 下标映射到全局编号）
    """
    labels: dict[str, str] = {}
    table: list[dict] = []
    for rec in records:
        ans = rec.get("answer") or {}
        for cit in ans.get("citations", []):
            key = _citation_key(cit)
            if key in labels:
                continue
            label = f"R{len(table) + 1}"
            labels[key] = label
            entry = dict(cit)
            entry["label"] = label
            entry["display"] = _citation_display(cit)
            table.append(entry)
    return table, labels


def _citations_text(table: list[dict]) -> str:
    if not table:
        return "（无）"
    return "\n".join(f"[{e['label']}] {e['display']}" for e in table)


# ---------------------------------------------------------------------------
# 材料序列化（只含已取得内容，无 gold、无新数字）
# ---------------------------------------------------------------------------

def _record_material(record: dict, labels: dict[str, str]) -> str:
    """把单条记录序列化为 prompt 可读文本；claim 的 citation_refs 下标 → 全局编号。"""
    case_id = record.get("case_id", "?")
    question = record.get("question", "")
    comp = record.get("completion") or {}
    ans = record.get("answer") or {}

    # 该题 citations 的全局编号（按顺序），供 citation_refs 下标映射。
    cit_labels: list[str] = []
    for cit in ans.get("citations", []):
        cit_labels.append(labels.get(_citation_key(cit), "R?"))

    lines = [f"### {case_id} · {question}"]
    lines.append(f"- 完成状态: {comp.get('actual_status', '?')} / {comp.get('stop_reason') or '-'}")
    lines.append(f"- 答案: {ans.get('answer_text') or '（无答案）'}")
    claims = ans.get("claims", [])
    if claims:
        lines.append("- 断言:")
        for c in claims:
            refs = [cit_labels[i] for i in c.get("citation_refs", []) if i < len(cit_labels)]
            tag = "事实" if c.get("kind") == "fact" else "研判"
            ref_str = ",".join(refs) if refs else "无引用"
            lines.append(f"  - [{tag}] {c.get('text')}（引用: {ref_str}）")
    unresolved = ans.get("unresolved_items", []) or []
    if unresolved:
        lines.append("- 未解决:")
        for u in unresolved:
            lines.append(f"  - {u}")
    return "\n".join(lines)


def _materials_text(records: list[dict], labels: dict[str, str]) -> str:
    if not records:
        return "（无材料）"
    return "\n\n".join(_record_material(r, labels) for r in records)


# ---------------------------------------------------------------------------
# Markdown 组装（水印 + 引用表确定性注入，LLM 只生成正文）
# ---------------------------------------------------------------------------

def assemble_markdown(*, company_id: str, section_id: str, body: str,
                      citations: list[dict], limitations: list[str]) -> str:
    """把 LLM 正文 + 确定性水印 + 引用表 + 未解决项组装为最终预览 Markdown。

    水印与引用表不委托 LLM，保证「明显水印」与「可展开引用」恒存在。
    """
    lines = [
        f"# {WATERMARK}",
        "",
        f"> 公司 `{company_id}` · 章节 `{section_id}` · 非正式预览，未通过任何章节质量门。"
        f"由 41 问 Actual-Path ResearchOutcome 汇总生成，不重新检索、不重新计算财务数字。",
        "",
    ]
    body = body.strip()
    # 防御：若 LLM 自带了标题/水印，去重避免重复注入。
    for hdr in (f"# {WATERMARK}", f"## {WATERMARK}"):
        body = body.replace(hdr, "").strip()
    lines.append(body)
    lines.append("")
    lines.append("## 引用")
    lines.append("")
    if citations:
        for e in citations:
            lines.append(f"- [{e['label']}] {e['display']}")
    else:
        lines.append("- （无引用）")
    lines.append("")
    lines.append("## 未解决事项")
    lines.append("")
    if limitations:
        for lim in limitations:
            lines.append(f"- {lim}")
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _collect_limitations(records: list[dict]) -> list[str]:
    """未解决事项：各题 unresolved_items 去重 + 非 FULL 题的显式缺口标记。"""
    seen: set[str] = set()
    out: list[str] = []
    for rec in records:
        for u in (rec.get("answer") or {}).get("unresolved_items", []) or []:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    for rec in records:
        comp = rec.get("completion") or {}
        status = comp.get("actual_status", "?")
        if status != "FULL":
            note = f"{rec.get('case_id')}: {status}（{comp.get('stop_reason') or '-'}）"
            if note not in seen:
                seen.add(note)
                out.append(note)
    return out


# ---------------------------------------------------------------------------
# 预览生成（LLM 可注入，供专项 eval mock）
# ---------------------------------------------------------------------------

def _llm_generate(prompt: str, model: str | None) -> str:
    """调用真实 LLM 生成正文（真实运行路径）。

    关闭推理：预览是受控的「只复述已取得材料、不新增事实」任务，推理内容会占
    output_tokens 导致截断/空正文。
    """
    resp = llm_client.chat_with_usage(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        max_tokens=4096,
        prompt_version=PREVIEW_PROMPT_VERSION,
        thinking={"type": "disabled"},
    )
    return resp.text


def build_preview(
    *,
    result_dir: str,
    section_id: str,
    company_id: str,
    model: str | None = None,
    output_root: str = DEFAULT_OUTPUT_ROOT,
    llm_generate=_llm_generate,
) -> ResearchPreview:
    """生成选定章节的预览草稿（确定性排序 → 轻量 Prompt → 组装水印/引用）。"""
    import config

    records = _sort_records(_select_section(_load_records(result_dir), section_id))
    table, labels = _build_citation_labels(records)

    prompt = _render_template(
        llm_client.load_prompt(PREVIEW_PROMPT_VERSION),
        {
            "company_id": company_id,
            "section_id": section_id,
            "materials": _materials_text(records, labels),
            "citations": _citations_text(table),
        },
    )

    body = llm_generate(prompt, model)
    limitations = _collect_limitations(records)

    markdown = assemble_markdown(
        company_id=company_id, section_id=section_id, body=body,
        citations=table, limitations=limitations,
    )

    preview = ResearchPreview(
        preview_id=f"preview_{Path(result_dir).name}_{section_id}",
        section_id=section_id,
        outcome_ids=[r.get("case_id") for r in records],
        markdown=markdown,
        citations=[{k: e.get(k) for k in ("label", "ref_type", "evidence_id",
                                          "source_snapshot_id", "snapshot_id", "item_code",
                                          "formula_id", "formula_version", "period",
                                          "page_number", "display")} for e in table],
        limitations=limitations,
        generated_at=_now_iso(),
        prompt_version=PREVIEW_PROMPT_VERSION,
        model_version=model or config.LLM_MODEL,
        formal_section_passed=False,
    )

    _write_preview(preview, output_root)
    return preview


def _write_preview(preview: ResearchPreview, output_root: str) -> Path:
    """落盘 <output_root>/<run_id>/<section>.(md|preview.json)。"""
    run_id = preview.preview_id.removeprefix("preview_").removesuffix(
        f"_{preview.section_id}")
    out_dir = Path(output_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{preview.section_id}.md"
    md.write_text(preview.markdown, encoding="utf-8")
    sidecar = out_dir / f"{preview.section_id}.preview.json"
    sidecar.write_text(
        json.dumps(asdict(preview), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote research preview to %s", out_dir)
    return out_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str]):
    import argparse
    p = argparse.ArgumentParser(description="Phase 3 Batch B 章节预览生成器")
    p.add_argument("--actual-path-result", required=True, dest="result_dir")
    p.add_argument("--section", required=True)
    p.add_argument("--company", default="300750", dest="company_id")
    p.add_argument("--output", default=DEFAULT_OUTPUT_ROOT, dest="output_root")
    p.add_argument("--model", default=None)
    p.add_argument("--no-llm", action="store_true",
                   help="跳过 LLM，仅输出确定性材料/引用表（调试预览纯函数）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.no_llm:
        records = _sort_records(_select_section(_load_records(args.result_dir), args.section))
        table, labels = _build_citation_labels(records)
        print(json.dumps({
            "result_dir": args.result_dir,
            "section": args.section,
            "n_outcomes": len(records),
            "outcome_ids": [r.get("case_id") for r in records],
            "citations": table,
            "limitations": _collect_limitations(records),
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        preview = build_preview(
            result_dir=args.result_dir, section_id=args.section,
            company_id=args.company_id, model=args.model, output_root=args.output_root)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(json.dumps({
        "preview_id": preview.preview_id,
        "section_id": preview.section_id,
        "n_outcomes": len(preview.outcome_ids),
        "n_citations": len(preview.citations),
        "n_limitations": len(preview.limitations),
        "formal_section_passed": preview.formal_section_passed,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
