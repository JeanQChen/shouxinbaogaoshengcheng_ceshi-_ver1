"""只读调试：逐位置表引用扫描在「营业收入构成详见下表。」上到底发生了什么。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import evidence_reader as ER  # noqa: E402

_T = "营业收入构成详见下表。"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = _T
    n = len(s)
    print("len:", n)
    for i in range(n):
        print(f"i={i} ch={s[i]!r} startswith下表={s.startswith('下表', i)} "
              f"startswith见下表={s.startswith('见下表', i)} "
              f"startswith如下表={s.startswith('如下表', i)}")
    cands = []
    i = 0
    while i < n:
        best = ""
        for m in ER._TABLE_REFERENCE_MARKERS:
            if len(m) > len(best) and s.startswith(m, i):
                best = m
        if best:
            cands.append((i, i + len(best), best))
            i += len(best)
        else:
            i += 1
    print("table cands:", cands)
    print("actual count:", len(ER.iter_reference_occurrences(s)))


if __name__ == "__main__":
    main()
