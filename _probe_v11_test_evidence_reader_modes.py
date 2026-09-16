"""只读探针：用 test_evidence_reader 的真实 fixture 观察新契约下的工具返回。

不改任何文件，只打印每个 mode 调用的 status/message/data 结构，供把该测试模块
的续表断言更新到当前契约（锚点块结构派生身份）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from evals import test_evidence_reader as T  # noqa: E402
from harness.evidence_reader import BoundedEvidenceInspectionAdapter  # noqa: E402
from harness.table_structure import open_table_signature  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = T._make_evidence_db(Path(td))
        adapter = BoundedEvidenceInspectionAdapter(db)
        base = {"company_id": T._COMPANY, "document_id": T._DOC,
                "document_version": T._DOCV, "evidence_set_version": T._SETV}

        for (page, blk, _sp, etype, text, _pl) in T._BLOCKS:
            sig = open_table_signature(text)
            if sig is not None:
                print(f"open_table_signature({page},{blk},{etype}):", json.dumps(
                    sig, ensure_ascii=False)[:200])

        eid_5_2, _ = T._block_identity(5, 2)
        eid_5_1, _ = T._block_identity(5, 1)
        eid_6_0, _ = T._block_identity(6, 0)
        cases = [
            ("anchor=(6,0) 有未闭合结构的一行", {"mode": "table_continuation",
                                                 "page_number": 6, "block_index": 0,
                                                 "limit": 10, "evidence_id": eid_6_0}),
            ("no evidence_id", {"mode": "table_continuation", "page_number": 5,
                                "block_index": 1, "limit": 10,
                                "table_title": "营业收入构成"}),
            ("anchor=paragraph 5,1", {"mode": "table_continuation", "page_number": 5,
                                      "block_index": 1, "limit": 10,
                                      "evidence_id": eid_5_1}),
            ("anchor=table 5,2 declared match", {"mode": "table_continuation",
                                                 "page_number": 5, "block_index": 2,
                                                 "limit": 10, "evidence_id": eid_5_2,
                                                 "table_title": "营业收入构成"}),
            ("anchor=table 5,2 declared mismatch", {"mode": "table_continuation",
                                                     "page_number": 5, "block_index": 2,
                                                     "limit": 10, "evidence_id": eid_5_2,
                                                     "table_title": "营业成本构成"}),
            ("explicit_reference 详见块", {"mode": "explicit_reference",
                                           "page_number": 9, "block_index": 0,
                                           "limit": 10,
                                           "reference_target": "详见 24、所有权或使用权受到限制的资产"}),
        ]
        for label, extra in cases:
            args = dict(base)
            args.update(extra)
            res = adapter.execute(args)
            print("=" * 70)
            print(label, "->", res.status, "|", res.message)
            if isinstance(res.data, dict):
                print("   data keys:", sorted(res.data.keys()))
                print("   blocks:", [b.get("evidence_id", "")[:12]
                                     for b in (res.data.get("blocks") or [])])
                print("   has_more:", res.data.get("has_more"))


if __name__ == "__main__":
    main()
