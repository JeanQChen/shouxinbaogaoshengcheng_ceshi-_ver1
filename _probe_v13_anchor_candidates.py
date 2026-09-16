"""只读取证（真实语料）：v13 三个 P1 的现状before取证。

1. P1-1：``_detect_reference_targets`` 是否把「如下表」与其子串「下表」各产出一条请求；
        ``iter_reference_marker_occurrences`` 的逐 occurrence 结果到底是什么。
2. P1-2：真实锚点块内 ``reference_target_table_objects`` 的全部候选（含被误判的散文候选）。
3. P1-3：当前 ``table_object_id`` 的 canonical payload 只含行数不含表体内容。

零写入：只读 evidence.db，不触碰 evaluation/results。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.context_expansion import _detect_reference_targets  # noqa: E402
from harness.evidence_reader import (  # noqa: E402
    ReadonlyEvidenceReader,
    iter_reference_marker_occurrences,
)
from harness.table_structure import (  # noqa: E402
    is_columnar_row,
    is_closure_row,
    is_unit_line,
    normalize_line,
    reference_target_table_objects,
    table_object_id,
)

SEED = "c783f2277baa5eda1659bc5c3fab5d46"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    reader = ReadonlyEvidenceReader(Path("data/evidence.db"))
    blk = reader.get_block(SEED)
    if blk is None:
        raise SystemExit(f"拒绝：真实 seed {SEED} 不在 evidence.db")
    text = blk.text or ""
    print(f"seed={SEED} p{blk.page_number} blk{blk.block_index} len={len(text)}")
    print(f"source_name={blk.source_name} document_version={blk.document_version} "
          f"set={blk.evidence_set_version}")
    print("---- P1-1: _detect_reference_targets（请求目标） ----")
    print(json.dumps(_detect_reference_targets(text), ensure_ascii=False))
    print("---- P1-1: iter_reference_marker_occurrences（逐 occurrence） ----")
    occ = iter_reference_marker_occurrences(text)
    print(json.dumps(occ, ensure_ascii=False))
    for (m, s, e) in occ:
        print(f"  occurrence {m!r} [{s},{e}) 原文切片={text[s:e]!r}")
    print("---- P1-2: reference_target_table_objects(text, 首个 occurrence end) ----")
    me = occ[0][2] if occ else 0
    objs = reference_target_table_objects(text, me)
    print(f"candidate_count={len(objs)}")
    print(json.dumps(objs, ensure_ascii=False, indent=2))
    print("---- 候选所在行明细 ----")
    for idx, o in enumerate(objs):
        seg = text[o["target_start"]:o["target_end"]]
        print(f"候选[{idx}] start={o['target_start']} end={o['target_end']} "
              f"title={o['target_table_title']!r}")
        print(f"        body_rows={o['target_body_rows']} "
              f"structure_rows={o['target_structure_rows']} closed={o['target_closed']}")
        print(f"        slice={seg!r}")
        print(f"        object_id={o['target_object_id']}")
    print("---- P1-3: 目标表体行明细（判断 object_id 是否绑定真实内容） ----")
    if objs:
        o = objs[0]
        seg = normalize_line(text[o["target_start"]:o["target_end"]])
        print(f"规范化后的目标切片={seg!r}")
        for line in text[o["target_start"]:o["target_end"]].splitlines():
            n = normalize_line(line)
            print(f"  行 {n!r} columnar={is_columnar_row(n)} unit={is_unit_line(n)} "
                  f"closure={is_closure_row(n)}")
    print("---- P1-3: 同结构不同表体内容是否撞 ID（构造对照） ----")
    _kw = dict(title="表5-5参股企业情况", header_rows=("序号  企业名称",), structure_rows=2)
    a = table_object_id(body_row_texts=("1   A公司",), **_kw)
    b = table_object_id(body_row_texts=("1   B公司",), **_kw)
    c = table_object_id(body_row_texts=("1  A 公司",), **_kw)
    print(f"不同企业名：{a[:16]} vs {b[:16]} -> 相同? {a == b}（必须不同）")
    print(f"仅空白差异：{a[:16]} vs {c[:16]} -> 相同? {a == c}（必须相同）")


if __name__ == "__main__":
    main()
