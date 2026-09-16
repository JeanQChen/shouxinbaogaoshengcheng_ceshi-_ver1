"""只读取证：v14 新原语（保列身份 / 逐行证据 / 共享角色裁定）在反例与真实语料上的表现。

零写入。仅打印事实，不参与任何验收判定。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.evidence_reader import ReadonlyEvidenceReader  # noqa: E402
from harness import table_structure as T  # noqa: E402

SEED = "c783f2277baa5eda1659bc5c3fab5d46"

# §三 P1-2：这段治理类折行散文必须**被拒绝**为表对象（且不得产生伪 target_not_obtained）。
GOVERNANCE = """公司治理架构
公司  严格按照公司法建立制度
股东大会  董事会依法履职
后续说明。"""

CASES: list[tuple[str, str, bool]] = [
    ("反例·治理折行散文（必须拒绝）", GOVERNANCE, False),
    ("正例·单层表头+两数据行",
     "营业收入构成\n项目  2024年  2023年\n收入  1000  900\n成本  600  500", True),
    ("正例·多层表头（子列层，必须识别）",
     "主要子公司情况\n单位：万元\n序号  子公司名称  注册地  注册资本  持股比例  取得方式\n"
     "直接  间接\n1  A公司  北京市  1000  51%  20%", True),
    ("正例·全文本列表",
     "子公司名称  注册地  持股比例\nA公司  北京市  51%\nB公司  上海市  49%", True),
    ("正例·数字表（无单位行）",
     "营业收入构成\n项目  2024年  2023年\n收入  1000  900", True),
    ("正例·表题含标点",
     "表5-5截至2025年12月末发行人主要参股及联营、合营企业情况\n"
     "序号  重要的合营企业或联营企业  注册地  持股比例  取得方式\n"
     "1  洛阳栾川钼业集团股份有限公司  洛阳市  24.9%  权益法", True),
    ("正例·无显式表号但结构充分",
     "主要子公司情况\n序号  名称  注册地  持股比例\n1  A公司  北京市  51%\n"
     "2  B公司  上海市  49%", True),
    ("反例·仅两列两行（与折行散文不可区分）",
     "经营情况\n公司  严格按照公司法建立制度\n股东大会  董事会依法履职", False),
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 78)
    print("--- 一、控例：结构性表对象资格（_table_object_at 经 reference_target_table_objects）---")
    for name, text, expect_obj in CASES:
        objs = T.reference_target_table_objects(text, 0)
        flags = T.detect_table_start_flags([T.normalize_line(l) for l in text.splitlines()])
        ok = bool(objs) == expect_obj
        print(f"[{'OK ' if ok else 'FAIL'}] {name}: 表对象={len(objs)} 期望={'有' if expect_obj else '无'}"
              f" 通用表题flag={flags}")
        for o in objs:
            print(f"        title={o['target_table_title']!r} 表头={o['target_header_rows']} "
                  f"role={o['target_header_decision']} 列数={o['target_column_count']} "
                  f"表体={o['target_body_row_texts']} obj={o['target_object_id'][:12]}")

    print("=" * 78)
    print("--- 二、P2：一行表头 + 两行数据必须保留两行表体 ---")
    h, d, dec = T.assign_table_row_roles(
        ["项目  2024年  2023年", "收入  1000  900", "成本  600  500"])
    print(f"表头={h} 其余={d} 裁定={dec}  (期望其余为两行数据、裁定={T.HEADER_DECISION_FIRST_DATA})")
    h2, d2, dec2 = T.assign_table_row_roles(
        ["序号  名称  注册地  注册资本  持股比例  取得方式", "直接  间接",
         "1  A公司  北京市  1000  51%  20%"])
    print(f"表头={h2} 其余={d2} 裁定={dec2}  (期望表头两行、裁定={T.HEADER_DECISION_SUBCOLUMN})")

    print("=" * 78)
    print("--- 三、P1-1：保列身份反例 ---")
    kw = {"title": "表5-5 参股情况", "unit": "单位：万元",
          "header_rows": ("序号  名称  金额",)}
    a = T.table_object_id(body_row_texts=("A  1  23",), **kw)
    b = T.table_object_id(body_row_texts=("A  12  3",), **kw)
    c = T.table_object_id(body_row_texts=("A  1   23",), **kw)
    d_ = T.table_object_id(body_row_texts=("A  1  23", "B  2  3"), **kw)
    e = T.table_object_id(body_row_texts=("B  2  3", "A  1  23"), **kw)
    print(f"列边界不同(A|1|23 vs A|12|3) 撞ID? {a == b}  (期望 False)")
    print(f"仅列间距宽度不同 撞ID? {a == c}  (期望 True)")
    print(f"行序改变 撞ID? {a == e}  (期望 False)")
    print(f"多一行 撞ID? {a == d_}  (期望 False)")
    print(f"payload={T.table_object_payload(body_row_texts=('A  1  23',), **kw)}")

    print("=" * 78)
    print("--- 四、真实 seed 块：起点集合与表5-5 目标 ---")
    text = ReadonlyEvidenceReader(Path("data/evidence.db")).get_block(SEED).text
    lines = [T.normalize_line(l) for l in text.splitlines()]
    starts = T.table_start_indices(lines)
    for i in starts:
        print(f"  start[{i}] title={lines[i]!r} explicit={T.is_explicit_table_title(lines[i])}")
    objs = T.reference_target_table_objects(text, 297)
    for o in objs:
        print(f"  台账候选: start={o['target_start']} end={o['target_end']} "
              f"title={o['target_table_title']!r} role={o['target_header_decision']} "
              f"表头={o['target_header_rows']} 列数={o['target_column_count']} "
              f"表体={o['target_body_row_texts']} 终止={o['target_end_boundary']} "
              f"obj={o['target_object_id'][:12]}")
    print(f"  逐行 flag（仅结构行）:")
    flags = T.detect_table_start_flags(lines)
    for i, t in enumerate(lines):
        if not t:
            continue
        if T.is_columnar_row(t) or flags[i] or T.is_title_form(t):
            print(f"    [{i:3d}] flag={int(flags[i])} form={int(T.is_title_form(t))} "
                  f"col={int(T.is_columnar_row(t))} n={len(T.column_cells(t))} | {t[:56]}")


if __name__ == "__main__":
    main()
