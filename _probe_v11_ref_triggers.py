"""只读取证：v11 各 run 的「边界策略可用性 + 真实引用标记」普查。

对每个 v11 run 打印：
- aspect 的边界策略状态（verified/incomplete/unavailable + 原因）；
- 真实 seed 文本中的 ``detect_reference_targets`` 命中；
- 已采纳材料 payload 预览中的命中。

只读，不触发任何扩读。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from harness.context_expansion import detect_reference_targets

RESULTS = Path("evaluation/results")
PREFIX = "r2_material_slice_r2_sixcat_v11_"


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 只读诊断
        return {"__error__": str(exc)}


def main() -> None:
    for run in sorted(RESULTS.glob(PREFIX + "*")):
        print("=" * 78)
        print(run.name)
        bv = _load(run / "boundary_verification.json") or {}
        for asp in bv.get("aspects") or []:
            for rec in asp.get("records") or []:
                print("  aspect:", rec.get("aspect_id"),
                      "| status:", rec.get("status"),
                      "| verified:", (rec.get("verification") or {}).get("verified"),
                      "| reason:", str(rec.get("reason"))[:90])
        _sm = _load(run / "seed_manifest.json") or {}
        seeds = _sm.get("entries") or _sm.get("seeds") or []
        for e in seeds:
            hits = detect_reference_targets(str(e.get("text") or ""))
            print("  seed", e.get("evidence_id", "")[:12], "page", e.get("page_number"),
                  "hits:", hits)
        prev = run / "payload_preview"
        if prev.is_dir():
            for f in sorted(prev.glob("*.json")):
                doc = _load(f)
                texts: list[str] = []
                if isinstance(doc, dict):
                    texts = [str(doc.get("text") or doc.get("content") or "")]
                elif isinstance(doc, list):
                    for it in doc:
                        if isinstance(it, dict):
                            texts.append(str(it.get("text") or it.get("content") or ""))
                        else:
                            texts.append(str(it))
                for text in texts:
                    hits = detect_reference_targets(text)
                    if hits:
                        print("  material", f.stem[:12], "hits:", hits,
                              "|", text[:60].replace("\n", "⏎"))


if __name__ == "__main__":
    main()
