"""只读取证：§六条件 6 的篡改反例审计当前状态（声明 vs 真实测试源码标签）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.six_category_acceptance import (  # noqa: E402
    _gate_registry_audit,
    _tamper_counterexample_audit,
)


def main() -> None:
    reg = _gate_registry_audit()
    aud = _tamper_counterexample_audit()
    print("gate_registry.holds =", reg["holds"])
    print("  missing_declarations    =", reg["missing_declarations"])
    print("  undeclared_registrations=", reg["undeclared_registrations"])
    print("tamper.satisfied =", aud["satisfied"])
    print("  counterexample_count =", aud["counterexample_count"])
    print("  by_kind =", json.dumps(aud["by_kind"], ensure_ascii=False))
    print("  required_kinds_covered =", aud["required_kinds_covered"])
    print("  unbypassable =", json.dumps(aud["unbypassable_counterexamples"],
                                      ensure_ascii=False))
    for row in aud["counterexamples"]:
        if not row["holds"]:
            print("   MISSING:", row["id"], "|", row["kind"], "|", row["label"])


if __name__ == "__main__":
    main()
