"""只读取证（真实语料）：对 v12 真实 run 副本做**定向篡改**，证明验收侧会 fail-closed。

场景（§三 / §五.7 / §六.6）：把 v12 真实产物 ``expansion_trace.jsonl`` 里的
``reference_binding`` 改成

1. 目标块指向旧实现的错误块 P43 ``049de1a2…``（表5-6 发行人组织结构图）；
2. 目标对象身份改成任意伪造值；
3. 标记偏移整体平移；
4. 目标位置挪到标记**之前**；
5. 文档身份改成另一版本/集合；

每种篡改都必须在生产验收器下被判为不可信（``contradictory`` / 绑定问题非空 /
``target_resolvable`` 门失败），而**未篡改的原件仍为 resolved**。

实现方式：把真实 run 目录**复制到临时目录**后改动副本，绝不触碰
``evaluation/results`` 下的任何既有产物（零写入历史目录）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.six_category_acceptance import (  # noqa: E402
    CATEGORY_EXPLICIT_CROSS_REFERENCE,
    verify_category,
)

RUN = Path("evaluation/results/r2_material_slice_r2_sixcat_v12_major_subsidiaries_20260916")
WRONG_BLOCK = "049de1a26d73f3e89611679694ff4f06"


def _load_trace(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_trace(path: Path, steps: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n",
                    encoding="utf-8")


def _first_binding(steps: list[dict]) -> dict | None:
    for s in steps:
        if isinstance(s.get("reference_binding"), dict):
            return s["reference_binding"]
    return None


def _mutate(kind: str, steps: list[dict]) -> None:
    b = _first_binding(steps)
    if b is None:
        raise SystemExit("拒绝：副本 trace 中没有 reference_binding，无法构造篡改场景")
    if kind == "wrong_target_block":          # 目标块 → 旧实现的错误目标 P43
        b["target_evidence_id"] = WRONG_BLOCK
    elif kind == "forged_object_identity":    # 目标对象身份 → 伪造
        b["target_object_id"] = "0" * 64
    elif kind == "shifted_marker_offset":     # 标记偏移平移
        b["marker_start"] = int(b["marker_start"]) + 3
        b["marker_end"] = int(b["marker_end"]) + 3
    elif kind == "target_before_marker":      # 目标位置挪到标记之前
        b["target_start"] = int(b["marker_start"]) - 10
        b["target_end"] = int(b["marker_start"]) - 1
    elif kind == "foreign_document_identity":  # 文档/版本身份换成另一份真实年报
        b["document_version"] = "sha256-b4f1713d7b821eb0"
        b["evidence_set_version"] = "set-elsewhere"
    else:
        raise SystemExit(f"未知篡改类型 {kind}")


def _verify(run_dir: Path) -> dict:
    v = verify_category(CATEGORY_EXPLICIT_CROSS_REFERENCE, run_dir)
    ref = (v.facts or {}).get("explicit_reference_audit") or {}
    return {
        "state": ref.get("state"),
        "capability_verdict": v.capability_verdict,
        "failed_gates": [g for g, _ in (v.failed_gates or ())],
        "reference_binding_problems": ref.get("reference_binding_problems"),
        "resolution_targets": ref.get("resolution_targets"),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("原件（未篡改）:", json.dumps(_verify(RUN), ensure_ascii=False))

    kinds = ["wrong_target_block", "forged_object_identity", "shifted_marker_offset",
             "target_before_marker", "foreign_document_identity"]
    rows: list[dict] = []
    for kind in kinds:
        tmp = Path(tempfile.mkdtemp(prefix=f"v12tamper_{kind}_"))
        copy = tmp / RUN.name
        shutil.copytree(RUN, copy)
        steps = _load_trace(copy / "expansion_trace.jsonl")
        _mutate(kind, steps)
        _write_trace(copy / "expansion_trace.jsonl", steps)
        res = _verify(copy)
        rejected = (res["state"] == "contradictory"
                    or bool(res["reference_binding_problems"])
                    or res["capability_verdict"] != "PASS")
        rows.append({"tamper": kind, "rejected": rejected, **res})
        print(f"\n篡改 {kind}: 拒绝={rejected}")
        print("  ", json.dumps(res, ensure_ascii=False)[:500])
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n全部篡改场景均被 fail-closed 拒绝:",
          all(r["rejected"] for r in rows), f"（{len(rows)}/{len(rows)}）")


if __name__ == "__main__":
    main()
