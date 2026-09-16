"""只读调试：detect_reference_occurrences 在「营业收入构成详见下表。」上的原始候选。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import evidence_reader as ER  # noqa: E402

_T = "营业收入构成详见下表。"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("utf-8 直接：", repr(_T))
    print("table markers:", ER._TABLE_REFERENCE_MARKERS)
    print("named markers:", ER._NAMED_REFERENCE_MARKERS)
    print("named raw:", [o.to_dict() for o in ER._named_reference_occurrences(_T)])
    occs = ER.iter_reference_occurrences(_T)
    print("count:", len(occs))
    for o in occs:
        print("  ", o.to_dict(), "target=", o.request_target)


if __name__ == "__main__":
    main()
