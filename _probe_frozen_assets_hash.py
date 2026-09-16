"""§十一 只读复核：冻结资产与仓库状态的零修改证据。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
FROZEN = [
    "templates/contracts/standard_v2.yaml",
    "templates/contracts/standard_v3.yaml",
    "templates/policies/source_policy_v1.yaml",
    "templates/writing_specs/credit_report_v1.yaml",
    "templates/presentation_profiles/interview_demo_v1.yaml",
]


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")


def main() -> None:
    rc, head = _git("rev-parse", "HEAD")
    print(f"HEAD = {head.strip()}")
    rc, out = _git("status", "--short", "--", *FROZEN)
    print(f"frozen assets in git status --short: {out.strip()!r}  (空＝未改动)")
    print("\n冻结资产 SHA256（本轮零修改）：")
    for rel in FROZEN:
        p = ROOT / rel
        if not p.exists():
            print(f"  {rel}: <不存在>")
            continue
        print(f"  {rel}: {hashlib.sha256(p.read_bytes()).hexdigest()}")
    rc, out = _git("diff", "--stat", "HEAD", "--", "templates/")
    print(f"\ngit diff --stat HEAD -- templates/ = {out.strip()!r}  (空＝模板目录零改动)")
    rc, out = _git("stash", "list")
    print(f"git stash list = {out.strip()!r}  (空＝本轮未 stash)")
    rc, out = _git("log", "--oneline", "-3")
    print("最近 commit：")
    print(out.rstrip())


if __name__ == "__main__":
    main()
