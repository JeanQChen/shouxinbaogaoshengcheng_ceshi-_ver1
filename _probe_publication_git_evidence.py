"""§七 只读取证补充：publication 两个历史文件的 git 级证据（逐字、可复现）。

输出（全部只读，不改 index / worktree / ACL）：
1. ``git status --short`` 中与 publication 目录相关的行（逐字）；
2. ``git ls-files --stage`` 该目录的**完整逐字输出**（目录内全部被跟踪文件）；
3. 全仓 ``git ls-files --stage`` 的**行数与 sha256**（证明证据可复现，不靠省略）；
4. 每个目标文件的：index blob sha / HEAD blob sha / worktree 内容 sha
   （``git hash-object``）三者比对——一致即「worktree 与 HEAD 逐字节相同」；
5. ``git diff --stat HEAD -- <path>`` 与 ``.gitattributes`` 是否存在
   （排除 filter/EOL 转换导致的「看起来不一致」）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DIR = "evaluation/results/publication_run_20260911T_jsonfix"
TARGETS = [f"{DIR}/publication.json", f"{DIR}/report.md"]


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True)
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, (out + err)


def main() -> None:
    print("=" * 72)
    print("§七 git 级逐字证据（只读）")
    print("=" * 72)

    rc, out = _git("status", "--short")
    pub_lines = [ln for ln in out.splitlines() if DIR in ln]
    print(f"\n[1] git status --short 中 publication 目录相关行 rc={rc} 行数={len(pub_lines)}")
    print(repr("\n".join(pub_lines)))
    print("    → 空即该目录相对 HEAD/index/worktree 无任何改动、无删除、无未跟踪文件")

    rc, out = _git("ls-files", "--stage", "--", DIR)
    print(f"\n[2] git ls-files --stage -- {DIR} rc={rc}")
    print(out.rstrip("\n") if out.strip() else "<empty>")

    rc, out = _git("ls-files", "--stage")
    blob = out.encode("utf-8")
    print(f"\n[3] 全仓 git ls-files --stage rc={rc} 行数={len(out.splitlines())} "
          f"sha256={hashlib.sha256(blob).hexdigest()}")
    print("    （完整输出可复现：同一 HEAD 下重跑该命令应得到同一 sha256）")

    rc, head = _git("rev-parse", "HEAD")
    head_sha = head.strip()
    print(f"\n[4] HEAD={head_sha}")

    for rel in TARGETS:
        rc_i, idx = _git("ls-files", "--stage", "--", rel)
        idx_sha = idx.split()[1] if idx.split() else "<none>"
        rc_h, hsha = _git("rev-parse", f"HEAD:{rel}")
        hsha = hsha.strip()
        rc_w, wsha = _git("hash-object", rel)
        wsha = wsha.strip()
        rc_d, dstat = _git("diff", "--stat", "HEAD", "--", rel)
        print(f"\n    {rel}")
        print(f"      index  blob sha = {idx_sha}")
        print(f"      HEAD   blob sha = {hsha}")
        print(f"      worktree sha   = {wsha}  (git hash-object，按仓库 filter 规则)")
        print(f"      index==HEAD = {idx_sha == hsha} | worktree==HEAD = {wsha == hsha}")
        print(f"      git diff --stat HEAD -- path = {dstat.strip()!r}")

    ga = ROOT / ".gitattributes"
    print(f"\n[5] .gitattributes 存在 = {ga.exists()}")
    print("    无 .gitattributes ⇒ 无 EOL/filter 转换，说明「worktree 缺失」不可能由检出规则解释")

    print("\n[6] 只读边界：本探针只执行 git status/ls-files/rev-parse/cat-file/hash-object/diff")
    print("    与 os.stat/open，绝不执行 restore / checkout / reset / clean / rm / chmod / icacls。")


if __name__ == "__main__":
    main()
