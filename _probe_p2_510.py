"""只读取证：P2 角色裁定在真实 表5-10 单块摊平表上的实际切列与裁定结果。零写入。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import set_enumeration as SE  # noqa: E402
from harness import table_structure as T  # noqa: E402

TEXT = (
    "表 5-10发行人主营业务收入构成表\n\n"
    "单位：万元，%\n"
    "项目  2025年  2024年  2023年\n"
    "金额  占比  金额  占比  金额  占比\n\n"
    "动力电池系统  31,650,636.9 74.7  25,304,133.7  69.9  28,525,291.7  71.2\n\n"
    "储能电池系统  6,243,982.0  14.7  5,729,046.0  15.8  5,990,052.2  14.9\n\n"
    "合计  42,370,183.3100.0  36,201,255.3 100.0  40,091,704.5 100.0\n\n"
    "发行人主要营业收入来自于动力电池系统、储能电池系统、电池材料及回收和电池\n"
    "矿产资源四大业务板块。2023-2025年，发行人营业收入分别为 40,091,704.5万元。"
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

for line in TEXT.splitlines():
    s = T.normalize_line(line)
    if not s:
        continue
    print(f"canonical_cells={T.canonical_cells(s)}")
    print(f"  _split_columns={SE._split_columns(s)}")
    print(f"  numeric={[c for c in SE._split_columns(s) if SE._is_numeric_cell(c)]}")
