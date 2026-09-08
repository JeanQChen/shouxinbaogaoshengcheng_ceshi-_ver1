"""Eval: Phase 3 章节预览生成器纯函数层 —— Phase 3 Batch B commit 6。

用法: python -m evals.test_research_preview

断言（无真实 LLM / 无网络；LLM 用注入的 mock 替代）：
- _render_template：{{key}} 替换、JSON 花括号原样保留；
- _load_records / _select_section / _sort_records：确定性排序（priority→case_id）、
  章节过滤、空目录 fail-closed；
- _build_citation_labels：跨题去重 + 首次出现顺序编号 + display 可展开；
- _record_material：claim 的 citation_refs 下标映射到全局 R 编号；
- assemble_markdown：水印恒存在、引用表恒附加、LLM 回显标题不重复注入；
- _collect_limitations：unresolved 去重 + 非 FULL 题缺口标记；
- build_preview：mock LLM → ResearchPreview（formal_section_passed=False、水印在 markdown、
  outcome_ids 排序稳定）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import build_research_preview as B


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _record(case_id, *, section="company", priority="P0", question="成立时间？",
            status="FULL", stop="COMPLETED", answer_text="2001 年成立",
            claims=None, citations=None, unresolved=None) -> dict:
    return {
        "case_id": case_id,
        "question": question,
        "section_id": section,
        "priority": priority,
        "completion": {"actual_status": status, "stop_reason": stop},
        "answer": {
            "answer_text": answer_text,
            "claims": claims or [],
            "citations": citations or [],
            "unresolved_items": unresolved or [],
        },
        "evidence_ids": [],
        "structured_refs": [],
        "external_snapshot_ids": [],
    }


def _cit(ref_type, evidence_id=None, page_number=None, source_snapshot_id=None):
    c = {"ref_type": ref_type}
    if evidence_id is not None:
        c["evidence_id"] = evidence_id
    if page_number is not None:
        c["page_number"] = page_number
    if source_snapshot_id is not None:
        c["source_snapshot_id"] = source_snapshot_id
    return c


def _write_case_results(dir_path: str, records: list[dict]) -> str:
    p = Path(dir_path)
    p.mkdir(parents=True, exist_ok=True)
    f = p / "case_results.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                 encoding="utf-8")
    return str(p)


def _mock_llm(body: str):
    def _gen(prompt: str, model=None) -> str:
        return body
    return _gen


# ---------------------------------------------------------------------------
# main
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

    # ---- _render_template ----
    out = B._render_template("{{a}} {x} {{b}}", {"a": "1", "b": "2"})
    check(out == "1 {x} 2", "_render_template：{{key}} 替换且保留 JSON 花括号")

    # ---- load / select / sort ----
    recs = [
        _record("C", priority="P2"),
        _record("A", priority="P0"),
        _record("B", priority="P1", section="industry"),
        _record("D", priority="P0"),
    ]
    d = _write_case_results(tempfile.mkdtemp(), recs)
    loaded = B._load_records(d)
    check(len(loaded) == 4, "_load_records：读回 4 条")
    sel = B._select_section(loaded, "company")
    check(len(sel) == 3 and all(r["section_id"] == "company" for r in sel),
          "_select_section：只留 company")
    sorted_ids = [r["case_id"] for r in B._sort_records(sel)]
    check(sorted_ids == ["A", "D", "C"], "_sort_records：P0(P0 case_id 序) → P2")
    try:
        B._select_section(loaded, "nonexistent")
        check(False, "_select_section：空章节应抛错")
    except ValueError:
        check(True, "_select_section：空章节 fail-closed")

    # ---- citation labels：去重 + 顺序 ----
    recs2 = [
        _record("A", citations=[_cit("evidence", "e1", 3)]),
        _record("D", citations=[_cit("evidence", "e1", 3), _cit("external", None, None, "s1")]),
        _record("C", citations=[_cit("evidence", "e2")]),
    ]
    table, labels = B._build_citation_labels(recs2)
    check(len(table) == 3 and [t["label"] for t in table] == ["R1", "R2", "R3"],
          "引用去重：3 个唯一引用，编号 R1..R3")
    check(labels[B._citation_key(_cit("evidence", "e1", 3))] == "R1"
          and labels[B._citation_key(_cit("external", None, None, "s1"))] == "R2",
          "首次出现顺序决定编号")
    check(table[0]["display"].startswith("证据") and "e1" in table[0]["display"],
          "evidence 引用 display 可展开")
    check("s1" in table[1]["display"] and "外部快照" in table[1]["display"],
          "external 引用 display 可展开")

    # ---- material：citation_refs 下标 → 全局编号 ----
    recs3 = [
        _record("A", citations=[_cit("evidence", "e1", 3)],
                claims=[{"claim_id": "c1", "text": "实控人为 X", "kind": "fact",
                         "citation_refs": [0]}]),
    ]
    t3, l3 = B._build_citation_labels(recs3)
    mat = B._record_material(recs3[0], l3)
    check("R1" in mat and "实控人为 X" in mat and "事实" in mat,
          "material：claim 的 citation_refs 映射到 R1")

    # ---- assemble_markdown：水印 + 引用表恒存在 ----
    md = B.assemble_markdown(company_id="300750", section_id="company", body="正文",
                             citations=table, limitations=["未取得 X"])
    check(B.WATERMARK in md and "## 引用" in md and "[R1]" in md and "[R2]" in md,
          "assemble：水印 + 引用表恒存在")
    check("未取得 X" in md, "assemble：limitations 注入")
    # LLM 回显水印 → 去重，不重复
    md2 = B.assemble_markdown(company_id="300750", section_id="company",
                              body=f"# {B.WATERMARK}\n正文", citations=[], limitations=[])
    check(md2.count(B.WATERMARK) == 1, "assemble：LLM 回显水印去重，只出现一次")

    # ---- _collect_limitations ----
    lims = B._collect_limitations([
        _record("A", unresolved=["缺担保明细"], status="FULL"),
        _record("B", unresolved=["缺担保明细"], status="PARTIAL", stop="BUDGET_EXTERNAL"),
    ])
    check(lims[0] == "缺担保明细" and lims.count("缺担保明细") == 1,
          "limitations：unresolved 去重")
    check(any("B:" in x and "PARTIAL" in x for x in lims),
          "limitations：非 FULL 题缺口标记")

    # ---- build_preview（mock LLM）----
    recs4 = [
        _record("A", priority="P1", question="主营业务？", answer_text="锂电",
                citations=[_cit("evidence", "e1", 5)]),
        _record("B", priority="P0", question="实控人？", answer_text="曾毓群",
                citations=[_cit("evidence", "e2")], unresolved=["担保明细未取得"]),
    ]
    d4 = _write_case_results(tempfile.mkdtemp(), recs4)
    out_root = tempfile.mkdtemp()
    preview = B.build_preview(
        result_dir=d4, section_id="company", company_id="300750",
        model="mock-model", output_root=out_root,
        llm_generate=_mock_llm("## 主体与基本信息\n公司成立。[R1][R2]\n"),
    )
    check(preview.formal_section_passed is False,
          "build_preview：formal_section_passed 恒 False")
    check(preview.outcome_ids == ["B", "A"], "build_preview：outcome_ids 按 priority 排序稳定")
    check(B.WATERMARK in preview.markdown and "[R1]" in preview.markdown
          and "[R2]" in preview.markdown, "build_preview：markdown 含水印 + 引用")
    check(preview.prompt_version == "research_preview_v1"
          and preview.model_version == "mock-model", "build_preview：版本字段填充")
    out_dir = Path(out_root) / Path(d4).name
    check((out_dir / "company.md").exists() and (out_dir / "company.preview.json").exists(),
          "build_preview：落盘 md + preview.json")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(main(), ensure_ascii=False, indent=2))
