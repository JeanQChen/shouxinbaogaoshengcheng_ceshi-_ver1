"""只读取证（R2 定点修复）：真实锚点上的结构性表引用绑定 + 错误目标拒绝记录。

复现 v11 真实现场：seed ``c783f227…``（p41 blk0）块内「如下表」之后紧邻真正目标
「表5-5 截至2025年12月末发行人主要参股及联营、合营企业情况」，而旧实现把目标错误绑到
后续块的「表5-6 发行人组织结构图」。

本脚本**只读**：直接对 ``data/evidence.db`` 调用生产 adapter，用 v11 trace 里真实记录的
args 形状，打印
  1. 逐 occurrence 标记位置与标记后原文片段；
  2. 新绑定记录（作用域 / 目标表题 / 目标身份 / 目标位置 / 理由）；
  3. 目标对象正文片段（人工核对确实是同块 表5-5 对象）；
  4. **拒绝记录**：旧实现会命中的 P43「表5-6」目标现在被明确排除（不在绑定内 + 逐条理由）。

零 LLM / 零网络 / 零写库。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.evidence_reader import (  # noqa: E402
    BoundedEvidenceInspectionAdapter,
    ReadonlyEvidenceReader,
    iter_reference_marker_occurrences,
)
from harness.table_structure import reference_target_table_objects  # noqa: E402

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v11_major_subsidiaries_20260916")
DB = Path("data/evidence.db")
ASPECT = "company_subsidiaries.major_subsidiaries"


def main() -> None:
    sm = json.loads((RUN / "seed_manifest.json").read_text(encoding="utf-8"))
    entry = next(e for e in sm["entries"] if e.get("aspect_id") == ASPECT)
    anchor = str(entry["evidence_id"])
    page = int(entry["page_number"])
    blk = int(entry["block_index"])
    doc = str(entry["document_id"]); dv = str(entry["document_version"])
    setv = str(entry["evidence_set_version"])
    print(f"seed: {anchor[:12]} p{page} blk{blk} {doc}/{dv}/{setv} aspect={ASPECT}")

    trace_path = RUN / "expansion_trace.jsonl"
    xref_steps = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        if (s.get("arguments") or {}).get("mode") == "explicit_reference":
            xref_steps.append(s)
    print(f"\nv11 trace 中 mode=explicit_reference 步骤: {len(xref_steps)}")
    for s in xref_steps:
        a = s.get("arguments") or {}
        print(f"  step{s.get('step_index')} seed={str(s.get('seed_evidence_id'))[:12]} "
              f"anchor={str(s.get('anchor_evidence_id'))[:12]} "
              f"page={a.get('page_number')} blk={a.get('block_index')} "
              f"target={a.get('reference_target')!r} outputs={s.get('outputs')} "
              f"stop={s.get('stop_reason')!r}")

    reader = ReadonlyEvidenceReader(DB)
    ablk = reader.get_block(anchor)
    text = (ablk.text or "") if ablk else ""
    print(f"\n--- 发起块正文（{len(text)} 字符）---")
    print(text)

    print("\n--- §二.4 逐 occurrence 标记（同起点取最长，互不重叠）---")
    occ = iter_reference_marker_occurrences(text)
    for m, s0, e0 in occ:
        print(f"  marker={m!r} start={s0} end={e0}")
        print(f"    标记后原文={text[e0:e0 + 120]!r}")

    print("\n--- §二.2 标记之后同块内的**全部**可验证表对象（升序）---")
    objs = reference_target_table_objects(text, occ[0][2]) if occ else []
    for i, o in enumerate(objs):
        print(f"  [{i}] title={o['target_table_title']!r}")
        print(f"      object_id={o['target_object_id'][:16]}… "
              f"start={o['target_start']} end={o['target_end']} "
              f"body_rows={o['target_body_rows']} header={o['target_header_rows']}")
        print(f"      片段={text[o['target_start']:o['target_end']][:160]!r}")

    # ---- 生产读取（v11 trace 的真实 args 形状：不传 evidence_id）----
    xref = next((s for s in xref_steps if str(s.get("seed_evidence_id")) == anchor), None)
    target_text = str((xref or {}).get("arguments", {}).get("reference_target") or "如下表")
    adapter = BoundedEvidenceInspectionAdapter(DB)
    res = adapter.execute({
        "company_id": entry["company_id"], "document_id": doc,
        "document_version": dv, "evidence_set_version": setv,
        "mode": "explicit_reference", "reference_target": target_text,
        "page_number": page, "block_index": blk, "limit": 4})
    print(f"\n--- 生产读取结果（reference_target={target_text!r}）---")
    print(f"status={res.status} message={res.message!r} evidence_ids={res.evidence_ids}")
    binding = (res.data or {}).get("reference_binding") or {}
    print("\n--- 绑定记录 ---")
    print(json.dumps(binding, ensure_ascii=False, indent=2))

    # ---- 错误目标拒绝记录 ----
    wrong = "049de1a26d73f3e89611679694ff4f06"
    print("\n--- 错误目标拒绝记录（旧实现命中的 P43「表5-6 发行人组织结构图」）---")
    print(f"  旧实现命中块 evidence_id = {wrong[:12]}…")
    print(f"  该块是否出现在本次读取输出中: {wrong in (res.evidence_ids or [])}")
    print(f"  绑定 target_evidence_id       = {str(binding.get('target_evidence_id'))[:12]}…")
    print(f"  绑定 target_object_id 是否命中表5-6: "
          f"{any(o['target_object_id'] == binding.get('target_object_id') and '表5-6' in o['target_table_title'] for o in objs)}")
    print(f"  绑定 target_table_title        = {binding.get('target_table_title')!r}")
    ok = (res.status == "SUCCESS"
          and binding.get("resolution_scope") == "same_block"
          and binding.get("target_evidence_id") == anchor
          and "表5-5" in str(binding.get("target_table_title"))
          and "表5-6" not in str(binding.get("target_table_title")))
    print(f"\n同块 表5-5 目标绑定成立（表5-6 被拒绝）: {ok}")


if __name__ == "__main__":
    main()
