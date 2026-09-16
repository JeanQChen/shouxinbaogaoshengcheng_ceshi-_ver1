"""§七 只读诊断：``evaluation/results/publication_run_20260911T_jsonfix`` 两个历史文件异常。

对 ``publication.json`` 与 ``report.md`` 逐项只读核查：
1. Git index 是否仍跟踪（``git ls-files --stage``）
2. HEAD blob 是否存在（``git cat-file -e HEAD:<path>`` + ``git rev-parse``）
3. worktree 路径是否存在、是否为文件/目录、大小、可读性
4. 目录条目枚举（区分「真删除」/「ACL 不可读」/「其他文件系统异常」）
5. ``git status --porcelain`` 对该路径的判定

**只读**：不 restore / checkout / reset / 删除 / 覆盖 / 改 ACL / 重新生成。
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DIR = "evaluation/results/publication_run_20260911T_jsonfix"
TARGETS = [f"{DIR}/publication.json", f"{DIR}/report.md"]


def _git(*args: str) -> tuple[int, str]:
    """只读 git 子命令；绝不执行写操作。"""
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True)
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    err = (p.stderr or b"").decode("utf-8", "replace").strip()
    return p.returncode, (out + ("\n" + err if err else ""))


def _stat_report(rel: str) -> dict:
    p = ROOT / rel
    info: dict = {"path": rel, "exists": p.exists(),
                  "is_file": p.is_file(), "is_dir": p.is_dir()}
    try:
        st = p.stat()
        info["size"] = st.st_size
        info["st_mode_octal"] = oct(stat.S_IMODE(st.st_mode))
    except OSError as e:
        info["stat_error"] = f"{type(e).__name__}: {e}"
    try:
        with open(p, "rb") as f:
            head = f.read(64)
        info["readable"] = True
        info["first_bytes_repr"] = repr(head[:32])
    except OSError as e:
        info["readable"] = False
        info["read_error"] = f"{type(e).__name__}: {e}"
    return info


def main() -> None:
    print("=" * 72)
    print("§七 只读诊断：历史 publication 产物异常")
    print("=" * 72)

    print("\n--- 0. 目录条目枚举 ---")
    d = ROOT / DIR
    if d.exists():
        try:
            entries = sorted(os.listdir(d))
            print(f"目录存在；条目数={len(entries)}")
            for e in entries:
                ep = d / e
                print(f"  {e!r} is_file={ep.is_file()} is_dir={ep.is_dir()}")
        except OSError as ex:
            print(f"目录列举失败: {type(ex).__name__}: {ex}")
    else:
        print(f"目录不存在: {DIR}")

    for rel in TARGETS:
        print(f"\n--- 目标: {rel} ---")
        rc, out = _git("ls-files", "--stage", "--", rel)
        print(f"[index ] git ls-files --stage rc={rc} out={out!r}")
        print(f"[index ] tracked_by_index={bool(out.strip())}")

        rc, head = _git("rev-parse", "HEAD")
        head_sha = head.strip()
        print(f"[HEAD  ] HEAD={head_sha[:12] if head_sha else head!r}")

        rc, out = _git("cat-file", "-e", f"HEAD:{rel}")
        print(f"[HEAD  ] cat-file -e HEAD:{rel} rc={rc} → blob_exists_in_HEAD={rc == 0}")

        rc, out = _git("rev-parse", f"HEAD:{rel}")
        print(f"[HEAD  ] rev-parse HEAD:{rel} rc={rc} sha={out.strip()!r}")

        rc, out = _git("status", "--porcelain", "--", rel)
        print(f"[status] porcelain={out!r}")

        rc, out = _git("log", "-1", "--format=%h %ad %s", "--date=short", "--", rel)
        print(f"[log   ] last_commit_touching={out.strip()!r}")

        print(f"[wt    ] {_stat_report(rel)}")

    print("\n--- 1. 汇总判定 ---")
    for rel in TARGETS:
        wt = _stat_report(rel)
        rc_blob, _ = _git("cat-file", "-e", f"HEAD:{rel}")
        rc_idx, idx = _git("ls-files", "--stage", "--", rel)
        if wt["exists"] and wt.get("readable"):
            verdict = "worktree 正常可读"
        elif wt["exists"] and not wt.get("readable"):
            verdict = "worktree 存在但不可读（疑似 ACL/权限不可读）"
        elif idx.strip() and rc_blob == 0:
            verdict = "worktree 缺失但 index+HEAD 完好（疑似真删除/未检出）"
        else:
            verdict = "其他文件系统异常（index 或 HEAD 也不完整）"
        print(f"  {rel}: exists={wt['exists']} readable={wt.get('readable')} "
              f"index_tracked={bool(idx.strip())} head_blob={rc_blob == 0} → {verdict}")


if __name__ == "__main__":
    main()
