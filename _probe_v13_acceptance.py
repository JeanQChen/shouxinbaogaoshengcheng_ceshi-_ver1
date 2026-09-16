"""只读取证（v13 run）：§六 验收侧独立重算的真实证据链。

1. 显式引用审计（生产验收器 ``verify_category`` 独立重算，含 occurrence 身份、同块候选数、
   对象身份/表体摘要）与逐目标明细；
2. 「如下表 / 下表」请求计数（证明重复请求消失）；
3. P43 ``049de1a2…``「表5-6」是否出现在任何 v13 产物的**绑定 / 材料 / 装配**位置；
4. 表体篡改对照：同一 occurrence 下企业名/金额变化 → 对象身份变化；仅空白差异 → 不变。

零写入：只读 run 目录与 evidence.db。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import ReadonlyEvidenceReader, iter_reference_occurrences  # noqa: E402
from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    verify_category,
)
from harness.table_structure import (  # noqa: E402
    reference_target_table_objects,
    table_object_id,
)

RUN_DIR = Path("evaluation/results/r2_material_slice_r2_sixcat_v13_major_subsidiaries_20260916")
SEED = "c783f2277baa5eda1659bc5c3fab5d46"
WRONG = "049de1a26d73f3e89611679694ff4f06"


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("---- 1. 显式引用审计（验收侧独立重算） ----")
    v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, RUN_DIR)
    ref = (v.facts or {}).get("explicit_reference_audit") or {}
    print(json.dumps({"material_state": v.material_state,
                      "capability_verdict": v.capability_verdict,
                      "report_impact": v.report_impact,
                      "verdict": v.verdict,
                      "failed_gates": v.failed_gates,
                      "reason": v.reason,
                      "audit": ref}, ensure_ascii=False, indent=2))

    print("---- 2. 「如下表 / 下表」请求计数（v12 重复请求是否消失） ----")
    steps = _read_jsonl(RUN_DIR / "expansion_trace.jsonl")
    xref = [s for s in steps if (s.get("arguments") or {}).get("mode") == "explicit_reference"]
    targets = [(s["arguments"].get("reference_target"),
                s["arguments"].get("reference_marker"),
                s["arguments"].get("marker_start"),
                s["arguments"].get("marker_end"),
                s["arguments"].get("reference_occurrence_index"),
                s["arguments"].get("reference_kind")) for s in xref]
    print(json.dumps({"explicit_reference_step_count": len(xref),
                      "declared_requests": targets,
                      "duplicate_marker_requests": len(targets) - len(set(
                          (t[1], t[2], t[3]) for t in targets)),
                      "outputs": [s.get("outputs") for s in xref],
                      "stop_reasons": [s.get("stop_reason") for s in xref]},
                     ensure_ascii=False, indent=2))

    print("---- 3. P43 表5-6（必须被拒绝的旧错误目标）在产物中的出现情况 ----")
    hits: list[dict] = []
    for p in sorted(RUN_DIR.rglob("*")):
        if not p.is_file():
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if WRONG in txt:
            hits.append({"file": p.relative_to(RUN_DIR).as_posix(), "count": txt.count(WRONG)})
    material_index = json.loads((RUN_DIR / "material_index.json").read_text(encoding="utf-8"))
    assemblies = json.loads((RUN_DIR / "assemblies.json").read_text(encoding="utf-8"))
    aspect_links = json.loads((RUN_DIR / "aspect_links.json").read_text(encoding="utf-8"))
    blob = json.dumps({"material_index": material_index, "assemblies": assemblies,
                       "aspect_links": aspect_links}, ensure_ascii=False)
    print(json.dumps({"files_containing_wrong_evidence_id": hits,
                      "wrong_id_in_binding_material_assembly":
                          {"material_index": WRONG in json.dumps(material_index, ensure_ascii=False),
                           "assemblies": WRONG in json.dumps(assemblies, ensure_ascii=False),
                           "aspect_links": WRONG in json.dumps(aspect_links, ensure_ascii=False)}},
                     ensure_ascii=False, indent=2))

    print("---- 4. 表对象身份绑定真实表体（生产侧独立重算 + 篡改对照） ----")
    reader = ReadonlyEvidenceReader(Path("data/evidence.db"))
    text = reader.get_block(SEED).text
    occs = iter_reference_occurrences(text)
    me = occs[0].end
    objs = reference_target_table_objects(text, me)
    o = objs[0]
    print(json.dumps({"occurrences": [x.to_dict() for x in occs],
                      "same_block_candidate_count": len(objs),
                      "target_table_title": o["target_table_title"],
                      "target_start": o["target_start"], "target_end": o["target_end"],
                      "target_body_row_texts": o["target_body_row_texts"],
                      "target_body_digest": o["target_body_digest"],
                      "target_object_id": o["target_object_id"],
                      "target_end_boundary": o["target_end_boundary"]},
                     ensure_ascii=False, indent=2))
    kw = dict(title=o["target_table_title"], unit=o["target_unit"],
              header_rows=tuple(o["target_header_rows"]),
              structure_rows=o["target_structure_rows"])
    real = o["target_body_row_texts"]
    tampered_name = tuple(t.replace("洛阳栾川钼业集团股份有限公司", "示例企业名称变更")
                          for t in real)
    tampered_amount = tuple(t.replace("24.9%", "25.0%") for t in real)
    spaced = tuple("  " + t.replace("  ", "   ") + " " for t in real)
    print(json.dumps({
        "id_real": table_object_id(body_row_texts=real, **kw),
        "id_tampered_enterprise_name": table_object_id(body_row_texts=tampered_name, **kw),
        "id_tampered_ratio": table_object_id(body_row_texts=tampered_amount, **kw),
        "id_whitespace_only_diff": table_object_id(body_row_texts=spaced, **kw),
        "name_change_changes_id": table_object_id(body_row_texts=tampered_name, **kw) != table_object_id(body_row_texts=real, **kw),
        "ratio_change_changes_id": table_object_id(body_row_texts=tampered_amount, **kw) != table_object_id(body_row_texts=real, **kw),
        "whitespace_keeps_id": table_object_id(body_row_texts=spaced, **kw) == table_object_id(body_row_texts=real, **kw),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
